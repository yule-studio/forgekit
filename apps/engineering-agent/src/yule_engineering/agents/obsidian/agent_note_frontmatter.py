"""Contract-stamped frontmatter for agent-authored vault notes.

One shared vault; per-agent identity lives in **metadata**, not the note
color. This module is the thin bridge between the agent invocation
contract registry (``governance.agent_contract_registry``) and the
Obsidian writer/exporter: given a role + note kind it produces the
contract frontmatter keys (``agent / role / department / obsidian_lane /
color_token / write_owner / retrieval_weight``) so every note an agent
writes carries its identity for metadata-driven retrieval.

It is **purely additive**: :func:`stamp_contract_frontmatter` merges the
contract keys into a caller's existing frontmatter dict without removing
or renaming any existing key. The exporter keeps owning the base note
shape (``title / kind / status / topic / tags / …``); this only layers
the agent identity on top.

Where it plugs in: the exporter in :mod:`export_render` builds a base
frontmatter dict (``_frontmatter`` / ``render_work_report_note``). A
caller that knows the authoring role can pass that dict through
:func:`stamp_contract_frontmatter` before :func:`write_note` so the note
lands with its contract identity. Retrieval (``yule_memory``) then keys
on those metadata fields; ``color_token`` rides along as a passive
human-visual aid only.
"""

from __future__ import annotations

from typing import Any, Mapping, MutableMapping, Sequence

from ..governance.agent_contract_registry import AgentContract, contract_for
from ..governance import note_frontmatter as nf

# The contract-identity keys this module layers onto a note. ``title`` /
# ``kind`` / ``status`` / ``project`` / ``topic`` / ``tags`` / ``created_at``
# / ``related`` are owned by the exporter, so we only project identity +
# retrieval-weight keys here (never clobbering caller values).
CONTRACT_KEYS: tuple[str, ...] = (
    "agent",
    "role",
    "department",
    "obsidian_lane",
    "color_token",
    "write_owner",
    "retrieval_weight",
)


def contract_frontmatter_keys(
    role: str,
    *,
    kind: str,
    retrieval_weight: float | None = None,
) -> dict[str, Any]:
    """Return just the contract-identity keys for *role* + note *kind*.

    Thin wrapper over :func:`note_frontmatter.build_frontmatter` that
    drops the exporter-owned keys, leaving only the agent identity +
    retrieval-weight projection (see :data:`CONTRACT_KEYS`).
    """

    contract = _resolve_contract(role)
    full = nf.build_frontmatter(
        contract,
        title="",
        kind=kind,
        retrieval_weight=retrieval_weight,  # type: ignore[arg-type]
    )
    return {key: full[key] for key in CONTRACT_KEYS}


def stamp_contract_frontmatter(
    base: Mapping[str, Any],
    role: str,
    *,
    kind: str | None = None,
    retrieval_weight: float | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Return *base* with the contract-identity keys merged in additively.

    ``kind`` defaults to ``base['kind']`` when omitted so the exporter's
    chosen note kind drives the retrieval weight. Existing keys in *base*
    are preserved unless ``overwrite`` is True — by default we never
    clobber a value the exporter already set, we only fill in the
    contract keys that are missing/empty.
    """

    merged: dict[str, Any] = dict(base)
    resolved_kind = kind if kind is not None else str(merged.get("kind") or "")
    identity = contract_frontmatter_keys(
        role, kind=resolved_kind, retrieval_weight=retrieval_weight
    )
    for key, value in identity.items():
        if overwrite or _is_blank(merged.get(key)):
            merged[key] = value
    return merged


def build_agent_note_frontmatter(
    role: str,
    *,
    title: str,
    kind: str,
    status: str = "draft",
    project: str = "",
    topic: str = "",
    tags: Sequence[str] = (),
    created_at: str = "1970-01-01T00:00:00Z",
    related: Sequence[str] = (),
    retrieval_weight: float | None = None,
) -> dict[str, Any]:
    """Build a full contract-stamped frontmatter for *role*'s note.

    Convenience entry point for callers that don't already have a base
    frontmatter dict — resolves the contract and delegates to
    :func:`note_frontmatter.build_frontmatter`.
    """

    contract = _resolve_contract(role)
    return nf.build_frontmatter(
        contract,
        title=title,
        kind=kind,
        status=status,
        project=project,
        topic=topic,
        tags=tags,
        created_at=created_at,
        related=related,
        retrieval_weight=retrieval_weight,  # type: ignore[arg-type]
    )


def _resolve_contract(role: str | AgentContract) -> AgentContract:
    if isinstance(role, AgentContract):
        return role
    return contract_for(role)


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


__all__ = (
    "CONTRACT_KEYS",
    "contract_frontmatter_keys",
    "stamp_contract_frontmatter",
    "build_agent_note_frontmatter",
)
