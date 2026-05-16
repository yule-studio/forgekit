"""engineering_conversation — intent classification + phrase matchers.

Single source of truth for "what is the user trying to do right now?"
The two public entry points:

- :func:`detect_engineering_intent` — input is a user message, output is
  an :class:`EngineeringIntentMatch` carrying one of the 13 intent IDs.
  Ordering matters: read-only intents (session count / list / blocked
  reason / continue / change direction / status diagnostic) win over
  confirmation, which wins over intake. A bug fix for "should never
  promote to new intake" lives here, not in the response formatter.
- :func:`split_task_branches` — splits a single message on Korean
  conjunctions (``그리고`` / ``또``) and English ``and`` so multi-prong
  asks can be surfaced as a split proposal.

Phrase tuples grouped by intent:

- :data:`_CONFIRMATION_PHRASES` + :data:`_CONFIRMATION_STANDALONE`
- :data:`_STATUS_DIAGNOSTIC_PHRASES`
- :data:`_SESSION_COUNT_PHRASES`, :data:`_SESSION_LIST_PHRASES`,
  :data:`_BLOCKED_REASON_PHRASES`, :data:`_CONTINUE_EXISTING_PHRASES`,
  :data:`_CHANGE_DIRECTION_PHRASES`
- :data:`_GENERAL_HELP_PHRASES`, :data:`_VAGUE_TOKEN_RUNS`

Matcher helpers: :func:`_is_confirmation`, :func:`_is_status_diagnostic`,
:func:`_is_session_count_query`, :func:`_is_session_list_query`,
:func:`_is_blocked_reason_query`, :func:`_is_continue_existing_work`,
:func:`_is_change_direction`, :func:`_asks_for_general_help`,
:func:`_looks_too_vague`, :func:`_asks_to_continue_existing_thread`,
:func:`_asks_to_start_new_thread`.

:func:`_looks_like_multiple_tasks` lives in ``.task_shaping``.
"""

from __future__ import annotations

import re

from .models import (
    BLOCKED_REASON_QUERY,
    CHANGE_DIRECTION,
    CONFIRM_INTAKE,
    CONTINUE_EXISTING_WORK,
    EngineeringIntentMatch,
    GENERAL_ENGINEERING_HELP,
    NEEDS_CLARIFICATION,
    SESSION_COUNT_QUERY,
    SESSION_LIST_QUERY,
    SPLIT_TASK_PROPOSAL,
    STATUS_DIAGNOSTIC,
    TASK_INTAKE_CANDIDATE,
)


# ---------------------------------------------------------------------------
# Public entry — intent dispatch
# ---------------------------------------------------------------------------


def detect_engineering_intent(message_text: str) -> EngineeringIntentMatch:
    """Map *message_text* to one of the five engineering intents.

    Order matters: confirmation phrases must short-circuit so that follow-up
    "이대로 진행" never mis-classifies as a new intake.
    """

    from .task_shaping import _looks_like_multiple_tasks

    normalized = _normalize(message_text)
    if not normalized:
        return EngineeringIntentMatch(
            intent_id=NEEDS_CLARIFICATION,
            label="비어 있는 메시지",
            confidence="high",
        )

    # P0-J (#146) — read-only intent precedence. session_count /
    # session_list / blocked_reason / continue_existing_work /
    # change_direction 은 STATUS_DIAGNOSTIC 보다 먼저 매칭해 절대
    # auto_collect 경로로 흘러가지 않게 한다.
    if _is_session_count_query(normalized):
        return EngineeringIntentMatch(
            intent_id=SESSION_COUNT_QUERY,
            label="세션 개수 질의",
            confidence="high",
        )
    if _is_session_list_query(normalized):
        return EngineeringIntentMatch(
            intent_id=SESSION_LIST_QUERY,
            label="세션 목록 질의",
            confidence="high",
        )
    if _is_blocked_reason_query(normalized):
        return EngineeringIntentMatch(
            intent_id=BLOCKED_REASON_QUERY,
            label="막힘 원인 질의",
            confidence="high",
        )
    if _is_change_direction(normalized):
        return EngineeringIntentMatch(
            intent_id=CHANGE_DIRECTION,
            label="방향 수정",
            confidence="high",
        )
    if _is_continue_existing_work(normalized):
        return EngineeringIntentMatch(
            intent_id=CONTINUE_EXISTING_WORK,
            label="기존 작업 이어가기",
            confidence="high",
        )

    # Status / diagnostic intent must be checked BEFORE confirmation.
    # "왜 안 됐어?", "운영 리서치는 안 열어?", "지금 뭐 하는 중?" must
    # NOT be promoted to a new intake or read as a go-ahead.
    if _is_status_diagnostic(normalized):
        return EngineeringIntentMatch(
            intent_id=STATUS_DIAGNOSTIC,
            label="상태 확인",
            confidence="high",
        )

    if _is_confirmation(normalized):
        return EngineeringIntentMatch(
            intent_id=CONFIRM_INTAKE,
            label="진행 확정",
            confidence="high",
        )

    if _asks_for_general_help(normalized):
        return EngineeringIntentMatch(
            intent_id=GENERAL_ENGINEERING_HELP,
            label="일반 안내",
            confidence="high",
        )

    if _looks_too_vague(normalized):
        return EngineeringIntentMatch(
            intent_id=NEEDS_CLARIFICATION,
            label="추가 정보 필요",
            confidence="medium",
        )

    if _looks_like_multiple_tasks(message_text):
        return EngineeringIntentMatch(
            intent_id=SPLIT_TASK_PROPOSAL,
            label="작업 분리 제안",
            confidence="medium",
        )

    return EngineeringIntentMatch(
        intent_id=TASK_INTAKE_CANDIDATE,
        label="작업 후보",
        confidence="medium",
    )


def split_task_branches(message_text: str) -> tuple[str, ...]:
    """Heuristic split — returns 2+ sub-prompts when the user combined asks.

    Splits on Korean conjunctions (``그리고``/``또``) and English ``and``
    when surrounded by spaces. Drops empty fragments and trims whitespace.
    """

    parts = re.split(_SPLIT_PATTERN, message_text)
    cleaned = tuple(part.strip(" ,.;:") for part in parts if part and part.strip(" ,.;:"))
    if len(cleaned) <= 1:
        return ()
    return cleaned


# ---------------------------------------------------------------------------
# Intent detection helpers
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


_CONFIRMATION_PHRASES = (
    "이대로 진행",
    "이대로 등록",
    # P0-N2 (live bug #4) — "그대로" 변형. routing.is_non_actionable_prompt
    # 는 이미 잡지만 intent_detection 은 못 잡아서 TASK_INTAKE_CANDIDATE
    # 로 떨어지고 새 세션을 만들었다. 이제 CONFIRM_INTAKE → P0-K 가드 →
    # APPROVAL_ACTION ack 경로로 흘러간다.
    "그대로 진행",
    "그대로 등록",
    "이걸로 등록",
    "이걸로 진행",
    "그럼 이걸로",
    "그럼 등록",
    "그럼 진행",
    "좋아 진행",
    "좋습니다 진행",
    "오케이 진행",
    "ok 진행",
    "새 작업으로 진행",
    "새 작업으로 시작",
    "그렇게 등록",
    "그렇게 진행",
    "진행해줘",
    "진행 해줘",
    "진행해 주세요",
    "등록해줘",
    "등록해 주세요",
    "yes 등록",
    "yes 진행",
    "go 진행",
    "확정",
    "확정해",
    # P0-K (#148) — 새 command-only 합성. CONFIRM_INTAKE → APPROVAL_ACTION
    # 다운그레이드 (build_engineering_conversation_response 의 가드) 로
    # 흘러가서 새 intake/research 절대 안 만듦.
    "작업 승인 할게",
    "작업 승인할게",
    "작업 승인",
    "승인 할게",
    "승인할게",
    "승인하고 진행해",
    "승인하고 진행",
    "승인해줘",
    "승인 해줘",
    "계속 해",
    "계속해",
    "계속 진행",
    "이어서 해",
    "이어서 진행",
)

_CONFIRMATION_STANDALONE = frozenset(
    {
        "ok",
        "okay",
        "오케이",
        "오케",
        "오키",
        "yes",
        "yep",
        "go",
        "고",
        "ㄱㄱ",
        "확정",
        "진행",
        "등록",
    }
)


def _is_confirmation(normalized: str) -> bool:
    if normalized in _CONFIRMATION_STANDALONE:
        return True
    return any(phrase in normalized for phrase in _CONFIRMATION_PHRASES)


def _asks_to_continue_existing_thread(*texts: str) -> bool:
    normalized = " ".join(
        " ".join(str(text or "").lower().split()) for text in texts
    )
    return any(
        signal in normalized
        for signal in (
            "새로 등록하지 말고",
            "새로 만들지 말고",
            "새 스레드 만들지",
            "새 thread 만들지",
            "기존 스레드",
            "기존 thread",
            "열려 있는 스레드",
            "열려있는 스레드",
            "열려 있는 thread",
            "열려있는 thread",
            "이어가",
            "이어 가",
            "이어서",
            "reuse thread",
            "same thread",
        )
    )


def _asks_to_start_new_thread(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    return any(
        signal in normalized
        for signal in (
            "새 작업으로 진행",
            "새 작업으로 시작",
            "새로 등록",
            "새 스레드로",
            "새 thread로",
            "새 세션으로",
            "new thread",
            "new session",
        )
    )


_STATUS_DIAGNOSTIC_PHRASES = (
    "멤버 봇",
    "멤버봇",
    "역할 봇",
    "역할봇",
    "member bot",
    "member-bot",
    "운영 리서치는 안 열",
    "운영-리서치는 안 열",
    "운영 리서치 안 열",
    "운영-리서치 안 열",
    "운영 리서치는 왜",
    "운영-리서치는 왜",
    "운영 리서치 왜",
    "운영-리서치 왜",
    "운영 리서치 왜 안 열",
    "운영-리서치 왜 안 열",
    "리서치 왜 실패",
    "리서치 왜 안",
    "리서치는 왜 안",
    "리서치 어떻게 됐",
    "왜 안 됐",
    "왜 안됐",
    "왜 멈췄",
    "왜 멈춰",
    "왜 막혔",
    "왜 막혀",
    "뭐가 막혔",
    "어디서 막혔",
    "왜 안 열",
    "왜 안열",
    "왜 안 열렸",
    "왜 안열렸",
    "왜 못",
    "왜 실패",
    "지금 뭐 하",
    "지금 뭐하",
    "지금 무엇",
    "지금 어떤 상태",
    "지금 어디까지",
    "현재 상태",
    "현재 진행",
    "상태 알려",
    "상태 알려줘",
    "상태 좀 알려",
    "상태 확인",
    "상태 체크",
    "다시 확인해",
    "다시 한번 확인",
    "다시 한 번 확인",
    "진행 상황",
    "진행상황",
    "진행 어디",
    "진행 어떻게",
    # P0-N1 (live bug #2) — yes/no shaped progress check phrases.
    # "작업 진행하고 있는 거야?" / "지금 진행 중이야?" / "잘 돌아가고
    # 있어?" 등은 "지금 뭐 하는 중" 의 변종이라 STATUS_DIAGNOSTIC 로
    # 잡아야 한다. 이전엔 "진행 상황"/"진행 어디" 만 잡혀서 yes/no
    # 형태가 TASK_INTAKE_CANDIDATE 로 떨어지고 새 세션을 만들었다.
    "진행하고 있",
    "진행 하고 있",
    "진행 중이",
    "진행중이",
    "진행 중인지",
    "진행중인지",
    "돌아가고 있",
    "돌아가는 중",
    "돌아가는중",
    "잘 되고 있",
    "잘 되어 있",
    "잘 되고있",
    "어디까지 됐",
    "어디까지 갔",
    "어디까지 진행",
    "어떻게 됐",
    "어떻게 되어",
    "어떻게 되고",
    "어떻게 진행",
    "obsidian 왜 안",
    "obsidian 왜 못",
    "obsidian 안 들어갔",
    "obsidian 안들어갔",
    "옵시디언 왜 안",
    "옵시디언 안 들어갔",
    "forum 왜 안",
    "forum 안 열",
    "포럼 왜 안",
    "포럼 안 열",
    "what's the status",
    "what is the status",
    "what happened",
    "where are we",
    "why did it fail",
    "why didn't it",
    "status check",
    "status update",
    "progress check",
)


def _is_status_diagnostic(normalized: str) -> bool:
    """Check whether the user is asking about state, not filing work.

    Triggers on Korean and English diagnostic phrasings — "왜 안 됐어",
    "지금 뭐 하는 중", "Obsidian 왜 안 들어갔", "운영-리서치는 안 열어",
    etc. Important: a question mark alone is not enough — actual new
    work requests can also end with "?". We require an explicit status
    phrase so casual "이거 어때?" doesn't fall through.
    """

    if not normalized:
        return False
    return any(phrase in normalized for phrase in _STATUS_DIAGNOSTIC_PHRASES)


# ---------------------------------------------------------------------------
# P0-J (#146) read-only intent matchers
# ---------------------------------------------------------------------------


_SESSION_COUNT_PHRASES = (
    "세션 몇 개",
    "세션 몇개",
    "세션 작업들 몇",
    "세션들 몇",
    "열린 작업 몇 개",
    "열린 작업 몇개",
    "열린 작업들 몇",
    "오픈 세션 몇",
    "open session count",
    "how many open sessions",
    "현재 세션 수",
    "활성 세션 수",
    "진행 중인 세션 몇",
    "진행 중인 세션이 몇",
)

_SESSION_LIST_PHRASES = (
    "세션 목록",
    "세션 리스트",
    "오픈 세션 뭐",
    "오픈 세션 뭐뭐",
    "열린 세션 목록",
    "열린 작업 목록",
    "진행 중인 세션 목록",
    "open session list",
    "list open sessions",
    "세션 다 보여줘",
    "열린 작업 보여줘",
)

_BLOCKED_REASON_PHRASES = (
    "왜 멈췄",
    "왜 멈춰",
    "뭐가 막혔",
    "뭐가 막혀",
    "왜 안 됐",
    "왜 안돼",
    "왜 안 돼",
    "왜 안되",
    "왜 막혔",
    "왜 막혀",
    "어디서 막혔",
    "stuck",
    "blocked",
    "blocking",
    "왜 진행 안",
    "왜 진척 안",
)

_CONTINUE_EXISTING_PHRASES = (
    "이전 작업 이어",
    "이어서 해",
    "이어서 진행",
    "이어서 작업",
    "그 세션 계속",
    "그 작업 계속",
    "계속 진행해",
    "continue existing",
    "resume session",
    "기존 세션 이어",
    "그 세션 이어",
    "원래 작업 이어",
)

_CHANGE_DIRECTION_PHRASES = (
    "방향 수정",
    "방향 바꿔",
    "방향 변경",
    "직진 말고",
    "리서치 말고 구현",
    "검색 말고",
    "조사 말고 구현",
    "자료 추가가 아니라 방향",
    "자료 추가 아니라 방향",
    "그쪽 말고",
    "방향 틀어",
    "redirect",
    "change direction",
    "pivot",
    "다른 쪽으로",
)


def _is_session_count_query(normalized: str) -> bool:
    return any(phrase in normalized for phrase in _SESSION_COUNT_PHRASES)


def _is_session_list_query(normalized: str) -> bool:
    return any(phrase in normalized for phrase in _SESSION_LIST_PHRASES)


def _is_blocked_reason_query(normalized: str) -> bool:
    return any(phrase in normalized for phrase in _BLOCKED_REASON_PHRASES)


def _is_continue_existing_work(normalized: str) -> bool:
    return any(phrase in normalized for phrase in _CONTINUE_EXISTING_PHRASES)


def _is_change_direction(normalized: str) -> bool:
    return any(phrase in normalized for phrase in _CHANGE_DIRECTION_PHRASES)


_GENERAL_HELP_PHRASES = (
    "engineering-agent",
    "엔지니어링 에이전트",
    "엔지니어링 봇",
    "어떻게 쓰",
    "어떻게 써",
    "어떻게 사용",
    "기능 알려",
    "도움말",
    "도와줄 수 있",
    "help",
    "what can you do",
    "사용법",
    "사용법 알려",
    "쓰는 법",
    "쓰는법",
    "뭐 할 수 있",
    "뭘 할 수 있",
    "뭐 해 줄 수 있",
    "뭐 해줄 수 있",
    "어떤 명령",
    "어떤 기능",
    "명령어 알려",
    "command list",
    "available commands",
    "intake 어떻게",
    "intake 가 뭐",
    "intake가 뭐",
)


_GENERAL_HELP_STANDALONE = frozenset(
    {
        "help",
        "?help",
        "/help",
        "도움말",
        "도움",
        "사용법",
        "헬프",
        "h",
    }
)


def _asks_for_general_help(normalized: str) -> bool:
    if normalized in _GENERAL_HELP_STANDALONE:
        return True
    return any(phrase in normalized for phrase in _GENERAL_HELP_PHRASES)


_VAGUE_TOKEN_RUNS = (
    "도와줘",
    "도와 줘",
    "할 일 있어",
    "할일 있어",
    "작업 있어",
    "뭐 해야",
    "뭐해야",
    "할 거",
    "할거",
)


def _looks_too_vague(normalized: str) -> bool:
    if len(normalized) <= 3:
        return True
    word_count = len(normalized.split())
    if word_count == 1:
        return True
    if word_count <= 3 and any(token in normalized for token in _VAGUE_TOKEN_RUNS):
        return True
    return False


_SPLIT_PATTERN = re.compile(r"\s*그리고\s+|\s*,\s*그리고\s+|\s*또\s+|\s+and\s+", re.IGNORECASE)


__all__ = (
    "detect_engineering_intent",
    "split_task_branches",
    "_normalize",
    "_is_confirmation",
    "_is_status_diagnostic",
    "_is_session_count_query",
    "_is_session_list_query",
    "_is_blocked_reason_query",
    "_is_continue_existing_work",
    "_is_change_direction",
    "_asks_for_general_help",
    "_looks_too_vague",
    "_asks_to_continue_existing_thread",
    "_asks_to_start_new_thread",
    "_CONFIRMATION_PHRASES",
    "_CONFIRMATION_STANDALONE",
    "_STATUS_DIAGNOSTIC_PHRASES",
    "_SESSION_COUNT_PHRASES",
    "_SESSION_LIST_PHRASES",
    "_BLOCKED_REASON_PHRASES",
    "_CONTINUE_EXISTING_PHRASES",
    "_CHANGE_DIRECTION_PHRASES",
    "_GENERAL_HELP_PHRASES",
    "_GENERAL_HELP_STANDALONE",
    "_VAGUE_TOKEN_RUNS",
    "_SPLIT_PATTERN",
)
