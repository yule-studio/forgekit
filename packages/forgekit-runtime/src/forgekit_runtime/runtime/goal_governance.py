"""Goal ↔ decision-lane governance binding — make the design chain a real execution rule.

Before this module the always-on goal loop was an *execution queue*: the scheduler
decomposed a big goal and linked packets, and the exec tick ran them through the
lightweight ``autopilot.chain`` — the heavy **decision-lane** design artifacts (PM brief →
gateway → tech-lead decision with a ≥2-option stack comparison → specialist briefing) were
fully built (``forgekit_runtime.decision_lane``) but **never consulted**. A long goal could
reach specialist execution with no design artifact at all.

This binds the two: a goal's governance chain is recorded in the SAME append-only
``decision_log`` the ``/council`` surface reads, keyed by ``session_id = goal.id``. The
readiness ladder (no_pm_brief → meeting → decision → handoff → executable) is then derived
from that log and ENFORCED:

- the scheduler records a **PM brief first** (the first artifact a decomposition emits) and
  marks the goal governance-required;
- the exec tick refuses to run a governance-required goal's packets until the chain is
  ``executable`` — *설계 없는 구현 금지*.

Anti-fake: this never fabricates a tech-lead stack comparison or signoff to pass its own
gate — it frames the PM brief from the goal (honest product framing), records whatever
artifacts exist with their real validator verdicts, and BLOCKS until a human/tech-lead
supplies the missing design. The seeded PM brief is intentionally incomplete (no
``user_value``/acceptance/metrics) so readiness honestly stays pending until completed.

Reuses ``decision_lane`` entirely (no new schema). Owner: ``packages/forgekit-runtime/runtime``.
"""

from __future__ import annotations

from typing import Callable, Mapping, Optional, Tuple

# goal evidence marker: this goal's specialist execution is gated on the design chain.
GOV_REQUIRED = "governance"          # Goal.add_evidence kind — design chain mandatory
GOV_PENDING = "governance_pending"   # exec-tick refusal record (design artifact missing)


def _utc(now: Optional[Callable[[], str]]) -> Callable[[], str]:
    if now is not None:
        return now
    from forgekit_goal.models import _utcnow  # the package's real clock
    return _utcnow


# ── governance-required marker ────────────────────────────────────────────────────────
def is_governance_required(goal) -> bool:
    """True when this goal's execution is gated on a completed design chain."""

    return any(e.kind == GOV_REQUIRED for e in goal.evidence)


def mark_governance_required(goal, reason: str = "", *,
                             now: Optional[Callable[[], str]] = None):
    """Idempotently mark a goal governance-required (records a ``governance`` evidence)."""

    if is_governance_required(goal):
        return goal
    return goal.add_evidence(GOV_REQUIRED, reason or "design chain required (PM→tech-lead→specialist)",
                             ref=goal.id, now=_utc(now))


def governance_anchor(goal, by_id: Mapping[str, object]):
    """The goal whose design chain gates *goal*'s execution — its governance-required parent
    if any, else itself. A decomposed feature's design decision lives on the parent; its
    per-area children execute only once the parent chain is executable."""

    parent = by_id.get(goal.parent_id) if goal.parent_id else None
    if parent is not None and is_governance_required(parent):
        return parent
    return goal


# ── PM brief framing (honest, intentionally incomplete until a human completes it) ─────
def frame_pm_brief(goal, *, user_value: str = "", acceptance: Tuple[str, ...] = (),
                   success_metrics: Tuple[str, ...] = (), constraints: Tuple[str, ...] = ()):
    """Seed a :class:`PMBrief` from the goal. topic/problem are honest goal framing; the
    human-judgement fields (user_value / acceptance / success_metrics) default empty so the
    seeded brief is a real-but-incomplete draft — readiness stays pending until completed,
    never faked into validity."""

    from ..decision_lane import PMBrief

    return PMBrief(
        topic=goal.title or goal.id,
        problem=goal.intent or goal.title or "",
        user_value=user_value,
        acceptance_criteria=tuple(acceptance),
        success_metrics=tuple(success_metrics),
        constraints=tuple(constraints),
        requested_by="operator",
    )


# ── record governance artifacts against the goal's session (goal.id) ──────────────────
def record_pm_brief(goal, *, brief=None, env: Optional[Mapping[str, str]] = None,
                    user_value: str = "", acceptance: Tuple[str, ...] = (),
                    success_metrics: Tuple[str, ...] = (), at: str = ""):
    """Record the PM brief as the FIRST governance artifact for this goal (session=goal.id)."""

    from ..decision_lane import record_lane_artifacts

    b = brief if brief is not None else frame_pm_brief(
        goal, user_value=user_value, acceptance=acceptance, success_metrics=success_metrics)
    return record_lane_artifacts(goal.id, brief=b, env=env, at=at)


def record_artifacts(goal_id: str, *, env: Optional[Mapping[str, str]] = None, at: str = "",
                     **artifacts):
    """Thin pass-through to record any decision-lane artifacts (gateway / meeting / decision /
    handoff / briefing / consult) against the goal's governance session. Used by the operator
    surface / tests to ADVANCE the chain — each is validated and recorded with its real verdict."""

    from ..decision_lane import record_lane_artifacts

    return record_lane_artifacts(goal_id, env=env, at=at, **artifacts)


# ── readiness derived from the goal's governance log ──────────────────────────────────
def governance_readiness(goal_or_id, *, env: Optional[Mapping[str, str]] = None):
    """The :class:`LaneReadiness` for a goal, reconstructed from its append-only governance
    log (the same log ``/council`` reads). ``no_pm_brief`` when nothing recorded yet."""

    from ..decision_lane import readiness_from_log, replay_governance_log

    gid = goal_or_id if isinstance(goal_or_id, str) else goal_or_id.id
    events = replay_governance_log(gid, env=env)
    return readiness_from_log(events)


def design_ready(goal_or_id, *, env: Optional[Mapping[str, str]] = None) -> bool:
    """True only when the goal's design chain reached ``executable`` (PM brief + meeting +
    signed tech-lead decision + valid handoff) — the specialist-execution precondition."""

    return governance_readiness(goal_or_id, env=env).executable


def design_gate(goal, by_id: Mapping[str, object], *,
                env: Optional[Mapping[str, str]] = None) -> Tuple[bool, str, str]:
    """Execution gate for *goal*: ``(allowed, stage, reason)``.

    A goal not governance-required (directly or via its governance-required parent) is always
    allowed (backward-compatible). A governance-required goal is allowed ONLY when its anchor's
    design chain is ``executable``; otherwise blocked with the honest next-required artifact."""

    anchor = governance_anchor(goal, by_id)
    if not is_governance_required(anchor):
        return True, "not_required", ""
    r = governance_readiness(anchor, env=env)
    if r.executable:
        return True, r.stage, ""
    return False, r.stage, (r.next_action or "design artifact 필요")


def governance_lines(goal, *, env: Optional[Mapping[str, str]] = None) -> Tuple[str, ...]:
    """`/goal govern <id>` — the goal's design-chain readiness + next required artifact."""

    r = governance_readiness(goal, env=env)
    head = (f"goal governance — {goal.id} · {'설계 완료(실행 가능)' if r.executable else '설계 진행 중'}"
            + ("" if is_governance_required(goal) else "  [dim](governance 비강제 goal)[/dim]"))
    body = list(r.lines())
    body.append("  [dim]전체 트레일: `/council " + goal.id + "` · 기록: PM brief→gateway→tech-lead "
                "decision(스택 ≥2)→handoff[/dim]")
    return (head, *body)


__all__ = (
    "GOV_REQUIRED", "GOV_PENDING",
    "is_governance_required", "mark_governance_required", "governance_anchor",
    "frame_pm_brief", "record_pm_brief", "record_artifacts",
    "governance_readiness", "design_ready", "design_gate", "governance_lines",
)
