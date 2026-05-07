"""obsidian_write job worker — A-M5b wiring.

Migrates the in-process Obsidian write path onto the queue. Today
the gateway / approval handler calls ``write_note`` directly inside
its handler; M5b makes the same call go through a queued
``obsidian_write`` row so:

  * The supervisor sees vault writes as audit-grade events.
  * Duplicate "save the same research → vault" requests dedup at
    the queue layer instead of relying on filename suffixing.
  * A standalone ``eng-obsidian-writer`` process (M6) consumes the
    same rows the gateway-side helper does today, with no producer
    rewire.
  * The hard "approval required for final knowledge / overwrite"
    rule lives in one place — the worker — instead of being checked
    by every caller.

Scope this commit lands:

  * :data:`JOB_TYPE_OBSIDIAN_WRITE` + :class:`ObsidianWriteRequest`
  * :class:`ObsidianWriterWorker` with idempotent enqueue +
    process_job + run_one + heartbeat.
  * Approval guard: ``note_kind=="knowledge"`` or ``overwrite=True``
    requires ``approval_id`` + ``approved_by`` + ``approved_at``;
    missing info → ``failed_retryable`` with a constant error.
  * ``session.extra['obsidian_writes'][<note_kind>]`` stash so the
    status diagnostic / Phase 5 surface can describe what landed
    where without re-reading the vault.

What it does **not** land (deferred):

  * Default render dispatcher for every ``note_kind`` — only a
    minimal ``research`` / ``decision`` default. Producers for
    ``meeting`` / ``knowledge`` / ``work-report`` pass their own
    ``render_fn`` until M5b-2 wires every kind.
  * Routing connection from M5a's ``ApprovalWorker`` (an "승인" reply
    in ``#승인-대기`` enqueueing the write). That's M5a-2.
  * The ``yule run-service eng-obsidian-writer`` long-running
    consumer loop. That's M6.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
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


JOB_TYPE_OBSIDIAN_WRITE: str = "obsidian_write"
SERVICE_ID_OBSIDIAN_WRITER: str = "eng-obsidian-writer"


# Note kinds — map 1:1 to the export folder routing in
# :mod:`agents.obsidian.export`. ``knowledge`` is the load-bearing
# "long-term decision record" kind that requires explicit approval.
NOTE_KIND_RESEARCH: str = "research"
NOTE_KIND_DECISION: str = "decision"
NOTE_KIND_MEETING: str = "meeting"
NOTE_KIND_KNOWLEDGE: str = "knowledge"
NOTE_KIND_WORK_REPORT: str = "work-report"


# Skipped reasons surfaced via :class:`ObsidianWriteJobOutcome`.
SKIPPED_DUPLICATE: str = "duplicate_in_flight"
SKIPPED_CLAIMED_BY_OTHER_WORKER: str = "claimed_by_other_worker"
SKIPPED_APPROVAL_REQUIRED: str = "approval_information_missing"
SKIPPED_VAULT_UNAVAILABLE: str = "vault_root_unavailable"


_ACTIVE_STATES: Tuple[JobState, ...] = (
    JobState.QUEUED,
    JobState.ASSIGNED,
    JobState.IN_PROGRESS,
    JobState.WAITING_FOR_ROLE,
    JobState.RESEARCHING,
    JobState.PENDING_APPROVAL,
    JobState.READY_FOR_OBSIDIAN,
)


# Note kinds that require explicit human approval. ``knowledge`` is
# the canonical "long-term decision record" — losing one to a stale
# overwrite is the regression M5b's guard exists to prevent.
_APPROVAL_REQUIRED_KINDS: frozenset[str] = frozenset(
    {NOTE_KIND_KNOWLEDGE}
)


@dataclass(frozen=True)
class ObsidianWriteRequest:
    """Strongly-typed payload for an ``obsidian_write`` job.

    The worker treats ``approval_id`` / ``approved_by`` /
    ``approved_at`` as opaque strings — it does **not** validate
    that the approval is real. The producer (M5a-2 routing) is
    responsible for only ever populating those fields after a
    legitimate ``ApprovalWorker`` outcome. The worker just refuses
    to run when they're missing on a kind/overwrite combination
    that requires them.
    """

    session_id: str
    note_kind: str
    title: str
    source_thread_id: Optional[int] = None
    source_thread_url: Optional[str] = None
    approval_id: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    project: Optional[str] = None
    layout: Optional[str] = None
    vault_path: Optional[str] = None
    overwrite: bool = False
    dry_run: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ObsidianWriteRequest":
        return cls(
            session_id=str(payload.get("session_id") or ""),
            note_kind=str(payload.get("note_kind") or ""),
            title=str(payload.get("title") or ""),
            source_thread_id=_coerce_int(payload.get("source_thread_id")),
            source_thread_url=_optional_str(payload.get("source_thread_url")),
            approval_id=_optional_str(payload.get("approval_id")),
            approved_by=_optional_str(payload.get("approved_by")),
            approved_at=_optional_str(payload.get("approved_at")),
            project=_optional_str(payload.get("project")),
            layout=_optional_str(payload.get("layout")),
            vault_path=_optional_str(payload.get("vault_path")),
            overwrite=bool(payload.get("overwrite", False)),
            dry_run=bool(payload.get("dry_run", False)),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "session_id": self.session_id,
            "note_kind": self.note_kind,
            "title": self.title,
            "source_thread_id": self.source_thread_id,
            "source_thread_url": self.source_thread_url,
            "approval_id": self.approval_id,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "project": self.project,
            "layout": self.layout,
            "vault_path": self.vault_path,
            "overwrite": self.overwrite,
            "dry_run": self.dry_run,
            "metadata": dict(self.metadata),
        }

    def has_full_approval(self) -> bool:
        """True iff every approval field is populated.

        This is the **shape** check, not a real authorisation check —
        the producer is responsible for only ever filling these
        fields after :class:`ApprovalWorker` confirms a human approval.
        """

        return bool(
            (self.approval_id or "").strip()
            and (self.approved_by or "").strip()
            and (self.approved_at or "").strip()
        )

    def requires_approval(self) -> bool:
        """True when this write needs explicit human approval.

        Kicks for ``note_kind == "knowledge"`` (long-term knowledge
        record) and for any ``overwrite=True`` (replacing an
        existing note is irreversible from the audit standpoint).
        """

        return self.note_kind in _APPROVAL_REQUIRED_KINDS or self.overwrite


# Render / write / vault-resolver injection seams. Production wires
# these to ``agents.obsidian.export`` + ``agents.obsidian.writer``;
# tests pass closures.

#: Build the rendered note for a write request. Returns whatever the
#: bound write_fn knows how to consume — typically an ``ObsidianNote``
#: from ``agents.obsidian.export`` but also tolerated as a tuple of
#: ``(target_path, content)`` for tests that don't need the full
#: export chain.
RenderNoteFn = Callable[
    [ObsidianWriteRequest], Union[Any, Awaitable[Any]]
]

#: Persist a rendered note onto the vault. Defaults to
#: ``agents.obsidian.writer.write_note``. Result is whatever the
#: writer returns — captured into the queue's ``result_json`` for
#: the supervisor diagnostic.
WriteNoteFn = Callable[
    [Any, Path, "ObsidianWriteRequest"],
    Union[Any, Awaitable[Any]],
]

#: Resolve the vault root for a given request. Defaults to
#: ``agents.obsidian.writer.resolve_vault_root`` consulting
#: ``OBSIDIAN_VAULT_PATH`` (or the request's explicit override).
VaultRootResolver = Callable[
    [ObsidianWriteRequest], Optional[Union[Path, str]]
]


@dataclass(frozen=True)
class ObsidianWriteJobOutcome:
    job: Optional[Job]
    write_result: Optional[Any] = None
    skipped_reason: Optional[str] = None


class ObsidianWriterWorker:
    """Idempotent worker for ``obsidian_write`` jobs."""

    def __init__(
        self,
        *,
        queue: JobQueue,
        render_fn: RenderNoteFn,
        write_fn: WriteNoteFn,
        vault_root_resolver: VaultRootResolver,
        heartbeats: Optional[HeartbeatStore] = None,
        worker_id: Optional[str] = None,
    ) -> None:
        self._queue = queue
        self._render_fn = render_fn
        self._write_fn = write_fn
        self._vault_root_resolver = vault_root_resolver
        self._heartbeats = heartbeats
        self._worker_id = (
            worker_id or f"{SERVICE_ID_OBSIDIAN_WRITER}:{os.getpid()}"
        )

    # ------------------------------------------------------------------
    # Producer side
    # ------------------------------------------------------------------

    def find_active(
        self,
        *,
        session_id: str,
        note_kind: str,
        source_thread_id: Optional[int],
        title: str,
    ) -> Optional[Job]:
        """Return any non-terminal ``obsidian_write`` job for the
        ``(session_id, note_kind, source_thread_id?, title)`` triple.

        ``source_thread_id`` participates only when populated — the
        same kind+title for the same session is a duplicate even
        without a thread id (CLI sync runs lack thread context but
        still need dedup).
        """

        if not session_id or not note_kind:
            return None
        for job in self._queue.list_for_session(
            session_id, states=_ACTIVE_STATES
        ):
            if job.job_type != JOB_TYPE_OBSIDIAN_WRITE:
                continue
            payload = job.payload or {}
            if str(payload.get("note_kind") or "") != note_kind:
                continue
            if str(payload.get("title") or "") != title:
                continue
            existing_thread = _coerce_int(payload.get("source_thread_id"))
            if (
                source_thread_id is not None
                and existing_thread is not None
                and existing_thread != source_thread_id
            ):
                continue
            return job
        return None

    def enqueue(
        self,
        request: ObsidianWriteRequest,
        *,
        priority: int = 0,
        max_attempts: int = 3,
        now: Optional[float] = None,
    ) -> Tuple[Job, bool]:
        existing = self.find_active(
            session_id=request.session_id,
            note_kind=request.note_kind,
            source_thread_id=request.source_thread_id,
            title=request.title,
        )
        if existing is not None:
            return existing, False
        job = self._queue.enqueue(
            session_id=request.session_id,
            job_type=JOB_TYPE_OBSIDIAN_WRITE,
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
    ) -> ObsidianWriteJobOutcome:
        if self._heartbeats is not None:
            try:
                self._heartbeats.record(
                    SERVICE_ID_OBSIDIAN_WRITER,
                    pid=os.getpid(),
                    metadata={"job_id": job.job_id},
                    now=now,
                )
            except Exception:  # noqa: BLE001 - heartbeat is observability only
                pass

        in_progress = self._queue.transition(
            job.job_id, JobState.IN_PROGRESS, now=now
        )
        request = ObsidianWriteRequest.from_payload(in_progress.payload or {})

        # --- approval guard --------------------------------------------------
        if request.requires_approval() and not request.has_full_approval():
            self._queue.transition(
                in_progress.job_id,
                JobState.FAILED_RETRYABLE,
                result={"error": SKIPPED_APPROVAL_REQUIRED},
                clear_lease=True,
                now=now,
            )
            return ObsidianWriteJobOutcome(
                job=in_progress,
                write_result=None,
                skipped_reason=SKIPPED_APPROVAL_REQUIRED,
            )

        # --- vault root ------------------------------------------------------
        try:
            vault_root_raw = self._vault_root_resolver(request)
        except Exception as exc:  # noqa: BLE001 - resolver bug == vault unavailable
            self._queue.transition(
                in_progress.job_id,
                JobState.FAILED_RETRYABLE,
                result={
                    "error": SKIPPED_VAULT_UNAVAILABLE,
                    "detail": _short_error(exc),
                },
                clear_lease=True,
                now=now,
            )
            return ObsidianWriteJobOutcome(
                job=in_progress,
                write_result=None,
                skipped_reason=SKIPPED_VAULT_UNAVAILABLE,
            )

        if vault_root_raw is None:
            self._queue.transition(
                in_progress.job_id,
                JobState.FAILED_RETRYABLE,
                result={"error": SKIPPED_VAULT_UNAVAILABLE},
                clear_lease=True,
                now=now,
            )
            return ObsidianWriteJobOutcome(
                job=in_progress,
                write_result=None,
                skipped_reason=SKIPPED_VAULT_UNAVAILABLE,
            )

        vault_root = Path(vault_root_raw)

        # --- render + write --------------------------------------------------
        try:
            note = await _maybe_await(self._render_fn(request))
            write_result = await _maybe_await(
                self._write_fn(note, vault_root, request)
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

        # --- session.extra stash --------------------------------------------
        # Best-effort: the queue row is already SAVED below regardless.
        # Persist a small JSON-friendly summary so the status
        # diagnostic surface (Phase 5) can describe what landed without
        # re-reading the vault.
        self._stash_write_result_on_session(
            request=request,
            write_result=write_result,
            vault_root=vault_root,
            now=now,
        )

        result_summary = self._summarize_write_result(
            request=request,
            write_result=write_result,
            vault_root=vault_root,
        )
        saved = self._queue.transition(
            in_progress.job_id,
            JobState.SAVED,
            result=result_summary,
            clear_lease=True,
            now=now,
        )
        return ObsidianWriteJobOutcome(
            job=saved, write_result=write_result
        )

    async def run_one(
        self,
        request: ObsidianWriteRequest,
        *,
        priority: int = 0,
        max_attempts: int = 3,
        now: Optional[float] = None,
    ) -> ObsidianWriteJobOutcome:
        if not request.session_id:
            raise ValueError("ObsidianWriteRequest.session_id is required")
        if not request.note_kind:
            raise ValueError("ObsidianWriteRequest.note_kind is required")
        if not request.title:
            raise ValueError("ObsidianWriteRequest.title is required")

        job, created = self.enqueue(
            request,
            priority=priority,
            max_attempts=max_attempts,
            now=now,
        )
        if not created:
            return ObsidianWriteJobOutcome(
                job=job,
                write_result=None,
                skipped_reason=SKIPPED_DUPLICATE,
            )

        picked = self._queue.pick(
            worker_id=self._worker_id,
            job_types=[JOB_TYPE_OBSIDIAN_WRITE],
            now=now,
        )
        if picked is None or picked.job_id != job.job_id:
            return ObsidianWriteJobOutcome(
                job=picked or job,
                write_result=None,
                skipped_reason=SKIPPED_CLAIMED_BY_OTHER_WORKER,
            )

        return await self.process_job(picked, now=now)

    # ------------------------------------------------------------------
    # session.extra stash
    # ------------------------------------------------------------------

    def _stash_write_result_on_session(
        self,
        *,
        request: ObsidianWriteRequest,
        write_result: Any,
        vault_root: Path,
        now: Optional[float],
    ) -> None:
        """Persist a small JSON-friendly write summary onto
        ``session.extra['obsidian_writes'][<note_kind>]``.

        Mirrors the Phase 4 ``role_research_results`` pattern —
        latest-wins per kind so the status diagnostic stays compact.
        Best-effort: any failure is swallowed so observability never
        blocks the queue from transitioning to SAVED.
        """

        if not request.session_id:
            return
        try:
            from ..workflow_state import (
                load_session as _load,
                update_session as _update,
            )
            from dataclasses import replace as _replace
        except Exception:  # noqa: BLE001 - partial install fallback
            return
        try:
            session = _load(request.session_id)
        except Exception:  # noqa: BLE001
            return
        if session is None:
            return

        summary = self._summarize_write_result(
            request=request,
            write_result=write_result,
            vault_root=vault_root,
        )
        summary["recorded_at"] = (
            datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
            if now is not None
            else datetime.now(tz=timezone.utc).isoformat()
        )

        extra = dict(getattr(session, "extra", None) or {})
        bucket = dict(extra.get("obsidian_writes") or {})
        bucket[request.note_kind] = summary
        extra["obsidian_writes"] = bucket
        try:
            updated = _replace(session, extra=extra)
        except TypeError:
            try:
                live = getattr(session, "extra", None)
                if isinstance(live, dict):
                    live["obsidian_writes"] = bucket
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            # update_session requires the ``now`` kwarg — without it
            # the call raises TypeError and the stash is silently lost.
            _update(updated, now=datetime.now(tz=timezone.utc))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _summarize_write_result(
        self,
        *,
        request: ObsidianWriteRequest,
        write_result: Any,
        vault_root: Path,
    ) -> dict[str, Any]:
        target_path = getattr(write_result, "target_path", None)
        written = bool(getattr(write_result, "written", False))
        dry_run = bool(getattr(write_result, "dry_run", request.dry_run))
        suffix_applied = bool(
            getattr(write_result, "suffix_applied", False)
        )
        return {
            "completed": True,
            "note_kind": request.note_kind,
            "title": request.title,
            "vault_root": str(vault_root),
            "target_path": str(target_path) if target_path else None,
            "written": written,
            "dry_run": dry_run,
            "overwrite": request.overwrite,
            "suffix_applied": suffix_applied,
            "approval_id": request.approval_id,
            "approved_by": request.approved_by,
        }


# ---------------------------------------------------------------------------
# Default render dispatcher (research / decision only — others must
# inject their own render_fn at this stage). Producers for
# ``meeting`` / ``knowledge`` / ``work-report`` either pass a custom
# render_fn or wait for M5b-2 to wire those kinds.
# ---------------------------------------------------------------------------


def default_render_fn(request: ObsidianWriteRequest) -> Any:
    """Best-effort default render for ``research`` / ``decision``.

    Reuses :func:`agents.obsidian.export.render_research_note` —
    same path the legacy CLI sync drives. Raises for unsupported
    kinds so a producer that forgets to inject ``render_fn`` for
    e.g. ``knowledge`` fails loudly instead of silently writing the
    wrong content.
    """

    if request.note_kind not in (NOTE_KIND_RESEARCH, NOTE_KIND_DECISION):
        raise ObsidianRenderError(
            f"default_render_fn does not support note_kind={request.note_kind!r}; "
            "pass a custom render_fn"
        )
    # Lazy import to keep this module light when only the queue
    # primitives are needed (the obsidian export chain pulls a fair
    # amount of code in).
    from ..obsidian.export import (
        recommend_path,
        render_research_note,
    )
    from ..research.pack import pack_from_dict
    from ..workflow_state import load_session

    session = load_session(request.session_id)
    if session is None:
        raise ObsidianRenderError(
            f"session {request.session_id!r} not found; default render needs it"
        )
    raw_pack = (session.extra or {}).get("research_pack")
    if not isinstance(raw_pack, Mapping) or not raw_pack:
        raise ObsidianRenderError(
            "default render needs session.extra['research_pack']"
        )
    pack = pack_from_dict(dict(raw_pack))
    return render_research_note(
        pack=pack,
        session=session,
        kind=request.note_kind,
        project_override=request.project,
        layout_override=request.layout,
    )


def default_write_fn(
    note: Any,
    vault_root: Path,
    request: ObsidianWriteRequest,
) -> Any:
    """Default writer that delegates to :func:`agents.obsidian.writer.write_note`.

    The actual collision policy / suffix handling lives in
    ``write_note`` — we only forward ``overwrite`` / ``dry_run``
    from the request.
    """

    from ..obsidian.writer import write_note

    return write_note(
        note,
        vault_root,
        overwrite=request.overwrite,
        dry_run=request.dry_run,
    )


def default_vault_root_resolver(
    request: ObsidianWriteRequest,
) -> Optional[Path]:
    """Default resolver — request override > ``OBSIDIAN_VAULT_PATH``.

    Returns ``None`` (which the worker treats as
    :data:`SKIPPED_VAULT_UNAVAILABLE`) when the env / override is
    unusable, instead of raising — that keeps "vault not configured"
    out of the runner exception path.
    """

    from ..obsidian.writer import resolve_vault_root, ObsidianWriteError

    try:
        return resolve_vault_root(override=request.vault_path)
    except ObsidianWriteError:
        return None


class ObsidianRenderError(RuntimeError):
    """Raised by :func:`default_render_fn` when it can't build a note."""


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _short_error(exc: BaseException) -> str:
    msg = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    return f"{type(exc).__name__}: {msg}"[:500]


__all__ = (
    "JOB_TYPE_OBSIDIAN_WRITE",
    "NOTE_KIND_DECISION",
    "NOTE_KIND_KNOWLEDGE",
    "NOTE_KIND_MEETING",
    "NOTE_KIND_RESEARCH",
    "NOTE_KIND_WORK_REPORT",
    "ObsidianRenderError",
    "ObsidianWriteJobOutcome",
    "ObsidianWriteRequest",
    "ObsidianWriterWorker",
    "RenderNoteFn",
    "SERVICE_ID_OBSIDIAN_WRITER",
    "SKIPPED_APPROVAL_REQUIRED",
    "SKIPPED_CLAIMED_BY_OTHER_WORKER",
    "SKIPPED_DUPLICATE",
    "SKIPPED_VAULT_UNAVAILABLE",
    "VaultRootResolver",
    "WriteNoteFn",
    "default_render_fn",
    "default_vault_root_resolver",
    "default_write_fn",
)
