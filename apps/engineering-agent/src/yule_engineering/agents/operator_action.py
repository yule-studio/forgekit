"""Operator action inbox — `#승인-대기` 가 approval-only 가 아닌
operator-facing inbox 로 동작하기 위한 모델/렌더러/리플라이 파서.

배경
====
현재 `#승인-대기` 는 ``ApprovalRequest`` (write/PR/merge/deploy 같은
승인 액션) 카드만 게시한다. 그러나 engineering-agent 가 진행 중에
필요한 외부 정보 (서버 IP, SSH 자격, 실제 secret 값) 까지 같은
채널에서 받아내야 "조용히 멈추는" 회귀를 막을 수 있다.

기술 자율 vs 외부 사실 경계 (`docs/approval-matrix.md` §6 참조):

- **agent 가 스스로 판단**: JWT vs session, DB 이름, Docker compose
  구조, Next/Nest 연결 방식, auth/API 구조, 디렉터리 구조, 일반
  기술 선택.
- **반드시 사람에게 받음**: 실제 서버 IP / hostname, SSH user/key,
  실제 도메인, 실제 secret 값, 운영 DB/Redis/API endpoint, 클라우드
  계정 식별자, "이 환경 수정해도 되는가" 같은 권한 사실.

본 모듈은 두 번째 항목을 위한 **operator action request** 를 정의한다.
queue / Discord / session 어느 한 쪽에도 의존하지 않는 pure 모델로
유지해 어디서든 import 가능하게 한다.

핵심 surface
============
- :class:`OperatorActionType` — 5 가지 요청 유형.
- :class:`OperatorSessionState` — 세션 대기 상태.
- :class:`OperatorActionRequest` — 카드/세션 양쪽이 공유하는 dataclass.
- :func:`render_operator_action_card` — markdown 카드 본문.
- :func:`parse_operator_action_reply` — `key=value` 응답 파서.
- :func:`session_state_for_request_type` — request_type → 대기 상태 매핑.
- :func:`is_external_fact_required` — 사람에게 물어야 하는 키워드 감지.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class OperatorActionType(str, Enum):
    """5 가지 operator-facing 요청 유형.

    문자열 값은 ``ApprovalRequest.extra['operator_action']['request_type']``
    에 저장돼 SQLite TEXT 컬럼을 그대로 round-trip 한다. 새 유형을 추가할
    때는 :data:`_REQUEST_TYPE_LABELS` / :data:`_REQUEST_TYPE_HINTS` 도
    같이 갱신해야 한다.
    """

    APPROVAL_REQUIRED = "approval_required"
    INFO_REQUIRED = "info_required"
    ACCESS_REQUIRED = "access_required"
    SECRET_REQUIRED = "secret_required"
    DECISION_REQUIRED = "decision_required"


class OperatorSessionState(str, Enum):
    """세션이 어떤 종류의 사람 응답을 기다리는지.

    ``WorkflowState`` (세션 macro 라이프사이클) 옆에 붙는 sub-state.
    `session.extra['operator_state']` 에 저장된다. ``running`` 은 모든
    필요한 응답이 채워진 뒤 복귀하는 기본값이다.
    """

    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_USER_INPUT = "waiting_user_input"
    WAITING_ACCESS = "waiting_access"
    WAITING_SECRET = "waiting_secret"
    BLOCKED_EXTERNAL = "blocked_external"


# 기술 선택 vs 외부 사실 경계의 좌측 (agent 자율) — 본 모듈은 우측에
# 해당하는 외부 사실 키워드만 감지한다. 좌측 키워드는 코드/문서 레퍼런스
# 용도로만 남기고 dispatcher 가 알아서 판단한다.
EXTERNAL_FACT_KEYWORDS: Tuple[Tuple[str, OperatorActionType], ...] = (
    # INFO — 운영 사실 / 식별자
    ("서버 ip", OperatorActionType.INFO_REQUIRED),
    ("server ip", OperatorActionType.INFO_REQUIRED),
    ("hostname", OperatorActionType.INFO_REQUIRED),
    ("실제 도메인", OperatorActionType.INFO_REQUIRED),
    ("운영 도메인", OperatorActionType.INFO_REQUIRED),
    ("배포 대상 서버", OperatorActionType.INFO_REQUIRED),
    ("운영 db", OperatorActionType.INFO_REQUIRED),
    ("운영 redis", OperatorActionType.INFO_REQUIRED),
    ("운영 api endpoint", OperatorActionType.INFO_REQUIRED),
    ("클라우드 프로젝트", OperatorActionType.INFO_REQUIRED),
    ("cloud project", OperatorActionType.INFO_REQUIRED),
    ("계정 식별자", OperatorActionType.INFO_REQUIRED),
    # ACCESS — 권한 / 접근
    ("ssh 접근", OperatorActionType.ACCESS_REQUIRED),
    ("ssh user", OperatorActionType.ACCESS_REQUIRED),
    ("ssh key", OperatorActionType.ACCESS_REQUIRED),
    ("ssh-key", OperatorActionType.ACCESS_REQUIRED),
    ("repo access", OperatorActionType.ACCESS_REQUIRED),
    ("권한 부여", OperatorActionType.ACCESS_REQUIRED),
    ("환경 수정해도", OperatorActionType.ACCESS_REQUIRED),
    ("이 서버 수정해도", OperatorActionType.ACCESS_REQUIRED),
    ("cloud access", OperatorActionType.ACCESS_REQUIRED),
    # SECRET — 실제 값 / 저장 위치
    ("실제 secret 값", OperatorActionType.SECRET_REQUIRED),
    ("실제 secret value", OperatorActionType.SECRET_REQUIRED),
    ("secret 등록", OperatorActionType.SECRET_REQUIRED),
    ("secret 저장 위치", OperatorActionType.SECRET_REQUIRED),
    ("jwt_secret 값", OperatorActionType.SECRET_REQUIRED),
    ("api 키 값", OperatorActionType.SECRET_REQUIRED),
    ("api key 값", OperatorActionType.SECRET_REQUIRED),
    ("실제 토큰", OperatorActionType.SECRET_REQUIRED),
)


def session_state_for_request_type(
    request_type: OperatorActionType,
) -> OperatorSessionState:
    """request_type 에 대응하는 ``waiting_*`` 상태."""

    return _REQUEST_TYPE_TO_STATE.get(
        request_type, OperatorSessionState.BLOCKED_EXTERNAL
    )


_REQUEST_TYPE_TO_STATE: Mapping[OperatorActionType, OperatorSessionState] = {
    OperatorActionType.APPROVAL_REQUIRED: OperatorSessionState.WAITING_APPROVAL,
    OperatorActionType.INFO_REQUIRED: OperatorSessionState.WAITING_USER_INPUT,
    OperatorActionType.ACCESS_REQUIRED: OperatorSessionState.WAITING_ACCESS,
    OperatorActionType.SECRET_REQUIRED: OperatorSessionState.WAITING_SECRET,
    OperatorActionType.DECISION_REQUIRED: OperatorSessionState.WAITING_USER_INPUT,
}


# ---------------------------------------------------------------------------
# Hard rails — agent 가 절대 자동으로 하면 안 되는 secret 작업
# ---------------------------------------------------------------------------


SECRET_AUTO_ALLOWED: Tuple[str, ...] = (
    "secret key 이름 정의",
    ".env.example / compose / CI wiring 작성",
    "GitHub Actions secret 이름 제안",
    "어떤 값이 필요한지 설명",
)
"""agent 가 사람 승인 없이 진행해도 되는 secret 관련 작업 목록.

본 리스트 밖의 secret 작업 (실제 값 생성, secret store/cloud secret
manager 무단 수정, prod `.env` 직접 변경) 은 SECRET_REQUIRED 로
사람에게 명시 요청해야 한다.
"""


SECRET_AUTO_FORBIDDEN: Tuple[str, ...] = (
    "실제 secret 값을 임의로 생성해 운영 환경에 저장",
    "GitHub secret / cloud secret manager 를 사용자 동의 없이 수정",
    "prod .env 파일을 직접 변경",
    "기존 secret 값을 다른 위치로 복사 / mirror",
)


# ---------------------------------------------------------------------------
# Request dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OperatorActionRequest:
    """Operator-facing action 요청.

    `#승인-대기` 카드 한 장이 가져야 할 모든 정보를 보유한다. queue 의
    ``ApprovalRequest`` 와 1:1 으로 매핑돼 ``approval_post`` job 의
    payload 로 round-trip 된다 — 변환 헬퍼는
    :func:`to_approval_request_payload` / :func:`from_approval_request_extra`.

    필드
    ----
    request_type
        :class:`OperatorActionType` — 5 유형 중 하나.
    session_id
        세션 식별자. 응답 router 가 어느 세션의 ``operator_state`` 를
        업데이트할지 결정하는 근거.
    stage
        지금 멈춘 단계 ("backend-engineer 가 deploy 직전").
    why_blocked
        한국어로 "왜 사람이 필요한지" — 예: "deploy target 서버 IP 를
        agent 가 추측할 수 없습니다".
    expected_answer
        지금 필요한 답변 1줄. 짧을수록 좋다.
    answer_examples
        ``("host=10.0.0.5",)`` 처럼 thread reply 예시 시퀀스.
    next_action
        응답 후 어떤 작업이 이어지는지. operator 가 "내가 답하면 어디로
        가는지" 즉시 알 수 있어야 함.
    timeout_hint
        timeout / fallback 메모. 비어있을 수 있음.
    """

    request_type: OperatorActionType
    session_id: str
    title: str
    stage: str
    why_blocked: str
    expected_answer: str
    answer_examples: Tuple[str, ...] = ()
    next_action: str = ""
    timeout_hint: str = ""
    requested_by: str = "engineering-agent"
    extra: Mapping[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Round-trip helpers — ApprovalRequest.extra ↔ OperatorActionRequest
    # ------------------------------------------------------------------

    def to_extra_payload(self) -> Mapping[str, Any]:
        """``ApprovalRequest.extra['operator_action']`` 에 들어갈 dict."""

        return {
            "request_type": self.request_type.value,
            "session_id": self.session_id,
            "title": self.title,
            "stage": self.stage,
            "why_blocked": self.why_blocked,
            "expected_answer": self.expected_answer,
            "answer_examples": list(self.answer_examples),
            "next_action": self.next_action,
            "timeout_hint": self.timeout_hint,
            "requested_by": self.requested_by,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_extra_payload(
        cls, payload: Mapping[str, Any]
    ) -> "OperatorActionRequest":
        """:meth:`to_extra_payload` 의 역변환. queue row 에서 복원할 때 사용."""

        raw_type = str(payload.get("request_type") or "").strip()
        try:
            request_type = OperatorActionType(raw_type)
        except ValueError:
            request_type = OperatorActionType.APPROVAL_REQUIRED
        return cls(
            request_type=request_type,
            session_id=str(payload.get("session_id") or ""),
            title=str(payload.get("title") or ""),
            stage=str(payload.get("stage") or ""),
            why_blocked=str(payload.get("why_blocked") or ""),
            expected_answer=str(payload.get("expected_answer") or ""),
            answer_examples=tuple(
                str(item) for item in (payload.get("answer_examples") or ())
            ),
            next_action=str(payload.get("next_action") or ""),
            timeout_hint=str(payload.get("timeout_hint") or ""),
            requested_by=str(
                payload.get("requested_by") or "engineering-agent"
            ),
            extra=dict(payload.get("extra") or {}),
        )


# ---------------------------------------------------------------------------
# Card rendering
# ---------------------------------------------------------------------------


_REQUEST_TYPE_LABELS: Mapping[OperatorActionType, str] = {
    OperatorActionType.APPROVAL_REQUIRED: "승인 필요",
    OperatorActionType.INFO_REQUIRED: "정보 필요",
    OperatorActionType.ACCESS_REQUIRED: "접근 / 권한 필요",
    OperatorActionType.SECRET_REQUIRED: "Secret 필요",
    OperatorActionType.DECISION_REQUIRED: "정책 / 제품 판단 필요",
}


_REQUEST_TYPE_EMOJIS: Mapping[OperatorActionType, str] = {
    OperatorActionType.APPROVAL_REQUIRED: "✅",
    OperatorActionType.INFO_REQUIRED: "❓",
    OperatorActionType.ACCESS_REQUIRED: "🔐",
    OperatorActionType.SECRET_REQUIRED: "🗝️",
    OperatorActionType.DECISION_REQUIRED: "🧭",
}


_REQUEST_TYPE_REPLY_HINT: Mapping[OperatorActionType, str] = {
    OperatorActionType.APPROVAL_REQUIRED: (
        "이 카드 thread 에서 `승인` / `이대로 진행` 또는 `반려` / `보류` 로 답해 주세요."
    ),
    OperatorActionType.INFO_REQUIRED: (
        "이 카드 thread 에서 `key=value` 형태 (예: `host=10.0.0.5`) 로 답해 주세요."
    ),
    OperatorActionType.ACCESS_REQUIRED: (
        "이 카드 thread 에서 `user=...`, `auth=ssh-key` 같이 답해 주세요. 실제 키 값은 직접 붙이지 말고 저장 위치를 지정합니다."
    ),
    OperatorActionType.SECRET_REQUIRED: (
        "이 카드 thread 에서 `github_secret=NAME` 또는 `env_file=.env.prod` 로 저장 위치/주입 방법을 지정해 주세요. 값 자체는 채널에 붙이지 마세요."
    ),
    OperatorActionType.DECISION_REQUIRED: (
        "이 카드 thread 에서 한 줄로 결정 (예: `decision=옵션A`) 을 적어 주세요."
    ),
}


def render_operator_action_card(request: OperatorActionRequest) -> str:
    """*request* 를 ``#승인-대기`` 채널에 게시할 markdown 본문으로 렌더링.

    카드는 6 블록으로 구성: 헤더 / 세션·단계 / 이유 / 필요한 답변 +
    예시 / 응답 후 동작 / thread reply 안내. 결정성 (입력이 같으면 출력
    같음) 을 유지해 ``ApprovalWorker`` 의 dedup 키가 동일 카드를 재게시
    하지 않게 한다.
    """

    label = _REQUEST_TYPE_LABELS.get(
        request.request_type, request.request_type.value
    )
    emoji = _REQUEST_TYPE_EMOJIS.get(request.request_type, "📨")
    lines: list[str] = []
    lines.append(
        f"{emoji} **[{label}] {request.title or request.request_type.value}**"
    )
    lines.append("")
    lines.append(
        f"세션: `{request.session_id or 'unknown'}` · 요청자: `{request.requested_by}`"
    )
    if request.stage:
        lines.append(f"단계: {request.stage}")
    lines.append("")

    if request.why_blocked:
        lines.append("🔎 왜 사람이 필요한가")
        lines.append(request.why_blocked)
        lines.append("")

    if request.expected_answer:
        lines.append("📥 지금 필요한 답변")
        lines.append(f"- {request.expected_answer}")
    if request.answer_examples:
        lines.append("")
        lines.append("📌 답변 예시")
        for example in request.answer_examples:
            if example:
                lines.append(f"- `{example}`")
    lines.append("")

    if request.next_action:
        lines.append("➡️ 응답 후 진행")
        lines.append(request.next_action)
        lines.append("")

    if request.timeout_hint:
        lines.append(f"⏱ 타임아웃 / fallback: {request.timeout_hint}")
        lines.append("")

    hint = _REQUEST_TYPE_REPLY_HINT.get(
        request.request_type, _REQUEST_TYPE_REPLY_HINT[OperatorActionType.APPROVAL_REQUIRED]
    )
    lines.append(hint)

    if request.request_type == OperatorActionType.SECRET_REQUIRED:
        lines.append("")
        lines.append(
            "🛡 hard rail: agent 는 실제 secret 값을 생성/저장/수정하지 "
            "않습니다. 저장 위치만 지정해 주세요."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reply parsing
# ---------------------------------------------------------------------------


_KV_LINE_RE = re.compile(
    r"(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*(?P<value>.+?)\s*$"
)


# request_type 별로 "응답이 충분한가" 를 판단할 때 보는 필수 키 집합.
# 한 카드가 두 종류 키를 동시에 받을 수 있도록 둘 중 하나만 매칭돼도 OK.
_REQUIRED_KEYS_BY_TYPE: Mapping[OperatorActionType, Tuple[Tuple[str, ...], ...]] = {
    OperatorActionType.INFO_REQUIRED: (
        ("host",),
        ("hostname",),
        ("ip",),
        ("domain",),
        ("endpoint",),
        ("project",),
        ("account",),
    ),
    OperatorActionType.ACCESS_REQUIRED: (
        ("user", "auth"),
        ("ssh_user",),
        ("ssh_key",),
        ("access_method",),
    ),
    OperatorActionType.SECRET_REQUIRED: (
        ("github_secret",),
        ("env_file",),
        ("vault_path",),
        ("secret_store",),
        ("cloud_secret",),
    ),
    OperatorActionType.DECISION_REQUIRED: (("decision",), ("choice",)),
    OperatorActionType.APPROVAL_REQUIRED: (("intent",),),
}


# secret 카드에 대해 "raw 값" 을 그대로 채널에 붙인 응답을 거부할 때
# 사용. 값 자체가 공유되면 audit 누수가 생긴다 — 저장 위치만 받는다.
_SECRET_VALUE_KEY_HINT_RE = re.compile(
    r"(?:^|\W)(secret_value|raw_value|value)\s*=", re.IGNORECASE
)


@dataclass(frozen=True)
class OperatorActionReply:
    """파싱된 thread reply 결과.

    ``answers`` 는 정규화된 key=value mapping. ``is_complete`` 는 해당
    request_type 의 필수 키 집합 중 하나가 충족됐는지 여부 — True 일 때
    상위 router 는 세션 상태를 ``running`` 으로 복귀시킨다.
    ``rejected_reason`` 은 secret 카드에 raw 값이 붙은 경우 등 거부 사유.
    """

    request_type: OperatorActionType
    answers: Mapping[str, str]
    is_complete: bool
    rejected_reason: Optional[str] = None
    raw_text: str = ""


def parse_operator_action_reply(
    *,
    request_type: OperatorActionType,
    text: str,
) -> OperatorActionReply:
    """thread reply *text* 를 ``key=value`` 라인들로 분해해 결과 반환.

    파싱 규칙
    --------
    - 한 줄에 하나씩 ``key=value`` 형태. 키는 알파벳/숫자/`.-_`.
    - 줄 머리에 `- ` / `* ` 가 붙어도 허용.
    - 동일 키가 여러 번 나오면 마지막 값을 채택 (덮어쓰기 의도).
    - SECRET_REQUIRED 카드에 ``secret_value=...`` / ``raw_value=...``
      / ``value=...`` 로 raw 값을 붙이면 ``rejected_reason="secret_value_inline"``
      을 채워 응답 자체를 거부한다 (저장 위치만 받는다).
    """

    raw = (text or "").strip()
    answers: dict[str, str] = {}

    if request_type == OperatorActionType.SECRET_REQUIRED and _SECRET_VALUE_KEY_HINT_RE.search(raw):
        return OperatorActionReply(
            request_type=request_type,
            answers={},
            is_complete=False,
            rejected_reason="secret_value_inline",
            raw_text=raw,
        )

    for line in raw.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        # bullet prefix 제거
        if cleaned.startswith(("- ", "* ", "• ")):
            cleaned = cleaned[2:].strip()
        match = _KV_LINE_RE.match(cleaned)
        if not match:
            continue
        key = match.group("key").strip().lower()
        value = match.group("value").strip().strip("`").strip()
        if not value:
            continue
        answers[key] = value

    if not answers:
        # APPROVAL_REQUIRED 는 텍스트 자체가 의도라 별도 처리. 다른 타입은
        # 키 형태가 안 맞으면 미완으로 본다.
        return OperatorActionReply(
            request_type=request_type,
            answers={},
            is_complete=False,
            rejected_reason="no_key_value_pairs" if request_type != OperatorActionType.APPROVAL_REQUIRED else None,
            raw_text=raw,
        )

    requirement_groups = _REQUIRED_KEYS_BY_TYPE.get(request_type, ())
    is_complete = False
    for group in requirement_groups:
        if all(key in answers for key in group):
            is_complete = True
            break

    return OperatorActionReply(
        request_type=request_type,
        answers=answers,
        is_complete=is_complete,
        rejected_reason=None if is_complete else "missing_required_keys",
        raw_text=raw,
    )


# ---------------------------------------------------------------------------
# External-fact detector — agent 자율 판단 vs 사람 요청 가드
# ---------------------------------------------------------------------------


def is_external_fact_required(text: str) -> Optional[OperatorActionType]:
    """*text* 가 외부 사실/권한/secret 키워드를 포함하면 해당 타입 반환.

    None 일 때는 agent 가 자율 판단해도 된다는 뜻. 이 헬퍼는 보수적이라
    상위 호출자가 추가 정보를 가지고 있다면 override 할 수 있다.
    """

    if not text:
        return None
    haystack = " ".join(text.lower().split())
    for keyword, request_type in EXTERNAL_FACT_KEYWORDS:
        if keyword in haystack:
            return request_type
    return None


# ---------------------------------------------------------------------------
# Session-state stamping helpers (pure — caller persists)
# ---------------------------------------------------------------------------


SESSION_EXTRA_OPERATOR_STATE_KEY: str = "operator_state"
"""``WorkflowSession.extra`` 에 :class:`OperatorSessionState` 가 저장되는 키."""

SESSION_EXTRA_PENDING_REQUESTS_KEY: str = "operator_pending_requests"
"""``WorkflowSession.extra`` 에 미해결 ``OperatorActionRequest`` 가 쌓이는 list."""

SESSION_EXTRA_ANSWERED_KEY: str = "operator_answered_requests"
"""사람이 답을 채운 ``OperatorActionReply`` 가 쌓이는 audit list."""


def stamp_pending_request(
    *,
    session_extra: Mapping[str, Any],
    request: OperatorActionRequest,
) -> Mapping[str, Any]:
    """*session_extra* 에 미해결 요청과 ``waiting_*`` 상태를 새겨 반환.

    pure: 입력 dict 를 mutate 하지 않고 새 dict 를 만든다. caller 가
    이 결과로 ``WorkflowSession.extra`` 를 갱신한 뒤 ``update_session``
    으로 persistence.
    """

    extra: dict[str, Any] = dict(session_extra or {})
    pending: list[Any] = list(extra.get(SESSION_EXTRA_PENDING_REQUESTS_KEY) or [])
    pending.append({
        "request_type": request.request_type.value,
        "title": request.title,
        "stage": request.stage,
        "why_blocked": request.why_blocked,
        "expected_answer": request.expected_answer,
    })
    # cap 32 — 한 세션에 그 이상 미해결이면 어차피 운영 사고
    if len(pending) > 32:
        pending = pending[-32:]
    extra[SESSION_EXTRA_PENDING_REQUESTS_KEY] = pending
    extra[SESSION_EXTRA_OPERATOR_STATE_KEY] = session_state_for_request_type(
        request.request_type
    ).value
    return extra


def stamp_answered_request(
    *,
    session_extra: Mapping[str, Any],
    reply: OperatorActionReply,
    answered_by: str,
    answered_at: str,
) -> Mapping[str, Any]:
    """*session_extra* 에 응답 audit 을 새기고, 미해결이 모두 해소되면
    ``operator_state`` 를 ``running`` 으로 복귀시킨다."""

    extra: dict[str, Any] = dict(session_extra or {})
    answered: list[Any] = list(extra.get(SESSION_EXTRA_ANSWERED_KEY) or [])
    answered.append({
        "request_type": reply.request_type.value,
        "answers": dict(reply.answers),
        "answered_by": answered_by,
        "answered_at": answered_at,
        "rejected_reason": reply.rejected_reason,
        "is_complete": reply.is_complete,
    })
    if len(answered) > 64:
        answered = answered[-64:]
    extra[SESSION_EXTRA_ANSWERED_KEY] = answered

    if not reply.is_complete:
        # 응답이 불완전하면 상태는 그대로 (사람이 다시 답할 때까지 대기)
        return extra

    # 같은 타입의 미해결 1 건을 제거 — pending 은 가장 오래된 것부터 제거.
    pending: list[Any] = list(extra.get(SESSION_EXTRA_PENDING_REQUESTS_KEY) or [])
    target_value = reply.request_type.value
    for index, entry in enumerate(pending):
        if isinstance(entry, dict) and entry.get("request_type") == target_value:
            del pending[index]
            break
    extra[SESSION_EXTRA_PENDING_REQUESTS_KEY] = pending

    if not pending:
        extra[SESSION_EXTRA_OPERATOR_STATE_KEY] = OperatorSessionState.RUNNING.value
    else:
        # 다음 미해결의 타입을 다시 반영
        next_entry = pending[0]
        try:
            next_type = OperatorActionType(str(next_entry.get("request_type")))
        except (ValueError, AttributeError):
            next_type = OperatorActionType.APPROVAL_REQUIRED
        extra[SESSION_EXTRA_OPERATOR_STATE_KEY] = session_state_for_request_type(
            next_type
        ).value

    return extra


# ---------------------------------------------------------------------------
# ApprovalRequest 변환 — queue 측 페이로드와 1:1 매핑
# ---------------------------------------------------------------------------


# request_type → ``ApprovalRequest.approval_kind`` 매핑. APPROVAL_REQUIRED
# 만 호출자가 자체 kind 를 지정하도록 None 반환 (engineering_write /
# pr_merge / obsidian_write 등 기존 vocabulary 가 그대로 쓰임).
_REQUEST_TYPE_TO_APPROVAL_KIND: Mapping[OperatorActionType, Optional[str]] = {
    OperatorActionType.APPROVAL_REQUIRED: None,
    OperatorActionType.INFO_REQUIRED: "info_request",
    OperatorActionType.ACCESS_REQUIRED: "access_request",
    OperatorActionType.SECRET_REQUIRED: "secret_request",
    OperatorActionType.DECISION_REQUIRED: "decision_request",
}


def operator_action_to_approval_payload(
    request: OperatorActionRequest,
    *,
    approval_kind_override: Optional[str] = None,
    summary: str = "",
    requested_action: str = "",
    created_by: str = "",
    source_channel_id: Optional[int] = None,
    source_thread_id: Optional[int] = None,
    source_message_id: Optional[int] = None,
) -> Mapping[str, Any]:
    """*request* 를 ``ApprovalRequest.to_payload()`` 가 기대하는 dict 으로 변환.

    queue 의 ``ApprovalWorker.enqueue`` 에 그대로 넘길 수 있는 모양이다.
    ``ApprovalRequest`` 자체를 import 하지 않고 dict shape 만 만든다 —
    operator_action 모듈은 job_queue 의존을 갖지 않는다.
    """

    kind = approval_kind_override or _REQUEST_TYPE_TO_APPROVAL_KIND.get(
        request.request_type
    )
    if not kind:
        kind = "operator_action"

    summary_value = summary or request.why_blocked
    requested = requested_action or request.expected_answer
    created = created_by or request.requested_by

    extra: dict[str, Any] = dict(request.extra or {})
    extra["operator_action"] = request.to_extra_payload()

    return {
        "session_id": request.session_id,
        "approval_kind": kind,
        "title": request.title or kind,
        "summary": summary_value,
        "requested_action": requested,
        "created_by": created,
        "source_channel_id": source_channel_id,
        "source_thread_id": source_thread_id,
        "source_message_id": source_message_id,
        "extra": extra,
    }


def operator_action_request_from_approval_payload(
    payload: Mapping[str, Any],
) -> Optional[OperatorActionRequest]:
    """``approval_post`` queue row 의 payload 에서
    :class:`OperatorActionRequest` 를 복원. 없으면 ``None``.

    reply router 가 어느 카드인지 식별한 뒤 해당 request 를 다시
    빌드해 reply 처리에 사용한다.
    """

    extra = (payload or {}).get("extra") or {}
    op_payload = extra.get("operator_action") if isinstance(extra, Mapping) else None
    if not isinstance(op_payload, Mapping):
        return None
    try:
        return OperatorActionRequest.from_extra_payload(op_payload)
    except Exception:  # noqa: BLE001
        return None


__all__ = (
    "EXTERNAL_FACT_KEYWORDS",
    "OperatorActionReply",
    "OperatorActionRequest",
    "OperatorActionType",
    "OperatorSessionState",
    "SECRET_AUTO_ALLOWED",
    "SECRET_AUTO_FORBIDDEN",
    "SESSION_EXTRA_ANSWERED_KEY",
    "SESSION_EXTRA_OPERATOR_STATE_KEY",
    "SESSION_EXTRA_PENDING_REQUESTS_KEY",
    "is_external_fact_required",
    "operator_action_request_from_approval_payload",
    "operator_action_to_approval_payload",
    "parse_operator_action_reply",
    "render_operator_action_card",
    "session_state_for_request_type",
    "stamp_answered_request",
    "stamp_pending_request",
)
