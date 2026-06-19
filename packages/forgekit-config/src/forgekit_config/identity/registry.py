"""Canonical agent identity registry — the single SSoT (pure / stdlib-only).

Canonical id = the FORMAL agent id (``frontend-engineer``), never the abbreviation.
Abbreviations (``fe``) and manifest variants are accepted as *aliases* and normalise
to the canonical identity here — so the abbreviation→formal mapping lives in exactly
ONE place (this module), not scattered across vault/authorship, manifests and docs.

Seed rows carry the *explicit* fields (role label, department, vault css/colour,
aliases); GitHub App prefix + git author are *derived* by rule in :mod:`models`.
``scan_manifests`` cross-checks the on-disk manifests and reports drift rather than
silently diverging.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Tuple

from .models import AgentIdentity

# (canonical_id, role_label, department, cssclass, color, email_local, aliases)
_SEED: Tuple[tuple, ...] = (
    ("product-manager", "Product (PM)", "product", "fk-pm", "#f23ccf", "pm", ("product-agent", "pm")),
    ("gateway", "Engineering Gateway", "engineering", "fk-gateway", "#8b90a0", "gateway", ()),
    ("tech-lead", "Tech Lead", "engineering", "fk-techlead", "#00d8f0", "techlead", ()),
    ("frontend-engineer", "Frontend", "engineering", "fk-fe", "#3ddc97", "fe", ("fe",)),
    ("backend-engineer", "Backend", "engineering", "fk-be", "#e0b020", "be", ("be",)),
    ("devops-engineer", "DevOps", "engineering", "fk-devops", "#ff5c7a", "devops", ("devops",)),
    ("qa-engineer", "QA", "engineering", "fk-qa", "#9b8cf0", "qa", ("qa",)),
    ("security-engineer", "Security", "engineering", "fk-security", "#ff8c42", "security", ("security",)),
    ("ai-engineer", "AI Engineer", "engineering", "fk-ai", "#38bdf8", "ai", ()),
    ("platform-runtime-engineer", "Platform Runtime", "engineering", "fk-platform", "#14b8a6", "platform", ()),
    ("knowledge-engineer", "Knowledge Engineer", "engineering", "fk-knowledge", "#f59e0b", "knowledge", ()),
    ("ops-observer", "Ops Observer", "ops", "fk-ops", "#2f6f7a", "ops", ()),
    ("ux-ui-designer", "UX/UI Designer", "design", "fk-ux", "#7dd3fc", "uxui", ("ux",)),
    ("design-systems-designer", "Design Systems", "design", "fk-dsys", "#a78bfa", "dsys", ()),
    ("illustration-brand-designer", "Illustration/Brand", "design", "fk-illus", "#fb7185", "brandart", ()),
    ("design-lead", "Design Lead", "design", "fk-design-lead", "#22d3ee", "designlead", ()),
    ("user-researcher", "User Researcher", "product", "fk-user-research", "#c084fc", "user-research", ()),
    ("growth-marketer", "Growth", "marketing", "fk-growth", "#34d399", "growth", ()),
    ("growth-analyst", "Growth Analyst", "marketing", "fk-growth-analyst", "#10b981", "growth-analyst", ()),
    ("content-strategist", "Content", "marketing", "fk-content", "#f59e0b", "content", ()),
    ("brand-manager", "Brand Manager", "marketing", "fk-brand", "#ec4899", "brand", ()),
    ("seo-specialist", "SEO", "marketing", "fk-seo", "#84cc16", "seo", ()),
    ("budget-analyst", "Budget Analyst", "finance", "fk-budget", "#fbbf24", "budget", ()),
    ("recruiter", "Recruiter", "people", "fk-recruiter", "#60a5fa", "recruiter", ()),
    ("people-ops", "People Ops", "people", "fk-peopleops", "#f472b6", "peopleops", ()),
    ("culture-coach", "Culture Coach", "people", "fk-culture", "#a78bfa", "culture", ()),
    ("contract-reviewer", "Contract Reviewer", "legal", "fk-contract", "#f97316", "contract", ()),
    ("privacy-officer", "Privacy Officer", "legal", "fk-privacy", "#fb7185", "privacy", ()),
    ("sales-rep", "Sales", "sales", "fk-sales", "#22c55e", "sales", ()),
    ("customer-success", "Customer Success", "sales", "fk-cs", "#06b6d4", "cs", ()),
)

_FALLBACK = AgentIdentity("forgekit", "forgekit", "core", "fk-agent", "#8b90a0", (), "agent")


def _build() -> Tuple[Dict[str, AgentIdentity], Dict[str, str]]:
    by_id: Dict[str, AgentIdentity] = {}
    alias_to_id: Dict[str, str] = {}
    for cid, label, dept, css, color, email, aliases in _SEED:
        ident = AgentIdentity(cid, label, dept, css, color, tuple(aliases), email)
        by_id[cid] = ident
        alias_to_id[cid] = cid
        for a in aliases:
            alias_to_id[a] = cid
    return by_id, alias_to_id


_BY_ID, _ALIAS = _build()


def all_identities() -> Tuple[AgentIdentity, ...]:
    return tuple(_BY_ID.values())


def canonical_id(agent_id: str) -> str:
    """Normalise any id/alias to its canonical id ("" if unknown)."""

    return _ALIAS.get((agent_id or "").strip(), "")


def resolve_identity(agent_id: str) -> AgentIdentity:
    """Resolve a formal id OR an alias to the canonical identity (fallback if unknown)."""

    cid = canonical_id(agent_id)
    return _BY_ID.get(cid, _FALLBACK)


def is_known(agent_id: str) -> bool:
    return bool(canonical_id(agent_id))


# --- projections (the seams vault / git / github surfaces will read) --------
def vault_identity_for(agent_id: str) -> dict:
    ident = resolve_identity(agent_id)
    return {"agent_author": ident.canonical_id, "agent_role": ident.role_label,
            "cssclass": ident.vault_cssclass, "callout": ident.vault_callout,
            "agent_color": ident.vault_color}


def git_identity_for(agent_id: str) -> dict:
    ident = resolve_identity(agent_id)
    return {"name": ident.git_author_name, "email": ident.git_author_email,
            "canonical_id": ident.canonical_id, "role": ident.role_label}


def github_app_identity_for(agent_id: str) -> dict:
    ident = resolve_identity(agent_id)
    return {"env_prefix": ident.github_app_env_prefix, "shared_prefix": "YULE_GITHUB_APP_SHARED_",
            "supports_github_app": ident.supports_github_app, "canonical_id": ident.canonical_id}


def to_dict() -> dict:
    return {"identities": [i.to_dict() for i in all_identities()],
            "aliases": dict(_ALIAS)}


# --- manifest cross-check (drift detection, not a second SSoT) ---------------
def scan_manifests(agents_root: Path) -> Dict[str, list]:
    """Cross-check on-disk manifests against the registry. Returns structured drift.

    ``unknown`` = manifest id not in the registry (alias or canonical); ``prefix_drift``
    = manifest github_app_env_prefix differs from the derived one. Never raises."""

    out = {"matched": [], "unknown": [], "prefix_drift": []}
    try:
        manifests = sorted(Path(agents_root).rglob("manifest.json"))
    except OSError:
        return out
    for mf in manifests:
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        mid = str(data.get("id", "") or data.get("role", "")).strip()
        cid = canonical_id(mid) or canonical_id(str(data.get("role", "")).strip())
        if not cid:
            out["unknown"].append(mid)
            continue
        out["matched"].append(cid)
        declared = str(data.get("github_app_env_prefix", "") or "").strip()
        if declared and declared != _BY_ID[cid].github_app_env_prefix:
            out["prefix_drift"].append((mid, declared, _BY_ID[cid].github_app_env_prefix))
    return out


__all__ = (
    "all_identities", "canonical_id", "resolve_identity", "is_known",
    "vault_identity_for", "git_identity_for", "github_app_identity_for",
    "to_dict", "scan_manifests",
)
