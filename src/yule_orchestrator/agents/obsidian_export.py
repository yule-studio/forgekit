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


FILENAME_BASENAME_LIMIT = 100  # tip-of-the-iceberg cap for the bare filename
FILENAME_SLUG_LIMIT = 50  # leaves room for ``YYYY-MM-DD_<kind>-`` prefix
TITLE_LIMIT = 50  # short, readable Obsidian H1 / frontmatter title


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
      - default (no ``project``): ``Agents/Engineering/<kind>/<YYYY-MM-DD_<kind>-<slug>>.md``.
      - with ``project``: ``10-projects/<project-slug>/<kind>/<YYYY-MM-DD_<kind>-<slug>>.md``.

    The slug is capped at :data:`FILENAME_SLUG_LIMIT` characters and the
    full basename never exceeds :data:`FILENAME_BASENAME_LIMIT` so the
    Obsidian indexer / git checkout never trips on path-length limits.
    """

    folder = _kind_to_project_folder(kind, project) if project else _kind_to_folder(kind)
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
    normalized = (kind or "").strip().lower()
    if normalized in ("decision", "decisions"):
        return "decision"
    if normalized in ("reference", "references"):
        return "reference"
    return "research"


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


_RESEARCH_PREFIX_RE = re.compile(r"^\s*\[(?:Research|Decision|Reference)\]\s*", re.IGNORECASE)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def _clean_title(text: str) -> str:
    """Strip Discord/Obsidian markup that doesn't belong in a title.

    Removes ``[Research]`` / ``[Decision]`` style prefixes, markdown
    ``**bold**`` markers, line breaks, and runs of whitespace. The output
    is a single-line plain string that downstream summarisation /
    truncation can work on without escape-sequence surprises.
    """

    if not text:
        return ""
    cleaned = _RESEARCH_PREFIX_RE.sub("", text)
    cleaned = _BOLD_RE.sub(r"\1", cleaned)
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
