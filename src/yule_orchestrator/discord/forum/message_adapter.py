"""``on_message`` adapter for operations-research forum threads — A-M7.5b.

Wires the M7.5 pure helpers (``forum_obsidian_handoff`` +
``role_selection.parse_role_change_request``) into Discord's
``on_message``. Kept in its own module so ``bot.py``'s already-large
``on_message`` only adds a single call site, matching the same
shape ``_route_engineering_approval_reply`` (M6.1b-2) follows.

Adapter invariants:

  * Lazy imports — no SQLite / queue cost when the message lands in
    a non-forum channel.
  * ``handled=False`` short-circuit — caller falls through to the
    existing engineering route exactly the way the approval reply
    adapter does.
  * **Order**: forum save request first, role-change second. A
    save request that also happens to mention a role would be
    routed to the approval card; a pure role-change message gets
    persisted as an active_research_roles update.
  * Bot self-messages and slash commands are dropped silently.

What this module does NOT do:

  * Touch ``#승인-대기`` — that's owned by the approval reply
    router (M6.1b-2).
  * Touch the work-thread Obsidian approval flow — preserved
    untouched.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional


logger = logging.getLogger(__name__)


# Friendly responses for the role-change branch. The save-request
# branch reuses the templates exposed by ``forum_obsidian_handoff``.
RESPONSE_ROLE_ADDED: str = (
    "✅ 다음 turn 부터 {added} 도 함께 참여하도록 했어요. "
    "이번 thread 의 토의에 새 관점을 추가합니다."
)
RESPONSE_ROLE_REMOVED: str = (
    "✅ {removed} 는 이번 thread 의 활성 역할에서 빠집니다. 필요하면 다시 "
    "“{first} 도 참여시켜” 라고 말해 주세요."
)
RESPONSE_ROLE_ALL_TEAM: str = (
    "✅ 사용자가 명시 요청해서 이번 thread 는 전체 팀 관점으로 진행해요. "
    "다음 turn 부터 모든 역할이 자기 관점으로 정리합니다."
)
RESPONSE_ROLE_NO_CHANGE: str = (
    "ℹ️ 활성 역할 목록에 변동이 없어요. 현재 참여 역할: {active}."
)
RESPONSE_ROLE_CHANGE_NO_SESSION: str = (
    "❓ 이 thread 에 연결된 세션을 찾지 못해 활성 역할을 갱신하지 못했어요. "
    "thread 가 만들어진 직후가 아니라면 `/engineer_show` 로 확인해 주세요."
)


# Skipped-reason constants for tests + status surfaces.
SKIPPED_NOT_FORUM_THREAD: str = "not_forum_thread"
SKIPPED_NO_INTENT: str = "no_intent"


@dataclass(frozen=True)
class ForumMessageRouteResult:
    """What the adapter decided.

    ``handled`` follows the same convention as the approval reply
    adapter: True means the user got a response (success or
    friendly error) and ``on_message`` must short-circuit; False
    means fall through to the engineering route.
    """

    handled: bool
    response_sent: Optional[str] = None
    skipped_reason: Optional[str] = None
    approval_job_id: Optional[str] = None
    role_change_audit: Optional[dict] = None


def _is_forum_thread(message: Any) -> bool:
    """Forum threads are Discord text-channels with a non-None parent.

    ``parent_id`` is the cheap path; ``parent`` exists on the
    fully-hydrated channel object. Either signal is enough — we
    just need to know "this came from inside a forum/thread".
    """

    channel = getattr(message, "channel", None)
    if channel is None:
        return False
    return getattr(channel, "parent_id", None) is not None or getattr(
        channel, "parent", None
    ) is not None


async def route_forum_message(
    *,
    message: Any,
    text: str,
    discord_module: Any,
    send_chunks_factory: Optional[Callable[[Any], Callable[..., Awaitable[Any]]]] = None,
    queue: Any = None,
    approval_worker: Any = None,
    obsidian_writer_worker: Any = None,
    session_lister: Any = None,
    session_loader: Any = None,
    session_updater: Any = None,
    save_request_detector: Optional[Callable[[str], bool]] = None,
) -> ForumMessageRouteResult:
    """Route a forum-thread ``on_message`` event into the M7.5 helpers.

    Returns ``handled=False`` for non-forum messages so ``bot.py``
    falls through to its existing engineering / planning routing.

    Production wiring (no kwargs supplied) lazy-builds the queue +
    approval worker + session lister/updater. Tests inject all four
    so they can drive every branch without a SQLite cache or live
    Discord send.
    """

    if not _is_forum_thread(message):
        return ForumMessageRouteResult(
            handled=False, skipped_reason=SKIPPED_NOT_FORUM_THREAD
        )
    if not text or not text.strip():
        return ForumMessageRouteResult(
            handled=False, skipped_reason=SKIPPED_NO_INTENT
        )

    # ------------------------------------------------------------------
    # Branch 1 — Obsidian save request → ApprovalRequest producer.
    # ------------------------------------------------------------------

    from ...agents.job_queue.forum_obsidian_handoff import (
        SKIPPED_NOT_SAVE_REQUEST,
        render_handoff_response,
        route_forum_obsidian_save_request,
    )

    queue_handle, worker_handle, session_lister_fn = _resolve_queue_deps(
        queue=queue,
        approval_worker=approval_worker,
        session_lister=session_lister,
    )
    obsidian_writer_handle = _resolve_obsidian_writer_worker(
        obsidian_writer_worker=obsidian_writer_worker,
        queue=queue_handle,
    )
    handoff_outcome = await route_forum_obsidian_save_request(
        message=message,
        text=text,
        queue=queue_handle,
        approval_worker=worker_handle,
        session_lister=session_lister_fn,
        save_request_detector=save_request_detector,
        obsidian_writer_worker=obsidian_writer_handle,
    )
    if handoff_outcome.handled:
        rendered = render_handoff_response(handoff_outcome)
        sender = _resolve_send_chunks(
            discord_module=discord_module,
            send_chunks_factory=send_chunks_factory,
        )
        if rendered and sender is not None:
            try:
                await sender(message.channel, rendered)
            except Exception:  # noqa: BLE001 - send is best-effort
                logger.warning(
                    "forum message adapter: send_chunks raised on save reply",
                    exc_info=True,
                )
        return ForumMessageRouteResult(
            handled=True,
            response_sent=rendered,
            skipped_reason=handoff_outcome.skipped_reason,
            approval_job_id=handoff_outcome.approval_job_id,
        )

    # ``handled=False`` from the handoff means "not a save request".
    # Continue to the role-change branch.

    # ------------------------------------------------------------------
    # Branch 2 — role-change request ("QA도 참여시켜").
    # ------------------------------------------------------------------

    from ...agents.lifecycle.role_selection import (
        ROLE_TECH_LEAD,
        apply_role_change,
        append_role_change_audit,
        get_effective_active_roles,
        parse_role_change_request,
    )

    change = parse_role_change_request(text)
    if change is None:
        # ------------------------------------------------------------------
        # Branch 3 — conversational follow-up (P0-F).
        # ------------------------------------------------------------------
        followup_outcome = await _route_forum_followup(
            message=message,
            text=text,
            discord_module=discord_module,
            send_chunks_factory=send_chunks_factory,
            session_lister=session_lister,
            session_loader=session_loader,
            session_updater=session_updater,
        )
        if followup_outcome.handled:
            return ForumMessageRouteResult(
                handled=True,
                response_sent=followup_outcome.response_sent,
                skipped_reason=followup_outcome.skipped_reason,
            )
        return ForumMessageRouteResult(
            handled=False, skipped_reason=SKIPPED_NO_INTENT
        )

    session_loader_fn, session_updater_fn = _resolve_session_persistence(
        session_loader=session_loader, session_updater=session_updater
    )
    session = _resolve_session_for_forum_thread(
        message=message,
        session_lister=session_lister_fn,
    )
    sender = _resolve_send_chunks(
        discord_module=discord_module,
        send_chunks_factory=send_chunks_factory,
    )

    if session is None:
        if sender is not None:
            try:
                await sender(message.channel, RESPONSE_ROLE_CHANGE_NO_SESSION)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "forum message adapter: send_chunks raised on no-session reply",
                    exc_info=True,
                )
        return ForumMessageRouteResult(
            handled=True,
            response_sent=RESPONSE_ROLE_CHANGE_NO_SESSION,
            skipped_reason="no_session_for_thread",
        )

    current_active = list(get_effective_active_roles(session))
    requested_by = _author_handle(message)
    requested_at = (
        datetime.now(tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )
    outcome = apply_role_change(
        current_active=current_active,
        change=change,
        requested_by=requested_by,
        requested_at=requested_at,
    )

    new_active = tuple(outcome.new_active_roles)
    persisted = _persist_role_change(
        session=session,
        new_active_roles=new_active,
        audit=outcome.audit,
        session_updater=session_updater_fn,
        append_audit_fn=append_role_change_audit,
    )
    if not persisted:
        # Persistence failed — surface a friendly message but
        # don't pretend the role list changed.
        if sender is not None:
            try:
                await sender(
                    message.channel, RESPONSE_ROLE_CHANGE_NO_SESSION
                )
            except Exception:  # noqa: BLE001
                pass
        return ForumMessageRouteResult(
            handled=True,
            response_sent=RESPONSE_ROLE_CHANGE_NO_SESSION,
            skipped_reason="persist_failed",
        )

    response = _format_role_change_response(
        change=change,
        outcome=outcome,
        active=new_active,
    )
    if sender is not None and response:
        try:
            await sender(message.channel, response)
        except Exception:  # noqa: BLE001 - send best-effort
            logger.warning(
                "forum message adapter: send_chunks raised on role reply",
                exc_info=True,
            )
    return ForumMessageRouteResult(
        handled=True,
        response_sent=response,
        role_change_audit=dict(outcome.audit),
    )


# ---------------------------------------------------------------------------
# Internal helpers — production defaults + persistence shim.
# ---------------------------------------------------------------------------


def _resolve_queue_deps(*, queue, approval_worker, session_lister):
    """Build production queue + ApprovalWorker on demand.

    Tests pass all three; production passes None and we lazy-import
    so a non-forum message never pays the SQLite open cost.
    """

    if queue is not None and approval_worker is not None and session_lister is not None:
        return queue, approval_worker, session_lister
    from ...agents.job_queue import (
        ApprovalWorker,
        HeartbeatStore,
        JobQueue,
    )
    from ...agents.job_queue.approval_discord_poster import (
        build_approval_channel_resolver,
        build_production_post_fn,
    )
    from ...agents.workflow_state import list_sessions as _list_sessions

    q = queue or JobQueue()
    if approval_worker is None:
        worker = ApprovalWorker(
            queue=q,
            heartbeats=HeartbeatStore(),
            post_fn=build_production_post_fn(),
            channel_resolver=build_approval_channel_resolver(),
        )
    else:
        worker = approval_worker

    if session_lister is None:
        def _lister(*, limit: int = 100):
            return _list_sessions(limit=limit)

        lister = _lister
    else:
        lister = session_lister

    return q, worker, lister


def _resolve_obsidian_writer_worker(*, obsidian_writer_worker, queue):
    """Return the production :class:`ObsidianWriterWorker` lazily.

    Tests pass ``None`` to skip the M10c auto-save loop; production
    wiring builds the worker on the same SQLite queue + heartbeat
    store so research-log enqueues land in the same DB the
    standalone consumer (M6) reads.
    """

    if obsidian_writer_worker is not None:
        return obsidian_writer_worker
    try:
        from ...agents.job_queue import HeartbeatStore
        from ...agents.job_queue.obsidian_writer_worker import (
            ObsidianWriterWorker,
            default_render_fn,
            default_vault_root_resolver,
            default_write_fn,
        )
    except Exception:  # noqa: BLE001
        return None
    try:
        return ObsidianWriterWorker(
            queue=queue,
            heartbeats=HeartbeatStore(),
            render_fn=default_render_fn,
            write_fn=default_write_fn,
            vault_root_resolver=default_vault_root_resolver,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "forum message adapter: ObsidianWriterWorker init raised",
            exc_info=True,
        )
        return None


def _resolve_session_persistence(*, session_loader, session_updater):
    if session_loader is not None and session_updater is not None:
        return session_loader, session_updater
    try:
        from ...agents.workflow_state import (
            load_session as _load,
            update_session as _update,
        )
    except Exception:  # noqa: BLE001 - partial install
        return session_loader, session_updater
    return session_loader or _load, session_updater or _update


def _resolve_send_chunks(*, discord_module, send_chunks_factory):
    if send_chunks_factory is None:
        return None
    try:
        return send_chunks_factory(discord_module)
    except TypeError:
        # Test stub: factory takes no args.
        try:
            return send_chunks_factory()
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001
        return None


def _resolve_session_for_forum_thread(*, message, session_lister):
    """Same shape as ``forum_obsidian_handoff`` resolver — kept here
    so the role-change branch doesn't import a private helper.
    """

    if session_lister is None:
        return None
    channel = getattr(message, "channel", None)
    channel_id = getattr(channel, "id", None)
    if channel_id is None:
        return None
    try:
        sessions = session_lister(limit=100)
    except TypeError:
        try:
            sessions = session_lister()
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001
        return None

    # P0-K (#148) — primary lookup: research_forum_thread_id.
    for session in sessions or ():
        extra = getattr(session, "extra", None) or {}
        if not isinstance(extra, dict):
            continue
        forum_thread_id = extra.get("research_forum_thread_id")
        if forum_thread_id is None:
            continue
        try:
            if int(forum_thread_id) == int(channel_id):
                return session
        except (TypeError, ValueError):
            continue

    # P0-K (#148) — fallback: resumed_thread_id. When the user
    # continues a session via "기존 세션 X 이어가자" and later asks
    # for a role-change in the resumed thread ("백엔드도 포함시켜"),
    # the resumed thread channel doesn't carry the original
    # research_forum_thread_id. Look up sessions whose extra
    # ``resumed_thread_id`` matches the current channel so the
    # role-change branch can resolve and update the right session.
    for session in sessions or ():
        extra = getattr(session, "extra", None) or {}
        if not isinstance(extra, dict):
            continue
        resumed_thread_id = extra.get("resumed_thread_id")
        if resumed_thread_id is None:
            continue
        try:
            if int(resumed_thread_id) == int(channel_id):
                return session
        except (TypeError, ValueError):
            continue

    # P0-N3 (live bug #3) — last-resort: ``session.thread_id``. The
    # work thread created by thread_kickoff_fn carries its own thread
    # id on the session row, but a fresh session has no
    # ``research_forum_thread_id`` (forum publish happens later) and
    # no ``resumed_thread_id`` (not resumed). Role-change typed in
    # this work thread previously fell through to "no session" and
    # surfaced ``RESPONSE_ROLE_CHANGE_NO_SESSION``. Resolve against
    # ``session.thread_id`` so the role update lands.
    for session in sessions or ():
        thread_id_attr = getattr(session, "thread_id", None)
        if thread_id_attr is None:
            continue
        try:
            if int(thread_id_attr) == int(channel_id):
                return session
        except (TypeError, ValueError):
            continue
    return None


def _persist_role_change(
    *,
    session: Any,
    new_active_roles: tuple,
    audit: dict,
    session_updater: Any,
    append_audit_fn,
) -> bool:
    """Mutate ``session.extra['active_research_roles']`` + append audit.

    Returns True on success. Production uses
    :func:`agents.workflow_state.update_session`; tests inject a
    capture stub.
    """

    if session is None or session_updater is None:
        return False
    try:
        from dataclasses import replace as _replace
    except Exception:  # noqa: BLE001
        return False

    extra_in = dict(getattr(session, "extra", None) or {})
    extra_in["active_research_roles"] = list(new_active_roles)
    new_extra = append_audit_fn(extra_in, audit)
    try:
        updated = _replace(session, extra=new_extra)
    except TypeError:
        # SimpleNamespace-style session — mutate in place for tests.
        if hasattr(session, "extra") and isinstance(session.extra, dict):
            session.extra.update(new_extra)
            try:
                session_updater(session, now=datetime.now(tz=timezone.utc))
            except Exception:  # noqa: BLE001
                pass
            return True
        return False
    try:
        session_updater(updated, now=datetime.now(tz=timezone.utc))
    except Exception:  # noqa: BLE001
        return False
    return True


def _author_handle(message: Any) -> str:
    author = getattr(message, "author", None)
    if author is None:
        return "unknown"
    name = (
        getattr(author, "global_name", None)
        or getattr(author, "name", None)
        or getattr(author, "display_name", None)
    )
    user_id = getattr(author, "id", None)
    if name and user_id is not None:
        return f"{name} ({user_id})"
    if name:
        return str(name)
    if user_id is not None:
        return f"user:{user_id}"
    return "unknown"


def _format_role_change_response(*, change, outcome, active) -> str:
    if change.action == "replace_all_team":
        return RESPONSE_ROLE_ALL_TEAM
    if outcome.added_roles:
        return RESPONSE_ROLE_ADDED.format(
            added=", ".join(outcome.added_roles)
        )
    if outcome.removed_roles:
        first = outcome.removed_roles[0]
        return RESPONSE_ROLE_REMOVED.format(
            removed=", ".join(outcome.removed_roles), first=first
        )
    # Defensive — request had a role list but everything was already
    # active / nothing matched. Surface the current state so the
    # operator knows the system saw the request.
    return RESPONSE_ROLE_NO_CHANGE.format(active=", ".join(active))


async def _route_forum_followup(
    *,
    message: Any,
    text: str,
    discord_module: Any,
    send_chunks_factory: Optional[Callable[[Any], Callable[..., Awaitable[Any]]]],
    session_lister: Any,
    session_loader: Any,
    session_updater: Any,
):
    """P0-F branch 3 — conversational follow-up.

    Resolves the thread's existing session (same helper the
    role-change branch uses) and delegates to
    :func:`forum_conversation_adapter.handle_forum_followup`. Drops
    silently when there is no session anchored to the thread or
    when the follow-up helper itself declines to respond.
    """

    from ..forum.conversation_adapter import (
        ForumFollowupResult,
        handle_forum_followup,
    )

    _, _, session_lister_fn = _resolve_queue_deps(
        queue=None,
        approval_worker=None,
        session_lister=session_lister,
    )
    _, session_updater_fn = _resolve_session_persistence(
        session_loader=session_loader,
        session_updater=session_updater,
    )
    session = _resolve_session_for_forum_thread(
        message=message,
        session_lister=session_lister_fn,
    )
    sender = _resolve_send_chunks(
        discord_module=discord_module,
        send_chunks_factory=send_chunks_factory,
    )
    if session is None:
        return ForumFollowupResult(
            handled=False,
            skipped_reason="no_session_for_thread",
        )
    return await handle_forum_followup(
        message=message,
        text=text,
        session=session,
        session_updater=session_updater_fn,
        send_chunks=sender,
    )


__all__ = (
    "ForumMessageRouteResult",
    "RESPONSE_ROLE_ADDED",
    "RESPONSE_ROLE_ALL_TEAM",
    "RESPONSE_ROLE_CHANGE_NO_SESSION",
    "RESPONSE_ROLE_NO_CHANGE",
    "RESPONSE_ROLE_REMOVED",
    "SKIPPED_NOT_FORUM_THREAD",
    "SKIPPED_NO_INTENT",
    "route_forum_message",
)
