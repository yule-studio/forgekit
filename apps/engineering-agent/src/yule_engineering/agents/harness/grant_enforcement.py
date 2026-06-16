"""Runtime enforcement of the slash-command / skill grant table (issue #185 follow-up).

The grant *table* (``slash_command_grants``) is the SSoT for "is X granted to
Y?". This module is the *enforcement* layer the runtime calls when an actor
(department or ``<dept>/<role>``) is about to use a built-in slash command or a
custom skill. It turns a grant lookup into one of three verdicts:

    ALLOW     — granted; proceed.
    ADVISORY  — a *known, grantable* capability that this actor does not hold;
                surface a warning but let the gateway/operator decide. The
                capability is benign-by-catalog (it could be granted), so a
                hard stop would be over-blocking.
    BLOCK     — a capability that must never be used by this actor: an unknown
                capability (not in the catalog at all) or a non-grantable
                built-in (interactive / operator-only UI). Also blocks unknown
                actors (no department in the table).

The advisory-vs-block line is fixed here in code, mirrored in
``docs/agent-slash-commands.md`` §"grant 강제", and locked by
``tests/agents/test_grant_enforcement.py``:

    | situation                                  | verdict  |
    | ------------------------------------------ | -------- |
    | granted to actor                           | ALLOW    |
    | ungranted, known builtin, grantable=true   | ADVISORY |
    | ungranted, known custom skill              | ADVISORY |
    | ungranted, builtin grantable=false         | BLOCK    |
    | unknown command / skill (not in catalog)   | BLOCK    |
    | unknown actor (no department)              | BLOCK    |

This module is pure-Python and deterministic — no side effects on import, no
live CLI. It only reads a :class:`GrantTable`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .slash_command_grants import GrantTable


class CapabilityKind(str, Enum):
    COMMAND = "command"
    SKILL = "skill"


class GrantVerdict(str, Enum):
    ALLOW = "allow"
    ADVISORY = "advisory"
    BLOCK = "block"


@dataclass(frozen=True)
class GrantDecision:
    """Outcome of evaluating one capability use for one actor."""

    actor_id: str
    kind: CapabilityKind
    capability: str
    verdict: GrantVerdict
    reason: str

    @property
    def allowed(self) -> bool:
        return self.verdict is GrantVerdict.ALLOW

    @property
    def blocked(self) -> bool:
        return self.verdict is GrantVerdict.BLOCK

    @property
    def advisory(self) -> bool:
        return self.verdict is GrantVerdict.ADVISORY

    def surface(self) -> str:
        """One-line human-facing surface string (for receipts / logs)."""

        icon = {
            GrantVerdict.ALLOW: "✅",
            GrantVerdict.ADVISORY: "⚠️",
            GrantVerdict.BLOCK: "⛔",
        }[self.verdict]
        return f"{icon} {self.verdict.value.upper()} {self.kind.value} {self.capability} — {self.reason}"


def evaluate_command(table: GrantTable, actor_id: str, command: str) -> GrantDecision:
    """Evaluate a built-in slash command use for *actor_id*."""

    eff = table.effective_grants(actor_id)
    if eff is None:
        return _decision(
            actor_id, CapabilityKind.COMMAND, command, GrantVerdict.BLOCK,
            f"unknown actor '{actor_id}' — no department in grant table",
        )

    if eff.grants_command(command):
        return _decision(
            actor_id, CapabilityKind.COMMAND, command, GrantVerdict.ALLOW,
            "granted to actor",
        )

    spec = table.builtin_commands.get(command)
    if spec is None:
        return _decision(
            actor_id, CapabilityKind.COMMAND, command, GrantVerdict.BLOCK,
            "unknown command — not in built-in catalog",
        )
    if not spec.grantable:
        return _decision(
            actor_id, CapabilityKind.COMMAND, command, GrantVerdict.BLOCK,
            "non-grantable command (interactive / operator-only UI)",
        )
    return _decision(
        actor_id, CapabilityKind.COMMAND, command, GrantVerdict.ADVISORY,
        "ungranted but grantable — gateway/operator may extend the grant",
    )


def evaluate_skill(table: GrantTable, actor_id: str, skill: str) -> GrantDecision:
    """Evaluate a custom skill use for *actor_id*."""

    eff = table.effective_grants(actor_id)
    if eff is None:
        return _decision(
            actor_id, CapabilityKind.SKILL, skill, GrantVerdict.BLOCK,
            f"unknown actor '{actor_id}' — no department in grant table",
        )

    if eff.grants_skill(skill):
        return _decision(
            actor_id, CapabilityKind.SKILL, skill, GrantVerdict.ALLOW,
            "granted to actor",
        )

    if skill not in table.custom_skills:
        return _decision(
            actor_id, CapabilityKind.SKILL, skill, GrantVerdict.BLOCK,
            "unknown skill — not in custom-skill catalog",
        )
    return _decision(
        actor_id, CapabilityKind.SKILL, skill, GrantVerdict.ADVISORY,
        "ungranted but registered — gateway/operator may extend the grant",
    )


def evaluate_capability(
    table: GrantTable, actor_id: str, capability: str, *, kind: Optional[CapabilityKind] = None
) -> GrantDecision:
    """Evaluate a command or skill; infers *kind* from the ``/`` prefix if omitted."""

    if kind is None:
        kind = CapabilityKind.COMMAND if capability.startswith("/") else CapabilityKind.SKILL
    if kind is CapabilityKind.COMMAND:
        return evaluate_command(table, actor_id, capability)
    return evaluate_skill(table, actor_id, capability)


def _decision(
    actor_id: str,
    kind: CapabilityKind,
    capability: str,
    verdict: GrantVerdict,
    reason: str,
) -> GrantDecision:
    return GrantDecision(
        actor_id=actor_id,
        kind=kind,
        capability=capability,
        verdict=verdict,
        reason=reason,
    )


__all__ = (
    "CapabilityKind",
    "GrantVerdict",
    "GrantDecision",
    "evaluate_command",
    "evaluate_skill",
    "evaluate_capability",
)
