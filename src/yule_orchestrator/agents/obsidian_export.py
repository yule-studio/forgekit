"""Obsidian-bound Markdown serializer (string-only, no IO).

Converts a :class:`ResearchPack` (and optionally a deliberation
:class:`TechLeadSynthesis`) into a single Markdown string with YAML
frontmatter, plus a recommended vault-relative path.

This module never writes files. The actual file-write step is the
operator's call (or a future ``yule obsidian sync`` command). Keeping the
contract at the string level means tests and dry-runs are trivial and the
unit can be reviewed without a real vault.

The frontmatter shape and path rules are stable contract-v0; downstream
file writers and Obsidian readers should treat the YAML as the source of
truth for title/source/roles/status/session_id/created_at.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Mapping, Optional, Sequence

from .deliberation import TechLeadSynthesis
from .research.pack import ResearchAttachment, ResearchPack
from .workflow_state import WorkflowSession


CONTRACT_VERSION = "research-forum-export/v0"

# ---------------------------------------------------------------------------
# Layout / project policy (yule-agent-vault is the default)
# ---------------------------------------------------------------------------
#
# yule-agent-vault layout (default):
#   00-inbox/unsorted/        — 애매한 문서, kind 미지정/미상
#   10-projects/{project}/    — 프로젝트 관련 (research/decisions/references/task-logs/meeting-notes)
#   20-areas/{area}/          — 지속 참고 기술 개념
#   30-resources/             — 일반 자료 요약
#   40-patterns/              — 재사용 설계/구현 방식
#   50-snippets/{lang}/       — 코드 조각
#   60-troubleshooting/{area}/— 에러 해결
#   70-daily/                 — 일일 작업 기록
#   90-archive/               — 오래된 문서
#
# legacy-agent layout (opt-in via OBSIDIAN_EXPORT_LAYOUT=legacy-agent):
#   Agents/Engineering/{Research|Decisions|References}/
#
# Switching the default required a one-time path change for callers that
# expected the flat ``Agents/Engineering/...`` tree; any pre-existing notes
# in that tree are unaffected because Obsidian-side files are never moved
# by this module — only future writes pick up the new layout.

ENV_DEFAULT_PROJECT = "OBSIDIAN_DEFAULT_PROJECT"
ENV_EXPORT_LAYOUT = "OBSIDIAN_EXPORT_LAYOUT"

LAYOUT_YULE_AGENT_VAULT = "yule-agent-vault"
LAYOUT_LEGACY_AGENT = "legacy-agent"
KNOWN_LAYOUTS = (LAYOUT_YULE_AGENT_VAULT, LAYOUT_LEGACY_AGENT)
DEFAULT_LAYOUT = LAYOUT_YULE_AGENT_VAULT

# Hard-coded fallback when ``OBSIDIAN_DEFAULT_PROJECT`` is not set.
DEFAULT_PROJECT = "yule-studio-agent"

# yule-agent-vault top-level folders.
INBOX_BASE = "00-inbox"
INBOX_UNSORTED = f"{INBOX_BASE}/unsorted"
PROJECTS_BASE = "10-projects"
AREAS_BASE = "20-areas"
RESOURCES_BASE = "30-resources"
PATTERNS_BASE = "40-patterns"
SNIPPETS_BASE = "50-snippets"
TROUBLESHOOTING_BASE = "60-troubleshooting"
DAILY_BASE = "70-daily"
ARCHIVE_BASE = "90-archive"

# Per-project subdirectories (yule-agent-vault layout).
PROJECT_RESEARCH_SUBDIR = "research"
PROJECT_DECISIONS_SUBDIR = "decisions"
PROJECT_REFERENCES_SUBDIR = "references"
PROJECT_TASK_LOGS_SUBDIR = "task-logs"
PROJECT_MEETING_NOTES_SUBDIR = "meeting-notes"
PROJECT_KNOWLEDGE_SUBDIR = "knowledge"
PROJECT_WORK_REPORTS_SUBDIR = "reports"

# Legacy-agent layout (kept for opt-in legacy mode + back-compat imports).
VAULT_BASE = "Agents/Engineering"
PATH_RESEARCH = f"{VAULT_BASE}/Research"
PATH_DECISIONS = f"{VAULT_BASE}/Decisions"
PATH_REFERENCES = f"{VAULT_BASE}/References"

# Recognised kind aliases → canonical project subdirectory. Anything not
# in this map gets routed to ``00-inbox/unsorted/`` so it surfaces for
# triage instead of silently landing in research/.
_KIND_TO_PROJECT_SUBDIR: Mapping[str, str] = {
    "research": PROJECT_RESEARCH_SUBDIR,
    "decision": PROJECT_DECISIONS_SUBDIR,
    "decisions": PROJECT_DECISIONS_SUBDIR,
    "reference": PROJECT_REFERENCES_SUBDIR,
    "references": PROJECT_REFERENCES_SUBDIR,
    "task-log": PROJECT_TASK_LOGS_SUBDIR,
    "task-logs": PROJECT_TASK_LOGS_SUBDIR,
    "tasklog": PROJECT_TASK_LOGS_SUBDIR,
    "meeting": PROJECT_MEETING_NOTES_SUBDIR,
    "meeting-note": PROJECT_MEETING_NOTES_SUBDIR,
    "meeting-notes": PROJECT_MEETING_NOTES_SUBDIR,
    "knowledge": PROJECT_KNOWLEDGE_SUBDIR,
    # Phase 5: Engineering Agent's "업무 보고서" — one note per
    # research+deliberation cycle. Lands under ``reports/`` so it
    # sits next to task-logs but stays distinct (task-logs are
    # operator-driven; work-reports are gateway-emitted summaries).
    "work-report": PROJECT_WORK_REPORTS_SUBDIR,
    "work_report": PROJECT_WORK_REPORTS_SUBDIR,
    "report": PROJECT_WORK_REPORTS_SUBDIR,
    "reports": PROJECT_WORK_REPORTS_SUBDIR,
}

# Filename label per kind (used by ``recommend_path`` to build basenames
# like ``YYYY-MM-DD_decision-<slug>.md``). Anything unrecognised falls back
# to ``research`` so legacy callers keep working.
_KIND_TO_LABEL: Mapping[str, str] = {
    "research": "research",
    "decision": "decision",
    "decisions": "decision",
    "reference": "reference",
    "references": "reference",
    "task-log": "task-log",
    "task-logs": "task-log",
    "tasklog": "task-log",
    "meeting": "meeting",
    "meeting-note": "meeting",
    "meeting-notes": "meeting",
    "knowledge": "knowledge",
    "work-report": "work-report",
    "work_report": "work-report",
    "report": "work-report",
    "reports": "work-report",
}


@dataclass(frozen=True)
class ExportPath:
    """Vault-relative path proposal for one note."""

    folder: str
    filename: str

    @property
    def full(self) -> str:
        return f"{self.folder}/{self.filename}"


@dataclass(frozen=True)
class ObsidianNote:
    """One Markdown document ready to write into the vault.

    The caller is expected to take ``content`` and persist it at
    ``path.full`` inside the operator's vault root. ``frontmatter`` is
    exposed separately so importers can re-read the YAML without parsing
    Markdown.
    """

    path: ExportPath
    content: str
    frontmatter: dict


# ---------------------------------------------------------------------------
# Path rules
# ---------------------------------------------------------------------------


FILENAME_BASENAME_LIMIT = 100  # tip-of-the-iceberg cap for the bare filename
FILENAME_SLUG_LIMIT = 50  # leaves room for ``YYYY-MM-DD_<kind>-`` prefix
TITLE_LIMIT = 50  # short, readable Obsidian H1 / frontmatter title


def resolve_layout(
    layout: Optional[str] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """Return the canonical layout name (yule-agent-vault | legacy-agent).

    Resolution: explicit *layout* arg (validated) → ``OBSIDIAN_EXPORT_LAYOUT``
    env (case-insensitive, validated) → :data:`DEFAULT_LAYOUT`. Anything
    unrecognised silently degrades to the default — operators get the new
    vault tree by default and legacy mode is strictly opt-in.
    """

    if layout:
        normalized = str(layout).strip().lower()
        if normalized in KNOWN_LAYOUTS:
            return normalized
        return DEFAULT_LAYOUT
    env_map: Mapping[str, str] = env if env is not None else os.environ
    raw = (env_map.get(ENV_EXPORT_LAYOUT) or "").strip().lower()
    if raw in KNOWN_LAYOUTS:
        return raw
    return DEFAULT_LAYOUT


def resolve_default_project(
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """Return the operator-configured default project (or fallback).

    Reads ``OBSIDIAN_DEFAULT_PROJECT`` from *env* (or the live process env)
    and trims whitespace. When unset, falls back to :data:`DEFAULT_PROJECT`
    so the default-write target is always a concrete folder.
    """

    env_map: Mapping[str, str] = env if env is not None else os.environ
    raw = (env_map.get(ENV_DEFAULT_PROJECT) or "").strip()
    return raw or DEFAULT_PROJECT


def recommend_path(
    *,
    title: str,
    kind: str,
    created_at: Optional[datetime] = None,
    project: Optional[str] = None,
    layout: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> ExportPath:
    """Return the recommended export path.

    Layout default is :data:`LAYOUT_YULE_AGENT_VAULT` — note targets land
    under ``10-projects/<project>/<kind>/``. Caller can opt into the legacy
    flat tree by passing ``layout="legacy-agent"`` or via the
    ``OBSIDIAN_EXPORT_LAYOUT`` env.

    Project resolution for the default layout: explicit *project* arg →
    ``OBSIDIAN_DEFAULT_PROJECT`` env → :data:`DEFAULT_PROJECT`
    (``yule-studio-agent``). The session-aware project chain that prefers
    ``session.extra["project"]`` is run by :func:`render_research_note`
    before this function is called — keeping that lookup in the renderer
    means callers using the bare ``recommend_path`` API don't need to
    construct a ``WorkflowSession`` just to get the right folder.

    *kind* aliases (case-insensitive): ``research`` /
    ``decision`` (or ``decisions``) / ``reference`` (or ``references``)
    / ``task-log`` (or ``task-logs``) / ``meeting`` (or ``meeting-note`` /
    ``meeting-notes``). Anything else routes to ``00-inbox/unsorted/`` so
    the operator notices the unrecognised kind instead of silently
    burying it under research/.

    The slug is capped at :data:`FILENAME_SLUG_LIMIT` and the full basename
    never exceeds :data:`FILENAME_BASENAME_LIMIT` so Obsidian and git stay
    within their path-length limits.
    """

    layout_resolved = resolve_layout(layout, env=env)
    if layout_resolved == LAYOUT_LEGACY_AGENT:
        folder = _legacy_kind_to_folder(kind)
    else:
        project_resolved = (
            (project or "").strip() or resolve_default_project(env=env)
        )
        folder = _yule_vault_kind_to_folder(kind, project_resolved)
    when = created_at or datetime.utcnow()
    if isinstance(when, datetime):
        date_part = when.date().isoformat()
    elif isinstance(when, date):
        date_part = when.isoformat()
    else:
        date_part = datetime.utcnow().date().isoformat()
    kind_normalized = _kind_short_label(kind)
    slug = _slugify(title, max_chars=FILENAME_SLUG_LIMIT)
    if not slug:
        slug = "untitled"
    basename = f"{date_part}_{kind_normalized}-{slug}.md"
    if len(basename) > FILENAME_BASENAME_LIMIT:
        # Hard cap — trim slug further so the basename always fits.
        keep = FILENAME_BASENAME_LIMIT - (len(date_part) + 1 + len(kind_normalized) + 1 + 3)
        basename = f"{date_part}_{kind_normalized}-{slug[:max(1, keep)]}.md"
    return ExportPath(folder=folder, filename=basename)


def _kind_short_label(kind: str) -> str:
    """Return the filename-safe short label for *kind*.

    Falls back to ``research`` for unknown kinds — the folder routing
    sends them to ``00-inbox/unsorted/`` but the filename label stays
    deterministic so legacy filename tests keep passing.
    """

    normalized = (kind or "").strip().lower()
    return _KIND_TO_LABEL.get(normalized, "research")


def _yule_vault_kind_to_folder(kind: str, project: str) -> str:
    """yule-agent-vault folder for *kind* under ``10-projects/<project>/``.

    Unknown *kind* values land in ``00-inbox/unsorted/`` so they show up
    in the operator's triage queue instead of being silently buried.
    """

    normalized = (kind or "").strip().lower()
    subdir = _KIND_TO_PROJECT_SUBDIR.get(normalized)
    if subdir is None:
        return INBOX_UNSORTED
    project_slug = _slugify(project) or _slugify(DEFAULT_PROJECT) or "uncategorized"
    return f"{PROJECTS_BASE}/{project_slug}/{subdir}"


def _legacy_kind_to_folder(kind: str) -> str:
    """Legacy ``Agents/Engineering/<Kind>/`` mapping (opt-in only)."""

    normalized = (kind or "").strip().lower()
    if normalized in ("decision", "decisions"):
        return PATH_DECISIONS
    if normalized in ("reference", "references"):
        return PATH_REFERENCES
    return PATH_RESEARCH


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
    if chosen_kind == "knowledge":
        # Knowledge mode delegates to the richer template — semantic
        # title, role-by-role review, decisions/next-actions split — so
        # the existing research/decision/reference branches stay
        # byte-stable for vaults that haven't migrated.
        from .knowledge_writer import render_knowledge_note

        return render_knowledge_note(
            pack=pack,
            session=session,
            synthesis=synthesis,
            project=project,
            layout=layout,
            env=env,
            exported_at=exported_at,
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


def _slugify(value: str, *, max_chars: int = FILENAME_SLUG_LIMIT) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFC", value)
    cleaned = re.sub(r"[^0-9A-Za-z가-힣]+", "-", normalized).strip("-").lower()
    return cleaned[:max(1, max_chars)]


# ---------------------------------------------------------------------------
# Short title derivation (deterministic, LLM-free)
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
    """Render a :class:`agents.work_report.WorkReport` (or its dict
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
