"""Design discomfort → improvement packet (design WT4) — owner-routed, user-value.

Proves: discomfort is structured as WHY-it's-a-user-problem (not aesthetics), routed
to the right design specialist, and promoted to a typed packet. Pure → CI.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.design import discomfort as DC
from forgekit_console.design import roles as R


class AnalyzeTests(unittest.TestCase):
    def test_routes_to_correct_specialist(self) -> None:
        self.assertEqual(DC.analyze_discomfort("버튼 간격이 좁아 누르기 불편").recommended_owner,
                         R.ROLE_UX_UI)
        self.assertEqual(DC.analyze_discomfort("토큰/컴포넌트 일관성이 깨짐").recommended_owner,
                         R.ROLE_DESIGN_SYSTEMS)
        self.assertEqual(DC.analyze_discomfort("브랜드 아이콘이 제각각").recommended_owner,
                         R.ROLE_ILLUSTRATION_BRAND)

    def test_frames_user_value_not_aesthetics(self) -> None:
        d = DC.analyze_discomfort("스페이싱이 불편", affected_flow="온보딩")
        self.assertTrue(d.why_it_matters)
        self.assertIn("마찰", d.why_it_matters)        # user-value framing
        self.assertEqual(d.affected_flow, "온보딩")
        self.assertEqual(d.ux_issue, "스페이싱이 불편")  # categorised


class PromoteTests(unittest.TestCase):
    def test_promotes_to_typed_packet_per_owner(self) -> None:
        ux = DC.analyze_discomfort("간격 불편")
        kind, pkt = DC.promote_to_packet(ux)
        self.assertEqual(kind, DC.PACKET_FRONTEND_IMPL)
        self.assertEqual(pkt.recommended_owner, R.ROLE_UX_UI)
        self.assertTrue(pkt.user_discomfort)

        sysd = DC.analyze_discomfort("토큰 불일치")
        kind2, _ = DC.promote_to_packet(sysd)
        self.assertEqual(kind2, DC.PACKET_DESIGN_SYSTEM_FIX)

    def test_reference_awareness_in_packet(self) -> None:
        from forgekit_console.uiref import figma_reference

        d = DC.analyze_discomfort("간격 불편")
        # missing reference → noted honestly in the why
        _, pkt = DC.promote_to_packet(d, reference=figma_reference())
        self.assertIn("reference", pkt.why_it_matters)


if __name__ == "__main__":
    unittest.main()
