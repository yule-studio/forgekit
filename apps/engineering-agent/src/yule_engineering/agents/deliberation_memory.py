"""RetrievedMemory citation / rendering helpers (Phase 3 follow-up).

Extracted verbatim from :mod:`deliberation` (behavior-preserving split).
These helpers assign stable citation IDs to retrieval hits and render
them as evidence one-liners / prompt blocks. They operate purely on the
duck-typed :class:`~deliberation.RetrievedMemory` shape (via ``getattr``
and :func:`dataclasses.replace`), so this module has **no** dependency on
:mod:`deliberation` and sits at the bottom of the import graph. The public
helpers are re-exported from :mod:`deliberation` so existing importers
(``from .deliberation import assign_citation_ids``) keep resolving.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional, Sequence, Tuple


def assign_citation_ids(
    memory_context: Sequence["RetrievedMemory"],
) -> Tuple["RetrievedMemory", ...]:
    """Return a copy of *memory_context* with ``citation_id`` populated.

    Hits that already carry a non-empty ``citation_id`` keep it; empty
    slots receive sequential ``m1``, ``m2``, ... labels in input order.
    The output preserves the original order so callers can rely on the
    label matching the hit position. Empty input returns an empty tuple.
    """

    if not memory_context:
        return ()
    used: set[str] = set()
    for hit in memory_context:
        existing = (getattr(hit, "citation_id", "") or "").strip()
        if existing:
            used.add(existing)

    labelled: list[RetrievedMemory] = []
    for index, hit in enumerate(memory_context, start=1):
        existing = (getattr(hit, "citation_id", "") or "").strip()
        if existing:
            labelled.append(hit)
            continue
        candidate = f"m{index}"
        # If a custom id earlier in the input collides with the
        # position-derived candidate, fall through to the next free
        # slot. In practice this only kicks in when callers pre-set
        # ``m<N>`` themselves; otherwise the position is the id.
        bump = 0
        while candidate in used:
            bump += 1
            candidate = f"m{index + bump}"
        used.add(candidate)
        labelled.append(replace(hit, citation_id=candidate))
    return tuple(labelled)


def memory_evidence_lines(
    memory_context: Sequence["RetrievedMemory"],
    *,
    limit: int = 2,
) -> Tuple[str, ...]:
    """Render up to *limit* memory hits as evidence-style one-liners.

    Format: ``[<cid> · <source>·<note_kind>] <title> — <path> · <snippet>``.
    Citation IDs are auto-assigned when missing so each rendered line is
    unambiguously back-pointable to a structured hit. FTS5 highlight
    markers (``«»``) are stripped. Empty input or hits with neither title
    nor snippet are skipped — never raises.
    """

    if not memory_context:
        return ()
    labelled = assign_citation_ids(memory_context)
    out: list[str] = []
    for hit in labelled:
        title = (getattr(hit, "title", "") or "").strip()
        snippet = _strip_fts_markers(
            (getattr(hit, "snippet", "") or "").strip()
        )
        if not title and not snippet:
            continue
        source_kind = (getattr(hit, "source_kind", "") or "memory").strip() or "memory"
        note_kind = getattr(hit, "note_kind", None)
        tag = f"{source_kind}·{note_kind}" if note_kind else source_kind
        path = (getattr(hit, "path", None) or "").strip()
        cid = (getattr(hit, "citation_id", "") or "").strip() or "m?"
        line = f"[{cid} · {tag}] {title or '(제목 없음)'}"
        if path:
            line += f" — {path}"
        if snippet:
            line += f" · {snippet[:160]}"
        out.append(line)
        if len(out) >= max(0, limit):
            break
    return tuple(out)


def memory_hits_by(
    memory_context: Sequence["RetrievedMemory"],
    *,
    kind: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 3,
) -> Tuple["RetrievedMemory", ...]:
    """Return up to *limit* hits matching ``kind`` and/or ``source``.

    The returned tuple shares citation IDs assigned by
    :func:`assign_citation_ids`, so callers can quote a stable ``[m1]``
    label alongside structured fields (path/score/source). Empty result
    when nothing matches — callers branch on this to preserve
    deterministic output.
    """

    if not memory_context:
        return ()
    labelled = assign_citation_ids(memory_context)
    matches: list[RetrievedMemory] = []
    for hit in labelled:
        if kind is not None and getattr(hit, "note_kind", None) != kind:
            continue
        if source is not None and getattr(hit, "source_kind", None) != source:
            continue
        matches.append(hit)
        if len(matches) >= max(1, limit):
            break
    return tuple(matches)


def memory_hint_for_role(
    memory_context: Sequence["RetrievedMemory"],
    *,
    kind: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 1,
) -> Optional[str]:
    """Return the first matching hit's ``[cid] title`` string or ``None``.

    Now backed by :func:`memory_hits_by` so the citation id is part of
    the phrase by default — callers that quote this in risks/next_actions
    automatically carry the back-pointer to the structured block. When
    ``limit > 1`` only the first match is returned (legacy semantic).
    """

    matches = memory_hits_by(
        memory_context, kind=kind, source=source, limit=max(1, limit)
    )
    if not matches:
        return None
    first = matches[0]
    title = (getattr(first, "title", "") or "").strip() or "(제목 없음)"
    cid = (getattr(first, "citation_id", "") or "").strip()
    return f"[{cid}] {title}" if cid else title


def format_memory_block(
    memory_context: Sequence["RetrievedMemory"],
) -> str:
    """Return a multi-line, runner/prompt-friendly memory block.

    Designed to be spliced directly into an LLM prompt (or written to a
    debug log) — preserves citation_id, source/note kind, title, path,
    score, and a short snippet. The deterministic fallback uses
    :func:`memory_evidence_lines` for in-message rendering; this function
    is for the structured context the runner sees alongside the role
    take.

    Empty input returns ``""`` so the caller can simply ``if block:
    prompt += block``.
    """

    if not memory_context:
        return ""
    labelled = assign_citation_ids(memory_context)
    lines: list[str] = []
    for hit in labelled:
        cid = (getattr(hit, "citation_id", "") or "").strip() or "m?"
        source_kind = getattr(hit, "source_kind", "") or "memory"
        note_kind = getattr(hit, "note_kind", None)
        kind_part = f"{source_kind}/{note_kind}" if note_kind else source_kind
        title = (getattr(hit, "title", "") or "").strip() or "(제목 없음)"
        path = (getattr(hit, "path", None) or "").strip()
        score = float(getattr(hit, "score", 0.0) or 0.0)
        header = f"[{cid}] ({kind_part}) {title}"
        if path:
            header += f" — {path}"
        header += f" (score={score:.3f})"
        lines.append(header)
        snippet = _strip_fts_markers(
            (getattr(hit, "snippet", "") or "").strip()
        )
        if snippet:
            lines.append(f"snippet: {snippet[:240]}")
    return "\n".join(lines)


def _strip_fts_markers(text: str) -> str:
    """Remove FTS5 highlight markers (``«»``) and trim whitespace."""

    if not text:
        return ""
    return text.replace("«", "").replace("»", "").strip()
