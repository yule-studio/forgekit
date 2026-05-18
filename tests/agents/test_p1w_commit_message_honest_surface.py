"""P1-W — _commit_message 가 live-edit / record-only 정직 구분.

옛 wiring 은 항상 "RecordOnly editor 의 dry 산출" 본문을 만들어서 P1-V
LiveCodeEditor 가 실제 코드 수정한 commit 도 "이건 dry plan" 이라고
거짓말했다.  본 모듈은 context.metadata["live_editor_apply"] +
edited_files 신호 시 새 본문 + ✨ gitmoji 가 떨어지고, 옛 record-only
경로는 그대로 보존되며, 양쪽 다 enforce_commit_message 통과하는지
회귀한다.

stdlib unittest 만 사용.
"""

from __future__ import annotations

import unittest
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.governance.repo_write_policy import (
    enforce_commit_message,
)
from yule_orchestrator.agents.job_queue.coding_executor_live import (
    _commit_message,
)
from yule_orchestrator.agents.job_queue.coding_executor_worker import (
    CodingExecuteRequest,
    WorktreeContext,
)


def _request(**overrides: Any) -> CodingExecuteRequest:
    base = {
        "session_id": "sess-p1w",
        "executor_role": "fullstack-engineer",
        "user_request": "implement /search route",
        "generated_prompt": "implement /search route under app/api/search/",
        "write_scope": ("app/",),
        "forbidden_scope": (),
        "safety_rules": (),
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
        # metadata 비어있음 → record-only 분기.
    )


# ---------------------------------------------------------------------------
# Live-edit branch
# ---------------------------------------------------------------------------


class LiveEditCommitMessageTests(unittest.TestCase):
    def test_uses_sparkle_gitmoji_for_live_edit(self) -> None:
        msg = _commit_message(_request(), _live_edit_ctx())
        first_line = msg.splitlines()[0]
        self.assertTrue(first_line.startswith("✨"), first_line)
        self.assertNotIn("📝", first_line)

    def test_includes_issue_number(self) -> None:
        msg = _commit_message(_request(issue_number=5), _live_edit_ctx())
        self.assertIn("#5", msg.splitlines()[0])

    def test_title_says_구현_not_계획(self) -> None:
        msg = _commit_message(_request(), _live_edit_ctx())
        first_line = msg.splitlines()[0]
        self.assertIn("구현", first_line)
        self.assertNotIn("계획 기록", first_line)

    def test_body_lists_changed_files(self) -> None:
        msg = _commit_message(_request(), _live_edit_ctx())
        self.assertIn("app/api/search/route.ts", msg)
        self.assertIn("components/SearchBox.tsx", msg)

    def test_body_surfaces_provider_and_model(self) -> None:
        msg = _commit_message(_request(), _live_edit_ctx())
        self.assertIn("provider=claude-cli", msg)
        self.assertIn("model=claude-sonnet-4-6", msg)

    def test_body_says_live_llm_editor_not_recordonly(self) -> None:
        msg = _commit_message(_request(), _live_edit_ctx())
        self.assertIn("live LLM editor", msg)
        self.assertNotIn("RecordOnly editor 의 dry 산출", msg)

    def test_many_files_truncated_with_summary(self) -> None:
        ten_files = tuple(f"app/page-{i}.tsx" for i in range(10))
        ctx = _live_edit_ctx(files=ten_files)
        msg = _commit_message(_request(), ctx)
        # 처음 5 개만 list 되고 그 다음 한 줄로 "… (N 개 추가)" 가 와야.
        self.assertIn("app/page-0.tsx", msg)
        self.assertIn("app/page-4.tsx", msg)
        self.assertIn("5 개 추가 파일 생략", msg)
        # 6+ 인덱스는 본문에서 빠져야 함 (truncated).
        self.assertNotIn("app/page-7.tsx", msg)

    def test_refused_by_scope_surfaced_when_present(self) -> None:
        ctx = WorktreeContext(
            branch="feat/x",
            worktree_path="/tmp/wt",
            base_commit_sha="abc",
            edited_files=("app/page.tsx",),
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
        msg = _commit_message(_request(), ctx)
        self.assertIn("write_scope 밖", msg)
        self.assertIn("infrastructure/k8s.yaml", msg)

    def test_refused_by_forbidden_surfaced_when_present(self) -> None:
        ctx = WorktreeContext(
            branch="feat/x",
            worktree_path="/tmp/wt",
            base_commit_sha="abc",
            edited_files=("app/page.tsx",),
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
        msg = _commit_message(_request(), ctx)
        self.assertIn("forbidden_scope", msg)
        self.assertIn(".github/workflows/ci.yaml", msg)

    def test_passes_enforce_commit_message(self) -> None:
        msg = _commit_message(_request(), _live_edit_ctx())
        # raise PolicyViolation if not policy-compliant
        enforce_commit_message(msg, is_initial=False)

    def test_override_gitmoji_respected(self) -> None:
        # 운영자가 metadata 로 명시한 gitmoji 가 ✨ 보다 우선.
        ctx = _live_edit_ctx()
        new_metadata = dict(ctx.metadata or {})
        new_metadata["commit_gitmoji_override"] = "🐛"
        ctx = WorktreeContext(
            branch=ctx.branch,
            worktree_path=ctx.worktree_path,
            base_commit_sha=ctx.base_commit_sha,
            edited_files=ctx.edited_files,
            metadata=new_metadata,
        )
        msg = _commit_message(_request(), ctx)
        self.assertTrue(msg.splitlines()[0].startswith("🐛"))


# ---------------------------------------------------------------------------
# Record-only branch (옛 동작 회귀 가드)
# ---------------------------------------------------------------------------


class RecordOnlyCommitMessageTests(unittest.TestCase):
    def test_uses_memo_gitmoji_for_record_only(self) -> None:
        msg = _commit_message(_request(), _record_only_ctx())
        self.assertTrue(msg.splitlines()[0].startswith("📝"))

    def test_says_recordonly_dry(self) -> None:
        msg = _commit_message(_request(), _record_only_ctx())
        self.assertIn("RecordOnly editor 의 dry 산출", msg)
        self.assertNotIn("live LLM editor", msg)

    def test_passes_enforce_commit_message(self) -> None:
        msg = _commit_message(_request(), _record_only_ctx())
        enforce_commit_message(msg, is_initial=False)

    def test_no_live_audit_no_edited_files_still_record_only(self) -> None:
        # edge — 정말 0건 이지만 metadata 가 빈 경우.  record-only 분기로
        # 떨어진다 (옛 behavior 유지).
        ctx = WorktreeContext(branch="x", worktree_path="/tmp", edited_files=())
        msg = _commit_message(_request(), ctx)
        self.assertTrue(msg.splitlines()[0].startswith("📝"))

    def test_live_audit_with_plan_markdown_only_stays_record_only(self) -> None:
        ctx = WorktreeContext(
            branch="feat/search-issue-5",
            worktree_path="/tmp/wt-fake",
            base_commit_sha="cafe1234",
            edited_files=("runs/coding-executor-plans/feat-search-issue-5.md",),
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
        msg = _commit_message(_request(), ctx)
        self.assertTrue(msg.splitlines()[0].startswith("📝"), msg)
        self.assertIn("RecordOnly editor 의 dry 산출", msg)
        self.assertNotIn("live LLM editor", msg)


# ---------------------------------------------------------------------------
# Source-grep guard
# ---------------------------------------------------------------------------


class WiringGuardTests(unittest.TestCase):
    def test_commit_message_source_branches_on_live_editor_apply(self) -> None:
        import inspect

        from yule_orchestrator.agents.job_queue import coding_executor_live as mod

        source = inspect.getsource(mod._commit_message)
        self.assertIn("live_editor_apply", source)
        self.assertIn("live LLM editor", source)
        self.assertIn("RecordOnly", source)  # 옛 경로 보존 회귀


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
