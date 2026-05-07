"""``yule runtime status`` CLI adapter — A-M6.3.

Glue between argparse and :func:`build_runtime_status`. Kept in a
sibling module so the data builder + renderer in ``status.py``
stay free of CLI / IO concerns and remain unit-testable without
arguments mocking.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from ..agents.job_queue.heartbeat import HeartbeatStore
from ..agents.job_queue.store import JobQueue
from .status import (
    build_runtime_status,
    render_runtime_status_json,
    render_runtime_status_text,
)


def run_runtime_status_command(
    *,
    profile: str = "engineering",
    emit_json: bool = False,
    db_path: Optional[Path] = None,
    failed_limit: int = 10,
) -> int:
    """Build the snapshot and print it. Always read-only.

    Returns 0 on success, 78 (``EX_CONFIG``) when the profile is
    unknown — same convention as ``run-service`` so an operator's
    typo lands a clear failure code.
    """

    queue = JobQueue(db_path=db_path)
    heartbeats = HeartbeatStore(db_path=db_path)
    try:
        report = build_runtime_status(
            profile=profile,
            queue=queue,
            heartbeats=heartbeats,
            failed_limit=max(0, int(failed_limit)),
        )
    except ValueError as exc:
        sys.stderr.write(f"yule runtime status: {exc}\n")
        return 78

    if emit_json:
        sys.stdout.write(render_runtime_status_json(report) + "\n")
    else:
        sys.stdout.write(render_runtime_status_text(report) + "\n")
    return 0


__all__ = ("run_runtime_status_command",)
