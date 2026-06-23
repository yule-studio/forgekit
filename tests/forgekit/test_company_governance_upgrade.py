"""ForgeKit company-governance upgrade — adoption review + merge receipt + log wiring.

Proves the wave's mandatory adoption discipline and the merge-boundary identity trail:

- ``AdoptionReview`` enforces the eight 도입 효율 검토 fields, a 3-axis review (proposer +
  canonical PM + canonical tech-lead + ≥1 engineering specialist), a ponytail verdict, and
  exactly one of adopt-now / collect-first / hold;
- the **adopted ≠ equipped** gate (``can_equip``): only a VALID adopt-now review may
  proceed to equipping; collect-first / hold / invalid → never (the Hephaistos split);
- collect-first requires a Nexus evidence ref (근거만 누적, no activation); adopt-now
  requires a follow-up owner + verification — no "looks good" adoption;
- ``MergeReceipt`` anti-fake: a ``merged`` outcome requires a registry executor, approval
  metadata, an identity trail, a passing CI, and a merge commit — no fake green merge;
- both artifacts wire into the replay-able governance log (KIND_ADOPTION / KIND_MERGE)
  with valid/invalid flags and an operator-readable decision trail.

Hermetic + pure: roles resolve from the identity registry; the log writes under an
isolated FORGEKIT_HOME tempdir.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _rel in (
    "packages/forgekit-runtime/src", "packages/forgekit-config/src",
    "packages/forgekit-provider/src", "packages/forgekit-contracts/src",
    "packages/forgekit-goal/src", "packages/hephaistos/src",
    "packages/armory/src", "packages/nexus/src", "apps/forgekit-console/src",
):
    _p = str(_ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import forgekit_runtime.decision_lane as DL
from forgekit_runtime.decision_lane import AdoptionReview, MergeReceipt, RejectedOption


def _review(**kw):
    base = dict(
        candidate_id="ruff", candidate_kind="tool",
        current_pain="lint 느림", expected_benefit="10x 빠른 lint",
        overlap_with_existing="flake8 대체", operational_cost="낮음",
        maintenance_risk="중간", provider_runtime_fit="python toolchain 적합",
        governance_security_impact="로컬 전용, secret 미접근", why_adopt_now="즉시 체감 + 낮은 비용",
        proposed_by="backend-engineer", reviewed_by_pm="product-manager",
        reviewed_by_tech_lead="tech-lead", specialist_consulted=("backend-engineer",),
        ponytail_verdict="wrapper 불필요, 직접 호출", adoption_verdict="adopt-now",
        follow_up_owner="devops-engineer", verification=("ruff --version",),
        rejected_alternatives=(RejectedOption(name="flake8", why_not="느림"),))
    base.update(kw)
    return AdoptionReview(**base)


def _merge(**kw):
    base = dict(
        pr_ref="430", issue_ref="429", branch="feat/x", merge_commit="abc123",
        executor="tech-lead", decision_ref="decision:1",
        approval_metadata="decision=..;level=L2",
        commit_trailers=("Forgekit-Agent: tech-lead", "Forgekit-Approval: .."),
        ci_status="passing", outcome="merged")
    base.update(kw)
    return MergeReceipt(**base)


class AdoptionReviewTests(unittest.TestCase):
    def test_valid_adopt_now_passes(self):
        self.assertEqual(DL.validate_adoption_review(_review()), ())

    def test_all_eight_fields_required(self):
        for attr in ("current_pain", "expected_benefit", "overlap_with_existing",
                     "operational_cost", "maintenance_risk", "provider_runtime_fit",
                     "governance_security_impact", "why_adopt_now"):
            v = DL.validate_adoption_review(_review(**{attr: ""}))
            self.assertTrue(v, f"{attr} 비었는데 통과함")

    def test_three_axis_review_required(self):
        # PM axis must be the canonical product-manager.
        self.assertTrue(DL.validate_adoption_review(_review(reviewed_by_pm="backend-engineer")))
        # tech-lead axis must be the canonical tech-lead.
        self.assertTrue(DL.validate_adoption_review(_review(reviewed_by_tech_lead="backend-engineer")))
        # ≥1 engineering specialist.
        self.assertTrue(DL.validate_adoption_review(_review(specialist_consulted=())))
        # a non-engineering "specialist" doesn't satisfy the axis.
        self.assertTrue(DL.validate_adoption_review(_review(specialist_consulted=("product-manager",))))

    def test_ponytail_verdict_required(self):
        self.assertTrue(DL.validate_adoption_review(_review(ponytail_verdict="")))

    def test_verdict_must_be_one_of_three(self):
        self.assertTrue(DL.validate_adoption_review(_review(adoption_verdict="maybe")))
        for verdict in DL.ADOPTION_VERDICTS:
            self.assertIn(verdict, ("adopt-now", "collect-first", "hold"))

    def test_adopt_now_requires_owner_and_verification(self):
        self.assertTrue(DL.validate_adoption_review(_review(follow_up_owner="")))
        self.assertTrue(DL.validate_adoption_review(_review(verification=())))

    def test_collect_first_requires_nexus_evidence(self):
        # collect-first without an evidence ref is rejected (근거 누적처 없음).
        self.assertTrue(DL.validate_adoption_review(
            _review(adoption_verdict="collect-first", nexus_evidence_ref="")))
        # with it, valid.
        self.assertEqual(DL.validate_adoption_review(
            _review(adoption_verdict="collect-first",
                    nexus_evidence_ref="nexus://ideas/x")), ())


class AdoptedNotEquippedTests(unittest.TestCase):
    def test_only_valid_adopt_now_can_equip(self):
        self.assertTrue(DL.can_equip(_review()))

    def test_collect_first_never_equips(self):
        r = _review(adoption_verdict="collect-first", nexus_evidence_ref="nexus://x")
        self.assertFalse(DL.can_equip(r))
        self.assertIn("collect-first", DL.equip_block_reason(r))

    def test_hold_never_equips(self):
        r = _review(adoption_verdict="hold")
        self.assertFalse(DL.can_equip(r))
        self.assertIn("hold", DL.equip_block_reason(r))

    def test_invalid_review_never_equips(self):
        r = _review(verification=())     # invalid adopt-now
        self.assertFalse(DL.can_equip(r))
        self.assertIn("미통과", DL.equip_block_reason(r))


class MergeReceiptTests(unittest.TestCase):
    def test_valid_merge_passes(self):
        self.assertEqual(DL.validate_merge_receipt(_merge()), ())

    def test_merged_requires_passing_ci(self):
        self.assertTrue(DL.validate_merge_receipt(_merge(ci_status="failing")))

    def test_merged_requires_identity_trail(self):
        self.assertTrue(DL.validate_merge_receipt(_merge(commit_trailers=())))

    def test_merged_requires_known_executor(self):
        self.assertTrue(DL.validate_merge_receipt(_merge(executor="nobody-9000")))

    def test_merged_requires_approval_and_commit(self):
        self.assertTrue(DL.validate_merge_receipt(_merge(approval_metadata="")))
        self.assertTrue(DL.validate_merge_receipt(_merge(merge_commit="")))

    def test_blocked_must_carry_reasons_and_no_trail(self):
        # blocked with no reasons → fake silent block.
        self.assertTrue(DL.validate_merge_receipt(
            MergeReceipt(pr_ref="9", outcome="blocked")))
        # blocked carrying an identity trail → fake approval.
        self.assertTrue(DL.validate_merge_receipt(
            MergeReceipt(pr_ref="9", outcome="blocked",
                         blocking_reasons=("ci red",), commit_trailers=("t",))))
        # honest blocked.
        self.assertEqual(DL.validate_merge_receipt(
            MergeReceipt(pr_ref="9", outcome="blocked",
                         blocking_reasons=("ci red",))), ())


class GovernanceLogWiringTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.env = {"FORGEKIT_HOME": self._dir}

    def test_adoption_and_merge_recorded_valid(self):
        evs = DL.record_lane_artifacts("sess", adoption=_review(), merge=_merge(),
                                       env=self.env)
        by_kind = {e.kind: e for e in evs}
        self.assertTrue(by_kind[DL.KIND_ADOPTION].valid)
        self.assertTrue(by_kind[DL.KIND_MERGE].valid)

    def test_fake_artifacts_recorded_invalid(self):
        evs = DL.record_lane_artifacts(
            "sess", adoption=_review(verification=()),
            merge=_merge(ci_status="failing"), env=self.env)
        by_kind = {e.kind: e for e in evs}
        self.assertFalse(by_kind[DL.KIND_ADOPTION].valid)
        self.assertFalse(by_kind[DL.KIND_MERGE].valid)

    def test_decision_trail_surfaces_facts(self):
        DL.record_lane_artifacts("sess", adoption=_review(), merge=_merge(), env=self.env)
        trail = DL.decision_trail_from_log(DL.replay_governance_log("sess", env=self.env))
        joined = "\n".join(trail)
        self.assertIn("verdict=adopt-now", joined)
        self.assertIn("3축", joined)
        self.assertIn("closes #429", joined)
        self.assertIn("identity-trail", joined)

    def test_new_kinds_registered(self):
        self.assertIn(DL.KIND_ADOPTION, DL.EVENT_KINDS)
        self.assertIn(DL.KIND_MERGE, DL.EVENT_KINDS)


if __name__ == "__main__":
    unittest.main()
