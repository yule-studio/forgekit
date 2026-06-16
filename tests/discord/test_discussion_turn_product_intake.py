"""Integration tests — PM intake pre-step wired into the discussion turn.

PM intake gate(``product_intake_seam``) 가 ``build_discussion_turn_response``
앞단에 additive 하게 끼었을 때:

1. vague feature request → PM clarification short-circuit (engineering 분류/합성
   을 건너뛰고 PM 결정 질문만).
2. fully-specified feature → product packet 이 engineering 본문 앞에 carry 되고
   engineering flow 가 그대로 이어짐.
3. non-feature request → 기존 engineering flow 무변경 (regression).
4. gate OFF(기본값) → 제품 요청이어도 byte-for-byte 기존 동작.

PM clarification 은 engineering 의 기술 clarification 과 라벨/operator state 가
분리되어 있어야 한다 (PM ≠ engineering).
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.discussion import DiscussionMode
from yule_discord.engineering.discussion_turn import (
    OPERATOR_STATE_CLARIFICATION,
    build_discussion_turn_response,
)
from yule_discord.engineering.product_intake_seam import (
    PM_STATE_CLARIFICATION,
    PM_STATE_HANDOFF_READY,
    run_product_intake,
)


class VagueFeatureClarificationTestCase(unittest.TestCase):
    """vague feature request → PM clarification short-circuit."""

    def test_vague_feature_short_circuits_to_pm_clarification(self) -> None:
        result = build_discussion_turn_response(
            message_text="영상 업로드 서비스 구현해줘",
            product_intake_gate=True,
        )
        # PM 단계 — engineering 분류/합성을 돌지 않았다.
        self.assertIsNotNone(result.product_intake)
        self.assertTrue(result.product_intake.intercepted)
        self.assertTrue(result.product_intake.short_circuit)
        self.assertIsNone(result.classification)
        self.assertIsNone(result.synthesis)
        self.assertIsNone(result.handoff)

    def test_pm_clarification_label_is_distinct_from_engineering(self) -> None:
        result = build_discussion_turn_response(
            message_text="영상 업로드 서비스 구현해줘",
            product_intake_gate=True,
        )
        # PM 라벨이 본문에 명시되고 engineering 토의/clarification 헤더가 아님.
        self.assertIn("PM clarification", result.rendered_text)
        self.assertNotIn("**모드:**", result.rendered_text)
        # 번호 옵션 + 추천 picks.
        self.assertIn("(추천)", result.rendered_text)
        self.assertIn("공개 정책", result.rendered_text)

    def test_pm_operator_status_is_product_layer(self) -> None:
        result = build_discussion_turn_response(
            message_text="영상 업로드 서비스 구현해줘",
            product_intake_gate=True,
        )
        status = result.operator_status
        # PM state 는 engineering state 와 prefix/layer 로 구분된다.
        self.assertEqual(status["layer"], "product")
        self.assertEqual(status["state"], PM_STATE_CLARIFICATION)
        self.assertNotEqual(status["state"], OPERATOR_STATE_CLARIFICATION)
        self.assertEqual(status["primary_actor"], "user")
        self.assertIn("PM clarification", status["headline"])


class SpecifiedFeatureHandoffTestCase(unittest.TestCase):
    """fully-specified feature → packet carried, engineering continues."""

    def test_specified_feature_carries_packet_then_proceeds(self) -> None:
        result = build_discussion_turn_response(
            message_text="누구나 업로드, 즉시 공개, 최신순 노출되는 영상 업로드 기능 만들어줘",
            product_intake_gate=True,
        )
        # 가로챘지만 short-circuit 하지 않음 — engineering 으로 계속.
        self.assertTrue(result.product_intake.intercepted)
        self.assertFalse(result.product_intake.short_circuit)
        self.assertEqual(
            result.product_intake.operator_status["state"], PM_STATE_HANDOFF_READY
        )
        # engineering 분류/합성이 정상적으로 돌았다.
        self.assertIsNotNone(result.classification)
        self.assertIsNotNone(result.synthesis)

    def test_packet_summary_carried_into_rendered_text(self) -> None:
        result = build_discussion_turn_response(
            message_text="누구나 업로드, 즉시 공개, 최신순 노출되는 영상 업로드 기능 만들어줘",
            product_intake_gate=True,
        )
        text = result.rendered_text
        # product packet 요약(acceptance / implied / non-goals)이 본문에 carry.
        self.assertIn("PM product packet", text)
        self.assertIn("acceptance criteria", text)
        self.assertIn("implied features", text)
        self.assertIn("non-goals", text)
        # engineering 응답 본문도 packet 뒤에 그대로 이어진다 (mode 헤더).
        self.assertIn("**모드:**", text)


class NonFeatureRegressionTestCase(unittest.TestCase):
    """non-feature request → 기존 engineering flow 무변경."""

    def test_non_feature_with_gate_on_unchanged(self) -> None:
        # gate ON 이어도 비-제품 요청은 가로채지 않는다.
        result = build_discussion_turn_response(
            message_text="이 구조 맞아? devops 관점에서 어떻게 풀지",
            product_intake_gate=True,
        )
        self.assertIsNotNone(result.product_intake)
        self.assertFalse(result.product_intake.intercepted)
        # 기존 engineering 토의 동작 그대로.
        self.assertEqual(result.classification.mode, DiscussionMode.DISCUSSION)
        self.assertIn("devops", result.rendered_text.lower())
        self.assertIn("권한 제안", result.rendered_text)

    def test_gate_off_is_byte_for_byte_unchanged_for_product_ask(self) -> None:
        # gate 기본값(off) — 제품 요청이어도 PM pre-step 이 돌지 않는다.
        off = build_discussion_turn_response(
            message_text="영상 업로드 서비스 구현해줘",
        )
        explicit_off = build_discussion_turn_response(
            message_text="영상 업로드 서비스 구현해줘",
            product_intake_gate=False,
        )
        self.assertIsNone(off.product_intake)
        self.assertIsNone(explicit_off.product_intake)
        # 두 호출 모두 engineering flow 를 그대로 탔다.
        self.assertEqual(off.rendered_text, explicit_off.rendered_text)
        self.assertIsNotNone(off.classification)


class SeamUnitTestCase(unittest.TestCase):
    """``run_product_intake`` seam 단독 동작 (discussion turn 과 독립)."""

    def test_non_product_passes_through(self) -> None:
        res = run_product_intake("오늘 일정 알려줘")
        self.assertFalse(res.intercepted)
        self.assertFalse(res.short_circuit)
        self.assertEqual(res.operator_status, {})

    def test_clarification_short_circuits(self) -> None:
        res = run_product_intake("영상 업로드 서비스 구현해줘")
        self.assertTrue(res.intercepted)
        self.assertTrue(res.short_circuit)
        self.assertIn("PM clarification", res.rendered_text)
        self.assertEqual(res.operator_status["layer"], "product")

    def test_handoff_ready_carries_context_no_short_circuit(self) -> None:
        res = run_product_intake(
            "누구나 업로드, 즉시 공개, 최신순 노출되는 영상 업로드 기능 만들어줘"
        )
        self.assertTrue(res.intercepted)
        self.assertFalse(res.short_circuit)
        self.assertIn("PM product packet", res.handoff_context)
        self.assertEqual(res.operator_status["state"], PM_STATE_HANDOFF_READY)


if __name__ == "__main__":
    unittest.main()
