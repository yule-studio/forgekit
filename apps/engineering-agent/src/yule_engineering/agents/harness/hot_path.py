"""Hot-path enforcement seam — grant gate + per-run receipt (issue #185 follow-up).

This module is the *wiring* between the deterministic harness primitives
(:mod:`grant_enforcement`, :mod:`execution_receipt`) and the live role-runner
dispatch path (:mod:`agents.runners.role_runner` /
:mod:`agents.runners.bootstrap`). It is intentionally thin and pure:

  * :func:`build_capability_block_gate` returns a ``pre_dispatch_gate`` callable
    the dispatcher runs *before* contacting any provider. It reads the requested
    capabilities off ``RoleRunnerInput.metadata['capabilities']`` and, if any is
    a hard BLOCK under the grant table, short-circuits the take with a
    ``STATUS_BLOCKED`` output. ADVISORY / ALLOW never block — advisories are
    surfaced in the receipt, not enforced as a stop.
  * :func:`dispatch_receipt` builds the per-run :class:`ExecutionReceipt`
    (loaded docs/policies, agent/role, granted skills, blocked-or-missing,
    selected runner, warnings, compaction/cleanup status) from a finished
    dispatch.

Decoupling rule: this module imports from ``agents.runners.role_runner`` (which
has no harness dependency), so ``agents.runners.bootstrap`` must import this
module *lazily* (inside functions) to avoid an import cycle.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence, Tuple

from ..runners.role_runner import (
    STATUS_BLOCKED,
    RoleRunnerInput,
    RoleRunnerOutput,
)
from .execution_receipt import ExecutionReceipt, build_execution_receipt
from .grant_enforcement import GrantDecision, GrantVerdict, evaluate_capability
from .security_gate import assess_security_review
from .slash_command_grants import GrantTable

# Optional change-context for security auto-dispatch, read off input metadata.
CHANGE_METADATA_KEY = "change"

# Where a caller declares the capabilities (skills / slash commands) a take will
# use, so the gate can enforce grants for them.
CAPABILITIES_METADATA_KEY = "capabilities"
# Optional explicit actor override; otherwise derived as "<agent>/<role>".
ACTOR_METADATA_KEY = "actor_id"

DEFAULT_AGENT_ID = "engineering-agent"

# Provider label stamped on a gate-blocked output (never a real backend).
GATE_PROVIDER = "grant-gate"


def actor_id_for(input_: RoleRunnerInput, *, agent_id: str = DEFAULT_AGENT_ID) -> str:
    """Resolve the grant-table actor for *input_*.

    Honors an explicit ``metadata['actor_id']``; otherwise builds
    ``"<agent_id>/<role>"`` (role normalized to its last path segment), or the
    bare agent id when no role is present.
    """

    md = input_.metadata or {}
    explicit = md.get(ACTOR_METADATA_KEY)
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    role = (input_.role or "").split("/", 1)[-1].strip()
    return f"{agent_id}/{role}" if role else agent_id


def requested_capabilities(input_: RoleRunnerInput) -> Tuple[str, ...]:
    """Capabilities declared on ``metadata['capabilities']`` (list of str)."""

    md = input_.metadata or {}
    raw = md.get(CAPABILITIES_METADATA_KEY)
    out: list[str] = []
    if isinstance(raw, (list, tuple)):
        for cap in raw:
            if isinstance(cap, str) and cap.strip():
                out.append(cap.strip())
    return tuple(out)


def evaluate_input_capabilities(
    table: GrantTable, input_: RoleRunnerInput, *, agent_id: str = DEFAULT_AGENT_ID
) -> Tuple[str, Tuple[GrantDecision, ...]]:
    """Return ``(actor_id, decisions)`` for *input_*'s requested capabilities."""

    actor = actor_id_for(input_, agent_id=agent_id)
    caps = requested_capabilities(input_)
    decisions = tuple(evaluate_capability(table, actor, cap) for cap in caps)
    return actor, decisions


def split_decisions(
    decisions: Sequence[GrantDecision],
) -> Tuple[Tuple[GrantDecision, ...], Tuple[GrantDecision, ...], Tuple[GrantDecision, ...]]:
    """Split into ``(blocked, advisory, allowed)``."""

    blocked = tuple(d for d in decisions if d.verdict is GrantVerdict.BLOCK)
    advisory = tuple(d for d in decisions if d.verdict is GrantVerdict.ADVISORY)
    allowed = tuple(d for d in decisions if d.verdict is GrantVerdict.ALLOW)
    return blocked, advisory, allowed


def block_output(
    input_: RoleRunnerInput, blocked: Sequence[GrantDecision]
) -> RoleRunnerOutput:
    """A ``STATUS_BLOCKED`` take naming the blocked capabilities."""

    reasons = "; ".join(d.surface() for d in blocked) or "ungranted capability"
    return RoleRunnerOutput(
        provider=GATE_PROVIDER,
        status=STATUS_BLOCKED,
        text="",
        detail=f"grant gate blocked dispatch: {reasons}",
        used_fallback=False,
        metrics={
            "blocked_capabilities": [d.capability for d in blocked],
            "actor_id": actor_id_for(input_),
        },
    )


def build_capability_block_gate(
    table: GrantTable, *, agent_id: str = DEFAULT_AGENT_ID
):
    """Build a ``pre_dispatch_gate`` that blocks BLOCK-verdict capabilities.

    ALLOW and ADVISORY both return ``None`` (proceed). Only a hard BLOCK
    short-circuits the take. Never raises — a take with no declared
    capabilities always proceeds.
    """

    def _gate(session: Any, input_: RoleRunnerInput) -> Optional[RoleRunnerOutput]:
        _actor, decisions = evaluate_input_capabilities(table, input_, agent_id=agent_id)
        blocked, _advisory, _allowed = split_decisions(decisions)
        if blocked:
            return block_output(input_, blocked)
        return None

    return _gate


def dispatch_receipt(
    loaded_context: Any,
    table: GrantTable,
    input_: RoleRunnerInput,
    output: RoleRunnerOutput,
    *,
    agent_id: str = DEFAULT_AGENT_ID,
    compaction: Any = None,
    cleanup: Any = None,
) -> ExecutionReceipt:
    """Build the per-run execution receipt for a finished dispatch.

    ``selected_runner`` is the winning provider (or ``grant-gate`` on a block);
    requested capabilities flow through so blocked/advisory ones surface under
    "blocked or missing".
    """

    actor = actor_id_for(input_, agent_id=agent_id)
    # Security auto-dispatch: when the take carries change-context metadata,
    # evaluate whether security-engineer review must intercept and record it.
    security = None
    md = getattr(input_, "metadata", None) or {}
    change = md.get(CHANGE_METADATA_KEY)
    if isinstance(change, dict):
        security = assess_security_review(change)
    return build_execution_receipt(
        loaded_context,
        table,
        actor_id=actor,
        selected_runner=output.provider,
        requested_capabilities=requested_capabilities(input_),
        compaction=compaction,
        cleanup=cleanup,
        security=security,
    )


__all__ = (
    "CAPABILITIES_METADATA_KEY",
    "ACTOR_METADATA_KEY",
    "DEFAULT_AGENT_ID",
    "GATE_PROVIDER",
    "actor_id_for",
    "requested_capabilities",
    "evaluate_input_capabilities",
    "split_decisions",
    "block_output",
    "build_capability_block_gate",
    "dispatch_receipt",
)
