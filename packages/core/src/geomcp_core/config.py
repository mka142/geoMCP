"""Core configuration from the environment (the server loads `.env` into it before importing core)."""

import os
from pathlib import Path

USER_AGENT = "geomcp/0.1 (+https://github.com/; geodata MCP server)"


def data_dir() -> Path:
    return Path(os.environ.get("GEOMCP_DATA_DIR", str(Path.home() / ".geomcp")))


def env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None
