"""Discord summary helper — daily role digest renderer.

Produces a short markdown block per role. Rules:

  * At most 5 items (the daily limit) per role.
  * Each line carries title / importance / source name + URL.
  * Body refers the reader to the full Obsidian document — Discord
    is *not* where the long-form knowledge note lives.
  * No actual Discord posting happens here. The post-side wiring
    (gateway / status-poster) is left for follow-up so the surface
    stays test-friendly and offline.
"""

from __future__ import annotations

from typing import Iterable, List, Sequence

from .models import EngineeringKnowledgeItem, Importance, KnowledgeShareScope


_IMPORTANCE_BADGES = {
    Importance.CRITICAL: "🟥 critical",
    Importance.HIGH: "🟧 high",
    Importance.MEDIUM: "🟨 medium",
    Importance.LOW: "🟩 low",
}


_MAX_PER_ROLE = 5


_SHARE_SCOPE_TAGS = {
    KnowledgeShareScope.PUBLIC: "",
    KnowledgeShareScope.TEAM_INTERNAL: " · 🔒 team-internal",
    KnowledgeShareScope.RESTRICTED: " · 🔒 공개 제한",
}


def _format_line(index: int, item: EngineeringKnowledgeItem) -> str:
    """Format a single digest line, honouring ``share_scope``.

    - ``PUBLIC``: title + badge + source link, identical to the v0
      output so existing assertions continue to pass.
    - ``TEAM_INTERNAL``: same surface, plus a ``team-internal`` tag so
      downstream readers know the body is Obsidian-only.
    - ``RESTRICTED``: title and source replaced by an opaque
      ``topic_key`` reference + the share-scope tag — no title, no
      source URL, no body. The Obsidian footer line still tells the
      reader where to look up the full record.
    """

    badge = _IMPORTANCE_BADGES.get(item.importance, item.importance.value)
    scope_tag = _SHARE_SCOPE_TAGS.get(item.share_scope, "")
    if item.share_scope == KnowledgeShareScope.RESTRICTED:
        return (
            f"{index}. **🔒 공개 제한된 자료** "
            f"(`{item.topic_key}`) — {badge}{scope_tag}"
        )
    return (
        f"{index}. **{item.title}** — {badge} · "
        f"[{item.source_name}]({item.source_url}){scope_tag}"
    )


def render_daily_role_summary(
    role_id: str,
    items: Sequence[EngineeringKnowledgeItem],
    *,
    today: str = "",
    extra_note: str = "",
) -> str:
    """Render the daily digest for *role_id*.

    Truncates to the first 5 items so a misconfigured collector
    can't flood the channel. When the list is empty, returns a
    single-line "no new knowledge" message — useful for the audit /
    no-op day so the channel still gets a heartbeat.
    """

    visible = list(items[:_MAX_PER_ROLE])
    lines: List[str] = []
    header = f"### 📚 {role_id} — engineering knowledge ({len(visible)}/{_MAX_PER_ROLE})"
    if today:
        header = f"{header} · {today}"
    lines.append(header)
    if not visible:
        lines.append("- 오늘 새로 수집된 기술 이슈가 없습니다.")
    else:
        for index, item in enumerate(visible, start=1):
            lines.append(_format_line(index, item))
    lines.append(
        "_상세 문서와 실습 가이드는 Obsidian `engineering-knowledge` 노트를 참고하세요._"
    )
    if extra_note:
        lines.append("")
        lines.append(extra_note)
    return "\n".join(lines)


def render_multi_role_summary(
    by_role: Iterable[tuple],
    *,
    today: str = "",
) -> str:
    """Render multiple role digests stacked into one markdown blob.

    *by_role* is an iterable of ``(role_id, items)`` tuples. Roles
    appear in the iteration order so the caller controls layout.
    """

    blocks: List[str] = []
    for role_id, items in by_role:
        blocks.append(render_daily_role_summary(role_id, items, today=today))
    return "\n\n".join(blocks)


__all__ = [
    "render_daily_role_summary",
    "render_multi_role_summary",
]
