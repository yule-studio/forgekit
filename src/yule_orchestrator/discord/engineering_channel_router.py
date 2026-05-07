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
from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence, Union

from ..agents.coding.authorization import (
    CodingAuthorizationProposal,
    format_authorization_message,
    recommend_authorization,
)
from ..agents.coding.job import (
    STATUS_READY,
    build_coding_job_from_proposal,
)
from ..agents.obsidian.approval import (
    ObsidianApprovalError,
    build_save_proposal,
    execute_pending_proposal,
    get_pending_proposal,
    is_obsidian_approval,
    is_obsidian_save_request,
    store_pending_proposal,
)
from ..agents.research.persistence import persist_research_artifacts
from ..agents.routing import (
    ACTION_APPEND_CONTEXT,
    ACTION_ASK,
    ACTION_CREATE,
    ACTION_JOIN,
    EngineeringRoutingDecision,
    _explicit_session_request,
    decide_routing,
    is_bot_echo_phrase,
    is_command_only_prompt,
    is_non_actionable_prompt,
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

    # Coding authorization gate — handles the two new MVP intents:
    #   ① "코딩 권한 제안" / "수정 권한 제안" → build proposal preview,
    #   ② "수정 승인" / "이대로 구현 진행" / "구현 시작" → flip pending
    #      proposal to a ready CodingJob.
    # Runs before the runtime preflight so a bare approval phrase never
    # gets re-classified as a new task.
    if list_sessions_fn is not None:
        coding = await _run_coding_authorization_gate(
            message=message,
            prompt_text=prompt_text,
            list_sessions_fn=list_sessions_fn,
            send_chunks=send_chunks,
        )
        if coding is not None:
            return coding

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

    # Explicit-session-id JOIN — the user typed `기존 세션 <id>` so we
    # already know which session they want; bypass preflight and
    # conversation_fn so the runtime classifier doesn't intercept
    # ("이어가" continue verbs would otherwise route into recall).
    # The append payload prefers a cached canonical_prompt over the
    # routing-command reply itself so the JOIN never appends "기존
    # 세션 <id> 이어가" as the resumed task body.
    if thread_continuation_fn is not None:
        explicit_session_id = _explicit_session_request(prompt_text)
        if explicit_session_id:
            try:
                from ..agents.workflow_state import load_session as _load_session
                target_session = _load_session(explicit_session_id)
            except Exception:  # noqa: BLE001 - lookup failures fall through to legacy flow
                target_session = None
            if target_session is not None:
                explicit_canonical = _recall_clarification_canonical_prompt(message)
                join_intake = (explicit_canonical or prompt_text or "").strip()
                if join_intake:
                    target_extra = dict(getattr(target_session, "extra", None) or {})
                    forum_thread_id = (
                        target_extra.get("research_forum_thread_id")
                        or target_extra.get("forum_thread_id")
                    )
                    try:
                        forum_id_int = (
                            int(forum_thread_id)
                            if forum_thread_id is not None
                            else None
                        )
                    except (TypeError, ValueError):
                        forum_id_int = None
                    synthetic_outcome = EngineeringConversationOutcome(
                        content="",
                        intake_prompt=join_intake,
                    )
                    synthetic_decision = EngineeringRoutingDecision(
                        action=ACTION_JOIN,
                        matched_session_id=getattr(target_session, "session_id", None),
                        matched_thread_id=getattr(target_session, "thread_id", None),
                        matched_forum_thread_id=forum_id_int,
                        confidence="high",
                        reason=(
                            f"explicit '기존 세션 {explicit_session_id}' override"
                        ),
                    )
                    explicit_result = await _handle_join_or_append(
                        message=message,
                        outcome=synthetic_outcome,
                        decision=synthetic_decision,
                        intake_prompt=join_intake,
                        send_chunks=send_chunks,
                        thread_continuation_fn=thread_continuation_fn,
                        research_loop_fn=None,
                    )
                    if explicit_result is not None:
                        _clear_clarification_context(message)
                        return explicit_result

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
            research_loop_fn=research_loop_fn,
            obsidian_writer_fn=obsidian_writer_fn,
            obsidian_env=obsidian_env,
        )
        if preflight is not None:
            return preflight

    # Clarification follow-up CREATE branch — when the prior turn
    # showed candidates and the user replied "새 작업으로 진행" (or a
    # verbose paraphrase like "기존 후보들은 다 제거해주고 새 작업으로
    # 진행해줘"), the cached canonical_prompt is the actionable
    # Research원문 — NOT the user's routing-command reply. Drive
    # intake + kickoff + research_loop with the canonical so
    # session.prompt / forum body / role-bot context all see the real
    # task. Without a cached canonical we refuse outright (no zombie
    # session whose prompt is the routing-command phrase).
    if _looks_like_new_work_selection(prompt_text):
        clarification_canonical = _recall_clarification_canonical_prompt(message)
        clarification_candidates = _recall_clarification_candidates(message)
        clarification_cache_present = (
            _clarification_context_key(message) in _GATEWAY_CLARIFICATION_CONTEXT
        )
        if clarification_canonical:
            create_result = await _drive_clarification_create_new_work(
                message=message,
                canonical_prompt=clarification_canonical,
                intake_fn=intake_fn,
                thread_kickoff_fn=thread_kickoff_fn,
                send_chunks=send_chunks,
                research_loop_fn=research_loop_fn,
            )
            if create_result is not None:
                _clear_clarification_context(message)
                return create_result
        elif clarification_candidates or clarification_cache_present:
            # Older cache entry from before the canonical_prompt fix
            # (or candidates lost during truncation) — refuse to spawn
            # a session with the routing-command phrase as session.prompt.
            await send_chunks(
                message.channel,
                (
                    "직전 clarification 캐시에서 원문 task 본문을 찾지 못했어요.\n"
                    "진행할 업무 원문을 다시 알려주세요. \"새 작업으로 진행\"은 "
                    "routing 명령이라 작업 본문으로 사용할 수 없어요."
                ),
            )
            _clear_clarification_context(message)
            return EngineeringRouteResult(handled=True)

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

    # Clarification follow-up canonical-prompt rewrite. Last turn's
    # clarification stashed the original task description (e.g. "[Research]
    # 하네스 엔지니어링…"). When the user replies with a routing-command
    # phrase ("새 작업으로 진행" / "기존 세션 abc"), we substitute the
    # cached canonical text into ``intake_prompt`` so every downstream
    # writer (intake_fn → session.prompt, _handle_join_or_append → append
    # payload, research_loop_fn → forum body / research query) sees the
    # real task instead of the routing-command reply. ``decide_routing``
    # still receives the user's literal reply via ``routing_input`` so
    # explicit-session and "새 작업으로 진행" parsing still fire.
    clarification_canonical = _recall_clarification_canonical_prompt(message)
    if clarification_canonical:
        intake_prompt = clarification_canonical
        # The follow-up reply is the user's decision after seeing the
        # original prompt last turn, so it's confirmed even when the
        # literal text is just "새 작업으로 진행" / "1번".
        confirmed = True

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
    # Routing input vs intake_prompt:
    # - ``routing_input`` is what ``decide_routing`` parses for explicit
    #   session ids ("기존 세션 abc"), explicit new-work signals
    #   ("새 작업으로 진행"), and similarity scoring against open sessions.
    # - ``intake_prompt`` is what we persist as ``session.prompt`` (CREATE)
    #   or hand to ``_handle_join_or_append`` as the append payload (JOIN).
    # In a clarification follow-up these diverge — the user's reply is the
    # routing signal but the cached canonical_prompt is the task content.
    if clarification_canonical:
        routing_input = (prompt_text or "").strip() or clarification_canonical
    else:
        routing_input = intake_prompt or prompt_text
    routing_prompt = routing_input
    routing_thread_id = _thread_id_for_runtime(message)

    # Confirm-routing + bot-echo guard. The firewall rejects when the
    # user's reply is a non-actionable phrase AND we have no canonical
    # task description to substitute. With a stored canonical_prompt
    # the rewrite above already swapped intake_prompt to actionable
    # text so a CREATE/JOIN can land safely on the canonical content.
    if (
        is_non_actionable_prompt(routing_input)
        and not clarification_canonical
        and routing_thread_id is None
    ):
        if is_bot_echo_phrase(routing_input):
            clarification = (
                "방금 받은 메시지가 gateway가 보낸 안내문 문구와 똑같아서 "
                "새 작업으로 등록하지 않았어요.\n"
                "진행할 업무 원문을 다시 알려주세요. 짧은 확인 문구는 "
                "작업 본문으로 사용할 수 없어요."
            )
        else:
            clarification = (
                "진행할 업무 원문을 다시 알려주세요. \"이대로 진행\" / "
                "\"새 작업으로 진행\" 같은 확인 문구는 작업 본문으로 "
                "사용할 수 없어요.\n"
                "기존 작업을 이어가려면 `기존 세션 <id>`로 답해 주세요."
            )
        await send_chunks(message.channel, clarification)
        return EngineeringRouteResult(
            handled=True,
            conversation_message=outcome.content or None,
        )

    try:
        routing_decision = decide_routing(
            prompt=routing_prompt,
            thread_id=routing_thread_id,
        )
    except Exception as exc:  # noqa: BLE001 - routing must not crash the bot
        routing_decision = EngineeringRoutingDecision(
            action=ACTION_CREATE,
            reason=f"decide_routing fallback: {exc}",
            confidence="low",
        )

    # Clarification follow-up cleanup — the canonical_prompt is about to
    # be consumed (CREATE writes it as session.prompt, JOIN/append uses
    # it as the payload). Drop the cache so the next message in this
    # channel does not re-use the same canonical against an unrelated
    # routing-command reply.
    if clarification_canonical:
        _clear_clarification_context(message)

    if routing_decision.action == ACTION_ASK:
        # Stash the routing decision's candidates AND the canonical
        # task description so the next-turn follow-up ("1번" / "기존
        # 세션 …" / "새 작업으로 진행") joins the right session OR
        # creates a new one with the real intake_prompt — never with
        # the routing-command phrase. ``intake_prompt`` here is the
        # canonical task text (long Research원문 in the live MVP bug)
        # that ``decide_routing`` just scored against.
        _remember_clarification_candidates(
            message,
            routing_decision.candidate_summaries,
            canonical_prompt=intake_prompt,
        )
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

    # Defensive intake guard — even if the upstream routing guard
    # didn't trip (e.g. thread_id was set but no anchor matched and
    # token scoring returned CREATE), we must NOT persist a zombie
    # session whose prompt is "새 작업으로 진행" / "이대로 진행" /
    # a bot-echo paste-back. The CREATE branch is the last writer of
    # session.prompt, so this is the final firewall.
    if is_non_actionable_prompt(intake_prompt):
        clarification = (
            "진행할 업무 원문을 다시 알려주세요. \"이대로 진행\" / "
            "\"새 작업으로 진행\" 같은 확인 문구나 gateway 안내문은 "
            "작업 본문으로 사용할 수 없어요."
        )
        await send_chunks(message.channel, clarification)
        return EngineeringRouteResult(
            handled=True,
            conversation_message=outcome.content or None,
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
    # Phase 1 wiring: stash active role selection on the new session so
    # downstream research_loop / work_report / status diagnostic all
    # see the same set without re-running the rule bank.
    session = _persist_role_selection(session, intake_prompt)
    session = _persist_lifecycle_mode(session, intake_prompt)
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
            # Phase 1 stabilisation: stamp the new work-thread id back
            # on session.thread_id so status / Obsidian / continuation
            # lookups by thread anchor resolve cleanly. Without this
            # the session row stayed thread-less in SQLite even after
            # a successful kickoff.
            session = _persist_thread_id(session, thread_id)

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

    # Phase 4: post a deterministic work report once the research +
    # synthesis pass closes. Always best-effort; a failure here keeps
    # the existing reply chain intact.
    await _emit_work_report_preview(
        message=message,
        session=session,
        canonical_prompt=intake_prompt,
        send_chunks=send_chunks,
        collection_outcome=outcome.collection_outcome,
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


# MVP closure refactor — clarification cache + selection helpers were
# extracted to :mod:`discord.engineering.clarification` so the router
# stays focused on flow orchestration. The router-prefixed (``_``)
# names below are kept as aliases so existing tests / callers that
# import from ``engineering_channel_router`` keep working.
from .engineering.clarification import (
    GATEWAY_CLARIFICATION_CONTEXT as _GATEWAY_CLARIFICATION_CONTEXT,
    clarification_context_key as _clarification_context_key,
    clear_clarification_context as _clear_clarification_context,
    looks_like_new_work_selection as _looks_like_new_work_selection,
    recall_clarification_candidates as _recall_clarification_candidates,
    recall_clarification_canonical_prompt as _recall_clarification_canonical_prompt,
    remember_clarification_candidates as _remember_clarification_candidates,
    try_select_candidate as _try_select_candidate,
)


async def _drive_clarification_create_new_work(
    *,
    message: Any,
    canonical_prompt: str,
    intake_fn: "IntakeFn",
    thread_kickoff_fn: "ThreadKickoffFn",
    send_chunks: SendChunksFn,
    research_loop_fn: Optional["ResearchLoopFn"],
) -> Optional[EngineeringRouteResult]:
    """Drive intake → kickoff → research_loop with a cached canonical
    prompt when the user's clarification follow-up was "새 작업으로
    진행".

    Bypasses ``conversation_fn`` and ``decide_routing`` entirely so
    the new ``session.prompt`` is the canonical task text — never the
    routing-command phrase the user just typed. Defensive guards
    refuse non-actionable canonicals (the same firewall as the legacy
    intake path).
    """

    if is_non_actionable_prompt(canonical_prompt):
        clarification = (
            "방금 받은 메시지는 routing 명령(`새 작업으로 진행`) 이라 "
            "session.prompt 로 쓸 수 없고, 직전 clarification 캐시에서도 "
            "원문 task 본문을 찾지 못했어요. 진행할 업무 원문을 다시 "
            "알려주세요."
        )
        await send_chunks(message.channel, clarification)
        return EngineeringRouteResult(handled=True)

    try:
        intake = intake_fn(
            prompt=canonical_prompt,
            write_requested=False,
            channel_id=getattr(getattr(message, "channel", None), "id", None),
            user_id=getattr(getattr(message, "author", None), "id", None),
        )
        intake = await _maybe_await(intake)
    except Exception as exc:  # noqa: BLE001 - surface error to user, do not crash bot
        await send_chunks(message.channel, f"⚠️ engineer intake 실패: {exc}")
        return EngineeringRouteResult(handled=True, error=str(exc))

    intake_message = getattr(intake, "message", None)
    session = getattr(intake, "session", None)
    plan = getattr(intake, "plan", None)
    # Phase 1 wiring: stash active role selection on the freshly
    # spawned session before kickoff / research_loop / work_report
    # consume it.
    session = _persist_role_selection(session, canonical_prompt)
    session = _persist_lifecycle_mode(session, canonical_prompt)
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
            topic=None,
        )
    except Exception as exc:  # noqa: BLE001
        kickoff_error = str(exc)
        await send_chunks(message.channel, f"⚠️ thread kickoff 실패: {exc}")
        kickoff = None
    if kickoff is not None:
        kickoff_message = getattr(kickoff, "message", None)
        thread_id = getattr(kickoff, "thread_id", None)
        # Phase 1 stabilisation: stamp the new work-thread id back on
        # session.thread_id so subsequent status / Obsidian lookups
        # resolve via the thread anchor.
        session = _persist_thread_id(session, thread_id)
        if kickoff_message:
            await send_chunks(message.channel, kickoff_message)

    research_loop_report = None
    if research_loop_fn is not None and session is not None and kickoff is not None:
        research_loop_report = await _run_research_loop_hook(
            research_loop_fn=research_loop_fn,
            message=message,
            session=session,
            prompt_text=canonical_prompt,
            send_chunks=send_chunks,
            thread_id=thread_id,
        )

    # Phase 4: post the deterministic work report at lifecycle close.
    await _emit_work_report_preview(
        message=message,
        session=session,
        canonical_prompt=canonical_prompt,
        send_chunks=send_chunks,
        collection_outcome=None,
    )

    return EngineeringRouteResult(
        handled=True,
        session_id=session_id,
        thread_id=thread_id,
        kickoff_message=kickoff_message,
        research_loop_report=research_loop_report,
        error=kickoff_error,
    )


async def _handle_clarification_selection(
    *,
    message: Any,
    selected: dict,
    prompt_text: str,
    canonical_prompt: Optional[str],
    send_chunks: SendChunksFn,
    thread_continuation_fn: Optional[ThreadContinuationFn],
) -> Optional[EngineeringRouteResult]:
    """Drive the legacy join helper for a clarification follow-up
    selection. ``canonical_prompt`` (when present) is the original task
    description captured at clarification time — used as the join /
    append payload so session.extra and forum body see real content.
    The user's routing-command reply (``prompt_text``) is dropped from
    the join payload entirely. Returns a populated result on success or
    ``None`` to leave the cache in place and fall through to the regular
    flow."""

    if thread_continuation_fn is None:
        return None
    intake_prompt = (canonical_prompt or "").strip() or prompt_text
    synthetic_outcome = EngineeringConversationOutcome(
        content="",
        intake_prompt=intake_prompt,
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
        intake_prompt=intake_prompt,
        send_chunks=send_chunks,
        thread_continuation_fn=thread_continuation_fn,
        research_loop_fn=None,
    )


# ---------------------------------------------------------------------------
# Coding authorization gate
# ---------------------------------------------------------------------------


# MVP closure refactor — phrase-detection predicates moved to
# :mod:`discord.engineering.phrase_detect`. Re-exported here under
# the historical underscore-prefixed names so existing tests / callers
# (e.g. ``engineering_channel_router.is_coding_approval_phrase``) keep
# working.
from .engineering.phrase_detect import (
    CODING_APPROVAL_PHRASES as _CODING_APPROVAL_PHRASES,
    CODING_PROPOSAL_REQUEST_PHRASES as _CODING_PROPOSAL_REQUEST_PHRASES,
    CONTINUATION_RESEARCH_KEYWORDS as _CONTINUATION_RESEARCH_KEYWORDS,
    NO_CODING_INTENT_PHRASES as _NO_CODING_INTENT_PHRASES,
    continuation_requests_research as _continuation_requests_research,
    is_coding_approval_phrase,
    is_coding_proposal_request,
    user_explicitly_blocked_coding as _user_explicitly_blocked_coding,
)


def _find_session_with_pending_coding_proposal(
    *,
    message: Any,
    list_sessions_fn: Callable[..., Sequence[Any]],
) -> Optional[Any]:
    """Pick the session whose ``extra['coding_proposal']`` should pair
    with this approval phrase. Mirrors ``_find_session_with_pending_proposal``
    but reads the coding key instead of the obsidian key."""

    try:
        try:
            sessions = list_sessions_fn(limit=50)
        except TypeError:
            sessions = list_sessions_fn()
    except Exception:  # noqa: BLE001
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

    candidates = [
        s
        for s in sessions
        if isinstance(getattr(s, "extra", None), Mapping)
        and dict(getattr(s, "extra")).get("coding_proposal")
    ]
    if not candidates:
        return None

    if thread_id is not None:
        for session in candidates:
            if getattr(session, "thread_id", None) == thread_id:
                return session

    if scoped_channel_id is not None:
        same_scope = [
            s
            for s in candidates
            if getattr(s, "channel_id", None) == scoped_channel_id
            and (user_id is None or getattr(s, "user_id", None) == user_id)
        ]
        if same_scope:
            return _most_recent_session(same_scope)

    return _most_recent_session(candidates)


def _find_latest_open_session(
    *,
    message: Any,
    list_sessions_fn: Callable[..., Sequence[Any]],
) -> Optional[Any]:
    """Pick the session a coding proposal should target when the user
    didn't reference one explicitly. Same channel/thread > same channel
    > most recently updated open session."""

    try:
        try:
            sessions = list_sessions_fn(limit=50)
        except TypeError:
            sessions = list_sessions_fn()
    except Exception:  # noqa: BLE001
        return None
    if not sessions:
        return None

    open_sessions = [s for s in sessions if not _is_terminal(s)]
    if not open_sessions:
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

    if thread_id is not None:
        for session in open_sessions:
            if getattr(session, "thread_id", None) == thread_id:
                return session

    if scoped_channel_id is not None:
        same_scope = [
            s
            for s in open_sessions
            if getattr(s, "channel_id", None) == scoped_channel_id
        ]
        if same_scope:
            return _most_recent_session(same_scope)

    return _most_recent_session(open_sessions)


def _is_terminal(session: Any) -> bool:
    state = getattr(session, "state", None)
    state_value = getattr(state, "value", state)
    return str(state_value).lower() in {"completed", "rejected"}


def _persist_coding_proposal(
    session: Any,
    proposal: CodingAuthorizationProposal,
) -> Any:
    """Stash a fresh proposal under ``session.extra['coding_proposal']``."""

    return _persist_extra_keys(
        session,
        {
            "coding_proposal": _proposal_to_dict(proposal),
            "coding_job": None,  # supersedes any prior pending job copy
        },
    )


def _persist_coding_job(session: Any, job_payload: Mapping[str, object]) -> Any:
    """Replace any pending proposal with the approved coding job payload."""

    return _persist_extra_keys(
        session,
        {
            "coding_job": dict(job_payload),
            "coding_proposal": None,  # consumed
        },
    )


def _persist_role_selection(
    session: Any,
    canonical_prompt: str,
) -> Any:
    """Run :func:`role_selection.recommend_active_roles` against
    *canonical_prompt* and stash the result on ``session.extra``.

    Best-effort: import or persistence failures simply skip — the
    legacy "all roles" fallback path remains operational. Used right
    after intake so the work-report builder + research scoping see a
    populated ``active_research_roles`` from turn one.
    """

    if session is None:
        return session
    try:
        from ..agents.lifecycle.role_selection import (
            apply_role_selection_to_extra,
            recommend_active_roles,
        )
    except Exception:  # noqa: BLE001
        return session
    try:
        hint_sequence = tuple(getattr(session, "role_sequence", ()) or ())
    except Exception:  # noqa: BLE001
        hint_sequence = ()
    try:
        selection = recommend_active_roles(
            user_prompt=canonical_prompt or "",
            hint_role_sequence=hint_sequence,
        )
    except Exception:  # noqa: BLE001
        return session
    try:
        existing = dict(getattr(session, "extra", {}) or {})
    except Exception:  # noqa: BLE001
        existing = {}
    merged = apply_role_selection_to_extra(existing, selection)
    # Only forward the four selection-specific keys to _persist_extra_keys
    # so we don't accidentally rewrite unrelated extras with stale copies.
    selection_updates = {
        key: merged[key]
        for key in (
            "active_research_roles",
            "excluded_research_roles",
            "role_selection_source",
            "role_selection_reasons",
        )
        if key in merged
    }
    if not selection_updates:
        return session
    return _persist_extra_keys(session, selection_updates)


def _persist_lifecycle_mode(session: Any, canonical_prompt: str) -> Any:
    """Mark *session* as research-only when the prompt signals that.

    Live regression: the gateway used to advertise an executor role
    ("실행 후보 backend-engineer") even on a request like "오늘은 코드
    수정 없이 자료 수집이 목표야". Phase 2 fixes that by stashing the
    lifecycle mode at intake so every downstream consumer (work_report
    builder, status diagnostic, member-bot research path) reads the
    same answer.

    The session.extra layout matches the spec's bullet 5:
        lifecycle_mode: "research_only" | "implementation"
        executor_role:  null when research-only
        research_leads: list[str]   roles leading the investigation

    Best-effort — any import or persistence failure leaves the session
    untouched so a partial agent layout cannot block intake.
    """

    if session is None:
        return session
    try:
        from ..agents.coding.authorization import (
            LIFECYCLE_MODE_IMPLEMENTATION,
            LIFECYCLE_MODE_RESEARCH_ONLY,
            recommend_authorization,
        )
    except Exception:  # noqa: BLE001
        return session

    try:
        proposal = recommend_authorization(user_request=canonical_prompt or "")
    except Exception:  # noqa: BLE001
        return session

    if proposal.lifecycle_mode == LIFECYCLE_MODE_RESEARCH_ONLY:
        updates = {
            "lifecycle_mode": LIFECYCLE_MODE_RESEARCH_ONLY,
            "executor_role": None,
            "research_leads": list(proposal.research_leads),
        }
    else:
        updates = {
            "lifecycle_mode": LIFECYCLE_MODE_IMPLEMENTATION,
        }
    return _persist_extra_keys(session, updates)


def _work_report_to_dict(report: Any) -> dict:
    """Serialise a :class:`agents.reports.work_report.WorkReport` into a plain
    JSON-friendly dict so the workflow store can persist it under
    ``session.extra['work_report']``."""

    return {
        "session_id": getattr(report, "session_id", None),
        "title": getattr(report, "title", "") or "",
        "canonical_prompt": getattr(report, "canonical_prompt", "") or "",
        "executive_summary": getattr(report, "executive_summary", "") or "",
        "research_summary": getattr(report, "research_summary", "") or "",
        "tech_lead_recommendation": getattr(
            report, "tech_lead_recommendation", ""
        )
        or "",
        "role_decisions": dict(getattr(report, "role_decisions", {}) or {}),
        "risks": list(getattr(report, "risks", ()) or ()),
        "proposed_next_steps": list(
            getattr(report, "proposed_next_steps", ()) or ()
        ),
        "requires_code_change": bool(
            getattr(report, "requires_code_change", False)
        ),
        "recommended_executor_role": getattr(
            report, "recommended_executor_role", None
        ),
        "approval_request": getattr(report, "approval_request", None),
        "participants": list(getattr(report, "participants", ()) or ()),
        "reference_count": int(getattr(report, "reference_count", 0) or 0),
        "research_stop_reason": getattr(report, "research_stop_reason", None),
        "under_covered_roles": list(
            getattr(report, "under_covered_roles", ()) or ()
        ),
        # Phase 3 status gate fields.
        "status": getattr(report, "status", "interim"),
        "missing_roles": list(getattr(report, "missing_roles", ()) or ()),
        "has_research_pack": bool(
            getattr(report, "has_research_pack", False)
        ),
        "has_synthesis": bool(getattr(report, "has_synthesis", False)),
    }


async def _emit_work_report_preview(
    *,
    message: Any,
    session: Any,
    canonical_prompt: str,
    send_chunks: SendChunksFn,
    collection_outcome: Any = None,
    fallback_participants: Sequence[str] = (),
) -> None:
    """Build + persist + post a :class:`WorkReport` for *session*.

    Best-effort end-of-lifecycle hook: builds a deterministic work
    report from ``session.extra``, stashes a snapshot under
    ``session.extra['work_report']`` so the status diagnostic + Phase
    5 Obsidian export can read it back, and posts a Markdown preview
    to the originating Discord channel. Any failure here must NOT
    undo the intake / kickoff / research_loop that already landed —
    every step is wrapped so the user-visible reply is always
    delivered.
    """

    if session is None:
        return
    try:
        from ..agents.reports.work_report import (
            build_work_report,
            format_work_report_markdown,
        )
    except Exception:  # noqa: BLE001 - import wiring failure must not crash bot
        return

    try:
        extra = dict(getattr(session, "extra", {}) or {})
    except Exception:  # noqa: BLE001
        extra = {}

    stop_reason: Optional[str] = None
    under_covered: tuple = ()
    if collection_outcome is not None:
        stop_reason = getattr(collection_outcome, "stop_reason", None)
        try:
            under_covered = tuple(
                getattr(collection_outcome, "under_covered_roles", ()) or ()
            )
        except TypeError:
            under_covered = ()

    try:
        report = build_work_report(
            session_id=getattr(session, "session_id", None),
            canonical_prompt=canonical_prompt,
            extra=extra,
            research_stop_reason=stop_reason,
            under_covered_roles=under_covered,
            fallback_participants=fallback_participants,
        )
    except Exception:  # noqa: BLE001 - report build is non-fatal
        return

    try:
        _persist_extra_keys(session, {"work_report": _work_report_to_dict(report)})
    except Exception:  # noqa: BLE001 - cache failures must not block the user reply
        pass

    try:
        body = format_work_report_markdown(report)
    except Exception:  # noqa: BLE001
        body = ""
    if body:
        try:
            await send_chunks(message.channel, body)
        except Exception:  # noqa: BLE001
            pass


def _persist_extra_keys(session: Any, updates: Mapping[str, object]) -> Any:
    """Merge *updates* into ``session.extra`` and persist via ``update_session``.

    Always mutates the live ``extra`` dict when one is present, so test
    fixtures using mutable dataclass stubs observe the new keys without
    having to capture the returned session. Production WorkflowSession
    is frozen — for that path we rely on ``dataclasses.replace`` +
    ``update_session`` to land the change in SQLite.

    Stabilisation Phase 1: persistence failures used to be silently
    swallowed, which made live debugging impossible. We now stamp a
    ``persistence_error`` entry on the session's live extra dict (when
    available) so the status diagnostic + supervisor can surface
    "왜 저장이 안 됐어?" without having to grep logs. The user-visible
    reply chain is still kept intact (no exception leaks past this
    helper).
    """

    try:
        from dataclasses import replace as _dc_replace
        from datetime import datetime as _dt

        from ..agents.workflow_state import update_session
    except Exception as exc:  # noqa: BLE001
        _record_persistence_failure(
            session,
            step="import update_session",
            reason=str(exc),
            updates=updates,
        )
        return session

    # Try in-place mutation first so test stubs (plain dataclasses with
    # a regular dict ``extra``) observe the change directly. Production
    # WorkflowSession holds an immutable mapping; this no-ops there.
    live = getattr(session, "extra", None)
    if isinstance(live, dict):
        for key, value in updates.items():
            live[key] = value

    existing = dict(getattr(session, "extra", {}) or {})
    merged = {**existing, **dict(updates)}
    try:
        updated = _dc_replace(session, extra=merged)
    except TypeError:
        # Non-dataclass stub — in-place mutation above already covered it.
        return session
    try:
        update_session(updated, now=_dt.now().astimezone())
    except Exception as exc:  # noqa: BLE001
        _record_persistence_failure(
            updated,
            step="update_session",
            reason=str(exc),
            updates=updates,
        )
    return updated


def _record_persistence_failure(
    session: Any,
    *,
    step: str,
    reason: str,
    updates: Mapping[str, object],
) -> None:
    """Stamp a persistence failure note on the live ``session.extra``.

    Best-effort — the session.extra mutation is wrapped so even
    pathological stubs never raise out of this helper. The note keeps
    the offending step + reason + the keys that were being written so
    the diagnostic responder can show the operator exactly which
    update silently failed during the live MVP loop.
    """

    if session is None:
        return
    try:
        live = getattr(session, "extra", None)
        if isinstance(live, dict):
            live["persistence_error"] = {
                "step": step,
                "reason": reason,
                "keys": sorted(str(k) for k in (updates or {}).keys()),
            }
    except Exception:  # noqa: BLE001
        return


def _persist_thread_id(
    session: Any,
    thread_id: Optional[int],
) -> Any:
    """Write the Discord work-thread id back to ``session.thread_id``.

    MVP closure refactor: delegates to
    :func:`agents.lifecycle.persistence.persist_thread_link` so the
    router and any other caller (member-bot, supervisor cleanup)
    follow the same persistence contract — including the structured
    ``persistence_error`` stamp on failure. Behaviour is identical to
    the prior inline implementation; only the import/replace
    sequence is consolidated upstream.
    """

    from ..agents.lifecycle.persistence import persist_thread_link

    result = persist_thread_link(session, thread_id)
    return result.session


def _proposal_to_dict(proposal: CodingAuthorizationProposal) -> Mapping[str, object]:
    return {
        "session_id": proposal.session_id,
        "user_request": proposal.user_request,
        "executor_role": proposal.executor_role,
        "review_roles": list(proposal.review_roles),
        "participant_roles": list(proposal.participant_roles),
        "write_scope": list(proposal.write_scope),
        "forbidden_scope": list(proposal.forbidden_scope),
        "reason": proposal.reason,
        "safety_rules": list(proposal.safety_rules),
        "approval_required": bool(proposal.approval_required),
        "metadata": dict(proposal.metadata),
        "lifecycle_mode": proposal.lifecycle_mode,
        "research_leads": list(proposal.research_leads),
    }


def _proposal_from_dict(payload: Mapping[str, object]) -> CodingAuthorizationProposal:
    lifecycle_mode = str(payload.get("lifecycle_mode") or "implementation")
    raw_executor = payload.get("executor_role")
    if lifecycle_mode == "research_only":
        executor_role = str(raw_executor or "")
    else:
        executor_role = str(raw_executor or "tech-lead")
    return CodingAuthorizationProposal(
        session_id=payload.get("session_id"),
        user_request=str(payload.get("user_request") or ""),
        executor_role=executor_role,
        review_roles=tuple(payload.get("review_roles") or ()),
        participant_roles=tuple(payload.get("participant_roles") or ()),
        write_scope=tuple(payload.get("write_scope") or ()),
        forbidden_scope=tuple(payload.get("forbidden_scope") or ()),
        reason=str(payload.get("reason") or ""),
        safety_rules=tuple(payload.get("safety_rules") or ()),
        approval_required=bool(payload.get("approval_required", True)),
        metadata=dict(payload.get("metadata") or {}),
        lifecycle_mode=lifecycle_mode,
        research_leads=tuple(payload.get("research_leads") or ()),
    )


async def _run_coding_authorization_gate(
    *,
    message: Any,
    prompt_text: str,
    list_sessions_fn: Callable[..., Sequence[Any]],
    send_chunks: SendChunksFn,
) -> Optional[EngineeringRouteResult]:
    """Two-branch gate.

    1. ``is_coding_proposal_request`` — build a fresh proposal and
       stash it under ``session.extra['coding_proposal']``, then post
       the preview. The user follows up with an approval phrase.
    2. ``is_coding_approval_phrase`` — flip the latest stashed
       proposal into a ``CodingJob`` (status=ready) and persist under
       ``session.extra['coding_job']``.

    Returns ``None`` when the message isn't either kind so the caller
    falls through to the rest of the route.
    """

    # Hard "no code change" override: if the user explicitly said
    # "코드 수정 하지 말고 리서치만" the coding gate must not act on
    # this message even if it also contains a proposal/approval phrase.
    if _user_explicitly_blocked_coding(prompt_text):
        return None

    if is_coding_proposal_request(prompt_text):
        target = _find_latest_open_session(
            message=message,
            list_sessions_fn=list_sessions_fn,
        )
        if target is None:
            await send_chunks(
                message.channel,
                (
                    "현재 채널에 매칭되는 열린 engineering-agent 세션이 보이지 않아요.\n"
                    "먼저 작업을 접수해서 세션을 만들고 다시 `코딩 권한 제안`이라고 답해 주세요."
                ),
            )
            return EngineeringRouteResult(handled=True)

        proposal = recommend_authorization(
            user_request=getattr(target, "prompt", "") or "",
            session_id=getattr(target, "session_id", None),
        )
        _persist_coding_proposal(target, proposal)
        await send_chunks(message.channel, format_authorization_message(proposal))
        return EngineeringRouteResult(
            handled=True,
            session_id=getattr(target, "session_id", None),
            thread_id=getattr(target, "thread_id", None),
        )

    if is_coding_approval_phrase(prompt_text):
        owner = _find_session_with_pending_coding_proposal(
            message=message,
            list_sessions_fn=list_sessions_fn,
        )
        if owner is None:
            await send_chunks(
                message.channel,
                (
                    "지금은 대기 중인 코딩 권한 제안이 없어요.\n"
                    "먼저 `코딩 권한 제안` 이라고 답해서 Tech Lead 추천을 받아 주세요."
                ),
            )
            return EngineeringRouteResult(handled=True)

        extra = dict(getattr(owner, "extra", {}) or {})
        payload = extra.get("coding_proposal")
        if not isinstance(payload, Mapping):
            await send_chunks(
                message.channel,
                "대기 중인 코딩 권한 제안 payload를 읽지 못했어요. 다시 `코딩 권한 제안`을 시도해 주세요.",
            )
            return EngineeringRouteResult(handled=True)

        from datetime import datetime as _dt
        from datetime import timezone as _tz

        approved_at = _dt.now(_tz.utc)
        proposal = _proposal_from_dict(payload)
        try:
            job = build_coding_job_from_proposal(
                proposal,
                status=STATUS_READY,
                approved_at=approved_at,
            )
        except Exception as exc:  # noqa: BLE001
            await send_chunks(
                message.channel,
                f"⚠️ 코딩 권한 승인 중 오류가 발생했어요: {exc}",
            )
            return EngineeringRouteResult(handled=True, error=str(exc))

        _persist_coding_job(owner, job.to_dict())

        thread_label = (
            f"thread `{job.session_id}`"
            if job.session_id
            else "(session id 미기록)"
        )
        await send_chunks(
            message.channel,
            "\n".join(
                [
                    "**[engineering-agent] 코딩 권한 승인 완료**",
                    "",
                    f"executor: `{job.executor_role}`",
                    f"세션: {thread_label}",
                    f"승인 시각: {approved_at.isoformat()}",
                    "",
                    "이제 executor에게 안전한 prompt가 전달될 준비가 됐어요. 실제 코드 변경은 executor가 계획을 보여 드린 뒤에만 진행합니다.",
                ]
            ),
        )
        return EngineeringRouteResult(
            handled=True,
            session_id=getattr(owner, "session_id", None),
            thread_id=getattr(owner, "thread_id", None),
        )

    return None


# MVP closure refactor — explicit session id regex moved to
# :mod:`agents.lifecycle.resolver` so router / bot / obsidian gate
# share one canonical implementation. The router-private alias is
# kept for backward compat with internal callers (and the runtime
# preflight ``_explicit_session_id`` substring check).
from ..agents.lifecycle.resolver import (
    _EXPLICIT_SESSION_ID_RE as _EXPLICIT_SESSION_ID_RE,
    extract_explicit_session_id as _extract_session_id_from_router_text,
)


def _can_save_to_obsidian(session: Any) -> tuple[bool, Optional[str]]:
    """Return (allowed, blocking_reason).

    Phase 4 stab: an Obsidian write must NOT proceed when the
    lifecycle hasn't actually closed. Reads ``session.extra`` for the
    Phase 2/3 status keys and refuses if research is empty / forum
    isn't connected / work_report is not ready/final.

    Returns ``(True, None)`` to allow, ``(False, "<korean reason>")``
    to block. Test stubs that don't carry rich extras get a generous
    "missing canonical readiness" reason rather than a hard pass.
    """

    # Refactor: delegate to the canonical :mod:`agents.lifecycle.status`
    # helper so the router, work_report builder, and Discord status
    # diagnostic all share one set of "can we save?" rules. The block
    # reasons stay identical to keep operator-visible messages stable.
    from ..agents.lifecycle.status import can_write_obsidian_record

    return can_write_obsidian_record(session)


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

    Phase 4 stab: explicit "세션 <id> 기준으로 저장해줘" prompts now
    resolve via the id first (load_session) — channel/thread walks
    only fire when the user didn't name a session. Final write is
    blocked when the lifecycle is incomplete (no research_pack,
    interim/insufficient work_report, missing role coverage).

    Returns a populated :class:`EngineeringRouteResult` when the message
    was an approval phrase (regardless of whether the write succeeded),
    or ``None`` to fall through to the runtime preflight + conversation
    flow. We deliberately keep this branch above the runtime classifier
    so a bare "저장 승인" never gets promoted to ``new_work_request``.
    """

    # Phase 4 stab: accept "세션 <id> 기준으로 저장 승인" by stripping
    # the explicit-id preamble before testing the approval phrase.
    explicit_id = _extract_session_id_from_router_text(prompt_text)
    test_text = prompt_text
    if explicit_id and not is_obsidian_approval(test_text):
        # Drop the "세션 <id> 기준으로" prefix and re-test so a
        # session-scoped approval still routes through this gate.
        stripped = _EXPLICIT_SESSION_ID_RE.sub("", prompt_text).strip()
        if stripped:
            for filler in ("기준으로", "기준 으로", "기준에서", "기준"):
                if stripped.startswith(filler):
                    stripped = stripped[len(filler):].strip()
                    break
            if is_obsidian_approval(stripped):
                test_text = stripped

    if not is_obsidian_approval(test_text):
        return None

    candidate: Optional[Any] = None
    if explicit_id:
        try:
            from ..agents.workflow_state import load_session as _load_session

            candidate = _load_session(explicit_id)
        except Exception:  # noqa: BLE001 - lookup failure falls through
            candidate = None
        if candidate is None:
            await send_chunks(
                message.channel,
                (
                    f"세션 `{explicit_id}` 을 찾지 못했어요.\n"
                    "session id 가 정확한지 확인하거나, 새 작업이라면 `새 작업으로 진행`이라고 답해 주세요."
                ),
            )
            return EngineeringRouteResult(
                handled=True,
                error=f"obsidian approval: explicit session {explicit_id} not found",
            )

    if candidate is None:
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

    allowed, block_reason = _can_save_to_obsidian(candidate)
    if not allowed:
        await send_chunks(
            message.channel,
            (
                "Obsidian 저장을 진행하지 않았어요.\n"
                f"차단 사유: {block_reason}\n"
                "lifecycle 이 완료되면 다시 `저장 승인` 으로 답해 주세요."
            ),
        )
        return EngineeringRouteResult(
            handled=True,
            session_id=getattr(candidate, "session_id", None),
            error=f"obsidian approval blocked: {block_reason}",
        )

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
    research_loop_fn: Optional[ResearchLoopFn] = None,
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
    stored_canonical = _recall_clarification_canonical_prompt(message)
    if stored_candidates:
        selected = _try_select_candidate(prompt_text, stored_candidates)
        if selected is not None:
            join_result = await _handle_clarification_selection(
                message=message,
                selected=selected,
                prompt_text=prompt_text,
                canonical_prompt=stored_canonical,
                send_chunks=send_chunks,
                thread_continuation_fn=thread_continuation_fn,
            )
            if join_result is not None:
                _clear_clarification_context(message)
                return join_result

    # 0b. Clarification follow-up "새 작업으로 진행" path is *not*
    # handled in the preflight (we do not own intake_fn / kickoff_fn
    # here). The caller (``route_engineering_message``) inspects the
    # same cache via ``_recall_clarification_canonical_prompt`` after
    # preflight returns None, and runs the legacy CREATE branch with
    # the cached canonical_prompt as ``intake_prompt``.

    # 0c. Explicit "기존 세션 <id>" reply with a stored canonical_prompt:
    # the runtime recall doesn't parse the explicit-session-id pattern
    # so it would force ASK_CLARIFICATION here even though decide_routing
    # could resolve the JOIN cleanly. Hand off to the legacy flow so
    # the canonical_prompt rewrite in ``route_engineering_message``
    # handles the append payload.
    if (
        stored_canonical
        and isinstance(prompt_text, str)
        and _explicit_session_request(prompt_text)
    ):
        return None

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
        # one carrying the prompt text as ``intake_prompt``. When a
        # clarification cache stashed a canonical_prompt last turn we
        # use that instead so the append payload carries the original
        # task description, not the routing-command reply.
        canonical_for_join = _recall_clarification_canonical_prompt(message)
        join_intake_prompt = canonical_for_join or prompt_text
        synthetic_outcome = EngineeringConversationOutcome(
            content="",
            intake_prompt=join_intake_prompt,
        )
        synthetic_decision = EngineeringRoutingDecision(
            action=ACTION_JOIN,
            matched_session_id=primary.payload.get("session_id"),
            matched_thread_id=primary.payload.get("thread_id"),
            matched_forum_thread_id=primary.payload.get("forum_thread_id"),
            confidence=intent.confidence,
            reason=f"runtime preflight · {intent.intent_id}",
        )
        # Decide whether to pass research_loop_fn into the join/append
        # helper. The runtime preflight only re-triggers research
        # collection when the live MVP bug repeats: the matched session
        # has no research_pack yet *and* the continuation prompt names
        # research-shaped intent. Otherwise we keep the legacy
        # "no auto research loop on join" contract so simple resume /
        # status pings don't kick off a fresh forum sweep.
        effective_research_loop_fn: Optional[ResearchLoopFn] = None
        if (
            research_loop_fn is not None
            and _continuation_requests_research(prompt_text)
        ):
            matched_session = _load_session_by_id(
                list_sessions_fn,
                primary.payload.get("session_id"),
            )
            matched_extra: Mapping[str, Any]
            try:
                matched_extra = dict(getattr(matched_session, "extra", {}) or {})
            except Exception:  # noqa: BLE001
                matched_extra = {}
            if not matched_extra.get("research_pack"):
                effective_research_loop_fn = research_loop_fn
        result = await _handle_join_or_append(
            message=message,
            outcome=synthetic_outcome,
            decision=synthetic_decision,
            intake_prompt=join_intake_prompt,
            send_chunks=send_chunks,
            thread_continuation_fn=thread_continuation_fn,
            research_loop_fn=effective_research_loop_fn,
        )
        if result is not None:
            if canonical_for_join:
                _clear_clarification_context(message)
            return result
        # Fallthrough to clarification when continuation couldn't reach
        # the matched thread (e.g. it's archived) — do NOT silently
        # create a new session. Stash the candidates so the user can
        # reply with "1번" / "기존 세션으로 진행" on the next turn.
        _remember_clarification_candidates(
            message,
            recall.candidates,
            canonical_prompt=prompt_text,
        )
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
        _remember_clarification_candidates(
            message,
            recall.candidates,
            canonical_prompt=prompt_text,
        )
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
        # Phase 4: post the deterministic work report at lifecycle close.
        # Skipped on pure ACTION_APPEND_CONTEXT — append-only turns
        # don't have a fresh research outcome to summarise.
        await _emit_work_report_preview(
            message=message,
            session=continued_session,
            canonical_prompt=intake_prompt,
            send_chunks=send_chunks,
            collection_outcome=outcome.collection_outcome,
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
    # Phase 1 fix: research loops can run for tens of seconds (autonomous
    # collection + forum publish + member-bot fan-out). Discord's typing
    # indicator auto-expires after ~10s, so without the keepalive the
    # user saw long silent gaps. Wrap the work in ``typing_keepalive``
    # so "입력 중..." stays visible from the moment we start collecting
    # until the loop returns a follow-up message (or an error).
    from .typing_indicator import typing_keepalive

    try:
        async with typing_keepalive(
            message.channel,
            label="research_loop",
            session_id=getattr(session, "session_id", None),
        ):
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

    # MVP closure refactor: delegate to lifecycle_persistence so the
    # canonical + legacy mirror keys are always written by one helper.
    # Behaviour is identical; the helper covers the dataclass replace,
    # in-place test-stub fallback, structured persistence_error stamp,
    # and stale-error cleanup that this function used to inline.
    from ..agents.lifecycle.persistence import persist_research_forum_link

    open_call_posted = report.kickoff_posted if report.forum_comment_mode == "member-bots" else None
    open_call_error = report.kickoff_error if report.forum_comment_mode == "member-bots" else None
    persist_research_forum_link(
        session,
        thread_id=report.forum_thread_id,
        url=report.forum_thread_url,
        open_call_posted=open_call_posted,
        open_call_error=open_call_error,
        forum_comment_mode=report.forum_comment_mode,
    )


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
            from ..agents.research.collector import resolve_forum_comment_mode
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
        from ..agents.research.collector import extract_urls
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
