"""Bounded discovery loop + promotion criteria — accumulate over a 24h window.

A single `run_discovery_sweep` is ONE pass. A real "collect over 24 hours" assistant
needs a BOUNDED loop that runs repeated sweeps across a wall-clock budget, merging each
into the persisted ledger so signal ACCUMULATES (dedup + freshness) rather than resetting
every run. That driver lives here, together with the **promotion criteria** that decide
which accumulated ideas have earned a "should we ask the operator about this?" — because
the loop's whole purpose is to push corroborated, fresh ideas toward that decision.

Honesty rails:
  * the clock is INJECTED (a sequence of wall-clock timestamps) — the core never sleeps
    or fakes time; a host (daemon / goal-scheduler) supplies real cadence;
  * with no ``fetcher`` the collectors are honestly empty, so the loop runs offline in CI
    on repo-local signal alone;
  * promotion is a *proposal to ask the operator*, never an execution.

ponytail verdict: a NEW module (not a wrapper over sweep) is warranted — the bounded
accumulation loop and its corroboration/freshness criteria are genuinely new behavior,
not a thin re-export. Loop-driver and promotion-policy share one file because they are
one purpose (accumulate → qualify for the operator); splitting them would be ceremony.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional, Sequence, Tuple

from .ledger import DiscoveryLedger, LedgerIdea
from .sweep import run_discovery_sweep

# --- promotion criteria (which accumulated ideas to ASK the operator about) ----
DEFAULT_MIN_SCORE = 2.0       # below this a brief is too weak to bother the operator
DEFAULT_MIN_SEEN = 2          # must surface across ≥2 sweeps (corroboration, not noise)
DEFAULT_FRESH_HOURS = 36.0    # last seen within this window → still "live" interest


@dataclass(frozen=True)
class PromotionPolicy:
    """Thresholds for "this idea is worth asking the operator about" (tunable)."""

    min_score: float = DEFAULT_MIN_SCORE
    min_seen_count: int = DEFAULT_MIN_SEEN
    fresh_within_hours: float = DEFAULT_FRESH_HOURS


def _parse_ts(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def age_hours(now: str, last_seen: str) -> Optional[float]:
    """Hours between *last_seen* and *now* (ISO). None when either is unparseable."""

    a, b = _parse_ts(now), _parse_ts(last_seen)
    if a is None or b is None:
        return None
    return max(0.0, (a - b).total_seconds() / 3600.0)


def is_fresh(idea: LedgerIdea, now: str, *, within_hours: float = DEFAULT_FRESH_HOURS) -> bool:
    """Seen within the freshness window. Unknown age → not penalised (honest)."""

    age = age_hours(now, idea.last_seen)
    return True if age is None else age <= within_hours


def freshness_label(idea: LedgerIdea, now: str) -> str:
    age = age_hours(now, idea.last_seen)
    if age is None:
        return "관측 시각 불명"
    if age < 1:
        return "방금 관측"
    if age < 24:
        return f"{int(age)}시간 전 관측"
    return f"{int(age / 24)}일 전 관측"


def candidate_reason(idea: LedgerIdea, now: str) -> str:
    """Why this idea qualifies — corroboration + score + freshness, for the operator."""

    return f"{idea.seen_count}회 교차 관측 · score {idea.score} · {freshness_label(idea, now)}"


def is_candidate(idea: LedgerIdea, now: str, policy: PromotionPolicy = PromotionPolicy()) -> bool:
    """A pending idea that is corroborated (≥N sweeps), scored, and still fresh."""

    return (idea.pending
            and idea.score >= policy.min_score
            and idea.seen_count >= policy.min_seen_count
            and is_fresh(idea, now, within_hours=policy.fresh_within_hours))


def ask_candidates(ledger: DiscoveryLedger, now: str, *,
                   policy: PromotionPolicy = PromotionPolicy(),
                   limit: int = 5) -> List[Tuple[LedgerIdea, str]]:
    """The "ask the operator later" queue — corroborated, fresh, high-score pending ideas.

    Ordered most-corroborated first (seen across more sweeps = stronger signal), then by
    score. Each entry carries a human reason so the operator sees WHY it surfaced."""

    cands = [i for i in ledger.pending() if is_candidate(i, now, policy)]
    cands.sort(key=lambda i: (-i.seen_count, -i.score, i.first_seen))
    return [(i, candidate_reason(i, now)) for i in cands[:limit]]


def stale_pending(ledger: DiscoveryLedger, now: str, *,
                  within_hours: float = DEFAULT_FRESH_HOURS) -> List[LedgerIdea]:
    """Pending ideas not seen within the window — interest may have gone cold."""

    return [i for i in ledger.pending() if not is_fresh(i, now, within_hours=within_hours)]


# --- bounded loop driver -------------------------------------------------------
DEFAULT_WINDOW_HOURS = 24.0
DEFAULT_MAX_TICKS = 24
DEFAULT_MIN_INTERVAL_MIN = 30.0


@dataclass(frozen=True)
class LoopBudget:
    """The bound on a discovery loop: how long, how many ticks, how often."""

    window_hours: float = DEFAULT_WINDOW_HOURS
    max_ticks: int = DEFAULT_MAX_TICKS
    min_interval_minutes: float = DEFAULT_MIN_INTERVAL_MIN


@dataclass(frozen=True)
class LoopTick:
    """One sweep merged into the ledger — what accumulated this tick."""

    index: int
    at: str
    new_count: int
    seen_count: int
    total_tracked: int
    pending: int

    def to_dict(self) -> dict:
        return {"index": self.index, "at": self.at, "new_count": self.new_count,
                "seen_count": self.seen_count, "total_tracked": self.total_tracked,
                "pending": self.pending}


@dataclass(frozen=True)
class DiscoveryLoopReport:
    """The whole bounded window: per-tick accumulation + end-of-window candidates."""

    started_at: str
    ended_at: str
    window_hours: float
    ticks: Tuple[LoopTick, ...]
    new_total: int
    seen_total: int
    candidates: Tuple[dict, ...]   # {title, why, reason} ask-the-operator queue
    stopped_reason: str

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at, "ended_at": self.ended_at,
            "window_hours": self.window_hours, "ticks": [t.to_dict() for t in self.ticks],
            "new_total": self.new_total, "seen_total": self.seen_total,
            "candidates": list(self.candidates), "stopped_reason": self.stopped_reason,
        }

    def lines(self) -> Tuple[str, ...]:
        out: List[str] = [
            f"discovery loop — {self.window_hours:g}h bounded window",
            f"- ticks: {len(self.ticks)}회 · 중단 사유: {self.stopped_reason}",
            f"- 누적: 새 {self.new_total} · 다시 관측 {self.seen_total}",
            f"- operator 에게 물어볼 후보: {len(self.candidates)}건",
        ]
        for i, c in enumerate(self.candidates, 1):
            out.append(f"[{i}] {c['title']}")
            out.append(f"    근거: {c['reason']}")
        if not self.candidates:
            out.append("  (아직 교차 관측·신선도 기준을 넘은 후보 없음 — 더 누적되면 표면화)")
        return tuple(out)


def discovery_loop_tick(repo_root, ledger: DiscoveryLedger, *, now: str,
                        config: Optional[dict] = None, fetcher=None,
                        extra_signals: Sequence[str] = ()) -> LoopTick:
    """One loop tick: sweep → merge into *ledger* → tick summary (mutates the ledger).

    The host (daemon / goal-scheduler) calls this on its real cadence; the *now* string
    is the host's wall clock. Offline-safe: no fetcher → collectors honestly empty."""

    sweep = run_discovery_sweep(repo_root, fetcher=fetcher, config=config,
                                extra_signals=extra_signals)
    new, updated = ledger.record_sweep(sweep, now=now)
    s = ledger.summary()
    return LoopTick(index=0, at=now, new_count=len(new), seen_count=len(updated),
                    total_tracked=s["total"], pending=s["pending"])


def run_discovery_loop(repo_root, *, clock: Iterable[str], budget: LoopBudget = LoopBudget(),
                       ledger: Optional[DiscoveryLedger] = None,
                       config: Optional[dict] = None, fetcher=None,
                       policy: PromotionPolicy = PromotionPolicy(),
                       persist_env: Optional[dict] = None,
                       extra_signals: Sequence[str] = ()) -> DiscoveryLoopReport:
    """Drive bounded sweeps across the window in *clock* — accumulate into the ledger.

    *clock* is the injected sequence of wall-clock timestamps (ISO) at which the host
    would tick — deterministic in tests, real wall time in the daemon. The loop ticks at
    each timestamp until: the window (``window_hours`` from the first tick) is exhausted,
    ``max_ticks`` is reached, or the clock runs dry. Timestamps closer together than
    ``min_interval_minutes`` are skipped (don't hammer sources). The ledger accumulates;
    end-of-window candidates are the operator's ask-me-later queue."""

    ledger = ledger if ledger is not None else DiscoveryLedger()
    ticks: List[LoopTick] = []
    new_total = seen_total = 0
    start: Optional[datetime] = None
    last_at: Optional[str] = None
    last_dt: Optional[datetime] = None
    stopped = "clock-exhausted"

    for ts in clock:
        dt = _parse_ts(ts)
        if dt is None:
            continue
        if start is None:
            start = dt
        elif (dt - start).total_seconds() / 3600.0 > budget.window_hours:
            stopped = "window-exhausted"
            break
        if last_dt is not None and (dt - last_dt).total_seconds() / 60.0 < budget.min_interval_minutes:
            continue  # too soon since the last tick — respect cadence, skip
        if len(ticks) >= budget.max_ticks:
            stopped = "max-ticks"
            break
        tick = discovery_loop_tick(repo_root, ledger, now=ts, config=config,
                                   fetcher=fetcher, extra_signals=extra_signals)
        ticks.append(LoopTick(index=len(ticks), at=tick.at, new_count=tick.new_count,
                              seen_count=tick.seen_count, total_tracked=tick.total_tracked,
                              pending=tick.pending))
        new_total += tick.new_count
        seen_total += tick.seen_count
        last_at, last_dt = ts, dt

    if persist_env is not None:
        ledger.save(persist_env)

    end_at = last_at or (start.isoformat() if start else "")
    cands = ask_candidates(ledger, end_at, policy=policy) if end_at else []
    candidates = tuple({"title": i.title, "why": i.why, "reason": r} for i, r in cands)
    return DiscoveryLoopReport(
        started_at=(start.isoformat() if start else ""), ended_at=end_at,
        window_hours=budget.window_hours, ticks=tuple(ticks),
        new_total=new_total, seen_total=seen_total,
        candidates=candidates, stopped_reason=stopped)


__all__ = (
    "PromotionPolicy", "age_hours", "is_fresh", "freshness_label", "candidate_reason",
    "is_candidate", "ask_candidates", "stale_pending",
    "LoopBudget", "LoopTick", "DiscoveryLoopReport",
    "discovery_loop_tick", "run_discovery_loop",
    "DEFAULT_MIN_SCORE", "DEFAULT_MIN_SEEN", "DEFAULT_FRESH_HOURS",
    "DEFAULT_WINDOW_HOURS", "DEFAULT_MAX_TICKS", "DEFAULT_MIN_INTERVAL_MIN",
)
