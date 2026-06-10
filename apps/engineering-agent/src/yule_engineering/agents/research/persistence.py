"""Persist ResearchPack/synthesis artifacts onto a workflow session.

Used by:

- The Discord engineering channel router, immediately after ``intake_fn``
  creates a session, so the collected ``ResearchPack`` lands in
  ``session.extra["research_pack"]`` even if the downstream research loop
  short-circuits as ``insufficient`` or fails entirely.
- The forum research-loop hook, after the deliberation runs, to
  additionally persist ``TechLeadSynthesis`` and the ``CollectionOutcome``
  metadata.

The function is idempotent — repeated calls with the same payload write
the same ``session.extra`` keys, so persisting eagerly at intake time and
again later from the forum hook is safe.

Failures are caught and logged so this helper never breaks the caller's
control flow; the worst case is that ``session.extra`` does not get the
new keys, which the Obsidian sync CLI surfaces explicitly.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any, Optional

from ..deliberation import synthesis_to_dict
from .pack_render import pack_to_dict
from ..workflow_state import WorkflowSession, update_session


def persist_research_artifacts(
    session: Optional[WorkflowSession],
    pack: Any = None,
    *,
    collection_outcome: Any = None,
    synthesis: Any = None,
    synthesis_text: Optional[str] = None,
) -> Optional[WorkflowSession]:
    """Write research artifacts onto ``session.extra`` and return the new session.

    Stabilisation Phase 2 — even when no pack landed, the helper now
    stamps explicit ``research_status`` / ``research_source_count`` /
    ``research_stop_reason`` / ``research_missing_roles`` /
    ``research_active_roles`` keys derived from *collection_outcome*
    so the work-report builder + status diagnostic can tell the
    difference between "research_pack 누락" and "research 아직 시작
    안 함". Persistence failures are stamped under
    ``research_pack_error`` instead of being silently swallowed.

    Returns the original session unchanged when there is nothing to
    persist (all inputs None) or when the caller passed ``session=None``.
    """

    if session is None:
        return session
    if pack is None and synthesis is None and collection_outcome is None:
        return session
    try:
        extra = dict(getattr(session, "extra", None) or {})
        # Phase 2: derive a deterministic research status snapshot from
        # whatever combination of (pack, collection_outcome) we got.
        # Always write these keys so downstream readers (work_report,
        # status diagnostic, Obsidian gate) don't have to re-derive
        # the same booleans.
        source_count = _resolve_source_count(pack, collection_outcome)
        if pack is not None:
            extra["research_pack"] = pack_to_dict(pack)
            extra["research_source_count"] = source_count
            extra["research_status"] = "ready" if source_count > 0 else "insufficient"
        elif collection_outcome is not None:
            extra["research_source_count"] = source_count
            extra["research_status"] = "insufficient"
        stop_reason = (
            getattr(collection_outcome, "stop_reason", None)
            if collection_outcome is not None
            else None
        )
        if stop_reason:
            extra["research_stop_reason"] = str(stop_reason)
        if collection_outcome is not None:
            under_covered = list(
                getattr(collection_outcome, "under_covered_roles", ()) or ()
            )
            if under_covered:
                extra["research_missing_roles"] = under_covered
            active_roles = list(
                getattr(collection_outcome, "active_roles", ()) or ()
            )
            if active_roles:
                extra["research_active_roles"] = active_roles
            mode = getattr(collection_outcome, "mode", None)
            mode_value = getattr(mode, "value", mode)
            extra["research_collection"] = {
                "mode": str(mode_value) if mode_value is not None else None,
                "collector_name": getattr(collection_outcome, "collector_name", None),
                "query": getattr(collection_outcome, "query", None),
                "auto_collected_count": getattr(
                    collection_outcome, "auto_collected_count", None
                ),
            }
        if synthesis is not None:
            extra["research_synthesis"] = synthesis_to_dict(synthesis)
        if synthesis_text:
            extra["research_synthesis_text"] = str(synthesis_text)
        # Persistence succeeded — clear any prior error stamp so the
        # diagnostic doesn't keep showing a stale failure.
        extra.pop("research_pack_error", None)
        updated = replace(session, extra=extra)
        return update_session(updated, now=datetime.now().astimezone())
    except Exception as exc:  # noqa: BLE001 - forum loop can continue without persisted context
        # Phase 2: stamp a structured failure on the live extra so the
        # status diagnostic + supervisor can tell the operator which
        # step (pack serialisation / SQLite write) blew up.
        try:
            live = getattr(session, "extra", None)
            if isinstance(live, dict):
                live["research_pack_error"] = {
                    "step": "persist_research_artifacts",
                    "reason": str(exc),
                }
        except Exception:  # noqa: BLE001
            pass
        print(f"warning: research pack persistence failed: {exc}")
        return session


def _resolve_source_count(pack: Any, collection_outcome: Any) -> int:
    """Best-effort source count from either a pack or an outcome.

    Prefers the live ``pack.sources`` length so post-iteration counts
    are accurate; falls back to ``collection_outcome.auto_collected_count``
    when no pack is present (NEEDS_USER_INPUT path).
    """

    if pack is not None:
        sources = getattr(pack, "sources", None)
        try:
            return int(len(sources)) if sources is not None else 0
        except TypeError:
            return 0
    if collection_outcome is not None:
        try:
            return int(
                getattr(collection_outcome, "auto_collected_count", 0) or 0
            )
        except (TypeError, ValueError):
            return 0
    return 0
