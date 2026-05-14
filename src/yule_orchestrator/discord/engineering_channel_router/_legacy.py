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
    from ..typing_indicator import typing_keepalive

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
        from ...agents.reports.work_report import (
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

    from ...agents.runtime.models import RuntimeObservation

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
    from ..typing_indicator import typing_keepalive

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


def _research_loop_blocked_by_command_only(prompt_text: Optional[str]) -> bool:
    """P0-K (#148) — True when *prompt_text* is a bare approval/proceed
    phrase like "진행 해줘" / "이대로 진행" / "작업 승인 할게 진행 해줘".

    The research loop's first action is to query against ``prompt_text``;
    queries like "진행 해줘" surface canned hits whose title becomes the
    new forum thread name (``[Reference] 진행 해줘``). Block the loop
    rather than let the operational phrase reach the collector.

    Returns False when ``prompt_text`` is None / empty / substantive
    so the existing legitimate research path is unaffected.
    """

    if not prompt_text:
        return False
    try:
        from ...agents.routing import is_non_actionable_prompt
    except Exception:  # noqa: BLE001 - partial install safe-side
        return False
    return bool(is_non_actionable_prompt(prompt_text))


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

    A-M3 wiring: the actual ``research_loop_fn`` invocation now happens
    inside :class:`ResearchWorker`, so each gateway call lands as a
    ``research_collect`` job in the SQLite job queue and goes through
    the ``queued → assigned → in_progress → saved`` state machine.
    Concretely:

      * Duplicate intakes for the same session are dropped at the
        ``enqueue`` step — the user sees "이미 진행 중" instead of a
        second collect kicking off.
      * Worker crashes mid-run leave the row in ``in_progress`` with
        a lease; the M2 supervisor sweep moves it back to
        ``failed_retryable`` so a future pick can retry.
      * The Discord-visible artifacts (``follow_up_message``,
        ``forum_status_message``, ``session.extra`` updates) are
        unchanged — only state-machine framing is added around the
        same call.

    Errors are still caught and reported via a ``⚠️`` chat line so a
    research loop failure does not undo the intake + kickoff that
    already landed.
    """

    attachments = extract_message_attachments(message)
    # Phase 1 fix: research loops can run for tens of seconds (autonomous
    # collection + forum publish + member-bot fan-out). Discord's typing
    # indicator auto-expires after ~10s, so without the keepalive the
    # user saw long silent gaps. Wrap the work in ``typing_keepalive``
    # so "입력 중..." stays visible from the moment we start collecting
    # until the loop returns a follow-up message (or an error).
    from ..typing_indicator import typing_keepalive
    from ...agents.job_queue import (
        HeartbeatStore,
        JobQueue,
        ResearchWorker,
    )

    session_id = getattr(session, "session_id", "") or ""
    queue = JobQueue()
    worker = ResearchWorker(queue=queue, heartbeats=HeartbeatStore())

    async def _runner(_job: Any) -> Any:
        return await _maybe_await(
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

    try:
        async with typing_keepalive(
            message.channel,
            label="research_loop",
            session_id=session_id or None,
        ):
            outcome = await worker.run_one(
                session_id=session_id,
                runner=_runner,
                payload={
                    "thread_id": thread_id,
                    "role_for_research": role_for_research,
                    "prompt_excerpt": (prompt_text or "")[:160],
                },
            )
    except Exception as exc:  # noqa: BLE001 - non-fatal; report and return
        report = EngineeringResearchLoopReport(error=str(exc))
        await send_chunks(
            message.channel,
            f"⚠️ research loop 실패: {exc}",
        )
        return report

    if outcome.skipped_reason == "duplicate_in_flight":
        # Idempotency notice — keeps the user informed without
        # double-running the collector. We deliberately don't post
        # the typical "운영-리서치 forum thread 게시:" status here
        # because the original in-flight job will publish it.
        await send_chunks(
            message.channel,
            "⏳ 이 세션은 이미 운영-리서치 수집이 진행 중이에요. "
            "끝나는 대로 thread에 결과가 올라옵니다.",
        )
        return EngineeringResearchLoopReport()
    if outcome.skipped_reason == "claimed_by_other_worker":
        # Race only relevant once M6 introduces a standalone worker.
        # In M3 in-process this branch is theoretical; surfacing a
        # message keeps the contract explicit.
        return EngineeringResearchLoopReport()

    report = _coerce_research_loop_report(outcome.runner_result)
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
    from ...agents.lifecycle.persistence import persist_research_forum_link

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
            from ...agents.research.collector import resolve_forum_comment_mode
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
                from ..engineering_team_runtime import research_open_call_directive
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
                from ..research_forum import chunk_for_discord_message
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
                from ..research_forum import chunk_for_discord_message
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


# P0-P step 4: value coercion helpers extracted to .utils.
from .utils import _optional_str, _safe_int  # noqa: E402,F401 — re-export


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


# P0-P step 4: async + message-parsing + env + recall coverage extracted to .utils.
from .utils import (  # noqa: E402,F401 — re-export for back-compat
    _attach_recall_coverage,
    _maybe_await,
    _normalize_channel_name,
    _optional_bool_env,
    _optional_int_env,
    _optional_string_env,
    extract_message_attachments,
    extract_user_links_from_message,
)
