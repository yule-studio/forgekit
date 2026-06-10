"""KnowledgeNote — semantic Obsidian-bound document for human re-reading.

Where :func:`obsidian_export.render_research_note` serialises a raw
:class:`ResearchPack` (sources, attachments, links) for archival, the
knowledge writer composes a *story* of one work session: original ask,
collected evidence, role-by-role critique, tech-lead synthesis, decisions
made, and the next actions. The output is a Markdown document a person
opening the vault months later can use to reconstruct what happened.

Key contracts:

- ``build_knowledge_note(...)`` returns a :class:`KnowledgeNote` with a
  stable section list — render order matches the contract spec
  (목적 / 원문 요청 / 결론 / 자료 / 역할별 검토 / Tech Lead 종합 /
  결정 / 다음 액션 / 관련 세션). Empty sections still render with
  ``- (없음)`` so a reader can tell missing-by-design from absent.
- Title resolution is **deterministic and LLM-free**: explicit
  session/thread topic → original-prompt noun phrase → synthesis
  consensus → pack title → ``<task_type> 작업 정리``. URL fragments and
  filler phrases (``자료 링크``, ``오늘은``, …) are stripped before any
  truncation so a short, semantic title survives.
- Vault path follows the same layout policy as ``render_research_note``
  (``10-projects/<project>/<kind>/`` under yule-agent-vault) — the
  knowledge note registers as ``kind="knowledge"`` so
  ``recommend_path`` routes it under a project-specific
  ``knowledge/`` subfolder.

This module never writes files. The downstream ``obsidian_writer``
takes the rendered :class:`ObsidianNote` and persists it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from ..deliberation import (
    RoleTake,
    TechLeadOpening,
    TechLeadSynthesis,
    render_role_take,
)
from ..research.pack import ResearchAttachment, ResearchPack, ResearchSource
from ..workflow_state import WorkflowSession


KNOWLEDGE_KIND = "knowledge"
KNOWLEDGE_CONTRACT_VERSION = "knowledge-note/v0"

# Soft cap for the H1/title — long enough to keep semantic verbs but
# short enough that filename slugs stay under the 100-char Obsidian/git
# basename limit.
KNOWLEDGE_TITLE_LIMIT = 60

KNOWLEDGE_SUBDIR = "knowledge"

# Section ordering is part of the contract — reorder here, not in the
# caller, so frontmatter consumers always see the same key order in the
# rendered document.
SECTION_PURPOSE = "작업 목적"
SECTION_ORIGINAL_PROMPT = "원문 요청"
SECTION_CONCLUSION = "현재 결론"
SECTION_SOURCES = "수집 자료"
SECTION_ROLE_REVIEW = "역할별 검토"
SECTION_TECH_LEAD = "Tech Lead 종합"
SECTION_DECISIONS = "결정 / 제안"
SECTION_NEXT_ACTIONS = "다음 액션"
SECTION_RELATED_SESSION = "관련 세션"


# ---------------------------------------------------------------------------
# Title scrubbing helpers
# ---------------------------------------------------------------------------


# Filler phrases the prompt parser must drop *before* truncation — they
# shorten the available budget without adding signal. Keep ordered: the
# longest patterns first so a substring strip doesn't half-match a longer
# phrase.
_FILLER_PHRASES: tuple[str, ...] = (
    "자료 링크 모음",
    "자료 링크",
    "오늘은",
    "오늘",
    "내일은",
    "내일",
    "지금은",
    "지금",
    "이번에는",
    "이번엔",
    "다음 주제로",
    "다음으로",
    "이를 위해",
    "그래서",
    "그러니까",
    "이대로",
    "먼저",
    "한번",
    "잠깐",
)

_SUFFIX_FILLER: tuple[str, ...] = (
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
)

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_LABEL_PREFIX_RE = re.compile(
    r"^\s*\[(?:Research|Decision|Reference|Knowledge)\]\s*",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")


def scrub_title_text(text: str) -> str:
    """Strip URLs, ``[Research]``-style prefixes, bold markers, and filler.

    Returns a single-line, whitespace-collapsed string. Empty input or a
    string that becomes empty after scrubbing returns ``""`` so callers
    can fall back to the next candidate in the resolution chain.
    """

    if not text:
        return ""
    cleaned = _LABEL_PREFIX_RE.sub("", text)
    cleaned = _BOLD_RE.sub(r"\1", cleaned)
    cleaned = _URL_RE.sub(" ", cleaned)
    cleaned = cleaned.replace("\r", " ").replace("\n", " ")
    for suffix in _SUFFIX_FILLER:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
            break
    for phrase in _FILLER_PHRASES:
        cleaned = cleaned.replace(phrase, " ")
    cleaned = _WHITESPACE_RE.sub(" ", cleaned)
    return cleaned.strip(" -·…,.;:")


def _truncate_to_phrase(text: str, *, max_chars: int) -> str:
    """Cut *text* on the earliest sentence boundary that fits *max_chars*."""

    cleaned = scrub_title_text(text)
    if not cleaned:
        return ""
    for sep in (". ", "! ", "? ", "。", "·", " — ", " - ", "다. ", "다 "):
        idx = cleaned.find(sep)
        if 0 < idx < max_chars * 4:
            cleaned = cleaned[:idx]
            break
    cleaned = scrub_title_text(cleaned)
    if not cleaned:
        return ""
    if len(cleaned) <= max_chars:
        return cleaned
    head = cleaned[:max_chars]
    pivot = head.rfind(" ")
    if pivot >= max_chars // 2:
        head = head[:pivot]
    return head.rstrip(" ,.;:") + "…"


def _looks_like_full_prompt(text: str) -> bool:
    """Heuristic: a sentence-shaped paragraph is probably the raw prompt."""

    if any(marker in text for marker in (". ", "다. ", "다 ", "려 합니다", "고 싶")):
        return True
    if text.count(" ") > 8:
        return True
    return False


def _session_topic(session: Optional[WorkflowSession]) -> Optional[str]:
    """Operator-curated topic stashed on the session (if any).

    The router pre-stashes one of these keys at intake time when a
    request is clearly tied to a known thread/topic — preferring them
    over heuristic derivation keeps the title stable across re-renders.
    """

    if session is None:
        return None
    extra = dict(getattr(session, "extra", None) or {})
    for key in (
        "topic",
        "thread_topic",
        "short_title",
        "research_title",
        "knowledge_title",
        "routing_decision_title",
    ):
        candidate = extra.get(key)
        if isinstance(candidate, str):
            cleaned = scrub_title_text(candidate)
            if cleaned:
                return cleaned[:KNOWLEDGE_TITLE_LIMIT]
    return None


def _synthesis_title(synthesis: Optional[TechLeadSynthesis]) -> Optional[str]:
    """Pull a title-shaped phrase out of the tech-lead synthesis."""

    if synthesis is None:
        return None
    consensus = (synthesis.consensus or "").strip()
    if not consensus:
        return None
    candidate = _truncate_to_phrase(consensus, max_chars=KNOWLEDGE_TITLE_LIMIT)
    return candidate or None


def _pack_title_or_summary(pack: Optional[ResearchPack]) -> Optional[str]:
    """Best title-shaped phrase out of a research pack.

    Prefers a clean, intentionally-short ``pack.title``; falls back to a
    truncated ``pack.summary`` (often the first sentence of a Discord
    message) so a knowledge note built directly from a forum starter
    still gets a readable header.
    """

    if pack is None:
        return None
    raw_title = (getattr(pack, "title", "") or "").strip()
    cleaned_title = scrub_title_text(raw_title)
    if cleaned_title and len(cleaned_title) <= KNOWLEDGE_TITLE_LIMIT and not _looks_like_full_prompt(cleaned_title):
        return cleaned_title
    for candidate in (raw_title, getattr(pack, "summary", "") or ""):
        derived = _truncate_to_phrase(candidate, max_chars=KNOWLEDGE_TITLE_LIMIT)
        if derived:
            return derived
    return None


def _task_type_fallback(session: Optional[WorkflowSession]) -> str:
    """Final fallback — never lets the title collapse to ``""``."""

    task_type = (
        getattr(session, "task_type", None) if session is not None else None
    )
    label = (task_type or "").strip() or "engineering"
    return f"{label} 작업 정리"


def derive_knowledge_title(
    *,
    pack: Optional[ResearchPack] = None,
    session: Optional[WorkflowSession] = None,
    synthesis: Optional[TechLeadSynthesis] = None,
    original_prompt: Optional[str] = None,
    max_chars: int = KNOWLEDGE_TITLE_LIMIT,
) -> str:
    """Pick a short, semantic title for the knowledge note.

    Resolution order (the first non-empty candidate wins):

    1. ``session.extra["topic" | "thread_topic" | "short_title" | …]`` —
       operator-curated.
    2. Noun-phrase from *original_prompt* (or ``session.prompt``) after
       URL/filler scrubbing.
    3. First sentence of ``synthesis.consensus``.
    4. ``pack.title`` (cleaned) when intentionally short, else first
       sentence of ``pack.summary``.
    5. ``<task_type> 작업 정리`` so the H1 is never empty.
    """

    explicit = _session_topic(session)
    if explicit:
        return explicit[:max_chars]

    prompt = (original_prompt or "").strip() or (
        (getattr(session, "prompt", None) or "").strip() if session else ""
    )
    if prompt:
        derived = _truncate_to_phrase(prompt, max_chars=max_chars)
        if derived:
            return derived

    synthesis_title = _synthesis_title(synthesis)
    if synthesis_title:
        return synthesis_title[:max_chars]

    pack_title = _pack_title_or_summary(pack)
    if pack_title:
        return pack_title[:max_chars]

    return _task_type_fallback(session)[:max_chars]


# ---------------------------------------------------------------------------
# Body / frontmatter assembly
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KnowledgeNote:
    """The structured knowledge document, pre-render.

    ``body_sections`` is an ordered ``(heading, body_markdown)`` list so
    callers that want to splice a section (e.g. an LLM-generated summary)
    can do so without re-parsing the rendered markdown. ``frontmatter``
    is a plain dict for the YAML head; ``content`` lazily formats the
    full document.
    """

    title: str
    topic: str
    summary: str
    body_sections: Sequence[Tuple[str, str]]
    frontmatter: Mapping[str, Any]
    vault_folder: str
    vault_filename: str

    @property
    def vault_path(self) -> str:
        return f"{self.vault_folder}/{self.vault_filename}"


def _bullets(items: Iterable[str]) -> str:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned:
        return "- (없음)"
    return "\n".join(f"- {item}" for item in cleaned)


def _purpose_section(
    *,
    session: Optional[WorkflowSession],
    synthesis: Optional[TechLeadSynthesis],
    title: str,
) -> str:
    """Best-effort statement of *why* this work was undertaken.

    Pulls from the operator-supplied purpose key first, falls through to
    a synthesis-derived sentence, then a generic task_type-based line so
    the section is never empty.
    """

    extra = dict(getattr(session, "extra", None) or {}) if session else {}
    candidate = extra.get("work_purpose") or extra.get("purpose")
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    if synthesis is not None and (synthesis.consensus or "").strip():
        first = scrub_title_text(synthesis.consensus.split(".")[0])
        if first:
            return f"{first}을 정리하기 위한 작업"
    task_type = (getattr(session, "task_type", None) or "").strip() if session else ""
    if task_type:
        return f"{task_type} 흐름에서 {title}을(를) 정리"
    return f"{title}에 대한 정리"


def _conclusion_section(
    *,
    synthesis: Optional[TechLeadSynthesis],
    role_turns: Sequence[RoleTake],
) -> str:
    """The current conclusion line — synthesis takes priority."""

    if synthesis is not None and (synthesis.consensus or "").strip():
        return synthesis.consensus.strip()
    # Role-take perspectives can act as a stand-in conclusion when there
    # is no synthesis yet (mid-thread snapshot).
    perspectives = [
        scrub_title_text(getattr(t, "perspective", "") or "")
        for t in role_turns
        if getattr(t, "perspective", None)
    ]
    perspectives = [p for p in perspectives if p]
    if perspectives:
        return "현재까지 정리된 관점:\n" + _bullets(perspectives)
    return "(아직 결론이 수렴되지 않았습니다.)"


def _sources_section(pack: Optional[ResearchPack]) -> str:
    """Render collected sources in a reader-oriented form.

    Discord/web URLs land first as bullet links so a reader can click
    through; attachments and code-context follow with one bullet each so
    provenance survives review.
    """

    if pack is None:
        return "- (없음)"
    lines: list[str] = []
    seen_urls: dict[str, None] = {}
    for source in pack.sources:
        url = (source.source_url or "").strip()
        if url and url not in seen_urls:
            seen_urls[url] = None
            label = (source.title or url).strip() or url
            label = scrub_title_text(label) or url
            role = (source.role or "").strip()
            role_part = f" — _{role}_" if role else ""
            lines.append(f"- [{label}]({url}){role_part}")
    for att in pack.attachments:
        kind = (att.kind or "file").strip() or "file"
        name = (att.filename or "").strip()
        url = (att.url or "").strip()
        descriptor = f"`{kind}`"
        if name:
            descriptor = f"{descriptor} {name}"
        if url:
            descriptor = f"{descriptor} <{url}>"
        if att.description:
            descriptor = f"{descriptor} — {att.description.strip()}"
        lines.append(f"- {descriptor}")
    if not lines:
        return "- (없음)"
    return "\n".join(lines)


def _role_review_section(role_turns: Sequence[RoleTake]) -> str:
    """Render every role take so the per-role critique is preserved.

    Uses :func:`render_role_take` so the per-role 4-section contract
    (관점/근거/리스크/다음 행동) survives into the Obsidian doc — the
    same shape the Discord forum already shows. Each take is wrapped in
    a ``###`` subhead so the document outline groups by role.
    """

    if not role_turns:
        return "- (역할별 검토가 아직 기록되지 않았습니다.)"
    blocks: list[str] = []
    for take in role_turns:
        role_label = getattr(take, "role", "") or "(unknown)"
        rendered = render_role_take(take).strip()
        blocks.append(f"### {role_label}\n{rendered}")
    return "\n\n".join(blocks)


def _tech_lead_section(synthesis: Optional[TechLeadSynthesis]) -> str:
    """Render the tech-lead synthesis as a multi-paragraph block."""

    if synthesis is None:
        return "- (아직 종합 의견이 기록되지 않았습니다.)"
    parts: list[str] = []
    if (synthesis.consensus or "").strip():
        parts.append(f"**합의안:** {synthesis.consensus.strip()}")
    parts.append("**해야 할 일**\n" + _bullets(synthesis.todos))
    parts.append("**더 조사할 것**\n" + _bullets(synthesis.open_research))
    if synthesis.approval_required:
        reason = (synthesis.approval_reason or "쓰기 승인 필요").strip()
        parts.append(f"**승인 필요:** yes — {reason}")
    else:
        parts.append("**승인 필요:** no")
    return "\n\n".join(parts)


def _decisions_section(
    *,
    synthesis: Optional[TechLeadSynthesis],
    role_turns: Sequence[RoleTake],
    explicit: Sequence[str],
) -> str:
    """Decisions / proposals section — merges synthesis + role + explicit."""

    items: list[str] = list(explicit)
    if synthesis is not None:
        items.extend(synthesis.user_decisions_needed)
    for take in role_turns:
        if isinstance(take, TechLeadOpening):
            items.extend(take.decisions_needed)
    return _bullets(_dedup(items))


def _next_actions_section(
    *,
    synthesis: Optional[TechLeadSynthesis],
    role_turns: Sequence[RoleTake],
    explicit: Sequence[str],
) -> str:
    """Next actions section — merges synthesis todos + role next_actions."""

    items: list[str] = list(explicit)
    if synthesis is not None:
        items.extend(synthesis.todos)
    for take in role_turns:
        next_actions = getattr(take, "next_actions", ()) or ()
        items.extend(next_actions)
    return _bullets(_dedup(items))


def _related_session_section(session: Optional[WorkflowSession]) -> str:
    if session is None:
        return "- (세션 정보 없음)"
    bits = [f"- session_id: `{session.session_id}`"]
    task_type = (getattr(session, "task_type", None) or "").strip()
    if task_type:
        bits.append(f"- task_type: `{task_type}`")
    state = getattr(getattr(session, "state", None), "value", None)
    if state:
        bits.append(f"- state: `{state}`")
    if session.thread_id is not None:
        bits.append(f"- thread_id: `{session.thread_id}`")
    if session.executor_role:
        bits.append(f"- executor_role: `{session.executor_role}`")
    return "\n".join(bits)


def _dedup(items: Iterable[str]) -> Tuple[str, ...]:
    seen: dict[str, None] = {}
    for item in items:
        if not item:
            continue
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen[text] = None
    return tuple(seen.keys())


def _resolve_original_prompt(
    *,
    session: Optional[WorkflowSession],
    pack: Optional[ResearchPack],
    explicit: Optional[str] = None,
) -> str:
    """Pick the most authoritative original prompt to preserve.

    Order: explicit kwarg → ``session.prompt`` → request topic on the
    pack → pack.summary when it differs meaningfully from the title →
    pack.title when it looks sentence-shaped.
    """

    if explicit and explicit.strip():
        return explicit.strip()
    if session is not None:
        prompt = (getattr(session, "prompt", None) or "").strip()
        if prompt:
            return prompt
    if pack is not None:
        request = getattr(pack, "request", None)
        if request is not None:
            for attr in ("question", "topic"):
                value = (getattr(request, attr, "") or "").strip()
                if value:
                    return value
        summary = (getattr(pack, "summary", "") or "").strip()
        title = (getattr(pack, "title", "") or "").strip()
        if summary and summary != title and len(summary) > len(title):
            return summary
        if title and _looks_like_full_prompt(title):
            return title
    return ""


def _resolve_status(
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


def _resolve_roles(
    pack: Optional[ResearchPack],
    role_turns: Sequence[RoleTake],
) -> list[str]:
    seen: dict[str, None] = {}
    for take in role_turns:
        role = (getattr(take, "role", "") or "").strip()
        if role and role not in seen:
            seen[role] = None
    if pack is not None:
        for role in pack.author_roles:
            cleaned = (role or "").strip()
            if cleaned and cleaned not in seen:
                seen[cleaned] = None
    return list(seen.keys())


def _resolve_sources(pack: Optional[ResearchPack]) -> list[str]:
    if pack is None:
        return []
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


def _format_section_block(heading: str, body: str) -> str:
    body_clean = body.strip() or "- (없음)"
    return f"## {heading}\n{body_clean}"


# ---------------------------------------------------------------------------
# Path / project helpers
# ---------------------------------------------------------------------------


def recommend_knowledge_path(
    *,
    title: str,
    project: Optional[str],
    created_at: Optional[datetime],
    layout: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
):
    """Return the knowledge-note vault path.

    Routes through :func:`obsidian_export.recommend_path` with
    ``kind="knowledge"`` so every note lands under the project's
    ``knowledge/`` subfolder by default. Legacy-agent layout falls back
    to the inbox bucket because there is no historical
    ``Agents/Engineering/Knowledge`` tree.
    """

    from .export import (
        FILENAME_BASENAME_LIMIT,
        FILENAME_SLUG_LIMIT,
        INBOX_UNSORTED,
        LAYOUT_LEGACY_AGENT,
        PROJECTS_BASE,
        ExportPath,
        _slugify,
        resolve_default_project,
        resolve_layout,
    )

    # F15 v2: canonical shape `knowledge-<topic-slug>.md` — date prefix removed
    # (validated by filename_convention.validate_filename).
    layout_resolved = resolve_layout(layout, env=env)
    slug = _slugify(title, max_chars=FILENAME_SLUG_LIMIT) or "untitled"
    basename = f"knowledge-{slug}.md"
    if len(basename) > FILENAME_BASENAME_LIMIT:
        keep = FILENAME_BASENAME_LIMIT - (len("knowledge") + 1 + 3)
        basename = f"knowledge-{slug[:max(1, keep)]}.md"

    if layout_resolved == LAYOUT_LEGACY_AGENT:
        # No legacy parent for ``knowledge/`` exists — route to the inbox
        # bucket so notes still land somewhere predictable when a vault
        # has not migrated to the yule-agent-vault tree.
        return ExportPath(folder=INBOX_UNSORTED, filename=basename)

    project_resolved = (project or "").strip() or resolve_default_project(env=env)
    project_slug = _slugify(project_resolved) or "uncategorized"
    folder = f"{PROJECTS_BASE}/{project_slug}/{KNOWLEDGE_SUBDIR}"
    return ExportPath(folder=folder, filename=basename)


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------


def build_knowledge_note(
    *,
    pack: Optional[ResearchPack] = None,
    session: Optional[WorkflowSession] = None,
    synthesis: Optional[TechLeadSynthesis] = None,
    role_turns: Sequence[RoleTake] = (),
    decisions: Sequence[str] = (),
    next_actions: Sequence[str] = (),
    original_prompt: Optional[str] = None,
    title: Optional[str] = None,
    project: Optional[str] = None,
    layout: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    exported_at: Optional[datetime] = None,
) -> KnowledgeNote:
    """Compose a :class:`KnowledgeNote` from the available work artifacts.

    Designed to be **fully tolerant of missing inputs** — a research-only
    snapshot (``pack`` alone) still produces a well-formed knowledge
    note; richer inputs (``synthesis`` + ``role_turns``) light up the
    review/decision/next-action sections. Callers who curate a title or
    purpose pre-stash them in ``session.extra``; the deterministic
    fallback chain takes over otherwise.
    """

    resolved_title = (title or "").strip() or derive_knowledge_title(
        pack=pack,
        session=session,
        synthesis=synthesis,
        original_prompt=original_prompt,
    )
    prompt = _resolve_original_prompt(
        session=session, pack=pack, explicit=original_prompt
    )

    purpose_body = _purpose_section(
        session=session, synthesis=synthesis, title=resolved_title
    )
    sections: list[Tuple[str, str]] = [
        (SECTION_PURPOSE, purpose_body),
        (
            SECTION_ORIGINAL_PROMPT,
            prompt or "(원문 요청이 기록되지 않았습니다.)",
        ),
        (
            SECTION_CONCLUSION,
            _conclusion_section(synthesis=synthesis, role_turns=role_turns),
        ),
        (SECTION_SOURCES, _sources_section(pack)),
        (SECTION_ROLE_REVIEW, _role_review_section(role_turns)),
        (SECTION_TECH_LEAD, _tech_lead_section(synthesis)),
        (
            SECTION_DECISIONS,
            _decisions_section(
                synthesis=synthesis,
                role_turns=role_turns,
                explicit=decisions,
            ),
        ),
        (
            SECTION_NEXT_ACTIONS,
            _next_actions_section(
                synthesis=synthesis,
                role_turns=role_turns,
                explicit=next_actions,
            ),
        ),
        (SECTION_RELATED_SESSION, _related_session_section(session)),
    ]

    summary = (synthesis.consensus.strip() if synthesis is not None and synthesis.consensus else "")
    if not summary:
        summary = (getattr(pack, "summary", "") or "").strip()

    created_at = (
        getattr(pack, "created_at", None)
        if pack is not None
        else None
    )
    if created_at is None and session is not None:
        created_at = getattr(session, "created_at", None)

    # Resolve project the same way render_research_note does so a
    # session-stamped project survives. Caller's explicit kwarg wins.
    project_resolved = _resolve_project(
        project=project, session=session, layout=layout, env=env
    )
    vault = recommend_knowledge_path(
        title=resolved_title,
        project=project_resolved,
        created_at=created_at,
        layout=layout,
        env=env,
    )

    frontmatter: dict[str, Any] = {
        "title": resolved_title,
        "topic": resolved_title,
        "original_prompt": prompt or None,
        "session_id": getattr(session, "session_id", None) if session else None,
        "kind": KNOWLEDGE_KIND,
        "status": _resolve_status(synthesis, session),
        "roles": _resolve_roles(pack, role_turns),
        "sources": _resolve_sources(pack),
        "created_at": _iso_or_none(created_at),
        "task_type": getattr(session, "task_type", None) if session else None,
        "project": project_resolved,
        "contract": KNOWLEDGE_CONTRACT_VERSION,
    }
    if synthesis is not None:
        frontmatter["approval_required"] = bool(synthesis.approval_required)
    if exported_at is not None:
        frontmatter["exported_at"] = exported_at.replace(microsecond=0).isoformat()

    return KnowledgeNote(
        title=resolved_title,
        topic=resolved_title,
        summary=summary,
        body_sections=tuple(sections),
        frontmatter=frontmatter,
        vault_folder=vault.folder,
        vault_filename=vault.filename,
    )


def _resolve_project(
    *,
    project: Optional[str],
    session: Optional[WorkflowSession],
    layout: Optional[str],
    env: Optional[Mapping[str, str]],
) -> Optional[str]:
    """Walk the project resolution chain, mirroring render_research_note."""

    from .export import (
        LAYOUT_LEGACY_AGENT,
        resolve_default_project,
        resolve_layout,
    )

    layout_resolved = resolve_layout(layout, env=env)
    explicit = (project or "").strip()
    if explicit:
        return explicit
    if layout_resolved == LAYOUT_LEGACY_AGENT:
        return None
    if session is not None:
        extra = dict(getattr(session, "extra", None) or {})
        candidate = extra.get("project") or extra.get("project_name")
        if candidate:
            return str(candidate).strip() or None
    return resolve_default_project(env=env)


def render_knowledge_markdown(note: KnowledgeNote) -> str:
    """Render a :class:`KnowledgeNote` to a single Markdown blob.

    Frontmatter ordering follows the dict's insertion order from
    :func:`build_knowledge_note`. Body sections render in the contract
    order set by :func:`build_knowledge_note` — callers should reorder
    by mutating the input dataclass before calling, not here.
    """

    from .export_render import _format_frontmatter

    head = _format_frontmatter(dict(note.frontmatter))
    body = [f"# {note.title}"]
    for heading, content in note.body_sections:
        body.append(_format_section_block(heading, content))
    return head + "\n\n" + "\n\n".join(body).strip() + "\n"


def render_knowledge_note(
    *,
    pack: Optional[ResearchPack] = None,
    session: Optional[WorkflowSession] = None,
    synthesis: Optional[TechLeadSynthesis] = None,
    role_turns: Sequence[RoleTake] = (),
    decisions: Sequence[str] = (),
    next_actions: Sequence[str] = (),
    original_prompt: Optional[str] = None,
    title: Optional[str] = None,
    project: Optional[str] = None,
    layout: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    exported_at: Optional[datetime] = None,
    kind: Optional[str] = None,
):
    """Build + render a knowledge note as an :class:`ObsidianNote`.

    This is the public entry point used by ``obsidian_export.render_research_note``
    when ``kind="knowledge"`` is requested, so the existing CLI/sync
    pipeline can opt into the richer template without changing call
    sites.

    Pass ``kind="knowledge-note"`` to opt into the M10a top-level vault
    layout (``20-knowledge/<basename>.md`` instead of
    ``10-projects/<project>/knowledge/<basename>.md``). Any other value
    of *kind* (or omitting it) keeps the legacy project-nested routing
    so existing callers stay byte-stable.
    """

    from .export import ExportPath, ObsidianNote
    from .note_kinds import (
        KIND_KNOWLEDGE_NOTE,
        canonical_kind,
        folder_for_canonical_kind,
    )

    note = build_knowledge_note(
        pack=pack,
        session=session,
        synthesis=synthesis,
        role_turns=role_turns,
        decisions=decisions,
        next_actions=next_actions,
        original_prompt=original_prompt,
        title=title,
        project=project,
        layout=layout,
        env=env,
        exported_at=exported_at,
    )
    content = render_knowledge_markdown(note)

    folder = note.vault_folder
    canonical = canonical_kind(kind)
    # Route only the canonical M10a name ``knowledge-note`` to the new
    # top-level folder; the legacy short form ``knowledge`` stays
    # project-nested so existing notes / tests remain stable.
    if canonical == KIND_KNOWLEDGE_NOTE and (kind or "").strip().lower() in {
        "knowledge-note",
        "knowledge_note",
    }:
        m10a_folder = folder_for_canonical_kind(canonical)
        if m10a_folder is not None:
            folder = m10a_folder

    return ObsidianNote(
        path=ExportPath(folder=folder, filename=note.vault_filename),
        content=content,
        frontmatter=dict(note.frontmatter),
    )
