"""Approval reply routing — A-M5a-2.

Connects the empty link between :class:`ApprovalWorker` (M5a) and
:class:`ObsidianWriterWorker` (M5b). When a user replies in
``#승인-대기`` (or in the source thread) with "승인" / "이대로
진행" / "저장 승인", this module:

  1. Parses the reply intent (APPROVE / REJECT / HOLD / UNCLEAR).
  2. Resolves which ``approval_post`` job (already in ``saved`` —
     i.e. the card has been broadcast to the user) the reply
     belongs to.
  3. Converts an APPROVE'd request whose ``approval_kind`` is
     :data:`APPROVAL_KIND_OBSIDIAN_WRITE` into an
     :class:`ObsidianWriteRequest` carrying ``approval_id`` /
     ``approved_by`` / ``approved_at`` and enqueues it on the
     ``obsidian_write`` queue.
  4. On REJECT, stamps a "rejected" record onto session.extra so
     the supervisor diagnostic can show "사용자가 X 을 반려함".
  5. On HOLD / UNCLEAR, no-ops with a clear outcome — the gateway
     can render a "더 명확히 답해 주세요" reply elsewhere.

This module is **pure-Python**: no Discord client, no message
posting. The Discord-side wiring (M6.1b) calls
:func:`handle_approval_reply` from its on_message handler with the
parsed text + the user id + the source channel/thread/message ids.

The legacy in-channel ``is_obsidian_approval`` UX in
``agents/obsidian/approval.py`` is *not* replaced — it stays
authoritative for the existing pending-proposal flow. M5a-2 adds a
**queue-side** path so the gateway has both surfaces available
during the M6.1b transition; the older path remains intact and
green.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from .approval_worker import (
    APPROVAL_KIND_OBSIDIAN_WRITE,
    ApprovalRequest,
    JOB_TYPE_APPROVAL_POST,
)
from .obsidian_writer_worker import (
    JOB_TYPE_OBSIDIAN_WRITE,
    NOTE_KIND_KNOWLEDGE,
    ObsidianWriteRequest,
    ObsidianWriterWorker,
)
from .state_machine import JobState
from .store import Job, JobQueue


# ---------------------------------------------------------------------------
# Intent parsing
# ---------------------------------------------------------------------------


class ApprovalIntent(str, Enum):
    """What the user's reply means.

    String-valued so a caller can serialise the value into the
    audit log without an extra mapping. ``UNCLEAR`` covers both
    "no recognisable intent" (typo / unrelated chatter) and
    "intent recognised but ambiguous" (e.g. "음 일단 봐줘").
    """

    APPROVE = "approve"
    REJECT = "reject"
    HOLD = "hold"
    UNCLEAR = "unclear"


# Phrases that explicitly approve — full-text match (after
# whitespace collapse + lowercase). Mirrors the load-bearing entries
# from :data:`agents.obsidian.approval._APPROVAL_PHRASES` so dev who
# uses either path sees consistent vocabulary.
_APPROVE_PHRASES: frozenset[str] = frozenset(
    {
        "승인",
        "이대로 진행",
        "이대로진행",
        "이대로 저장",
        "이대로저장",
        "저장 승인",
        "저장승인",
        "obsidian 저장 승인",
        "옵시디언 저장 승인",
        "vault 저장 승인",
        "approve",
        "approved",
        "ok",
        "okay",
        "go",
        "진행",
        "save approved",
        "approve save",
    }
)


_REJECT_PHRASES: frozenset[str] = frozenset(
    {
        "반려",
        "거절",
        "거부",
        "저장 반려",
        "저장하지 마",
        "저장하지마",
        "저장 안 해도 돼",
        "저장 안할게",
        "reject",
        "rejected",
        "no go",
        "do not save",
        "don't save",
    }
)


_HOLD_PHRASES: frozenset[str] = frozenset(
    {
        "보류",
        "잠깐 보류",
        "잠시 보류",
        "일단 보류",
        "wait",
        "hold",
        "hold off",
        "later",
    }
)


_NORMALIZE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Whitespace-collapse + lowercase for phrase matching."""

    return _NORMALIZE_RE.sub(" ", (text or "").lower()).strip()


def parse_approval_intent(text: str) -> ApprovalIntent:
    """Classify a reply into APPROVE / REJECT / HOLD / UNCLEAR.

    Reject / hold are checked **before** approve so a reply like
    "저장 승인 반려" (technically nonsensical, but possible from
    a user fixing a previous reply) doesn't mis-classify as APPROVE
    on the first hit.
    """

    norm = _normalize(text)
    if not norm:
        return ApprovalIntent.UNCLEAR
    if any(phrase in norm for phrase in _REJECT_PHRASES):
        return ApprovalIntent.REJECT
    if any(phrase in norm for phrase in _HOLD_PHRASES):
        return ApprovalIntent.HOLD
    if norm in _APPROVE_PHRASES:
        return ApprovalIntent.APPROVE
    if any(phrase in norm for phrase in _APPROVE_PHRASES):
        # Phrase-contained approve (e.g. "이대로 진행해 줘") — accept
        # only when the reply is short enough that the approve
        # phrase is the dominant content. Long replies that happen
        # to mention "ok" stay UNCLEAR.
        if len(norm) <= 40:
            return ApprovalIntent.APPROVE
    return ApprovalIntent.UNCLEAR


# ---------------------------------------------------------------------------
# Request resolver — find the pending approval the reply belongs to
# ---------------------------------------------------------------------------


# We look for SAVED rows because that's the state ApprovalWorker
# leaves a row in after the card has been broadcast. ASSIGNED /
# IN_PROGRESS would mean the post hasn't landed yet — replying
# "approve" to a card that doesn't exist in the channel yet would
# be a producer bug. We still surface those as a diagnostic via a
# separate helper if needed.
_REPLYABLE_STATES: tuple[JobState, ...] = (JobState.SAVED,)


def find_replyable_approval(
    *,
    queue: JobQueue,
    session_id: str,
    approval_kind: Optional[str] = None,
    source_message_id: Optional[int] = None,
    source_thread_id: Optional[int] = None,
    replied_message_id: Optional[int] = None,
) -> Optional[Job]:
    """Return the approval_post job a reply most likely refers to.

    Resolution priority (most specific first):

      1. ``replied_message_id`` match against the posted card's
         ``result.posted_message_id`` — strongest signal when the
         operator quoted the card directly (Discord ``message.reference
         .message_id``).
      2. ``source_message_id`` match — when the reply quotes the
         original card. (Discord doesn't always carry this; the
         resolver tolerates None.)
      3. ``source_thread_id`` match — reply in the same thread
         where the card was posted.
      4. Most recent SAVED ``approval_post`` for the session.

    The approval_kind filter keeps the resolver from returning a
    ``research_promotion`` row when the reply is meant for a
    pending ``obsidian_write`` card (a session can have several
    distinct kinds in flight).
    """

    if not session_id:
        return None
    candidates = [
        job
        for job in queue.list_for_session(
            session_id, states=_REPLYABLE_STATES
        )
        if job.job_type == JOB_TYPE_APPROVAL_POST
    ]
    if approval_kind is not None:
        candidates = [
            job
            for job in candidates
            if (job.payload or {}).get("approval_kind") == approval_kind
        ]
    if not candidates:
        return None

    # 0. P0-T live smoke fix — replied_message_id 가 posted_message_id 와
    # 같은 카드를 우선 반환. Discord 의 message.reference.message_id 가
    # 가장 안정적인 매칭 신호.
    if replied_message_id is not None:
        for job in candidates:
            result = getattr(job, "result", None) or {}
            if not isinstance(result, Mapping):
                continue
            posted = result.get("posted_message_id")
            if posted is not None and int(posted) == int(replied_message_id):
                return job

    # 1. exact source_message_id match — strongest signal.
    if source_message_id is not None:
        for job in candidates:
            existing = (job.payload or {}).get("source_message_id")
            if existing is not None and int(existing) == int(source_message_id):
                return job

    # 2. source_thread_id match.
    if source_thread_id is not None:
        thread_matches = [
            job
            for job in candidates
            if (job.payload or {}).get("source_thread_id") is not None
            and int((job.payload or {}).get("source_thread_id"))
            == int(source_thread_id)
        ]
        if thread_matches:
            return _most_recent(thread_matches)

    # 3. Most recent for the session — operator 가 #승인-대기 채널에
    # 그냥 "승인" 만 친 경우의 최후 fallback. approval_kind 필터링이
    # 같은 종류의 카드만 남기므로 안전.
    return _most_recent(candidates)


def _most_recent(jobs: Sequence[Job]) -> Job:
    return max(jobs, key=lambda j: j.created_at)


# P1-Q-2 — session-agnostic matcher.  옛 wiring 은 router 가 session_id 를
# 먼저 추정 (most-recent fallback) 한 뒤 그 세션 안에서만 카드를 찾았다.
# global 채널 (`#승인-대기`) 의 generic reply 가 무관한 세션 (txn-pending-1
# 같은 fixture) 로 잘못 라우팅되는 회귀의 직접 원인.  본 함수는 raw
# replied_message_id 만으로 모든 세션의 SAVED approval_post 를 scan 해서
# 정확한 카드를 찾는다.  매칭되면 caller 가 그 카드의 session_id 를 그대로
# 사용 — session-first 추정 자체가 필요 없어짐.


def find_approval_by_posted_message_id(
    *,
    queue: JobQueue,
    posted_message_id: int,
    approval_kind: Optional[str] = None,
) -> Optional[Job]:
    """Discord reply 의 ``message.reference.message_id`` 로 모든 세션을
    scan 해서 일치 카드 반환.  session_id 모를 때 안전.
    """

    if not posted_message_id:
        return None
    import json as _json
    import sqlite3 as _sqlite3
    from .state_machine import JobState as _JobState

    db_path = getattr(queue, "_db_path", None)
    if db_path is None:
        return None
    try:
        with _sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = _sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM job_queue
                WHERE job_type = ?
                  AND state = ?
                ORDER BY created_at DESC
                LIMIT 200
                """,
                (JOB_TYPE_APPROVAL_POST, _JobState.SAVED.value),
            ).fetchall()
    except Exception:  # noqa: BLE001 — never crash matcher on db blip
        return None

    target = int(posted_message_id)
    for row in rows or ():
        try:
            result_raw = row["result_json"] or "{}"
            result = _json.loads(result_raw)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(result, Mapping):
            continue
        posted = result.get("posted_message_id")
        if posted is None or int(posted) != target:
            continue
        # approval_kind 필터 — 카드 종류 일치까지 강제 (없으면 통과)
        if approval_kind is not None:
            try:
                payload = _json.loads(row["payload_json"] or "{}")
            except Exception:  # noqa: BLE001
                continue
            if str(payload.get("approval_kind") or "") != approval_kind:
                continue
        # 모든 사용자에게 한 줄 row → Job 변환
        from .store import _row_to_job  # type: ignore[attr-defined]

        return _row_to_job(row)
    return None


def find_open_approval_cards_by_kind(
    *,
    queue: JobQueue,
    approval_kind: str,
    limit: int = 100,
) -> Tuple[Job, ...]:
    """``approval_kind`` 의 SAVED 카드 전부 — ambiguity 감지용.

    router 가 replied_message_id 없이 generic reply 를 받았는데 같은 kind
    의 open 카드가 2 개 이상이면 ambiguity 응답.
    """

    import json as _json
    import sqlite3 as _sqlite3
    from .state_machine import JobState as _JobState

    db_path = getattr(queue, "_db_path", None)
    if db_path is None:
        return ()
    try:
        with _sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = _sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM job_queue
                WHERE job_type = ?
                  AND state = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (JOB_TYPE_APPROVAL_POST, _JobState.SAVED.value, int(limit)),
            ).fetchall()
    except Exception:  # noqa: BLE001
        return ()
    from .store import _row_to_job  # type: ignore[attr-defined]

    matches: list = []
    for row in rows or ():
        try:
            payload = _json.loads(row["payload_json"] or "{}")
        except Exception:  # noqa: BLE001
            continue
        if str(payload.get("approval_kind") or "") != approval_kind:
            continue
        matches.append(_row_to_job(row))
    return tuple(matches)


# ---------------------------------------------------------------------------
# Approval → ObsidianWriteRequest converter
# ---------------------------------------------------------------------------


def approval_to_obsidian_write_request(
    *,
    approval_request: ApprovalRequest,
    approval_id: str,
    approved_by: str,
    approved_at: Optional[str] = None,
    source_message_id: Optional[int] = None,
    note_kind: Optional[str] = None,
) -> ObsidianWriteRequest:
    """Convert an ``approval_post`` request into an
    :class:`ObsidianWriteRequest`.

    Raises :class:`ValueError` when ``approval_request.approval_kind``
    is not :data:`APPROVAL_KIND_OBSIDIAN_WRITE` — the converter
    refuses to silently broaden into "any approval triggers a write".

    ``note_kind`` defaults to ``knowledge`` (final, irreversible save)
    because that's the canonical Obsidian approval card today; M6.2
    can pass an explicit override when other kinds get cards too.
    """

    if approval_request.approval_kind != APPROVAL_KIND_OBSIDIAN_WRITE:
        raise ValueError(
            "approval_to_obsidian_write_request requires "
            f"approval_kind={APPROVAL_KIND_OBSIDIAN_WRITE!r}, got "
            f"{approval_request.approval_kind!r}"
        )

    resolved_at = (approved_at or "").strip() or _utc_now_iso()
    extra = dict(approval_request.extra or {})

    # A-M7.6 — preserve forum-handoff hydration payload.
    # The producer stamped these fields on ApprovalRequest.extra so
    # the writer can compose a hydrated knowledge note instead of
    # an empty stub.
    metadata: dict[str, Any] = {
        "decision_id": extra.get("decision_id"),
        "policy_level": extra.get("policy_level"),
        "approval_kind": approval_request.approval_kind,
        "approval_job_id": approval_id,
    }
    # M10b — every hydration field that survived the approval card
    # round-trip must land on ObsidianWriteRequest.metadata so the
    # renderer can compose a hydrated knowledge note. Order is
    # alphabetical-ish for grep stability, not load-order sensitive.
    for key in (
        "topic_key",
        "canonical_title",
        "source_thread_url",
        "source_thread_title",
        "thread_snapshot",
        "extracted_links",
        "selected_roles",
        "research_pack_title",
        "ledger_revision",
        "origin",
        "requested_by",
        "requested_at",
    ):
        if key in extra and extra[key] is not None:
            metadata[key] = extra[key]

    source_thread_url = None
    raw_url = extra.get("source_thread_url")
    if isinstance(raw_url, str) and raw_url.strip():
        source_thread_url = raw_url.strip()

    # Prefer the canonical (normalised) title over the raw approval
    # request title — the producer might have stored a longer title
    # before the ledger normalised it. Fall back to the request
    # title for older sessions.
    canonical = extra.get("canonical_title")
    if isinstance(canonical, str) and canonical.strip():
        chosen_title = canonical.strip()
    else:
        chosen_title = approval_request.title

    return ObsidianWriteRequest(
        session_id=approval_request.session_id,
        note_kind=note_kind or NOTE_KIND_KNOWLEDGE,
        title=chosen_title,
        source_thread_id=approval_request.source_thread_id,
        source_thread_url=source_thread_url,
        approval_id=approval_id,
        approved_by=approved_by,
        approved_at=resolved_at,
        project=None,
        layout=None,
        vault_path=None,
        overwrite=False,
        dry_run=False,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Outcome model + the main router
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApprovalReplyOutcome:
    """What the router decided.

    ``intent`` is the parsed reply classification.
    ``approval_job_id`` is the ``approval_post`` row the reply was
    matched against (None when no candidate was found).
    ``write_job_id`` is the ``obsidian_write`` row that was
    enqueued (None for non-APPROVE intents or for replies that
    matched a non-Obsidian approval).
    ``skipped_reason`` distinguishes friendly no-ops (HOLD /
    UNCLEAR / no candidate / duplicate) from genuine errors.
    ``rejection_recorded`` is True after a REJECT reply has been
    stamped onto session.extra.
    """

    intent: ApprovalIntent
    approval_job_id: Optional[str] = None
    write_job_id: Optional[str] = None
    skipped_reason: Optional[str] = None
    rejection_recorded: bool = False
    audit: Mapping[str, Any] = field(default_factory=dict)


def handle_approval_reply(
    *,
    queue: JobQueue,
    obsidian_worker: ObsidianWriterWorker,
    text: str,
    session_id: str,
    approved_by: str,
    source_message_id: Optional[int] = None,
    source_thread_id: Optional[int] = None,
    approval_kind: Optional[str] = APPROVAL_KIND_OBSIDIAN_WRITE,
    approved_at: Optional[str] = None,
    persist_rejection_fn: Optional[
        "PersistRejectionFn"
    ] = None,
    now: Optional[float] = None,
) -> ApprovalReplyOutcome:
    """Route a user's reply to its approval consequence.

    Pure function: no Discord posting. The gateway / member-bot
    on_message handler calls this after it has identified the
    reply belongs to an approval channel / thread, and the helper
    decides whether to enqueue an Obsidian write, record a
    rejection, or do nothing.
    """

    intent = parse_approval_intent(text)

    if intent in (ApprovalIntent.HOLD, ApprovalIntent.UNCLEAR):
        return ApprovalReplyOutcome(
            intent=intent,
            skipped_reason="intent_not_actionable",
        )

    approval_job = find_replyable_approval(
        queue=queue,
        session_id=session_id,
        approval_kind=approval_kind,
        source_message_id=source_message_id,
        source_thread_id=source_thread_id,
    )
    if approval_job is None:
        return ApprovalReplyOutcome(
            intent=intent,
            skipped_reason="no_matching_approval",
        )

    if intent == ApprovalIntent.REJECT:
        persist_fn = persist_rejection_fn or _default_persist_rejection
        try:
            persist_fn(
                queue=queue,
                approval_job=approval_job,
                rejected_by=approved_by,
                rejected_at=approved_at or _utc_now_iso(),
                source_message_id=source_message_id,
                reason=text,
            )
            recorded = True
        except Exception:  # noqa: BLE001 - audit is best-effort
            recorded = False
        return ApprovalReplyOutcome(
            intent=intent,
            approval_job_id=approval_job.job_id,
            rejection_recorded=recorded,
            audit={
                "rejected_by": approved_by,
                "rejected_at": approved_at or _utc_now_iso(),
                "source_message_id": source_message_id,
                "decision_id": (approval_job.payload or {})
                .get("extra", {})
                .get("decision_id"),
            },
        )

    # APPROVE branch — convert + enqueue.
    request = ApprovalRequest.from_payload(approval_job.payload or {})
    if request.approval_kind != APPROVAL_KIND_OBSIDIAN_WRITE:
        # Non-Obsidian approvals (e.g. research_promotion) are
        # accepted but currently have no follow-on worker. Surface
        # the matched job + skipped_reason so the operator side can
        # decide what to do.
        return ApprovalReplyOutcome(
            intent=intent,
            approval_job_id=approval_job.job_id,
            skipped_reason="approval_kind_not_handled",
        )

    write_request = approval_to_obsidian_write_request(
        approval_request=request,
        approval_id=approval_job.job_id,
        approved_by=approved_by,
        approved_at=approved_at,
        source_message_id=source_message_id,
    )
    write_job, created = obsidian_worker.enqueue(
        write_request, now=now
    )
    return ApprovalReplyOutcome(
        intent=intent,
        approval_job_id=approval_job.job_id,
        write_job_id=write_job.job_id,
        skipped_reason=None if created else "duplicate_obsidian_write",
        audit={
            "approved_by": approved_by,
            "approved_at": write_request.approved_at,
            "source_message_id": source_message_id,
            "decision_id": (request.extra or {}).get("decision_id"),
        },
    )


# ---------------------------------------------------------------------------
# Rejection persistence — stamps session.extra so the supervisor
# diagnostic can see "X 카드는 사용자가 반려" without re-reading the
# queue.
# ---------------------------------------------------------------------------


from typing import Callable

PersistRejectionFn = Callable[..., None]


def _default_persist_rejection(
    *,
    queue: JobQueue,
    approval_job: Job,
    rejected_by: str,
    rejected_at: str,
    source_message_id: Optional[int],
    reason: str,
) -> None:
    """Best-effort: stash rejection on session.extra and stamp the
    approval_post row's ``result`` so the audit trail is visible
    from both sides (queue row + session view).
    """

    try:
        from ..workflow_state import (
            load_session as _load,
            update_session as _update,
        )
        from dataclasses import replace as _replace
    except Exception:  # noqa: BLE001 - partial install
        return

    payload = approval_job.payload or {}
    record = {
        "approval_job_id": approval_job.job_id,
        "approval_kind": payload.get("approval_kind"),
        "rejected_by": rejected_by,
        "rejected_at": rejected_at,
        "source_message_id": source_message_id,
        "reason": (reason or "")[:500],
    }

    try:
        session = _load(approval_job.session_id)
    except Exception:  # noqa: BLE001
        session = None
    if session is not None:
        extra = dict(getattr(session, "extra", None) or {})
        bucket = list(extra.get("approval_rejections") or [])
        bucket.append(record)
        # Keep history-light: cap at 64 entries so a busy session
        # doesn't bloat the cache row.
        if len(bucket) > 64:
            bucket = bucket[-64:]
        extra["approval_rejections"] = bucket
        try:
            updated = _replace(session, extra=extra)
            _update(updated, now=datetime.now(tz=timezone.utc))
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


__all__ = (
    "ApprovalIntent",
    "ApprovalReplyOutcome",
    "approval_to_obsidian_write_request",
    "find_approval_by_posted_message_id",
    "find_open_approval_cards_by_kind",
    "find_replyable_approval",
    "handle_approval_reply",
    "parse_approval_intent",
)
