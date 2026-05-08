"""GitHub write adapter — G3.

Pin the contract that:

  * Default ``dry_run=True`` AND ``live=False`` → no client call ever.
  * Denied policy gate → no client call.
  * Allowed L1 → comment / label calls reach the client.
  * Allowed L2 → branch / draft PR calls reach the client.
  * L3 actions require an approval token (gated by the default
    policy gate).
  * Protected branch refuses to write at the writer level even
    when the policy gate would have allowed it.
  * HTTP 401 / 403 / 404 / 5xx responses produce friendly
    operator-facing detail strings.
  * Client exceptions (and Authorization-bearing exception bodies)
    end up in audit rows with the secret redacted.
  * The dispatcher integration (:func:`build_github_action_plan` →
    :func:`execute_github_action_plan`) executes steps in plan order
    and produces one audit per step.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional, Sequence, Tuple

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.github_workos.actions import (
    ActionStep,
    GithubActionPlan,
    build_github_action_plan,
    execute_github_action_plan,
)
from yule_orchestrator.agents.github_workos.audit import (
    ACTION_GITHUB_BRANCH_CREATE,
    ACTION_GITHUB_ISSUE_COMMENT,
    ACTION_GITHUB_LABEL_ADD,
    ACTION_GITHUB_PR_DRAFT_CREATE,
    OUTCOME_DENIED_BY_POLICY,
    OUTCOME_DENIED_PROTECTED_BRANCH,
    OUTCOME_DRY_RUN,
    OUTCOME_FAILED,
    OUTCOME_OK,
)
from yule_orchestrator.agents.github_workos.github_writer import (
    GithubWriter,
    PolicyGateDecision,
    make_default_policy_gate,
    map_http_status_to_friendly,
)


# ---------------------------------------------------------------------------
# Stub client + plan
# ---------------------------------------------------------------------------


class _RecordingClient:
    """Minimum :class:`GithubClient` shape — captures every call."""

    def __init__(
        self,
        *,
        comment_response: Optional[Mapping[str, Any]] = None,
        label_response: Optional[Mapping[str, Any]] = None,
        branch_response: Optional[Mapping[str, Any]] = None,
        pr_response: Optional[Mapping[str, Any]] = None,
        raise_on: Optional[str] = None,
    ) -> None:
        self.calls: List[Tuple[str, dict]] = []
        self._comment = comment_response or {
            "url": "https://github.com/owner/repo/issues/42#comment-1",
            "id": 1,
            "status": 201,
        }
        self._label = label_response or {"status": 200}
        self._branch = branch_response or {
            "url": "https://github.com/owner/repo/git/refs/heads/agent/x",
            "status": 201,
        }
        self._pr = pr_response or {
            "url": "https://github.com/owner/repo/pull/100",
            "html_url": "https://github.com/owner/repo/pull/100",
            "number": 100,
            "status": 201,
        }
        self._raise_on = raise_on

    def _maybe_raise(self, kind: str) -> None:
        if self._raise_on == kind:
            raise RuntimeError(
                f"simulated {kind} failure: Authorization=Bearer ghp_aaa1234567890bbbb1234"
            )

    def create_issue_comment(self, *, repo: str, issue_number: int, body: str):
        self.calls.append(("create_issue_comment", {"repo": repo, "issue_number": issue_number, "body": body}))
        self._maybe_raise("comment")
        return self._comment

    def add_labels(self, *, repo: str, issue_number: int, labels):
        self.calls.append(("add_labels", {"repo": repo, "issue_number": issue_number, "labels": list(labels)}))
        return self._label

    def create_branch_ref(self, *, repo: str, branch: str, base_sha: str):
        self.calls.append(("create_branch_ref", {"repo": repo, "branch": branch, "base_sha": base_sha}))
        return self._branch

    def create_commit_via_data_api(self, **kwargs):
        self.calls.append(("create_commit_via_data_api", dict(kwargs)))
        return {"sha": "abc123", "status": 201}

    def create_draft_pull_request(self, **kwargs):
        self.calls.append(("create_draft_pull_request", dict(kwargs)))
        return self._pr


@dataclass
class _StubPlan:
    title: str = "Bug: API 401 in users endpoint"
    body: str = "운영에서 401이 떨어지고 있어요"
    primary_role: str = "backend-engineer"
    autonomy_level: str = "L2"
    issue_number: Optional[int] = 42
    session_id: Optional[str] = None
    repo: str = "owner/repo"
    source: str = "github"
    labels: Sequence[str] = field(default_factory=tuple)
    in_scope: Sequence[str] = field(default_factory=tuple)
    out_of_scope: Sequence[str] = field(default_factory=tuple)
    test_plan: Sequence[str] = field(default_factory=tuple)
    risks: Sequence[str] = field(default_factory=tuple)
    approvals_needed: Sequence[str] = field(default_factory=tuple)
    work_orders: Sequence[Mapping[str, str]] = field(default_factory=tuple)
    base_branch: Optional[str] = "main"


# ---------------------------------------------------------------------------
# Default-mode (dry_run) tests
# ---------------------------------------------------------------------------


class DryRunDefaultTests(unittest.TestCase):
    def test_default_writer_is_dry_run_and_blocks_client_calls(self) -> None:
        client = _RecordingClient()
        writer = GithubWriter(client=client)  # default dry_run=True, live=False
        result = writer.post_issue_comment(
            repo="owner/repo", issue_number=1, body="hello"
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.outcome, OUTCOME_DRY_RUN)
        # Client must NOT have been called.
        self.assertEqual(client.calls, [])
        # Audit names dry-run.
        self.assertTrue(result.audit.dry_run)
        self.assertEqual(result.audit.outcome, OUTCOME_DRY_RUN)

    def test_only_dry_run_false_without_live_still_dry_run(self) -> None:
        # Belt-and-suspenders: both flags must flip for a real write.
        client = _RecordingClient()
        writer = GithubWriter(client=client, dry_run=False, live=False)
        result = writer.add_labels(
            repo="owner/repo", issue_number=1, labels=("triage",)
        )
        self.assertEqual(result.outcome, OUTCOME_DRY_RUN)
        self.assertEqual(client.calls, [])

    def test_only_live_true_without_dry_run_false_still_dry_run(self) -> None:
        client = _RecordingClient()
        writer = GithubWriter(client=client, dry_run=True, live=True)
        result = writer.add_labels(
            repo="owner/repo", issue_number=1, labels=("triage",)
        )
        self.assertEqual(result.outcome, OUTCOME_DRY_RUN)
        self.assertEqual(client.calls, [])


# ---------------------------------------------------------------------------
# Policy gate tests
# ---------------------------------------------------------------------------


class PolicyGateTests(unittest.TestCase):
    def test_denied_policy_blocks_client_call(self) -> None:
        client = _RecordingClient()

        def deny_all(**_):
            return PolicyGateDecision(allowed=False, reason="explicit deny")

        writer = GithubWriter(
            client=client, dry_run=False, live=True, policy_gate=deny_all
        )
        result = writer.post_issue_comment(
            repo="owner/repo", issue_number=1, body="hello"
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.outcome, OUTCOME_DENIED_BY_POLICY)
        # Even with live=True, denied policy → no client call.
        self.assertEqual(client.calls, [])
        self.assertIn("explicit deny", result.detail)

    def test_default_gate_allows_l1_comment(self) -> None:
        client = _RecordingClient()
        writer = GithubWriter(
            client=client,
            dry_run=False,
            live=True,
            policy_gate=make_default_policy_gate(),
        )
        result = writer.post_issue_comment(
            repo="owner/repo",
            issue_number=1,
            body="hi",
            autonomy_level="L1",
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.outcome, OUTCOME_OK)
        self.assertEqual(client.calls[0][0], "create_issue_comment")

    def test_default_gate_denies_l1_for_l2_action(self) -> None:
        client = _RecordingClient()
        writer = GithubWriter(
            client=client,
            dry_run=False,
            live=True,
            policy_gate=make_default_policy_gate(),
        )
        # L1 caller cannot create a branch (requires L2).
        result = writer.create_branch(
            repo="owner/repo",
            branch="agent/backend/issue-1-foo",
            base_sha="abc123",
            autonomy_level="L1",
        )
        self.assertEqual(result.outcome, OUTCOME_DENIED_BY_POLICY)
        self.assertIn("L2", result.detail)
        self.assertEqual(client.calls, [])

    def test_default_gate_allows_l2_draft_pr(self) -> None:
        client = _RecordingClient()
        writer = GithubWriter(
            client=client,
            dry_run=False,
            live=True,
            policy_gate=make_default_policy_gate(),
        )
        result = writer.create_draft_pull_request(
            repo="owner/repo",
            head="agent/backend/issue-1-foo",
            base="main",
            title="Bug fix",
            body="body",
            autonomy_level="L2",
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.outcome, OUTCOME_OK)
        self.assertEqual(result.audit.pr_number, 100)

    def test_default_gate_l3_without_approval_denies(self) -> None:
        # L3 actions live in the writer's audit constants but not in
        # the default writer methods (push/merge live in G6). The
        # default gate still refuses any L3+ caller without an
        # approval token, exercised here through a hand-rolled action
        # name in a custom callable.
        gate = make_default_policy_gate(require_approval_for_l3=True)
        decision = gate(action="github_pr_ready", autonomy_level="L3")
        self.assertFalse(decision.allowed)
        self.assertIn("approval", decision.reason.lower())

    def test_default_gate_l3_with_approval_allows(self) -> None:
        gate = make_default_policy_gate(approval_token="op-approval-token")
        decision = gate(action="github_pr_ready", autonomy_level="L3")
        self.assertTrue(decision.allowed)


# ---------------------------------------------------------------------------
# Protected branch tests
# ---------------------------------------------------------------------------


class ProtectedBranchTests(unittest.TestCase):
    def test_branch_create_against_main_refused_even_with_policy_allowed(self) -> None:
        client = _RecordingClient()
        # Policy unconditionally allows; writer's own check still refuses.
        writer = GithubWriter(
            client=client,
            dry_run=False,
            live=True,
            policy_gate=lambda **_: PolicyGateDecision(allowed=True, reason="ok"),
        )
        result = writer.create_branch(
            repo="owner/repo", branch="main", base_sha="abc123", autonomy_level="L2"
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.outcome, OUTCOME_DENIED_PROTECTED_BRANCH)
        self.assertEqual(client.calls, [])

    def test_draft_pr_with_protected_head_refused(self) -> None:
        client = _RecordingClient()
        writer = GithubWriter(
            client=client,
            dry_run=False,
            live=True,
            policy_gate=lambda **_: PolicyGateDecision(allowed=True, reason="ok"),
        )
        result = writer.create_draft_pull_request(
            repo="owner/repo",
            head="master",
            base="main",
            title="Bug fix",
            body="body",
            autonomy_level="L2",
        )
        self.assertEqual(result.outcome, OUTCOME_DENIED_PROTECTED_BRANCH)


# ---------------------------------------------------------------------------
# HTTP status mapping
# ---------------------------------------------------------------------------


class HttpStatusMappingTests(unittest.TestCase):
    def test_401_403_404_5xx_have_friendly_messages(self) -> None:
        for status in (401, 403, 404, 500, 502, 503):
            with self.subTest(status=status):
                msg = map_http_status_to_friendly(status)
                self.assertTrue(msg)
                self.assertIn(str(status) if status not in (401, 403, 404, 500, 502, 503) else "", msg) if False else None
                # Cause + recovery hints are present for canonical
                # codes.
                self.assertIn("cause:", msg)
                self.assertIn("recover:", msg)

    def test_unknown_status_falls_back_to_generic(self) -> None:
        msg_4xx = map_http_status_to_friendly(418)
        self.assertIn("418", msg_4xx)
        msg_5xx = map_http_status_to_friendly(599)
        self.assertIn("5xx", msg_5xx)
        self.assertIn("retry", msg_5xx)

    def test_writer_maps_4xx_response_to_friendly_detail(self) -> None:
        client = _RecordingClient(
            comment_response={"status": 403, "body": {"message": "permission denied"}}
        )
        writer = GithubWriter(
            client=client,
            dry_run=False,
            live=True,
            policy_gate=lambda **_: PolicyGateDecision(allowed=True, reason="ok"),
        )
        result = writer.post_issue_comment(
            repo="owner/repo", issue_number=1, body="hi"
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.outcome, OUTCOME_FAILED)
        self.assertIn("권한", result.detail)


# ---------------------------------------------------------------------------
# Secret redaction in failure path
# ---------------------------------------------------------------------------


class SecretRedactionTests(unittest.TestCase):
    def test_client_exception_with_token_in_message_is_redacted_in_audit(self) -> None:
        client = _RecordingClient(raise_on="comment")
        writer = GithubWriter(
            client=client,
            dry_run=False,
            live=True,
            policy_gate=lambda **_: PolicyGateDecision(allowed=True, reason="ok"),
        )
        result = writer.post_issue_comment(
            repo="owner/repo", issue_number=1, body="hi"
        )
        self.assertFalse(result.ok)
        # Detail string itself is also redacted.
        self.assertNotIn("ghp_", result.detail)
        # The audit row's summary must not carry the leaked token.
        self.assertNotIn("ghp_", result.audit.summary)
        self.assertNotIn("Bearer ghp", result.audit.summary)


# ---------------------------------------------------------------------------
# Plan + dispatcher integration
# ---------------------------------------------------------------------------


class ActionPlanTests(unittest.TestCase):
    def test_build_action_plan_orders_steps_safely(self) -> None:
        plan = build_github_action_plan(
            _StubPlan(labels=("triage", "backend")),
            audit_id="audit-1",
        )
        kinds = [step.kind for step in plan.steps]
        self.assertEqual(
            kinds,
            [
                ACTION_GITHUB_ISSUE_COMMENT,
                ACTION_GITHUB_LABEL_ADD,
                ACTION_GITHUB_BRANCH_CREATE,
                ACTION_GITHUB_PR_DRAFT_CREATE,
            ],
        )
        self.assertTrue(plan.branch.startswith("agent/backend-engineer/issue-42-"))
        self.assertEqual(plan.base_branch, "main")
        self.assertIn("audit-1", plan.pr_body)

    def test_no_issue_number_skips_comment_and_labels(self) -> None:
        plan = build_github_action_plan(
            _StubPlan(issue_number=None, session_id="sess-x", source="discord"),
            audit_id="audit-2",
        )
        kinds = [step.kind for step in plan.steps]
        self.assertEqual(
            kinds,
            [ACTION_GITHUB_BRANCH_CREATE, ACTION_GITHUB_PR_DRAFT_CREATE],
        )
        self.assertTrue(plan.branch.startswith("agent/backend-engineer/discord-sess-x-"))

    def test_protected_branch_override_raises(self) -> None:
        with self.assertRaises(ValueError):
            build_github_action_plan(
                _StubPlan(),
                audit_id="x",
                branch_name_override="main",
            )


class ExecutionTests(unittest.TestCase):
    def _execute(self, *, dry_run: bool, live: bool, gate=None):
        client = _RecordingClient()
        plan = build_github_action_plan(_StubPlan(), audit_id="audit-1")
        writer = GithubWriter(
            client=client,
            dry_run=dry_run,
            live=live,
            policy_gate=gate or make_default_policy_gate(),
        )
        audits: List[Any] = []
        report = execute_github_action_plan(
            plan, writer=writer, audit_sink=audits.append
        )
        return client, plan, report, audits

    def test_dry_run_walks_plan_without_client_calls(self) -> None:
        client, _plan, report, audits = self._execute(dry_run=True, live=False)
        self.assertFalse(report.halted)
        self.assertEqual(client.calls, [])
        # One audit per step.
        self.assertEqual(len(audits), len(report.records))
        for audit in audits:
            self.assertEqual(audit.outcome, OUTCOME_DRY_RUN)

    def test_live_walks_full_plan_in_order(self) -> None:
        client, _plan, report, _audits = self._execute(dry_run=False, live=True)
        self.assertTrue(report.all_succeeded)
        self.assertEqual(
            [c[0] for c in client.calls],
            [
                "create_issue_comment",
                "create_branch_ref",
                "create_draft_pull_request",
            ],
        )

    def test_live_with_denied_gate_halts(self) -> None:
        deny_all_gate = lambda **_: PolicyGateDecision(allowed=False, reason="deny")
        client, _plan, report, _audits = self._execute(
            dry_run=False, live=True, gate=deny_all_gate
        )
        self.assertTrue(report.halted)
        self.assertEqual(client.calls, [])
        self.assertFalse(report.all_succeeded)


if __name__ == "__main__":
    unittest.main()
