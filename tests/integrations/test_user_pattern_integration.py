from __future__ import annotations

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from datetime import date
import unittest
from unittest.mock import patch

from yule_orchestrator.integrations.calendar.models import CalendarTodo
from yule_orchestrator.planning.models import PlanningInputs, PlanningSourceStatus
from yule_orchestrator.planning.tasks import build_task_candidates
from yule_orchestrator.storage.task_history import UserPatternSignals


def _todo_inputs(title: str, *, plan_date: date) -> PlanningInputs:
    return PlanningInputs(
        plan_date=plan_date,
        timezone="KST",
        source_statuses=[
            PlanningSourceStatus(source_id="calendar", source_type="calendar", ok=True, item_count=1),
        ],
        warnings=[],
        calendar_events=[],
        calendar_todos=[
            CalendarTodo(
                item_uid="todo-1",
                title=title,
                start=None,
                due=plan_date.isoformat(),
                start_all_day=False,
                due_all_day=True,
                status="NEEDS-ACTION",
                completed=False,
                completed_at=None,
                priority=0,
                percent_complete=None,
                calendar_name="내 할 일",
                source="naver-caldav",
                description="",
                last_modified=None,
            )
        ],
        github_issues=[],
        reminders=[],
    )


class UserPatternIntegrationTestCase(unittest.TestCase):
    @patch("yule_orchestrator.planning.tasks.compute_user_pattern_signals_batch")
    def test_skip_heavy_history_lowers_priority_score(self, signals_mock) -> None:
        signals_mock.return_value = {
            "PR 리뷰": UserPatternSignals(
                source_event_title="PR 리뷰",
                total_count=4,
                done_count=1,
                skipped_count=3,
                typical_block_minutes=None,
            )
        }

        inputs = _todo_inputs("PR 리뷰", plan_date=date(2026, 4, 22))
        candidates = build_task_candidates(inputs)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertTrue(any("건너뛴 패턴" in reason for reason in candidate.reasons))
        # baseline todo "due today" score is 75 (40 + 35); penalty subtracts ~11 (15 * 0.75)
        self.assertLess(candidate.priority_score, 75)

    @patch("yule_orchestrator.planning.tasks.compute_user_pattern_signals_batch")
    def test_done_heavy_history_raises_priority_score(self, signals_mock) -> None:
        signals_mock.return_value = {
            "회고 정리": UserPatternSignals(
                source_event_title="회고 정리",
                total_count=5,
                done_count=4,
                skipped_count=1,
                typical_block_minutes=None,
            )
        }

        inputs = _todo_inputs("회고 정리", plan_date=date(2026, 4, 22))
        candidates = build_task_candidates(inputs)

        candidate = candidates[0]
        self.assertTrue(any("완료한 패턴" in reason for reason in candidate.reasons))
        self.assertGreaterEqual(candidate.priority_score, 80)

    @patch("yule_orchestrator.planning.tasks.compute_user_pattern_signals_batch")
    def test_typical_block_minutes_overrides_default_estimated_minutes(self, signals_mock) -> None:
        signals_mock.return_value = {
            "설계 검토": UserPatternSignals(
                source_event_title="설계 검토",
                total_count=3,
                done_count=3,
                skipped_count=0,
                typical_block_minutes=90,
            )
        }

        inputs = _todo_inputs("설계 검토", plan_date=date(2026, 4, 22))
        candidates = build_task_candidates(inputs)

        self.assertEqual(candidates[0].estimated_minutes, 90)
        self.assertTrue(any("평소" in reason and "90분" in reason for reason in candidates[0].reasons))

    @patch("yule_orchestrator.planning.tasks.compute_user_pattern_signals_batch")
    def test_low_history_count_does_not_apply_pattern_signals(self, signals_mock) -> None:
        signals_mock.return_value = {
            "설계 검토": UserPatternSignals(
                source_event_title="설계 검토",
                total_count=1,
                done_count=0,
                skipped_count=1,
                typical_block_minutes=120,
            )
        }

        inputs = _todo_inputs("설계 검토", plan_date=date(2026, 4, 22))
        candidates = build_task_candidates(inputs)

        candidate = candidates[0]
        self.assertEqual(candidate.estimated_minutes, 60)
        self.assertFalse(any("패턴" in reason for reason in candidate.reasons))


if __name__ == "__main__":
    unittest.main()
