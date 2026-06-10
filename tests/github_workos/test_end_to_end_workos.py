"""[G5] GitHub work-order end-to-end harness.

Drives the issue → triage → role work orders → branch → PR body →
dry-run audit chain through :class:`FakeGitHubAPI` so the test is
hermetic. The G2/G3 production code may not be in place yet — when
it is, swap the in-test triage stub for the real
``IssueTriage.triage`` and the rest of the harness moves with it.

Five test classes mirror the brief:

  * ``IssueReadHarnessTests`` — the auth + issue read seam.
  * ``TriageProducesRoleAssignmentsTests`` — every role gets the
    fields the dispatcher needs.
  * ``BranchAndPRPlanTests`` — branch name lives under ``feat/``,
    PR body carries the senior-quality plan.
  * ``DryRunAuditTests`` — dry-run is the default, no PR opens, no
    network call escapes the fake.
  * ``SafetyHardRailsTests`` — main push, force push, merge,
    deploy, secret modify all refused; PEM/token redacted in any
    surfaced error.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from tests.github_workos._fakes import (
    FakeGitHubAPI,
    FakeGitHubAPIError,
    FakeGitHubAppAuth,
    FakeIssue,
    FakeWorkOrderExecutor,
    RoleAssignment,
    SeniorQualityValidationError,
    TriageReport,
    make_default_pr_plan,
    redact_secret_blob,
)


# ---------------------------------------------------------------------------
# Shared fixture builder
# ---------------------------------------------------------------------------


def _seed_repo(api: FakeGitHubAPI) -> FakeIssue:
    issue = FakeIssue(
        number=42,
        title="forum_obsidian_handoff: research-log dedup 누락",
        body=(
            "운영-리서치 thread 에서 같은 주제 두 번째 save 요청이 "
            "research-log 에서는 dedup 되지 않는 회귀.\n"
            "- 재현: thread A 에서 'Obsidian 에 정리해줘' 두 번\n"
            "- 기대: research-log 한 개만 enqueue"
        ),
        labels=("bug", "obsidian"),
        author="masterway",
    )
    api.issues[issue.number] = issue
    return issue


def _stub_triage(*, issue: FakeIssue, dry_run: bool = True) -> TriageReport:
    """Hand-crafted triage that mirrors the contract G2 must satisfy.

    G2's real implementation will derive these fields from the issue
    body via the role profiles + tech-lead synthesis. The harness
    only needs the *shape* to round-trip correctly.
    """

    return TriageReport(
        issue_number=issue.number,
        intent="bugfix",
        scope_summary=(
            "forum_obsidian_handoff 의 research-log dedup 키를 보강해 "
            "동일 thread 의 두 번째 save 요청에서도 한 번만 enqueue 되게 한다."
        ),
        role_assignments=(
            RoleAssignment(
                role="backend-engineer",
                responsibilities=(
                    "research-log enqueue 의 dedup 키에 (session, topic_key, "
                    "thread_id) 추가",
                    "기존 forum_obsidian_handoff 테스트 회귀 없는지 확인",
                ),
                deliverables=(
                    "apps/engineering-agent/src/yule_engineering/agents/job_queue/forum_obsidian_handoff.py 패치",
                    "tests/job_queue/test_forum_obsidian_handoff.py 신규 케이스",
                ),
            ),
            RoleAssignment(
                role="qa-engineer",
                responsibilities=(
                    "두 번째 save 요청에서 research-log 가 1개로 유지되는지 검증",
                ),
                deliverables=(
                    "tests/job_queue 회귀 케이스 통과 보고",
                ),
            ),
        ),
        branch_name_plan="feat/research-log-dedup-thread-id",
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# Issue read seam
# ---------------------------------------------------------------------------


class IssueReadHarnessTests(unittest.TestCase):
    def test_get_issue_via_authenticated_api_records_call(self) -> None:
        # G1 + G2 seam — auth produces a token, the API call is
        # recorded, and the issue body comes back without secret leakage.
        auth = FakeGitHubAppAuth()
        api = FakeGitHubAPI()
        issue = _seed_repo(api)

        # Caller must pass auth headers — the fake doesn't validate the
        # token but the call sequence proves G1 was consulted.
        headers = auth.authenticated_headers()
        self.assertIn("Authorization", headers)
        self.assertEqual(auth.issued_count, 1)

        fetched = api.get_issue(issue.number)
        self.assertEqual(fetched.number, 42)
        self.assertIn("get_issue", [c[0] for c in api.calls])

    def test_auth_failure_redacts_pem_in_error(self) -> None:
        # The PEM file path / contents must NEVER leak in error output.
        # Simulate a credential helper that surfaces the PEM in its
        # exception (the production worry); the harness verifies the
        # redact helper scrubs it before logging.
        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIBOgIBAAJBAJ7realsecret==\n"
            "-----END RSA PRIVATE KEY-----\n"
        )
        try:
            raise RuntimeError(
                f"failed to sign JWT: {pem}\nAuthorization: Bearer ghs_realsecret_token_xxxxxxxxxx"
            )
        except RuntimeError as exc:
            redacted = redact_secret_blob(str(exc))
        self.assertNotIn("BEGIN RSA PRIVATE KEY", redacted)
        self.assertNotIn("ghs_realsecret_token_xxxxxxxxxx", redacted)
        self.assertIn("[REDACTED PEM]", redacted)


# ---------------------------------------------------------------------------
# Triage / role work orders
# ---------------------------------------------------------------------------


class TriageProducesRoleAssignmentsTests(unittest.TestCase):
    def test_every_role_assignment_has_responsibilities_and_deliverables(
        self,
    ) -> None:
        api = FakeGitHubAPI()
        issue = _seed_repo(api)
        report = _stub_triage(issue=issue)

        self.assertGreaterEqual(len(report.role_assignments), 1)
        for assignment in report.role_assignments:
            with self.subTest(role=assignment.role):
                self.assertTrue(assignment.role)
                self.assertGreaterEqual(len(assignment.responsibilities), 1)
                self.assertTrue(
                    all(s.strip() for s in assignment.responsibilities)
                )
                self.assertGreaterEqual(len(assignment.deliverables), 1)
                self.assertTrue(
                    all(s.strip() for s in assignment.deliverables)
                )

    def test_branch_name_plan_lives_under_feat(self) -> None:
        # The executor refuses to dispatch outside ``feat/*``; the
        # triage stub already enforces that, so we just pin it here.
        api = FakeGitHubAPI()
        issue = _seed_repo(api)
        report = _stub_triage(issue=issue)
        self.assertTrue(report.branch_name_plan.startswith("feat/"))

    def test_dry_run_is_default(self) -> None:
        api = FakeGitHubAPI()
        issue = _seed_repo(api)
        report = _stub_triage(issue=issue)
        self.assertTrue(report.dry_run)


# ---------------------------------------------------------------------------
# Branch + PR body planning (executor-driven)
# ---------------------------------------------------------------------------


class BranchAndPRPlanTests(unittest.TestCase):
    def test_executor_creates_branch_under_feat(self) -> None:
        api = FakeGitHubAPI()
        issue = _seed_repo(api)
        executor = FakeWorkOrderExecutor(github=api)
        plan = make_default_pr_plan(
            title="research-log dedup 회귀 수정",
            branch="feat/research-log-dedup-thread-id",
        )

        result = executor.run(triage=_stub_triage(issue=issue), plan=plan)
        # Branch was created; main never touched.
        self.assertIn(
            "feat/research-log-dedup-thread-id", api.branches
        )
        self.assertNotIn("main", api.branches)
        self.assertEqual(result.branch, "feat/research-log-dedup-thread-id")

    def test_executor_renders_pr_body_with_every_senior_section(self) -> None:
        # Even in dry-run the executor renders the body so the operator
        # can review it. The audit log records the rendered length so
        # an empty render is loud.
        api = FakeGitHubAPI()
        issue = _seed_repo(api)
        executor = FakeWorkOrderExecutor(github=api)
        plan = make_default_pr_plan(title="dedup 수정")

        result = executor.run(triage=_stub_triage(issue=issue), plan=plan)
        body = plan.to_pr_body()
        for heading in (
            "## 숨은 리스크",
            "## 비범위",
            "## 테스트 계획",
            "## 사람 승인 필요 작업",
            "## 제외 역할 사유",
            "## 실패 복구 시나리오",
        ):
            self.assertIn(heading, body)
        self.assertTrue(
            any("dry_run:body_rendered" in line for line in result.audit_log)
        )

    def test_executor_refuses_branch_outside_feat(self) -> None:
        api = FakeGitHubAPI()
        issue = _seed_repo(api)
        executor = FakeWorkOrderExecutor(github=api)
        plan = make_default_pr_plan(title="x")
        bad_triage = _stub_triage(issue=issue)
        # Replace branch_name_plan with a non-feat path.
        from dataclasses import replace as _replace

        bad_triage = _replace(bad_triage, branch_name_plan="hotfix/whatever")
        with self.assertRaises(FakeGitHubAPIError) as ctx:
            executor.run(triage=bad_triage, plan=plan)
        self.assertIn("feat/", str(ctx.exception))


# ---------------------------------------------------------------------------
# Dry-run audit: no PR opens, no network call
# ---------------------------------------------------------------------------


class DryRunAuditTests(unittest.TestCase):
    def test_dry_run_does_not_open_pull_request(self) -> None:
        api = FakeGitHubAPI()
        issue = _seed_repo(api)
        executor = FakeWorkOrderExecutor(github=api)
        plan = make_default_pr_plan(title="x")
        result = executor.run(triage=_stub_triage(issue=issue), plan=plan)
        self.assertTrue(result.dry_run)
        self.assertIsNone(result.pull_request_number)
        # Pull-request store stays empty.
        self.assertEqual(api.pull_requests, {})
        # No push call recorded.
        self.assertNotIn(
            "push_branch", [c[0] for c in api.calls]
        )

    def test_dry_run_blocks_live_http_calls(self) -> None:
        # Anything trying to reach the real GitHub raises the safety
        # exception — guards against an executor accidentally bypassing
        # the fake.
        api = FakeGitHubAPI()
        with self.assertRaises(FakeGitHubAPIError):
            api.http_get("https://api.github.com/repos/yule-studio/yule-studio-agent")

    def test_explicit_opt_in_can_open_pull_request(self) -> None:
        # Document the *real* dispatch path so the contract isn't
        # purely dry-run. force_dry_run=False + triage.dry_run=False
        # is the only combination that opens a PR — tested here for
        # completeness; production callers must keep dry-run as default.
        from dataclasses import replace as _replace

        api = FakeGitHubAPI()
        issue = _seed_repo(api)
        executor = FakeWorkOrderExecutor(github=api)
        plan = make_default_pr_plan(title="dispatch")
        triage_live = _replace(_stub_triage(issue=issue), dry_run=False)
        result = executor.run(
            triage=triage_live, plan=plan, force_dry_run=False
        )
        self.assertFalse(result.dry_run)
        self.assertIsNotNone(result.pull_request_number)
        pr = api.pull_requests[result.pull_request_number]
        self.assertTrue(pr.draft)  # always draft — humans flip the switch
        self.assertEqual(pr.base, "main")
        self.assertEqual(pr.head, "feat/research-log-dedup-thread-id")


# ---------------------------------------------------------------------------
# Safety hard rails
# ---------------------------------------------------------------------------


class SafetyHardRailsTests(unittest.TestCase):
    def test_main_branch_create_refused(self) -> None:
        api = FakeGitHubAPI()
        with self.assertRaises(FakeGitHubAPIError):
            api.create_branch(name="main")

    def test_main_branch_push_refused(self) -> None:
        api = FakeGitHubAPI()
        api.create_branch(name="feat/safe", base="main")
        with self.assertRaises(FakeGitHubAPIError):
            api.push_branch(name="main")

    def test_force_push_refused(self) -> None:
        api = FakeGitHubAPI()
        api.create_branch(name="feat/safe")
        with self.assertRaises(FakeGitHubAPIError) as ctx:
            api.push_branch(name="feat/safe", force=True)
        self.assertIn("force push", str(ctx.exception))

    def test_merge_deploy_secret_modify_all_refused(self) -> None:
        api = FakeGitHubAPI()
        for action in ("merge", "deploy", "update_secret"):
            with self.subTest(action=action):
                with self.assertRaises(FakeGitHubAPIError):
                    getattr(api, action)()

    def test_pr_body_redacts_pem_and_tokens(self) -> None:
        # If a buggy renderer accidentally pulls a token / PEM into the
        # PR body, the open_pull_request path still scrubs it.
        api = FakeGitHubAPI()
        api.create_branch(name="feat/safe")
        # Real GitHub install tokens are alphanumeric (no underscores)
        # — match that shape so the regex actually fires.
        leak_token = "ghs_shouldNeverAppearAAAAAAAAAAAAAAAAAAAA"
        leak = (
            "-----BEGIN RSA PRIVATE KEY-----\nbad\n-----END RSA PRIVATE KEY-----\n"
            f"{leak_token}"
        )
        pr = api.open_pull_request(
            title="x", body=leak, head="feat/safe", draft=True
        )
        self.assertNotIn("BEGIN RSA PRIVATE KEY", pr.body)
        self.assertNotIn(leak_token, pr.body)
        self.assertIn("[REDACTED", pr.body)


if __name__ == "__main__":
    unittest.main()
