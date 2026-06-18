"""Handoff evidence — persist a Handoff as JSON under ``runs/forgekit/handoff/``.

Append-style evidence on disk so a run is reproducible/auditable. Best-effort and
guarded (never raises into the console); returns the written path or ``None``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9가-힣]+", "-", (text or "").lower()).strip("-")
    return s[:48] or "handoff"


def write_handoff_evidence(handoff, runs_root) -> Optional[Path]:
    """Write *handoff* to ``<runs_root>/runs/forgekit/handoff/<slug>.json``. None on failure."""

    try:
        out_dir = Path(runs_root) / "runs" / "forgekit" / "handoff"
        out_dir.mkdir(parents=True, exist_ok=True)
        name = _slug(getattr(handoff, "project", "") or getattr(handoff, "raw_ask", ""))
        path = out_dir / f"{name}.json"
        path.write_text(
            json.dumps(handoff.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path
    except OSError:
        return None


__all__ = ("write_handoff_evidence",)
