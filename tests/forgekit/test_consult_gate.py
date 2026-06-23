"""Consult-required merge gate — adjudication + artifact verification + wave roll-up.

The integration-wave merge criterion: a design/review-bearing change may not merge
without a consult artifact (valid consult verdict / design-decision ref / recorded waive).
A pure verification/QA/docs change needs no consult. A *fake* consult does not satisfy.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_runtime.decision_lane import (
    CONSULT_MISSING,
    CONSULT_NOT_REQUIRED,
    CONSULT_SATISFIED,
    CONSULT_WAIVED,
    ChangeUnderReview,
    ConsultNote,
    adjudicate_consult,
    consult_gate_report,
    consult_required,
)


def _good_consult() -> ConsultNote:
    return ConsultNote(
        "c-stack-1", "ORM 선택", "tech-lead",
        to_roles=("backend-engineer",), question="JPA vs jOOQ — 트랜잭션 경계 영향?")


class ConsultRequiredTests(unittest.TestCase):
    def test_design_kinds_require_consult(self) -> None:
        for kind in ("design", "architecture", "stack", "api-contract", "security", "ux"):
            self.assertTrue(
                consult_required(ChangeUnderReview("p", change_kinds=(kind,))), kind)

    def test_pure_verification_kinds_do_not_require(self) -> None:
        for kinds in (("test",), ("docs",), ("qa", "integration"), ("evidence", "ci")):
            self.assertFalse(
                consult_required(ChangeUnderReview("p", change_kinds=kinds)), kinds)

    def test_mixed_kinds_require_if_any_design(self) -> None:
        # a docs+test change that ALSO touches api-contract is consult-required.
        self.assertTrue(consult_required(
            ChangeUnderReview("p", change_kinds=("docs", "test", "api-contract"))))

    def test_kind_matching_is_case_insensitive(self) -> None:
        self.assertTrue(consult_required(
            ChangeUnderReview("p", change_kinds=("Design", "  STACK "))))


class AdjudicationTests(unittest.TestCase):
    def test_not_required_passes(self) -> None:
        v = adjudicate_consult(
            ChangeUnderReview("PR#1", change_kinds=("integration", "test", "docs")))
        self.assertEqual(v.status, CONSULT_NOT_REQUIRED)
        self.assertFalse(v.required)
        self.assertFalse(v.blocker)
        self.assertTrue(v.merge_ok())

    def test_required_missing_is_blocker(self) -> None:
        v = adjudicate_consult(
            ChangeUnderReview("PR#2", change_kinds=("design", "api-contract")))
        self.assertEqual(v.status, CONSULT_MISSING)
        self.assertTrue(v.required)
        self.assertTrue(v.blocker)
        self.assertFalse(v.merge_ok())

    def test_required_valid_consult_satisfies(self) -> None:
        v = adjudicate_consult(
            ChangeUnderReview("PR#3", change_kinds=("stack",), consult=_good_consult()))
        self.assertEqual(v.status, CONSULT_SATISFIED)
        self.assertFalse(v.blocker)
        self.assertEqual(v.artifact, "consult:c-stack-1")

    def test_required_design_ref_satisfies(self) -> None:
        v = adjudicate_consult(ChangeUnderReview(
            "PR#4", change_kinds=("architecture",), design_refs=("decision-2026-06-22-a",)))
        self.assertEqual(v.status, CONSULT_SATISFIED)
        self.assertFalse(v.blocker)
        self.assertTrue(v.artifact.startswith("design-log:"))

    def test_required_waive_passes_with_reason(self) -> None:
        v = adjudicate_consult(ChangeUnderReview(
            "PR#5", change_kinds=("design",), waive_reason="버튼 색만 토큰값 교체, 디자인 결정 아님"))
        self.assertEqual(v.status, CONSULT_WAIVED)
        self.assertFalse(v.blocker)
        self.assertTrue(v.artifact.startswith("waived:"))

    def test_blank_waive_does_not_pass(self) -> None:
        # a whitespace-only waive reason is not a recorded reason.
        v = adjudicate_consult(
            ChangeUnderReview("PR#6", change_kinds=("design",), waive_reason="   "))
        self.assertEqual(v.status, CONSULT_MISSING)
        self.assertTrue(v.blocker)

    def test_fake_consult_does_not_satisfy(self) -> None:
        # a consult with no consultee and no question is rejected by validate_consult,
        # so it cannot satisfy the gate — and the verdict says why.
        fake = ConsultNote("c-fake", "x", "tech-lead", to_roles=(), question="")
        v = adjudicate_consult(
            ChangeUnderReview("PR#7", change_kinds=("design",), consult=fake))
        self.assertEqual(v.status, CONSULT_MISSING)
        self.assertTrue(v.blocker)
        self.assertTrue(any("무효" in r for r in v.reasons))

    def test_fake_consult_but_design_ref_present_still_satisfies(self) -> None:
        # a bad consult does not block a change that also carries a real decision ref;
        # the fake is surfaced as context but the design-log satisfies the gate.
        fake = ConsultNote("c-fake", "x", "tech-lead", to_roles=(), question="")
        v = adjudicate_consult(ChangeUnderReview(
            "PR#8", change_kinds=("stack",), consult=fake, design_refs=("decision-x",)))
        self.assertEqual(v.status, CONSULT_SATISFIED)
        self.assertFalse(v.blocker)


class ReportTests(unittest.TestCase):
    def test_report_splits_and_blocks(self) -> None:
        changes = [
            ChangeUnderReview("intake", change_kinds=("integration", "test")),
            ChangeUnderReview("armory", change_kinds=("docs",)),
            ChangeUnderReview("api", change_kinds=("api-contract",), consult=_good_consult()),
            ChangeUnderReview("ux", change_kinds=("ux",), waive_reason="copy 문구만"),
            ChangeUnderReview("schema", change_kinds=("schema", "data-model")),  # missing
        ]
        rep = consult_gate_report(changes)
        self.assertEqual({v.ref for v in rep.satisfied}, {"api"})
        self.assertEqual({v.ref for v in rep.waived}, {"ux"})
        self.assertEqual({v.ref for v in rep.missing}, {"schema"})
        self.assertEqual({v.ref for v in rep.not_required}, {"intake", "armory"})
        self.assertTrue(rep.merge_blocked)  # one missing → whole wave blocked

    def test_report_not_blocked_when_all_covered(self) -> None:
        changes = [
            ChangeUnderReview("a", change_kinds=("integration",)),
            ChangeUnderReview("b", change_kinds=("design",), design_refs=("d-1",)),
            ChangeUnderReview("c", change_kinds=("stack",), waive_reason="검증된 기본값 유지"),
        ]
        rep = consult_gate_report(changes)
        self.assertFalse(rep.merge_blocked)
        self.assertEqual(len(rep.missing), 0)

    def test_report_lines_render(self) -> None:
        rep = consult_gate_report([ChangeUnderReview("x", change_kinds=("design",))])
        lines = rep.lines()
        self.assertTrue(any("MERGE BLOCKED" in ln for ln in lines))

    def test_report_to_dict_roundtrip(self) -> None:
        rep = consult_gate_report([
            ChangeUnderReview("a", change_kinds=("test",)),
            ChangeUnderReview("b", change_kinds=("security",)),
        ])
        d = rep.to_dict()
        self.assertEqual(d["missing"], ["b"])
        self.assertEqual(d["not_required"], ["a"])
        self.assertTrue(d["merge_blocked"])


if __name__ == "__main__":
    unittest.main()
