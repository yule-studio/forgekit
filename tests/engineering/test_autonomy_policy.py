"""autonomy_policy — A-M10a unit tests.

Pin the M10 5-tier ladder so future refactors can't accidentally
relax a default level (the regression cost of "quietly bumped
``main_branch_push`` to L2" is catastrophic). Coverage:

  * canonical action defaults at every level (L0–L4),
  * unknown action defaults to L4 (safer regression path),
  * risk metadata escalation rules (critical / irreversible /
    external_side_effect / cost_major / data_secret),
  * ``proposed_level`` can only escalate, not relax,
  * audit_required flag matches the spec (False for L0, True for
    L1+),
  * ``can_auto_execute`` gates L3/L4 to human handoff,
  * ``to_action_context`` bridge for L3/L4 produces a legacy
    ApprovalContext compatible with M5a's ApprovalDecision pipeline,
  * reason text is non-empty for every level.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.lifecycle.autonomy_policy import (
    ACTION_AGENT_OPS_RECORD,
    ACTION_BLOG_PUBLICATION,
    ACTION_BRANCH_MERGE,
    ACTION_DEPLOY,
    ACTION_DESTRUCTIVE_DELETE,
    ACTION_FORUM_HANDOFF_DECISION,
    ACTION_HEARTBEAT_CHECK,
    ACTION_KNOWLEDGE_NOTE_FINALIZE,
    ACTION_LOCAL_COMMIT,
    ACTION_LOCAL_FILE_READ,
    ACTION_MAIN_BRANCH_PUSH,
    ACTION_PROD_DB_WRITE,
    ACTION_PUSH_TO_SHARED_REPO,
    ACTION_RESEARCH_LOG_SAVE,
    ACTION_SECRET_ACCESS,
    ACTION_SELF_IMPROVEMENT_PROPOSAL,
    ACTION_STATUS_QUERY,
    ACTION_TEST_EXECUTE,
    ACTION_THREAD_SNAPSHOT_CAPTURE,
    ACTION_TOPIC_LOOKUP,
    ACTION_USER_ORDERED_RESEARCH,
    ACTION_VAULT_REMOTE_PUSH,
    AutonomyContext,
    AutonomyLevel,
    can_auto_execute,
    decide_autonomy,
    DATA_SECRET,
    RISK_CRITICAL,
    COST_MAJOR,
)


# ---------------------------------------------------------------------------
# Default level mapping — one canonical action per level
# ---------------------------------------------------------------------------


class DefaultLevelTests(unittest.TestCase):
    def _decide(self, action: str):
        return decide_autonomy(AutonomyContext(action=action, session_id="s"))

    def test_l0_status_query(self) -> None:
        d = self._decide(ACTION_STATUS_QUERY)
        self.assertEqual(d.autonomy_level, AutonomyLevel.L0_AUTO_RECORD_OPTIONAL)
        self.assertFalse(d.audit_required)
        self.assertFalse(d.requires_human)

    def test_l0_local_file_read_and_topic_lookup(self) -> None:
        for action in (
            ACTION_LOCAL_FILE_READ,
            ACTION_TOPIC_LOOKUP,
            ACTION_HEARTBEAT_CHECK,
        ):
            with self.subTest(action=action):
                d = self._decide(action)
                self.assertEqual(
                    d.autonomy_level, AutonomyLevel.L0_AUTO_RECORD_OPTIONAL
                )

    def test_l1_research_actions_all_audit_required(self) -> None:
        for action in (
            ACTION_USER_ORDERED_RESEARCH,
            ACTION_THREAD_SNAPSHOT_CAPTURE,
            ACTION_RESEARCH_LOG_SAVE,
            ACTION_AGENT_OPS_RECORD,
            ACTION_FORUM_HANDOFF_DECISION,
        ):
            with self.subTest(action=action):
                d = self._decide(action)
                self.assertEqual(
                    d.autonomy_level, AutonomyLevel.L1_AUTO_RECORD_REQUIRED
                )
                self.assertTrue(d.audit_required)
                self.assertFalse(d.requires_human)

    def test_l2_self_improvement_local_commit_test_exec(self) -> None:
        for action in (
            ACTION_SELF_IMPROVEMENT_PROPOSAL,
            ACTION_LOCAL_COMMIT,
            ACTION_TEST_EXECUTE,
        ):
            with self.subTest(action=action):
                d = self._decide(action)
                self.assertEqual(
                    d.autonomy_level, AutonomyLevel.L2_AUTO_POST_REPORT
                )
                self.assertTrue(d.audit_required)
                self.assertFalse(d.requires_human)

    def test_l3_human_required_for_finalize_and_push(self) -> None:
        for action in (
            ACTION_KNOWLEDGE_NOTE_FINALIZE,
            ACTION_PUSH_TO_SHARED_REPO,
            ACTION_VAULT_REMOTE_PUSH,
        ):
            with self.subTest(action=action):
                d = self._decide(action)
                self.assertEqual(d.autonomy_level, AutonomyLevel.L3_HUMAN_APPROVAL)
                self.assertTrue(d.requires_human)
                self.assertTrue(d.audit_required)

    def test_l4_strong_or_forbidden_for_main_push_deploy_secret(self) -> None:
        for action in (
            ACTION_MAIN_BRANCH_PUSH,
            ACTION_BRANCH_MERGE,
            ACTION_DEPLOY,
            ACTION_SECRET_ACCESS,
            ACTION_PROD_DB_WRITE,
            ACTION_DESTRUCTIVE_DELETE,
            ACTION_BLOG_PUBLICATION,
        ):
            with self.subTest(action=action):
                d = self._decide(action)
                self.assertEqual(
                    d.autonomy_level,
                    AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN,
                )
                self.assertTrue(d.requires_human)

    def test_unknown_action_defaults_to_l4(self) -> None:
        d = self._decide("invent_a_new_thing")
        self.assertEqual(
            d.autonomy_level, AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN
        )


# ---------------------------------------------------------------------------
# Risk metadata escalation
# ---------------------------------------------------------------------------


class EscalationTests(unittest.TestCase):
    def test_critical_risk_lifts_l1_to_l4(self) -> None:
        d = decide_autonomy(
            AutonomyContext(
                action=ACTION_USER_ORDERED_RESEARCH,
                risk_level=RISK_CRITICAL,
            )
        )
        self.assertEqual(
            d.autonomy_level, AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN
        )
        self.assertIn("risk_critical", d.escalation_reasons)

    def test_irreversible_lifts_l1_to_l3(self) -> None:
        d = decide_autonomy(
            AutonomyContext(
                action=ACTION_USER_ORDERED_RESEARCH,
                reversible=False,
            )
        )
        self.assertEqual(d.autonomy_level, AutonomyLevel.L3_HUMAN_APPROVAL)
        self.assertIn("irreversible", d.escalation_reasons)

    def test_external_side_effect_lifts_l2_to_l3(self) -> None:
        d = decide_autonomy(
            AutonomyContext(
                action=ACTION_LOCAL_COMMIT,
                external_side_effect=True,
            )
        )
        self.assertEqual(d.autonomy_level, AutonomyLevel.L3_HUMAN_APPROVAL)
        self.assertIn("external_side_effect", d.escalation_reasons)

    def test_cost_major_lifts_l1_to_l3(self) -> None:
        d = decide_autonomy(
            AutonomyContext(
                action=ACTION_USER_ORDERED_RESEARCH,
                cost_impact=COST_MAJOR,
            )
        )
        self.assertEqual(d.autonomy_level, AutonomyLevel.L3_HUMAN_APPROVAL)
        self.assertIn("cost_major", d.escalation_reasons)

    def test_data_secret_lifts_to_l4(self) -> None:
        d = decide_autonomy(
            AutonomyContext(
                action=ACTION_LOCAL_FILE_READ,
                data_sensitivity=DATA_SECRET,
            )
        )
        self.assertEqual(
            d.autonomy_level, AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN
        )
        self.assertIn("data_secret", d.escalation_reasons)

    def test_proposed_level_can_only_escalate(self) -> None:
        # Proposing L3 on an L1 action escalates.
        d = decide_autonomy(
            AutonomyContext(
                action=ACTION_USER_ORDERED_RESEARCH,
                proposed_level=AutonomyLevel.L3_HUMAN_APPROVAL,
            )
        )
        self.assertEqual(d.autonomy_level, AutonomyLevel.L3_HUMAN_APPROVAL)
        self.assertIn("proposed_level_override", d.escalation_reasons)

    def test_proposed_level_below_default_is_ignored(self) -> None:
        # Proposing L0 on an L4 action MUST NOT relax the level.
        d = decide_autonomy(
            AutonomyContext(
                action=ACTION_MAIN_BRANCH_PUSH,
                proposed_level=AutonomyLevel.L0_AUTO_RECORD_OPTIONAL,
            )
        )
        self.assertEqual(
            d.autonomy_level, AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN
        )
        self.assertNotIn("proposed_level_override", d.escalation_reasons)


# ---------------------------------------------------------------------------
# Auto-execute gate + bridge to legacy approval policy
# ---------------------------------------------------------------------------


class AutoExecuteGateTests(unittest.TestCase):
    def test_l0_l1_l2_can_auto_execute(self) -> None:
        for action in (
            ACTION_STATUS_QUERY,
            ACTION_USER_ORDERED_RESEARCH,
            ACTION_LOCAL_COMMIT,
        ):
            with self.subTest(action=action):
                d = decide_autonomy(AutonomyContext(action=action))
                self.assertTrue(can_auto_execute(d))

    def test_l3_l4_cannot_auto_execute(self) -> None:
        for action in (ACTION_KNOWLEDGE_NOTE_FINALIZE, ACTION_DEPLOY):
            with self.subTest(action=action):
                d = decide_autonomy(AutonomyContext(action=action))
                self.assertFalse(can_auto_execute(d))


class LegacyBridgeTests(unittest.TestCase):
    def test_l3_to_action_context_carries_session_metadata(self) -> None:
        decision = decide_autonomy(
            AutonomyContext(
                action=ACTION_KNOWLEDGE_NOTE_FINALIZE,
                session_id="sess-99",
                topic_key="devops-roadmap-12345",
                summary="DevOps 학습 로드맵 knowledge note 확정",
            )
        )
        ctx = decision.to_action_context()
        self.assertEqual(ctx.session_id, "sess-99")
        self.assertEqual(ctx.action_type, "obsidian_final_knowledge_write")
        self.assertEqual(ctx.extra.get("topic_key"), "devops-roadmap-12345")
        self.assertEqual(
            ctx.extra.get("autonomy_level"),
            AutonomyLevel.L3_HUMAN_APPROVAL.value,
        )

    def test_l4_to_action_context_routes_to_legacy_high_risk_action(self) -> None:
        decision = decide_autonomy(
            AutonomyContext(action=ACTION_MAIN_BRANCH_PUSH, session_id="s")
        )
        ctx = decision.to_action_context()
        self.assertEqual(ctx.action_type, "git_push")

    def test_l1_to_action_context_raises(self) -> None:
        decision = decide_autonomy(
            AutonomyContext(action=ACTION_USER_ORDERED_RESEARCH)
        )
        with self.assertRaises(ValueError):
            decision.to_action_context()


# ---------------------------------------------------------------------------
# Reason + payload
# ---------------------------------------------------------------------------


class ReasonAndPayloadTests(unittest.TestCase):
    def test_reason_non_empty_for_every_level(self) -> None:
        for action in (
            ACTION_STATUS_QUERY,
            ACTION_USER_ORDERED_RESEARCH,
            ACTION_LOCAL_COMMIT,
            ACTION_KNOWLEDGE_NOTE_FINALIZE,
            ACTION_MAIN_BRANCH_PUSH,
        ):
            with self.subTest(action=action):
                d = decide_autonomy(AutonomyContext(action=action))
                self.assertTrue(d.reason.strip(), f"empty reason for {action}")

    def test_explicit_reason_overrides_default(self) -> None:
        d = decide_autonomy(
            AutonomyContext(
                action=ACTION_USER_ORDERED_RESEARCH,
                reason="사용자가 #운영-리서치 thread 에 명시 오더한 리서치",
            )
        )
        self.assertIn("사용자가", d.reason)

    def test_to_payload_round_trip_keys(self) -> None:
        d = decide_autonomy(AutonomyContext(action=ACTION_RESEARCH_LOG_SAVE))
        payload = d.to_payload()
        for key in (
            "decision_id",
            "action",
            "autonomy_level",
            "audit_required",
            "requires_human",
            "reason",
            "escalation_reasons",
        ):
            self.assertIn(key, payload)
        self.assertEqual(
            payload["autonomy_level"],
            AutonomyLevel.L1_AUTO_RECORD_REQUIRED.value,
        )


if __name__ == "__main__":
    unittest.main()
