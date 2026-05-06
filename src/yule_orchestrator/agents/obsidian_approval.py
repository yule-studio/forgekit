"""Discord-driven Obsidian save flow: preview → approval → write.

Phase D plumbing for the engineering gateway. The CLI (``yule obsidian sync``)
still owns the headless path; this module adds an in-channel flow so the
operator can ask "Obsidian에 정리해줘" and approve a preview without leaving
Discord.

Responsibilities:

- :func:`is_obsidian_save_request` — detect the user's save intent (the
  runtime classifier already promotes these to ``execute_existing_step``;
  we keep the lexicon here so the router can branch independently).
- :func:`is_obsidian_approval` — detect ``저장 승인`` / ``이대로 저장`` /
  ``승인``. Approval phrases must NOT be promoted to a brand-new task.
- :func:`build_save_proposal` — load the session's persisted research_pack
  (and optional synthesis), render the :class:`ObsidianNote`, and return a
  :class:`ObsidianSaveProposal` envelope with the human-readable preview
  message, vault-relative target path, and serialised payload to stash on
  ``session.extra``.
- :func:`store_pending_proposal` / :func:`get_pending_proposal` /
  :func:`clear_pending_proposal` — proposal memory at session scope. The
  router writes the channel/thread/user identifiers into the payload so a
  subsequent approval message can be matched without reading the full
  recall pipeline.
- :func:`execute_pending_proposal` — re-render the note and call the
  injected writer (default: :func:`obsidian_writer.write_note`) to land
  the file in the vault. Updates ``session.extra["obsidian"]`` with the
  ``last_write_path`` / ``last_write_status`` event.

The module is intentionally pure-Python with all IO seams injected so the
flow can be exercised in unit tests without a real vault, real Discord, or
real cache.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from .deliberation import TechLeadSynthesis, synthesis_from_dict
from .knowledge_writer import render_knowledge_note
from .obsidian_export import ObsidianNote, render_research_note
from .obsidian_writer import (
    ENV_VAULT_PATH,
    ObsidianWriteError,
    ObsidianWriteResult,
    resolve_vault_root,
    write_note,
)
from .research_pack import ResearchPack, pack_from_dict
from .workflow_state import WorkflowSession, update_session


# ---------------------------------------------------------------------------
# Phrase banks
# ---------------------------------------------------------------------------


# Save-intent phrases. ``classify_intent_deterministic`` already promotes
# these to ``execute_existing_step``; we keep the lexicon here so the
# router preflight can decide *which* execute-step branch to take.
_OBSIDIAN_SAVE_PHRASES: tuple[str, ...] = (
    "obsidian에 정리",
    "obsidian 에 정리",
    "obsidian에 저장",
    "obsidian 에 저장",
    "obsidian 노트 정리",
    "obsidian 동기화",
    "옵시디언에 정리",
    "옵시디언에 저장",
    "옵시디언 정리",
    "옵시디언 동기화",
    "이 세션 기준으로 저장",
    "토의 기록 obsidian에 남",
    "토의 기록 옵시디언에 남",
    "vault에 저장",
    "vault 에 저장",
    "save to obsidian",
    "save to vault",
)


# Approval phrases. The router must check the *trimmed* text only — they
# are short and distinct so a substring match would over-fire on long
# free-form prompts (e.g. "이 작업도 같이 승인해줘" is not approving the
# Obsidian write). We accept them as either the entire normalized text or
# as a whitespace-bounded standalone phrase.
_APPROVAL_STANDALONE: frozenset[str] = frozenset(
    {
        "저장 승인",
        "저장승인",
        "저장 해줘",
        "저장해줘",
        "이대로 저장",
        "이대로 저장해",
        "이대로 저장해줘",
        "이대로저장",
        "이대로 보내줘",
        "vault 저장 승인",
        "obsidian 저장 승인",
        "옵시디언 저장 승인",
        "save approved",
        "approve save",
        "approve and save",
        "go ahead and save",
    }
)


# Bare "승인" / "approve" tokens — accepted only when the message is exactly
# that single word (so "이거 승인 부탁해요" does NOT fire). The router
# additionally requires a pending proposal before acting on a bare token,
# preventing context-free approvals from being interpreted as Obsidian
# writes.
_BARE_APPROVAL_TOKENS: frozenset[str] = frozenset(
    {"승인", "확인", "approve", "approved", "go", "ok"}
)


_NORMALIZE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _NORMALIZE_RE.sub(" ", (text or "").lower()).strip()


def is_obsidian_save_request(text: str) -> bool:
    """Heuristic save-intent detector.

    Returns True when *text* mentions Obsidian (or 옵시디언 / vault) plus a
    "정리/저장/남겨" verb. Used by the router to disambiguate the two
    branches of ``INTENT_EXECUTE_EXISTING_STEP`` (save vs other future
    execute steps).
    """

    normalized = _normalize(text)
    if not normalized:
        return False
    return any(phrase in normalized for phrase in _OBSIDIAN_SAVE_PHRASES)


def is_obsidian_approval(text: str, *, has_pending_proposal: bool = False) -> bool:
    """Heuristic approval detector.

    Phrases like ``저장 승인`` / ``이대로 저장`` are accepted as exact-trim
    matches. Bare ``승인`` / ``approve`` is only accepted when
    *has_pending_proposal* is True — otherwise the router would happily
    treat a generic "approved" reply as an Obsidian write.
    """

    normalized = _normalize(text)
    if not normalized:
        return False
    if normalized in _APPROVAL_STANDALONE:
        return True
    for phrase in _APPROVAL_STANDALONE:
        # Allow phrases embedded inside a slightly larger sentence — e.g.
        # "네, 저장 승인합니다" — but only when the standalone phrase is at
        # least 4 chars so we don't trigger on "ok"/"go" tokens here.
        if len(phrase) >= 4 and phrase in normalized and len(normalized) <= len(phrase) + 12:
            return True
    if has_pending_proposal and normalized in _BARE_APPROVAL_TOKENS:
        return True
    return False


# ---------------------------------------------------------------------------
# Proposal data
# ---------------------------------------------------------------------------


PROPOSAL_KEY = "obsidian"
PROPOSAL_PENDING_KEY = "pending_proposal"
PROPOSAL_HISTORY_KEY = "events"


@dataclass(frozen=True)
class ObsidianSaveProposal:
    """The preview the gateway proposes to the user.

    ``preview_message`` is the Discord-facing text — title, vault-relative
    path, summary, included sections, and the explicit "answer 저장 승인"
    instruction. ``payload`` is the JSON-friendly dict the router stores on
    ``session.extra`` so a later approval can re-render the same note
    without going back through the conversation layer.
    """

    session_id: str
    title: str
    vault_relative_path: str
    summary: str
    sections: tuple[str, ...]
    preview_message: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class ObsidianApprovalOutcome:
    """Result of executing a pending proposal.

    ``success=True`` means the writer landed the file. ``vault_relative_path``
    is the post-collision path — possibly with ``_2`` / ``_3`` appended.
    ``message`` is the Discord-facing line.
    """

    success: bool
    message: str
    vault_relative_path: Optional[str] = None
    target_path: Optional[Path] = None
    suffix_applied: bool = False
    original_target_path: Optional[Path] = None


# ---------------------------------------------------------------------------
# Preview builder
# ---------------------------------------------------------------------------


def build_save_proposal(
    session: WorkflowSession,
    *,
    actor_user_id: Optional[int] = None,
    project: Optional[str] = None,
    layout: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    now: Optional[datetime] = None,
) -> ObsidianSaveProposal:
    """Render an :class:`ObsidianNote` from *session* and wrap it as a proposal.

    Raises :class:`ObsidianApprovalError` when the session has no persisted
    research pack (``session.extra["research_pack"]``) — without one we
    have no body to render. The router catches this and replies with a
    plain-language explanation.

    Channel/thread/user identifiers are stashed in the proposal payload so
    a follow-up approval message can match the proposal even when the
    runtime preflight cannot anchor it via thread alone (e.g. when the
    user replied in the parent channel).
    """

    pack_payload = (session.extra or {}).get("research_pack")
    if not pack_payload:
        raise ObsidianApprovalError(
            f"세션 `{session.session_id}` 에 저장할 research_pack 이 없어요. "
            "토의/자료 정리가 끝난 뒤 다시 요청해 주세요."
        )

    try:
        pack = pack_from_dict(pack_payload)
    except Exception as exc:  # noqa: BLE001 — surface as friendly error
        raise ObsidianApprovalError(
            f"세션 `{session.session_id}` 의 research_pack 을 읽지 못했어요: {exc}"
        ) from exc

    synthesis: Optional[TechLeadSynthesis] = None
    synthesis_payload = (session.extra or {}).get("research_synthesis")
    if synthesis_payload:
        try:
            synthesis = synthesis_from_dict(synthesis_payload)
        except Exception:  # noqa: BLE001 — degrade gracefully
            synthesis = None

    note = _render_note_for_session(
        pack=pack,
        session=session,
        synthesis=synthesis,
        project=project,
        layout=layout,
        env=env,
    )

    sections = _section_titles(note)
    title = str(note.frontmatter.get("title") or note.path.filename)
    summary = (pack.summary or "").strip() or "(요약 미포함)"
    preview_message = _format_preview_message(
        title=title,
        vault_relative_path=note.path.full,
        summary=summary,
        sections=sections,
        session_id=session.session_id,
    )

    payload = {
        "session_id": session.session_id,
        "title": title,
        "vault_relative_path": note.path.full,
        "summary": summary,
        "sections": list(sections),
        "channel_id": session.channel_id,
        "thread_id": session.thread_id,
        "user_id": session.user_id,
        "actor_user_id": actor_user_id,
        "project": project,
        "layout": layout,
        "created_at": (now or datetime.utcnow()).isoformat(),
    }

    return ObsidianSaveProposal(
        session_id=session.session_id,
        title=title,
        vault_relative_path=note.path.full,
        summary=summary,
        sections=tuple(sections),
        preview_message=preview_message,
        payload=payload,
    )


def _render_note_for_session(
    *,
    pack: ResearchPack,
    session: WorkflowSession,
    synthesis: Optional[TechLeadSynthesis],
    project: Optional[str],
    layout: Optional[str],
    env: Optional[Mapping[str, str]],
) -> ObsidianNote:
    """Render the saved note via the KnowledgeNote layer.

    The KnowledgeNote template is the human-readable Phase C document
    (목적 / 원문 / 결론 / 자료 / 역할별 검토 / Tech Lead 종합 / 결정 /
    다음 액션 / 관련 세션). When it can't be rendered (older sessions,
    missing imports), we fall back to the legacy ``render_research_note``
    so the operator still gets a valid note rather than a hard failure.
    """

    try:
        return render_knowledge_note(
            pack=pack,
            session=session,
            synthesis=synthesis,
            project=project,
            layout=layout,
            env=env,
        )
    except Exception:  # noqa: BLE001 — fall back to research-only rendering
        return render_research_note(
            pack,
            session=session,
            synthesis=synthesis,
            project=project,
            layout=layout,
            env=env,
        )


def _section_titles(note: ObsidianNote) -> tuple[str, ...]:
    """Pull `## ...` headings out of *note.content* in document order."""

    titles: list[str] = []
    for line in note.content.splitlines():
        if line.startswith("## "):
            titles.append(line[3:].strip())
    return tuple(titles)


def _format_preview_message(
    *,
    title: str,
    vault_relative_path: str,
    summary: str,
    sections: Sequence[str],
    session_id: str,
) -> str:
    """Render the Discord-facing preview block."""

    section_lines = "\n".join(f"- {title}" for title in sections) if sections else "- (없음)"
    summary_block = summary
    if len(summary_block) > 600:
        summary_block = summary_block[:600].rstrip() + "…"
    return (
        "**[engineering-agent] Obsidian 저장 미리보기**\n"
        f"세션: `{session_id}`\n"
        f"제목: {title}\n"
        f"저장 경로: `{vault_relative_path}`\n\n"
        "요약:\n"
        f"{summary_block}\n\n"
        "포함 섹션:\n"
        f"{section_lines}\n\n"
        "저장하려면 `저장 승인` 이라고 답해 주세요. "
        "수정이 필요하면 변경 요청만 답해 주시면 다시 미리보기를 만들어 드릴게요."
    )


# ---------------------------------------------------------------------------
# Pending proposal storage on session.extra
# ---------------------------------------------------------------------------


def store_pending_proposal(
    session: WorkflowSession,
    proposal: ObsidianSaveProposal,
    *,
    now: Optional[datetime] = None,
) -> WorkflowSession:
    """Persist *proposal* into ``session.extra["obsidian"]["pending_proposal"]``.

    Returns the updated session. Idempotent: storing twice replaces the
    pending proposal with the latest preview.
    """

    extra = dict(session.extra or {})
    obs = dict(extra.get(PROPOSAL_KEY) or {})
    obs[PROPOSAL_PENDING_KEY] = dict(proposal.payload)
    obs["pending_note_title"] = proposal.title
    obs["pending_path"] = proposal.vault_relative_path
    extra[PROPOSAL_KEY] = obs
    updated = replace(session, extra=extra)
    return update_session(updated, now=now or datetime.now().astimezone())


def get_pending_proposal(session: WorkflowSession) -> Optional[Mapping[str, Any]]:
    """Return the pending proposal payload, or ``None`` when there isn't one."""

    obs = (session.extra or {}).get(PROPOSAL_KEY) or {}
    pending = obs.get(PROPOSAL_PENDING_KEY)
    if isinstance(pending, Mapping):
        return pending
    return None


def clear_pending_proposal(
    session: WorkflowSession, *, now: Optional[datetime] = None
) -> WorkflowSession:
    """Drop the pending proposal from ``session.extra``.

    Called immediately after a successful write (or when the operator
    explicitly cancels). Failing writes keep the proposal so the operator
    can retry without re-rendering.
    """

    extra = dict(session.extra or {})
    obs = dict(extra.get(PROPOSAL_KEY) or {})
    obs.pop(PROPOSAL_PENDING_KEY, None)
    obs.pop("pending_note_title", None)
    obs.pop("pending_path", None)
    extra[PROPOSAL_KEY] = obs
    updated = replace(session, extra=extra)
    return update_session(updated, now=now or datetime.now().astimezone())


def record_write_event(
    session: WorkflowSession,
    *,
    status: str,
    vault_relative_path: Optional[str] = None,
    error: Optional[str] = None,
    now: Optional[datetime] = None,
) -> WorkflowSession:
    """Append a status event to ``session.extra["obsidian"]["events"]``.

    Also pins the most recent ``last_write_path`` / ``last_write_status``
    keys so status diagnostic questions can answer "Obsidian 저장 어떻게
    됐어?" without scanning the events list.
    """

    extra = dict(session.extra or {})
    obs = dict(extra.get(PROPOSAL_KEY) or {})
    events = list(obs.get(PROPOSAL_HISTORY_KEY) or [])
    occurred = (now or datetime.now().astimezone()).isoformat()
    events.append(
        {
            "status": status,
            "vault_relative_path": vault_relative_path,
            "error": error,
            "occurred_at": occurred,
        }
    )
    obs[PROPOSAL_HISTORY_KEY] = events
    obs["last_write_status"] = status
    obs["last_write_at"] = occurred
    if vault_relative_path:
        obs["last_write_path"] = vault_relative_path
    if error:
        obs["last_write_error"] = error
    extra[PROPOSAL_KEY] = obs
    updated = replace(session, extra=extra)
    return update_session(updated, now=now or datetime.now().astimezone())


# ---------------------------------------------------------------------------
# Approval execution
# ---------------------------------------------------------------------------


WriterFn = Callable[..., ObsidianWriteResult]


class ObsidianApprovalError(RuntimeError):
    """Raised when a preview can't be rendered or executed safely.

    The router wraps these into a friendly chat message so a missing
    research pack / missing vault path / writer failure never produces a
    bare traceback in Discord.
    """


def execute_pending_proposal(
    session: WorkflowSession,
    *,
    vault_path_override: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    writer_fn: Optional[WriterFn] = None,
    project: Optional[str] = None,
    layout: Optional[str] = None,
    now: Optional[datetime] = None,
) -> tuple[WorkflowSession, ObsidianApprovalOutcome]:
    """Execute the pending proposal stored on *session*.

    Re-renders the note from the persisted research_pack/synthesis (the
    preview payload is for human review only — we never trust it as the
    source of truth for body content). Calls *writer_fn* (defaults to
    :func:`obsidian_writer.write_note`) with the resolved vault root.

    Returns ``(updated_session, outcome)``. The session reflects the
    cleared pending proposal and an appended ``events`` row. On vault /
    writer failure the proposal is preserved so the operator can retry
    after fixing the issue.
    """

    pending = get_pending_proposal(session)
    if not pending:
        raise ObsidianApprovalError(
            "지금은 대기 중인 Obsidian 저장 제안이 없어요. "
            "먼저 `Obsidian에 정리해줘` 처럼 저장 미리보기를 요청해 주세요."
        )

    try:
        vault_root = resolve_vault_root(env=env, override=vault_path_override)
    except ObsidianWriteError as exc:
        message = (
            f"⚠️ Obsidian vault 경로를 사용할 수 없어요: {exc}\n"
            f"`{ENV_VAULT_PATH}` 환경 변수를 확인하고 다시 `저장 승인` 해 주세요."
        )
        updated = record_write_event(
            session,
            status="vault_unavailable",
            error=str(exc),
            now=now,
        )
        return updated, ObsidianApprovalOutcome(success=False, message=message)

    pack_payload = (session.extra or {}).get("research_pack")
    if not pack_payload:
        message = (
            "⚠️ 세션에 research_pack 이 더 이상 보이지 않아 저장을 진행할 수 없어요. "
            "다시 `Obsidian에 정리해줘` 로 미리보기부터 만들어 주세요."
        )
        updated = record_write_event(
            session,
            status="missing_pack",
            error="research_pack disappeared from session.extra",
            now=now,
        )
        return updated, ObsidianApprovalOutcome(success=False, message=message)

    try:
        pack = pack_from_dict(pack_payload)
    except Exception as exc:  # noqa: BLE001
        message = (
            f"⚠️ 저장 직전 research_pack 파싱이 실패했어요: {exc}. "
            "vault 경로 / 세션 상태를 확인하고 다시 시도해 주세요."
        )
        updated = record_write_event(
            session,
            status="pack_parse_error",
            error=str(exc),
            now=now,
        )
        return updated, ObsidianApprovalOutcome(success=False, message=message)

    synthesis: Optional[TechLeadSynthesis] = None
    synthesis_payload = (session.extra or {}).get("research_synthesis")
    if synthesis_payload:
        try:
            synthesis = synthesis_from_dict(synthesis_payload)
        except Exception:  # noqa: BLE001
            synthesis = None

    project_resolved = project or pending.get("project") or None
    layout_resolved = layout or pending.get("layout") or None

    note = _render_note_for_session(
        pack=pack,
        session=session,
        synthesis=synthesis,
        project=project_resolved,
        layout=layout_resolved,
        env=env,
    )

    write_callable: WriterFn = writer_fn or write_note
    try:
        # Default writer signature: write_note(note, vault_root, *, overwrite, dry_run).
        result = write_callable(note, vault_root, overwrite=False, dry_run=False)
    except ObsidianWriteError as exc:
        message = (
            f"⚠️ Obsidian 쓰기 실패: {exc}. "
            "vault 권한이나 경로를 확인하고 다시 `저장 승인` 해 주세요."
        )
        updated = record_write_event(
            session,
            status="write_failed",
            error=str(exc),
            now=now,
        )
        return updated, ObsidianApprovalOutcome(success=False, message=message)
    except Exception as exc:  # noqa: BLE001 — wrap arbitrary writer exceptions
        message = (
            f"⚠️ Obsidian 쓰기 중 예상치 못한 오류: {exc}. 잠시 뒤 다시 시도해 주세요."
        )
        updated = record_write_event(
            session,
            status="write_failed",
            error=str(exc),
            now=now,
        )
        return updated, ObsidianApprovalOutcome(success=False, message=message)

    target_path = result.target_path
    try:
        relative = target_path.relative_to(vault_root)
        relative_str = str(relative)
    except (ValueError, AttributeError):
        relative_str = str(target_path)

    cleared = clear_pending_proposal(session, now=now)
    updated = record_write_event(
        cleared,
        status="written",
        vault_relative_path=relative_str,
        now=now,
    )

    suffix_note = ""
    if result.suffix_applied and result.original_target_path is not None:
        suffix_note = (
            f"\n기존 파일과 충돌해 자동으로 `{Path(relative_str).name}` 로 저장했어요 "
            f"(원래 경로: `{result.original_target_path.name}`)."
        )

    message = (
        f"Obsidian 저장 완료 ✅\n"
        f"vault relative path: `{relative_str}`"
        f"{suffix_note}"
    )

    return updated, ObsidianApprovalOutcome(
        success=True,
        message=message,
        vault_relative_path=relative_str,
        target_path=target_path,
        suffix_applied=result.suffix_applied,
        original_target_path=result.original_target_path,
    )


__all__ = (
    "ObsidianApprovalError",
    "ObsidianApprovalOutcome",
    "ObsidianSaveProposal",
    "PROPOSAL_KEY",
    "PROPOSAL_PENDING_KEY",
    "build_save_proposal",
    "clear_pending_proposal",
    "execute_pending_proposal",
    "get_pending_proposal",
    "is_obsidian_approval",
    "is_obsidian_save_request",
    "record_write_event",
    "store_pending_proposal",
)
