from __future__ import annotations

import os
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.storage import (
    TaskCompletionEvent,
    compute_user_pattern_signals,
    query_task_completion_stats,
    record_task_completion_event,
)


class TaskHistoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path("tests/.tmp/task-history-tests")
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)
        try:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
        except (FileNotFoundError, PermissionError) as exc:
            self.skipTest(f"temporary directory is not writable: {exc}")
        self.db_path = self.temp_dir / "cache.sqlite3"
        self.previous_db_path = os.environ.get("YULE_CACHE_DB_PATH")
        os.environ["YULE_CACHE_DB_PATH"] = str(self.db_path)

    def tearDown(self) -> None:
        if self.previous_db_path is None:
            os.environ.pop("YULE_CACHE_DB_PATH", None)
        else:
            os.environ["YULE_CACHE_DB_PATH"] = self.previous_db_path
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_record_and_query_done_count(self) -> None:
        plan_date = date(2026, 4, 24)
        for index in range(3):
            record_task_completion_event(
                TaskCompletionEvent(
                    plan_date=plan_date,
                    checkpoint_id=f"cp-{index}",
                    status="done",
                    user_id=777,
                    responded_at=datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc),
                )
            )

        record_task_completion_event(
            TaskCompletionEvent(
                plan_date=plan_date,
                checkpoint_id="cp-3",
                status="skipped",
                user_id=777,
                responded_at=datetime(2026, 4, 24, 11, 0, tzinfo=timezone.utc),
            )
        )

        stats = query_task_completion_stats(
            user_id=777,
            reference_time=datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(stats.total_count, 4)
        self.assertEqual(stats.done_count, 3)
        self.assertEqual(stats.skipped_count, 1)
        self.assertAlmostEqual(stats.done_ratio, 0.75)

    def test_query_filters_by_source_event_title(self) -> None:
        plan_date = date(2026, 4, 24)
        record_task_completion_event(
            TaskCompletionEvent(
                plan_date=plan_date,
                checkpoint_id="cp-1",
                status="done",
                user_id=777,
                responded_at=datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc),
                source_event_title="업무 수행",
            )
        )
        record_task_completion_event(
            TaskCompletionEvent(
                plan_date=plan_date,
                checkpoint_id="cp-2",
                status="skipped",
                user_id=777,
                responded_at=datetime(2026, 4, 24, 11, 0, tzinfo=timezone.utc),
                source_event_title="회의",
            )
        )

        work_stats = query_task_completion_stats(
            source_event_title="업무 수행",
            reference_time=datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(work_stats.total_count, 1)
        self.assertEqual(work_stats.done_count, 1)

    def test_query_excludes_events_outside_days_back_window(self) -> None:
        record_task_completion_event(
            TaskCompletionEvent(
                plan_date=date(2025, 1, 1),
                checkpoint_id="cp-old",
                status="done",
                user_id=777,
                responded_at=datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc),
            )
        )
        record_task_completion_event(
            TaskCompletionEvent(
                plan_date=date(2026, 4, 20),
                checkpoint_id="cp-recent",
                status="done",
                user_id=777,
                responded_at=datetime(2026, 4, 20, 10, 0, tzinfo=timezone.utc),
            )
        )

        recent_stats = query_task_completion_stats(
            days_back=30,
            reference_time=datetime(2026, 4, 25, 0, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(recent_stats.total_count, 1)
        self.assertEqual(recent_stats.done_count, 1)

    def test_query_returns_zero_stats_when_database_missing(self) -> None:
        # Remove the database so the query path runs against a non-existent file.
        if self.db_path.exists():
            self.db_path.unlink()

        stats = query_task_completion_stats(user_id=777)

        self.assertEqual(stats.total_count, 0)
        self.assertEqual(stats.done_ratio, 0.0)

    def test_compute_user_pattern_signals_returns_skip_ratio_and_typical_minutes(self) -> None:
        plan_date = date(2026, 4, 24)
        # Two skipped events for "PR 리뷰"
        for index in range(2):
            record_task_completion_event(
                TaskCompletionEvent(
                    plan_date=plan_date,
                    checkpoint_id=f"cp-skip-{index}",
                    status="skipped",
                    user_id=777,
                    responded_at=datetime(2026, 4, 24, 11, 0, tzinfo=timezone.utc),
                    source_event_title="PR 리뷰",
                    block_minutes=60,
                )
            )
        # Three done events with 90-minute blocks
        for index in range(3):
            record_task_completion_event(
                TaskCompletionEvent(
                    plan_date=plan_date,
                    checkpoint_id=f"cp-done-{index}",
                    status="done",
                    user_id=777,
                    responded_at=datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc),
                    source_event_title="PR 리뷰",
                    block_minutes=90,
                )
            )

        signals = compute_user_pattern_signals(
            source_event_title="PR 리뷰",
            user_id=777,
            reference_time=datetime(2026, 4, 25, 0, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(signals.total_count, 5)
        self.assertEqual(signals.done_count, 3)
        self.assertEqual(signals.skipped_count, 2)
        self.assertAlmostEqual(signals.skip_ratio, 0.4)
        self.assertAlmostEqual(signals.done_ratio, 0.6)
        self.assertEqual(signals.typical_block_minutes, 90)

    def test_compute_user_pattern_signals_returns_empty_when_no_history(self) -> None:
        signals = compute_user_pattern_signals(source_event_title="never seen")
        self.assertEqual(signals.total_count, 0)
        self.assertIsNone(signals.typical_block_minutes)


if __name__ == "__main__":
    unittest.main()
