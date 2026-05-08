"""Autonomous-execution producers — A-M10b helpers.

Compose :class:`ObsidianWriteRequest` instances for the L1/L2 note
kinds without forcing the caller to know the layout/folder/metadata
shape. Trigger wiring (research-loop completion → research-log,
audit-flush window → agent-ops) lives in M10c.

These helpers are pure — they neither queue nor write. They build a
typed request and the caller decides where/when to enqueue it via
:meth:`ObsidianWriterWorker.enqueue`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence


__all__ = (
    "build_agent_ops_request",
    "build_research_log_request",
    "build_simple_body_request",
)


def _utc_today_iso() -> str:
    return datetime.now(tz=timezone.utc).date().isoformat()


def build_research_log_request(
    *,
    session: Any,
    snapshot: Any = None,
    canonical_title: Optional[str] = None,
    topic_key: Optional[str] = None,
    source_thread_url: Optional[str] = None,
    source_thread_title: Optional[str] = None,
    selected_roles: Sequence[str] = (),
    project: Optional[str] = None,
    layout: Optional[str] = None,
    requested_by: Optional[str] = None,
) -> Any:
    """Compose an :class:`ObsidianWriteRequest` for research-log.

    Reads research_pack / research_synthesis_text out of
    ``session.extra`` so the renderer has hydration material. The
    *snapshot* (a :class:`ThreadSnapshot`) is optional — when None
    the renderer will fall back to whatever the session-extras
    carry. Empty-body guard runs at render time, not here.
    """

    from ..job_queue.obsidian_writer_worker import (
        NOTE_KIND_RESEARCH_LOG,
        ObsidianWriteRequest,
    )

    extra = getattr(session, "extra", None) or {}
    if not isinstance(extra, Mapping):
        extra = {}

    snapshot_payload: Mapping[str, Any] = {}
    if snapshot is not None and hasattr(snapshot, "to_payload"):
        try:
            payload = snapshot.to_payload()
            if isinstance(payload, Mapping):
                snapshot_payload = dict(payload)
        except Exception:  # noqa: BLE001
            snapshot_payload = {}

    research_pack = extra.get("research_pack") if isinstance(extra, Mapping) else None
    synthesis_text = (
        extra.get("research_synthesis_text")
        if isinstance(extra, Mapping)
        else None
    )

    title = (
        canonical_title
        or (research_pack.get("title") if isinstance(research_pack, Mapping) else None)
        or _short(getattr(session, "prompt", None))
        or "research-log"
    )

    metadata: dict[str, Any] = {
        "autonomy_level": "L1_AUTO_RECORD_REQUIRED",
        "original_prompt": getattr(session, "prompt", None) or "",
        "selected_roles": list(selected_roles),
    }
    if topic_key:
        metadata["topic_key"] = topic_key
    if source_thread_url:
        metadata["source_thread_url"] = source_thread_url
    if source_thread_title:
        metadata["source_thread_title"] = source_thread_title
    if requested_by:
        metadata["requested_by"] = requested_by
    if snapshot_payload:
        metadata["thread_snapshot"] = snapshot_payload
    if isinstance(research_pack, Mapping):
        metadata["research_pack"] = dict(research_pack)
    if isinstance(synthesis_text, str) and synthesis_text.strip():
        metadata["synthesis_text"] = synthesis_text.strip()
    if canonical_title:
        metadata["canonical_title"] = canonical_title

    return ObsidianWriteRequest(
        session_id=str(getattr(session, "session_id", "") or ""),
        note_kind=NOTE_KIND_RESEARCH_LOG,
        title=str(title)[:80],
        source_thread_id=_first_int(
            getattr(session, "thread_id", None),
            extra.get("research_forum_thread_id") if isinstance(extra, Mapping) else None,
        ),
        source_thread_url=source_thread_url,
        project=project,
        layout=layout,
        metadata=metadata,
    )


def build_agent_ops_request(
    *,
    session: Any,
    audit_entries: Optional[Iterable[Any]] = None,
    title: Optional[str] = None,
    project: Optional[str] = None,
    layout: Optional[str] = None,
) -> Any:
    """Compose an :class:`ObsidianWriteRequest` for agent-ops.

    *audit_entries* is an iterable of either :class:`AgentOpsEntry`
    or already-serialised payload dicts. When None, reads the list
    out of ``session.extra['agent_ops_audit']``.
    """

    from ..job_queue.obsidian_writer_worker import (
        NOTE_KIND_AGENT_OPS,
        ObsidianWriteRequest,
    )
    from .agent_ops_log import (
        AgentOpsEntry,
        SESSION_EXTRA_KEY,
    )

    raw_payloads: list[Mapping[str, Any]] = []
    if audit_entries is None:
        extra = getattr(session, "extra", None) or {}
        if isinstance(extra, Mapping):
            stored = extra.get(SESSION_EXTRA_KEY)
            if isinstance(stored, list):
                raw_payloads = [
                    item for item in stored if isinstance(item, Mapping)
                ]
    else:
        for entry in audit_entries:
            if isinstance(entry, AgentOpsEntry):
                raw_payloads.append(dict(entry.to_payload()))
            elif isinstance(entry, Mapping):
                raw_payloads.append(dict(entry))

    note_title = title or f"agent-ops {_utc_today_iso()}"

    return ObsidianWriteRequest(
        session_id=str(getattr(session, "session_id", "") or ""),
        note_kind=NOTE_KIND_AGENT_OPS,
        title=note_title,
        project=project,
        layout=layout,
        metadata={
            "autonomy_level": "L1_AUTO_RECORD_REQUIRED",
            "audit_entries": raw_payloads,
        },
    )


def build_simple_body_request(
    *,
    session: Any,
    note_kind: str,
    title: str,
    body: str,
    autonomy_level: str = "L2_AUTO_POST_REPORT",
    project: Optional[str] = None,
    layout: Optional[str] = None,
    extras: Optional[Mapping[str, Any]] = None,
) -> Any:
    """Compose an :class:`ObsidianWriteRequest` for postmortem /
    proposal / blog-draft kinds. Producer authors the body.
    """

    from ..job_queue.obsidian_writer_worker import ObsidianWriteRequest

    metadata: dict[str, Any] = {
        "autonomy_level": autonomy_level,
        "body": body,
    }
    if extras:
        for key, value in dict(extras).items():
            if key not in metadata and value is not None:
                metadata[key] = value
    return ObsidianWriteRequest(
        session_id=str(getattr(session, "session_id", "") or ""),
        note_kind=note_kind,
        title=title,
        project=project,
        layout=layout,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _short(value: Any, *, limit: int = 80) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:limit]


def _first_int(*candidates: Any) -> Optional[int]:
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            return int(candidate)
        except (TypeError, ValueError):
            continue
    return None
