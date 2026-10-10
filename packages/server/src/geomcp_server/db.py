"""SQLite (or any SQLAlchemy URL) state: schema, engine and migrations."""

from datetime import datetime
from functools import cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Boolean, DateTime, Engine, Float, Integer, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    secret_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tool: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # running, succeeded, failed
    eta_s: Mapped[float] = mapped_column(Float)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: structured content and text
    error: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: {code, message, hint}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Usage(Base):
    __tablename__ = "usage"

    month: Mapped[str] = mapped_column(String(7), primary_key=True)  # YYYY-MM (UTC)
    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    units: Mapped[float] = mapped_column(Float, default=0.0)


class ToolCall(Base):
    """One row per tool call, for `geomcp-admin stats` (who used what, how fast, at what quota cost)."""

    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    tool: Mapped[str] = mapped_column(String(64))
    client: Mapped[str] = mapped_column(String(200))  # API key name, or "local" over stdio
    ok: Mapped[bool] = mapped_column(Boolean)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_s: Mapped[float] = mapped_column(Float)
    units: Mapped[float] = mapped_column(Float, default=0.0)


@cache
def engine(url: str) -> Engine:
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    eng = create_engine(url)
    if eng.dialect.name == "sqlite":

        @event.listens_for(eng, "connect")
        def _pragmas(conn, _record):  # type: ignore[no-untyped-def]
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

    return eng


def sessions(url: str) -> sessionmaker:  # type: ignore[type-arg]
    return sessionmaker(engine(url), expire_on_commit=False)


def migrate(url: str) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.attributes["engine"] = engine(url)
    command.upgrade(cfg, "head")
