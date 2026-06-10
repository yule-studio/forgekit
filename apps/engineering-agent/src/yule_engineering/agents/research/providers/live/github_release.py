"""GitHub release live provider (F5 / #92).

GitHub repo 의 release 메타데이터를 read-only 로 수집해 :class:`LiveEvidence`
로 정규화한다. 본 provider 는 GitHub REST API (또는 Atom feed) 양쪽 모두
지원할 수 있도록 ``release_fetch`` 콜러블에만 의존한다 — 실제 transport
선택은 caller 책임이다.

Hard rails:
  * env ``YULE_RESEARCH_LIVE_ENABLED`` 가 ``true`` 가 아니면 fetch skip.
  * source ``allow_listed=False`` / ``robots_compliant=False`` 면 skip.
  * release body 는 raw markdown 그대로 노출하지 않고 PasteGuard
    redacted summary 로 변환.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping, Optional, Sequence, Tuple

from ...security_compat import guard_text
from . import KIND_GITHUB_RELEASE, LiveEvidence, LiveSource


# release_fetch(repo: str) → 정규화된 dict 시퀀스.
# 각 dict 는 다음 키를 가진다:
#   - "tag_name": str (e.g. "v1.2.3")
#   - "name": str (release title, optional)
#   - "html_url": str
#   - "published_at": str (ISO8601, optional)
#   - "body": str (release notes markdown, optional)
ReleaseFetcher = Callable[[str], Sequence[Mapping[str, str]]]


@dataclass(frozen=True)
class GithubReleaseProvider:
    """GitHub release ingest provider.

    * ``sources`` — :data:`LiveSource.kind == "github_release"` 만 다룸.
      ``source.url`` 에는 ``"owner/repo"`` 형태의 repo slug 를 담는다
      (예: ``"fastapi/fastapi"``).
    * ``release_fetch`` — repo slug → release dict 시퀀스.
    * ``env_enabled`` — ``YULE_RESEARCH_LIVE_ENABLED`` bool.
    * ``max_releases_per_repo`` — repo 당 최대 release 수 (default 5).
    """

    sources: Tuple[LiveSource, ...]
    release_fetch: Optional[ReleaseFetcher] = None
    env_enabled: bool = False
    max_releases_per_repo: int = 5

    name: str = "github_release"

    def ingest(self) -> Tuple[LiveEvidence, ...]:
        if not self.env_enabled or self.release_fetch is None:
            return ()

        out: list[LiveEvidence] = []
        for src in self.sources:
            if src.kind != KIND_GITHUB_RELEASE:
                continue
            if not src.allow_listed or not src.robots_compliant:
                continue
            repo_slug = (src.url or "").strip()
            if not repo_slug or "/" not in repo_slug:
                continue
            try:
                releases = self.release_fetch(repo_slug)
            except Exception:  # noqa: BLE001 - 외부 fetch 격리
                continue
            for raw in list(releases)[: self.max_releases_per_repo]:
                ev = _to_evidence(src, raw, repo_slug)
                if ev is not None:
                    out.append(ev)
        return tuple(out)


def _to_evidence(
    src: LiveSource,
    raw: Mapping[str, str],
    repo_slug: str,
) -> Optional[LiveEvidence]:
    """release dict → :class:`LiveEvidence`. PasteGuard 통과."""

    tag = (raw.get("tag_name") or "").strip()
    name = (raw.get("name") or "").strip() or tag
    if not tag and not name:
        return None
    title = guard_text(f"{repo_slug} {name or tag}".strip())
    url = (raw.get("html_url") or "").strip() or (
        f"https://github.com/{repo_slug}/releases/tag/{tag}" if tag else ""
    )
    body = (raw.get("body") or "").strip()
    summary = guard_text(_shorten(body, limit=400))
    published_at = _parse_iso(raw.get("published_at"))
    return LiveEvidence(
        source=src,
        title=title,
        url=url,
        summary=summary,
        published_at=published_at,
        tags=(tag,) if tag else (),
        extra={"repo": repo_slug, "tag": tag},
    )


def _shorten(text: str, *, limit: int) -> str:
    if not text:
        return ""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"


def _parse_iso(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    raw = raw.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


__all__ = (
    "GithubReleaseProvider",
    "ReleaseFetcher",
)
