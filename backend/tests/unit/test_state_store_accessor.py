"""worker-controls REQ-6: one StateStore per process via the lru_cache accessor.

ensure_tables() (5 REST round-trips) runs once at first use — not per request
now that the worker routes compose a store per click.
"""

import pytest

import core.state_store
from core.state_store import get_state_store


@pytest.fixture(autouse=True)
def clean_cache():
    """A store cached under one test's env (or a fake factory) must not leak
    across tests in either direction (gate ER-W1). Kept local — the systemic
    clearing lives in `secret_env` (PR #15 review M1), which this module does
    not consume: it tests the cache itself against a monkeypatched factory."""
    get_state_store.cache_clear()
    yield
    get_state_store.cache_clear()


def test_get_state_store_is_cached(monkeypatch):
    constructions: list[object] = []

    def counting_factory(settings):
        constructions.append(settings)
        return object()

    monkeypatch.setattr(core.state_store, "state_store_from_settings", counting_factory)
    first = get_state_store()
    second = get_state_store()
    assert first is second
    assert len(constructions) == 1
