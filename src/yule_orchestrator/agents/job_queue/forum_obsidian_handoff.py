"""Operations-research forum → ApprovalRequest producer — A-M7.5.

The user complaint that closes A-M7 family: a message in
``#운영-리서치`` saying "Obsidian 에 정리하고 싶어" should produce a
``#승인-대기`` card, not silently no-op. The approval worker / reply
router / Obsidian writer have been live since A-M5/M6, but the
producer that turns a forum-thread save request into an
:class:`ApprovalRequest` was never wired.

This module is the missing producer. It is **pure-Python** — no
Discord client, no SQLite. The Discord-side adapter
(``discord/bot.py`` ``on_message``) calls
:func:`route_forum_obsidian_save_request` after a regular forum
message is observed, with all dependencies injected so the producer
stays unit-testable.

Lifecycle (matching the user spec's flow):

  1. User posts in a research forum thread: "Obsidian 에 정리하고 싶어".
  2. The phrase detector :func:`agents.obsidian.approval.is_obsidian_save_request`
     fires.
  3. We resolve the *session_id* by walking recent open sessions and
     matching ``session.extra['research_forum_thread_id']`` against
     the message's channel id.
  4. We build an :class:`ApprovalRequest` (kind = OBSIDIAN_WRITE) with
     the thread's id / title / source-message id stamped in
     ``extra``.
  5. We call :class:`ApprovalWorker.run_one` so the queue posts the
     card to ``#승인-대기`` and the row lands SAVED. Idempotency is
     handled by ``ApprovalWorker.find_active`` — same
     ``(session_id, kind, source_message_id)`` triple won't enqueue
     a second card.
  6. The Discord-side caller renders a friendly thread reply with
     "approval card 게시 완료" or "이미 같은 요청이 진행 중" based on
     the outcome.

What this module deliberately does NOT do:

  * Write to Obsidian directly. Vault writes only happen after the
    user approves in ``#승인-대기`` — that path is owned by M5a-2's
    :func:`agents.job_queue.approval_reply.handle_approval_reply`.
  * Touch ``session.extra``. The approval row carries everything
    the writer needs; mutating session state would duplicate state
    across two surfaces.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    Any,
    Awaitable,
    Callable,
    Iterable,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from .approval_worker import (
    APPROVAL_KIND_OBSIDIAN_WRITE,
    ApprovalRequest,
    ApprovalWorker,
)


logger = logging.getLogger(__name__)


# Skipped reasons surfaced via :class:`ForumObsidianHandoffOutcome` so
# the Discord adapter / status surface can match exact strings.
SKIPPED_NOT_SAVE_REQUEST: str = "not_save_request"
SKIPPED_NO_SESSION_FOR_THREAD: str = "no_session_for_thread"
SKIPPED_DUPLICATE_APPROVAL: str = "duplicate_approval_in_flight"
SKIPPED_APPROVAL_WORKER_RAISED: str = "approval_worker_raised"
SKIPPED_APPROVAL_CHANNEL_UNSET: str = "approval_channel_unset"
# A-M7.6: topic-key dedup outcomes — distinct from message-id dedup
# so the operator sees a different message when the same topic /
# thread already has a card or saved note.
SKIPPED_TOPIC_PENDING_APPROVAL: str = "topic_pending_approval"
SKIPPED_TOPIC_ALREADY_SAVED: str = "topic_already_saved"


# Friendly reply templates for the Discord-side adapter. Operator
# can read them from the test snapshot — they are the entire
# user-facing vocabulary the producer adds.
RESPONSE_APPROVAL_QUEUED: str = (
    "📨 Obsidian 저장 요청을 받았어요. `#승인-대기` 채널에 승인 카드를 "
    "게시했어요 (job=`{approval_job_id}`)."
)
RESPONSE_APPROVAL_DUPLICATE: str = (
    "⏳ 이 thread 의 동일 저장 요청이 이미 `#승인-대기` 큐에 들어가 있어요."
)
RESPONSE_NO_SESSION_FOR_THREAD: str = (
    "❓ 이 thread 에 연결된 세션을 찾지 못했어요. thread 가 만들어진 직후가 "
    "아니라면 `/engineer_show` 로 세션 id 를 확인해 주세요."
)
RESPONSE_APPROVAL_CHANNEL_UNSET: str = (
    "⚠️ `#승인-대기` 채널이 환경설정에 없어요. 운영자가 "
    "`DISCORD_ENGINEERING_APPROVAL_CHANNEL_*` 를 설정한 뒤 다시 시도해 주세요."
)
RESPONSE_APPROVAL_FAILED: str = (
    "⚠️ 저장 요청은 인식했지만 승인 카드 게시에 실패했어요. "
    "잠시 후 다시 시도하거나 운영자에게 알려 주세요. "
    "운영자 진단 코드는 `journalctl` 에 남깁니다."
)
# A-M7.6 — topic-key dedup messages. Carry the existing approval
# job id / vault path so the operator can navigate without
# re-running ``/engineer_show``.
RESPONSE_TOPIC_PENDING_APPROVAL: str = (
    "⏳ 이 주제(`{topic_key}`)는 이미 `#승인-대기` 에 카드가 올라가 있어요 "
    "(job=`{approval_job_id}`). 사용자가 카드에 “승인” 또는 “반려” 답신을 "
    "남기면 다음 단계로 진행합니다."
)
RESPONSE_TOPIC_ALREADY_SAVED: str = (
    "📚 이 주제(`{topic_key}`)는 이미 vault 에 저장되어 있어요 "
    "(`{vault_path}`). 개정본으로 다시 저장하려면 “개정본으로 저장해줘” "
    "처럼 명시 요청해 주세요. 자동 덮어쓰기는 막혀 있어요."
)


# Inputs the producer doesn't own — injected so tests don't need a
# live Discord client / SQLite cache / workflow store.
SessionListerFn = Callable[..., Iterable[Any]]


@dataclass(frozen=True)
class ForumObsidianHandoffOutcome:
    """What :func:`route_forum_obsidian_save_request` decided.

    ``handled`` is True iff the message was a save request directed
    at this producer. The Discord-side caller uses it to short-circuit
    its remaining routing — when ``handled`` is True the user got a
    response (success or friendly error) and no further engineering
    routing should run.

    ``approval_job_id`` is the queue row id when posting succeeded,
    so the caller can show it in the friendly reply for cross-reference.
    """

    handled: bool
    approval_job_id: Optional[str] = None
    skipped_reason: Optional[str] = None
    response_template: Optional[str] = None
    error: Optional[str] = None


def _find_existing_approval_for_message(
    *,
    queue: Any,
    session_id: str,
    approval_kind: str,
    source_message_id: Optional[int],
) -> Optional[Any]:
    """Find any prior approval_post row (any state, including SAVED)
    keyed on the same source forum message id.

    The caller's idempotency key is the forum-thread message id —
    Discord guarantees it's unique per message. So as long as a
    prior approval row carries the same ``source_message_id`` we
    can safely reuse it instead of enqueuing a duplicate. Walks the
    session's row list once; the queue is small per-session.
    """

    if not session_id or source_message_id is None:
        return None
    try:
        rows = queue.list_for_session(session_id)
    except Exception:  # noqa: BLE001
        return None
    for row in rows or ():
        if getattr(row, "job_type", None) != "approval_post":
            continue
        payload = getattr(row, "payload", None) or {}
        if str(payload.get("approval_kind") or "") != approval_kind:
            continue
        existing_src = payload.get("source_message_id")
        try:
            if existing_src is not None and int(existing_src) == int(
                source_message_id
            ):
                return row
        except (TypeError, ValueError):
            continue
    return None


def _is_forum_thread_message(message: Any) -> bool:
    """Best-effort 'this came from a thread inside a forum channel' check.

    Discord text-channel messages have ``parent_id == None``;
    thread messages have ``parent_id`` pointing at the forum
    channel. We don't care which forum — the session-list match
    later restricts to research forum threads.
    """

    channel = getattr(message, "channel", None)
    if channel is None:
        return False
    parent_id = getattr(channel, "parent_id", None)
    parent = getattr(channel, "parent", None)
    return parent_id is not None or parent is not None


def _resolve_session_for_forum_thread(
    *,
    message: Any,
    session_lister: Optional[SessionListerFn],
) -> Optional[Any]:
    """Walk recent sessions, return the one whose
    ``session.extra['research_forum_thread_id']`` matches the
    forum-thread channel id. Returns the full session so the caller
    can read its prompt, role_sequence, etc.
    """

    if session_lister is None:
        return None
    channel = getattr(message, "channel", None)
    channel_id_raw = getattr(channel, "id", None)
    if channel_id_raw is None:
        return None
    try:
        channel_id = int(channel_id_raw)
    except (TypeError, ValueError):
        return None

    try:
        sessions = session_lister(limit=100)
    except TypeError:
        try:
            sessions = session_lister()
        except Exception:  # noqa: BLE001 - lister is best-effort
            return None
    except Exception:  # noqa: BLE001
        return None

    for session in sessions or ():
        extra = getattr(session, "extra", None) or {}
        if not isinstance(extra, Mapping):
            continue
        forum_thread_id = extra.get("research_forum_thread_id")
        if forum_thread_id is None:
            continue
        try:
            if int(forum_thread_id) == channel_id:
                return session
        except (TypeError, ValueError):
            continue
    return None


def _build_approval_request(
    *,
    session: Any,
    message: Any,
    requested_by: str,
    requested_at: str,
    ledger_record: Any,
    snapshot_payload: Mapping[str, Any],
    selected_roles: Sequence[str] = (),
) -> ApprovalRequest:
    """Compose the :class:`ApprovalRequest` for a forum-thread save.

    The ``extra`` mapping carries everything the approval reply
    router needs to hydrate the knowledge note on the writer side
    — without re-walking the session list or the thread:

      * ``source_thread_url`` / ``source_thread_title`` — operator
        navigation; preserved through the M5a-2 reply converter.
      * ``topic_key`` / ``canonical_title`` — A-M7.6 ledger fields
        so the writer's filename / frontmatter stay stable across
        revisions.
      * ``thread_snapshot`` — operator messages + role-by-role
        summary + extracted links (see :class:`ThreadSnapshot`).
      * ``selected_roles`` — what the role-selection layer picked
        for this session at intake time.
    """

    channel = getattr(message, "channel", None)
    thread_id = _safe_int(getattr(channel, "id", None))
    thread_name = (
        getattr(channel, "name", None)
        or getattr(channel, "_name", None)
        or ""
    )
    thread_url = (
        getattr(message, "jump_url", None)
        or _build_jump_url(getattr(channel, "guild", None), channel, message)
    )
    source_message_id = _safe_int(getattr(message, "id", None))

    canonical_title = getattr(ledger_record, "canonical_title", "") or ""
    topic_key = getattr(ledger_record, "topic_key", "") or ""

    extra_meta: dict[str, Any] = {
        "decision_id": f"forum-save:{topic_key or 'no-topic'}:"
        f"{source_message_id or ''}",
        "policy_level": "L3_HUMAN_REQUIRED",
        "source_thread_title": thread_name,
        "source_thread_url": thread_url,
        "requested_by": requested_by,
        "requested_at": requested_at,
        "origin": "research_forum_save_request",
        # A-M7.6 hydration payload — ObsidianWriteRequest.metadata
        # picks these up via approval_to_obsidian_write_request.
        "topic_key": topic_key,
        "canonical_title": canonical_title,
        "thread_snapshot": dict(snapshot_payload or {}),
        "selected_roles": list(selected_roles),
        "ledger_revision": getattr(ledger_record, "revision", 1),
    }

    research_pack = (getattr(session, "extra", None) or {}).get("research_pack")
    if isinstance(research_pack, Mapping):
        title_hint = research_pack.get("title")
        if isinstance(title_hint, str) and title_hint.strip():
            extra_meta["research_pack_title"] = title_hint.strip()

    summary = _build_summary(session, thread_name)
    # A-M7.6 — title comes from the canonical ledger title, not the
    # raw research_pack / prompt. The ledger normaliser already
    # stripped ``[Research]`` prefixes and capped length.
    title = canonical_title or _build_title(session, thread_name, research_pack)
    requested_action = (
        "현재 운영-리서치 thread 의 합의안과 자료를 Obsidian vault 에 "
        "knowledge note 로 적재합니다."
    )

    return ApprovalRequest(
        session_id=str(getattr(session, "session_id", "")),
        approval_kind=APPROVAL_KIND_OBSIDIAN_WRITE,
        title=title,
        summary=summary,
        requested_action=requested_action,
        created_by=requested_by,
        source_channel_id=_safe_int(
            getattr(channel, "parent_id", None)
            or getattr(channel, "id", None)
        ),
        source_thread_id=thread_id,
        source_message_id=source_message_id,
        extra=extra_meta,
    )


def _build_title(
    session: Any, thread_name: str, research_pack: Any
) -> str:
    if isinstance(research_pack, Mapping):
        title_hint = research_pack.get("title")
        if isinstance(title_hint, str) and title_hint.strip():
            return title_hint.strip()
    if thread_name:
        return str(thread_name).strip()
    prompt = (getattr(session, "prompt", "") or "").strip()
    if prompt:
        return prompt[:80]
    return f"세션 {getattr(session, 'session_id', '')}"


def _build_summary(session: Any, thread_name: str) -> str:
    prompt = (getattr(session, "prompt", "") or "").strip()
    if prompt:
        head = prompt[:160]
        if len(prompt) > 160:
            head += "…"
        return f"운영-리서치 thread (`{thread_name}`) — {head}"
    return f"운영-리서치 thread (`{thread_name}`) 의 합의안 저장 요청"


# ---------------------------------------------------------------------------
# A-M7.6 — topic-key dedup + revision detection
# ---------------------------------------------------------------------------


_REVISION_PHRASES: Tuple[str, ...] = (
    "개정본",
    "다시 저장",
    "덮어써",
    "갱신해",
    "다시 정리해",
    "revision",
    "supersede",
    "overwrite",
)


def _is_revision_request(text: Optional[str]) -> bool:
    """Whether the user explicitly opted into a revision write.

    The default save phrase ("Obsidian 에 정리해줘") is treated as a
    new save; only when the user adds a revision marker do we
    bypass the saved-state guard.
    """

    if not text:
        return False
    lowered = text.lower()
    return any(phrase in lowered for phrase in _REVISION_PHRASES)


def _topic_dedup_check(
    *,
    queue: Any,
    session_id: str,
    topic_key: str,
    research_thread_id: Optional[int],
    note_kind: str,
) -> Optional[Tuple[str, Any]]:
    """Look for any prior approval_post or obsidian_write row that
    targets the same topic + thread + kind.

    Returns ``(reason, row)`` where reason is one of:
      * ``"topic_pending"`` — approval_post still in_flight (queued/
        assigned/in_progress) or saved (carded but not yet replied).
      * ``"topic_obsidian_in_flight"`` — obsidian_write already
        queued/in_progress for the topic (treat as pending — the
        writer will pick it up).
      * ``"topic_saved"`` — obsidian_write already SAVED with a
        vault_path; revision flow needed.

    Returns ``None`` when no matching row exists — caller proceeds
    to enqueue a fresh approval card.
    """

    if not session_id or not topic_key:
        return None
    try:
        rows = queue.list_for_session(session_id)
    except Exception:  # noqa: BLE001
        return None

    pending_approval: Optional[Any] = None
    obsidian_in_flight: Optional[Any] = None
    obsidian_saved: Optional[Any] = None

    for row in rows or ():
        payload = getattr(row, "payload", None) or {}
        result = getattr(row, "result", None) or {}
        row_thread = payload.get("source_thread_id")
        try:
            row_thread_int = (
                int(row_thread) if row_thread is not None else None
            )
        except (TypeError, ValueError):
            row_thread_int = None

        # Topic match: prefer explicit topic_key in the row's
        # extra/metadata; fall back to thread id when both rows
        # share it (older rows pre-M7.6 don't carry topic_key).
        row_topic = ""
        if isinstance(payload.get("extra"), Mapping):
            row_topic = str(payload["extra"].get("topic_key") or "")
        if not row_topic and isinstance(payload.get("metadata"), Mapping):
            row_topic = str(payload["metadata"].get("topic_key") or "")
        topic_match = (
            (row_topic and row_topic == topic_key)
            or (
                research_thread_id is not None
                and row_thread_int == research_thread_id
            )
        )
        if not topic_match:
            continue

        job_type = getattr(row, "job_type", "")
        state_value = getattr(getattr(row, "state", None), "value", "")

        if job_type == "approval_post":
            row_kind = str(payload.get("approval_kind") or "")
            if row_kind != APPROVAL_KIND_OBSIDIAN_WRITE:
                continue
            # Pending approval = any non-terminal state.
            if state_value not in {"failed_terminal", "failed_retryable"}:
                pending_approval = pending_approval or row
        elif job_type == "obsidian_write":
            row_kind = str(payload.get("note_kind") or "")
            if row_kind != note_kind:
                continue
            if state_value == "saved":
                obsidian_saved = obsidian_saved or row
            elif state_value not in {"failed_terminal", "failed_retryable"}:
                obsidian_in_flight = obsidian_in_flight or row

    # Saved wins (revision-needed) over in-flight; in-flight wins
    # over pending-approval (further along in the lifecycle).
    if obsidian_saved is not None:
        return ("topic_saved", obsidian_saved)
    if obsidian_in_flight is not None:
        return ("topic_obsidian_in_flight", obsidian_in_flight)
    if pending_approval is not None:
        return ("topic_pending", pending_approval)
    return None


def _vault_path_from_row(row: Any) -> Optional[str]:
    """Best-effort extraction of vault path from a SAVED obsidian
    write row. The worker stamps target_path / vault_root on the
    result; we read whichever is populated.
    """

    result = getattr(row, "result", None) or {}
    if not isinstance(result, Mapping):
        return None
    target = result.get("target_path") or result.get("vault_path")
    if target:
        return str(target)
    return None


async def route_forum_obsidian_save_request(
    *,
    message: Any,
    text: str,
    queue: Any,
    approval_worker: ApprovalWorker,
    session_lister: Optional[SessionListerFn] = None,
    save_request_detector: Optional[Callable[[str], bool]] = None,
    now: Optional[datetime] = None,
    requested_by_resolver: Optional[Callable[[Any], str]] = None,
    thread_history_fetcher: Optional[
        Callable[[Any], Awaitable[Iterable[Any]]]
    ] = None,
    role_resolver: Optional[Callable[[Any], Optional[str]]] = None,
    session_updater: Optional[Callable[..., Any]] = None,
    note_kind: str = "knowledge",
) -> ForumObsidianHandoffOutcome:
    """Detect a save request and enqueue a #승인-대기 card via the worker.

    A-M7.6 dedup priority — first match wins, callers fall through:

      1. **Topic pending approval** — same ``topic_key`` (or thread
         when topic_key is missing for legacy rows) already has an
         active ``approval_post``. Reply with the existing job id;
         no new card.
      2. **Topic obsidian write in-flight** — approval was given,
         writer hasn't drained yet. Same friendly "already pending".
      3. **Topic already saved** — vault note exists. Reply with
         the saved vault path and ask for an explicit "개정본으로
         저장해줘" if the user wants a revision. The producer DOES
         enqueue a fresh card when *text* matches a revision phrase.
      4. **Same source_message_id** — message-level idempotency
         (M7.5). Operator double-tapping the same Discord message
         can't accidentally enqueue twice.

    *thread_history_fetcher* is a coroutine returning recent thread
    messages (production wires it to ``message.channel.history``);
    when ``None`` the snapshot only contains the save-request
    message itself. *role_resolver* maps Discord author → role id.
    *session_updater* persists ledger transitions; production wires
    ``workflow_state.update_session``.
    """

    detector = save_request_detector or _default_save_request_detector
    if not text or not detector(text):
        return ForumObsidianHandoffOutcome(
            handled=False, skipped_reason=SKIPPED_NOT_SAVE_REQUEST
        )

    if not _is_forum_thread_message(message):
        return ForumObsidianHandoffOutcome(
            handled=False, skipped_reason=SKIPPED_NOT_SAVE_REQUEST
        )

    session = _resolve_session_for_forum_thread(
        message=message, session_lister=session_lister
    )
    if session is None:
        return ForumObsidianHandoffOutcome(
            handled=True,
            skipped_reason=SKIPPED_NO_SESSION_FOR_THREAD,
            response_template=RESPONSE_NO_SESSION_FOR_THREAD,
        )

    # A-M7.6 — build / read the topic ledger BEFORE generating the
    # approval request. The ledger gives us the canonical title +
    # topic key the dedup checks need.
    from ..lifecycle.research_topic import (
        STATUS_PENDING_APPROVAL,
        STATUS_SAVED,
        build_ledger_record,
        read_topic_ledger,
        transition_topic_ledger,
        write_topic_ledger,
    )
    from ..lifecycle.thread_snapshot import (
        ThreadSnapshot,
        collapse_thread_to_snapshot,
        extract_links_from_text,
    )

    channel = getattr(message, "channel", None)
    research_thread_id = _safe_int(getattr(channel, "id", None))
    selected_roles = _read_selected_roles(session)

    existing_record = read_topic_ledger(session)
    ledger_record = existing_record or build_ledger_record(
        session=session,
        research_thread_id=research_thread_id,
        active_roles=selected_roles,
    )

    # Topic-level dedup (priority 1-3).
    revision_requested = _is_revision_request(text)
    dedup = _topic_dedup_check(
        queue=queue,
        session_id=str(getattr(session, "session_id", "")),
        topic_key=ledger_record.topic_key,
        research_thread_id=research_thread_id,
        note_kind=note_kind,
    )
    if dedup is not None:
        reason, row = dedup
        if reason == "topic_saved" and not revision_requested:
            vault_path = _vault_path_from_row(row) or "vault"
            return ForumObsidianHandoffOutcome(
                handled=True,
                approval_job_id=getattr(row, "job_id", None),
                skipped_reason=SKIPPED_TOPIC_ALREADY_SAVED,
                response_template=RESPONSE_TOPIC_ALREADY_SAVED.format(
                    topic_key=ledger_record.topic_key,
                    vault_path=vault_path,
                ),
            )
        if reason in {"topic_pending", "topic_obsidian_in_flight"}:
            return ForumObsidianHandoffOutcome(
                handled=True,
                approval_job_id=getattr(row, "job_id", None),
                skipped_reason=SKIPPED_TOPIC_PENDING_APPROVAL,
                response_template=RESPONSE_TOPIC_PENDING_APPROVAL.format(
                    topic_key=ledger_record.topic_key,
                    approval_job_id=getattr(row, "job_id", "?") or "?",
                ),
            )
        # topic_saved + revision_requested → fall through and enqueue
        # a new approval card (revision lifecycle).

    # Priority 4 — message-level idempotency (M7.5).
    requested_by = (
        requested_by_resolver(message)
        if requested_by_resolver is not None
        else _default_requested_by(message)
    )
    when = (now or datetime.now(tz=timezone.utc)).replace(microsecond=0)
    requested_at = when.isoformat()

    # Collect the thread snapshot (Discord history + the save
    # request itself + URLs in the request text). Production wires
    # ``thread_history_fetcher``; tests pass a stub list.
    raw_history: list[Any] = []
    if thread_history_fetcher is not None:
        try:
            history_result = await thread_history_fetcher(message)
            if history_result is not None:
                raw_history = list(history_result)
        except Exception:  # noqa: BLE001 - history fetch best-effort
            logger.warning(
                "forum obsidian handoff: thread history fetch raised",
                exc_info=True,
            )
            raw_history = []
    # The triggering message itself counts toward the snapshot — it
    # often carries "see https://..." links.
    raw_history.append(message)
    snapshot: ThreadSnapshot = collapse_thread_to_snapshot(
        raw_history,
        role_resolver=role_resolver,
        captured_at=requested_at,
    )

    # Inject session.extra signals (role_research_results /
    # synthesis_text / pre-extracted links) into the snapshot when
    # the live thread didn't carry them. This keeps the renderer
    # populated even on a sparse forum thread.
    snapshot = _enrich_snapshot_from_session(
        snapshot=snapshot, session=session, request_text=text
    )

    # Snapshot ready — also bump ledger to PENDING_APPROVAL so the
    # next save request on the same topic short-circuits at dedup.
    transitioned = transition_topic_ledger(
        ledger_record,
        status=STATUS_PENDING_APPROVAL,
        revision_bump=revision_requested,
    )
    _persist_ledger(
        session=session,
        record=transitioned,
        session_updater=session_updater,
    )
    ledger_record = transitioned

    request = _build_approval_request(
        session=session,
        message=message,
        requested_by=requested_by,
        requested_at=requested_at,
        ledger_record=ledger_record,
        snapshot_payload=snapshot.to_payload(),
        selected_roles=selected_roles,
    )

    existing = _find_existing_approval_for_message(
        queue=queue,
        session_id=request.session_id,
        approval_kind=request.approval_kind,
        source_message_id=request.source_message_id,
    )
    if existing is not None:
        return ForumObsidianHandoffOutcome(
            handled=True,
            approval_job_id=getattr(existing, "job_id", None),
            skipped_reason=SKIPPED_DUPLICATE_APPROVAL,
            response_template=RESPONSE_APPROVAL_DUPLICATE,
        )

    try:
        outcome = await approval_worker.run_one(request)
    except Exception as exc:  # noqa: BLE001 - never let producer crash on_message
        logger.warning(
            "forum obsidian handoff: ApprovalWorker.run_one raised",
            exc_info=True,
        )
        return ForumObsidianHandoffOutcome(
            handled=True,
            skipped_reason=SKIPPED_APPROVAL_WORKER_RAISED,
            error=_short_error(exc),
            response_template=RESPONSE_APPROVAL_FAILED,
        )

    job = getattr(outcome, "job", None)
    job_id = getattr(job, "job_id", None) if job is not None else None
    skipped = getattr(outcome, "skipped_reason", None)

    if skipped == "duplicate_in_flight":
        return ForumObsidianHandoffOutcome(
            handled=True,
            approval_job_id=job_id,
            skipped_reason=SKIPPED_DUPLICATE_APPROVAL,
            response_template=RESPONSE_APPROVAL_DUPLICATE,
        )
    if skipped == "approval_channel_unset":
        return ForumObsidianHandoffOutcome(
            handled=True,
            approval_job_id=job_id,
            skipped_reason=SKIPPED_APPROVAL_CHANNEL_UNSET,
            response_template=RESPONSE_APPROVAL_CHANNEL_UNSET,
        )
    if skipped is not None:
        # claimed_by_other_worker / unknown — surface as friendly fail.
        return ForumObsidianHandoffOutcome(
            handled=True,
            approval_job_id=job_id,
            skipped_reason=skipped,
            response_template=RESPONSE_APPROVAL_FAILED,
            error=skipped,
        )

    return ForumObsidianHandoffOutcome(
        handled=True,
        approval_job_id=job_id,
        response_template=RESPONSE_APPROVAL_QUEUED,
    )


def render_handoff_response(
    outcome: ForumObsidianHandoffOutcome,
) -> Optional[str]:
    """Format the friendly Discord reply for *outcome*.

    Returns ``None`` when there's nothing to say (handled=False, or
    intentional silent skip). Otherwise renders the response template
    with any captured fields (job id / error string).
    """

    if not outcome.handled or outcome.response_template is None:
        return None
    text = outcome.response_template
    fields: dict[str, str] = {}
    if outcome.approval_job_id:
        fields["approval_job_id"] = outcome.approval_job_id
    if outcome.error:
        fields["error"] = outcome.error
    try:
        return text.format(**fields)
    except KeyError:
        return text


# ---------------------------------------------------------------------------
# Defaults — production wiring uses these; tests inject stubs.
# ---------------------------------------------------------------------------


def _default_save_request_detector(text: str) -> bool:
    """Lazy import of the canonical phrase detector. Keeps this
    module light when only the producer dataclasses are needed.
    """

    try:
        from ..obsidian.approval import is_obsidian_save_request
    except Exception:  # noqa: BLE001 - partial install fallback
        return False
    return bool(is_obsidian_save_request(text))


def _default_requested_by(message: Any) -> str:
    author = getattr(message, "author", None)
    if author is None:
        return "unknown"
    name = (
        getattr(author, "global_name", None)
        or getattr(author, "name", None)
        or getattr(author, "display_name", None)
    )
    user_id = getattr(author, "id", None)
    if name and user_id is not None:
        return f"{name} ({user_id})"
    if name:
        return str(name)
    if user_id is not None:
        return f"user:{user_id}"
    return "unknown"


def _build_jump_url(guild: Any, channel: Any, message: Any) -> Optional[str]:
    guild_id = _safe_int(getattr(guild, "id", None))
    channel_id = _safe_int(getattr(channel, "id", None))
    message_id = _safe_int(getattr(message, "id", None))
    if guild_id is None or channel_id is None or message_id is None:
        return None
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _short_error(exc: BaseException) -> str:
    msg = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    return f"{type(exc).__name__}: {msg}"[:300]


# ---------------------------------------------------------------------------
# A-M7.6 — session.extra readers + ledger persistence shim
# ---------------------------------------------------------------------------


def _read_selected_roles(session: Any) -> Sequence[str]:
    extra = getattr(session, "extra", None) or {}
    if not isinstance(extra, Mapping):
        return ()
    raw = extra.get("active_research_roles")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(r) for r in raw if isinstance(r, str) and r)


def _enrich_snapshot_from_session(
    *,
    snapshot: Any,
    session: Any,
    request_text: Optional[str],
) -> Any:
    """Pull session.extra signals (role_research_results /
    research_synthesis_text / research_pack URLs) into the
    snapshot when the live thread fetcher didn't catch them.

    Returns a (possibly new) :class:`ThreadSnapshot`. Pure — does
    not mutate session.extra.
    """

    from ..lifecycle.thread_snapshot import (
        ThreadSnapshot,
        extract_links_from_text,
    )

    extra = getattr(session, "extra", None) or {}
    if not isinstance(extra, Mapping):
        return snapshot

    # Role-by-role research summaries (Phase 4 persistence).
    role_summaries = dict(getattr(snapshot, "role_summaries", None) or {})
    raw_role_results = extra.get("role_research_results")
    if isinstance(raw_role_results, Mapping):
        for role, payload in raw_role_results.items():
            if not isinstance(payload, Mapping):
                continue
            top_findings = payload.get("top_findings")
            summary_bits: list[str] = []
            if isinstance(top_findings, list):
                for finding in top_findings[:3]:
                    if isinstance(finding, str) and finding.strip():
                        summary_bits.append(finding.strip())
                    elif isinstance(finding, Mapping):
                        title = finding.get("title") or finding.get("snippet")
                        if isinstance(title, str) and title.strip():
                            summary_bits.append(title.strip())
            if summary_bits and not role_summaries.get(str(role)):
                role_summaries[str(role)] = " · ".join(summary_bits)

    # Tech-lead synthesis text → push into role_summaries under
    # "tech-lead" if the thread didn't already capture it.
    synth_text = extra.get("research_synthesis_text")
    if (
        isinstance(synth_text, str)
        and synth_text.strip()
        and "tech-lead" not in role_summaries
    ):
        head = synth_text.strip().splitlines()[0]
        role_summaries["tech-lead"] = head[:300]

    # Augment links with the research_pack URLs + URLs in the
    # save-request text (fallback for sparse threads).
    links_existing = list(getattr(snapshot, "extracted_links", None) or [])
    seen = set(links_existing)

    def _add(url: str) -> None:
        if url and url not in seen:
            seen.add(url)
            links_existing.append(url)

    raw_pack = extra.get("research_pack")
    if isinstance(raw_pack, Mapping):
        urls = raw_pack.get("urls")
        if isinstance(urls, list):
            for url in urls:
                if isinstance(url, str):
                    _add(url)
    for url in extract_links_from_text(request_text):
        _add(url)

    return ThreadSnapshot(
        messages=getattr(snapshot, "messages", ()),
        extracted_links=tuple(links_existing),
        role_summaries=role_summaries,
        captured_at=getattr(snapshot, "captured_at", None),
    )


def _persist_ledger(
    *,
    session: Any,
    record: Any,
    session_updater: Optional[Callable[..., Any]] = None,
) -> None:
    """Best-effort persistence of the topic ledger record. Failure
    is swallowed — the ledger is observability; losing it doesn't
    block the approval card from going out.
    """

    if session is None:
        return
    try:
        from dataclasses import replace as _replace

        from ..lifecycle.research_topic import write_topic_ledger
        from ..workflow_state import update_session as _default_update
    except Exception:  # noqa: BLE001
        return

    updater = session_updater or _default_update
    extra_in = dict(getattr(session, "extra", None) or {})
    new_extra = write_topic_ledger(extra_in, record)
    try:
        updated = _replace(session, extra=new_extra)
    except TypeError:
        # SimpleNamespace-shaped session in tests — mutate in place.
        if isinstance(getattr(session, "extra", None), dict):
            session.extra.update(new_extra)
        return
    try:
        updater(updated, now=datetime.now(tz=timezone.utc))
    except Exception:  # noqa: BLE001
        logger.warning(
            "forum obsidian handoff: ledger persist raised", exc_info=True
        )


__all__ = (
    "ForumObsidianHandoffOutcome",
    "RESPONSE_APPROVAL_CHANNEL_UNSET",
    "RESPONSE_APPROVAL_DUPLICATE",
    "RESPONSE_APPROVAL_FAILED",
    "RESPONSE_APPROVAL_QUEUED",
    "RESPONSE_NO_SESSION_FOR_THREAD",
    "RESPONSE_TOPIC_ALREADY_SAVED",
    "RESPONSE_TOPIC_PENDING_APPROVAL",
    "SKIPPED_APPROVAL_CHANNEL_UNSET",
    "SKIPPED_APPROVAL_WORKER_RAISED",
    "SKIPPED_DUPLICATE_APPROVAL",
    "SKIPPED_NO_SESSION_FOR_THREAD",
    "SKIPPED_NOT_SAVE_REQUEST",
    "SKIPPED_TOPIC_ALREADY_SAVED",
    "SKIPPED_TOPIC_PENDING_APPROVAL",
    "render_handoff_response",
    "route_forum_obsidian_save_request",
)
