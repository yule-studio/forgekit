"""Note frontmatter schema — what every agent stamps on a vault note.

One shared vault; per-agent identity comes from this **metadata** (not the
color). Retrieval reads ``agent / role / kind / status / topic / project /
retrieval_weight`` — ``color_token`` is a human visual aid only. The schema is
fixed here so a governance test can assert any note carries it.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .agent_contract_registry import AgentContract

# Required frontmatter keys (order is the rendered order).
SCHEMA_KEYS = (
    "title", "department", "agent", "role", "kind", "status", "project", "topic",
    "tags", "created_at", "related", "write_owner", "obsidian_lane",
    "color_token", "retrieval_weight",
)

# kind → default retrieval weight (memory-policy §4: canonical+2 / reusable+1 /
# decision+1 / retrospective+0.5 / status+0.5 / else 0).
_RETRIEVAL_WEIGHT = {
    "canonical": 2.0, "reusable": 1.0, "decision": 1.0,
    "retrospective": 0.5, "status": 0.5,
}


def default_retrieval_weight(kind: str) -> float:
    return _RETRIEVAL_WEIGHT.get(kind, 0.0)


def build_frontmatter(
    contract: AgentContract,
    *,
    title: str,
    kind: str,
    status: str = "draft",
    project: str = "",
    topic: str = "",
    tags: Sequence[str] = (),
    created_at: str = "1970-01-01T00:00:00Z",
    related: Sequence[str] = (),
    retrieval_weight: float = None,  # type: ignore[assignment]
) -> dict:
    """Build the frontmatter mapping for a note authored by *contract*'s role."""

    weight = default_retrieval_weight(kind) if retrieval_weight is None else retrieval_weight
    return {
        "title": title,
        "department": contract.department_id,
        "agent": contract.agent_id,
        "role": contract.role_id,
        "kind": kind,
        "status": status,
        "project": project,
        "topic": topic,
        "tags": list(tags),
        "created_at": created_at,
        "related": list(related),
        "write_owner": contract.role_id,
        "obsidian_lane": contract.obsidian_write_target,
        "color_token": contract.color_token,
        "retrieval_weight": weight,
    }


def render_frontmatter(data: Mapping[str, Any]) -> str:
    """Render a frontmatter mapping to a ``---`` fenced YAML-ish block."""

    lines = ["---"]
    for key in SCHEMA_KEYS:
        value = data.get(key)
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(str(v) for v in value)}]")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def validate_frontmatter(data: Mapping[str, Any]) -> tuple:
    """Return a tuple of missing/empty required keys (empty tuple = valid)."""

    missing = []
    for key in SCHEMA_KEYS:
        if key not in data:
            missing.append(key)
        elif key in ("agent", "role", "department", "obsidian_lane", "color_token") and not data[key]:
            missing.append(key)
    return tuple(missing)


__all__ = (
    "SCHEMA_KEYS", "default_retrieval_weight",
    "build_frontmatter", "render_frontmatter", "validate_frontmatter",
)
