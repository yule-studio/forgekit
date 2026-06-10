from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence
from uuid import uuid4

from ..storage import save_json_cache

RUNTIME_METRIC_NAMESPACE = "runtime-metrics"
RUNTIME_METRIC_PROVIDER = "yule-orchestrator"
RUNTIME_METRIC_TTL_SECONDS = 30 * 24 * 60 * 60


@dataclass(frozen=True)
class RuntimeStepMetric:
    name: str
    duration_seconds: float
    ok: bool
    started_at: str
    ended_at: str
    metadata: Mapping[str, Any]
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "duration_seconds": round(self.duration_seconds, 3),
            "ok": self.ok,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "metadata": dict(self.metadata),
        }
        if self.error:
            payload["error"] = self.error
        return payload


def save_runtime_metric_run(
    *,
    workflow: str,
    started_at: datetime,
    ended_at: datetime,
    steps: Sequence[RuntimeStepMetric],
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    run_id = f"{workflow}:{started_at.strftime('%Y%m%dT%H%M%S')}:{uuid4().hex[:8]}"
    payload = {
        "run_id": run_id,
        "workflow": workflow,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "total_seconds": round(max(0.0, (ended_at - started_at).total_seconds()), 3),
        "steps": [step.to_dict() for step in steps],
        "metadata": dict(metadata or {}),
    }

    try:
        save_json_cache(
            namespace=RUNTIME_METRIC_NAMESPACE,
            cache_key=run_id,
            provider=RUNTIME_METRIC_PROVIDER,
            range_start=str((metadata or {}).get("plan_date") or ""),
            range_end=str((metadata or {}).get("plan_date") or ""),
            scope_hash=workflow,
            ttl_seconds=RUNTIME_METRIC_TTL_SECONDS,
            payload=payload,
            metadata={"workflow": workflow},
        )
    except Exception:
        pass

    return payload
