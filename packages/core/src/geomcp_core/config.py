"""Core configuration from the environment (the server loads `.env` into it before importing core)."""

import os
from importlib.metadata import version
from pathlib import Path


def data_dir() -> Path:
    return Path(os.environ.get("GEOMCP_DATA_DIR", str(Path.home() / ".geomcp")))


def env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def user_agent() -> str:
    """Public services (Nominatim above all) ask for a way to reach the operator: GEOMCP_CONTACT, an email or URL."""
    contact = env("GEOMCP_CONTACT")
    return f"geomcp/{version('geomcp-core')} ({f'+{contact}; ' if contact else ''}geodata MCP server)"
