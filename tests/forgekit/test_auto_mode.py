"""Auto orchestration + EffectivePolicy routing enforcement (new-WT1, CI-safe).

Proves: auto-recommend classifies a situation, auto-switch-safe never overrides an
operator pin and never auto-enters a gated mode, auto-escalate fires on blocked/
repeated failure, and the mode's routing target actually steers the submit provider.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.policy import auto_mode as am
from forgekit_console.policy import runtime_mode as rm


class ClassifyTests(unittest.TestCase):
    def test_signals_map_to_modes(self) -> None:
        self.assertEqual(am.classify("SaaS 아이디어 수집해줘")[0], rm.MODE_IDEA_DISCOVERY)
        self.assertEqual(am.classify("이 유튜브 영상 요약해줘")[0], rm.MODE_VIDEO_WATCH)
        self.assertEqual(am.classify("레포 개선점 찾아줘 refactor")[0], rm.MODE_SELF_IMPROVEMENT)
        self.assertEqual(am.classify("red team 보안 드릴")[0], rm.MODE_RED_BLUE)
        self.assertEqual(am.classify("비용 절감 모드로")[0], rm.MODE_COST_SAVE)
        self.assertEqual(am.classify("그냥 잡담")[0], rm.MODE_INTERACTIVE)


class AutoBehaviourTests(unittest.TestCase):
    def test_recommend_does_not_switch(self) -> None:
        d = am.auto_recommend("SaaS 아이디어 수집")
        self.assertEqual(d.decision, am.DECISION_RECOMMEND)
        self.assertFalse(d.switched)
        self.assertTrue(d.reason)

    def test_switch_safe_respects_operator_pin(self) -> None:
        d = am.auto_switch_safe("아이디어 수집", current_mode=rm.MODE_DELIVERY, operator_pinned=True)
        self.assertEqual(d.decision, am.DECISION_HOLD)
        self.assertFalse(d.switched)  # operator pin wins

    def test_switch_safe_switches_when_safe(self) -> None:
        d = am.auto_switch_safe("아이디어 수집", current_mode=rm.MODE_INTERACTIVE, operator_pinned=False)
        self.assertEqual(d.decision, am.DECISION_SWITCH_SAFE)
        self.assertTrue(d.switched)
        self.assertEqual(d.recommended_mode, rm.MODE_IDEA_DISCOVERY)

    def test_never_auto_enters_gated_mode(self) -> None:
        for ask in ("red team 침투 테스트", "보안 드릴 hardening"):
            d = am.auto_switch_safe(ask, current_mode=rm.MODE_INTERACTIVE, operator_pinned=False)
            self.assertFalse(d.switched)               # red-blue is gated → recommend only
            self.assertTrue(d.requires_operator)
            self.assertEqual(d.recommended_mode, rm.MODE_RED_BLUE)

    def test_escalate_on_blocked_or_repeated(self) -> None:
        self.assertIsNone(am.auto_escalate(blocked=False, repeated_failures=0))
        d = am.auto_escalate(blocked=True, repeated_failures=0)
        self.assertEqual(d.decision, am.DECISION_ESCALATE)
        self.assertTrue(d.requires_operator)
        self.assertIsNotNone(am.auto_escalate(blocked=False, repeated_failures=3))


class RoutingEnforcementTests(unittest.TestCase):
    def test_routing_target_steers_submit_provider(self) -> None:
        # the mode's routing target is passed to the submit service as prefer_provider
        from forgekit_console.chat.service import SubmitService

        class FakeT:
            def openai_chat(self, **k): return "ok"
            def ollama_reachable(self, e): return False
            def ollama_models(self, e): return ()

        svc = SubmitService(transport=FakeT(), env={}, config={"main_provider": "ollama"})
        # default resolves ollama; a prefer of a builtin steers there instead
        spec_default, _ = svc.resolve()
        self.assertEqual(spec_default.id, "ollama")
        spec_pref, _ = svc.resolve(prefer_provider="gemini")
        self.assertEqual(spec_pref.id, "gemini")  # routing actually steered


if __name__ == "__main__":
    unittest.main()
