import pytest

from geomcp_core import http


def test_user_agent_carries_the_operator_contact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEOMCP_CONTACT", "ops@example.com")
    with http.client() as c:
        assert "+ops@example.com" in c.headers["User-Agent"]
    monkeypatch.delenv("GEOMCP_CONTACT")
    with http.client() as c:
        assert c.headers["User-Agent"].startswith("geomcp/") and "+" not in c.headers["User-Agent"]
