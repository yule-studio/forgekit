"""Slash-command / skill grant table — loader + validator (issue #185).

The grant table (``agents/grants/slash-command-grants.json``) is the SSoT
for *which agent (department) may use which slash command / custom skill,
at what autonomy level*. Built-in slash commands (``/compact``,
``/context`` …) and custom skills (registry markdown specs) are granted
per department, with optional per-role overrides.

This module is pure-Python and deterministic:

  * No side effects on import; no live CLI / network.
  * :func:`load_grant_table` parses + structures the JSON.
  * :meth:`GrantTable.validate` returns a list of human-readable problems
    (or raises via :meth:`GrantTable.validate_or_raise`) so a governance
    test can lock the table down without the runtime depending on it.
  * :meth:`GrantTable.effective_grants` merges a department grant with any
    ``<agent>/<role>`` override.

The runtime *enforcement* of grants (blocking an ungranted slash command
mid-dispatch) is intentionally a follow-up — this module only loads,
validates, and answers "is X granted to Y?".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

VALID_AUTONOMY: Tuple[str, ...] = ("L0", "L1", "L2", "L3", "L4")
VALID_APPROVAL: Tuple[str, ...] = ("false", "role-approver", "human")


class GrantValidationError(ValueError):
    """Raised by :meth:`GrantTable.validate_or_raise` when the table is
    inconsistent (unknown command/skill, missing spec file, bad autonomy)."""


# ---------------------------------------------------------------------------
# Catalog entries
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuiltinCommand:
    """One built-in harness slash command (e.g. ``/compact``)."""

    command: str
    purpose: str
    harness: Tuple[str, ...]
    interactive_only: bool
    grantable: bool


@dataclass(frozen=True)
class CustomSkill:
    """One custom skill backed by a registry markdown spec."""

    skill_id: str
    spec: str
    purpose: str
    default_autonomy: str
    harness: Tuple[str, ...]
    extra: Mapping[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Grant entries
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandGrant:
    command: str
    autonomy: str


@dataclass(frozen=True)
class SkillGrant:
    skill: str
    autonomy: str


@dataclass(frozen=True)
class DepartmentGrants:
    agent_id: str
    c_level: str
    builtin: Tuple[CommandGrant, ...]
    skills: Tuple[SkillGrant, ...]
    notes: str


@dataclass(frozen=True)
class RoleOverride:
    role_id: str
    add_builtin: Tuple[CommandGrant, ...]
    add_skills: Tuple[SkillGrant, ...]
    notes: str


@dataclass(frozen=True)
class EffectiveGrants:
    """A department's grants merged with any matching role override."""

    actor_id: str
    builtin: Tuple[CommandGrant, ...]
    skills: Tuple[SkillGrant, ...]

    def grants_command(self, command: str) -> bool:
        return any(g.command == command for g in self.builtin)

    def grants_skill(self, skill: str) -> bool:
        return any(g.skill == skill for g in self.skills)


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GrantTable:
    schema_version: str
    builtin_commands: Mapping[str, BuiltinCommand]
    custom_skills: Mapping[str, CustomSkill]
    departments: Mapping[str, DepartmentGrants]
    role_overrides: Mapping[str, RoleOverride]
    raw: Mapping[str, object]

    # -- queries -----------------------------------------------------------

    def effective_grants(self, actor_id: str) -> Optional[EffectiveGrants]:
        """Merge a department's grants with a role override.

        *actor_id* may be a department id (``"engineering-agent"``) or a
        ``"<agent>/<role>"`` actor (``"engineering-agent/tech-lead"``).
        Returns ``None`` if the department is unknown.
        """

        department_id = actor_id.split("/", 1)[0]
        dept = self.departments.get(department_id)
        if dept is None:
            return None

        builtin: Dict[str, CommandGrant] = {g.command: g for g in dept.builtin}
        skills: Dict[str, SkillGrant] = {g.skill: g for g in dept.skills}

        override = self.role_overrides.get(actor_id)
        if override is not None:
            for g in override.add_builtin:
                builtin[g.command] = g
            for g in override.add_skills:
                skills[g.skill] = g

        return EffectiveGrants(
            actor_id=actor_id,
            builtin=tuple(builtin.values()),
            skills=tuple(skills.values()),
        )

    def is_command_granted(self, actor_id: str, command: str) -> bool:
        eff = self.effective_grants(actor_id)
        return bool(eff and eff.grants_command(command))

    def is_skill_granted(self, actor_id: str, skill: str) -> bool:
        eff = self.effective_grants(actor_id)
        return bool(eff and eff.grants_skill(skill))

    # -- validation --------------------------------------------------------

    def validate(self, *, repo_root: Optional[Path] = None) -> list[str]:
        """Return a list of human-readable problems (empty == healthy).

        When *repo_root* is given, custom-skill spec files are checked for
        existence on disk.
        """

        problems: list[str] = []

        # custom skill catalog: spec files + autonomy sanity
        for skill_id, skill in self.custom_skills.items():
            if skill.default_autonomy not in VALID_AUTONOMY:
                problems.append(
                    f"custom_skills[{skill_id}].default_autonomy "
                    f"{skill.default_autonomy!r} not in {VALID_AUTONOMY}"
                )
            if repo_root is not None and skill.spec:
                spec_path = repo_root / skill.spec
                if not spec_path.is_file():
                    problems.append(
                        f"custom_skills[{skill_id}].spec missing on disk: {skill.spec}"
                    )

        # department grants reference the catalog correctly
        for dept_id, dept in self.departments.items():
            for g in dept.builtin:
                cmd = self.builtin_commands.get(g.command)
                if cmd is None:
                    problems.append(
                        f"grants[{dept_id}] references unknown builtin {g.command!r}"
                    )
                elif not cmd.grantable:
                    problems.append(
                        f"grants[{dept_id}] grants non-grantable builtin "
                        f"{g.command!r} (interactive_only / operator-only)"
                    )
                if g.autonomy not in VALID_AUTONOMY:
                    problems.append(
                        f"grants[{dept_id}] builtin {g.command!r} autonomy "
                        f"{g.autonomy!r} not in {VALID_AUTONOMY}"
                    )
            for g in dept.skills:
                if g.skill not in self.custom_skills:
                    problems.append(
                        f"grants[{dept_id}] references unknown skill {g.skill!r}"
                    )
                if g.autonomy not in VALID_AUTONOMY:
                    problems.append(
                        f"grants[{dept_id}] skill {g.skill!r} autonomy "
                        f"{g.autonomy!r} not in {VALID_AUTONOMY}"
                    )

        # role overrides reference a known department + catalog entries
        for role_id, override in self.role_overrides.items():
            department_id = role_id.split("/", 1)[0]
            if department_id not in self.departments:
                problems.append(
                    f"role_overrides[{role_id}] department {department_id!r} unknown"
                )
            for g in override.add_builtin:
                if g.command not in self.builtin_commands:
                    problems.append(
                        f"role_overrides[{role_id}] unknown builtin {g.command!r}"
                    )
            for g in override.add_skills:
                if g.skill not in self.custom_skills:
                    problems.append(
                        f"role_overrides[{role_id}] unknown skill {g.skill!r}"
                    )

        return problems

    def validate_or_raise(self, *, repo_root: Optional[Path] = None) -> None:
        problems = self.validate(repo_root=repo_root)
        if problems:
            raise GrantValidationError(
                "slash-command grant table invalid:\n  - "
                + "\n  - ".join(problems)
            )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def default_grants_path() -> Path:
    """Repo-default location of the grant SSoT JSON."""

    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "agents" / "grants" / "slash-command-grants.json"


def _command_grants(items: object) -> Tuple[CommandGrant, ...]:
    out: list[CommandGrant] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, Mapping):
                out.append(
                    CommandGrant(
                        command=str(item.get("command", "")),
                        autonomy=str(item.get("autonomy", "")),
                    )
                )
    return tuple(out)


def _skill_grants(items: object) -> Tuple[SkillGrant, ...]:
    out: list[SkillGrant] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, Mapping):
                out.append(
                    SkillGrant(
                        skill=str(item.get("skill", "")),
                        autonomy=str(item.get("autonomy", "")),
                    )
                )
    return tuple(out)


def load_grant_table(path: Optional[Path] = None) -> GrantTable:
    """Load + structure the grant table. Does not validate — call
    :meth:`GrantTable.validate` / :meth:`GrantTable.validate_or_raise`.
    """

    grants_path = path or default_grants_path()
    data = json.loads(grants_path.read_text(encoding="utf-8"))

    builtin_commands: Dict[str, BuiltinCommand] = {}
    for command, meta in (data.get("builtin_commands") or {}).items():
        meta = meta if isinstance(meta, Mapping) else {}
        builtin_commands[command] = BuiltinCommand(
            command=command,
            purpose=str(meta.get("purpose", "")),
            harness=tuple(meta.get("harness", []) or ()),
            interactive_only=bool(meta.get("interactive_only", False)),
            grantable=bool(meta.get("grantable", False)),
        )

    custom_skills: Dict[str, CustomSkill] = {}
    for skill_id, meta in (data.get("custom_skills") or {}).items():
        meta = meta if isinstance(meta, Mapping) else {}
        known = {"spec", "purpose", "default_autonomy", "harness"}
        extra = {k: v for k, v in meta.items() if k not in known}
        custom_skills[skill_id] = CustomSkill(
            skill_id=skill_id,
            spec=str(meta.get("spec", "")),
            purpose=str(meta.get("purpose", "")),
            default_autonomy=str(meta.get("default_autonomy", "")),
            harness=tuple(meta.get("harness", []) or ()),
            extra=extra,
        )

    departments: Dict[str, DepartmentGrants] = {}
    for dept_id, meta in (data.get("grants") or {}).items():
        meta = meta if isinstance(meta, Mapping) else {}
        departments[dept_id] = DepartmentGrants(
            agent_id=dept_id,
            c_level=str(meta.get("c_level", "")),
            builtin=_command_grants(meta.get("builtin")),
            skills=_skill_grants(meta.get("skills")),
            notes=str(meta.get("notes", "")),
        )

    role_overrides: Dict[str, RoleOverride] = {}
    for role_id, meta in (data.get("role_overrides") or {}).items():
        meta = meta if isinstance(meta, Mapping) else {}
        role_overrides[role_id] = RoleOverride(
            role_id=role_id,
            add_builtin=_command_grants(meta.get("add_builtin")),
            add_skills=_skill_grants(meta.get("add_skills")),
            notes=str(meta.get("notes", "")),
        )

    return GrantTable(
        schema_version=str(data.get("schema_version", "")),
        builtin_commands=builtin_commands,
        custom_skills=custom_skills,
        departments=departments,
        role_overrides=role_overrides,
        raw=data,
    )
