"""Canonical M10a note kinds + folder routing + approval matrix.

The Obsidian Knowledge Ops core splits the vault into five top-level
folders, one per long-lived note kind:

  * ``research-log``    → ``10-research-log/``
  * ``knowledge-note``  → ``20-knowledge/``
  * ``decision-record`` → ``30-decisions/``
  * ``agent-ops``       → ``40-agent-ops/``
  * ``blog-draft``      → ``50-blog-drafts/``

Approval policy (M10a contract):

  * ``research-log`` / ``agent-ops`` / ``blog-draft`` are
    **approval-free** (L1/L2 in the autonomy ladder defined by
    :mod:`agents.lifecycle.autonomy_policy`). The producer captures
    them as the work happens; humans review afterwards.
  * ``knowledge-note`` / ``decision-record`` are the **approval-
    required** canonical archives. A write only lands when the
    request carries a full approval triple
    (``approval_id`` / ``approved_by`` / ``approved_at``). The actual
    guard runs inside :class:`agents.job_queue.obsidian_writer_worker
    .ObsidianWriterWorker.process_job`; this module is the policy
    declaration the worker (and any caller) consults.

The module is **policy-only** — it never reads/writes the vault, never
talks to the queue. Tests can import and exercise everything in-process.

Backward compatibility: legacy short kind names ``knowledge`` and
``decision`` resolve to the new canonical names so existing producers
that still emit ``knowledge`` keep their approval guard. The active
folder routing for legacy kinds (``10-projects/<project>/...``) is
unchanged in :mod:`agents.obsidian.export` — only the M10a canonical
names ``knowledge-note`` / ``decision-record`` route to the new
top-level folders. Migration of the legacy short names to the M10a
layout is deferred so we don't disrupt M10b tests pinned at the
project-nested layout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Canonical M10a kinds + folder mapping
# ---------------------------------------------------------------------------


KIND_RESEARCH_LOG: str = "research-log"
KIND_KNOWLEDGE_NOTE: str = "knowledge-note"
KIND_DECISION_RECORD: str = "decision-record"
KIND_AGENT_OPS: str = "agent-ops"
KIND_BLOG_DRAFT: str = "blog-draft"

#: Canonical kind names in the order they appear in the vault tree.
M10A_KINDS: Tuple[str, ...] = (
    KIND_RESEARCH_LOG,
    KIND_KNOWLEDGE_NOTE,
    KIND_DECISION_RECORD,
    KIND_AGENT_OPS,
    KIND_BLOG_DRAFT,
)


FOLDER_RESEARCH_LOG: str = "10-research-log"
FOLDER_KNOWLEDGE: str = "20-knowledge"
FOLDER_DECISIONS: str = "30-decisions"
FOLDER_AGENT_OPS: str = "40-agent-ops"
FOLDER_BLOG_DRAFTS: str = "50-blog-drafts"


_KIND_TO_TOPLEVEL_FOLDER: Mapping[str, str] = {
    KIND_RESEARCH_LOG: FOLDER_RESEARCH_LOG,
    KIND_KNOWLEDGE_NOTE: FOLDER_KNOWLEDGE,
    KIND_DECISION_RECORD: FOLDER_DECISIONS,
    KIND_AGENT_OPS: FOLDER_AGENT_OPS,
    KIND_BLOG_DRAFT: FOLDER_BLOG_DRAFTS,
}


# Aliases → canonical. Snake_case + legacy short forms collapse here so
# producers that still emit ``knowledge`` (rather than ``knowledge-note``)
# still hit the approval guard.
_KIND_ALIASES: Mapping[str, str] = {
    "research_log": KIND_RESEARCH_LOG,
    "research-log": KIND_RESEARCH_LOG,
    "knowledge": KIND_KNOWLEDGE_NOTE,
    "knowledge_note": KIND_KNOWLEDGE_NOTE,
    "knowledge-note": KIND_KNOWLEDGE_NOTE,
    "decision": KIND_DECISION_RECORD,
    "decisions": KIND_DECISION_RECORD,
    "decision_record": KIND_DECISION_RECORD,
    "decision-record": KIND_DECISION_RECORD,
    "agent_ops": KIND_AGENT_OPS,
    "agent-ops": KIND_AGENT_OPS,
    "blog_draft": KIND_BLOG_DRAFT,
    "blog-draft": KIND_BLOG_DRAFT,
}


# Approval-required canonical kinds. Authoring/saving an entry of
# these kinds without a real human approval is a regression — the worker
# enforces the gate at the queue boundary.
_APPROVAL_REQUIRED: frozenset[str] = frozenset(
    {KIND_KNOWLEDGE_NOTE, KIND_DECISION_RECORD}
)


def canonical_kind(kind: Optional[str]) -> Optional[str]:
    """Return the canonical M10a kind for *kind*, or ``None``.

    Accepts canonical names, snake_case variants, and legacy short
    forms. Unrecognised values return ``None`` so callers can decide
    whether to fall through to a legacy routing helper.
    """

    if not kind:
        return None
    norm = str(kind).strip().lower()
    return _KIND_ALIASES.get(norm)


def is_canonical_kind(kind: Optional[str]) -> bool:
    """True when *kind* (or its alias) maps to one of the M10a kinds."""

    return canonical_kind(kind) is not None


def folder_for_canonical_kind(kind: Optional[str]) -> Optional[str]:
    """Return the M10a top-level vault folder for *kind*, or ``None``.

    The returned string never carries a trailing slash. Path joining is
    the caller's responsibility — typically :func:`recommend_path` in
    :mod:`agents.obsidian.export` appends a date-stamped basename.
    """

    canonical = canonical_kind(kind)
    if canonical is None:
        return None
    return _KIND_TO_TOPLEVEL_FOLDER[canonical]


def requires_approval(kind: Optional[str]) -> bool:
    """True when this kind requires explicit human approval.

    Canonical-kind aware: ``knowledge``, ``knowledge-note``, ``decision``,
    ``decision-record`` all return True; ``research-log`` / ``agent-ops``
    / ``blog-draft`` return False. Unrecognised kinds return False so
    legacy callers (e.g. ``research``, ``meeting``) keep their existing
    behaviour — the worker still enforces overwrite-based approval on
    top of this matrix.
    """

    canonical = canonical_kind(kind)
    if canonical is None:
        return False
    return canonical in _APPROVAL_REQUIRED


# ---------------------------------------------------------------------------
# Title normalization
# ---------------------------------------------------------------------------


#: Default title length cap (characters) used by :func:`normalize_title`.
TITLE_NORMALIZED_LIMIT: int = 60


_LABEL_PREFIX_RE = re.compile(
    r"^\s*\[(?:Research|Decision|Reference|Knowledge|Agent[- ]?Ops|Blog[- ]?Draft|"
    r"Research[- ]?Log|Knowledge[- ]?Note|Decision[- ]?Record)\]\s*",
    re.IGNORECASE,
)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_title(text: str, *, max_chars: int = TITLE_NORMALIZED_LIMIT) -> str:
    """Return *text* normalised for use as an Obsidian H1 / filename slug.

    Operations (in order):

    1. Strip ``[Research]`` / ``[Decision]`` / ``[Knowledge]`` /
       ``[Knowledge-Note]`` / ``[Research-Log]`` etc. label prefixes.
    2. Drop ``**bold**`` markers (the markdown is preserved by the body
       renderer; titles stay plain).
    3. Remove inline URLs (titles like ``자료 링크 https://x`` lose the URL).
    4. Collapse whitespace and line breaks to single spaces.
    5. Truncate at the first sentence boundary that fits *max_chars*; if
       no boundary lands inside the budget, cut at the nearest word
       boundary above ``max_chars * 0.5`` and append ``…``.

    Empty input or input that becomes empty after scrubbing returns
    ``""`` so callers can fall back to the next title candidate in the
    resolution chain.
    """

    if not text:
        return ""
    cleaned = _LABEL_PREFIX_RE.sub("", text)
    cleaned = _BOLD_RE.sub(r"\1", cleaned)
    cleaned = _URL_RE.sub(" ", cleaned)
    cleaned = cleaned.replace("\r", " ").replace("\n", " ")
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip(" -·…,.;:")
    if not cleaned:
        return ""
    if len(cleaned) <= max_chars:
        return cleaned
    # Try a sentence-boundary cut first so the truncated title still
    # ends on a meaningful clause.
    for sep in (". ", "! ", "? ", "。", " — ", " - ", "다. ", "다 "):
        idx = cleaned.find(sep)
        if 0 < idx <= max_chars:
            return cleaned[:idx].rstrip(" ,.;:")
    head = cleaned[:max_chars]
    pivot = head.rfind(" ")
    if pivot >= max_chars // 2:
        head = head[:pivot]
    return head.rstrip(" ,.;:") + "…"


# ---------------------------------------------------------------------------
# Renderer interface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NoteRenderContext:
    """Structured envelope every M10a renderer can consume.

    The five M10a kinds share a payload shape: a normalized ``title``,
    one or more body markdown blocks, the Discord/forum thread that
    triggered the write (``source_thread_url``), an optional list of
    extra reference links (``links``), and per-role summary lines
    (``role_notes`` keyed by role id).

    ``frontmatter_extras`` is a free-form mapping merged into the
    rendered note's YAML frontmatter so producers can attach audit
    keys (``topic_key``, ``approval_job_id``, ``autonomy_level``)
    without subclassing.

    The empty-content guard :meth:`has_content` returns False when no
    body block, no ``role_notes`` value, and no ``links`` carry any
    visible text. Renderers MUST raise an :class:`Exception`
    (typically :class:`agents.job_queue.obsidian_writer_worker
    .ObsidianRenderError`) instead of writing a hollow file when this
    guard fails.
    """

    title: str
    note_kind: str
    body_blocks: Sequence[str] = ()
    source_thread_url: Optional[str] = None
    links: Sequence[str] = ()
    role_notes: Mapping[str, str] = field(default_factory=dict)
    frontmatter_extras: Mapping[str, Any] = field(default_factory=dict)

    @property
    def canonical_kind(self) -> Optional[str]:
        """Return the canonical M10a kind for :attr:`note_kind`."""

        return canonical_kind(self.note_kind)

    @property
    def folder(self) -> Optional[str]:
        """Return the M10a top-level folder for :attr:`note_kind`."""

        return folder_for_canonical_kind(self.note_kind)

    @property
    def requires_approval(self) -> bool:
        """True when writing this note requires a human approval."""

        return requires_approval(self.note_kind)

    def has_content(self) -> bool:
        """True iff at least one block / role note / link is non-empty.

        Used by renderers as an empty-note guard so a hollow vault file
        never lands. Whitespace-only entries don't count.
        """

        for block in self.body_blocks:
            if str(block or "").strip():
                return True
        for value in self.role_notes.values():
            if str(value or "").strip():
                return True
        for url in self.links:
            if str(url or "").strip():
                return True
        return False


# ---------------------------------------------------------------------------
# Rendered helpers (used by future renderers / consumers)
# ---------------------------------------------------------------------------


def render_links_block(links: Sequence[str], *, heading: str = "## 추가 자료") -> str:
    """Render *links* as a bulleted markdown block, or ``""`` when empty.

    Whitespace-only entries are dropped; duplicates collapse while
    preserving first-seen order so the markdown stays stable across
    re-renders.
    """

    seen: dict[str, None] = {}
    for raw in links or ():
        text = str(raw or "").strip()
        if text and text not in seen:
            seen[text] = None
    if not seen:
        return ""
    lines = [heading, ""]
    for url in seen.keys():
        lines.append(f"- {url}")
    return "\n".join(lines).rstrip() + "\n"


def render_role_notes_block(
    role_notes: Mapping[str, str],
    *,
    heading: str = "## 역할별 노트",
) -> str:
    """Render *role_notes* as a labelled markdown block, or ``""`` empty.

    Roles with empty / whitespace-only values are dropped. Roles render
    in sorted order so re-runs produce byte-stable markdown.
    """

    entries: list[tuple[str, str]] = []
    for role, body in (role_notes or {}).items():
        key = str(role or "").strip()
        text = str(body or "").strip()
        if not key or not text:
            continue
        entries.append((key, text))
    if not entries:
        return ""
    entries.sort(key=lambda pair: pair[0])
    lines = [heading, ""]
    for role, body in entries:
        lines.append(f"### {role}")
        lines.append("")
        lines.append(body)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_source_thread_block(
    source_thread_url: Optional[str],
    *,
    title: Optional[str] = None,
) -> str:
    """Render a ``## 출처 thread`` block, or ``""`` when no URL is set."""

    text = (source_thread_url or "").strip()
    if not text:
        return ""
    lines = ["## 출처 thread", ""]
    if title and title.strip():
        lines.append(f"- 제목: {title.strip()}")
    lines.append(f"- URL: {text}")
    return "\n".join(lines).rstrip() + "\n"


__all__ = (
    "FOLDER_AGENT_OPS",
    "FOLDER_BLOG_DRAFTS",
    "FOLDER_DECISIONS",
    "FOLDER_KNOWLEDGE",
    "FOLDER_RESEARCH_LOG",
    "KIND_AGENT_OPS",
    "KIND_BLOG_DRAFT",
    "KIND_DECISION_RECORD",
    "KIND_KNOWLEDGE_NOTE",
    "KIND_RESEARCH_LOG",
    "M10A_KINDS",
    "NoteRenderContext",
    "TITLE_NORMALIZED_LIMIT",
    "canonical_kind",
    "folder_for_canonical_kind",
    "is_canonical_kind",
    "normalize_title",
    "render_links_block",
    "render_role_notes_block",
    "render_source_thread_block",
    "requires_approval",
)
