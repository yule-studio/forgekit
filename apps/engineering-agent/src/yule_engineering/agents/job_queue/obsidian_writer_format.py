"""obsidian_write default render dispatcher — formatting split.

Extracted verbatim from :mod:`obsidian_writer_worker` as a pure
behaviour-preserving refactor (split axis: worker loop / persistence /
formatting). This module owns the **formatting** responsibility: the
default render dispatcher that turns an :class:`ObsidianWriteRequest`
into a renderable note for the configured ``write_fn``.

Producers for ``meeting`` / ``work-report`` still inject their own
``render_fn``; this dispatcher covers ``research`` / ``decision`` /
``knowledge`` plus the A-M10b autonomous-execution kinds.

Import direction is one-way: this module imports the request type,
the note-kind constants, and :class:`ObsidianRenderError` from
:mod:`obsidian_writer_worker`. The worker re-exports
:func:`default_render_fn` after importing it here, so every existing
``from .obsidian_writer_worker import default_render_fn`` call site
keeps working with no import-time cycle.
"""

from __future__ import annotations

from typing import Any, Mapping

from .obsidian_writer_worker import (
    NOTE_KIND_AGENT_OPS,
    NOTE_KIND_BLOG_DRAFT,
    NOTE_KIND_DECISION,
    NOTE_KIND_DECISION_RECORD,
    NOTE_KIND_FAILURE_POSTMORTEM,
    NOTE_KIND_KNOWLEDGE,
    NOTE_KIND_KNOWLEDGE_NOTE,
    NOTE_KIND_RESEARCH,
    NOTE_KIND_RESEARCH_LOG,
    NOTE_KIND_SELF_IMPROVEMENT_PROPOSAL,
    NOTE_KIND_TASK_LOG,
    ObsidianRenderError,
    ObsidianWriteRequest,
)


# ---------------------------------------------------------------------------
# Default render dispatcher (research / decision only — others must
# inject their own render_fn at this stage). Producers for
# ``meeting`` / ``knowledge`` / ``work-report`` either pass a custom
# render_fn or wait for M5b-2 to wire those kinds.
# ---------------------------------------------------------------------------


_DEFAULT_RENDER_KINDS: frozenset[str] = frozenset(
    {
        NOTE_KIND_RESEARCH,
        NOTE_KIND_DECISION,
        # A-M7.5e: knowledge kind delegates to
        # ``render_knowledge_note`` (already wired inside
        # ``render_research_note`` since the knowledge_writer split).
        # Approval guard above (``request.requires_approval`` /
        # ``has_full_approval``) still runs first so an unauthorised
        # knowledge write never reaches this renderer.
        NOTE_KIND_KNOWLEDGE,
        # A-M10a — canonical Knowledge Ops names. ``knowledge-note``
        # shares the knowledge body renderer but routes to
        # ``20-knowledge/`` via the path resolver; ``decision-record``
        # falls into the research/decision branch with kind="decision-
        # record" and lands in ``30-decisions/``.
        NOTE_KIND_KNOWLEDGE_NOTE,
        NOTE_KIND_DECISION_RECORD,
        # A-M10b — autonomous-execution kinds. No approval guard
        # because they map to L1/L2 in the autonomy ladder. The
        # producer is responsible for stuffing the relevant payload
        # (snapshot / audit list / postmortem body / proposal body
        # / blog draft body) into ``request.metadata`` so the
        # renderer has something to write.
        NOTE_KIND_RESEARCH_LOG,
        NOTE_KIND_AGENT_OPS,
        NOTE_KIND_FAILURE_POSTMORTEM,
        NOTE_KIND_SELF_IMPROVEMENT_PROPOSAL,
        NOTE_KIND_BLOG_DRAFT,
        # P1-B — coding executor progress lands here. Producer pre-renders
        # the body so the default render just wraps it.
        NOTE_KIND_TASK_LOG,
    }
)


_M10B_AUTONOMOUS_KINDS: frozenset[str] = frozenset(
    {
        NOTE_KIND_RESEARCH_LOG,
        NOTE_KIND_AGENT_OPS,
        NOTE_KIND_FAILURE_POSTMORTEM,
        NOTE_KIND_SELF_IMPROVEMENT_PROPOSAL,
        NOTE_KIND_BLOG_DRAFT,
        NOTE_KIND_TASK_LOG,
    }
)


def _render_autonomous_note(request: ObsidianWriteRequest) -> Any:
    """Render an A-M10b L1/L2 autonomous note.

    These kinds run without an approval triple and without depending
    on a still-open ``WorkflowSession`` row. The producer (M10c
    triggers) stuffs the relevant payload into ``request.metadata``
    and the renderer composes the markdown + path. Empty-body
    requests are rejected so a hollow vault file never lands.

    Per kind the producer must populate at minimum:

      * ``research-log`` — ``thread_snapshot`` payload
        (:meth:`ThreadSnapshot.to_payload`) and/or
        ``research_pack`` summary; ``original_prompt`` optional.
      * ``agent-ops`` — ``audit_entries`` list of
        :class:`AgentOpsEntry` payloads.
      * ``failure-postmortem`` / ``self-improvement-proposal`` /
        ``blog-draft`` — free-form ``body`` markdown.
    """

    metadata = dict(request.metadata or {})
    if request.note_kind == NOTE_KIND_RESEARCH_LOG:
        from ..obsidian.research_log_writer import render_research_log_note

        return render_research_log_note(request=request, metadata=metadata)
    if request.note_kind == NOTE_KIND_AGENT_OPS:
        from ..obsidian.research_log_writer import render_agent_ops_note

        return render_agent_ops_note(request=request, metadata=metadata)
    # postmortem / proposal / blog-draft share a generic markdown
    # composer — the producer has already authored the body.
    from ..obsidian.research_log_writer import render_simple_body_note

    return render_simple_body_note(request=request, metadata=metadata)


def default_render_fn(request: ObsidianWriteRequest) -> Any:
    """Default render for ``research`` / ``decision`` / ``knowledge``.

    Approval guard stays in :meth:`ObsidianWriterWorker.process_job`;
    this helper is only reached for knowledge requests after an
    approval triple (``approval_id`` / ``approved_by`` /
    ``approved_at``) is present.

    Pack handling differs by kind (A-M7.5f):

      * ``research`` / ``decision`` — ``session.extra['research_pack']``
        is **required**. Both kinds quote sources / findings directly
        and a missing pack means the rendered note has no body
        worth writing.
      * ``knowledge`` — pack is **optional**. The forum-handoff
        producer enqueues knowledge writes for sessions whose pack
        was never collected (operator just wants to capture the
        thread's consensus). When the pack is missing we call
        :func:`agents.obsidian.knowledge_writer.render_knowledge_note`
        directly with whatever context the request + session carry
        (title, prompt, source thread metadata) so the note still
        lands in the vault with audit-grade frontmatter.

    Raises :class:`ObsidianRenderError` for unsupported kinds
    (``meeting`` / ``work-report``) so a producer that forgets to
    inject ``render_fn`` fails loudly instead of writing the wrong
    content.
    """

    if request.note_kind not in _DEFAULT_RENDER_KINDS:
        raise ObsidianRenderError(
            f"default_render_fn does not support note_kind={request.note_kind!r}; "
            f"supported: {sorted(_DEFAULT_RENDER_KINDS)} — pass a custom render_fn"
        )
    # A-M10b — autonomous-execution kinds short-circuit the
    # research_pack / session lookup path. Their payload lives
    # entirely in ``request.metadata`` so the renderer can run
    # without a session row (handy for daily agent-ops rollups
    # whose session may already be closed).
    if request.note_kind in _M10B_AUTONOMOUS_KINDS:
        return _render_autonomous_note(request)

    # Lazy import to keep this module light when only the queue
    # primitives are needed (the obsidian export chain pulls a fair
    # amount of code in).
    from ..obsidian.export_render import render_research_note
    from ..research.pack_render import pack_from_dict
    from ..workflow_state import load_session

    session = load_session(request.session_id)
    if session is None:
        raise ObsidianRenderError(
            f"session {request.session_id!r} not found; default render needs it"
        )

    raw_pack = (session.extra or {}).get("research_pack")
    pack = (
        pack_from_dict(dict(raw_pack))
        if isinstance(raw_pack, Mapping) and raw_pack
        else None
    )

    if request.note_kind in (NOTE_KIND_KNOWLEDGE, NOTE_KIND_KNOWLEDGE_NOTE):
        # A-M7.5f no-pack fallback + A-M7.6 thread-snapshot
        # hydration. The forum handoff producer stuffs a
        # ``thread_snapshot`` payload into request.metadata so the
        # renderer can quote the actual operator discussion / role
        # summaries / extracted links instead of writing a hollow
        # stub. Empty-note guard: if pack is None AND the snapshot
        # carries nothing AND no synthesis is recorded on the
        # session, refuse to write — better failed_retryable than
        # an empty vault file.
        from ..lifecycle.thread_snapshot import (
            ThreadSnapshot,
            render_thread_snapshot_block,
        )
        from ..obsidian.knowledge_writer import render_knowledge_note

        metadata = dict(request.metadata or {})
        snapshot = ThreadSnapshot.from_payload(
            metadata.get("thread_snapshot")
        )

        # M10b — top-level metadata.extracted_links is a producer-
        # promoted view of the snapshot's link bucket. If the
        # snapshot itself is missing them (older payload, or a
        # non-forum producer that only stamps the top-level field),
        # merge them so the renderer still quotes every URL the
        # operator collected.
        meta_links_raw = metadata.get("extracted_links") or ()
        if isinstance(meta_links_raw, (list, tuple)):
            meta_links = tuple(
                str(u) for u in meta_links_raw if isinstance(u, str) and u
            )
            if meta_links and not snapshot.extracted_links:
                snapshot = ThreadSnapshot(
                    messages=snapshot.messages,
                    extracted_links=meta_links,
                    role_summaries=snapshot.role_summaries,
                    captured_at=snapshot.captured_at,
                )

        synthesis_text = (
            (session.extra or {}).get("research_synthesis_text")
            if isinstance(getattr(session, "extra", None), Mapping)
            else None
        )

        # Empty-note guard — forbid hollow vault file.
        if (
            pack is None
            and snapshot.is_empty
            and not (synthesis_text and str(synthesis_text).strip())
        ):
            raise ObsidianRenderError(
                "knowledge note has no body to write — pack/snapshot/"
                "synthesis 모두 비어 있어 vault 저장을 거부합니다 "
                "(failed_retryable: hydration 부족)"
            )

        rendered = render_knowledge_note(
            pack=pack,
            session=session,
            original_prompt=getattr(session, "prompt", None),
            title=request.title or None,
            project=request.project,
            layout=request.layout,
            kind=request.note_kind,
        )

        # Splice the snapshot block into the rendered note so the
        # vault file carries the operator's reasoning trail. The
        # renderer returns ObsidianNote(path, content, frontmatter);
        # we append a hydration block and return a new dataclass
        # (frozen — must construct).
        snapshot_block = render_thread_snapshot_block(snapshot)
        thread_url = metadata.get("source_thread_url")
        thread_title = metadata.get("source_thread_title")
        topic_key = metadata.get("topic_key")
        approval_job_id = metadata.get("approval_job_id")

        header_lines: list[str] = []
        if thread_url:
            header_lines.append(f"- 운영-리서치 thread: {thread_url}")
        if thread_title:
            header_lines.append(f"- thread 제목: {thread_title}")
        if topic_key:
            header_lines.append(f"- topic_key: `{topic_key}`")
        if approval_job_id:
            header_lines.append(f"- approval_job_id: `{approval_job_id}`")
        header_block = (
            "## 출처 / 추적 ID\n" + "\n".join(header_lines)
            if header_lines
            else ""
        )

        appended = "\n\n".join(
            block for block in (rendered.content, header_block, snapshot_block) if block
        )
        # Stamp hydration into frontmatter for downstream search.
        new_frontmatter = dict(rendered.frontmatter)
        if topic_key:
            new_frontmatter.setdefault("topic_key", topic_key)
        if thread_url:
            new_frontmatter.setdefault("source_thread_url", thread_url)
        if approval_job_id:
            new_frontmatter.setdefault("approval_job_id", approval_job_id)
        # M10b — hydrated revision marker. The producer bumps
        # ``ledger_revision`` when the operator opts into "다시 저장 /
        # 개정본". Stamping the revision (and the prior revision the
        # new note supersedes) lets a vault scan find earlier hollow
        # copies as superseded candidates without auto-deleting them.
        ledger_revision_raw = metadata.get("ledger_revision")
        try:
            ledger_revision = (
                int(ledger_revision_raw)
                if ledger_revision_raw is not None
                else None
            )
        except (TypeError, ValueError):
            ledger_revision = None
        if ledger_revision is not None and ledger_revision >= 1:
            new_frontmatter.setdefault("ledger_revision", ledger_revision)
            if ledger_revision > 1:
                new_frontmatter.setdefault(
                    "supersedes_revision", ledger_revision - 1
                )

        from ..obsidian.export import ObsidianNote

        return ObsidianNote(
            path=rendered.path,
            content=appended,
            frontmatter=new_frontmatter,
        )

    # research / decision — pack is mandatory, same as before.
    if pack is None:
        raise ObsidianRenderError(
            "default render needs session.extra['research_pack']"
        )
    return render_research_note(
        pack=pack,
        session=session,
        kind=request.note_kind,
        project=request.project,
        layout=request.layout,
    )


__all__ = (
    "default_render_fn",
)
