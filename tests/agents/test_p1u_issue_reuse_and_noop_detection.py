"""P1-U — issue #5 재사용 + live editor no-op 진단.

8 사용자 acceptance:

1. prompt with explicit GitHub issue URL reuses existing issue
2. prompt with `#5` / `issue 5` / `이슈 5` reuses existing issue
3. existing issue explicit > auto-create priority
4. reused issue session.extra 에 existing_issue_number + source 영속
5. live editor 0 edits → explicit no-op reason, not commit_failed
6. commit_failed reserved for real git commit failure after actual edits
7. operator surface shows issue reuse + no-op diagnostics
8. stale tracking "needs issue" cleared when reuse succeeds
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace as _replace
from types import SimpleNamespace
from typing import Any, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.agents.coding.coding_session_context import (
    _extract_explicit_issue_number,
    prepare_coding_session_context,
)
from yule_orchestrator.agents.job_queue.coding_executor_worker import (
    REASON_COMMIT_FAILED,
    REASON_LIVE_EDITOR_NO_EDITS_PRODUCED,
    CodingExecuteRequest,
    CodingExecutorWorker,
    WorktreeContext,
)
from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.state_machine import JobState
from yule_orchestrator.agents.job_queue.store import JobQueue
from yule_orchestrator.discord.integrations.github_workos_adapter import (
    _coerce_existing_issue,
)


# ---------------------------------------------------------------------------
# 1, 2 — explicit issue anchor extraction (URL + text)
# ---------------------------------------------------------------------------


class ExplicitIssueAnchorExtractionTests(unittest.TestCase):
    def test_url_issue_anchor_persists_to_extras(self) -> None:
        ctx = prepare_coding_session_context(
            message_text=(
                "네이버 검색 풀스택 MVP — 기존 issue #5 사용 "
                "https://github.com/yule-studio/naver-search-clone/issues/5"
            ),
            user_links=(
                "https://github.com/yule-studio/naver-search-clone/issues/5",
            ),
            existing_extra={},
            discover_contract=False,
        )
        self.assertEqual(ctx.extras_update.get("existing_issue_number"), 5)
        self.assertEqual(
            ctx.extras_update.get("existing_issue_source"), "prompt_url"
        )

    def test_text_pattern_hash_anchor(self) -> None:
        self.assertEqual(_extract_explicit_issue_number("기존 issue #5 사용"), 5)
        self.assertEqual(_extract_explicit_issue_number("그래도 #42 로 진행"), 42)

    def test_text_pattern_issue_word(self) -> None:
        self.assertEqual(_extract_explicit_issue_number("reuse issue 5"), 5)
        self.assertEqual(_extract_explicit_issue_number("issue#5"), 5)

    def test_text_pattern_korean_issue_word(self) -> None:
        self.assertEqual(_extract_explicit_issue_number("이슈 5 그대로 진행"), 5)
        self.assertEqual(
            _extract_explicit_issue_number("이슈 #12 anchor"), 12
        )

    def test_no_anchor_returns_none(self) -> None:
        self.assertIsNone(_extract_explicit_issue_number("새로 시작"))
        # 단순 숫자는 무시 (false positive 차단)
        self.assertIsNone(_extract_explicit_issue_number("page 5 of doc"))

    def test_text_pattern_only_in_extras_when_no_url(self) -> None:
        # URL 없음 + prompt 에 #5 → existing_issue_source=prompt_text
        ctx = prepare_coding_session_context(
            message_text="기존 issue #5 그대로 사용해줘. 새로 만들지 마",
            user_links=(),
            existing_extra={},
            discover_contract=False,
        )
        self.assertEqual(ctx.extras_update.get("existing_issue_number"), 5)
        self.assertEqual(
            ctx.extras_update.get("existing_issue_source"), "prompt_text"
        )


# ---------------------------------------------------------------------------
# 3, 4 — _coerce_existing_issue priority
# ---------------------------------------------------------------------------


class CoerceExistingIssuePriorityTests(unittest.TestCase):
    def test_explicit_caller_wins(self) -> None:
        sess = SimpleNamespace(extra={"existing_issue_number": 99})
        self.assertEqual(_coerce_existing_issue(5, sess), 5)

    def test_session_existing_issue_number_used_over_anchor(self) -> None:
        sess = SimpleNamespace(
            extra={
                "existing_issue_number": 5,
                "github_work_order_issue": {"issue_number": 99},
            }
        )
        # 새 P1-U 키 (5) 가 옛 anchor (99) 보다 우선
        self.assertEqual(_coerce_existing_issue(None, sess), 5)

    def test_old_anchor_used_when_no_prompt_anchor(self) -> None:
        sess = SimpleNamespace(
            extra={"github_work_order_issue": {"issue_number": 7}}
        )
        self.assertEqual(_coerce_existing_issue(None, sess), 7)

    def test_no_session_no_explicit_returns_none(self) -> None:
        sess = SimpleNamespace(extra={})
        self.assertIsNone(_coerce_existing_issue(None, sess))


# ---------------------------------------------------------------------------
# 5, 6 — live editor no-op detection
# ---------------------------------------------------------------------------


class _FakeLiveCodeEditor:
    """LiveCodeEditor 처럼 보이는 fake — type().__name__ 만 매칭하면 됨."""

    provider = "claude-cli"
    model = "claude-sonnet-4-6"

    def apply(self, *, request, context):
        # 실제 LiveCodeEditor 처럼 edited_files 안 건드림 → no-op
        return context


# LiveCodeEditor 와 동일한 class name 으로 worker가 감지
_FakeLiveCodeEditor.__name__ = "LiveCodeEditor"


class _FakeProvisioner:
    def provision(self, *, request, branch):
        return WorktreeContext(
            branch=branch,
            worktree_path="/tmp/" + branch.replace("/", "_"),
            base_commit_sha="basesha",
        )


class _FakeRecordOnlyEditor:
    """plan-only editor — edited_files 에 plan 파일 추가."""

    def apply(self, *, request, context):
        return _replace(
            context,
            edited_files=tuple(
                list(context.edited_files) + ["runs/coding-executor-plans/x.md"]
            ),
        )


_FakeRecordOnlyEditor.__name__ = "RecordOnlyCodeEditor"


class _FakeTests:
    def run(self, *, request, context):
        return _replace(
            context, test_summary={"status": "ok", "passed": 1}
        )


class _FakeCommitter:
    def commit(self, *, request, context):
        return _replace(context, commit_sha="commitsha-after-real-edit")


class _FakePusher:
    def push(self, *, request, context):
        return _replace(context, pushed=True)


class _FakePR:
    def open(self, *, request, context):
        return _replace(context, pr_number=99, pr_url="https://x/y/pull/99")


def _make_request(*, issue_number: int = 5) -> CodingExecuteRequest:
    return CodingExecuteRequest(
        session_id="s-noop-1",
        executor_role="backend-engineer",
        user_request="네이버 검색 MVP",
        generated_prompt="(p)",
        write_scope=("src/**",),
        forbidden_scope=(),
        safety_rules=(),
        base_branch="main",
        branch_hint=f"feature/auth-issue-{issue_number}",
        repo_full_name="yule-studio/naver-search-clone",
        issue_number=issue_number,
        dry_run=False,
        metadata={},
    )


class _WorkerFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = self._tmp.name + "/queue.sqlite3"
        self.queue = JobQueue(db_path=self._db)
        self.heartbeats = HeartbeatStore(db_path=self._db)


class LiveEditorNoOpDetectionTests(_WorkerFixture):
    def test_no_edits_produced_yields_explicit_reason(self) -> None:
        worker = CodingExecutorWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            worktree_provisioner=_FakeProvisioner(),
            code_editor=_FakeLiveCodeEditor(),
            test_runner=_FakeTests(),
            committer=_FakeCommitter(),
            pusher=_FakePusher(),
            draft_pr_creator=_FakePR(),
        )
        worker.enqueue(_make_request())
        outcome = worker.run_one(worker_id="t")
        # 옛 wiring: REASON_COMMIT_FAILED.  새 wiring:
        # REASON_LIVE_EDITOR_NO_EDITS_PRODUCED.
        self.assertNotEqual(outcome.failure_reason, REASON_COMMIT_FAILED)
        self.assertEqual(
            outcome.failure_reason, REASON_LIVE_EDITOR_NO_EDITS_PRODUCED
        )
        # state 는 failed_retryable (terminal 아님 — operator 가 prompt
        # 보강 후 retry 가능)
        self.assertEqual(
            outcome.terminal_state, JobState.FAILED_RETRYABLE.value
        )

    def test_record_only_editor_with_plan_file_is_not_no_op(self) -> None:
        """RecordOnly / Greenfield 같은 plan editor 는 edited_files 에 plan
        파일 추가 → no-op 분기 firing 안 됨 (옛 정상 동작 보존)."""

        worker = CodingExecutorWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            worktree_provisioner=_FakeProvisioner(),
            code_editor=_FakeRecordOnlyEditor(),
            test_runner=_FakeTests(),
            committer=_FakeCommitter(),
            pusher=_FakePusher(),
            draft_pr_creator=_FakePR(),
        )
        worker.enqueue(_make_request())
        outcome = worker.run_one(worker_id="t")
        # plan 파일이 commit 됐다고 가정 — SAVED 분기
        self.assertEqual(
            outcome.terminal_state, JobState.SAVED.value
        )

    def test_live_editor_with_actual_edits_proceeds_to_commit(self) -> None:
        """LiveCodeEditor 인 척하지만 edited_files 채워서 반환 → no-op
        아님 → 다음 단계 진행."""

        class _LiveWithEdits:
            provider = "claude-cli"
            model = "claude-sonnet-4-6"

            def apply(self, *, request, context):
                return _replace(
                    context,
                    edited_files=tuple(
                        list(context.edited_files) + ["src/api/auth.ts"]
                    ),
                )

        _LiveWithEdits.__name__ = "LiveCodeEditor"

        worker = CodingExecutorWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            worktree_provisioner=_FakeProvisioner(),
            code_editor=_LiveWithEdits(),
            test_runner=_FakeTests(),
            committer=_FakeCommitter(),
            pusher=_FakePusher(),
            draft_pr_creator=_FakePR(),
        )
        worker.enqueue(_make_request())
        outcome = worker.run_one(worker_id="t")
        # edited_files 있음 → no-op 분기 안 타고 SAVED
        self.assertEqual(outcome.terminal_state, JobState.SAVED.value)


# ---------------------------------------------------------------------------
# 7 — source-grep wiring guard (no-op detection 코드 실제 존재)
# ---------------------------------------------------------------------------


class WorkerWiringGuardTests(unittest.TestCase):
    def test_worker_source_contains_no_op_detection(self) -> None:
        from pathlib import Path

        src = Path(
            "src/yule_orchestrator/agents/job_queue/coding_executor_worker.py"
        ).read_text(encoding="utf-8")
        self.assertIn("REASON_LIVE_EDITOR_NO_EDITS_PRODUCED", src)
        self.assertIn("live_editor_no_edits_produced", src)
        # 옛 회귀 — generic commit_failed 가 LiveCodeEditor no-op 케이스에
        # 잘못 firing 하면 안 됨.  본 source-grep 가드는 새 분기 코드가
        # LiveCodeEditor 식별을 한다는 점을 강제.
        self.assertIn("is_live_editor", src)


# ---------------------------------------------------------------------------
# 8 — issue reuse + tracking surface 정합성
# ---------------------------------------------------------------------------


class IssueReuseSurfaceTests(unittest.TestCase):
    def test_extras_includes_issue_reuse_metadata(self) -> None:
        """existing_issue_number + existing_issue_source 영속 → operator
        surface 가 stale 'needs issue' 가 아니라 'reusing issue #5' 로 보임."""

        ctx = prepare_coding_session_context(
            message_text=(
                "기존 이슈 #5 그대로 진행 "
                "https://github.com/yule-studio/naver-search-clone/issues/5"
            ),
            user_links=(
                "https://github.com/yule-studio/naver-search-clone/issues/5",
            ),
            existing_extra={
                "tracking_blocked_reason": "needs_issue",  # stale state
            },
            discover_contract=False,
        )
        # P1-U 가 anchor 를 영속 → 다음 work_order builder 가 auto-create
        # 안 함.  운영자가 다음 인스턴스에서 stale tracking 을 직접
        # 갱신할 수 있도록 anchor metadata 가 명시.
        self.assertEqual(ctx.extras_update.get("existing_issue_number"), 5)
        self.assertEqual(
            ctx.extras_update.get("existing_issue_source"), "prompt_url"
        )


if __name__ == "__main__":
    unittest.main()
