"""Background research refresh planner — pure decision layer.

Master plan §6.1 (Background Knowledge Loop) says role-based sources must
keep refreshing on their own cadence so that knowledge sits in the vault
*before* a Discord request lands. This module owns the "which sources are
due right now?" decision:

  * :class:`SourceRefreshState` — what we know about a source's last
    successful (or failed) refresh attempt.
  * :class:`RefreshPlanEntry` — one decision row (``due`` / ``skipped`` /
    ``backoff``) the orchestrator can act on.
  * :class:`RefreshPlan` — the per-role bundle returned by the planner.
  * :func:`compute_refresh_plan` — pure function: given the current time
    + the per-source state map, return what the next ingestion tick
    should attempt.
  * :func:`record_refresh_outcome` — small immutable helper to update a
    state row after the orchestrator actually called the adapter.

Strict no-I/O. The actual scheduler loop (cron / Celery beat / runtime
spawn) lives outside this package — this module just answers "what now?"
deterministically so tests can pin the behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from .models import SourceEntry
from .source_registry import role_sources


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """ISO-8601 ('YYYY-MM-DDTHH:MM:SSZ' or with offset) → aware datetime."""

    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# State row
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceRefreshState:
    """Persisted state for one source between refresh ticks.

    The orchestrator owns persistence (sqlite / vault sidecar / in-memory
    map). This module never reads or writes — callers pass the map in
    and apply the returned mutations.

    ``last_status``:

      * ``"never"`` — source has never been attempted (the default for a
        freshly-seeded entry; planner treats it as immediately due).
      * ``"success"`` — last attempt produced items (may be 0 — the
        adapter just finished without raising).
      * ``"failure"`` — last attempt raised. ``consecutive_failures`` is
        incremented and the planner applies an exponential backoff
        before next retry.
    """

    source_id: str
    last_attempted_at: Optional[str] = None
    last_succeeded_at: Optional[str] = None
    last_status: str = "never"
    consecutive_failures: int = 0
    items_collected_last_run: int = 0
    notes: str = ""

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "source_id": self.source_id,
            "last_attempted_at": self.last_attempted_at,
            "last_succeeded_at": self.last_succeeded_at,
            "last_status": self.last_status,
            "consecutive_failures": int(self.consecutive_failures),
            "items_collected_last_run": int(self.items_collected_last_run),
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Plan entries
# ---------------------------------------------------------------------------


_DECISION_DUE = "due"
_DECISION_SKIPPED_FRESH = "skipped_fresh"
_DECISION_SKIPPED_BACKOFF = "skipped_backoff"
_DECISION_SKIPPED_REVIEW_REQUIRED = "skipped_review_required"
_DECISION_SKIPPED_AUTO_COLLECT_DISABLED = "skipped_auto_collect_disabled"


@dataclass(frozen=True)
class RefreshPlanEntry:
    source_id: str
    decision: str
    reason: str
    next_eligible_at: Optional[str] = None

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "source_id": self.source_id,
            "decision": self.decision,
            "reason": self.reason,
            "next_eligible_at": self.next_eligible_at,
        }


@dataclass(frozen=True)
class RefreshPlan:
    role: str
    now_iso: str
    due: Tuple[RefreshPlanEntry, ...]
    skipped: Tuple[RefreshPlanEntry, ...]
    tick_quota: int

    def due_source_ids(self) -> Tuple[str, ...]:
        return tuple(entry.source_id for entry in self.due)

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "role": self.role,
            "now": self.now_iso,
            "tick_quota": int(self.tick_quota),
            "due": [entry.to_payload() for entry in self.due],
            "skipped": [entry.to_payload() for entry in self.skipped],
        }


# ---------------------------------------------------------------------------
# Backoff
# ---------------------------------------------------------------------------


# Exponential: 1× / 2× / 4× / 8× of the source interval, capped at 24h.
_BACKOFF_MULTIPLIERS = (1, 2, 4, 8)
_BACKOFF_CAP_MINUTES = 24 * 60


def _backoff_delay_minutes(
    interval_minutes: int, consecutive_failures: int
) -> int:
    """Increase wait between retries the more we've failed in a row.

    0 failures returns the base interval; each subsequent failure
    multiplies up the chain until the cap. Anchored to *interval* so
    fast-cadence feeds (security advisories) recover quicker than slow
    ones (docs sitemaps).
    """

    if consecutive_failures <= 0:
        return interval_minutes
    multiplier = _BACKOFF_MULTIPLIERS[
        min(consecutive_failures - 1, len(_BACKOFF_MULTIPLIERS) - 1)
    ]
    return min(interval_minutes * multiplier, _BACKOFF_CAP_MINUTES)


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


def compute_refresh_plan(
    role_id: str,
    *,
    now: Optional[datetime] = None,
    states: Mapping[str, SourceRefreshState] = (),  # type: ignore[assignment]
    tick_quota: Optional[int] = None,
    include_review_required: bool = False,
    include_auto_collect_disabled: bool = False,
) -> RefreshPlan:
    """Decide which sources are due for *role_id* right now.

    Behaviour:

      1. Walk the role's full source list (per-role + common-core).
      2. For each entry, look up its :class:`SourceRefreshState`. A
         missing entry is treated as ``never`` — immediately due.
      3. Compute ``next_eligible_at = last_attempted_at + interval``,
         where ``interval`` includes exponential backoff for prior
         failures.
      4. ``review_required`` and ``auto_collect=False`` sources are
         skipped by default (operator can opt in via the kwargs).
      5. Apply *tick_quota* (default = role's daily limit) — once the
         number of due sources exceeds the quota, the rest are
         re-classified as ``skipped(reason=fresh-quota)`` so a single
         tick never floods the adapter layer.

    Pure function. Output is stable for fixed inputs — handy for tests.
    """

    now_dt = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
    now_iso = _format_iso(now_dt)

    sources = role_sources(role_id)
    state_map: Mapping[str, SourceRefreshState] = dict(states or {})

    due: List[RefreshPlanEntry] = []
    skipped: List[RefreshPlanEntry] = []

    for source in sources:
        if not source.auto_collect and not include_auto_collect_disabled:
            skipped.append(
                RefreshPlanEntry(
                    source_id=source.source_id,
                    decision=_DECISION_SKIPPED_AUTO_COLLECT_DISABLED,
                    reason="auto_collect=False",
                )
            )
            continue
        if source.review_required and not include_review_required:
            skipped.append(
                RefreshPlanEntry(
                    source_id=source.source_id,
                    decision=_DECISION_SKIPPED_REVIEW_REQUIRED,
                    reason="review_required=True",
                )
            )
            continue

        state = state_map.get(source.source_id) or SourceRefreshState(
            source_id=source.source_id
        )
        interval_minutes = source.effective_refresh_interval_minutes()
        delay_minutes = _backoff_delay_minutes(
            interval_minutes, state.consecutive_failures
        )

        last_dt = _parse_iso(state.last_attempted_at)
        if last_dt is None:
            # Never attempted — immediately due.
            due.append(
                RefreshPlanEntry(
                    source_id=source.source_id,
                    decision=_DECISION_DUE,
                    reason="never_attempted",
                )
            )
            continue

        next_eligible_at = last_dt + timedelta(minutes=delay_minutes)
        if now_dt >= next_eligible_at:
            reason = (
                "due"
                if state.last_status == "success"
                else f"retry_after_{state.consecutive_failures}_failures"
            )
            due.append(
                RefreshPlanEntry(
                    source_id=source.source_id,
                    decision=_DECISION_DUE,
                    reason=reason,
                    next_eligible_at=_format_iso(next_eligible_at),
                )
            )
        else:
            decision = (
                _DECISION_SKIPPED_BACKOFF
                if state.consecutive_failures > 0
                else _DECISION_SKIPPED_FRESH
            )
            skipped.append(
                RefreshPlanEntry(
                    source_id=source.source_id,
                    decision=decision,
                    reason=(
                        f"interval_{interval_minutes}m_backoff_x"
                        f"{_BACKOFF_MULTIPLIERS[min(state.consecutive_failures - 1, len(_BACKOFF_MULTIPLIERS) - 1)] if state.consecutive_failures > 0 else 1}"
                    ),
                    next_eligible_at=_format_iso(next_eligible_at),
                )
            )

    # Apply tick quota — protects the adapter layer from a thundering
    # herd when many sources happen to be due in the same tick.
    quota = tick_quota if tick_quota is not None else _default_tick_quota(role_id)
    if quota >= 0 and len(due) > quota:
        kept, overflow = due[:quota], due[quota:]
        skipped.extend(
            RefreshPlanEntry(
                source_id=entry.source_id,
                decision="skipped_quota",
                reason=f"tick_quota_{quota}_exceeded",
                next_eligible_at=entry.next_eligible_at,
            )
            for entry in overflow
        )
        due = kept

    return RefreshPlan(
        role=role_id,
        now_iso=now_iso,
        due=tuple(due),
        skipped=tuple(skipped),
        tick_quota=quota,
    )


def _default_tick_quota(role_id: str) -> int:
    """Default sources per single tick = role daily limit (5).

    Reusing the daily limit means at most ``daily_limit`` adapter calls
    fire per tick per role even if every source happens to be due. The
    operator can override via *tick_quota* on :func:`compute_refresh_plan`.
    """

    from .source_registry import daily_limit_for_role

    return daily_limit_for_role(role_id)


# ---------------------------------------------------------------------------
# Outcome recording (immutable update)
# ---------------------------------------------------------------------------


def record_refresh_outcome(
    state: SourceRefreshState,
    *,
    now: Optional[datetime] = None,
    success: bool,
    items_collected: int = 0,
    notes: str = "",
) -> SourceRefreshState:
    """Return a new state row reflecting the outcome of this attempt.

    Pure — never mutates *state*. The orchestrator should swap the row
    in its persistence layer with the returned value.
    """

    attempted_iso = _format_iso(now or datetime.now(tz=timezone.utc))
    if success:
        return replace(
            state,
            last_attempted_at=attempted_iso,
            last_succeeded_at=attempted_iso,
            last_status="success",
            consecutive_failures=0,
            items_collected_last_run=int(items_collected),
            notes=notes,
        )
    return replace(
        state,
        last_attempted_at=attempted_iso,
        last_status="failure",
        consecutive_failures=int(state.consecutive_failures) + 1,
        items_collected_last_run=0,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Per-role multi-tick coverage check (operational guard)
# ---------------------------------------------------------------------------


def overdue_axes_for_role(
    role_id: str,
    *,
    states: Mapping[str, SourceRefreshState],
    now: Optional[datetime] = None,
    grace_factor: float = 2.0,
) -> Tuple[str, ...]:
    """Axes whose every source is past ``interval × grace_factor`` overdue.

    Used as a "we're failing this axis entirely" guard — the orchestrator
    can surface this on the operator dashboard so a long-broken adapter
    doesn't hide a missing knowledge axis. Returns axis values (string
    form) sorted for determinism.
    """

    now_dt = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
    state_map = dict(states or {})

    sources = role_sources(role_id)
    axis_health: dict[str, bool] = {}
    for source in sources:
        if not source.axes:
            continue
        state = state_map.get(source.source_id)
        last_succeeded = (
            _parse_iso(state.last_succeeded_at) if state else None
        )
        threshold_minutes = (
            source.effective_refresh_interval_minutes() * max(grace_factor, 1.0)
        )
        is_healthy = (
            last_succeeded is not None
            and now_dt - last_succeeded
            <= timedelta(minutes=threshold_minutes)
        )
        for axis in source.axes:
            key = axis.value
            if axis_health.get(key) is True:
                continue
            axis_health[key] = is_healthy or axis_health.get(key, False)

    overdue = [axis for axis, healthy in axis_health.items() if not healthy]
    return tuple(sorted(overdue))


__all__ = [
    "RefreshPlan",
    "RefreshPlanEntry",
    "SourceRefreshState",
    "compute_refresh_plan",
    "overdue_axes_for_role",
    "record_refresh_outcome",
]
