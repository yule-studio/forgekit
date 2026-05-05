from __future__ import annotations

from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.integrations.calendar.models import CalendarQueryResult
from yule_orchestrator.integrations.github.issues import GitHubIssue
from yule_orchestrator.integrations.calendar.errors import build_calendar_error
from yule_orchestrator.integrations.github.issues import GitHubIssueError
from yule_orchestrator.planning.inputs import collect_planning_inputs


class PlanningInputsTestCase(unittest.TestCase):
    @patch("yule_orchestrator.planning.inputs.list_calendar_state_records")
    def test_collect_planning_inputs_prefers_local_calendar_state(
        self,
        list_calendar_state_records_mock,
    ) -> None:
        list_calendar_state_records_mock.return_value = [
            SimpleNamespace(
                item_type="todo",
                payload={
                    "item_uid": "todo-1",
                    "title": "오늘 해야 할 업무",
                    "start": None,
                    "due": "2026-04-23",
                    "start_all_day": False,
                    "due_all_day": True,
                    "status": "NEEDS-ACTION",
                    "completed": False,
                    "completed_at": None,
                    "priority": None,
                    "percent_complete": None,
                    "calendar_name": "내 할 일",
                    "source": "naver-caldav",
                    "description": "",
                    "last_modified": None,
                    "category_color": "27",
                },
            )
        ]

        inputs = collect_planning_inputs(
            plan_date=date(2026, 4, 23),
            include_calendar=True,
            include_github=False,
            reminders=[],
        )

        self.assertEqual(len(inputs.calendar_todos), 1)
        self.assertEqual(inputs.calendar_todos[0].category_color, "27")
        self.assertEqual(inputs.source_statuses[0].source_id, "calendar-state")
        self.assertTrue(inputs.source_statuses[0].ok)

    @patch("yule_orchestrator.planning.inputs.list_calendar_state_records")
    def test_collect_planning_inputs_emits_warning_when_state_is_empty(
        self,
        list_calendar_state_records_mock,
    ) -> None:
        list_calendar_state_records_mock.return_value = []

        inputs = collect_planning_inputs(
            plan_date=date(2026, 4, 23),
            include_calendar=True,
            include_github=False,
            reminders=[],
        )

        calendar_status = inputs.source_statuses[0]
        self.assertEqual(calendar_status.source_id, "calendar-state")
        self.assertFalse(calendar_status.ok)
        self.assertEqual(inputs.calendar_todos, [])
        self.assertEqual(inputs.calendar_events, [])
        self.assertTrue(any("calendar" in warning for warning in inputs.warnings))

    @patch("yule_orchestrator.planning.inputs.list_naver_calendar_items")
    @patch("yule_orchestrator.planning.inputs.list_calendar_state_records")
    def test_collect_planning_inputs_fetches_calendar_only_after_state_miss(
        self,
        list_calendar_state_records_mock,
        list_naver_calendar_items_mock,
    ) -> None:
        list_calendar_state_records_mock.return_value = []
        list_naver_calendar_items_mock.return_value = SimpleNamespace(events=[], todos=[])

        inputs = collect_planning_inputs(
            plan_date=date(2026, 4, 23),
            include_calendar=True,
            include_github=False,
            reminders=[],
            allow_live_calendar_fetch=True,
        )

        list_naver_calendar_items_mock.assert_called_once_with(date(2026, 4, 23), date(2026, 4, 23))
        self.assertEqual(inputs.source_statuses[0].source_id, "calendar-live")
        self.assertTrue(inputs.source_statuses[0].ok)

    @patch("yule_orchestrator.planning.inputs.list_naver_calendar_items")
    @patch("yule_orchestrator.planning.inputs.list_calendar_state_records")
    def test_collect_planning_inputs_preserves_calendar_live_fetch_error(
        self,
        list_calendar_state_records_mock,
        list_naver_calendar_items_mock,
    ) -> None:
        list_calendar_state_records_mock.return_value = []
        list_naver_calendar_items_mock.side_effect = build_calendar_error(
            code="network_timeout",
            category="network",
            message="calendar timeout",
            retry_strategy="backoff",
            retryable=True,
            recommended_retry_count=3,
            manual_action_required=False,
            alert_recommended=True,
            recovery_hint="retry later",
        )

        inputs = collect_planning_inputs(
            plan_date=date(2026, 4, 23),
            include_calendar=True,
            include_github=False,
            reminders=[],
            allow_live_calendar_fetch=True,
        )

        self.assertEqual(inputs.source_statuses[0].source_id, "calendar-live")
        self.assertFalse(inputs.source_statuses[0].ok)
        self.assertEqual(inputs.source_statuses[0].warning, "calendar timeout")
        self.assertIn("calendar: calendar timeout", inputs.warnings)

    def test_collect_planning_inputs_emits_warning_when_github_not_prefetched(self) -> None:
        inputs = collect_planning_inputs(
            plan_date=date(2026, 4, 23),
            include_calendar=False,
            include_github=True,
            reminders=[],
        )

        github_status = next(
            status for status in inputs.source_statuses if status.source_type == "github"
        )
        self.assertFalse(github_status.ok)
        self.assertEqual(inputs.github_issues, [])
        self.assertTrue(any("github" in warning for warning in inputs.warnings))

    @patch("yule_orchestrator.planning.inputs.list_open_issues")
    def test_collect_planning_inputs_preserves_github_live_fetch_error(self, list_open_issues_mock) -> None:
        list_open_issues_mock.side_effect = GitHubIssueError("gh auth expired")

        inputs = collect_planning_inputs(
            plan_date=date(2026, 4, 23),
            include_calendar=False,
            include_github=True,
            reminders=[],
            allow_live_github_fetch=True,
        )

        github_status = next(
            status for status in inputs.source_statuses if status.source_type == "github"
        )
        self.assertEqual(github_status.source_id, "github-issues-live")
        self.assertFalse(github_status.ok)
        self.assertEqual(github_status.warning, "gh auth expired")
        self.assertIn("github: gh auth expired", inputs.warnings)

    @patch("yule_orchestrator.planning.inputs.list_calendar_state_records")
    def test_collect_planning_inputs_uses_prefetched_sources(
        self,
        list_calendar_state_records_mock,
    ) -> None:
        list_calendar_state_records_mock.return_value = []
        prefetched_calendar_result = CalendarQueryResult(
            source="naver-caldav",
            start_date=date(2026, 4, 23),
            end_date=date(2026, 4, 23),
            events=[],
            todos=[],
            metrics={},
        )
        prefetched_github_issues = [
            GitHubIssue(
                number=1,
                repository="owner/repo",
                title="Issue title",
                url="https://github.com/owner/repo/issues/1",
                owner="owner",
                scope="personal",
            )
        ]

        inputs = collect_planning_inputs(
            plan_date=date(2026, 4, 23),
            include_calendar=True,
            include_github=True,
            reminders=[],
            prefetched_calendar_result=prefetched_calendar_result,
            prefetched_github_issues=prefetched_github_issues,
        )

        list_calendar_state_records_mock.assert_not_called()
        self.assertEqual(inputs.source_statuses[0].source_id, "calendar-prefetched")
        self.assertEqual(inputs.source_statuses[1].source_id, "github-issues-prefetched")
        self.assertEqual(len(inputs.github_issues), 1)
        self.assertEqual(inputs.warnings, [])
