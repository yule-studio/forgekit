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
from datetime import datetime
from typing import Mapping, Optional


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
# A-M10b — autonomous engineering ops subdirectories. Land within
# the project tree so they sit next to the human-driven research /
# knowledge / decisions notes; no top-level shuffle of the existing
# yule-agent-vault layout.
PROJECT_RESEARCH_LOG_SUBDIR = "research-log"
PROJECT_AGENT_OPS_SUBDIR = "agent-ops"
PROJECT_POSTMORTEMS_SUBDIR = "agent-ops/postmortems"
PROJECT_PROPOSALS_SUBDIR = "agent-ops/proposals"
PROJECT_BLOG_DRAFTS_SUBDIR = "blog-drafts"

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
    # A-M10b — autonomous engineering ops kinds.
    "research-log": PROJECT_RESEARCH_LOG_SUBDIR,
    "research_log": PROJECT_RESEARCH_LOG_SUBDIR,
    "agent-ops": PROJECT_AGENT_OPS_SUBDIR,
    "agent_ops": PROJECT_AGENT_OPS_SUBDIR,
    "failure-postmortem": PROJECT_POSTMORTEMS_SUBDIR,
    "failure_postmortem": PROJECT_POSTMORTEMS_SUBDIR,
    "postmortem": PROJECT_POSTMORTEMS_SUBDIR,
    "self-improvement-proposal": PROJECT_PROPOSALS_SUBDIR,
    "self_improvement_proposal": PROJECT_PROPOSALS_SUBDIR,
    "proposal": PROJECT_PROPOSALS_SUBDIR,
    "blog-draft": PROJECT_BLOG_DRAFTS_SUBDIR,
    "blog_draft": PROJECT_BLOG_DRAFTS_SUBDIR,
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
    # A-M10b
    "research-log": "research-log",
    "research_log": "research-log",
    "agent-ops": "agent-ops",
    "agent_ops": "agent-ops",
    "failure-postmortem": "postmortem",
    "failure_postmortem": "postmortem",
    "postmortem": "postmortem",
    "self-improvement-proposal": "proposal",
    "self_improvement_proposal": "proposal",
    "proposal": "proposal",
    "blog-draft": "blog-draft",
    "blog_draft": "blog-draft",
    # A-M10a — canonical knowledge ops kinds (full names). Approval-required
    # archives that route to top-level vault folders (20-knowledge,
    # 30-decisions). The legacy short forms ``knowledge`` and ``decision``
    # above keep the existing ``10-projects/<project>/knowledge|decisions/``
    # routing so M10b tests pinned at the project-nested layout stay green.
    "knowledge-note": "knowledge",
    "knowledge_note": "knowledge",
    "decision-record": "decision",
    "decision_record": "decision",
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
    # F15 v2: filename drops date prefix and uses dash separator —
    # canonical shape `<kind>-<topic-slug>[-issue-<n>].md` enforced by
    # filename_convention.validate_filename. Date lives in frontmatter
    # `created_at` and the body version table.
    kind_normalized = _kind_short_label(kind)
    slug = _slugify(title, max_chars=FILENAME_SLUG_LIMIT)
    if not slug:
        slug = "untitled"
    basename = f"{kind_normalized}-{slug}.md"
    if len(basename) > FILENAME_BASENAME_LIMIT:
        keep = FILENAME_BASENAME_LIMIT - (len(kind_normalized) + 1 + 3)
        basename = f"{kind_normalized}-{slug[:max(1, keep)]}.md"
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

    M10a canonical names ``knowledge-note`` / ``decision-record`` route to
    the new top-level Knowledge Ops folders (``20-knowledge/``,
    ``30-decisions/``) — the project segment is dropped because these
    archives are operator-wide rather than project-bound. The legacy
    short forms ``knowledge`` / ``decision`` keep their original
    project-nested routing so M10b tests stay green and existing notes
    in ``10-projects/<project>/knowledge/`` are not orphaned.

    Unknown *kind* values land in ``00-inbox/unsorted/`` so they show up
    in the operator's triage queue instead of being silently buried.
    """

    from .note_kinds import (
        KIND_DECISION_RECORD,
        KIND_KNOWLEDGE_NOTE,
        canonical_kind,
        folder_for_canonical_kind,
    )

    normalized = (kind or "").strip().lower()
    canonical = canonical_kind(normalized)
    # Only the freshly-introduced canonical names take the M10a top-level
    # routing; the legacy short forms (``knowledge``, ``decision``) keep
    # their project-nested layout.
    if (
        canonical in (KIND_KNOWLEDGE_NOTE, KIND_DECISION_RECORD)
        and normalized in {KIND_KNOWLEDGE_NOTE, KIND_DECISION_RECORD,
                            "knowledge_note", "decision_record"}
    ):
        m10a_folder = folder_for_canonical_kind(canonical)
        if m10a_folder is not None:
            return m10a_folder

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
# Slug helper (shared by path routing above)
# ---------------------------------------------------------------------------


def _slugify(value: str, *, max_chars: int = FILENAME_SLUG_LIMIT) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFC", value)
    cleaned = re.sub(r"[^0-9A-Za-z가-힣]+", "-", normalized).strip("-").lower()
    return cleaned[:max(1, max_chars)]
