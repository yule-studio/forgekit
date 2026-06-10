"""P0-K e2e — command-only operational phrase must never spawn new research/forum (#148).

User-reported scenarios:

  1. "진행 해줘" on existing session — no new intake, no new thread,
     no _run_research_loop_hook, canonical prompt 유지.
  2. "작업 승인 할게 진행 해줘" — approval/proceed, no new forum thread.
  3. "이대로 진행" — no "[Reference] 이대로 진행" thread.
  4. "세션 <id> 계속 진행해" — existing session continue, no new research loop.
  5. Genuine direction update — same-session direction update **allowed**.
  6. Resumed thread role-change — session resolve via resumed_thread_id.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.routing import (
    is_command_only_prompt,
    is_non_actionable_prompt,
)
from yule_discord.engineering_channel_router import (
    _research_loop_blocked_by_command_only,
)
from yule_discord.engineering_conversation import (
    APPROVAL_ACTION,
    CONFIRM_INTAKE,
    TASK_INTAKE_CANDIDATE,
    build_engineering_conversation_response,
)
from yule_discord.forum.message_adapter import (
    _resolve_session_for_forum_thread,
)
from yule_discord.research_forum import (
    derive_research_topic,
)


# ---------------------------------------------------------------------------
# Phrase classification
# ---------------------------------------------------------------------------


class CommandOnlyPhraseTests(unittest.TestCase):
    """The expanded phrase set catches every user-reported example."""

    def test_user_examples_are_command_only(self) -> None:
        for text in (
            "진행 해줘",
            "작업 승인 할게 진행 해줘",
            "이대로 진행",
            "승인하고 진행해",
            "계속 해",
            "이어서 해",
            "오케이 진행",
            "승인할게",
            "새 작업으로 진행",
        ):
            with self.subTest(text=text):
                self.assertTrue(
                    is_command_only_prompt(text),
                    f"{text!r} must be classified as command-only",
                )
                self.assertTrue(is_non_actionable_prompt(text))

    def test_substantive_messages_are_not_command_only(self) -> None:
        for text in (
            "백엔드도 포함시켜줘",
            "로그인 말고 검색부터 먼저 구현해",
            "Next.js + Postgres 회원가입 화면 만들어줘",
            "회원가입 화면 만들어줘",
            "지금 뭐 하고 있어?",
        ):
            with self.subTest(text=text):
                self.assertFalse(
                    is_command_only_prompt(text),
                    f"{text!r} is substantive — must NOT be command-only",
                )


# ---------------------------------------------------------------------------
# Conversation envelope — no intake / no auto_collect for command-only
# ---------------------------------------------------------------------------


class ConversationEnvelopeTests(unittest.TestCase):
    def test_proceed_haejwo_yields_approval_action(self) -> None:
        response = build_engineering_conversation_response("진행 해줘")
        self.assertEqual(response.intent_id, APPROVAL_ACTION)
        self.assertFalse(response.ready_to_intake)
        self.assertTrue(response.is_status_query)
        self.assertIn("새 리서치 thread 는 만들지 않습니다", response.content)

    def test_approval_compound_yields_approval_action(self) -> None:
        response = build_engineering_conversation_response(
            "작업 승인 할게 진행 해줘"
        )
        self.assertEqual(response.intent_id, APPROVAL_ACTION)
        self.assertFalse(response.ready_to_intake)

    def test_idaero_jin_haeng_alone_yields_approval_action(self) -> None:
        response = build_engineering_conversation_response("이대로 진행")
        self.assertEqual(response.intent_id, APPROVAL_ACTION)
        self.assertFalse(response.ready_to_intake)

    def test_seungin_hago_yields_approval_action(self) -> None:
        response = build_engineering_conversation_response("승인하고 진행해")
        self.assertEqual(response.intent_id, APPROVAL_ACTION)

    def test_idaero_jin_haeng_with_substantive_last_prompt_still_promotes(self) -> None:
        # P0-K preserves CONFIRM_INTAKE for legitimate "confirm prior
        # proposal" — only downgrades when intake_prompt itself is bare
        # command-only. Substantive last_proposed_prompt rescues it.
        response = build_engineering_conversation_response(
            "이대로 진행",
            last_proposed_prompt="회원가입 화면 만들어줘",
        )
        self.assertEqual(response.intent_id, CONFIRM_INTAKE)
        self.assertTrue(response.ready_to_intake)

    def test_substantive_direction_update_still_intake_candidate(self) -> None:
        response = build_engineering_conversation_response(
            "로그인 말고 검색부터 먼저 구현해"
        )
        self.assertEqual(response.intent_id, TASK_INTAKE_CANDIDATE)


# ---------------------------------------------------------------------------
# Research loop guard helper
# ---------------------------------------------------------------------------


class ResearchLoopGuardTests(unittest.TestCase):
    def test_command_only_blocks_research_loop(self) -> None:
        for text in (
            "진행 해줘",
            "이대로 진행",
            "작업 승인 할게 진행 해줘",
            "승인하고 진행해",
        ):
            with self.subTest(text=text):
                self.assertTrue(_research_loop_blocked_by_command_only(text))

    def test_substantive_prompt_allows_research_loop(self) -> None:
        for text in (
            "Next.js 회원가입 페이지 만들어줘",
            "로그인 말고 검색부터 먼저 구현해",
            "백엔드도 포함시켜줘",
        ):
            with self.subTest(text=text):
                self.assertFalse(_research_loop_blocked_by_command_only(text))

    def test_empty_or_none_does_not_block(self) -> None:
        self.assertFalse(_research_loop_blocked_by_command_only(None))
        self.assertFalse(_research_loop_blocked_by_command_only(""))


# ---------------------------------------------------------------------------
# Forum thread title guard
# ---------------------------------------------------------------------------


class ForumThreadTitleGuardTests(unittest.TestCase):
    """`[Reference] 진행 해줘` thread spam is the canonical bug — block at title source."""

    def test_command_only_title_falls_back_to_default(self) -> None:
        pack = SimpleNamespace(
            title="진행 해줘",
            summary="",
            tags=(),
            request=None,
        )
        topic = derive_research_topic(pack)
        self.assertEqual(topic, "engineering 작업")
        self.assertNotIn("진행", topic)

    def test_command_only_summary_falls_back(self) -> None:
        pack = SimpleNamespace(
            title="",
            summary="이대로 진행",
            tags=(),
            request=None,
        )
        topic = derive_research_topic(pack)
        self.assertEqual(topic, "engineering 작업")

    def test_substantive_title_used_normally(self) -> None:
        pack = SimpleNamespace(
            title="Next.js 회원가입 화면",
            summary="",
            tags=(),
            request=None,
        )
        topic = derive_research_topic(pack)
        self.assertEqual(topic, "Next.js 회원가입 화면")

    def test_command_only_pack_with_substantive_tag_uses_tag(self) -> None:
        # Edge case: pack.title is command-only but pack.tags is
        # substantive — tag should win.
        pack = SimpleNamespace(
            title="진행 해줘",
            summary="",
            tags=("auth", "search"),
            request=None,
        )
        topic = derive_research_topic(pack)
        self.assertIn("auth", topic)
        self.assertNotIn("진행", topic)


# ---------------------------------------------------------------------------
# Forum session resolve — resumed_thread_id fallback
# ---------------------------------------------------------------------------


def _fake_message(channel_id: int):
    channel = SimpleNamespace(id=channel_id, parent_id=99, parent=SimpleNamespace())
    return SimpleNamespace(channel=channel)


def _session_with_extra(**extra):
    return SimpleNamespace(extra=dict(extra))


class ResumedThreadResolveTests(unittest.TestCase):
    def test_resumed_thread_id_matches_when_research_forum_thread_absent(self) -> None:
        message = _fake_message(channel_id=5050)
        sessions = [
            _session_with_extra(resumed_thread_id=5050),
            _session_with_extra(research_forum_thread_id=1111),
        ]
        resolved = _resolve_session_for_forum_thread(
            message=message, session_lister=lambda limit: sessions
        )
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.extra["resumed_thread_id"], 5050)

    def test_research_forum_thread_id_preferred_over_resumed(self) -> None:
        # When both keys point to different sessions, primary lookup
        # wins (research_forum_thread_id is the canonical anchor).
        message = _fake_message(channel_id=5050)
        primary = _session_with_extra(research_forum_thread_id=5050)
        secondary = _session_with_extra(resumed_thread_id=5050)
        resolved = _resolve_session_for_forum_thread(
            message=message, session_lister=lambda limit: [secondary, primary]
        )
        self.assertIs(resolved, primary)

    def test_no_match_returns_none(self) -> None:
        message = _fake_message(channel_id=99999)
        sessions = [
            _session_with_extra(research_forum_thread_id=111),
            _session_with_extra(resumed_thread_id=222),
        ]
        resolved = _resolve_session_for_forum_thread(
            message=message, session_lister=lambda limit: sessions
        )
        self.assertIsNone(resolved)

    def test_p0n3_session_thread_id_fallback_for_work_thread_role_change(
        self,
    ) -> None:
        # P0-N3 (live bug #3): a fresh session created by
        # ``thread_kickoff_fn`` has ``session.thread_id`` set but no
        # ``research_forum_thread_id`` (forum publish is later) and no
        # ``resumed_thread_id`` (not resumed). Role-change in this work
        # thread must still resolve so the active-role update lands.
        message = _fake_message(channel_id=4444)
        session = SimpleNamespace(extra={}, thread_id=4444)
        resolved = _resolve_session_for_forum_thread(
            message=message, session_lister=lambda limit: [session]
        )
        self.assertIs(resolved, session)

    def test_p0n3_session_thread_id_does_not_override_primary_lookup(
        self,
    ) -> None:
        # Primary (research_forum_thread_id) and last-resort
        # (session.thread_id) might both match different sessions; the
        # primary anchor must still win.
        message = _fake_message(channel_id=4444)
        primary = SimpleNamespace(
            extra={"research_forum_thread_id": 4444}, thread_id=9999
        )
        secondary = SimpleNamespace(extra={}, thread_id=4444)
        resolved = _resolve_session_for_forum_thread(
            message=message,
            session_lister=lambda limit: [secondary, primary],
        )
        self.assertIs(resolved, primary)


# ---------------------------------------------------------------------------
# P0-N2 audit — comprehensive command-only leak surface coverage
# ---------------------------------------------------------------------------


class P0N2CommandOnlyLeakSurfaceAuditTests(unittest.TestCase):
    """P0-N2 (live bug #4): pin the 4 critical write sites against the
    full command-only phrase set so future regressions can't reintroduce
    "[Reference] 진행 해줘" thread spam, command-only session.prompt,
    or research-loop queries against routing phrases.

    Audited surfaces:

      1. ``derive_research_topic`` — refuses command-only title/summary
         and falls back to ``engineering 작업``.
      2. ``_research_loop_blocked_by_command_only`` — short-circuits the
         research loop when ``prompt_text`` is non-actionable.
      3. ``build_engineering_conversation_response`` — refuses to set
         ``ready_to_intake=True`` when intake_prompt is non-actionable,
         downgrading to APPROVAL_ACTION ack.
      4. ``routing.is_non_actionable_prompt`` — the canonical predicate
         every guard consults. Phrase set audit lives here.
    """

    # Expanded set covering legacy P0-K examples + P0-N1 yes/no progress
    # questions (which are STATUS_DIAGNOSTIC, not command-only — they
    # MUST NOT trip is_non_actionable_prompt) + compound phrases.
    COMMAND_ONLY_PHRASES = (
        "진행 해줘",
        "이대로 진행",
        "작업 승인 할게 진행 해줘",
        "승인하고 진행해",
        "오케이 진행",
        "승인할게",
        "계속 해",
        "이어서 해",
        "그대로 진행",
        "기존 세션으로 진행",
    )

    SUBSTANTIVE_PHRASES = (
        "Next.js 회원가입 페이지 만들어줘",
        "로그인 말고 검색부터 먼저 구현해",
        "백엔드도 포함시켜줘",
        "결제 모듈 멱등성 회귀 정리해줘",
    )

    STATUS_QUESTION_PHRASES = (
        # P0-N1 added these — status questions, not command-only. They
        # must NOT be flagged as non-actionable (a false positive would
        # make the status diagnostic surface refuse to answer).
        "작업 진행하고 있는 거야?",
        "지금 작업 진행하고 있는 거야?",
        "잘 돌아가고 있어?",
    )

    def test_research_topic_falls_back_for_all_command_only(self) -> None:
        for phrase in self.COMMAND_ONLY_PHRASES:
            with self.subTest(phrase=phrase):
                pack = SimpleNamespace(
                    title=phrase, summary="", tags=(), request=None
                )
                self.assertEqual(
                    derive_research_topic(pack), "engineering 작업"
                )

    def test_research_loop_blocked_for_all_command_only(self) -> None:
        for phrase in self.COMMAND_ONLY_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertTrue(_research_loop_blocked_by_command_only(phrase))

    def test_research_loop_not_blocked_for_substantive(self) -> None:
        for phrase in self.SUBSTANTIVE_PHRASES + self.STATUS_QUESTION_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertFalse(_research_loop_blocked_by_command_only(phrase))

    def test_intake_refused_for_all_command_only(self) -> None:
        # When the conversation layer is called with a bare command-only
        # phrase and no substantive last_proposed_prompt, it must NEVER
        # emit ``ready_to_intake=True`` — that's the precondition for
        # the gateway to call ``intake_fn`` with the routing-command
        # phrase as session.prompt. Some phrases (e.g. "기존 세션으로
        # 진행") legitimately route as TASK_INTAKE_CANDIDATE at the
        # conversation layer and are caught by the router-level
        # ``is_non_actionable_prompt(intake_prompt)`` firewall — so we
        # assert only the write-protection invariant here.
        for phrase in self.COMMAND_ONLY_PHRASES:
            with self.subTest(phrase=phrase):
                response = build_engineering_conversation_response(phrase)
                self.assertFalse(
                    response.ready_to_intake,
                    f"{phrase!r} produced ready_to_intake=True",
                )

    def test_is_non_actionable_phrase_set_covers_yes_no_status_false_positives(
        self,
    ) -> None:
        # Status questions must NOT be classified as non-actionable —
        # otherwise the status diagnostic responder would refuse to
        # answer. This pins P0-N1 + P0-N2 against each other.
        from yule_engineering.agents.routing import is_non_actionable_prompt

        for phrase in self.STATUS_QUESTION_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertFalse(
                    is_non_actionable_prompt(phrase),
                    f"status question {phrase!r} mis-flagged as non-actionable",
                )


# ---------------------------------------------------------------------------
# P0-N5 audit — gateway message ↔ action consistency
# ---------------------------------------------------------------------------


class P0N5MessageActionConsistencyAuditTests(unittest.TestCase):
    """P0-N5 (live bug #6): when the gateway *says* "새 리서치 thread 는
    만들지 않습니다" or "새 intake / research thread 를 만들지 않" in a
    response body, the envelope must carry the matching invariant flags
    that prevent the router from spawning a session.

    These checks pin the operator-facing promise to the actual
    EngineeringConversationResponse contract — without them, a future
    refactor could change the text without changing the flags (or vice
    versa) and produce gateway lies.

    Invariant: if the body promises "no new thread / no new intake",
    the envelope must satisfy ``ready_to_intake=False`` AND
    ``is_status_query=True`` so:

      1. ``_handle_join_or_append`` is never entered (router checks
         is_status_query and short-circuits at the conversation reply).
      2. ``intake_fn`` is never invoked because ready_to_intake=False
         prevents the route from advancing to the CREATE branch.
    """

    PROMISE_PHRASES = (
        "새 리서치 thread 는 만들지 않습니다",
        "새 intake / research thread 를 만들지 않",
        "기존 작업 흐름을 이어갑니다",
    )

    PROMISE_BEARING_TEXTS = (
        # APPROVAL_ACTION ack (P0-K).
        "진행 해줘",
        "이대로 진행",
        "그대로 진행",
        "승인할게",
        "오케이 진행",
        # CONTINUE_EXISTING_WORK / CHANGE_DIRECTION read-only intents.
        "이전 작업 이어서 해줘",
        "방향 수정",
        # STATUS_DIAGNOSTIC (P0-N1 yes/no variants stay read-only).
        "작업 진행하고 있는 거야?",
        "지금 잘 돌아가고 있어?",
    )

    def test_promise_phrases_imply_no_intake_invariant(self) -> None:
        for text in self.PROMISE_BEARING_TEXTS:
            with self.subTest(text=text):
                response = build_engineering_conversation_response(text)
                if not any(p in response.content for p in self.PROMISE_PHRASES):
                    # Body doesn't make the "no new thread" promise →
                    # nothing to enforce here.
                    continue
                self.assertFalse(
                    response.ready_to_intake,
                    f"{text!r} promises no new thread but ready_to_intake=True",
                )
                self.assertTrue(
                    response.is_status_query,
                    f"{text!r} promises no new thread but is_status_query=False — "
                    "router would still proceed to intake",
                )

    def test_read_only_intents_carry_is_status_query_flag(self) -> None:
        # READ_ONLY_INTENTS is the canonical hard-blocklist. Every
        # intent in it must produce ``is_status_query=True`` envelopes
        # so the router's preflight short-circuit fires.
        from yule_discord.engineering_conversation import (
            READ_ONLY_INTENTS,
        )

        text_for_intent = {
            "status_diagnostic": "지금 뭐 하는 중이야?",
            "session_count_query": "지금 열려 있는 세션 작업들 몇 개 있어?",
            "session_list_query": "오픈 세션 뭐뭐 있어?",
            "blocked_reason_query": "왜 멈췄어?",
            "continue_existing_work": "이전 작업 이어서 해줘",
            "change_direction": "방향 수정",
            "approval_action": "진행 해줘",
        }
        for intent_id in READ_ONLY_INTENTS:
            text = text_for_intent.get(intent_id)
            self.assertIsNotNone(
                text, f"missing fixture text for read-only intent {intent_id!r}"
            )
            with self.subTest(intent_id=intent_id):
                response = build_engineering_conversation_response(text)
                self.assertEqual(response.intent_id, intent_id)
                self.assertTrue(
                    response.is_status_query,
                    f"intent {intent_id!r} missing is_status_query=True",
                )
                self.assertFalse(response.ready_to_intake)


if __name__ == "__main__":
    unittest.main()
