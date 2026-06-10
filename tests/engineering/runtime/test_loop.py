"""Phase 1 — runtime loop skeleton contract tests.

The skeleton loop's contract is purely about ordering and stage
isolation: every stage must be reachable, calls must happen in the
documented order, mocks must be injectable, and a stage exception
must not crash the whole run. The actual classifier / recall / decide
logic comes in Phases 2–5.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.runtime import (
    ACTION_NOOP,
    ACTION_REPLY,
    INTENT_GENERAL_CHAT,
    INTENT_NEW_WORK_REQUEST,
    INTENT_STATUS_QUESTION,
    RuntimeAction,
    RuntimeDecision,
    RuntimeInput,
    RuntimeIntent,
    RuntimeObservation,
    RuntimeRecallResult,
    RuntimeRecord,
    RuntimeResearchPlan,
    run_runtime_loop,
)


def _input(message: str = "안녕", role: str = "gateway") -> RuntimeInput:
    return RuntimeInput(role_id=role, message_text=message)


class StageOrderingTests(unittest.TestCase):
    def test_default_loop_visits_every_stage_in_order(self) -> None:
        order: list[str] = []

        def observe(_in):
            order.append("observe")
            return RuntimeObservation(role_id=_in.role_id, message_text=_in.message_text)

        def understand(_obs, _in):
            order.append("understand")
            return RuntimeIntent(intent_id=INTENT_GENERAL_CHAT)

        def recall(_obs, _intent, _in):
            order.append("recall")
            return RuntimeRecallResult()

        def research(_obs, _intent, _recall, _in):
            order.append("research")
            return RuntimeResearchPlan()

        def decide(_obs, intent, _recall, plan, _in):
            order.append("decide")
            return RuntimeDecision(
                intent=intent,
                research_plan=plan,
                actions=(RuntimeAction(action_id=ACTION_NOOP),),
            )

        def act(decision, _in):
            order.append("act")
            return tuple(decision.actions)

        def record(_result, _in):
            order.append("record")
            return (RuntimeRecord(kind="action_taken"),)

        result = run_runtime_loop(
            _input(),
            observe_fn=observe,
            understand_fn=understand,
            recall_fn=recall,
            research_fn=research,
            decide_fn=decide,
            act_fn=act,
            record_fn=record,
        )
        self.assertEqual(
            order,
            ["observe", "understand", "recall", "research", "decide", "act", "record"],
        )
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].kind, "action_taken")


class StagePropagationTests(unittest.TestCase):
    def test_observation_flows_into_understand(self) -> None:
        captured: dict = {}

        def observe(_in):
            return RuntimeObservation(
                role_id=_in.role_id,
                message_text=_in.message_text,
                normalized_text="custom",
            )

        def understand(obs, _in):
            captured["observed_text"] = obs.normalized_text
            return RuntimeIntent(intent_id=INTENT_NEW_WORK_REQUEST, confidence="high")

        result = run_runtime_loop(
            _input("hello"),
            observe_fn=observe,
            understand_fn=understand,
        )
        self.assertEqual(captured["observed_text"], "custom")
        self.assertEqual(result.intent.intent_id, INTENT_NEW_WORK_REQUEST)
        self.assertEqual(result.intent.confidence, "high")

    def test_recall_sees_intent(self) -> None:
        captured: dict = {}

        def understand(_obs, _in):
            return RuntimeIntent(intent_id=INTENT_STATUS_QUESTION)

        def recall(_obs, intent, _in):
            captured["intent_id"] = intent.intent_id
            return RuntimeRecallResult(reason="seen")

        result = run_runtime_loop(
            _input("status?"),
            understand_fn=understand,
            recall_fn=recall,
        )
        self.assertEqual(captured["intent_id"], INTENT_STATUS_QUESTION)
        self.assertEqual(result.recall.reason, "seen")

    def test_decide_sees_research_plan(self) -> None:
        captured: dict = {}

        def research(_obs, _intent, _recall, _in):
            return RuntimeResearchPlan(run=True, reason="needs-collection", max_provider_calls=4)

        def decide(_obs, intent, _recall, plan, _in):
            captured["plan_run"] = plan.run
            captured["plan_max_calls"] = plan.max_provider_calls
            return RuntimeDecision(
                intent=intent,
                research_plan=plan,
                actions=(RuntimeAction(action_id=ACTION_NOOP),),
            )

        run_runtime_loop(
            _input(),
            research_fn=research,
            decide_fn=decide,
        )
        self.assertTrue(captured["plan_run"])
        self.assertEqual(captured["plan_max_calls"], 4)


class DefaultStagesProduceSafeOutputTests(unittest.TestCase):
    def test_default_stages_yield_general_chat_noop(self) -> None:
        result = run_runtime_loop(_input("hello"))
        self.assertEqual(result.intent.intent_id, INTENT_GENERAL_CHAT)
        self.assertEqual(result.recall.candidates, ())
        self.assertFalse(result.research_plan.run)
        self.assertEqual(result.primary_action_id, ACTION_NOOP)
        # Default record returns nothing — keeps things side-effect free.
        self.assertEqual(result.records, ())
        self.assertIsNone(result.error)

    def test_default_observation_extracts_urls_and_normalizes(self) -> None:
        result = run_runtime_loop(_input("Hello https://example.com/a    world"))
        self.assertIn("https://example.com/a", result.observation.extracted_urls)
        self.assertEqual(result.observation.normalized_text, "hello https://example.com/a world")


class StageFailureIsCapturedTests(unittest.TestCase):
    def test_understand_failure_falls_back_and_records_error(self) -> None:
        def understand(_obs, _in):
            raise RuntimeError("classifier exploded")

        result = run_runtime_loop(_input(), understand_fn=understand)
        # Loop continued and produced a deterministic fallback intent
        # so Decide / Act always have something usable.
        self.assertEqual(result.intent.intent_id, INTENT_GENERAL_CHAT)
        self.assertIn("understand", result.error or "")
        self.assertIn("classifier exploded", result.error or "")

    def test_decide_failure_emits_safe_reply_action(self) -> None:
        def decide(*_a, **_kw):
            raise RuntimeError("decide blew up")

        result = run_runtime_loop(_input(), decide_fn=decide)
        self.assertEqual(result.primary_action_id, ACTION_REPLY)
        self.assertIn("decide", result.error or "")

    def test_observe_failure_short_circuits_with_error(self) -> None:
        def observe(_in):
            raise RuntimeError("observe down")

        result = run_runtime_loop(_input(), observe_fn=observe)
        self.assertIn("observe", result.error or "")
        # The result is still safe to render — every field is filled.
        self.assertEqual(result.observation.role_id, "gateway")

    def test_record_failure_does_not_clobber_actions(self) -> None:
        def act(decision, _in):
            return tuple(decision.actions) or (RuntimeAction(action_id=ACTION_NOOP),)

        def record(_result, _in):
            raise RuntimeError("record down")

        result = run_runtime_loop(_input(), act_fn=act, record_fn=record)
        # Action survived even though record failed.
        self.assertEqual(result.actions_taken[0].action_id, ACTION_NOOP)
        self.assertEqual(result.records, ())
        self.assertIn("record", result.error or "")


class ActionPropagationTests(unittest.TestCase):
    def test_act_can_override_decision_actions(self) -> None:
        def decide(_obs, intent, _recall, plan, _in):
            return RuntimeDecision(
                intent=intent,
                research_plan=plan,
                actions=(RuntimeAction(action_id=ACTION_NOOP),),
            )

        def act(_decision, _in):
            return (RuntimeAction(action_id=ACTION_REPLY, payload={"text": "안녕"}),)

        result = run_runtime_loop(_input(), decide_fn=decide, act_fn=act)
        self.assertEqual(result.primary_action_id, ACTION_REPLY)
        self.assertEqual(result.actions_taken[0].payload["text"], "안녕")


if __name__ == "__main__":
    unittest.main()
