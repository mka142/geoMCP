"""File cache for adapter results: one JSON file per key, with an optional expiry.

Year chunks that can no longer change are stored without expiry; recent chunks expire, because
preliminary products (ERA5T) are replaced and new acquisitions arrive.
"""

import hashlib
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from geomcp_core.config import data_dir

SETTLED_AFTER_DAYS = 90
RECENT_TTL_S = 6 * 3600


def _path(namespace: str, key: str) -> Path:
    digest = hashlib.sha1(key.encode()).hexdigest()
    return data_dir() / "cache" / namespace / digest[:2] / f"{digest}.json"


def get(namespace: str, key: str) -> Any | None:
    p = _path(namespace, key)
    try:
        payload = json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if payload["expires"] is not None and payload["expires"] < time.time():
        return None
    return payload["value"]


def put(namespace: str, key: str, value: Any, ttl_s: float | None) -> None:
    p = _path(namespace, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"key": key, "expires": None if ttl_s is None else time.time() + ttl_s, "value": value}))
    tmp.replace(p)  # atomic, so concurrent readers never see a partial file


def ttl_for_chunk(chunk_end: date, today: date) -> float | None:
    """None (keep forever) once the chunk is settled, otherwise a short TTL."""
    return None if chunk_end <= today - timedelta(days=SETTLED_AFTER_DAYS) else RECENT_TTL_S
