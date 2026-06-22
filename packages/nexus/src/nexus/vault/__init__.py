"""Vault authorship (WT5) — Obsidian-safe note metadata: who wrote it + handoff phase."""

from __future__ import annotations

from .authorship import (
    AGENT_IDENTITIES,
    AgentIdentity,
    NoteFrontmatter,
    identity_for,
    vault_css_snippet,
)
from .note import build_authored_note, note_from_handoff, write_note
from .evidence import (
    AccumulationResult,
    accumulate_goal_evidence,
    accumulate_records,
    write_evidence_note,
)

__all__ = (
    "AGENT_IDENTITIES",
    "AgentIdentity",
    "NoteFrontmatter",
    "identity_for",
    "vault_css_snippet",
    "build_authored_note",
    "note_from_handoff",
    "write_note",
    "AccumulationResult",
    "accumulate_goal_evidence",
    "accumulate_records",
    "write_evidence_note",
)
