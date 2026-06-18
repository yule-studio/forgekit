"""Vault authorship — WHO wrote a note + the handoff phase, as Obsidian-safe metadata.

The goal: looking at a vault note you can tell which agent wrote it and at which
handoff phase, and the notes are visually distinguishable. This is done with
metadata Obsidian actually supports — NOT a fake "colour the text" feature:

  * ``cssclasses`` frontmatter — Obsidian applies the class to the note container;
    the user's snippet (:func:`vault_css_snippet`) styles ``.fk-pm`` etc. with the
    agent's colour. Real, standard Obsidian.
  * a typed ``> [!fk-pm]`` callout marker at the top — themable, and readable even
    without the snippet.
  * authorship frontmatter: ``agent_author / agent_role / handoff_from / handoff_to
    / phase / source_flow`` on top of the repo's standard note frontmatter.

We never claim Obsidian colours arbitrary text; the colour comes from the user
adding the provided CSS snippet, and the metadata is useful regardless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Tuple

from ..identity import registry as _idreg


@dataclass(frozen=True)
class AgentIdentity:
    """An agent's stable vault identity — author id, role label, css class, colour.

    This is a thin view over the canonical identity registry (the SSoT). ``agent_id``
    here is always the CANONICAL id, so an abbreviation (``fe``) and the formal id
    (``frontend-engineer``) both yield the same vault identity."""

    agent_id: str
    role_label: str
    cssclass: str       # Obsidian cssclasses value (user snippet targets this)
    color: str          # hex token used by the generated CSS snippet
    callout: str        # callout type for the `> [!type]` marker


def _from_registry(ident) -> AgentIdentity:
    return AgentIdentity(ident.canonical_id, ident.role_label, ident.vault_cssclass,
                         ident.vault_color, ident.vault_callout)


# Derived from the canonical registry (NOT a second hardcoded SSoT) — keyed by
# canonical id. The abbreviation→canonical mapping lives only in identity.registry.
AGENT_IDENTITIES: Mapping[str, AgentIdentity] = {
    ident.canonical_id: _from_registry(ident) for ident in _idreg.all_identities()
}


def identity_for(agent_id: str) -> AgentIdentity:
    """Resolve any id/alias to its canonical vault identity (registry-backed)."""

    return _from_registry(_idreg.resolve_identity(agent_id))


def _yaml_scalar(value: str) -> str:
    s = str(value or "")
    # quote when it could be misread as YAML (colon / leading special)
    if s == "" or any(c in s for c in ":#") or s[:1] in "!&*[]{}>|@`\"'":
        return '"' + s.replace('"', '\\"') + '"'
    return s


@dataclass(frozen=True)
class NoteFrontmatter:
    """Standard repo note frontmatter + the authorship/handoff extension."""

    title: str
    kind: str = "note"
    status: str = "draft"
    created_at: str = ""           # caller-supplied ISO date (no fake clock here)
    tags: Tuple[str, ...] = ()
    related: Tuple[str, ...] = ()
    home_hub: str = ""
    # authorship / handoff extension
    agent_author: str = ""
    agent_role: str = ""
    handoff_from: str = ""
    handoff_to: str = ""
    phase: str = ""
    source_flow: str = ""
    cssclasses: Tuple[str, ...] = ()
    agent_color: str = ""

    def to_yaml(self) -> str:
        lines = ["---"]
        lines.append(f"title: {_yaml_scalar(self.title)}")
        lines.append(f"kind: {_yaml_scalar(self.kind)}")
        lines.append(f"status: {_yaml_scalar(self.status)}")
        if self.created_at:
            lines.append(f"created_at: {_yaml_scalar(self.created_at)}")
        lines.append("tags: [" + ", ".join(_yaml_scalar(t) for t in self.tags) + "]")
        lines.append("related: [" + ", ".join(_yaml_scalar(r) for r in self.related) + "]")
        if self.home_hub:
            lines.append(f"home_hub: {_yaml_scalar(self.home_hub)}")
        # authorship block
        lines.append(f"agent_author: {_yaml_scalar(self.agent_author)}")
        lines.append(f"agent_role: {_yaml_scalar(self.agent_role)}")
        if self.handoff_from:
            lines.append(f"handoff_from: {_yaml_scalar(self.handoff_from)}")
        if self.handoff_to:
            lines.append(f"handoff_to: {_yaml_scalar(self.handoff_to)}")
        if self.phase:
            lines.append(f"phase: {_yaml_scalar(self.phase)}")
        if self.source_flow:
            lines.append(f"source_flow: {_yaml_scalar(self.source_flow)}")
        lines.append("cssclasses: [" + ", ".join(_yaml_scalar(c) for c in self.cssclasses) + "]")
        if self.agent_color:
            lines.append(f"agent_color: {_yaml_scalar(self.agent_color)}")
        lines.append("---")
        return "\n".join(lines)


def vault_css_snippet() -> str:
    """The CSS snippet (for the user's vault ``.obsidian/snippets/``) that colours
    each agent's notes via their ``cssclasses``. This is the REAL color mechanism."""

    out = ["/* forgekit agent authorship colours — drop into .obsidian/snippets/ */"]
    for ident in AGENT_IDENTITIES.values():
        out.append(
            f".{ident.cssclass} {{ --fk-color: {ident.color}; "
            f"border-left: 3px solid {ident.color}; padding-left: 8px; }}"
        )
        out.append(
            f".callout[data-callout=\"{ident.callout}\"] {{ "
            f"--callout-color: {ident.color}; }}"
        )
    return "\n".join(out) + "\n"


__all__ = (
    "AgentIdentity", "AGENT_IDENTITIES", "identity_for",
    "NoteFrontmatter", "vault_css_snippet",
)
