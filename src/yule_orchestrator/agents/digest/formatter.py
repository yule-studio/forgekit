"""F13 — Discord 카드 포맷 (GeekNews 스타일).

사용자 design (2026-05-12):
> "수집 결과를 부서별로 분류해서 각 부서 채널에 1차 게시합니다."
> "1차 수집은 본문 전문이 아니라 `제목`, `URL`, `발행일`, `요약`, `태그`, `출처`, `부서 후보`만 저장합니다."

PasteGuard 통합: caller 가 `guard_outbound(channel=DISCORD)` 후 전달.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence


@dataclass(frozen=True)
class DigestCard:
    """Discord 한 카드의 정형 데이터.

    ``render_text()`` 로 plain-text 변환. embed 빌더 (discord.Embed) 는
    discord.py 의존이라 caller 가 별도로 사용.
    """

    title: str
    url: str
    summary: str
    source_host: str
    published_at: Optional[str]
    tags: Sequence[str]
    dept_primary: str
    affected_depts: Sequence[str]
    meeting_trigger: bool
    role_hint: Optional[str] = None

    def render_text(self) -> str:
        """GeekNews 카드 — Discord 메시지 (markdown 사용 가능).

        형식:
            **{title}**
            <{url}>
            > {summary}
            출처: `{source_host}` · {published_at} · 태그: {tags}
            부서: {primary} ({affected})
        """

        lines: list = []
        # 제목 (강조)
        lines.append(f"**{self.title}**")
        # URL — Discord embed 자동 unfurl 방지로 `<...>` wrap
        lines.append(f"<{self.url}>")
        # 요약 (인용 블록)
        if self.summary:
            summary = self.summary.strip()
            if len(summary) > 280:
                summary = summary[:277] + "..."
            lines.append(f"> {summary}")
        # 메타 (source + published_at + tags)
        meta_parts = [f"출처: `{self.source_host}`"]
        if self.published_at:
            meta_parts.append(self.published_at)
        if self.tags:
            meta_parts.append("태그: " + ", ".join(self.tags))
        lines.append(" · ".join(meta_parts))
        # 부서 라우팅
        affected_str = ", ".join(self.affected_depts) if self.affected_depts else self.dept_primary
        dept_line = f"부서: {self.dept_primary}"
        if affected_str != self.dept_primary:
            dept_line += f" (영향: {affected_str})"
        if self.meeting_trigger:
            dept_line += " · 🟡 운영-리서치 회의 후보"
        if self.role_hint:
            dept_line += f" · role={self.role_hint}"
        lines.append(dept_line)
        return "\n".join(lines)


def format_card(
    *,
    title: str,
    url: str,
    summary: str,
    source_host: str,
    published_at: Optional[datetime] = None,
    tags: Optional[Sequence[str]] = None,
    dept_primary: str,
    affected_depts: Sequence[str] = (),
    meeting_trigger: bool = False,
    role_hint: Optional[str] = None,
) -> DigestCard:
    """팩토리 — datetime 을 KST iso 문자열로 정규화하고 빈 필드 안전 처리."""

    published_iso: Optional[str] = None
    if published_at is not None:
        try:
            published_iso = published_at.strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001 — best-effort
            published_iso = str(published_at)[:10]
    return DigestCard(
        title=(title or "(no title)").strip(),
        url=(url or "").strip(),
        summary=(summary or "").strip(),
        source_host=source_host.strip(),
        published_at=published_iso,
        tags=tuple(tags or ()),
        dept_primary=dept_primary,
        affected_depts=tuple(affected_depts) if affected_depts else (dept_primary,),
        meeting_trigger=meeting_trigger,
        role_hint=role_hint,
    )


__all__ = ("DigestCard", "format_card")
