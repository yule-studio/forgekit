"""ForgeKit 도입 효율 검토 — 8점 검토 + 3축 리뷰 + adopt-now/collect-first/hold + no fake adoption.

외부 plugin/skill/collector/rule/workflow/tool 후보가 "좋아 보인다"만으로 채택되지 않음을
강제한다: 8점 검토 substantive, PM+tech-lead+specialist 3축, verdict 3종, adopted≠equipped,
collect-first/hold 는 장착 금지(fake adoption 차단).
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_runtime.decision_lane import (
    VERDICT_ADOPT_NOW,
    VERDICT_COLLECT_FIRST,
    VERDICT_HOLD,
    ToolAdoptionReview,
    adoption_artifact_ref,
    adoption_review_report,
    can_equip,
    has_three_axis_review,
    validate_adoption_review,
)

_FIELDS = dict(
    current_pain="discovery 수집 토픽을 매번 수동 튜닝해 1차 분류가 느리다",
    expected_benefit="RSS 자동 큐레이션으로 1차 분류 시간을 절반으로 줄인다",
    overlap_with_existing="기존 discovery sweep 와 부분 중복 — normalizer 재사용 가능",
    operational_cost="runtime tick 1개 추가 + state_dir 용량 소폭 증가",
    maintenance_risk="외부 RSS 스키마 변경 시 파서가 깨질 수 있다",
    provider_runtime_fit="provider 무관, runtime tick 에 bounded 로 배선 가능",
    governance_security_impact="outbound fetch only, secret 없음, redaction 불필요",
    why_now="pain 이 명확하고 기존 normalizer 를 재사용하므로 adopt-now",
)


def _review(**over) -> ToolAdoptionReview:
    base = dict(candidate_id="rss-collector", candidate_kind="collector",
                verdict=VERDICT_ADOPT_NOW,
                reviewers=("product-manager", "tech-lead", "backend-engineer"),
                adopted=True, **_FIELDS)
    base.update(over)
    return ToolAdoptionReview(**base)


class ValidAdoptionTests(unittest.TestCase):
    def test_full_adopt_now_is_valid_and_equippable(self) -> None:
        r = _review(equipped=True)
        self.assertEqual(validate_adoption_review(r), ())
        self.assertTrue(can_equip(r))
        self.assertEqual(adoption_artifact_ref(r), "adoption:rss-collector:adopt-now")

    def test_three_axis_requires_specialist_beyond_pm_techlead(self) -> None:
        self.assertTrue(has_three_axis_review(_review()))
        self.assertFalse(has_three_axis_review(_review(reviewers=("product-manager", "tech-lead"))))


class AntiFakeTests(unittest.TestCase):
    def test_blank_efficiency_point_rejected(self) -> None:
        r = _review(maintenance_risk="")
        self.assertTrue(any("maintenance_risk" in e for e in validate_adoption_review(r)))

    def test_placeholder_efficiency_point_rejected(self) -> None:
        # too-short field is a placeholder, not a reason.
        r = _review(expected_benefit="ok")
        self.assertTrue(any("expected_benefit" in e for e in validate_adoption_review(r)))

    def test_unknown_candidate_kind_rejected(self) -> None:
        r = _review(candidate_kind="gadget")
        self.assertTrue(any("candidate_kind" in e for e in validate_adoption_review(r)))

    def test_adopt_now_without_three_axis_rejected_and_not_equippable(self) -> None:
        r = _review(reviewers=("product-manager", "tech-lead"))
        self.assertTrue(any("3축" in e for e in validate_adoption_review(r)))
        self.assertFalse(can_equip(r))

    def test_unknown_reviewer_role_rejected(self) -> None:
        r = _review(reviewers=("product-manager", "tech-lead", "wizard-of-oz"))
        self.assertTrue(any("레지스트리" in e for e in validate_adoption_review(r)))

    def test_adopted_flag_must_mirror_verdict(self) -> None:
        # collect-first with adopted=True is inconsistent.
        r = _review(verdict=VERDICT_COLLECT_FIRST, adopted=True)
        self.assertTrue(any("verdict 와 불일치" in e for e in validate_adoption_review(r)))

    def test_collect_first_cannot_be_equipped(self) -> None:
        r = _review(verdict=VERDICT_COLLECT_FIRST, adopted=False, equipped=True)
        viol = validate_adoption_review(r)
        self.assertTrue(any("fake adoption" in e for e in viol))
        self.assertTrue(any("장착(equip) 금지" in e for e in viol))
        self.assertFalse(can_equip(r))

    def test_hold_is_valid_but_never_equippable(self) -> None:
        r = _review(verdict=VERDICT_HOLD, adopted=False)
        self.assertEqual(validate_adoption_review(r), ())
        self.assertFalse(can_equip(r))

    def test_invalid_review_has_no_artifact_ref(self) -> None:
        # a review that fails validation cannot satisfy the consult gate.
        r = _review(current_pain="")
        self.assertEqual(adoption_artifact_ref(r), "")


class ReportTests(unittest.TestCase):
    def test_report_splits_by_verdict(self) -> None:
        rep = adoption_review_report([
            _review(candidate_id="rss-collector", equipped=True),
            _review(candidate_id="ponytail-cli", verdict=VERDICT_COLLECT_FIRST, adopted=False),
            _review(candidate_id="big-framework", verdict=VERDICT_HOLD, adopted=False),
        ])
        self.assertEqual({r.candidate_id for r in rep.adopt_now}, {"rss-collector"})
        self.assertEqual({r.candidate_id for r in rep.collect_first}, {"ponytail-cli"})
        self.assertEqual({r.candidate_id for r in rep.hold}, {"big-framework"})
        self.assertEqual({r.candidate_id for r in rep.equipped}, {"rss-collector"})
        self.assertFalse(rep.fake_adoption_blocked)

    def test_report_flags_fake_adoption(self) -> None:
        rep = adoption_review_report([
            _review(candidate_id="ok"),
            _review(candidate_id="sneaky", verdict=VERDICT_COLLECT_FIRST, adopted=False, equipped=True),
        ])
        self.assertTrue(rep.fake_adoption_blocked)
        self.assertIn("sneaky", {r.candidate_id for r in rep.invalid})

    def test_report_lines_render(self) -> None:
        rep = adoption_review_report([_review(candidate_id="sneaky",
                                              verdict=VERDICT_HOLD, adopted=False, equipped=True)])
        self.assertTrue(any("FAKE ADOPTION BLOCKED" in ln for ln in rep.lines()))


if __name__ == "__main__":
    unittest.main()
