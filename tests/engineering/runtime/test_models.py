"""Phase 1 — runtime data model contract tests.

The runtime hands these dataclasses through every stage, so the
defaults and invariants matter: every field should default to a value
that's safe to render (no None where a tuple is expected, intents and
actions stay in the documented vocabulary, etc.).
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.runtime import (
    ACTION_NOOP,
    ACTION_REPLY,
    INTENT_GENERAL_CHAT,
    INTENT_NEW_WORK_REQUEST,
    KNOWN_ACTIONS,
    KNOWN_INTENTS,
    RuntimeAction,
    RuntimeDecision,
    RuntimeInput,
    RuntimeIntent,
    RuntimeObservation,
    RuntimeRecallResult,
    RuntimeRecord,
    RuntimeResearchPlan,
    RuntimeResult,
    SessionCandidate,
)


class IntentVocabularyTests(unittest.TestCase):
    def test_known_intents_include_all_required_categories(self) -> None:
        # The phase-2 classifier promises these eight categories.
        required = {
            "new_work_request",
            "continue_existing_work",
            "summarize_previous_work",
            "status_question",
            "diagnostic_question",
            "execute_existing_step",
            "general_chat",
            "clarification_needed",
            "append_context",
        }
        self.assertTrue(required.issubset(set(KNOWN_INTENTS)))

    def test_known_actions_include_all_required_categories(self) -> None:
        required = {
            "reply",
            "ask_clarification",
            "create_session",
            "join_session",
            "append_context",
            "run_research",
            "publish_forum",
            "request_role_turn",
            "record_memory",
            "propose_approval",
            "noop",
        }
        self.assertTrue(required.issubset(set(KNOWN_ACTIONS)))


class RuntimeInputDefaultsTests(unittest.TestCase):
    def test_required_fields_only(self) -> None:
        item = RuntimeInput(role_id="gateway", message_text="hi")
        self.assertEqual(item.role_id, "gateway")
        self.assertEqual(item.message_text, "hi")
        # Tuples / mappings default to empty so callers can iterate
        # without ``or ()``.
        self.assertEqual(tuple(item.attachments), ())
        self.assertEqual(tuple(item.user_links), ())
        self.assertEqual(tuple(item.mentions), ())
        self.assertEqual(dict(item.policy), {})
        self.assertIsNone(item.received_at)
        self.assertIsNone(item.last_proposed_prompt)

    def test_full_construction_round_trip(self) -> None:
        now = datetime.now(timezone.utc)
        item = RuntimeInput(
            role_id="engineering-agent/tech-lead",
            message_text="어제 작업 이어서",
            channel_id=10,
            thread_id=20,
            author_id=30,
            message_id=40,
            attachments=("a",),
            user_links=("https://example.test",),
            mentions=(99,),
            received_at=now,
            last_proposed_prompt="이전 제안 텍스트",
            policy={"role_focus": "tech-lead"},
        )
        self.assertEqual(item.policy["role_focus"], "tech-lead")
        self.assertEqual(tuple(item.user_links), ("https://example.test",))


class RuntimeObservationTests(unittest.TestCase):
    def test_defaults_are_renderable(self) -> None:
        obs = RuntimeObservation(role_id="r", message_text="x")
        self.assertEqual(obs.normalized_text, "")
        self.assertFalse(obs.has_attachments)
        self.assertEqual(tuple(obs.extracted_urls), ())


class RuntimeIntentTests(unittest.TestCase):
    def test_intent_id_must_use_known_vocabulary_in_practice(self) -> None:
        # The dataclass itself doesn't enforce the vocabulary (so future
        # phases can extend), but every runtime intent we construct in
        # production uses a known id. Pin that contract here so an
        # accidental typo in another module fails loudly.
        intent = RuntimeIntent(intent_id=INTENT_NEW_WORK_REQUEST)
        self.assertIn(intent.intent_id, KNOWN_INTENTS)

    def test_alt_intents_default_empty(self) -> None:
        intent = RuntimeIntent(intent_id=INTENT_GENERAL_CHAT)
        self.assertEqual(tuple(intent.alt_intents), ())
        self.assertEqual(intent.confidence, "medium")


class SessionCandidateTests(unittest.TestCase):
    def test_minimal_construction(self) -> None:
        cand = SessionCandidate(session_id="abc")
        self.assertEqual(cand.session_id, "abc")
        self.assertEqual(cand.score, 0.0)
        self.assertEqual(dict(cand.extra), {})

    def test_rich_construction(self) -> None:
        cand = SessionCandidate(
            session_id="abc",
            title="Hermes intake",
            score=0.7,
            why="title overlap",
            state="in_progress",
            task_type="research",
            thread_id=42,
            forum_thread_id=4242,
            has_research_pack=True,
            has_synthesis=False,
            extra={"updated_at": "2026-05-06"},
        )
        self.assertEqual(cand.score, 0.7)
        self.assertTrue(cand.has_research_pack)
        self.assertFalse(cand.has_synthesis)


class RuntimeRecallResultTests(unittest.TestCase):
    def test_defaults_are_safe(self) -> None:
        result = RuntimeRecallResult()
        self.assertIsNone(result.matched_session_id)
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.memory_hits, ())
        self.assertEqual(result.confidence, "low")


class RuntimeResearchPlanTests(unittest.TestCase):
    def test_default_does_not_run(self) -> None:
        plan = RuntimeResearchPlan()
        self.assertFalse(plan.run)
        self.assertEqual(tuple(plan.providers), ())
        self.assertEqual(plan.max_provider_calls, 0)


class RuntimeActionTests(unittest.TestCase):
    def test_default_payload_is_mutable_friendly(self) -> None:
        action = RuntimeAction(action_id=ACTION_REPLY)
        self.assertEqual(dict(action.payload), {})

    def test_action_id_uses_known_vocabulary_in_practice(self) -> None:
        for action_id in (ACTION_REPLY, ACTION_NOOP):
            self.assertIn(action_id, KNOWN_ACTIONS)


class RuntimeDecisionTests(unittest.TestCase):
    def test_default_research_plan_is_inert(self) -> None:
        decision = RuntimeDecision(intent=RuntimeIntent(intent_id=INTENT_GENERAL_CHAT))
        self.assertFalse(decision.research_plan.run)
        self.assertEqual(decision.actions, ())


class RuntimeRecordTests(unittest.TestCase):
    def test_kind_required(self) -> None:
        record = RuntimeRecord(kind="intent_detected")
        self.assertEqual(record.kind, "intent_detected")
        self.assertEqual(dict(record.data), {})


class RuntimeResultPrimaryActionTests(unittest.TestCase):
    def _bare_result(
        self,
        *,
        actions_taken: tuple = (),
        decision_actions: tuple = (),
    ) -> RuntimeResult:
        intent = RuntimeIntent(intent_id=INTENT_GENERAL_CHAT)
        return RuntimeResult(
            role_id="gateway",
            observation=RuntimeObservation(role_id="gateway", message_text=""),
            intent=intent,
            recall=RuntimeRecallResult(),
            research_plan=RuntimeResearchPlan(),
            decision=RuntimeDecision(intent=intent, actions=decision_actions),
            actions_taken=actions_taken,
        )

    def test_primary_action_id_prefers_actions_taken(self) -> None:
        result = self._bare_result(
            actions_taken=(RuntimeAction(action_id=ACTION_REPLY),),
            decision_actions=(RuntimeAction(action_id=ACTION_NOOP),),
        )
        self.assertEqual(result.primary_action_id, ACTION_REPLY)

    def test_primary_action_id_falls_back_to_decision(self) -> None:
        result = self._bare_result(
            decision_actions=(RuntimeAction(action_id=ACTION_NOOP),),
        )
        self.assertEqual(result.primary_action_id, ACTION_NOOP)

    def test_primary_action_id_none_when_empty(self) -> None:
        result = self._bare_result()
        self.assertIsNone(result.primary_action_id)


if __name__ == "__main__":
    unittest.main()
