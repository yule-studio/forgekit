"""Obsidian note rendering / formatting (string-only, no IO).

Extracted from :mod:`agents.obsidian.export` along the **renderer** seam:
this module owns the *rendering* responsibility — turning a
:class:`ResearchPack` / work-report payload into Markdown body +
YAML frontmatter + short-title derivation — while ``export.py`` keeps the
*path / routing* responsibility (layout resolution, ``recommend_path``,
kind→folder mapping) plus the shared dataclasses and constants.

Dependency direction is one-way: ``export_render`` imports the path
helpers, dataclasses and stable constants from ``export``; ``export``
never imports back from here (no import-time cycle). External callers
that need the public renderers (``render_research_note`` /
``render_work_report_note``) import them from this module.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..deliberation import TechLeadSynthesis
from ..research.pack import ResearchAttachment, ResearchPack
from ..workflow_state import WorkflowSession

from .export import (
    CONTRACT_VERSION,
    LAYOUT_LEGACY_AGENT,
    TITLE_LIMIT,
    ObsidianNote,
    recommend_path,
    resolve_default_project,
    resolve_layout,
)


# ---------------------------------------------------------------------------
# Public renderers
# ---------------------------------------------------------------------------


def render_research_note(
    pack: ResearchPack,
    *,
    session: Optional[WorkflowSession] = None,
    synthesis: Optional[TechLeadSynthesis] = None,
    kind: Optional[str] = None,
    exported_at: Optional[datetime] = None,
    project: Optional[str] = None,
    layout: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> ObsidianNote:
    """Render a ResearchPack into an Obsidian-ready note.

    *kind* defaults based on whether a synthesis is provided
    (``decision`` if so, else ``research``). Pass ``"reference"``
    explicitly for pure UX/design reference notes.

    Path layout follows :data:`DEFAULT_LAYOUT` (yule-agent-vault) by
    default — notes land under ``10-projects/<project>/<kind>/``. The
    project resolution chain runs in this order:

    1. Explicit *project* kwarg (e.g. CLI ``--project``).
    2. ``session.extra["project"]`` / ``session.extra["project_name"]``.
    3. ``OBSIDIAN_DEFAULT_PROJECT`` env (via *env* or ``os.environ``).
    4. Hard-coded :data:`DEFAULT_PROJECT` (``yule-studio-agent``).

    Pass ``layout="legacy-agent"`` (or set
    ``OBSIDIAN_EXPORT_LAYOUT=legacy-agent``) to get the old
    ``Agents/Engineering/...`` flat tree — used only by callers that
    haven't migrated their vault yet.
    """

    chosen_kind = (kind or _infer_kind(synthesis)).lower()
    if chosen_kind in ("knowledge", "knowledge-note", "knowledge_note"):
        # Knowledge mode delegates to the richer template — semantic
        # title, role-by-role review, decisions/next-actions split — so
        # the existing research/decision/reference branches stay
        # byte-stable for vaults that haven't migrated. The M10a
        # canonical name ``knowledge-note`` shares the body renderer
        # but routes to ``20-knowledge/`` via :func:`recommend_path`.
        from .knowledge_writer import render_knowledge_note

        return render_knowledge_note(
            pack=pack,
            session=session,
            synthesis=synthesis,
            project=project,
            layout=layout,
            env=env,
            exported_at=exported_at,
            kind=chosen_kind,
        )
    layout_resolved = resolve_layout(layout, env=env)
    chosen_project = _resolve_project(
        project=project,
        session=session,
        layout=layout_resolved,
        env=env,
    )
    short_title = derive_short_title(pack, session=session)
    frontmatter = _frontmatter(
        pack=pack,
        session=session,
        synthesis=synthesis,
        kind=chosen_kind,
        exported_at=exported_at,
        short_title=short_title,
    )
    if chosen_project:
        frontmatter["project"] = chosen_project
    body_lines = _body(
        pack, synthesis=synthesis, session=session, short_title=short_title
    )
    content = _format_frontmatter(frontmatter) + "\n\n" + "\n\n".join(body_lines).strip() + "\n"
    path = recommend_path(
        title=short_title,
        kind=chosen_kind,
        created_at=pack.created_at,
        project=chosen_project,
        layout=layout_resolved,
        env=env,
    )
    return ObsidianNote(path=path, content=content, frontmatter=frontmatter)


def _resolve_project(
    *,
    project: Optional[str],
    session: Optional[WorkflowSession],
    layout: str,
    env: Optional[Mapping[str, str]],
) -> Optional[str]:
    """Walk the project resolution chain.

    Order: explicit kwarg → ``session.extra["project"|"project_name"]`` →
    ``OBSIDIAN_DEFAULT_PROJECT`` env → :data:`DEFAULT_PROJECT`. Returns
    ``None`` only in legacy-agent layout — there the legacy flat tree
    doesn't carry a project segment so we drop it from frontmatter as
    well.
    """

    if layout == LAYOUT_LEGACY_AGENT:
        # Legacy mode keeps frontmatter project-less unless caller set one
        # explicitly. session.extra/env are intentionally ignored so the
        # legacy notes stay byte-stable for vaults that haven't migrated.
        explicit = (project or "").strip()
        return explicit or None

    explicit = (project or "").strip()
    if explicit:
        return explicit
    from_session = _project_from_session(session)
    if from_session:
        return from_session
    return resolve_default_project(env=env)


def _project_from_session(session: Optional[WorkflowSession]) -> Optional[str]:
    """Best-effort project derivation from session metadata.

    Looks for an explicit ``project`` (or ``project_name``) key in
    ``session.extra``. Operators stash it there at intake time when a
    request is clearly tied to a known project. Returns ``None`` when
    nothing usable is found.
    """

    if session is None:
        return None
    extra = dict(getattr(session, "extra", None) or {})
    candidate = extra.get("project") or extra.get("project_name")
    if candidate:
        return str(candidate).strip() or None
    return None



# ---------------------------------------------------------------------------
# Frontmatter / body builders
# ---------------------------------------------------------------------------


def _frontmatter(
    *,
    pack: ResearchPack,
    session: Optional[WorkflowSession],
    synthesis: Optional[TechLeadSynthesis],
    kind: str,
    exported_at: Optional[datetime],
    short_title: Optional[str] = None,
) -> dict:
    title = (short_title or _clean_title(pack.title) or "(untitled)").strip() or "(untitled)"
    original_prompt = _resolve_original_prompt(pack=pack, session=session)
    fm: dict = {
        "title": title,
        "source": pack.primary_url or _first_source_url(pack),
        "roles": list(pack.author_roles),
        "status": _status_from(synthesis, session),
        "session_id": getattr(session, "session_id", None) if session else None,
        "created_at": _iso_or_none(pack.created_at),
        "kind": kind,
        "tags": _tags_for(pack, kind),
        "topic": title,
        "task_type": getattr(session, "task_type", None) if session else None,
        "sources": _source_descriptors(pack),
        "contract": CONTRACT_VERSION,
    }
    if original_prompt and original_prompt != title:
        fm["original_prompt"] = original_prompt
    if synthesis is not None:
        fm["approval_required"] = bool(synthesis.approval_required)
    if exported_at is not None:
        fm["exported_at"] = exported_at.replace(microsecond=0).isoformat()
    return fm


def _body(
    pack: ResearchPack,
    *,
    synthesis: Optional[TechLeadSynthesis],
    session: Optional[WorkflowSession],
    short_title: Optional[str] = None,
) -> list[str]:
    blocks: list[str] = []

    h1 = (short_title or _clean_title(pack.title) or "(untitled)").strip() or "(untitled)"
    blocks.append(f"# {h1}")

    original_prompt = _resolve_original_prompt(pack=pack, session=session)
    if original_prompt and original_prompt != h1:
        blocks.append("## 원문 요청\n" + original_prompt)

    if synthesis is not None:
        blocks.append("## 합의안\n" + synthesis.consensus)
        if synthesis.todos:
            blocks.append("## 해야 할 일\n" + _bullets(synthesis.todos))
        if synthesis.open_research:
            blocks.append("## 더 조사할 것\n" + _bullets(synthesis.open_research))
        if synthesis.user_decisions_needed:
            blocks.append(
                "## 사용자 결정 필요\n" + _bullets(synthesis.user_decisions_needed)
            )
        approval_line = (
            "yes" + (f" — {synthesis.approval_reason}" if synthesis.approval_reason else "")
            if synthesis.approval_required
            else "no"
        )
        blocks.append(f"## 승인 필요 여부\n{approval_line}")

    if pack.summary:
        blocks.append("## 요약\n" + pack.summary.strip())

    if pack.urls:
        blocks.append("## 자료 링크\n" + _bullets(pack.urls))

    if pack.attachments:
        blocks.append("## 첨부\n" + _bullets(_attachment_lines(pack.attachments)))

    if pack.sources:
        source_lines = []
        for source in pack.sources:
            bits = []
            if source.author_role:
                bits.append(f"**{source.author_role}**")
            if source.posted_at:
                bits.append(source.posted_at.isoformat())
            if source.source_url:
                bits.append(source.source_url)
            if source.title:
                bits.append(source.title)
            if bits:
                source_lines.append(" · ".join(bits))
        if source_lines:
            blocks.append("## 출처\n" + _bullets(source_lines))

    if session is not None:
        meta_lines = [f"- session_id: `{session.session_id}`"]
        if session.task_type:
            meta_lines.append(f"- task_type: `{session.task_type}`")
        if session.executor_role:
            meta_lines.append(f"- executor_role: `{session.executor_role}`")
        blocks.append("## 메타\n" + "\n".join(meta_lines))

    return blocks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _infer_kind(synthesis: Optional[TechLeadSynthesis]) -> str:
    return "decision" if synthesis is not None else "research"


def _status_from(
    synthesis: Optional[TechLeadSynthesis],
    session: Optional[WorkflowSession],
) -> str:
    if synthesis is not None and synthesis.approval_required:
        return "approval-pending"
    if synthesis is not None:
        return "decided"
    if session is None:
        return "captured"
    state = getattr(getattr(session, "state", None), "value", None)
    if state in (None, "intake"):
        return "captured"
    return state


def _tags_for(pack: ResearchPack, kind: str) -> list[str]:
    base_tag = kind.lower().rstrip("s")  # decision/research/reference
    seen: dict[str, None] = {base_tag: None}
    for tag in pack.tags:
        cleaned = (tag or "").strip()
        if cleaned and cleaned not in seen:
            seen[cleaned] = None
    return list(seen.keys())


def _first_source_url(pack: ResearchPack) -> Optional[str]:
    for source in pack.sources:
        if source.source_url:
            return source.source_url
    return None


def _source_descriptors(pack: ResearchPack) -> list[str]:
    """Return a flat string list usable as a YAML inline sequence.

    Combines URLs (primary + per-source) with attachment ids, deduped.
    Frontmatter consumers (indexers) only need a stable identifier per
    source — full provenance lives in the Markdown body.
    """

    seen: dict[str, None] = {}
    for url in pack.urls:
        if url and url not in seen:
            seen[url] = None
    for att in pack.attachments:
        ident = att.url or att.filename
        if ident and ident not in seen:
            seen[ident] = None
    return list(seen.keys())


def _iso_or_none(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    return str(value)


def _bullets(items: Iterable[str]) -> str:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned:
        return "- (없음)"
    return "\n".join(f"- {item}" for item in cleaned)


def _attachment_lines(attachments: Sequence[ResearchAttachment]) -> list[str]:
    out: list[str] = []
    for att in attachments:
        bits = [f"`{att.kind}`"]
        if att.filename:
            bits.append(att.filename)
        bits.append(f"<{att.url}>")
        if att.description:
            bits.append(f"— {att.description}")
        out.append(" ".join(bits))
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Filler / connector phrases that pad the start of a Korean prompt without
# adding signal. Removed before the deterministic summarizer kicks in.
_FILLER_PREFIXES = (
    "오늘은 ",
    "오늘 ",
    "내일은 ",
    "지금은 ",
    "이번에는 ",
    "이번엔 ",
    "다음 주제로 ",
    "다음으로 ",
    "이를 위해 ",
    "그래서 ",
    "그러니까 ",
    "한번 ",
    "잠깐 ",
)

_FILLER_SUFFIXES = (
    " 고민해보려 합니다.",
    " 고민해보려 합니다",
    " 고민해보고자 합니다.",
    " 고민해보고자 합니다",
    " 정리해보려 합니다.",
    " 정리해보려 합니다",
    " 검토해보려 합니다.",
    " 검토해보려 합니다",
    " 해보려 합니다.",
    " 해보려 합니다",
    " 합니다.",
    " 합니다",
)


_RESEARCH_PREFIX_RE = re.compile(
    r"^\s*\[(?:Research|Decision|Reference|Knowledge)\]\s*",
    re.IGNORECASE,
)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_URL_IN_TEXT_RE = re.compile(r"https?://\S+", re.IGNORECASE)
# Filler phrases the title generator must drop *before* truncation —
# they shorten the available budget without adding signal.
_TITLE_FILLER_PHRASES: tuple[str, ...] = (
    "자료 링크 모음",
    "자료 링크",
    "이대로",
)


def _clean_title(text: str) -> str:
    """Strip Discord/Obsidian markup that doesn't belong in a title.

    Removes ``[Research]`` / ``[Decision]`` / ``[Knowledge]`` style
    prefixes, markdown ``**bold**`` markers, raw URLs (which sometimes
    end up in auto-generated titles like ``자료 링크 https://...``),
    body filler phrases (``자료 링크``, ``이대로``), line breaks, and
    runs of whitespace. The output is a single-line plain string that
    downstream summarisation / truncation can work on without escape-
    sequence surprises.
    """

    if not text:
        return ""
    cleaned = _RESEARCH_PREFIX_RE.sub("", text)
    cleaned = _BOLD_RE.sub(r"\1", cleaned)
    cleaned = _URL_IN_TEXT_RE.sub(" ", cleaned)
    for phrase in _TITLE_FILLER_PHRASES:
        cleaned = cleaned.replace(phrase, " ")
    cleaned = cleaned.replace("\r", " ").replace("\n", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" -·…")


def _strip_fillers(text: str) -> str:
    """Drop common Korean filler prefixes/suffixes that pad a prompt."""

    out = text.strip()
    if not out:
        return ""
    changed = True
    while changed:
        changed = False
        for prefix in _FILLER_PREFIXES:
            if out.startswith(prefix):
                out = out[len(prefix):].lstrip()
                changed = True
                break
    for suffix in _FILLER_SUFFIXES:
        if out.endswith(suffix):
            out = out[: -len(suffix)].rstrip()
            break
    return out


def _summarize_for_title(text: str, *, max_chars: int = TITLE_LIMIT) -> str:
    """Deterministic short-title summariser.

    Strategy: clean → strip fillers → take the first sentence/clause →
    truncate at a word boundary under ``max_chars``. Korean and English
    both supported because we rely on simple punctuation/whitespace and
    don't tokenise on script.
    """

    cleaned = _clean_title(text)
    cleaned = _strip_fillers(cleaned)
    if not cleaned:
        return ""
    # Split on the first strong sentence-ending punctuation.
    for sep in (". ", "! ", "? ", "。", "·", " — ", " - ", "\u2014"):
        idx = cleaned.find(sep)
        if 0 < idx < max_chars * 3:
            cleaned = cleaned[:idx].strip(" ,.;:")
            break
    cleaned = _strip_fillers(cleaned)
    if len(cleaned) <= max_chars:
        return cleaned
    # Word-boundary trim — prefer not to cut a token in half. Korean
    # words are space-delimited at the eojeol boundary so this works
    # for both scripts.
    head = cleaned[:max_chars]
    pivot = head.rfind(" ")
    if pivot >= int(max_chars * 0.5):
        head = head[:pivot]
    return head.rstrip(" ,.;:") + "…"


def derive_short_title(
    pack: ResearchPack,
    *,
    session: Optional[WorkflowSession] = None,
    max_chars: int = TITLE_LIMIT,
) -> str:
    """Pick a short, readable title for the Obsidian note.

    Resolution order:
      1. ``session.extra["short_title"]`` / ``session.extra["research_title"]``
         / ``session.extra["routing_decision_title"]`` if any are set —
         operators pre-stash a curated title at intake time.
      2. ``pack.title`` after :func:`_clean_title` if it's already short
         and looks intentional (≤ ``max_chars`` and not a full sentence).
      3. :func:`_summarize_for_title` of (in order) ``pack.title``,
         ``pack.summary``, or ``session.prompt``.
      4. Literal fallback ``"engineering 작업"`` so the title is never
         empty — keeps filename slugify from collapsing to "untitled".
    """

    if session is not None:
        extra = dict(getattr(session, "extra", None) or {})
        for key in ("short_title", "research_title", "routing_decision_title"):
            candidate = extra.get(key)
            if isinstance(candidate, str):
                cleaned = _clean_title(candidate)
                if cleaned:
                    return cleaned[:max_chars]

    pack_title = _clean_title(getattr(pack, "title", "") or "")
    if pack_title and len(pack_title) <= max_chars and not _looks_like_full_prompt(pack_title):
        return pack_title

    for candidate in (
        getattr(pack, "title", "") or "",
        getattr(pack, "summary", "") or "",
        getattr(session, "prompt", None) if session else None,
    ):
        if not candidate:
            continue
        derived = _summarize_for_title(candidate, max_chars=max_chars)
        if derived:
            return derived

    return "engineering 작업"


def _resolve_original_prompt(
    *,
    pack: ResearchPack,
    session: Optional[WorkflowSession],
) -> Optional[str]:
    """Pick the operator-facing original prompt for the note body/frontmatter.

    Order: ``session.prompt`` (most authoritative — the actual Discord
    message), then ``pack.summary`` if it's longer than the title, then
    ``pack.title`` itself when it's clearly a full sentence (the legacy
    "title=full prompt" data we want to migrate). Returns ``None`` when
    there is nothing meaningful to preserve.
    """

    if session is not None:
        prompt = (getattr(session, "prompt", None) or "").strip()
        if prompt:
            return prompt
    summary = (getattr(pack, "summary", "") or "").strip()
    title = (getattr(pack, "title", "") or "").strip()
    if summary and summary != title and len(summary) > len(title):
        return summary
    if title and _looks_like_full_prompt(title):
        return title
    return None


def _looks_like_full_prompt(text: str) -> bool:
    """Heuristic: a sentence-shaped paragraph is probably the raw prompt."""

    # Multiple Korean sentence enders or hangul predicate endings = prompt.
    if any(marker in text for marker in (". ", "다. ", "다 ", "려 합니다", "고 싶")):
        return True
    if text.count(" ") > 8:  # very long, treat as full prompt
        return True
    return False


def _format_frontmatter(fm: dict) -> str:
    lines = ["---"]
    for key, value in fm.items():
        lines.append(_format_yaml_pair(key, value))
    lines.append("---")
    return "\n".join(lines)


def _format_yaml_pair(key: str, value) -> str:
    if value is None:
        return f"{key}: null"
    if isinstance(value, bool):
        return f"{key}: {'true' if value else 'false'}"
    if isinstance(value, (int, float)):
        return f"{key}: {value}"
    if isinstance(value, list):
        if not value:
            return f"{key}: []"
        items = ", ".join(_yaml_scalar(v) for v in value)
        return f"{key}: [{items}]"
    return f"{key}: {_yaml_scalar(value)}"


def _yaml_scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    needs_quote = (
        ": " in text
        or text.startswith(" ")
        or text.endswith(" ")
        or text.startswith("-")
        or text.startswith("'")
        or text.startswith('"')
        or text.startswith("[")
        or text.startswith("{")
        or text.startswith("#")
        or "\n" in text
        or "," in text
    )
    if needs_quote:
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


# ---------------------------------------------------------------------------
# Phase 5 — Work-report renderer
# ---------------------------------------------------------------------------


def render_work_report_note(
    *,
    report: Any,
    session_id: Optional[str] = None,
    project: Optional[str] = None,
    layout: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    exported_at: Optional[datetime] = None,
) -> ObsidianNote:
    """Render a :class:`agents.reports.work_report.WorkReport` (or its dict
    snapshot persisted in ``session.extra['work_report']``) as an
    Obsidian note under ``10-projects/<project>/reports/``.

    Accepts either the dataclass instance or the JSON-friendly dict
    that :func:`engineering_channel_router._work_report_to_dict`
    persists. Body sections mirror the Discord preview but stay
    longer-form because Obsidian doesn't have a 2000-char ceiling.
    """

    payload = _coerce_work_report_payload(report)
    if not payload:
        raise ValueError("render_work_report_note: report payload is empty")
    title = str(payload.get("title") or "untitled work report").strip()
    canonical = str(payload.get("canonical_prompt") or "").strip()
    short_title = title or canonical[:80] or "work report"
    when = exported_at or datetime.utcnow()
    chosen_kind = "work-report"
    layout_resolved = resolve_layout(layout, env=env)
    project_resolved = (project or "").strip() or resolve_default_project(env=env)

    frontmatter: dict = {
        "title": _frontmatter_title(short_title),
        "kind": chosen_kind,
        "exported_at": when.replace(microsecond=0).isoformat(timespec="seconds"),
        "session_id": payload.get("session_id") or session_id or "unknown",
        "participants": list(payload.get("participants") or []),
        "requires_code_change": bool(payload.get("requires_code_change")),
        "research_stop_reason": payload.get("research_stop_reason"),
        "reference_count": int(payload.get("reference_count") or 0),
    }
    if project_resolved:
        frontmatter["project"] = project_resolved
    executor = payload.get("recommended_executor_role")
    if executor:
        frontmatter["recommended_executor_role"] = executor

    body_lines: list[str] = [f"# {short_title}"]
    if canonical:
        body_lines.append("\n## 원문\n\n> " + canonical.replace("\n", "\n> "))
    summary = str(payload.get("executive_summary") or "").strip()
    if summary:
        body_lines.append("\n## 요약\n\n" + summary)
    recommendation = str(payload.get("tech_lead_recommendation") or "").strip()
    if recommendation and recommendation != summary:
        body_lines.append("\n## Tech-lead 권고\n\n" + recommendation)

    participants = list(payload.get("participants") or [])
    if participants:
        body_lines.append("\n## 참가자\n\n" + ", ".join(str(r) for r in participants))
    role_decisions = dict(payload.get("role_decisions") or {})
    if role_decisions:
        decision_lines = [
            f"- `{role}` — {reason}" for role, reason in role_decisions.items()
        ]
        body_lines.append("\n## 역할별 참여 사유\n\n" + "\n".join(decision_lines))

    risks = list(payload.get("risks") or [])
    if risks:
        body_lines.append(
            "\n## 위험 / open research\n\n" + "\n".join(f"- {r}" for r in risks)
        )
    next_steps = list(payload.get("proposed_next_steps") or [])
    if next_steps:
        body_lines.append(
            "\n## 다음 액션\n\n" + "\n".join(f"- {s}" for s in next_steps)
        )

    if payload.get("requires_code_change"):
        cta_lines = ["\n## 코드 수정 권한"]
        if executor:
            cta_lines.append(f"\nrecommended executor: `{executor}`")
        approval = str(payload.get("approval_request") or "").strip()
        if approval:
            cta_lines.append("\n" + approval)
        body_lines.append("\n".join(cta_lines))

    stop_reason = payload.get("research_stop_reason")
    under_covered = list(payload.get("under_covered_roles") or [])
    if stop_reason or under_covered:
        meta_lines = ["\n## research 메타"]
        if stop_reason:
            meta_lines.append(f"- stop_reason: {stop_reason}")
        if under_covered:
            meta_lines.append(
                "- under_covered_roles: " + ", ".join(str(r) for r in under_covered)
            )
        body_lines.append("\n".join(meta_lines))

    content = (
        _format_frontmatter(frontmatter)
        + "\n\n"
        + "\n".join(line for line in body_lines if line is not None).strip()
        + "\n"
    )
    path = recommend_path(
        title=short_title,
        kind=chosen_kind,
        created_at=when,
        project=project_resolved,
        layout=layout_resolved,
        env=env,
    )
    return ObsidianNote(path=path, content=content, frontmatter=frontmatter)


def _coerce_work_report_payload(report: Any) -> dict:
    """Accept either a ``WorkReport`` dataclass or its dict snapshot."""

    if report is None:
        return {}
    if isinstance(report, Mapping):
        return dict(report)
    payload: dict = {}
    for key in (
        "session_id",
        "title",
        "canonical_prompt",
        "executive_summary",
        "research_summary",
        "tech_lead_recommendation",
        "role_decisions",
        "risks",
        "proposed_next_steps",
        "requires_code_change",
        "recommended_executor_role",
        "approval_request",
        "participants",
        "reference_count",
        "research_stop_reason",
        "under_covered_roles",
    ):
        if hasattr(report, key):
            payload[key] = getattr(report, key)
    return payload


def _frontmatter_title(title: str) -> str:
    """Trim *title* to the obsidian-export TITLE_LIMIT for frontmatter."""

    if not title:
        return "work report"
    if len(title) <= TITLE_LIMIT:
        return title
    return title[: TITLE_LIMIT - 1].rstrip() + "…"
