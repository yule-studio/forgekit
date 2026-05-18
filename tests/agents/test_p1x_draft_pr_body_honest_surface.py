"""P1-X — _draft_pr_body 가 live edit / record-only 정직 구분.

옛 _draft_pr_body 는 항상 "RecordOnlyCodeEditor 가 만든 계획 markdown
만 포함" + "live editor 미연결" + "mode: live (RecordOnly editor)" 라고
박아서, P1-V LiveCodeEditor 가 실제로 코드 수정한 PR 도 reviewer 가
record-only 라고 오해 → fake success.  본 모듈은 metadata 기반 분기 +
양쪽 validate_pr_body 통과 + 5 섹션 보존을 회귀한다.
"""

from __future__ import annotations

import unittest
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.governance.runtime_policy import validate_pr_body
from yule_orchestrator.agents.job_queue.coding_executor_live import (
    _draft_pr_body,
)
from yule_orchestrator.agents.job_queue.coding_executor_worker import (
    CodingExecuteRequest,
    WorktreeContext,
)


def _request(**overrides: Any) -> CodingExecuteRequest:
    base = {
        "session_id": "sess-p1x",
        "executor_role": "fullstack-engineer",
        "user_request": "implement /search route",
        "generated_prompt": "implement /search route",
        "write_scope": ("app/", "components/"),
        "forbidden_scope": (".github/workflows/",),
        "safety_rules": ("no force push", "no merge to main"),
        "base_branch": "main",
        "branch_hint": "feat/search-issue-5",
        "repo_full_name": "yule-studio/naver-search-clone",
        "issue_number": 5,
        "dry_run": False,
        "metadata": {},
    }
    base.update(overrides)
    return CodingExecuteRequest(**base)


def _live_edit_ctx(*, files=("app/api/search/route.ts", "components/SearchBox.tsx")) -> WorktreeContext:
    return WorktreeContext(
        branch="feat/search-issue-5",
        worktree_path="/tmp/wt-fake",
        base_commit_sha="cafe1234",
        edited_files=tuple(files),
        commit_sha="deadbeefcafef00d",
        metadata={
            "live_editor_apply": {
                "provider": "claude-cli",
                "model": "claude-sonnet-4-6",
                "detected_changed_files": list(files),
                "refused_by_scope": [],
                "refused_by_forbidden": [],
            }
        },
    )


def _record_only_ctx() -> WorktreeContext:
    return WorktreeContext(
        branch="feat/search-issue-5",
        worktree_path="/tmp/wt-fake",
        base_commit_sha="cafe1234",
        edited_files=("runs/coding-executor-plans/feat-search-issue-5.md",),
        commit_sha="deadbeefcafef00d",
    )


# ---------------------------------------------------------------------------
# Live-edit PR body
# ---------------------------------------------------------------------------


class LiveEditPRBodyTests(unittest.TestCase):
    def test_says_live_llm_editor(self) -> None:
        body = _draft_pr_body(_request(), _live_edit_ctx())
        self.assertIn("live LLM editor", body)

    def test_does_not_claim_record_only_plan_markdown(self) -> None:
        body = _draft_pr_body(_request(), _live_edit_ctx())
        self.assertNotIn("`RecordOnlyCodeEditor` 가 만든 계획 markdown 만 포함", body)
        self.assertNotIn("live editor 미연결", body)

    def test_audit_mode_says_live_code_editor_not_record_only(self) -> None:
        body = _draft_pr_body(_request(), _live_edit_ctx())
        audit_lines = [
            ln for ln in body.splitlines() if "mode:" in ln and "live" in ln
        ]
        self.assertTrue(audit_lines, body)
        joined = "\n".join(audit_lines)
        self.assertIn("LiveCodeEditor", joined)
        self.assertNotIn("RecordOnly editor", joined)

    def test_lists_changed_files(self) -> None:
        body = _draft_pr_body(_request(), _live_edit_ctx())
        self.assertIn("app/api/search/route.ts", body)
        self.assertIn("components/SearchBox.tsx", body)

    def test_provider_and_model_visible(self) -> None:
        body = _draft_pr_body(_request(), _live_edit_ctx())
        self.assertIn("claude-cli", body)
        self.assertIn("claude-sonnet-4-6", body)

    def test_many_files_truncated(self) -> None:
        ten = tuple(f"app/page-{i}.tsx" for i in range(10))
        ctx = _live_edit_ctx(files=ten)
        body = _draft_pr_body(_request(), ctx)
        self.assertIn("app/page-0.tsx", body)
        self.assertIn("app/page-7.tsx", body)
        # 8 + 1 truncation line → page-8 / page-9 빠짐.
        self.assertIn("2 개 추가 파일 생략", body)
        self.assertNotIn("app/page-9.tsx", body)

    def test_refused_by_scope_surfaced(self) -> None:
        ctx = WorktreeContext(
            branch="feat/x",
            worktree_path="/tmp/wt",
            edited_files=("app/page.tsx",),
            commit_sha="abc",
            metadata={
                "live_editor_apply": {
                    "provider": "claude-cli",
                    "model": "claude-sonnet-4-6",
                    "detected_changed_files": ["app/page.tsx"],
                    "refused_by_scope": ["infrastructure/k8s.yaml"],
                    "refused_by_forbidden": [],
                }
            },
        )
        body = _draft_pr_body(_request(), ctx)
        self.assertIn("write_scope 밖", body)
        self.assertIn("infrastructure/k8s.yaml", body)

    def test_refused_by_forbidden_surfaced(self) -> None:
        ctx = WorktreeContext(
            branch="feat/x",
            worktree_path="/tmp/wt",
            edited_files=("app/page.tsx",),
            commit_sha="abc",
            metadata={
                "live_editor_apply": {
                    "provider": "claude-cli",
                    "model": "claude-sonnet-4-6",
                    "detected_changed_files": ["app/page.tsx"],
                    "refused_by_scope": [],
                    "refused_by_forbidden": [".github/workflows/ci.yaml"],
                }
            },
        )
        body = _draft_pr_body(_request(), ctx)
        self.assertIn("forbidden_scope", body)
        self.assertIn(".github/workflows/ci.yaml", body)

    def test_passes_validate_pr_body(self) -> None:
        body = _draft_pr_body(_request(), _live_edit_ctx())
        result = validate_pr_body(body)
        self.assertTrue(result.ok, result)
        self.assertEqual(result.missing_sections, ())
        self.assertTrue(result.audit_block_present)

    def test_includes_issue_link(self) -> None:
        body = _draft_pr_body(_request(issue_number=5), _live_edit_ctx())
        self.assertIn("close #5", body)


# ---------------------------------------------------------------------------
# Record-only PR body (옛 동작 회귀 가드)
# ---------------------------------------------------------------------------


class RecordOnlyPRBodyTests(unittest.TestCase):
    def test_says_record_only_plan_markdown(self) -> None:
        body = _draft_pr_body(_request(), _record_only_ctx())
        self.assertIn("`RecordOnlyCodeEditor` 가 만든 계획 markdown 만 포함", body)

    def test_audit_mode_still_record_only(self) -> None:
        body = _draft_pr_body(_request(), _record_only_ctx())
        self.assertIn("RecordOnly editor", body)

    def test_does_not_claim_live_llm_edit(self) -> None:
        body = _draft_pr_body(_request(), _record_only_ctx())
        self.assertNotIn("live LLM editor", body)

    def test_passes_validate_pr_body(self) -> None:
        body = _draft_pr_body(_request(), _record_only_ctx())
        result = validate_pr_body(body)
        self.assertTrue(result.ok, result)
        self.assertEqual(result.missing_sections, ())

    def test_live_audit_with_plan_markdown_only_stays_record_only(self) -> None:
        ctx = WorktreeContext(
            branch="feat/search-issue-5",
            worktree_path="/tmp/wt-fake",
            base_commit_sha="cafe1234",
            edited_files=("runs/coding-executor-plans/feat-search-issue-5.md",),
            commit_sha="deadbeefcafef00d",
            metadata={
                "live_editor_apply": {
                    "provider": "claude-cli",
                    "model": "claude-sonnet-4-6",
                    "detected_changed_files": [
                        "runs/coding-executor-plans/feat-search-issue-5.md"
                    ],
                }
            },
        )
        body = _draft_pr_body(_request(), ctx)
        self.assertIn("`RecordOnlyCodeEditor` 가 만든 계획 markdown 만 포함", body)
        self.assertIn("RecordOnly editor", body)
        self.assertNotIn("live LLM editor", body)


# ---------------------------------------------------------------------------
# Source-grep guard
# ---------------------------------------------------------------------------


class WiringGuardTests(unittest.TestCase):
    def test_draft_pr_body_branches_on_live_editor_apply(self) -> None:
        import inspect

        from yule_orchestrator.agents.job_queue import coding_executor_live as mod

        source = inspect.getsource(mod._draft_pr_body)
        self.assertIn("live_editor_apply", source)
        self.assertIn("live LLM editor", source)
        self.assertIn("LiveCodeEditor", source)
        # 옛 record-only 경로 보존 회귀
        self.assertIn("RecordOnly editor", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
