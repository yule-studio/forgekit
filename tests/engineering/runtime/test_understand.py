"""Phase 2 — runtime intent classifier tests.

The deterministic classifier is the only thing standing between the
gateway and ``auto_collect=True`` for every Discord message. Pin every
required phrasing here so a regression that re-promotes "어제 작업
이어서 요약해줘" to a brand-new task fails loudly.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_agent_runtime import (
    INTENT_APPEND_CONTEXT,
    INTENT_CLARIFICATION_NEEDED,
    INTENT_CONTINUE_EXISTING_WORK,
    INTENT_DIAGNOSTIC_QUESTION,
    INTENT_EXECUTE_EXISTING_STEP,
    INTENT_GENERAL_CHAT,
    INTENT_NEW_WORK_REQUEST,
    INTENT_STATUS_QUESTION,
    INTENT_SUMMARIZE_PREVIOUS_WORK,
    RuntimeInput,
    RuntimeIntent,
    RuntimeObservation,
)
from yule_agent_runtime.understand import (
    classify_intent_deterministic,
    make_understand_fn,
)


def _classify(text: str, *, last_proposed: str | None = None, role: str = "gateway") -> RuntimeIntent:
    obs = RuntimeObservation(
        role_id=role,
        message_text=text,
        normalized_text=" ".join(text.lower().split()),
    )
    inp = RuntimeInput(
        role_id=role,
        message_text=text,
        last_proposed_prompt=last_proposed,
    )
    return classify_intent_deterministic(obs, inp)


class ExplicitNewWorkOverrideTests(unittest.TestCase):
    def test_force_new_work_phrase_is_new_work_request(self) -> None:
        intent = _classify("새 작업으로 진행")
        self.assertEqual(intent.intent_id, INTENT_NEW_WORK_REQUEST)
        self.assertEqual(intent.confidence, "high")

    def test_force_new_work_overrides_backreference(self) -> None:
        # Even with "어제" present, an explicit "새 작업으로 시작" must win.
        intent = _classify("어제 작업이랑 비슷하지만 새 작업으로 시작해줘")
        self.assertEqual(intent.intent_id, INTENT_NEW_WORK_REQUEST)


class StatusAndDiagnosticTests(unittest.TestCase):
    def test_status_phrases(self) -> None:
        cases = (
            "지금 뭐 하는 중이야?",
            "현재 상태 알려줘",
            "진행상황 좀",
            "어디까지 갔어?",
            "어떻게 됐어?",
            "status check please",
        )
        for text in cases:
            with self.subTest(text=text):
                intent = _classify(text)
                self.assertEqual(intent.intent_id, INTENT_STATUS_QUESTION, text)

    def test_diagnostic_phrases(self) -> None:
        cases = (
            "운영 리서치는 안 열어?",
            "왜 안 됐어?",
            "리서치 왜 실패했어?",
            "옵시디언 왜 안 들어갔어?",
            "왜 멈췄어",
        )
        for text in cases:
            with self.subTest(text=text):
                intent = _classify(text)
                self.assertEqual(intent.intent_id, INTENT_DIAGNOSTIC_QUESTION, text)


class SummarizePreviousWorkTests(unittest.TestCase):
    def test_yesterday_continue_summarize(self) -> None:
        intent = _classify("어제 작업 이어서 요약해줘")
        self.assertEqual(intent.intent_id, INTENT_SUMMARIZE_PREVIOUS_WORK)

    def test_named_project_summary(self) -> None:
        intent = _classify("헤르메스 작업 어디까지 했는지 정리해줘")
        # "어디까지 했는지" hits the status pattern first which is fine —
        # status responder will still surface the existing session. The
        # follow-up "정리해줘" alone (no status verb) is the pure
        # summarize variant covered below.
        self.assertIn(
            intent.intent_id,
            {INTENT_SUMMARIZE_PREVIOUS_WORK, INTENT_STATUS_QUESTION},
        )

    def test_pure_summarize_named_project(self) -> None:
        intent = _classify("헤르메스 작업 정리해줘")
        self.assertEqual(intent.intent_id, INTENT_SUMMARIZE_PREVIOUS_WORK)

    def test_recap_english(self) -> None:
        intent = _classify("recap of yesterday's task please")
        self.assertEqual(intent.intent_id, INTENT_SUMMARIZE_PREVIOUS_WORK)


class ContinueExistingWorkTests(unittest.TestCase):
    def test_named_project_continue(self) -> None:
        intent = _classify("헤르메스 작업 이어서 가자")
        self.assertEqual(intent.intent_id, INTENT_CONTINUE_EXISTING_WORK)

    def test_backreference_continue(self) -> None:
        intent = _classify("어제 작업 이어서 진행하자")
        self.assertEqual(intent.intent_id, INTENT_CONTINUE_EXISTING_WORK)

    def test_named_project_without_continue_verb_falls_through(self) -> None:
        intent = _classify("헤르메스 검토 다시 보자")
        # Named project + work noun should still route to continue, not
        # be promoted to a fresh new_work_request.
        self.assertEqual(intent.intent_id, INTENT_CONTINUE_EXISTING_WORK)


class ForceContinuePhraseTests(unittest.TestCase):
    """Phase A: explicit continuation phrases that historically were
    misclassified as new_work_request because they only contain
    confirm-shaped text + a back-reference noun (no continue verb)."""

    def test_existing_session_progress_phrase(self) -> None:
        intent = _classify("기존 세션으로 진행")
        self.assertEqual(intent.intent_id, INTENT_CONTINUE_EXISTING_WORK)
        self.assertEqual(intent.confidence, "high")

    def test_existing_work_progress_phrase(self) -> None:
        intent = _classify("기존 작업으로 진행")
        self.assertEqual(intent.intent_id, INTENT_CONTINUE_EXISTING_WORK)

    def test_existing_session_continue_phrase(self) -> None:
        intent = _classify("기존 세션으로 이어가자")
        self.assertEqual(intent.intent_id, INTENT_CONTINUE_EXISTING_WORK)

    def test_in_this_thread_phrase(self) -> None:
        for text in (
            "이 thread에서 이어가자",
            "이 thread로 진행",
            "이 스레드에서 진행",
            "여기서 진행해줘",
            "여기서 이어가자",
        ):
            with self.subTest(text=text):
                intent = _classify(text)
                self.assertEqual(intent.intent_id, INTENT_CONTINUE_EXISTING_WORK)

    def test_force_new_work_still_wins_over_continue(self) -> None:
        # Even with "기존" present, an explicit "새 작업으로 진행" must
        # remain a brand-new session — force-new is checked first.
        intent = _classify("기존 작업이랑 비슷하지만 새 작업으로 진행")
        self.assertEqual(intent.intent_id, INTENT_NEW_WORK_REQUEST)


class ExpandedExecuteStepTests(unittest.TestCase):
    def test_research_forum_directive(self) -> None:
        intent = _classify("운영-리서치에 정리해줘")
        self.assertEqual(intent.intent_id, INTENT_EXECUTE_EXISTING_STEP)

    def test_research_forum_directive_no_dash(self) -> None:
        intent = _classify("운영 리서치에 정리해줘")
        self.assertEqual(intent.intent_id, INTENT_EXECUTE_EXISTING_STEP)

    def test_session_scoped_summary(self) -> None:
        intent = _classify("이 세션 기준으로 정리해줘")
        self.assertEqual(intent.intent_id, INTENT_EXECUTE_EXISTING_STEP)

    def test_thread_scoped_summary(self) -> None:
        intent = _classify("이 스레드 기준으로 정리해줘")
        self.assertEqual(intent.intent_id, INTENT_EXECUTE_EXISTING_STEP)


class ExecuteExistingStepTests(unittest.TestCase):
    def test_obsidian_export_request(self) -> None:
        intent = _classify("이건 Obsidian에 정리해줘")
        self.assertEqual(intent.intent_id, INTENT_EXECUTE_EXISTING_STEP)

    def test_meeting_record_summary(self) -> None:
        intent = _classify("토의 기록 정리해서 남겨줘")
        self.assertEqual(intent.intent_id, INTENT_EXECUTE_EXISTING_STEP)


class AppendContextTests(unittest.TestCase):
    def test_append_context_phrase(self) -> None:
        intent = _classify(
            "이 자료만 기존 작업에 참고로 붙여줘 https://example.test/a"
        )
        self.assertEqual(intent.intent_id, INTENT_APPEND_CONTEXT)


class ConfirmationTests(unittest.TestCase):
    def test_standalone_ok_with_pending_proposal(self) -> None:
        intent = _classify("ok", last_proposed="결제 모듈 리팩터링 1차 안")
        self.assertEqual(intent.intent_id, INTENT_NEW_WORK_REQUEST)

    def test_standalone_ok_without_pending_proposal(self) -> None:
        intent = _classify("ok")
        self.assertEqual(intent.intent_id, INTENT_CLARIFICATION_NEEDED)

    def test_phrase_confirm_with_pending_proposal(self) -> None:
        intent = _classify("이대로 진행", last_proposed="제안 텍스트")
        self.assertEqual(intent.intent_id, INTENT_NEW_WORK_REQUEST)
        self.assertEqual(intent.confidence, "high")

    def test_phrase_confirm_without_pending_proposal_is_medium(self) -> None:
        intent = _classify("이대로 진행")
        self.assertEqual(intent.intent_id, INTENT_NEW_WORK_REQUEST)
        self.assertEqual(intent.confidence, "medium")


class NewWorkRequestDefaultTests(unittest.TestCase):
    def test_typical_implementation_request(self) -> None:
        intent = _classify(
            "Stripe pricing 페이지 hero copy 정리하는 프론트엔드 작업 만들어줘"
        )
        self.assertEqual(intent.intent_id, INTENT_NEW_WORK_REQUEST)

    def test_followup_step_does_not_become_new_work(self) -> None:
        # Common regression: gateway promoted "Obsidian에 저장해줘"
        # to a new task when it should reuse the existing session.
        intent = _classify("Obsidian에 저장해줘")
        self.assertNotEqual(intent.intent_id, INTENT_NEW_WORK_REQUEST)


class VagueAndPleasantryTests(unittest.TestCase):
    def test_empty_message_is_clarification(self) -> None:
        intent = _classify("")
        self.assertEqual(intent.intent_id, INTENT_CLARIFICATION_NEEDED)

    def test_too_short_is_clarification(self) -> None:
        intent = _classify("뭐")
        self.assertEqual(intent.intent_id, INTENT_CLARIFICATION_NEEDED)

    def test_pleasantry_is_general_chat(self) -> None:
        intent = _classify("안녕하세요")
        # 4-token rule: short pleasantry should land as general_chat.
        self.assertEqual(intent.intent_id, INTENT_GENERAL_CHAT)


class MakeUnderstandFnTests(unittest.TestCase):
    def test_classifier_override_wins(self) -> None:
        def fake(_obs, _in):
            return RuntimeIntent(
                intent_id=INTENT_GENERAL_CHAT,
                confidence="high",
                reason="llm-said-so",
            )

        understand = make_understand_fn(classifier=fake)
        obs = RuntimeObservation(role_id="gateway", message_text="새 작업으로 진행")
        inp = RuntimeInput(role_id="gateway", message_text="새 작업으로 진행")
        intent = understand(obs, inp)
        self.assertEqual(intent.intent_id, INTENT_GENERAL_CHAT)
        self.assertEqual(intent.reason, "llm-said-so")

    def test_classifier_returning_none_falls_back(self) -> None:
        def fake(_obs, _in):
            return None

        understand = make_understand_fn(classifier=fake)
        obs = RuntimeObservation(
            role_id="gateway",
            message_text="새 작업으로 진행",
            normalized_text="새 작업으로 진행",
        )
        inp = RuntimeInput(role_id="gateway", message_text="새 작업으로 진행")
        intent = understand(obs, inp)
        self.assertEqual(intent.intent_id, INTENT_NEW_WORK_REQUEST)

    def test_classifier_exception_falls_back_with_annotation(self) -> None:
        def fake(_obs, _in):
            raise RuntimeError("llm down")

        understand = make_understand_fn(classifier=fake)
        obs = RuntimeObservation(
            role_id="gateway",
            message_text="어제 작업 이어서 요약해줘",
            normalized_text="어제 작업 이어서 요약해줘",
        )
        inp = RuntimeInput(role_id="gateway", message_text="어제 작업 이어서 요약해줘")
        intent = understand(obs, inp)
        self.assertEqual(intent.intent_id, INTENT_SUMMARIZE_PREVIOUS_WORK)
        self.assertIn("llm down", intent.reason)
        self.assertIn("fallback", intent.reason)


if __name__ == "__main__":
    unittest.main()
