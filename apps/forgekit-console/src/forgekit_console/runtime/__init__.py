"""Forgekit runtime — bounded always-on loop + runbook fallback (WT3)."""

from __future__ import annotations

from .loop import (
    AUTONOMY_BOUNDED,
    AUTONOMY_OBSERVE,
    CAT_INFRA,
    CAT_PRODUCT,
    BoundedRuntimeLoop,
    Finding,
    LoopResult,
)
from .daemon import BoundedDaemon, DaemonResult, TickOutcome
from .heartbeat import Heartbeat, read_heartbeat, write_heartbeat
from .runbook import RunbookNote, build_runbook, infer_area

__all__ = (
    "AUTONOMY_BOUNDED",
    "AUTONOMY_OBSERVE",
    "CAT_INFRA",
    "CAT_PRODUCT",
    "BoundedRuntimeLoop",
    "Finding",
    "LoopResult",
    "RunbookNote",
    "build_runbook",
    "infer_area",
)
