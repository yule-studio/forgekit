# yule-integrations

External service integrations for Yule Studio agents. Two nested
sub-packages: `calendar` (Naver CalDAV) and `github` (issues + pulls via
the `gh` CLI).

## Responsibility

- **`calendar`** — `errors`, `models` (`CalendarEvent`,
  `CalendarQueryResult`, `CalendarTodo`, `build_fallback_item_uid`),
  `parsing`, `naver_caldav` (`list_naver_calendar_events`,
  `list_naver_calendar_items`), `rendering`, and a CalDAV-result `cache`.
- **`github`** — `issues` (`list_open_issues`, `render_open_issues`,
  `GitHubIssue`, `GitHubIssueError`), `pulls`
  (`list_open_pull_requests`, `render_open_pull_requests`,
  `GitHubPullRequest`), and a `cache` for both.

## Dependency rule

Runtime DAG is **`yule_integrations → yule_storage`** (one direction):

- `calendar.cache` & `github.cache` import `load_json_cache`,
  `save_json_cache`, `list_json_cache_entries` from `yule_storage`.
- `calendar.naver_caldav` imports `sync_calendar_query_result` from
  `yule_storage.calendar_state`.

`yule_storage` only references `yule_integrations` under
`TYPE_CHECKING` (type hints), so there is no runtime cycle.

`yule_integrations` depends on `yule_storage` at runtime, but this is
resolved via the monorepo path (both packages' `src` are on
`PYTHONPATH` / the root editable install) rather than a hard pip
dependency, so it is intentionally not listed under `[project]
dependencies`.

Third-party runtime deps: `caldav` and `icalendar` (lazily imported
inside `naver_caldav`). GitHub access shells out to the `gh` CLI via
`subprocess` — no HTTP client dependency.

## Compatibility

`yule_orchestrator.integrations.{calendar,github}.<mod>` are thin
shims. Leaf submodule shims alias the new modules via `sys.modules`, so
deep imports and test patches against
`yule_orchestrator.integrations.<pkg>.<mod>.<attr>` resolve to the
identical module objects. The package `__init__` shims re-export the
same public names as the originals.
