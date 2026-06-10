"""Compatibility shim — local storage now lives in ``yule_storage``.

The SQLite-backed local cache, calendar-state sync, and task-history
stores were extracted into the standalone ``yule-storage`` package so
they carry no agent/discord runtime dependencies. This module re-exports
the same public names so every existing
``from yule_engineering.storage import ...`` keeps resolving to the
identical objects.

The submodule shims (``_sqlite``, ``calendar_state``, ``local_cache``,
``task_history``) alias the new modules via ``sys.modules`` so that test
patches against deep paths (e.g.
``yule_engineering.storage.local_cache._reset_cleanup_schedule_for_tests``)
operate on the *same* module object the real code uses.
"""

from yule_storage import (
    CalendarStateRecord,
    CalendarStateSyncSummary,
    LocalCacheEntry,
    TaskCompletionEvent,
    TaskCompletionStats,
    UserPatternSignals,
    cleanup_calendar_state_records,
    cleanup_json_cache,
    compute_user_pattern_signals,
    compute_user_pattern_signals_batch,
    list_calendar_state_records,
    list_json_cache_entries,
    load_json_cache,
    local_cache_database_path,
    query_task_completion_stats,
    record_task_completion_event,
    save_json_cache,
    sync_calendar_query_result,
)


__all__ = [
    "CalendarStateRecord",
    "CalendarStateSyncSummary",
    "LocalCacheEntry",
    "TaskCompletionEvent",
    "TaskCompletionStats",
    "UserPatternSignals",
    "cleanup_calendar_state_records",
    "cleanup_json_cache",
    "compute_user_pattern_signals",
    "compute_user_pattern_signals_batch",
    "list_json_cache_entries",
    "list_calendar_state_records",
    "load_json_cache",
    "local_cache_database_path",
    "query_task_completion_stats",
    "record_task_completion_event",
    "save_json_cache",
    "sync_calendar_query_result",
]
