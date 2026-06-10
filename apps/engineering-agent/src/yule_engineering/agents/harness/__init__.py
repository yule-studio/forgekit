"""Harness bridge — registry SSoT projected onto Claude Code / Codex (issue #185).

This package holds the *bridge* between Yule's runtime-agnostic markdown
registry (``agents/<agent>/{skills,commands,hooks}/*.md`` + the grant
SSoT) and the harness-native artifacts the runners actually invoke
(``.claude/`` for Claude Code, ``.agents/`` for Codex, plugin bundles).

Design rule (revises ECC foundation Appendix A.3): the markdown registry
and :mod:`slash_command_grants` JSON stay the single source of truth.
Harness directories are *generated* by ``scripts/sync_harness_skills.py``
and must not be hand-edited.

Surfaces:

  * :mod:`slash_command_grants` — load + validate the per-department
    slash-command / skill grant table. Pure-Python, deterministic, no
    side effects on import.

Out of scope (follow-up PRs): runtime enforcement of grants inside the
RoleRunner dispatch; live ``/compact`` invocation wiring.
"""

from __future__ import annotations

from .slash_command_grants import (
    BuiltinCommand,
    CommandGrant,
    CustomSkill,
    DepartmentGrants,
    EffectiveGrants,
    GrantTable,
    GrantValidationError,
    RoleOverride,
    SkillGrant,
    default_grants_path,
    load_grant_table,
)

__all__ = (
    "BuiltinCommand",
    "CommandGrant",
    "CustomSkill",
    "DepartmentGrants",
    "EffectiveGrants",
    "GrantTable",
    "GrantValidationError",
    "RoleOverride",
    "SkillGrant",
    "default_grants_path",
    "load_grant_table",
)
