from pathlib import Path

import pytest

from geomcp_server import auth
from geomcp_server.db import migrate


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'geomcp.db'}"
    migrate(url)
    return url


def test_key_lifecycle(db_url: str) -> None:
    token = auth.create_key(db_url, "ceo-laptop")
    key = auth.check_token(db_url, token)
    assert key is not None and key.name == "ceo-laptop"
    assert auth.revoke_key(db_url, key.id)
    assert auth.check_token(db_url, token) is None
    assert not auth.revoke_key(db_url, key.id)


def test_secret_is_stored_only_as_hash(db_url: str) -> None:
    token = auth.create_key(db_url, "x")
    secret = token.split("_", 2)[2]
    (key,) = auth.list_keys(db_url)
    assert secret not in key.secret_sha256 and len(key.secret_sha256) == 64


@pytest.mark.parametrize("bad", ["", "gmk", "gmk_abc", "xyz_abc_def", "gmk_000000000000_wrong"])
def test_malformed_or_unknown_tokens_are_rejected(db_url: str, bad: str) -> None:
    auth.create_key(db_url, "x")
    assert auth.check_token(db_url, bad) is None


def test_wrong_secret_for_existing_id_is_rejected(db_url: str) -> None:
    token = auth.create_key(db_url, "x")
    prefix, key_id, _ = token.split("_", 2)
    assert auth.check_token(db_url, f"{prefix}_{key_id}_not-the-secret") is None


def test_google_allowlist() -> None:
    allowed = ["@example.com", "ceo@gmail.com"]
    assert auth.email_allowed("Anna@Example.com", "true", allowed)
    assert auth.email_allowed("ceo@gmail.com", True, allowed)
    assert not auth.email_allowed("anna@example.com", "false", allowed)  # unverified
    assert not auth.email_allowed("x@evil-example.com.org", "true", allowed)
    assert not auth.email_allowed("x@notexample.com", "true", allowed)
    assert not auth.email_allowed(None, "true", allowed)


def test_google_sign_in_needs_an_allowlist(db_url: str) -> None:
    with pytest.raises(ValueError, match="ALLOWED_EMAILS"):
        auth.http_auth(db_url, "https://geo.example.org", "id", "secret", [])
    assert isinstance(auth.http_auth(db_url, "https://geo.example.org", None, None, []), auth.ApiKeyVerifier)


async def test_google_tokens_outside_the_allowlist_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastmcp.server.auth import AccessToken
    from fastmcp.server.auth.oauth_proxy import OAuthProxy

    async def upstream(self: object, token: str) -> AccessToken:
        email = {"t1": "anna@example.com", "t2": "eve@gmail.com"}[token]
        return AccessToken(token=token, client_id="sub", scopes=[], claims={"email": email, "email_verified": True})

    monkeypatch.setattr(OAuthProxy, "load_access_token", upstream)
    google = auth.GoogleSignIn(
        client_id="id", client_secret="secret", base_url="https://geo.example.org", allowed_emails=["@example.com"]
    )
    ok = await google.verify_token("t1")
    assert ok is not None and ok.client_id == "anna@example.com"  # stats show the person, not a Google id
    assert await google.verify_token("t2") is None


@pytest.mark.parametrize(
    "raw", ['["@example.com", "a@gmail.com"]', "@example.com, a@gmail.com", "[@example.com,a@gmail.com]"]
)
def test_allowed_emails_setting_formats(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from geomcp_server.settings import Settings

    monkeypatch.setenv("GEOMCP_ALLOWED_EMAILS", raw)
    assert Settings(_env_file=None).allowed_emails == ["@example.com", "a@gmail.com"]  # pyright: ignore[reportCallIssue]
