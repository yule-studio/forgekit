"""Bounded always-on runtime loop — observe → classify → packet → handoff → wait.

This is the WT3 core: a LONG-running posture that is explicitly **bounded autonomy**,
never "infinite self-direction". It only ever:

  observe (scan for gaps) → classify → packetize (PM intake) → hand off (gateway) →
  WAIT for the operator.

It NEVER executes a privileged action (deploy / IAM / infra apply / secret). A
privileged finding produces a **runbook note** + a WAIT for operator approval — the
honest "I can't do this, here's how you can" path. Repeated blocked findings escalate
to the operator inbox (reusing :class:`lifecycle.failure_escalation`). Destructive
work is structurally impossible here: there is no execute phase.

The loop is a DETERMINISTIC stepped state machine (``run(findings)`` processes a
bounded number of findings and returns a full trace) — not a hidden background
thread — so it is unit-testable and an operator can see exactly what it did.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

from . import runbook as rb

# Handoff seam (WT2 boundary): the intake→packet bridge (``handoff.run_handoff``) lives
# in the operator app, not in this core package. The app (forgekit-console) injects it
# via ``register_handoff_runner`` so this package never imports an app (packages → apps
# hard rail). A loop may also take a per-instance ``handoff_runner``.
_handoff_runner: Optional[Callable] = None


def register_handoff_runner(fn: Optional[Callable]) -> None:
    """Register the process-wide handoff runner (called by the operator app)."""

    global _handoff_runner
    _handoff_runner = fn

# phases (the only things the loop may do — note: no EXECUTE phase exists) --------
PHASE_OBSERVE = "observe"
PHASE_CLASSIFY = "classify"
PHASE_PACKET = "packet"
PHASE_HANDOFF = "handoff"
PHASE_RUNBOOK = "runbook"
PHASE_WAIT = "wait"
PHASE_HALTED = "halted"

# autonomy levels the loop honours (mirrors policy.runtime_mode) -----------------
AUTONOMY_OBSERVE = "observe"   # watch: observe + classify + note only
AUTONOMY_BOUNDED = "bounded"   # always-on: + packetize + handoff + wait

# finding categories -------------------------------------------------------------
CAT_PRODUCT = "product"   # a missing feature/UX gap → PM packet
CAT_DESIGN = "design"     # design/spacing gap → PM packet (FE)
CAT_OPS = "ops"           # operational gap → may be privileged
CAT_INFRA = "infra"       # infra/deploy/IAM/secret → ALWAYS privileged → runbook


@dataclass(frozen=True)
class Finding:
    """One observed gap. ``privileged`` marks work forgekit may NOT execute."""

    project: str
    description: str
    category: str = CAT_PRODUCT
    privileged: bool = False

    def to_dict(self) -> dict:
        return {
            "project": self.project, "description": self.description,
            "category": self.category, "privileged": self.privileged,
        }


@dataclass(frozen=True)
class LoopStep:
    iteration: int
    phase: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {"iteration": self.iteration, "phase": self.phase, "detail": self.detail}


@dataclass
class LoopResult:
    steps: List[LoopStep] = field(default_factory=list)
    handoffs: List[dict] = field(default_factory=list)
    runbooks: List[rb.RunbookNote] = field(default_factory=list)
    waiting: bool = False
    halted: bool = True
    halt_reason: str = ""
    escalated: bool = False

    @property
    def blocked_count(self) -> int:
        return len(self.runbooks)

    def to_dict(self) -> dict:
        return {
            "steps": [s.to_dict() for s in self.steps],
            "handoffs": self.handoffs,
            "runbooks": [{"title": n.title, "area": n.area} for n in self.runbooks],
            "waiting": self.waiting,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "escalated": self.escalated,
            "blocked_count": self.blocked_count,
        }


@dataclass
class BoundedRuntimeLoop:
    """A bounded observe→classify→packet→handoff→wait loop. No execute phase."""

    autonomy: str = AUTONOMY_BOUNDED
    max_iterations: int = 10
    escalator: Optional[object] = None
    # injected intake→packet bridge; falls back to the process-wide registered runner.
    handoff_runner: Optional[Callable] = None

    def run(self, findings: Sequence[Finding]) -> LoopResult:
        result = LoopResult()
        n = 0
        for finding in findings:
            if n >= self.max_iterations:
                result.halt_reason = f"max_iterations({self.max_iterations}) 도달 — bounded"
                self._maybe_escalate(result, "max-iterations")
                return result
            n += 1
            result.steps.append(LoopStep(n, PHASE_OBSERVE, finding.description))
            result.steps.append(LoopStep(n, PHASE_CLASSIFY, f"category={finding.category}"))

            if self.autonomy == AUTONOMY_OBSERVE:
                # watch: report only — no packet, no handoff, no action.
                result.steps.append(LoopStep(n, PHASE_WAIT, "observe-only (watch) → 보고만"))
                continue

            if finding.privileged or finding.category == CAT_INFRA:
                # forgekit may NOT execute → runbook + WAIT for operator (honest).
                note = rb.build_runbook(rb.infer_area(finding.description),
                                        title=finding.description, context=finding.project)
                result.runbooks.append(note)
                result.steps.append(LoopStep(n, PHASE_RUNBOOK, f"area={note.area}"))
                result.steps.append(LoopStep(n, PHASE_WAIT, "operator 승인 필요 (권한 없음)"))
                result.waiting = True
                self._record_blocked(finding)
                continue

            # bounded product/design gap → PM packet + tech-lead handoff, then WAIT.
            handoff = self._packetize(finding)
            result.steps.append(LoopStep(n, PHASE_PACKET, "PM intake → packet"))
            result.steps.append(LoopStep(n, PHASE_HANDOFF, "gateway → tech-lead split"))
            result.handoffs.append(handoff.to_dict())
            for blocked in handoff.split.blocked:
                note = rb.build_runbook(rb.infer_area(blocked.title), title=blocked.title,
                                        context=finding.project)
                result.runbooks.append(note)
                result.waiting = True
            result.steps.append(LoopStep(n, PHASE_WAIT, "operator 검토/승인 대기"))
            result.waiting = result.waiting or True

        result.halt_reason = result.halt_reason or f"{n}개 finding 처리 후 정지 (bounded, idle)"
        if result.waiting:
            self._maybe_escalate(result, "operator-wait")
        return result

    def _packetize(self, finding: Finding):
        runner = self.handoff_runner or _handoff_runner
        if runner is None:
            raise RuntimeError(
                "handoff runner not configured — the operator app must call "
                "forgekit_runtime.runtime.loop.register_handoff_runner(...) "
                "(or pass handoff_runner=) before running a bounded loop."
            )
        return runner(finding.description, project=finding.project)

    def _record_blocked(self, finding: Finding) -> None:
        if self.escalator is None:
            return
        try:
            from ..lifecycle.failure_escalation import FailureSignature, KIND_POLICY

            self.escalator.record_failure(
                FailureSignature(KIND_POLICY, "privileged-blocked", finding.project or "infra"),
                symptom=f"권한 없는 영역 반복 발견: {finding.description}",
                attempted_fix="runbook note 생성 + operator 승인 대기",
            )
        except Exception:  # noqa: BLE001 - escalation must never break the loop
            pass

    def _maybe_escalate(self, result: LoopResult, reason: str) -> None:
        # surface that the bounded loop is parked waiting on a human (not a silent stall)
        if self.escalator is None or not result.waiting:
            return
        try:
            from ..lifecycle.failure_escalation import FailureSignature, KIND_STATUS_SURFACE

            outcome = self.escalator.record_failure(
                FailureSignature(KIND_STATUS_SURFACE, f"always-on-{reason}", "runtime"),
                symptom="always-on 루프가 operator 응답 대기로 정지",
                attempted_fix="관측→분류→패킷→handoff 완료, 실행은 승인 필요",
            )
            result.escalated = bool(getattr(outcome, "escalated", False))
        except Exception:  # noqa: BLE001
            pass


__all__ = (
    "PHASE_OBSERVE", "PHASE_CLASSIFY", "PHASE_PACKET", "PHASE_HANDOFF",
    "PHASE_RUNBOOK", "PHASE_WAIT", "PHASE_HALTED",
    "AUTONOMY_OBSERVE", "AUTONOMY_BOUNDED",
    "CAT_PRODUCT", "CAT_DESIGN", "CAT_OPS", "CAT_INFRA",
    "Finding", "LoopStep", "LoopResult", "BoundedRuntimeLoop",
    "register_handoff_runner",
)
