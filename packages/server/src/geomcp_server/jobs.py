"""Background jobs for tool calls that would exceed the synchronous time budget.

Jobs run in this process (a small thread pool) and their results live in SQLite, so any client
can poll get_job. A restart marks unfinished jobs as failed rather than losing them silently.
"""

import contextvars
import json
import secrets
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import delete, update

from geomcp_core.models import GeomcpError
from geomcp_server.db import Job, sessions

log = structlog.get_logger()
KEEP = timedelta(days=7)
Outcome = tuple[dict[str, Any], str]  # structured content, text summary


class JobRunner:
    def __init__(self, db_url: str, workers: int = 2) -> None:
        self.db_url = db_url
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="geomcp-job")
        with sessions(db_url).begin() as s:
            s.execute(
                update(Job)
                .where(Job.status == "running")
                .values(
                    status="failed",
                    error=json.dumps(
                        {
                            "code": "server_restarted",
                            "message": "The server restarted while this job was running.",
                            "hint": "Call the tool again.",
                        }
                    ),
                    finished_at=datetime.now(UTC),
                )
            )
            s.execute(delete(Job).where(Job.created_at < datetime.now(UTC) - KEEP))

    def submit(self, tool: str, eta_s: float, fn: Callable[[], Outcome]) -> str:
        job_id = f"j_{secrets.token_hex(8)}"
        with sessions(self.db_url).begin() as s:
            s.add(Job(id=job_id, tool=tool, status="running", eta_s=eta_s, created_at=datetime.now(UTC)))
        self.pool.submit(contextvars.copy_context().run, self._run, job_id, fn)  # keeps the caller for stats
        return job_id

    def _run(self, job_id: str, fn: Callable[[], Outcome]) -> None:
        values: dict[str, Any]
        try:
            structured, text = fn()
            values = {"status": "succeeded", "result": json.dumps({"structured": structured, "text": text})}
        except GeomcpError as e:
            values = {"status": "failed", "error": json.dumps(e.as_dict())}
        except Exception as e:  # report unexpected failures to the client instead of losing the job
            log.exception("job_crashed", job_id=job_id)
            values = {
                "status": "failed",
                "error": json.dumps(
                    {
                        "code": "internal_error",
                        "message": f"The job failed unexpectedly ({type(e).__name__}); the server log has details.",
                        "hint": "Try again; if it repeats, ask the server's owner.",
                    }
                ),
            }
        with sessions(self.db_url).begin() as s:
            s.execute(update(Job).where(Job.id == job_id).values(**values, finished_at=datetime.now(UTC)))

    def wait(self, job_id: str, timeout_s: float) -> Job | None:
        """The job, waiting up to `timeout_s` for it to finish."""
        deadline = time.monotonic() + timeout_s
        while True:
            job = self.get(job_id)
            if job is None or job.status != "running" or time.monotonic() >= deadline:
                return job
            time.sleep(0.5)

    def get(self, job_id: str) -> Job | None:
        with sessions(self.db_url)() as s:
            return s.get(Job, job_id)
