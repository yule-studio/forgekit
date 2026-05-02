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

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional, Sequence

from .deliberation import TechLeadSynthesis
from .research_pack import ResearchAttachment, ResearchPack
from .workflow_state import WorkflowSession


CONTRACT_VERSION = "research-forum-export/v0"

VAULT_BASE = "Agents/Engineering"
PATH_RESEARCH = f"{VAULT_BASE}/Research"
PATH_DECISIONS = f"{VAULT_BASE}/Decisions"
PATH_REFERENCES = f"{VAULT_BASE}/References"

# yule-agent-vault layout (compatibility mode — opt-in via ``project=``).
# When the caller passes a project name, ResearchPack/DecisionRecord/
# Reference notes land under ``10-projects/<project>/{research,decisions,references}/``
# instead of the legacy ``Agents/Engineering/...`` flat tree. The default
# behaviour is unchanged so existing vaults keep working.
PROJECTS_BASE = "10-projects"
PROJECT_RESEARCH_SUBDIR = "research"
PROJECT_DECISIONS_SUBDIR = "decisions"
PROJECT_REFERENCES_SUBDIR = "references"


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


def recommend_path(
    *,
    title: str,
    kind: str,
    created_at: Optional[datetime] = None,
    project: Optional[str] = None,
) -> ExportPath:
    """Return the recommended export path.

    *kind* must be one of ``research``/``decision``/``reference`` (case
    insensitive). Anything else falls back to ``research``.

    Path layout:
      - default (no ``project``): ``Agents/Engineering/<kind>/<YYYY-MM-DD_slug>.md``.
      - with ``project``: ``10-projects/<project-slug>/<kind>/<YYYY-MM-DD_slug>.md``
        — the yule-agent-vault layout. Existing vaults keep using the
        default; new vaults can opt in by passing the project name.
    """

    folder = _kind_to_project_folder(kind, project) if project else _kind_to_folder(kind)
    when = created_at or datetime.utcnow()
    if isinstance(when, datetime):
        date_part = when.date().isoformat()
    elif isinstance(when, date):
        date_part = when.isoformat()
    else:
        date_part = datetime.utcnow().date().isoformat()
    slug = _slugify(title)
    if not slug:
        slug = "untitled"
    return ExportPath(folder=folder, filename=f"{date_part}_{slug}.md")


def _kind_to_project_folder(kind: str, project: str) -> str:
    project_slug = _slugify(project) or "uncategorized"
    normalized = (kind or "").strip().lower()
    if normalized in ("decision", "decisions"):
        return f"{PROJECTS_BASE}/{project_slug}/{PROJECT_DECISIONS_SUBDIR}"
    if normalized in ("reference", "references"):
        return f"{PROJECTS_BASE}/{project_slug}/{PROJECT_REFERENCES_SUBDIR}"
    return f"{PROJECTS_BASE}/{project_slug}/{PROJECT_RESEARCH_SUBDIR}"


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
) -> ObsidianNote:
    """Render a ResearchPack into an Obsidian-ready note.

    *kind* defaults based on whether a synthesis is provided
    (``decision`` if so, else ``research``). Pass ``"reference"``
    explicitly for pure UX/design reference notes.

    *project* opts the resulting note path into the
    ``10-projects/<project>/...`` yule-agent-vault layout. When omitted
    the legacy ``Agents/Engineering/...`` tree is preserved so existing
    vaults keep working.
    """

    chosen_kind = (kind or _infer_kind(synthesis)).lower()
    chosen_project = project or _project_from_session(session)
    frontmatter = _frontmatter(
        pack=pack,
        session=session,
        synthesis=synthesis,
        kind=chosen_kind,
        exported_at=exported_at,
    )
    if chosen_project:
        frontmatter["project"] = chosen_project
    body_lines = _body(pack, synthesis=synthesis, session=session)
    content = _format_frontmatter(frontmatter) + "\n\n" + "\n\n".join(body_lines).strip() + "\n"
    path = recommend_path(
        title=pack.title,
        kind=chosen_kind,
        created_at=pack.created_at,
        project=chosen_project,
    )
    return ObsidianNote(path=path, content=content, frontmatter=frontmatter)


def _project_from_session(session: Optional[WorkflowSession]) -> Optional[str]:
    """Best-effort project derivation from session metadata.

    Looks for an explicit ``project`` key in ``session.extra`` first
    (operators can stash it there at intake time). Returns ``None`` when
    nothing usable is found — the path then defaults to the legacy
    layout.
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
) -> dict:
    fm: dict = {
        "title": pack.title or "(untitled)",
        "source": pack.primary_url or _first_source_url(pack),
        "roles": list(pack.author_roles),
        "status": _status_from(synthesis, session),
        "session_id": getattr(session, "session_id", None) if session else None,
        "created_at": _iso_or_none(pack.created_at),
        "kind": kind,
        "tags": _tags_for(pack, kind),
        "topic": pack.title or "(untitled)",
        "task_type": getattr(session, "task_type", None) if session else None,
        "sources": _source_descriptors(pack),
        "contract": CONTRACT_VERSION,
    }
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
) -> list[str]:
    blocks: list[str] = []

    blocks.append(f"# {pack.title or '(untitled)'}")

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


def _kind_to_folder(kind: str) -> str:
    normalized = (kind or "").strip().lower()
    if normalized in ("decision", "decisions"):
        return PATH_DECISIONS
    if normalized in ("reference", "references"):
        return PATH_REFERENCES
    return PATH_RESEARCH


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


def _slugify(value: str) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFC", value)
    cleaned = re.sub(r"[^0-9A-Za-z가-힣]+", "-", normalized).strip("-").lower()
    return cleaned[:80]


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
