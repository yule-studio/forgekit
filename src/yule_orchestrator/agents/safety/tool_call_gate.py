"""Pre-tool-call gate that turns a risk class into an action verdict
(F12 / #103).

Where :mod:`risk_classifier` answers *"how dangerous is this tool
call?"*, this module answers *"given the caller's autonomy level,
should we ALLOW it, REQUIRE_APPROVAL, or BLOCK it?"*. The decision
is the cross of:

  * :class:`yule_orchestrator.agents.safety.risk_classifier.RiskClass`
    (SAFE / LOW / MEDIUM / HIGH / CRITICAL)
  * the autonomy level string carried on
    :class:`~yule_orchestrator.agents.safety.risk_classifier.ToolCallContext`
    (L0_manual_only / L1_advisory / L2_autonomous_record /
    L3_human_approval / L4_full_autonomous)

Hard rails (regression-tested in
``tests/engineering/test_tool_gate_governance.py``):

  * CRITICAL is ``BLOCK`` for *every* autonomy level. No flag, no
    env var, no caller-supplied autonomy short-circuits this.
  * Env switch ``YULE_TOOL_GATE_ENABLED=false`` makes the gate
    *transparent* (always ALLOW) but emits an explicit
    :mod:`warnings` warning so a governance test can detect that
    the safety circuit is bypassed.
  * Protected branch pushes climb to CRITICAL in the classifier;
    the gate then blocks them.
  * BLOCK verdicts register a stable signature
    (``tool_gate.<risk_class>.blocked``) on the mistake ledger if
    one was injected, so repeated attempts accumulate the same
    audit row.
"""

from __future__ import annotations

import enum
import os
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Tuple

from .risk_classifier import (
    RiskClass,
    RiskSignal,
    ToolCallContext,
    classify_tool_call,
)


# ---------------------------------------------------------------------------
# Env knobs
# ---------------------------------------------------------------------------


ENV_TOOL_GATE_ENABLED: str = "YULE_TOOL_GATE_ENABLED"
ENV_TOOL_GATE_DEFAULT_AUTONOMY: str = "YULE_TOOL_GATE_DEFAULT_AUTONOMY"


_DEFAULT_AUTONOMY_FALLBACK: str = "L2_autonomous_record"


def _env_truthy(value: Optional[str]) -> bool:
    """Permissive truthy decode (``true`` / ``1`` / ``yes`` / ``on``)."""

    if value is None:
        return False
    return value.strip().lower() in {"true", "1", "yes", "on"}


def _gate_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """Return whether the gate circuit is active.

    Default is **ON** (safety-first). The gate is considered
    disabled only when the operator sets
    ``YULE_TOOL_GATE_ENABLED`` to an explicit falsey value
    (``false`` / ``0`` / ``no`` / ``off``).
    """

    src = env if env is not None else os.environ
    raw = src.get(ENV_TOOL_GATE_ENABLED)
    if raw is None:
        return True
    text = raw.strip().lower()
    if not text:
        return True
    return text in {"true", "1", "yes", "on"}


def _resolve_autonomy(
    ctx_autonomy: str,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """Pick the autonomy level for *ctx*.

    Context's own ``autonomy_level`` always wins; falling back to
    ``YULE_TOOL_GATE_DEFAULT_AUTONOMY`` and finally to
    ``L2_autonomous_record``.
    """

    explicit = (ctx_autonomy or "").strip()
    if explicit:
        return explicit
    src = env if env is not None else os.environ
    configured = (src.get(ENV_TOOL_GATE_DEFAULT_AUTONOMY) or "").strip()
    if configured:
        return configured
    return _DEFAULT_AUTONOMY_FALLBACK


# ---------------------------------------------------------------------------
# Action + verdict dataclasses
# ---------------------------------------------------------------------------


class GateAction(str, enum.Enum):
    """Decision the gate emits for a single tool call."""

    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class ToolGateVerdict:
    """Outcome of a :func:`gate_tool_call` call.

    ``signatures`` lists the mistake-ledger signatures that
    *would* be registered for the verdict (the gate only writes
    them when ``ledger`` is injected — see :func:`gate_tool_call`).
    """

    risk_class: RiskClass
    action: GateAction
    reason: str
    signatures: Tuple[str, ...] = ()
    evaluated_at: str = ""

    def is_blocked(self) -> bool:
        return self.action is GateAction.BLOCK

    def requires_approval(self) -> bool:
        return self.action is GateAction.REQUIRE_APPROVAL

    def is_allowed(self) -> bool:
        return self.action is GateAction.ALLOW


# ---------------------------------------------------------------------------
# autonomy × RiskClass matrix
# ---------------------------------------------------------------------------


_R = RiskClass
_A = GateAction


# Each row maps a normalised autonomy key → mapping of RiskClass
# → GateAction. Normalisation: lowercase, strip whitespace.
_GATE_MATRIX: Mapping[str, Mapping[RiskClass, GateAction]] = {
    "l0_manual_only": {
        _R.SAFE: _A.REQUIRE_APPROVAL,
        _R.LOW: _A.REQUIRE_APPROVAL,
        _R.MEDIUM: _A.REQUIRE_APPROVAL,
        _R.HIGH: _A.REQUIRE_APPROVAL,
        _R.CRITICAL: _A.BLOCK,
    },
    "l1_advisory": {
        _R.SAFE: _A.ALLOW,
        _R.LOW: _A.ALLOW,
        _R.MEDIUM: _A.REQUIRE_APPROVAL,
        _R.HIGH: _A.REQUIRE_APPROVAL,
        _R.CRITICAL: _A.BLOCK,
    },
    "l2_autonomous_record": {
        _R.SAFE: _A.ALLOW,
        _R.LOW: _A.ALLOW,
        _R.MEDIUM: _A.ALLOW,
        _R.HIGH: _A.REQUIRE_APPROVAL,
        _R.CRITICAL: _A.BLOCK,
    },
    "l3_human_approval": {
        _R.SAFE: _A.ALLOW,
        _R.LOW: _A.REQUIRE_APPROVAL,
        _R.MEDIUM: _A.REQUIRE_APPROVAL,
        _R.HIGH: _A.REQUIRE_APPROVAL,
        _R.CRITICAL: _A.BLOCK,
    },
    "l4_full_autonomous": {
        _R.SAFE: _A.ALLOW,
        _R.LOW: _A.ALLOW,
        _R.MEDIUM: _A.ALLOW,
        _R.HIGH: _A.ALLOW,
        _R.CRITICAL: _A.BLOCK,
    },
}


# Short alias map so callers can pass either "L2_autonomous_record"
# or the bare "L2" — both resolve to the same row. Hard rail: even
# bare "L4" cannot escape CRITICAL → BLOCK because the matrix row
# itself carries that mapping.
_AUTONOMY_ALIASES: Mapping[str, str] = {
    "l0": "l0_manual_only",
    "l1": "l1_advisory",
    "l2": "l2_autonomous_record",
    "l3": "l3_human_approval",
    "l4": "l4_full_autonomous",
}


def _normalise_autonomy_key(value: str) -> str:
    text = (value or "").strip().lower()
    if not text:
        return "l2_autonomous_record"
    if text in _GATE_MATRIX:
        return text
    if text in _AUTONOMY_ALIASES:
        return _AUTONOMY_ALIASES[text]
    return text  # let the lookup below fall back


def _resolve_action(autonomy_key: str, risk: RiskClass) -> Tuple[GateAction, str]:
    """Resolve a matrix cell, hard-railing CRITICAL → BLOCK."""

    # Hard rail: CRITICAL is BLOCK regardless of autonomy. This
    # is the single most important invariant of F12 — we encode
    # it once here so a future matrix edit cannot accidentally
    # weaken it.
    if risk is RiskClass.CRITICAL:
        return GateAction.BLOCK, "hard_rail.critical_always_blocked"

    row = _GATE_MATRIX.get(autonomy_key)
    if row is None:
        # Unknown autonomy level — fall back to the strictest sane
        # default (L0_manual_only) so the gate never accidentally
        # ALLOWs an action from an unrecognised caller.
        return (
            GateAction.REQUIRE_APPROVAL,
            f"autonomy.unknown.fallback_l0:{autonomy_key}",
        )

    action = row.get(risk)
    if action is None:  # pragma: no cover - defensive
        return (
            GateAction.REQUIRE_APPROVAL,
            f"matrix.missing_cell:{autonomy_key}:{risk.value}",
        )
    return action, f"matrix.{autonomy_key}.{risk.value}"


# ---------------------------------------------------------------------------
# Gate entry point
# ---------------------------------------------------------------------------


_GATE_DISABLED_WARNING_FORMAT: str = (
    "YULE tool-call gate is disabled via env "
    f"{ENV_TOOL_GATE_ENABLED}=false — tool calls are not being "
    "checked. This is a regression guard surface; expect "
    "test_tool_gate_governance to flag it."
)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def _ledger_signature(risk: RiskClass) -> str:
    return f"tool_gate.{risk.value.lower()}.blocked"


def _register_block_signature(
    *,
    ledger: Any,
    risk: RiskClass,
    ctx: ToolCallContext,
) -> str:
    """Best-effort: record a BLOCK on the injected mistake ledger.

    The ledger object must expose ``record_mistake(role=,
    pattern=, signature=, blocker_level=)`` — that's the
    public surface of
    :class:`yule_orchestrator.agents.learning.mistake_ledger.MistakeLedger`.
    Any other shape (or any exception) is swallowed so a failing
    ledger never silently leaks into the gate verdict.
    """

    signature = _ledger_signature(risk)
    if ledger is None:
        return signature

    try:
        # Late import keeps the safety package independent of
        # learning at import time — the gate only needs the
        # ``record_mistake`` duck-typed call site.
        from ..learning.mistake_ledger import BlockerLevel  # noqa: WPS433

        role = (ctx.role or "engineering-agent").strip() or "engineering-agent"
        pattern = f"tool_call_gate.{risk.value.lower()}"
        ledger.record_mistake(
            role=role,
            pattern=pattern,
            signature=signature,
            blocker_level=BlockerLevel.BLOCK,
        )
    except Exception:  # noqa: BLE001 - never let the audit leg crash the gate
        pass
    return signature


def gate_tool_call(
    ctx: ToolCallContext,
    *,
    classifier: Callable[
        [ToolCallContext], Tuple[RiskClass, Tuple[RiskSignal, ...]]
    ] = classify_tool_call,
    ledger: Any = None,
    env: Optional[Mapping[str, str]] = None,
) -> ToolGateVerdict:
    """Run the gate for one tool call.

    Wiring:

      1. If ``YULE_TOOL_GATE_ENABLED`` decodes to false → emit a
         warning and return a transparent ``(SAFE, ALLOW)``
         verdict. The reason field cites ``gate disabled`` so
         downstream audit can tell apart "legitimately SAFE" from
         "safety circuit bypassed".
      2. Otherwise classify the call via *classifier*, look up the
         action in the autonomy × RiskClass matrix, and return a
         :class:`ToolGateVerdict`.
      3. If the verdict is BLOCK *and* ``ledger`` is supplied,
         register the canonical ``tool_gate.<risk_class>.blocked``
         signature so repeat attempts accumulate.
    """

    if not _gate_enabled(env=env):
        warnings.warn(
            _GATE_DISABLED_WARNING_FORMAT,
            stacklevel=2,
        )
        return ToolGateVerdict(
            risk_class=RiskClass.SAFE,
            action=GateAction.ALLOW,
            reason="gate disabled",
            signatures=(),
            evaluated_at=_now_iso(),
        )

    risk_class, _signals = classifier(ctx)
    autonomy = _resolve_autonomy(ctx.autonomy_level, env=env)
    key = _normalise_autonomy_key(autonomy)
    action, reason = _resolve_action(key, risk_class)

    signatures: Tuple[str, ...] = ()
    if action is GateAction.BLOCK:
        sig = _register_block_signature(
            ledger=ledger,
            risk=risk_class,
            ctx=ctx,
        )
        signatures = (sig,)

    return ToolGateVerdict(
        risk_class=risk_class,
        action=action,
        reason=reason,
        signatures=signatures,
        evaluated_at=_now_iso(),
    )


__all__ = (
    "ENV_TOOL_GATE_DEFAULT_AUTONOMY",
    "ENV_TOOL_GATE_ENABLED",
    "GateAction",
    "ToolGateVerdict",
    "gate_tool_call",
)
