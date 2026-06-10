"""P1-L-2 end-to-end — 10 사용자 명시 회귀 케이스.

1. explicit ``approval_required`` prompt 가 mode/topology/scope persist
2. explicit ``autonomous_merge`` prompt 가 mode/topology/scope persist
3. coding_execute 성공 → ``pr_merge_pending`` stage 진입
4. ``approval_required`` 모드에서 approval card 정확히 1 회만 enqueue
5. approval reply 가 ``handle_pr_merge_approval_reply`` 로 라우팅
6. merge 성공 시 session.extra 에 ``pr_merged`` stamp
7. merge 성공 후 next coding slice 자동 enqueue
8. ``autonomous_merge`` 모드가 auto-merge continuation path 시작
9. executor 가 single-writer 유지 — 동시 두 row 가 동일 anchor 로 enqueue 안 됨
10. FE/BE parallel planning metadata 가 audit 위해 session.extra 에 보존

각 케이스는 stdlib unittest 만 사용. 실제 GitHub / Discord 호출 없음.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_engineering.agents.coding.coding_session_context import (
    prepare_coding_session_context,
)
from yule_engineering.agents.lifecycle.session_mode import (
    EXTRA_DECIDED_AT,
    EXTRA_DECIDED_BY,
    EXTRA_SCOPE,
    EXTRA_TOPOLOGY,
    EXTRA_WORK_MODE,
    SCOPE_FULL_STACK,
    TOPOLOGY_SINGLE,
    WORK_MODE_APPROVAL,
    WORK_MODE_AUTONOMOUS,
    DECIDED_BY_USER,
    ensure_session_mode,
    parse_mode_hints,
)
from yule_engineering.agents.job_queue.coding_executor_worker import (
    CodingExecuteRequest,
    CodingExecutorWorker,
    JOB_TYPE_CODING_EXECUTE,
    WorktreeContext,
)
from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.next_slice_dispatcher import (
    EXTRA_CODING_BACKLOG,
    EXTRA_SESSION_COMPLETED_REASON,
    NextSliceAction,
    decide_next_slice,
    dispatch_next_coding_slice,
)
from yule_engineering.agents.job_queue.pr_approval import (
    PRMergeProposal,
    PRMergeReplyDispatch,
    PRMergeReplyIntent,
    PRMergeReplyResult,
)
from yule_engineering.agents.job_queue.pr_merge_continuation import (
    EXTRA_PR_MERGE_AUDIT,
    EXTRA_PR_MERGE_PR_NUMBER,
    EXTRA_PR_MERGE_REPO,
    EXTRA_PR_MERGE_STAGE,
    STAGE_PR_MERGE_BLOCKED,
    STAGE_PR_MERGE_PENDING,
    STAGE_PR_MERGED,
)
from yule_engineering.agents.job_queue.pr_merge_continuation_worker import (
    ACTION_APPROVAL_CARD_ENQUEUED,
    ACTION_AUTONOMOUS_MERGE_BLOCKED,
    ACTION_AUTONOMOUS_MERGE_SUCCEEDED,
    ACTION_SKIPPED_ALREADY_ENQUEUED,
    advance_pending_session,
    iter_pending_session_ids,
)
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue
from yule_engineering.agents.workflow_state import (
    WorkflowSession,
    WorkflowState,
    load_session,
    save_session,
)


# ---------------------------------------------------------------------------
# 공통 fixture
# ---------------------------------------------------------------------------


def _ctx_replace(ctx: WorktreeContext, **changes) -> WorktreeContext:
    from dataclasses import replace

    return replace(ctx, **changes)


class _FakeProvisioner:
    def provision(self, *, request, branch):
        return WorktreeContext(
            branch=branch,
            worktree_path=f"/tmp/wt-{branch.replace('/', '_')}",
            base_commit_sha="basesha0",
        )


class _FakeEditor:
    def apply(self, *, request, context):
        return _ctx_replace(context, edited_files=("src/x.py",))


class _FakeTests:
    def run(self, *, request, context):
        return _ctx_replace(
            context, test_summary={"status": "ok", "passed": 1}
        )


class _FakeCommitter:
    def commit(self, *, request, context):
        return _ctx_replace(context, commit_sha="commitsha")


class _FakePusher:
    def push(self, *, request, context):
        return _ctx_replace(context, pushed=True)


class _FakePRCreator:
    def __init__(self, *, pr_number: int = 42) -> None:
        self.pr_number = pr_number

    def open(self, *, request, context):
        return _ctx_replace(
            context,
            pr_number=self.pr_number,
            pr_url=(
                f"https://github.com/yule-studio/naver-search-clone/pull/"
                f"{self.pr_number}"
            ),
        )


def _request(session_id: str) -> CodingExecuteRequest:
    return CodingExecuteRequest(
        session_id=session_id,
        executor_role="backend-engineer",
        user_request="네이버 검색 풀스택 MVP 구현해줘",
        generated_prompt="(prompt)",
        write_scope=("src/**",),
        forbidden_scope=(),
        safety_rules=(),
        base_branch="main",
        branch_hint=f"agent/backend-engineer/issue-1-{session_id}",
        repo_full_name="yule-studio/naver-search-clone",
        issue_number=1,
        dry_run=False,
        metadata={},
    )


def _seed_session(
    session_id: str,
    *,
    extra: Optional[Mapping[str, Any]] = None,
) -> None:
    now = datetime.now(tz=timezone.utc)
    save_session(
        WorkflowSession(
            session_id=session_id,
            prompt="네이버 검색 풀스택 MVP 구현해줘",
            task_type="coding_execute",
            state=WorkflowState.IN_PROGRESS,
            created_at=now,
            updated_at=now,
            executor_role="backend-engineer",
            extra=dict(extra or {}),
        )
    )


class _WorkerFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=self._db)
        self.heartbeats = HeartbeatStore(db_path=self._db)

    def _build_worker(self, pr_number: int = 42) -> CodingExecutorWorker:
        return CodingExecutorWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            worktree_provisioner=_FakeProvisioner(),
            code_editor=_FakeEditor(),
            test_runner=_FakeTests(),
            committer=_FakeCommitter(),
            pusher=_FakePusher(),
            draft_pr_creator=_FakePRCreator(pr_number=pr_number),
        )


# ---------------------------------------------------------------------------
# 1. explicit approval_required prompt persists mode/topology/scope
# ---------------------------------------------------------------------------


class ExplicitApprovalRequiredPromptTests(unittest.TestCase):
    def test_explicit_approval_required_persists_mode(self) -> None:
        hints = parse_mode_hints(
            "approval_required, single_repo, full_stack_single_repo "
            "네이버 검색 풀스택 MVP 구현해줘"
        )
        self.assertEqual(hints["work_mode"], WORK_MODE_APPROVAL)
        self.assertEqual(hints["topology"], TOPOLOGY_SINGLE)
        self.assertEqual(hints["scope"], SCOPE_FULL_STACK)

        extra: dict = {}
        decision = ensure_session_mode(
            extra,
            user_hint_work_mode=hints["work_mode"],
            user_hint_topology=hints["topology"],
            user_hint_scope=hints["scope"],
        )
        self.assertEqual(extra[EXTRA_WORK_MODE], WORK_MODE_APPROVAL)
        self.assertEqual(extra[EXTRA_TOPOLOGY], TOPOLOGY_SINGLE)
        self.assertEqual(extra[EXTRA_SCOPE], SCOPE_FULL_STACK)
        self.assertEqual(extra[EXTRA_DECIDED_BY], DECIDED_BY_USER)
        self.assertIn(EXTRA_DECIDED_AT, extra)
        self.assertTrue(decision.persisted)


# ---------------------------------------------------------------------------
# 2. explicit autonomous_merge prompt persists mode/topology/scope
# ---------------------------------------------------------------------------


class ExplicitAutonomousMergePromptTests(unittest.TestCase):
    def test_explicit_autonomous_merge_persists_mode(self) -> None:
        ctx = prepare_coding_session_context(
            message_text=(
                "autonomous_merge, single_repo, full_stack_single_repo "
                "네이버 검색 풀스택 MVP 구현해줘 "
                "https://github.com/yule-studio/naver-search-clone"
            ),
            user_links=("https://github.com/yule-studio/naver-search-clone",),
            existing_extra={},
            discover_contract=False,
        )
        self.assertEqual(ctx.session_mode.work_mode, WORK_MODE_AUTONOMOUS)
        self.assertEqual(ctx.session_mode.topology, TOPOLOGY_SINGLE)
        self.assertEqual(ctx.session_mode.scope, SCOPE_FULL_STACK)
        # extras_update 가 session.extra 로 머지 가능한 dict 를 갖고 있어야
        # 함 — 호출자가 그대로 적용하면 영속됨.
        self.assertEqual(
            ctx.extras_update[EXTRA_WORK_MODE], WORK_MODE_AUTONOMOUS
        )


# ---------------------------------------------------------------------------
# 3. coding_execute success transitions to pr_merge_pending
# ---------------------------------------------------------------------------


class CodingExecuteSuccessTransitionsTests(_WorkerFixture):
    def test_success_stamps_pr_merge_pending(self) -> None:
        session_id = "txn-pending-1"
        _seed_session(
            session_id, extra={EXTRA_WORK_MODE: WORK_MODE_APPROVAL}
        )
        worker = self._build_worker()
        worker.enqueue(_request(session_id))
        outcome = worker.run_one(worker_id="t")
        self.assertEqual(outcome.terminal_state, JobState.SAVED.value)

        session = load_session(session_id)
        self.assertEqual(
            session.extra[EXTRA_PR_MERGE_STAGE], STAGE_PR_MERGE_PENDING
        )
        self.assertEqual(session.extra[EXTRA_PR_MERGE_PR_NUMBER], 42)


# ---------------------------------------------------------------------------
# 4. approval_required mode enqueues card exactly once (dedup)
# ---------------------------------------------------------------------------


class ApprovalCardEnqueuedOnceTests(unittest.TestCase):
    def test_two_sweeps_only_one_card_enqueued(self) -> None:
        enqueued: List[PRMergeProposal] = []

        @dataclass
        class _FakeEnqueueOutcome:
            approval_job_id: str = "fake-job-1"

        async def fake_enqueue(*, session, proposal, **_kwargs):
            enqueued.append(proposal)
            return _FakeEnqueueOutcome()

        extra: dict = {
            EXTRA_WORK_MODE: WORK_MODE_APPROVAL,
            EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
            EXTRA_PR_MERGE_PR_NUMBER: 7,
            EXTRA_PR_MERGE_REPO: "yule-studio/naver-search-clone",
            "pr_merge_pr_url": "https://github.com/yule-studio/naver-search-clone/pull/7",
            "pr_merge_head_sha": "sha7",
            "pr_merge_base_branch": "main",
        }

        persisted: List[Mapping[str, Any]] = []

        def persist(new_extra: Mapping[str, Any]) -> None:
            persisted.append(dict(new_extra))
            extra.clear()
            extra.update(new_extra)

        loop = asyncio.new_event_loop()
        try:
            outcome1 = loop.run_until_complete(
                advance_pending_session(
                    session_id="s",
                    session_extra=extra,
                    persist_extra=persist,
                    approval_enqueuer=fake_enqueue,
                )
            )
            outcome2 = loop.run_until_complete(
                advance_pending_session(
                    session_id="s",
                    session_extra=extra,
                    persist_extra=persist,
                    approval_enqueuer=fake_enqueue,
                )
            )
        finally:
            loop.close()
        self.assertEqual(outcome1.action, ACTION_APPROVAL_CARD_ENQUEUED)
        self.assertEqual(outcome2.action, ACTION_SKIPPED_ALREADY_ENQUEUED)
        self.assertEqual(len(enqueued), 1)
        # audit 에 enqueue event 한 줄 영구히
        audit = extra[EXTRA_PR_MERGE_AUDIT]
        events = [a for a in audit if a.get("event") == "approval_card_enqueued"]
        self.assertEqual(len(events), 1)


# ---------------------------------------------------------------------------
# 5. approval reply routes into handle_pr_merge_approval_reply
# ---------------------------------------------------------------------------


class ApprovalReplyRoutingTests(unittest.TestCase):
    def test_router_calls_handle_pr_merge_when_card_exists(self) -> None:
        """``_try_handle_pr_merge_reply`` 가 PR_MERGE 카드를 찾으면
        ``handle_pr_merge_approval_reply`` 를 호출하고 결과를 ack 한다.

        직접 import 가 가능한 helper 를 단위 테스트 — Discord 객체 없이.
        """

        from yule_engineering.discord.approval import reply_router as router_mod

        # ``handle_pr_merge_approval_reply`` 를 fake 로 교체해서 호출되는지
        # 확인. 진짜 queue scan 까지 하지 않아도 wiring 만 검증되면 충분.
        called: dict = {}

        async def fake_handler(**kwargs):
            called["kwargs"] = kwargs
            return PRMergeReplyResult(
                intent=PRMergeReplyIntent.APPROVE,
                merge_disabled=True,
            )

        # find_replyable_approval 도 fake — None 이 아니라 sentinel 반환.
        original_find = router_mod.find_replyable_approval
        original_handler = router_mod.handle_pr_merge_approval_reply
        router_mod.find_replyable_approval = lambda **kw: object()
        router_mod.handle_pr_merge_approval_reply = fake_handler

        sent: List[str] = []

        async def send_chunks(channel, text, **_kwargs):
            sent.append(text)

        class _FakeChannel:
            def __init__(self) -> None:
                self.id = 999

        class _FakeMessage:
            channel = _FakeChannel()

        try:
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    router_mod._try_handle_pr_merge_reply(
                        queue=None,  # fake_find ignores
                        text="승인",
                        session_id="s",
                        approved_by="codwithyc",
                        approved_at="2026-05-17T00:00:00+00:00",
                        source_message_id=1,
                        source_thread_id=2,
                        send_chunks=send_chunks,
                        message=_FakeMessage(),
                        merge_executor=None,
                        on_result=None,
                    )
                )
            finally:
                loop.close()
        finally:
            router_mod.find_replyable_approval = original_find
            router_mod.handle_pr_merge_approval_reply = original_handler

        self.assertIsNotNone(result)
        self.assertTrue(result.handled)
        self.assertIn("kwargs", called)
        self.assertEqual(called["kwargs"]["session_id"], "s")
        self.assertEqual(len(sent), 1)
        # merge_disabled 면 RESPONSE_PR_MERGE_DISABLED 가 ack
        from yule_engineering.discord.approval.reply_router import (
            RESPONSE_PR_MERGE_DISABLED,
        )

        self.assertEqual(sent[0], RESPONSE_PR_MERGE_DISABLED)


# ---------------------------------------------------------------------------
# 6. merge success stamps pr_merged
# ---------------------------------------------------------------------------


class MergeSuccessStampsTests(unittest.TestCase):
    def test_autonomous_merge_success_advances_to_pr_merged(self) -> None:
        extra: dict = {
            EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS,
            EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
            EXTRA_PR_MERGE_PR_NUMBER: 9,
            EXTRA_PR_MERGE_REPO: "yule-studio/naver-search-clone",
            "pr_merge_pr_url": "https://github.com/yule-studio/naver-search-clone/pull/9",
            "pr_merge_head_sha": "sha9",
            "pr_merge_base_branch": "main",
        }

        def fake_executor(dispatch: PRMergeReplyDispatch) -> Mapping[str, Any]:
            return {"merge_sha": "MERGEsha9", "method": "squash"}

        persisted: List[Mapping[str, Any]] = []

        def persist(new_extra: Mapping[str, Any]) -> None:
            persisted.append(dict(new_extra))
            extra.clear()
            extra.update(new_extra)

        loop = asyncio.new_event_loop()
        try:
            outcome = loop.run_until_complete(
                advance_pending_session(
                    session_id="s-9",
                    session_extra=extra,
                    persist_extra=persist,
                    merge_executor=fake_executor,
                )
            )
        finally:
            loop.close()

        self.assertEqual(outcome.action, ACTION_AUTONOMOUS_MERGE_SUCCEEDED)
        self.assertEqual(outcome.merge_sha, "MERGEsha9")
        self.assertEqual(extra[EXTRA_PR_MERGE_STAGE], STAGE_PR_MERGED)
        audit = extra[EXTRA_PR_MERGE_AUDIT]
        self.assertTrue(
            any(e.get("stage") == STAGE_PR_MERGED for e in audit),
            audit,
        )


# ---------------------------------------------------------------------------
# 7. merge success triggers next coding slice automatically
# ---------------------------------------------------------------------------


class NextSliceTriggerTests(unittest.TestCase):
    def test_dispatch_next_coding_slice_after_merge(self) -> None:
        extra: dict = {
            EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGED,
            EXTRA_CODING_BACKLOG: [
                {"summary": "auth-frontend", "executor_role": "frontend-engineer"},
                {"summary": "search-ui", "executor_role": "frontend-engineer"},
            ],
        }
        persisted: List[Mapping[str, Any]] = []
        enqueued: List[Mapping[str, Any]] = []

        def persist(new_extra: Mapping[str, Any]) -> None:
            persisted.append(dict(new_extra))
            extra.clear()
            extra.update(new_extra)

        def enqueue(session_id: str, slice_spec: Mapping[str, Any]) -> None:
            enqueued.append((session_id, dict(slice_spec)))

        decision = dispatch_next_coding_slice(
            session_id="s-next",
            session_extra=extra,
            persist_extra=persist,
            enqueue_slice=enqueue,
        )
        self.assertEqual(decision.action, NextSliceAction.DISPATCH_SLICE)
        self.assertEqual(decision.remaining_backlog, 1)
        self.assertEqual(enqueued[0][0], "s-next")
        self.assertEqual(enqueued[0][1]["summary"], "auth-frontend")
        # 새 extra 에서 backlog 한 칸 줄어듦
        self.assertEqual(len(extra[EXTRA_CODING_BACKLOG]), 1)
        self.assertEqual(extra[EXTRA_CODING_BACKLOG][0]["summary"], "search-ui")

    def test_empty_backlog_marks_session_done(self) -> None:
        extra: dict = {
            EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGED,
            EXTRA_CODING_BACKLOG: [],
        }
        persisted: List[Mapping[str, Any]] = []
        done: List[str] = []

        def persist(new_extra: Mapping[str, Any]) -> None:
            persisted.append(dict(new_extra))
            extra.clear()
            extra.update(new_extra)

        decision = dispatch_next_coding_slice(
            session_id="s-done",
            session_extra=extra,
            persist_extra=persist,
            on_session_done=done.append,
        )
        self.assertEqual(decision.action, NextSliceAction.SESSION_DONE)
        self.assertEqual(done, ["s-done"])
        self.assertEqual(
            extra[EXTRA_SESSION_COMPLETED_REASON],
            "backlog_empty_after_merge",
        )


# ---------------------------------------------------------------------------
# 8. autonomous_merge mode starts auto-merge continuation path
# ---------------------------------------------------------------------------


class AutonomousMergeContinuationStartsTests(unittest.TestCase):
    def test_blocked_path_for_gate_fail(self) -> None:
        extra: dict = {
            EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS,
            EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
            EXTRA_PR_MERGE_PR_NUMBER: 5,
            EXTRA_PR_MERGE_REPO: "yule-studio/naver-search-clone",
            "pr_merge_pr_url": "https://github.com/yule-studio/naver-search-clone/pull/5",
            "pr_merge_head_sha": "sha5",
            "pr_merge_base_branch": "main",
        }

        def fake_executor(dispatch):
            return {
                "gate_failed_step": "checks_green",
                "gate_reason": "2 failing checks",
                "checks_summary": "failure, failure",
            }

        persisted: List[Mapping[str, Any]] = []

        def persist(new_extra: Mapping[str, Any]) -> None:
            persisted.append(dict(new_extra))
            extra.clear()
            extra.update(new_extra)

        loop = asyncio.new_event_loop()
        try:
            outcome = loop.run_until_complete(
                advance_pending_session(
                    session_id="s-block",
                    session_extra=extra,
                    persist_extra=persist,
                    merge_executor=fake_executor,
                )
            )
        finally:
            loop.close()

        self.assertEqual(outcome.action, ACTION_AUTONOMOUS_MERGE_BLOCKED)
        self.assertEqual(extra[EXTRA_PR_MERGE_STAGE], STAGE_PR_MERGE_BLOCKED)
        audit_entry = extra[EXTRA_PR_MERGE_AUDIT][0]
        self.assertEqual(audit_entry["gate_failed_step"], "checks_green")


# ---------------------------------------------------------------------------
# 9. executor remains single-writer (no parallel enqueue for same anchor)
# ---------------------------------------------------------------------------


class SingleWriterGuardTests(_WorkerFixture):
    def test_same_session_role_branch_dedups_to_one_job(self) -> None:
        """현재 ``CodingJob`` 은 single executor_role 모델이라 동일 anchor
        에 대해 dedup 가 작동해야 한다. P1-L-2 는 이 가정을 깨지 않는다.
        """

        worker = self._build_worker()
        first, created1 = worker.enqueue(_request("single-writer-session"))
        second, created2 = worker.enqueue(_request("single-writer-session"))
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(first.job_id, second.job_id)
        # 별도 role 은 별개 anchor — 허용
        from dataclasses import replace as _replace

        other = _replace(
            _request("single-writer-session"), executor_role="frontend-engineer"
        )
        third, created3 = worker.enqueue(other)
        self.assertTrue(created3)
        self.assertNotEqual(first.job_id, third.job_id)


# ---------------------------------------------------------------------------
# 10. FE/BE parallel planning metadata preserved
# ---------------------------------------------------------------------------


class FrontendBackendPlanningMetadataTests(unittest.TestCase):
    def test_role_take_metadata_persists_alongside_pr_merge_stage(self) -> None:
        """FE/BE 가 planning 단계에서 role take/review 를 남기면 이 정보가
        머지 continuation 진입 후에도 session.extra 에 그대로 살아 있어야
        operator 가 status panel 에서 모든 role 의 참여를 본다.
        """

        extra: dict = {
            "role_takes": {
                "frontend-engineer": {"plan": "auth UI 단순화"},
                "backend-engineer": {"plan": "POST /auth/login 엔드포인트"},
            },
            "role_reviews": [
                {"role": "frontend-engineer", "status": "ok"},
                {"role": "backend-engineer", "status": "ok"},
            ],
        }
        # decide_post_pr_action 이 stage 를 머지하면 role_takes / role_reviews
        # 는 절대 덮어쓰지 않아야 한다.
        from yule_engineering.agents.job_queue.pr_merge_continuation import (
            decide_post_pr_action,
        )

        extra_with_mode = dict(extra)
        extra_with_mode[EXTRA_WORK_MODE] = WORK_MODE_AUTONOMOUS
        decision = decide_post_pr_action(
            session_id="fe-be",
            session_extra=extra_with_mode,
            repo_full_name="yule-studio/naver-search-clone",
            pr_number=3,
            pr_url="https://github.com/yule-studio/naver-search-clone/pull/3",
            head_sha="sha3",
            base_branch="main",
        )
        merged = dict(extra_with_mode)
        for k, v in decision.extra_updates.items():
            merged[k] = v
        # planning 메타 보존
        self.assertIn("role_takes", merged)
        self.assertEqual(
            merged["role_takes"]["frontend-engineer"]["plan"],
            "auth UI 단순화",
        )
        self.assertEqual(
            merged["role_takes"]["backend-engineer"]["plan"],
            "POST /auth/login 엔드포인트",
        )
        self.assertEqual(len(merged["role_reviews"]), 2)
        # 그리고 PR merge stage 도 같이 stamp 됨
        self.assertEqual(merged[EXTRA_PR_MERGE_STAGE], STAGE_PR_MERGE_PENDING)


# ---------------------------------------------------------------------------
# Boilerplate
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    unittest.main()
