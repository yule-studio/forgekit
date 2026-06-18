"""Git attribution + GitHub App status — registry-backed, honest. Pure / stdlib-only.

Turns a canonical agent identity into the things a commit path needs: a git author
(``Name <email>``) and structured commit trailers (``Forgekit-Agent`` …), plus an
HONEST GitHub App credential status read from the environment:

  * ``dedicated_configured`` — the agent's own ``<PREFIX>APP_ID`` / ``INSTALLATION_ID`` /
    ``PRIVATE_KEY_PEM`` are all present.
  * ``partial_credentials``  — some of those three are set, but not all (mis-config).
  * ``shared_fallback``      — the agent's own app is absent but ``YULE_GITHUB_APP_SHARED_*``
    is configured.
  * ``missing``              — no dedicated and no shared credentials.
  * ``planned``              — the agent doesn't support a GitHub App.

This module never *performs* a commit and never *creates* an app — it only builds the
author/trailers/status so a real commit path can use them and an operator surface can
show them. Creating/installing the GitHub App is an org-admin step (runbook), not here.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional, Tuple

from . import registry as reg

# github app status
APP_DEDICATED = "dedicated_configured"
APP_PARTIAL = "partial_credentials"
APP_SHARED = "shared_fallback"
APP_MISSING = "missing"
APP_PLANNED = "planned"

_REQUIRED_SUFFIXES = ("APP_ID", "INSTALLATION_ID", "PRIVATE_KEY_PEM")
_SHARED_PREFIX = "YULE_GITHUB_APP_SHARED_"


def git_author_for(agent_id: str) -> str:
    """``Forgekit <Role> <local@forgekit.local>`` for the canonical identity."""

    g = reg.git_identity_for(agent_id)
    return f"{g['name']} <{g['email']}>"


def _present(env: Mapping[str, str], prefix: str) -> Tuple[int, int]:
    have = sum(1 for s in _REQUIRED_SUFFIXES if str(env.get(prefix + s, "") or "").strip())
    return have, len(_REQUIRED_SUFFIXES)


def github_app_status(agent_id: str, env: Optional[Mapping[str, str]] = None) -> str:
    """Honest credential status for the agent's GitHub App (dedicated/shared/missing…)."""

    env = os.environ if env is None else env
    ident = reg.resolve_identity(agent_id)
    if not ident.supports_github_app:
        return APP_PLANNED
    have, need = _present(env, ident.github_app_env_prefix)
    if have == need:
        return APP_DEDICATED
    if have > 0:
        return APP_PARTIAL
    shared_have, shared_need = _present(env, _SHARED_PREFIX)
    if shared_have == shared_need:
        return APP_SHARED
    return APP_MISSING


def commit_trailers(agent_id: str, *, flow: str = "", mode: str = "",
                    handoff_from: str = "", handoff_to: str = "", approval: str = "",
                    env: Optional[Mapping[str, str]] = None) -> Tuple[str, ...]:
    """Structured commit trailers for an agent-attributed commit (registry-backed).

    Only emits trailers for values that exist — never fabricates a handoff/mode that
    didn't happen. The GitHub-App trailer reflects the REAL credential status."""

    ident = reg.resolve_identity(agent_id)
    app = github_app_status(agent_id, env)
    lines = [
        f"Forgekit-Agent: {ident.canonical_id}",
        f"Forgekit-Role: {ident.role_label}",
        f"Forgekit-Dept: {ident.department}",
        f"Forgekit-GitHub-App: {app}",
        f"Forgekit-Vault-Class: {ident.vault_cssclass}",
    ]
    if flow:
        lines.append(f"Forgekit-Flow: {flow}")
    if mode:
        lines.append(f"Forgekit-Mode: {mode}")
    if handoff_from:
        lines.append(f"Forgekit-Handoff-From: {handoff_from}")
    if handoff_to:
        lines.append(f"Forgekit-Handoff-To: {handoff_to}")
    if approval:
        lines.append(f"Forgekit-Approval: {approval}")
    return tuple(lines)


def attribution_preview(agent_id: str, env: Optional[Mapping[str, str]] = None) -> dict:
    """Everything an operator surface (`/whoami`) needs — git + vault + app, all honest."""

    ident = reg.resolve_identity(agent_id)
    return {
        "canonical_id": ident.canonical_id, "role": ident.role_label,
        "department": ident.department,
        "git_author": git_author_for(agent_id),
        "vault_cssclass": ident.vault_cssclass, "vault_color": ident.vault_color,
        "github_app_env_prefix": ident.github_app_env_prefix,
        "github_app_status": github_app_status(agent_id, env),
    }


def render_whoami_lines(agent_id: str, env: Optional[Mapping[str, str]] = None) -> Tuple[str, ...]:
    """Operator-facing `/whoami` lines (active agent identity, honest app status)."""

    p = attribution_preview(agent_id, env)
    app = p["github_app_status"]
    app_note = {
        APP_DEDICATED: "전용 app 자격 설정됨", APP_SHARED: "shared org app fallback 사용",
        APP_PARTIAL: "자격 일부만 설정 — 점검 필요", APP_MISSING: "자격 없음 — setup runbook 필요",
        APP_PLANNED: "GitHub App 미대상(planned)",
    }.get(app, app)
    return (
        f"agent: {p['canonical_id']} ({p['role']} · {p['department']})",
        f"  git author : {p['git_author']}",
        f"  vault      : cssclass={p['vault_cssclass']} color={p['vault_color']}",
        f"  github app : {app} — {app_note}",
        f"               env prefix {p['github_app_env_prefix']}<APP_ID|INSTALLATION_ID|PRIVATE_KEY_PEM>",
    )


def identity_audit_lines(env: Optional[Mapping[str, str]] = None) -> Tuple[str, ...]:
    """Operator audit (`/whoami`) — git author + GitHub App status for every agent,
    grouped honestly so missing/partial credentials are visible, not hidden."""

    rows = [(i.canonical_id, github_app_status(i.canonical_id, env)) for i in reg.all_identities()]
    counts: dict = {}
    for _, st in rows:
        counts[st] = counts.get(st, 0) + 1
    lines = ["agent identity — git author = registry, GitHub App status = env (정직):",
             "  app status: " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "-")]
    flagged = [cid for cid, st in rows if st in (APP_PARTIAL,)]
    if flagged:
        lines.append("  ⚠ partial credentials: " + ", ".join(flagged))
    lines.append(f"  dedicated app 자격 = env {reg.resolve_identity('tech-lead').github_app_env_prefix}"
                 "APP_ID|INSTALLATION_ID|PRIVATE_KEY_PEM (예시). 실제 App 생성/설치는 org-admin runbook.")
    lines.append("  `/whoami <agent>` 로 개별 git author / vault / app 자격 상세.")
    return tuple(lines)


__all__ = (
    "APP_DEDICATED", "APP_PARTIAL", "APP_SHARED", "APP_MISSING", "APP_PLANNED",
    "git_author_for", "github_app_status", "commit_trailers",
    "attribution_preview", "render_whoami_lines", "identity_audit_lines",
)
