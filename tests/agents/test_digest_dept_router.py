"""F13 부서 라우터 회귀."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.digest.dept_router import (
    DEPARTMENTS,
    classify_evidence,
)


class DeptRouterTests(unittest.TestCase):
    def test_owasp_routes_to_engineering(self) -> None:
        v = classify_evidence(host="owasp.org", title="XSS 방어", summary="cheat sheet 갱신")
        self.assertEqual(v.primary, "engineering")

    def test_apple_hig_routes_to_design(self) -> None:
        v = classify_evidence(host="developer.apple.com", title="HIG 업데이트", summary="")
        self.assertEqual(v.primary, "design")

    def test_unknown_host_defaults_to_engineering(self) -> None:
        v = classify_evidence(host="totally-unknown.example", title="hi", summary="")
        self.assertEqual(v.primary, "engineering")
        self.assertFalse(v.meeting_trigger)

    def test_multi_dept_keyword_triggers_meeting(self) -> None:
        v = classify_evidence(
            host="owasp.org",
            title="Breaking change in OAuth flow",
            summary="affects accessibility and security review",
        )
        self.assertTrue(v.meeting_trigger)

    def test_tech_lead_source_always_triggers_meeting(self) -> None:
        v = classify_evidence(
            host="martinfowler.com",
            title="event sourcing",
            summary="architecture note",
        )
        self.assertTrue(v.meeting_trigger)

    def test_design_keyword_in_design_host_no_meeting(self) -> None:
        v = classify_evidence(host="material.io", title="버튼 컴포넌트 갱신", summary="")
        self.assertFalse(v.meeting_trigger)
        self.assertEqual(v.primary, "design")

    def test_departments_constant(self) -> None:
        self.assertEqual(set(DEPARTMENTS), {"planning", "design", "engineering"})


if __name__ == "__main__":
    unittest.main()
