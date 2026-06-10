"""Forum→Obsidian handoff — intake / snapshot-enrichment side.

Split out of :mod:`forum_obsidian_handoff` along the
``intake / routing / persistence`` axis. This module owns the
**intake** responsibility: reading the session's selected research
roles out of ``session.extra`` and enriching a sparse thread
snapshot with session-side signals (role research results,
synthesis text, research-pack URLs) before the snapshot is handed
to the approval-request builder.

Pure functions — they never mutate ``session.extra``. The
orchestrator (:mod:`forum_obsidian_handoff`) imports these one-way;
nothing here imports the orchestrator back.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence, Tuple

from .approval_worker import APPROVAL_KIND_OBSIDIAN_WRITE


def _read_selected_roles(session: Any) -> Sequence[str]:
    extra = getattr(session, "extra", None) or {}
    if not isinstance(extra, Mapping):
        return ()
    raw = extra.get("active_research_roles")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(r) for r in raw if isinstance(r, str) and r)


def _enrich_snapshot_from_session(
    *,
    snapshot: Any,
    session: Any,
    request_text: Optional[str],
) -> Any:
    """Pull session.extra signals (role_research_results /
    research_synthesis_text / research_pack URLs) into the
    snapshot when the live thread fetcher didn't catch them.

    Returns a (possibly new) :class:`ThreadSnapshot`. Pure — does
    not mutate session.extra.
    """

    from ..lifecycle.thread_snapshot import (
        ThreadSnapshot,
        extract_links_from_text,
    )

    extra = getattr(session, "extra", None) or {}
    if not isinstance(extra, Mapping):
        return snapshot

    # Role-by-role research summaries (Phase 4 persistence).
    role_summaries = dict(getattr(snapshot, "role_summaries", None) or {})
    raw_role_results = extra.get("role_research_results")
    if isinstance(raw_role_results, Mapping):
        for role, payload in raw_role_results.items():
            if not isinstance(payload, Mapping):
                continue
            top_findings = payload.get("top_findings")
            summary_bits: list[str] = []
            if isinstance(top_findings, list):
                for finding in top_findings[:3]:
                    if isinstance(finding, str) and finding.strip():
                        summary_bits.append(finding.strip())
                    elif isinstance(finding, Mapping):
                        title = finding.get("title") or finding.get("snippet")
                        if isinstance(title, str) and title.strip():
                            summary_bits.append(title.strip())
            if summary_bits and not role_summaries.get(str(role)):
                role_summaries[str(role)] = " · ".join(summary_bits)

    # Tech-lead synthesis text → push into role_summaries under
    # "tech-lead" if the thread didn't already capture it.
    synth_text = extra.get("research_synthesis_text")
    if (
        isinstance(synth_text, str)
        and synth_text.strip()
        and "tech-lead" not in role_summaries
    ):
        head = synth_text.strip().splitlines()[0]
        role_summaries["tech-lead"] = head[:300]

    # Augment links with the research_pack URLs + URLs in the
    # save-request text (fallback for sparse threads).
    links_existing = list(getattr(snapshot, "extracted_links", None) or [])
    seen = set(links_existing)

    def _add(url: str) -> None:
        if url and url not in seen:
            seen.add(url)
            links_existing.append(url)

    raw_pack = extra.get("research_pack")
    if isinstance(raw_pack, Mapping):
        urls = raw_pack.get("urls")
        if isinstance(urls, list):
            for url in urls:
                if isinstance(url, str):
                    _add(url)
    for url in extract_links_from_text(request_text):
        _add(url)

    return ThreadSnapshot(
        messages=getattr(snapshot, "messages", ()),
        extracted_links=tuple(links_existing),
        role_summaries=role_summaries,
        captured_at=getattr(snapshot, "captured_at", None),
    )


# ---------------------------------------------------------------------------
# A-M7.6 — topic-key dedup + revision detection
# ---------------------------------------------------------------------------


_REVISION_PHRASES: Tuple[str, ...] = (
    "개정본",
    "다시 저장",
    "덮어써",
    "갱신해",
    "다시 정리해",
    "revision",
    "supersede",
    "overwrite",
)


def _is_revision_request(text: Optional[str]) -> bool:
    """Whether the user explicitly opted into a revision write.

    The default save phrase ("Obsidian 에 정리해줘") is treated as a
    new save; only when the user adds a revision marker do we
    bypass the saved-state guard.
    """

    if not text:
        return False
    lowered = text.lower()
    return any(phrase in lowered for phrase in _REVISION_PHRASES)


def _topic_dedup_check(
    *,
    queue: Any,
    session_id: str,
    topic_key: str,
    research_thread_id: Optional[int],
    note_kind: str,
) -> Optional[Tuple[str, Any]]:
    """Look for any prior approval_post or obsidian_write row that
    targets the same topic + thread + kind.

    Returns ``(reason, row)`` where reason is one of:
      * ``"topic_pending"`` — approval_post still in_flight (queued/
        assigned/in_progress) or saved (carded but not yet replied).
      * ``"topic_obsidian_in_flight"`` — obsidian_write already
        queued/in_progress for the topic (treat as pending — the
        writer will pick it up).
      * ``"topic_saved"`` — obsidian_write already SAVED with a
        vault_path; revision flow needed.

    Returns ``None`` when no matching row exists — caller proceeds
    to enqueue a fresh approval card.
    """

    if not session_id or not topic_key:
        return None
    try:
        rows = queue.list_for_session(session_id)
    except Exception:  # noqa: BLE001
        return None

    pending_approval: Optional[Any] = None
    obsidian_in_flight: Optional[Any] = None
    obsidian_saved: Optional[Any] = None

    for row in rows or ():
        payload = getattr(row, "payload", None) or {}
        result = getattr(row, "result", None) or {}
        row_thread = payload.get("source_thread_id")
        try:
            row_thread_int = (
                int(row_thread) if row_thread is not None else None
            )
        except (TypeError, ValueError):
            row_thread_int = None

        # Topic match: prefer explicit topic_key in the row's
        # extra/metadata; fall back to thread id when both rows
        # share it (older rows pre-M7.6 don't carry topic_key).
        row_topic = ""
        if isinstance(payload.get("extra"), Mapping):
            row_topic = str(payload["extra"].get("topic_key") or "")
        if not row_topic and isinstance(payload.get("metadata"), Mapping):
            row_topic = str(payload["metadata"].get("topic_key") or "")
        topic_match = (
            (row_topic and row_topic == topic_key)
            or (
                research_thread_id is not None
                and row_thread_int == research_thread_id
            )
        )
        if not topic_match:
            continue

        job_type = getattr(row, "job_type", "")
        state_value = getattr(getattr(row, "state", None), "value", "")

        if job_type == "approval_post":
            row_kind = str(payload.get("approval_kind") or "")
            if row_kind != APPROVAL_KIND_OBSIDIAN_WRITE:
                continue
            # Pending approval = any non-terminal state.
            if state_value not in {"failed_terminal", "failed_retryable"}:
                pending_approval = pending_approval or row
        elif job_type == "obsidian_write":
            row_kind = str(payload.get("note_kind") or "")
            if row_kind != note_kind:
                continue
            if state_value == "saved":
                obsidian_saved = obsidian_saved or row
            elif state_value not in {"failed_terminal", "failed_retryable"}:
                obsidian_in_flight = obsidian_in_flight or row

    # Saved wins (revision-needed) over in-flight; in-flight wins
    # over pending-approval (further along in the lifecycle).
    if obsidian_saved is not None:
        return ("topic_saved", obsidian_saved)
    if obsidian_in_flight is not None:
        return ("topic_obsidian_in_flight", obsidian_in_flight)
    if pending_approval is not None:
        return ("topic_pending", pending_approval)
    return None


def _vault_path_from_row(row: Any) -> Optional[str]:
    """Best-effort extraction of vault path from a SAVED obsidian
    write row. The worker stamps target_path / vault_root on the
    result; we read whichever is populated.
    """

    result = getattr(row, "result", None) or {}
    if not isinstance(result, Mapping):
        return None
    target = result.get("target_path") or result.get("vault_path")
    if target:
        return str(target)
    return None


__all__ = (
    "_enrich_snapshot_from_session",
    "_is_revision_request",
    "_read_selected_roles",
    "_topic_dedup_check",
    "_vault_path_from_row",
)
