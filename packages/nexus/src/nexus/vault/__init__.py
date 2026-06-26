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
    LANE_DISCOVERY,
    LANE_GOAL,
    LANE_SELF_IMPROVE,
    EvidenceMeta,
    build_evidence_note,
    discovery_intake_meta,
    evidence_subdir,
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
    "LANE_GOAL",
    "LANE_DISCOVERY",
    "LANE_SELF_IMPROVE",
    "EvidenceMeta",
    "evidence_subdir",
    "build_evidence_note",
    "write_evidence_note",
    "discovery_intake_meta",
)
