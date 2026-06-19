"""Canonical agent identity model + derivation rules. Pure / stdlib-only.

One :class:`AgentIdentity` per canonical agent. Some fields are *explicit* (vault
css/colour — not derivable from the id) and some are *derived* by fixed rule
(GitHub App env prefix, git author) so the SSoT stays small and consistent:

  * ``github_app_env_prefix`` = ``YULE_GITHUB_APP_<CANONICAL_UPPER_SNAKE>_``
  * ``git_author_name``       = ``Forgekit <role_label>``
  * ``git_author_email``      = ``<email_local>@forgekit.local``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


def github_app_env_prefix(canonical_id: str, *, shared: bool = False) -> str:
    if shared:
        return "YULE_GITHUB_APP_SHARED_"
    snake = (canonical_id or "").strip().replace("-", "_").upper()
    return f"YULE_GITHUB_APP_{snake}_"


def git_author_name(role_label: str) -> str:
    return f"Forgekit {role_label}".strip()


def git_author_email(email_local: str) -> str:
    return f"{(email_local or 'agent').strip()}@forgekit.local"


@dataclass(frozen=True)
class AgentIdentity:
    """A canonical agent identity — the SSoT row every surface reads."""

    canonical_id: str
    role_label: str
    department: str
    vault_cssclass: str          # explicit (e.g. fk-fe — not fk-frontend-engineer)
    vault_color: str             # explicit hex token
    identity_aliases: Tuple[str, ...] = ()   # abbreviations / manifest variants
    email_local: str = ""        # left side of the git author email (defaults to css stem)
    supports_github_app: bool = True
    supports_vault_authorship: bool = True
    manifest_path: str = ""      # set when cross-checked against an on-disk manifest

    # --- derived projections ------------------------------------------------
    @property
    def vault_callout(self) -> str:
        return self.vault_cssclass  # callout type mirrors the css class

    @property
    def github_app_env_prefix(self) -> str:
        return github_app_env_prefix(self.canonical_id)

    @property
    def git_author_name(self) -> str:
        return git_author_name(self.role_label)

    @property
    def git_author_email(self) -> str:
        stem = self.email_local or self.vault_cssclass.replace("fk-", "") or self.canonical_id
        return git_author_email(stem)

    def to_dict(self) -> dict:
        return {
            "canonical_id": self.canonical_id, "role_label": self.role_label,
            "department": self.department,
            "github_app_env_prefix": self.github_app_env_prefix,
            "git_author_name": self.git_author_name,
            "git_author_email": self.git_author_email,
            "vault_cssclass": self.vault_cssclass, "vault_callout": self.vault_callout,
            "vault_color": self.vault_color, "identity_aliases": list(self.identity_aliases),
            "supports_github_app": self.supports_github_app,
            "supports_vault_authorship": self.supports_vault_authorship,
            "manifest_path": self.manifest_path,
        }


__all__ = ("AgentIdentity", "github_app_env_prefix", "git_author_name", "git_author_email")
