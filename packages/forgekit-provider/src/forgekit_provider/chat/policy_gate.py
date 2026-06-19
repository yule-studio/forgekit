"""Submit policy gate (WT1 runtime-teeth) — EffectivePolicy enforced BEFORE submit.

This is the seam that turns "displayed posture" into real behaviour. Given the
current runtime EffectivePolicy + a usage snapshot, it decides — BEFORE the provider
is called — whether to ALLOW (and which provider to route to), HOLD (approval-wait /
hold-all), or THROTTLE (budget posture crossed). The app builds the context (it owns
the live policy state); the ENFORCEMENT logic lives here, not in app.py.

Pure + dependency-light (only the policy seams), so the teeth are unit-testable with
injected policies/snapshots — no provider, no terminal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..policy import usage_policy as up

# gate actions
GATE_ALLOW = "allow"
GATE_HOLD = "hold"          # do NOT call the provider (approval-wait / hold-all)
GATE_THROTTLE = "throttle"  # budget posture crossed — defer / refuse the spend


@dataclass(frozen=True)
class UsageSnapshot:
    """What's been spent vs the budget (tokens). 0 budget → unbounded."""

    spent_tokens: int = 0
    budget_tokens: int = 0

    @property
    def has_budget(self) -> bool:
        return self.budget_tokens > 0


@dataclass(frozen=True)
class SubmitContext:
    """Everything the gate needs — the live runtime policy + usage, built by the app."""

    runtime_mode: str = ""
    effective_policy: object = None       # policy.runtime_mode.EffectivePolicy | None
    usage: UsageSnapshot = UsageSnapshot()


@dataclass(frozen=True)
class GateDecision:
    action: str
    reason: str = ""
    routing_target: str = ""    # the provider the mode routes to (ALLOW only)
    next_action: str = ""

    @property
    def allowed(self) -> bool:
        return self.action == GATE_ALLOW

    def held_result(self, runtime_mode: str = ""):
        """Build the SubmitResult for a HOLD/THROTTLE decision (no provider call)."""

        from . import models as m

        if self.action == GATE_THROTTLE:
            return m.SubmitResult(
                ok=False, mode=m.MODE_HELD, category=m.CAT_BUDGET_THROTTLED,
                runtime_mode=runtime_mode, throttled=True,
                text=self.reason, next_action=self.next_action)
        return m.SubmitResult(
            ok=False, mode=m.MODE_HELD, category=m.CAT_POLICY_HELD,
            runtime_mode=runtime_mode, text=self.reason, next_action=self.next_action)


def evaluate_gate(ctx: SubmitContext) -> GateDecision:
    """Decide ALLOW / HOLD / THROTTLE from the EffectivePolicy + usage. Real teeth."""

    pol = ctx.effective_policy
    if pol is None:
        # no resolved policy (e.g. setup-required) → let the service report no-provider.
        return GateDecision(GATE_ALLOW, "no policy")

    # 1) approval posture — hold-all (approval-wait) blocks the submit outright.
    if getattr(pol, "holds_all_actions", lambda: False)():
        return GateDecision(
            GATE_HOLD, f"{getattr(pol, 'mode_label', ctx.runtime_mode)} — 모든 행동 보류(hold-all)",
            next_action="Shift+Tab 으로 모드를 바꾸거나 승인 후 다시 시도하세요.")

    # 2) budget posture — consult the usage policy with the live snapshot.
    usage = getattr(pol, "usage", None)
    snap = ctx.usage
    if usage is not None and snap.has_budget and up.should_throttle(
            usage, snap.spent_tokens, snap.budget_tokens):
        return GateDecision(
            GATE_THROTTLE,
            f"budget posture({getattr(pol, 'budget_posture', '?')}) — "
            f"{snap.spent_tokens}/{snap.budget_tokens}tok 사용, reserve 진입 → throttle",
            next_action="budget 상향 또는 cost-save 모드로 전환하세요.")

    # 3) allow — route to the mode's resolved provider.
    target = getattr(pol, "routing_target", lambda: "")()
    return GateDecision(GATE_ALLOW, "ok", routing_target=target)


__all__ = (
    "GATE_ALLOW", "GATE_HOLD", "GATE_THROTTLE",
    "UsageSnapshot", "SubmitContext", "GateDecision", "evaluate_gate",
)
