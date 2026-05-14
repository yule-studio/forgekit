"""engineering_channel_router — research loop hook + P0-K command-only guard.

Owns the bridge between the gateway and the autonomous research loop:

- :func:`_research_loop_blocked_by_command_only` — P0-K (#148) hard
  rule. When the user's prompt is a bare proceed/approval phrase the
  research loop must NEVER fire (otherwise "진행 해줘" gets queried as
  a research topic and lands as ``[Reference] 진행 해줘`` thread spam).
- :func:`_run_research_loop_hook` — invoke the loop hook, surface its
  follow-up / status messages, never crash the bot on hook failure.
- :func:`_maybe_persist_research_pack` — write the collected pack to
  session.extra so the status diagnostic sees it later.
- :func:`persist_research_forum_status` — operator-facing summary line
  posted after a successful publish.
- :func:`_format_member_bots_forum_status` — member-bots vs gateway
  publication mode display.
- :func:`make_default_research_loop` — factory for the default hook
  the production gateway wires when no override is provided.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping, Optional, Sequence

from .models import (
    EngineeringResearchLoopReport,
    ResearchLoopFn,
    SendChunksFn,
)
from .session_persistence import _persist_extra_keys
from ...agents.research.persistence import persist_research_artifacts
from .utils import _maybe_await, _optional_str, _safe_int, extract_message_attachments

logger = logging.getLogger(__name__)


def _coerce_research_loop_report(raw: Any) -> EngineeringResearchLoopReport:
    """Defer to ``_legacy._coerce_research_loop_report`` (extracted to
    .reporting in P0-P step 10). Until then research_loop calls it
    through this thin shim to avoid a circular import."""

    from .main import _coerce_research_loop_report as _impl

    return _impl(raw)


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


__all__ = (
    "_maybe_persist_research_pack",
    "_research_loop_blocked_by_command_only",
    "_run_research_loop_hook",
    "persist_research_forum_status",
    "_format_member_bots_forum_status",
    "make_default_research_loop",
)
