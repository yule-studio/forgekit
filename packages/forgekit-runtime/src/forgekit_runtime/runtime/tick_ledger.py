"""Per-tick runtime continuity ledger — provider lane + budget + receipt, tick by tick.

The heartbeat says *whether* the loop is alive; this says *what each tick did with which
provider lane and budget*, durably. One append-only JSONL record per tick:

  tick · ts · provider lane (brain / actual transport / fallback) · executed · blocked ·
  waiting · executed paths (the receipt) · budget snapshot (spent/limit).

So an operator can answer "is my long-running goal still progressing, through which
provider, within budget?" from a durable trail — not a momentary status string. Append is
best-effort (never breaks a tick); reads are bounded. Mirrors the usage/forge ledgers'
append-only JSONL shape (no new persistence engine).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Tuple

from forgekit_config import paths as P

from .provider_lane import TickProviderLane

TICK_LEDGER_NAME = "runtime-tick-ledger.jsonl"


def tick_ledger_path(env: Optional[Mapping[str, str]] = None) -> Path:
    return P.forgekit_home(env) / "state" / TICK_LEDGER_NAME


@dataclass(frozen=True)
class TickRecord:
    tick: int
    ts: str
    lane: dict                         # TickProviderLane.to_dict()
    executed: int = 0
    blocked: int = 0
    waiting: bool = False
    executed_paths: Tuple[str, ...] = field(default_factory=tuple)
    budget: dict = field(default_factory=dict)   # {spent, budget, ratio, over}
    skipped_reason: str = ""

    @property
    def provider_lane(self) -> TickProviderLane:
        return TickProviderLane.from_dict(self.lane)

    def to_dict(self) -> dict:
        return {"tick": self.tick, "ts": self.ts, "lane": self.lane, "executed": self.executed,
                "blocked": self.blocked, "waiting": self.waiting,
                "executed_paths": list(self.executed_paths), "budget": self.budget,
                "skipped_reason": self.skipped_reason}

    @classmethod
    def from_dict(cls, d: dict) -> "TickRecord":
        return cls(tick=int(d.get("tick", 0)), ts=str(d.get("ts", "")),
                   lane=dict(d.get("lane", {})), executed=int(d.get("executed", 0)),
                   blocked=int(d.get("blocked", 0)), waiting=bool(d.get("waiting")),
                   executed_paths=tuple(d.get("executed_paths", ())),
                   budget=dict(d.get("budget", {})), skipped_reason=str(d.get("skipped_reason", "")))

    def line(self) -> str:
        """One operator-facing line for this tick (lane + work + budget)."""

        lane = self.provider_lane
        b = self.budget or {}
        bud = (f" · budget {b.get('spent', 0)}/{b.get('budget', 0) or '∞'}tok"
               + ("(over)" if b.get("over") else ""))
        work = f"exec {self.executed}"
        if self.blocked:
            work += f"/blocked {self.blocked}"
        if self.waiting:
            work += "/waiting"
        return f"  tick {self.tick}: lane {lane.short()} · {work}{bud}"


def append_tick_record(rec: TickRecord, *, env: Optional[Mapping[str, str]] = None,
                       path: Optional[Path] = None) -> bool:
    """Append one tick record (best-effort — a ledger write never breaks a tick)."""

    p = path or tick_ledger_path(env)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def read_tick_records(*, env: Optional[Mapping[str, str]] = None,
                      path: Optional[Path] = None, limit: int = 0) -> Tuple[TickRecord, ...]:
    """Read tick records oldest-first; ``limit`` keeps only the last N (0 = all)."""

    p = path or tick_ledger_path(env)
    try:
        text = Path(p).read_text(encoding="utf-8")
    except OSError:
        return ()
    recs = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(TickRecord.from_dict(json.loads(line)))
        except (ValueError, TypeError):
            continue
    if limit and len(recs) > limit:
        recs = recs[-limit:]
    return tuple(recs)


def latest(env: Optional[Mapping[str, str]] = None,
           path: Optional[Path] = None) -> Optional[TickRecord]:
    recs = read_tick_records(env=env, path=path, limit=1)
    return recs[-1] if recs else None


def tick_ledger_lines(records: Tuple[TickRecord, ...]) -> Tuple[str, ...]:
    """Operator projection — recent per-tick provider-lane + budget continuity. Read-only."""

    if not records:
        return ("runtime tick ledger: (기록 없음 — `forgekit runtime serve` 가 아직 tick 을 안 남김)",)
    last = records[-1]
    lane = last.provider_lane
    lines = ["runtime 진행 연속성 (per-tick provider lane · budget):",
             f"  현재 lane: {lane.label()}"]
    if lane.fallback_used and lane.fallback_chain:
        lines.append(f"  fallback chain: {' → '.join(lane.fallback_chain)}")
    lines.append(f"  최근 {len(records)} tick:")
    lines.extend(r.line() for r in records)
    total_exec = sum(r.executed for r in records)
    lines.append(f"  누적 executed(safe-class) {total_exec} · 마지막 tick {last.tick}")
    return tuple(lines)


__all__ = ("TICK_LEDGER_NAME", "tick_ledger_path", "TickRecord", "append_tick_record",
           "read_tick_records", "latest", "tick_ledger_lines")
