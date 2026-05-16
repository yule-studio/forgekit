"""Production wiring for the runtime self-improvement loop.

이 모듈은 :class:`SelfImprovementDispatcher` 에 필요한 hook 들을 실제
SQLite 큐 / heartbeat / Discord 큐로 연결한다. 모든 hook 은 실패 시
log + 안전한 None 반환을 유지해 supervisor 가 절대 죽지 않게 한다.

운영자가 켜는 env:

* ``YULE_SELF_IMPROVEMENT_ENABLED`` — truthy 면 supervisor 가 loop 시작.
* ``YULE_SELF_IMPROVEMENT_INTERVAL_SECONDS`` — sweep interval (기본 300s).
* ``YULE_SELF_IMPROVEMENT_LEDGER_PATH`` — problem ledger sidecar JSON.
* ``YULE_SELF_IMPROVEMENT_WORKTREE_ROOT`` — git worktree base path.
* ``YULE_SELF_IMPROVEMENT_BASE_BRANCH`` — worktree 가 분기할 base ref.

자동 wiring 의 안전 기본값:

* 자동 머지 / 푸시 / 배포 절대 금지 — :mod:`delegated_operator` 의 영구
  escalation 화이트리스트로 이미 차단됨. 본 wiring 은 그 위에 코드/PR
  생성도 *draft* 까지만 만들도록 executor payload 의 ``draft_pr=True``
  플래그를 강제한다.
* operator action hook 은 사람 응답을 기다리는 카드만 만든다.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from .agent_ops_log import AgentOpsEntry
from .delegated_operator import DelegatedDecision, DelegatedRateLedger
from .problem_ledger import ProblemLedger, ProblemObject, default_ledger_path
from .runtime_self_improvement_loop import (
    ExecutorHandoffHook,
    ObservationProvider,
    ObsidianRecordHook,
    OperatorActionHook,
    SelfImprovementDispatcher,
)
from .self_improvement_seed_detectors import ObservationContext
from .self_improvement_worktree import (
    GitWorktreeProvisioner,
    InMemoryWorktreeRegistry,
    WorktreeProvisionOutcome,
    WorktreeProvisioner,
)
from .tech_lead_triage import TriageVerdict


logger = logging.getLogger(__name__)


ENV_ENABLED: str = "YULE_SELF_IMPROVEMENT_ENABLED"
ENV_INTERVAL_SECONDS: str = "YULE_SELF_IMPROVEMENT_INTERVAL_SECONDS"
ENV_LEDGER_PATH: str = "YULE_SELF_IMPROVEMENT_LEDGER_PATH"
ENV_WORKTREE_ROOT: str = "YULE_SELF_IMPROVEMENT_WORKTREE_ROOT"
ENV_WORKTREE_REGISTRY_PATH: str = "YULE_SELF_IMPROVEMENT_WORKTREE_REGISTRY"
ENV_BASE_BRANCH: str = "YULE_SELF_IMPROVEMENT_BASE_BRANCH"
ENV_REPO_CWD: str = "YULE_SELF_IMPROVEMENT_REPO_CWD"
ENV_DAILY_CAP: str = "YULE_SELF_IMPROVEMENT_DAILY_CAP"

DEFAULT_INTERVAL_SECONDS: float = 300.0


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


def is_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    env = env if env is not None else os.environ
    raw = (env.get(ENV_ENABLED) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def resolve_interval_seconds(env: Optional[Mapping[str, str]] = None) -> float:
    env = env if env is not None else os.environ
    raw = (env.get(ENV_INTERVAL_SECONDS) or "").strip()
    if not raw:
        return DEFAULT_INTERVAL_SECONDS
    try:
        return max(30.0, float(raw))
    except ValueError:
        return DEFAULT_INTERVAL_SECONDS


# ---------------------------------------------------------------------------
# Observation provider — production
# ---------------------------------------------------------------------------


def build_observation_provider(
    *,
    job_queue: Any,
    heartbeat_store: Any,
    session_loader: Optional[Callable[[], Sequence[Any]]] = None,
    job_limit: int = 200,
    failed_limit: int = 50,
    session_limit: int = 100,
) -> ObservationProvider:
    """Closure pulling the current snapshot from SQLite + workflow_state.

    Every call rebuilds the snapshot — keeping it cheap (~one COUNT
    query + one SELECT LIMIT) so the supervisor's 5-minute sweep cost
    stays negligible.
    """

    def _resolve_jobs() -> Sequence[Any]:
        # We don't have a "list_active_jobs" API on the JobQueue today —
        # the existing surface aggregates by state. For dedup we mostly
        # care about ``approval_post`` rows in saved/in_progress and
        # ``obsidian_write`` rows in failed_retryable. Use
        # ``recent_failed`` + a manual scan of saved/in_progress later;
        # for now ``recent_failed`` is enough to drive the new detectors
        # because all seed signals key off failure or stale state.
        try:
            return tuple(job_queue.recent_failed(limit=failed_limit))
        except Exception:  # noqa: BLE001
            logger.warning(
                "self-improvement: job_queue.recent_failed raised",
                exc_info=True,
            )
            return ()

    def _resolve_failed_jobs() -> Sequence[Any]:
        return _resolve_jobs()  # same surface — caller dedupes if needed

    def _resolve_sessions() -> Sequence[Any]:
        if session_loader is not None:
            try:
                return tuple(session_loader())
            except Exception:  # noqa: BLE001
                logger.warning(
                    "self-improvement: session_loader raised",
                    exc_info=True,
                )
                return ()
        try:
            from ..workflow_state import list_sessions

            return tuple(list_sessions(limit=session_limit))
        except Exception:  # noqa: BLE001
            logger.warning(
                "self-improvement: workflow_state.list_sessions raised",
                exc_info=True,
            )
            return ()

    def _resolve_heartbeats() -> Mapping[str, Mapping[str, Any]]:
        try:
            records = heartbeat_store.list_all()
        except Exception:  # noqa: BLE001
            logger.warning(
                "self-improvement: heartbeat_store.list_all raised",
                exc_info=True,
            )
            return {}
        out: dict[str, Mapping[str, Any]] = {}
        for record in records:
            payload = {
                "updated_at": datetime.fromtimestamp(
                    float(record.last_beat), tz=timezone.utc
                )
                .replace(microsecond=0)
                .isoformat(),
                "pid": record.pid,
            }
            metadata = getattr(record, "metadata", None) or {}
            if isinstance(metadata, Mapping):
                payload.update({k: v for k, v in metadata.items()})
            out[record.service_id] = payload
        return out

    def _provide() -> ObservationContext:
        jobs = _resolve_jobs()
        return ObservationContext(
            jobs=jobs,
            failed_jobs=_resolve_failed_jobs(),
            sessions=_resolve_sessions(),
            heartbeats=_resolve_heartbeats(),
            now=datetime.now(tz=timezone.utc).replace(microsecond=0),
        )

    return _provide


# ---------------------------------------------------------------------------
# Operator action hook — production
# ---------------------------------------------------------------------------


def build_operator_action_hook(
    *,
    poster: Optional[Callable[..., Optional[str]]] = None,
) -> OperatorActionHook:
    """Build an OperatorActionHook that posts a card to ``#승인-대기``.

    ``poster`` is the actual Discord publisher. If None, the hook just
    logs and returns None — the supervisor will still update the
    problem ledger to ``waiting_operator`` so the operator can find it
    via ``yule runtime status``.
    """

    def _hook(
        *,
        problem: ProblemObject,
        verdict: TriageVerdict,
        decision: DelegatedDecision,
    ) -> Optional[str]:
        body_lines = [
            f"## [{problem.severity.upper()}] {problem.signal_id}",
            "",
            f"**문제 요약:** {problem.summary}",
            f"**owner 추천:** {verdict.primary_owner} "
            f"(+{', '.join(verdict.co_owner_roles) or '없음'})",
            f"**제안 action:** {verdict.suggested_action}",
            f"**triage 사유:** {verdict.rationale}",
            f"**escalation 사유:** {decision.escalation_reason or '미정'}",
            f"**signature:** `{problem.signature}`",
            f"**occurrence:** {problem.occurrence_count}",
            "",
            "사용자 응답이 필요합니다 — `승인` / `반려` / `보류` 중 하나로 답해 주세요.",
        ]
        body = "\n".join(body_lines)
        if poster is None:
            logger.info(
                "self-improvement operator action (no poster wired): %s",
                body,
            )
            return None
        try:
            return poster(
                title=f"self-improvement: {problem.signal_id}",
                body=body,
                problem_signature=problem.signature,
                owner_role=verdict.primary_owner,
                escalation_reason=decision.escalation_reason,
            )
        except Exception:  # noqa: BLE001 - hook must not crash supervisor
            logger.warning(
                "self-improvement: operator action poster raised",
                exc_info=True,
            )
            return None

    return _hook


# ---------------------------------------------------------------------------
# Executor handoff hook — production
# ---------------------------------------------------------------------------


def build_executor_handoff_hook(
    *,
    enqueue_fn: Optional[Callable[..., Optional[str]]] = None,
    repo_full_name: Optional[str] = None,
    dry_run_default: bool = True,
) -> ExecutorHandoffHook:
    """Build an ExecutorHandoffHook that hands the problem off to the
    coding executor (or whichever runner the operator wires up).

    The hook builds a *self-contained payload* with the canonical
    fields the user spec listed:

      - problem_signature
      - reproduction_steps (from evidence)
      - likely_cause (heuristic rationale)
      - related_files (best-effort from evidence)
      - success_criteria (heuristic per signal)
      - test_requirements
      - delegated_approval (always True at this point)
      - escalation_boundary (which actions are forbidden)

    ``enqueue_fn`` returns the executor job id (str) on success or
    None on failure / not wired.  We never let the executor *push to
    main* or *merge* — those stay L4 in autonomy_policy and would
    immediately be blocked by ``delegated_operator``.
    """

    def _hook(
        *,
        problem: ProblemObject,
        verdict: TriageVerdict,
        decision: DelegatedDecision,
        worktree: Optional[WorktreeProvisionOutcome],
    ) -> Optional[str]:
        payload: Mapping[str, Any] = {
            "problem_signature": problem.signature,
            "signal_id": problem.signal_id,
            "severity": problem.severity,
            "summary": problem.summary,
            "evidence": dict(problem.evidence),
            "reproduction_steps": _reproduction_steps(problem),
            "likely_cause": verdict.rationale,
            "related_files": _related_files(problem),
            "success_criteria": _success_criteria(problem),
            "test_requirements": [
                "기존 회귀 통과",
                f"새 회귀 테스트가 {problem.signal_id} signature 를 재현",
            ],
            "delegated_approval": True,
            "escalation_boundary": [
                "merge",
                "release_tag",
                "deploy",
                "secret_modify",
                "main_branch_push",
                "destructive_delete",
            ],
            "draft_pr_only": True,  # 자동 머지 절대 금지
            "owner_role": verdict.primary_owner,
            "co_owner_roles": list(verdict.co_owner_roles),
            "approval_decision": decision.to_payload(),
            "repo_full_name": repo_full_name,
            "dry_run": dry_run_default,
        }
        if worktree is not None:
            payload = {
                **payload,
                "branch": worktree.metadata.branch,
                "worktree_path": worktree.metadata.path,
                "worktree_reused": worktree.reused,
            }
        if enqueue_fn is None:
            logger.info(
                "self-improvement executor handoff (no enqueue_fn wired): %s",
                payload,
            )
            return None
        try:
            return enqueue_fn(payload=payload)
        except Exception:  # noqa: BLE001
            logger.warning(
                "self-improvement: executor enqueue_fn raised",
                exc_info=True,
            )
            return None

    return _hook


def _reproduction_steps(problem: ProblemObject) -> list:
    sample = problem.evidence.get("sample") or problem.evidence.get("samples") or []
    if isinstance(sample, list) and sample:
        return [
            f"동일 evidence 로 {problem.signal_id} 재현: {item}"
            for item in sample[:3]
        ]
    return [f"signature={problem.signature} 가 sweep 중 {problem.occurrence_count}회 감지"]


def _related_files(problem: ProblemObject) -> list:
    # Heuristic: signal id → 후보 파일. detectors / 모듈 매핑.
    candidates = {
        "engineering_write_reply_mismatch": [
            "src/yule_orchestrator/discord/approval/reply_router.py",
            "src/yule_orchestrator/agents/job_queue/approval_reply.py",
        ],
        "approval_no_matching_reply": [
            "src/yule_orchestrator/discord/approval/reply_router.py",
            "src/yule_orchestrator/agents/job_queue/approval_worker.py",
        ],
        "qa_test_misclassification": [
            "src/yule_orchestrator/discord/engineering_channel_router/main.py",
            "src/yule_orchestrator/agents/routing.py",
        ],
        "coding_continuation_stalled": [
            "src/yule_orchestrator/agents/job_queue/work_order_coding_continuation.py",
            "src/yule_orchestrator/agents/job_queue/coding_execute_dispatcher.py",
        ],
        "supervisor_watch_unknown_surface": [
            "src/yule_orchestrator/runtime/status.py",
            "src/yule_orchestrator/runtime/status_poster.py",
        ],
        "obsidian_render_failure": [
            "src/yule_orchestrator/agents/job_queue/obsidian_writer_worker.py",
        ],
        "member_bot_presence_confusion": [
            "src/yule_orchestrator/discord/member/bot.py",
        ],
        "issueless_bootstrap_failure": [
            "src/yule_orchestrator/agents/job_queue/github_work_order.py",
        ],
    }
    return candidates.get(problem.signal_id, [])


def _success_criteria(problem: ProblemObject) -> list:
    base = [
        f"signature={problem.signature} 의 재현 케이스가 새 회귀에서 차단됨",
        "새 / 변경된 회귀 테스트가 모두 PASS",
        "기존 테스트 회귀 없음",
    ]
    if problem.signal_id in {"engineering_write_reply_mismatch", "approval_no_matching_reply"}:
        base.append(
            "engineering_write approval card 에 reply 매칭 회귀 케이스 추가 + 통과"
        )
    if problem.signal_id == "coding_continuation_stalled":
        base.append(
            "approval → coding_execute dispatch bridge 의 회귀 케이스 추가"
        )
    return base


# ---------------------------------------------------------------------------
# Obsidian record hook — production
# ---------------------------------------------------------------------------


def build_obsidian_record_hook(
    *,
    supervisor_session_id: Optional[str] = None,
) -> ObsidianRecordHook:
    """Append the audit entries to a supervisor session's extra so the
    existing :mod:`agents.lifecycle.agent_ops_log` rendering picks them
    up. No new vault layout — we reuse the agent-ops audit folder.

    Best-effort: if workflow_state can't be reached the hook is a no-op
    (the audit info is still echoed to journalctl by the loop).
    """

    def _hook(
        *,
        problem: ProblemObject,
        entries: Sequence[AgentOpsEntry],
    ) -> None:
        if supervisor_session_id is None:
            logger.debug(
                "self-improvement obsidian hook: no supervisor session "
                "configured; skipping vault sync"
            )
            return
        if not entries:
            return
        try:
            from ..workflow_state import load_session, update_session

            session = load_session(supervisor_session_id)
        except Exception:  # noqa: BLE001
            logger.debug(
                "self-improvement obsidian hook: workflow_state load failed",
                exc_info=True,
            )
            return
        if session is None:
            return
        from .agent_ops_log import append_agent_ops_audit

        extra = dict(getattr(session, "extra", None) or {})
        for entry in entries:
            extra = dict(append_agent_ops_audit(extra, entry))
        try:
            from dataclasses import replace as _replace

            updated = _replace(session, extra=extra)
            update_session(updated, now=datetime.now(tz=timezone.utc))
        except Exception:  # noqa: BLE001
            logger.debug(
                "self-improvement obsidian hook: workflow_state update failed",
                exc_info=True,
            )

    return _hook


# ---------------------------------------------------------------------------
# Top-level factory — wires the whole thing
# ---------------------------------------------------------------------------


def build_self_improvement_dispatcher(
    *,
    job_queue: Any,
    heartbeat_store: Any,
    env: Optional[Mapping[str, str]] = None,
    operator_action_poster: Optional[Callable[..., Optional[str]]] = None,
    executor_enqueue_fn: Optional[Callable[..., Optional[str]]] = None,
    obsidian_supervisor_session_id: Optional[str] = None,
    worktree_provisioner: Optional[WorktreeProvisioner] = None,
    session_loader: Optional[Callable[[], Sequence[Any]]] = None,
    repo_full_name: Optional[str] = None,
    dry_run_default: bool = True,
) -> SelfImprovementDispatcher:
    """Compose every hook + ledger into a single dispatcher.

    The factory is env-aware: ledger / registry sidecars + cwd + base
    branch all come from ``YULE_SELF_IMPROVEMENT_*`` env keys with safe
    defaults so the supervisor works out of the box.
    """

    env = env if env is not None else os.environ

    ledger = ProblemLedger(ledger_path=default_ledger_path(env=env))
    rate_ledger = DelegatedRateLedger()
    registry_path: Optional[Path] = None
    raw_registry = (env.get(ENV_WORKTREE_REGISTRY_PATH) or "").strip()
    if raw_registry:
        registry_path = Path(raw_registry).expanduser()
    elif env.get("YULE_CACHE_DB_PATH"):
        registry_path = Path(env["YULE_CACHE_DB_PATH"]).expanduser().parent / (
            "self_improvement_worktrees.json"
        )
    worktree_registry = InMemoryWorktreeRegistry(sidecar_path=registry_path)

    provisioner: Optional[WorktreeProvisioner] = worktree_provisioner
    if provisioner is None:
        provisioner = GitWorktreeProvisioner()

    repo_cwd = (env.get(ENV_REPO_CWD) or env.get("YULE_REPO_ROOT") or os.getcwd())
    base_branch = (env.get(ENV_BASE_BRANCH) or "main").strip() or "main"

    observation_provider = build_observation_provider(
        job_queue=job_queue,
        heartbeat_store=heartbeat_store,
        session_loader=session_loader,
    )
    operator_hook = build_operator_action_hook(poster=operator_action_poster)
    executor_hook = build_executor_handoff_hook(
        enqueue_fn=executor_enqueue_fn,
        repo_full_name=repo_full_name,
        dry_run_default=dry_run_default,
    )
    obsidian_hook = build_obsidian_record_hook(
        supervisor_session_id=obsidian_supervisor_session_id
    )

    return SelfImprovementDispatcher(
        observation_provider=observation_provider,
        problem_ledger=ledger,
        rate_ledger=rate_ledger,
        worktree_registry=worktree_registry,
        worktree_provisioner=provisioner,
        operator_action_hook=operator_hook,
        executor_handoff_hook=executor_hook,
        obsidian_record_hook=obsidian_hook,
        repo_cwd=str(repo_cwd),
        base_branch=base_branch,
    )


__all__ = (
    "DEFAULT_INTERVAL_SECONDS",
    "ENV_BASE_BRANCH",
    "ENV_DAILY_CAP",
    "ENV_ENABLED",
    "ENV_INTERVAL_SECONDS",
    "ENV_LEDGER_PATH",
    "ENV_REPO_CWD",
    "ENV_WORKTREE_REGISTRY_PATH",
    "ENV_WORKTREE_ROOT",
    "build_executor_handoff_hook",
    "build_observation_provider",
    "build_obsidian_record_hook",
    "build_operator_action_hook",
    "build_self_improvement_dispatcher",
    "is_enabled",
    "resolve_interval_seconds",
)
