"""engineering_conversation — main entry + generic surface formatters.

This is the final stage of the conversation pipeline. Every other
module in the package feeds into :func:`build_engineering_conversation_response`,
which interprets the detected intent and produces the
:class:`EngineeringConversationResponse` envelope the gateway acts on.

Public:

- :func:`build_engineering_conversation_response` — main entry.

Generic surface formatters (used by ``build_engineering_conversation_response``
and not specialised enough to belong to ``status_responses`` or
``research_bootstrap``):

- :func:`_format_general_help`
- :func:`_format_clarification_question`
- :func:`_format_split_proposal`
- :func:`_format_intake_candidate_question`
- :func:`_summarize_topic`
- :func:`_pretty_task_type`
- :func:`_pretty_provider`
- :func:`_prepend_mention`

Dependency direction (audit doc §2):
``models`` / ``intent_detection`` / ``task_shaping`` / ``status_responses``
/ ``research_bootstrap`` all feed into here. This module imports from
all of them but is never imported by any of them, so there is no
cycle.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from .intent_detection import (
    _asks_to_continue_existing_thread,
    _asks_to_start_new_thread,
    detect_engineering_intent,
    split_task_branches,
)
from .models import (
    APPROVAL_ACTION,
    BLOCKED_REASON_QUERY,
    CHANGE_DIRECTION,
    CONFIRM_INTAKE,
    CONTINUE_EXISTING_WORK,
    EngineeringConversationResponse,
    EngineeringIntentMatch,
    GENERAL_ENGINEERING_HELP,
    NEEDS_CLARIFICATION,
    SESSION_COUNT_QUERY,
    SESSION_LIST_QUERY,
    SPLIT_TASK_PROPOSAL,
    STATUS_DIAGNOSTIC,
    TASK_INTAKE_CANDIDATE,
)
from .research_bootstrap import (
    _format_coding_bootstrap_body,
    _format_collection_announcement,
    _format_intake_with_collection,
    _maybe_run_auto_collect,
)
from .status_responses import (
    _asks_about_member_bots,
    format_blocked_reason_response,
    format_change_direction_response,
    format_continue_existing_response,
    format_session_count_response,
    format_session_list_response,
    format_status_diagnostic_response,
)
from .task_shaping import (
    _looks_like_write_request,
    _suggest_task_type,
)


# ---------------------------------------------------------------------------
# Public entry point
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
        from ...agents.routing import is_bot_echo_phrase as _is_bot_echo
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

    # P0-J (#146) — read-only intents hard rule. NEVER call
    # _maybe_run_auto_collect / NEVER set ready_to_intake=True.
    if intent.intent_id == SESSION_COUNT_QUERY:
        body = format_session_count_response()
        return EngineeringConversationResponse(
            content=_prepend_mention(body, mention_user_id),
            intent_id=SESSION_COUNT_QUERY,
            mention_user_id=mention_user_id,
            is_status_query=True,
        )

    if intent.intent_id == SESSION_LIST_QUERY:
        body = format_session_list_response()
        return EngineeringConversationResponse(
            content=_prepend_mention(body, mention_user_id),
            intent_id=SESSION_LIST_QUERY,
            mention_user_id=mention_user_id,
            is_status_query=True,
        )

    if intent.intent_id == BLOCKED_REASON_QUERY:
        session = None
        if callable(status_session_loader):
            try:
                try:
                    session = status_session_loader(message_text=message_text)
                except TypeError:
                    session = status_session_loader()
            except Exception:  # noqa: BLE001 - loader failure must not crash
                session = None
        body = format_blocked_reason_response(session)
        return EngineeringConversationResponse(
            content=_prepend_mention(body, mention_user_id),
            intent_id=BLOCKED_REASON_QUERY,
            mention_user_id=mention_user_id,
            is_status_query=True,
        )

    if intent.intent_id == CONTINUE_EXISTING_WORK:
        session = None
        if callable(status_session_loader):
            try:
                try:
                    session = status_session_loader(message_text=message_text)
                except TypeError:
                    session = status_session_loader()
            except Exception:  # noqa: BLE001
                session = None
        body = format_continue_existing_response(session)
        return EngineeringConversationResponse(
            content=_prepend_mention(body, mention_user_id),
            intent_id=CONTINUE_EXISTING_WORK,
            mention_user_id=mention_user_id,
            is_status_query=True,
        )

    if intent.intent_id == CHANGE_DIRECTION:
        session = None
        if callable(status_session_loader):
            try:
                try:
                    session = status_session_loader(message_text=message_text)
                except TypeError:
                    session = status_session_loader()
            except Exception:  # noqa: BLE001
                session = None
        body = format_change_direction_response(session, user_text=message_text)
        return EngineeringConversationResponse(
            content=_prepend_mention(body, mention_user_id),
            intent_id=CHANGE_DIRECTION,
            mention_user_id=mention_user_id,
            is_status_query=True,
        )

    # P0-K (#148) — APPROVAL_ACTION early match. When detect returned
    # APPROVAL_ACTION because the message text was a bare proceed/
    # approval phrase, we upgrade to CONFIRM_INTAKE *only* when there's
    # a substantive last_proposed_prompt to confirm. Otherwise we ack
    # the approval and stop (never create new intake / research / forum).
    if intent.intent_id == APPROVAL_ACTION:
        try:
            from ...agents.routing import is_non_actionable_prompt as _approval_guard
        except Exception:  # noqa: BLE001
            _approval_guard = None  # type: ignore[assignment]
        proposed_is_substantive = (
            bool(last_proposed_prompt)
            and (_approval_guard is None or not _approval_guard(last_proposed_prompt))
        )
        if proposed_is_substantive:
            # Upgrade — fall through to CONFIRM_INTAKE handling below.
            intent = EngineeringIntentMatch(
                intent_id=CONFIRM_INTAKE,
                label="진행 확정",
                confidence="high",
            )
        else:
            body = (
                "✅ 승인 반영했습니다. 기존 작업 흐름을 이어갑니다.\n"
                "새 리서치 thread 는 만들지 않습니다."
            )
            return EngineeringConversationResponse(
                content=_prepend_mention(body, mention_user_id),
                intent_id=APPROVAL_ACTION,
                mention_user_id=mention_user_id,
                is_status_query=True,
            )

    if intent.intent_id == CONFIRM_INTAKE:
        intake_prompt = last_proposed_prompt or message_text
        # P0-K (#148) — defense-in-depth. The APPROVAL_ACTION branch
        # above usually catches bare confirms, but a future caller
        # might pass a command-only message_text + no
        # last_proposed_prompt directly into CONFIRM_INTAKE. Refuse
        # to use a command-only intake_prompt.
        try:
            from ...agents.routing import is_non_actionable_prompt
        except Exception:  # noqa: BLE001 - partial install safe-side
            is_non_actionable_prompt = None  # type: ignore[assignment]
        if (
            is_non_actionable_prompt is not None
            and is_non_actionable_prompt(intake_prompt)
        ):
            body = (
                "✅ 승인 반영했습니다. 기존 작업 흐름을 이어갑니다.\n"
                "새 리서치 thread 는 만들지 않습니다."
            )
            return EngineeringConversationResponse(
                content=_prepend_mention(body, mention_user_id),
                intent_id=APPROVAL_ACTION,
                mention_user_id=mention_user_id,
                is_status_query=True,
            )
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

    # P0-J (#145) — coding bootstrap bypass. When the user has a GitHub
    # repo target + stack mention + write intent, the collector's
    # "자료 부족" surface is wrong (the *anchor* material is the repo
    # itself). Replace the NEEDS_USER_INPUT body with a bootstrap
    # acknowledgement so the gateway proceeds to coding handoff.
    bootstrap_outcome = None
    try:
        from ...agents.coding.coding_bootstrap import (
            STATUS_BYPASS,
            evaluate_coding_bootstrap,
        )

        bootstrap_outcome = evaluate_coding_bootstrap(
            message_text=message_text,
            user_links=user_links,
        )
    except Exception:  # noqa: BLE001 - partial install fallback
        bootstrap_outcome = None

    # default: TASK_INTAKE_CANDIDATE
    if collection is not None:
        # P0-J: if collector reported NEEDS_USER_INPUT but bootstrap
        # says bypass, swap the body to the bootstrap announcement.
        collection_mode = getattr(getattr(collection, "mode", None), "value", "")
        if (
            collection_mode == "needs_user_input"
            and bootstrap_outcome is not None
            and bootstrap_outcome.status == STATUS_BYPASS
        ):
            body = _format_coding_bootstrap_body(
                message_text=message_text,
                bootstrap=bootstrap_outcome,
                suggested_task_type=suggested,
            )
        else:
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


# ---------------------------------------------------------------------------
# Generic surface formatters
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
        from ...agents.research.collector import pretty_task_type
    except Exception:  # noqa: BLE001
        return value or "일반"
    return pretty_task_type(value)


def _pretty_provider(name: Optional[str]) -> str:
    """Delegate to the centralised provider label map."""

    try:
        from ...agents.research.collector import pretty_provider
    except Exception:  # noqa: BLE001
        return name or "알 수 없음"
    return pretty_provider(name)


def _prepend_mention(content: str, mention_user_id: Optional[int]) -> str:
    if mention_user_id is None:
        return content
    return f"<@{mention_user_id}>\n\n{content}".strip()


__all__ = (
    "build_engineering_conversation_response",
    "_format_general_help",
    "_format_clarification_question",
    "_format_split_proposal",
    "_format_intake_candidate_question",
    "_summarize_topic",
    "_pretty_task_type",
    "_pretty_provider",
    "_prepend_mention",
)
