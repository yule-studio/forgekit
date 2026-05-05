from __future__ import annotations

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from yule_orchestrator.cli.daily import run_daily_warmup_command


class DailyWarmupTestCase(unittest.TestCase):
    @patch("yule_orchestrator.cli.daily.save_runtime_metric_run")
    @patch("yule_orchestrator.cli.daily.save_daily_plan_snapshot")
    @patch("yule_orchestrator.cli.daily.build_daily_plan")
    @patch("yule_orchestrator.cli.daily.build_planning_inputs")
    @patch("yule_orchestrator.cli.daily.load_reminder_items")
    @patch("yule_orchestrator.cli.daily.list_open_issues")
    @patch("yule_orchestrator.cli.daily.list_naver_calendar_items")
    def test_daily_warmup_syncs_sources_and_saves_snapshot(
        self,
        list_naver_calendar_items_mock,
        list_open_issues_mock,
        load_reminder_items_mock,
        build_planning_inputs_mock,
        build_daily_plan_mock,
        save_daily_plan_snapshot_mock,
        save_runtime_metric_run_mock,
    ) -> None:
        list_naver_calendar_items_mock.return_value = SimpleNamespace(events=[object()], todos=[object()], metrics={})
        list_open_issues_mock.return_value = [object()]
        load_reminder_items_mock.return_value = []
        build_planning_inputs_mock.return_value = object()
        build_daily_plan_mock.return_value = SimpleNamespace(
            daily_plan=SimpleNamespace(
                summary=SimpleNamespace(recommended_task_count=3),
                checkpoints=[object(), object()],
            )
        )
        save_daily_plan_snapshot_mock.return_value = SimpleNamespace(
            cache_key="snapshot-key",
            generated_at=datetime.fromisoformat("2026-04-23T05:58:00+09:00"),
        )
        save_runtime_metric_run_mock.return_value = {
            "run_id": "daily-warmup:1",
            "steps": [],
        }

        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = run_daily_warmup_command(
                date_text="2026-04-23",
                github_limit=20,
                reminders_file=None,
                skip_calendar=False,
                skip_github=False,
                force_refresh=True,
                reminder_lead_minutes=5,
                json_output=True,
            )

        self.assertEqual(exit_code, 0)
        list_naver_calendar_items_mock.assert_called_once()
        self.assertTrue(list_naver_calendar_items_mock.call_args.kwargs["force_refresh"])
        list_open_issues_mock.assert_called_once_with(limit=20, force_refresh=True)
        self.assertEqual(
            build_planning_inputs_mock.call_args.kwargs["calendar_events"],
            list_naver_calendar_items_mock.return_value.events,
        )
        self.assertEqual(
            build_planning_inputs_mock.call_args.kwargs["calendar_todos"],
            list_naver_calendar_items_mock.return_value.todos,
        )
        self.assertIs(
            build_planning_inputs_mock.call_args.kwargs["github_issues"],
            list_open_issues_mock.return_value,
        )
        self.assertFalse(build_planning_inputs_mock.call_args.kwargs["warnings"])
        self.assertEqual(
            [status.source_id for status in build_planning_inputs_mock.call_args.kwargs["source_statuses"]],
            ["calendar-prefetched", "github-issues-prefetched"],
        )
        save_daily_plan_snapshot_mock.assert_called_once()
        save_runtime_metric_run_mock.assert_called_once()
        self.assertIn('"action": "daily_warmup"', stdout.getvalue())
        self.assertIn('"snapshot-key"', stdout.getvalue())

    @patch("yule_orchestrator.cli.daily.save_runtime_metric_run")
    @patch("yule_orchestrator.cli.daily.save_daily_plan_snapshot")
    @patch("yule_orchestrator.cli.daily.build_daily_plan")
    @patch("yule_orchestrator.cli.daily.build_planning_inputs")
    @patch("yule_orchestrator.cli.daily.load_reminder_items")
    @patch("yule_orchestrator.cli.daily.list_open_issues")
    @patch("yule_orchestrator.cli.daily.list_naver_calendar_items")
    def test_daily_warmup_passes_fetch_failures_into_planning_inputs(
        self,
        list_naver_calendar_items_mock,
        list_open_issues_mock,
        load_reminder_items_mock,
        build_planning_inputs_mock,
        build_daily_plan_mock,
        save_daily_plan_snapshot_mock,
        save_runtime_metric_run_mock,
    ) -> None:
        list_naver_calendar_items_mock.side_effect = RuntimeError("calendar fetch failed")
        list_open_issues_mock.return_value = []
        load_reminder_items_mock.return_value = []
        build_planning_inputs_mock.return_value = object()
        build_daily_plan_mock.return_value = SimpleNamespace(
            daily_plan=SimpleNamespace(
                summary=SimpleNamespace(recommended_task_count=0),
                checkpoints=[],
            )
        )
        save_daily_plan_snapshot_mock.return_value = SimpleNamespace(
            cache_key="snapshot-key",
            generated_at=datetime.fromisoformat("2026-04-23T05:58:00+09:00"),
        )
        save_runtime_metric_run_mock.return_value = {
            "run_id": "daily-warmup:2",
            "steps": [],
        }

        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = run_daily_warmup_command(
                date_text="2026-04-23",
                github_limit=20,
                reminders_file=None,
                skip_calendar=False,
                skip_github=False,
                force_refresh=False,
                reminder_lead_minutes=5,
                json_output=True,
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(build_planning_inputs_mock.call_args.kwargs["calendar_events"], [])
        self.assertEqual(build_planning_inputs_mock.call_args.kwargs["calendar_todos"], [])
        self.assertIn("calendar: calendar fetch failed", build_planning_inputs_mock.call_args.kwargs["warnings"])
        calendar_status = build_planning_inputs_mock.call_args.kwargs["source_statuses"][0]
        self.assertEqual(calendar_status.source_id, "calendar-prefetched")
        self.assertFalse(calendar_status.ok)
