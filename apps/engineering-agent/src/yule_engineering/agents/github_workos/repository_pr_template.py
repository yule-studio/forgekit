"""Repository PR template discovery + fill — Fix.

Until now, every draft PR opened by ``yule github smoke-pr`` rendered
the agent's structured ``render_pr_body`` output verbatim. Reviewers
saw a body that didn't look like a normal team PR — none of the
repo's existing template sections (관련 이슈 / 과제 내용 / 스크린샷 /
레퍼런스) showed up.

This module fixes that by:

  1. Discovering the repository's PR template via GitHub's standard
     locations (priority order matches GitHub's own resolution rule).
  2. Splitting the template into ``## ...`` sections.
  3. Filling each section with triage-derived content using
     keyword-fuzzy heading matching (Korean + English aware).
  4. Appending a deterministic ``## 🤖 Agent WorkOS Audit`` block so
     reviewers can always grep audit_id / branch / commit / role
     even when the template never declared an audit slot.

When no template is found we never fabricate one — callers fall back
to ``render_pr_body`` and stamp a ``template_missing`` audit note.

Secret discipline:
    * Render output is run through :func:`redact_secret_like` so
      tokens / pem blocks accidentally leaked into a triage field
      never reach the GitHub API.
    * The discovered template path is included in the audit so a
      reviewer can compare the raw template — but the template's
      file *contents* are sourced as-is (we trust the repo).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
    Iterable,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

from .pr_template import TriagePlanLike
from ..lifecycle.agent_ops_log import AGENT_OPS_VAULT_FOLDER  # noqa: F401  (re-export contract)
from ...github_app.doctor import redact_secret_like


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


# Priority order matches GitHub's PR-template resolution. The
# extension-less ``.github/PULL_REQUEST_TEMPLATE`` is recognised
# because GitHub still renders it — the in-repo copy uses exactly
# that filename.
DEFAULT_TEMPLATE_PATHS: Tuple[str, ...] = (
    ".github/pull_request_template.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/pull_request_template",
    ".github/PULL_REQUEST_TEMPLATE",
    "PULL_REQUEST_TEMPLATE.md",
    "PULL_REQUEST_TEMPLATE",
)
DEFAULT_TEMPLATE_DIR: str = ".github/PULL_REQUEST_TEMPLATE"


# Audit reasons surfaced via :class:`RepositoryPrTemplate.found`.
TEMPLATE_REASON_FOUND_FILE: str = "found_file"
TEMPLATE_REASON_FOUND_DIRECTORY_FIRST: str = "found_directory_first"
TEMPLATE_REASON_NOT_FOUND: str = "template_missing"


@dataclass(frozen=True)
class RepositoryPrTemplate:
    """Outcome of repository PR-template discovery.

    ``raw_text`` is empty when ``found`` is False — the caller is
    then expected to fall back to ``render_pr_body``.

    ``section_headings`` is the ordered tuple of ``## ...`` headings
    parsed from the template (without the leading ``## ``). Useful
    for audit-trail debugging without re-parsing.
    """

    found: bool
    source_path: Optional[str]
    raw_text: str = ""
    section_headings: Tuple[str, ...] = ()
    discovery_reason: str = TEMPLATE_REASON_NOT_FOUND


def discover_repository_pr_template(
    *,
    repo_root: str,
    candidate_paths: Optional[Sequence[str]] = None,
) -> RepositoryPrTemplate:
    """Walk priority paths under *repo_root* and return the first
    template found.

    A directory entry (e.g. ``.github/PULL_REQUEST_TEMPLATE/``)
    expands to the first ``*.md`` file inside (sorted), matching
    GitHub's "default template" behaviour for multi-template repos.
    """

    root = Path(repo_root)
    paths = tuple(candidate_paths) if candidate_paths is not None else DEFAULT_TEMPLATE_PATHS

    for relative in paths:
        candidate = root / relative
        if candidate.is_dir():
            picked = _pick_directory_template(candidate)
            if picked is not None:
                return _read_template(
                    picked,
                    repo_root=root,
                    reason=TEMPLATE_REASON_FOUND_DIRECTORY_FIRST,
                )
            continue
        if candidate.is_file():
            return _read_template(
                candidate, repo_root=root, reason=TEMPLATE_REASON_FOUND_FILE
            )

    # Final sweep — explicit directory check even when not in the
    # candidate path list (e.g. ``.github/PULL_REQUEST_TEMPLATE/``
    # with custom layout).
    candidate_dir = root / DEFAULT_TEMPLATE_DIR
    if candidate_dir.is_dir():
        picked = _pick_directory_template(candidate_dir)
        if picked is not None:
            return _read_template(
                picked, repo_root=root, reason=TEMPLATE_REASON_FOUND_DIRECTORY_FIRST
            )

    return RepositoryPrTemplate(
        found=False,
        source_path=None,
        raw_text="",
        section_headings=(),
        discovery_reason=TEMPLATE_REASON_NOT_FOUND,
    )


def _pick_directory_template(directory: Path) -> Optional[Path]:
    """Pick the first ``*.md`` (sorted) under *directory*.

    Stable sort lets a repo encode "default template" by naming —
    e.g. ``00-default.md`` lands first. Hidden files (``.``) are
    skipped.
    """

    candidates: list[Path] = []
    for entry in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
        if entry.name.startswith("."):
            continue
        if not entry.is_file():
            continue
        if entry.suffix.lower() in (".md", ".markdown"):
            candidates.append(entry)
    return candidates[0] if candidates else None


def _read_template(
    path: Path,
    *,
    repo_root: Path,
    reason: str,
) -> RepositoryPrTemplate:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return RepositoryPrTemplate(
            found=False,
            source_path=str(path),
            raw_text="",
            section_headings=(),
            discovery_reason=TEMPLATE_REASON_NOT_FOUND,
        )
    headings = _parse_section_headings(raw)
    try:
        rel = path.resolve().relative_to(repo_root.resolve())
        source = str(rel)
    except ValueError:
        source = str(path)
    return RepositoryPrTemplate(
        found=True,
        source_path=source,
        raw_text=raw,
        section_headings=tuple(headings),
        discovery_reason=reason,
    )


# ---------------------------------------------------------------------------
# Fill context
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrTemplateFillContext:
    """All information the agent injects into the template.

    Field naming mirrors the smoke-pr CLI surface so wiring is one-
    line: ``PrTemplateFillContext(audit_id=..., branch=..., …)``.
    """

    audit_id: str
    branch: str
    commit_sha: str
    actor: str = "yule-studio-engineering-agent[bot]"
    primary_role: str = ""
    autonomy_level: str = ""
    issue_number: Optional[int] = None
    issue_url: str = ""
    purpose: str = ""
    change_summary: Sequence[str] = ()
    test_plan: Sequence[str] = ()
    risks: Sequence[str] = ()
    approvals_needed: Sequence[str] = ()
    work_orders: Sequence[Mapping[str, str]] = ()
    trace_links: Mapping[str, str] = field(default_factory=dict)
    smoke_mode: bool = False
    smoke_marker_path: str = ""
    base_branch: str = ""
    repo_full_name: str = ""
    extra_notes: Sequence[str] = ()


# ---------------------------------------------------------------------------
# Section heading classification
# ---------------------------------------------------------------------------


_HEADING_PATTERN: re.Pattern[str] = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.MULTILINE)

# Ordered classification rules. First-match wins so generic last.
# NOTE: order matters — first match wins. Specific anchors like
# "레퍼런스" / "스크린샷" / "테스트" come before the broader purpose
# keywords so a heading like "📚 레퍼런스 (또는 새로 알게 된 내용)"
# is classified as ``reference``, not ``purpose`` (the generic
# substring "내용" used to bleed into purpose).
_HEADING_KEYWORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("issue", ("관련 이슈", "이슈", "issue", "related", "linked")),
    ("reference", ("레퍼런스", "참고", "근거", "reference", "doc", "리서치")),
    ("screenshot", ("스크린샷", "screenshot", "image", "이미지")),
    ("test", ("테스트", "test", "qa", "verification", "검증")),
    ("risk", ("리스크", "risk", "위험", "안전")),
    ("approval", ("승인", "approval", "review", "검토")),
    ("notes", ("비고", "기타", "memo", "notes")),
    ("purpose", ("과제", "summary", "변경 요약", "what", "description")),
)


def _classify_heading(heading: str) -> str:
    """Map a heading string to a canonical kind via keyword match.

    Strips emoji / shortcodes / punctuation before matching.
    """

    normalised = _normalise_heading(heading)
    for kind, keywords in _HEADING_KEYWORDS:
        for keyword in keywords:
            if keyword in normalised:
                return kind
    return "other"


def _normalise_heading(heading: str) -> str:
    text = heading.strip()
    # Strip ``:emoji:`` shortcodes.
    text = re.sub(r":[a-zA-Z0-9_+\-]+:", "", text)
    # Strip standalone unicode emoji characters (rough range).
    text = re.sub(r"[\U00002700-\U000027BF\U0001F300-\U0001FAFF]", "", text)
    text = text.lower()
    text = re.sub(r"[^\wㄱ-ㅎㅏ-ㅣ가-힣]+", " ", text)
    return text.strip()


def _parse_section_headings(raw_text: str) -> list[str]:
    return [
        match.group(2).strip()
        for match in _HEADING_PATTERN.finditer(raw_text)
        if len(match.group(1)) == 2  # only ``## `` level headings
    ]


# ---------------------------------------------------------------------------
# Fill
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilledPrTemplate:
    """Outcome of :func:`fill_repository_pr_template`.

    ``rendered`` is the final markdown the CLI passes to GitHub.
    ``sections_filled`` is the list of section *kinds* the fill
    populated (vs. left untouched). Audit-friendly.
    """

    rendered: str
    sections_filled: Tuple[str, ...]
    template_path: Optional[str]


_MERGE_BLOCKED_NOTICE: str = (
    "## ⚠️ Merge 금지\n\n"
    "이 PR 은 GitHub App (G1~G6) 의 라이브 smoke 검증용으로 자동 생성되었습니다. "
    "**병합하지 마세요.** smoke 검증이 끝나면 `gh pr close` 로 닫고, "
    "필요한 경우 별도 운영자 승인 절차를 통해 새 PR 을 만드세요."
)

_AGENT_AUDIT_HEADING: str = "🤖 Agent WorkOS Audit"


def fill_repository_pr_template(
    template: RepositoryPrTemplate,
    context: PrTemplateFillContext,
) -> FilledPrTemplate:
    """Fill *template* with values from *context*.

    Always returns a :class:`FilledPrTemplate`. When *template* is
    not found, the rendered body is empty — caller falls back to
    :func:`render_pr_body`.

    Behaviour highlights:
        * Section bodies in the template that consist entirely of
          HTML comments (``<!-- ... -->``) are *replaced* with the
          agent-provided content. Mixed content sections (comment +
          text) get the agent content appended below the existing
          guidance so the reviewer still sees the team's writing
          tips.
        * The Agent WorkOS Audit section is always appended (even
          when the template covered every other field) so audit_id
          / branch / commit / actor are reliably present.
        * Smoke-mode prepends the merge-blocked notice; non-smoke
          omits it entirely.
    """

    if not template.found or not template.raw_text:
        return FilledPrTemplate(
            rendered="", sections_filled=(), template_path=template.source_path
        )

    sections = _split_sections(template.raw_text)
    filled_kinds: list[str] = []

    rendered_parts: list[str] = []
    for section in sections:
        kind = _classify_heading(section.heading) if section.heading else "preamble"
        new_body = _fill_section(kind=kind, original_body=section.body, context=context)
        if new_body is not None:
            filled_kinds.append(kind)
            section_block = _render_section(section.heading_line, new_body)
        else:
            section_block = _render_section(section.heading_line, section.body)
        rendered_parts.append(section_block)

    body = "\n\n".join(part.rstrip() for part in rendered_parts).rstrip() + "\n"

    audit_block = _render_audit_block(context)
    body = body.rstrip() + "\n\n" + audit_block + "\n"
    if context.extra_notes:
        body = body.rstrip() + "\n\n" + _render_extra_notes(context.extra_notes) + "\n"

    if context.smoke_mode:
        body = _MERGE_BLOCKED_NOTICE + "\n\n" + body

    body = redact_secret_like(body)
    return FilledPrTemplate(
        rendered=body,
        sections_filled=tuple(filled_kinds),
        template_path=template.source_path,
    )


def compose_pr_body(
    *,
    repo_root: str,
    plan: Optional[TriagePlanLike],
    context: PrTemplateFillContext,
    fallback_renderer: Optional[Any] = None,
) -> ComposedPrBody:
    """High-level helper used by the live PR creation paths.

    Discovers the repo PR template, fills it when found, otherwise
    delegates to *fallback_renderer* (the existing
    :func:`pr_template.render_pr_body`) and stamps the
    ``template_missing`` audit reason so the caller can include it
    in the audit log.
    """

    template = discover_repository_pr_template(repo_root=repo_root)
    if template.found:
        filled = fill_repository_pr_template(template, context)
        return ComposedPrBody(
            rendered=filled.rendered,
            template_path=filled.template_path,
            template_missing=False,
            template_reason=template.discovery_reason,
            sections_filled=filled.sections_filled,
        )

    # Fallback path.
    if fallback_renderer is None or plan is None:
        rendered = _render_minimal_audit_body(context)
    else:
        try:
            rendered_body = fallback_renderer(plan, audit_id=context.audit_id).render()
        except Exception:  # noqa: BLE001
            rendered_body = _render_minimal_audit_body(context)
            return ComposedPrBody(
                rendered=rendered_body,
                template_path=None,
                template_missing=True,
                template_reason=TEMPLATE_REASON_NOT_FOUND,
                sections_filled=(),
            )
        # Append audit block so even fallback path carries deterministic metadata.
        audit_block = _render_audit_block(context)
        rendered_body = rendered_body.rstrip() + "\n\n" + audit_block + "\n"
        if context.smoke_mode:
            rendered_body = _MERGE_BLOCKED_NOTICE + "\n\n" + rendered_body
        rendered = redact_secret_like(rendered_body)

    return ComposedPrBody(
        rendered=rendered,
        template_path=None,
        template_missing=True,
        template_reason=TEMPLATE_REASON_NOT_FOUND,
        sections_filled=(),
    )


@dataclass(frozen=True)
class ComposedPrBody:
    rendered: str
    template_path: Optional[str]
    template_missing: bool
    template_reason: str
    sections_filled: Tuple[str, ...]


# ---------------------------------------------------------------------------
# Section internals
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Section:
    heading_line: str  # exactly as it appears in the source ("## ✨ 과제 내용")
    heading: str       # without leading ``## `` and emoji
    body: str          # text between this heading and the next


def _split_sections(raw_text: str) -> Tuple[_Section, ...]:
    """Split markdown into ``## ...`` sections.

    Pre-section content (under a leading ``# H1`` or no headings)
    becomes the first section with ``heading_line=""``.
    """

    lines = raw_text.splitlines()
    sections: list[_Section] = []
    current_lines: list[str] = []
    current_heading_line: str = ""
    for line in lines:
        match = re.match(r"^(##\s+)(.*?)\s*$", line)
        if match:
            if current_heading_line or current_lines:
                sections.append(
                    _Section(
                        heading_line=current_heading_line,
                        heading=current_heading_line[3:].strip() if current_heading_line else "",
                        body="\n".join(current_lines).rstrip(),
                    )
                )
            current_heading_line = line
            current_lines = []
        else:
            current_lines.append(line)
    sections.append(
        _Section(
            heading_line=current_heading_line,
            heading=current_heading_line[3:].strip() if current_heading_line else "",
            body="\n".join(current_lines).rstrip(),
        )
    )
    return tuple(sections)


def _render_section(heading_line: str, body: str) -> str:
    if heading_line:
        return f"{heading_line}\n{body.rstrip()}".rstrip()
    return body.rstrip()


_HTML_COMMENT_PATTERN: re.Pattern[str] = re.compile(r"<!--.*?-->", re.DOTALL)


def _body_is_only_comment(body: str) -> bool:
    stripped = _HTML_COMMENT_PATTERN.sub("", body).strip()
    return stripped == ""


def _fill_section(
    *,
    kind: str,
    original_body: str,
    context: PrTemplateFillContext,
) -> Optional[str]:
    """Return new body for *kind*, or None to leave the original.

    ``preamble`` and unrecognised sections are left intact so the
    template's prose (e.g. an opening paragraph) is preserved.
    """

    if kind == "issue":
        return _issue_section_body(original_body, context)
    if kind == "purpose":
        return _purpose_section_body(original_body, context)
    if kind == "screenshot":
        # Agent-generated PRs never have screenshots; leave a clear
        # placeholder so the reviewer doesn't expect one.
        return "_(N/A — agent-generated PR; UI 변경 없음)_"
    if kind == "test":
        return _bullet_block(context.test_plan, fallback="_(테스트 계획 없음 — dry-run smoke)_")
    if kind == "risk":
        return _bullet_block(context.risks, fallback="_(추가 리스크 없음)_")
    if kind == "approval":
        return _bullet_block(
            context.approvals_needed,
            fallback="_(추가 승인 필요 항목 없음)_",
        )
    if kind == "reference":
        return _reference_section_body(original_body, context)
    if kind == "notes":
        return _notes_section_body(original_body, context)
    return None


def _issue_section_body(original_body: str, context: PrTemplateFillContext) -> str:
    parts: list[str] = []
    if context.issue_number:
        parts.append(f"- #{int(context.issue_number)}")
    if context.issue_url:
        parts.append(f"- {context.issue_url}")
    if not parts:
        parts.append("_(연결 issue 없음 — Discord intake 또는 자체 발화)_")
    if not _body_is_only_comment(original_body):
        # Preserve the operator's notes that aren't HTML comments.
        kept = _HTML_COMMENT_PATTERN.sub("", original_body).strip()
        if kept:
            parts.append("")
            parts.append(kept)
    return "\n".join(parts)


def _purpose_section_body(original_body: str, context: PrTemplateFillContext) -> str:
    lines: list[str] = []
    if context.purpose.strip():
        lines.append(context.purpose.strip())
    summary_block = _bullet_block(context.change_summary, fallback="")
    if summary_block:
        lines.append("")
        lines.append("### 변경 요약")
        lines.append(summary_block)
    if not lines:
        lines.append("_(작업 목적 미정 — triage 결과 누락)_")
    return "\n".join(lines)


def _reference_section_body(original_body: str, context: PrTemplateFillContext) -> str:
    parts: list[str] = []
    trace_lines = _trace_links_lines(context.trace_links)
    if trace_lines:
        parts.extend(trace_lines)
    if context.work_orders:
        parts.append("")
        parts.append("### Agent work orders")
        parts.append(_work_orders_block(context.work_orders))
    if not parts:
        parts.append("_(추가 레퍼런스 없음)_")
    if not _body_is_only_comment(original_body):
        kept = _HTML_COMMENT_PATTERN.sub("", original_body).strip()
        if kept:
            parts.append("")
            parts.append(kept)
    return "\n".join(parts)


def _notes_section_body(original_body: str, context: PrTemplateFillContext) -> str:
    if context.extra_notes:
        return _bullet_block(context.extra_notes, fallback="_(추가 비고 없음)_")
    if _body_is_only_comment(original_body):
        return "_(추가 비고 없음)_"
    return original_body


def _bullet_block(items: Iterable[str], *, fallback: str = "") -> str:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned:
        return fallback
    return "\n".join(f"- {item}" for item in cleaned)


def _work_orders_block(orders: Sequence[Mapping[str, str]]) -> str:
    if not orders:
        return "_(없음)_"
    lines: list[str] = []
    for order in orders:
        if not isinstance(order, Mapping):
            continue
        autonomy = (str(order.get("autonomy_level") or "").strip()) or "L?"
        action = (str(order.get("action") or "").strip()) or "(unspecified)"
        target = (str(order.get("target") or "").strip())
        suffix = f" → `{target}`" if target else ""
        lines.append(f"- `{autonomy}` **{action}**{suffix}")
    return "\n".join(lines) if lines else "_(없음)_"


def _trace_links_lines(trace_links: Mapping[str, str]) -> list[str]:
    if not trace_links:
        return []
    preferred_order = ("github", "discord", "obsidian", "agent_ops_audit")
    seen: set = set()
    lines: list[str] = []
    for key in preferred_order:
        url = (trace_links.get(key) or "").strip()
        if url:
            lines.append(f"- **{key}**: {url}")
            seen.add(key)
    for key, value in sorted(trace_links.items()):
        if key in seen:
            continue
        url = (str(value) or "").strip()
        if url:
            lines.append(f"- **{key}**: {url}")
    return lines


def _render_audit_block(context: PrTemplateFillContext) -> str:
    lines: list[str] = [f"## {_AGENT_AUDIT_HEADING}", ""]
    lines.append(f"- audit_id: `{context.audit_id or 'pending'}`")
    if context.branch:
        suffix = f" (from `{context.base_branch}`)" if context.base_branch else ""
        lines.append(f"- branch: `{context.branch}`{suffix}")
    if context.commit_sha:
        lines.append(f"- commit: `{context.commit_sha}`")
    if context.repo_full_name:
        lines.append(f"- repo: `{context.repo_full_name}`")
    if context.primary_role:
        lines.append(f"- role: `{context.primary_role}`")
    if context.autonomy_level:
        lines.append(f"- autonomy_level: `{context.autonomy_level}`")
    lines.append(f"- actor: `{context.actor}`")
    if context.smoke_mode:
        lines.append("- mode: `smoke (do-not-merge)`")
    else:
        lines.append("- mode: `live`")
    if context.smoke_marker_path:
        lines.append(f"- smoke marker: `{context.smoke_marker_path}`")
    if context.issue_url:
        lines.append(f"- issue: {context.issue_url}")
    work_orders = _work_orders_block(context.work_orders)
    if work_orders and work_orders != "_(없음)_":
        lines.append("")
        lines.append("### work orders")
        lines.append(work_orders)
    trace_lines = _trace_links_lines(context.trace_links)
    if trace_lines:
        lines.append("")
        lines.append("### trace links")
        lines.extend(trace_lines)
    return "\n".join(lines)


def _render_extra_notes(notes: Sequence[str]) -> str:
    block = _bullet_block(notes, fallback="")
    if not block:
        return ""
    return f"## Automation Notes\n\n{block}"


def _render_minimal_audit_body(context: PrTemplateFillContext) -> str:
    """Last-resort body when no template *and* no fallback renderer.

    Still carries the full audit block so reviewers can grep
    audit_id even in the degraded path.
    """

    block = _render_audit_block(context)
    body = block + "\n"
    if context.smoke_mode:
        body = _MERGE_BLOCKED_NOTICE + "\n\n" + body
    return redact_secret_like(body)


__all__ = (
    "ComposedPrBody",
    "DEFAULT_TEMPLATE_DIR",
    "DEFAULT_TEMPLATE_PATHS",
    "FilledPrTemplate",
    "PrTemplateFillContext",
    "RepositoryPrTemplate",
    "TEMPLATE_REASON_FOUND_DIRECTORY_FIRST",
    "TEMPLATE_REASON_FOUND_FILE",
    "TEMPLATE_REASON_NOT_FOUND",
    "compose_pr_body",
    "discover_repository_pr_template",
    "fill_repository_pr_template",
)
