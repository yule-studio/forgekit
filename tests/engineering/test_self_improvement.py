"""self_improvement — A-M10c detection skeleton tests."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.lifecycle.autonomy_policy import (
    AutonomyLevel,
)
from yule_orchestrator.agents.lifecycle.self_improvement import (
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    SIGNAL_DUPLICATE_TOPIC_APPROVAL,
    SIGNAL_EMPTY_KNOWLEDGE_NOTE,
    SIGNAL_FAILED_RETRYABLE_PILEUP,
    SIGNAL_REPEATED_USER_COMPLAINT,
    SIGNAL_STALE_HEARTBEAT,
    SelfImprovementProposal,
    SelfImprovementSignal,
    collect_self_improvement_signals,
    detect_duplicate_topic_approval,
    detect_empty_knowledge_note_attempts,
    detect_failed_retryable_pileup,
    detect_stale_heartbeat,
    plan_self_improvement_proposal,
    plan_self_improvement_proposals,
    propose_runtime_code_change_stub,
    render_signals_as_proposal_body,
)


def _job(*, state, job_type="approval_post", payload=None, result=None, job_id="j"):
    return SimpleNamespace(
        state=SimpleNamespace(value=state),
        job_type=job_type,
        payload=payload or {},
        result=result or {},
        job_id=job_id,
    )


class FailedRetryablePileupTests(unittest.TestCase):
    def test_under_threshold_returns_none(self) -> None:
        jobs = [_job(state="failed_retryable") for _ in range(2)]
        self.assertIsNone(detect_failed_retryable_pileup(jobs=jobs, threshold=3))

    def test_over_threshold_returns_signal(self) -> None:
        jobs = [
            _job(state="failed_retryable", job_id=f"j{i}", job_type="approval_post")
            for i in range(5)
        ]
        sig = detect_failed_retryable_pileup(jobs=jobs, threshold=3)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.signal, SIGNAL_FAILED_RETRYABLE_PILEUP)
        self.assertEqual(sig.evidence["count"], 5)
        self.assertIn("approval_post", sig.evidence["job_types"])

    def test_double_threshold_escalates_to_high(self) -> None:
        jobs = [
            _job(state="failed_retryable", job_id=f"j{i}") for i in range(8)
        ]
        sig = detect_failed_retryable_pileup(jobs=jobs, threshold=3)
        assert sig is not None
        self.assertEqual(sig.severity, SEVERITY_HIGH)


class DuplicateTopicApprovalTests(unittest.TestCase):
    def test_two_active_approvals_same_topic_flag(self) -> None:
        jobs = [
            _job(
                state="queued",
                job_type="approval_post",
                payload={"extra": {"topic_key": "k"}},
                job_id="a",
            ),
            _job(
                state="in_progress",
                job_type="approval_post",
                payload={"extra": {"topic_key": "k"}},
                job_id="b",
            ),
        ]
        sig = detect_duplicate_topic_approval(jobs=jobs)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.signal, SIGNAL_DUPLICATE_TOPIC_APPROVAL)
        self.assertIn("k", sig.evidence["topics"])
        self.assertEqual(sig.severity, SEVERITY_HIGH)

    def test_terminal_failed_rows_ignored(self) -> None:
        jobs = [
            _job(
                state="failed_terminal",
                job_type="approval_post",
                payload={"extra": {"topic_key": "k"}},
                job_id="a",
            ),
            _job(
                state="queued",
                job_type="approval_post",
                payload={"extra": {"topic_key": "k"}},
                job_id="b",
            ),
        ]
        self.assertIsNone(detect_duplicate_topic_approval(jobs=jobs))


class StaleHeartbeatTests(unittest.TestCase):
    def test_stale_service_flagged(self) -> None:
        now = datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc)
        beat = (now - timedelta(seconds=900)).isoformat()
        sig = detect_stale_heartbeat(
            heartbeats={"eng-obsidian-writer": {"updated_at": beat}},
            now=now,
            stale_after_seconds=600,
        )
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.signal, SIGNAL_STALE_HEARTBEAT)
        self.assertIn("eng-obsidian-writer", sig.evidence["stale_service_ids"])

    def test_recent_heartbeat_returns_none(self) -> None:
        now = datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc)
        beat = (now - timedelta(seconds=30)).isoformat()
        self.assertIsNone(
            detect_stale_heartbeat(
                heartbeats={"x": {"updated_at": beat}},
                now=now,
                stale_after_seconds=600,
            )
        )


class EmptyKnowledgeNoteTests(unittest.TestCase):
    def test_two_or_more_hydration_failures_flag(self) -> None:
        jobs = [
            _job(
                state="failed_retryable",
                job_type="obsidian_write",
                result={"error": "knowledge note ... hydration 부족"},
                job_id=f"j{i}",
            )
            for i in range(3)
        ]
        sig = detect_empty_knowledge_note_attempts(failed_jobs=jobs)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.signal, SIGNAL_EMPTY_KNOWLEDGE_NOTE)
        self.assertEqual(sig.severity, SEVERITY_MEDIUM)

    def test_single_failure_no_signal(self) -> None:
        jobs = [
            _job(
                state="failed_retryable",
                job_type="obsidian_write",
                result={"error": "hydration 부족"},
            )
        ]
        self.assertIsNone(detect_empty_knowledge_note_attempts(failed_jobs=jobs))


class CollectorAndRendererTests(unittest.TestCase):
    def test_collect_returns_signals_high_severity_first(self) -> None:
        jobs = [_job(state="failed_retryable") for _ in range(8)]
        # Add a duplicate topic approval too.
        jobs.extend(
            [
                _job(
                    state="queued",
                    job_type="approval_post",
                    payload={"extra": {"topic_key": "k"}},
                    job_id="a",
                ),
                _job(
                    state="queued",
                    job_type="approval_post",
                    payload={"extra": {"topic_key": "k"}},
                    job_id="b",
                ),
            ]
        )
        signals = collect_self_improvement_signals(jobs=jobs)
        self.assertEqual(len(signals), 2)
        # Both high-severity → tie-broken by signal id alphabetical:
        # duplicate_topic_approval < failed_retryable_pileup
        self.assertEqual(signals[0].severity, SEVERITY_HIGH)
        self.assertEqual(signals[1].severity, SEVERITY_HIGH)

    def test_render_signals_as_proposal_body_includes_each(self) -> None:
        signals = [
            SelfImprovementSignal(
                signal=SIGNAL_FAILED_RETRYABLE_PILEUP,
                severity=SEVERITY_HIGH,
                summary="failed_retryable 누적",
                evidence={"count": 5},
                detected_at="2026-05-08T10:00:00+00:00",
            )
        ]
        body = render_signals_as_proposal_body(signals)
        self.assertIn("self-improvement proposal", body)
        self.assertIn(SIGNAL_FAILED_RETRYABLE_PILEUP, body)
        self.assertIn("failed_retryable 누적", body)
        self.assertIn("제안 조치", body)
        self.assertIn("자동 기록 안내", body)

    def test_empty_signals_render_no_signals_block(self) -> None:
        body = render_signals_as_proposal_body([])
        self.assertIn("감지된 신호 없음", body)


# ---------------------------------------------------------------------------
# A-M12 — proposal planner + dangerous-action stub
# ---------------------------------------------------------------------------


def _signal(
    *,
    name: str = SIGNAL_FAILED_RETRYABLE_PILEUP,
    severity: str = SEVERITY_MEDIUM,
    summary: str = "8 failed_retryable jobs",
    evidence=None,
) -> SelfImprovementSignal:
    return SelfImprovementSignal(
        signal=name,
        severity=severity,
        summary=summary,
        evidence=evidence or {"count": 8},
        detected_at="2026-05-08T10:00:00+00:00",
    )


class PlanProposalAutoExecutableTests(unittest.TestCase):
    """L1/L2 signals resolve to an ObsidianWriteRequest the supervisor
    can enqueue without human approval."""

    def test_failed_retryable_pileup_plans_l2_failure_postmortem(self) -> None:
        plan = plan_self_improvement_proposal(
            _signal(name=SIGNAL_FAILED_RETRYABLE_PILEUP),
            session_id="sess-supervisor",
        )
        self.assertTrue(plan.is_auto_executable)
        self.assertFalse(plan.needs_human_approval)
        self.assertFalse(plan.is_blocked)
        self.assertEqual(
            plan.decision.autonomy_level,
            AutonomyLevel.L2_AUTO_POST_REPORT,
        )
        self.assertEqual(plan.write_request.note_kind, "failure-postmortem")
        self.assertEqual(plan.write_request.session_id, "sess-supervisor")
        self.assertIn("self-improvement", plan.write_request.title)
        # Body carries the rendered signal markdown.
        body = plan.write_request.metadata.get("body") or ""
        self.assertIn(SIGNAL_FAILED_RETRYABLE_PILEUP, body)
        # Signal id + severity stamped onto the request extras.
        meta = plan.write_request.metadata
        self.assertEqual(
            meta.get("self_improvement_signal"),
            SIGNAL_FAILED_RETRYABLE_PILEUP,
        )
        self.assertEqual(meta.get("severity"), SEVERITY_MEDIUM)
        self.assertEqual(meta.get("decision_id"), plan.decision.decision_id)

    def test_repeated_user_complaint_plans_self_improvement_proposal_kind(
        self,
    ) -> None:
        plan = plan_self_improvement_proposal(
            _signal(
                name=SIGNAL_REPEATED_USER_COMPLAINT,
                summary="동일 불만 3회 반복",
                evidence={"count": 3},
            ),
        )
        self.assertTrue(plan.is_auto_executable)
        self.assertEqual(
            plan.write_request.note_kind, "self-improvement-proposal"
        )

    def test_stale_heartbeat_plans_failure_postmortem(self) -> None:
        plan = plan_self_improvement_proposal(
            _signal(
                name=SIGNAL_STALE_HEARTBEAT,
                severity=SEVERITY_HIGH,
                summary="supervisor stale 15m",
                evidence={"stale_service_ids": ["eng-obsidian-writer"]},
            ),
        )
        self.assertTrue(plan.is_auto_executable)
        self.assertEqual(plan.write_request.note_kind, "failure-postmortem")

    def test_plan_batch_preserves_order(self) -> None:
        signals = (
            _signal(name=SIGNAL_FAILED_RETRYABLE_PILEUP),
            _signal(name=SIGNAL_REPEATED_USER_COMPLAINT, summary="x"),
        )
        plans = plan_self_improvement_proposals(signals)
        self.assertEqual(len(plans), 2)
        self.assertEqual(plans[0].signal.signal, SIGNAL_FAILED_RETRYABLE_PILEUP)
        self.assertEqual(plans[1].signal.signal, SIGNAL_REPEATED_USER_COMPLAINT)


class PlanProposalApprovalRoutingTests(unittest.TestCase):
    """L3 contexts produce an approval envelope, never a write_request."""

    def test_runtime_code_change_action_routes_to_l3_envelope(self) -> None:
        plan = plan_self_improvement_proposal(
            _signal(),
            session_id="sess-x",
            action_override="runtime_code_change",
        )
        self.assertFalse(plan.is_auto_executable)
        self.assertTrue(plan.needs_human_approval)
        self.assertFalse(plan.is_blocked)
        self.assertEqual(
            plan.decision.autonomy_level,
            AutonomyLevel.L3_HUMAN_APPROVAL,
        )
        self.assertIsNone(plan.write_request)
        env = plan.approval_envelope or {}
        self.assertEqual(env.get("kind"), "approval_request")
        self.assertEqual(env.get("session_id"), "sess-x")
        self.assertEqual(
            env.get("requested_action"), "runtime_code_change"
        )
        self.assertIn("signal", env)

    def test_irreversible_metadata_escalates_l2_to_l3(self) -> None:
        plan = plan_self_improvement_proposal(
            _signal(name=SIGNAL_FAILED_RETRYABLE_PILEUP),
            reversible=False,  # autonomy_policy escalates → L3
        )
        self.assertEqual(
            plan.decision.autonomy_level,
            AutonomyLevel.L3_HUMAN_APPROVAL,
        )
        self.assertIsNone(plan.write_request)
        self.assertIsNotNone(plan.approval_envelope)


class PlanProposalDangerousActionsBlockedTests(unittest.TestCase):
    """L4 actions (push to main, deploy, secret access) MUST not be
    auto-executed and must NOT generate an approval envelope inside the
    self-improvement loop — the proposal is marked blocked so the
    supervisor records the L4 verdict for human review elsewhere."""

    def test_main_branch_push_action_blocked(self) -> None:
        plan = plan_self_improvement_proposal(
            _signal(),
            action_override="main_branch_push",
        )
        self.assertTrue(plan.is_blocked)
        self.assertFalse(plan.is_auto_executable)
        self.assertFalse(plan.needs_human_approval)
        self.assertIsNone(plan.write_request)
        self.assertIsNone(plan.approval_envelope)
        self.assertEqual(
            plan.decision.autonomy_level,
            AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN,
        )
        self.assertTrue(plan.blocked_reason)

    def test_destructive_delete_blocked(self) -> None:
        plan = plan_self_improvement_proposal(
            _signal(),
            action_override="destructive_delete",
        )
        self.assertTrue(plan.is_blocked)
        self.assertIsNone(plan.write_request)

    def test_secret_modify_blocked(self) -> None:
        plan = plan_self_improvement_proposal(
            _signal(),
            action_override="secret_modify",
        )
        self.assertTrue(plan.is_blocked)

    def test_critical_risk_escalates_unknown_action_to_l4_blocked(
        self,
    ) -> None:
        plan = plan_self_improvement_proposal(
            _signal(),
            action_override="some_brand_new_dangerous_verb",
            risk_level="critical",
        )
        # Default for unknown action is already L4; risk_critical also
        # escalates. Either way: blocked, no auto-execution.
        self.assertTrue(plan.is_blocked)
        self.assertIsNone(plan.write_request)


class RuntimeCodeChangeStubTests(unittest.TestCase):
    """The adapter stub must:

      * always resolve to L3 (never auto-execute branch + commit),
      * never create a write_request,
      * never reach git / GitHub / runner — just produce an approval
        envelope so M13 e2e can verify "we *would* propose this".
    """

    def test_stub_always_proposes_l3_approval(self) -> None:
        plan = propose_runtime_code_change_stub(
            summary="fix hydration regression in research-loop",
            session_id="sess-z",
        )
        self.assertEqual(
            plan.decision.autonomy_level,
            AutonomyLevel.L3_HUMAN_APPROVAL,
        )
        self.assertIsNone(plan.write_request)
        self.assertTrue(plan.needs_human_approval)
        env = plan.approval_envelope or {}
        self.assertEqual(env.get("requested_action"), "runtime_code_change")
        self.assertIn("fix hydration", env.get("summary", ""))

    def test_stub_does_not_invoke_git_or_runner(self) -> None:
        # Defensive: assert decide_fn / request_builder are never
        # asked to do anything for an L3 verdict — write_request stays
        # None even if a request_builder closure is provided.
        called_builder: list = []

        def trapping_builder(**_kw):  # pragma: no cover - asserted not called
            called_builder.append(_kw)
            return object()

        # plan_self_improvement_proposal is the underlying call; passing
        # request_builder to the stub-equivalent path should not
        # invoke it for L3 actions.
        plan = plan_self_improvement_proposal(
            _signal(),
            action_override="runtime_code_change",
            request_builder=trapping_builder,
        )
        self.assertFalse(called_builder)
        self.assertEqual(
            plan.decision.autonomy_level,
            AutonomyLevel.L3_HUMAN_APPROVAL,
        )

    def test_stub_critical_risk_escalates_to_l4_blocked(self) -> None:
        plan = propose_runtime_code_change_stub(
            summary="rewrite core scheduler",
            risk_level="critical",
        )
        # critical risk pushes runtime_code_change up to L4 →
        # blocked, no envelope, no request.
        self.assertTrue(plan.is_blocked)
        self.assertIsNone(plan.write_request)
        self.assertIsNone(plan.approval_envelope)


class M13E2eSeamTests(unittest.TestCase):
    """The dispatch_fn seam M13 e2e relies on — the planner returns
    a fully-typed :class:`SelfImprovementProposal` so a test recorder
    can assert "the supervisor sweep produced this exact proposal"
    without scraping the queue."""

    def test_proposal_serialises_to_payload_friendly_dict(self) -> None:
        plan = plan_self_improvement_proposal(_signal())
        # Decision payload is what dispatch_fn / e2e tests will quote.
        decision_payload = plan.decision.to_payload()
        self.assertEqual(
            decision_payload["autonomy_level"],
            AutonomyLevel.L2_AUTO_POST_REPORT.value,
        )
        self.assertEqual(
            decision_payload["action"], "failure_postmortem_create"
        )
        # Signal payload survives round-trip.
        signal_payload = plan.signal.to_payload()
        self.assertEqual(
            signal_payload["signal"], SIGNAL_FAILED_RETRYABLE_PILEUP
        )
        self.assertEqual(signal_payload["severity"], SEVERITY_MEDIUM)


if __name__ == "__main__":
    unittest.main()
