"""Engineering-agent Discord workflow orchestrator.

Walks one task through ``intake → approved → in_progress → completed``
(or → ``rejected``). Composes the dispatcher's plan with reference picking
(user-provided URLs first, then task_type fallback) and produces the three
standard Discord messages: intake, progress, completion.

Single-executor + write gate is enforced here too: ``approve()`` is the
only path that flips a write-requested session into ``approved``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from .messaging.dispatcher import Dispatcher, DispatchPlan, DispatchRequest, TaskType
from .review_loop import (
    ReviewFeedback,
    ReviewRouting,
    format_review_intake_message,
    format_review_reply_message,
    from_payload as review_feedback_from_payload,
    route_review_feedback,
    to_payload as review_feedback_to_payload,
)
from .workflow_state import (
    WorkflowSession,
    WorkflowState,
    load_session,
    new_session_id,
    save_session,
    update_session,
)


_URL_PATTERN = re.compile(r"https?://[\w\-./?=&%#:+,@!~*'();$]+", re.IGNORECASE)


class WorkflowError(RuntimeError):
    """Raised when a state transition is not allowed."""


@dataclass(frozen=True)
class IntakeResult:
    session: WorkflowSession
    plan: DispatchPlan
    message: str


@dataclass(frozen=True)
class ProgressResult:
    session: WorkflowSession
    message: str


@dataclass(frozen=True)
class CompletionResult:
    session: WorkflowSession
    message: str


@dataclass(frozen=True)
class ReviewIntakeResult:
    session: WorkflowSession
    feedback: ReviewFeedback
    routing: ReviewRouting
    message: str


@dataclass(frozen=True)
class ReviewReplyResult:
    session: WorkflowSession
    message: str


class WorkflowOrchestrator:
    """Pure-Python orchestrator. Discord layer wraps this; CLI uses it directly.

    *now_fn* is injected so tests can pin time without monkeypatching.
    """

    def __init__(
        self,
        dispatcher: Dispatcher,
        *,
        now_fn: Optional[callable] = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.now_fn = now_fn or datetime.now

    def intake(
        self,
        prompt: str,
        *,
        task_type: Optional[TaskType] = None,
        write_requested: bool = False,
        channel_id: Optional[int] = None,
        user_id: Optional[int] = None,
        thread_id: Optional[int] = None,
        extra_user_links: Sequence[str] = (),
    ) -> IntakeResult:
        if not prompt.strip():
            raise WorkflowError("prompt must not be empty")

        request = DispatchRequest(
            prompt=prompt,
            task_type=task_type,
            write_requested=write_requested,
            user_approved=False,
        )
        plan = self.dispatcher.dispatch(request)
        executor = plan.executor()
        now = self.now_fn()
        user_links = _merge_user_links(extract_urls(prompt), extra_user_links)

        session = WorkflowSession(
            session_id=new_session_id(),
            prompt=prompt,
            task_type=plan.task_type.value,
            state=WorkflowState.INTAKE,
            created_at=now,
            updated_at=now,
            role_sequence=tuple(plan.role_sequence),
            executor_role=executor.role if executor else None,
            executor_runner=executor.runner_id if executor else None,
            references_user=user_links,
            references_suggested=tuple(plan.reference_sources),
            channel_id=channel_id,
            user_id=user_id,
            thread_id=thread_id,
            write_requested=write_requested,
            write_blocked_reason=plan.write_block_reason,
        )
        save_session(session)
        return IntakeResult(
            session=session,
            plan=plan,
            message=format_intake_message(session, plan),
        )

    def approve(self, session_id: str) -> WorkflowSession:
        session = self._require_session(session_id)
        if session.state not in {WorkflowState.INTAKE}:
            raise WorkflowError(
                f"session {session_id} is in state {session.state.value}; cannot approve"
            )
        approved = replace(
            session,
            state=WorkflowState.APPROVED,
            write_blocked_reason=None,
        )
        return update_session(approved, now=self.now_fn())

    def reject(self, session_id: str, *, reason: str) -> WorkflowSession:
        session = self._require_session(session_id)
        if session.state in {WorkflowState.COMPLETED, WorkflowState.REJECTED}:
            raise WorkflowError(
                f"session {session_id} is already {session.state.value}"
            )
        rejected = replace(
            session,
            state=WorkflowState.REJECTED,
            rejection_reason=reason or "rejected",
        )
        return update_session(rejected, now=self.now_fn())

    def progress(self, session_id: str, *, note: str) -> ProgressResult:
        session = self._require_session(session_id)
        if session.state == WorkflowState.INTAKE:
            raise WorkflowError(
                f"session {session_id} not yet approved; intake must be approved first"
            )
        if session.state in {WorkflowState.COMPLETED, WorkflowState.REJECTED}:
            raise WorkflowError(
                f"session {session_id} already {session.state.value}; progress is closed"
            )
        notes = tuple(session.progress_notes) + ((note.strip() or "(empty note)"),)
        in_progress = replace(session, state=WorkflowState.IN_PROGRESS, progress_notes=notes)
        updated = update_session(in_progress, now=self.now_fn())
        return ProgressResult(session=updated, message=format_progress_message(updated))

    def complete(
        self,
        session_id: str,
        *,
        summary: str,
        references_used: Sequence[Mapping[str, Any]] = (),
    ) -> CompletionResult:
        session = self._require_session(session_id)
        if session.state in {WorkflowState.COMPLETED, WorkflowState.REJECTED}:
            raise WorkflowError(
                f"session {session_id} already {session.state.value}"
            )
        if session.state == WorkflowState.INTAKE:
            raise WorkflowError(
                f"session {session_id} not yet approved; cannot complete from intake"
            )
        completed = replace(
            session,
            state=WorkflowState.COMPLETED,
            summary=(summary or "").strip() or None,
            references_used=tuple(dict(item) for item in references_used),
        )
        updated = update_session(completed, now=self.now_fn())
        return CompletionResult(session=updated, message=format_completion_message(updated))

    def record_review_feedback(
        self,
        session_id: str,
        feedback: ReviewFeedback,
    ) -> ReviewIntakeResult:
        """Append a review feedback record and re-route to the right role.

        Allowed from any state except ``REJECTED``. If the session was
        ``COMPLETED``, the session re-opens to ``IN_PROGRESS`` so subsequent
        ``progress()`` / ``complete()`` / ``respond_to_review()`` calls work.
        """

        session = self._require_session(session_id)
        if session.state == WorkflowState.REJECTED:
            raise WorkflowError(
                f"session {session_id} is rejected; review feedback is not accepted"
            )

        routing = route_review_feedback(feedback)
        feedback_record = dict(review_feedback_to_payload(feedback))
        feedback_record["routing"] = {
            "primary_role": routing.primary_role,
            "supporting_roles": list(routing.supporting_roles),
            "reasons": list(routing.reasons),
            "reference_needed": routing.reference_needed,
            "reference_sources": list(routing.reference_sources),
            "reference_gaps": list(routing.reference_gaps),
        }

        new_state = (
            WorkflowState.IN_PROGRESS
            if session.state == WorkflowState.COMPLETED
            else session.state
        )
        new_cycle = session.review_cycle + 1
        new_feedbacks = tuple(session.review_feedbacks) + (feedback_record,)
        thread_id = feedback.target_thread_id or session.thread_id

        updated = replace(
            session,
            state=new_state,
            review_cycle=new_cycle,
            review_feedbacks=new_feedbacks,
            thread_id=thread_id,
        )
        updated = update_session(updated, now=self.now_fn())

        message = format_review_intake_message(
            feedback,
            routing,
            session_id=updated.session_id,
            review_cycle=new_cycle,
        )
        return ReviewIntakeResult(
            session=updated,
            feedback=feedback,
            routing=routing,
            message=message,
        )

    def respond_to_review(
        self,
        session_id: str,
        *,
        feedback_id: str,
        applied: Sequence[str],
        proposed: Sequence[str] = (),
        remaining: Sequence[str] = (),
        references_used: Sequence[Mapping[str, Any]] = (),
    ) -> ReviewReplyResult:
        session = self._require_session(session_id)
        if session.state == WorkflowState.REJECTED:
            raise WorkflowError(
                f"session {session_id} is rejected; review reply is not allowed"
            )

        target_record: Optional[Mapping[str, Any]] = None
        for record in session.review_feedbacks:
            if record.get("feedback_id") == feedback_id:
                target_record = record
                break
        if target_record is None:
            raise WorkflowError(
                f"feedback {feedback_id} not found in session {session_id}"
            )

        feedback = _feedback_from_record(target_record)
        routing = _routing_from_record(target_record, fallback=feedback)
        message = format_review_reply_message(
            feedback,
            routing,
            session_id=session.session_id,
            review_cycle=session.review_cycle,
            applied=applied,
            proposed=proposed,
            remaining=remaining,
            references_used=references_used,
        )
        note = (
            f"review cycle {session.review_cycle} 회신: "
            f"적용 {len(applied)}건, 제안 {len(proposed)}건, 남음 {len(remaining)}건"
        )
        notes = tuple(session.progress_notes) + (note,)
        used = tuple(dict(item) for item in session.references_used) + tuple(
            dict(item) for item in references_used
        )
        updated = replace(session, progress_notes=notes, references_used=used)
        updated = update_session(updated, now=self.now_fn())
        return ReviewReplyResult(session=updated, message=message)

    def get(self, session_id: str) -> Optional[WorkflowSession]:
        return load_session(session_id)

    def _require_session(self, session_id: str) -> WorkflowSession:
        session = load_session(session_id)
        if session is None:
            raise WorkflowError(f"session {session_id} not found")
        return session


def _feedback_from_record(record: Mapping[str, Any]) -> ReviewFeedback:
    payload = {key: value for key, value in record.items() if key != "routing"}
    return review_feedback_from_payload(payload)


def _routing_from_record(record: Mapping[str, Any], *, fallback: ReviewFeedback) -> ReviewRouting:
    routing_data = record.get("routing")
    if isinstance(routing_data, Mapping):
        return ReviewRouting(
            feedback_id=fallback.feedback_id,
            primary_role=str(routing_data.get("primary_role") or "tech-lead"),
            supporting_roles=tuple(
                str(item) for item in routing_data.get("supporting_roles") or ()
            ),
            reasons=tuple(str(item) for item in routing_data.get("reasons") or ()),
            reference_needed=bool(routing_data.get("reference_needed")),
            reference_sources=tuple(
                str(item) for item in routing_data.get("reference_sources") or ()
            ),
            reference_gaps=tuple(
                str(item) for item in routing_data.get("reference_gaps") or ()
            ),
        )
    return route_review_feedback(fallback)


def extract_urls(text: str) -> tuple[str, ...]:
    matches = _URL_PATTERN.findall(text or "")
    seen: dict[str, None] = {}
    for url in matches:
        cleaned = url.rstrip(".,);")
        if cleaned and cleaned not in seen:
            seen[cleaned] = None
    return tuple(seen.keys())


def _merge_user_links(prompt_links: Sequence[str], extra: Sequence[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for url in tuple(prompt_links) + tuple(extra):
        cleaned = url.strip().rstrip(".,);") if isinstance(url, str) else ""
        if cleaned and cleaned not in seen:
            seen[cleaned] = None
    return tuple(seen.keys())


# --- Message formatters ----------------------------------------------------


def format_intake_message(session: WorkflowSession, plan: DispatchPlan) -> str:
    lines: list[str] = [
        "**[engineering-agent] 새 작업 접수**",
        f"세션 ID: `{session.session_id}`",
        f"분류: {plan.task_type.value}",
        f"참여 후보: {', '.join(plan.role_sequence)}",
    ]
    executor = plan.executor()
    if executor:
        runner = executor.runner_id or "<no runner>"
        lines.append(f"실행 후보: {executor.role} ({runner}, score={executor.score})")
    advisors = plan.advisors()
    if advisors:
        advisor_text = ", ".join(
            f"{a.role}/{a.runner_id or '?'}" for a in advisors
        )
        lines.append(f"검토 후보: {advisor_text}")

    lines.append("")
    lines.append("**참고 레퍼런스 (제안)**")
    if session.references_user:
        lines.append("- 사용자 제공 (1순위):")
        for url in session.references_user:
            lines.append(f"  - {url}")
    if session.references_suggested:
        lines.append("- task_type 추천 카테고리:")
        for source in session.references_suggested:
            lines.append(f"  - {source}")
    if not session.references_user and not session.references_suggested:
        lines.append("- (이 task_type에는 시각 reference를 강제하지 않습니다)")

    lines.append("")
    if session.write_requested and session.write_blocked_reason:
        lines.append("**승인 필요**")
        lines.append(f"- {session.write_blocked_reason}")
        lines.append(
            f"- 진행하려면 `yule engineer approve --session {session.session_id}` 또는 Discord에서 ✅로 승인해주세요."
        )
    else:
        lines.append("승인 없이 진행 가능 (write 작업 없음 또는 사전 승인됨).")

    return "\n".join(lines)


def format_progress_message(session: WorkflowSession) -> str:
    lines: list[str] = [
        "**[engineering-agent] 진행 상황**",
        f"세션 ID: `{session.session_id}`",
        f"상태: {session.state.value}",
        f"실행 후보: {session.executor_role} ({session.executor_runner or '?'})",
    ]
    if session.progress_notes:
        lines.append("")
        lines.append("**최근 메모**")
        for idx, note in enumerate(session.progress_notes[-5:], start=1):
            lines.append(f"{idx}. {note}")
    return "\n".join(lines)


def format_completion_message(session: WorkflowSession) -> str:
    lines: list[str] = [
        "**[engineering-agent] 완료 보고**",
        f"세션 ID: `{session.session_id}`",
        f"분류: {session.task_type}",
        f"실행 후보: {session.executor_role} ({session.executor_runner or '?'})",
    ]
    if session.summary:
        lines.append("")
        lines.append("**요약**")
        lines.append(session.summary)

    if session.references_used:
        lines.append("")
        lines.append("**반영한 레퍼런스**")
        for item in session.references_used:
            lines.append(_format_used_reference(item))
    elif session.references_user or session.references_suggested:
        lines.append("")
        lines.append("**반영한 레퍼런스**")
        lines.append("- (없음 — 본 작업은 reference를 직접 인용하지 않았습니다)")

    if session.write_requested and not session.summary:
        lines.append("")
        lines.append("note: 요약이 비어 있습니다. 다음 작업 전 보강이 필요합니다.")
    return "\n".join(lines)


def _format_used_reference(item: Mapping[str, Any]) -> str:
    title = str(item.get("title") or "").strip() or "(제목 없음)"
    source = str(item.get("source") or "").strip()
    url = str(item.get("url") or "").strip()
    rationale = str(item.get("rationale") or item.get("takeaway") or "").strip()
    head = f"- **{title}**"
    if source:
        head += f" · {source}"
    if url:
        head += f" — {url}"
    if rationale:
        head += f"\n  ↪ {rationale}"
    return head
