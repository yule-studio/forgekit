"""Live /compact canary — measure the deterministic estimator against real tokens.

The compaction core estimates tokens with ``chars/4``. A *canary* run validates
that estimate against the real ``compact_boundary`` the harness reports for a
live ``/compact``: it always computes the deterministic estimate (so the
fallback path is never lost) and, when the live flag is on and a compact
function is available, also captures the live boundary and reports the
**estimate-vs-live error**. This is the operational evidence that the estimator
is (or isn't) trustworthy.

Flag on/off is explicit:
  * flag off (default) → estimate mode. Deterministic. No live call.
  * flag on + compact_fn available → live mode. Live tokens are authoritative;
    estimate is still reported alongside for drift.
  * flag on but live unavailable / unparseable → graceful fallback to estimate
    mode with a warning (deterministic fallback preserved).

Never raises into the caller — a live failure degrades to estimate + warning.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence, Tuple

from .context_compaction import CompactionNote, CompactionTurn, build_compaction_summary
from .compaction_protocol import Checkpoint, CompactionReceipt, run_compaction_to_vault

ENV_CANARY = "YULE_COMPACT_LIVE_CANARY_ENABLED"

# A compact function returns a duck-typed CompactBoundary (.parsed / .pre_tokens
# / .post_tokens / .warning). ClaudeCodeRunner.compact is the production one.
CompactFn = Callable[[Optional[str]], Any]


def canary_enabled(env: Optional[dict] = None) -> bool:
    env_map = env if env is not None else os.environ
    return (env_map.get(ENV_CANARY) or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class CompactCanaryReport:
    session_id: str
    focus: Optional[str]
    mode: str  # "live" | "estimate"
    estimate_pre: int
    estimate_post: int
    live_pre: Optional[int]
    live_post: Optional[int]
    estimate_error_pct: Optional[float]  # |estimate_pre - live_pre| / live_pre
    authoritative_source: str  # estimate | live_compact_boundary
    task_log_note_path: Optional[str]
    committed: bool
    warnings: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "focus": self.focus,
            "mode": self.mode,
            "estimate_pre": self.estimate_pre,
            "estimate_post": self.estimate_post,
            "live_pre": self.live_pre,
            "live_post": self.live_post,
            "estimate_error_pct": self.estimate_error_pct,
            "authoritative_source": self.authoritative_source,
            "task_log_note_path": self.task_log_note_path,
            "committed": self.committed,
            "warnings": list(self.warnings),
        }

    def render(self) -> str:
        lines = [
            f"compact canary — session {self.session_id} ({self.mode} mode)",
            f"- estimate: pre={self.estimate_pre} post={self.estimate_post} (chars/4)",
        ]
        if self.mode == "live":
            lines.append(f"- live: pre={self.live_pre} post={self.live_post} (compact_boundary)")
            lines.append(f"- estimate error vs live: {self.estimate_error_pct}%")
        lines.append(f"- authoritative: {self.authoritative_source}")
        lines.append(f"- note: {self.task_log_note_path or '-'} (committed={self.committed})")
        for w in self.warnings:
            lines.append(f"- warning: {w}")
        return "\n".join(lines) + "\n"


def run_compact_canary(
    turns: Sequence[CompactionTurn],
    *,
    session_id: str,
    vault_root: Path,
    project: str,
    focus: Optional[str] = None,
    checkpoint: Optional[Checkpoint] = None,
    created_at: Optional[datetime] = None,
    issue: Optional[int] = None,
    original_prompt: Optional[str] = None,
    compact_fn: Optional[CompactFn] = None,
    enabled: Optional[bool] = None,
) -> Tuple[Optional[CompactionNote], CompactionReceipt, CompactCanaryReport]:
    """Run the canary: deterministic estimate + (optional) live compact_boundary.

    Returns ``(note, receipt, report)``. The vault note is written by the
    deterministic core (working tree only, never commits). When live tokens are
    captured they are authoritative in the receipt; the report always carries
    the estimate too so drift is visible.
    """

    flag_on = canary_enabled() if enabled is None else enabled
    warnings: List[str] = []

    # Always compute the deterministic estimate first — the fallback baseline.
    summary = build_compaction_summary(turns, session_id=session_id, focus=focus)
    estimate_pre, estimate_post = summary.pre_tokens, summary.post_tokens

    boundary = None
    if flag_on and compact_fn is not None:
        try:
            boundary = compact_fn(focus)
        except Exception as exc:  # noqa: BLE001 - live must never break the canary
            warnings.append(f"live /compact raised: {type(exc).__name__} — using estimate")
            boundary = None
    elif flag_on and compact_fn is None:
        warnings.append("canary enabled but no compact_fn — using estimate")

    note, receipt = run_compaction_to_vault(
        turns,
        session_id=session_id,
        vault_root=vault_root,
        project=project,
        focus=focus,
        checkpoint=checkpoint,
        created_at=created_at,
        issue=issue,
        original_prompt=original_prompt,
        enabled=flag_on,
        compact_boundary=boundary,
    )

    live_mode = receipt.token_source == "live_compact_boundary"
    live_pre = receipt.pre_tokens if live_mode else None
    live_post = receipt.post_tokens if live_mode else None
    error_pct: Optional[float] = None
    if live_mode and live_pre:
        error_pct = round(abs(estimate_pre - live_pre) / max(1, live_pre) * 100.0, 1)

    report = CompactCanaryReport(
        session_id=session_id,
        focus=focus,
        mode="live" if live_mode else "estimate",
        estimate_pre=estimate_pre,
        estimate_post=estimate_post,
        live_pre=live_pre,
        live_post=live_post,
        estimate_error_pct=error_pct,
        authoritative_source=receipt.token_source,
        task_log_note_path=receipt.task_log_note_path,
        committed=receipt.committed,
        warnings=tuple(warnings) + tuple(receipt.warnings),
    )
    return note, receipt, report


def default_compact_fn() -> CompactFn:
    """Production compact function backed by ClaudeCodeRunner (lazy import)."""

    def _fn(focus: Optional[str]) -> Any:
        from ..runners.claude_code import ClaudeCodeRunner

        return ClaudeCodeRunner().compact(focus=focus)

    return _fn


__all__ = (
    "ENV_CANARY",
    "CompactCanaryReport",
    "canary_enabled",
    "run_compact_canary",
    "default_compact_fn",
)
