from geomcp_core import registry
from geomcp_core.catalog import CATEGORIES, ENTRIES


def test_every_registered_source_has_a_catalogue_entry() -> None:
    ids = {a.info.id for a in registry.adapters() if not a.info.id.startswith("synthetic")}
    assert ids <= set(ENTRIES), f"missing catalogue entries: {sorted(ids - set(ENTRIES))}"


def test_entries_are_complete() -> None:
    for e in ENTRIES.values():
        assert e.category in CATEGORIES, e.id
        assert e.examples and e.tools and e.measures and e.history, e.id
