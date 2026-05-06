"""Engineering-agent free-form conversation layer.

This module is the **conversational front door** for the engineering-agent
gateway in the ``#업무-접수`` channel. It receives a user's natural-language
message and returns a structured :class:`EngineeringConversationResponse`
that downstream code (bot.py, commands.py, future dispatcher) consumes to
decide whether to:

- reply only (general help / clarification questions),
- propose a task split before intake,
- or actually call ``workflow.intake`` because the user confirmed.

It deliberately does **not** import :mod:`workflow` or the dispatcher so it
can be exercised in unit tests without DB/Discord dependencies. The bot
layer is responsible for translating ``ready_to_intake`` into the actual
``workflow.intake`` call.

How this differs from ``discord/conversation.py`` (planning-agent):

- planning conversation is *snapshot-bound* — it leans on
  ``DailyPlanSnapshot`` and answers deterministic queries about the day.
- engineering conversation is *task-shaping* — it interprets a free-form
  request, asks for missing context, suggests breaking down multi-prong
  asks, and only commits to a session once the user explicitly says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional, Sequence
from urllib.parse import urlparse

from ..agents.dispatcher import TaskType
from ..agents.research_pack import (
    ResearchAttachment,
    ResearchPack,
    ResearchSource,
    extract_urls,
)
from ..agents.session_status import (
    diagnose_session,
    render_member_bot_summary,
)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


GENERAL_ENGINEERING_HELP = "general_engineering_help"
TASK_INTAKE_CANDIDATE = "task_intake_candidate"
NEEDS_CLARIFICATION = "needs_clarification"
CONFIRM_INTAKE = "confirm_intake"
SPLIT_TASK_PROPOSAL = "split_task_proposal"
STATUS_DIAGNOSTIC = "status_diagnostic"


@dataclass(frozen=True)
class EngineeringIntentMatch:
    """What the user seems to want from engineering-agent right now."""

    intent_id: str
    label: str
    confidence: str = "medium"  # "high" / "medium" / "low"


@dataclass(frozen=True)
class EngineeringConversationResponse:
    """Envelope returned by :func:`build_engineering_conversation_response`.

    Downstream Discord layer reads:

    - ``ready_to_intake=True`` → call ``workflow.intake`` with the
      preserved ``intake_prompt``.
    - ``needs_clarification=True`` → reply with ``content`` and wait for
      another user turn.
    - ``proposed_splits`` non-empty → reply with split proposal; user picks
      one or types a confirmation phrase to proceed with the original ask.
    - ``research_pack`` set → autonomous collector returned ≥1 result.
      Forum publisher / deliberation should consume this pack instead of
      asking the user for more material.
    - ``collection_outcome`` carries the raw ``CollectionOutcome`` (mode,
      collector_name, query, count) so the Discord wiring can post the
      ``format_collection_summary`` block to the research forum.
    """

    content: str
    intent_id: str
    ready_to_intake: bool = False
    needs_clarification: bool = False
    proposed_splits: Sequence[str] = field(default_factory=tuple)
    suggested_task_type: Optional[str] = None
    write_likely: bool = False
    intake_prompt: Optional[str] = None
    mention_user_id: Optional[int] = None
    research_pack: Optional[Any] = None
    collection_outcome: Optional[Any] = None
    # When True the gateway must NOT auto-collect, NOT create a new
    # session, NOT ask for confirmation. The user is asking what's
    # currently happening, not requesting new work — the response
    # already describes the existing state.
    is_status_query: bool = False


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def build_engineering_conversation_response(
    message_text: str,
    *,
    author_user_id: Optional[int] = None,
    mention_user: bool = False,
    last_proposed_prompt: Optional[str] = None,
    auto_collect: bool = True,
    user_links: Sequence[str] = (),
    user_attachments: Sequence[Any] = (),
    role_for_research: str = "engineering-agent/tech-lead",
    session_id: Optional[str] = None,
    collector_config: Optional[Any] = None,
    collector: Optional[Any] = None,
    status_session_loader: Optional[Any] = None,
) -> EngineeringConversationResponse:
    """Classify *message_text* and produce an actionable response envelope.

    *last_proposed_prompt* lets the caller stash the most recent task-shaped
    message so a follow-up confirmation ("이대로 진행해") can reuse it as
    ``intake_prompt`` instead of the literal confirmation string. The bot
    layer is expected to pass this in from its per-channel state.

    *auto_collect* controls the research collector wire-up. When True (and
    the message has substantive content), the conversation layer first
    calls ``auto_collect_or_request_more_input`` so the gateway can answer
    "1차 자료를 수집했습니다" with a populated ``ResearchPack`` instead of
    immediately asking the user for links. Pass *user_links* /
    *user_attachments* whenever the inbound Discord message already
    carries them so the collector can short-circuit to ``USER_PROVIDED``.

    *collector_config* / *collector* are injection seams for tests and
    let alternate environments swap providers without touching env vars.
    """

    intent = detect_engineering_intent(message_text)
    mention_user_id = author_user_id if mention_user else None

    # Bot-echo guard — when the user pastes one of the gateway's own
    # template lines back into the channel, the live MVP loop fires:
    # gateway treats the paste as a fresh research request, runs
    # auto_collect, asks for confirmation, repeats. Catch it before
    # any branch (status / confirm / split / intake) can act on the
    # echoed text. Status questions are exempt because the diagnostic
    # responder reads existing state, not the message body.
    try:
        from ..agents.routing import is_bot_echo_phrase as _is_bot_echo
    except Exception:  # noqa: BLE001 - never fail conversation on import wiring
        _is_bot_echo = None
    if (
        _is_bot_echo is not None
        and intent.intent_id != STATUS_DIAGNOSTIC
        and _is_bot_echo(message_text)
    ):
        body = (
            "방금 받은 메시지가 gateway가 보낸 안내문 문구와 똑같아서 "
            "새 작업으로 등록하지 않았어요.\n"
            "진행할 업무 원문을 다시 알려주세요."
        )
        return EngineeringConversationResponse(
            content=_prepend_mention(body, mention_user_id),
            intent_id=NEEDS_CLARIFICATION,
            needs_clarification=True,
            mention_user_id=mention_user_id,
        )

    if intent.intent_id == STATUS_DIAGNOSTIC:
        # User is asking what's going on with the existing work, not
        # filing a new task. Read the latest open session via the
        # injected loader (bot.py wires find_latest_open_session) and
        # describe its real state. We never trigger auto_collect or
        # intake here.
        #
        # The loader is allowed to take ``message_text`` as a kwarg so
        # explicit "세션 a8d1707808ac" hints in the message body are
        # preferred over the channel/user lookup.
        session = None
        if callable(status_session_loader):
            try:
                try:
                    session = status_session_loader(message_text=message_text)
                except TypeError:
                    session = status_session_loader()
            except Exception:  # noqa: BLE001 - loader failures must not crash gateway
                session = None
        is_member_bot_question = _asks_about_member_bots(message_text)
        body = format_status_diagnostic_response(
            session,
            is_member_bot_question=is_member_bot_question,
        )
        return EngineeringConversationResponse(
            content=_prepend_mention(body, mention_user_id),
            intent_id=STATUS_DIAGNOSTIC,
            mention_user_id=mention_user_id,
            is_status_query=True,
        )

    if intent.intent_id == CONFIRM_INTAKE:
        intake_prompt = last_proposed_prompt or message_text
        suggested = _suggest_task_type(intake_prompt)
        write_likely = _looks_like_write_request(intake_prompt)
        if (
            _asks_to_continue_existing_thread(intake_prompt, message_text)
            and not _asks_to_start_new_thread(message_text)
        ):
            body = (
                "좋습니다. 새 작업으로 등록하지 않고, 열려 있는 thread를 찾아 이어갈게요.\n"
                "찾아낸 세션 ID와 이어갈 위치를 바로 안내드리겠습니다."
            )
        else:
            body = (
                "좋습니다. 이대로 작업을 등록할게요.\n"
                "intake가 만들어지면 세션 ID와 승인 안내를 이어서 드릴게요."
            )
        return EngineeringConversationResponse(
            content=_prepend_mention(body, mention_user_id),
            intent_id=CONFIRM_INTAKE,
            ready_to_intake=True,
            suggested_task_type=suggested,
            write_likely=write_likely,
            intake_prompt=intake_prompt,
            mention_user_id=mention_user_id,
        )

    if intent.intent_id == GENERAL_ENGINEERING_HELP:
        body = _format_general_help()
        return EngineeringConversationResponse(
            content=_prepend_mention(body, mention_user_id),
            intent_id=GENERAL_ENGINEERING_HELP,
            mention_user_id=mention_user_id,
        )

    if intent.intent_id == NEEDS_CLARIFICATION:
        body = _format_clarification_question(message_text)
        return EngineeringConversationResponse(
            content=_prepend_mention(body, mention_user_id),
            intent_id=NEEDS_CLARIFICATION,
            needs_clarification=True,
            mention_user_id=mention_user_id,
        )

    suggested = _suggest_task_type(message_text)
    write_likely = _looks_like_write_request(message_text)
    collection = _maybe_run_auto_collect(
        message_text=message_text,
        suggested_task_type=suggested,
        auto_collect=auto_collect,
        user_links=user_links,
        user_attachments=user_attachments,
        role_for_research=role_for_research,
        session_id=session_id,
        collector_config=collector_config,
        collector=collector,
    )

    if intent.intent_id == SPLIT_TASK_PROPOSAL:
        splits = split_task_branches(message_text)
        body = _format_split_proposal(splits)
        if collection is not None:
            body = body + "\n\n" + _format_collection_announcement(collection)
        return EngineeringConversationResponse(
            content=_prepend_mention(body, mention_user_id),
            intent_id=SPLIT_TASK_PROPOSAL,
            proposed_splits=tuple(splits),
            suggested_task_type=suggested,
            write_likely=write_likely,
            intake_prompt=message_text,
            mention_user_id=mention_user_id,
            research_pack=getattr(collection, "pack", None),
            collection_outcome=collection,
        )

    # default: TASK_INTAKE_CANDIDATE
    if collection is not None:
        body = _format_intake_with_collection(
            message_text=message_text,
            suggested_task_type=suggested,
            write_likely=write_likely,
            collection=collection,
        )
    else:
        body = _format_intake_candidate_question(
            message_text=message_text,
            suggested_task_type=suggested,
            write_likely=write_likely,
        )
    return EngineeringConversationResponse(
        content=_prepend_mention(body, mention_user_id),
        intent_id=TASK_INTAKE_CANDIDATE,
        suggested_task_type=suggested,
        write_likely=write_likely,
        intake_prompt=message_text,
        mention_user_id=mention_user_id,
        research_pack=getattr(collection, "pack", None),
        collection_outcome=collection,
    )


def _format_intake_with_collection(
    *,
    message_text: str,
    suggested_task_type: Optional[str],
    write_likely: bool,
    collection: Any,
) -> str:
    """Unified intake response when the auto-collector ran.

    Output structure (matches the team-lead voice spec):

    1. Greeting that names what we're doing.
    2. Understanding paragraph echoing a short topic + classification.
    3. Action paragraph describing what just happened or what's next.
    4. (auto_collected / user_provided only) compact meta tail.
    5. Confirmation prompt — except in NEEDS_USER_INPUT where we wait
       for the user's reply instead of asking them to confirm.
    """

    mode = getattr(collection, "mode", None)
    mode_value = getattr(mode, "value", str(mode))
    topic = _summarize_topic(message_text)

    paragraphs: list[str] = []

    # 1. greeting
    if mode_value == "auto_collected":
        paragraphs.append("좋아요. 먼저 1차 자료를 모아볼게요.")
    elif mode_value == "user_provided":
        paragraphs.append("받았어요. 보내주신 자료를 1순위로 두고 시작할게요.")
    elif mode_value == "needs_user_input":
        paragraphs.append("받았어요. 다만 더 정확하게 도와드리려면 자료가 조금 더 필요해요.")
    else:
        paragraphs.append("작업 내용을 받았어요.")

    # 2. understanding
    understand = [f"이번 요청은 “{topic}”으로 이해했어요."]
    if write_likely:
        understand.append(
            "코드나 문서 쓰기가 동반되는 작업으로 보여서, 진행 전에 한 번 확인할게요."
        )
    elif suggested_task_type:
        understand.append(
            f"분석·검토 위주의 {_pretty_task_type(suggested_task_type)} 작업으로 이해하고 있습니다."
        )
    paragraphs.append("\n".join(understand))

    # 3. action — depends on mode
    count = getattr(collection, "auto_collected_count", 0) or 0
    if mode_value == "auto_collected":
        paragraphs.append(
            f"방금 {count}개의 참고 자료 후보를 수집했어요.\n"
            "이 자료들은 운영-리서치에 정리해두고, 이어서 각 역할이 자기 관점으로 검토하게 할게요."
        )
    elif mode_value == "user_provided":
        paragraphs.append(
            "보내주신 자료로 바로 검토를 시작하고, 정리된 결과는 운영-리서치에 함께 남길게요."
        )
    elif mode_value == "needs_user_input":
        prompt = getattr(collection, "user_prompt", None) or (
            "관련 자료를 한두 개 붙여 주시면 더 정확하게 도와드릴 수 있어요."
        )
        paragraphs.append(
            "자동 수집이 비어 있어서, 자료를 한 번 같이 보고 가는 게 좋겠어요.\n"
            f"{prompt}"
        )

    # 4. meta tail (auto_collected / user_provided only)
    if mode_value in ("auto_collected", "user_provided"):
        paragraphs.append(_format_collection_meta_block(collection))

    # 5. confirm — skip when we're waiting for more user input
    if mode_value != "needs_user_input":
        paragraphs.append(
            "맞으면 `이대로 진행`이라고 답해 주세요. 빠진 부분이 있으면 추가로 알려주셔도 좋아요."
        )

    return "\n\n".join(paragraphs)


def _maybe_run_auto_collect(
    *,
    message_text: str,
    suggested_task_type: Optional[str],
    auto_collect: bool,
    user_links: Sequence[str],
    user_attachments: Sequence[Any],
    role_for_research: str,
    session_id: Optional[str],
    collector_config: Optional[Any],
    collector: Optional[Any],
):
    """Run the autonomous collector and return its outcome (or None).

    Returns ``None`` when:
    - ``auto_collect`` is False, or
    - the message text is too short / blank to query usefully, or
    - importing the collector module fails (defensive).

    Otherwise returns a ``CollectionOutcome``. The caller decides how to
    splice it into the response body.
    """

    if not auto_collect:
        return None
    if not (message_text or "").strip():
        return None
    # Bot-echo / command-only guard — the gateway's own template lines
    # ("좋습니다. 이대로 작업을 등록할게요…" / "자료가 부족합니다…")
    # and bare confirm phrases ("새 작업으로 진행" / "이대로 진행")
    # must never be queried as fresh research material. Without this
    # guard the live MVP loop fires: user pastes the bot's own line
    # back, gateway auto-collects 11 sources, gateway then asks for
    # confirmation, user replies with another command-only phrase,
    # repeat. See ``routing.is_non_actionable_prompt`` for the
    # canonical predicate.
    try:
        from ..agents.routing import is_non_actionable_prompt as _is_blocked
    except Exception:  # noqa: BLE001 - never block conversation on import wiring
        _is_blocked = None
    if _is_blocked is not None and _is_blocked(message_text):
        return None
    try:
        from ..agents.research_collector import (
            CollectorConfig as _CollectorConfig,
            auto_collect_or_request_more_input,
        )
    except Exception:  # noqa: BLE001 - never block conversation on collector wiring
        return None

    cfg = collector_config
    if cfg is None:
        try:
            cfg = _CollectorConfig.from_env()
        except Exception:  # noqa: BLE001
            return None

    try:
        return auto_collect_or_request_more_input(
            role=role_for_research,
            prompt=message_text,
            task_type=suggested_task_type,
            user_links=user_links,
            user_attachments=user_attachments,
            session_id=session_id,
            config=cfg,
            collector=collector,
        )
    except Exception:  # noqa: BLE001
        return None


def _format_collection_announcement(collection: Any) -> str:
    """Conversational paragraph(s) added when auto-collection ran.

    Tone follows the team-lead voice: 1) what we just did, 2) what's
    next. Internal jargon (collector / query / forum / deliberation) is
    rephrased — collector → 수집 방식, forum → 운영-리서치, deliberation →
    역할별 검토.

    Three modes:
    - AUTO_COLLECTED → "방금 N개의 참고 자료 후보를 수집했어요. ..." + meta
    - USER_PROVIDED → "보내주신 자료를 1순위로 두고 검토할게요." + meta
    - NEEDS_USER_INPUT → 사용자에게 자료 요청 (collector가 빈 결과)
    """

    mode = getattr(collection, "mode", None)
    mode_value = getattr(mode, "value", str(mode))

    if mode_value == "auto_collected":
        count = getattr(collection, "auto_collected_count", 0) or 0
        body = (
            f"먼저 1차 자료를 모아 봤어요. 방금 {count}개의 참고 자료 후보를 찾았습니다.\n"
            "이 자료들은 운영-리서치에 정리해두고, 이어서 각 역할이 자기 관점으로 검토하게 할게요."
        )
        return body + "\n\n" + _format_collection_meta_block(collection)

    if mode_value == "user_provided":
        body = (
            "사용자 제공 자료를 1순위로 두고 검토를 시작할게요.\n"
            "정리한 결과는 운영-리서치에 함께 남길 예정이에요."
        )
        return body + "\n\n" + _format_collection_meta_block(collection)

    if mode_value == "needs_user_input":
        prompt = getattr(collection, "user_prompt", None) or (
            "관련 자료를 한두 개 붙여 주시면 더 정확하게 도와드릴 수 있어요."
        )
        return (
            "자동 수집이 비어 있어, 자료를 한 번 같이 보고 가는 게 좋겠어요.\n"
            f"{prompt}"
        )

    return ""


def _format_collection_meta_block(collection: Any) -> str:
    """Compact key-value tail used under the collection announcement.

    Format:
        수집 정보:
        - 수집 방식: 기본 검색(mock)
        - 수집 자료: N건
        - 다음 단계: 역할별 검토
    """

    count = getattr(collection, "auto_collected_count", 0) or 0
    name = getattr(collection, "collector_name", "?")
    return (
        "수집 정보:\n"
        f"- 수집 방식: {_pretty_provider(name)}\n"
        f"- 수집 자료: {count}건\n"
        "- 다음 단계: 역할별 검토"
    )


def detect_engineering_intent(message_text: str) -> EngineeringIntentMatch:
    """Map *message_text* to one of the five engineering intents.

    Order matters: confirmation phrases must short-circuit so that follow-up
    "이대로 진행" never mis-classifies as a new intake.
    """

    normalized = _normalize(message_text)
    if not normalized:
        return EngineeringIntentMatch(
            intent_id=NEEDS_CLARIFICATION,
            label="비어 있는 메시지",
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
    "진행해 주세요",
    "등록해줘",
    "등록해 주세요",
    "yes 등록",
    "yes 진행",
    "go 진행",
    "확정",
    "확정해",
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


_GENERAL_HELP_PHRASES = (
    "engineering-agent",
    "엔지니어링 에이전트",
    "엔지니어링 봇",
    "어떻게 쓰",
    "어떻게 써",
    "어떻게 사용",
    "기능 알려",
    "도움말",
    "help",
    "what can you do",
    "사용법",
    "뭐 할 수 있",
)


def _asks_for_general_help(normalized: str) -> bool:
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


def _looks_like_multiple_tasks(message_text: str) -> bool:
    branches = split_task_branches(message_text)
    if len(branches) < 2:
        return False
    # Require each fragment to look "task-like" (>=2 words). Otherwise we
    # mis-fire on "음 그리고 좋아".
    return all(len(part.split()) >= 2 for part in branches)


def _looks_like_write_request(message_text: str) -> bool:
    normalized = _normalize(message_text)
    write_signals = (
        "구현",
        "만들",
        "추가",
        "수정",
        "고쳐",
        "고치",
        "리팩",
        "refactor",
        "implement",
        "build",
        "create",
        "fix",
        "패치",
        "patch",
        "PR",
        "pull request",
        "draft",
        "짜야",
        "짜줘",
        "짜자",
        "작성",
        "쓸게",
        "써줘",
    )
    review_signals = ("어떻게 생각", "분석", "리뷰", "review", "검토", "조사")
    if any(signal.lower() in normalized for signal in review_signals):
        return False
    return any(signal.lower() in normalized for signal in write_signals)


# ---------------------------------------------------------------------------
# task_type hint
# ---------------------------------------------------------------------------


_TASK_TYPE_KEYWORDS: tuple[tuple[TaskType, tuple[str, ...]], ...] = (
    (
        TaskType.VISUAL_POLISH,
        ("visual ", "polish", "리디자인", "redesign", "시각 정리", "visual cleanup"),
    ),
    (
        TaskType.ONBOARDING_FLOW,
        ("onboarding", "온보딩", "signup flow", "가입 흐름", "first-run"),
    ),
    (
        TaskType.EMAIL_CAMPAIGN,
        ("email", "이메일", "campaign", "캠페인", "광고", "ad creative"),
    ),
    (TaskType.LANDING_PAGE, ("landing", "랜딩", "marketing page", "히어로")),
    (TaskType.QA_TEST, ("regression", "회귀", "qa", "test plan", "테스트 시나리오")),
    (
        TaskType.PLATFORM_INFRA,
        ("infra", "deploy", "ci ", " ci", "docker", "k8s", "terraform", "github action"),
    ),
    (
        TaskType.FRONTEND_FEATURE,
        ("frontend", "ui ", "component", "컴포넌트", "react", "next.js", "vue"),
    ),
    (
        TaskType.BACKEND_FEATURE,
        ("backend", "api ", "schema", "database", "migration", "도메인", "service layer"),
    ),
)


def _suggest_task_type(message_text: str) -> Optional[str]:
    normalized = _normalize(message_text)
    for task_type, keywords in _TASK_TYPE_KEYWORDS:
        for keyword in keywords:
            if keyword in normalized:
                return task_type.value
    return None


# ---------------------------------------------------------------------------
# Response body formatters
# ---------------------------------------------------------------------------


def _format_general_help() -> str:
    return (
        "engineering-agent예요. 받은 요청을 정리해서 멤버들과 함께 다음 단계로 이어갑니다.\n\n"
        "이렇게 말씀해 주시면 도움이 됩니다:\n"
        "- 무엇을 만들거나 고치고 싶은지 한두 문장으로 설명\n"
        "- 참고할 화면이나 링크가 있으면 함께 붙여 주세요\n"
        "- 갈래가 여러 개면 한 번에 적어 주셔도 좋아요. 제가 나눠 제안해 드릴게요.\n\n"
        "확정할 때는 `이대로 진행`이라고 답해 주시면 그 다음 단계로 넘어갑니다."
    )


def _coerce_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _format_coding_status_line(
    proposal_payload: Any,
    job_payload: Any,
) -> Optional[str]:
    """Render a single ``- coding_job: ...`` line for the diagnostic.

    Coding job (approved) wins over a still-pending proposal. Empty
    extras yield ``None`` so the caller skips the line entirely.
    """

    if isinstance(job_payload, Mapping) and job_payload:
        status = job_payload.get("status") or "ready"
        executor = job_payload.get("executor_role") or "unknown"
        scope = job_payload.get("write_scope") or ()
        if isinstance(scope, (list, tuple)) and scope:
            preview = ", ".join(str(s) for s in tuple(scope)[:2])
            if len(scope) > 2:
                preview += " 외"
            return (
                f"- coding_job: {status} (executor=`{executor}`, write_scope={preview})"
            )
        return f"- coding_job: {status} (executor=`{executor}`)"
    if isinstance(proposal_payload, Mapping) and proposal_payload:
        executor = proposal_payload.get("executor_role") or "unknown"
        return (
            f"- coding_job: pending-approval (executor=`{executor}`) — "
            "사용자 `수정 승인` 대기"
        )
    return None


def format_status_diagnostic_response(
    session: Optional[Any],
    *,
    is_member_bot_question: bool = False,
) -> str:
    """Render a real-state status answer for the gateway.

    Reads ``session.state``, ``session.extra``, and known keys
    (``research_pack``, ``forum_thread_id``/``research_forum_thread_id``,
    ``research_loop_report``, ``forum_publish_error``) so the gateway
    can say "research_pack: 있음 · forum: 게시 실패 · 마지막 오류: 4000자
    초과" instead of guessing. When *session* is None we explicitly tell
    the operator we couldn't find an open session.

    *is_member_bot_question* tilts the answer toward "멤버 봇들은 뭐 하고
    있어?" — we still print the full state header but append a short
    member-bot focused section pointing at the forum thread instead of
    duplicating role comments here.
    """

    if session is None:
        return (
            "현재 채널/스레드에 매칭되는 열린 engineering-agent 세션이 보이지 않아요.\n"
            "확인하려는 작업의 session id를 알려 주시거나, "
            "이어갈 thread 안에서 다시 말씀해 주세요."
        )

    extra = dict(getattr(session, "extra", {}) or {})
    research_pack = extra.get("research_pack")
    forum_thread_id = (
        extra.get("research_forum_thread_id")
        or extra.get("forum_thread_id")
    )
    forum_thread_url = extra.get("forum_thread_url") or extra.get(
        "research_forum_thread_url"
    )
    forum_publish_error = (
        extra.get("forum_publish_error")
        or extra.get("research_forum_error")
    )
    research_loop_report = extra.get("research_loop_report")
    synthesis = extra.get("research_synthesis")
    forum_comment_mode = extra.get("forum_comment_mode")
    forum_kickoff_posted = extra.get("forum_kickoff_posted")
    forum_kickoff_error = extra.get("forum_kickoff_error")
    coding_proposal_payload = extra.get("coding_proposal")
    coding_job_payload = extra.get("coding_job")
    canonical_prompt_override = _coerce_str(extra.get("canonical_prompt_override"))
    latest_continuation_prompt = _coerce_str(
        extra.get("latest_continuation_prompt")
    )
    resumed_thread_id = extra.get("resumed_thread_id")

    state_value = getattr(session, "state", None)
    state_label = getattr(state_value, "value", state_value) or "unknown"
    session_id = getattr(session, "session_id", None) or "unknown"
    task_type = getattr(session, "task_type", None) or "unknown"

    lines = [
        "현재 engineering-agent 세션 상태를 확인했어요.",
        "",
        f"- 세션: `{session_id}`",
        f"- 상태: {state_label}",
        f"- 종류: {task_type}",
        f"- research_pack: {'있음' if research_pack else '없음'}",
    ]

    coding_status_line = _format_coding_status_line(
        coding_proposal_payload, coding_job_payload
    )
    if coding_status_line:
        lines.append(coding_status_line)

    if canonical_prompt_override:
        canonical_short = canonical_prompt_override
        if len(canonical_short) > 160:
            canonical_short = canonical_short[:157] + "..."
        lines.append(f"- canonical 작업 prompt: {canonical_short}")
    if latest_continuation_prompt and (
        not canonical_prompt_override
        or latest_continuation_prompt != canonical_prompt_override
    ):
        cont_short = latest_continuation_prompt
        if len(cont_short) > 160:
            cont_short = cont_short[:157] + "..."
        lines.append(f"- 최근 continuation prompt: {cont_short}")
    if resumed_thread_id is not None:
        lines.append(f"- 이어붙인 thread id: `{resumed_thread_id}`")

    if forum_thread_id or forum_thread_url:
        thread_label = forum_thread_url or f"thread `{forum_thread_id}`"
        lines.append(f"- 운영-리서치 forum: 게시됨 ({thread_label})")
    elif forum_publish_error:
        lines.append("- 운영-리서치 forum: 게시 실패")
        lines.append(f"  · 마지막 오류: {forum_publish_error}")
    elif research_pack:
        lines.append("- 운영-리서치 forum: 아직 게시되지 않음 (자료는 수집 완료)")
    else:
        lines.append("- 운영-리서치 forum: 자료 수집 전이라 게시 단계가 아님")

    # Forum comment mode signals — only meaningful once the forum
    # publish actually ran (so we condition on having a thread or an
    # explicit error). In member-bots mode we explain that per-role
    # comments come from each member bot, not the gateway.
    if forum_comment_mode == "member-bots":
        lines.append("- 모드: member-bots (각 멤버 봇이 자기 계정으로 댓글)")
        # Phase B canonical names (research_open_call_*) override the
        # legacy forum_kickoff_* keys when both are present so the
        # diagnostic always describes the latest writer's intent.
        kickoff_posted = extra.get("research_open_call_posted")
        kickoff_error = extra.get("research_open_call_error")
        if kickoff_posted is None and forum_kickoff_posted is not None:
            kickoff_posted = forum_kickoff_posted
            kickoff_error = forum_kickoff_error
        if kickoff_posted is True:
            lines.append("  · open-call directive: 게시 완료")
        elif kickoff_posted is False:
            reason = kickoff_error or "원인 미확인"
            lines.append(f"  · open-call directive: 게시 실패 — {reason}")
        # Always close with a pointer to where the actual role comments
        # land so the operator knows the gateway summary isn't where to
        # judge member bot work.
        lines.append(
            "  · 후속 댓글은 운영-리서치 thread에서 직접 확인해 주세요."
        )
    elif forum_comment_mode == "gateway":
        lines.append("- 모드: gateway (역할별 댓글을 게이트웨이가 직접 게시)")

    role_turns = extra.get("role_turns")
    if isinstance(role_turns, Mapping) and role_turns:
        # Phase B activity log — show each role that actually spoke (or
        # tried to). Sorted by role name for stable diagnostic output.
        lines.append("- 역할 활동 기록:")
        for role_name in sorted(role_turns.keys()):
            entry = role_turns.get(role_name)
            if not isinstance(entry, Mapping):
                continue
            status = entry.get("status") or "?"
            kind = entry.get("kind") or "?"
            posted_at = entry.get("posted_at")
            error = entry.get("error")
            descriptor = f"{role_name}: {status} ({kind}"
            if posted_at:
                descriptor += f", {posted_at}"
            descriptor += ")"
            if error:
                descriptor += f" — {error}"
            lines.append(f"  · {descriptor}")

    if research_loop_report:
        report_error = None
        report_status = None
        if isinstance(research_loop_report, Mapping):
            report_error = research_loop_report.get("error")
            report_status = research_loop_report.get("forum_status_message")
        else:
            report_error = getattr(research_loop_report, "error", None)
            report_status = getattr(
                research_loop_report, "forum_status_message", None
            )
        if report_error:
            lines.append(f"- research loop 오류: {report_error}")
        elif report_status:
            short = " ".join(str(report_status).split())
            if len(short) > 160:
                short = short[:157] + "..."
            lines.append(f"- 최근 보고: {short}")

    if synthesis:
        lines.append("- tech-lead synthesis: 기록됨")
    elif research_pack:
        lines.append("- tech-lead synthesis: 아직 기록되지 않음")

    # Phase 4 — surface role_selection + work_report so the user can
    # see *who* participated and *whether* a deliverable already
    # landed. ``active_research_roles`` comes from the role_selection
    # module; ``work_report`` is the snapshot the gateway posts at
    # lifecycle close.
    active_roles_value = extra.get("active_research_roles")
    if isinstance(active_roles_value, list) and active_roles_value:
        role_names = ", ".join(str(r) for r in active_roles_value if r)
        if role_names:
            selection_source = extra.get("role_selection_source") or "?"
            lines.append(
                f"- 활성 role: {role_names} (선정: {selection_source})"
            )

    work_report_payload = extra.get("work_report")
    if isinstance(work_report_payload, Mapping):
        title = str(work_report_payload.get("title") or "?")
        if len(title) > 80:
            title = title[:77] + "..."
        requires_change = bool(
            work_report_payload.get("requires_code_change")
        )
        code_flag = (
            "코드 수정 필요"
            if requires_change
            else "코드 수정 없음"
        )
        ref_count = work_report_payload.get("reference_count") or 0
        stop_reason = work_report_payload.get("research_stop_reason")
        meta_bits = [f"자료 {ref_count}건", code_flag]
        if stop_reason:
            meta_bits.append(f"stop: {stop_reason}")
        lines.append(
            f"- 업무 보고서: 작성됨 — \"{title}\" · "
            + " · ".join(meta_bits)
        )
    elif synthesis:
        lines.append("- 업무 보고서: 아직 미작성")

    progress_notes = tuple(getattr(session, "progress_notes", ()) or ())
    if progress_notes:
        last = progress_notes[-1]
        last_short = " ".join(str(last).split())
        if len(last_short) > 160:
            last_short = last_short[:157] + "..."
        lines.append(f"- 마지막 진행 노트: {last_short}")

    # Phase E: surface the structured diagnostic helper signals so the
    # operator sees "왜 멈췄는지" without re-deriving the rules.
    # ``primary_signal`` skips info-only signals so we never crowd the
    # response with "research_pack 미수집" noise when the session is
    # genuinely just at the start.
    report = diagnose_session(session)
    actionable = tuple(s for s in report.signals if s.severity != "info")
    if actionable:
        lines.append("")
        lines.append("감지된 다음 단계:")
        for signal in actionable:
            tag = _STATUS_SEVERITY_TAGS.get(signal.severity, signal.severity)
            lines.append(f"- {tag} {signal.title}")
            if signal.detail:
                detail = " ".join(str(signal.detail).split())
                if len(detail) > 200:
                    detail = detail[:197] + "..."
                lines.append(f"  · 원인: {detail}")
            if signal.propose:
                propose = " ".join(str(signal.propose).split())
                if len(propose) > 200:
                    propose = propose[:197] + "..."
                lines.append(f"  · 제안: {propose}")

    if is_member_bot_question:
        lines.append("")
        lines.extend(render_member_bot_summary(report).splitlines())

    lines.append("")
    lines.append(
        "추가로 보고 싶은 항목(예: 출처 목록, role take 진행)을 알려 주시면 그 부분만 더 자세히 정리해 드릴게요."
    )
    return "\n".join(lines)


_STATUS_SEVERITY_TAGS = {
    "failed": "[FAILED]",
    "blocked": "[BLOCKED]",
    "stale": "[STALE]",
    "info": "[INFO]",
}


_MEMBER_BOT_PHRASES = (
    "멤버 봇",
    "멤버봇",
    "역할 봇",
    "역할봇",
    "member bot",
    "member-bot",
)


def _asks_about_member_bots(message_text: str) -> bool:
    normalized = _normalize(message_text)
    return any(phrase in normalized for phrase in _MEMBER_BOT_PHRASES)


def _format_clarification_question(message_text: str) -> str:
    return (
        "받았어요. 다만 지금 내용만으로는 어디부터 봐야 할지 잡히지 않아서 한 번 더 여쭐게요.\n\n"
        "다음 중 한두 가지만 알려 주시면 충분합니다:\n"
        "- 어느 화면 / API / 흐름을 다루고 싶은지\n"
        "- 막혀 있는 지점이나 원하는 결과\n"
        "- 참고할 링크나 스크린샷이 있는지"
    )


def _format_split_proposal(splits: Sequence[str]) -> str:
    if not splits:
        return _format_intake_candidate_question(
            message_text="",
            suggested_task_type=None,
            write_likely=False,
        )
    lines = [
        "요청에 갈래가 여러 개 있어 보여요. 이렇게 나눠 보겠습니다."
    ]
    for idx, branch in enumerate(splits, start=1):
        lines.append(f"{idx}. {branch}")
    lines.append("")
    lines.append(
        "각각을 별도 세션으로 만들 수도 있고, 하나로 묶어서 진행할 수도 있어요. "
        "원하시는 방식을 알려 주시거나 `이대로 진행`이라고 답해 주시면 한 세션으로 정리할게요."
    )
    return "\n".join(lines)


def _format_intake_candidate_question(
    *,
    message_text: str,
    suggested_task_type: Optional[str],
    write_likely: bool,
) -> str:
    """Conversational intake response (no auto-collection branch).

    Three short paragraphs in the team-lead voice:
    1. 받아들임,
    2. 이해한 작업 요약 (요청 본문은 30~60자로 축약),
    3. 확정 안내.
    """

    topic = _summarize_topic(message_text)
    paragraphs: list[str] = []

    paragraphs.append("작업 내용을 받았어요.")

    understand = [f"이번 요청은 “{topic}”으로 이해했어요."]
    if write_likely:
        understand.append(
            "코드나 문서 쓰기가 동반되는 작업으로 보여서, 진행 전에 한 번 확인할게요."
        )
    elif suggested_task_type:
        understand.append(
            f"분석·검토 위주의 {_pretty_task_type(suggested_task_type)} 작업으로 보입니다."
        )
    paragraphs.append("\n".join(understand))

    paragraphs.append(
        "맞으면 `이대로 진행`이라고 답해 주세요. 빠진 부분이 있으면 추가로 알려주셔도 좋아요."
    )
    return "\n\n".join(paragraphs)


def _summarize_topic(text: Optional[str], max_chars: int = 60) -> str:
    """Truncate the user's request to a short topic phrase for echoing back.

    Strategy:
    1. Take the first non-empty line.
    2. Cut at the first sentence boundary (``. ? ! 。``) inside the line
       when that boundary is shorter than *max_chars*.
    3. Otherwise hard-trim to *max_chars* with an ellipsis.

    Keeps Korean / multi-byte characters intact (we trim by Unicode chars).
    """

    cleaned_lines = [
        line.strip() for line in (text or "").splitlines() if line.strip()
    ]
    head = cleaned_lines[0] if cleaned_lines else ""
    if not head:
        return "(요청 본문 없음)"

    # Prefer cutting at the first sentence boundary if it falls inside
    # the budget. ``.``/``?``/``!`` (Latin) and ``。``/``？``/``！``/``,``/``、``
    # cover Korean & English source styles.
    sentence_boundaries = (".", "?", "!", "。", "？", "！")
    earliest = -1
    for token in sentence_boundaries:
        idx = head.find(token)
        if idx == -1:
            continue
        if 8 <= idx <= max_chars and (earliest == -1 or idx < earliest):
            earliest = idx
    if earliest != -1:
        return head[: earliest + 1].strip()

    if len(head) <= max_chars:
        return head
    return head[: max(1, max_chars - 1)].rstrip() + "…"


def _pretty_task_type(value: Optional[str]) -> str:
    """Delegate to the centralised label map in ``research_collector``.

    Imported lazily so this module stays importable even if collector
    is being refactored or temporarily unavailable.
    """

    try:
        from ..agents.research_collector import pretty_task_type
    except Exception:  # noqa: BLE001
        return value or "일반"
    return pretty_task_type(value)


def _pretty_provider(name: Optional[str]) -> str:
    """Delegate to the centralised provider label map."""

    try:
        from ..agents.research_collector import pretty_provider
    except Exception:  # noqa: BLE001
        return name or "알 수 없음"
    return pretty_provider(name)


def _prepend_mention(content: str, mention_user_id: Optional[int]) -> str:
    if mention_user_id is None:
        return content
    return f"<@{mention_user_id}>\n\n{content}".strip()


# ---------------------------------------------------------------------------
# Research collection layer
# ---------------------------------------------------------------------------
#
# 자유 대화 분류와 별개로, 같은 메시지에서 ResearchPack 을 만들 후보 자료를
# 골라내는 단계. ResearchPack 데이터 모델 자체는 ``agents/research_pack.py`` 에
# 있으므로 여기서는 그 위에 얹는 분류 / 부족 판정 / 역할별 제안만 담당한다.
#
# 외부 네트워크는 절대 부르지 않는다. URL 도메인을 보고 휴리스틱으로
# source_type 만 부여하고, 실제 fetch 는 후속 단계에서 한다.


SOURCE_TYPE_USER_MESSAGE = "user_message"
SOURCE_TYPE_URL = "url"
SOURCE_TYPE_WEB_RESULT = "web_result"
SOURCE_TYPE_IMAGE_REFERENCE = "image_reference"
SOURCE_TYPE_FILE_ATTACHMENT = "file_attachment"
SOURCE_TYPE_GITHUB_ISSUE = "github_issue"
SOURCE_TYPE_GITHUB_PR = "github_pr"
SOURCE_TYPE_CODE_CONTEXT = "code_context"
SOURCE_TYPE_OFFICIAL_DOCS = "official_docs"
SOURCE_TYPE_COMMUNITY_SIGNAL = "community_signal"
SOURCE_TYPE_DESIGN_REFERENCE = "design_reference"


ALL_SOURCE_TYPES: tuple[str, ...] = (
    SOURCE_TYPE_USER_MESSAGE,
    SOURCE_TYPE_URL,
    SOURCE_TYPE_WEB_RESULT,
    SOURCE_TYPE_IMAGE_REFERENCE,
    SOURCE_TYPE_FILE_ATTACHMENT,
    SOURCE_TYPE_GITHUB_ISSUE,
    SOURCE_TYPE_GITHUB_PR,
    SOURCE_TYPE_CODE_CONTEXT,
    SOURCE_TYPE_OFFICIAL_DOCS,
    SOURCE_TYPE_COMMUNITY_SIGNAL,
    SOURCE_TYPE_DESIGN_REFERENCE,
)


# 이미지 확장자: 요구사항에 따라 png, jpg, jpeg, webp, gif 는 image_reference.
IMAGE_EXTENSIONS: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".webp", ".gif")


# Discord 첨부의 content_type 이 image/* 로 오는 경우도 있으므로 같이 본다.
_IMAGE_CONTENT_TYPE_PREFIX = "image/"


# 디자인 reference 도메인 (자동 fetch 금지 소스 포함). URL 분류 단계에서
# source_type 만 design_reference 로 라우팅하고, 실제 자동 수집은 안 한다
# (engineering-agent/discord-workflow.md §4.3, env-strategy.md §7).
_DESIGN_REFERENCE_HOSTS: tuple[str, ...] = (
    "pinterest.com",
    "pinterest.co.kr",
    "kr.pinterest.com",
    "notefolio.net",
    "behance.net",
    "awwwards.com",
    "dribbble.com",
    "mobbin.com",
    "pageflows.com",
    "canva.com",
    "wix.com",
    "wixstudio.com",
    "templates.wix.com",
)


# 공식 문서 도메인 (휴리스틱). 부분 일치 (endswith) 로 본다.
_OFFICIAL_DOCS_HOST_SUFFIXES: tuple[str, ...] = (
    "developer.mozilla.org",
    "docs.python.org",
    "react.dev",
    "reactjs.org",
    "vuejs.org",
    "nextjs.org",
    "vitejs.dev",
    "nodejs.org",
    "go.dev",
    "kubernetes.io",
    "docs.docker.com",
    "developers.google.com",
    "cloud.google.com",
    "docs.aws.amazon.com",
    "learn.microsoft.com",
    "docs.microsoft.com",
    "developer.apple.com",
    "developer.android.com",
    "developer.chrome.com",
    "web.dev",
    "owasp.org",
    "ecma-international.org",
    "rfc-editor.org",
    "tools.ietf.org",
)


# 커뮤니티 신호 도메인. forum/discussion/Q&A 류.
_COMMUNITY_SIGNAL_HOST_SUFFIXES: tuple[str, ...] = (
    "reddit.com",
    "stackoverflow.com",
    "stackexchange.com",
    "news.ycombinator.com",
    "ycombinator.com",
    "lobste.rs",
    "discord.com",
    "discord.gg",
    "twitter.com",
    "x.com",
    "medium.com",
    "dev.to",
    "qiita.com",
    "velog.io",
    "tistory.com",
)


# 역할별 우선 수집 source_type 순서 (앞쪽이 가장 중요).
ROLE_RESEARCH_PROFILES: Mapping[str, tuple[str, ...]] = {
    "product-designer": (
        SOURCE_TYPE_IMAGE_REFERENCE,
        SOURCE_TYPE_DESIGN_REFERENCE,
        SOURCE_TYPE_FILE_ATTACHMENT,
        SOURCE_TYPE_URL,
        SOURCE_TYPE_USER_MESSAGE,
        SOURCE_TYPE_COMMUNITY_SIGNAL,
    ),
    "backend-engineer": (
        SOURCE_TYPE_OFFICIAL_DOCS,
        SOURCE_TYPE_GITHUB_ISSUE,
        SOURCE_TYPE_GITHUB_PR,
        SOURCE_TYPE_CODE_CONTEXT,
        SOURCE_TYPE_URL,
        SOURCE_TYPE_USER_MESSAGE,
    ),
    "frontend-engineer": (
        SOURCE_TYPE_OFFICIAL_DOCS,
        SOURCE_TYPE_IMAGE_REFERENCE,
        SOURCE_TYPE_DESIGN_REFERENCE,
        SOURCE_TYPE_CODE_CONTEXT,
        SOURCE_TYPE_GITHUB_PR,
        SOURCE_TYPE_URL,
        SOURCE_TYPE_USER_MESSAGE,
    ),
    "qa-engineer": (
        SOURCE_TYPE_GITHUB_ISSUE,
        SOURCE_TYPE_CODE_CONTEXT,
        SOURCE_TYPE_USER_MESSAGE,
        SOURCE_TYPE_GITHUB_PR,
        SOURCE_TYPE_OFFICIAL_DOCS,
        SOURCE_TYPE_URL,
    ),
    "tech-lead": (
        SOURCE_TYPE_USER_MESSAGE,
        SOURCE_TYPE_GITHUB_ISSUE,
        SOURCE_TYPE_GITHUB_PR,
        SOURCE_TYPE_OFFICIAL_DOCS,
        SOURCE_TYPE_CODE_CONTEXT,
        SOURCE_TYPE_URL,
        SOURCE_TYPE_DESIGN_REFERENCE,
        SOURCE_TYPE_IMAGE_REFERENCE,
    ),
    "ai-engineer": (
        SOURCE_TYPE_OFFICIAL_DOCS,
        SOURCE_TYPE_CODE_CONTEXT,
        SOURCE_TYPE_GITHUB_ISSUE,
        SOURCE_TYPE_GITHUB_PR,
        SOURCE_TYPE_COMMUNITY_SIGNAL,
        SOURCE_TYPE_URL,
        SOURCE_TYPE_USER_MESSAGE,
    ),
    "devops-engineer": (
        SOURCE_TYPE_OFFICIAL_DOCS,
        SOURCE_TYPE_GITHUB_PR,
        SOURCE_TYPE_GITHUB_ISSUE,
        SOURCE_TYPE_CODE_CONTEXT,
        SOURCE_TYPE_URL,
        SOURCE_TYPE_USER_MESSAGE,
    ),
}


# task_type 별로 "이건 꼭 있어야 함" 인 source_type. 부족 판정에 사용.
_REQUIRED_SOURCE_TYPES_BY_TASK_TYPE: Mapping[str, tuple[str, ...]] = {
    TaskType.LANDING_PAGE.value: (SOURCE_TYPE_IMAGE_REFERENCE, SOURCE_TYPE_DESIGN_REFERENCE),
    TaskType.VISUAL_POLISH.value: (SOURCE_TYPE_IMAGE_REFERENCE, SOURCE_TYPE_DESIGN_REFERENCE),
    TaskType.ONBOARDING_FLOW.value: (SOURCE_TYPE_IMAGE_REFERENCE, SOURCE_TYPE_DESIGN_REFERENCE),
    TaskType.EMAIL_CAMPAIGN.value: (SOURCE_TYPE_IMAGE_REFERENCE, SOURCE_TYPE_DESIGN_REFERENCE),
    TaskType.FRONTEND_FEATURE.value: (SOURCE_TYPE_OFFICIAL_DOCS, SOURCE_TYPE_IMAGE_REFERENCE),
    TaskType.BACKEND_FEATURE.value: (SOURCE_TYPE_OFFICIAL_DOCS, SOURCE_TYPE_GITHUB_ISSUE),
    TaskType.QA_TEST.value: (SOURCE_TYPE_GITHUB_ISSUE, SOURCE_TYPE_CODE_CONTEXT),
    TaskType.PLATFORM_INFRA.value: (SOURCE_TYPE_OFFICIAL_DOCS, SOURCE_TYPE_CODE_CONTEXT),
}


@dataclass(frozen=True)
class ResearchCandidate:
    """One unit of research collected from a Discord conversation turn.

    ``ResearchPack`` (in ``agents/research_pack.py``) is the long-lived neutral
    artifact, but it lacks a few engineering-loop fields (source_type,
    why_relevant, risk_or_limit, confidence). ``ResearchCandidate`` carries
    those explicitly and is what the conversation/forum layers feed into a
    pack via :func:`build_research_pack_from_candidates`.
    """

    source_type: str
    title: str
    summary: str
    collected_by_role: str
    why_relevant: str
    risk_or_limit: Optional[str] = None
    confidence: str = "medium"  # "high" / "medium" / "low"
    url: Optional[str] = None
    attachment_id: Optional[str] = None
    collected_at: Optional[datetime] = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResearchCollectionResult:
    """Outcome of a single message → research collection pass.

    ``insufficient`` is True when the layer thinks the user must add more
    context before deliberation can start (no URLs, no attachments, and the
    text alone is too thin or task_type demands missing categories).
    ``role_assignments`` maps role → tuple of source_types the role still
    lacks given the role's profile. Empty mapping when nothing is missing.
    """

    candidates: Sequence[ResearchCandidate]
    insufficient: bool = False
    insufficient_reason: Optional[str] = None
    follow_up_prompt: Optional[str] = None
    role_assignments: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Attachment shape (discord.py-agnostic)
# ---------------------------------------------------------------------------


def _attachment_field(attachment: Any, *names: str) -> Any:
    """Read the first available attribute or mapping key from an attachment.

    Discord.py exposes attachments as objects with attributes; tests pass
    plain ``SimpleNamespace`` or dicts. We accept both.
    """

    if isinstance(attachment, Mapping):
        for name in names:
            if name in attachment and attachment[name] is not None:
                return attachment[name]
        return None
    for name in names:
        value = getattr(attachment, name, None)
        if value is not None:
            return value
    return None


def _attachment_filename(attachment: Any) -> str:
    raw = _attachment_field(attachment, "filename", "name")
    return str(raw or "").strip()


def _attachment_url(attachment: Any) -> Optional[str]:
    raw = _attachment_field(attachment, "url", "proxy_url")
    if raw is None:
        return None
    cleaned = str(raw).strip()
    return cleaned or None


def _attachment_content_type(attachment: Any) -> str:
    raw = _attachment_field(attachment, "content_type", "mime_type")
    return str(raw or "").strip().lower()


def _attachment_id(attachment: Any) -> Optional[str]:
    raw = _attachment_field(attachment, "id", "attachment_id")
    if raw is None:
        return None
    return str(raw).strip() or None


def _attachment_size(attachment: Any) -> Optional[int]:
    raw = _attachment_field(attachment, "size", "size_bytes")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def classify_attachment(
    *,
    filename: str = "",
    content_type: str = "",
) -> str:
    """Decide whether a Discord attachment is an image_reference or generic file.

    Image is detected by extension (``.png/.jpg/.jpeg/.webp/.gif``) OR by an
    ``image/*`` content_type. Anything else falls back to file_attachment.
    """

    name = (filename or "").strip().lower()
    if name.endswith(IMAGE_EXTENSIONS):
        return SOURCE_TYPE_IMAGE_REFERENCE
    ctype = (content_type or "").strip().lower()
    if ctype.startswith(_IMAGE_CONTENT_TYPE_PREFIX):
        return SOURCE_TYPE_IMAGE_REFERENCE
    return SOURCE_TYPE_FILE_ATTACHMENT


def classify_url(url: str) -> str:
    """Bucket a URL into a source_type by host heuristic.

    GitHub ``/issues/<n>`` and ``/pull/<n>`` short-circuit to the dedicated
    types so qa/backend roles can see them without a network call. Pinterest
    / Behance / Awwwards / Wix / Canva style hosts are flagged as
    design_reference. Documentation-flavored hosts become official_docs;
    Reddit/HN/Stack* become community_signal. Anything else is the generic
    ``url`` bucket.
    """

    if not url:
        return SOURCE_TYPE_URL
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if not host:
        return SOURCE_TYPE_URL

    if host.endswith("github.com"):
        path = parsed.path or ""
        if re.search(r"/issues/\d+", path):
            return SOURCE_TYPE_GITHUB_ISSUE
        if re.search(r"/pull/\d+", path):
            return SOURCE_TYPE_GITHUB_PR

    for design_host in _DESIGN_REFERENCE_HOSTS:
        if host == design_host or host.endswith("." + design_host):
            return SOURCE_TYPE_DESIGN_REFERENCE

    for docs_suffix in _OFFICIAL_DOCS_HOST_SUFFIXES:
        if host == docs_suffix or host.endswith("." + docs_suffix):
            return SOURCE_TYPE_OFFICIAL_DOCS

    for community_suffix in _COMMUNITY_SIGNAL_HOST_SUFFIXES:
        if host == community_suffix or host.endswith("." + community_suffix):
            return SOURCE_TYPE_COMMUNITY_SIGNAL

    return SOURCE_TYPE_URL


def _truncate(text: str, *, max_chars: int = 200) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"


def _why_relevant_for(source_type: str, *, task_type: Optional[str]) -> str:
    if source_type == SOURCE_TYPE_USER_MESSAGE:
        return "사용자가 직접 적어 준 요구사항이라 모든 역할의 출발점이다."
    if source_type == SOURCE_TYPE_IMAGE_REFERENCE:
        return "디자이너/프론트엔드의 시각 reference 1순위. moodboard 후보."
    if source_type == SOURCE_TYPE_FILE_ATTACHMENT:
        return "사용자가 첨부한 파일 — 컨텍스트로 그대로 인용 가능."
    if source_type == SOURCE_TYPE_DESIGN_REFERENCE:
        return "디자인 참고 자료 (Pinterest/Behance/Awwwards 등). 자동 수집 금지 소스이므로 사용자 제공 링크만 인정."
    if source_type == SOURCE_TYPE_OFFICIAL_DOCS:
        return "공식 문서. 백엔드/프론트엔드/인프라 역할의 1순위 신뢰원."
    if source_type == SOURCE_TYPE_GITHUB_ISSUE:
        return "GitHub issue. QA/백엔드 회귀/요구사항 추적의 직접 근거."
    if source_type == SOURCE_TYPE_GITHUB_PR:
        return "GitHub PR. 변경 이력과 리뷰 흐름의 직접 근거."
    if source_type == SOURCE_TYPE_COMMUNITY_SIGNAL:
        return "커뮤니티 신호. 비공식이지만 사용자 페인포인트나 사례 빠르게 본다."
    if source_type == SOURCE_TYPE_URL:
        if task_type:
            return f"{task_type} 후속 검토용 일반 URL. 도메인 분류 미지정."
        return "도메인 분류 없는 일반 URL. 후속 단계에서 재분류한다."
    if source_type == SOURCE_TYPE_CODE_CONTEXT:
        return "현재 레포 코드/문서 맥락. backend/qa 가 회귀 기준으로 활용."
    if source_type == SOURCE_TYPE_WEB_RESULT:
        return "검색 결과. 후속 fetch 단계에서 채워질 슬롯."
    return "후속 분류 대기."


def _risk_or_limit_for(source_type: str) -> Optional[str]:
    if source_type == SOURCE_TYPE_DESIGN_REFERENCE:
        return "Pinterest/Notefolio/Behance/Mobbin/Page Flows/Awwwards 등은 약관상 자동 수집 금지. 사용자 제공 링크로만 사용한다."
    if source_type == SOURCE_TYPE_COMMUNITY_SIGNAL:
        return "비공식 신호. 단독 근거로는 부족하므로 official_docs 또는 code_context 와 교차 검증해야 한다."
    if source_type == SOURCE_TYPE_USER_MESSAGE:
        return "원문 그대로의 요구사항이므로 해석 차이가 생길 수 있다. 1차 deliberation 에서 명확화 질문을 동반해야 한다."
    if source_type == SOURCE_TYPE_FILE_ATTACHMENT:
        return "Discord CDN URL 은 만료될 수 있으므로 본문 발췌나 hash 를 함께 보존하는 게 안전하다."
    if source_type == SOURCE_TYPE_IMAGE_REFERENCE:
        return "Discord CDN URL 은 만료될 수 있으므로 캡션/텍스트 설명을 함께 적어두는 게 좋다."
    return None


def _confidence_for(source_type: str) -> str:
    if source_type in (
        SOURCE_TYPE_USER_MESSAGE,
        SOURCE_TYPE_OFFICIAL_DOCS,
        SOURCE_TYPE_GITHUB_ISSUE,
        SOURCE_TYPE_GITHUB_PR,
        SOURCE_TYPE_FILE_ATTACHMENT,
        SOURCE_TYPE_IMAGE_REFERENCE,
    ):
        return "high"
    if source_type in (
        SOURCE_TYPE_URL,
        SOURCE_TYPE_DESIGN_REFERENCE,
        SOURCE_TYPE_CODE_CONTEXT,
    ):
        return "medium"
    return "low"


def collect_research_candidates_from_message(
    message_text: str,
    *,
    attachments: Sequence[Any] = (),
    author_role: str = "tech-lead",
    posted_at: Optional[datetime] = None,
    task_type: Optional[str] = None,
) -> ResearchCollectionResult:
    """Pull research candidates out of a single Discord message.

    Builds, in order:

    1. one ``user_message`` candidate from *message_text* (always, when the
       text is non-empty),
    2. one candidate per URL found inside the text, classified by host into
       url / design_reference / official_docs / github_issue / github_pr /
       community_signal,
    3. one candidate per attachment, classified into image_reference or
       file_attachment.

    If the result has only the user message (no URL, no attachment) and the
    text is short, the result is flagged ``insufficient`` and a Korean
    follow-up prompt is filled in. ``role_assignments`` reports per-role
    missing source_types whenever *task_type* is known.
    """

    candidates: list[ResearchCandidate] = []
    text = (message_text or "").strip()

    if text:
        candidates.append(
            ResearchCandidate(
                source_type=SOURCE_TYPE_USER_MESSAGE,
                title=_truncate(text, max_chars=80),
                summary=_truncate(text, max_chars=400),
                collected_by_role=author_role,
                why_relevant=_why_relevant_for(SOURCE_TYPE_USER_MESSAGE, task_type=task_type),
                risk_or_limit=_risk_or_limit_for(SOURCE_TYPE_USER_MESSAGE),
                confidence=_confidence_for(SOURCE_TYPE_USER_MESSAGE),
                collected_at=posted_at,
            )
        )

    for url in extract_urls(text):
        url_type = classify_url(url)
        candidates.append(
            ResearchCandidate(
                source_type=url_type,
                title=_url_title(url),
                summary=_truncate(url, max_chars=400),
                collected_by_role=author_role,
                why_relevant=_why_relevant_for(url_type, task_type=task_type),
                risk_or_limit=_risk_or_limit_for(url_type),
                confidence=_confidence_for(url_type),
                url=url,
                collected_at=posted_at,
            )
        )

    for attachment in attachments:
        filename = _attachment_filename(attachment)
        content_type = _attachment_content_type(attachment)
        url = _attachment_url(attachment)
        attachment_id = _attachment_id(attachment)
        size_bytes = _attachment_size(attachment)
        kind = classify_attachment(filename=filename, content_type=content_type)
        title = filename or (f"attachment-{attachment_id}" if attachment_id else "(attachment)")
        summary_parts: list[str] = []
        if filename:
            summary_parts.append(filename)
        if content_type:
            summary_parts.append(content_type)
        if size_bytes is not None:
            summary_parts.append(f"{size_bytes} bytes")
        summary = " · ".join(summary_parts) or "(no metadata)"
        candidates.append(
            ResearchCandidate(
                source_type=kind,
                title=title,
                summary=summary,
                collected_by_role=author_role,
                why_relevant=_why_relevant_for(kind, task_type=task_type),
                risk_or_limit=_risk_or_limit_for(kind),
                confidence=_confidence_for(kind),
                url=url,
                attachment_id=attachment_id,
                collected_at=posted_at,
                extra={
                    "filename": filename or None,
                    "content_type": content_type or None,
                    "size_bytes": size_bytes,
                },
            )
        )

    insufficient, reason = _evaluate_research_sufficiency(
        candidates=candidates,
        text=text,
        task_type=task_type,
    )
    follow_up = format_insufficient_research_prompt(reason) if insufficient else None
    role_assignments = (
        suggest_role_research_assignments(
            task_type=task_type,
            collected_source_types=tuple(c.source_type for c in candidates),
        )
        if task_type
        else {}
    )

    return ResearchCollectionResult(
        candidates=tuple(candidates),
        insufficient=insufficient,
        insufficient_reason=reason,
        follow_up_prompt=follow_up,
        role_assignments=role_assignments,
    )


def suggest_role_research_assignments(
    *,
    task_type: Optional[str],
    collected_source_types: Sequence[str],
    roles: Sequence[str] = (
        "product-designer",
        "frontend-engineer",
        "backend-engineer",
        "qa-engineer",
        "tech-lead",
    ),
    max_per_role: int = 3,
) -> Mapping[str, tuple[str, ...]]:
    """Return per-role lists of source_types still missing.

    Iterates each role's ``ROLE_RESEARCH_PROFILES`` ranking, drops
    source_types we already have, and trims to *max_per_role* items so the
    operator gets a small actionable nudge instead of the whole catalogue.

    A role is omitted from the returned mapping if it has nothing to ask
    for. *task_type* is currently advisory — it informs which categories
    are required (see ``_REQUIRED_SOURCE_TYPES_BY_TASK_TYPE``) but the
    role's personal profile drives the ordering.
    """

    have = {st for st in collected_source_types if st}
    required: tuple[str, ...] = ()
    if task_type and task_type in _REQUIRED_SOURCE_TYPES_BY_TASK_TYPE:
        required = _REQUIRED_SOURCE_TYPES_BY_TASK_TYPE[task_type]

    assignments: dict[str, tuple[str, ...]] = {}
    for role in roles:
        profile = ROLE_RESEARCH_PROFILES.get(role)
        if not profile:
            continue
        ordered: list[str] = []
        # Required-by-task_type first (if not yet collected) — but only for
        # roles whose profile actually values that source_type.
        for source_type in required:
            if source_type in have:
                continue
            if source_type in profile and source_type not in ordered:
                ordered.append(source_type)
        for source_type in profile:
            if source_type in have:
                continue
            if source_type == SOURCE_TYPE_USER_MESSAGE:
                # 사용자가 직접 발화한 case 가 아닌 한 user_message 는 이미
                # 채워지므로 추천에서 빼준다. 비어 있으면 자연스럽게 노출.
                continue
            if source_type not in ordered:
                ordered.append(source_type)
            if len(ordered) >= max_per_role:
                break
        if ordered:
            assignments[role] = tuple(ordered[:max_per_role])
    return assignments


def format_insufficient_research_prompt(reason: Optional[str] = None) -> str:
    """Return the Korean follow-up question we send when the pack is too thin.

    Always opens with "자료가 부족합니다." per spec so the operator can rely
    on string matching in tests / instrumentation.
    """

    body = (
        "자료가 부족합니다. 참고할 링크나 이미지를 올려주실까요?"
    )
    if reason:
        body += f"\n사유: {reason}"
    body += (
        "\n다음 중 하나라도 함께 주시면 deliberation 단계로 바로 넘어갈 수 있어요."
        "\n- 참고 화면이나 스크린샷"
        "\n- 관련 이슈 / PR / 공식 문서 링크"
        "\n- 비슷한 사례를 본 경쟁 서비스 URL"
    )
    return body


def build_research_pack_from_candidates(
    *,
    title: str,
    candidates: Sequence[ResearchCandidate],
    channel_id: Optional[int] = None,
    thread_id: Optional[int] = None,
    message_id: Optional[int] = None,
    posted_at: Optional[datetime] = None,
    tags: Sequence[str] = (),
    extra: Optional[Mapping[str, Any]] = None,
) -> ResearchPack:
    """Materialise a ``ResearchPack`` from collected candidates.

    Each candidate becomes one ``ResearchSource``. The engineering-loop
    fields (source_type, why_relevant, risk_or_limit, confidence,
    attachment_id) are stashed in ``ResearchSource.extra`` so the neutral
    research_pack data model never has to grow per-department fields.
    """

    if not candidates:
        raise ValueError("build_research_pack_from_candidates requires at least one candidate")

    sources: list[ResearchSource] = []
    primary_url: Optional[str] = None
    for candidate in candidates:
        if primary_url is None and candidate.url:
            primary_url = candidate.url
        attachments: tuple[ResearchAttachment, ...] = ()
        if candidate.source_type in (SOURCE_TYPE_IMAGE_REFERENCE, SOURCE_TYPE_FILE_ATTACHMENT):
            kind = "image" if candidate.source_type == SOURCE_TYPE_IMAGE_REFERENCE else "file"
            attachment_url = candidate.url or candidate.attachment_id or ""
            attachments = (
                ResearchAttachment(
                    kind=kind,
                    url=attachment_url,
                    filename=str(candidate.extra.get("filename") or "") or candidate.title,
                    content_type=str(candidate.extra.get("content_type") or "") or None,
                    size_bytes=candidate.extra.get("size_bytes"),
                    description=candidate.summary or None,
                ),
            )
        sources.append(
            ResearchSource(
                source_url=candidate.url,
                title=candidate.title,
                summary=candidate.summary,
                author_role=candidate.collected_by_role,
                channel_id=channel_id,
                thread_id=thread_id,
                message_id=message_id,
                posted_at=candidate.collected_at or posted_at,
                attachments=attachments,
                extra={
                    "source_type": candidate.source_type,
                    "why_relevant": candidate.why_relevant,
                    "risk_or_limit": candidate.risk_or_limit,
                    "confidence": candidate.confidence,
                    "attachment_id": candidate.attachment_id,
                    **{k: v for k, v in candidate.extra.items() if k not in {
                        "filename",
                        "content_type",
                        "size_bytes",
                    }},
                },
            )
        )

    return ResearchPack(
        title=(title or "(untitled)").strip() or "(untitled)",
        summary=candidates[0].summary,
        primary_url=primary_url,
        sources=tuple(sources),
        tags=tuple(tags),
        created_at=posted_at,
        extra=dict(extra or {}),
    )


# ---------------------------------------------------------------------------
# Sufficiency / helpers
# ---------------------------------------------------------------------------


def _evaluate_research_sufficiency(
    *,
    candidates: Sequence[ResearchCandidate],
    text: str,
    task_type: Optional[str],
) -> tuple[bool, Optional[str]]:
    has_url = any(c.url for c in candidates)
    has_attachment = any(c.attachment_id for c in candidates)
    has_user_message = any(c.source_type == SOURCE_TYPE_USER_MESSAGE for c in candidates)

    if not candidates:
        return True, "메시지 본문도 첨부도 없어 수집된 자료가 없습니다."

    if not has_user_message and not has_url and not has_attachment:
        return True, "참고 링크와 첨부 파일이 모두 비어 있습니다."

    if has_user_message and not has_url and not has_attachment:
        word_count = len(text.split())
        if word_count < 6 or len(text) < 25:
            return True, "사용자 메시지만 있고 너무 짧아 deliberation 단서가 부족합니다."

    if task_type and task_type in _REQUIRED_SOURCE_TYPES_BY_TASK_TYPE:
        required = _REQUIRED_SOURCE_TYPES_BY_TASK_TYPE[task_type]
        collected = {c.source_type for c in candidates}
        if SOURCE_TYPE_IMAGE_REFERENCE in required and SOURCE_TYPE_DESIGN_REFERENCE in required:
            if not (collected & {SOURCE_TYPE_IMAGE_REFERENCE, SOURCE_TYPE_DESIGN_REFERENCE}):
                return True, f"task_type `{task_type}` 은 시각 reference(이미지 또는 디자인 링크)가 1개 이상 필요합니다."
        else:
            missing = [st for st in required if st not in collected]
            if missing:
                return True, (
                    f"task_type `{task_type}` 에 필요한 자료가 빠져 있습니다: "
                    + ", ".join(missing)
                )

    return False, None


def _url_title(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or ""
    if host and path and path != "/":
        return f"{host}{path}"
    return host or url


# Re-exports for callers that want a one-stop import surface.
__all__ = [
    # existing
    "EngineeringConversationResponse",
    "EngineeringIntentMatch",
    "build_engineering_conversation_response",
    "detect_engineering_intent",
    "split_task_branches",
    # research collection
    "ALL_SOURCE_TYPES",
    "IMAGE_EXTENSIONS",
    "ROLE_RESEARCH_PROFILES",
    "ResearchCandidate",
    "ResearchCollectionResult",
    "build_research_pack_from_candidates",
    "classify_attachment",
    "classify_url",
    "collect_research_candidates_from_message",
    "format_insufficient_research_prompt",
    "format_status_diagnostic_response",
    "suggest_role_research_assignments",
    # source_type constants
    "SOURCE_TYPE_USER_MESSAGE",
    "SOURCE_TYPE_URL",
    "SOURCE_TYPE_WEB_RESULT",
    "SOURCE_TYPE_IMAGE_REFERENCE",
    "SOURCE_TYPE_FILE_ATTACHMENT",
    "SOURCE_TYPE_GITHUB_ISSUE",
    "SOURCE_TYPE_GITHUB_PR",
    "SOURCE_TYPE_CODE_CONTEXT",
    "SOURCE_TYPE_OFFICIAL_DOCS",
    "SOURCE_TYPE_COMMUNITY_SIGNAL",
    "SOURCE_TYPE_DESIGN_REFERENCE",
    # intent constants (existing)
    "GENERAL_ENGINEERING_HELP",
    "TASK_INTAKE_CANDIDATE",
    "NEEDS_CLARIFICATION",
    "CONFIRM_INTAKE",
    "SPLIT_TASK_PROPOSAL",
]
