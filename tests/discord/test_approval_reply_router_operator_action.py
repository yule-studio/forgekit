"""Operator-action reply 라우팅 회귀 테스트 — P0-S.

`#승인-대기` 채널에 operator-action (INFO/ACCESS/SECRET/DECISION) 카드가
SAVED 로 들어가 있을 때, 사용자가 thread 에서 ``key=value`` 응답을
보내면:

  1. router 가 operator-action 카드를 우선 매칭한다 (기존 approval
     vocabulary 보다 먼저).
  2. 응답이 완전하면 친절한 ack (`RESPONSE_OPERATOR_INFO_OK` 등) 게시.
  3. SECRET 카드에 raw 값을 붙이면 거부 응답.
  4. 응답이 부족하면 미완 응답.
  5. operator-action 카드가 없을 때는 기존 approval 흐름이 그대로
     동작 (회귀 방지).
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.approval_worker import (
    APPROVAL_KIND_OBSIDIAN_WRITE,
    ApprovalRequest,
    ApprovalWorker,
)
from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.obsidian_writer_worker import (
    ObsidianWriterWorker,
)
from yule_engineering.agents.job_queue.operator_action_reply import (
    handle_operator_action_reply,
)
from yule_engineering.agents.job_queue.store import JobQueue
from yule_engineering.agents.operator_action import (
    OperatorActionRequest,
    OperatorActionType,
    OperatorSessionState,
    operator_action_to_approval_payload,
    stamp_pending_request,
)
from yule_discord.approval.reply_router import (
    RESPONSE_APPROVED,
    RESPONSE_OPERATOR_ACCESS_OK,
    RESPONSE_OPERATOR_DECISION_OK,
    RESPONSE_OPERATOR_INFO_OK,
    RESPONSE_OPERATOR_MISSING_KEYS,
    RESPONSE_OPERATOR_SECRET_OK,
    RESPONSE_OPERATOR_SECRET_VALUE_REJECTED,
    route_approval_channel_message,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _msg(*, channel_id: int, content: str, message_id: int = 999, author_bot: bool = False):
    channel = SimpleNamespace(id=channel_id, name="승인-대기")
    author = SimpleNamespace(
        id=42,
        bot=author_bot,
        name="masterway",
        global_name="masterway",
    )
    return SimpleNamespace(
        channel=channel, author=author, content=content, id=message_id
    )


class _OperatorActionFixture(unittest.TestCase):
    SESSION_ID = "sess-op-1"
    APPROVAL_CHANNEL_ID = 70000

    def setUp(self) -> None:  # noqa: D401
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=self._db)
        self.heartbeats = HeartbeatStore(db_path=self._db)

        async def _post_fn(_request, _rendered):
            return {"posted_message_id": 1}

        self.approval_worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=_post_fn,
            channel_resolver=lambda: 8888,
        )

        # ObsidianWriterWorker stub — needed because route_* signature
        # demands one even when the operator-action branch fires.
        def _render_fn(_request):
            return {"rendered": True}

        def _write_fn(_note, _vault, _request):
            return None

        self.obsidian_worker = ObsidianWriterWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            render_fn=_render_fn,
            write_fn=_write_fn,
            vault_root_resolver=lambda _r: Path(self._tmp.name) / "vault",
        )

        self.sent: List[str] = []

        async def _send_chunks(_channel, text: str, *args, **kwargs):
            self.sent.append(text)

        self.send_chunks = _send_chunks

        # 카드의 source_thread_id 가 reply 가 들어온 채널 (= APPROVAL_CHANNEL_ID)
        # 이라고 가정 — discord 에서 thread reply 가 channel.id 로 들어오는
        # 케이스를 모사. session lister 도 같은 thread 로 매칭 가능하게 둔다.
        self.fake_session = SimpleNamespace(
            session_id=self.SESSION_ID,
            thread_id=self.APPROVAL_CHANNEL_ID,
            updated_at="2026-05-15T10:00",
            extra={},
        )
        self.session_lister = lambda: [self.fake_session]

    def _seed_operator_action_card(
        self, request: OperatorActionRequest
    ) -> str:
        payload = operator_action_to_approval_payload(
            request,
            created_by="backend-engineer",
            source_thread_id=self.APPROVAL_CHANNEL_ID,
            source_message_id=12345,
        )
        approval_request = ApprovalRequest.from_payload(payload)
        outcome = _run(self.approval_worker.run_one(approval_request))
        assert outcome.job is not None
        return outcome.job.job_id

    def _seed_obsidian_card(self) -> str:
        request = ApprovalRequest(
            session_id=self.SESSION_ID,
            approval_kind=APPROVAL_KIND_OBSIDIAN_WRITE,
            title="결정 노트",
            summary="x",
            requested_action="vault 저장",
            created_by="tech-lead",
            source_thread_id=self.APPROVAL_CHANNEL_ID,
            source_message_id=42,
            extra={"decision_id": "dec-1"},
        )
        outcome = _run(self.approval_worker.run_one(request))
        assert outcome.job is not None
        return outcome.job.job_id


class OperatorActionDispatchTests(_OperatorActionFixture):
    def test_info_card_reply_routes_to_operator_handler(self) -> None:
        self._seed_operator_action_card(
            OperatorActionRequest(
                request_type=OperatorActionType.INFO_REQUIRED,
                session_id=self.SESSION_ID,
                title="deploy 대상 서버 IP 필요",
                stage="backend-engineer 가 deploy 직전",
                why_blocked="서버 IP 추측 불가",
                expected_answer="host 또는 hostname",
                answer_examples=("host=10.0.0.5",),
                next_action="deploy 진행",
            )
        )

        msg = _msg(channel_id=self.APPROVAL_CHANNEL_ID, content="host=10.0.0.5")
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
        self.assertIsNotNone(result.operator_outcome)
        op = result.operator_outcome
        assert op is not None
        self.assertTrue(op.handled)
        self.assertEqual(op.request_type, OperatorActionType.INFO_REQUIRED)
        assert op.reply is not None
        self.assertTrue(op.reply.is_complete)
        self.assertEqual(op.reply.answers, {"host": "10.0.0.5"})
        self.assertEqual(self.sent, [RESPONSE_OPERATOR_INFO_OK])

    def test_secret_card_with_raw_value_is_rejected(self) -> None:
        self._seed_operator_action_card(
            OperatorActionRequest(
                request_type=OperatorActionType.SECRET_REQUIRED,
                session_id=self.SESSION_ID,
                title="JWT_SECRET 저장 위치 필요",
                stage="auth wiring",
                why_blocked="실제 JWT_SECRET 값을 만들 수 없습니다",
                expected_answer="github_secret 이름 또는 env_file 경로",
                answer_examples=("github_secret=JWT_SECRET",),
                next_action="auth 모듈 wiring",
            )
        )

        msg = _msg(
            channel_id=self.APPROVAL_CHANNEL_ID,
            content="value=super-secret-string",
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
        self.assertEqual(self.sent, [RESPONSE_OPERATOR_SECRET_VALUE_REJECTED])
        op = result.operator_outcome
        assert op is not None and op.reply is not None
        self.assertEqual(op.reply.rejected_reason, "secret_value_inline")
        self.assertFalse(op.reply.is_complete)

    def test_secret_card_with_github_secret_is_accepted(self) -> None:
        self._seed_operator_action_card(
            OperatorActionRequest(
                request_type=OperatorActionType.SECRET_REQUIRED,
                session_id=self.SESSION_ID,
                title="JWT_SECRET 저장 위치 필요",
                stage="auth wiring",
                why_blocked="실제 JWT_SECRET 값을 만들 수 없습니다",
                expected_answer="github_secret 이름 또는 env_file 경로",
                answer_examples=("github_secret=JWT_SECRET",),
                next_action="auth 모듈 wiring",
            )
        )

        msg = _msg(
            channel_id=self.APPROVAL_CHANNEL_ID,
            content="github_secret=JWT_SECRET",
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
        self.assertEqual(self.sent, [RESPONSE_OPERATOR_SECRET_OK])

    def test_access_card_reply(self) -> None:
        self._seed_operator_action_card(
            OperatorActionRequest(
                request_type=OperatorActionType.ACCESS_REQUIRED,
                session_id=self.SESSION_ID,
                title="prod SSH 접근 필요",
                stage="systemd 재시작 직전",
                why_blocked="prod SSH user / key 모릅니다",
                expected_answer="user / auth 방식",
                answer_examples=("user=deploy", "auth=ssh-key"),
                next_action="systemd 재시작",
            )
        )
        msg = _msg(
            channel_id=self.APPROVAL_CHANNEL_ID,
            content="user=deploy\nauth=ssh-key",
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
        self.assertEqual(self.sent, [RESPONSE_OPERATOR_ACCESS_OK])

    def test_decision_card_reply(self) -> None:
        self._seed_operator_action_card(
            OperatorActionRequest(
                request_type=OperatorActionType.DECISION_REQUIRED,
                session_id=self.SESSION_ID,
                title="환불 정책 확인 필요",
                stage="환불 핸들러 직전",
                why_blocked="제품 결정 사항입니다",
                expected_answer="환불 정책",
                answer_examples=("decision=14일 이내 전액 환불",),
                next_action="환불 핸들러 작성",
            )
        )
        msg = _msg(
            channel_id=self.APPROVAL_CHANNEL_ID,
            content="decision=14일 이내 전액 환불",
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
        self.assertEqual(self.sent, [RESPONSE_OPERATOR_DECISION_OK])

    def test_missing_keys_returns_friendly_hint(self) -> None:
        self._seed_operator_action_card(
            OperatorActionRequest(
                request_type=OperatorActionType.INFO_REQUIRED,
                session_id=self.SESSION_ID,
                title="deploy 대상 서버 IP 필요",
                stage="backend-engineer 가 deploy 직전",
                why_blocked="서버 IP 추측 불가",
                expected_answer="host 또는 hostname",
                answer_examples=("host=10.0.0.5",),
                next_action="deploy 진행",
            )
        )

        msg = _msg(
            channel_id=self.APPROVAL_CHANNEL_ID,
            content="음 잘 모르겠어",
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
        self.assertEqual(self.sent, [RESPONSE_OPERATOR_MISSING_KEYS])

    def test_existing_obsidian_approval_flow_unchanged(self) -> None:
        # 회귀 보장 — operator-action 카드가 없으면 기존 obsidian write
        # 흐름이 그대로 동작.
        self._seed_obsidian_card()
        msg = _msg(channel_id=self.APPROVAL_CHANNEL_ID, content="승인")
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
        # operator-action 카드가 없었으므로 outcome 은 기존 path
        self.assertIsNone(result.operator_outcome)
        self.assertEqual(len(self.sent), 1)
        # 새 path 응답이 절대 안 나와야 함
        self.assertNotIn(RESPONSE_OPERATOR_SECRET_OK, self.sent)
        self.assertNotIn(RESPONSE_OPERATOR_INFO_OK, self.sent)

    def test_silent_stop_regression_card_required(self) -> None:
        """카드가 SAVED 가 아니면 (= 게시 자체를 안 했으면) operator-action
        분기가 발동하지 않아야 한다 — 즉 "카드 없이 멈추면 안 된다" 의
        역방향 회귀: 카드가 없으면 라우터도 조용히 처리하지 않고 일반
        approval path 로 빠지는지.
        """

        # operator-action 카드를 enqueue 하지 않고 reply 만 들어옴.
        msg = _msg(channel_id=self.APPROVAL_CHANNEL_ID, content="host=10.0.0.5")
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
        # operator-action 분기는 절대 발동하지 않음
        self.assertIsNone(result.operator_outcome)


class HandleOperatorActionReplyDirectTests(unittest.TestCase):
    """``handle_operator_action_reply`` 직접 호출 — session.extra 갱신 라인 고정."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.queue = JobQueue(db_path=Path(self._tmp.name) / "q.sqlite3")
        self.heartbeats = HeartbeatStore(
            db_path=Path(self._tmp.name) / "q.sqlite3"
        )

        async def _post_fn(_request, _rendered):
            return {"posted_message_id": 1}

        self.approval_worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=_post_fn,
            channel_resolver=lambda: 8888,
        )

    def _seed(self, request: OperatorActionRequest) -> str:
        payload = operator_action_to_approval_payload(
            request, source_thread_id=11, source_message_id=22
        )
        ar = ApprovalRequest.from_payload(payload)
        outcome = _run(self.approval_worker.run_one(ar))
        assert outcome.job is not None
        return outcome.job.job_id

    def test_session_state_returns_to_running_after_complete_reply(self) -> None:
        request = OperatorActionRequest(
            request_type=OperatorActionType.INFO_REQUIRED,
            session_id="sess-x",
            title="server ip 필요",
            stage="deploy 직전",
            why_blocked="추측 금지",
            expected_answer="host",
            answer_examples=("host=1.1.1.1",),
            next_action="deploy",
        )
        self._seed(request)

        # 가짜 session: 이미 waiting_user_input 상태로 들어가 있다고 가정
        existing_extra = stamp_pending_request(session_extra={}, request=request)
        captured: dict[str, Any] = {}

        def _load(_session_id):
            return SimpleNamespace(extra=existing_extra)

        def _update(session, new_extra):
            captured["extra"] = dict(new_extra)
            return session

        outcome = handle_operator_action_reply(
            queue=self.queue,
            text="host=1.1.1.1",
            session_id="sess-x",
            answered_by="masterway",
            answered_at="2026-05-15T10:00:00+00:00",
            source_message_id=22,
            source_thread_id=11,
            load_session_fn=_load,
            update_session_fn=_update,
        )

        self.assertTrue(outcome.handled)
        self.assertEqual(outcome.new_state, OperatorSessionState.RUNNING)
        # session 갱신 페이로드도 같은 결정 반영
        self.assertEqual(
            captured["extra"]["operator_state"],
            OperatorSessionState.RUNNING.value,
        )
        # pending list 비었음
        self.assertEqual(captured["extra"]["operator_pending_requests"], [])

    def test_no_matching_card_returns_handled_false(self) -> None:
        outcome = handle_operator_action_reply(
            queue=self.queue,
            text="host=1.1.1.1",
            session_id="no-such-session",
            answered_by="m",
            source_message_id=22,
            source_thread_id=11,
            load_session_fn=lambda _: None,
            update_session_fn=lambda s, e: s,
        )
        self.assertFalse(outcome.handled)
        self.assertEqual(outcome.skipped_reason, "no_matching_operator_action")


if __name__ == "__main__":
    unittest.main()
