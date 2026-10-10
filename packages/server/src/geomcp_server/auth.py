"""HTTP-mode authentication: API keys, plus optional Google sign-in for clients that only speak OAuth.

Keys look like `gmk_<id>_<secret>`. Only the SHA-256 of the secret is stored: the secret is 256 random bits,
so a fast hash is safe here (slow hashes such as Argon2 protect low-entropy human passwords).
Google sign-in (Claude Desktop and claude.ai custom connectors) uses FastMCP's GoogleProvider; only verified
emails on GEOMCP_ALLOWED_EMAILS get in.
"""

import asyncio
import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from typing import Any

from fastmcp.server.auth import AccessToken, AuthProvider, MultiAuth, TokenVerifier
from fastmcp.server.auth.providers.google import GoogleProvider
from mcp.server.auth.provider import AccessToken as UpstreamToken
from sqlalchemy import select

from geomcp_server.db import ApiKey, sessions

PREFIX = "gmk"


def _sha256(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def create_key(db_url: str, name: str) -> str:
    """Store a new key and return the token; it is shown once and cannot be recovered."""
    key_id = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    with sessions(db_url).begin() as s:
        s.add(ApiKey(id=key_id, name=name, secret_sha256=_sha256(secret), created_at=datetime.now(UTC)))
    return f"{PREFIX}_{key_id}_{secret}"


def list_keys(db_url: str) -> list[ApiKey]:
    with sessions(db_url)() as s:
        return list(s.scalars(select(ApiKey).order_by(ApiKey.created_at)))


def revoke_key(db_url: str, key_id: str) -> bool:
    with sessions(db_url).begin() as s:
        key = s.get(ApiKey, key_id)
        if key is None or key.revoked_at is not None:
            return False
        key.revoked_at = datetime.now(UTC)
        return True


def check_token(db_url: str, token: str) -> ApiKey | None:
    prefix, _, rest = token.partition("_")
    key_id, _, secret = rest.partition("_")
    if prefix != PREFIX or not key_id or not secret:
        return None
    with sessions(db_url)() as s:
        key = s.get(ApiKey, key_id)
    if key is None or key.revoked_at is not None:
        return None
    return key if hmac.compare_digest(key.secret_sha256, _sha256(secret)) else None


class ApiKeyVerifier(TokenVerifier):
    def __init__(self, db_url: str) -> None:
        super().__init__()
        self.db_url = db_url

    async def verify_token(self, token: str) -> AccessToken | None:
        key = await asyncio.to_thread(check_token, self.db_url, token)
        if key is None:
            return None
        return AccessToken(token=token, client_id=key.name, scopes=[], subject=key.id)


def email_allowed(email: str | None, verified: object, allowed: list[str]) -> bool:
    """`allowed` holds whole emails or `@domain` entries; Google sends `email_verified` as "true" or True."""
    if not email or str(verified).lower() != "true":
        return False
    email = email.lower()
    return any(email == a.lower() or (a.startswith("@") and email.endswith(a.lower())) for a in allowed)


class GoogleSignIn(GoogleProvider):
    """Google OAuth limited to an email allowlist; the email becomes the client name in logs and stats."""

    def __init__(self, *, allowed_emails: list[str], **kwargs: Any) -> None:
        super().__init__(required_scopes=["openid", "email"], **kwargs)
        self.allowed_emails = allowed_emails

    async def load_access_token(self, token: str) -> UpstreamToken | None:  # every token check ends here
        found = await super().load_access_token(token)
        claims = (found.claims or {}) if found else {}
        if found is None or not email_allowed(claims.get("email"), claims.get("email_verified"), self.allowed_emails):
            return None
        return found.model_copy(update={"client_id": claims["email"]})


def http_auth(
    db_url: str, public_url: str, google_client_id: str | None, google_client_secret: str | None,
    allowed_emails: list[str],
) -> AuthProvider:  # fmt: skip
    """API keys alone, or Google sign-in first and API keys as the fallback."""
    keys = ApiKeyVerifier(db_url)
    if not google_client_id:
        return keys
    if not google_client_secret or not allowed_emails:
        raise ValueError("Google sign-in needs GEOMCP_GOOGLE_CLIENT_SECRET and GEOMCP_ALLOWED_EMAILS.")
    google = GoogleSignIn(
        client_id=google_client_id, client_secret=google_client_secret, base_url=public_url,
        allowed_emails=allowed_emails,
    )  # fmt: skip
    return MultiAuth(server=google, verifiers=[keys], required_scopes=[])  # API keys carry no scopes
