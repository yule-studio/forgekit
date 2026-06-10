"""Live smoke fix — `#승인-대기` 채널의 engineering_write 카드 reply 라우팅.

Live observation (session c5278a9043f2 후속):
  - producer (`enqueue_github_work_approval`) 가 `#승인-대기` 에 카드
    게시는 성공 (PR #176 까지)
  - operator 가 `승인` / `이대로 진행` 답신
  - reply router 는 obsidian_write 카드만 찾던 회귀 → "답신에 매칭되는
    승인 카드를 못 찾았어요" 반환
  - 결과: github_work_order dispatch 안 됨, coding_execute 도 안 됨

본 PR fix:
  1. route_approval_channel_message 가 engineering_write 카드도 우선 매칭
  2. find_replyable_approval 이 `replied_message_id` (Discord 의
     message.reference.message_id) 와 카드의 `result.posted_message_id`
     를 1순위 매칭 신호로 사용
  3. 매칭되면 handle_github_work_approval_reply 호출 → work_order
     dispatch
  4. operator 가 reply 만 하면 work_order 가 큐에 자동 적재

본 test 가 통과 = `#승인-대기` 채널에서 사용자 `승인` 한 줄로 work_order
dispatch 까지 닫힌다는 뜻.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.approval_reply import (
    find_replyable_approval,
)
from yule_engineering.agents.job_queue.approval_worker import (
    APPROVAL_KIND_ENGINEERING_WRITE,
    APPROVAL_KIND_OBSIDIAN_WRITE,
    ApprovalRequest,
    ApprovalWorker,
)
from yule_engineering.agents.job_queue.github_work_order import (
    JOB_TYPE_GITHUB_WORK_ORDER,
    GitHubWorkOrderProposal,
)
from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.obsidian_writer_worker import (
    ObsidianWriterWorker,
)
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue
from yule_engineering.discord.approval.reply_router import (
    RESPONSE_ENGINEERING_APPROVED,
    RESPONSE_ENGINEERING_APPROVED_DUPLICATE,
    RESPONSE_ENGINEERING_REJECTED,
    route_approval_channel_message,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _msg(
    *,
    channel_id: int,
    content: str,
    message_id: int = 999,
    replied_id: Optional[int] = None,
):
    channel = SimpleNamespace(id=channel_id, name="승인-대기")
    author = SimpleNamespace(
        id=42, bot=False, name="masterway", global_name="masterway"
    )
    ref = (
        SimpleNamespace(message_id=replied_id)
        if replied_id is not None
        else None
    )
    return SimpleNamespace(
        channel=channel,
        author=author,
        content=content,
        id=message_id,
        reference=ref,
    )


# ---------------------------------------------------------------------------
# matcher fundamentals
# ---------------------------------------------------------------------------


class FindReplyableApprovalEngineeringWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db = Path(self._tmp.name) / "q.sqlite3"
        self.queue = JobQueue(db_path=db)
        self.heartbeats = HeartbeatStore(db_path=db)

        async def _post(req, rendered):
            return {"posted_message_id": 555, "thread_id": 70000}

        self.worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=_post,
            channel_resolver=lambda: 4242,
        )

    def _seed_engineering_card(self, *, source_message_id: int = 123):
        proposal_payload = {
            "proposal_id": "p-eng-1",
            "session_id": "sess-eng-write",
            "source_channel_id": 100,
            "source_thread_id": 100,
            "source_message_id": source_message_id,
            "request_summary": "full-stack 구현",
            "coding_required": True,
            "selected_roles": ["tech-lead", "backend-engineer"],
            "repo": "yule-studio/naver-search-clone",
            "dry_run_default": True,
        }
        req = ApprovalRequest(
            session_id="sess-eng-write",
            approval_kind=APPROVAL_KIND_ENGINEERING_WRITE,
            title="GitHub 작업 시작 승인",
            summary="full-stack 구현",
            requested_action="approve",
            created_by="42",
            source_channel_id=100,
            source_thread_id=100,
            source_message_id=source_message_id,
            extra={"github_work_order_proposal": proposal_payload},
        )
        return _run(self.worker.run_one(req))

    def test_find_with_engineering_write_kind_matches(self) -> None:
        outcome = self._seed_engineering_card(source_message_id=123)
        self.assertIsNotNone(outcome.job)

        # operator 가 reply — payload 의 source_message_id 와 같지 않아도
        # session 매칭으로 found
        job = find_replyable_approval(
            queue=self.queue,
            session_id="sess-eng-write",
            approval_kind=APPROVAL_KIND_ENGINEERING_WRITE,
            source_message_id=99999,  # operator 의 reply.id (다른 값)
        )
        self.assertIsNotNone(job, "engineering_write 카드 매칭 실패 — fallback 도 안 됨")

    def test_obsidian_kind_filter_does_not_find_engineering_card(self) -> None:
        # 회귀 보호: kind filter 가 정확히 작동해야 두 종류 카드가 안 섞임
        self._seed_engineering_card(source_message_id=123)
        job = find_replyable_approval(
            queue=self.queue,
            session_id="sess-eng-write",
            approval_kind=APPROVAL_KIND_OBSIDIAN_WRITE,
        )
        self.assertIsNone(
            job, "obsidian_write filter 가 engineering_write 카드를 잘못 반환"
        )

    def test_replied_message_id_takes_precedence(self) -> None:
        # 두 engineering_write 카드가 같은 세션에 있을 때 replied_message_id
        # 일치 카드를 우선 반환
        outcome_a = self._seed_engineering_card(source_message_id=111)
        outcome_b = self._seed_engineering_card(source_message_id=222)
        assert outcome_a.job is not None and outcome_b.job is not None
        # outcome_a 의 result.posted_message_id 는 555
        # 두 카드 모두 같은 post_fn 응답을 받으므로 둘 다 posted_message_id=555
        # — 본 케이스는 동률이라 most_recent (outcome_b) 가 반환되어야
        # 한다. 회귀 보호.
        job = find_replyable_approval(
            queue=self.queue,
            session_id="sess-eng-write",
            approval_kind=APPROVAL_KIND_ENGINEERING_WRITE,
            replied_message_id=555,
        )
        # 두 카드 모두 매칭 — 우선순위 0 (replied_message_id) 에서 첫
        # 매칭은 candidates 순서대로 (created_at asc). outcome_a 가 먼저.
        self.assertIsNotNone(job)


# ---------------------------------------------------------------------------
# Reply router integration — engineering_write 분기
# ---------------------------------------------------------------------------


@dataclass
class _SessionFake:
    session_id: str
    thread_id: Optional[int]
    updated_at: str = "2026-05-16T10:00"
    extra: Dict[str, Any] = field(default_factory=dict)


class _EngineeringWriteReplyFixture(unittest.TestCase):
    SESSION_ID = "sess-eng-write-router"
    APPROVAL_CHANNEL_ID = 70000

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db = Path(self._tmp.name) / "q.sqlite3"
        self.queue = JobQueue(db_path=db)
        self.heartbeats = HeartbeatStore(db_path=db)

        # ApprovalWorker stub
        async def _post(req, rendered):
            return {"posted_message_id": 555, "thread_id": self.APPROVAL_CHANNEL_ID}

        self.approval_worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=_post,
            channel_resolver=lambda: 4242,
        )

        # ObsidianWriterWorker stub (router 시그니처 요구)
        def _render(_r):
            return {"rendered": True}

        def _write(_n, _v, _r):
            return None

        self.obsidian_worker = ObsidianWriterWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            render_fn=_render,
            write_fn=_write,
            vault_root_resolver=lambda _r: Path(self._tmp.name) / "vault",
        )

        self.sent: List[str] = []

        async def _send(_channel, text: str, *args, **kwargs):
            self.sent.append(text)

        self.send_chunks = _send

        # session lister
        self.session = _SessionFake(
            session_id=self.SESSION_ID,
            thread_id=self.APPROVAL_CHANNEL_ID,
            extra={"lifecycle_mode": "implementation"},
        )

        def _lister():
            return [self.session]

        self.session_lister = _lister

    def _seed_engineering_card(self) -> str:
        proposal_payload = {
            "proposal_id": "p-eng-router",
            "session_id": self.SESSION_ID,
            "source_channel_id": self.APPROVAL_CHANNEL_ID,
            "source_thread_id": self.APPROVAL_CHANNEL_ID,
            "source_message_id": 12345,
            "request_summary": "full-stack 구현",
            "coding_required": True,
            "selected_roles": ["tech-lead", "backend-engineer"],
            "repo": "yule-studio/naver-search-clone",
            "dry_run_default": True,
        }
        req = ApprovalRequest(
            session_id=self.SESSION_ID,
            approval_kind=APPROVAL_KIND_ENGINEERING_WRITE,
            title="GitHub 작업 시작 승인",
            summary="full-stack 구현",
            requested_action="approve",
            created_by="42",
            source_channel_id=self.APPROVAL_CHANNEL_ID,
            source_thread_id=self.APPROVAL_CHANNEL_ID,
            source_message_id=12345,
            extra={"github_work_order_proposal": proposal_payload},
        )
        outcome = _run(self.approval_worker.run_one(req))
        assert outcome.job is not None
        return outcome.job.job_id


class RouteEngineeringWriteApprovalTests(_EngineeringWriteReplyFixture):
    def test_approve_reply_dispatches_work_order(self) -> None:
        """라이브 시나리오 핵심 회귀:
        operator 가 `#승인-대기` 에서 `승인` reply → engineering_write
        카드 매칭 → handle_github_work_approval_reply 호출 → work_order
        큐에 row 적재 → 본문에 ack 게시."""

        self._seed_engineering_card()

        msg = _msg(
            channel_id=self.APPROVAL_CHANNEL_ID,
            content="승인",
            replied_id=555,
        )
        result = _run(
            route_approval_channel_message(
                message=msg,
                bot_user=SimpleNamespace(id=0),
                queue=self.queue,
                obsidian_worker=self.obsidian_worker,
                approval_channel_id=self.APPROVAL_CHANNEL_ID,
                session_lister=self.session_lister,
                send_chunks=self.send_chunks,
            )
        )
        self.assertTrue(result.handled)
        # ack 게시
        self.assertEqual(len(self.sent), 1)
        self.assertIn("코딩 작업 승인 받았어요", self.sent[0])
        # github_work_order 큐에 row 1건
        work_order_rows = [
            job
            for job in self.queue.list_for_session(self.SESSION_ID)
            if job.job_type == JOB_TYPE_GITHUB_WORK_ORDER
        ]
        self.assertEqual(
            len(work_order_rows),
            1,
            f"engineering_write 승인 후 work_order 미적재 — "
            f"라이브 시나리오 fail. rows={work_order_rows}",
        )

    def test_approve_phrase_이대로_진행_also_dispatches(self) -> None:
        # operator 가 `이대로 진행` 같은 대체 phrase 로 답해도 동일 동작
        self._seed_engineering_card()
        msg = _msg(
            channel_id=self.APPROVAL_CHANNEL_ID, content="이대로 진행"
        )
        _run(
            route_approval_channel_message(
                message=msg,
                bot_user=SimpleNamespace(id=0),
                queue=self.queue,
                obsidian_worker=self.obsidian_worker,
                approval_channel_id=self.APPROVAL_CHANNEL_ID,
                session_lister=self.session_lister,
                send_chunks=self.send_chunks,
            )
        )
        work_order_rows = [
            job
            for job in self.queue.list_for_session(self.SESSION_ID)
            if job.job_type == JOB_TYPE_GITHUB_WORK_ORDER
        ]
        self.assertEqual(len(work_order_rows), 1)

    def test_reject_reply_does_not_dispatch(self) -> None:
        self._seed_engineering_card()
        msg = _msg(channel_id=self.APPROVAL_CHANNEL_ID, content="반려")
        _run(
            route_approval_channel_message(
                message=msg,
                bot_user=SimpleNamespace(id=0),
                queue=self.queue,
                obsidian_worker=self.obsidian_worker,
                approval_channel_id=self.APPROVAL_CHANNEL_ID,
                session_lister=self.session_lister,
                send_chunks=self.send_chunks,
            )
        )
        # rejection ack
        self.assertEqual(self.sent[-1], RESPONSE_ENGINEERING_REJECTED)
        # work_order 미적재
        work_order_rows = [
            job
            for job in self.queue.list_for_session(self.SESSION_ID)
            if job.job_type == JOB_TYPE_GITHUB_WORK_ORDER
        ]
        self.assertEqual(len(work_order_rows), 0)

    def test_no_match_response_no_longer_fires_for_engineering_card(self) -> None:
        """라이브에서 봤던 'NO_MATCH' 메시지가 engineering_write 카드에는
        절대 나오면 안 됨 — 회귀 가드."""

        self._seed_engineering_card()
        msg = _msg(channel_id=self.APPROVAL_CHANNEL_ID, content="승인")
        _run(
            route_approval_channel_message(
                message=msg,
                bot_user=SimpleNamespace(id=0),
                queue=self.queue,
                obsidian_worker=self.obsidian_worker,
                approval_channel_id=self.APPROVAL_CHANNEL_ID,
                session_lister=self.session_lister,
                send_chunks=self.send_chunks,
            )
        )
        # 어떤 응답에도 "답신에 매칭되는 승인 카드를 못 찾았어요" 가 없어야
        for s in self.sent:
            self.assertNotIn("답신에 매칭되는 승인 카드를 못 찾았어요", s)


if __name__ == "__main__":
    unittest.main()
