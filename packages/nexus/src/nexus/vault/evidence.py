"""Nexus evidence schema — the fixed metadata every accumulated artifact carries.

A goal that runs but shows ``children/evidence: 0`` does not look like a long-term
operating system. The fix is to make goal/autopilot progression **write real Nexus
artifacts** with identifiable metadata, so the evidence axis actually accumulates and
``/goal evidence`` reflects live artifacts.

This module fixes that metadata as ONE reusable schema (:class:`EvidenceMeta`) and an
authored-note writer, used by the goal→Nexus bridge today and by the discovery-intake
seam (future external collection) tomorrow — the same shape, so the 24h loop can rely
on it. The minimal required keys are exactly:

    goal_id · lane · packet_id · role · status · created_at · evidence_path

plus ``source`` (where the artifact came from). The note is *authored* by ``role`` so the
agent colour/visibility separation is carried by schema + identity, never by ad-hoc text.

Honesty rails: no vault root → ``None`` (never a fake write); the body is STRUCTURED
sections (summary + linkage), never a raw dump; ``evidence_path`` is the note's own
vault-relative path so the schema is self-describing and links back.

ponytail verdict: a new module (not a wrapper over note.py) is warranted — it fixes a
cross-lane schema (goal + discovery + self-improve) that note.py deliberately does not
know about. It composes ``build_authored_note`` (no duplicate frontmatter logic) and adds
only the schema + lane seams. Kept in ``nexus.vault`` because Nexus owns the evidence axis.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Tuple

from .note import build_authored_note, write_note

# lanes — which progression an artifact belongs to (the `lane` schema field) ----
LANE_GOAL = "goal"                 # goal progression (packet/execution/decision evidence)
LANE_DISCOVERY = "discovery"       # external collection intake (the future seam)
LANE_SELF_IMPROVE = "self-improve"  # forgekit self-improvement loop

# where evidence notes land — raw intake (honest: not curated). One subdir per lane,
# then per goal_id, so a goal's accumulated artifacts are co-located and browsable.
_EVIDENCE_ROOT = "00-inbox/evidence"


def evidence_subdir(lane: str, goal_id: str = "") -> str:
    """Vault-relative dir for a lane's evidence (``00-inbox/evidence/<lane>[/<goal_id>]``)."""

    base = f"{_EVIDENCE_ROOT}/{lane or 'misc'}"
    return f"{base}/{goal_id}" if goal_id else base


@dataclass(frozen=True)
class EvidenceMeta:
    """The fixed metadata every accumulated Nexus artifact carries (the reusable schema)."""

    goal_id: str
    lane: str
    packet_id: str = ""
    role: str = "knowledge-engineer"   # canonical role id → authored note colour/visibility
    source: str = ""                   # e.g. goal-progression / discovery-intake
    status: str = ""                   # the producing goal/packet status at write time
    created_at: str = ""               # caller-supplied ISO (no fake clock here)
    evidence_path: str = ""            # vault-relative path of THIS note (self-describing)

    def to_dict(self) -> dict:
        return {
            "goal_id": self.goal_id, "lane": self.lane, "packet_id": self.packet_id,
            "role": self.role, "source": self.source, "status": self.status,
            "created_at": self.created_at, "evidence_path": self.evidence_path,
        }

    def frontmatter(self) -> dict:
        """The minimal-metadata block written into the note frontmatter (ordered)."""

        return {
            "goal_id": self.goal_id, "lane": self.lane, "packet_id": self.packet_id,
            "role": self.role, "source": self.source, "status": self.status,
            "evidence_path": self.evidence_path,
        }

    def tags(self) -> Tuple[str, ...]:
        return tuple(t for t in ("forgekit", "evidence", self.lane, self.status) if t)


def build_evidence_note(meta: EvidenceMeta, *, title: str, summary: str,
                        sections: Sequence[Tuple[str, str]] = ()) -> str:
    """Author a structured evidence note carrying *meta* as frontmatter (no raw dump).

    Authored by ``meta.role`` so colour/visibility follow the identity registry. The body
    is a핵심 요약 + a linkage block + any extra *sections* — structured, never a dump."""

    link_lines = [
        f"- goal_id: {meta.goal_id or '-'}",
        f"- lane: {meta.lane}",
        f"- packet_id: {meta.packet_id or '-'}",
        f"- role: {meta.role}",
        f"- source: {meta.source or '-'}",
        f"- status: {meta.status or '-'}",
        f"- created_at: {meta.created_at or '-'}",
        f"- evidence_path: {meta.evidence_path or '-'}",
    ]
    body_parts = ["## 핵심 요약", f"- {summary}", "", "## linkage (evidence schema)", *link_lines]
    for heading, text in sections:
        body_parts += ["", f"## {heading}", text]
    return build_authored_note(
        meta.role, title=title, body="\n".join(body_parts), kind="evidence",
        status="draft", created_at=meta.created_at, phase=meta.lane,
        source_flow=meta.source, tags=meta.tags(),
        related=tuple(x for x in (meta.goal_id, meta.packet_id) if x),
        extra=meta.frontmatter())


def write_evidence_note(meta: EvidenceMeta, vault_root, *, title: str, summary: str,
                        slug: str, sections: Sequence[Tuple[str, str]] = ()
                        ) -> Optional[Tuple[Path, EvidenceMeta]]:
    """Write an evidence note under the connected vault. ``None`` when not writable.

    Computes the vault-relative subpath FIRST, stamps it into ``meta.evidence_path`` (so the
    schema is self-describing), then writes. Returns ``(path, meta_with_path)`` or ``None``
    (no vault / write fail) — never a fake write."""

    if not vault_root:
        return None
    from dataclasses import replace

    subpath = f"{evidence_subdir(meta.lane, meta.goal_id)}/{slug}.md"
    meta = replace(meta, evidence_path=subpath)
    content = build_evidence_note(meta, title=title, summary=summary, sections=sections)
    path = write_note(content, vault_root, subpath)
    return (path, meta) if path else None


# --- future discovery automation seam ----------------------------------------
# The 24h external-collection loop attaches HERE: it builds a discovery-lane EvidenceMeta
# and calls write_evidence_note with the SAME schema/location rules as goal evidence, so
# collected intake accumulates on the one evidence axis (queryable by lane/role). This is
# the named code seam referenced in docs/nexus-evidence-axis.md — not yet driven by an
# automated collector (that is the autonomy lane), but live and tested so it is ready to wire.
def discovery_intake_meta(
    source_id: str,
    *,
    role: str = "user-researcher",
    status: str = "intake",
    created_at: str = "",
    packet_id: str = "",
    goal_id: str = "",
) -> EvidenceMeta:
    """An EvidenceMeta for a discovery-intake artifact (``lane=discovery``).

    ``goal_id`` is the owning lane bucket — defaults to the source id so intake co-locates
    under ``00-inbox/evidence/discovery/<source_id>/`` even when not tied to a goal yet."""

    return EvidenceMeta(
        goal_id=goal_id or source_id, lane=LANE_DISCOVERY, packet_id=packet_id,
        role=role, source="discovery-intake", status=status, created_at=created_at)


__all__ = (
    "LANE_GOAL", "LANE_DISCOVERY", "LANE_SELF_IMPROVE",
    "EvidenceMeta", "evidence_subdir", "build_evidence_note", "write_evidence_note",
    "discovery_intake_meta",
)
