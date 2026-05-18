"""Discord adapter that routes ``#승인-대기`` replies into the
queue's :func:`handle_approval_reply` — A-M6.1b-2.

Pure-Python helper. ``bot.py`` 's ``on_message`` calls
:func:`route_approval_channel_message` early, before the
engineering route. When the message arrives in the configured
approval channel, this helper:

  1. Resolves the matching ``approval_post`` job by walking each
     open WorkflowSession (the user's reply usually doesn't carry
     a session id; we scan recent open sessions).
  2. Calls :func:`handle_approval_reply` with the parsed reply +
     the user / channel / message ids.
  3. Renders a short friendly response via *send_chunks*.

If the message isn't in the approval channel the function returns
``handled=False`` so ``on_message`` falls through to its existing
engineering routing — the legacy in-channel approval UX is
untouched.

The helper is sync-friendly: every Discord-side dependency
(channel id matchers, send_chunks, session_lister) is injected
so unit tests can drive every branch without a real Discord
client.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Optional, Sequence

from ...agents.job_queue.approval_reply import (
    APPROVAL_KIND_OBSIDIAN_WRITE,
    ApprovalIntent,
    ApprovalReplyOutcome,
    find_replyable_approval,
    handle_approval_reply,
    parse_approval_intent,
)
from ...agents.job_queue.approval_worker import (
    APPROVAL_KIND_ENGINEERING_WRITE,
    ApprovalRequest,
)
from ...agents.job_queue.obsidian_writer_worker import ObsidianWriterWorker
from ...agents.job_queue.pr_approval import (
    APPROVAL_KIND_PR_MERGE,
    PRMergeExecutor,
    PRMergeReplyIntent,
    PRMergeReplyResult,
    handle_pr_merge_approval_reply,
)
from ...agents.job_queue.operator_action_reply import (
    OperatorActionReplyOutcome,
    find_pending_operator_action_for_reply,
    handle_operator_action_reply,
)
from ...agents.job_queue.store import JobQueue
from ...agents.operator_action import OperatorActionType, OperatorSessionState


logger = logging.getLogger(__name__)


# Friendly response templates. Operator can read these directly
# from the test snapshots — they are the entire user-facing
# vocabulary the queue path adds, separate from the legacy
# in-channel approval UX.
RESPONSE_APPROVED: str = (
    "✅ 승인 받았어요. Obsidian 저장 큐에 넣었습니다 (job=`{write_job_id}`)."
)
RESPONSE_DUPLICATE: str = (
    "⏳ 이미 같은 결정이 저장 큐에 들어가 있어요. 기존 job 을 그대로 진행합니다."
)
RESPONSE_REJECTED: str = (
    "🚫 반려 처리했어요. 저장하지 않도록 audit 에 기록했습니다."
)
RESPONSE_NO_MATCH: str = (
    "❓ 답신에 매칭되는 승인 카드를 못 찾았어요. 카드 게시 후 30 분 안에 답해 주세요."
)
RESPONSE_HOLD_OR_UNCLEAR: str = (
    "⏸ 승인/반려 의도를 확인할 수 없어요. `승인` / `이대로 진행` 또는 `반려` 로 답해 주세요."
)
RESPONSE_UNSUPPORTED_KIND: str = (
    "ℹ️ 이 승인 유형은 아직 자동 처리하지 않아요. 운영자가 별도로 처리합니다."
)
RESPONSE_REJECTION_AUDIT_FAILED: str = (
    "⚠️ 반려는 인식했지만 audit 기록에 실패했어요. 운영자에게 다시 알려 주세요."
)


# Operator action reply (P0-S) — INFO/ACCESS/SECRET/DECISION 카드 ack.
RESPONSE_OPERATOR_INFO_OK: str = (
    "📥 정보 받았어요. 세션을 다시 진행 (`running`) 으로 돌려놓을게요."
)
RESPONSE_OPERATOR_ACCESS_OK: str = (
    "🔐 접근 정보 받았어요. 세션을 다시 진행 (`running`) 으로 돌려놓을게요."
)
RESPONSE_OPERATOR_SECRET_OK: str = (
    "🗝 secret 저장 위치 받았어요. 실제 값은 지정한 위치에서 주입됩니다 — agent 가 값을 직접 만들지 않습니다."
)
RESPONSE_OPERATOR_DECISION_OK: str = (
    "🧭 결정 받았어요. 세션을 다시 진행 (`running`) 으로 돌려놓을게요."
)
RESPONSE_OPERATOR_MISSING_KEYS: str = (
    "❓ 응답에서 필요한 `key=value` 를 찾지 못했어요. 카드의 답변 예시를 참고해 다시 답해 주세요."
)
RESPONSE_OPERATOR_SECRET_VALUE_REJECTED: str = (
    "🚫 secret 값을 채널에 직접 붙이지 마세요. 저장 위치만 지정해 주세요 — 예: `github_secret=JWT_SECRET` 또는 `env_file=.env.prod`."
)


# Engineering write — coding work_order 승인 응답
RESPONSE_ENGINEERING_APPROVED: str = (
    "✅ 코딩 작업 승인 받았어요. `github_work_order` 큐로 이어집니다 (job=`{job_id}`)."
)
RESPONSE_ENGINEERING_APPROVED_DUPLICATE: str = (
    "⏳ 같은 작업이 이미 `github_work_order` 큐에 있어요. 기존 job 을 그대로 진행합니다."
)
RESPONSE_ENGINEERING_REJECTED: str = (
    "🚫 코딩 작업 승인 거절. work_order 생성 없이 종료합니다."
)
RESPONSE_ENGINEERING_PROPOSAL_MISSING: str = (
    "⚠️ 카드 payload 에 work_order proposal 이 없어 dispatch 못 했어요. 운영자에게 다시 알려 주세요."
)


# PR merge approval — P1-L-2 wiring. handle_pr_merge_approval_reply 결과
# 를 사람 친화적으로 ack.
RESPONSE_PR_MERGE_MERGED: str = (
    "✅ PR 머지 승인 — gate 통과 후 merge 완료. merge_sha=`{merge_sha}`."
)
RESPONSE_PR_MERGE_GATE_FAILED: str = (
    "🚫 PR 머지 거부됨 — 5-step gate `{failed_step}` 실패. 이유: {reason}"
)
RESPONSE_PR_MERGE_DISABLED: str = (
    "⏸ PR 머지 비활성화 — `YULE_GITHUB_MERGE_ENABLED=true` 또는 merge_executor 가 wiring 되어야 동작합니다."
)
RESPONSE_PR_MERGE_REJECTED: str = (
    "🚫 PR 머지 반려 처리. audit 에 기록했습니다."
)
RESPONSE_PR_MERGE_REVISE: str = (
    "🔁 수정 후 다시 — 새 commit 이 push 되면 새 카드가 게시됩니다."
)
RESPONSE_PR_MERGE_NO_CARD: str = (
    "❓ 답신에 매칭되는 PR merge 카드를 못 찾았어요."
)


_OPERATOR_OK_RESPONSES: dict[OperatorActionType, str] = {
    OperatorActionType.INFO_REQUIRED: RESPONSE_OPERATOR_INFO_OK,
    OperatorActionType.ACCESS_REQUIRED: RESPONSE_OPERATOR_ACCESS_OK,
    OperatorActionType.SECRET_REQUIRED: RESPONSE_OPERATOR_SECRET_OK,
    OperatorActionType.DECISION_REQUIRED: RESPONSE_OPERATOR_DECISION_OK,
}


# Inputs the adapter doesn't own — injected so tests can drive the
# behaviour without a real Discord client / live SQLite session.
SessionListerFn = Callable[[], Iterable[Any]]
SendChunksFn = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ApprovalReplyRouteResult:
    """What ``on_message`` learned from this helper.

    ``handled=True`` means the helper either replied to the user
    or recognised the channel as the approval channel and chose
    a deliberate no-op. The caller must NOT fall through to the
    engineering route in that case — replies in the approval
    channel are not engineering intake.

    P0-S — operator-action 카드 (INFO/ACCESS/SECRET/DECISION) 응답이
    매칭되면 ``operator_outcome`` 이 채워진다. ``outcome`` (기존
    APPROVE/REJECT 처리 결과) 과 둘 중 하나만 set 된다.
    """

    handled: bool
    outcome: Optional[ApprovalReplyOutcome] = None
    response_sent: Optional[str] = None
    skipped_reason: Optional[str] = None
    operator_outcome: Optional[OperatorActionReplyOutcome] = None


def is_approval_channel_message(
    *,
    message: Any,
    approval_channel_id: Optional[int],
    approval_channel_name: Optional[str] = None,
) -> bool:
    """ID-first, NAME-fallback channel matcher.

    NAME match is loose ("contains") so the same channel renamed
    from "승인-대기" to "승인 대기" doesn't break routing — the
    operator-facing convention only changes id, name is human
    readable.
    """

    channel = getattr(message, "channel", None)
    if channel is None:
        return False
    channel_id = getattr(channel, "id", None)
    if approval_channel_id is not None and channel_id is not None:
        try:
            if int(channel_id) == int(approval_channel_id):
                return True
        except (TypeError, ValueError):
            pass
    if approval_channel_name:
        channel_name = (
            getattr(channel, "name", None)
            or getattr(channel, "_name", None)
            or ""
        )
        if approval_channel_name.strip() and approval_channel_name.strip() in str(channel_name):
            return True
    return False


async def route_approval_channel_message(
    *,
    message: Any,
    bot_user: Any,
    queue: JobQueue,
    obsidian_worker: ObsidianWriterWorker,
    approval_channel_id: Optional[int],
    approval_channel_name: Optional[str] = None,
    session_lister: Optional[SessionListerFn] = None,
    send_chunks: SendChunksFn,
    now_iso: Optional[str] = None,
    pr_merge_executor: Optional[PRMergeExecutor] = None,
    on_pr_merge_result: Optional[Callable[[PRMergeReplyResult], Any]] = None,
    pr_merge_ready_for_review_action: Optional[Callable[..., Any]] = None,
) -> ApprovalReplyRouteResult:
    """Inspect *message* for an approval reply in the approval
    channel; if so, route through :func:`handle_approval_reply`
    and reply.

    Returns ``handled=False`` for messages outside the approval
    channel so ``on_message`` keeps its existing fall-through to
    the engineering route. Bot's own messages are dropped silently
    (handled=False) so a friendly reply doesn't trigger a recursive
    "approve" detection.
    """

    if getattr(getattr(message, "author", None), "bot", False):
        return ApprovalReplyRouteResult(handled=False)

    if not is_approval_channel_message(
        message=message,
        approval_channel_id=approval_channel_id,
        approval_channel_name=approval_channel_name,
    ):
        return ApprovalReplyRouteResult(handled=False)

    text = str(getattr(message, "content", "") or "").strip()
    if not text:
        return ApprovalReplyRouteResult(
            handled=True, skipped_reason="empty_message"
        )

    source_message_id = _safe_int(getattr(message, "id", None))
    source_thread_id = _safe_int(
        getattr(getattr(message, "channel", None), "id", None)
    )
    approved_by = _author_handle(message)
    # P0-T live smoke fix — Discord reply 가 카드를 직접 quote 한 경우
    # `message.reference.message_id` 가 posted_message_id 와 같음.
    # find_replyable_approval 에 전달돼 가장 강한 매칭 신호로 사용.
    replied_message_id = _safe_int(
        getattr(getattr(message, "reference", None), "message_id", None)
    )

    # P0-S — operator-action 카드 매칭이 우선. INFO/ACCESS/SECRET/DECISION
    # 카드는 ``key=value`` 어휘라서 일반 approval intent 파서에 걸리면
    # UNCLEAR 로 떨어져 사람 응답이 잠깐 사라진다. 채널의 SAVED
    # operator-action 카드를 먼저 본다.
    operator_session_id = _resolve_session_for_reply(
        message=message, session_lister=session_lister
    )
    if operator_session_id and (
        find_pending_operator_action_for_reply(
            queue=queue,
            session_id=operator_session_id,
            source_message_id=source_message_id,
            source_thread_id=source_thread_id,
        )
        is not None
    ):
        op_outcome = handle_operator_action_reply(
            queue=queue,
            text=text,
            session_id=operator_session_id,
            answered_by=approved_by,
            answered_at=now_iso,
            source_message_id=source_message_id,
            source_thread_id=source_thread_id,
        )
        op_response = _render_operator_outcome_message(op_outcome)
        if op_response is not None:
            await send_chunks(message.channel, op_response)
        return ApprovalReplyRouteResult(
            handled=True,
            outcome=None,
            response_sent=op_response,
            skipped_reason=op_outcome.skipped_reason,
            operator_outcome=op_outcome,
        )

    intent = parse_approval_intent(text)
    if intent in (ApprovalIntent.HOLD, ApprovalIntent.UNCLEAR):
        # The helper still calls handle_approval_reply for
        # symmetry, but we know the outcome will be a no-op so
        # we save the cost of the SQLite scan.
        await send_chunks(message.channel, RESPONSE_HOLD_OR_UNCLEAR)
        return ApprovalReplyRouteResult(
            handled=True,
            outcome=None,
            response_sent=RESPONSE_HOLD_OR_UNCLEAR,
            skipped_reason="intent_not_actionable",
        )

    session_id = operator_session_id
    if not session_id:
        await send_chunks(message.channel, RESPONSE_NO_MATCH)
        return ApprovalReplyRouteResult(
            handled=True,
            outcome=None,
            response_sent=RESPONSE_NO_MATCH,
            skipped_reason="no_session_for_reply",
        )

    # P1-L-2 — PR merge approval card 가 같은 session 에 있으면 먼저 처리.
    # 같은 채널에 engineering_write / obsidian_write 와 동시에 떠 있을 수
    # 있어서 PR merge 가 가장 먼저 매칭되도록 보장 (5-step gate 가 들어가
    # 있는 가장 무거운 path).
    pr_merge_outcome = await _try_handle_pr_merge_reply(
        queue=queue,
        text=text,
        session_id=session_id,
        approved_by=approved_by,
        approved_at=now_iso,
        source_message_id=source_message_id,
        source_thread_id=source_thread_id,
        send_chunks=send_chunks,
        message=message,
        merge_executor=pr_merge_executor,
        on_result=on_pr_merge_result,
        ready_for_review_action=pr_merge_ready_for_review_action,
    )
    if pr_merge_outcome is not None:
        return pr_merge_outcome

    # P0-T live smoke fix (session c5278a9043f2 후속):
    # engineering_write 카드도 reply 매칭 — obsidian_write 만 보던 회귀 차단.
    # 매칭되면 handle_github_work_approval_reply 호출 → work_order dispatch.
    engineering_outcome = await _try_handle_engineering_write_reply(
        queue=queue,
        text=text,
        session_id=session_id,
        approved_by=approved_by,
        approved_at=now_iso,
        source_message_id=source_message_id,
        source_thread_id=source_thread_id,
        replied_message_id=replied_message_id,
        send_chunks=send_chunks,
        message=message,
        intent=intent,
    )
    if engineering_outcome is not None:
        return engineering_outcome

    outcome = handle_approval_reply(
        queue=queue,
        obsidian_worker=obsidian_worker,
        text=text,
        session_id=session_id,
        approved_by=approved_by,
        source_message_id=source_message_id,
        source_thread_id=source_thread_id,
        approval_kind=APPROVAL_KIND_OBSIDIAN_WRITE,
        approved_at=now_iso,
    )

    response = _render_outcome_message(outcome)
    if response is not None:
        await send_chunks(message.channel, response)

    return ApprovalReplyRouteResult(
        handled=True,
        outcome=outcome,
        response_sent=response,
        skipped_reason=outcome.skipped_reason,
    )


async def _try_handle_pr_merge_reply(
    *,
    queue: JobQueue,
    text: str,
    session_id: str,
    approved_by: str,
    approved_at: Optional[str],
    source_message_id: Optional[int],
    source_thread_id: Optional[int],
    send_chunks: SendChunksFn,
    message: Any,
    merge_executor: Optional[PRMergeExecutor],
    on_result: Optional[Callable[[PRMergeReplyResult], Any]],
    ready_for_review_action: Optional[Callable[..., Any]] = None,
) -> Optional[ApprovalReplyRouteResult]:
    """P1-L-2 — PR merge approval card 응답 처리.

    카드가 없으면 ``None`` 반환 (caller 가 engineering_write / obsidian
    fallback 로 계속). 카드가 있으면 :func:`handle_pr_merge_approval_reply`
    호출 후 결과를 친절한 한국어로 ack.
    """

    # 빠른 사전 체크 — 같은 session 에 PR merge 카드가 있는지.
    matching = find_replyable_approval(
        queue=queue,
        session_id=session_id,
        approval_kind=APPROVAL_KIND_PR_MERGE,
        source_message_id=source_message_id,
        source_thread_id=source_thread_id,
    )
    if matching is None:
        return None

    result = await handle_pr_merge_approval_reply(
        queue=queue,
        text=text,
        session_id=session_id,
        approved_by=approved_by,
        source_message_id=source_message_id,
        source_thread_id=source_thread_id,
        approved_at=approved_at or "",
        merge_executor=merge_executor,
        ready_for_review_action=ready_for_review_action,
    )

    response = _render_pr_merge_result(result)
    if response is not None:
        await send_chunks(message.channel, response)

    if on_result is not None:
        try:
            res = on_result(result)
            if hasattr(res, "__await__"):
                await res
        except Exception:  # noqa: BLE001 - never crash the router
            logger.warning(
                "pr_merge on_result callback raised for session %s",
                session_id,
                exc_info=True,
            )

    return ApprovalReplyRouteResult(
        handled=True,
        outcome=None,
        response_sent=response,
        skipped_reason=result.skipped_reason,
    )


def _render_pr_merge_result(result: PRMergeReplyResult) -> Optional[str]:
    """:class:`PRMergeReplyResult` → 한국어 ack 메시지."""

    if result.skipped_reason == "no_matching_approval":
        return RESPONSE_PR_MERGE_NO_CARD
    if result.intent == PRMergeReplyIntent.REJECT:
        return RESPONSE_PR_MERGE_REJECTED
    if result.intent == PRMergeReplyIntent.REVISE_AND_REPEAT:
        return RESPONSE_PR_MERGE_REVISE
    if result.intent in (PRMergeReplyIntent.HOLD, PRMergeReplyIntent.UNCLEAR):
        return RESPONSE_HOLD_OR_UNCLEAR
    # APPROVE 경로
    if result.merge_disabled:
        return RESPONSE_PR_MERGE_DISABLED
    if result.gate_failed_step:
        return RESPONSE_PR_MERGE_GATE_FAILED.format(
            failed_step=result.gate_failed_step,
            reason=result.gate_reason or "(없음)",
        )
    merge_result = result.merge_result or {}
    merge_sha = str(merge_result.get("merge_sha") or "")
    if merge_sha:
        return RESPONSE_PR_MERGE_MERGED.format(merge_sha=merge_sha[:12])
    return RESPONSE_PR_MERGE_NO_CARD


async def _try_handle_engineering_write_reply(
    *,
    queue: JobQueue,
    text: str,
    session_id: str,
    approved_by: str,
    approved_at: Optional[str],
    source_message_id: Optional[int],
    source_thread_id: Optional[int],
    replied_message_id: Optional[int],
    send_chunks: SendChunksFn,
    message: Any,
    intent: ApprovalIntent,
) -> Optional[ApprovalReplyRouteResult]:
    """engineering_write 카드 응답 처리. None 이면 카드 없음 → caller 가
    obsidian fallback 로 계속 진행."""

    # 1. 카드 찾기 — engineering_write kind 로 명시 검색
    job = find_replyable_approval(
        queue=queue,
        session_id=session_id,
        approval_kind=APPROVAL_KIND_ENGINEERING_WRITE,
        source_message_id=source_message_id,
        source_thread_id=source_thread_id,
        replied_message_id=replied_message_id,
    )
    if job is None:
        return None

    # 2. payload 에서 ApprovalRequest 빌드
    payload = job.payload or {}
    approval_request = ApprovalRequest.from_payload(payload)

    if intent == ApprovalIntent.REJECT:
        await send_chunks(message.channel, RESPONSE_ENGINEERING_REJECTED)
        return ApprovalReplyRouteResult(
            handled=True,
            outcome=None,
            response_sent=RESPONSE_ENGINEERING_REJECTED,
            skipped_reason="engineering_write_rejected",
        )

    # 3. APPROVE → work_order dispatch
    from ..integrations.github_workos_adapter import (
        handle_github_work_approval_reply,
    )

    dispatch_outcome = handle_github_work_approval_reply(
        queue=queue,
        approval_request=approval_request,
        approval_id=job.job_id,
        approved_by=approved_by,
        approved_at=approved_at,
        dry_run=None,
    )

    response: str
    if dispatch_outcome.skipped_reason == "proposal_payload_missing":
        response = RESPONSE_ENGINEERING_PROPOSAL_MISSING
    elif dispatch_outcome.skipped_reason and "duplicate" in dispatch_outcome.skipped_reason:
        response = RESPONSE_ENGINEERING_APPROVED_DUPLICATE
    elif dispatch_outcome.dispatched_job_id:
        response = RESPONSE_ENGINEERING_APPROVED.format(
            job_id=dispatch_outcome.dispatched_job_id
        )
    elif dispatch_outcome.skipped_reason:
        # awaiting_approval / approval_kind_mismatch 등 — 디버깅용 ack
        response = (
            f"ℹ️ engineering_write dispatch skip ({dispatch_outcome.skipped_reason})."
        )
    else:
        response = RESPONSE_ENGINEERING_APPROVED.format(job_id="-")

    await send_chunks(message.channel, response)
    return ApprovalReplyRouteResult(
        handled=True,
        outcome=None,
        response_sent=response,
        skipped_reason=dispatch_outcome.skipped_reason,
    )


def _render_operator_outcome_message(
    outcome: OperatorActionReplyOutcome,
) -> Optional[str]:
    """operator-action reply outcome → 짧은 ack 메시지.

    - ``rejected_reason == "secret_value_inline"``: 보안 가드. 채널에
      raw secret 을 붙인 응답을 명시적으로 거부한다.
    - ``rejected_reason == "missing_required_keys"`` 등 미완: 다시 답해
      달라고 안내.
    - 완료: request_type 별 친절한 ack.
    """

    if not outcome.handled:
        return None
    reply = outcome.reply
    if reply is None:
        return RESPONSE_OPERATOR_MISSING_KEYS

    if reply.rejected_reason == "secret_value_inline":
        return RESPONSE_OPERATOR_SECRET_VALUE_REJECTED
    if not reply.is_complete:
        return RESPONSE_OPERATOR_MISSING_KEYS

    request_type = outcome.request_type or reply.request_type
    return _OPERATOR_OK_RESPONSES.get(request_type, RESPONSE_OPERATOR_INFO_OK)


def _render_outcome_message(outcome: ApprovalReplyOutcome) -> Optional[str]:
    if outcome.intent == ApprovalIntent.APPROVE:
        if outcome.write_job_id is not None and outcome.skipped_reason is None:
            return RESPONSE_APPROVED.format(write_job_id=outcome.write_job_id)
        if outcome.skipped_reason == "duplicate_obsidian_write":
            return RESPONSE_DUPLICATE
        if outcome.skipped_reason == "no_matching_approval":
            return RESPONSE_NO_MATCH
        if outcome.skipped_reason == "approval_kind_not_handled":
            return RESPONSE_UNSUPPORTED_KIND
        # Fallback — shouldn't normally happen.
        return RESPONSE_NO_MATCH

    if outcome.intent == ApprovalIntent.REJECT:
        if outcome.skipped_reason == "no_matching_approval":
            return RESPONSE_NO_MATCH
        if outcome.rejection_recorded:
            return RESPONSE_REJECTED
        return RESPONSE_REJECTION_AUDIT_FAILED

    # HOLD / UNCLEAR are short-circuited before this runs; surface
    # the conservative response if we ever land here.
    return RESPONSE_HOLD_OR_UNCLEAR


# ---------------------------------------------------------------------------
# Session resolution
# ---------------------------------------------------------------------------


def _resolve_session_for_reply(
    *,
    message: Any,
    session_lister: Optional[SessionListerFn],
) -> Optional[str]:
    """Pick the session whose approval card the reply most likely
    targets.

    The reply text rarely carries a session id, so we walk the
    list of recent open sessions and return the first one whose
    ``thread_id`` matches the message's source channel id (the
    user replied in the work thread) or whose
    ``research_forum_thread_id`` matches. Falls back to the most
    recent open session for the same channel.

    The lister is injected so tests don't need a workflow store.
    Production wiring (``bot.py``) passes a closure around the
    real ``list_sessions`` query.
    """

    if session_lister is None:
        return None
    channel_id = _safe_int(
        getattr(getattr(message, "channel", None), "id", None)
    )
    sessions = list(session_lister() or ())
    if not sessions:
        return None

    if channel_id is not None:
        for session in sessions:
            thread_id = _safe_int(getattr(session, "thread_id", None))
            if thread_id is not None and thread_id == channel_id:
                sid = _safe_str(getattr(session, "session_id", None))
                if sid:
                    return sid
        for session in sessions:
            extra = getattr(session, "extra", None) or {}
            forum_id = _safe_int(
                extra.get("research_forum_thread_id")
                if isinstance(extra, dict)
                else None
            )
            if forum_id is not None and forum_id == channel_id:
                sid = _safe_str(getattr(session, "session_id", None))
                if sid:
                    return sid

    # Fallback — most recently updated session. Discord channels
    # like ``#승인-대기`` are global, not per-thread, so walking by
    # update order is the next-best pointer.
    most_recent = max(
        sessions,
        key=lambda s: _safe_str(getattr(s, "updated_at", "")) or "",
        default=None,
    )
    if most_recent is None:
        return None
    return _safe_str(getattr(most_recent, "session_id", None))


def _author_handle(message: Any) -> str:
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


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = (
    "ApprovalReplyRouteResult",
    "RESPONSE_APPROVED",
    "RESPONSE_DUPLICATE",
    "RESPONSE_ENGINEERING_APPROVED",
    "RESPONSE_ENGINEERING_APPROVED_DUPLICATE",
    "RESPONSE_ENGINEERING_PROPOSAL_MISSING",
    "RESPONSE_ENGINEERING_REJECTED",
    "RESPONSE_HOLD_OR_UNCLEAR",
    "RESPONSE_NO_MATCH",
    "RESPONSE_OPERATOR_ACCESS_OK",
    "RESPONSE_OPERATOR_DECISION_OK",
    "RESPONSE_OPERATOR_INFO_OK",
    "RESPONSE_OPERATOR_MISSING_KEYS",
    "RESPONSE_OPERATOR_SECRET_OK",
    "RESPONSE_OPERATOR_SECRET_VALUE_REJECTED",
    "RESPONSE_PR_MERGE_DISABLED",
    "RESPONSE_PR_MERGE_GATE_FAILED",
    "RESPONSE_PR_MERGE_MERGED",
    "RESPONSE_PR_MERGE_NO_CARD",
    "RESPONSE_PR_MERGE_REJECTED",
    "RESPONSE_PR_MERGE_REVISE",
    "RESPONSE_REJECTED",
    "RESPONSE_REJECTION_AUDIT_FAILED",
    "RESPONSE_UNSUPPORTED_KIND",
    "is_approval_channel_message",
    "route_approval_channel_message",
)
