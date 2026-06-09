"""engineering_channel_router — runtime intent + recall short-circuit.

Owns the runtime-classifier-driven preflight that intercepts
"pointing-back-at-existing-work" intents (continue / summarize /
execute / append) before they reach the legacy conversation +
intake flow. Also hosts the join/append driver used by both
preflight and the legacy flow when a session match is high-confidence.

Re-exported helpers:

- :func:`_run_runtime_preflight` — main preflight entry.
- :func:`_handle_join_or_append` — legacy join helper.
- :func:`_thread_id_for_runtime` / :func:`_observation_for_runtime` —
  message → runtime input marshalling.
- :func:`_format_runtime_preflight_clarification` — multi-candidate
  display when preflight needs to ask the user.
- :data:`_PREFLIGHT_SHORT_CIRCUIT_INTENTS` — the four intents the
  preflight intercepts.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Mapping, Optional, Sequence

from ..engineering.clarification import (
    GATEWAY_CLARIFICATION_CONTEXT as _GATEWAY_CLARIFICATION_CONTEXT,
    clarification_context_key as _clarification_context_key,
    clear_clarification_context as _clear_clarification_context,
    recall_clarification_candidates as _recall_clarification_candidates,
    recall_clarification_canonical_prompt as _recall_clarification_canonical_prompt,
    remember_clarification_candidates as _remember_clarification_candidates,
    try_select_candidate as _try_select_candidate,
)
from ..engineering.phrase_detect import (
    continuation_requests_research as _continuation_requests_research,
)
from yule_orchestrator.agents.lifecycle.resolver import (
    extract_explicit_session_id as _explicit_session_request,
)
from yule_orchestrator.agents.routing import (
    ACTION_APPEND_CONTEXT,
    ACTION_ASK,
    ACTION_CREATE,
    ACTION_JOIN,
    EngineeringRoutingDecision,
)
from yule_orchestrator.agents.runtime import (
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
from .models import (
    EngineeringConversationOutcome,
    EngineeringResearchLoopReport,
    EngineeringRouteResult,
    ResearchLoopFn,
    SendChunksFn,
    ThreadContinuationFn,
)
from .obsidian_gate import _run_obsidian_preview_branch
from .reporting import _emit_work_report_preview
from .research_loop import (
    _maybe_persist_research_pack,
    _research_loop_blocked_by_command_only,
    _run_research_loop_hook,
)
from .session_persistence import (
    _load_session_by_id,
    _most_recent_session,
)
from .utils import (
    _attach_recall_coverage,
    _maybe_await,
    extract_message_attachments,
)

logger = logging.getLogger(__name__)


def is_obsidian_save_request(text: str) -> bool:
    """Lazy thunk to avoid hard import of obsidian.approval at module load."""

    from yule_orchestrator.agents.obsidian.approval import is_obsidian_save_request as _impl

    return _impl(text)


def _handle_clarification_selection(*args, **kwargs):
    """Defer to ``_legacy._handle_clarification_selection`` (final
    extraction lives in P0-P step 12 inside main.py)."""

    from .main import _handle_clarification_selection as _impl

    return _impl(*args, **kwargs)


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
from ..engineering.clarification import (
    GATEWAY_CLARIFICATION_CONTEXT as _GATEWAY_CLARIFICATION_CONTEXT,
    clarification_context_key as _clarification_context_key,
    clear_clarification_context as _clear_clarification_context,
    looks_like_new_work_selection as _looks_like_new_work_selection,
    recall_clarification_candidates as _recall_clarification_candidates,
    recall_clarification_canonical_prompt as _recall_clarification_canonical_prompt,
    remember_clarification_candidates as _remember_clarification_candidates,
    try_select_candidate as _try_select_candidate,
)

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
    # F16 (issue #128): attach coverage scoring to every recall result
    # the runtime preflight touches. The coverage value is *derived*
    # metadata — legacy callers ignore it, observability can read it,
    # and the future ``decide_gateway`` path branches on it.
    recall = _attach_recall_coverage(recall)
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

    from yule_orchestrator.agents.runtime.models import RuntimeObservation

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
    # P0-E (#134 후속): JOIN/APPEND 의 thread lookup + resume 도 long-running
    # path (Discord API 조회 + 세션 hydration). conversation_fn wrap 과 동일
    # 6s interval 로 typing 유지 — 끊김 race 방지.
    from ..ui.typing_indicator import typing_keepalive

    async with typing_keepalive(
        getattr(message, "channel", None),
        interval=6.0,
        label="gateway:thread-continuation",
    ):
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
    # P0-K (#148) — the continuation path is the very site that
    # produced "[Reference] 진행 해줘" thread spam. Block the research
    # loop whenever intake_prompt is a command-only operational
    # phrase.
    blocked_by_command_only = _research_loop_blocked_by_command_only(intake_prompt)
    if (
        not is_append_only
        and not blocked_by_command_only
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


__all__ = (
    "_PREFLIGHT_SHORT_CIRCUIT_INTENTS",
    "_run_runtime_preflight",
    "_thread_id_for_runtime",
    "_observation_for_runtime",
    "_format_runtime_preflight_clarification",
    "_handle_join_or_append",
)
