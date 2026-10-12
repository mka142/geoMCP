"""Server settings, read from GEOMCP_* environment variables (and a .env file if present)."""

from functools import cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEOMCP_", env_file=".env", extra="ignore")

    data_dir: Path = Path.home() / ".geomcp"
    db_url: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    allowed_hosts: list[str] | None = None
    public_url: str | None = None  # e.g. https://geo.example.org; HTTP mode uses http://host:port when unset
    google_client_id: str | None = None  # Google sign-in for OAuth-only clients; see docs/deploy-vps.md
    google_client_secret: str | None = None
    allowed_emails: Annotated[list[str], NoDecode] = []  # who may sign in with Google: emails or "@domain"
    # where OAuth clients may be sent back after sign-in: claude.ai, Claude Desktop and mobile (claude.com is the
    # announced successor), ChatGPT connectors, and loopback ports for Claude Code, mcp-remote and other local clients
    oauth_redirect_uris: Annotated[list[str], NoDecode] = [
        "https://claude.ai/api/mcp/auth_callback",
        "https://claude.com/api/mcp/auth_callback",
        "https://chatgpt.com/connector_platform_oauth_redirect",
        "http://localhost:*",
        "http://127.0.0.1:*",
    ]
    sync_budget_s: float = 45.0
    sentinel_hub_monthly_units: float = 10_000.0
    sentinel_hub_monthly_requests: int = 10_000

    @field_validator("allowed_emails", "oauth_redirect_uris", mode="before")
    @classmethod
    def _comma_list(cls, v: object) -> object:
        """Comma-separated (`a@x.com, @y.com`) or a JSON list."""
        if isinstance(v, str):
            return [e.strip(" \"'[]") for e in v.split(",") if e.strip(" \"'[]")]
        return v

    @property
    def database_url(self) -> str:
        return self.db_url or f"sqlite:///{self.data_dir / 'geomcp.db'}"


@cache
def get_settings() -> Settings:
    return Settings()
