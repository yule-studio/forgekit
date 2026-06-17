"""Restricted design vault note (design WT5) — index/packet only, never raw asset.

The vault stores a RESTRICTED note (source id + external path reference + allowed
roles + packet links + sensitivity metadata) — NEVER the raw ``.fig`` / export. The
frontmatter reuses the authorship strategy (agent_author/role/cssclasses) plus
``visibility: restricted`` + ``publish: false`` + ``design_source_id``. "raw = private
source, vault = packet/index." Pure string building → testable.
"""

from __future__ import annotations

from typing import Optional, Sequence

from ..vault.authorship import identity_for


def _yaml_list(items: Sequence[str]) -> str:
    return "[" + ", ".join(str(i) for i in items) + "]"


def build_restricted_design_note(
    *,
    design_source_id: str,
    source_path: str,
    access_state: str,
    allowed_roles: Sequence[str],
    author_role: str = "design-lead",
    packet_links: Sequence[str] = (),
    created_at: str = "",
    handoff_to: str = "tech-lead",
) -> str:
    """A restricted vault note — metadata + packet links only, NO raw asset content."""

    ident = identity_for(author_role)
    fm = ["---",
          f"title: design reference — {design_source_id}",
          "kind: design-reference",
          "status: restricted",
          f"created_at: {created_at}" if created_at else "created_at:",
          "tags: [design, restricted]",
          f"agent_author: {ident.agent_id}",
          f"agent_role: {ident.role_label}",
          "source_flow: design-reference",
          f"design_source_id: {design_source_id}",
          "visibility: restricted",
          "publish: false",
          f"cssclasses: [{ident.cssclass}]",
          f"agent_color: \"{ident.color}\"",
          f"handoff_to: {handoff_to}",
          f"allowed_roles: {_yaml_list(allowed_roles)}",
          "---"]
    body = [
        f"> [!{ident.callout}] {ident.role_label} · design reference (restricted)",
        "",
        "## 핵심 (raw 자산 아님 — index/packet 만)",
        f"- design_source_id: {design_source_id}",
        f"- raw source (external, 비저장): `{source_path}`",
        f"- access_state: {access_state}",
        f"- allowed_roles: {', '.join(allowed_roles)}",
        "",
        "## packet links",
    ]
    body += [f"- {link}" for link in packet_links] or ["- (아직 packet 없음)"]
    body += [
        "",
        "## 주의",
        "- raw `.fig`/export 는 vault 본문에 싣지 않음 (private source).",
        "- non-design role 은 이 note 가 아니라 projection/packet 으로 작업.",
    ]
    return "\n".join(fm) + "\n\n" + "\n".join(body) + "\n"


__all__ = ("build_restricted_design_note",)
