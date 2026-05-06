from __future__ import annotations

import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.discord.bot import (
    _ENGINEERING_LAST_PROPOSED,
    _ENGINEERING_LAST_RESEARCH_CONTEXT,
    _checkpoint_window_minutes,
    _default_engineering_conversation_fn,
    _filter_unsent_checkpoints,
    _mark_checkpoints_sent,
    _next_checkpoint_scan,
    _resolve_due_checkpoints,
)
from yule_orchestrator.discord.planning_runtime import (
    build_due_checkpoints,
    load_prefetched_due_checkpoints,
    prefetch_checkpoint_snapshots,
)
from yule_orchestrator.planning.models import PlanningCheckpoint


class DiscordBotRuntimeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path("tests/.tmp/discord-bot-runtime")
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)
        try:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
        except (FileNotFoundError, PermissionError) as exc:
            self.skipTest(f"temporary directory is not writable in this environment: {exc}")
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

    def test_next_checkpoint_scan_rounds_up_to_next_minute(self) -> None:
        current = self._dt("2026-04-22T09:55:12+09:00")

        next_scan = _next_checkpoint_scan(current)

        self.assertEqual(next_scan, self._dt("2026-04-22T09:56:00+09:00"))

    def test_checkpoint_window_minutes_rounds_partial_minute_up(self) -> None:
        start = self._dt("2026-04-22T09:55:00+09:00")
        end = self._dt("2026-04-22T09:56:01+09:00")

        self.assertEqual(_checkpoint_window_minutes(start, end), 2)

    def test_filter_unsent_checkpoints_uses_local_cache(self) -> None:
        first = self._checkpoint("checkpoint-1", "2026-04-22T09:55:00+09:00")
        second = self._checkpoint("checkpoint-2", "2026-04-22T10:55:00+09:00")
        _mark_checkpoints_sent(555, [first])

        unsent = _filter_unsent_checkpoints(555, [first, second])

        self.assertEqual([checkpoint.checkpoint_id for checkpoint in unsent], ["checkpoint-2"])

    @patch("yule_orchestrator.discord.planning_runtime.load_daily_plan_snapshot")
    def test_build_due_checkpoints_scans_across_midnight(
        self,
        load_daily_plan_snapshot_mock,
    ) -> None:
        first_day = self._checkpoint("checkpoint-1", "2026-04-22T23:59:00+09:00")
        second_day = self._checkpoint("checkpoint-2", "2026-04-23T00:01:00+09:00")

        load_daily_plan_snapshot_mock.side_effect = [
            self._snapshot([first_day]),
            self._snapshot([second_day]),
        ]

        due = build_due_checkpoints(
            self._dt("2026-04-22T23:58:00+09:00"),
            window_minutes=5,
        )

        self.assertEqual(
            [checkpoint.checkpoint_id for checkpoint in due],
            ["checkpoint-1", "checkpoint-2"],
        )
        self.assertEqual(load_daily_plan_snapshot_mock.call_count, 2)

    @patch("yule_orchestrator.discord.bot.build_due_checkpoints")
    @patch("yule_orchestrator.discord.bot.load_prefetched_due_checkpoints")
    def test_resolve_due_checkpoints_prefers_prefetched_snapshots(
        self,
        load_prefetched_due_checkpoints_mock,
        build_due_checkpoints_mock,
    ) -> None:
        checkpoint = self._checkpoint("checkpoint-1", "2026-04-22T09:55:00+09:00")
        load_prefetched_due_checkpoints_mock.return_value = ([checkpoint], True)

        resolved = _resolve_due_checkpoints(
            self._dt("2026-04-22T09:54:00+09:00"),
            self._dt("2026-04-22T09:55:00+09:00"),
        )

        self.assertEqual([item.checkpoint_id for item in resolved], ["checkpoint-1"])
        build_due_checkpoints_mock.assert_not_called()

    @patch("yule_orchestrator.discord.planning_runtime.build_daily_checkpoints_for_date")
    def test_prefetch_checkpoint_snapshots_can_be_loaded_without_live_fetch(
        self,
        build_daily_checkpoints_for_date_mock,
    ) -> None:
        build_daily_checkpoints_for_date_mock.return_value = [
            self._checkpoint("checkpoint-1", "2026-04-22T09:55:00+09:00")
        ]

        prefetch_checkpoint_snapshots(
            self._dt("2026-04-22T09:50:00+09:00"),
            prefetch_minutes=5,
        )
        loaded, cache_complete = load_prefetched_due_checkpoints(
            self._dt("2026-04-22T09:54:00+09:00"),
            self._dt("2026-04-22T09:55:00+09:00"),
        )

        self.assertTrue(cache_complete)
        self.assertEqual([item.checkpoint_id for item in loaded], ["checkpoint-1"])

    @staticmethod
    def _dt(value: str):
        from datetime import datetime

        return datetime.fromisoformat(value)

    @staticmethod
    def _checkpoint(checkpoint_id: str, remind_at: str) -> PlanningCheckpoint:
        return PlanningCheckpoint(
            checkpoint_id=checkpoint_id,
            remind_at=remind_at,
            source_event_uid="event-1",
            source_event_title="업무 수행",
            block_id="block-1",
            block_title="업무 수행",
            block_start="2026-04-22T09:00:00+09:00",
            block_end="2026-04-22T10:00:00+09:00",
            prompt="업무 수행 마무리됐는지 확인해 주세요.",
        )

    @staticmethod
    def _snapshot(checkpoints: list[PlanningCheckpoint]):
        class DailyPlan:
            def __init__(self, due_checkpoints: list[PlanningCheckpoint]) -> None:
                self.checkpoints = due_checkpoints

        class Envelope:
            def __init__(self, due_checkpoints: list[PlanningCheckpoint]) -> None:
                self.daily_plan = DailyPlan(due_checkpoints)

        class Snapshot:
            def __init__(self, due_checkpoints: list[PlanningCheckpoint]) -> None:
                self.envelope = Envelope(due_checkpoints)

        return Snapshot(checkpoints)


class EngineeringConversationBridgeTestCase(unittest.TestCase):
    @patch(
        "yule_orchestrator.discord.engineering_conversation."
        "build_engineering_conversation_response"
    )
    def test_default_bridge_accepts_and_forwards_research_context(
        self,
        build_response_mock,
    ) -> None:
        self.addCleanup(_ENGINEERING_LAST_PROPOSED.pop, 999, None)
        self.addCleanup(_ENGINEERING_LAST_RESEARCH_CONTEXT.pop, 999, None)
        pack = object()
        collection = object()
        build_response_mock.return_value = SimpleNamespace(
            content="좋아요. 먼저 1차 자료를 모아볼게요.",
            intent_id="task_intake_candidate",
            ready_to_intake=False,
            intake_prompt="Obsidian memory 설계",
            write_likely=False,
            research_pack=pack,
            collection_outcome=collection,
        )

        outcome = _default_engineering_conversation_fn(
            message_text="Obsidian memory 설계",
            author_user_id=4242,
            channel_id=999,
            bot_user=object(),
            attachments=("image.png",),
            user_links=("https://example.com/ref",),
            auto_collect=True,
            role_for_research="engineering-agent/product-designer",
            session_id="sess-1",
        )

        build_response_mock.assert_called_once()
        _, kwargs = build_response_mock.call_args
        self.assertEqual(kwargs["user_attachments"], ("image.png",))
        self.assertEqual(kwargs["user_links"], ("https://example.com/ref",))
        self.assertTrue(kwargs["auto_collect"])
        self.assertEqual(
            kwargs["role_for_research"],
            "engineering-agent/product-designer",
        )
        self.assertEqual(kwargs["session_id"], "sess-1")
        self.assertIs(outcome.research_pack, pack)
        self.assertIs(outcome.collection_outcome, collection)
        self.assertEqual(
            outcome.role_for_research,
            "engineering-agent/product-designer",
        )

    @patch(
        "yule_orchestrator.discord.engineering_conversation."
        "build_engineering_conversation_response"
    )
    def test_default_bridge_reuses_research_context_on_confirmation(
        self,
        build_response_mock,
    ) -> None:
        pack = object()
        collection = object()
        _ENGINEERING_LAST_PROPOSED[999] = "Obsidian memory 설계"
        _ENGINEERING_LAST_RESEARCH_CONTEXT[999] = {
            "intake_prompt": "Obsidian memory 설계",
            "research_pack": pack,
            "collection_outcome": collection,
            "role_for_research": "engineering-agent/tech-lead",
        }
        self.addCleanup(_ENGINEERING_LAST_PROPOSED.pop, 999, None)
        self.addCleanup(_ENGINEERING_LAST_RESEARCH_CONTEXT.pop, 999, None)
        build_response_mock.return_value = SimpleNamespace(
            content="좋습니다. 이대로 작업을 등록할게요.",
            intent_id="confirm_intake",
            ready_to_intake=True,
            intake_prompt="Obsidian memory 설계",
            write_likely=False,
            research_pack=None,
            collection_outcome=None,
        )

        outcome = _default_engineering_conversation_fn(
            message_text="이대로 진행",
            author_user_id=4242,
            channel_id=999,
            bot_user=object(),
        )

        self.assertIs(outcome.research_pack, pack)
        self.assertIs(outcome.collection_outcome, collection)
        self.assertEqual(outcome.role_for_research, "engineering-agent/tech-lead")
        self.assertNotIn(999, _ENGINEERING_LAST_PROPOSED)
        self.assertNotIn(999, _ENGINEERING_LAST_RESEARCH_CONTEXT)

    @patch(
        "yule_orchestrator.discord.engineering_conversation."
        "build_engineering_conversation_response"
    )
    def test_default_bridge_keeps_last_prompt_for_existing_thread_retry(
        self,
        build_response_mock,
    ) -> None:
        _ENGINEERING_LAST_PROPOSED[999] = "새로 등록하지 말고 기존 스레드에서 이어가줘"
        self.addCleanup(_ENGINEERING_LAST_PROPOSED.pop, 999, None)
        self.addCleanup(_ENGINEERING_LAST_RESEARCH_CONTEXT.pop, 999, None)
        build_response_mock.return_value = SimpleNamespace(
            content="기존 thread를 찾아 이어갈게요.",
            intent_id="confirm_intake",
            ready_to_intake=True,
            intake_prompt="새로 등록하지 말고 기존 스레드에서 이어가줘",
            write_likely=False,
            research_pack=None,
            collection_outcome=None,
        )

        _default_engineering_conversation_fn(
            message_text="이대로 진행",
            author_user_id=4242,
            channel_id=999,
            bot_user=object(),
        )

        self.assertEqual(
            _ENGINEERING_LAST_PROPOSED[999],
            "새로 등록하지 말고 기존 스레드에서 이어가줘",
        )
