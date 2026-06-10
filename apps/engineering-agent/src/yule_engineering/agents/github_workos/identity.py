"""Agent identities for the GitHub workspace.

Each engineering role has a static :class:`AgentIdentity` here. The
identity carries:

  * the GitHub-side display name (the bot user-facing label),
  * which **GitHub App actor** publishes the action — for this repo
    it's always :data:`GITHUB_APP_ACTOR` (a single shared App acts on
    behalf of every role; per-role distinction is done with the
    role label on issues / PRs, not separate App users),
  * the **commit author policy** — :data:`COMMIT_AUTHOR_POLICY_OWNER_AS_AUTHOR`
    means commits are authored as the *owner* (the human running the
    repo), not as the bot. The bot is only the committer. This keeps
    the audit trail clean: "the human owns the change; the bot
    routed it."
  * ``responsibilities`` — short bullet list, sourced from the live
    :class:`agents.role_profiles.RoleProfile`.
  * ``coding_surface`` — files/domains this role is expected to write.
  * ``review_surface`` — files/domains this role reviews even when
    they don't write them.
  * ``forbidden_actions`` / ``must_review`` / ``escalation_rules`` —
    pulled straight off the underlying profile so we don't fork the
    contract.

Built once at import time from the canonical role profile registry
so adding a domain keyword stays a one-profile edit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Tuple

from ..role_profiles import RoleProfile, all_role_profiles


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


#: The single GitHub App actor that publishes every engineering-agent
#: write to GitHub. The role distinction is encoded in issue / PR
#: labels and audit metadata, NOT in separate bot users.
GITHUB_APP_ACTOR: str = "yule-studio-engineering-agent[bot]"


#: Policy id for ``commit_author_owner``. Commits are authored as the
#: human repo-owner; the bot signs as committer only. Kept as a string
#: constant so audit rows / status surfaces can quote the same id the
#: code uses.
COMMIT_AUTHOR_POLICY_OWNER_AS_AUTHOR: str = "owner-as-author"


# ---------------------------------------------------------------------------
# Identity dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentIdentity:
    role_id: str
    github_display_name: str
    github_app_actor: str
    commit_author_policy: str
    mission: str
    responsibilities: Tuple[str, ...]
    coding_surface: Tuple[str, ...]
    review_surface: Tuple[str, ...]
    forbidden_actions: Tuple[str, ...]
    must_review: Tuple[str, ...]
    escalation_rules: Tuple[str, ...]


# ---------------------------------------------------------------------------
# Per-role coding/review surfaces
# ---------------------------------------------------------------------------
#
# Surfaces are intentionally short, lower-cased, slash-delimited globs
# / domain words so triage and audit rows can reuse them as labels.
# They are NOT regexes — the executor (G3) will translate them into
# real path globs when it builds work-area constraints.


_CODING_SURFACE: Mapping[str, Tuple[str, ...]] = {
    "tech-lead": (
        "docs/operations.md",
        "docs/m13-readiness.md",
        "policies/runtime/agents/engineering-agent/*.md",
        "apps/engineering-agent/src/yule_engineering/agents/lifecycle/senior_agent.py",
    ),
    "backend-engineer": (
        "src/main/java/**/*.java",
        "src/main/resources/**",
        "build.gradle*",
        "pom.xml",
        "apps/engineering-agent/src/yule_engineering/**/server*.py",
    ),
    "frontend-engineer": (
        "app/**",
        "components/**",
        "pages/**",
        "src/**/*.tsx",
        "src/**/*.ts",
        "next.config.*",
        "tailwind.config.*",
    ),
    "devops-engineer": (
        ".github/workflows/**",
        "deploy/**",
        "Dockerfile*",
        "docker-compose*.yml",
        "infra/**",
        "scripts/deploy/**",
    ),
    "qa-engineer": (
        "tests/**",
        "src/test/**/*.java",
        "**/*.spec.ts",
        "**/*.test.ts",
        "policies/runtime/agents/engineering-agent/live-regression.md",
    ),
    "ai-engineer": (
        "apps/engineering-agent/src/yule_engineering/agents/runners/**",
        "apps/engineering-agent/src/yule_engineering/agents/lifecycle/self_improvement.py",
        "apps/engineering-agent/src/yule_engineering/agents/research/**",
        "apps/engineering-agent/src/yule_engineering/memory/**",
    ),
    "product-designer": (
        "docs/design/**",
        "design/**",
        "components/**",
        "app/**/page.tsx",
    ),
}


_REVIEW_SURFACE: Mapping[str, Tuple[str, ...]] = {
    "tech-lead": (
        "**/*",  # tech-lead reviews everything, sign-off owner.
    ),
    "backend-engineer": (
        "src/main/java/**",
        "src/main/resources/**",
        "build.gradle*",
        "pom.xml",
        ".github/workflows/**",  # CI for backend builds.
    ),
    "frontend-engineer": (
        "app/**",
        "components/**",
        "pages/**",
        "src/**/*.tsx",
        "src/**/*.ts",
        "design/**",
    ),
    "devops-engineer": (
        ".github/workflows/**",
        "deploy/**",
        "Dockerfile*",
        "docker-compose*.yml",
        "infra/**",
        "src/main/resources/application*.yml",
    ),
    "qa-engineer": (
        "tests/**",
        "src/test/**",
        "**/*.spec.ts",
        "**/*.test.ts",
        ".github/workflows/**",  # ensures CI runs the new tests.
    ),
    "ai-engineer": (
        "apps/engineering-agent/src/yule_engineering/agents/**",
        "apps/engineering-agent/src/yule_engineering/memory/**",
        "policies/runtime/agents/**",
    ),
    "product-designer": (
        "design/**",
        "components/**",
        "app/**/page.tsx",
        "docs/design/**",
    ),
}


_GITHUB_DISPLAY_NAME: Mapping[str, str] = {
    "tech-lead": "Tech Lead (yule-studio-engineering-agent)",
    "backend-engineer": "Backend Engineer (yule-studio-engineering-agent)",
    "frontend-engineer": "Frontend Engineer (yule-studio-engineering-agent)",
    "devops-engineer": "DevOps Engineer (yule-studio-engineering-agent)",
    "qa-engineer": "QA Engineer (yule-studio-engineering-agent)",
    "ai-engineer": "AI Engineer (yule-studio-engineering-agent)",
    "product-designer": "Product Designer (yule-studio-engineering-agent)",
}


SUPPORTED_ROLE_IDS: Tuple[str, ...] = (
    "tech-lead",
    "backend-engineer",
    "frontend-engineer",
    "devops-engineer",
    "qa-engineer",
    "ai-engineer",
    "product-designer",
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _build_identity(role_id: str, profile: RoleProfile) -> AgentIdentity:
    return AgentIdentity(
        role_id=role_id,
        github_display_name=_GITHUB_DISPLAY_NAME.get(
            role_id, profile.display_name
        ),
        github_app_actor=GITHUB_APP_ACTOR,
        commit_author_policy=COMMIT_AUTHOR_POLICY_OWNER_AS_AUTHOR,
        mission=profile.mission,
        responsibilities=tuple(profile.responsibilities),
        coding_surface=_CODING_SURFACE.get(role_id, ()),
        review_surface=_REVIEW_SURFACE.get(role_id, ()),
        forbidden_actions=tuple(profile.forbidden_actions),
        must_review=tuple(profile.must_review),
        escalation_rules=tuple(profile.escalation_rules),
    )


_IDENTITY_CACHE: dict[str, AgentIdentity] = {}


def _ensure_cache() -> Mapping[str, AgentIdentity]:
    if _IDENTITY_CACHE:
        return _IDENTITY_CACHE
    profiles = all_role_profiles()
    for role_id in SUPPORTED_ROLE_IDS:
        profile = profiles.get(role_id)
        if profile is None:
            # Defensive — registry shape changed under us. Build a
            # minimal identity so callers don't crash, but flag it.
            _IDENTITY_CACHE[role_id] = AgentIdentity(
                role_id=role_id,
                github_display_name=_GITHUB_DISPLAY_NAME.get(role_id, role_id),
                github_app_actor=GITHUB_APP_ACTOR,
                commit_author_policy=COMMIT_AUTHOR_POLICY_OWNER_AS_AUTHOR,
                mission="",
                responsibilities=(),
                coding_surface=_CODING_SURFACE.get(role_id, ()),
                review_surface=_REVIEW_SURFACE.get(role_id, ()),
                forbidden_actions=(),
                must_review=(),
                escalation_rules=(),
            )
            continue
        _IDENTITY_CACHE[role_id] = _build_identity(role_id, profile)
    return _IDENTITY_CACHE


def agent_identity(role_id: str) -> AgentIdentity:
    """Return the :class:`AgentIdentity` for *role_id*.

    Raises :class:`KeyError` for unknown roles — the engineering-agent
    only ships identities for the seven canonical roles. Mistyping a
    role id should be loud, not silently fall through to a dummy.
    """

    cache = _ensure_cache()
    if role_id not in cache:
        raise KeyError(f"unknown engineering role id: {role_id!r}")
    return cache[role_id]


def all_agent_identities() -> Mapping[str, AgentIdentity]:
    """Return the full cached map of role_id → :class:`AgentIdentity`."""

    return dict(_ensure_cache())


__all__ = [
    "AgentIdentity",
    "COMMIT_AUTHOR_POLICY_OWNER_AS_AUTHOR",
    "GITHUB_APP_ACTOR",
    "SUPPORTED_ROLE_IDS",
    "agent_identity",
    "all_agent_identities",
]
