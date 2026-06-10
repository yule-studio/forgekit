"""F14 PreambleBuilder — 공유 컨텍스트 1회 해석.

gstack 의 `scripts/resolvers/preamble.ts` 패턴 차용. agent spawn prompt 가
매번 conventions / governance / role profile 5 파일을 re-read 하는 비용을
1회로 압축.

핵심 hard rails:
  - 파일 mtime 기반 invalidation — 정책 갱신 시 자동 재빌드.
  - PasteGuard 통과 — secret 포함된 policy 파일 redact (caller 책임).
  - section 단위 truncation — 160KB ceiling (F14 commit 4 가 가드).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple


_REPO_ROOT = Path(__file__).resolve().parents[6]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreambleSection:
    """한 정책 파일 → preamble 의 한 섹션."""

    title: str
    path: str  # repo-relative
    body: str
    mtime: float
    size_bytes: int

    def short_fingerprint(self) -> str:
        """파일 무결성 ID — 8자 sha1."""

        return hashlib.sha1(self.body.encode("utf-8")).hexdigest()[:8]


@dataclass(frozen=True)
class Preamble:
    """해석 완료된 preamble. 모든 agent prompt 의 prefix 로 사용 가능."""

    sections: Tuple[PreambleSection, ...]
    built_at_iso: str
    total_size_bytes: int

    def render_markdown(self, *, max_section_chars: int = 4000) -> str:
        """모든 섹션을 하나의 markdown 으로. 섹션당 max_section_chars 절단.

        max_section_chars 는 토큰 ceiling 가드 (≈ 16KB total 목표).
        """

        lines: list = ["# agent-preamble (auto-generated, shared cache)"]
        lines.append("")
        for s in self.sections:
            lines.append(f"## {s.title}")
            lines.append(f"_source: `{s.path}` · {s.size_bytes} bytes · {s.short_fingerprint()}_")
            body = s.body.strip()
            if len(body) > max_section_chars:
                body = body[:max_section_chars] + "\n\n...(truncated by preamble builder)"
            lines.append(body)
            lines.append("")
        return "\n".join(lines)

    def manifest(self) -> Mapping[str, str]:
        """fingerprint 매트릭스 — 캐시 invalidation 비교용."""

        return {s.path: s.short_fingerprint() for s in self.sections}


# ---------------------------------------------------------------------------
# Default source matrix — gstack-style "what every agent should see".
# ---------------------------------------------------------------------------


_DEFAULT_SOURCES: Tuple[Tuple[str, str], ...] = (
    (
        "Issue / PR / Obsidian / Auto-merge 컨벤션",
        "policies/runtime/agents/engineering-agent/issue-pr-conventions.md",
    ),
    (
        "Engineering Agent Governance",
        "policies/runtime/agents/engineering-agent/governance.md",
    ),
    (
        "Write Ownership Policy",
        "policies/runtime/agents/engineering-agent/write-ownership.md",
    ),
    (
        "GitHub Workflow Policy",
        "policies/runtime/agents/engineering-agent/github-workflow.md",
    ),
    (
        "Obsidian Governance",
        "policies/runtime/agents/engineering-agent/obsidian-governance.md",
    ),
)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class PreambleBuilder:
    """매 spawn 마다 5 파일 read → PreambleCache 가 이 결과를 hold.

    Builder 자체는 stateless — caller (PreambleCache) 가 mtime 비교로
    재빌드 결정.
    """

    def __init__(
        self,
        *,
        repo_root: Optional[Path] = None,
        sources: Optional[Sequence[Tuple[str, str]]] = None,
    ) -> None:
        self._repo_root = Path(repo_root) if repo_root else _REPO_ROOT
        self._sources = tuple(sources) if sources is not None else _DEFAULT_SOURCES

    def build(self) -> Preamble:
        """5 파일 1회 read → Preamble 인스턴스."""

        from datetime import datetime, timezone

        sections: list = []
        total = 0
        for title, rel_path in self._sources:
            path = self._repo_root / rel_path
            if not path.is_file():
                # 정책 파일 누락 — 빈 섹션 (caller 가 governance test 로 가드)
                sections.append(
                    PreambleSection(
                        title=title, path=rel_path, body=f"_(missing: {rel_path})_",
                        mtime=0.0, size_bytes=0,
                    )
                )
                continue
            body = path.read_text(encoding="utf-8")
            size = len(body.encode("utf-8"))
            sections.append(
                PreambleSection(
                    title=title,
                    path=rel_path,
                    body=body,
                    mtime=path.stat().st_mtime,
                    size_bytes=size,
                )
            )
            total += size

        return Preamble(
            sections=tuple(sections),
            built_at_iso=datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat(),
            total_size_bytes=total,
        )

    @property
    def sources(self) -> Tuple[Tuple[str, str], ...]:
        return self._sources


def build_default_preamble(repo_root: Optional[Path] = None) -> Preamble:
    """1회용 헬퍼 — cache 미사용 시 (테스트 / one-shot CLI)."""

    return PreambleBuilder(repo_root=repo_root).build()


__all__ = (
    "Preamble",
    "PreambleBuilder",
    "PreambleSection",
    "build_default_preamble",
)
