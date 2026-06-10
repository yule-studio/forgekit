"""Runtime status — operator action surface (Round 4 마무리).

Extracted from :mod:`runtime.status` (split axis ``operator_actions``).

Round 4 후속까지는 "지금 큐가 어떤 상태인가" 까지만 surface 되어 있었다.
운영자가 그 상태에서 "그래서 내가 뭘 해야 하지?" 를 결정하려면 warnings
텍스트를 한 번 더 파싱해야 했다. 본 라운드는 그 결정을 코드 측에서 한 번
정렬해서, 텍스트/마크다운/JSON 모두 동일한 "operator action" 항목을 보고
동일한 다음 단계를 도출할 수 있게 만든다. 분류는 1:1 mapping:

  * needs_approval (1+ session) → `#승인-대기` reply
  * blocked (1+ session) → 사유 점검 + 수동 재진입/세션 종료 결정
  * stalled_discussion → discussion follow-up worker 가 못 따라잡은 경우
    (현재는 needs_approval 보다 약한 신호로 surface; producer 가 producer
    tick 마다 재시도하므로 대기 가능)
  * failed_ci → coding_execute 가 retry_ready 로 큐에 다시 들어간 경우
    (CI orchestrator 가 잡고 있으나 30분 안에 동일 사유 반복 시 수동 점검)
  * lock_contention → 같은 scope 의 lock 이 ticks 에 걸쳐 잡혀있는 경우
  * stale_service / unknown_service / circuit_open / failed_terminal_jobs
    → 기존 warning 라인의 운영자 명령을 OperatorAction 으로 정렬

모든 액션은 "high" / "medium" / "low" 세 단계만 사용한다 — Discord 포스트
헤더의 우선순위 정렬에 쓰이는 만큼 더 잘게 쪼개봐야 운영자 인지에 도움이
안 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple

from .status import (
    AUTONOMY_OUTCOME_LOCKED,
    HEALTH_ALIVE,
    HEALTH_CIRCUIT_OPEN,
    HEALTH_GRACEFUL_DISABLED,
    HEALTH_STALE,
    HEALTH_UNKNOWN,
    RuntimeStatusReport,
)


OPERATOR_ACTION_HIGH: str = "high"
OPERATOR_ACTION_MEDIUM: str = "medium"
OPERATOR_ACTION_LOW: str = "low"


# Stable kind keys — used by the JSON renderer + the markdown poster
# so a downstream consumer can route on action kind without parsing
# the headline string.
ACTION_KIND_NEEDS_APPROVAL: str = "needs_approval"
ACTION_KIND_BLOCKED: str = "blocked"
ACTION_KIND_RETRY_READY_BACKLOG: str = "retry_ready_backlog"
ACTION_KIND_LOCK_CONTENTION: str = "lock_contention"
ACTION_KIND_AUTONOMY_ERROR: str = "autonomy_error"
ACTION_KIND_STALE_SERVICE: str = "stale_service"
ACTION_KIND_UNKNOWN_SERVICE: str = "unknown_service"
ACTION_KIND_CIRCUIT_OPEN: str = "circuit_open"
ACTION_KIND_FAILED_TERMINAL: str = "failed_terminal_jobs"
# P0-T — graceful-disable 와 unknown 을 operator hint 측에서도 구분.
ACTION_KIND_GRACEFUL_DISABLED: str = "graceful_disabled_service"


@dataclass(frozen=True)
class OperatorAction:
    """One actionable item the operator should resolve.

    *kind* is a stable identifier (one of the ``ACTION_KIND_*``
    constants) so a Discord poster / dashboard can route on it
    without parsing the human-readable headline.

    *severity* is one of ``high`` / ``medium`` / ``low`` — the
    compact renderer sorts ``high`` first.

    *next_step* must be a copy-pasteable command or a literal
    Discord reply string when applicable; the operator should not
    need to read any other doc to act on a single action row.
    """

    kind: str
    severity: str
    headline: str
    next_step: str
    affected: Tuple[str, ...] = ()
    icon: str = ""

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "headline": self.headline,
            "next_step": self.next_step,
            "affected": list(self.affected),
            "icon": self.icon,
        }


_SEVERITY_ORDER: Mapping[str, int] = {
    OPERATOR_ACTION_HIGH: 0,
    OPERATOR_ACTION_MEDIUM: 1,
    OPERATOR_ACTION_LOW: 2,
}


def summarize_operator_actions(
    report: "RuntimeStatusReport",
) -> Tuple[OperatorAction, ...]:
    """Project *report* into the actions an operator should resolve.

    Pure read — no side effects. Sorted ``high`` → ``low`` so a
    truncated render still surfaces the urgent items. Returns an
    empty tuple when nothing operator-actionable is going on (used
    by the compact view to render the green "all clear" line).
    """

    actions: list[OperatorAction] = []

    # --- circuit_open: supervisor stopped restarting on purpose.
    circuit_open = [
        s for s in report.services if s.health == HEALTH_CIRCUIT_OPEN
    ]
    if circuit_open:
        ids = tuple(s.service_id for s in circuit_open)
        first = ids[0]
        actions.append(
            OperatorAction(
                kind=ACTION_KIND_CIRCUIT_OPEN,
                severity=OPERATOR_ACTION_HIGH,
                headline=(
                    f"circuit OPEN — {len(ids)} service(s) won't auto-restart"
                ),
                next_step=f"yule runtime circuit reset {first}",
                affected=ids,
                icon="🛑",
            )
        )

    # --- stale services: was alive, went quiet.
    stale = [s for s in report.services if s.health == HEALTH_STALE]
    if stale:
        ids = tuple(s.service_id for s in stale)
        first = ids[0]
        actions.append(
            OperatorAction(
                kind=ACTION_KIND_STALE_SERVICE,
                severity=OPERATOR_ACTION_HIGH,
                headline=(
                    f"{len(ids)} service(s) stale — heartbeat past deadline"
                ),
                next_step=(
                    f"yule run-service {first}  # or: systemctl restart "
                    f"yule-run-service@{first}.service"
                ),
                affected=ids,
                icon="💤",
            )
        )

    # --- unknown implemented services: never started.
    unknown = [
        s for s in report.services if s.health == HEALTH_UNKNOWN and s.implemented
    ]
    if unknown:
        ids = tuple(s.service_id for s in unknown)
        first = ids[0]
        actions.append(
            OperatorAction(
                kind=ACTION_KIND_UNKNOWN_SERVICE,
                severity=OPERATOR_ACTION_HIGH,
                headline=(
                    f"{len(ids)} service(s) never reported a heartbeat "
                    "— worker likely never started"
                ),
                next_step=(
                    f"yule runtime up  # or single: yule run-service {first}"
                ),
                affected=ids,
                icon="❓",
            )
        )

    # P0-T — graceful-disabled services: token / env 가 비어있는 명시적
    # 비활성 상태. UNKNOWN 과 다르게 "restart 한다고 해결 안 됨" 으로
    # operator hint 분리. env_key 가 metadata 에 있으면 그걸 가리킴.
    disabled = [
        s
        for s in report.services
        if s.health == HEALTH_GRACEFUL_DISABLED and s.implemented
    ]
    if disabled:
        ids = tuple(s.service_id for s in disabled)
        env_keys = sorted(
            {
                str(s.metadata.get("env_key") or "")
                for s in disabled
                if isinstance(s.metadata, Mapping)
                and s.metadata.get("env_key")
            }
        )
        env_hint = (
            f" (env: {', '.join(env_keys)})" if env_keys else ""
        )
        actions.append(
            OperatorAction(
                kind=ACTION_KIND_GRACEFUL_DISABLED,
                severity=OPERATOR_ACTION_LOW,
                headline=(
                    f"{len(ids)} service(s) graceful-disabled "
                    "— operator chose to keep these offline"
                ),
                next_step=(
                    "토큰을 .env.local 에 추가한 뒤 `yule runtime up` 으로 "
                    "다시 올리세요" + env_hint
                ),
                affected=ids,
                icon="🔌",
            )
        )

    # --- failed_terminal jobs: no auto-retry.
    failed_terminal = [
        f for f in report.failed_recent if f.state == "failed_terminal"
    ]
    if failed_terminal:
        ids = tuple(f.job_id for f in failed_terminal)
        actions.append(
            OperatorAction(
                kind=ACTION_KIND_FAILED_TERMINAL,
                severity=OPERATOR_ACTION_HIGH,
                headline=(
                    f"{len(ids)} job(s) in failed_terminal — manual review only"
                ),
                next_step=(
                    "yule runtime status --json  # inspect result_json, then "
                    "requeue or close the session"
                ),
                affected=ids,
                icon="🧨",
            )
        )

    # --- needs_approval funnel rows — operator reply on `#승인-대기`.
    needs_approval = [
        c
        for c in report.completion_funnel_recent
        if c.completion_status == "needs_approval"
    ]
    if needs_approval:
        ids = tuple(c.session_id or c.job_id for c in needs_approval)
        actions.append(
            OperatorAction(
                kind=ACTION_KIND_NEEDS_APPROVAL,
                severity=OPERATOR_ACTION_HIGH,
                headline=(
                    f"{len(ids)} session(s) waiting on `#승인-대기` reply"
                ),
                next_step=(
                    "Reply `이대로 저장` (또는 해당 카드의 승인 버튼) in "
                    "`#승인-대기` to advance"
                ),
                affected=ids,
                icon="🙋",
            )
        )

    # --- blocked funnel rows — manual decision required.
    blocked = [
        c
        for c in report.completion_funnel_recent
        if c.completion_status == "blocked"
    ]
    if blocked:
        ids = tuple(c.session_id or c.job_id for c in blocked)
        # Surface up to two reasons inline so the operator can spot
        # whether everything blocked on the same root (e.g. all
        # `protected_branch_blocked`).
        reasons = sorted({c.reason for c in blocked if c.reason})[:2]
        why = f" — reason(s): {', '.join(reasons)}" if reasons else ""
        actions.append(
            OperatorAction(
                kind=ACTION_KIND_BLOCKED,
                severity=OPERATOR_ACTION_HIGH,
                headline=(
                    f"{len(ids)} session(s) blocked — autonomy will not retry"
                    f"{why}"
                ),
                next_step=(
                    "Inspect `coding_execute_progress` in session.extra; "
                    "manually requeue or close the session."
                ),
                affected=ids,
                icon="⛔",
            )
        )

    # --- autonomy producer error — supervisor logs needed.
    errored_ticks = [t for t in report.autonomy_recent if t.error]
    if errored_ticks:
        ids = tuple(t.tick_id for t in errored_ticks)
        actions.append(
            OperatorAction(
                kind=ACTION_KIND_AUTONOMY_ERROR,
                severity=OPERATOR_ACTION_MEDIUM,
                headline=(
                    f"{len(ids)} autonomy tick(s) errored — runtime may be "
                    "falling behind"
                ),
                next_step=(
                    "journalctl -u yule.target  # search for "
                    "`autonomy producer` traceback near the tick id"
                ),
                affected=ids,
                icon="⚠️",
            )
        )

    # --- persistent lock contention — usually transient; medium severity.
    locked_dispatches: list[str] = []
    for tick in report.autonomy_recent:
        for d in tick.dispatches:
            if d.outcome == AUTONOMY_OUTCOME_LOCKED:
                locked_dispatches.append(
                    d.session_id or d.executor_role or d.branch_hint or d.source
                )
    if locked_dispatches:
        unique_ids = tuple(sorted(set(locked_dispatches)))
        actions.append(
            OperatorAction(
                kind=ACTION_KIND_LOCK_CONTENTION,
                severity=OPERATOR_ACTION_MEDIUM,
                headline=(
                    f"{len(locked_dispatches)} dispatch(es) blocked on locks "
                    f"({len(unique_ids)} scope(s))"
                ),
                next_step=(
                    "Usually clears within 1-2 ticks. If it persists, "
                    "`systemctl restart "
                    "yule-run-service@eng-supervisor-watch.service` to drop "
                    "the in-memory lock registry."
                ),
                affected=unique_ids,
                icon="🔒",
            )
        )

    # --- retry_ready backlog (low severity — informational).
    # A handful is fine, but a session that keeps landing on
    # retry_ready hints at a CI loop the operator may want to look at.
    retry_ready = [
        c
        for c in report.completion_funnel_recent
        if c.completion_status == "retry_ready"
    ]
    if len(retry_ready) >= 3:
        ids = tuple(c.session_id or c.job_id for c in retry_ready)
        actions.append(
            OperatorAction(
                kind=ACTION_KIND_RETRY_READY_BACKLOG,
                severity=OPERATOR_ACTION_LOW,
                headline=(
                    f"{len(ids)} retry_ready completions in recent funnel — "
                    "CI may be flapping"
                ),
                next_step=(
                    "Inspect the failing PR (gh pr checks <pr>) and decide "
                    "whether to keep auto-retrying or close the session."
                ),
                affected=ids,
                icon="🔁",
            )
        )

    actions.sort(key=lambda a: _SEVERITY_ORDER.get(a.severity, 99))
    return tuple(actions)


# ---------------------------------------------------------------------------
# Compact summary — short helper for journal logs / Discord top-line.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompactStatusSummary:
    """One-line counters + the top operator action.

    The full :class:`RuntimeStatusReport` stays the source of truth;
    this projection is the "what would I tweet about the runtime
    right now" view. Used by the supervisor's compact log line and
    by ``yule runtime status --compact`` (future CLI).
    """

    profile: str
    services_alive: int
    services_stale: int
    services_unknown: int
    services_circuit_open: int
    queue_in_progress: int
    queue_failed_terminal: int
    queue_failed_retryable: int
    autonomy_ticks_recent: int
    autonomy_ticks_errored: int
    autonomy_locked_dispatches: int
    funnel_done: int
    funnel_retry_ready: int
    funnel_needs_approval: int
    funnel_blocked: int
    top_action: Optional[OperatorAction]
    actions_total: int

    def is_clean(self) -> bool:
        """True when there is nothing operator-actionable to do."""

        return self.top_action is None and self.actions_total == 0


def build_compact_status_summary(
    report: "RuntimeStatusReport",
    *,
    actions: Optional[Sequence[OperatorAction]] = None,
) -> CompactStatusSummary:
    """Project *report* into a :class:`CompactStatusSummary`.

    *actions* defaults to the result of
    :func:`summarize_operator_actions(report)` so the caller can
    request both views with one pass when they need both. Tests pass
    a precomputed tuple to verify the projection is independent of
    the mapping function.
    """

    action_seq = (
        tuple(actions) if actions is not None else summarize_operator_actions(report)
    )

    services_alive = sum(1 for s in report.services if s.health == HEALTH_ALIVE)
    services_stale = sum(1 for s in report.services if s.health == HEALTH_STALE)
    services_unknown = sum(
        1 for s in report.services if s.health == HEALTH_UNKNOWN and s.implemented
    )
    services_circuit_open = sum(
        1 for s in report.services if s.health == HEALTH_CIRCUIT_OPEN
    )

    queue_in_progress = sum(j.in_progress for j in report.job_types)
    queue_failed_terminal = sum(j.failed_terminal for j in report.job_types)
    queue_failed_retryable = sum(j.failed_retryable for j in report.job_types)

    autonomy_ticks_errored = sum(1 for t in report.autonomy_recent if t.error)
    autonomy_locked_dispatches = sum(
        1
        for t in report.autonomy_recent
        for d in t.dispatches
        if d.outcome == AUTONOMY_OUTCOME_LOCKED
    )

    funnel_done = sum(
        1 for c in report.completion_funnel_recent if c.completion_status == "done"
    )
    funnel_retry_ready = sum(
        1
        for c in report.completion_funnel_recent
        if c.completion_status == "retry_ready"
    )
    funnel_needs_approval = sum(
        1
        for c in report.completion_funnel_recent
        if c.completion_status == "needs_approval"
    )
    funnel_blocked = sum(
        1
        for c in report.completion_funnel_recent
        if c.completion_status == "blocked"
    )

    top = action_seq[0] if action_seq else None
    return CompactStatusSummary(
        profile=report.profile,
        services_alive=services_alive,
        services_stale=services_stale,
        services_unknown=services_unknown,
        services_circuit_open=services_circuit_open,
        queue_in_progress=queue_in_progress,
        queue_failed_terminal=queue_failed_terminal,
        queue_failed_retryable=queue_failed_retryable,
        autonomy_ticks_recent=len(report.autonomy_recent),
        autonomy_ticks_errored=autonomy_ticks_errored,
        autonomy_locked_dispatches=autonomy_locked_dispatches,
        funnel_done=funnel_done,
        funnel_retry_ready=funnel_retry_ready,
        funnel_needs_approval=funnel_needs_approval,
        funnel_blocked=funnel_blocked,
        top_action=top,
        actions_total=len(action_seq),
    )
