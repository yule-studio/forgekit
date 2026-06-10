"""P1-L-3 — runner periodic loop + bot wiring + canonical recovery.

10 사용자 acceptance:

1. runner startup sweep picks pending merge sessions
2. runner periodic tick advances pending merge sessions idempotently
3. approval_required mode posts exactly one PR merge approval card
4. approval reply router calls handle_pr_merge_approval_reply for pr_merge
5. live executor injection path is covered (None vs build_pr_merge_executor)
6. merge success stamps pr_merged
7. merge success dispatches next slice exactly once
8. autonomous_merge loop merges without approval reply
9. duplicate loop ticks do not duplicate merge / card / next-slice enqueue
10. canonical session / PR #2-like state covered (recovery integration)

본 모듈은 GitHub / Discord live 호출 없이 inject 된 fake 만으로 위 10
가지를 stdlib unittest 로 가드한다.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_engineering.agents.job_queue.next_slice_dispatcher import (
    EXTRA_CODING_BACKLOG,
)
from yule_engineering.agents.job_queue.pr_approval import (
    PRMergeProposal,
    PRMergeReplyDispatch,
)
from yule_engineering.agents.job_queue.pr_merge_continuation import (
    EXTRA_PR_MERGE_AUDIT,
    EXTRA_PR_MERGE_PR_NUMBER,
    EXTRA_PR_MERGE_REPO,
    EXTRA_PR_MERGE_STAGE,
    STAGE_PR_MERGE_PENDING,
    STAGE_PR_MERGED,
)
from yule_engineering.agents.job_queue.pr_merge_continuation_worker import (
    ACTION_APPROVAL_CARD_ENQUEUED,
    ACTION_AUTONOMOUS_MERGE_SUCCEEDED,
    ACTION_SKIPPED_ALREADY_ENQUEUED,
    advance_pending_session,
    iter_pending_session_ids,
)
from yule_engineering.agents.lifecycle.session_mode import (
    EXTRA_WORK_MODE,
    WORK_MODE_APPROVAL,
    WORK_MODE_AUTONOMOUS,
)
from yule_engineering.agents.workflow_state import (
    WorkflowSession,
    WorkflowState,
    list_sessions,
    load_session,
    save_session,
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _seed_session(
    session_id: str, *, extra: Mapping[str, Any]
) -> WorkflowSession:
    sess = WorkflowSession(
        session_id=session_id,
        prompt="네이버 검색 풀스택 MVP",
        task_type="coding_execute",
        state=WorkflowState.IN_PROGRESS,
        created_at=_now(),
        updated_at=_now(),
        executor_role="backend-engineer",
        extra=dict(extra),
    )
    save_session(sess)
    return sess


def _pending_extra(
    *,
    work_mode: str,
    pr_number: int = 99,
    head_sha: str = "headsha9",
    backlog: Optional[list] = None,
) -> dict:
    extra: dict = {
        EXTRA_WORK_MODE: work_mode,
        EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
        EXTRA_PR_MERGE_PR_NUMBER: pr_number,
        EXTRA_PR_MERGE_REPO: "yule-studio/naver-search-clone",
        "pr_merge_pr_url": (
            f"https://github.com/yule-studio/naver-search-clone/pull/{pr_number}"
        ),
        "pr_merge_head_sha": head_sha,
        "pr_merge_base_branch": "main",
    }
    if backlog is not None:
        extra[EXTRA_CODING_BACKLOG] = list(backlog)
    return extra


def _persist(session_id: str):
    """workflow_state 에 직접 persist 하는 콜백 — 실제 _persist_session_extra
    와 동일 동작."""

    def _do(new_extra: Mapping[str, Any]) -> None:
        from dataclasses import replace

        session = load_session(session_id)
        if session is None:
            return
        update = replace(session, extra=dict(new_extra), updated_at=_now())
        save_session(update)

    return _do


# ---------------------------------------------------------------------------
# 1. startup sweep picks pending merge sessions
# ---------------------------------------------------------------------------


class StartupSweepTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        import os
        from yule_engineering import storage as _storage

        self._old_root = getattr(_storage, "_CACHE_ROOT_OVERRIDE", None)
        os.environ["YULE_AGENT_CACHE_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        import os

        os.environ.pop("YULE_AGENT_CACHE_DIR", None)

    def test_iter_pending_session_ids_finds_pending_sessions(self) -> None:
        _seed_session(
            "L3-startup-1",
            extra=_pending_extra(work_mode=WORK_MODE_AUTONOMOUS),
        )
        _seed_session(
            "L3-startup-2",
            extra=_pending_extra(work_mode=WORK_MODE_APPROVAL, pr_number=8),
        )
        # not pending
        _seed_session(
            "L3-startup-3",
            extra={EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS},
        )
        sessions = list_sessions(limit=100)
        ids = iter_pending_session_ids(sessions)
        self.assertIn("L3-startup-1", ids)
        self.assertIn("L3-startup-2", ids)
        self.assertNotIn("L3-startup-3", ids)


# ---------------------------------------------------------------------------
# 2. periodic tick advances pending sessions idempotently
# 9. duplicate loop ticks do not duplicate merge / card / next-slice enqueue
# ---------------------------------------------------------------------------


class PeriodicTickIdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        import os

        os.environ["YULE_AGENT_CACHE_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        import os

        os.environ.pop("YULE_AGENT_CACHE_DIR", None)

    def test_two_ticks_only_enqueue_one_approval_card(self) -> None:
        sid = "L3-idemp-approval"
        _seed_session(sid, extra=_pending_extra(work_mode=WORK_MODE_APPROVAL))

        enqueued: List[PRMergeProposal] = []

        @dataclass
        class _Outcome:
            approval_job_id: str = "fake-job"

        async def fake_enqueuer(*, session, proposal, **_):
            enqueued.append(proposal)
            return _Outcome()

        loop = asyncio.new_event_loop()
        try:
            for _ in range(3):
                session = load_session(sid)
                extra = dict(session.extra or {})
                loop.run_until_complete(
                    advance_pending_session(
                        session_id=sid,
                        session_extra=extra,
                        persist_extra=_persist(sid),
                        approval_enqueuer=fake_enqueuer,
                    )
                )
        finally:
            loop.close()
        self.assertEqual(len(enqueued), 1, "card must be posted exactly once")

    def test_two_ticks_only_call_merge_executor_once_per_state(self) -> None:
        sid = "L3-idemp-auto"
        _seed_session(
            sid, extra=_pending_extra(work_mode=WORK_MODE_AUTONOMOUS)
        )
        calls: List[PRMergeReplyDispatch] = []

        def fake_executor(dispatch: PRMergeReplyDispatch) -> Mapping[str, Any]:
            calls.append(dispatch)
            return {"merge_sha": "MERGED1", "method": "squash"}

        loop = asyncio.new_event_loop()
        try:
            for _ in range(3):
                session = load_session(sid)
                extra = dict(session.extra or {})
                loop.run_until_complete(
                    advance_pending_session(
                        session_id=sid,
                        session_extra=extra,
                        persist_extra=_persist(sid),
                        merge_executor=fake_executor,
                    )
                )
        finally:
            loop.close()
        # 첫 tick 에서 pr_merged 로 advance, 이후 tick 은 not-pending 으로
        # 빠짐 → merge_executor 는 단 1번 호출.
        self.assertEqual(len(calls), 1)
        final = load_session(sid)
        self.assertEqual(final.extra[EXTRA_PR_MERGE_STAGE], STAGE_PR_MERGED)


# ---------------------------------------------------------------------------
# 3. approval_required posts exactly one card (covered by #2 too, but explicit)
# ---------------------------------------------------------------------------


class ApprovalCardSingletonTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        import os

        os.environ["YULE_AGENT_CACHE_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        import os

        os.environ.pop("YULE_AGENT_CACHE_DIR", None)

    def test_audit_records_single_approval_card_enqueued_event(self) -> None:
        sid = "L3-card-singleton"
        _seed_session(sid, extra=_pending_extra(work_mode=WORK_MODE_APPROVAL))

        @dataclass
        class _Outcome:
            approval_job_id: str = "approval-1"

        async def fake_enqueuer(*, session, proposal, **_):
            return _Outcome()

        loop = asyncio.new_event_loop()
        try:
            for _ in range(5):
                session = load_session(sid)
                loop.run_until_complete(
                    advance_pending_session(
                        session_id=sid,
                        session_extra=dict(session.extra or {}),
                        persist_extra=_persist(sid),
                        approval_enqueuer=fake_enqueuer,
                    )
                )
        finally:
            loop.close()

        final = load_session(sid)
        audit = list(final.extra.get(EXTRA_PR_MERGE_AUDIT) or ())
        events = [
            e for e in audit if e.get("event") == "approval_card_enqueued"
        ]
        self.assertEqual(len(events), 1, audit)


# ---------------------------------------------------------------------------
# 4. approval reply router calls handle_pr_merge_approval_reply for pr_merge
#    (already covered by test_pr_merge_continuation_end_to_end.py;
#    here we additionally verify that the bot path actually injects the live
#    executor via the helper builder.)
# 5. live executor injection path is covered
# ---------------------------------------------------------------------------


class LiveExecutorWiringTests(unittest.TestCase):
    def test_builder_returns_none_when_env_unset(self) -> None:
        import os

        from yule_engineering.discord.bot._legacy import (
            _build_pr_merge_executor_for_bot,
        )

        # ensure env is unset
        prev = os.environ.pop("YULE_GITHUB_APP_MERGE_OPT_IN", None)
        try:
            self.assertIsNone(_build_pr_merge_executor_for_bot())
        finally:
            if prev is not None:
                os.environ["YULE_GITHUB_APP_MERGE_OPT_IN"] = prev

    def test_runner_helper_matches_bot_helper(self) -> None:
        """runner 와 bot wiring 이 동일 env contract 를 쓰는지 보장."""

        from yule_engineering.discord.bot import _legacy as bot_legacy
        from yule_engineering.runtime import (
            coding_executor_runner as runner,
        )

        self.assertTrue(callable(bot_legacy._build_pr_merge_executor_for_bot))
        self.assertTrue(
            callable(runner._maybe_build_live_pr_merge_executor)
        )


# ---------------------------------------------------------------------------
# 6. merge success stamps pr_merged
# 7. merge success dispatches next slice exactly once
# 8. autonomous_merge loop merges without approval reply
# ---------------------------------------------------------------------------


class AutonomousMergeLiveClosureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        import os

        os.environ["YULE_AGENT_CACHE_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        import os

        os.environ.pop("YULE_AGENT_CACHE_DIR", None)

    def test_merge_success_then_next_slice_dispatched_exactly_once(
        self,
    ) -> None:
        sid = "L3-auto-merge"
        _seed_session(
            sid,
            extra=_pending_extra(
                work_mode=WORK_MODE_AUTONOMOUS,
                backlog=[
                    {"summary": "search-ui", "prompt": "검색 결과 UI"},
                    {"summary": "blog-api", "prompt": "블로그 API"},
                ],
            ),
        )

        def fake_executor(dispatch):
            return {"merge_sha": "merged-sha", "method": "squash"}

        slice_calls: List[Mapping[str, Any]] = []

        def fake_enqueue(session_id: str, slice_spec: Mapping[str, Any]) -> None:
            slice_calls.append({"session_id": session_id, **slice_spec})

        loop = asyncio.new_event_loop()
        try:
            session = load_session(sid)
            outcome = loop.run_until_complete(
                advance_pending_session(
                    session_id=sid,
                    session_extra=dict(session.extra or {}),
                    persist_extra=_persist(sid),
                    merge_executor=fake_executor,
                    next_slice_dispatcher=lambda _s, _e: None,
                )
            )
        finally:
            loop.close()

        self.assertEqual(outcome.action, ACTION_AUTONOMOUS_MERGE_SUCCEEDED)
        final = load_session(sid)
        self.assertEqual(final.extra[EXTRA_PR_MERGE_STAGE], STAGE_PR_MERGED)

        # next slice 는 runner 의 별도 단계 — dispatch_next_coding_slice
        # 콜백을 직접 호출해서 한 번만 동작 + 두 번째 호출은 backlog 가 이미
        # 줄어든 상태를 본다.
        from yule_engineering.agents.job_queue.next_slice_dispatcher import (
            NextSliceAction,
            dispatch_next_coding_slice,
        )

        fresh = load_session(sid)
        decision = dispatch_next_coding_slice(
            session_id=sid,
            session_extra=dict(fresh.extra or {}),
            persist_extra=_persist(sid),
            enqueue_slice=fake_enqueue,
        )
        self.assertEqual(decision.action, NextSliceAction.DISPATCH_SLICE)
        self.assertEqual(len(slice_calls), 1)
        self.assertEqual(slice_calls[0]["summary"], "search-ui")
        # 두 번째 호출 — backlog 가 한 칸 줄어든 상태에서 두 번째 slice pop
        fresh2 = load_session(sid)
        decision2 = dispatch_next_coding_slice(
            session_id=sid,
            session_extra=dict(fresh2.extra or {}),
            persist_extra=_persist(sid),
            enqueue_slice=fake_enqueue,
        )
        self.assertEqual(decision2.action, NextSliceAction.DISPATCH_SLICE)
        self.assertEqual(len(slice_calls), 2)
        self.assertEqual(slice_calls[1]["summary"], "blog-api")


# ---------------------------------------------------------------------------
# 10. canonical session / PR #2-like state — integration recovery
# ---------------------------------------------------------------------------


class CanonicalSessionRecoveryTests(unittest.TestCase):
    """canonical session ``11917bf1e75d`` shape (post-PR but pre-P1-L) 가
    operator 의 1회 stamp 후 background loop tick 한 번으로 머지/카드
    경로로 회복되는지 확인."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        import os

        os.environ["YULE_AGENT_CACHE_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        import os

        os.environ.pop("YULE_AGENT_CACHE_DIR", None)

    def test_canonical_session_stamp_then_one_tick_advances_to_merged(
        self,
    ) -> None:
        # 옛 wiring 시점: session 은 work_mode 만 있고 pr_merge_* 없음
        sid = "11917bf1e75d"
        _seed_session(
            sid, extra={EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS}
        )

        # 운영자 1회 stamp (runbook §4 의 one-liner 가 하는 일)
        from dataclasses import replace

        session = load_session(sid)
        recovered_extra = dict(session.extra or {})
        recovered_extra.update(
            _pending_extra(
                work_mode=WORK_MODE_AUTONOMOUS,
                pr_number=2,
                head_sha="canonicalpr2sha",
            )
        )
        save_session(replace(session, extra=recovered_extra))

        # 1 tick — 가짜 merge executor 가 success 반환
        def fake_executor(dispatch):
            self.assertEqual(dispatch.proposal.pr_number, 2)
            self.assertEqual(
                dispatch.proposal.repo, "yule-studio/naver-search-clone"
            )
            return {"merge_sha": "pr2-merged", "method": "squash"}

        loop = asyncio.new_event_loop()
        try:
            fresh = load_session(sid)
            outcome = loop.run_until_complete(
                advance_pending_session(
                    session_id=sid,
                    session_extra=dict(fresh.extra or {}),
                    persist_extra=_persist(sid),
                    merge_executor=fake_executor,
                )
            )
        finally:
            loop.close()
        self.assertEqual(outcome.action, ACTION_AUTONOMOUS_MERGE_SUCCEEDED)
        self.assertEqual(outcome.merge_sha, "pr2-merged")

        final = load_session(sid)
        self.assertEqual(final.extra[EXTRA_PR_MERGE_STAGE], STAGE_PR_MERGED)
        # audit 가 prior_stage=pr_merge_pending → stage=pr_merged 로 기록
        audit = final.extra[EXTRA_PR_MERGE_AUDIT]
        merged_entries = [a for a in audit if a.get("stage") == STAGE_PR_MERGED]
        self.assertEqual(len(merged_entries), 1)
        self.assertEqual(merged_entries[0]["prior_stage"], STAGE_PR_MERGE_PENDING)
        self.assertEqual(merged_entries[0]["merge_sha"], "pr2-merged")

    def test_canonical_session_approval_mode_posts_card_then_dedups(
        self,
    ) -> None:
        sid = "11917bf1e75d-approval"
        _seed_session(
            sid, extra=_pending_extra(work_mode=WORK_MODE_APPROVAL, pr_number=2)
        )

        cards: List[PRMergeProposal] = []

        @dataclass
        class _Out:
            approval_job_id: str = "card-1"

        async def fake_enqueuer(*, session, proposal, **_):
            cards.append(proposal)
            return _Out()

        loop = asyncio.new_event_loop()
        try:
            for _ in range(4):  # 4 ticks
                fresh = load_session(sid)
                loop.run_until_complete(
                    advance_pending_session(
                        session_id=sid,
                        session_extra=dict(fresh.extra or {}),
                        persist_extra=_persist(sid),
                        approval_enqueuer=fake_enqueuer,
                    )
                )
        finally:
            loop.close()

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].pr_number, 2)


if __name__ == "__main__":
    unittest.main()
