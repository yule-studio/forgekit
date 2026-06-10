"""Smoke tests for the extracted ``yule_integrations`` package.

Covers the public API of both nested sub-packages (``calendar`` +
``github``), a no-network model round-trip, and old-path shim identity
(the ``yule_engineering.integrations`` modules must be the *same*
objects as ``yule_integrations``). No CalDAV / ``gh`` calls are made.
"""

from __future__ import annotations

from yule_integrations.calendar import (
    CalendarEvent,
    CalendarIntegrationError,
    CalendarQueryResult,
    CalendarTodo,
    list_naver_calendar_items,
    render_calendar_items,
)
from yule_integrations.calendar.models import build_fallback_item_uid
from yule_integrations.github import (
    GitHubIssue,
    GitHubIssueError,
    list_open_issues,
    render_open_issues,
)
from yule_integrations.github.pulls import (
    GitHubPullRequest,
    list_open_pull_requests,
)


def test_public_api_is_importable() -> None:
    assert callable(list_naver_calendar_items)
    assert callable(render_calendar_items)
    assert callable(list_open_issues)
    assert callable(render_open_issues)
    assert callable(list_open_pull_requests)
    assert issubclass(CalendarIntegrationError, Exception)
    assert issubclass(GitHubIssueError, Exception)
    for cls in (
        CalendarEvent,
        CalendarTodo,
        CalendarQueryResult,
        GitHubIssue,
        GitHubPullRequest,
    ):
        assert cls is not None


def test_fallback_uid_is_deterministic() -> None:
    uid_a = build_fallback_item_uid("event", "alpha", "beta")
    uid_b = build_fallback_item_uid("event", "alpha", "beta")
    assert uid_a == uid_b
    assert isinstance(uid_a, str) and uid_a


def test_old_path_shim_identity() -> None:
    import yule_integrations.calendar as new_calendar
    import yule_integrations.calendar.models as new_models
    import yule_integrations.github.cache as new_gh_cache
    import yule_integrations.github.issues as new_gh_issues

    import yule_engineering.integrations.calendar as old_calendar
    import yule_engineering.integrations.calendar.models as old_models
    import yule_engineering.integrations.github.cache as old_gh_cache
    import yule_engineering.integrations.github.issues as old_gh_issues

    assert old_models is new_models
    assert old_gh_cache is new_gh_cache
    assert old_gh_issues is new_gh_issues
    # Package-level shim re-exports the identical public objects.
    assert old_calendar.CalendarEvent is new_calendar.CalendarEvent
    assert old_calendar.list_naver_calendar_items is (
        new_calendar.list_naver_calendar_items
    )


def test_integrations_depends_on_storage() -> None:
    # The runtime edge integrations -> storage must resolve: cache modules
    # import the storage JSON-cache helpers as module-level names.
    import yule_integrations.calendar.cache as cal_cache
    import yule_integrations.github.cache as gh_cache

    assert callable(cal_cache.load_json_cache)
    assert callable(gh_cache.save_json_cache)
