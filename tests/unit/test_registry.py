from importlib.metadata import EntryPoint

import pytest

from geomcp_core import registry


def test_an_adapter_that_fails_to_load_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    broken = EntryPoint(name="broken", value="geomcp_core.does_not_exist:Nope", group="geomcp.adapters")
    good = EntryPoint(
        name="synthetic", value="geomcp_core.adapters.synthetic:SyntheticAdapter", group="geomcp.adapters"
    )
    monkeypatch.setattr(registry, "entry_points", lambda group: [broken, good])
    registry.adapters.cache_clear()
    try:
        assert [a.info.id for a in registry.adapters()] == ["synthetic"]
    finally:
        registry.adapters.cache_clear()
