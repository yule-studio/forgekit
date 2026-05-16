"""Live smoke fix — coding_authorization_gate 가 approval card 자동 enqueue.

Reproduces session ``c5278a9043f2`` 의 두 갭:

1. 사용자 메시지 ``approval_required, full_stack_single_repo`` 가 어떤
   coding_proposal phrase 와도 매칭 안 돼 gate 가 발동 안 했었음.
2. gate 가 발동해 본문 채널 proposal preview 까지는 띄웠지만 `#승인-대기`
   에 카드 게시는 누락. → P0-T smoke fix: approval_worker 가 inject 되면
   `enqueue_github_work_approval` 자동 호출.

이 test 가 통과 = 두 갭이 다시 silently 닫혀도 가장 먼저 잡힌다.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional, Tuple

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.approval_worker import (
    APPROVAL_KIND_ENGINEERING_WRITE,
    ApprovalRequest,
    ApprovalWorker,
)
from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.store import JobQueue
from yule_orchestrator.discord.engineering.phrase_detect import (
    CODING_PROPOSAL_REQUEST_PHRASES,
    is_coding_proposal_request,
)
from yule_orchestrator.discord.engineering_channel_router.coding_gate import (
    _run_coding_authorization_gate,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _msg(channel_id: int = 100, content: str = "", message_id: int = 999):
    channel = SimpleNamespace(id=channel_id, name="업무-접수")
    author = SimpleNamespace(id=42, bot=False, name="masterway", global_name="masterway")
    return SimpleNamespace(channel=channel, author=author, content=content, id=message_id)


# ---------------------------------------------------------------------------
# Phrase detection regression
# ---------------------------------------------------------------------------


class PhraseDetectionTests(unittest.TestCase):
    def test_session_c5278a9043f2_repro_phrase_recognised(self) -> None:
        prompt = (
            "approval_required, single_repo, full_stack_single_repo로 진행해줘.\n"
            "repo: https://github.com/yule-studio/naver-search-clone.git\n"
            "목표: Next.js + NestJS + PostgreSQL + Docker Compose 기반 회원가입/검색"
        )
        self.assertTrue(
            is_coding_proposal_request(prompt),
            "approval_required / full_stack_single_repo 토큰이 coding "
            "proposal request 로 인식되지 않으면 gate 가 발동 못 함",
        )

    def test_legacy_korean_phrases_still_work(self) -> None:
        for phrase in (
            "이 작업 코딩 권한 제안해줘",
            "수정 권한 제안",
            "구현 권한 제안",
        ):
            with self.subTest(phrase=phrase):
                self.assertTrue(is_coding_proposal_request(phrase))

    def test_unrelated_phrases_not_misclassified(self) -> None:
        for phrase in (
            "오늘 날씨 어때",
            "이 PR 좀 봐줘",
            "조사해줘",
        ):
            with self.subTest(phrase=phrase):
                self.assertFalse(is_coding_proposal_request(phrase))

    def test_new_p0t_tokens_in_phrases_table(self) -> None:
        # 정책 상수가 silently 약해지지 않게 핀
        for token in (
            "approval_required",
            "full_stack_single_repo",
            "single_repo",
        ):
            self.assertIn(token, CODING_PROPOSAL_REQUEST_PHRASES)


# ---------------------------------------------------------------------------
# Approval card auto-enqueue wiring
# ---------------------------------------------------------------------------


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db = Path(self._tmp.name) / "q.sqlite3"
        self.queue = JobQueue(db_path=db)
        self.heartbeats = HeartbeatStore(db_path=db)
        self.posted_cards: List[Tuple[ApprovalRequest, str]] = []

        async def _post(req, rendered):
            self.posted_cards.append((req, rendered))
            return {"posted_message_id": 1}

        self.approval_worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=_post,
            channel_resolver=lambda: 4242,
        )

        # Discord channel send_chunks capture
        self.sent: List[str] = []

        async def _send(_channel, text: str, *args, **kwargs):
            self.sent.append(text)

        self.send_chunks = _send

        # Open session that the gate will pick up
        self.session = SimpleNamespace(
            session_id="sess-c5278",
            prompt=(
                "approval_required, single_repo, full_stack_single_repo로 진행해줘. "
                "repo: https://github.com/yule-studio/naver-search-clone.git "
                "목표: Next.js + NestJS + PostgreSQL + Docker Compose 기반 회원가입/검색"
            ),
            task_type="full-stack-app",
            state="intake",
            channel_id=100,
            thread_id=None,
            user_id=42,
            extra={
                "lifecycle_mode": "implementation",
                "active_research_roles": ["tech-lead", "backend-engineer"],
            },
            updated_at="2026-05-16T10:00:00",
        )

        def _list_sessions(limit=50):
            return [self.session]

        self.list_sessions = _list_sessions


class CodingGateApprovalEnqueueTests(_Fixture):
    def test_p0t_message_triggers_gate_and_enqueues_approval_card(self) -> None:
        msg = _msg(
            content=(
                "approval_required, single_repo, full_stack_single_repo로 진행해줘"
            )
        )
        result = _run(
            _run_coding_authorization_gate(
                message=msg,
                prompt_text=msg.content,
                list_sessions_fn=self.list_sessions,
                send_chunks=self.send_chunks,
                approval_worker=self.approval_worker,
            )
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.handled)
        # 본문에 권한 제안 preview 게시
        self.assertTrue(
            any("코딩 권한 제안" in s or "engineering-agent" in s for s in self.sent),
            f"본문에 proposal preview 가 보이지 않음: {self.sent}",
        )
        # `#승인-대기` 카드가 실제로 게시됨
        self.assertEqual(
            len(self.posted_cards),
            1,
            f"#승인-대기 카드 게시 누락 — posted={self.posted_cards}",
        )
        approval_request, _ = self.posted_cards[0]
        self.assertEqual(
            approval_request.approval_kind,
            APPROVAL_KIND_ENGINEERING_WRITE,
        )
        # 본문에 `#승인-대기 카드 게시 완료` ack 도 게시
        self.assertTrue(
            any("`#승인-대기` 카드 게시 완료" in s for s in self.sent),
            f"카드 게시 ack 누락: {self.sent}",
        )

    def test_no_approval_worker_keeps_legacy_behavior(self) -> None:
        # caller 가 worker 를 inject 하지 않으면 (회귀 보호) 기존 동작 유지
        msg = _msg(
            content="이 작업 코딩 권한 제안"
        )
        result = _run(
            _run_coding_authorization_gate(
                message=msg,
                prompt_text=msg.content,
                list_sessions_fn=self.list_sessions,
                send_chunks=self.send_chunks,
                approval_worker=None,
            )
        )
        self.assertIsNotNone(result)
        # 본문 proposal preview 만 게시, 카드 0건
        self.assertEqual(self.posted_cards, [])
        self.assertTrue(any("engineering-agent" in s for s in self.sent))

    def test_legacy_korean_phrase_with_worker_also_enqueues(self) -> None:
        # 기존 한국어 phrase 도 worker inject 시 카드 enqueue
        msg = _msg(content="이 작업 코딩 권한 제안")
        _run(
            _run_coding_authorization_gate(
                message=msg,
                prompt_text=msg.content,
                list_sessions_fn=self.list_sessions,
                send_chunks=self.send_chunks,
                approval_worker=self.approval_worker,
            )
        )
        self.assertEqual(len(self.posted_cards), 1)


if __name__ == "__main__":
    unittest.main()
