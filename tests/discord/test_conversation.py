from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.discord.bot import _extract_conversation_prompt, _should_handle_message
from yule_orchestrator.discord.checkpoint_state import (
    CHECKPOINT_RESPONSE_STATUS_DONE,
    CHECKPOINT_RESPONSE_STATUS_SKIPPED,
    CheckpointPendingResponse,
)
from yule_orchestrator.discord.conversation import (
    build_conversation_response,
    build_conversation_response_envelope,
    detect_conversation_intent,
)
from yule_orchestrator.planning.ollama_config import OllamaConversationConfig


class DiscordConversationTestCase(unittest.TestCase):
    def test_should_handle_message_in_conversation_channel(self) -> None:
        message = _message(content="오늘 일정 브리핑 다시 해줘", channel_id=123, channel_name="planning", mentions=[])
        bot_user = _user(999, "yule-planning-bot")

        handled = _should_handle_message(
            message=message,
            bot_user=bot_user,
            conversation_channel_id=123,
            conversation_channel_name=None,
            conversation_reply_mode="plain-message-or-mention",
        )

        self.assertTrue(handled)

    def test_should_handle_message_when_channel_name_matches(self) -> None:
        message = _message(content="오늘 일정 브리핑 다시 해줘", channel_id=321, channel_name="planning-chat", mentions=[])
        bot_user = _user(999, "yule-planning-bot")

        handled = _should_handle_message(
            message=message,
            bot_user=bot_user,
            conversation_channel_id=None,
            conversation_channel_name="planning-chat",
            conversation_reply_mode="plain-message-or-mention",
        )

        self.assertTrue(handled)

    def test_should_handle_message_when_bot_is_mentioned(self) -> None:
        message = _message(
            content="<@999> 오늘 뭐부터 해야 해?",
            channel_id=555,
            channel_name="random",
            mentions=[_user(999, "yule-planning-bot")],
        )
        bot_user = _user(999, "yule-planning-bot")

        handled = _should_handle_message(
            message=message,
            bot_user=bot_user,
            conversation_channel_id=None,
            conversation_channel_name=None,
            conversation_reply_mode="mention-only",
        )

        self.assertTrue(handled)

    def test_should_handle_message_blocks_daily_channel_even_with_mention(self) -> None:
        message = _message(
            content="<@999> 브리핑 다시 해줘",
            channel_id=111,
            channel_name="daily-briefing",
            mentions=[_user(999, "yule-planning-bot")],
        )
        bot_user = _user(999, "yule-planning-bot")

        handled = _should_handle_message(
            message=message,
            bot_user=bot_user,
            conversation_channel_id=222,
            conversation_channel_name="planning-chat",
            conversation_reply_mode="plain-message-or-mention",
            daily_channel_id=111,
            daily_channel_name="daily-briefing",
        )

        self.assertFalse(handled, "DAILY 채널에서는 멘션이 있어도 응답하지 않아야 합니다.")

    def test_should_handle_message_allows_daily_channel_when_it_is_also_conversation(self) -> None:
        message = _message(
            content="브리핑 다시 해줘",
            channel_id=111,
            channel_name="daily-briefing",
            mentions=[],
        )
        bot_user = _user(999, "yule-planning-bot")

        handled = _should_handle_message(
            message=message,
            bot_user=bot_user,
            conversation_channel_id=111,
            conversation_channel_name="daily-briefing",
            conversation_reply_mode="plain-message-or-mention",
            daily_channel_id=111,
            daily_channel_name="daily-briefing",
        )

        self.assertTrue(handled, "CONVERSATION이 DAILY로 fallback된 경우엔 응답해야 합니다.")

    def test_should_handle_message_allows_chat_channel_with_plain_message(self) -> None:
        message = _message(
            content="오늘 뭐부터 해야 해?",
            channel_id=222,
            channel_name="planning-chat",
            mentions=[],
        )
        bot_user = _user(999, "yule-planning-bot")

        handled = _should_handle_message(
            message=message,
            bot_user=bot_user,
            conversation_channel_id=222,
            conversation_channel_name="planning-chat",
            conversation_reply_mode="plain-message-or-mention",
            daily_channel_id=111,
            daily_channel_name="daily-briefing",
        )

        self.assertTrue(handled, "CHAT 채널에서는 평문 메시지에도 응답해야 합니다.")

    def test_extract_conversation_prompt_removes_bot_mentions(self) -> None:
        message = _message(content="<@999> 오늘 뭐부터 해야 해?", channel_id=555, channel_name="chat", mentions=[])
        bot_user = _user(999, "yule-planning-bot")

        prompt = _extract_conversation_prompt(message=message, bot_user=bot_user)

        self.assertEqual(prompt, "오늘 뭐부터 해야 해?")

    def test_detect_conversation_intent_returns_schedule_change_proposal(self) -> None:
        intent = detect_conversation_intent("오후 일정 좀 뒤로 미루는 안 제안해줘")

        self.assertEqual(intent.intent_id, "schedule_change_proposal")
        self.assertTrue(intent.proposal_only)

    @patch("yule_orchestrator.discord.conversation.generate_ollama_text")
    @patch("yule_orchestrator.discord.conversation.load_ollama_conversation_config")
    @patch("yule_orchestrator.discord.conversation.load_plan_today_snapshot")
    def test_build_conversation_response_uses_ollama_for_priority_response(
        self,
        load_plan_today_snapshot_mock,
        load_ollama_conversation_config_mock,
        generate_ollama_text_mock,
    ) -> None:
        load_plan_today_snapshot_mock.return_value = _snapshot(
            generated_at="2026-04-24T09:00:00+09:00",
            prioritized_titles=["Discord bot 대화형 응답 붙이기", "체크포인트 정리"],
            checkpoint_times=["2026-04-24T09:55:00+09:00"],
            suggested_blocks=[("2026-04-24T10:00:00+09:00", "2026-04-24T11:00:00+09:00", "Discord bot 대화형 응답 붙이기")],
        )
        load_ollama_conversation_config_mock.return_value = OllamaConversationConfig(
            enabled=True,
            endpoint="http://localhost:11434",
            model="gemma3:latest",
            timeout_seconds=20,
        )
        generate_ollama_text_mock.return_value = "지금은 Discord bot 대화형 응답 붙이기부터 정리하면 좋겠습니다.\n\n그 다음 체크포인트는 09:55입니다."

        content = build_conversation_response(
            "오늘 뭐부터 해야 해?",
            author_user_id=777,
            reference_time=datetime.fromisoformat("2026-04-24T09:10:00+09:00"),
        )

        self.assertIn("Discord bot 대화형 응답 붙이기", content)
        self.assertNotIn("<@777>", content)
        generate_ollama_text_mock.assert_called_once()

    @patch("yule_orchestrator.discord.conversation.load_ollama_conversation_config")
    @patch("yule_orchestrator.discord.conversation.load_plan_today_snapshot")
    def test_build_conversation_response_returns_schedule_change_proposal_without_execution(
        self,
        load_plan_today_snapshot_mock,
        load_ollama_conversation_config_mock,
    ) -> None:
        load_plan_today_snapshot_mock.return_value = _snapshot(
            generated_at="2026-04-24T09:00:00+09:00",
            prioritized_titles=["mail-mail 동작 원리 정리"],
            checkpoint_times=["2026-04-24T09:55:00+09:00"],
            suggested_blocks=[],
        )
        load_ollama_conversation_config_mock.return_value = OllamaConversationConfig(
            enabled=False,
            endpoint="http://localhost:11434",
            model="gemma3:latest",
            timeout_seconds=20,
        )

        content = build_conversation_response(
            "오후 일정 좀 뒤로 미루는 안 제안해줘",
            author_user_id=777,
            reference_time=datetime.fromisoformat("2026-04-24T09:10:00+09:00"),
        )

        self.assertIn("제안:", content)
        self.assertIn("승인 전 메모:", content)
        self.assertIn("아직 실제 일정이나 상태는 변경하지 않았습니다.", content)

    @patch("yule_orchestrator.discord.conversation.load_ollama_conversation_config")
    @patch("yule_orchestrator.discord.conversation.load_plan_today_snapshot")
    @patch("yule_orchestrator.discord.conversation.build_due_checkpoints")
    def test_build_conversation_response_returns_checkpoint_summary(
        self,
        build_due_checkpoints_mock,
        load_plan_today_snapshot_mock,
        load_ollama_conversation_config_mock,
    ) -> None:
        load_plan_today_snapshot_mock.return_value = None
        load_ollama_conversation_config_mock.return_value = OllamaConversationConfig(
            enabled=False,
            endpoint="http://localhost:11434",
            model="gemma3:latest",
            timeout_seconds=20,
        )
        build_due_checkpoints_mock.return_value = [
            _checkpoint("2026-04-24T09:55:00+09:00", "업무 수행 마무리 확인")
        ]

        content = build_conversation_response(
            "다음 체크포인트 알려줘",
            author_user_id=777,
            reference_time=datetime.fromisoformat("2026-04-24T09:10:00+09:00"),
        )

        self.assertIn("09:55", content)
        self.assertIn("업무 수행 마무리 확인", content)

    @patch("yule_orchestrator.discord.conversation.load_ollama_conversation_config")
    @patch("yule_orchestrator.discord.conversation.load_plan_today_snapshot")
    def test_envelope_signals_regeneration_when_snapshot_missing_for_briefing_intent(
        self,
        load_plan_today_snapshot_mock,
        load_ollama_conversation_config_mock,
    ) -> None:
        load_plan_today_snapshot_mock.return_value = None
        load_ollama_conversation_config_mock.return_value = OllamaConversationConfig(
            enabled=False,
            endpoint="http://localhost:11434",
            model="gemma3:latest",
            timeout_seconds=20,
        )

        envelope = build_conversation_response_envelope(
            "오늘 브리핑 다시 정리해줘",
            author_user_id=777,
            reference_time=datetime.fromisoformat("2026-04-24T09:10:00+09:00"),
        )

        self.assertTrue(envelope.regenerate_snapshot)
        self.assertEqual(envelope.intent_id, "briefing_refresh")
        self.assertIn("브리핑 데이터를 준비하고 있습니다", envelope.content)

    @patch("yule_orchestrator.discord.conversation.save_json_cache")
    @patch("yule_orchestrator.discord.conversation.load_ollama_conversation_config")
    @patch("yule_orchestrator.discord.conversation.load_plan_today_snapshot")
    def test_checkpoint_lookup_without_due_items_asks_for_yes_no_confirmation(
        self,
        load_plan_today_snapshot_mock,
        load_ollama_conversation_config_mock,
        save_json_cache_mock,
    ) -> None:
        load_plan_today_snapshot_mock.return_value = _snapshot(
            generated_at="2026-04-24T09:00:00+09:00",
            prioritized_titles=["mail-mail 동작 원리 정리"],
            checkpoint_times=[],
            suggested_blocks=[],
        )
        load_ollama_conversation_config_mock.return_value = OllamaConversationConfig(
            enabled=False,
            endpoint="http://localhost:11434",
            model="gemma3:latest",
            timeout_seconds=20,
        )

        content = build_conversation_response(
            "다음 체크포인트 알려줘",
            author_user_id=777,
            conversation_scope="guild:1:channel:2",
            reference_time=datetime.fromisoformat("2026-04-24T11:45:00+09:00"),
        )

        self.assertIn("yes", content.lower())
        self.assertIn("no", content.lower())
        save_json_cache_mock.assert_called()

    @patch("yule_orchestrator.discord.conversation.clear_checkpoint_pending_response")
    @patch("yule_orchestrator.discord.conversation.mark_checkpoint_responded")
    @patch("yule_orchestrator.discord.conversation.load_checkpoint_pending_response")
    def test_yes_reply_marks_pending_checkpoints_as_done(
        self,
        load_pending_mock,
        mark_responded_mock,
        clear_pending_mock,
    ) -> None:
        plan_date = datetime.fromisoformat("2026-04-24T00:00:00+09:00").date()
        load_pending_mock.return_value = CheckpointPendingResponse(
            user_id=777,
            plan_date=plan_date,
            channel_id=42,
            checkpoint_ids=("cp-1", "cp-2"),
            sent_at=datetime.fromisoformat("2026-04-24T09:55:00+09:00"),
        )

        envelope = build_conversation_response_envelope(
            "완료",
            author_user_id=777,
            reference_time=datetime.fromisoformat("2026-04-24T10:00:00+09:00"),
        )

        self.assertEqual(envelope.intent_id, "checkpoint_response")
        self.assertIn("완료", envelope.content)
        self.assertEqual(mark_responded_mock.call_count, 2)
        statuses = {call.kwargs["status"] for call in mark_responded_mock.call_args_list}
        self.assertEqual(statuses, {CHECKPOINT_RESPONSE_STATUS_DONE})
        ids = {call.kwargs["checkpoint_id"] for call in mark_responded_mock.call_args_list}
        self.assertEqual(ids, {"cp-1", "cp-2"})
        clear_pending_mock.assert_called_once_with(user_id=777)

    @patch("yule_orchestrator.discord.conversation.clear_checkpoint_pending_response")
    @patch("yule_orchestrator.discord.conversation.mark_checkpoint_responded")
    @patch("yule_orchestrator.discord.conversation.load_checkpoint_pending_response")
    def test_skip_reply_marks_pending_checkpoints_as_skipped(
        self,
        load_pending_mock,
        mark_responded_mock,
        clear_pending_mock,
    ) -> None:
        plan_date = datetime.fromisoformat("2026-04-24T00:00:00+09:00").date()
        load_pending_mock.return_value = CheckpointPendingResponse(
            user_id=777,
            plan_date=plan_date,
            channel_id=42,
            checkpoint_ids=("cp-1",),
            sent_at=datetime.fromisoformat("2026-04-24T09:55:00+09:00"),
        )

        envelope = build_conversation_response_envelope(
            "건너뛰기",
            author_user_id=777,
            reference_time=datetime.fromisoformat("2026-04-24T10:00:00+09:00"),
        )

        self.assertEqual(envelope.intent_id, "checkpoint_response")
        self.assertIn("건너뛰기", envelope.content)
        mark_responded_mock.assert_called_once()
        self.assertEqual(
            mark_responded_mock.call_args.kwargs["status"],
            CHECKPOINT_RESPONSE_STATUS_SKIPPED,
        )
        clear_pending_mock.assert_called_once_with(user_id=777)

    @patch("yule_orchestrator.discord.conversation.load_ollama_conversation_config")
    @patch("yule_orchestrator.discord.conversation.load_plan_today_snapshot")
    @patch("yule_orchestrator.discord.conversation.mark_checkpoint_responded")
    @patch("yule_orchestrator.discord.conversation.load_checkpoint_pending_response")
    def test_yes_reply_falls_through_when_no_pending_checkpoint(
        self,
        load_pending_mock,
        mark_responded_mock,
        load_plan_today_snapshot_mock,
        load_ollama_conversation_config_mock,
    ) -> None:
        load_pending_mock.return_value = None
        load_plan_today_snapshot_mock.return_value = None
        load_ollama_conversation_config_mock.return_value = OllamaConversationConfig(
            enabled=False,
            endpoint="http://localhost:11434",
            model="gemma3:latest",
            timeout_seconds=20,
        )

        envelope = build_conversation_response_envelope(
            "yes",
            author_user_id=777,
            reference_time=datetime.fromisoformat("2026-04-24T10:00:00+09:00"),
        )

        self.assertNotEqual(envelope.intent_id, "checkpoint_response")
        mark_responded_mock.assert_not_called()

    @patch("yule_orchestrator.discord.conversation.clear_checkpoint_pending_response")
    @patch("yule_orchestrator.discord.conversation.mark_checkpoint_responded")
    @patch("yule_orchestrator.discord.conversation.load_checkpoint_pending_response")
    def test_done_replies_recognize_korean_and_chat_slang_variants(
        self,
        load_pending_mock,
        mark_responded_mock,
        clear_pending_mock,
    ) -> None:
        plan_date = datetime.fromisoformat("2026-04-24T00:00:00+09:00").date()
        for reply in ["넵", "응", "ㅇㅇ", "ok", "OK", "Yeah!", "완료요", "끝났어요", "  done  "]:
            load_pending_mock.reset_mock()
            mark_responded_mock.reset_mock()
            clear_pending_mock.reset_mock()
            load_pending_mock.return_value = CheckpointPendingResponse(
                user_id=777,
                plan_date=plan_date,
                channel_id=42,
                checkpoint_ids=("cp-1",),
                sent_at=datetime.fromisoformat("2026-04-24T09:55:00+09:00"),
            )

            envelope = build_conversation_response_envelope(
                reply,
                author_user_id=777,
                reference_time=datetime.fromisoformat("2026-04-24T10:00:00+09:00"),
            )

            self.assertEqual(envelope.intent_id, "checkpoint_response", f"reply={reply!r}")
            mark_responded_mock.assert_called_once()
            self.assertEqual(
                mark_responded_mock.call_args.kwargs["status"],
                CHECKPOINT_RESPONSE_STATUS_DONE,
                f"reply={reply!r}",
            )

    @patch("yule_orchestrator.discord.conversation.clear_checkpoint_pending_response")
    @patch("yule_orchestrator.discord.conversation.mark_checkpoint_responded")
    @patch("yule_orchestrator.discord.conversation.load_checkpoint_pending_response")
    def test_skip_replies_recognize_korean_and_chat_slang_variants(
        self,
        load_pending_mock,
        mark_responded_mock,
        clear_pending_mock,
    ) -> None:
        plan_date = datetime.fromisoformat("2026-04-24T00:00:00+09:00").date()
        for reply in ["아니야", "ㄴㄴ", "Nope.", "스킵", "패스", "  skip!  "]:
            load_pending_mock.reset_mock()
            mark_responded_mock.reset_mock()
            clear_pending_mock.reset_mock()
            load_pending_mock.return_value = CheckpointPendingResponse(
                user_id=777,
                plan_date=plan_date,
                channel_id=42,
                checkpoint_ids=("cp-1",),
                sent_at=datetime.fromisoformat("2026-04-24T09:55:00+09:00"),
            )

            envelope = build_conversation_response_envelope(
                reply,
                author_user_id=777,
                reference_time=datetime.fromisoformat("2026-04-24T10:00:00+09:00"),
            )

            self.assertEqual(envelope.intent_id, "checkpoint_response", f"reply={reply!r}")
            mark_responded_mock.assert_called_once()
            self.assertEqual(
                mark_responded_mock.call_args.kwargs["status"],
                CHECKPOINT_RESPONSE_STATUS_SKIPPED,
                f"reply={reply!r}",
            )


def _user(user_id: int, name: str):
    class User:
        def __init__(self, value: int, text: str) -> None:
            self.id = value
            self.name = text

    return User(user_id, name)


def _message(*, content: str, channel_id: int, channel_name: str, mentions: list[object]):
    class Channel:
        def __init__(self, value: int, name: str) -> None:
            self.id = value
            self.name = name
            self.parent = None
            self.parent_id = None

    class Message:
        def __init__(self) -> None:
            self.content = content
            self.channel = Channel(channel_id, channel_name)
            self.mentions = mentions

    return Message()


def _snapshot(
    *,
    generated_at: str,
    prioritized_titles: list[str],
    checkpoint_times: list[str],
    suggested_blocks: list[tuple[str, str, str]],
):
    class Task:
        def __init__(self, title: str) -> None:
            self.title = title
            self.priority_level = "high"
            self.due_date = "2026-04-24"

    class Checkpoint:
        def __init__(self, remind_at: str) -> None:
            self.remind_at = remind_at
            self.prompt = "체크포인트"

    class Block:
        def __init__(self, start: str, end: str, title: str) -> None:
            self.start = start
            self.end = end
            self.title = title

    class DailyPlan:
        def __init__(self) -> None:
            self.plan_date = datetime.fromisoformat("2026-04-24T00:00:00+09:00").date()
            self.discord_briefing = "테스트용 오늘 브리핑"
            self.morning_briefing = "테스트용 아침 브리핑"
            self.prioritized_tasks = [Task(title) for title in prioritized_titles]
            self.checkpoints = [Checkpoint(value) for value in checkpoint_times]
            self.suggested_time_blocks = [Block(start, end, title) for start, end, title in suggested_blocks]
            self.time_block_briefings = []

    class Envelope:
        def __init__(self) -> None:
            self.daily_plan = DailyPlan()

    class Snapshot:
        def __init__(self) -> None:
            self.generated_at = datetime.fromisoformat(generated_at)
            self.envelope = Envelope()
            self.is_stale = False

    return Snapshot()


def _checkpoint(remind_at: str, prompt: str):
    class Checkpoint:
        def __init__(self) -> None:
            self.remind_at = remind_at
            self.prompt = prompt

    return Checkpoint()
