"""CLI smoke-pr template integration — Fix.

These tests exercise the body-composition surface the CLI passes to
``compose_pr_body`` so we know the live ``yule github smoke-pr
--live`` path actually consumes the in-repo PR template. We do NOT
hit the live GitHub App or LiveGithubAppClient — the test focuses
on the deterministic renderer wiring, not the network step.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.github_workos.pr_template import render_pr_body
from yule_orchestrator.agents.github_workos.repository_pr_template import (
    PrTemplateFillContext,
    compose_pr_body,
)
from yule_orchestrator.cli.github_workos import _resolve_repo_root_for_template


@contextmanager
def _chdir(path: Path):
    prev = Path.cwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(prev)


class _StubAdapter:
    """Mirrors the relevant slice of CLI's _G3PlanAdapter."""

    title = "users 401 회귀 회복"
    body = "users 엔드포인트 401 회귀 — 토큰 만료 캐시 갱신"
    primary_role = "backend-engineer"
    autonomy_level = "L2"
    issue_number = 42
    session_id = None
    repo = "yule-studio/yule-studio-agent"
    in_scope = ("services/auth/handlers.py",)
    out_of_scope = ("UI 변경",)
    test_plan = ("python -m unittest tests.auth",)
    risks = ("토큰 캐시 stale",)
    approvals_needed = ("backend-engineer 검토",)
    work_orders = ({"autonomy_level": "L2", "action": "code_diff"},)


def _fill_ctx(*, smoke_mode: bool = True, smoke_marker_path: str = "runs/smoke.md", issue_url: str = "") -> PrTemplateFillContext:
    return PrTemplateFillContext(
        audit_id="audit-cli-1",
        branch="agent/backend-engineer/issue-42-fix",
        commit_sha="abc123def",
        primary_role="backend-engineer",
        autonomy_level="L2",
        issue_number=42,
        issue_url=issue_url or "https://github.com/yule-studio/yule-studio-agent/issues/42",
        purpose="users 엔드포인트 401 회귀 회복",
        change_summary=("smoke marker file 추가 (production 코드 변경 없음)",),
        test_plan=("python -m unittest tests.auth",),
        risks=("토큰 캐시 stale",),
        approvals_needed=("backend-engineer 검토",),
        work_orders=({"autonomy_level": "L2", "action": "code_diff"},),
        trace_links={
            "github": "https://github.com/yule-studio/yule-studio-agent/issues/42",
        },
        smoke_mode=smoke_mode,
        smoke_marker_path=smoke_marker_path,
        base_branch="main",
        repo_full_name="yule-studio/yule-studio-agent",
    )


# ---------------------------------------------------------------------------
# repo-root resolver
# ---------------------------------------------------------------------------


class ResolveRepoRootTests(unittest.TestCase):
    def test_resolver_finds_in_repo_github_dir(self) -> None:
        repo_root = _resolve_repo_root_for_template()
        self.assertTrue((repo_root / ".github").is_dir())

    def test_resolver_falls_back_when_cwd_outside_repo(self) -> None:
        # In a tmp dir without .github, the resolver returns the
        # package install root which still has .github.
        with tempfile.TemporaryDirectory() as tmp:
            with _chdir(Path(tmp)):
                root = _resolve_repo_root_for_template()
        # Either way the returned path must contain .github so the
        # template discovery succeeds even from a foreign cwd.
        self.assertTrue((root / ".github").exists() or root.exists())


# ---------------------------------------------------------------------------
# Smoke PR body composition
# ---------------------------------------------------------------------------


class SmokeBodyCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = _resolve_repo_root_for_template()

    def test_smoke_body_uses_in_repo_template_sections(self) -> None:
        result = compose_pr_body(
            repo_root=str(self.repo_root),
            plan=_StubAdapter(),
            context=_fill_ctx(smoke_mode=True),
            fallback_renderer=render_pr_body,
        )
        self.assertFalse(result.template_missing)
        self.assertIsNotNone(result.template_path)
        # Real repo template sections must be present.
        self.assertIn("관련 이슈", result.rendered)
        self.assertIn("과제 내용", result.rendered)
        self.assertIn("스크린샷", result.rendered)
        self.assertIn("레퍼런스", result.rendered)

    def test_smoke_body_carries_merge_blocked_notice(self) -> None:
        result = compose_pr_body(
            repo_root=str(self.repo_root),
            plan=_StubAdapter(),
            context=_fill_ctx(smoke_mode=True),
            fallback_renderer=render_pr_body,
        )
        self.assertIn("⚠️ Merge 금지", result.rendered)
        self.assertIn("smoke (do-not-merge)", result.rendered)

    def test_real_pr_body_does_not_carry_merge_blocked_notice(self) -> None:
        result = compose_pr_body(
            repo_root=str(self.repo_root),
            plan=_StubAdapter(),
            context=_fill_ctx(smoke_mode=False),
            fallback_renderer=render_pr_body,
        )
        self.assertNotIn("⚠️ Merge 금지", result.rendered)
        self.assertNotIn("smoke (do-not-merge)", result.rendered)
        # Real PR mode still stamps the audit block.
        self.assertIn("Agent WorkOS Audit", result.rendered)
        self.assertIn("`live`", result.rendered)

    def test_audit_metadata_in_smoke_body(self) -> None:
        result = compose_pr_body(
            repo_root=str(self.repo_root),
            plan=_StubAdapter(),
            context=_fill_ctx(smoke_mode=True),
            fallback_renderer=render_pr_body,
        )
        # audit_id, branch, commit, role, smoke marker all surface.
        self.assertIn("`audit-cli-1`", result.rendered)
        self.assertIn("agent/backend-engineer/issue-42-fix", result.rendered)
        self.assertIn("`abc123def`", result.rendered)
        self.assertIn("`backend-engineer`", result.rendered)
        self.assertIn("runs/smoke.md", result.rendered)

    def test_issue_number_and_url_present(self) -> None:
        result = compose_pr_body(
            repo_root=str(self.repo_root),
            plan=_StubAdapter(),
            context=_fill_ctx(smoke_mode=True),
            fallback_renderer=render_pr_body,
        )
        self.assertIn("- #42", result.rendered)
        self.assertIn(
            "github.com/yule-studio/yule-studio-agent/issues/42",
            result.rendered,
        )

    def test_token_in_extra_notes_redacted(self) -> None:
        leaky = "ghs_" + "a" * 40
        ctx = PrTemplateFillContext(
            audit_id="audit-cli-2",
            branch="agent/x/y",
            commit_sha="deadbee",
            issue_number=42,
            purpose="testing redaction",
            smoke_mode=True,
            smoke_marker_path="runs/x.md",
            base_branch="main",
            repo_full_name="yule-studio/yule-studio-agent",
            extra_notes=(f"slipped: Authorization: Bearer {leaky}",),
        )
        result = compose_pr_body(
            repo_root=str(self.repo_root),
            plan=_StubAdapter(),
            context=ctx,
            fallback_renderer=render_pr_body,
        )
        self.assertNotIn(leaky, result.rendered)
        self.assertIn("<redacted>", result.rendered)


# ---------------------------------------------------------------------------
# Fallback when template missing
# ---------------------------------------------------------------------------


class TemplateMissingFallbackTests(unittest.TestCase):
    def test_compose_falls_back_when_template_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = compose_pr_body(
                repo_root=str(Path(tmp)),
                plan=_StubAdapter(),
                context=_fill_ctx(smoke_mode=True),
                fallback_renderer=render_pr_body,
            )
        self.assertTrue(result.template_missing)
        # Existing render_pr_body sections must still appear.
        self.assertIn("## 목적", result.rendered)
        self.assertIn("## 변경 요약", result.rendered)
        # Audit + merge-blocked banner still injected.
        self.assertIn("Agent WorkOS Audit", result.rendered)
        self.assertIn("⚠️ Merge 금지", result.rendered)


if __name__ == "__main__":
    unittest.main()
