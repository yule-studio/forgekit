"""Static risk classifier for individual tool calls (F12 / #103).

Tool calls are *atomic actions the agent can perform on the world*:
reading a file, running a unit test, opening an external HTTP
connection, committing to git, force-pushing to a protected
branch, etc. The classifier inspects a :class:`ToolCallContext`
and returns one of five :class:`RiskClass` levels plus the
:class:`RiskSignal` evidence list that drove the decision.

Design contract:

  * **Deterministic and pure** — same ``ToolCallContext`` →
    same ``(RiskClass, signals)``. No I/O, no clock, no env
    reads. This is what lets the gate layer (``tool_call_gate``)
    sit on top of it without re-running side-effecty logic.
  * **Static rule catalogue** — the ≥15 rule patterns are
    enumerated below. New rules append to :data:`_RULES` so the
    catalogue stays auditable in a single place.
  * **Vocabulary aligned with F7** — same five names as the F7
    auto-merge risk class so an operator reading either feed
    sees one consistent severity ladder
    (SAFE → LOW → MEDIUM → HIGH → CRITICAL).
  * **Escalation wins** — multiple matching signals only raise
    the class, never lower it. CRITICAL signals are absolute.

Hard rails:

  * Protected branch (``main`` / ``master`` / ``develop`` /
    ``prod``) push always lands at CRITICAL — covered by both
    ``protected_branch_push`` rule and explicit
    ``_protected_branch_target`` evidence.
  * ``rm -rf`` / ``--no-verify`` / ``force push`` /
    ``git reset --hard`` / sandbox-disable shapes all land at
    CRITICAL even when other signals look benign.
  * Unknown tool ids default to MEDIUM (not SAFE) — an unknown
    surface should be treated as if it can touch real state until
    the catalogue learns it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Callable, Sequence, Tuple


# ---------------------------------------------------------------------------
# RiskClass
# ---------------------------------------------------------------------------


class RiskClass(str, enum.Enum):
    """Severity ladder for tool calls.

    Same names as the F7 (#98) auto-merge :class:`RiskClass` —
    the alignment is intentional so operator-facing surfaces show
    one vocabulary across both axes (PR-level vs. tool-call-level).
    """

    SAFE = "SAFE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


_CLASS_ORDER = {
    RiskClass.SAFE: 0,
    RiskClass.LOW: 1,
    RiskClass.MEDIUM: 2,
    RiskClass.HIGH: 3,
    RiskClass.CRITICAL: 4,
}


def _max_class(a: RiskClass, b: RiskClass) -> RiskClass:
    return a if _CLASS_ORDER[a] >= _CLASS_ORDER[b] else b


# ---------------------------------------------------------------------------
# Context + signal dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCallContext:
    """Snapshot of one tool call before it fires.

    ``tool_id`` is the canonical short identifier (``git_push``,
    ``read_file``, ``external_http_fetch``, ...). ``target`` is
    the primary subject of the call — a file path, a branch
    name, a URL, depending on the tool. ``args`` carries
    additional positional arguments as a tuple of strings so the
    classifier can pattern-match on them (e.g. ``rm`` with
    ``-rf`` flag → CRITICAL).
    """

    tool_id: str
    target: str = ""
    args: Tuple[str, ...] = ()
    role: str = ""
    session_id: str = ""
    autonomy_level: str = ""

    def normalised_tool_id(self) -> str:
        return (self.tool_id or "").strip().lower()

    def normalised_target(self) -> str:
        return (self.target or "").strip()

    def normalised_args(self) -> Tuple[str, ...]:
        return tuple(str(arg or "") for arg in self.args or ())


@dataclass(frozen=True)
class RiskSignal:
    """One piece of evidence that contributed to the verdict.

    ``weight`` is the :class:`RiskClass` this signal pushes the
    overall verdict toward. ``evidence`` is a short, redaction-
    safe string suitable for audit logs (it must never echo
    secrets — callers already pass non-sensitive identifiers).
    """

    name: str
    weight: RiskClass
    evidence: str


# ---------------------------------------------------------------------------
# Rule catalogue
# ---------------------------------------------------------------------------


# Protected branches that must never accept a direct push.
_PROTECTED_BRANCHES: frozenset[str] = frozenset(
    {"main", "master", "develop", "release", "prod", "production"}
)


# Tool ids grouped by their baseline risk class. The classifier
# consults these tables first; downstream rules may *escalate*
# (e.g. ``edit_file`` is LOW by default but if the target is
# ``.env.local`` it climbs to HIGH).
_SAFE_TOOL_IDS: frozenset[str] = frozenset(
    {
        "read_file",
        "glob",
        "grep",
        "git_status",
        "git_log",
        "git_diff",
        "ls",
    }
)

_LOW_TOOL_IDS: frozenset[str] = frozenset(
    {
        "edit_file",
        "git_add",
        "unittest",
        "pytest",
    }
)

_MEDIUM_TOOL_IDS: frozenset[str] = frozenset(
    {
        "git_commit",
        "git_branch_create",
        "new_module_add",
        "subprocess_within_repo",
    }
)

_HIGH_TOOL_IDS: frozenset[str] = frozenset(
    {
        "git_push",
        "external_http_fetch",
        "subprocess_outside_repo",
        "live_llm_call",
        "env_local_modify",
        "secret_decode_attempt",
    }
)

_CRITICAL_TOOL_IDS: frozenset[str] = frozenset(
    {
        "git_reset_hard",
        "rm_rf",
        "force_push",
        "secret_rotation",
        "protected_branch_push",
        "dangerouslyDisableSandbox".lower(),
        "git_no_verify",
    }
)


def _baseline_for_tool(tool_id: str) -> Tuple[RiskClass, str]:
    """Map a normalised tool id to its baseline risk class."""

    if tool_id in _SAFE_TOOL_IDS:
        return RiskClass.SAFE, "tool_id.catalogue.safe"
    if tool_id in _LOW_TOOL_IDS:
        return RiskClass.LOW, "tool_id.catalogue.low"
    if tool_id in _MEDIUM_TOOL_IDS:
        return RiskClass.MEDIUM, "tool_id.catalogue.medium"
    if tool_id in _HIGH_TOOL_IDS:
        return RiskClass.HIGH, "tool_id.catalogue.high"
    if tool_id in _CRITICAL_TOOL_IDS:
        return RiskClass.CRITICAL, "tool_id.catalogue.critical"
    # Unknown surface — default to MEDIUM so the gate at least
    # asks for approval at L1 / L3 rather than silently allowing.
    return RiskClass.MEDIUM, "tool_id.unknown.default_medium"


# ---------------------------------------------------------------------------
# Escalation rules
# ---------------------------------------------------------------------------


_DANGEROUS_ARG_FLAGS: Tuple[Tuple[str, RiskClass, str], ...] = (
    ("--force", RiskClass.CRITICAL, "args.flag.force"),
    ("-f", RiskClass.HIGH, "args.flag.short_force"),
    ("--no-verify", RiskClass.CRITICAL, "args.flag.no_verify"),
    ("--hard", RiskClass.CRITICAL, "args.flag.hard_reset"),
    ("-rf", RiskClass.CRITICAL, "args.flag.rm_rf"),
    ("dangerouslydisablesandbox", RiskClass.CRITICAL, "args.flag.sandbox_disable"),
)


_SECRET_KEYWORDS: Tuple[str, ...] = (
    "secret",
    "token",
    "credential",
    "api_key",
    "apikey",
    "private_key",
)


def _matches_protected_branch(target: str) -> bool:
    if not target:
        return False
    candidate = target.strip().lower()
    if candidate in _PROTECTED_BRANCHES:
        return True
    # Allow ``refs/heads/main`` style targets.
    if candidate.startswith("refs/heads/"):
        return candidate.rsplit("/", 1)[-1] in _PROTECTED_BRANCHES
    if candidate.startswith("origin/"):
        return candidate.split("/", 1)[1] in _PROTECTED_BRANCHES
    return False


def _matches_env_local(target: str) -> bool:
    if not target:
        return False
    lowered = target.strip().lower()
    return lowered.endswith(".env.local") or lowered.endswith("/.env") or lowered == ".env.local"


def _contains_secret_keyword(text: str) -> bool:
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in _SECRET_KEYWORDS)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


def classify_tool_call(
    ctx: ToolCallContext,
) -> Tuple[RiskClass, Tuple[RiskSignal, ...]]:
    """Return ``(RiskClass, signals)`` for *ctx*.

    The function is pure: no I/O, no env reads, no clock. The
    signal list is deterministic and ordered by the order rules
    fire — first the catalogue baseline, then escalation rules
    (protected branch / dangerous args / secret keywords).
    """

    tool_id = ctx.normalised_tool_id()
    target = ctx.normalised_target()
    args = ctx.normalised_args()

    signals: list[RiskSignal] = []

    baseline_class, baseline_name = _baseline_for_tool(tool_id)
    signals.append(
        RiskSignal(
            name=baseline_name,
            weight=baseline_class,
            evidence=f"tool_id={tool_id or '<empty>'}",
        )
    )
    verdict = baseline_class

    # Rule: protected branch push always climbs to CRITICAL.
    if tool_id in {"git_push", "force_push", "protected_branch_push"} and (
        _matches_protected_branch(target)
        or any(_matches_protected_branch(arg) for arg in args)
    ):
        signals.append(
            RiskSignal(
                name="rule.protected_branch.push",
                weight=RiskClass.CRITICAL,
                evidence=f"target={target or '<args>'}",
            )
        )
        verdict = _max_class(verdict, RiskClass.CRITICAL)

    # Rule: env.local / .env modifications are HIGH even via the
    # generic edit_file shape.
    if tool_id in {"edit_file", "env_local_modify", "write_file"} and _matches_env_local(
        target
    ):
        signals.append(
            RiskSignal(
                name="rule.env_local.modify",
                weight=RiskClass.HIGH,
                evidence=f"target={target}",
            )
        )
        verdict = _max_class(verdict, RiskClass.HIGH)

    # Rule: dangerous flag combinations regardless of tool id.
    lowered_args = tuple(arg.lower() for arg in args)
    for flag, weight, name in _DANGEROUS_ARG_FLAGS:
        if flag in lowered_args:
            signals.append(
                RiskSignal(
                    name=f"rule.dangerous_flag.{name.rsplit('.', 1)[-1]}",
                    weight=weight,
                    evidence=f"flag={flag}",
                )
            )
            verdict = _max_class(verdict, weight)

    # Rule: secret-related keywords in target or args nudge up to
    # HIGH (or stay CRITICAL if already there).
    if tool_id == "secret_decode_attempt":
        # already HIGH via catalogue; still emit explicit signal.
        signals.append(
            RiskSignal(
                name="rule.secret.decode_attempt",
                weight=RiskClass.HIGH,
                evidence="tool_id=secret_decode_attempt",
            )
        )
        verdict = _max_class(verdict, RiskClass.HIGH)
    elif _contains_secret_keyword(target) or any(
        _contains_secret_keyword(arg) for arg in args
    ):
        signals.append(
            RiskSignal(
                name="rule.secret.keyword",
                weight=RiskClass.HIGH,
                evidence=f"target_or_args_contains_secret_keyword",
            )
        )
        verdict = _max_class(verdict, RiskClass.HIGH)

    # Rule: subprocess pointing outside the repo escalates to
    # HIGH even if the catalogue had it as MEDIUM (e.g. when a
    # caller mislabels).
    if tool_id == "subprocess_outside_repo":
        signals.append(
            RiskSignal(
                name="rule.subprocess.outside_repo",
                weight=RiskClass.HIGH,
                evidence=f"target={target}",
            )
        )
        verdict = _max_class(verdict, RiskClass.HIGH)

    # Rule: secret rotation — even if caller normalised tool_id
    # differently, args mentioning rotation are CRITICAL.
    if tool_id == "secret_rotation" or "secret_rotation" in lowered_args:
        signals.append(
            RiskSignal(
                name="rule.secret.rotation",
                weight=RiskClass.CRITICAL,
                evidence="secret_rotation",
            )
        )
        verdict = _max_class(verdict, RiskClass.CRITICAL)

    return verdict, tuple(signals)


__all__ = (
    "RiskClass",
    "RiskSignal",
    "ToolCallContext",
    "classify_tool_call",
)
