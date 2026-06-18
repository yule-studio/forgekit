"""repo-autopilot orchestration + executor arbitration (WT2).

Runs a repo through the team phases — observe → classify → pm_structure →
gateway_route → tech_lead_signoff → execute → verify → record — but with hard rails:

* **repo allowlist** — only ``forgekit`` / ``bkurs-fe`` / ``bkurs-be`` (operator-set);
  any other repo is refused (no autopilot on arbitrary repos).
* **one executor at a time** — :class:`ExecutorArbiter` is a single-slot lock; while
  one role holds execution rights, every other is read/review/queue only.
* **limits** — diff / file / risk caps; only internal-approved SAFE work executes.
* **failure threshold / cooldown / kill switch** — repeated verify failures trip a
  cooldown then halt; an explicit kill switch stops everything.

Deterministic + offline → testable. "Execution" here is the orchestration record
(task split + verification), not a real mutation — actual file edits are out of this
WT's scope and remain behind the chain + a single executor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import chain as CH
from .artifacts import ExecutionTaskSplit, RepoFinding, VerificationReport

# the 8 team phases
PHASE_OBSERVE = "observe"
PHASE_CLASSIFY = "classify"
PHASE_PM = "pm_structure"
PHASE_GATEWAY = "gateway_route"
PHASE_SIGNOFF = "tech_lead_signoff"
PHASE_EXECUTE = "execute"
PHASE_VERIFY = "verify"
PHASE_RECORD = "record"

DEFAULT_ALLOWLIST: Tuple[str, ...] = ("forgekit", "bkurs-fe", "bkurs-be")


@dataclass
class ExecutorArbiter:
    """Single-slot execution lock — only ONE role may execute at a time."""

    _holder: Optional[str] = None
    _queue: List[str] = field(default_factory=list)
    log: List[str] = field(default_factory=list)

    def acquire(self, executor: str) -> bool:
        if self._holder is None:
            self._holder = executor
            self.log.append(f"grant:{executor}")
            return True
        if executor not in self._queue:
            self._queue.append(executor)
        return False

    def release(self, executor: str) -> None:
        if self._holder == executor:
            self.log.append(f"release:{executor}")
            self._holder = self._queue.pop(0) if self._queue else None
            if self._holder:
                self.log.append(f"grant:{self._holder}")

    @property
    def holder(self) -> Optional[str]:
        return self._holder

    @property
    def queued(self) -> Tuple[str, ...]:
        return tuple(self._queue)


@dataclass(frozen=True)
class AutopilotLimits:
    max_diff: int = 200
    max_files: int = 10
    max_risk_score: float = 1.0
    failure_threshold: int = 3


@dataclass
class AutopilotRunResult:
    repo: str
    steps: List[str] = field(default_factory=list)
    executed: List[dict] = field(default_factory=list)
    proposed: List[dict] = field(default_factory=list)   # needs user/operator (not auto)
    executor_log: List[str] = field(default_factory=list)
    blocked_repo: bool = False
    halted: bool = False
    halt_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "repo": self.repo, "steps": self.steps,
            "executed": self.executed, "proposed": self.proposed,
            "executor_log": self.executor_log,
            "blocked_repo": self.blocked_repo, "halted": self.halted,
            "halt_reason": self.halt_reason,
        }


@dataclass
class AutopilotOrchestrator:
    allowlist: Tuple[str, ...] = DEFAULT_ALLOWLIST
    limits: AutopilotLimits = field(default_factory=AutopilotLimits)
    kill_switch: bool = False
    mutator: Optional[object] = None   # BoundedMutator → REAL safe-class execution (WT3)

    def is_allowed(self, repo: str) -> bool:
        return (repo or "").strip() in self.allowlist

    def run_cycle(self, repo: str, findings: Sequence[RepoFinding], *,
                  arbiter: Optional[ExecutorArbiter] = None,
                  risk_of=lambda f: "") -> AutopilotRunResult:
        """One bounded autopilot cycle over *repo*'s findings (single executor)."""

        res = AutopilotRunResult(repo=repo)
        if not self.is_allowed(repo):
            res.blocked_repo = True
            res.halt_reason = f"repo '{repo}' 은 allowlist({', '.join(self.allowlist)}) 밖 — 거부"
            return res
        if self.kill_switch:
            res.halted = True
            res.halt_reason = "kill switch on — 정지"
            return res

        arb = arbiter or ExecutorArbiter()
        failures = 0
        for finding in findings:
            res.steps += [f"{PHASE_OBSERVE}:{finding.finding[:30]}", PHASE_CLASSIFY]
            packet, route, decision, trace = CH.run_internal_chain(
                finding, risk_class=risk_of(finding))
            res.steps += [PHASE_PM, PHASE_GATEWAY, f"{PHASE_SIGNOFF}:{decision.decision_class}"]
            if not CH.can_specialist_execute(decision):
                # not internal-approved safe → propose only (user/operator path)
                res.proposed.append({"finding": finding.finding,
                                     "decision_class": decision.decision_class,
                                     "approval_level": decision.approval_level})
                continue
            # SAFE class → ONE executor at a time
            executor = route.owner_role
            granted = arb.acquire(executor)
            if not granted:
                res.proposed.append({"finding": finding.finding, "queued_for": executor})
                continue

            # WT3: a mutator performs a REAL bounded write (verified). WITHOUT a mutator
            # there is NO fake execution — the item is recorded as proposed-only.
            if self.mutator is None:
                res.proposed.append({"finding": finding.finding, "decision_class": "safe",
                                     "note": "no mutator — propose-only (실제 실행 미연결)"})
                arb.release(executor)
                continue

            res.steps.append(f"{PHASE_EXECUTE}:{executor}")
            outcome = self._mutate(finding, repo)
            res.steps.append(PHASE_VERIFY)
            if not (outcome.executed and outcome.verified):
                failures += 1
                res.proposed.append({"finding": finding.finding, "executor": executor,
                                     "refused": outcome.refused_reason or "verify 실패"})
                arb.release(executor)
                if failures >= self.limits.failure_threshold:
                    res.halted = True
                    res.halt_reason = "verify 실패 반복 — cooldown/정지 (커밋하지 않음)"
                    break
                continue
            res.executed.append({"finding": finding.finding, "executor": executor,
                                 "verified": True, "path": outcome.path,
                                 "lines_changed": outcome.lines_changed})
            res.steps.append(PHASE_RECORD)
            arb.release(executor)   # release so the NEXT executor can take the slot
        res.executor_log = list(arb.log)
        # invariant: the arbiter granted to at most one holder at any instant (serial)
        return res

    def _mutate(self, finding, repo: str):
        """Build a safe-class note task for the finding → REAL bounded write via the mutator."""

        from .runner import ACTION_NOTE, ExecTask

        slug = "".join(c if c.isalnum() else "-" for c in finding.finding.lower())[:40].strip("-") or "task"
        rel = f"runs/forgekit/autopilot/{repo}-{slug}.md"
        content = (
            f"# autopilot note — {repo}\n\n"
            f"- finding: {finding.finding}\n"
            f"- kind: {getattr(finding, 'kind', '')}\n"
            f"- class: safe (internal-approved: PM→gateway→tech-lead)\n"
            f"- action: 안전 클래스 note 기록 (실제 코드 mutation 은 후속 단계)\n"
        )
        return self.mutator.execute(ExecTask(ACTION_NOTE, rel, content=content,
                                             summary=finding.finding))


__all__ = (
    "PHASE_OBSERVE", "PHASE_CLASSIFY", "PHASE_PM", "PHASE_GATEWAY", "PHASE_SIGNOFF",
    "PHASE_EXECUTE", "PHASE_VERIFY", "PHASE_RECORD", "DEFAULT_ALLOWLIST",
    "ExecutorArbiter", "AutopilotLimits", "AutopilotRunResult", "AutopilotOrchestrator",
)
