"""Renderers for A-M10b autonomous-execution note kinds.

Five kinds land here, all *non-approval* (L1/L2 in the autonomy
ladder defined in :mod:`agents.lifecycle.autonomy_policy`):

  * ``research-log`` — captured at the moment a research order
    fires, includes original prompt + thread snapshot + collected
    links + role summaries + (optional) tech-lead synthesis.
    *Not* a canonical knowledge note — that promotion is L3.
  * ``agent-ops`` — daily / per-session rollup of
    :class:`AgentOpsEntry` audit rows; quotes the autonomy
    decisions the agent took without human approval.
  * ``failure-postmortem`` / ``self-improvement-proposal`` /
    ``blog-draft`` — share a generic body renderer; the producer
    authored the markdown and just needs vault routing +
    frontmatter stamping.

Each renderer returns :class:`ObsidianNote`. Empty bodies are
rejected via :class:`ObsidianRenderError` so a hollow file never
lands in the vault.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from .export import ObsidianNote, recommend_path
from ..job_queue.obsidian_writer_worker import ObsidianRenderError


__all__ = (
    "ObsidianRenderError",
    "render_agent_ops_note",
    "render_research_log_note",
    "render_simple_body_note",
)


# ---------------------------------------------------------------------------
# Frontmatter helper
# ---------------------------------------------------------------------------


def _frontmatter(
    *,
    note_kind: str,
    title: str,
    request: Any,
    metadata: Mapping[str, Any],
    extras: Optional[Mapping[str, Any]] = None,
) -> dict:
    fm: dict[str, Any] = {
        "title": title,
        "kind": note_kind,
        "session_id": getattr(request, "session_id", "") or "",
        "autonomy_level": metadata.get("autonomy_level"),
        "created_at": _utc_now_iso(),
    }
    topic_key = metadata.get("topic_key")
    if topic_key:
        fm["topic_key"] = topic_key
    source_url = metadata.get("source_thread_url")
    if source_url:
        fm["source_thread_url"] = source_url
    source_title = metadata.get("source_thread_title")
    if source_title:
        fm["source_thread_title"] = source_title
    requested_by = metadata.get("requested_by")
    if requested_by:
        fm["requested_by"] = requested_by
    for key, value in dict(extras or {}).items():
        if value is not None and key not in fm:
            fm[key] = value
    return fm


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# research-log
# ---------------------------------------------------------------------------


def render_research_log_note(
    *,
    request: Any,
    metadata: Mapping[str, Any],
) -> ObsidianNote:
    """Render the research-log markdown.

    Required payload keys in *metadata* (any one of these is enough
    to clear the empty-body guard):

      * ``thread_snapshot`` — :meth:`ThreadSnapshot.to_payload` dict
      * ``synthesis_text`` — tech-lead synthesis string
      * ``research_pack`` — ResearchPack-shaped dict with title /
        summary / urls / sources

    Optional keys: ``original_prompt``, ``role_summaries``,
    ``selected_roles``, ``links``.
    """

    title = (
        request.title or metadata.get("canonical_title") or "research-log"
    ).strip() or "research-log"
    prompt = metadata.get("original_prompt") or metadata.get("prompt") or ""
    snapshot_payload = metadata.get("thread_snapshot")
    synthesis_text = metadata.get("synthesis_text") or ""
    pack = metadata.get("research_pack") or {}

    pack_has_body = bool(
        pack
        and (pack.get("urls") or pack.get("summary") or pack.get("title"))
    )
    snapshot_has_body = isinstance(snapshot_payload, Mapping) and (
        snapshot_payload.get("messages")
        or snapshot_payload.get("extracted_links")
        or snapshot_payload.get("role_summaries")
    )
    synthesis_has_body = bool(str(synthesis_text or "").strip())
    prompt_has_body = bool(str(prompt or "").strip())
    if not (
        pack_has_body
        or snapshot_has_body
        or synthesis_has_body
        or prompt_has_body
    ):
        raise ObsidianRenderError(
            "research-log note has no body to write — "
            "thread_snapshot/synthesis/research_pack/prompt 모두 비어 있어 "
            "vault 저장을 거부합니다 (failed_retryable: hydration 부족)"
        )

    sections: list[str] = [f"# {title}", ""]
    if prompt_has_body:
        sections.append("## 원문 요청")
        sections.append("")
        sections.append(str(prompt).strip())
        sections.append("")

    selected_roles = metadata.get("selected_roles") or []
    if selected_roles:
        sections.append("## 참여 역할")
        sections.append("")
        for role in selected_roles:
            sections.append(f"- {role}")
        sections.append("")

    if isinstance(pack, Mapping) and pack.get("summary"):
        sections.append("## 리서치 요약")
        sections.append("")
        sections.append(str(pack["summary"]).strip())
        sections.append("")

    if synthesis_has_body:
        sections.append("## tech-lead 합의")
        sections.append("")
        sections.append(str(synthesis_text).strip())
        sections.append("")

    if isinstance(snapshot_payload, Mapping) and snapshot_has_body:
        from ..lifecycle.thread_snapshot import (
            ThreadSnapshot,
            render_thread_snapshot_block,
        )

        snap = ThreadSnapshot.from_payload(snapshot_payload)
        snap_block = render_thread_snapshot_block(snap)
        if snap_block.strip():
            sections.append(snap_block)
            sections.append("")

    if isinstance(pack, Mapping) and pack.get("urls"):
        urls = [
            str(u) for u in (pack.get("urls") or []) if isinstance(u, str)
        ]
        if urls:
            sections.append("## 추가 자료")
            sections.append("")
            for url in urls:
                sections.append(f"- {url}")
            sections.append("")

    sections.append("## 자동 기록 안내")
    sections.append("")
    sections.append(
        "이 문서는 사용자 명시 오더에 대해 자동으로 기록된 research-log 입니다. "
        "canonical knowledge / decision-record 로 승격하려면 `#승인-대기` 의 "
        "승인 카드를 통해 별도 진행하세요."
    )
    sections.append("")

    body = "\n".join(sections).rstrip() + "\n"
    path = recommend_path(
        title=title,
        kind="research-log",
        project=request.project,
        layout=request.layout,
    )
    fm = _frontmatter(
        note_kind="research-log",
        title=title,
        request=request,
        metadata=metadata,
        extras={
            "selected_roles": list(selected_roles) if selected_roles else None,
        },
    )
    return ObsidianNote(path=path, content=body, frontmatter=fm)


# ---------------------------------------------------------------------------
# agent-ops
# ---------------------------------------------------------------------------


def render_agent_ops_note(
    *,
    request: Any,
    metadata: Mapping[str, Any],
) -> ObsidianNote:
    """Render the agent-ops daily / per-session rollup.

    ``metadata['audit_entries']`` is a list of
    :meth:`AgentOpsEntry.to_payload` dicts. Empty list → render
    error.
    """

    raw_entries = metadata.get("audit_entries") or []
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ObsidianRenderError(
            "agent-ops note has no audit entries to write — "
            "metadata['audit_entries'] 가 비어 있어 vault 저장을 거부합니다"
        )

    from ..lifecycle.agent_ops_log import (
        AgentOpsEntry,
        render_agent_ops_log_markdown,
    )

    entries: list[AgentOpsEntry] = []
    for raw in raw_entries:
        entry = AgentOpsEntry.from_payload(raw)
        if entry is not None:
            entries.append(entry)
    if not entries:
        raise ObsidianRenderError(
            "agent-ops note: audit_entries payload was malformed; "
            "nothing to render"
        )

    title = (
        request.title or metadata.get("canonical_title") or "agent-ops 로그"
    ).strip() or "agent-ops 로그"
    body = render_agent_ops_log_markdown(entries, title=title)
    path = recommend_path(
        title=title,
        kind="agent-ops",
        project=request.project,
        layout=request.layout,
    )
    fm = _frontmatter(
        note_kind="agent-ops",
        title=title,
        request=request,
        metadata=metadata,
        extras={"entry_count": len(entries)},
    )
    return ObsidianNote(path=path, content=body, frontmatter=fm)


# ---------------------------------------------------------------------------
# Generic simple-body renderer (postmortem / proposal / blog-draft)
# ---------------------------------------------------------------------------


def render_simple_body_note(
    *,
    request: Any,
    metadata: Mapping[str, Any],
) -> ObsidianNote:
    """Render postmortem / proposal / blog-draft kinds.

    The producer authored the markdown body and stuffed it into
    ``metadata['body']``; this renderer only adds vault routing,
    frontmatter, and a simple H1 if the body doesn't start with one.
    """

    body_raw = metadata.get("body")
    body = str(body_raw or "").strip()
    if not body:
        raise ObsidianRenderError(
            f"{request.note_kind} note has no body to write — "
            "metadata['body'] 가 비어 있어 vault 저장을 거부합니다"
        )

    title = (
        request.title or metadata.get("canonical_title") or request.note_kind
    ).strip() or request.note_kind
    if not body.lstrip().startswith("#"):
        body = f"# {title}\n\n{body}"
    if not body.endswith("\n"):
        body += "\n"

    path = recommend_path(
        title=title,
        kind=request.note_kind,
        project=request.project,
        layout=request.layout,
    )
    fm = _frontmatter(
        note_kind=request.note_kind,
        title=title,
        request=request,
        metadata=metadata,
    )
    return ObsidianNote(path=path, content=body, frontmatter=fm)
