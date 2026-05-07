"""Top-level research → deliberation → forum publication pipeline.

Wires the existing pieces together so a single Discord message can flow
through the full engineering-agent MVP:

1. ``collect_research_candidates_from_message`` (in
   ``discord/engineering_conversation.py``) classifies the message body,
   URLs, and attachments into a ``ResearchCollectionResult``.
2. When the result is sufficient, ``build_research_pack_from_candidates``
   materialises a ``ResearchPack``.
3. ``run_role_deliberation`` (deterministic fallback or runner-injected)
   produces a structured take per role in ``role_sequence``.
4. ``synthesize`` produces the tech-lead synthesis (합의안 / 해야 할 일 /
   더 조사할 것 / 사용자 결정 필요 / 승인 필요 여부).
5. ``RoleAssignment`` rows fall out of next_actions per role.
6. ``publish_research_loop_to_forum`` (async) posts the [Research] thread,
   role comments, and [Decision] synthesis comment via injected functions.

The ``run_research_loop`` entry point is **pure-Python and synchronous**:
no Discord API, no network. ``publish_research_loop_to_forum`` is async
but uses injected ``create_thread_fn`` / ``post_message_fn`` so tests stub
them. That keeps the loop itself testable without any I/O.

When the message has insufficient research, ``run_research_loop`` returns
an outcome with ``insufficient=True`` and a follow-up prompt — callers
should reply with ``outcome.follow_up_prompt`` and skip publication.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence, Tuple

from ...discord.engineering_conversation import (
    ResearchCandidate,
    ResearchCollectionResult,
    build_research_pack_from_candidates,
    collect_research_candidates_from_message,
    format_insufficient_research_prompt,
    suggest_role_research_assignments,
)
from ...discord.research_forum import (
    PREFIX_DECISION,
    PREFIX_REFERENCE,
    PREFIX_RESEARCH,
    ForumCommentOutcome,
    ForumPostOutcome,
    ResearchForumContext,
    chunk_for_discord_message,
    create_research_post,
    format_agent_comment,
    post_agent_comment,
)
from ..deliberation import (
    DeliberationContext,
    RoleTake,
    RunnerFn,
    TechLeadSynthesis,
    render_role_take,
    render_synthesis,
    run_role_deliberation,
    synthesize,
)
from .pack import ResearchPack
from ..workflow_state import WorkflowSession


# ---------------------------------------------------------------------------
# Outcome dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleAssignment:
    """One concrete task assigned to a role coming out of synthesis.

    ``role`` is the bare role name (``frontend-engineer``); ``actions`` is
    the role's next_actions list cleaned of empty entries; ``is_executor``
    flips True for the single role that holds the write gate.
    """

    role: str
    actions: Sequence[str]
    is_executor: bool = False


@dataclass(frozen=True)
class RoleLoopOutput:
    """Per-role artifacts the loop emits.

    ``take`` is the structured RoleTake for synthesis / debugging.
    ``rendered`` is what the bot would post in the Discord thread.
    ``comment_kwargs`` is the kwargs dict ready to hand to
    ``format_agent_comment`` / ``post_agent_comment`` so the forum
    publisher does not have to re-extract evidence/risks/next_actions.
    """

    role: str
    take: RoleTake
    rendered: str
    comment_kwargs: Mapping[str, Any]


@dataclass(frozen=True)
class ResearchLoopOutcome:
    """Bundle of everything one loop pass produced.

    When ``insufficient`` is True, callers should send
    ``follow_up_prompt`` back to the user and skip publication.
    """

    session: WorkflowSession
    collection: Any
    insufficient: bool
    follow_up_prompt: Optional[str]
    research_pack: Optional[ResearchPack]
    role_outputs: Sequence[RoleLoopOutput] = field(default_factory=tuple)
    synthesis: Optional[TechLeadSynthesis] = None
    synthesis_text: Optional[str] = None
    assignments: Sequence[RoleAssignment] = field(default_factory=tuple)
    role_research_gaps: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class ForumPublicationOutcome:
    """What the forum publisher returned for one loop outcome.

    ``thread`` is the create_research_post outcome (or None when skipped).
    ``role_comments`` is per-role comment outcomes keyed by role.
    ``kickoff_comment`` is set in ``member-bots`` mode when the gateway
    posts one open-call directive instead of impersonating every role.
    ``decision_comment`` is the tech-lead synthesis comment outcome.
    ``skipped_reason`` is filled when publication was skipped (e.g.
    insufficient research, no thread id from the create call).
    """

    thread: Optional[ForumPostOutcome]
    role_comments: Mapping[str, ForumCommentOutcome] = field(default_factory=dict)
    kickoff_comment: Optional[ForumCommentOutcome] = None
    decision_comment: Optional[ForumCommentOutcome] = None
    skipped_reason: Optional[str] = None

    @property
    def posted(self) -> bool:
        return bool(self.thread and self.thread.posted)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def run_research_loop(
    *,
    session: WorkflowSession,
    message_text: str,
    attachments: Sequence[Any] = (),
    posted_at: Optional[datetime] = None,
    role_sequence: Optional[Sequence[str]] = None,
    runner_fn: Optional[RunnerFn] = None,
    pack_title: Optional[str] = None,
    pack_tags: Sequence[str] = (),
    author_role: str = "tech-lead",
    research_pack: Optional[ResearchPack] = None,
    collection: Optional[Any] = None,
) -> ResearchLoopOutcome:
    """Run collection → deliberation → synthesis for one Discord message.

    The actual Discord posting is handled by
    :func:`publish_research_loop_to_forum`; this function never touches
    the network. *role_sequence* falls back to ``session.role_sequence``;
    when both are empty we default to the canonical engineering quintet.

    When *research_pack* is provided, collection is skipped and the loop
    deliberates directly over that pack. This is the production path for
    the autonomous collector: the conversation layer already gathered
    role-aware search results, so the forum/deliberation layer must not
    throw that context away and re-collect from the raw Discord message.
    """

    if research_pack is not None:
        return _run_pack_deliberation_loop(
            session=session,
            pack=research_pack,
            collection=collection,
            role_sequence=role_sequence,
            runner_fn=runner_fn,
        )

    collection = collect_research_candidates_from_message(
        message_text,
        attachments=attachments,
        author_role=author_role,
        posted_at=posted_at,
        task_type=session.task_type,
    )

    if collection.insufficient:
        return ResearchLoopOutcome(
            session=session,
            collection=collection,
            insufficient=True,
            follow_up_prompt=(
                collection.follow_up_prompt
                or format_insufficient_research_prompt(collection.insufficient_reason)
            ),
            research_pack=None,
            role_outputs=(),
            synthesis=None,
            synthesis_text=None,
            assignments=(),
            role_research_gaps=dict(collection.role_assignments or {}),
        )

    pack = _build_pack(
        session=session,
        collection=collection,
        title=pack_title,
        tags=pack_tags,
        posted_at=posted_at,
    )

    sequence = _resolve_role_sequence(session, role_sequence)
    role_outputs = _run_per_role_deliberation(
        session=session,
        role_sequence=sequence,
        pack=pack,
        runner_fn=runner_fn,
    )

    takes = tuple(output.take for output in role_outputs)
    synth = synthesize(session, takes, research_pack=pack)
    synth_text = render_synthesis(synth)
    assignments = _assignments_from_outputs(role_outputs, executor_role=session.executor_role)

    role_research_gaps = suggest_role_research_assignments(
        task_type=session.task_type,
        collected_source_types=tuple(c.source_type for c in collection.candidates),
    )

    return ResearchLoopOutcome(
        session=session,
        collection=collection,
        insufficient=False,
        follow_up_prompt=None,
        research_pack=pack,
        role_outputs=tuple(role_outputs),
        synthesis=synth,
        synthesis_text=synth_text,
        assignments=tuple(assignments),
        role_research_gaps=dict(role_research_gaps),
    )


CreateThreadFn = Callable[..., Awaitable[Any]]
PostMessageFn = Callable[..., Awaitable[Any]]


async def publish_research_loop_to_forum(
    outcome: ResearchLoopOutcome,
    *,
    forum_context: ResearchForumContext,
    create_thread_fn: CreateThreadFn,
    post_message_fn: PostMessageFn,
    posted_by: Optional[str] = None,
    thread_prefix: Optional[str] = None,
    collection_outcome: Optional[Any] = None,
    collection_role: Optional[str] = None,
    collection_next_steps: Sequence[str] = (),
    comment_mode: Optional[str] = None,
) -> ForumPublicationOutcome:
    """Post the loop outcome into the operations-research forum.

    Skipped when the outcome is ``insufficient`` or the research pack is
    missing. Posting failures (e.g. ``create_thread_fn`` raising) surface
    via ``ForumPostOutcome.error`` and ``ForumCommentOutcome.error`` —
    they do NOT raise, so the caller can show a fallback message.
    """

    if outcome.insufficient or outcome.research_pack is None:
        return ForumPublicationOutcome(
            thread=None,
            role_comments={},
            decision_comment=None,
            skipped_reason="insufficient research" if outcome.insufficient else "no research pack",
        )

    thread_outcome = await create_research_post(
        outcome.research_pack,
        forum_context=forum_context,
        create_thread_fn=create_thread_fn,
        posted_by=posted_by,
        prefix=thread_prefix or _default_thread_prefix(outcome.research_pack),
        collection_outcome=collection_outcome,
        collection_role=collection_role,
        collection_next_steps=collection_next_steps,
        post_message_fn=post_message_fn,
    )

    role_comments: dict[str, ForumCommentOutcome] = {}
    kickoff_comment: Optional[ForumCommentOutcome] = None
    decision_comment: Optional[ForumCommentOutcome] = None

    if thread_outcome.posted and thread_outcome.thread_id is not None:
        if _resolve_publication_comment_mode(comment_mode) == "member-bots":
            kickoff_comment = await _post_research_kickoff_comment(
                thread_id=thread_outcome.thread_id,
                session=outcome.session,
                post_message_fn=post_message_fn,
            )
            return ForumPublicationOutcome(
                thread=thread_outcome,
                role_comments={},
                kickoff_comment=kickoff_comment,
                decision_comment=None,
                skipped_reason=None,
            )

        for role_output in outcome.role_outputs:
            comment = await post_agent_comment(
                thread_id=thread_outcome.thread_id,
                role=role_output.role,
                post_message_fn=post_message_fn,
                **role_output.comment_kwargs,
            )
            role_comments[role_output.role] = comment

        if outcome.synthesis is not None and outcome.synthesis_text is not None:
            decision_comment = await _post_decision_comment(
                thread_id=thread_outcome.thread_id,
                synthesis=outcome.synthesis,
                synthesis_text=outcome.synthesis_text,
                post_message_fn=post_message_fn,
            )

    return ForumPublicationOutcome(
        thread=thread_outcome,
        role_comments=role_comments,
        kickoff_comment=kickoff_comment,
        decision_comment=decision_comment,
        skipped_reason=None,
    )


# ---------------------------------------------------------------------------
# Helpers (internal)
# ---------------------------------------------------------------------------


def _run_pack_deliberation_loop(
    *,
    session: WorkflowSession,
    pack: ResearchPack,
    collection: Optional[Any],
    role_sequence: Optional[Sequence[str]],
    runner_fn: Optional[RunnerFn],
) -> ResearchLoopOutcome:
    """Run deliberation over an already-collected ResearchPack."""

    sequence = _resolve_role_sequence(session, role_sequence)
    role_outputs = _run_per_role_deliberation(
        session=session,
        role_sequence=sequence,
        pack=pack,
        runner_fn=runner_fn,
    )
    takes = tuple(output.take for output in role_outputs)
    synth = synthesize(session, takes, research_pack=pack)
    synth_text = render_synthesis(synth)
    assignments = _assignments_from_outputs(role_outputs, executor_role=session.executor_role)
    role_research_gaps = suggest_role_research_assignments(
        task_type=session.task_type,
        collected_source_types=tuple(source.source_type for source in pack.sources),
    )

    return ResearchLoopOutcome(
        session=session,
        collection=collection,
        insufficient=False,
        follow_up_prompt=None,
        research_pack=pack,
        role_outputs=tuple(role_outputs),
        synthesis=synth,
        synthesis_text=synth_text,
        assignments=tuple(assignments),
        role_research_gaps=dict(role_research_gaps),
    )


async def _post_research_kickoff_comment(
    *,
    thread_id: int,
    session: WorkflowSession,
    post_message_fn: PostMessageFn,
) -> ForumCommentOutcome:
    try:
        from ...discord.engineering_team_runtime import research_open_call_directive
    except Exception as exc:  # noqa: BLE001
        return ForumCommentOutcome(
            posted=False,
            error=str(exc),
            body=None,
        )

    try:
        directive = research_open_call_directive(session)
    except Exception as exc:  # noqa: BLE001
        return ForumCommentOutcome(
            posted=False,
            error=str(exc),
            body=None,
        )

    body = (
        "자료 수집 seed를 올렸어요. 이제 각 멤버 봇이 자기 정책에 맞게 "
        "추가 조사하고, 필요한 take를 독립적으로 남깁니다.\n\n"
        f"{directive}"
    )
    # Directive can grow with role lists / tags so chunk before posting.
    # Each piece is ≤ DISCORD_MESSAGE_REPLY_LIMIT (1900) chars.
    for piece in chunk_for_discord_message(body) or (body,):
        try:
            await post_message_fn(thread_id=thread_id, content=piece)
        except Exception as exc:  # noqa: BLE001
            return ForumCommentOutcome(posted=False, error=str(exc), body=body)
    return ForumCommentOutcome(posted=True, body=body)


def _resolve_publication_comment_mode(value: Optional[str]) -> str:
    """Keep low-level publisher backwards-compatible unless caller opts in."""

    raw = (value or "gateway").strip().lower()
    if raw in {"member-bots", "gateway"}:
        return raw
    return "gateway"


_DEFAULT_ROLE_SEQUENCE: Tuple[str, ...] = (
    "engineering-agent/tech-lead",
    "engineering-agent/ai-engineer",
    "engineering-agent/product-designer",
    "engineering-agent/backend-engineer",
    "engineering-agent/frontend-engineer",
    "engineering-agent/qa-engineer",
)


def _resolve_role_sequence(
    session: WorkflowSession,
    override: Optional[Sequence[str]],
) -> Tuple[str, ...]:
    if override:
        return tuple(_normalize_role(r) for r in override if r)
    if session.role_sequence:
        return tuple(_normalize_role(r) for r in session.role_sequence if r)
    return _DEFAULT_ROLE_SEQUENCE


def _normalize_role(role: str) -> str:
    """Accept both ``tech-lead`` and ``engineering-agent/tech-lead``."""

    cleaned = (role or "").strip()
    if not cleaned:
        return cleaned
    if "/" in cleaned:
        return cleaned
    return f"engineering-agent/{cleaned}"


def _build_pack(
    *,
    session: WorkflowSession,
    collection: ResearchCollectionResult,
    title: Optional[str],
    tags: Sequence[str],
    posted_at: Optional[datetime],
) -> ResearchPack:
    pack_title = title or _default_pack_title(session, collection.candidates)
    return build_research_pack_from_candidates(
        title=pack_title,
        candidates=collection.candidates,
        channel_id=session.channel_id,
        thread_id=session.thread_id,
        message_id=None,
        posted_at=posted_at,
        tags=tags,
        extra={"session_id": session.session_id, "task_type": session.task_type},
    )


def _default_pack_title(
    session: WorkflowSession,
    candidates: Sequence[ResearchCandidate],
) -> str:
    prompt_first = (session.prompt or "").strip().splitlines()
    head = prompt_first[0].strip() if prompt_first else ""
    if head:
        if len(head) > 60:
            head = head[:57] + "..."
        return head
    if candidates:
        return candidates[0].title
    return "(untitled)"


def _run_per_role_deliberation(
    *,
    session: WorkflowSession,
    role_sequence: Sequence[str],
    pack: ResearchPack,
    runner_fn: Optional[RunnerFn],
) -> list[RoleLoopOutput]:
    outputs: list[RoleLoopOutput] = []
    previous: list[RoleTake] = []
    for role in role_sequence:
        ctx = DeliberationContext(
            session=session,
            role=role,
            research_pack=pack,
            previous_turns=tuple(previous),
        )
        take = run_role_deliberation(ctx, runner_fn=runner_fn)
        rendered = render_role_take(take)
        comment_kwargs = _comment_kwargs_for_take(role, take)
        outputs.append(
            RoleLoopOutput(
                role=role,
                take=take,
                rendered=rendered,
                comment_kwargs=comment_kwargs,
            )
        )
        previous.append(take)
    return outputs


def _comment_kwargs_for_take(role: str, take: RoleTake) -> Mapping[str, Any]:
    perspective = (getattr(take, "perspective", None) or "").strip()
    evidence = tuple(getattr(take, "evidence", ()) or ())
    risks = tuple(getattr(take, "risks", ()) or ())
    next_actions = tuple(getattr(take, "next_actions", ()) or ())

    risks_text = "\n".join(risks) if risks else ""
    confidence = "high" if evidence else "medium"
    confidence_reason = (
        f"근거 {len(evidence)}건 확보" if evidence else "근거 부족 — 추가 자료 필요"
    )

    return {
        "collected_materials": evidence,
        "interpretation": perspective or "(관점 미기재)",
        "risks": risks_text,
        "next_actions": next_actions,
        "confidence": confidence,
        "confidence_reason": confidence_reason,
    }


def _assignments_from_outputs(
    outputs: Sequence[RoleLoopOutput],
    *,
    executor_role: Optional[str],
) -> list[RoleAssignment]:
    assignments: list[RoleAssignment] = []
    short_executor = _short_role(executor_role) if executor_role else None
    for output in outputs:
        actions = tuple(
            a.strip()
            for a in (getattr(output.take, "next_actions", ()) or ())
            if a and a.strip()
        )
        if not actions:
            continue
        short = _short_role(output.role)
        assignments.append(
            RoleAssignment(
                role=short,
                actions=actions,
                is_executor=(short_executor is not None and short == short_executor),
            )
        )
    return assignments


def _short_role(role: str) -> str:
    if "/" in role:
        return role.split("/", 1)[1]
    return role


def _default_thread_prefix(pack: ResearchPack) -> str:
    """Pick [Reference] for visual-heavy packs, else [Research]."""

    has_visual = any(
        ((s.extra or {}).get("source_type") in {"image_reference", "design_reference"})
        or any(att.kind == "image" for att in s.attachments)
        for s in pack.sources
    )
    return PREFIX_REFERENCE if has_visual else PREFIX_RESEARCH


async def _post_decision_comment(
    *,
    thread_id: int,
    synthesis: TechLeadSynthesis,
    synthesis_text: str,
    post_message_fn: PostMessageFn,
) -> ForumCommentOutcome:
    """Post the tech-lead [Decision] comment into the thread.

    The body is a freeform multi-line post (not the role-comment template)
    because the synthesis already has its own structured shape. We prepend
    ``[Decision]`` so the policy's prefix vocabulary still applies.
    """

    body_lines = [f"{PREFIX_DECISION} 합의안 — {synthesis.consensus.strip()}", "", synthesis_text]
    body = "\n".join(body_lines).strip()
    # Synthesis text can be multi-paragraph and easily exceeds the
    # 2000-char ``content`` limit for thread messages. Chunk before
    # posting; the first chunk's id stands in for the comment id so
    # callers (StoredOutcome / persistence) keep a stable handle.
    pieces = chunk_for_discord_message(body) or (body,)
    first_message_id: Optional[int] = None
    for piece in pieces:
        try:
            result = await _maybe_await(post_message_fn(thread_id=thread_id, content=piece))
        except Exception as exc:  # noqa: BLE001 - surface to caller
            return ForumCommentOutcome(posted=False, error=str(exc), body=body)
        if first_message_id is None:
            first_message_id = _extract_id(result)
    return ForumCommentOutcome(posted=True, message_id=first_message_id, body=body)


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _extract_id(result: Any) -> Optional[int]:
    if result is None:
        return None
    if isinstance(result, Mapping):
        for key in ("id", "message_id"):
            value = result.get(key)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None
        return None
    for attr in ("id", "message_id"):
        value = getattr(result, attr, None)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


__all__ = [
    "ForumPublicationOutcome",
    "ResearchLoopOutcome",
    "RoleAssignment",
    "RoleLoopOutput",
    "publish_research_loop_to_forum",
    "run_research_loop",
]
