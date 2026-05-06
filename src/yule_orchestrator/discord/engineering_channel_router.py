"""Routing logic for the engineering #업무-접수 channel.

The Discord bot's planning conversation layer is preserved as-is; this
router handles the *engineering* path: free conversation in the intake
channel (or a thread under it), and — when the user signals confirmation
— a workflow intake plus a thread kickoff message.

The module is pure-Python: all I/O dependencies (engineering conversation
provider, workflow intake, thread kickoff, message sender) are injected
as callables so unit tests can drive the router without spinning up
discord.py. ``bot.py`` wires the production callables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Sequence, Union

from ..agents.obsidian_approval import (
    ObsidianApprovalError,
    build_save_proposal,
    execute_pending_proposal,
    get_pending_proposal,
    is_obsidian_approval,
    is_obsidian_save_request,
    store_pending_proposal,
)
from ..agents.research_persistence import persist_research_artifacts
from ..agents.routing import (
    ACTION_APPEND_CONTEXT,
    ACTION_ASK,
    ACTION_CREATE,
    ACTION_JOIN,
    EngineeringRoutingDecision,
    decide_routing,
    list_open_sessions,
)
from ..agents.runtime import (
    ACTION_APPEND_CONTEXT as RUNTIME_ACTION_APPEND_CONTEXT,
    ACTION_ASK_CLARIFICATION as RUNTIME_ACTION_ASK_CLARIFICATION,
    ACTION_JOIN_SESSION as RUNTIME_ACTION_JOIN_SESSION,
    INTENT_APPEND_CONTEXT as RUNTIME_INTENT_APPEND_CONTEXT,
    INTENT_CONTINUE_EXISTING_WORK as RUNTIME_INTENT_CONTINUE_EXISTING_WORK,
    INTENT_EXECUTE_EXISTING_STEP as RUNTIME_INTENT_EXECUTE_EXISTING_STEP,
    INTENT_SUMMARIZE_PREVIOUS_WORK as RUNTIME_INTENT_SUMMARIZE_PREVIOUS_WORK,
    RuntimeInput,
    RuntimeResearchPlan,
    classify_intent_deterministic,
    decide_default,
    make_recall_fn,
)


# Single-source confirmation lexicon; the engineering conversation layer
# may also detect intent and pre-set ``confirmed=True`` itself, in which
# case the router trusts that signal.
_CONFIRMATION_KEYWORDS: tuple[str, ...] = (
    "확정",
    "진행",
    "시작해",
    "시작하자",
    "시작할게",
    "시작합시다",
    "고고",
    "ㄱㄱ",
    "ㄱㄱㄱ",
    "맞아 진행",
    "그대로 진행",
    "그대로 가",
    "오케이 진행",
    "오케 진행",
    "go ahead",
    "let's go",
    "lets go",
    "kick off",
    "kickoff",
    "proceed",
    "approve and start",
)


@dataclass(frozen=True)
class EngineeringRouteContext:
    """Where the engineering intake channel lives.

    Both ``intake_channel_id`` and ``intake_channel_name`` are optional
    individually — if either one matches the message channel (or its
    parent, for a thread), the message is treated as engineering.
    """

    intake_channel_id: Optional[int] = None
    intake_channel_name: Optional[str] = None

    @property
    def configured(self) -> bool:
        return self.intake_channel_id is not None or bool(
            _normalize_channel_name(self.intake_channel_name)
        )

    @classmethod
    def from_env(cls) -> "EngineeringRouteContext":
        return cls(
            intake_channel_id=_optional_int_env("DISCORD_ENGINEERING_INTAKE_CHANNEL_ID"),
            intake_channel_name=_optional_string_env(
                "DISCORD_ENGINEERING_INTAKE_CHANNEL_NAME"
            ),
        )


@dataclass(frozen=True)
class EngineeringConversationOutcome:
    """The shape returned by the engineering free-conversation layer.

    ``confirmed=True`` means the user just expressed intent to start
    a real intake; ``intake_prompt`` is the canonicalised request for
    the workflow.  The conversation layer is free to omit those fields
    — the router falls back to a keyword-based confirmation check on
    the original user text.

    ``research_pack`` and ``collection_outcome`` carry the autonomous
    research collector's result through to the research-loop hook
    (forum publisher / deliberation kickoff). ``role_for_research``
    lets the conversation layer signal which role profile drove the
    collection so downstream code can render labels accordingly.
    """

    content: str
    confirmed: bool = False
    intake_prompt: Optional[str] = None
    write_requested: bool = False
    thread_topic: Optional[str] = None
    research_pack: Any = None
    collection_outcome: Any = None
    role_for_research: Optional[str] = None
    # When True the conversation already answered a status/diagnostic
    # question. The router must NOT route to intake/decide/auto_collect
    # — the user wasn't filing new work, they were asking what's going
    # on with existing work.
    is_status_query: bool = False


@dataclass(frozen=True)
class EngineeringThreadKickoff:
    """Result of creating a working thread and posting kickoff."""

    thread_id: Optional[int] = None
    message: Optional[str] = None


@dataclass(frozen=True)
class EngineeringThreadContinuation:
    """Result of continuing an already-open workflow thread."""

    session: Any
    thread_id: Optional[int] = None
    message: Optional[str] = None


@dataclass(frozen=True)
class EngineeringResearchLoopReport:
    """What the research loop hook reported back to the router.

    ``follow_up_message`` is sent to the user when the loop decided the
    research pack is too thin (e.g. no URL, no attachment for a
    landing-page task). ``forum_status_message`` is the operator-facing
    summary line ("운영-리서치 forum thread 게시: …") posted after a
    successful publish. ``error`` is filled when the hook itself raised;
    callers display it as a `⚠️` line and continue.
    """

    follow_up_message: Optional[str] = None
    forum_status_message: Optional[str] = None
    forum_thread_id: Optional[int] = None
    forum_thread_url: Optional[str] = None
    insufficient: bool = False
    error: Optional[str] = None
    # member-bots vs gateway publication mode signal — populated by the
    # research-loop hook from the publication outcome so status /
    # diagnostic responses can describe the live setup correctly.
    forum_comment_mode: Optional[str] = None
    # member-bots mode only: did the gateway successfully post the
    # ``[research-open:<session_id>]`` open-call directive that each
    # member bot is supposed to react to? ``None`` in gateway mode.
    kickoff_posted: Optional[bool] = None
    # member-bots mode only: stringified error from the open-call
    # directive post when ``kickoff_posted`` is False; otherwise None.
    kickoff_error: Optional[str] = None


@dataclass(frozen=True)
class EngineeringRouteResult:
    """What the router did with one Discord message.

    ``handled=False`` means this message is *not* an engineering channel
    message; the bot should fall through to its planning conversation
    path.  ``handled=True`` means the router has already replied (and
    optionally created an intake/thread), so the bot must not double-reply.
    """

    handled: bool
    conversation_message: Optional[str] = None
    intake_message: Optional[str] = None
    kickoff_message: Optional[str] = None
    session_id: Optional[str] = None
    thread_id: Optional[int] = None
    research_loop_report: Optional[EngineeringResearchLoopReport] = None
    error: Optional[str] = None
    routing_decision: Optional[EngineeringRoutingDecision] = None


SendChunksFn = Callable[[Any, str], Awaitable[None]]
ExtractPromptFn = Callable[..., str]
ConversationFn = Callable[..., Union[
    EngineeringConversationOutcome,
    Awaitable[EngineeringConversationOutcome],
    str,
    Awaitable[str],
]]
IntakeFn = Callable[..., Any]
ThreadKickoffFn = Callable[..., Awaitable[EngineeringThreadKickoff]]
ThreadContinuationFn = Callable[..., Union[
    Optional[EngineeringThreadContinuation],
    Awaitable[Optional[EngineeringThreadContinuation]],
]]
ResearchLoopFn = Callable[..., Union[
    EngineeringResearchLoopReport,
    Awaitable[EngineeringResearchLoopReport],
]]


def is_engineering_channel(
    *,
    message: Any,
    route_context: EngineeringRouteContext,
) -> bool:
    if not route_context.configured:
        return False

    channel = getattr(message, "channel", None)
    if channel is None:
        return False

    channel_id = getattr(channel, "id", None)
    parent = getattr(channel, "parent", None)
    parent_id = getattr(parent, "id", None) or getattr(channel, "parent_id", None)
    channel_name = _normalize_channel_name(getattr(channel, "name", None))
    parent_name = _normalize_channel_name(getattr(parent, "name", None))

    target_id = route_context.intake_channel_id
    target_name = _normalize_channel_name(route_context.intake_channel_name)

    if target_id is not None:
        if channel_id is not None and channel_id == target_id:
            return True
        if parent_id is not None and parent_id == target_id:
            return True
    if target_name:
        if channel_name == target_name:
            return True
        if parent_name == target_name:
            return True
    return False


def detect_confirmation_signal(text: str) -> bool:
    """Heuristic confirmation detector used when the conversation layer
    does not pre-classify intent.  Matches Korean and English go-ahead
    phrases conservatively — short ack words like ``yes``/``네`` are
    excluded so casual chat isn't promoted to a workflow intake."""

    if not text:
        return False
    normalized = " ".join(text.lower().split())
    if not normalized:
        return False
    return any(keyword in normalized for keyword in _CONFIRMATION_KEYWORDS)


def should_continue_existing_thread(*texts: str) -> bool:
    """True when the user asked to reuse an existing workflow thread/session."""

    normalized = " ".join(
        " ".join(str(text or "").lower().split()) for text in texts
    )
    if not normalized.strip():
        return False
    continuation_signals = (
        "새로 등록하지 말고",
        "새로 만들지 말고",
        "새 스레드 만들지",
        "새 thread 만들지",
        "새로운 스레드",
        "새 thread",
        "기존 스레드",
        "기존 thread",
        "열려 있는 스레드",
        "열려있는 스레드",
        "열려 있는 thread",
        "열려있는 thread",
        "이어가",
        "이어 가",
        "이어서",
        "continue existing",
        "reuse thread",
        "same thread",
        "do not create a new thread",
        "don't create a new thread",
    )
    return any(signal in normalized for signal in continuation_signals)


def should_start_new_thread(text: str) -> bool:
    """True when the latest user turn explicitly overrides continuation."""

    normalized = " ".join(str(text or "").lower().split())
    if not normalized:
        return False
    force_new_signals = (
        "새 작업으로 진행",
        "새 작업으로 시작",
        "새로 등록해",
        "새로 등록",
        "새 스레드로",
        "새 thread로",
        "새 세션으로",
        "new thread",
        "new session",
    )
    return any(signal in normalized for signal in force_new_signals)


async def route_engineering_message(
    *,
    message: Any,
    bot_user: Any,
    route_context: EngineeringRouteContext,
    extract_prompt: ExtractPromptFn,
    conversation_fn: ConversationFn,
    intake_fn: IntakeFn,
    thread_kickoff_fn: ThreadKickoffFn,
    send_chunks: SendChunksFn,
    research_loop_fn: Optional[ResearchLoopFn] = None,
    thread_continuation_fn: Optional[ThreadContinuationFn] = None,
    list_sessions_fn: Optional[Callable[..., Sequence[Any]]] = None,
    obsidian_writer_fn: Optional[Callable[..., Any]] = None,
    obsidian_env: Optional[Any] = None,
) -> EngineeringRouteResult:
    """Drive the engineering channel response.

    Order:
      1. If the message is not in an engineering channel, return ``handled=False``.
      2. Runtime preflight (only when ``list_sessions_fn`` is provided).
         When the message intent is one of ``continue_existing_work``,
         ``summarize_previous_work``, ``execute_existing_step`` or
         ``append_context``, we recall the matching workflow session
         and either join/append directly or send a clarification — both
         paths skip ``conversation_fn`` so ``auto_collect=True`` never
         runs for non-new-work intents.
      3. Otherwise: call the conversation layer; reply with whatever it
         produced.
      4. If the conversation (or fallback heuristic) says the user just
         confirmed, call ``intake_fn`` to create a workflow session.
      5. Post the intake summary, then kick off a thread.
      6. If ``research_loop_fn`` is provided, run it after kickoff and
         surface its follow-up / forum status message back to the user.
         Failures in the research loop are *non-fatal*: intake + kickoff
         already landed, so we report a `⚠️` line and return.
    """

    if not is_engineering_channel(message=message, route_context=route_context):
        return EngineeringRouteResult(handled=False)

    prompt_text = extract_prompt(message=message, bot_user=bot_user)
    prompt_text = (prompt_text or "").strip()
    if not prompt_text:
        return EngineeringRouteResult(handled=False)

    # Obsidian approval gate — runs before runtime preflight so an
    # explicit "저장 승인" / "이대로 저장" never falls through to the
    # default new-work classifier and intakes a brand-new session.
    if list_sessions_fn is not None:
        approval = await _run_obsidian_approval_gate(
            message=message,
            prompt_text=prompt_text,
            list_sessions_fn=list_sessions_fn,
            send_chunks=send_chunks,
            writer_fn=obsidian_writer_fn,
            env=obsidian_env,
        )
        if approval is not None:
            return approval

    # Runtime preflight — opt-in via ``list_sessions_fn``. The production
    # gateway in bot.py wires this to ``workflow_state.list_sessions`` so
    # auto_collect-first traffic for "어제 작업 이어서 요약해줘" and
    # similar back-references is intercepted before conversation_fn is
    # reached. Tests that don't inject a sessions source skip preflight,
    # preserving the legacy keyword-driven flow.
    if list_sessions_fn is not None:
        preflight = await _run_runtime_preflight(
            message=message,
            prompt_text=prompt_text,
            list_sessions_fn=list_sessions_fn,
            send_chunks=send_chunks,
            thread_continuation_fn=thread_continuation_fn,
            obsidian_writer_fn=obsidian_writer_fn,
            obsidian_env=obsidian_env,
        )
        if preflight is not None:
            return preflight

    attachments = extract_message_attachments(message)
    user_links = extract_user_links_from_message(message, prompt_text)
    raw_outcome = await _maybe_await(
        conversation_fn(
            message_text=prompt_text,
            author_user_id=getattr(message.author, "id", None),
            channel_id=getattr(getattr(message, "channel", None), "id", None),
            bot_user=bot_user,
            attachments=attachments,
            user_links=user_links,
            auto_collect=True,
        )
    )
    outcome = _coerce_outcome(raw_outcome, prompt_text=prompt_text)

    if outcome.content:
        await send_chunks(message.channel, outcome.content)

    # Status / diagnostic intent already answered with the real session
    # state. The conversation layer reads ``session.extra`` directly so
    # we must NOT proceed to intake / decide_routing / auto_collect —
    # those would create a new session for what was just a "왜 안 됐어?"
    # type question and re-trigger a "1차 자료 수집" template.
    if outcome.is_status_query:
        return EngineeringRouteResult(
            handled=True,
            conversation_message=outcome.content or None,
        )

    confirmed = outcome.confirmed or detect_confirmation_signal(prompt_text)
    intake_prompt = (outcome.intake_prompt or prompt_text).strip()
    if not confirmed or not intake_prompt:
        return EngineeringRouteResult(
            handled=True,
            conversation_message=outcome.content or None,
        )

    # New routing decision — replaces the boolean "should_continue_existing_thread"
    # heuristic. ``decide_routing`` looks at currently open workflow sessions and
    # returns one of join/create/ask/append-context. Failures fall back to the
    # legacy "create new" path so the bot never gets wedged.
    #
    # Use ``intake_prompt`` (the canonical task description) instead of
    # ``prompt_text`` (which is just the user's confirmation reply like
    # "이대로 진행") so similarity scoring runs on the actual work content,
    # not on the short confirm phrase. ``intake_prompt`` already falls back
    # to ``prompt_text`` when the conversation layer has no separate task
    # text (direct-confirm / single-message confirmation).
    routing_prompt = intake_prompt or prompt_text
    try:
        routing_decision = decide_routing(prompt=routing_prompt)
    except Exception as exc:  # noqa: BLE001 - routing must not crash the bot
        routing_decision = EngineeringRoutingDecision(
            action=ACTION_CREATE,
            reason=f"decide_routing fallback: {exc}",
            confidence="low",
        )

    if routing_decision.action == ACTION_ASK:
        clarification = _format_clarification_message(routing_decision)
        await send_chunks(message.channel, clarification)
        return EngineeringRouteResult(
            handled=True,
            conversation_message=outcome.content or None,
            routing_decision=routing_decision,
        )

    if routing_decision.action in (ACTION_JOIN, ACTION_APPEND_CONTEXT):
        result = await _handle_join_or_append(
            message=message,
            outcome=outcome,
            decision=routing_decision,
            intake_prompt=intake_prompt,
            send_chunks=send_chunks,
            thread_continuation_fn=thread_continuation_fn,
            research_loop_fn=research_loop_fn,
        )
        if result is not None:
            return result
        # Fell through (continuation failed to find the matched thread) →
        # treat as an explicit clarification; never silently create a new
        # session when the user signalled they wanted to continue.
        not_found_message = (
            "열려 있는 engineering-agent thread를 찾지 못해서 새 작업 세션은 만들지 않았습니다.\n"
            "이어갈 thread 안에서 다시 말해주시거나, 새 작업으로 시작하려면 `새 작업으로 진행`이라고 답해 주세요."
        )
        await send_chunks(message.channel, not_found_message)
        return EngineeringRouteResult(
            handled=True,
            conversation_message=outcome.content or None,
            error="existing engineering thread not found",
            routing_decision=routing_decision,
        )

    # CREATE branch — but if the user explicitly typed a "이어가" / "새로
    # 등록하지 말고" continuation phrase in this turn, give the
    # continuation function one chance to find a matching thread before
    # falling through to a fresh intake. This honours the user's explicit
    # signal without re-introducing the legacy "blindly join the latest
    # open session" bug.
    legacy_wants_continuation = (
        routing_decision.action == ACTION_CREATE
        and should_continue_existing_thread(prompt_text, intake_prompt)
        and not should_start_new_thread(prompt_text)
    )
    if legacy_wants_continuation:
        join_decision = EngineeringRoutingDecision(
            action=ACTION_JOIN,
            confidence="low",
            reason="explicit continuation phrase fallback (no scored match)",
            candidate_summaries=routing_decision.candidate_summaries,
        )
        legacy_result = await _handle_join_or_append(
            message=message,
            outcome=outcome,
            decision=join_decision,
            intake_prompt=intake_prompt,
            send_chunks=send_chunks,
            thread_continuation_fn=thread_continuation_fn,
            research_loop_fn=research_loop_fn,
        )
        if legacy_result is not None:
            return legacy_result
        not_found_message = (
            "열려 있는 engineering-agent thread를 찾지 못해서 새 작업 세션은 만들지 않았습니다.\n"
            "이어갈 thread 안에서 다시 말해주시거나, 새 작업으로 시작하려면 `새 작업으로 진행`이라고 답해 주세요."
        )
        await send_chunks(message.channel, not_found_message)
        return EngineeringRouteResult(
            handled=True,
            conversation_message=outcome.content or None,
            error="existing engineering thread not found",
            routing_decision=routing_decision,
        )

    try:
        intake = intake_fn(
            prompt=intake_prompt,
            write_requested=outcome.write_requested,
            channel_id=getattr(getattr(message, "channel", None), "id", None),
            user_id=getattr(getattr(message, "author", None), "id", None),
        )
        intake = await _maybe_await(intake)
    except Exception as exc:  # noqa: BLE001 - surface error to user, do not crash bot
        error_text = f"⚠️ engineer intake 실패: {exc}"
        await send_chunks(message.channel, error_text)
        return EngineeringRouteResult(
            handled=True,
            conversation_message=outcome.content or None,
            error=str(exc),
        )

    intake_message = getattr(intake, "message", None)
    session = getattr(intake, "session", None)
    plan = getattr(intake, "plan", None)

    session = _maybe_persist_research_pack(
        session,
        research_pack=outcome.research_pack,
        collection_outcome=outcome.collection_outcome,
    )
    session_id = getattr(session, "session_id", None)

    if intake_message:
        await send_chunks(message.channel, intake_message)

    kickoff_message: Optional[str] = None
    thread_id: Optional[int] = None
    kickoff_error: Optional[str] = None
    try:
        kickoff = await thread_kickoff_fn(
            channel=message.channel,
            session=session,
            plan=plan,
            topic=outcome.thread_topic,
        )
    except Exception as exc:  # noqa: BLE001 - intake already saved, just note kickoff issue
        kickoff_error = str(exc)
        await send_chunks(
            message.channel,
            f"⚠️ thread kickoff 실패: {exc}\n세션 `{session_id or '?'}` 은 이미 생성되어 있습니다.",
        )
    else:
        if kickoff is not None:
            thread_id = kickoff.thread_id
            kickoff_message = kickoff.message

    research_loop_report: Optional[EngineeringResearchLoopReport] = None
    if research_loop_fn is not None and session is not None:
        research_loop_report = await _run_research_loop_hook(
            research_loop_fn=research_loop_fn,
            message=message,
            session=session,
            prompt_text=intake_prompt,
            send_chunks=send_chunks,
            collection_outcome=outcome.collection_outcome,
            research_pack=outcome.research_pack,
            role_for_research=outcome.role_for_research,
            thread_id=thread_id,
        )

    return EngineeringRouteResult(
        handled=True,
        conversation_message=outcome.content or None,
        intake_message=intake_message,
        kickoff_message=kickoff_message,
        session_id=session_id,
        thread_id=thread_id,
        research_loop_report=research_loop_report,
        error=kickoff_error,
        routing_decision=routing_decision,
    )


_PREFLIGHT_SHORT_CIRCUIT_INTENTS = frozenset(
    {
        RUNTIME_INTENT_CONTINUE_EXISTING_WORK,
        RUNTIME_INTENT_SUMMARIZE_PREVIOUS_WORK,
        RUNTIME_INTENT_EXECUTE_EXISTING_STEP,
        RUNTIME_INTENT_APPEND_CONTEXT,
    }
)


# ---------------------------------------------------------------------------
# Clarification follow-up memory
#
# When the gateway shows the user a clarification with multiple candidate
# sessions, we cache the candidates per (channel_or_thread_id, user_id)
# so the next message can be a short pick like "1번" or "이걸로". The
# state is overwritten on each new clarification and cleared on any
# successful selection — no TTL is enforced because a stale entry only
# fires when the next message looks like a candidate selector, which is
# the exact case where reusing it is correct.
# ---------------------------------------------------------------------------


_GATEWAY_CLARIFICATION_CONTEXT: dict[
    tuple[Optional[int], Optional[int]], tuple[dict, ...]
] = {}


_NUMERIC_SELECTION_RE = __import__("re").compile(
    r"^\s*(\d{1,2})\s*(번|번째|개|위치)?\s*\.?\s*$"
)

# Map a Korean ordinal/positional prefix to a 1-based candidate index.
# We match by ``startswith`` after whitespace removal so phrases like
# "첫 번째 거" / "두번째로" still resolve.
_ORDINAL_KO_PREFIXES: tuple[tuple[str, int], ...] = (
    ("첫번째", 1),
    ("첫 번째", 1),
    ("첫째", 1),
    ("두번째", 2),
    ("두 번째", 2),
    ("둘째", 2),
    ("세번째", 3),
    ("세 번째", 3),
    ("셋째", 3),
    ("네번째", 4),
    ("네 번째", 4),
    ("넷째", 4),
    ("다섯번째", 5),
    ("다섯 번째", 5),
    ("다섯째", 5),
)


# Phrases that mean "the one I just showed" — only meaningful with at
# least one stored candidate. With multiple candidates these stay
# ambiguous and we ask for a number; with a single candidate they pick
# it. ``기존 세션으로 진행`` is included so users who saw a
# single-candidate clarification can confirm with that exact wording.
_DEMONSTRATIVE_SELECTION_PHRASES: tuple[str, ...] = (
    "이걸로",
    "이거로",
    "이걸루",
    "이거",
    "저걸로",
    "그걸로",
    "위에 거",
    "위에거",
    "위 거",
    "위 것",
    "방금 그거",
    "방금 그것",
    "방금 거",
    "기존 세션으로 진행",
    "기존 작업으로 진행",
)


def _clarification_context_key(message: Any) -> tuple[Optional[int], Optional[int]]:
    """Scope key for the clarification cache.

    Uses the channel/thread id the user is currently typing in, plus
    the author id, so a clarification shown to user A in #업무-접수
    doesn't get hijacked by user B's "1번" reply in the same channel.
    """

    channel = getattr(message, "channel", None)
    scope_id = getattr(channel, "id", None)
    user_id = getattr(getattr(message, "author", None), "id", None)
    return (scope_id, user_id)


def _remember_clarification_candidates(
    message: Any,
    candidates: Sequence[Any],
) -> None:
    """Stash candidate session ids + thread ids from the recall result.

    Stored as plain dicts so the cache value round-trips through
    pickling-friendly types and we never hold a reference to a
    dataclass that may grow new fields underneath us.
    """

    if not candidates:
        return
    serialized = tuple(
        {
            "session_id": getattr(cand, "session_id", None),
            "title": getattr(cand, "title", "") or "",
            "score": float(getattr(cand, "score", 0.0) or 0.0),
            "thread_id": getattr(cand, "thread_id", None),
            "forum_thread_id": getattr(cand, "forum_thread_id", None),
            "task_type": getattr(cand, "task_type", None),
        }
        for cand in candidates[:5]
        if getattr(cand, "session_id", None)
    )
    if serialized:
        _GATEWAY_CLARIFICATION_CONTEXT[_clarification_context_key(message)] = serialized


def _recall_clarification_candidates(message: Any) -> tuple[dict, ...]:
    return _GATEWAY_CLARIFICATION_CONTEXT.get(_clarification_context_key(message), ())


def _clear_clarification_context(message: Any) -> None:
    _GATEWAY_CLARIFICATION_CONTEXT.pop(_clarification_context_key(message), None)


def _try_select_candidate(
    text: str,
    candidates: tuple[dict, ...],
) -> Optional[dict]:
    """Resolve a follow-up message into a stored candidate, or None.

    Recognises:
    - bare number ``"1"`` / ordinal-shaped ``"1번"`` / ``"2번째"``
    - Korean ordinals ``"첫 번째"`` / ``"두번째"`` / ...
    - demonstrative phrases (``"이걸로"`` / ``"기존 세션으로 진행"``)
      — only return a hit when there's exactly one stored candidate so
      multi-candidate ambiguity falls through to a fresh clarification
      instead of being silently resolved.

    Out-of-range numbers (e.g. user typed "9번" but only 3 candidates)
    return None so the router can re-ask. The cache is left in place
    because the next reply might still be a valid pick.
    """

    if not candidates:
        return None
    cleaned = (text or "").strip().lower()
    if not cleaned:
        return None

    numeric_match = _NUMERIC_SELECTION_RE.match(cleaned)
    if numeric_match is not None:
        index = int(numeric_match.group(1)) - 1
        if 0 <= index < len(candidates):
            return candidates[index]
        return None

    for prefix, idx in _ORDINAL_KO_PREFIXES:
        if cleaned.startswith(prefix) and idx <= len(candidates):
            return candidates[idx - 1]

    if any(phrase in cleaned for phrase in _DEMONSTRATIVE_SELECTION_PHRASES):
        if len(candidates) == 1:
            return candidates[0]
        return None

    return None


async def _handle_clarification_selection(
    *,
    message: Any,
    selected: dict,
    prompt_text: str,
    send_chunks: SendChunksFn,
    thread_continuation_fn: Optional[ThreadContinuationFn],
) -> Optional[EngineeringRouteResult]:
    """Drive the legacy join helper for a clarification follow-up
    selection. Returns a populated result on success or ``None`` to
    leave the cache in place and fall through to the regular flow."""

    if thread_continuation_fn is None:
        return None
    synthetic_outcome = EngineeringConversationOutcome(
        content="",
        intake_prompt=prompt_text,
    )
    synthetic_decision = EngineeringRoutingDecision(
        action=ACTION_JOIN,
        matched_session_id=selected.get("session_id"),
        matched_thread_id=selected.get("thread_id"),
        matched_forum_thread_id=selected.get("forum_thread_id"),
        confidence="high",
        reason="clarification follow-up selection",
    )
    return await _handle_join_or_append(
        message=message,
        outcome=synthetic_outcome,
        decision=synthetic_decision,
        intake_prompt=prompt_text,
        send_chunks=send_chunks,
        thread_continuation_fn=thread_continuation_fn,
        research_loop_fn=None,
    )


async def _run_obsidian_approval_gate(
    *,
    message: Any,
    prompt_text: str,
    list_sessions_fn: Callable[..., Sequence[Any]],
    send_chunks: SendChunksFn,
    writer_fn: Optional[Callable[..., Any]] = None,
    env: Optional[Any] = None,
) -> Optional[EngineeringRouteResult]:
    """Try to interpret *prompt_text* as an Obsidian save approval.

    Returns a populated :class:`EngineeringRouteResult` when the message
    was an approval phrase (regardless of whether the write succeeded),
    or ``None`` to fall through to the runtime preflight + conversation
    flow. We deliberately keep this branch above the runtime classifier
    so a bare "저장 승인" never gets promoted to ``new_work_request``.
    """

    if not is_obsidian_approval(prompt_text):
        return None

    candidate = _find_session_with_pending_proposal(
        message=message,
        list_sessions_fn=list_sessions_fn,
    )
    if candidate is None:
        await send_chunks(
            message.channel,
            (
                "지금은 대기 중인 Obsidian 저장 제안이 없어요.\n"
                "먼저 `Obsidian에 정리해줘` 처럼 저장 미리보기를 만들어 주세요."
            ),
        )
        return EngineeringRouteResult(handled=True)

    try:
        updated, outcome = execute_pending_proposal(
            candidate,
            env=env,
            writer_fn=writer_fn,
        )
    except ObsidianApprovalError as exc:
        await send_chunks(message.channel, f"⚠️ Obsidian 저장 실패: {exc}")
        return EngineeringRouteResult(handled=True, error=str(exc))
    except Exception as exc:  # noqa: BLE001 — never let the bot crash on save
        await send_chunks(
            message.channel,
            f"⚠️ Obsidian 저장 중 예상치 못한 오류: {exc}",
        )
        return EngineeringRouteResult(handled=True, error=str(exc))

    await send_chunks(message.channel, outcome.message)
    return EngineeringRouteResult(
        handled=True,
        session_id=getattr(updated, "session_id", None),
    )


async def _run_obsidian_preview_branch(
    *,
    message: Any,
    prompt_text: str,
    decision_payload: Any,
    list_sessions_fn: Callable[..., Sequence[Any]],
    send_chunks: SendChunksFn,
) -> Optional[EngineeringRouteResult]:
    """Render the preview for an Obsidian save request and store the proposal.

    Called from :func:`_run_runtime_preflight` when the runtime intent
    classifier identified an Obsidian save request and Recall matched a
    session. We never call ``thread_continuation_fn`` here — joining the
    thread isn't the user's goal; they want to see what we'd write before
    approving it.
    """

    target_session_id: Optional[str] = None
    if hasattr(decision_payload, "get"):
        raw = decision_payload.get("session_id")
        target_session_id = str(raw) if raw is not None else None

    session = _load_session_by_id(list_sessions_fn, target_session_id)
    if session is None:
        await send_chunks(
            message.channel,
            (
                "**[engineering-agent] Obsidian 저장 대상 세션을 찾지 못했어요.**\n"
                "어떤 세션을 저장할지 `기존 세션 <id>` 처럼 답해 주세요."
            ),
        )
        return EngineeringRouteResult(
            handled=True,
            error="obsidian preview: matched session not loadable",
        )

    try:
        proposal = build_save_proposal(
            session,
            actor_user_id=getattr(getattr(message, "author", None), "id", None),
        )
    except ObsidianApprovalError as exc:
        await send_chunks(message.channel, f"⚠️ {exc}")
        return EngineeringRouteResult(handled=True, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        await send_chunks(
            message.channel,
            f"⚠️ Obsidian 미리보기 생성 실패: {exc}",
        )
        return EngineeringRouteResult(handled=True, error=str(exc))

    try:
        store_pending_proposal(session, proposal)
    except Exception as exc:  # noqa: BLE001 — preview survives even if persist fails
        await send_chunks(
            message.channel,
            f"⚠️ 저장 제안 기록 실패: {exc}\n미리보기는 아래에서 확인하실 수 있어요.",
        )

    await send_chunks(message.channel, proposal.preview_message)
    return EngineeringRouteResult(
        handled=True,
        session_id=session.session_id,
        thread_id=getattr(session, "thread_id", None),
    )


def _find_session_with_pending_proposal(
    *,
    message: Any,
    list_sessions_fn: Callable[..., Sequence[Any]],
) -> Optional[Any]:
    """Return the session that owns the most relevant pending proposal.

    Match priority:
      1. Sessions whose ``thread_id`` equals the message's channel id
         (for thread messages — Discord exposes the thread id as
         ``channel.id`` and the channel id as ``channel.parent_id``).
      2. Sessions in the same channel + same author with a proposal.
      3. Most recently updated session with a proposal.

    Returns ``None`` when no candidate has a pending proposal stashed in
    ``session.extra``.
    """

    try:
        try:
            sessions = list_sessions_fn(limit=50)
        except TypeError:
            sessions = list_sessions_fn()
    except Exception:  # noqa: BLE001 — recall outage must not crash bot
        return None
    if not sessions:
        return None

    channel = getattr(message, "channel", None)
    channel_id = getattr(channel, "id", None)
    parent_id = getattr(channel, "parent_id", None)
    if parent_id is None and getattr(channel, "parent", None) is None:
        thread_id = None
        scoped_channel_id = channel_id
    else:
        thread_id = channel_id
        scoped_channel_id = parent_id
    user_id = getattr(getattr(message, "author", None), "id", None)

    candidates_with_proposal = [
        s for s in sessions if get_pending_proposal(s) is not None
    ]
    if not candidates_with_proposal:
        return None

    if thread_id is not None:
        for session in candidates_with_proposal:
            if getattr(session, "thread_id", None) == thread_id:
                return session

    if scoped_channel_id is not None:
        same_scope = [
            s
            for s in candidates_with_proposal
            if getattr(s, "channel_id", None) == scoped_channel_id
            and (user_id is None or getattr(s, "user_id", None) == user_id)
        ]
        if same_scope:
            return _most_recent_session(same_scope)

    return _most_recent_session(candidates_with_proposal)


def _load_session_by_id(
    list_sessions_fn: Callable[..., Sequence[Any]],
    session_id: Optional[str],
) -> Optional[Any]:
    if not session_id:
        return None
    try:
        try:
            sessions = list_sessions_fn(limit=50)
        except TypeError:
            sessions = list_sessions_fn()
    except Exception:  # noqa: BLE001
        return None
    for session in sessions or ():
        if getattr(session, "session_id", None) == session_id:
            return session
    return None


def _most_recent_session(sessions: Sequence[Any]) -> Optional[Any]:
    if not sessions:
        return None

    def _sort_key(s: Any):
        ts = getattr(s, "updated_at", None)
        if ts is None:
            return (0, 0)
        try:
            epoch = ts.timestamp()
        except Exception:  # noqa: BLE001
            epoch = 0
        return (1, epoch)

    return max(sessions, key=_sort_key)


async def _run_runtime_preflight(
    *,
    message: Any,
    prompt_text: str,
    list_sessions_fn: Callable[..., Sequence[Any]],
    send_chunks: SendChunksFn,
    thread_continuation_fn: Optional[ThreadContinuationFn],
    obsidian_writer_fn: Optional[Callable[..., Any]] = None,
    obsidian_env: Optional[Any] = None,
) -> Optional[EngineeringRouteResult]:
    """Try to handle the message with the runtime loop's intent +
    recall result. Returns a populated :class:`EngineeringRouteResult`
    when the runtime took over (so the legacy flow must skip), or
    ``None`` to fall through to ``conversation_fn`` + intake.

    Short-circuits only the four "pointing-back-at-existing-work"
    intents: ``continue_existing_work``, ``summarize_previous_work``,
    ``execute_existing_step``, ``append_context``. Status / diagnostic
    questions still flow through ``conversation_fn`` because the
    existing layer has the actual session-state responder; new-work
    requests, confirmations, vague text, and pleasantries also flow
    through unchanged so all existing tests keep their contracts.
    """

    # 0. Clarification follow-up — fires before the classifier so a
    # short reply like "1번" or "기존 세션으로 진행" lands on the
    # right session even though those messages don't carry enough
    # context to classify on their own. Only consulted when we have a
    # cached set of candidates from a prior clarification turn.
    stored_candidates = _recall_clarification_candidates(message)
    if stored_candidates:
        selected = _try_select_candidate(prompt_text, stored_candidates)
        if selected is not None:
            join_result = await _handle_clarification_selection(
                message=message,
                selected=selected,
                prompt_text=prompt_text,
                send_chunks=send_chunks,
                thread_continuation_fn=thread_continuation_fn,
            )
            if join_result is not None:
                _clear_clarification_context(message)
                return join_result

    runtime_input = RuntimeInput(
        role_id="gateway",
        message_text=prompt_text,
        channel_id=getattr(getattr(message, "channel", None), "id", None),
        thread_id=_thread_id_for_runtime(message),
        author_id=getattr(getattr(message, "author", None), "id", None),
        message_id=getattr(message, "id", None),
    )
    observation = _observation_for_runtime(runtime_input)
    intent = classify_intent_deterministic(observation, runtime_input)
    if intent.intent_id not in _PREFLIGHT_SHORT_CIRCUIT_INTENTS:
        return None

    recall_fn = make_recall_fn(list_sessions_fn=list_sessions_fn)
    recall = recall_fn(observation, intent, runtime_input)
    decision = decide_default(
        observation,
        intent,
        recall,
        RuntimeResearchPlan(),
        runtime_input,
    )
    if not decision.actions:
        return None

    primary = decision.actions[0]

    # Obsidian save request with a matched session: build a preview and
    # store a pending proposal instead of joining + re-running research.
    # The user explicitly asked to save, not to resume work — we surface
    # the note we *would* write so they can confirm with `저장 승인`.
    if (
        intent.intent_id == RUNTIME_INTENT_EXECUTE_EXISTING_STEP
        and is_obsidian_save_request(prompt_text)
        and primary.action_id == RUNTIME_ACTION_JOIN_SESSION
    ):
        return await _run_obsidian_preview_branch(
            message=message,
            prompt_text=prompt_text,
            decision_payload=primary.payload,
            list_sessions_fn=list_sessions_fn,
            send_chunks=send_chunks,
        )

    if primary.action_id == RUNTIME_ACTION_JOIN_SESSION and thread_continuation_fn is not None:
        # Re-use the legacy join/append helper so research_loop_hook
        # still runs against the resumed session. The helper expects an
        # EngineeringConversationOutcome shape; we synthesise a minimal
        # one carrying the prompt text as ``intake_prompt``.
        synthetic_outcome = EngineeringConversationOutcome(
            content="",
            intake_prompt=prompt_text,
        )
        synthetic_decision = EngineeringRoutingDecision(
            action=ACTION_JOIN,
            matched_session_id=primary.payload.get("session_id"),
            matched_thread_id=primary.payload.get("thread_id"),
            matched_forum_thread_id=primary.payload.get("forum_thread_id"),
            confidence=intent.confidence,
            reason=f"runtime preflight · {intent.intent_id}",
        )
        result = await _handle_join_or_append(
            message=message,
            outcome=synthetic_outcome,
            decision=synthetic_decision,
            intake_prompt=prompt_text,
            send_chunks=send_chunks,
            thread_continuation_fn=thread_continuation_fn,
            research_loop_fn=None,  # Phase 4 MVP: no auto research loop here
        )
        if result is not None:
            return result
        # Fallthrough to clarification when continuation couldn't reach
        # the matched thread (e.g. it's archived) — do NOT silently
        # create a new session. Stash the candidates so the user can
        # reply with "1번" / "기존 세션으로 진행" on the next turn.
        _remember_clarification_candidates(message, recall.candidates)
        await send_chunks(
            message.channel,
            _format_runtime_preflight_clarification(intent.intent_id, recall.candidates),
        )
        return EngineeringRouteResult(
            handled=True,
            error="runtime preflight: continuation thread not reachable",
        )

    if primary.action_id in (
        RUNTIME_ACTION_ASK_CLARIFICATION,
        RUNTIME_ACTION_APPEND_CONTEXT,
    ):
        # ASK_CLARIFICATION: not enough confidence in any session match.
        # APPEND_CONTEXT with no thread_continuation_fn fallback: degrade
        # to clarification rather than silently dropping the user's
        # context append. Either way the message reuses the same
        # template so the operator sees what's missing — and we cache
        # the candidate list so a follow-up "1번" turn resolves cleanly.
        _remember_clarification_candidates(message, recall.candidates)
        await send_chunks(
            message.channel,
            _format_runtime_preflight_clarification(intent.intent_id, recall.candidates),
        )
        return EngineeringRouteResult(handled=True)

    if primary.action_id == RUNTIME_ACTION_APPEND_CONTEXT:
        # Future: route into a dedicated append helper. For Phase 4 MVP
        # we treat append as a join + ack without re-running research.
        if thread_continuation_fn is None:
            await send_chunks(
                message.channel,
                _format_runtime_preflight_clarification(intent.intent_id, recall.candidates),
            )
            return EngineeringRouteResult(handled=True)

    return None


def _thread_id_for_runtime(message: Any) -> Optional[int]:
    channel = getattr(message, "channel", None)
    if channel is None:
        return None
    # Discord threads expose ``id`` (the thread id) and ``parent_id``
    # (the channel they live under). For non-thread channels we leave
    # thread_id None so recall doesn't apply a spurious anchor.
    parent_id = getattr(channel, "parent_id", None)
    if parent_id is None and getattr(channel, "parent", None) is None:
        return None
    return getattr(channel, "id", None)


def _observation_for_runtime(input_: RuntimeInput):
    """Build a minimal observation locally so we don't import the
    runtime loop's default observe (keeps the router's import surface
    small)."""

    from ..agents.runtime.models import RuntimeObservation

    text = input_.message_text or ""
    return RuntimeObservation(
        role_id=input_.role_id,
        message_text=text,
        normalized_text=" ".join(text.lower().split()),
        channel_id=input_.channel_id,
        thread_id=input_.thread_id,
        author_id=input_.author_id,
        message_id=input_.message_id,
        has_attachments=bool(input_.attachments),
        last_proposed_prompt=input_.last_proposed_prompt,
    )


def _format_runtime_preflight_clarification(intent_id: str, candidates) -> str:
    """Render a clarification message for the four short-circuited
    intents when no session matched.

    Surfaces up to three candidate session ids so the operator can
    point at the right one with ``기존 세션 <id>``.
    """

    intent_labels = {
        RUNTIME_INTENT_CONTINUE_EXISTING_WORK: "기존 작업 이어가기",
        RUNTIME_INTENT_SUMMARIZE_PREVIOUS_WORK: "이전 작업 요약",
        RUNTIME_INTENT_EXECUTE_EXISTING_STEP: "기존 작업 후속 실행",
        RUNTIME_INTENT_APPEND_CONTEXT: "기존 작업에 자료 첨부",
    }
    label = intent_labels.get(intent_id, "기존 작업 처리")
    lines = [
        f"**[engineering-agent] 어떤 작업을 가리키시는지 확인이 필요해요.**",
        f"요청 의도: {label}",
        "",
    ]
    if candidates:
        lines.append("최근 열린 후보 세션이에요:")
        for cand in list(candidates)[:3]:
            tail = []
            if cand.task_type:
                tail.append(cand.task_type)
            if cand.thread_id is not None:
                tail.append(f"thread `{cand.thread_id}`")
            tail.append(f"score {cand.score:.2f}")
            head = cand.title or cand.session_id
            lines.append(f"- `{cand.session_id}` — {head} ({' · '.join(tail)})")
        lines.append("")
    lines.append(
        "이어갈 세션 ID를 `기존 세션 <id>` 처럼 답하시거나, 새 작업이라면 `새 작업으로 진행`이라고 답해 주세요."
    )
    return "\n".join(lines)


async def _handle_join_or_append(
    *,
    message: Any,
    outcome: "EngineeringConversationOutcome",
    decision: EngineeringRoutingDecision,
    intake_prompt: str,
    send_chunks: SendChunksFn,
    thread_continuation_fn: Optional[ThreadContinuationFn],
    research_loop_fn: Optional[ResearchLoopFn],
) -> Optional[EngineeringRouteResult]:
    """Try to attach the message to a matched existing session/thread.

    Returns the populated :class:`EngineeringRouteResult` on success or
    ``None`` when no thread could be located — caller is responsible for
    surfacing a "not found" notice. ``ACTION_APPEND_CONTEXT`` skips the
    research loop entirely; ``ACTION_JOIN`` runs it like the legacy
    continuation path so the resumed thread stays current.
    """

    if thread_continuation_fn is None:
        return None
    continuation = await _maybe_await(
        thread_continuation_fn(
            message=message,
            prompt=intake_prompt,
            write_requested=outcome.write_requested,
            thread_topic=outcome.thread_topic,
        )
    )
    if continuation is None:
        return None

    continued_session = continuation.session
    continued_session = _maybe_persist_research_pack(
        continued_session,
        research_pack=outcome.research_pack,
        collection_outcome=outcome.collection_outcome,
    )
    session_id = getattr(continued_session, "session_id", None)
    thread_id = continuation.thread_id
    if continuation.message:
        await send_chunks(message.channel, continuation.message)

    research_loop_report: Optional[EngineeringResearchLoopReport] = None
    is_append_only = decision.action == ACTION_APPEND_CONTEXT
    if (
        not is_append_only
        and research_loop_fn is not None
        and continued_session is not None
    ):
        research_loop_report = await _run_research_loop_hook(
            research_loop_fn=research_loop_fn,
            message=message,
            session=continued_session,
            prompt_text=intake_prompt,
            send_chunks=send_chunks,
            collection_outcome=outcome.collection_outcome,
            research_pack=outcome.research_pack,
            role_for_research=outcome.role_for_research,
            thread_id=thread_id,
        )

    return EngineeringRouteResult(
        handled=True,
        conversation_message=outcome.content or None,
        kickoff_message=continuation.message,
        session_id=session_id,
        thread_id=thread_id,
        research_loop_report=research_loop_report,
        routing_decision=decision,
    )


def _format_clarification_message(decision: EngineeringRoutingDecision) -> str:
    """Render the ASK action's prompt for the user.

    Uses up to 3 candidate summaries so the operator can pick which open
    session to join, or ask for a new one. Falls back to ``decision.reason``
    when no candidates are available so the message is never empty.
    """

    lines = ["**[engineering-agent] 어느 작업에 합류할까요?**"]
    if decision.reason:
        lines.append(decision.reason)
    if decision.candidate_summaries:
        lines.append("")
        for idx, candidate in enumerate(decision.candidate_summaries[:3], start=1):
            tail = []
            if candidate.task_type:
                tail.append(candidate.task_type)
            if candidate.thread_id is not None:
                tail.append(f"thread `{candidate.thread_id}`")
            tail.append(f"score {candidate.score:.2f}")
            lines.append(
                f"{idx}. `{candidate.session_id}` — {candidate.title} ({' · '.join(tail)})"
            )
    lines.append("")
    lines.append(
        "이어갈 세션 ID를 `기존 세션 <id>`처럼 답하시거나, `새 작업으로 진행`이라고 답해 주세요."
    )
    return "\n".join(lines)


def _maybe_persist_research_pack(
    session: Any,
    *,
    research_pack: Any,
    collection_outcome: Any,
) -> Any:
    """Persist the conversation-layer research pack onto a fresh session.

    Called immediately after intake (or thread continuation) creates the
    session, so the pack lands in ``session.extra["research_pack"]``
    independently of whether the downstream research loop runs, succeeds,
    or short-circuits as ``insufficient``. The forum research-loop hook
    persists again later for synthesis/collection metadata; the helper is
    idempotent so the double-write is safe.

    Returns the (possibly updated) session. No-op when ``session`` is None
    or there is nothing to persist.
    """

    if session is None:
        return session
    if research_pack is None and collection_outcome is None:
        return session
    return persist_research_artifacts(
        session,
        research_pack,
        collection_outcome=collection_outcome,
    )


async def _run_research_loop_hook(
    *,
    research_loop_fn: ResearchLoopFn,
    message: Any,
    session: Any,
    prompt_text: str,
    send_chunks: SendChunksFn,
    collection_outcome: Any = None,
    research_pack: Any = None,
    role_for_research: Optional[str] = None,
    thread_id: Optional[int] = None,
) -> EngineeringResearchLoopReport:
    """Call *research_loop_fn* with the message context and surface its result.

    The hook receives the autonomous collector's outputs
    (``collection_outcome``/``research_pack``) plus the working thread
    id so the production wiring can post the collection summary to the
    research forum and start a deliberation chain in the same thread —
    without the router needing to know the publisher/deliberation APIs.

    Errors are caught and reported via a ``⚠️`` chat line so a research
    loop failure does not undo the intake + kickoff that already landed.
    """

    attachments = extract_message_attachments(message)
    try:
        raw = await _maybe_await(
            research_loop_fn(
                session=session,
                message_text=prompt_text,
                attachments=attachments,
                channel=message.channel,
                collection_outcome=collection_outcome,
                research_pack=research_pack,
                role_for_research=role_for_research,
                thread_id=thread_id,
            )
        )
    except Exception as exc:  # noqa: BLE001 - non-fatal; report and return
        report = EngineeringResearchLoopReport(error=str(exc))
        await send_chunks(
            message.channel,
            f"⚠️ research loop 실패: {exc}",
        )
        return report

    report = _coerce_research_loop_report(raw)
    # Persist forum publication / open-call signals onto session.extra
    # so the diagnostic responder can describe the live setup later
    # without round-tripping through the publish object. Best-effort —
    # a cache write failure must not block the user-visible reply.
    try:
        persist_research_forum_status(session=session, report=report)
    except Exception:  # noqa: BLE001 - persistence is non-fatal
        pass
    if report.follow_up_message:
        await send_chunks(message.channel, report.follow_up_message)
    if report.forum_status_message:
        await send_chunks(message.channel, report.forum_status_message)
    if report.error and not report.follow_up_message and not report.forum_status_message:
        await send_chunks(message.channel, f"⚠️ research loop: {report.error}")
    return report


def persist_research_forum_status(
    *,
    session: Any,
    report: EngineeringResearchLoopReport,
) -> None:
    """Merge the research-loop report's mode/kickoff signals into session.extra.

    Writes the canonical Phase B keys so the diagnostic / status
    responder can describe the live setup later:

    - ``forum_comment_mode`` — ``"member-bots"`` or ``"gateway"``.
    - ``research_forum_thread_id`` / ``research_forum_thread_url`` — the
      forum thread the directive went into (or would go into).
    - ``research_open_call_posted`` — ``True`` / ``False`` / ``None``
      depending on whether the gateway posted the
      ``[research-open:<sid>]`` directive itself. ``None`` means the
      path didn't reach the kickoff post.
    - ``research_open_call_error`` — stringified failure reason when
      ``research_open_call_posted`` is ``False``; cleared on retry
      success.

    For backward compatibility the legacy ``forum_kickoff_posted`` /
    ``forum_kickoff_error`` keys are kept in sync — bot-side
    ``_persist_forum_comment_mode_to_session`` and existing diagnostic
    tests still consume those names.

    No-op when ``session`` has no ``session_id`` (e.g. lightweight test
    stubs) so callers don't need to special-case the path.
    """

    if session is None:
        return
    session_id = getattr(session, "session_id", None)
    if not session_id:
        return

    extra_updates: dict[str, Any] = {}
    if report.forum_comment_mode is not None:
        extra_updates["forum_comment_mode"] = report.forum_comment_mode
    if report.forum_thread_id is not None:
        extra_updates["research_forum_thread_id"] = report.forum_thread_id
    if report.forum_thread_url is not None:
        extra_updates["research_forum_thread_url"] = report.forum_thread_url

    if report.forum_comment_mode == "member-bots":
        # Always stamp the open-call signal pair — including ``None`` —
        # so a retry that succeeded clears the previous failure note.
        extra_updates["research_open_call_posted"] = report.kickoff_posted
        extra_updates["research_open_call_error"] = report.kickoff_error
        extra_updates["forum_kickoff_posted"] = report.kickoff_posted
        extra_updates["forum_kickoff_error"] = report.kickoff_error

    if not extra_updates:
        return

    try:
        from dataclasses import replace
        from datetime import datetime

        from ..agents.workflow_state import update_session
    except Exception:  # noqa: BLE001 - degrade silently for partial installs
        return

    existing_extra = dict(getattr(session, "extra", {}) or {})
    merged = {**existing_extra, **extra_updates}
    try:
        updated = replace(session, extra=merged)
    except TypeError:
        # ``replace`` only works on dataclasses — test stubs that use
        # plain objects fall through to mutating the live extra dict so
        # at least the in-memory session reflects the change.
        try:
            live = getattr(session, "extra", None)
            if isinstance(live, dict):
                live.update(extra_updates)
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        update_session(updated, now=datetime.now().astimezone())
    except Exception:  # noqa: BLE001
        pass


async def make_default_research_loop(
    *,
    session: Any,
    message_text: str,
    attachments: Sequence[Any],
    channel: Any,
    collection_outcome: Any = None,
    research_pack: Any = None,
    role_for_research: Optional[str] = None,
    thread_id: Optional[int] = None,
    forum_publisher: Optional[Callable[..., Awaitable[Any]]] = None,
    deliberation_runner: Optional[Callable[..., Any]] = None,
    post_to_thread: Optional[Callable[[int, str], Awaitable[None]]] = None,
    forum_comment_mode: Optional[str] = None,
    post_to_forum_thread: Optional[Callable[[int, str], Awaitable[None]]] = None,
) -> EngineeringResearchLoopReport:
    """Default plumbing that runs after intake + kickoff land.

    1. If ``research_pack`` is non-None and ``forum_publisher`` is wired,
       publish the collection summary to ``#운영-리서치``. The publisher
       is expected to return a value with ``.thread_id`` / ``.thread_url``
       / ``.error`` (e.g. :class:`ForumPostOutcome`).
      2. ``forum_comment_mode``:
       - ``"member-bots"`` (default) — after the forum post lands, the
         gateway posts one open-call ``[research-open:<sid>]`` directive.
         Each member bot's ``on_message`` handler sees the same job brief,
         gathers its own role-shaped evidence, and posts its own take.
       - ``"gateway"`` (legacy) — gateway runs the whole deliberation
         and pipes role takes back into the working thread (preserves
         pre-multi-bot behaviour for tests/operators without member tokens).
    3. If ``deliberation_runner`` is wired, run the deliberation loop
       with the research pack and post role takes + tech-lead synthesis
       into the working thread (via ``post_to_thread``) — only in
       ``gateway`` mode. ``member-bots`` mode skips this so member bots
       can speak with their own personas.

    All hooks are optional — when ``None`` we simply skip that step. The
    function never raises so ``_run_research_loop_hook`` keeps the bot
    alive even if a downstream module breaks.
    """

    follow_up: Optional[str] = None
    forum_status: Optional[str] = None
    forum_thread_id: Optional[int] = None
    forum_thread_url: Optional[str] = None
    insufficient = False
    error: Optional[str] = None
    # Tracked through the member-bots branch so the report can describe
    # whether the gateway actually got the open-call directive in front of
    # the role bots, plus the failure reason if it didn't. Stays ``None``
    # in gateway mode and in any code path that never reaches the kickoff
    # post (forum publish failed, post_to_forum_thread missing, ...).
    kickoff_posted: Optional[bool] = None
    kickoff_error: Optional[str] = None
    posted = False

    has_pack = research_pack is not None

    # Resolve the forum comment mode lazily so callers can override for tests
    # without depending on env state.
    if forum_comment_mode is None:
        try:
            from ..agents.research_collector import resolve_forum_comment_mode
        except Exception:  # noqa: BLE001
            forum_comment_mode = "member-bots"
        else:
            forum_comment_mode = resolve_forum_comment_mode()

    # 1. Forum publish
    if has_pack and forum_publisher is not None:
        try:
            forum_outcome = await _maybe_await(
                forum_publisher(
                    pack=research_pack,
                    collection_outcome=collection_outcome,
                    role=role_for_research,
                )
            )
        except Exception as exc:  # noqa: BLE001
            error = f"forum publish 실패: {exc}"
        else:
            posted = bool(getattr(forum_outcome, "posted", False))
            forum_thread_id = _safe_int(getattr(forum_outcome, "thread_id", None))
            forum_thread_url = _optional_str(getattr(forum_outcome, "thread_url", None))
            if posted:
                forum_status = "운영-리서치에 자료 정리를 남겼어요."
            else:
                fail_reason = _optional_str(getattr(forum_outcome, "error", None))
                forum_status = (
                    "운영-리서치 게시는 잠시 미뤄졌어요"
                    + (f" — {fail_reason}." if fail_reason else ".")
                )

        # member-bots mode: post one open-call directive into the freshly
        # created forum thread. Each member bot decides independently whether
        # to contribute, instead of following a gateway-authored speaking order.
        if (
            forum_comment_mode == "member-bots"
            and posted
            and forum_thread_id is not None
            and post_to_forum_thread is not None
            and session is not None
        ):
            try:
                from .engineering_team_runtime import research_open_call_directive
            except Exception:  # noqa: BLE001
                kickoff = None
            else:
                try:
                    kickoff = research_open_call_directive(session)
                except Exception:  # noqa: BLE001
                    kickoff = None
            if kickoff:
                kickoff_message = (
                    "자료 수집 seed를 올렸어요. 이제 각 멤버 봇이 자기 정책에 맞게 "
                    "추가 조사하고, 필요한 take를 독립적으로 남깁니다.\n\n"
                    f"{kickoff}"
                )
                from .research_forum import chunk_for_discord_message
                pieces = chunk_for_discord_message(kickoff_message) or (
                    kickoff_message,
                )
                try:
                    for piece in pieces:
                        await post_to_forum_thread(forum_thread_id, piece)
                except Exception as exc:  # noqa: BLE001
                    kickoff_posted = False
                    kickoff_error = f"forum kickoff 게시 실패: {exc}"
                    error = (error + " · " if error else "") + kickoff_error
                else:
                    kickoff_posted = True
                    # Replace the gateway-flavoured "자료 정리를 남겼어요."
                    # blurb with a member-bots-aware status. Operators were
                    # otherwise seeing "역할별 댓글 0건"-style wording even
                    # though each member bot is responsible for the role
                    # comment in this mode.
                    forum_status = _format_member_bots_forum_status(
                        thread_id=forum_thread_id,
                        thread_url=forum_thread_url,
                        kickoff_posted=True,
                        kickoff_error=None,
                    )
            else:
                # Couldn't compute the open-call directive (import failed or
                # research_open_call_directive raised) — record the
                # member-bots mode signal so diagnostics know the gateway
                # tried but the directive never made it into the thread.
                kickoff_posted = False
                kickoff_error = "research_open_call_directive 미생성"
                error = (error + " · " if error else "") + kickoff_error
        elif forum_comment_mode == "member-bots" and posted:
            # Mode is correct but the caller didn't wire ``post_to_forum_thread``
            # (e.g. early dev runs with a stub publisher). Surface a
            # member-bots-aware status anyway so the gateway summary doesn't
            # imply the gateway is going to post role comments.
            forum_status = _format_member_bots_forum_status(
                thread_id=forum_thread_id,
                thread_url=forum_thread_url,
                kickoff_posted=None,
                kickoff_error=None,
            )

    # 2. Deliberation in the working thread — gateway mode only.
    # member-bots mode hands the deliberation to each member bot via the
    # open-call protocol, so the gateway does not impersonate them here.
    should_run_gateway_deliberation = (
        has_pack
        and session is not None
        and thread_id is not None
        and deliberation_runner is not None
        and forum_comment_mode == "gateway"
    )
    if should_run_gateway_deliberation:
        try:
            deliberation_result = deliberation_runner(
                session=session,
                research_pack=research_pack,
            )
            deliberation_result = await _maybe_await(deliberation_result)
        except Exception as exc:  # noqa: BLE001
            error = (error + " · " if error else "") + f"deliberation 실패: {exc}"
        else:
            if post_to_thread is not None and deliberation_result is not None:
                rendered = list(getattr(deliberation_result, "turns", ()) or [])
                synthesis_text = _optional_str(
                    getattr(deliberation_result, "synthesis_text", None)
                )
                from .research_forum import chunk_for_discord_message
                try:
                    for record in rendered:
                        text = _optional_str(getattr(record, "rendered", None))
                        if not text:
                            continue
                        for piece in chunk_for_discord_message(text) or (text,):
                            await post_to_thread(thread_id, piece)
                    if synthesis_text:
                        for piece in (
                            chunk_for_discord_message(synthesis_text)
                            or (synthesis_text,)
                        ):
                            await post_to_thread(thread_id, piece)
                except Exception as exc:  # noqa: BLE001
                    error = (error + " · " if error else "") + (
                        f"thread 게시 실패: {exc}"
                    )

    if not has_pack:
        # No autonomous collector pack means the conversation already asked
        # the user for materials. Nothing to publish — surface an "insufficient"
        # signal so the gateway can short-circuit downstream wiring.
        insufficient = True

    return EngineeringResearchLoopReport(
        follow_up_message=follow_up,
        forum_status_message=forum_status,
        forum_thread_id=forum_thread_id,
        forum_thread_url=forum_thread_url,
        insufficient=insufficient,
        error=error,
        forum_comment_mode=forum_comment_mode,
        kickoff_posted=kickoff_posted,
        kickoff_error=kickoff_error,
    )


def _format_member_bots_forum_status(
    *,
    thread_id: Optional[int],
    thread_url: Optional[str],
    kickoff_posted: Optional[bool],
    kickoff_error: Optional[str],
) -> str:
    """Render the member-bots forum status surface.

    Avoids the gateway-mode "역할별 댓글 N건" wording — in member-bots
    mode the gateway never posts role comments by design, so reporting
    "0건" looks like a failure to operators. Instead we describe the
    mode, the open-call directive status, and where to actually find
    the role comments (the forum thread itself).
    """

    lines: list[str] = ["✅ 운영-리서치 forum 게시 완료"]
    if thread_url:
        lines.append(f"thread: {thread_url}")
    elif thread_id is not None:
        lines.append(f"thread id: {thread_id}")
    lines.append("모드: member-bots (각 멤버 봇이 자기 계정으로 댓글)")
    if kickoff_posted is True:
        lines.append("open-call directive: 게시 완료")
    elif kickoff_posted is False:
        reason = kickoff_error or "원인 미확인"
        lines.append(f"open-call directive: 게시 실패 — {reason}")
    else:
        # ``post_to_forum_thread`` wasn't wired by the caller, so the
        # gateway never even tried to post the directive. Operators
        # need to know that — otherwise they'd assume the gateway is
        # going to post role comments itself, like in legacy mode.
        lines.append(
            "open-call directive: 미게시 (post_to_forum_thread 미연결)"
        )
    lines.append(
        "각 멤버 봇의 후속 댓글은 운영-리서치 thread에서 확인하세요."
    )
    return "\n".join(lines)


def _coerce_research_loop_report(raw: Any) -> EngineeringResearchLoopReport:
    if isinstance(raw, EngineeringResearchLoopReport):
        return raw
    if raw is None:
        return EngineeringResearchLoopReport()
    raw_kickoff_posted = getattr(raw, "kickoff_posted", None)
    return EngineeringResearchLoopReport(
        follow_up_message=_optional_str(getattr(raw, "follow_up_message", None)),
        forum_status_message=_optional_str(getattr(raw, "forum_status_message", None)),
        forum_thread_id=_safe_int(getattr(raw, "forum_thread_id", None)),
        forum_thread_url=_optional_str(getattr(raw, "forum_thread_url", None)),
        insufficient=bool(getattr(raw, "insufficient", False)),
        error=_optional_str(getattr(raw, "error", None)),
        forum_comment_mode=_optional_str(getattr(raw, "forum_comment_mode", None)),
        kickoff_posted=(
            bool(raw_kickoff_posted) if raw_kickoff_posted is not None else None
        ),
        kickoff_error=_optional_str(getattr(raw, "kickoff_error", None)),
    )


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_outcome(
    raw: Any,
    *,
    prompt_text: str,
) -> EngineeringConversationOutcome:
    if isinstance(raw, EngineeringConversationOutcome):
        return raw
    if isinstance(raw, str):
        return EngineeringConversationOutcome(content=raw)
    # Allow the conversation layer to ship a custom dataclass with a
    # compatible ``content`` attribute.  We extract the optional fields
    # defensively so tomorrow's API additions don't break us today.
    content = str(getattr(raw, "content", "") or "")
    confirmed = bool(getattr(raw, "confirmed", False))
    intake_prompt_raw = getattr(raw, "intake_prompt", None)
    intake_prompt = (
        str(intake_prompt_raw).strip()
        if intake_prompt_raw is not None
        else None
    )
    write_requested = bool(getattr(raw, "write_requested", False))
    thread_topic_raw = getattr(raw, "thread_topic", None)
    thread_topic = (
        str(thread_topic_raw).strip()
        if thread_topic_raw is not None
        else None
    )
    # Optional autonomous-collector context. ``EngineeringConversationResponse``
    # surfaces these directly; other shapes can omit them safely.
    research_pack = getattr(raw, "research_pack", None)
    collection_outcome = getattr(raw, "collection_outcome", None)
    role_raw = getattr(raw, "role_for_research", None)
    role_for_research = (
        str(role_raw).strip() if role_raw is not None else None
    ) or None
    is_status_query = bool(getattr(raw, "is_status_query", False))
    return EngineeringConversationOutcome(
        content=content,
        confirmed=confirmed,
        intake_prompt=intake_prompt or None,
        write_requested=write_requested,
        thread_topic=thread_topic or None,
        research_pack=research_pack,
        collection_outcome=collection_outcome,
        role_for_research=role_for_research,
        is_status_query=is_status_query,
    )


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def extract_user_links_from_message(
    message: Any,
    prompt_text: Optional[str] = None,
) -> tuple[str, ...]:
    """Pull URLs out of the user's message body.

    Lazily delegates to :func:`research_collector.extract_urls` so we get
    the same regex + dedup the collector uses internally. Returns an empty
    tuple if the helper isn't importable (e.g. during a partial install).
    """

    text = (prompt_text or getattr(message, "content", "") or "")
    if not text:
        return ()
    try:
        from ..agents.research_collector import extract_urls
    except Exception:  # noqa: BLE001
        return ()
    return tuple(extract_urls(text))


def extract_message_attachments(message: Any) -> tuple[Any, ...]:
    """Return the message's attachments as a stable tuple, discord.py-agnostic.

    discord.py exposes ``message.attachments`` as a list of ``Attachment``
    objects, but tests pass plain dataclasses or dicts. We accept any iterable
    and drop ``None`` entries so the engineering conversation layer can rely
    on a clean sequence regardless of the Discord shape.
    """

    raw = getattr(message, "attachments", None)
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        return tuple(item for item in raw if item is not None)
    try:
        return tuple(item for item in raw if item is not None)
    except TypeError:
        return ()


def _normalize_channel_name(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).strip().lstrip("#").lower()


def _optional_int_env(name: str) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            f"{name} must be an integer value, got: {value!r}"
        ) from exc


def _optional_string_env(name: str) -> Optional[str]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = raw.strip()
    return value or None
