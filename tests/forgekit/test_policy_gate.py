"""Submit policy gate (WT1 runtime-teeth) — real enforcement BEFORE the provider call.

Proves the teeth: approval-wait/hold-all → HOLD (no provider), budget posture crossed
→ THROTTLE, otherwise ALLOW with the mode's routing target. Plus the service applies
the gate + records honest usage_basis=estimate (never fake-live). Pure → bare CI.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.chat import models as m
from forgekit_console.chat import policy_gate as G
from forgekit_console.chat.service import SubmitService
from forgekit_console.policy import runtime_mode as rm
from forgekit_console.policy.main_profile import profile_for


def _policy(mode):
    return rm.resolve_effective_policy(profile_for("claude"), mode)


class GateDecisionTests(unittest.TestCase):
    def test_no_policy_allows(self) -> None:
        self.assertEqual(G.evaluate_gate(G.SubmitContext()).action, G.GATE_ALLOW)

    def test_approval_wait_holds(self) -> None:
        ctx = G.SubmitContext(runtime_mode="Approval-wait",
                              effective_policy=_policy(rm.MODE_APPROVAL_WAIT))
        d = G.evaluate_gate(ctx)
        self.assertEqual(d.action, G.GATE_HOLD)
        self.assertIn("보류", d.reason)
        held = d.held_result("Approval-wait")
        self.assertTrue(held.held)
        self.assertEqual(held.category, m.CAT_POLICY_HELD)
        self.assertFalse(held.ok)

    def test_budget_posture_throttles(self) -> None:
        # cost-save → usage_mode strict (reserve 0) → spent >= budget throttles
        ctx = G.SubmitContext(
            runtime_mode="Cost-save", effective_policy=_policy(rm.MODE_COST_SAVE),
            usage=G.UsageSnapshot(spent_tokens=1000, budget_tokens=1000))
        d = G.evaluate_gate(ctx)
        self.assertEqual(d.action, G.GATE_THROTTLE)
        held = d.held_result("Cost-save")
        self.assertTrue(held.throttled)
        self.assertEqual(held.category, m.CAT_BUDGET_THROTTLED)

    def test_under_budget_allows_with_routing(self) -> None:
        ctx = G.SubmitContext(
            runtime_mode="Interactive", effective_policy=_policy(rm.MODE_INTERACTIVE),
            usage=G.UsageSnapshot(spent_tokens=10, budget_tokens=1000))
        d = G.evaluate_gate(ctx)
        self.assertEqual(d.action, G.GATE_ALLOW)
        self.assertEqual(d.routing_target, "claude")   # the mode's resolved provider


class FakeTransport:
    def __init__(self, reply="안녕하세요"):
        self.reply = reply
        self.calls = 0

    def openai_chat(self, **k):
        self.calls += 1
        return self.reply

    def ollama_reachable(self, e):
        return True

    def ollama_models(self, e):
        return ("gemma3:latest",)


class ServiceEnforcementTests(unittest.TestCase):
    def test_held_context_does_not_call_provider(self) -> None:
        t = FakeTransport()
        svc = SubmitService(transport=t, env={}, config={})
        ctx = G.SubmitContext(runtime_mode="Approval-wait",
                              effective_policy=_policy(rm.MODE_APPROVAL_WAIT))
        out = svc.submit("hi", context=ctx)
        self.assertTrue(out.held)
        self.assertEqual(t.calls, 0)   # provider NOT called (real hold)
        self.assertEqual(out.runtime_mode, "Approval-wait")

    def test_live_records_estimate_usage(self) -> None:
        t = FakeTransport(reply="이것은 한 문장 응답입니다.")
        svc = SubmitService(transport=t, env={}, config={})  # zero-config → ollama
        # ollama-routed policy → routing_target=ollama (openai-compatible, live)
        pol = rm.resolve_effective_policy(profile_for("ollama"), rm.MODE_INTERACTIVE)
        ctx = G.SubmitContext(runtime_mode="Interactive", effective_policy=pol)
        out = svc.submit("질문 텍스트", context=ctx)
        self.assertTrue(out.is_live)
        self.assertEqual(t.calls, 1)
        self.assertEqual(out.usage_basis, m.USAGE_ESTIMATE)   # honest — not fake-live
        self.assertGreater(out.total_tokens, 0)
        self.assertEqual(out.total_tokens, out.input_tokens + out.output_tokens)
        self.assertIn("estimate", out.receipt())
        self.assertIn("mode=Interactive", out.receipt())


if __name__ == "__main__":
    unittest.main()
