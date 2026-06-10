"""F16 — gateway recall-first 7-action decision matrix (issue #128).

Each test pins one branch in ``docs/runtime-recall-first.md §2.2``.
The runtime action_id stays one of the existing ``KNOWN_ACTIONS``;
the gateway-specific label rides on ``payload["gateway_action"]``.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.runtime.decide import decide_gateway
from yule_engineering.agents.runtime.models import (
    ACTION_APPEND_CONTEXT,
    ACTION_ASK_CLARIFICATION,
    ACTION_JOIN_SESSION,
    ACTION_REPLY,
    ACTION_REQUEST_ROLE_TURN,
    ACTION_RUN_RESEARCH,
    COVERAGE_HIGH,
    COVERAGE_LOW,
    COVERAGE_MEDIUM,
    GATEWAY_APPEND_CONTEXT,
    GATEWAY_ASK_CLARIFICATION,
    GATEWAY_FULL_RESEARCH,
    GATEWAY_HANDOFF_TECH_LEAD_NO_RESEARCH,
    GATEWAY_JOIN_EXISTING,
    GATEWAY_REPLY_ONLY,
    GATEWAY_TARGETED_RESEARCH,
    INTENT_APPEND_CONTEXT,
    INTENT_CLARIFICATION_NEEDED,
    INTENT_CONTINUE_EXISTING_WORK,
    INTENT_DIAGNOSTIC_QUESTION,
    INTENT_EXECUTE_EXISTING_STEP,
    INTENT_GENERAL_CHAT,
    INTENT_NEW_WORK_REQUEST,
    INTENT_STATUS_QUESTION,
    INTENT_SUMMARIZE_PREVIOUS_WORK,
    RecallCoverage,
    RuntimeInput,
    RuntimeIntent,
    RuntimeObservation,
    RuntimeRecallResult,
    RuntimeResearchPlan,
)


def _observation(text: str = "안녕") -> RuntimeObservation:
    return RuntimeObservation(
        role_id="gateway",
        message_text=text,
        normalized_text=text,
    )


def _input(text: str = "안녕") -> RuntimeInput:
    return RuntimeInput(role_id="gateway", message_text=text)


def _intent(intent_id: str) -> RuntimeIntent:
    return RuntimeIntent(intent_id=intent_id, confidence="high")


def _recall(
    *,
    coverage: RecallCoverage,
    matched_session_id: str = None,
    candidates: tuple = (),
    memory_hits: tuple = (),
) -> RuntimeRecallResult:
    return RuntimeRecallResult(
        matched_session_id=matched_session_id,
        candidates=candidates,
        memory_hits=memory_hits,
        coverage=coverage,
    )


def _plan() -> RuntimeResearchPlan:
    return RuntimeResearchPlan()


def _gateway_label(decision) -> str:
    first = decision.actions[0]
    return first.payload.get("gateway_action", "")


class GatewayReplyOnlyTests(unittest.TestCase):
    def test_status_with_high_fresh_coverage_replies_only(self) -> None:
        coverage = RecallCoverage(level=COVERAGE_HIGH, stale=False, sources=("session", "memory:rag"))
        decision = decide_gateway(
            _observation("status 어떻게 됐어?"),
            _intent(INTENT_STATUS_QUESTION),
            _recall(coverage=coverage, matched_session_id=None),
            _plan(),
            _input("status 어떻게 됐어?"),
        )
        self.assertEqual(decision.actions[0].action_id, ACTION_REPLY)
        self.assertEqual(_gateway_label(decision), GATEWAY_REPLY_ONLY)
        self.assertFalse(decision.research_plan.run)

    def test_general_chat_with_high_coverage_replies_only(self) -> None:
        coverage = RecallCoverage(level=COVERAGE_HIGH, stale=False, sources=("session",))
        decision = decide_gateway(
            _observation("어제 작업 뭐였더라"),
            _intent(INTENT_GENERAL_CHAT),
            _recall(coverage=coverage),
            _plan(),
            _input("어제 작업 뭐였더라"),
        )
        self.assertEqual(_gateway_label(decision), GATEWAY_REPLY_ONLY)


class GatewayAskClarificationTests(unittest.TestCase):
    def test_clarification_needed_intent_short_circuits(self) -> None:
        coverage = RecallCoverage(level=COVERAGE_HIGH, stale=False)
        decision = decide_gateway(
            _observation("?"),
            _intent(INTENT_CLARIFICATION_NEEDED),
            _recall(coverage=coverage),
            _plan(),
            _input("?"),
        )
        self.assertEqual(decision.actions[0].action_id, ACTION_ASK_CLARIFICATION)
        self.assertEqual(_gateway_label(decision), GATEWAY_ASK_CLARIFICATION)

    def test_chat_with_thin_coverage_asks_clarification_not_research(self) -> None:
        coverage = RecallCoverage(level=COVERAGE_LOW, stale=True)
        decision = decide_gateway(
            _observation("그 작업 어땠지"),
            _intent(INTENT_GENERAL_CHAT),
            _recall(coverage=coverage),
            _plan(),
            _input("그 작업 어땠지"),
        )
        self.assertEqual(_gateway_label(decision), GATEWAY_ASK_CLARIFICATION)
        self.assertFalse(decision.research_plan.run)

    def test_high_but_stale_chat_asks_clarification(self) -> None:
        coverage = RecallCoverage(level=COVERAGE_HIGH, stale=True, sources=("memory:rag",))
        decision = decide_gateway(
            _observation("그건 어떻게 됐어"),
            _intent(INTENT_DIAGNOSTIC_QUESTION),
            _recall(coverage=coverage),
            _plan(),
            _input("그건 어떻게 됐어"),
        )
        # high + stale → not REPLY_ONLY because stale; falls through to ask.
        self.assertEqual(_gateway_label(decision), GATEWAY_ASK_CLARIFICATION)


class GatewayJoinAppendTests(unittest.TestCase):
    def test_continue_with_matched_session_joins(self) -> None:
        coverage = RecallCoverage(level=COVERAGE_HIGH, stale=False)
        decision = decide_gateway(
            _observation("그 작업 이어서"),
            _intent(INTENT_CONTINUE_EXISTING_WORK),
            _recall(coverage=coverage, matched_session_id="sess-1"),
            _plan(),
            _input("그 작업 이어서"),
        )
        self.assertEqual(decision.actions[0].action_id, ACTION_JOIN_SESSION)
        self.assertEqual(_gateway_label(decision), GATEWAY_JOIN_EXISTING)
        self.assertEqual(decision.actions[0].payload["session_id"], "sess-1")

    def test_summarize_with_matched_session_joins(self) -> None:
        coverage = RecallCoverage(level=COVERAGE_HIGH, stale=False)
        decision = decide_gateway(
            _observation("어제 한 거 정리해줘"),
            _intent(INTENT_SUMMARIZE_PREVIOUS_WORK),
            _recall(coverage=coverage, matched_session_id="sess-2"),
            _plan(),
            _input("어제 한 거 정리해줘"),
        )
        self.assertEqual(_gateway_label(decision), GATEWAY_JOIN_EXISTING)

    def test_execute_existing_step_joins(self) -> None:
        coverage = RecallCoverage(level=COVERAGE_HIGH, stale=False)
        decision = decide_gateway(
            _observation("다음 step 실행"),
            _intent(INTENT_EXECUTE_EXISTING_STEP),
            _recall(coverage=coverage, matched_session_id="sess-3"),
            _plan(),
            _input("다음 step 실행"),
        )
        self.assertEqual(_gateway_label(decision), GATEWAY_JOIN_EXISTING)

    def test_append_context_with_matched_session(self) -> None:
        coverage = RecallCoverage(level=COVERAGE_HIGH, stale=False)
        decision = decide_gateway(
            _observation("이것도 같이 봐줘"),
            _intent(INTENT_APPEND_CONTEXT),
            _recall(coverage=coverage, matched_session_id="sess-4"),
            _plan(),
            _input("이것도 같이 봐줘"),
        )
        self.assertEqual(decision.actions[0].action_id, ACTION_APPEND_CONTEXT)
        self.assertEqual(_gateway_label(decision), GATEWAY_APPEND_CONTEXT)


class GatewayHandoffTechLeadTests(unittest.TestCase):
    def test_new_work_with_high_coverage_and_direction_inquiry_handoff(self) -> None:
        coverage = RecallCoverage(level=COVERAGE_HIGH, stale=False, sources=("session", "memory:rag"))
        decision = decide_gateway(
            _observation("이거 어떻게 생각해? 어떤 방향이 나을까"),
            _intent(INTENT_NEW_WORK_REQUEST),
            _recall(coverage=coverage),
            _plan(),
            _input("이거 어떻게 생각해? 어떤 방향이 나을까"),
        )
        self.assertEqual(decision.actions[0].action_id, ACTION_REQUEST_ROLE_TURN)
        self.assertEqual(_gateway_label(decision), GATEWAY_HANDOFF_TECH_LEAD_NO_RESEARCH)
        self.assertEqual(decision.actions[0].payload["role"], "tech-lead")
        self.assertFalse(decision.actions[0].payload["run_research"])
        self.assertFalse(decision.research_plan.run)

    def test_high_coverage_without_direction_phrase_targeted_or_full(self) -> None:
        # high + no direction phrase + not stale → falls through to research-or-handoff.
        # Per matrix this hits 5c (full research) because it doesn't match 5a/5b.
        coverage = RecallCoverage(level=COVERAGE_HIGH, stale=False)
        decision = decide_gateway(
            _observation("새로운 ETL 만들어야 해"),
            _intent(INTENT_NEW_WORK_REQUEST),
            _recall(coverage=coverage),
            _plan(),
            _input("새로운 ETL 만들어야 해"),
        )
        self.assertEqual(_gateway_label(decision), GATEWAY_FULL_RESEARCH)


class GatewayTargetedResearchTests(unittest.TestCase):
    def test_new_work_with_medium_coverage_targeted(self) -> None:
        coverage = RecallCoverage(level=COVERAGE_MEDIUM, stale=False, sources=("memory:rag",))
        decision = decide_gateway(
            _observation("Redis 쓰는 게 맞나"),
            _intent(INTENT_NEW_WORK_REQUEST),
            _recall(coverage=coverage),
            _plan(),
            _input("Redis 쓰는 게 맞나"),
        )
        self.assertEqual(decision.actions[0].action_id, ACTION_RUN_RESEARCH)
        self.assertEqual(_gateway_label(decision), GATEWAY_TARGETED_RESEARCH)
        self.assertTrue(decision.research_plan.run)
        self.assertEqual(decision.actions[0].payload["mode"], "targeted")
        self.assertEqual(decision.actions[0].payload["max_provider_calls"], 2)
        self.assertEqual(decision.research_plan.max_provider_calls, 2)

    def test_new_work_with_high_but_stale_targeted(self) -> None:
        coverage = RecallCoverage(level=COVERAGE_HIGH, stale=True, sources=("memory:rag",))
        decision = decide_gateway(
            _observation("이 모듈 다시 손봐야 해"),
            _intent(INTENT_NEW_WORK_REQUEST),
            _recall(coverage=coverage),
            _plan(),
            _input("이 모듈 다시 손봐야 해"),
        )
        self.assertEqual(_gateway_label(decision), GATEWAY_TARGETED_RESEARCH)


class GatewayFullResearchTests(unittest.TestCase):
    def test_new_work_low_coverage_full_research(self) -> None:
        coverage = RecallCoverage(level=COVERAGE_LOW, stale=True)
        decision = decide_gateway(
            _observation("처음 보는 주제 작업해야 해"),
            _intent(INTENT_NEW_WORK_REQUEST),
            _recall(coverage=coverage),
            _plan(),
            _input("처음 보는 주제 작업해야 해"),
        )
        self.assertEqual(_gateway_label(decision), GATEWAY_FULL_RESEARCH)
        self.assertTrue(decision.research_plan.run)
        self.assertEqual(decision.actions[0].payload["mode"], "full")

    def test_explicit_research_phrase_full_regardless_of_coverage(self) -> None:
        coverage = RecallCoverage(level=COVERAGE_HIGH, stale=False, sources=("session",))
        decision = decide_gateway(
            _observation("이거 한번 조사해줘"),
            _intent(INTENT_NEW_WORK_REQUEST),
            _recall(coverage=coverage),
            _plan(),
            _input("이거 한번 조사해줘"),
        )
        self.assertEqual(_gateway_label(decision), GATEWAY_FULL_RESEARCH)
        self.assertEqual(
            decision.actions[0].payload.get("trigger"), "explicit_phrase"
        )

    def test_research_only_english_phrase_full(self) -> None:
        coverage = RecallCoverage(level=COVERAGE_MEDIUM, stale=False)
        decision = decide_gateway(
            _observation("research only please"),
            _intent(INTENT_NEW_WORK_REQUEST),
            _recall(coverage=coverage),
            _plan(),
            _input("research only please"),
        )
        self.assertEqual(_gateway_label(decision), GATEWAY_FULL_RESEARCH)


class GatewayFallbackTests(unittest.TestCase):
    def test_continue_intent_without_matched_session_falls_to_clarify(self) -> None:
        coverage = RecallCoverage(level=COVERAGE_LOW, stale=True)
        decision = decide_gateway(
            _observation("그 작업 이어서"),
            _intent(INTENT_CONTINUE_EXISTING_WORK),
            _recall(coverage=coverage, matched_session_id=None),
            _plan(),
            _input("그 작업 이어서"),
        )
        # No matched session → no JOIN. Not a research intent → clarification.
        self.assertEqual(_gateway_label(decision), GATEWAY_ASK_CLARIFICATION)

    def test_coverage_none_treated_as_low_stale(self) -> None:
        # When recall.coverage is None, decide_gateway must default to
        # low+stale so we don't silently take a chat-grounded path.
        decision = decide_gateway(
            _observation("뭔가 일이 있어"),
            _intent(INTENT_NEW_WORK_REQUEST),
            RuntimeRecallResult(coverage=None),
            _plan(),
            _input("뭔가 일이 있어"),
        )
        self.assertEqual(_gateway_label(decision), GATEWAY_FULL_RESEARCH)


if __name__ == "__main__":
    unittest.main()
