"""engineering_channel_router — main orchestration entry.

Hosts the three remaining "orchestration core" functions after the
P0-P decomposition (steps 1-11) extracted everything else into
responsibility-aligned siblings:

- :func:`route_engineering_message` — the public router entry.
  Walks the message through (1) channel check, (2) coding gate,
  (3) Obsidian gate, (4) explicit-session-id join, (5) clarification
  CREATE branch, (6) runtime preflight, (7) ``conversation_fn``
  + intake / kickoff / research loop / work report.
- :func:`_drive_clarification_create_new_work` — drives intake +
  kickoff + research_loop with the cached canonical_prompt when
  the user picked "새 작업으로 진행" after a clarification.
- :func:`_handle_clarification_selection` — drives JOIN via the
  legacy join helper when the user picked an existing candidate.

Every other concern (models, utils, intent_detection, coding_gate,
obsidian_gate, session_persistence, research_loop, reporting,
runtime_preflight, clarification cache) lives in its own module —
see ``docs/p0p-engineering-channel-router-decomposition.md`` for the
full responsibility map.

The module is pure-Python: all I/O dependencies (engineering
conversation provider, workflow intake, thread kickoff, message
sender) are injected as callables so unit tests can drive the router
without spinning up discord.py. ``bot.py`` wires the production
callables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence, Union

from ...agents.coding.authorization import (
    CodingAuthorizationProposal,
    format_authorization_message,
    recommend_authorization,
)
from ...agents.coding.job import (
    STATUS_READY,
    build_coding_job_from_proposal,
)
from ...agents.obsidian.approval import (
    ObsidianApprovalError,
    build_save_proposal,
    execute_pending_proposal,
    get_pending_proposal,
    is_obsidian_approval,
    is_obsidian_save_request,
    store_pending_proposal,
)
from ...agents.research.persistence import persist_research_artifacts
from ...agents.routing import (
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
from ...agents.runtime import (
    ACTION_APPEND_CONTEXT as RUNTIME_ACTION_APPEND_CONTEXT,
    ACTION_ASK_CLARIFICATION as RUNTIME_ACTION_ASK_CLARIFICATION,
    ACTION_JOIN_SESSION as RUNTIME_ACTION_JOIN_SESSION,
    INTENT_APPEND_CONTEXT as RUNTIME_INTENT_APPEND_CONTEXT,
    INTENT_CONTINUE_EXISTING_WORK as RUNTIME_INTENT_CONTINUE_EXISTING_WORK,
    INTENT_EXECUTE_EXISTING_STEP as RUNTIME_INTENT_EXECUTE_EXISTING_STEP,
    INTENT_SUMMARIZE_PREVIOUS_WORK as RUNTIME_INTENT_SUMMARIZE_PREVIOUS_WORK,
    RecallCoverage,
    RuntimeInput,
    RuntimeRecallResult,
    RuntimeResearchPlan,
    classify_intent_deterministic,
    compute_recall_coverage,
    decide_default,
    make_recall_fn,
)

# P0-P step 11: clarification cache re-imported for _legacy call sites.
from ..engineering.clarification import (  # noqa: E402,F401 — local bindings
    GATEWAY_CLARIFICATION_CONTEXT as _GATEWAY_CLARIFICATION_CONTEXT,
    clarification_context_key as _clarification_context_key,
    clear_clarification_context as _clear_clarification_context,
    looks_like_new_work_selection as _looks_like_new_work_selection,
    recall_clarification_candidates as _recall_clarification_candidates,
    recall_clarification_canonical_prompt as _recall_clarification_canonical_prompt,
    remember_clarification_candidates as _remember_clarification_candidates,
    try_select_candidate as _try_select_candidate,
)
# P0-P step 11: runtime preflight / join helper re-imported.
from .runtime_preflight import (  # noqa: E402,F401 — local bindings
    _handle_join_or_append,
    _run_runtime_preflight,
)
# P0-P step 4: utils re-imported for _legacy call sites.
from .utils import (  # noqa: E402,F401 — local bindings for in-file callers
    _attach_recall_coverage,
    _maybe_await,
    _normalize_channel_name,
    _optional_bool_env,
    _optional_int_env,
    _optional_str,
    _optional_string_env,
    _safe_int,
    extract_message_attachments,
    extract_user_links_from_message,
)
# P0-P step 9 / 10: research_loop + reporting helpers re-imported.
from .research_loop import (  # noqa: E402,F401 — local bindings
    _research_loop_blocked_by_command_only,
    _run_research_loop_hook,
    _maybe_persist_research_pack,
    persist_research_forum_status,
)
from .reporting import (  # noqa: E402,F401 — local bindings
    _coerce_outcome,
    _coerce_research_loop_report,
    _emit_work_report_preview,
    _format_clarification_message,
)
# P0-P step 3: dataclasses + type aliases extracted to .models.
from .models import (  # noqa: E402,F401 — re-export for back-compat
    ConversationFn,
    EngineeringConversationOutcome,
    EngineeringResearchLoopReport,
    EngineeringRouteContext,
    EngineeringRouteResult,
    EngineeringThreadContinuation,
    EngineeringThreadKickoff,
    ExtractPromptFn,
    IntakeFn,
    ResearchLoopFn,
    SendChunksFn,
    ThreadContinuationFn,
    ThreadKickoffFn,
)


# P0-P step 5: channel + confirmation + continuation predicates
# extracted to .intent_detection.
from .intent_detection import (  # noqa: E402,F401 — re-export for back-compat
    _CONFIRMATION_KEYWORDS,
    detect_confirmation_signal,
    is_engineering_channel,
    should_continue_existing_thread,
    should_start_new_thread,
)


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
    approval_worker: Any = None,
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
            approval_worker=approval_worker,
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
                from ...agents.workflow_state import load_session as _load_session
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

    # Clarification follow-up CREATE branch — P0-M (#151 followup).
    # MUST run before the runtime preflight so a "새 작업으로 진행"
    # reply with a cached canonical_prompt never gets intercepted by
    # the runtime classifier (which would route it as
    # CONTINUE_EXISTING_WORK / JOIN_SESSION) and never falls through
    # to ``conversation_fn`` (which would classify the routing-command
    # phrase as CONFIRM_INTAKE → P0-K downgrades it to APPROVAL_ACTION
    # ack, leaving no new session — the user-reported regression).
    #
    # We only intercept when a clarification cache exists for this
    # channel/user pair. Without a cache, "새 작업으로 진행" might
    # still be a legitimate confirmation tied to a conversation-layer
    # ``last_proposed_prompt`` (long_task fixture in
    # ``test_long_research_prompt_followed_by_saejakeop_persists_real_prompt``)
    # — let the legacy flow handle that path so ``intake_fn`` still
    # gets called with the substantive prompt.
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
            _clear_clarification_context(message)
            # ``_drive_clarification_create_new_work`` always returns
            # an ``EngineeringRouteResult`` (success / error / refusal
            # via the non-actionable canonical guard). Return it
            # unconditionally so we never fall through to the
            # conversation layer and accidentally downgrade to
            # APPROVAL_ACTION ack.
            if create_result is not None:
                return create_result
            return EngineeringRouteResult(handled=True)
        if clarification_candidates or clarification_cache_present:
            # Cache present but canonical missing (older entry from
            # before the canonical_prompt fix, or candidates lost
            # during truncation) — refuse to spawn a session with the
            # routing-command phrase as ``session.prompt``.
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
        # No clarification cache — fall through to the legacy flow.
        # The bot-echo guard below + the ``is_non_actionable_prompt``
        # firewall at the routing stage already handle the bare-phrase
        # case (refuse with "진행할 업무 원문을 다시 알려주세요"), and
        # the conversation layer's ``last_proposed_prompt`` mechanism
        # still routes substantive confirmations correctly.

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

    attachments = extract_message_attachments(message)
    user_links = extract_user_links_from_message(message, prompt_text)
    # P0-D (#134): conversation_fn 가 auto_collect 를 돌리면 collector +
    # research_pack 적재까지 long-running (수 초 ~ 십수 초). 그 동안
    # 사용자에게 "처리 중" 신호가 끊겨 봇이 죽은 것처럼 보이던 문제.
    # typing_keepalive 가 ~6s 마다 typing event 재발사 → 첫 visible
    # reply (send_chunks) 까지 끊김 없이 유지. ignored / non-actionable /
    # bot-echo 분기는 본 라인 *전*에 이미 return 했으므로 silence 보존.
    from ..ui.typing_indicator import typing_keepalive

    async with typing_keepalive(
        getattr(message, "channel", None),
        interval=6.0,
        label="gateway:conversation",
    ):
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
    # P0-H stage 2 — gateway 가 prepare 한 work_mode / topology / scope /
    # github_target / repo_contract / coding_handoff_packet 를 session.extra
    # 에 stash. 이미 mode 가 박혀 있으면 helper 가 재질문 안 함.
    session = _persist_coding_session_context(
        session,
        message_text=prompt_text or intake_prompt,
        user_links=user_links,
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
            # Phase 1 stabilisation: stamp the new work-thread id back
            # on session.thread_id so status / Obsidian / continuation
            # lookups by thread anchor resolve cleanly. Without this
            # the session row stayed thread-less in SQLite even after
            # a successful kickoff.
            session = _persist_thread_id(session, thread_id)

    research_loop_report: Optional[EngineeringResearchLoopReport] = None
    if research_loop_fn is not None and session is not None:
        # P0-K (#148) — never let a command-only prompt drive the
        # research loop. The loop's first action is to query against
        # ``prompt_text``; queries like "진행 해줘" surface canned hits
        # whose title becomes the new forum thread name.
        if _research_loop_blocked_by_command_only(intake_prompt):
            research_loop_report = None
        else:
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


# P0-P step 11: runtime preflight + join/append driver extracted to .runtime_preflight.
from .runtime_preflight import (  # noqa: E402,F401 — re-export for back-compat
    _PREFLIGHT_SHORT_CIRCUIT_INTENTS,
    _run_runtime_preflight,
    _thread_id_for_runtime,
    _observation_for_runtime,
    _format_runtime_preflight_clarification,
    _handle_join_or_append,
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
    # P0-H stage 2 — coding session context (same wiring as the
    # primary intake path above). The clarification path doesn't have
    # a pre-extracted user_links list — re-extract from the message.
    session = _persist_coding_session_context(
        session,
        message_text=canonical_prompt,
        user_links=extract_user_links_from_message(message, canonical_prompt),
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
        # P0-K (#148) — guard parity with the primary intake path.
        if _research_loop_blocked_by_command_only(canonical_prompt):
            research_loop_report = None
        else:
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
from ..engineering.phrase_detect import (
    CODING_APPROVAL_PHRASES as _CODING_APPROVAL_PHRASES,
    CODING_PROPOSAL_REQUEST_PHRASES as _CODING_PROPOSAL_REQUEST_PHRASES,
    CONTINUATION_RESEARCH_KEYWORDS as _CONTINUATION_RESEARCH_KEYWORDS,
    NO_CODING_INTENT_PHRASES as _NO_CODING_INTENT_PHRASES,
    continuation_requests_research as _continuation_requests_research,
    is_coding_approval_phrase,
    is_coding_proposal_request,
    user_explicitly_blocked_coding as _user_explicitly_blocked_coding,
)


# P0-P step 6: session.extra mutations + load helpers extracted to
# .session_persistence. _legacy still calls these — re-import so the
# local bindings stay resolvable inside the orchestration functions.
from .session_persistence import (  # noqa: E402,F401 — re-import for local call sites
    _is_terminal,
    _load_session_by_id,
    _most_recent_session,
    _persist_coding_job,
    _persist_coding_proposal,
    _persist_coding_session_context,
    _persist_extra_keys,
    _persist_lifecycle_mode,
    _persist_role_selection,
    _persist_thread_id,
    _proposal_from_dict,
    _proposal_to_dict,
    _record_persistence_failure,
    _work_report_to_dict,
)
# P0-P step 7: coding 권한 / 승인 gate extracted to .coding_gate.
from .coding_gate import (  # noqa: E402,F401 — re-export for back-compat
    _find_session_with_pending_coding_proposal,
    _find_latest_open_session,
    _run_coding_authorization_gate,
)
# P0-P step 8: Obsidian 저장 gate extracted to .obsidian_gate.
from .obsidian_gate import (  # noqa: E402,F401 — re-export for back-compat
    _can_save_to_obsidian,
    _run_obsidian_approval_gate,
    _run_obsidian_preview_branch,
    _find_session_with_pending_proposal,
)




















# P0-P step 10: work_report preview + clarification display + outcome coercion
# extracted to .reporting.
from .reporting import (  # noqa: E402,F401 — re-export for back-compat
    _emit_work_report_preview,
    _format_clarification_message,
    _coerce_research_loop_report,
    _coerce_outcome,
)






















































