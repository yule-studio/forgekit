"""Coding job model + executor prompt generation tests.

The job carries the user's approval and the prompt the executor will
actually run with — every safety boundary needs to land in the prompt
text or the executor can't be trusted to honour it.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.coding.authorization import (
    recommend_authorization,
    reset_role_profile_cache,
)
from yule_orchestrator.agents.coding.job import (
    STATUS_PENDING_APPROVAL,
    STATUS_READY,
    build_coding_job_from_proposal,
    generate_executor_prompt,
)


def _proposal(text: str = "Spring Security API 인증 흐름 추가", session_id: str = "abc123"):
    reset_role_profile_cache()
    return recommend_authorization(user_request=text, session_id=session_id)


class BuildCodingJobTests(unittest.TestCase):
    def test_default_status_is_pending_approval(self) -> None:
        proposal = _proposal()
        job = build_coding_job_from_proposal(proposal)
        self.assertEqual(job.status, STATUS_PENDING_APPROVAL)
        self.assertIsNone(job.approved_at)

    def test_explicit_ready_status_records_approved_at(self) -> None:
        proposal = _proposal()
        approved = datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc)
        job = build_coding_job_from_proposal(
            proposal,
            status=STATUS_READY,
            approved_at=approved,
        )
        self.assertEqual(job.status, STATUS_READY)
        self.assertEqual(job.approved_at, approved)

    def test_required_fields_are_populated(self) -> None:
        proposal = _proposal()
        job = build_coding_job_from_proposal(proposal)
        # MVP-required fields per the brief:
        self.assertEqual(job.session_id, "abc123")
        self.assertEqual(job.executor_role, "backend-engineer")
        self.assertGreaterEqual(len(job.write_scope), 1)
        self.assertGreaterEqual(len(job.forbidden_scope), 1)
        self.assertTrue(job.generated_prompt)
        self.assertIsNotNone(job.created_at)

    def test_to_dict_round_trips_through_json(self) -> None:
        proposal = _proposal()
        job = build_coding_job_from_proposal(
            proposal,
            status=STATUS_READY,
            approved_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
        )
        payload = job.to_dict()
        # Smoke test JSON serialisability (session.extra goes through SQLite cache).
        encoded = json.dumps(payload, ensure_ascii=False)
        round_trip = json.loads(encoded)
        self.assertEqual(round_trip["executor_role"], "backend-engineer")
        self.assertEqual(round_trip["status"], STATUS_READY)
        self.assertEqual(round_trip["session_id"], "abc123")
        self.assertEqual(round_trip["approved_at"], "2026-05-06T00:00:00+00:00")

    def test_with_status_returns_modified_copy(self) -> None:
        proposal = _proposal()
        job = build_coding_job_from_proposal(proposal)
        approved = datetime(2026, 5, 6, tzinfo=timezone.utc)
        ready = job.with_status(STATUS_READY, at=approved)
        self.assertEqual(ready.status, STATUS_READY)
        self.assertEqual(ready.approved_at, approved)
        # Original instance unchanged (frozen dataclass).
        self.assertEqual(job.status, STATUS_PENDING_APPROVAL)
        self.assertIsNone(job.approved_at)


class GenerateExecutorPromptTests(unittest.TestCase):
    def test_prompt_includes_session_user_request_and_role(self) -> None:
        proposal = _proposal(text="React hero 컴포넌트 추가", session_id="frontend-1")
        prompt = generate_executor_prompt(
            proposal=proposal,
            role_profile={"domain_focus": "UI / React 컴포넌트"},
        )
        self.assertIn("frontend-1", prompt)
        self.assertIn("React hero 컴포넌트 추가", prompt)
        self.assertIn(proposal.executor_role, prompt)

    def test_prompt_lists_write_and_forbidden_scope(self) -> None:
        proposal = _proposal(text="Spring Security 인증 추가")
        prompt = generate_executor_prompt(proposal=proposal, role_profile={})
        self.assertIn("write scope", prompt)
        self.assertIn("forbidden scope", prompt)
        # The first scope item from the proposal must show up verbatim.
        self.assertIn(proposal.write_scope[0], prompt)
        self.assertIn(proposal.forbidden_scope[0], prompt)

    def test_prompt_carries_safety_rules_text(self) -> None:
        proposal = _proposal(text="React UI hero 추가")
        prompt = generate_executor_prompt(proposal=proposal, role_profile={})
        # The brief requires destructive-command, secret, and write_scope guards in the prompt.
        self.assertIn("safety rules", prompt)
        self.assertIn("git reset", prompt.lower() if "git reset" in prompt.lower() else prompt)
        self.assertIn("secret", prompt.lower())
        self.assertIn("write_scope", prompt)

    def test_prompt_lists_workflow_steps(self) -> None:
        proposal = _proposal(text="React UI hero 추가")
        prompt = generate_executor_prompt(proposal=proposal, role_profile={})
        # 7-step workflow contract.
        self.assertIn("작업 절차", prompt)
        self.assertIn("1.", prompt)
        self.assertIn("계획", prompt)
        self.assertIn("테스트", prompt)
        self.assertIn("destructive", prompt)

    def test_prompt_includes_role_expertise_when_profile_provided(self) -> None:
        proposal = _proposal(text="React hero 컴포넌트 추가")
        prompt = generate_executor_prompt(
            proposal=proposal,
            role_profile={
                "domain_focus": "UI / React 컴포넌트 / 접근성",
                "decision_criteria": [
                    "loading/error/empty 상태가 빠지면 결정으로 보지 않는다",
                ],
                "review_checklist": ["loading 상태 정의 확인"],
                "risk_focus": ["접근성 회귀"],
                "quality_bar": ["디자인 토큰 준수"],
            },
        )
        self.assertIn("UI / React 컴포넌트 / 접근성", prompt)
        self.assertIn("loading/error/empty", prompt)
        self.assertIn("접근성 회귀", prompt)
        self.assertIn("디자인 토큰 준수", prompt)

    def test_prompt_lists_reviewers_and_participants(self) -> None:
        proposal = _proposal(text="Spring Security 인증 흐름 추가")
        prompt = generate_executor_prompt(proposal=proposal, role_profile={})
        self.assertIn("tech-lead", prompt)
        self.assertIn("reviewer", prompt.lower())

    def test_prompt_handles_empty_user_request_gracefully(self) -> None:
        # Empty request → fallback proposal still produces a prompt.
        proposal = _proposal(text="   ")
        prompt = generate_executor_prompt(proposal=proposal, role_profile={})
        self.assertIn("clarify", prompt.lower())


class GeneratedPromptOnJobTests(unittest.TestCase):
    """The job's generated_prompt must equal what
    ``generate_executor_prompt`` produces for the same inputs — so
    callers can rely on either path."""

    def test_job_prompt_matches_helper_output(self) -> None:
        proposal = _proposal(text="React hero 컴포넌트 추가")
        # Use a stable in-memory profile so the job builder doesn't go
        # to disk for the live profile and pick up unrelated diff.
        role_profile = {"domain_focus": "UI / React"}
        helper = generate_executor_prompt(
            proposal=proposal, role_profile=role_profile
        )
        job = build_coding_job_from_proposal(
            proposal,
            role_profile=role_profile,
        )
        self.assertEqual(job.generated_prompt, helper)


if __name__ == "__main__":
    unittest.main()
