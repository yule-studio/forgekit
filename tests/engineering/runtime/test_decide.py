"""Phase 4A — runtime Decide stage tests.

The deterministic mapping must be sticky enough that the router
preflight in Phase 4B can rely on a single ``primary_action_id`` per
intent + recall combination.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_agent_runtime import (
    ACTION_APPEND_CONTEXT,
    ACTION_ASK_CLARIFICATION,
    ACTION_CREATE_SESSION,
    ACTION_JOIN_SESSION,
    ACTION_REPLY,
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
    RuntimeRecallResult,
    RuntimeResearchPlan,
    SessionCandidate,
)
from yule_agent_runtime.decide import decide_default


def _obs(text: str = "msg") -> RuntimeObservation:
    return RuntimeObservation(role_id="gateway", message_text=text)


def _input(text: str = "msg") -> RuntimeInput:
    return RuntimeInput(role_id="gateway", message_text=text)


def _recall_match(session_id: str = "abc", *, thread_id: int | None = None) -> RuntimeRecallResult:
    return RuntimeRecallResult(
        matched_session_id=session_id,
        matched_thread_id=thread_id,
        candidates=(SessionCandidate(session_id=session_id, score=0.7),),
        confidence="high",
        reason="match",
    )


def _recall_ambiguous() -> RuntimeRecallResult:
    return RuntimeRecallResult(
        candidates=(
            SessionCandidate(session_id="a", title="A", score=0.4),
            SessionCandidate(session_id="b", title="B", score=0.35),
        ),
        confidence="low",
        reason="ambiguous",
    )


def _decide(intent_id: str, recall: RuntimeRecallResult | None = None):
    return decide_default(
        _obs(),
        RuntimeIntent(intent_id=intent_id),
        recall or RuntimeRecallResult(),
        RuntimeResearchPlan(),
        _input(),
    )


class StatusDiagnosticDecisionTests(unittest.TestCase):
    def test_status_question_replies(self) -> None:
        decision = _decide(INTENT_STATUS_QUESTION)
        self.assertEqual(decision.actions[0].action_id, ACTION_REPLY)
        self.assertEqual(decision.actions[0].payload["variant"], "status")

    def test_diagnostic_question_replies(self) -> None:
        decision = _decide(INTENT_DIAGNOSTIC_QUESTION)
        self.assertEqual(decision.actions[0].action_id, ACTION_REPLY)
        self.assertEqual(decision.actions[0].payload["variant"], "diagnostic")


class JoinSessionDecisionTests(unittest.TestCase):
    def test_continue_with_match_yields_join(self) -> None:
        decision = _decide(INTENT_CONTINUE_EXISTING_WORK, _recall_match("s1", thread_id=99))
        action = decision.actions[0]
        self.assertEqual(action.action_id, ACTION_JOIN_SESSION)
        self.assertEqual(action.payload["session_id"], "s1")
        self.assertEqual(action.payload["thread_id"], 99)
        self.assertEqual(action.payload["intent"], INTENT_CONTINUE_EXISTING_WORK)

    def test_summarize_with_match_yields_join(self) -> None:
        decision = _decide(INTENT_SUMMARIZE_PREVIOUS_WORK, _recall_match("s2"))
        self.assertEqual(decision.actions[0].action_id, ACTION_JOIN_SESSION)

    def test_execute_with_match_yields_join(self) -> None:
        decision = _decide(INTENT_EXECUTE_EXISTING_STEP, _recall_match("s3"))
        self.assertEqual(decision.actions[0].action_id, ACTION_JOIN_SESSION)


class AskClarificationDecisionTests(unittest.TestCase):
    def test_continue_without_match_asks_clarification_with_candidates(self) -> None:
        decision = _decide(INTENT_CONTINUE_EXISTING_WORK, _recall_ambiguous())
        action = decision.actions[0]
        self.assertEqual(action.action_id, ACTION_ASK_CLARIFICATION)
        self.assertEqual(action.payload["intent"], INTENT_CONTINUE_EXISTING_WORK)
        self.assertEqual(action.payload["reason"], "no_matched_session")
        self.assertEqual(len(action.payload["candidates"]), 2)
        self.assertEqual(action.payload["candidates"][0]["session_id"], "a")

    def test_summarize_without_match_asks_clarification(self) -> None:
        decision = _decide(INTENT_SUMMARIZE_PREVIOUS_WORK)
        self.assertEqual(decision.actions[0].action_id, ACTION_ASK_CLARIFICATION)

    def test_execute_without_match_asks_clarification(self) -> None:
        decision = _decide(INTENT_EXECUTE_EXISTING_STEP)
        self.assertEqual(decision.actions[0].action_id, ACTION_ASK_CLARIFICATION)

    def test_clarification_needed_intent_yields_clarification(self) -> None:
        decision = _decide(INTENT_CLARIFICATION_NEEDED)
        self.assertEqual(decision.actions[0].action_id, ACTION_ASK_CLARIFICATION)


class AppendContextDecisionTests(unittest.TestCase):
    def test_append_with_match_yields_append(self) -> None:
        decision = _decide(INTENT_APPEND_CONTEXT, _recall_match("s4"))
        action = decision.actions[0]
        self.assertEqual(action.action_id, ACTION_APPEND_CONTEXT)
        self.assertEqual(action.payload["session_id"], "s4")

    def test_append_without_match_asks_clarification(self) -> None:
        decision = _decide(INTENT_APPEND_CONTEXT)
        self.assertEqual(decision.actions[0].action_id, ACTION_ASK_CLARIFICATION)


class NewWorkAndChatDecisionTests(unittest.TestCase):
    def test_new_work_yields_create_session(self) -> None:
        decision = _decide(INTENT_NEW_WORK_REQUEST)
        action = decision.actions[0]
        self.assertEqual(action.action_id, ACTION_CREATE_SESSION)
        self.assertIn("prompt", action.payload)

    def test_general_chat_yields_reply_chat(self) -> None:
        decision = _decide(INTENT_GENERAL_CHAT)
        action = decision.actions[0]
        self.assertEqual(action.action_id, ACTION_REPLY)
        self.assertEqual(action.payload["variant"], "chat")


class DecisionInvariantsTests(unittest.TestCase):
    def test_decision_preserves_intent_and_plan(self) -> None:
        intent = RuntimeIntent(intent_id=INTENT_NEW_WORK_REQUEST, confidence="high")
        plan = RuntimeResearchPlan(run=False, reason="not needed")
        decision = decide_default(
            _obs(),
            intent,
            RuntimeRecallResult(),
            plan,
            _input(),
        )
        self.assertIs(decision.intent, intent)
        self.assertIs(decision.research_plan, plan)
        self.assertGreaterEqual(len(decision.actions), 1)


if __name__ == "__main__":
    unittest.main()
