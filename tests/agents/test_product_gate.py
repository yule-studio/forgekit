"""Product intake gate — gateway seam + presenter (pure, additive)."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.product_intake.gate import run_product_gate, should_intercept
from yule_engineering.agents.product_intake.presenter import (
    handoff_summary,
    operator_status_line,
)
from yule_engineering.agents.product_intake.models import (
    READINESS_CLARIFICATION,
    READINESS_IMPLEMENTATION_CANDIDATE,
)


class InterceptTests(unittest.TestCase):
    def test_feature_request_intercepted(self) -> None:
        self.assertTrue(should_intercept("영상 업로드 서비스 구현해줘"))
        self.assertTrue(should_intercept("로그인 기능 만들어줘"))

    def test_non_feature_not_intercepted(self) -> None:
        self.assertFalse(should_intercept("오늘 일정 알려줘"))
        self.assertFalse(should_intercept(""))

    def test_non_intercepted_passes_through(self) -> None:
        out = run_product_gate("오늘 날씨 어때")
        self.assertFalse(out.intercepted)
        self.assertIsNone(out.packet)


class GateFlowTests(unittest.TestCase):
    def test_vague_video_clarification(self) -> None:
        out = run_product_gate("영상 업로드 서비스 구현해줘")
        self.assertTrue(out.intercepted)
        self.assertEqual(out.state, READINESS_CLARIFICATION)
        self.assertFalse(out.handoff_ready)
        # rendered questions carry numbered options + a recommendation tag
        text = "\n".join(out.clarification_questions)
        self.assertIn("공개 정책", text)
        self.assertIn("(추천)", text)

    def test_fully_specified_is_implementation_candidate(self) -> None:
        out = run_product_gate("누구나 업로드, 즉시 공개, 최신순 노출되는 영상 업로드 기능 만들어줘")
        self.assertEqual(out.state, READINESS_IMPLEMENTATION_CANDIDATE)
        self.assertTrue(out.handoff_ready)
        self.assertEqual(out.clarification_questions, ())

    def test_handoff_summary_carries_packet(self) -> None:
        out = run_product_gate("영상 업로드 서비스 구현해줘")
        summary = "\n".join(handoff_summary(out.packet))
        self.assertIn("product packet", summary)
        self.assertIn("acceptance criteria", summary)
        self.assertIn("non-goals", summary)
        self.assertIn("implied features", summary)

    def test_operator_status_distinguishes_pm_vs_handoff(self) -> None:
        clar = run_product_gate("영상 업로드 서비스 구현해줘")
        self.assertIn("PM clarification", operator_status_line(clar.packet))
        ready = run_product_gate("누구나 업로드, 즉시 공개, 최신순 노출되는 영상 업로드 기능 만들어줘")
        self.assertIn("handoff ready", operator_status_line(ready.packet))

    def test_outcome_serializable(self) -> None:
        d = run_product_gate("영상 업로드 서비스 구현해줘").to_dict()
        self.assertTrue(d["intercepted"])
        self.assertIn("packet", d)
        self.assertEqual(d["state"], READINESS_CLARIFICATION)


if __name__ == "__main__":
    unittest.main()
