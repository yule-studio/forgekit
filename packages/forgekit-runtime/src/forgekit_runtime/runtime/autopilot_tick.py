"""Daemon ↔ autopilot execution wiring (WT2 #241).

The always-on daemon used to only *observe and classify* each tick. This module makes
a tick actually DRIVE bounded autopilot execution: observe repo-local signals →
internal approval chain (PM → gateway → tech-lead) → **safe-class only** real mutation
via :class:`BoundedMutator` → verify → record. Everything risky/restricted/blocked is
surfaced (``waiting``) and never executed.

Cross-tick rails the daemon itself does not own:
- **dedupe** — a finding already executed this session is not re-run (avoids no-op churn
  that would otherwise trip the failure threshold).
- **cooldown** — after the orchestrator halts (repeated verify failures), execution is
  paused for ``cooldown_ticks`` and the next eligible tick is surfaced.

The orchestrator keeps the single-executor invariant and the safe-class boundary; this
ticker adds the time dimension. Pure except for the injected collector/mutator, so it is
deterministic in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from ..autopilot.artifacts import RepoFinding
from ..autopilot.orchestrator import AutopilotOrchestrator
from ..autopilot.runner import BoundedMutator
from .daemon import TickOutcome

# a standing restricted finding proves the bounded wait/notify path keeps surfacing
# (it contains "배포"/deploy → L4 restricted → never auto-executed).
_RESTRICTED_PROBE = "운영/배포 준비 점검"


def _sig(finding: RepoFinding) -> str:
    return f"{finding.repo}:{finding.finding}"


@dataclass
class AutopilotTicker:
    """Builds a daemon tick_fn that performs bounded autopilot execution (#241)."""

    repo_root: Path
    repo_name: str = "forgekit"
    mutator: Optional[object] = None
    orchestrator: Optional[AutopilotOrchestrator] = None
    collector: Optional[object] = None       # RepoLocalCollector (injectable for tests)
    max_findings: int = 4
    cooldown_ticks: int = 3
    # G1: also advance ACTIVE long-term goals each tick (physical safe-class execution of their
    # next pending packet via the execute_bridge). Default ON — this is what makes the always-on
    # loop goal-DRIVEN (continuity), not just repo-finding driven. env/config thread the goal
    # store + nexus vault root (None → real ~/.forgekit). Set execute_goals=False to disable.
    execute_goals: bool = True
    max_goals_per_tick: int = 3
    env: Optional[Mapping[str, str]] = None
    config: Optional[Mapping] = None
    _executed_sigs: Dict[str, int] = field(default_factory=dict)
    _cooldown_until: int = 0

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root)
        if self.mutator is None:
            self.mutator = BoundedMutator(self.repo_root)
        if self.orchestrator is None:
            self.orchestrator = AutopilotOrchestrator(mutator=self.mutator)

    # --- observation -------------------------------------------------------
    def _observe(self) -> List[RepoFinding]:
        findings: List[RepoFinding] = []
        try:
            collector = self.collector
            if collector is None:
                from nexus.sources import RepoLocalCollector

                collector = RepoLocalCollector(self.repo_root)
            for it in collector.collect(limit=self.max_findings):
                title = getattr(it, "title", "") or str(it)
                findings.append(RepoFinding(self.repo_name, title, kind="docs"))
        except Exception:  # noqa: BLE001 - observation must never crash the loop
            pass
        findings.append(RepoFinding(self.repo_name, _RESTRICTED_PROBE, kind="ops"))
        return findings

    @staticmethod
    def _risk_of(finding: RepoFinding) -> str:
        # let approval.classify_level read the wording; no forced override here.
        return ""

    # --- one tick ----------------------------------------------------------
    def tick(self, n: int) -> TickOutcome:
        if n < self._cooldown_until:
            return TickOutcome(
                summary=f"tick {n}: cooldown (실행 재개 tick {self._cooldown_until})",
                waiting=True, skipped_reason="cooldown", next_eligible_tick=self._cooldown_until,
            )
        observed = self._observe()
        # dedupe: drop findings already executed this session (avoid no-op churn).
        fresh = [f for f in observed if _sig(f) not in self._executed_sigs]
        deduped = len(observed) - len(fresh)

        result = self.orchestrator.run_cycle(self.repo_name, fresh, risk_of=self._risk_of)

        executed_paths = tuple(str(e.get("path", "")) for e in result.executed if e.get("path"))
        for e in result.executed:
            self._executed_sigs[f"{self.repo_name}:{e.get('finding', '')}"] = n

        next_eligible = 0
        if result.halted:
            self._cooldown_until = n + self.cooldown_ticks
            next_eligible = self._cooldown_until

        waiting = bool(result.proposed) or result.blocked_repo or result.halted
        skipped = ""
        if deduped:
            skipped = f"{deduped} dupes skipped"
        if result.halted:
            skipped = (skipped + "; " if skipped else "") + (result.halt_reason or "halt")

        # G1: advance ACTIVE long-term goals (physical safe-class execution of their next packet).
        # This is what makes the loop goal-DRIVEN; risky proposals stay awaiting_approval (surfaced).
        goal_exec = 0
        goal_paths: Tuple[str, ...] = ()
        goal_awaiting = 0
        if self.execute_goals:
            try:
                from .goal_exec_tick import execute_active_goals

                rep = execute_active_goals(self.repo_root, self.mutator, env=self.env,
                                           config=self.config, max_goals=self.max_goals_per_tick)
                goal_exec = rep.executed
                goal_paths = rep.executed_paths
                goal_awaiting = rep.awaiting
            except Exception:  # noqa: BLE001 - goal execution must never crash the loop
                pass

        total_exec = len(result.executed) + goal_exec
        all_paths = executed_paths + goal_paths
        waiting = waiting or goal_awaiting > 0
        summary = (f"tick {n}: exec {len(result.executed)}+goal {goal_exec} / "
                   f"propose {len(result.proposed)}"
                   + (f" / awaiting {goal_awaiting}" if goal_awaiting else "")
                   + (f" / {skipped}" if skipped else ""))
        if all_paths:
            summary += " · " + ", ".join(all_paths[:2])
        return TickOutcome(
            summary=summary, waiting=waiting, blocked_count=len(result.proposed) + goal_awaiting,
            executed=total_exec, executed_paths=all_paths,
            skipped_reason=skipped, next_eligible_tick=next_eligible,
        )

    def tick_fn(self):
        """Return a ``tick_fn(n) -> TickOutcome`` bound to this ticker (for the daemon)."""

        return self.tick


__all__ = ("AutopilotTicker",)
