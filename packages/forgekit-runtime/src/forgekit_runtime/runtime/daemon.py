"""Bounded always-on daemon (WT4) — a REAL long-running local runtime, not a sim.

``serve`` runs a real loop: each tick calls a bounded ``tick_fn`` (observe → classify →
packet → handoff → wait), writes a heartbeat, surfaces approval-needed to the operator
(notification), then sleeps ``poll_interval``. It stops on a kill-switch file, a
SIGTERM/SIGINT, or ``max_ticks``. It NEVER does privileged work — that is the tick_fn's
bounded contract; the daemon only schedules + heartbeats + notifies.

Everything IO is injectable (sleep / notifier / heartbeat path / tick_fn) so the loop
is deterministic in tests. The real process really runs; honest limits (e.g. macOS
sleep suspends the process) are documented, not faked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import heartbeat as HB


@dataclass(frozen=True)
class TickOutcome:
    """What one tick produced — the daemon only needs to know if an operator is needed.

    The execution fields (WT2 #241) let ``forgekit runtime status`` surface what the
    last tick actually DID: how many safe-class mutations executed, where, why a tick
    skipped, and the next tick eligible to act (cooldown)."""

    summary: str = ""
    waiting: bool = False          # an approval-needed / blocked condition this tick
    blocked_count: int = 0
    executed: int = 0              # safe-class mutations this tick actually performed+verified
    executed_paths: tuple = ()     # repo-relative paths really written this tick
    skipped_reason: str = ""       # why nothing executed (cooldown / dupes / halt)
    next_eligible_tick: int = 0    # tick at which execution resumes (0 = no cooldown)
    # provider/runtime continuity (lane B) — which provider lane this tick routed through
    # (brain vs actual transport vs fallback) + the budget snapshot. Optional so the
    # base daemon stays decoupled; the continuity wrapper fills them. Honest dicts or None.
    provider_lane: Optional[dict] = None
    budget: Optional[dict] = None


@dataclass
class DaemonResult:
    ticks: int = 0
    stopped_reason: str = ""
    waits: int = 0
    notified: int = 0
    heartbeats: int = 0
    executed: int = 0              # total safe-class mutations across the run (WT2 #241)
    resumed_from: int = 0          # prior heartbeat tick this run resumed from (0 = cold start)

    def to_dict(self) -> dict:
        return {"ticks": self.ticks, "stopped_reason": self.stopped_reason,
                "waits": self.waits, "notified": self.notified,
                "heartbeats": self.heartbeats, "executed": self.executed,
                "resumed_from": self.resumed_from}


@dataclass
class BoundedDaemon:
    """The long-running runtime wrapper. Bounded, heartbeated, operator-notified."""

    poll_interval: float = 60.0
    max_ticks: int = 0             # 0 → until kill/signal (a real long-run); >0 bounds it
    env: Optional[dict] = None
    heartbeat_path: Optional[Path] = None
    kill_switch_path: Optional[Path] = None
    notifier: Optional[object] = None
    sleep_fn: Optional[Callable[[float], None]] = None
    pid: int = 0
    resume: bool = True            # on serve start, continue tick numbering from the last heartbeat
    _stop: bool = False

    def request_stop(self) -> None:
        self._stop = True

    def _resume_tick(self) -> Tuple[int, str]:
        """Read the prior heartbeat and return (start_tick, note). Honest continuity only — we
        resume the tick counter (so cooldown/next_eligible_tick stay monotonic across a restart,
        e.g. launchd ``KeepAlive``), we do NOT fake that the prior process is still alive."""

        if not self.resume:
            return 0, ""
        prior = HB.read_heartbeat(path=self.heartbeat_path, env=self.env)
        if prior.tick <= 0:
            return 0, ""
        return prior.tick, f"resumed from tick {prior.tick} (prev status={prior.status})"

    def _heartbeat(self, status: str, tick: int, note: str = "") -> bool:
        from .heartbeat import Heartbeat

        from forgekit_provider.usage.ledger import now_ts  # reuse the real clock

        ok = HB.write_heartbeat(
            Heartbeat(status=status, tick=tick, ts=now_ts(self.env), pid=self.pid, note=note),
            path=self.heartbeat_path, env=self.env)
        return ok

    def _killed(self) -> bool:
        return HB.is_killed(path=self.kill_switch_path, env=self.env)

    def _notify_waiting(self, outcome: TickOutcome) -> bool:
        if self.notifier is None or not outcome.waiting:
            return False
        try:
            from ..notify.events import EVENT_APPROVAL_REQUIRED, NotificationEvent

            self.notifier.notify(NotificationEvent(
                EVENT_APPROVAL_REQUIRED, "forgekit always-on: 승인 필요",
                why=f"권한 없는 영역 {outcome.blocked_count}개에서 대기",
                action="runbook 확인 후 승인/거부", source="always-on-daemon"))
            return True
        except Exception:  # noqa: BLE001 - notify never breaks the loop
            return False

    def once(self, tick_fn: Callable[[int], TickOutcome]) -> TickOutcome:
        """Run a single tick (forgekit runtime once)."""

        outcome = tick_fn(1)
        self._heartbeat(HB.STATUS_IDLE, 1, outcome.summary)
        self._notify_waiting(outcome)
        return outcome

    def serve(self, tick_fn: Callable[[int], TickOutcome]) -> DaemonResult:
        """Run the bounded long-running loop. Stops on kill/signal/max_ticks."""

        res = DaemonResult()
        sleep = self.sleep_fn or _real_sleep
        self._install_signals()
        tick, resume_note = self._resume_tick()
        res.resumed_from = tick
        if tick:
            # honest startup heartbeat so `forgekit runtime status` shows the resume immediately.
            self._heartbeat(HB.STATUS_IDLE, tick, resume_note)
        run_ticks = 0          # ticks executed THIS serve (max_ticks bounds this, not the resumed offset)
        while not self._stop:
            if self._killed():
                res.stopped_reason = "kill switch"
                break
            if self.max_ticks and run_ticks >= self.max_ticks:
                res.stopped_reason = f"max_ticks({self.max_ticks})"
                break
            tick += 1
            run_ticks += 1
            outcome = tick_fn(tick)
            res.ticks = tick
            res.executed += outcome.executed
            if self._heartbeat(HB.STATUS_RUNNING, tick, outcome.summary):
                res.heartbeats += 1
            if outcome.waiting:
                res.waits += 1
                if self._notify_waiting(outcome):
                    res.notified += 1
            sleep(self.poll_interval)
        if not res.stopped_reason:
            res.stopped_reason = "stop requested"
        self._heartbeat(HB.STATUS_STOPPED, tick, res.stopped_reason)
        return res

    def _install_signals(self) -> None:
        try:
            import signal

            def _handler(signum, frame):  # pragma: no cover - signal path
                self._stop = True

            signal.signal(signal.SIGTERM, _handler)
            signal.signal(signal.SIGINT, _handler)
        except (ValueError, OSError, AttributeError):
            # not the main thread / unsupported → rely on kill switch / max_ticks
            pass


def _real_sleep(seconds: float) -> None:  # pragma: no cover - trivial
    import time

    time.sleep(max(0.0, seconds))


__all__ = ("TickOutcome", "DaemonResult", "BoundedDaemon")
