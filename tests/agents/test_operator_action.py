"""Operator action inbox 모델 / 렌더러 / 파서 테스트 — P0-S.

`#승인-대기` 가 approval-only 가 아니라 **operator action inbox** 로
동작하기 위한 핵심 회귀 라인. 다음 라인을 모두 핀:

  1. 5 가지 ``OperatorActionType`` 값.
  2. ``render_operator_action_card`` 가 request_type 별로 헤더/이모지/
     thread reply 안내를 정확히 분기.
  3. ``parse_operator_action_reply`` 가 ``key=value`` 라인을 정확히
     파싱하고, 필수 키 그룹이 충족되면 ``is_complete=True`` 반환.
  4. SECRET 카드에 raw 값 (``secret_value=`` / ``raw_value=`` / ``value=``)
     이 들어오면 거부 (``rejected_reason="secret_value_inline"``).
  5. ``stamp_pending_request`` / ``stamp_answered_request`` 가
     session.extra 의 ``operator_state`` / pending list / answered list
     를 정확히 변경.
  6. ``is_external_fact_required`` 가 키워드별로 올바른 타입 반환.
  7. ``operator_action_to_approval_payload`` round-trip 으로
     ``ApprovalRequest.from_payload`` 가 그대로 다시 카드를 렌더 가능.
  8. ``render_approval_request`` 가 operator_action payload 를 감지하면
     :func:`render_operator_action_card` 로 위임 — 즉 같은 worker 가
     기존 카드와 새 카드를 모두 출력.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.approval_worker import (
    ApprovalRequest,
    OPERATOR_ACTION_KINDS,
    APPROVAL_KIND_INFO_REQUEST,
    APPROVAL_KIND_ACCESS_REQUEST,
    APPROVAL_KIND_SECRET_REQUEST,
    APPROVAL_KIND_DECISION_REQUEST,
    render_approval_request,
)
from yule_orchestrator.agents.operator_action import (
    OperatorActionRequest,
    OperatorActionType,
    OperatorSessionState,
    SECRET_AUTO_ALLOWED,
    SECRET_AUTO_FORBIDDEN,
    SESSION_EXTRA_ANSWERED_KEY,
    SESSION_EXTRA_OPERATOR_STATE_KEY,
    SESSION_EXTRA_PENDING_REQUESTS_KEY,
    is_external_fact_required,
    operator_action_request_from_approval_payload,
    operator_action_to_approval_payload,
    parse_operator_action_reply,
    render_operator_action_card,
    session_state_for_request_type,
    stamp_answered_request,
    stamp_pending_request,
)


def _info_request() -> OperatorActionRequest:
    return OperatorActionRequest(
        request_type=OperatorActionType.INFO_REQUIRED,
        session_id="sess-1",
        title="deploy 대상 서버 IP 필요",
        stage="backend-engineer 가 deploy 직전",
        why_blocked="deploy target 서버 IP 를 추측할 수 없습니다",
        expected_answer="운영 서버 IP 또는 hostname",
        answer_examples=("host=10.0.0.5", "hostname=api.prod.example.com"),
        next_action="backend-engineer 가 deploy 스크립트의 host 값을 채워 진행",
        timeout_hint="30 분 무응답 시 deploy 보류",
    )


def _access_request() -> OperatorActionRequest:
    return OperatorActionRequest(
        request_type=OperatorActionType.ACCESS_REQUIRED,
        session_id="sess-2",
        title="prod 서버 SSH 접근 정보 필요",
        stage="devops-engineer 가 systemd 설정 변경 직전",
        why_blocked="prod 서버 SSH user/key 를 모릅니다",
        expected_answer="user / auth 방식",
        answer_examples=("user=deploy", "auth=ssh-key"),
        next_action="devops-engineer 가 SSH 접속해 systemd 재시작",
    )


def _secret_request() -> OperatorActionRequest:
    return OperatorActionRequest(
        request_type=OperatorActionType.SECRET_REQUIRED,
        session_id="sess-3",
        title="JWT_SECRET 저장 위치 필요",
        stage="backend-engineer 가 auth 모듈 wiring 직전",
        why_blocked="실제 JWT_SECRET 값을 추측해 만들 수 없습니다",
        expected_answer="github_secret 이름 또는 env_file 경로",
        answer_examples=("github_secret=JWT_SECRET", "env_file=.env.prod"),
        next_action="auth 모듈이 지정 위치에서 secret 을 읽도록 wiring",
    )


def _decision_request() -> OperatorActionRequest:
    return OperatorActionRequest(
        request_type=OperatorActionType.DECISION_REQUIRED,
        session_id="sess-4",
        title="유료 플랜 환불 정책 확인 필요",
        stage="backend-engineer 가 환불 핸들러 구현 직전",
        why_blocked="환불 가능 기간 정책이 제품 결정 사항입니다",
        expected_answer="환불 가능 일수 또는 정책 옵션",
        answer_examples=("decision=14일 이내 전액 환불",),
        next_action="결정대로 환불 핸들러 작성",
    )


class OperatorActionTypeEnumTests(unittest.TestCase):
    def test_five_types_present(self) -> None:
        self.assertEqual(
            {t.value for t in OperatorActionType},
            {
                "approval_required",
                "info_required",
                "access_required",
                "secret_required",
                "decision_required",
            },
        )

    def test_session_state_for_each_type(self) -> None:
        self.assertEqual(
            session_state_for_request_type(OperatorActionType.APPROVAL_REQUIRED),
            OperatorSessionState.WAITING_APPROVAL,
        )
        self.assertEqual(
            session_state_for_request_type(OperatorActionType.INFO_REQUIRED),
            OperatorSessionState.WAITING_USER_INPUT,
        )
        self.assertEqual(
            session_state_for_request_type(OperatorActionType.ACCESS_REQUIRED),
            OperatorSessionState.WAITING_ACCESS,
        )
        self.assertEqual(
            session_state_for_request_type(OperatorActionType.SECRET_REQUIRED),
            OperatorSessionState.WAITING_SECRET,
        )
        self.assertEqual(
            session_state_for_request_type(OperatorActionType.DECISION_REQUIRED),
            OperatorSessionState.WAITING_USER_INPUT,
        )


class OperatorCardRenderingTests(unittest.TestCase):
    def test_info_card_includes_required_fields(self) -> None:
        text = render_operator_action_card(_info_request())
        self.assertIn("[정보 필요]", text)
        self.assertIn("deploy 대상 서버 IP 필요", text)
        self.assertIn("세션: `sess-1`", text)
        self.assertIn("왜 사람이 필요한가", text)
        self.assertIn("운영 서버 IP", text)
        self.assertIn("`host=10.0.0.5`", text)
        self.assertIn("응답 후 진행", text)
        self.assertIn("타임아웃", text)
        self.assertIn("이 카드 thread 에서", text)

    def test_secret_card_carries_hard_rail(self) -> None:
        text = render_operator_action_card(_secret_request())
        self.assertIn("[Secret 필요]", text)
        self.assertIn("github_secret=JWT_SECRET", text)
        self.assertIn("env_file=.env.prod", text)
        # Hard rail line — agent 는 실제 값을 만들지 않음.
        self.assertIn("실제 secret 값을 생성/저장/수정하지", text)

    def test_access_card_uses_access_label(self) -> None:
        text = render_operator_action_card(_access_request())
        self.assertIn("[접근 / 권한 필요]", text)
        self.assertIn("user=deploy", text)
        self.assertIn("auth=ssh-key", text)

    def test_decision_card_uses_decision_label(self) -> None:
        text = render_operator_action_card(_decision_request())
        self.assertIn("[정책 / 제품 판단 필요]", text)
        self.assertIn("decision=14일 이내 전액 환불", text)

    def test_render_is_deterministic(self) -> None:
        a = render_operator_action_card(_info_request())
        b = render_operator_action_card(_info_request())
        self.assertEqual(a, b)


class OperatorReplyParserTests(unittest.TestCase):
    def test_info_reply_with_host_is_complete(self) -> None:
        reply = parse_operator_action_reply(
            request_type=OperatorActionType.INFO_REQUIRED,
            text="host=10.0.0.5",
        )
        self.assertEqual(reply.answers, {"host": "10.0.0.5"})
        self.assertTrue(reply.is_complete)
        self.assertIsNone(reply.rejected_reason)

    def test_access_reply_requires_user_and_auth(self) -> None:
        # user 만 있으면 미완 (그룹 ("user","auth") 충족 안 됨, ssh_user 도 없음)
        partial = parse_operator_action_reply(
            request_type=OperatorActionType.ACCESS_REQUIRED,
            text="user=deploy",
        )
        self.assertFalse(partial.is_complete)
        self.assertEqual(partial.rejected_reason, "missing_required_keys")

        full = parse_operator_action_reply(
            request_type=OperatorActionType.ACCESS_REQUIRED,
            text="user=deploy\nauth=ssh-key",
        )
        self.assertEqual(full.answers, {"user": "deploy", "auth": "ssh-key"})
        self.assertTrue(full.is_complete)

    def test_secret_reply_with_github_secret_is_complete(self) -> None:
        reply = parse_operator_action_reply(
            request_type=OperatorActionType.SECRET_REQUIRED,
            text="github_secret=JWT_SECRET",
        )
        self.assertTrue(reply.is_complete)
        self.assertEqual(reply.answers, {"github_secret": "JWT_SECRET"})

    def test_secret_reply_with_raw_value_rejected(self) -> None:
        reply = parse_operator_action_reply(
            request_type=OperatorActionType.SECRET_REQUIRED,
            text="value=super-secret-string",
        )
        self.assertFalse(reply.is_complete)
        self.assertEqual(reply.rejected_reason, "secret_value_inline")
        self.assertEqual(reply.answers, {})

    def test_secret_reply_with_secret_value_alias_rejected(self) -> None:
        for alias in ("secret_value", "raw_value"):
            reply = parse_operator_action_reply(
                request_type=OperatorActionType.SECRET_REQUIRED,
                text=f"{alias}=top-secret",
            )
            self.assertEqual(reply.rejected_reason, "secret_value_inline")

    def test_decision_reply(self) -> None:
        reply = parse_operator_action_reply(
            request_type=OperatorActionType.DECISION_REQUIRED,
            text="decision=14일 이내 전액 환불",
        )
        self.assertTrue(reply.is_complete)

    def test_bullet_prefix_is_stripped(self) -> None:
        reply = parse_operator_action_reply(
            request_type=OperatorActionType.INFO_REQUIRED,
            text="- host=10.0.0.5",
        )
        self.assertTrue(reply.is_complete)
        self.assertEqual(reply.answers, {"host": "10.0.0.5"})

    def test_unrecognised_keys_stay_unparsed(self) -> None:
        reply = parse_operator_action_reply(
            request_type=OperatorActionType.INFO_REQUIRED,
            text="just a free form sentence about something",
        )
        self.assertFalse(reply.is_complete)
        self.assertEqual(reply.rejected_reason, "no_key_value_pairs")


class SessionStampingTests(unittest.TestCase):
    def test_stamp_pending_sets_waiting_state_and_appends(self) -> None:
        extra = stamp_pending_request(
            session_extra={}, request=_info_request()
        )
        self.assertEqual(
            extra[SESSION_EXTRA_OPERATOR_STATE_KEY],
            OperatorSessionState.WAITING_USER_INPUT.value,
        )
        pending = extra[SESSION_EXTRA_PENDING_REQUESTS_KEY]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["request_type"], "info_required")
        self.assertEqual(pending[0]["title"], "deploy 대상 서버 IP 필요")

    def test_stamp_pending_does_not_mutate_input(self) -> None:
        original = {"unrelated": True}
        stamp_pending_request(session_extra=original, request=_secret_request())
        self.assertEqual(original, {"unrelated": True})

    def test_stamp_answered_running_returns_when_only_pending_resolved(self) -> None:
        after_pending = stamp_pending_request(
            session_extra={}, request=_info_request()
        )
        reply = parse_operator_action_reply(
            request_type=OperatorActionType.INFO_REQUIRED, text="host=10.0.0.5"
        )
        after_answered = stamp_answered_request(
            session_extra=after_pending,
            reply=reply,
            answered_by="masterway",
            answered_at="2026-05-15T10:00:00+00:00",
        )
        self.assertEqual(
            after_answered[SESSION_EXTRA_OPERATOR_STATE_KEY],
            OperatorSessionState.RUNNING.value,
        )
        self.assertEqual(after_answered[SESSION_EXTRA_PENDING_REQUESTS_KEY], [])
        answered = after_answered[SESSION_EXTRA_ANSWERED_KEY]
        self.assertEqual(len(answered), 1)
        self.assertTrue(answered[0]["is_complete"])
        self.assertEqual(answered[0]["answered_by"], "masterway")

    def test_stamp_answered_keeps_waiting_when_more_pending(self) -> None:
        # 2 카드 pending, 첫 번째만 응답 — state 는 두 번째의 type 으로
        extra = stamp_pending_request(session_extra={}, request=_info_request())
        extra = stamp_pending_request(session_extra=extra, request=_secret_request())
        reply = parse_operator_action_reply(
            request_type=OperatorActionType.INFO_REQUIRED, text="host=10.0.0.5"
        )
        after = stamp_answered_request(
            session_extra=extra,
            reply=reply,
            answered_by="m",
            answered_at="2026-05-15T10:00:00+00:00",
        )
        self.assertEqual(
            after[SESSION_EXTRA_OPERATOR_STATE_KEY],
            OperatorSessionState.WAITING_SECRET.value,
        )
        self.assertEqual(len(after[SESSION_EXTRA_PENDING_REQUESTS_KEY]), 1)
        self.assertEqual(
            after[SESSION_EXTRA_PENDING_REQUESTS_KEY][0]["request_type"],
            "secret_required",
        )

    def test_stamp_answered_incomplete_keeps_pending_and_state(self) -> None:
        extra = stamp_pending_request(session_extra={}, request=_secret_request())
        bad = parse_operator_action_reply(
            request_type=OperatorActionType.SECRET_REQUIRED,
            text="value=raw-string",
        )
        after = stamp_answered_request(
            session_extra=extra,
            reply=bad,
            answered_by="m",
            answered_at="2026-05-15T10:00:00+00:00",
        )
        # state 그대로 waiting_secret, pending 그대로
        self.assertEqual(
            after[SESSION_EXTRA_OPERATOR_STATE_KEY],
            OperatorSessionState.WAITING_SECRET.value,
        )
        self.assertEqual(len(after[SESSION_EXTRA_PENDING_REQUESTS_KEY]), 1)
        # audit 에는 거부 사유까지 기록
        last = after[SESSION_EXTRA_ANSWERED_KEY][-1]
        self.assertFalse(last["is_complete"])
        self.assertEqual(last["rejected_reason"], "secret_value_inline")


class ExternalFactDetectorTests(unittest.TestCase):
    def test_server_ip_keyword_returns_info(self) -> None:
        self.assertEqual(
            is_external_fact_required("배포 대상 서버 IP 가 필요해요"),
            OperatorActionType.INFO_REQUIRED,
        )

    def test_ssh_keyword_returns_access(self) -> None:
        self.assertEqual(
            is_external_fact_required("prod 서버 SSH 접근 가능?"),
            OperatorActionType.ACCESS_REQUIRED,
        )

    def test_secret_value_keyword_returns_secret(self) -> None:
        self.assertEqual(
            is_external_fact_required("실제 secret 값 알려줘"),
            OperatorActionType.SECRET_REQUIRED,
        )

    def test_pure_tech_choice_does_not_trigger(self) -> None:
        # JWT vs session 같은 단순 기술 선택은 자율 판단
        self.assertIsNone(
            is_external_fact_required("JWT 로 갈지 session 으로 갈지 결정해줘")
        )
        self.assertIsNone(
            is_external_fact_required("DB 이름 어떻게 잡지")
        )
        self.assertIsNone(
            is_external_fact_required("Docker Compose 구조 잡아줘")
        )


class ApprovalPayloadRoundTripTests(unittest.TestCase):
    def test_payload_roundtrip_renders_same_card(self) -> None:
        op = _info_request()
        payload = operator_action_to_approval_payload(
            op,
            created_by="backend-engineer",
            source_thread_id=42,
            source_message_id=99,
        )
        # 새 kind 가 OPERATOR_ACTION_KINDS 안에 있어야 reply router 가 찾는다
        self.assertIn(payload["approval_kind"], OPERATOR_ACTION_KINDS)
        self.assertEqual(payload["approval_kind"], APPROVAL_KIND_INFO_REQUEST)

        request = ApprovalRequest.from_payload(payload)
        rendered_via_worker = render_approval_request(request)
        rendered_direct = render_operator_action_card(op)
        # render_approval_request 가 operator_action payload 를 감지해서 동일하게 렌더링
        self.assertEqual(rendered_via_worker, rendered_direct)

    def test_payload_roundtrip_recovers_request(self) -> None:
        op = _secret_request()
        payload = operator_action_to_approval_payload(op)
        recovered = operator_action_request_from_approval_payload(payload)
        self.assertIsNotNone(recovered)
        assert recovered is not None  # for type checker
        self.assertEqual(recovered.request_type, OperatorActionType.SECRET_REQUIRED)
        self.assertEqual(recovered.title, "JWT_SECRET 저장 위치 필요")
        self.assertEqual(
            recovered.answer_examples,
            ("github_secret=JWT_SECRET", "env_file=.env.prod"),
        )

    def test_existing_approval_payload_without_operator_action_renders_default(self) -> None:
        # 회귀 보장: 기존 ApprovalRequest (operator_action 없음) 는 기본 렌더로 fallback
        request = ApprovalRequest(
            session_id="legacy-1",
            approval_kind="obsidian_write",
            title="legacy 카드",
            summary="기존 흐름",
            requested_action="저장 승인",
            created_by="research_worker",
        )
        text = render_approval_request(request)
        self.assertIn("[승인 요청 — Obsidian 저장]", text)
        self.assertIn("legacy 카드", text)
        # 새 카드 마커는 절대 안 나와야 함
        self.assertNotIn("정보 필요", text)
        self.assertNotIn("Secret 필요", text)

    def test_each_request_type_uses_correct_kind(self) -> None:
        cases = (
            (_info_request(), APPROVAL_KIND_INFO_REQUEST),
            (_access_request(), APPROVAL_KIND_ACCESS_REQUEST),
            (_secret_request(), APPROVAL_KIND_SECRET_REQUEST),
            (_decision_request(), APPROVAL_KIND_DECISION_REQUEST),
        )
        for op, expected_kind in cases:
            with self.subTest(request_type=op.request_type):
                payload = operator_action_to_approval_payload(op)
                self.assertEqual(payload["approval_kind"], expected_kind)


class SecretHardRailDocumentedTests(unittest.TestCase):
    """Hard rail 목록이 모듈에 코드 형태로 박혀있는지 확인.

    추후 누군가가 SECRET_AUTO_FORBIDDEN 항목을 임의로 지우면 이 테스트가
    fail 해서 docs/approval-matrix.md §6.3 가 검증 없이 무너지는 것을
    막는다.
    """

    def test_allowed_list_includes_env_example_wiring(self) -> None:
        joined = " | ".join(SECRET_AUTO_ALLOWED)
        self.assertIn(".env.example", joined)
        self.assertIn("GitHub Actions secret 이름", joined)

    def test_forbidden_list_includes_prod_env_change(self) -> None:
        joined = " | ".join(SECRET_AUTO_FORBIDDEN)
        self.assertIn("prod .env 파일을 직접 변경", joined)
        self.assertIn("실제 secret 값", joined)


if __name__ == "__main__":
    unittest.main()
