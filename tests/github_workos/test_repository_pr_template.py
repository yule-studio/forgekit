"""repository_pr_template — discovery + fill tests.

Pin:

  * priority order of PR-template discovery,
  * directory-mode "first stable .md" selection,
  * section-aware fill for the in-repo template (Korean headings
    with emoji prefixes),
  * smoke-mode merge-blocked banner injection,
  * non-smoke mode does NOT inject the banner,
  * audit block always appears with audit_id / branch / commit /
    role / actor,
  * secret redaction over the final body,
  * graceful template-missing fallback to ``render_pr_body``.
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.github_workos.pr_template import render_pr_body
from yule_orchestrator.agents.github_workos.repository_pr_template import (
    DEFAULT_TEMPLATE_PATHS,
    PrTemplateFillContext,
    TEMPLATE_REASON_FOUND_DIRECTORY_FIRST,
    TEMPLATE_REASON_FOUND_FILE,
    TEMPLATE_REASON_NOT_FOUND,
    compose_pr_body,
    discover_repository_pr_template,
    fill_repository_pr_template,
)


@dataclass
class _StubPlan:
    title: str = "Bug: API 401 in users endpoint"
    body: str = "오늘 운영 환경에서 401이 떨어지고 있어요"
    primary_role: str = "backend-engineer"
    autonomy_level: str = "L2"
    issue_number: Optional[int] = 42
    session_id: Optional[str] = None
    repo: Optional[str] = "yule-studio/yule-studio-agent"
    in_scope: Sequence[str] = ()
    out_of_scope: Sequence[str] = ()
    test_plan: Sequence[str] = ()
    risks: Sequence[str] = ()
    approvals_needed: Sequence[str] = ()
    work_orders: Sequence[Mapping[str, str]] = ()


def _ctx(**overrides) -> PrTemplateFillContext:
    base = {
        "audit_id": "audit-abc",
        "branch": "agent/backend-engineer/issue-42-fix",
        "commit_sha": "deadbee",
        "primary_role": "backend-engineer",
        "autonomy_level": "L2",
        "issue_number": 42,
        "issue_url": "https://github.com/yule-studio/yule-studio-agent/issues/42",
        "purpose": "users endpoint 의 401 회귀를 회복한다.",
        "change_summary": ("services/auth/handlers.py 토큰 만료 갱신",),
        "test_plan": ("python -m unittest tests.auth",),
        "risks": ("토큰 만료 캐시 무효화",),
        "approvals_needed": ("backend-engineer 검토",),
        "work_orders": ({"autonomy_level": "L2", "action": "code_diff"},),
        "trace_links": {
            "github": "https://github.com/yule-studio/yule-studio-agent/issues/42",
            "discord": "https://discord.com/channels/1/2/3",
        },
        "smoke_mode": False,
        "smoke_marker_path": "",
        "base_branch": "main",
        "repo_full_name": "yule-studio/yule-studio-agent",
        "extra_notes": (),
    }
    base.update(overrides)
    return PrTemplateFillContext(**base)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class DiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".github").mkdir()

    def test_discovers_lowercase_md_first(self) -> None:
        (self.root / ".github" / "pull_request_template.md").write_text("## low\n", encoding="utf-8")
        (self.root / ".github" / "PULL_REQUEST_TEMPLATE.md").write_text("## up\n", encoding="utf-8")
        result = discover_repository_pr_template(repo_root=str(self.root))
        self.assertTrue(result.found)
        self.assertTrue(result.source_path.endswith("pull_request_template.md"))

    def test_discovers_extensionless_template(self) -> None:
        (self.root / ".github" / "PULL_REQUEST_TEMPLATE").write_text(
            "## ✨ 과제 내용\nbody\n", encoding="utf-8"
        )
        result = discover_repository_pr_template(repo_root=str(self.root))
        self.assertTrue(result.found)
        self.assertEqual(result.discovery_reason, TEMPLATE_REASON_FOUND_FILE)
        self.assertIn("과제 내용", result.section_headings[0])

    def test_directory_picks_first_md_alphabetically(self) -> None:
        td = self.root / ".github" / "PULL_REQUEST_TEMPLATE"
        td.mkdir()
        (td / "10-bug.md").write_text("## bug\n", encoding="utf-8")
        (td / "00-default.md").write_text("## default\n", encoding="utf-8")
        result = discover_repository_pr_template(repo_root=str(self.root))
        self.assertTrue(result.found)
        self.assertEqual(result.discovery_reason, TEMPLATE_REASON_FOUND_DIRECTORY_FIRST)
        self.assertTrue(result.source_path.endswith("00-default.md"))

    def test_directory_skips_hidden_files(self) -> None:
        td = self.root / ".github" / "PULL_REQUEST_TEMPLATE"
        td.mkdir()
        (td / ".secret.md").write_text("nope\n", encoding="utf-8")
        (td / "real.md").write_text("## real\n", encoding="utf-8")
        result = discover_repository_pr_template(repo_root=str(self.root))
        self.assertTrue(result.source_path.endswith("real.md"))

    def test_returns_not_found_when_absent(self) -> None:
        result = discover_repository_pr_template(repo_root=str(self.root))
        self.assertFalse(result.found)
        self.assertIsNone(result.source_path)
        self.assertEqual(result.discovery_reason, TEMPLATE_REASON_NOT_FOUND)

    def test_priority_order_matches_default(self) -> None:
        # All seven default candidates must be considered before
        # giving up. Sanity-check the export hasn't drifted.
        self.assertIn(".github/pull_request_template.md", DEFAULT_TEMPLATE_PATHS)
        self.assertIn(".github/PULL_REQUEST_TEMPLATE", DEFAULT_TEMPLATE_PATHS)


# ---------------------------------------------------------------------------
# Fill — section mapping
# ---------------------------------------------------------------------------


_REPO_TEMPLATE = textwrap.dedent(
    """\
    ## 📌 관련 이슈
    <!-- 관련있는 이슈 번호(#000)을 적어주세요. -->

    ## ✨ 과제 내용
    <!-- 과제에 대한 설명을 적어주세요 -->

    ## :camera_with_flash: 스크린샷(선택)
    <!-- 스크린샷이 필요한 과제면 스크린샷을 첨부해주세요 -->

    ## 📚 레퍼런스 (또는 새로 알게 된 내용) 혹은 궁금한 사항들
    <!-- 참고할 사항이 있다면 적어주세요 -->
    """
)


class FillTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".github").mkdir()
        (self.root / ".github" / "PULL_REQUEST_TEMPLATE").write_text(
            _REPO_TEMPLATE, encoding="utf-8"
        )
        self.template = discover_repository_pr_template(repo_root=str(self.root))

    def test_template_section_headings_preserved(self) -> None:
        filled = fill_repository_pr_template(self.template, _ctx())
        for needle in (
            "## 📌 관련 이슈",
            "## ✨ 과제 내용",
            "스크린샷",
            "## 📚 레퍼런스",
        ):
            self.assertIn(needle, filled.rendered, needle)

    def test_issue_number_and_url_in_related_issue_section(self) -> None:
        filled = fill_repository_pr_template(self.template, _ctx())
        # ``- #42`` and the full URL both surface.
        self.assertIn("- #42", filled.rendered)
        self.assertIn("github.com/yule-studio/yule-studio-agent/issues/42", filled.rendered)

    def test_purpose_and_change_summary_in_task_section(self) -> None:
        filled = fill_repository_pr_template(self.template, _ctx())
        self.assertIn("users endpoint 의 401", filled.rendered)
        self.assertIn("services/auth/handlers.py 토큰 만료 갱신", filled.rendered)

    def test_screenshot_section_marked_not_applicable(self) -> None:
        filled = fill_repository_pr_template(self.template, _ctx())
        self.assertIn("agent-generated PR", filled.rendered)

    def test_reference_section_carries_trace_links(self) -> None:
        filled = fill_repository_pr_template(self.template, _ctx())
        self.assertIn("**github**", filled.rendered)
        self.assertIn("**discord**", filled.rendered)

    def test_audit_block_always_present(self) -> None:
        filled = fill_repository_pr_template(self.template, _ctx())
        self.assertIn("Agent WorkOS Audit", filled.rendered)
        self.assertIn("`audit-abc`", filled.rendered)
        self.assertIn("agent/backend-engineer/issue-42-fix", filled.rendered)
        self.assertIn("`deadbee`", filled.rendered)
        self.assertIn("`backend-engineer`", filled.rendered)
        self.assertIn("yule-studio-engineering-agent[bot]", filled.rendered)

    def test_smoke_mode_prepends_merge_blocked_notice(self) -> None:
        filled = fill_repository_pr_template(
            self.template, _ctx(smoke_mode=True, smoke_marker_path="runs/smoke.md")
        )
        self.assertIn("⚠️ Merge 금지", filled.rendered)
        self.assertIn("smoke (do-not-merge)", filled.rendered)
        self.assertTrue(
            filled.rendered.lstrip().startswith("## ⚠️ Merge 금지"),
            "merge-blocked banner must be the first section",
        )

    def test_non_smoke_mode_omits_merge_blocked_notice(self) -> None:
        filled = fill_repository_pr_template(self.template, _ctx(smoke_mode=False))
        self.assertNotIn("⚠️ Merge 금지", filled.rendered)
        self.assertNotIn("smoke (do-not-merge)", filled.rendered)
        self.assertIn("`live`", filled.rendered)

    def test_secret_like_strings_redacted(self) -> None:
        # Real GitHub installation tokens are 36+ chars after the
        # ``ghs_`` prefix. Use a length above the redaction threshold.
        leaky = "ghs_" + ("a" * 40)
        ctx = _ctx(
            extra_notes=(f"debug info: Authorization: Bearer {leaky}",),
        )
        filled = fill_repository_pr_template(self.template, ctx)
        self.assertNotIn(leaky, filled.rendered)
        self.assertIn("<redacted>", filled.rendered)

    def test_pem_block_redacted(self) -> None:
        ctx = _ctx(
            purpose=(
                "I leaked a key:\n"
                "-----BEGIN RSA PRIVATE KEY-----\n"
                "SECRET-LEAKED\n"
                "-----END RSA PRIVATE KEY-----\n"
                "should be scrubbed"
            ),
        )
        filled = fill_repository_pr_template(self.template, ctx)
        self.assertNotIn("SECRET-LEAKED", filled.rendered)
        self.assertIn("<redacted>", filled.rendered)


# ---------------------------------------------------------------------------
# Fallback path — no template found
# ---------------------------------------------------------------------------


class ComposeFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_compose_uses_template_when_present(self) -> None:
        (self.root / ".github").mkdir()
        (self.root / ".github" / "PULL_REQUEST_TEMPLATE").write_text(
            _REPO_TEMPLATE, encoding="utf-8"
        )
        result = compose_pr_body(
            repo_root=str(self.root),
            plan=_StubPlan(),
            context=_ctx(),
            fallback_renderer=render_pr_body,
        )
        self.assertFalse(result.template_missing)
        self.assertIn("관련 이슈", result.rendered)
        self.assertIn("Agent WorkOS Audit", result.rendered)

    def test_compose_falls_back_when_no_template(self) -> None:
        result = compose_pr_body(
            repo_root=str(self.root),
            plan=_StubPlan(),
            context=_ctx(),
            fallback_renderer=render_pr_body,
        )
        self.assertTrue(result.template_missing)
        self.assertEqual(result.template_reason, TEMPLATE_REASON_NOT_FOUND)
        # Fallback still includes the audit block + the existing
        # ``render_pr_body`` sections.
        self.assertIn("Agent WorkOS Audit", result.rendered)
        self.assertIn("## 목적", result.rendered)
        self.assertIn("`audit-abc`", result.rendered)

    def test_fallback_smoke_mode_gets_merge_blocked_notice(self) -> None:
        result = compose_pr_body(
            repo_root=str(self.root),
            plan=_StubPlan(),
            context=_ctx(smoke_mode=True),
            fallback_renderer=render_pr_body,
        )
        self.assertTrue(result.template_missing)
        self.assertIn("⚠️ Merge 금지", result.rendered)

    def test_fallback_no_plan_yields_minimal_audit_body(self) -> None:
        result = compose_pr_body(
            repo_root=str(self.root),
            plan=None,
            context=_ctx(smoke_mode=True),
            fallback_renderer=render_pr_body,
        )
        self.assertIn("Agent WorkOS Audit", result.rendered)
        self.assertIn("⚠️ Merge 금지", result.rendered)


# ---------------------------------------------------------------------------
# Repo-root fixture — verify the actual in-tree template parses
# ---------------------------------------------------------------------------


class InRepoTemplateTests(unittest.TestCase):
    """Pin that the template currently shipped at
    ``.github/PULL_REQUEST_TEMPLATE`` is parsed cleanly. If someone
    renames it, this test fires before live smoke-pr regresses.
    """

    def test_in_repo_template_is_discovered(self) -> None:
        # Repo root resolves relative to this test file's tree.
        repo_root = Path(__file__).resolve().parents[2]
        result = discover_repository_pr_template(repo_root=str(repo_root))
        self.assertTrue(
            result.found,
            f"in-repo PR template not discovered (looked under {repo_root})",
        )
        self.assertGreaterEqual(len(result.section_headings), 3)


if __name__ == "__main__":
    unittest.main()
