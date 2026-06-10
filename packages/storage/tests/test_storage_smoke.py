"""Smoke tests for the extracted ``yule_storage`` package.

Covers the public API surface, a JSON cache round-trip against a temp
SQLite db, and old-path shim identity (the ``yule_engineering.storage``
modules must be the *same* objects as ``yule_storage``).
"""

from __future__ import annotations

from pathlib import Path

from yule_storage import (
    CalendarStateRecord,
    LocalCacheEntry,
    TaskCompletionEvent,
    list_json_cache_entries,
    load_json_cache,
    save_json_cache,
    sync_calendar_query_result,
)


def test_public_api_is_importable() -> None:
    assert callable(load_json_cache)
    assert callable(save_json_cache)
    assert callable(list_json_cache_entries)
    assert callable(sync_calendar_query_result)
    assert LocalCacheEntry is not None
    assert CalendarStateRecord is not None
    assert TaskCompletionEvent is not None


def test_json_cache_roundtrip(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "cache.sqlite3"
    monkeypatch.setenv("YULE_CACHE_DB_PATH", str(db_path))

    save_json_cache(
        namespace="ns",
        cache_key="key",
        provider="test",
        range_start=None,
        range_end=None,
        scope_hash="scope",
        ttl_seconds=600,
        payload={"alpha": 1, "beta": [2, 3]},
    )

    entry = load_json_cache("ns", "key")
    assert entry is not None
    assert entry.cache_key == "key"
    assert entry.payload == {"alpha": 1, "beta": [2, 3]}

    entries = list_json_cache_entries("ns")
    assert any(e.cache_key == "key" for e in entries)


def test_old_path_shim_identity() -> None:
    import yule_storage
    import yule_storage.local_cache as new_local_cache
    import yule_engineering.storage as old_storage
    import yule_engineering.storage.local_cache as old_local_cache
    import yule_engineering.storage.calendar_state as old_calendar_state
    import yule_engineering.storage._sqlite as old_sqlite

    assert old_storage.load_json_cache is yule_storage.load_json_cache
    assert old_local_cache is new_local_cache
    assert old_calendar_state is yule_storage.calendar_state
    assert old_sqlite is yule_storage._sqlite
    assert (
        old_sqlite.SQLITE_WRITE_LOCK
        is yule_storage._sqlite.SQLITE_WRITE_LOCK
    )
