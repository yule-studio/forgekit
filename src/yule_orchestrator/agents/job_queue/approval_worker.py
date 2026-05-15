"""approval_post job worker — A-M5a wiring foundation.

The engineering runtime produces several "needs approval" moments
(research promotion to Obsidian, work_report 마침표 직전 코드 변경
승인, …). Today the in-channel UX (``is_obsidian_approval`` phrase
detection) handles them inline inside the user's thread. M5a adds
a queue-based **broadcast** path: a single worker reads
``approval_post`` jobs, renders the request as a card, and posts to
the dedicated ``#승인-대기`` channel so the user can approve from
one place instead of having to re-find each thread.

This commit lands only the **foundation**:

  * :data:`JOB_TYPE_APPROVAL_POST`
  * :class:`ApprovalRequest` payload dataclass
  * :class:`ApprovalWorker` (idempotent enqueue + process_job +
    run_one + heartbeat)
  * :func:`render_approval_request` markdown builder

Routing connection (where the gateway / lifecycle status decides
to enqueue an approval card) is intentionally deferred — the
existing in-channel approval UX must keep working unchanged. The
M5a-2 follow-up wires the producers (research promotion / Obsidian
write / engineering write) and removes the placeholder TODO at the
top of :func:`ApprovalWorker.enqueue`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Mapping,
    Optional,
    Tuple,
    Union,
)

from .heartbeat import HeartbeatStore
from .state_machine import JobState
from .store import Job, JobQueue


JOB_TYPE_APPROVAL_POST: str = "approval_post"
SERVICE_ID_APPROVAL_WORKER: str = "eng-approval-worker"


# Approval kinds — one per business reason the engineering runtime
# pauses for explicit user confirmation. Keep these as plain strings
# so the queue's ``payload_json`` round-trips without custom encoders.
APPROVAL_KIND_RESEARCH_PROMOTION: str = "research_promotion"
APPROVAL_KIND_OBSIDIAN_WRITE: str = "obsidian_write"
APPROVAL_KIND_ENGINEERING_WRITE: str = "engineering_write"

# Operator action inbox (P0-S) — `#승인-대기` 가 approval-only 가 아니라
# 정보/접근/secret/판단 요청까지 같은 채널에서 처리하기 위한 새 kind 들.
# render 분기는 ``ApprovalRequest.extra['operator_action']`` 페이로드를
# :func:`yule_orchestrator.agents.operator_action.render_operator_action_card`
# 로 위임한다 — 같은 worker, 같은 dedup, 같은 reply router.
APPROVAL_KIND_INFO_REQUEST: str = "info_request"
APPROVAL_KIND_ACCESS_REQUEST: str = "access_request"
APPROVAL_KIND_SECRET_REQUEST: str = "secret_request"
APPROVAL_KIND_DECISION_REQUEST: str = "decision_request"

OPERATOR_ACTION_KINDS: Tuple[str, ...] = (
    APPROVAL_KIND_INFO_REQUEST,
    APPROVAL_KIND_ACCESS_REQUEST,
    APPROVAL_KIND_SECRET_REQUEST,
    APPROVAL_KIND_DECISION_REQUEST,
)


# Skipped reasons surfaced via :class:`ApprovalJobOutcome`. Made into
# constants so the gateway / status diagnostic can match exact values
# instead of fragile substring checks.
SKIPPED_DUPLICATE: str = "duplicate_in_flight"
SKIPPED_CLAIMED_BY_OTHER_WORKER: str = "claimed_by_other_worker"
SKIPPED_APPROVAL_CHANNEL_UNSET: str = "approval_channel_unset"


_ACTIVE_STATES: Tuple[JobState, ...] = (
    JobState.QUEUED,
    JobState.ASSIGNED,
    JobState.IN_PROGRESS,
    JobState.WAITING_FOR_ROLE,
    JobState.RESEARCHING,
    JobState.PENDING_APPROVAL,
    JobState.READY_FOR_OBSIDIAN,
)


@dataclass(frozen=True)
class ApprovalRequest:
    """Strongly-typed payload for an approval card.

    ``source_message_id`` participates in the dedup key so an operator
    re-posting the same intake doesn't queue a second card; it also
    lets the worker's render add a "이 thread 의 ... 메시지에서 시작된
    요청" reference line back to the originating thread.
    """

    session_id: str
    approval_kind: str
    title: str
    summary: str
    requested_action: str
    created_by: str
    source_channel_id: Optional[int] = None
    source_thread_id: Optional[int] = None
    source_message_id: Optional[int] = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ApprovalRequest":
        """Lift a queue-row ``payload_json`` dict back into a typed
        :class:`ApprovalRequest`. Used by :class:`ApprovalWorker`'s
        consumer side; tolerates missing optional keys so older rows
        (or partially-populated tests) don't crash the worker loop.
        """

        return cls(
            session_id=str(payload.get("session_id") or ""),
            approval_kind=str(payload.get("approval_kind") or ""),
            title=str(payload.get("title") or ""),
            summary=str(payload.get("summary") or ""),
            requested_action=str(payload.get("requested_action") or ""),
            created_by=str(payload.get("created_by") or ""),
            source_channel_id=_coerce_int(payload.get("source_channel_id")),
            source_thread_id=_coerce_int(payload.get("source_thread_id")),
            source_message_id=_coerce_int(payload.get("source_message_id")),
            extra=dict(payload.get("extra") or {}),
        )

    def to_payload(self) -> Mapping[str, Any]:
        """JSON-friendly mirror of the dataclass for SQLite storage."""

        return {
            "session_id": self.session_id,
            "approval_kind": self.approval_kind,
            "title": self.title,
            "summary": self.summary,
            "requested_action": self.requested_action,
            "created_by": self.created_by,
            "source_channel_id": self.source_channel_id,
            "source_thread_id": self.source_thread_id,
            "source_message_id": self.source_message_id,
            "extra": dict(self.extra),
        }


# Discord posting is always async in production; tests pass a sync
# stub. Accept either by typing the return as ``Union[..., Awaitable]``.
ApprovalPostFn = Callable[
    [ApprovalRequest, str], Union[Any, Awaitable[Any]]
]

#: Resolves the live ``#승인-대기`` channel id at process_job time.
#: Production wires this to a function that reads
#: ``DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID`` (or resolves the channel
#: by name); tests pass a closure returning a fixed id / None.
ApprovalChannelResolver = Callable[[], Optional[int]]


@dataclass(frozen=True)
class ApprovalJobOutcome:
    """Container returned by :meth:`ApprovalWorker.run_one`.

    ``post_result`` is whatever the post_fn returned (e.g. the posted
    Discord message id) so the caller can stash it on session.extra
    if needed. ``skipped_reason`` distinguishes the three non-error
    bail-out paths: duplicate in flight / picked by another worker /
    approval channel unset.
    """

    job: Optional[Job]
    post_result: Optional[Any] = None
    skipped_reason: Optional[str] = None


def render_approval_request(request: ApprovalRequest) -> str:
    """Render *request* as the markdown card the worker posts.

    Section order is intentionally compact so the operator can scan
    the card without scrolling — title + 1-line summary + requested
    action + source thread pointer + approval phrase hint. The
    rendered text is **stable across worker restarts** (no
    timestamps embedded) so dedup hits are visible by content too.

    P0-S — operator action 카드 (INFO/ACCESS/SECRET/DECISION) 는 별도
    렌더러를 가진다. ``request.extra['operator_action']`` 페이로드가
    있으면 그쪽으로 위임한다.
    """

    operator_payload = (request.extra or {}).get("operator_action")
    if isinstance(operator_payload, Mapping):
        # 지연 import — operator_action 은 agents/ 패키지 위에 있고
        # job_queue/ 는 그 자식이므로 상위 import 가 가능하다. circular
        # 방지로 함수 호출 시점에만 가져온다.
        from ..operator_action import (
            OperatorActionRequest,
            render_operator_action_card,
        )

        try:
            op_request = OperatorActionRequest.from_extra_payload(operator_payload)
        except Exception:  # noqa: BLE001 — payload 손상 시 기본 렌더로 fallback
            op_request = None
        if op_request is not None:
            return render_operator_action_card(op_request)

    kind_label = _APPROVAL_KIND_LABELS.get(
        request.approval_kind, request.approval_kind
    )
    lines = [
        f"**[승인 요청 — {kind_label}] {request.title}**",
        "",
        f"세션: `{request.session_id}` · 요청자: `{request.created_by}`",
    ]
    summary = (request.summary or "").strip()
    if summary:
        lines.append(f"요약: {summary}")
    action = (request.requested_action or "").strip()
    if action:
        lines.append(f"요청 액션: {action}")

    pointer_bits: list[str] = []
    if request.source_channel_id:
        pointer_bits.append(f"채널 `{request.source_channel_id}`")
    if request.source_thread_id:
        pointer_bits.append(f"thread `{request.source_thread_id}`")
    if request.source_message_id:
        pointer_bits.append(f"메시지 `{request.source_message_id}`")
    if pointer_bits:
        lines.append("출처: " + " / ".join(pointer_bits))

    lines.append("")
    lines.append(
        "승인하려면 `승인` / `이대로 진행` / `Obsidian 저장 승인` 중 "
        "하나로 답해 주세요. 거절은 `반려` / `보류` 입니다."
    )
    return "\n".join(lines)


_APPROVAL_KIND_LABELS: Mapping[str, str] = {
    APPROVAL_KIND_RESEARCH_PROMOTION: "리서치 결과 승격",
    APPROVAL_KIND_OBSIDIAN_WRITE: "Obsidian 저장",
    APPROVAL_KIND_ENGINEERING_WRITE: "코드 변경",
    APPROVAL_KIND_INFO_REQUEST: "정보 필요",
    APPROVAL_KIND_ACCESS_REQUEST: "접근 / 권한 필요",
    APPROVAL_KIND_SECRET_REQUEST: "Secret 필요",
    APPROVAL_KIND_DECISION_REQUEST: "정책 / 제품 판단 필요",
}


class ApprovalWorker:
    """Idempotent worker for ``approval_post`` jobs."""

    def __init__(
        self,
        *,
        queue: JobQueue,
        post_fn: ApprovalPostFn,
        channel_resolver: ApprovalChannelResolver,
        heartbeats: Optional[HeartbeatStore] = None,
        worker_id: Optional[str] = None,
    ) -> None:
        self._queue = queue
        self._post_fn = post_fn
        self._channel_resolver = channel_resolver
        self._heartbeats = heartbeats
        self._worker_id = (
            worker_id or f"{SERVICE_ID_APPROVAL_WORKER}:{os.getpid()}"
        )

    # ------------------------------------------------------------------
    # Producer side
    # ------------------------------------------------------------------

    def find_active(
        self,
        *,
        session_id: str,
        approval_kind: str,
        source_message_id: Optional[int] = None,
    ) -> Optional[Job]:
        """Return any non-terminal ``approval_post`` job for the
        ``(session_id, approval_kind, source_message_id)`` triple.

        ``source_message_id=None`` is treated as a wildcard so a
        general "obsidian save" approval (no specific message) and
        a "obsidian save for message 12345" approval don't collide.
        """

        if not session_id or not approval_kind:
            return None
        for job in self._queue.list_for_session(
            session_id, states=_ACTIVE_STATES
        ):
            if job.job_type != JOB_TYPE_APPROVAL_POST:
                continue
            payload = job.payload or {}
            if str(payload.get("approval_kind") or "") != approval_kind:
                continue
            existing_src = payload.get("source_message_id")
            if (
                source_message_id is not None
                and existing_src is not None
                and int(existing_src) != int(source_message_id)
            ):
                continue
            return job
        return None

    def enqueue(
        self,
        request: ApprovalRequest,
        *,
        priority: int = 0,
        max_attempts: int = 3,
        now: Optional[float] = None,
    ) -> Tuple[Job, bool]:
        """Idempotent enqueue.

        Returns ``(job, created)``. *created* is False when an
        active row already exists — gateway uses that as the signal
        to skip showing "승인 카드 게시 요청 보냈음" twice.
        """

        existing = self.find_active(
            session_id=request.session_id,
            approval_kind=request.approval_kind,
            source_message_id=request.source_message_id,
        )
        if existing is not None:
            return existing, False
        job = self._queue.enqueue(
            session_id=request.session_id,
            job_type=JOB_TYPE_APPROVAL_POST,
            payload=request.to_payload(),
            priority=priority,
            max_attempts=max_attempts,
            now=now,
        )
        return job, True

    # ------------------------------------------------------------------
    # Consumer side
    # ------------------------------------------------------------------

    async def process_job(
        self,
        job: Job,
        *,
        now: Optional[float] = None,
    ) -> ApprovalJobOutcome:
        """Drive *job* through ``assigned → in_progress → saved`` (or
        ``failed_retryable``) using the worker's bound ``post_fn``.

        Channel-unset is handled as a **retryable** failure rather
        than a terminal one — the operator can correct the env and
        the M2 reaper / a manual requeue replays the post. We don't
        want a missing channel to silently swallow approval cards.
        """

        if self._heartbeats is not None:
            try:
                self._heartbeats.record(
                    SERVICE_ID_APPROVAL_WORKER,
                    pid=os.getpid(),
                    metadata={"job_id": job.job_id},
                    now=now,
                )
            except Exception:  # noqa: BLE001 - heartbeat is observability only
                pass

        in_progress = self._queue.transition(
            job.job_id, JobState.IN_PROGRESS, now=now
        )

        channel_id = self._resolve_channel()
        if channel_id is None:
            # Channel-unset → failed_retryable so the queue keeps
            # the row visible to the supervisor. The result.error
            # string is a constant so the diagnostic / live-regression
            # harness can match exact values.
            self._queue.transition(
                in_progress.job_id,
                JobState.FAILED_RETRYABLE,
                result={"error": SKIPPED_APPROVAL_CHANNEL_UNSET},
                clear_lease=True,
                now=now,
            )
            return ApprovalJobOutcome(
                job=in_progress,
                post_result=None,
                skipped_reason=SKIPPED_APPROVAL_CHANNEL_UNSET,
            )

        try:
            request = ApprovalRequest.from_payload(in_progress.payload or {})
            rendered = render_approval_request(request)
            post_result = await _maybe_await(
                self._post_fn(request, rendered)
            )
        except Exception as exc:  # noqa: BLE001 - error path
            self._queue.transition(
                in_progress.job_id,
                JobState.FAILED_RETRYABLE,
                result={"error": _short_error(exc)},
                clear_lease=True,
                now=now,
            )
            raise

        saved_result: dict[str, Any] = {"completed": True, "channel_id": channel_id}
        if isinstance(post_result, Mapping):
            for key in ("posted_message_id", "thread_id", "url"):
                value = post_result.get(key)
                if value is not None:
                    saved_result[key] = value

        saved = self._queue.transition(
            in_progress.job_id,
            JobState.SAVED,
            result=saved_result,
            clear_lease=True,
            now=now,
        )
        return ApprovalJobOutcome(job=saved, post_result=post_result)

    async def run_one(
        self,
        request: ApprovalRequest,
        *,
        priority: int = 0,
        max_attempts: int = 3,
        now: Optional[float] = None,
    ) -> ApprovalJobOutcome:
        """Producer + consumer single-shot helper used by the gateway.

        Steps:

          1. Idempotent ``enqueue`` keyed on ``(session, kind,
             source_message_id)``. Existing in-flight job →
             ``skipped_reason="duplicate_in_flight"``.
          2. ``pick`` to claim the lease.
          3. ``process_job`` posts the card (or marks
             ``failed_retryable`` for channel-unset / posting error).
        """

        if not request.session_id:
            raise ValueError("ApprovalRequest.session_id is required")
        if not request.approval_kind:
            raise ValueError("ApprovalRequest.approval_kind is required")

        job, created = self.enqueue(
            request,
            priority=priority,
            max_attempts=max_attempts,
            now=now,
        )
        if not created:
            return ApprovalJobOutcome(
                job=job,
                post_result=None,
                skipped_reason=SKIPPED_DUPLICATE,
            )

        picked = self._queue.pick(
            worker_id=self._worker_id,
            job_types=[JOB_TYPE_APPROVAL_POST],
            now=now,
        )
        if picked is None or picked.job_id != job.job_id:
            return ApprovalJobOutcome(
                job=picked or job,
                post_result=None,
                skipped_reason=SKIPPED_CLAIMED_BY_OTHER_WORKER,
            )

        return await self.process_job(picked, now=now)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_channel(self) -> Optional[int]:
        try:
            value = self._channel_resolver()
        except Exception:  # noqa: BLE001 - resolver bugs become channel-unset
            return None
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# Channel resolver helpers (exported so production can pass them in
# as the ``channel_resolver`` argument and tests can write a fixed
# closure with the same shape).
# ---------------------------------------------------------------------------


def env_approval_channel_resolver() -> Optional[int]:
    """Read ``DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID`` from env.

    Returns ``None`` when the var is unset or non-numeric. Production
    code wraps this with a name-fallback resolver that consults
    ``DISCORD_ENGINEERING_APPROVAL_CHANNEL_NAME`` against a live
    discord.Guild — that wrapper isn't here because it pulls in the
    discord client. The id-only path lives at the queue layer so
    tests don't need a Discord runtime.
    """

    raw = os.getenv("DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID")
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _maybe_await(value: Any) -> Any:
    """Await *value* if it's awaitable; otherwise return it as-is.

    Lets callers pass either a sync stub or a real Discord coroutine
    as ``post_fn`` without forking the worker code path. Mirrors the
    ``_maybe_await`` helper used by :func:`engineering_channel_router._run_research_loop_hook`.
    """

    if hasattr(value, "__await__"):
        return await value
    return value


def _short_error(exc: BaseException) -> str:
    """One-line error string used as the ``failed_retryable`` result.

    Same shape as :class:`ResearchWorker._short_error` and
    :class:`RoleTakeWorker._short_error` so the supervisor diagnostic
    can format any of the three with one helper.
    """

    msg = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    return f"{type(exc).__name__}: {msg}"[:500]


__all__ = (
    "APPROVAL_KIND_ACCESS_REQUEST",
    "APPROVAL_KIND_DECISION_REQUEST",
    "APPROVAL_KIND_ENGINEERING_WRITE",
    "APPROVAL_KIND_INFO_REQUEST",
    "APPROVAL_KIND_OBSIDIAN_WRITE",
    "APPROVAL_KIND_RESEARCH_PROMOTION",
    "APPROVAL_KIND_SECRET_REQUEST",
    "OPERATOR_ACTION_KINDS",
    "ApprovalChannelResolver",
    "ApprovalJobOutcome",
    "ApprovalPostFn",
    "ApprovalRequest",
    "ApprovalWorker",
    "JOB_TYPE_APPROVAL_POST",
    "SERVICE_ID_APPROVAL_WORKER",
    "SKIPPED_APPROVAL_CHANNEL_UNSET",
    "SKIPPED_CLAIMED_BY_OTHER_WORKER",
    "SKIPPED_DUPLICATE",
    "env_approval_channel_resolver",
    "render_approval_request",
)
