# yule-storage

Local-first SQLite persistence for Yule Studio agents. Three independent
stores sharing one write lock, with no agent/discord/LLM dependencies.

## Responsibility

- **`_sqlite`** — the shared `SQLITE_WRITE_LOCK` that serialises writes
  across all stores in-process.
- **`local_cache`** — generic JSON cache (`load_json_cache`,
  `save_json_cache`, `list_json_cache_entries`, `cleanup_json_cache`,
  `local_cache_database_path`, `LocalCacheEntry`).
- **`calendar_state`** — durable calendar-state sync
  (`sync_calendar_query_result`, `list_calendar_state_records`,
  `cleanup_calendar_state_records`, `CalendarStateRecord`,
  `CalendarStateSyncSummary`).
- **`task_history`** — task-completion history + user-pattern signals
  (`record_task_completion_event`, `query_task_completion_stats`,
  `compute_user_pattern_signals`, `compute_user_pattern_signals_batch`,
  plus `TaskCompletionEvent` / `TaskCompletionStats` /
  `UserPatternSignals`).

## Dependency rule

`yule_storage` depends on **stdlib + sqlite3 only** (`dependencies =
[]`). It MUST NOT import `yule_engineering` runtime, Discord, or agent
internals.

The runtime DAG is **`yule_integrations → yule_storage`** (one
direction). `yule_storage` does NOT import `yule_integrations` at
runtime. The single back-reference in `calendar_state.py` is a
`TYPE_CHECKING`-only import of
`yule_integrations.calendar.models.{CalendarEvent,CalendarQueryResult,CalendarTodo}`
for type hints — it never executes, so there is no import cycle.

## Compatibility

`yule_engineering.storage.{__init__,_sqlite,calendar_state,local_cache,task_history}`
are thin shims. The submodule shims alias the new modules via
`sys.modules`, so deep imports and test patches against
`yule_engineering.storage.<mod>.<attr>` resolve to the identical module
objects.
