"""tech_lead_triage — owner_role + suggested_action heuristic tests."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.lifecycle.autonomy_policy import (
    ACTION_FAILURE_POSTMORTEM_CREATE,
    ACTION_RUNTIME_CODE_CHANGE,
    ACTION_SELF_IMPROVEMENT_PROPOSAL,
)
from yule_engineering.agents.lifecycle.self_improvement import (
    SIGNAL_DUPLICATE_TOPIC_APPROVAL,
    SIGNAL_FAILED_RETRYABLE_PILEUP,
    SIGNAL_REPEATED_USER_COMPLAINT,
    SIGNAL_STALE_HEARTBEAT,
)
from yule_engineering.agents.lifecycle.self_improvement_seed_detectors import (
    SIGNAL_APPROVAL_NO_MATCHING_REPLY,
    SIGNAL_CODING_CONTINUATION_STALLED,
    SIGNAL_ENGINEERING_WRITE_REPLY_MISMATCH,
    SIGNAL_OBSIDIAN_RENDER_FAILURE,
    SIGNAL_QA_TEST_MISCLASSIFICATION,
    SIGNAL_SUPERVISOR_WATCH_UNKNOWN,
)
from yule_engineering.agents.lifecycle.tech_lead_triage import (
    PROBLEM_KIND_APPROVAL_FLOW,
    PROBLEM_KIND_CLASSIFICATION,
    PROBLEM_KIND_MEMORY_VAULT,
    PROBLEM_KIND_RUNTIME_CONFIG,
    ROLE_AI,
    ROLE_BACKEND,
    ROLE_DEVOPS,
    ROLE_FRONTEND,
    ROLE_TECH_LEAD,
    SCOPE_DELEGATED_OK,
    SCOPE_NEEDS_HUMAN,
    TriageVerdict,
    triage_problem,
)


class HeuristicOwnerMappingTests(unittest.TestCase):
    """§J 의 owner heuristic 그대로 매핑되는지."""

    def test_approval_router_routes_to_backend(self) -> None:
        verdict = triage_problem(
            signal_id=SIGNAL_ENGINEERING_WRITE_REPLY_MISMATCH,
        )
        self.assertEqual(verdict.problem_kind, PROBLEM_KIND_APPROVAL_FLOW)
        self.assertEqual(verdict.primary_owner, ROLE_BACKEND)
        self.assertIn(ROLE_TECH_LEAD, verdict.co_owner_roles)
        self.assertEqual(verdict.suggested_action, ACTION_RUNTIME_CODE_CHANGE)
        self.assertEqual(verdict.approval_scope_hint, SCOPE_DELEGATED_OK)

    def test_no_matching_reply_routes_to_backend(self) -> None:
        verdict = triage_problem(signal_id=SIGNAL_APPROVAL_NO_MATCHING_REPLY)
        self.assertEqual(verdict.primary_owner, ROLE_BACKEND)
        self.assertEqual(verdict.problem_kind, PROBLEM_KIND_APPROVAL_FLOW)

    def test_qa_misclassification_routes_to_backend_with_qa_co(self) -> None:
        verdict = triage_problem(signal_id=SIGNAL_QA_TEST_MISCLASSIFICATION)
        self.assertEqual(verdict.problem_kind, PROBLEM_KIND_CLASSIFICATION)
        self.assertEqual(verdict.primary_owner, ROLE_BACKEND)

    def test_supervisor_unknown_routes_to_devops(self) -> None:
        verdict = triage_problem(signal_id=SIGNAL_SUPERVISOR_WATCH_UNKNOWN)
        self.assertEqual(verdict.problem_kind, PROBLEM_KIND_RUNTIME_CONFIG)
        self.assertEqual(verdict.primary_owner, ROLE_DEVOPS)

    def test_vault_render_routes_to_ai(self) -> None:
        verdict = triage_problem(signal_id=SIGNAL_OBSIDIAN_RENDER_FAILURE)
        self.assertEqual(verdict.problem_kind, PROBLEM_KIND_MEMORY_VAULT)
        self.assertEqual(verdict.primary_owner, ROLE_AI)

    def test_stale_heartbeat_needs_human(self) -> None:
        verdict = triage_problem(signal_id=SIGNAL_STALE_HEARTBEAT)
        # supervisor restart 가 필요할 수 있어 사람 승인 권장.
        self.assertEqual(verdict.approval_scope_hint, SCOPE_NEEDS_HUMAN)
        self.assertEqual(verdict.primary_owner, ROLE_DEVOPS)

    def test_user_complaint_needs_human_with_external_fact(self) -> None:
        verdict = triage_problem(signal_id=SIGNAL_REPEATED_USER_COMPLAINT)
        self.assertEqual(verdict.approval_scope_hint, SCOPE_NEEDS_HUMAN)
        self.assertTrue(verdict.needs_external_fact)


class DeliberationFnSeamTests(unittest.TestCase):
    def test_deliberation_fn_overrides_heuristic(self) -> None:
        def _llm(*, signal_id, severity, evidence, summary):
            return TriageVerdict(
                problem_kind=PROBLEM_KIND_APPROVAL_FLOW,
                primary_owner="custom-role",
                co_owner_roles=(),
                suggested_action=ACTION_RUNTIME_CODE_CHANGE,
                approval_scope_hint=SCOPE_DELEGATED_OK,
                confidence=0.95,
                rationale="LLM verdict",
            )

        verdict = triage_problem(
            signal_id=SIGNAL_ENGINEERING_WRITE_REPLY_MISMATCH,
            deliberation_fn=_llm,
        )
        self.assertEqual(verdict.primary_owner, "custom-role")
        self.assertEqual(verdict.rationale, "LLM verdict")

    def test_deliberation_fn_none_returns_falls_through(self) -> None:
        def _llm(*, signal_id, severity, evidence, summary):
            return None

        verdict = triage_problem(
            signal_id=SIGNAL_ENGINEERING_WRITE_REPLY_MISMATCH,
            deliberation_fn=_llm,
        )
        # heuristic fallback
        self.assertEqual(verdict.primary_owner, ROLE_BACKEND)

    def test_deliberation_fn_raises_falls_through(self) -> None:
        def _llm(*, signal_id, severity, evidence, summary):
            raise RuntimeError("boom")

        verdict = triage_problem(
            signal_id=SIGNAL_ENGINEERING_WRITE_REPLY_MISMATCH,
            deliberation_fn=_llm,
        )
        self.assertEqual(verdict.primary_owner, ROLE_BACKEND)


class FallbackVerdictTests(unittest.TestCase):
    def test_unknown_signal_falls_back_to_tech_lead_needs_human(self) -> None:
        verdict = triage_problem(signal_id="brand_new_signal")
        self.assertEqual(verdict.primary_owner, ROLE_TECH_LEAD)
        self.assertEqual(verdict.approval_scope_hint, SCOPE_NEEDS_HUMAN)
        self.assertTrue(verdict.needs_external_fact)


if __name__ == "__main__":
    unittest.main()
