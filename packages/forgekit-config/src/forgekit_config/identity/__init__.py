"""Canonical agent identity — the single SSoT for who an agent is across forgekit.

Resolves manifest ids / vault-authorship abbreviations to ONE canonical identity
(role label, department, git author, GitHub App env prefix, Obsidian css/callout/
colour). Every surface (vault authorship, git attribution, doctor) reads from here so
the abbreviated (`fe`) vs formal (`frontend-engineer`) drift is fixed in one place.
"""

from .models import AgentIdentity
from .registry import (
    all_identities,
    git_identity_for,
    github_app_identity_for,
    resolve_identity,
    vault_identity_for,
)

__all__ = (
    "AgentIdentity", "all_identities", "resolve_identity",
    "vault_identity_for", "git_identity_for", "github_app_identity_for",
)
