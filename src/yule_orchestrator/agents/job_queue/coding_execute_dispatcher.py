"""Production wiring between approved coding_jobs and the executor worker.

Round 3 of #73. Round 1 + 2 landed:

  * the ``CodingExecutorWorker`` (Protocol seams + 7-step pipeline),
  * ``coding_executor_live`` (live Protocol implementations under one
    ``build_live_executor`` factory),
  * the ``ci_status`` retry policy + selector guard.

What was *missing* was the producer: when the engineering Discord
gateway flips ``session.extra['coding_job']`` to ``status="ready"``,
nothing actually enqueued a ``coding_execute`` row. Operators had to
manually call the worker's ``enqueue`` or run a hand-crafted CLI.

This module closes that gap:

  * :func:`iter_ready_coding_jobs` scans
    :func:`agents.workflow_state.list_sessions` for sessions whose
    ``extra['coding_job']`` is ``status="ready"`` and which haven't
    been dispatched yet (no ``extra['coding_execute_dispatch']`` or
    a previous attempt that already terminal'd cleanly).
  * :func:`build_coding_execute_request` lifts the persisted
    coding_job dict into a :class:`CodingExecuteRequest` that the
    worker accepts. Repo / base_branch / dry_run come from a
    deterministic precedence: ``coding_job.metadata`` →
    ``session.extra`` → injected env defaults.
  * :func:`dispatch_ready_coding_jobs` runs the full producer cycle
    once: load sessions, enqueue (idempotent via
    ``CodingExecutorWorker.find_active``), stamp
    ``extra['coding_execute_dispatch']`` so the next scan skips
    the row.
  * :class:`WorkflowSessionState` implements
    :class:`SessionStateLike` from the next-task selector so the
    same data also drives the runtime's "what next?" priority chain.

Pure-side safety:

  * The dispatcher *only* writes a single ``coding_execute_dispatch``
    audit dict back onto the session — no other session fields move.
  * Persistence is best-effort: a failed cache write logs a warning
    and the session stays "ready" so the next tick re-tries.
  * ``forbidden_scope`` from the persisted coding_job carries through
    unchanged. Hard rails (``is_protected_branch``, force-push) live
    on the worker; the dispatcher never relaxes them.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    Any,
    Callable,
    FrozenSet,
    Iterable,
    Iterator,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from .state_machine import JobState

from .coding_executor_worker import (
    CodingExecuteRequest,
    CodingExecutorWorker,
    JOB_TYPE_CODING_EXECUTE,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — env contract + session.extra keys
# ---------------------------------------------------------------------------


# session.extra key that the dispatcher writes after a successful enqueue.
# Exposing it as a constant lets tests + downstream readers keep one
# source of truth rather than re-typing the literal string.
SESSION_EXTRA_DISPATCH_KEY: str = "coding_execute_dispatch"
SESSION_EXTRA_CODING_JOB_KEY: str = "coding_job"

# Env contract — operator-tunable defaults applied when the persisted
# coding_job doesn't carry an explicit value.
ENV_DEFAULT_REPO: str = "YULE_CODING_EXECUTOR_REPO"
ENV_DEFAULT_BASE_BRANCH: str = "YULE_CODING_EXECUTOR_BASE_BRANCH"
ENV_DRY_RUN: str = "YULE_CODING_EXECUTOR_DRY_RUN"

DEFAULT_BASE_BRANCH: str = "main"


# Ready coding_job statuses we'll dispatch on. Anything else (pending
# approval, cancelled, in_progress already, completed, failed) is left
# alone — dispatch is idempotent + only fires on the explicit ready
# transition the gateway makes after the user types an approval phrase.
READY_STATUSES: frozenset[str] = frozenset({"ready"})


# P1-Z B — terminal session resurrection 차단 — sibling module 위임.
# helpers 본체는 ``coding_execute_terminal_skip`` 모듈에 있다 (dispatcher
# 자체 LOC 를 1000 임계 안에 유지).  여기서는 re-export 만.
from .coding_execute_terminal_skip import (
    SESSION_EXTRA_TERMINAL_SKIP_KEY,
    TERMINAL_SESSION_STATES,
    is_terminal_session as _is_terminal_session,
    stamp_terminal_session_skip as _stamp_terminal_session_skip_external,
)


# ---------------------------------------------------------------------------
# Session iteration helpers
# ---------------------------------------------------------------------------


# P0-Z: 어떤 JobState 들이 "dispatch 가 아직 살아있다" 로 인정되는지.
# 본 집합 밖이면 (e.g., FAILED_TERMINAL / SAVED 후 시간이 지난 row) 의
# 이전 dispatch marker 는 phantom 으로 분류, ready session 을 다시 enqueue
# 한다. canonical session ``11917bf1e75d`` 처럼 marker.job_id 는 있지만
# queue row 자체가 통째로 사라진 케이스가 본 분기의 핵심 motivation.
_ACTIVE_DISPATCH_STATES: FrozenSet[Any] = frozenset(
    {
        JobState.DISCOVERED,
        JobState.QUEUED,
        JobState.ASSIGNED,
        JobState.IN_PROGRESS,
        JobState.WAITING_FOR_ROLE,
        JobState.RESEARCHING,
        JobState.PENDING_APPROVAL,
        JobState.READY_FOR_OBSIDIAN,
    }
)


# Status tokens surfaced via DispatchedCodingJob.audit + status surface
# so operators can grep "phantom" / "stale" / "marker_*" in CI / Discord
# / troubleshooting ledger.
MARKER_STATE_VALID: str = "valid"
MARKER_STATE_MISSING: str = "missing"
MARKER_STATE_STALE: str = "stale"
# P1-C — failed_retryable / saved / failed_terminal 는 옛 P0-Z 코드에서
# 모두 phantom 으로 합쳐 처리했지만, 실제로는 "queue 가 이미 그 dispatch
# 의 결과를 알고 있는 상태" 다. 새 row 를 만들면 attempt 카운터가
# 초기화돼 ``max_attempts`` / backoff 가 무력화되고 infinite re-enqueue
# 가 일어난다. 본 2 종은 producer 가 절대 새로 enqueue 하면 안 되며,
# queue 의 retry semantics (``requeue_retryable``) 가 책임진다.
MARKER_STATE_PENDING_RETRY: str = "pending_retry"
MARKER_STATE_TERMINAL: str = "terminal"
PHANTOM_MARKER_REASON_NO_ROW: str = "marker_job_id_not_in_queue"
PHANTOM_MARKER_REASON_WRONG_TYPE: str = "marker_wrong_job_type"
PHANTOM_MARKER_REASON_WRONG_SESSION: str = "marker_wrong_session_id"
# P1-C: kept for backward-compat — older callers/tests reference this
# string. Producer 의 새 동작은 PENDING_RETRY / TERMINAL 분기로 분리.
PHANTOM_MARKER_REASON_TERMINAL: str = "marker_row_terminal_or_inactive"


@dataclass(frozen=True)
class DispatchMarkerCheck:
    """Outcome of :func:`validate_coding_dispatch_marker`.

    *state* is one of:

      * ``valid``   — marker 존재 + queue row 존재 + active state.
      * ``missing`` — marker 자체가 없음 (한 번도 dispatch 안 됨 OR
        cleared 됨). caller 는 새로 enqueue 가능.
      * ``stale``  — marker 는 있지만 queue 와 invariant 어긋남
        (phantom row / cross-talk / terminal). caller 는 self-heal
        후 re-enqueue 해야 한다.

    *reason* 은 stale 일 때만 ``PHANTOM_MARKER_REASON_*`` 중 하나.
    *marker_job_id* 는 audit 용 (없으면 None).
    """

    state: str
    reason: Optional[str] = None
    marker_job_id: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return self.state == MARKER_STATE_VALID

    @property
    def is_stale(self) -> bool:
        return self.state == MARKER_STATE_STALE


# P1-C — queue retry semantics 를 producer 가 침범하지 않게 분리.
_PENDING_RETRY_STATES: FrozenSet[Any] = frozenset(
    {JobState.FAILED_RETRYABLE}
)
_TERMINAL_STATES: FrozenSet[Any] = frozenset(
    {JobState.SAVED, JobState.FAILED_TERMINAL}
)


def validate_coding_dispatch_marker(
    *, session: Any, queue: Any
) -> DispatchMarkerCheck:
    """**Public SSoT** — session.extra ↔ job_queue invariant check.

    Invariant (P0-Z fix): ``session.extra['coding_execute_dispatch']``
    가 존재하면 그 ``job_id`` 의 queue row 도 같은 사실을 들고 있어야
    한다. 구체적으로:

      * queue.get(job_id) exists
      * row.job_type == ``coding_execute``
      * row.session_id == current session
      * row.state ∈ ``_ACTIVE_DISPATCH_STATES``

    위 중 하나라도 어긋나면 stale marker 로 분류. caller (producer /
    status / troubleshooting) 가 동일 SSoT 를 호출해 같은 결정을 내릴
    수 있도록 본 함수만 새로 추가 / 옛 ``has_been_dispatched`` 도 본
    함수를 호출하게 정렬했다.

    Queue lookup 자체가 raise 하면 보수적으로 valid 로 본다 — DB hiccup
    한 번이 active row 를 잘못 phantom 처리해 중복 enqueue 되는 걸
    막는다.
    """

    extra = getattr(session, "extra", None) or {}
    marker = extra.get(SESSION_EXTRA_DISPATCH_KEY)
    if not isinstance(marker, Mapping):
        return DispatchMarkerCheck(state=MARKER_STATE_MISSING)
    job_id = str(marker.get("job_id") or "").strip()
    if not job_id:
        return DispatchMarkerCheck(state=MARKER_STATE_MISSING)

    session_id = str(getattr(session, "session_id", "") or "").strip()
    try:
        row = queue.get(job_id)
    except Exception:  # noqa: BLE001 - DB hiccup → assume valid
        return DispatchMarkerCheck(
            state=MARKER_STATE_VALID, marker_job_id=job_id
        )
    if row is None:
        return DispatchMarkerCheck(
            state=MARKER_STATE_STALE,
            reason=PHANTOM_MARKER_REASON_NO_ROW,
            marker_job_id=job_id,
        )
    row_type = str(getattr(row, "job_type", "") or "").strip()
    if row_type and row_type != JOB_TYPE_CODING_EXECUTE:
        return DispatchMarkerCheck(
            state=MARKER_STATE_STALE,
            reason=PHANTOM_MARKER_REASON_WRONG_TYPE,
            marker_job_id=job_id,
        )
    row_session = str(getattr(row, "session_id", "") or "").strip()
    if row_session and session_id and row_session != session_id:
        return DispatchMarkerCheck(
            state=MARKER_STATE_STALE,
            reason=PHANTOM_MARKER_REASON_WRONG_SESSION,
            marker_job_id=job_id,
        )
    row_state = getattr(row, "state", None)
    if row_state in _PENDING_RETRY_STATES:
        # P1-C: queue retry semantics (``requeue_retryable``) 가 책임지는
        # 상태. producer 가 새 row 를 만들면 attempt 카운터 / backoff 가
        # 무력화돼 무한 재실행. caller (iter_ready_coding_jobs) 가 skip.
        return DispatchMarkerCheck(
            state=MARKER_STATE_PENDING_RETRY,
            reason=None,
            marker_job_id=job_id,
        )
    if row_state in _TERMINAL_STATES:
        # SAVED / FAILED_TERMINAL — dispatch 는 이미 결과까지 도달. producer
        # 가 다시 enqueue 할 일은 없음.
        return DispatchMarkerCheck(
            state=MARKER_STATE_TERMINAL,
            reason=None,
            marker_job_id=job_id,
        )
    if row_state not in _ACTIVE_DISPATCH_STATES:
        return DispatchMarkerCheck(
            state=MARKER_STATE_STALE,
            reason=PHANTOM_MARKER_REASON_TERMINAL,
            marker_job_id=job_id,
        )
    return DispatchMarkerCheck(
        state=MARKER_STATE_VALID, marker_job_id=job_id
    )


def _validate_dispatch_marker_against_queue(
    *,
    marker: Mapping[str, Any],
    session_id: str,
    queue: Any,
) -> Tuple[bool, Optional[str]]:
    """Thin tuple-style wrapper around :func:`validate_coding_dispatch_marker`.

    Preserved so older internal callers keep their import path. New code
    should call :func:`validate_coding_dispatch_marker` directly to get
    the structured :class:`DispatchMarkerCheck`.
    """

    # build a transient session view honoring the existing marker contract
    transient = type(
        "_TransientSession",
        (),
        {
            "extra": {SESSION_EXTRA_DISPATCH_KEY: dict(marker)},
            "session_id": session_id,
        },
    )()
    check = validate_coding_dispatch_marker(session=transient, queue=queue)
    return check.is_valid, (None if check.is_valid else check.reason)


@dataclass(frozen=True)
class ReadyCodingJob:
    """Snapshot of an approved-coding_job session ready for dispatch.

    ``coding_job`` is the persisted ``session.extra['coding_job']``
    dict (already serialised by :func:`agents.coding.job.CodingJob.to_dict`).
    ``session`` carries the full WorkflowSession for callers that need
    to write back (the dispatcher uses it to stamp dispatch metadata).
    """

    session: Any
    coding_job: Mapping[str, Any]
    session_id: str

    def executor_role(self) -> str:
        return str(self.coding_job.get("executor_role") or "").strip()

    def has_been_dispatched(
        self, *, queue: Optional[Any] = None
    ) -> bool:
        """Return True iff a marker exists AND queue confirms the row is
        in a state the producer should NOT bypass.

        P0-Z + P1-C: 옛 동작은 marker 존재만 보고 True 반환 → stranded
        deadlock. P0-Z 가 queue-aware 검증 도입했지만 ``failed_retryable``
        도 phantom 으로 잡아 infinite re-enqueue. 본 함수는 다음을
        "dispatched (producer skip)" 로 본다:

          * MARKER_STATE_VALID (active row)
          * MARKER_STATE_PENDING_RETRY (queue retry semantics handle)
          * MARKER_STATE_TERMINAL (saved / failed_terminal)

        나머지 (missing / stale) 만 caller 가 re-enqueue 후보로 본다.
        """

        extra = getattr(self.session, "extra", None) or {}
        marker = extra.get(SESSION_EXTRA_DISPATCH_KEY)
        if not isinstance(marker, Mapping) or not marker.get("job_id"):
            return False
        if queue is None:
            return True
        check = validate_coding_dispatch_marker(
            session=self.session, queue=queue
        )
        return check.state in (
            MARKER_STATE_VALID,
            MARKER_STATE_PENDING_RETRY,
            MARKER_STATE_TERMINAL,
        )

    def dispatch_marker_phantom_reason(
        self, *, queue: Any
    ) -> Optional[str]:
        """Return the phantom reason token (or None when marker is valid /
        absent). Useful for audit / log lines.
        """

        extra = getattr(self.session, "extra", None) or {}
        marker = extra.get(SESSION_EXTRA_DISPATCH_KEY)
        if not isinstance(marker, Mapping) or not marker.get("job_id"):
            return None
        is_valid, reason = _validate_dispatch_marker_against_queue(
            marker=marker, session_id=self.session_id, queue=queue
        )
        return None if is_valid else reason


def _read_coding_job(session: Any) -> Optional[Mapping[str, Any]]:
    extra = getattr(session, "extra", None)
    if not isinstance(extra, Mapping):
        return None
    payload = extra.get(SESSION_EXTRA_CODING_JOB_KEY)
    if not isinstance(payload, Mapping):
        return None
    return payload


def iter_ready_coding_jobs(
    *,
    session_loader: Optional[Callable[[], Iterable[Any]]] = None,
    include_dispatched: bool = False,
    queue: Optional[Any] = None,
) -> Iterator[ReadyCodingJob]:
    """Yield workflow sessions whose persisted coding_job is ``ready``.

    *session_loader* defaults to :func:`agents.workflow_state.list_sessions`
    with the standard limit. Tests inject a callable returning a fixed
    sequence so the iteration logic can be exercised without SQLite.

    Sessions that already have a dispatch marker are skipped unless
    *include_dispatched* is True (selector-side queries set this so
    the runtime's "what next?" surface still sees in-flight work).

    P0-Z phantom-marker fix: *queue* 가 inject 되면 marker 의 job_id 를
    queue 와 cross-check 한다. queue row 가 없거나 wrong type / wrong
    session / terminal state 면 phantom marker 로 분류, 본 함수가 ready
    session 을 yield 해서 caller (dispatcher) 가 새 row 를 enqueue 할 수
    있게 한다. 옛 동작 (queue=None) 은 변경 없음.
    """

    loader = session_loader or _default_session_loader
    try:
        sessions = list(loader() or ())
    except Exception:  # noqa: BLE001 - cache hiccup shouldn't crash dispatch
        logger.warning("iter_ready_coding_jobs: session loader raised", exc_info=True)
        return

    for session in sessions:
        # P1-Z B — terminal session 은 marker self-heal 대상에서 제외.
        # rejected/completed 인데 coding_job=ready 가 extra 에 남아있으면
        # 옛 wiring 은 새 coding_execute row 를 만들었다.  operator 가
        # 폐기한 세션이 runtime restart 후 살아나는 회귀의 직접 원인.
        if _is_terminal_session(session):
            continue
        coding_job = _read_coding_job(session)
        if not coding_job:
            continue
        status = str(coding_job.get("status") or "").strip().lower()
        if status not in READY_STATUSES:
            continue
        ready = ReadyCodingJob(
            session=session,
            coding_job=dict(coding_job),
            session_id=str(coding_job.get("session_id") or getattr(session, "session_id", "")),
        )
        if not ready.session_id or not ready.executor_role():
            continue
        if not include_dispatched and ready.has_been_dispatched(queue=queue):
            continue
        yield ready




def _default_session_loader() -> Sequence[Any]:
    from ..workflow_state import list_sessions

    return list_sessions(limit=200)


# ---------------------------------------------------------------------------
# CodingExecuteRequest builder
# ---------------------------------------------------------------------------


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_truthy_env(value: Optional[str]) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_repo(coding_job: Mapping[str, Any], session: Any, env_repo: Optional[str]) -> str:
    """Resolve repo_full_name with deterministic precedence.

    Order: ``coding_job.metadata['repo_full_name']`` →
    ``session.extra['github_work_order_issue']['repo']`` (P0-S anchor) →
    ``session.extra['coding_repo_full_name']`` → env default. Empty
    string is acceptable — the worker only refuses to push when the
    GithubAppPusher is invoked on an empty repo, which the dry-run
    + RecordOnly editor short-circuits correctly.
    """

    metadata = coding_job.get("metadata") or {}
    if isinstance(metadata, Mapping):
        from_meta = str(metadata.get("repo_full_name") or "").strip()
        if from_meta:
            return from_meta
    extra = getattr(session, "extra", None) or {}
    if isinstance(extra, Mapping):
        # P0-S — issue anchor 가 stamp 한 repo 를 fallback 으로 사용. issue
        # auto-create 후 같은 session 의 coding_job 이 metadata.repo_full_name
        # 을 못 채운 경우 (legacy proposal 빌더) 에도 자연스럽게 연결.
        anchor = extra.get("github_work_order_issue")
        if isinstance(anchor, Mapping):
            from_anchor = str(anchor.get("repo") or "").strip()
            if from_anchor:
                return from_anchor
        from_extra = str(extra.get("coding_repo_full_name") or "").strip()
        if from_extra:
            return from_extra
    return (env_repo or "").strip()


def _resolve_base_branch(coding_job: Mapping[str, Any], env_base: Optional[str]) -> str:
    metadata = coding_job.get("metadata") or {}
    if isinstance(metadata, Mapping):
        candidate = str(metadata.get("base_branch") or "").strip()
        if candidate:
            return candidate
    if env_base and env_base.strip():
        return env_base.strip()
    return DEFAULT_BASE_BRANCH


def _resolve_dry_run(coding_job: Mapping[str, Any], env_dry_run: Optional[str]) -> bool:
    """Default dry_run unless an explicit signal flips it false.

    The worker's hard rail interprets ``dry_run=True`` as "exercise the
    spec without invoking any Protocol". Production push is the
    risky path so we keep dry_run=True unless either the persisted
    coding_job or the env explicitly opts out. The env value wins
    when both are set (operator override).
    """

    metadata = coding_job.get("metadata") or {}
    metadata_dry: Optional[bool] = None
    if isinstance(metadata, Mapping):
        raw = metadata.get("dry_run")
        if raw is not None:
            metadata_dry = bool(raw)
    if env_dry_run is not None and env_dry_run.strip():
        flag = env_dry_run.strip().lower()
        if flag in {"0", "false", "no", "off"}:
            return False
        if flag in {"1", "true", "yes", "on"}:
            return True
    if metadata_dry is not None:
        return metadata_dry
    return True


def build_coding_execute_request(
    ready: ReadyCodingJob,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> CodingExecuteRequest:
    """Translate a :class:`ReadyCodingJob` into a worker request.

    The returned request matches the worker's payload contract — the
    caller can then ``worker.enqueue(request)`` directly. The function
    is pure: no I/O, no mutation of *ready*.
    """

    env_map = env if env is not None else os.environ
    coding_job = ready.coding_job

    metadata_in = coding_job.get("metadata") or {}
    proposal_metadata = (
        metadata_in.get("proposal_metadata")
        if isinstance(metadata_in, Mapping)
        else {}
    ) or {}

    issue_number = _coerce_int(
        (metadata_in.get("issue_number") if isinstance(metadata_in, Mapping) else None)
        or (
            proposal_metadata.get("issue_number")
            if isinstance(proposal_metadata, Mapping)
            else None
        )
    )
    # P0-S — issue anchor fallback. coding_job.metadata 에 issue_number 가
    # 없어도 session.extra["github_work_order_issue"]["issue_number"] 가
    # 있으면 그쪽을 사용. issue-less bootstrap path 가 metadata 채움 직전에
    # 호출되는 경우의 안전망.
    if issue_number is None:
        session_extra = getattr(ready.session, "extra", None) or {}
        if isinstance(session_extra, Mapping):
            anchor = session_extra.get("github_work_order_issue")
            if isinstance(anchor, Mapping):
                issue_number = _coerce_int(anchor.get("issue_number"))

    branch_hint = ""
    if isinstance(metadata_in, Mapping):
        branch_hint = str(metadata_in.get("branch_hint") or "").strip()
    if not branch_hint and isinstance(proposal_metadata, Mapping):
        branch_hint = str(proposal_metadata.get("branch_hint") or "").strip()

    forwarded_metadata = {}
    if isinstance(metadata_in, Mapping):
        # Pass through known keys the executor or test runner consume.
        for key in ("test_command", "executor_runner_id", "review_roles"):
            if key in metadata_in:
                forwarded_metadata[key] = metadata_in[key]
    forwarded_metadata.setdefault(
        "approved_at",
        coding_job.get("approved_at"),
    )
    forwarded_metadata.setdefault(
        "review_roles",
        list(coding_job.get("review_roles") or ()),
    )

    # P1-M D — slice_spec + session_prompt + work_mode 도 forward 해서
    # PR 생성 단계의 한국어 humanizer 가 사용. slice_spec 은 backlog 의
    # 첫 항목 또는 coding_job 자체의 spec.
    session_extra_meta = getattr(ready.session, "extra", None) or {}
    if isinstance(session_extra_meta, Mapping):
        slice_spec = (
            coding_job.get("slice_spec")
            if isinstance(coding_job, Mapping)
            else None
        )
        if slice_spec is None:
            slice_spec = session_extra_meta.get("current_coding_slice")
        if isinstance(slice_spec, Mapping):
            forwarded_metadata["slice_spec"] = dict(slice_spec)
        prompt_for_title = str(getattr(ready.session, "prompt", "") or "")
        if prompt_for_title:
            forwarded_metadata["session_prompt"] = prompt_for_title
        work_mode_val = session_extra_meta.get("work_mode")
        if work_mode_val:
            forwarded_metadata["work_mode"] = str(work_mode_val)

    return CodingExecuteRequest(
        session_id=ready.session_id,
        executor_role=ready.executor_role(),
        user_request=str(coding_job.get("user_request") or ""),
        generated_prompt=str(coding_job.get("generated_prompt") or ""),
        write_scope=tuple(
            str(s).strip() for s in (coding_job.get("write_scope") or ()) if str(s).strip()
        ),
        forbidden_scope=tuple(
            str(s).strip() for s in (coding_job.get("forbidden_scope") or ()) if str(s).strip()
        ),
        safety_rules=tuple(
            str(s).strip() for s in (coding_job.get("safety_rules") or ()) if str(s).strip()
        ),
        base_branch=_resolve_base_branch(
            coding_job, env_map.get(ENV_DEFAULT_BASE_BRANCH)
        ),
        branch_hint=branch_hint,
        repo_full_name=_resolve_repo(
            coding_job, ready.session, env_map.get(ENV_DEFAULT_REPO)
        ),
        issue_number=issue_number,
        dry_run=_resolve_dry_run(coding_job, env_map.get(ENV_DRY_RUN)),
        metadata=forwarded_metadata,
    )


# ---------------------------------------------------------------------------
# Dispatch (producer)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DispatchedCodingJob:
    """One row produced by :func:`dispatch_ready_coding_jobs`.

    ``created`` is False when the worker dedup'd against an existing
    in-flight job — the dispatcher still writes the dispatch marker
    so subsequent scans skip without re-checking the queue.
    ``error`` is non-empty when the enqueue raised; the marker is NOT
    persisted in that case so a transient failure self-recovers on
    the next tick.

    P0-Z phantom-marker fix: ``stale_marker_reason`` 가 set 되면 본
    session 의 옛 marker 가 phantom 으로 판정돼 본 tick 이 self-heal
    re-enqueue 를 수행했다는 뜻. operator surface (status / log /
    troubleshooting) 가 silent heal 이 되지 않게 노출한다.
    """

    session_id: str
    executor_role: str
    job_id: Optional[str]
    created: bool
    request: Optional[CodingExecuteRequest] = None
    error: Optional[str] = None
    stale_marker_reason: Optional[str] = None
    stale_marker_job_id: Optional[str] = None


def _persist_dispatch_marker(
    session: Any,
    *,
    job_id: str,
    request: CodingExecuteRequest,
    update_session_fn: Optional[Callable[..., Any]] = None,
    now: Optional[datetime] = None,
) -> bool:
    """Write the ``coding_execute_dispatch`` audit dict to the session.

    Returns True on persistence success. Failures log a warning and
    return False — the dispatcher logs the partial success but
    doesn't crash; the next tick will re-attempt and dedup against
    the worker queue's in-flight row.
    """

    if session is None:
        return False

    try:
        from dataclasses import replace as _replace
    except Exception:  # noqa: BLE001 - stdlib import shouldn't fail
        return False

    extra = dict(getattr(session, "extra", None) or {})
    when = (now or datetime.now(tz=timezone.utc)).replace(microsecond=0).isoformat()
    extra[SESSION_EXTRA_DISPATCH_KEY] = {
        "job_id": job_id,
        "executor_role": request.executor_role,
        "branch_hint": request.branch_hint,
        "dispatched_at": when,
        "dry_run": bool(request.dry_run),
        "repo_full_name": request.repo_full_name,
        "base_branch": request.base_branch,
    }

    # P0-Y marker correctness — actual queue row exists now, so progress
    # bucket gets the real ``coding_dispatch_queued`` marker (not the
    # earlier ``coding_job_ready`` stamp). operator surface 가 "큐에
    # 들어갔다" 고 말할 수 있는 정확한 시점.
    try:
        from .work_order_coding_continuation import (
            PROGRESS_CODING_DISPATCH_QUEUED,
            SESSION_EXTRA_PROGRESS_KEY,
        )
    except Exception:  # noqa: BLE001 - partial install
        PROGRESS_CODING_DISPATCH_QUEUED = None
        SESSION_EXTRA_PROGRESS_KEY = None
    if PROGRESS_CODING_DISPATCH_QUEUED and SESSION_EXTRA_PROGRESS_KEY:
        bucket = extra.get(SESSION_EXTRA_PROGRESS_KEY)
        if not isinstance(bucket, Mapping):
            bucket = {}
        bucket = dict(bucket)
        existing_entry = bucket.get(PROGRESS_CODING_DISPATCH_QUEUED)
        first_at = (
            existing_entry.get("at")
            if isinstance(existing_entry, Mapping) and existing_entry.get("at")
            else when
        )
        bucket[PROGRESS_CODING_DISPATCH_QUEUED] = {
            "at": first_at,
            "detail": {
                "job_id": job_id,
                "executor_role": request.executor_role,
                "repo_full_name": request.repo_full_name,
            },
        }
        extra[SESSION_EXTRA_PROGRESS_KEY] = bucket

    try:
        updated = _replace(session, extra=extra)
    except TypeError:
        return False

    persist = update_session_fn or _default_update_session
    try:
        persist(updated, now=(now or datetime.now(tz=timezone.utc)))
    except Exception:  # noqa: BLE001 - persistence is observability
        logger.warning(
            "coding_execute dispatcher: persisting dispatch marker raised",
            exc_info=True,
        )
        return False
    return True


def _default_update_session(session: Any, *, now: datetime) -> Any:
    from ..workflow_state import update_session

    return update_session(session, now=now)


def _stamp_terminal_session_skip(
    *,
    session_loader: Optional[Callable[[], Iterable[Any]]] = None,
    update_session_fn: Optional[Callable[..., Any]] = None,
    now: Optional[datetime] = None,
) -> int:
    """본체는 ``coding_execute_terminal_skip`` 모듈에 — 본 함수는 thin wrapper."""

    return _stamp_terminal_session_skip_external(
        session_loader=session_loader,
        update_session_fn=update_session_fn,
        coding_job_reader=_read_coding_job,
        dispatch_marker_key=SESSION_EXTRA_DISPATCH_KEY,
        ready_statuses=READY_STATUSES,
        now=now,
    )


def dispatch_ready_coding_jobs(
    *,
    worker: CodingExecutorWorker,
    session_loader: Optional[Callable[[], Iterable[Any]]] = None,
    update_session_fn: Optional[Callable[..., Any]] = None,
    env: Optional[Mapping[str, str]] = None,
    now: Optional[datetime] = None,
    validate_marker_against_queue: bool = True,
) -> Tuple[DispatchedCodingJob, ...]:
    """Run one producer cycle.

    Pulls every ``coding_job=ready`` session, builds the executor
    request, calls ``worker.enqueue`` (idempotent via the worker's own
    ``find_active`` dedup), then stamps the dispatch marker so the
    next call skips. Safe to invoke from a periodic scheduler tick or
    directly after the user's "수정 승인" message.

    P0-Z phantom-marker fix: *validate_marker_against_queue* (default
    True) 는 ``iter_ready_coding_jobs`` 에 worker._queue 를 inject 해
    marker 의 실제 queue row 존재성을 검증한다. phantom marker session
    은 본 함수가 normally yield → 같은 자리에서 re-enqueue 됨. duplicate
    enqueue 는 worker.find_active(...) 가 막아주므로 안전.
    """

    queue_for_validation = (
        getattr(worker, "_queue", None) if validate_marker_against_queue else None
    )

    # P1-Z B — pre-pass: rejected/completed session 에 ready coding_job 또는
    # dispatch marker 가 남아있으면 operator surface 에 ``terminal_session_skip``
    # audit 를 한 번 stamp 하고 넘어간다.  re-enqueue 는 absolutely 안 함.
    _stamp_terminal_session_skip(
        session_loader=session_loader,
        update_session_fn=update_session_fn,
        now=now,
    )

    out: list[DispatchedCodingJob] = []
    for ready in iter_ready_coding_jobs(
        session_loader=session_loader, queue=queue_for_validation
    ):
        stale_check: Optional[DispatchMarkerCheck] = None
        if queue_for_validation is not None:
            stale_check = validate_coding_dispatch_marker(
                session=ready.session, queue=queue_for_validation
            )
            # P1-C: pending_retry / terminal 는 producer 가 새 row 를 만들
            # 일이 아니다 — has_been_dispatched 가 이미 caller (iter_…)
            # 에서 skip 했지만, 만약 unusual race 로 여기까지 왔다면 stale
            # 라고 attribution 하지 않게 None 으로 reset.
            if stale_check.state in (
                MARKER_STATE_PENDING_RETRY,
                MARKER_STATE_TERMINAL,
                MARKER_STATE_VALID,
            ):
                stale_check = None
            if stale_check is not None and stale_check.is_stale:
                # P0-Z: NEVER silent — operator surface 가 self-heal 을
                # 한 줄로 본다. warning level (not info) 로 status surface
                # 에서도 noisy.
                logger.warning(
                    "coding_execute dispatcher: stale dispatch marker on "
                    "session=%s — reason=%s phantom_job_id=%s — re-enqueueing",
                    ready.session_id,
                    stale_check.reason,
                    stale_check.marker_job_id,
                )
        stale_reason_token = (
            stale_check.reason if stale_check is not None and stale_check.is_stale else None
        )
        stale_job_id_token = (
            stale_check.marker_job_id
            if stale_check is not None and stale_check.is_stale
            else None
        )
        try:
            request = build_coding_execute_request(ready, env=env)
        except Exception as exc:  # noqa: BLE001 - bad payload shouldn't kill loop
            logger.warning(
                "coding_execute dispatcher: build_request raised for session=%s",
                ready.session_id,
                exc_info=True,
            )
            out.append(
                DispatchedCodingJob(
                    session_id=ready.session_id,
                    executor_role=ready.executor_role(),
                    job_id=None,
                    created=False,
                    error=f"build_request failed: {exc}",
                    stale_marker_reason=stale_reason_token,
                    stale_marker_job_id=stale_job_id_token,
                )
            )
            continue

        try:
            job, created = worker.enqueue(request, now=(now.timestamp() if now else None))
        except Exception as exc:  # noqa: BLE001 - queue write may fail transiently
            logger.warning(
                "coding_execute dispatcher: worker.enqueue raised for session=%s",
                ready.session_id,
                exc_info=True,
            )
            out.append(
                DispatchedCodingJob(
                    session_id=ready.session_id,
                    executor_role=request.executor_role,
                    job_id=None,
                    created=False,
                    request=request,
                    error=f"enqueue failed: {exc}",
                    stale_marker_reason=stale_reason_token,
                    stale_marker_job_id=stale_job_id_token,
                )
            )
            continue

        _persist_dispatch_marker(
            ready.session,
            job_id=job.job_id,
            request=request,
            update_session_fn=update_session_fn,
            now=now,
        )
        out.append(
            DispatchedCodingJob(
                session_id=ready.session_id,
                executor_role=request.executor_role,
                job_id=job.job_id,
                created=created,
                request=request,
                stale_marker_reason=stale_reason_token,
                stale_marker_job_id=stale_job_id_token,
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# Selector adapter — feeds next_task_selector.SessionStateLike
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkflowSessionState:
    """Production :class:`SessionStateLike` implementation.

    Rounded out so the next-task selector can fire against real
    workflow_state without a custom shim. Keeps the implementation
    simple — we delegate the iteration to :func:`iter_ready_coding_jobs`
    and surface the per-row dict shape the selector documents.

    ``include_dispatched`` controls whether sessions whose jobs are
    already in-flight count as "approved coding job ready". Default
    is False so the selector doesn't re-pick a row the dispatcher
    already pushed onto the worker queue.
    """

    session_loader: Optional[Callable[[], Iterable[Any]]] = None
    include_dispatched: bool = False
    discussion_loader: Optional[Callable[[], Iterable[Mapping[str, Any]]]] = None

    def list_approved_coding_jobs(self) -> Sequence[Mapping[str, Any]]:
        rows: list[dict[str, Any]] = []
        for ready in iter_ready_coding_jobs(
            session_loader=self.session_loader,
            include_dispatched=self.include_dispatched,
        ):
            row = {
                "session_id": ready.session_id,
                "executor_role": ready.executor_role(),
                "coding_job": dict(ready.coding_job),
                "thread_id": getattr(ready.session, "thread_id", None),
                "channel_id": getattr(ready.session, "channel_id", None),
            }
            extra = getattr(ready.session, "extra", None) or {}
            if isinstance(extra, Mapping):
                marker = extra.get(SESSION_EXTRA_DISPATCH_KEY)
                if isinstance(marker, Mapping):
                    row["dispatch"] = dict(marker)
            rows.append(row)
        return rows

    def list_unresolved_discussion_threads(self) -> Sequence[Mapping[str, Any]]:
        if self.discussion_loader is None:
            return ()
        try:
            rows = list(self.discussion_loader() or ())
        except Exception:  # noqa: BLE001
            logger.warning(
                "WorkflowSessionState: discussion_loader raised",
                exc_info=True,
            )
            return ()
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, Mapping):
                normalized.append(dict(row))
        return normalized


__all__ = (
    "DEFAULT_BASE_BRANCH",
    "DispatchedCodingJob",
    "DispatchMarkerCheck",
    "ENV_DEFAULT_BASE_BRANCH",
    "ENV_DEFAULT_REPO",
    "ENV_DRY_RUN",
    "JOB_TYPE_CODING_EXECUTE",
    "MARKER_STATE_MISSING",
    "MARKER_STATE_PENDING_RETRY",
    "MARKER_STATE_STALE",
    "MARKER_STATE_TERMINAL",
    "MARKER_STATE_VALID",
    "PHANTOM_MARKER_REASON_NO_ROW",
    "PHANTOM_MARKER_REASON_TERMINAL",
    "PHANTOM_MARKER_REASON_WRONG_SESSION",
    "PHANTOM_MARKER_REASON_WRONG_TYPE",
    "READY_STATUSES",
    "ReadyCodingJob",
    "SESSION_EXTRA_CODING_JOB_KEY",
    "SESSION_EXTRA_DISPATCH_KEY",
    "WorkflowSessionState",
    "build_coding_execute_request",
    "validate_coding_dispatch_marker",
    "dispatch_ready_coding_jobs",
    "iter_ready_coding_jobs",
)
