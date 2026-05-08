"""PR body template — G3.

Every draft PR opened by the agent must follow the same section
contract so a reviewer (human or another agent) can scan it without
parsing a free-form body. The template surfaces:

  * **목적** — why this work exists; sourced from the triage plan.
  * **범위** / **비범위** — what *is* and *is not* in this PR.
  * **변경 요약** — bullet list of files / modules touched.
  * **테스트 계획** — how the reviewer can verify locally.
  * **리스크** — risks the agent flagged at triage time.
  * **승인 필요 항목** — explicit human-decision items.
  * **agent work orders** — list of (autonomy_level, action) the agent
    plans to execute next.
  * **audit id** — pointer back into the agent_ops_audit log.
  * **trace link** — Discord / Obsidian / GitHub source URLs so a
    reviewer can backtrack to the originating context.

The template is intentionally Markdown-shaped — the GitHub API renders
``## 목적`` directly. We never inject raw HTML so a malicious title
in the triage plan can't escape the body's structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Protocol, Sequence, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


PR_REQUIRED_SECTIONS: Tuple[str, ...] = (
    "목적",
    "범위",
    "비범위",
    "변경 요약",
    "테스트 계획",
    "리스크",
    "승인 필요 항목",
    "agent work orders",
    "audit id",
    "trace link",
)


# ---------------------------------------------------------------------------
# Triage-plan Protocol — only the fields PR template reads
# ---------------------------------------------------------------------------


class TriagePlanLike(Protocol):
    title: str
    body: str
    primary_role: str
    autonomy_level: str
    issue_number: Optional[int]
    session_id: Optional[str]
    repo: Optional[str]
    in_scope: Sequence[str]
    out_of_scope: Sequence[str]
    test_plan: Sequence[str]
    risks: Sequence[str]
    approvals_needed: Sequence[str]
    work_orders: Sequence[Mapping[str, str]]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrBodySection:
    heading: str
    body: str

    def render(self) -> str:
        body = (self.body or "").rstrip()
        if not body:
            body = "_(미정)_"
        return f"## {self.heading}\n{body}"


@dataclass(frozen=True)
class PrBody:
    sections: Tuple[PrBodySection, ...]
    title: str = ""

    def render(self) -> str:
        rendered_sections = "\n\n".join(s.render() for s in self.sections)
        if self.title:
            return f"# {self.title}\n\n{rendered_sections}\n"
        return rendered_sections + "\n"

    def has_section(self, heading: str) -> bool:
        for section in self.sections:
            if section.heading == heading:
                return True
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bullet_list(items: Iterable[str], *, empty_marker: str = "_(없음)_") -> str:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned:
        return empty_marker
    return "\n".join(f"- {item}" for item in cleaned)


def _work_orders_block(orders: Sequence[Mapping[str, str]]) -> str:
    if not orders:
        return "_(없음)_"
    lines: list[str] = []
    for order in orders:
        if not isinstance(order, Mapping):
            continue
        autonomy = (order.get("autonomy_level") or "").strip() or "L?"
        action = (order.get("action") or "").strip() or "(unspecified)"
        target = (order.get("target") or "").strip()
        suffix = f" → `{target}`" if target else ""
        lines.append(f"- `{autonomy}` **{action}**{suffix}")
    if not lines:
        return "_(없음)_"
    return "\n".join(lines)


def _trace_link_block(trace_links: Mapping[str, str]) -> str:
    if not trace_links:
        return "_(없음)_"
    lines: list[str] = []
    # Stable key order — github / discord / obsidian / agent — so a
    # reviewer skimming PRs sees the same layout each time.
    preferred_order = ("github", "discord", "obsidian", "agent_ops_audit")
    seen: set = set()
    for key in preferred_order:
        url = (trace_links.get(key) or "").strip()
        if url:
            lines.append(f"- **{key}**: {url}")
            seen.add(key)
    for key, value in sorted(trace_links.items()):
        if key in seen:
            continue
        url = (value or "").strip()
        if url:
            lines.append(f"- **{key}**: {url}")
    if not lines:
        return "_(없음)_"
    return "\n".join(lines)


def _purpose_block(plan: TriagePlanLike) -> str:
    body = (getattr(plan, "body", "") or "").strip()
    title = (getattr(plan, "title", "") or "").strip()
    if not title and not body:
        return "_(미정)_"
    parts: list[str] = []
    if title:
        parts.append(f"**작업 제목:** {title}")
    issue_number = getattr(plan, "issue_number", None)
    session_id = getattr(plan, "session_id", None)
    role = (getattr(plan, "primary_role", "") or "").strip()
    if issue_number:
        parts.append(f"**Issue:** #{int(issue_number)}")
    if session_id:
        parts.append(f"**Discord session:** `{session_id}`")
    if role:
        parts.append(f"**Primary role:** `{role}`")
    if body:
        parts.append("")
        parts.append(body)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------


def render_pr_body(
    plan: TriagePlanLike,
    *,
    audit_id: str,
    agent_work_orders: Optional[Sequence[Mapping[str, str]]] = None,
    trace_links: Optional[Mapping[str, str]] = None,
    change_summary: Optional[Sequence[str]] = None,
) -> PrBody:
    """Render the full PR body for *plan*.

    *audit_id* is the agent_ops_audit entry id from
    :func:`audit.build_github_audit_record`. It MUST be present so a
    reviewer can grep the audit log without guessing.

    *agent_work_orders* — optional override; falls back to
    ``plan.work_orders`` when absent.

    *trace_links* — operator-injected URL set (Discord thread URL,
    Obsidian note URL, original GitHub issue URL).

    *change_summary* — bullet list to render under "변경 요약". Caller
    fills this from :class:`actions.GithubActionPlan` so the section
    matches what the agent actually plans to write.
    """

    orders = (
        list(agent_work_orders)
        if agent_work_orders is not None
        else list(getattr(plan, "work_orders", ()) or ())
    )
    links = dict(trace_links or {})
    summary_items = list(change_summary or ())

    sections: Tuple[PrBodySection, ...] = (
        PrBodySection("목적", _purpose_block(plan)),
        PrBodySection(
            "범위",
            _bullet_list(getattr(plan, "in_scope", ()) or ()),
        ),
        PrBodySection(
            "비범위",
            _bullet_list(getattr(plan, "out_of_scope", ()) or ()),
        ),
        PrBodySection(
            "변경 요약",
            _bullet_list(summary_items, empty_marker="_(아직 미정 — dry-run)_"),
        ),
        PrBodySection(
            "테스트 계획",
            _bullet_list(getattr(plan, "test_plan", ()) or ()),
        ),
        PrBodySection(
            "리스크",
            _bullet_list(getattr(plan, "risks", ()) or ()),
        ),
        PrBodySection(
            "승인 필요 항목",
            _bullet_list(getattr(plan, "approvals_needed", ()) or ()),
        ),
        PrBodySection("agent work orders", _work_orders_block(orders)),
        PrBodySection("audit id", f"`{audit_id or 'pending'}`"),
        PrBodySection("trace link", _trace_link_block(links)),
    )
    title = (getattr(plan, "title", "") or "").strip()
    return PrBody(sections=sections, title=title)


__all__ = (
    "PR_REQUIRED_SECTIONS",
    "PrBody",
    "PrBodySection",
    "TriagePlanLike",
    "render_pr_body",
)
