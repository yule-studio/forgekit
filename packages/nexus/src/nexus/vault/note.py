"""Authored vault notes — build a note whose metadata says who wrote it + the phase.

Composes :mod:`vault.authorship` into a full markdown note: standard + authorship
frontmatter, a typed callout marker (themable), then the body. Also turns a WT2
:class:`handoff.packet.Handoff` into an authored note so the vault records "who did
what at which handoff phase". Pure string building + a guarded writer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple

from . import authorship as A


def build_authored_note(
    agent_id: str,
    title: str,
    body: str,
    *,
    kind: str = "note",
    status: str = "draft",
    created_at: str = "",
    handoff_from: str = "",
    handoff_to: str = "",
    phase: str = "",
    source_flow: str = "",
    tags: Sequence[str] = (),
    related: Sequence[str] = (),
    extra: Optional[Mapping[str, str]] = None,
) -> str:
    """A full markdown note authored by *agent_id* (frontmatter + callout + body).

    *extra* adds typed scalar frontmatter keys (ordered) beyond the authorship block —
    used to carry the evidence schema (goal_id/lane/packet_id/status/evidence_path) so it
    is queryable metadata, not buried in prose."""

    ident = A.identity_for(agent_id)
    fm = A.NoteFrontmatter(
        title=title, kind=kind, status=status, created_at=created_at,
        tags=tuple(tags), related=tuple(related),
        agent_author=ident.agent_id, agent_role=ident.role_label,
        handoff_from=handoff_from, handoff_to=handoff_to, phase=phase,
        source_flow=source_flow, cssclasses=(ident.cssclass,), agent_color=ident.color,
        extra=tuple((str(k), str(v)) for k, v in (extra or {}).items()),
    )
    marker = f"> [!{ident.callout}] {ident.role_label}"
    if phase:
        marker += f" · phase: {phase}"
    if handoff_from or handoff_to:
        marker += f" · {handoff_from or '?'} → {handoff_to or '?'}"
    return f"{fm.to_yaml()}\n\n{marker}\n\n{body.rstrip()}\n"


def note_from_handoff(handoff, *, created_at: str = "") -> str:
    """An authored note (tech-lead, handoff phase) summarising a WT2 Handoff."""

    packet = handoff.packet
    goal = getattr(packet, "user_goal", "") or "(목표 미파악)"
    lines = [
        f"## 핵심 요약",
        f"- 요청: {handoff.raw_ask}",
        f"- goal: {goal}",
        f"- 역할 분배: {len(handoff.split.tasks)}개 · blocked: {len(handoff.split.blocked)}개",
        "",
        "## 역할 split",
    ]
    for t in handoff.split.tasks:
        flag = " **(BLOCKED — operator/runbook 필요)**" if t.state == "blocked" else ""
        lines.append(f"- [{t.role_label}] {t.title}{flag}")
    lines += ["", "## handoff trace (누가 무엇을 언제)"]
    for tr in handoff.trace:
        lines.append(f"- {tr.author_role} ({tr.phase}): {tr.handoff_from} → {tr.handoff_to} — {tr.note}")
    return build_authored_note(
        "tech-lead",
        title=f"handoff — {handoff.project or goal[:24]}",
        body="\n".join(lines),
        kind="handoff", status="proposed", created_at=created_at,
        handoff_from="gateway", handoff_to="engineers",
        phase="tech-lead", source_flow="pm-intake-handoff",
        tags=("forgekit", "handoff"), related=(),
    )


def write_note(content: str, vault_root, subpath: str) -> Optional[Path]:
    """Write *content* to ``<vault_root>/<subpath>``. None on failure (guarded)."""

    try:
        path = Path(vault_root) / subpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path
    except OSError:
        return None


__all__ = ("build_authored_note", "note_from_handoff", "write_note")
