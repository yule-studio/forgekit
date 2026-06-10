"""engineering_channel_router — work_report preview + clarification display + outcome coercion.

Three thin responsibilities pulled out of ``_legacy``:

- :func:`_emit_work_report_preview` — build the deterministic
  :class:`WorkReport` for a session at lifecycle close, persist a
  snapshot, and post a Markdown preview to Discord.
- :func:`_format_clarification_message` — multi-candidate display for
  the ``ACTION_ASK`` branch (renders the candidate list the user picks
  from with "1번" / "기존 세션 …").
- :func:`_coerce_outcome` / :func:`_coerce_research_loop_report` —
  defensive shape coercion so adapters can ship custom dataclasses with
  compatible attrs and the router still gets a clean envelope.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from yule_engineering.agents.routing import EngineeringRoutingDecision
from .models import (
    EngineeringConversationOutcome,
    EngineeringResearchLoopReport,
    SendChunksFn,
)
from .session_persistence import _persist_extra_keys, _work_report_to_dict
from .utils import _optional_str, _safe_int


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
        from yule_engineering.agents.reports.work_report import (
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


# P0-P step 9: research loop hook + P0-K guard + forum status persistence
# extracted to .research_loop.
from .research_loop import (  # noqa: E402,F401 — re-export for back-compat
    _maybe_persist_research_pack,
    _research_loop_blocked_by_command_only,
    _run_research_loop_hook,
    persist_research_forum_status,
    _format_member_bots_forum_status,
    make_default_research_loop,
)

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


__all__ = (
    "_emit_work_report_preview",
    "_format_clarification_message",
    "_coerce_research_loop_report",
    "_coerce_outcome",
)
