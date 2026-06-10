"""approval_policy — A-M5-policy unit tests.

Pin every category from the spec:

  * L0/L1/L2/L3 default classification per action_type
  * High-risk actions (code/git/deploy/secret/external/data delete/
    Obsidian final knowledge) all map to L3
  * Public crawling / department feed post / dedup-tag map to L0/L1
  * Engineering risk review maps to L2
  * Risk metadata escalates lower defaults to L3 (critical risk,
    not reversible, external side effect, paid cost, sensitive data)
  * gateway_can_auto_approve refuses L3 (and L2)
  * Auto-approval audit record requires a non-empty reason
  * ``ApprovalDecision.to_approval_request`` only works for L3
  * ``ApprovalDecision.to_audit_record`` only works for non-L3
  * Markdown formatter surfaces every required field
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.approval_policy import (
    ACTION_CODE_WRITE,
    ACTION_DATA_DELETE,
    ACTION_DATA_OVERWRITE,
    ACTION_DEPARTMENT_FEED_POST,
    ACTION_DEPLOY,
    ACTION_ENGINEERING_CHANGE_REVIEW,
    ACTION_EXTERNAL_PAID_CALL,
    ACTION_EXTERNAL_PUBLICATION,
    ACTION_FILE_WRITE,
    ACTION_GIT_COMMIT,
    ACTION_GIT_PUSH,
    ACTION_GITHUB_PR_CREATE,
    ACTION_HEALTH_CHECK,
    ACTION_HEARTBEAT_EMIT,
    ACTION_INFRA_CHANGE,
    ACTION_OBSIDIAN_DRAFT_CREATE,
    ACTION_OBSIDIAN_FINAL_KNOWLEDGE_WRITE,
    ACTION_PUBLIC_RESEARCH_COLLECT,
    ACTION_RESEARCH_DEDUP_TAG,
    ACTION_RESEARCH_PROMOTION_CANDIDATE,
    ACTION_RSS_FETCH,
    ACTION_SECRET_ACCESS,
    ACTION_SUPERVISOR_SWEEP,
    ACTION_TECH_LEAD_RISK_REVIEW,
    AUTHORITY_HUMAN,
    AUTHORITY_POLICY,
    AUTHORITY_TECH_LEAD,
    COST_MAJOR,
    DATA_SECRET,
    RISK_CRITICAL,
    ActionContext,
    ApprovalLevel,
    decide_approval,
    format_audit_record_markdown,
    gateway_can_auto_approve,
)


def _ctx(action_type: str, **overrides) -> ActionContext:
    base = dict(
        action_type=action_type,
        session_id="sess-policy-1",
        job_id="job-1",
    )
    base.update(overrides)
    return ActionContext(**base)


# ---------------------------------------------------------------------------
# 1. Default level per action_type
# ---------------------------------------------------------------------------


class L0RecordOnlyTests(unittest.TestCase):
    """Plumbing actions need no audit interaction."""

    def test_heartbeat_is_l0(self) -> None:
        decision = decide_approval(_ctx(ACTION_HEARTBEAT_EMIT))
        self.assertEqual(decision.approval_level, ApprovalLevel.L0_RECORD_ONLY)
        self.assertEqual(decision.authority, AUTHORITY_POLICY)
        self.assertFalse(decision.human_approval_required)

    def test_supervisor_sweep_is_l0(self) -> None:
        decision = decide_approval(_ctx(ACTION_SUPERVISOR_SWEEP))
        self.assertEqual(decision.approval_level, ApprovalLevel.L0_RECORD_ONLY)

    def test_health_check_is_l0(self) -> None:
        decision = decide_approval(_ctx(ACTION_HEALTH_CHECK))
        self.assertEqual(decision.approval_level, ApprovalLevel.L0_RECORD_ONLY)


class L1AutoApprovedTests(unittest.TestCase):
    """Public crawling, department feed, dedup-tag, draft creation."""

    def test_public_research_collect_is_l1(self) -> None:
        decision = decide_approval(_ctx(ACTION_PUBLIC_RESEARCH_COLLECT))
        self.assertEqual(
            decision.approval_level, ApprovalLevel.L1_AUTO_APPROVED
        )
        self.assertEqual(decision.authority, AUTHORITY_POLICY)
        # Default reason template must populate so audit record is buildable.
        self.assertTrue(
            (decision.reason_human_approval_not_required or "").strip()
        )

    def test_rss_fetch_is_l1(self) -> None:
        self.assertEqual(
            decide_approval(_ctx(ACTION_RSS_FETCH)).approval_level,
            ApprovalLevel.L1_AUTO_APPROVED,
        )

    def test_department_feed_post_is_l1(self) -> None:
        self.assertEqual(
            decide_approval(_ctx(ACTION_DEPARTMENT_FEED_POST)).approval_level,
            ApprovalLevel.L1_AUTO_APPROVED,
        )

    def test_research_dedup_tag_is_l1(self) -> None:
        self.assertEqual(
            decide_approval(_ctx(ACTION_RESEARCH_DEDUP_TAG)).approval_level,
            ApprovalLevel.L1_AUTO_APPROVED,
        )

    def test_promotion_candidate_is_l1(self) -> None:
        self.assertEqual(
            decide_approval(_ctx(ACTION_RESEARCH_PROMOTION_CANDIDATE)).approval_level,
            ApprovalLevel.L1_AUTO_APPROVED,
        )


class L2TechLeadReviewTests(unittest.TestCase):
    """Engineering risk review goes to tech-lead."""

    def test_engineering_change_review_is_l2(self) -> None:
        decision = decide_approval(_ctx(ACTION_ENGINEERING_CHANGE_REVIEW))
        self.assertEqual(decision.approval_level, ApprovalLevel.L2_AGENT_REVIEW)
        self.assertEqual(decision.authority, AUTHORITY_TECH_LEAD)
        # routing_hint is what the gateway uses to steer this to a
        # tech-lead review job in M5a-2.
        self.assertEqual(decision.routing_hint, "tech-lead-review")

    def test_tech_lead_risk_review_is_l2(self) -> None:
        decision = decide_approval(_ctx(ACTION_TECH_LEAD_RISK_REVIEW))
        self.assertEqual(decision.approval_level, ApprovalLevel.L2_AGENT_REVIEW)


class L3HumanRequiredTests(unittest.TestCase):
    """Every "사용자 승인 필수" condition from the spec."""

    HIGH_RISK_ACTIONS = (
        ACTION_CODE_WRITE,
        ACTION_FILE_WRITE,
        ACTION_GIT_COMMIT,
        ACTION_GIT_PUSH,
        ACTION_GITHUB_PR_CREATE,
        ACTION_DEPLOY,
        ACTION_INFRA_CHANGE,
        ACTION_SECRET_ACCESS,
        ACTION_EXTERNAL_PAID_CALL,
        ACTION_EXTERNAL_PUBLICATION,
        ACTION_DATA_DELETE,
        ACTION_DATA_OVERWRITE,
        ACTION_OBSIDIAN_FINAL_KNOWLEDGE_WRITE,
    )

    def test_each_high_risk_action_is_l3(self) -> None:
        for action_type in self.HIGH_RISK_ACTIONS:
            with self.subTest(action_type=action_type):
                decision = decide_approval(_ctx(action_type))
                self.assertEqual(
                    decision.approval_level,
                    ApprovalLevel.L3_HUMAN_REQUIRED,
                )
                self.assertEqual(decision.authority, AUTHORITY_HUMAN)
                self.assertTrue(decision.human_approval_required)
                # L3 must have ``reason_human_approval_not_required=None`` —
                # there's no auto-bypass reason because a human is required.
                self.assertIsNone(
                    decision.reason_human_approval_not_required
                )

    def test_unknown_action_defaults_to_l3(self) -> None:
        # Unknown action types are the riskiest regression target —
        # default to L3 so an inadvertent producer can't auto-run
        # something the policy doesn't cover.
        decision = decide_approval(_ctx("brand_new_action_type"))
        self.assertEqual(
            decision.approval_level, ApprovalLevel.L3_HUMAN_REQUIRED
        )


# ---------------------------------------------------------------------------
# 2. Obsidian split — draft is L1, final knowledge save is L3
# ---------------------------------------------------------------------------


class ObsidianLevelTests(unittest.TestCase):
    def test_obsidian_draft_is_l1(self) -> None:
        decision = decide_approval(_ctx(ACTION_OBSIDIAN_DRAFT_CREATE))
        self.assertEqual(
            decision.approval_level, ApprovalLevel.L1_AUTO_APPROVED
        )

    def test_obsidian_final_knowledge_is_l3(self) -> None:
        decision = decide_approval(_ctx(ACTION_OBSIDIAN_FINAL_KNOWLEDGE_WRITE))
        self.assertEqual(
            decision.approval_level, ApprovalLevel.L3_HUMAN_REQUIRED
        )


# ---------------------------------------------------------------------------
# 3. Risk metadata escalation
# ---------------------------------------------------------------------------


class RiskEscalationTests(unittest.TestCase):
    def test_critical_risk_escalates_l1_to_l3(self) -> None:
        decision = decide_approval(
            _ctx(ACTION_RSS_FETCH, risk_level=RISK_CRITICAL)
        )
        self.assertEqual(
            decision.approval_level, ApprovalLevel.L3_HUMAN_REQUIRED
        )

    def test_irreversible_escalates_to_l3(self) -> None:
        decision = decide_approval(
            _ctx(ACTION_RESEARCH_DEDUP_TAG, reversible=False)
        )
        self.assertEqual(
            decision.approval_level, ApprovalLevel.L3_HUMAN_REQUIRED
        )

    def test_external_side_effect_escalates_to_l3(self) -> None:
        decision = decide_approval(
            _ctx(ACTION_PUBLIC_RESEARCH_COLLECT, external_side_effect=True)
        )
        self.assertEqual(
            decision.approval_level, ApprovalLevel.L3_HUMAN_REQUIRED
        )

    def test_major_cost_escalates_to_l3(self) -> None:
        decision = decide_approval(
            _ctx(ACTION_RSS_FETCH, cost_impact=COST_MAJOR)
        )
        self.assertEqual(
            decision.approval_level, ApprovalLevel.L3_HUMAN_REQUIRED
        )

    def test_secret_data_sensitivity_escalates_to_l3(self) -> None:
        decision = decide_approval(
            _ctx(ACTION_DEPARTMENT_FEED_POST, data_sensitivity=DATA_SECRET)
        )
        self.assertEqual(
            decision.approval_level, ApprovalLevel.L3_HUMAN_REQUIRED
        )

    def test_proposed_level_can_only_escalate(self) -> None:
        # Tech-lead pre-declaring L2 on an L1 action raises it to L2.
        decision = decide_approval(
            _ctx(
                ACTION_PUBLIC_RESEARCH_COLLECT,
                proposed_level=ApprovalLevel.L2_AGENT_REVIEW,
            )
        )
        self.assertEqual(
            decision.approval_level, ApprovalLevel.L2_AGENT_REVIEW
        )

    def test_proposed_level_cannot_downgrade_default(self) -> None:
        # Producer pre-declaring L1 on an L3 action stays at L3.
        decision = decide_approval(
            _ctx(
                ACTION_GIT_PUSH,
                proposed_level=ApprovalLevel.L1_AUTO_APPROVED,
            )
        )
        self.assertEqual(
            decision.approval_level, ApprovalLevel.L3_HUMAN_REQUIRED
        )


# ---------------------------------------------------------------------------
# 4. Gateway guard — gateway_can_auto_approve
# ---------------------------------------------------------------------------


class GatewayGuardTests(unittest.TestCase):
    def test_gateway_cannot_auto_approve_l3(self) -> None:
        # Highest-risk regression: gateway must NOT silently run an
        # L3 action. The guard returns False so the gateway hands
        # off to the approval card path instead.
        decision = decide_approval(_ctx(ACTION_DEPLOY))
        self.assertFalse(gateway_can_auto_approve(decision))

    def test_gateway_cannot_auto_approve_l2(self) -> None:
        # L2 needs tech-lead. Gateway is not tech-lead.
        decision = decide_approval(_ctx(ACTION_ENGINEERING_CHANGE_REVIEW))
        self.assertFalse(gateway_can_auto_approve(decision))

    def test_gateway_can_auto_approve_l0_and_l1(self) -> None:
        for action_type in (
            ACTION_HEARTBEAT_EMIT,
            ACTION_PUBLIC_RESEARCH_COLLECT,
        ):
            with self.subTest(action_type=action_type):
                decision = decide_approval(_ctx(action_type))
                self.assertTrue(gateway_can_auto_approve(decision))


# ---------------------------------------------------------------------------
# 5. Audit record contract
# ---------------------------------------------------------------------------


class AuditRecordTests(unittest.TestCase):
    def test_l1_decision_builds_audit_record(self) -> None:
        decision = decide_approval(_ctx(ACTION_RSS_FETCH))
        record = decision.to_audit_record()
        # Every required field surfaces.
        self.assertEqual(record.decision_id, decision.decision_id)
        self.assertEqual(record.action_type, ACTION_RSS_FETCH)
        self.assertEqual(record.approval_level, ApprovalLevel.L1_AUTO_APPROVED)
        self.assertEqual(record.authority, AUTHORITY_POLICY)
        self.assertFalse(record.human_approval_required)
        self.assertTrue(
            (record.reason_human_approval_not_required or "").strip()
        )

    def test_l3_decision_refuses_audit_record(self) -> None:
        # L3 must go through the approval card, not an audit-only
        # record. Calling to_audit_record on it is a producer bug.
        decision = decide_approval(_ctx(ACTION_DEPLOY))
        with self.assertRaises(ValueError):
            decision.to_audit_record()

    def test_l1_decision_with_blank_reason_refuses_audit_record(self) -> None:
        # If a producer overrides the default reason with whitespace,
        # the audit record builder must refuse — non-empty reason is
        # the load-bearing field that defends "왜 사람 승인 없이 실행했냐"
        # later.
        from yule_engineering.agents.approval_policy import ApprovalDecision

        decision = decide_approval(_ctx(ACTION_RSS_FETCH))
        # Force a blank reason via dataclass replace — emulates a
        # buggy producer that didn't supply a reason and where the
        # default template wasn't found either.
        broken = ApprovalDecision(
            **{
                **decision.__dict__,
                "reason_human_approval_not_required": "   ",
            }
        )
        with self.assertRaises(ValueError):
            broken.to_audit_record()


# ---------------------------------------------------------------------------
# 6. ApprovalRequest conversion
# ---------------------------------------------------------------------------


class ToApprovalRequestTests(unittest.TestCase):
    def test_l3_decision_converts_to_approval_request(self) -> None:
        decision = decide_approval(_ctx(ACTION_DEPLOY))
        request = decision.to_approval_request(
            title="prod 배포",
            summary="staging green, prod 배포 승인 필요",
            requested_action="kubectl apply",
            created_by="devops-engineer",
            source_thread_id=2002,
        )
        # Fields flow through to the M5a request shape.
        self.assertEqual(request.session_id, "sess-policy-1")
        self.assertEqual(request.title, "prod 배포")
        self.assertEqual(request.created_by, "devops-engineer")
        # Decision metadata is stashed under extra so the approval
        # card payload still carries the policy verdict.
        self.assertEqual(
            request.extra.get("decision_id"), decision.decision_id
        )
        self.assertEqual(
            request.extra.get("policy_level"),
            ApprovalLevel.L3_HUMAN_REQUIRED.value,
        )

    def test_non_l3_decisions_refuse_approval_request(self) -> None:
        for action_type in (
            ACTION_HEARTBEAT_EMIT,
            ACTION_PUBLIC_RESEARCH_COLLECT,
            ACTION_ENGINEERING_CHANGE_REVIEW,
        ):
            with self.subTest(action_type=action_type):
                decision = decide_approval(_ctx(action_type))
                with self.assertRaises(ValueError):
                    decision.to_approval_request(
                        title="x",
                        summary="x",
                        requested_action="x",
                        created_by="tech-lead",
                    )


# ---------------------------------------------------------------------------
# 7. Markdown formatter
# ---------------------------------------------------------------------------


class FormatAuditMarkdownTests(unittest.TestCase):
    def test_markdown_surfaces_required_fields(self) -> None:
        decision = decide_approval(
            _ctx(
                ACTION_PUBLIC_RESEARCH_COLLECT,
                reason_human_approval_not_required=(
                    "공개 RSS 피드 수집, 외부 비용·발행 없음"
                ),
            )
        )
        md = format_audit_record_markdown(decision.to_audit_record())
        self.assertIn("L1_AUTO_APPROVED", md)
        self.assertIn(ACTION_PUBLIC_RESEARCH_COLLECT, md)
        self.assertIn(decision.decision_id, md)
        self.assertIn("sess-policy-1", md)
        # Custom reason override flows through.
        self.assertIn("외부 비용·발행 없음", md)
        # Risk meta line (compact "메타:" block) carries every flag.
        self.assertIn("risk=", md)
        self.assertIn("reversible=", md)
        self.assertIn("external_side_effect=", md)
        self.assertIn("cost_impact=", md)
        self.assertIn("data_sensitivity=", md)


if __name__ == "__main__":
    unittest.main()
