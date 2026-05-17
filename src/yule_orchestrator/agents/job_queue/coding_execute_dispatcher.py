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
    Iterable,
    Iterator,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

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


# ---------------------------------------------------------------------------
# Session iteration helpers
# ---------------------------------------------------------------------------


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

    def has_been_dispatched(self) -> bool:
        extra = getattr(self.session, "extra", None) or {}
        marker = extra.get(SESSION_EXTRA_DISPATCH_KEY)
        if not isinstance(marker, Mapping):
            return False
        return bool(marker.get("job_id"))


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
) -> Iterator[ReadyCodingJob]:
    """Yield workflow sessions whose persisted coding_job is ``ready``.

    *session_loader* defaults to :func:`agents.workflow_state.list_sessions`
    with the standard limit. Tests inject a callable returning a fixed
    sequence so the iteration logic can be exercised without SQLite.

    Sessions that already have a dispatch marker are skipped unless
    *include_dispatched* is True (selector-side queries set this so
    the runtime's "what next?" surface still sees in-flight work).
    """

    loader = session_loader or _default_session_loader
    try:
        sessions = list(loader() or ())
    except Exception:  # noqa: BLE001 - cache hiccup shouldn't crash dispatch
        logger.warning("iter_ready_coding_jobs: session loader raised", exc_info=True)
        return

    for session in sessions:
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
        if not include_dispatched and ready.has_been_dispatched():
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
    """

    session_id: str
    executor_role: str
    job_id: Optional[str]
    created: bool
    request: Optional[CodingExecuteRequest] = None
    error: Optional[str] = None


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


def dispatch_ready_coding_jobs(
    *,
    worker: CodingExecutorWorker,
    session_loader: Optional[Callable[[], Iterable[Any]]] = None,
    update_session_fn: Optional[Callable[..., Any]] = None,
    env: Optional[Mapping[str, str]] = None,
    now: Optional[datetime] = None,
) -> Tuple[DispatchedCodingJob, ...]:
    """Run one producer cycle.

    Pulls every ``coding_job=ready`` session, builds the executor
    request, calls ``worker.enqueue`` (idempotent via the worker's own
    ``find_active`` dedup), then stamps the dispatch marker so the
    next call skips. Safe to invoke from a periodic scheduler tick or
    directly after the user's "수정 승인" message.
    """

    out: list[DispatchedCodingJob] = []
    for ready in iter_ready_coding_jobs(session_loader=session_loader):
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
    "ENV_DEFAULT_BASE_BRANCH",
    "ENV_DEFAULT_REPO",
    "ENV_DRY_RUN",
    "JOB_TYPE_CODING_EXECUTE",
    "READY_STATUSES",
    "ReadyCodingJob",
    "SESSION_EXTRA_CODING_JOB_KEY",
    "SESSION_EXTRA_DISPATCH_KEY",
    "WorkflowSessionState",
    "build_coding_execute_request",
    "dispatch_ready_coding_jobs",
    "iter_ready_coding_jobs",
)
