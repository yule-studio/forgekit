"""Token usage ledger (WT2) — append-only JSONL, the SSoT for usage/cost/budget.

One submit (or other usage event) = one JSON line appended to
``$FORGEKIT_HOME/state/usage-ledger.jsonl``. Reports/rollups read it; it is never the
display format itself. Token numbers carry a ``usage_basis`` (live / estimate / proxy
/ unknown) so live and estimated usage are NEVER mixed in a report. Pure stdlib
(json/os/datetime) so it runs in a bare CI install; ts is injectable for tests.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Tuple

from ..runtime_paths import state_dir

# usage_basis mirrors chat.models (live / estimate / proxy / unknown)
BASIS_LIVE = "live"
BASIS_ESTIMATE = "estimate"
BASIS_PROXY = "proxy"
BASIS_UNKNOWN = "unknown"

# event kinds
KIND_SUBMIT = "submit"
KIND_RECALL = "recall"
KIND_EVAL = "eval"
KIND_BENCHMARK = "benchmark"


def usage_ledger_path(env: Optional[Mapping[str, str]] = None) -> Path:
    return state_dir(env) / "usage-ledger.jsonl"


@dataclass(frozen=True)
class UsageEvent:
    ts: str
    session_id: str = ""
    task_id: str = ""
    kind: str = KIND_SUBMIT
    mode: str = ""               # forgekit runtime mode
    provider: str = ""
    model: str = ""
    category: str = ""           # SubmitResult category (ok / held / ...)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: Optional[float] = None   # None = no price proxy (honest)
    usage_basis: str = BASIS_UNKNOWN
    success: bool = True
    fallback: bool = False
    throttled: bool = False

    def to_row(self) -> dict:
        return {
            "ts": self.ts, "session_id": self.session_id, "task_id": self.task_id,
            "kind": self.kind, "mode": self.mode, "provider": self.provider,
            "model": self.model, "category": self.category,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens, "cost_usd": self.cost_usd,
            "usage_basis": self.usage_basis, "success": self.success,
            "fallback": self.fallback, "throttled": self.throttled,
        }


def append_event(event: UsageEvent, *, path: Optional[Path] = None,
                 env: Optional[Mapping[str, str]] = None) -> bool:
    """Append one event as a JSONL line. Best-effort (never raises)."""

    p = path or usage_ledger_path(env)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_row(), ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def read_events(path: Optional[Path] = None, *, env: Optional[Mapping[str, str]] = None,
                day: str = "") -> Tuple[dict, ...]:
    """Read all ledger rows (oldest-first). ``day`` (YYYY-MM-DD) filters by ts prefix."""

    p = path or usage_ledger_path(env)
    try:
        text = Path(p).read_text(encoding="utf-8")
    except OSError:
        return ()
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if day and not str(row.get("ts", "")).startswith(day):
            continue
        rows.append(row)
    return tuple(rows)


def now_ts(env: Optional[Mapping[str, str]] = None) -> str:
    """Current timestamp ISO (date+time). Real runtime clock (not a workflow script)."""

    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


def today(env: Optional[Mapping[str, str]] = None) -> str:
    from datetime import date

    return date.today().isoformat()


def new_session_id() -> str:
    import uuid

    return uuid.uuid4().hex[:12]


__all__ = (
    "BASIS_LIVE", "BASIS_ESTIMATE", "BASIS_PROXY", "BASIS_UNKNOWN",
    "KIND_SUBMIT", "KIND_RECALL", "KIND_EVAL", "KIND_BENCHMARK",
    "usage_ledger_path", "UsageEvent", "append_event", "read_events",
    "now_ts", "today", "new_session_id",
)
