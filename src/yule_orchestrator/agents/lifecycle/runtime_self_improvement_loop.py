"""Runtime self-improvement loop — gateway delegate + dispatcher.

Supervisor watch loop 가 :func:`run_supervisor_watch_loop` 안에서 호출하는
``self_improvement_detect_fn`` + ``self_improvement_dispatch_fn`` 두 hook
을 한 데 모아주는 통합 dispatcher.

흐름 (한 tick):

    1. ObservationContext 를 SQLite / heartbeat / session 에서 채운다.
    2. 기존 :func:`collect_self_improvement_signals` + 새로 추가한
       :func:`collect_seed_signals` 로 signal 들을 모은다.
    3. 각 signal 마다 :class:`ProblemLedger` 에 register_or_update.
    4. 새 / 갱신된 problem 에 대해 :func:`triage_problem` 으로 owner_role
       과 suggested_action 을 정한다.
    5. :func:`evaluate_delegated_approval` 로 위임 가능 여부를 결정.
    6. 결과에 따라:
         * delegated_ok + suggested_action 이 code change 면 worktree 분기
           → coding executor 에게 위임할 payload 까지만 만들고 enqueue
           hook 으로 넘김.
         * delegated_ok 이고 단순 보고면 :class:`AgentOpsEntry` 만 기록.
         * needs_human 이면 operator action card hook 으로 넘김.
         * blocked 면 ledger 만 갱신 + 사람 보고용 audit.
    7. ledger 를 새 상태로 적고 :class:`SelfImprovementTickReport` 를 반환.

본 모듈은 *외부 I/O 가 없다*. queue / Discord / git 은 모두 hook 으로
주입한다. 그래서:

* unit test 가 in-memory recorder 로 끝까지 흐름을 검증 가능.
* 프로덕션은 run_service.py 에서 hook 을 production wiring 으로 채운다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    Callable,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

from .agent_ops_log import (
    AgentOpsEntry,
    append_agent_ops_audit,
    build_agent_ops_entry,
)
from .autonomy_policy import (
    ACTION_AGENT_OPS_RECORD,
    ACTION_RUNTIME_CODE_CHANGE,
    AutonomyContext,
    AutonomyDecision,
    AutonomyLevel,
    decide_autonomy,
)
from .delegated_operator import (
    DelegatedDecision,
    DelegatedRateLedger,
    evaluate_delegated_approval,
)
from .problem_ledger import (
    ProblemLedger,
    ProblemObject,
    ProblemStatus,
    build_problem_signature,
)
from .self_improvement import (
    SelfImprovementSignal,
    collect_self_improvement_signals,
)
from .self_improvement_seed_detectors import (
    ObservationContext,
    collect_seed_signals,
)
from .self_improvement_worktree import (
    InMemoryWorktreeRegistry,
    WorktreeProvisioner,
    WorktreeProvisionOutcome,
    provision_worktree_for_problem,
)
from .troubleshooting_ledger import (
    CaptureOutcome as TroubleshootingCaptureOutcome,
    TroubleshootingLedger,
)
from .troubleshooting_record import (
    CaptureReason,
    DETECTED_BY_SELF_IMPROVEMENT,
    TroubleshootingStatus,
)
from .tech_lead_triage import (
    SCOPE_BLOCKED,
    SCOPE_DELEGATED_OK,
    SCOPE_NEEDS_HUMAN,
    TriageVerdict,
    triage_problem,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


class ObservationProvider(Protocol):
    """Fetches the runtime snapshot the loop operates on."""

    def __call__(self) -> ObservationContext:  # pragma: no cover - protocol
        ...


class OperatorActionHook(Protocol):
    """Surface an :class:`ProblemObject` as an operator-action card."""

    def __call__(
        self,
        *,
        problem: ProblemObject,
        verdict: TriageVerdict,
        decision: DelegatedDecision,
    ) -> Optional[str]:  # pragma: no cover - protocol
        ...


class ExecutorHandoffHook(Protocol):
    """Enqueue a coding_execute job (or equivalent) for a delegated fix."""

    def __call__(
        self,
        *,
        problem: ProblemObject,
        verdict: TriageVerdict,
        decision: DelegatedDecision,
        worktree: Optional[WorktreeProvisionOutcome],
    ) -> Optional[str]:  # pragma: no cover - protocol
        ...


class ObsidianRecordHook(Protocol):
    """Persist the audit row to the configured supervisor session.

    The default implementation writes to the supervisor session via
    workflow_state.update_session; tests inject a list-recorder.
    """

    def __call__(
        self,
        *,
        problem: ProblemObject,
        entries: Sequence[AgentOpsEntry],
    ) -> None:  # pragma: no cover - protocol
        ...


# ---------------------------------------------------------------------------
# Outcome dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProblemHandlingOutcome:
    """One problem's resolution-step result for a tick."""

    problem: ProblemObject
    verdict: TriageVerdict
    decision: DelegatedDecision
    worktree: Optional[WorktreeProvisionOutcome]
    executor_handoff_job_id: Optional[str]
    operator_action_id: Optional[str]
    audit_entries: Tuple[AgentOpsEntry, ...]
    final_status: ProblemStatus


@dataclass(frozen=True)
class SelfImprovementTickReport:
    """What :func:`run_self_improvement_tick` produced.

    Surfaced to the supervisor's status post so the operator sees
    "이번 sweep 에서 봇이 N개 발견 / M개 자동 처리 / K개 사람 대기"
    in one glance.
    """

    detected_signals: Tuple[SelfImprovementSignal, ...]
    new_problems: Tuple[ProblemObject, ...]
    handled: Tuple[ProblemHandlingOutcome, ...]
    skipped_terminal: Tuple[ProblemObject, ...]
    delegated_count: int
    waiting_operator_count: int
    blocked_count: int

    def summary_line(self) -> str:
        return (
            f"self-improvement: detected={len(self.detected_signals)} "
            f"new={len(self.new_problems)} delegated={self.delegated_count} "
            f"operator_wait={self.waiting_operator_count} blocked={self.blocked_count}"
        )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


@dataclass
class SelfImprovementDispatcher:
    """Owns the per-supervisor-process state.

    Keeps the :class:`ProblemLedger`, :class:`DelegatedRateLedger`, and
    :class:`InMemoryWorktreeRegistry` together so a single instance can
    be wired into the supervisor watch loop via two thin callables.
    """

    observation_provider: ObservationProvider
    problem_ledger: ProblemLedger
    rate_ledger: DelegatedRateLedger
    worktree_registry: InMemoryWorktreeRegistry
    worktree_provisioner: Optional[WorktreeProvisioner] = None
    operator_action_hook: Optional[OperatorActionHook] = None
    executor_handoff_hook: Optional[ExecutorHandoffHook] = None
    obsidian_record_hook: Optional[ObsidianRecordHook] = None
    troubleshooting_ledger: Optional[TroubleshootingLedger] = None
    triage_deliberation_fn: Optional[Callable[..., Optional[TriageVerdict]]] = None
    repo_cwd: str = "."
    base_branch: str = "main"
    actor: str = "self-improvement-runtime"
    last_report: Optional[SelfImprovementTickReport] = None

    # ------------------------------------------------------------------
    # Hook surface — two thin coroutines for the supervisor watch loop
    # ------------------------------------------------------------------

    def detect_fn(self) -> Tuple[SelfImprovementSignal, ...]:
        """Compatible with :func:`run_supervisor_watch_loop`'s
        ``self_improvement_detect_fn``. Synchronous: returns the signals
        the dispatch_fn will then process.
        """

        observation = self.observation_provider()
        legacy = collect_self_improvement_signals(
            jobs=observation.jobs,
            failed_jobs=observation.failed_jobs,
            heartbeats=observation.heartbeats,
        )
        seed = collect_seed_signals(observation)
        signals = tuple(legacy) + tuple(seed)
        # Cache the observation snapshot so dispatch_fn doesn't
        # double-pull state — it operates on the same picture.
        self._cached_observation = observation
        self._cached_signals = signals
        return signals

    def dispatch_fn(
        self,
        signal: SelfImprovementSignal,
        plan: Any,  # SelfImprovementProposal — kept loose to avoid circular import
    ) -> ProblemHandlingOutcome:
        """Per-signal dispatch — compatible with
        ``self_improvement_dispatch_fn`` (which is called once per signal
        by the supervisor watch loop).

        We re-derive the ProblemObject inside this method so dispatch
        is idempotent: even if `detect_fn` isn't paired (e.g. test
        wiring), one dispatch still produces a coherent outcome.
        """

        return self._handle_one(signal=signal, plan=plan)

    # ------------------------------------------------------------------
    # Standalone "run one full tick" — used by tests + cron entrypoints
    # ------------------------------------------------------------------

    def run_tick(self) -> SelfImprovementTickReport:
        signals = self.detect_fn()
        new_problems: List[ProblemObject] = []
        handled: List[ProblemHandlingOutcome] = []
        skipped: List[ProblemObject] = []
        delegated_count = 0
        waiting_operator_count = 0
        blocked_count = 0
        for signal in signals:
            outcome = self._handle_one(signal=signal, plan=None)
            if outcome.final_status == ProblemStatus.SUPPRESSED:
                skipped.append(outcome.problem)
                continue
            handled.append(outcome)
            if outcome.problem.delegated_ok:
                delegated_count += 1
            if outcome.final_status == ProblemStatus.WAITING_OPERATOR:
                waiting_operator_count += 1
            if outcome.final_status == ProblemStatus.BLOCKED:
                blocked_count += 1
            if outcome.problem.occurrence_count == 1:
                new_problems.append(outcome.problem)

        report = SelfImprovementTickReport(
            detected_signals=tuple(signals),
            new_problems=tuple(new_problems),
            handled=tuple(handled),
            skipped_terminal=tuple(skipped),
            delegated_count=delegated_count,
            waiting_operator_count=waiting_operator_count,
            blocked_count=blocked_count,
        )
        self.last_report = report
        return report

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _handle_one(
        self,
        *,
        signal: SelfImprovementSignal,
        plan: Any,
    ) -> ProblemHandlingOutcome:
        problem, is_new = self.problem_ledger.register_or_update(
            signal_id=signal.signal,
            severity=signal.severity,
            summary=signal.summary,
            evidence=signal.evidence,
        )
        if problem.is_terminal():
            return ProblemHandlingOutcome(
                problem=problem,
                verdict=triage_problem(
                    signal_id=signal.signal,
                    severity=signal.severity,
                    evidence=signal.evidence,
                    summary=signal.summary,
                ),
                decision=_skipped_decision(problem),
                worktree=None,
                executor_handoff_job_id=None,
                operator_action_id=None,
                audit_entries=(),
                final_status=problem.status,
            )

        verdict = triage_problem(
            signal_id=signal.signal,
            severity=signal.severity,
            evidence=signal.evidence,
            summary=signal.summary,
            deliberation_fn=self.triage_deliberation_fn,
        )

        autonomy_decision = decide_autonomy(
            AutonomyContext(
                action=verdict.suggested_action,
                summary=signal.summary,
                topic_key=str(signal.evidence.get("topic_key") or "") or None,
                risk_level=_risk_for_severity(signal.severity),
                reversible=True,
                external_side_effect=False,
                reason=verdict.rationale,
            )
        )

        # tech-lead 가 needs_human 으로 미리 분류했으면 위임 evaluator 를
        # 호출하지 않는다 — 그래서 rate-limit 가 사람 케이스로 인해 소모
        # 되지 않는다.
        if verdict.approval_scope_hint == SCOPE_NEEDS_HUMAN:
            delegated_decision = DelegatedDecision(
                delegated=False,
                action=verdict.suggested_action,
                scope=None,
                escalation_reason="triage_hint_needs_human",
                audit_summary=(
                    f"triage hint requested human review: "
                    f"{verdict.rationale}"
                ),
                problem_signature=problem.signature,
                retry_count=problem.retry_count,
                decided_at=_utc_now_iso(),
            )
        elif verdict.approval_scope_hint == SCOPE_BLOCKED:
            delegated_decision = DelegatedDecision(
                delegated=False,
                action=verdict.suggested_action,
                scope=None,
                escalation_reason="triage_hint_blocked",
                audit_summary=(
                    f"triage hint blocked: {verdict.rationale}"
                ),
                problem_signature=problem.signature,
                retry_count=problem.retry_count,
                decided_at=_utc_now_iso(),
            )
        else:
            delegated_decision = evaluate_delegated_approval(
                action=verdict.suggested_action,
                autonomy_level=autonomy_decision.autonomy_level,
                problem_signature=problem.signature,
                rate_ledger=self.rate_ledger,
            )

        audit_entries: List[AgentOpsEntry] = []
        worktree_outcome: Optional[WorktreeProvisionOutcome] = None
        executor_handoff_job_id: Optional[str] = None
        operator_action_id: Optional[str] = None

        # Always stamp a "detected" audit row so even no-op observations
        # are visible in the audit log.
        audit_entries.append(
            build_agent_ops_entry(
                decision=autonomy_decision,
                outcome=(
                    "self_improvement_detected"
                    if is_new
                    else "self_improvement_reobserved"
                ),
                summary=(
                    f"[{signal.signal}] {signal.summary} "
                    f"(occurrence={problem.occurrence_count}, "
                    f"owner={verdict.primary_owner})"
                ),
                actor=self.actor,
            )
        )

        final_status: ProblemStatus

        if delegated_decision.delegated:
            # 자동 처리 가능. 코드 변경 action 이면 worktree 분기 후
            # executor 에게 위임.
            audit_entries.append(
                build_agent_ops_entry(
                    decision=autonomy_decision,
                    outcome=f"delegated_auto_approved:{delegated_decision.scope}",
                    summary=delegated_decision.audit_summary,
                    actor=self.actor,
                )
            )
            wants_worktree = verdict.suggested_action in {
                ACTION_RUNTIME_CODE_CHANGE,
            }
            if wants_worktree and self.worktree_provisioner is not None:
                try:
                    worktree_outcome = provision_worktree_for_problem(
                        problem_signature=problem.signature,
                        owner_role=verdict.primary_owner,
                        spawned_by=self.actor,
                        base_branch=self.base_branch,
                        delegated_approval_state="delegated_ok",
                        provisioner=self.worktree_provisioner,
                        registry=self.worktree_registry,
                        cwd=self.repo_cwd,
                    )
                    audit_entries.append(
                        build_agent_ops_entry(
                            decision=autonomy_decision,
                            outcome=(
                                "worktree_reused"
                                if worktree_outcome.reused
                                else "worktree_created"
                            ),
                            summary=(
                                f"branch={worktree_outcome.metadata.branch} "
                                f"path={worktree_outcome.metadata.path}"
                            ),
                            actor=self.actor,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - log + continue
                    logger.warning(
                        "self-improvement: worktree provision failed for %s",
                        problem.signature,
                        exc_info=True,
                    )
                    audit_entries.append(
                        build_agent_ops_entry(
                            decision=autonomy_decision,
                            outcome=f"worktree_failed:{type(exc).__name__}",
                            summary=str(exc) or type(exc).__name__,
                            actor=self.actor,
                        )
                    )
                    worktree_outcome = None
            if self.executor_handoff_hook is not None:
                try:
                    executor_handoff_job_id = self.executor_handoff_hook(
                        problem=problem,
                        verdict=verdict,
                        decision=delegated_decision,
                        worktree=worktree_outcome,
                    )
                    if executor_handoff_job_id:
                        audit_entries.append(
                            build_agent_ops_entry(
                                decision=autonomy_decision,
                                outcome="executor_handoff_enqueued",
                                summary=f"job_id={executor_handoff_job_id}",
                                job_id=executor_handoff_job_id,
                                actor=self.actor,
                            )
                        )
                except Exception as exc:  # noqa: BLE001 - never crash dispatcher
                    logger.warning(
                        "self-improvement: executor handoff failed", exc_info=True
                    )
                    audit_entries.append(
                        build_agent_ops_entry(
                            decision=autonomy_decision,
                            outcome=f"executor_handoff_failed:{type(exc).__name__}",
                            summary=str(exc) or type(exc).__name__,
                            actor=self.actor,
                        )
                    )
            final_status = ProblemStatus.FIXING if wants_worktree else ProblemStatus.TRIAGED
            self.problem_ledger.transition(
                problem.signature,
                status=final_status,
                owner_role=verdict.primary_owner,
                suggested_next_action=verdict.suggested_action,
                approval_scope="delegated_ok",
                delegated_ok=True,
                worktree_branch=(
                    worktree_outcome.metadata.branch if worktree_outcome else None
                ),
                related_job_ids=(
                    (executor_handoff_job_id,)
                    if executor_handoff_job_id
                    else None
                ),
            )
        else:
            # 사람 승인 또는 영구 escalate
            audit_entries.append(
                build_agent_ops_entry(
                    decision=autonomy_decision,
                    outcome=(
                        f"escalated:{delegated_decision.escalation_reason or 'unknown'}"
                    ),
                    summary=delegated_decision.audit_summary,
                    actor=self.actor,
                )
            )
            if (
                verdict.approval_scope_hint != SCOPE_BLOCKED
                and self.operator_action_hook is not None
            ):
                try:
                    operator_action_id = self.operator_action_hook(
                        problem=problem,
                        verdict=verdict,
                        decision=delegated_decision,
                    )
                    if operator_action_id:
                        audit_entries.append(
                            build_agent_ops_entry(
                                decision=autonomy_decision,
                                outcome="operator_action_posted",
                                summary=f"action_id={operator_action_id}",
                                actor=self.actor,
                            )
                        )
                except Exception as exc:  # noqa: BLE001 - never crash dispatcher
                    logger.warning(
                        "self-improvement: operator action hook failed",
                        exc_info=True,
                    )
                    audit_entries.append(
                        build_agent_ops_entry(
                            decision=autonomy_decision,
                            outcome=f"operator_action_failed:{type(exc).__name__}",
                            summary=str(exc) or type(exc).__name__,
                            actor=self.actor,
                        )
                    )

            if verdict.approval_scope_hint == SCOPE_BLOCKED:
                final_status = ProblemStatus.BLOCKED
                self.problem_ledger.transition(
                    problem.signature,
                    status=final_status,
                    owner_role=verdict.primary_owner,
                    suggested_next_action=verdict.suggested_action,
                    approval_scope="blocked",
                    delegated_ok=False,
                    last_error=delegated_decision.audit_summary,
                )
            else:
                final_status = ProblemStatus.WAITING_OPERATOR
                self.problem_ledger.transition(
                    problem.signature,
                    status=final_status,
                    owner_role=verdict.primary_owner,
                    suggested_next_action=verdict.suggested_action,
                    approval_scope="needs_human",
                    delegated_ok=False,
                    last_error=delegated_decision.audit_summary,
                )

        if self.obsidian_record_hook is not None:
            try:
                self.obsidian_record_hook(
                    problem=self.problem_ledger.get(problem.signature) or problem,
                    entries=tuple(audit_entries),
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "self-improvement: obsidian_record_hook raised", exc_info=True
                )

        # Mandatory troubleshooting capture — every detected problem becomes
        # a structured record. Failures are logged + swallowed so a ledger
        # bug never breaks dispatch.
        if self.troubleshooting_ledger is not None:
            try:
                self._capture_troubleshooting(
                    signal=signal,
                    problem=self.problem_ledger.get(problem.signature) or problem,
                    verdict=verdict,
                    delegated_decision=delegated_decision,
                    worktree=worktree_outcome,
                    executor_handoff_job_id=executor_handoff_job_id,
                    operator_action_id=operator_action_id,
                    final_status=final_status,
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "self-improvement: troubleshooting capture raised",
                    exc_info=True,
                )

        final_problem = self.problem_ledger.get(problem.signature) or problem
        return ProblemHandlingOutcome(
            problem=final_problem,
            verdict=verdict,
            decision=delegated_decision,
            worktree=worktree_outcome,
            executor_handoff_job_id=executor_handoff_job_id,
            operator_action_id=operator_action_id,
            audit_entries=tuple(audit_entries),
            final_status=final_status,
        )

    # ------------------------------------------------------------------
    # Troubleshooting capture — every dispatched problem becomes a record
    # ------------------------------------------------------------------

    def _capture_troubleshooting(
        self,
        *,
        signal: SelfImprovementSignal,
        problem: ProblemObject,
        verdict: TriageVerdict,
        delegated_decision: DelegatedDecision,
        worktree: Optional[WorktreeProvisionOutcome],
        executor_handoff_job_id: Optional[str],
        operator_action_id: Optional[str],
        final_status: ProblemStatus,
    ) -> Optional[TroubleshootingCaptureOutcome]:
        if self.troubleshooting_ledger is None:
            return None

        capture_reason = _SIGNAL_TO_CAPTURE_REASON.get(
            signal.signal, CaptureReason.OPERATOR_MANUAL_INTERVENTION
        )

        severity = signal.severity or "medium"
        status = _PROBLEM_STATUS_TO_TROUBLESHOOTING_STATUS.get(
            final_status, TroubleshootingStatus.OPEN
        )
        attempted_fix = ""
        final_fix = ""
        prevention_rule = ""
        if worktree is not None:
            attempted_fix = (
                f"branch={worktree.metadata.branch} 분기 → executor handoff "
                f"({'reused' if worktree.reused else 'created'})"
            )
        if executor_handoff_job_id:
            final_fix = (
                f"executor job={executor_handoff_job_id} 위임 — draft PR 까지 진행 "
                "(merge 자동 금지)"
            )
        if operator_action_id:
            final_fix = (
                f"operator action card={operator_action_id} 발행 — 사람 응답 대기"
            )
        prevention_rule = verdict.rationale

        related_files = tuple(
            problem.evidence.get("related_files")
            if isinstance(problem.evidence.get("related_files"), (list, tuple))
            else ()
        )

        return self.troubleshooting_ledger.capture(
            title=f"self-improvement: {signal.signal}",
            capture_reason=capture_reason,
            detected_by=DETECTED_BY_SELF_IMPROVEMENT,
            owner_role=verdict.primary_owner,
            scope=signal.signal,
            symptom=signal.summary,
            severity=severity,
            exact_evidence=str(dict(signal.evidence) or "{}"),
            attempted_fix=attempted_fix,
            final_fix=final_fix,
            prevention_rule=prevention_rule,
            related_session_ids=tuple(problem.related_session_ids),
            related_job_ids=tuple(problem.related_job_ids)
            + ((executor_handoff_job_id,) if executor_handoff_job_id else ()),
            related_prs=tuple(problem.related_pr_urls),
            related_files=related_files,
            followup_required=(
                final_status == ProblemStatus.WAITING_OPERATOR
                or final_status == ProblemStatus.BLOCKED
            ),
            problem_signature=problem.signature,
            tags=("self-improvement",),
            status=status,
        )


# Map dispatcher's ProblemStatus → TroubleshootingStatus.
_PROBLEM_STATUS_TO_TROUBLESHOOTING_STATUS: Mapping[ProblemStatus, TroubleshootingStatus] = {
    ProblemStatus.DETECTED: TroubleshootingStatus.OPEN,
    ProblemStatus.TRIAGED: TroubleshootingStatus.OPEN,
    ProblemStatus.FIXING: TroubleshootingStatus.MITIGATED,
    ProblemStatus.VERIFYING: TroubleshootingStatus.MITIGATED,
    ProblemStatus.COMPLETED: TroubleshootingStatus.FIXED,
    ProblemStatus.BLOCKED: TroubleshootingStatus.OPEN,
    ProblemStatus.WAITING_OPERATOR: TroubleshootingStatus.OPEN,
    ProblemStatus.ESCALATED: TroubleshootingStatus.OPEN,
    ProblemStatus.SUPPRESSED: TroubleshootingStatus.SUPERSEDED,
}


# Map known self-improvement signal id → CaptureReason. Unknown signals
# fall back to OPERATOR_MANUAL_INTERVENTION so capture still happens.
_SIGNAL_TO_CAPTURE_REASON: Mapping[str, CaptureReason] = {
    "engineering_write_reply_mismatch": CaptureReason.APPROVAL_REPLY_MISMATCH,
    "approval_no_matching_reply": CaptureReason.APPROVAL_REPLY_MISMATCH,
    "qa_test_misclassification": CaptureReason.WRONG_CLASSIFICATION,
    "coding_continuation_stalled": CaptureReason.NO_CONTINUATION,
    "supervisor_watch_unknown_surface": CaptureReason.RUNTIME_UNKNOWN_CONFUSION,
    "obsidian_render_failure": CaptureReason.POLICY_EXISTS_NO_ENFORCEMENT,
    "member_bot_presence_confusion": CaptureReason.RUNTIME_UNKNOWN_CONFUSION,
    "issueless_bootstrap_failure": CaptureReason.PARTIAL_WIRING,
    "failed_retryable_pileup": CaptureReason.FAILED_RETRYABLE_NO_RECOVERY,
    "duplicate_topic_approval": CaptureReason.DUPLICATE_WORK_ORDER,
    "stale_heartbeat": CaptureReason.QUEUE_STUCK,
    "empty_knowledge_note": CaptureReason.POLICY_EXISTS_NO_ENFORCEMENT,
    "repeated_user_complaint": CaptureReason.OPERATOR_MANUAL_INTERVENTION,
}


# ---------------------------------------------------------------------------
# Reporting / summary helpers
# ---------------------------------------------------------------------------


def render_tick_report_lines(
    report: SelfImprovementTickReport,
) -> Tuple[str, ...]:
    """Compact, log-friendly summary of one tick.

    Used by the supervisor status post + tests.
    """

    lines: list[str] = [report.summary_line()]
    for outcome in report.handled:
        lines.append(
            f"  · [{outcome.problem.severity}] {outcome.problem.signal_id} "
            f"→ owner={outcome.verdict.primary_owner} "
            f"status={outcome.final_status.value}"
            + (
                f" worktree={outcome.worktree.metadata.branch}"
                if outcome.worktree
                else ""
            )
            + (
                f" executor_job={outcome.executor_handoff_job_id}"
                if outcome.executor_handoff_job_id
                else ""
            )
        )
    return tuple(lines)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _risk_for_severity(severity: str) -> str:
    if severity == "high":
        return "high"
    if severity == "low":
        return "low"
    return "medium"


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def _skipped_decision(problem: ProblemObject) -> DelegatedDecision:
    return DelegatedDecision(
        delegated=False,
        action="skip_terminal",
        scope=None,
        escalation_reason="problem_terminal",
        audit_summary=(
            f"skip: problem {problem.signature} already in terminal status "
            f"{problem.status.value}"
        ),
        problem_signature=problem.signature,
        retry_count=problem.retry_count,
        decided_at=_utc_now_iso(),
    )


__all__ = (
    "ExecutorHandoffHook",
    "ObservationProvider",
    "ObsidianRecordHook",
    "OperatorActionHook",
    "ProblemHandlingOutcome",
    "SelfImprovementDispatcher",
    "SelfImprovementTickReport",
    "render_tick_report_lines",
)
