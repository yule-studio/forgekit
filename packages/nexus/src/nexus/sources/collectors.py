"""Collectors — turn a source into :class:`SourceItem`s. Cost-free tier first.

* **repo-local** — fully offline, scans the repo for governance/doc/TODO gaps. The
  genuinely zero-cost live collector (no network), so it's exercised directly in CI.
* **GitHub / Reddit / RSS / Hacker News** — real, but network IO is via an injectable
  ``fetcher`` (default urllib); tests pass a fake fetcher so parsing is verified
  without the network. cost_class=free/low.
* **YouTube / Instagram / paid Google** — :class:`PlannedCollector` seams: they
  ALWAYS return ``[]`` and carry ``status=planned`` + a runbook note. They never
  fabricate live data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .contract import (
    COST_FREE,
    COST_LOW,
    STATUS_PLANNED,
    SourceItem,
    SourceSpec,
    TYPE_GEEKNEWS,
    TYPE_GITHUB,
    TYPE_HACKERNEWS,
    TYPE_REDDIT,
    TYPE_REPO_LOCAL,
    TYPE_RSS,
)

# GeekNews (news.hada.io) — Korean HN-like dev/startup radar; public RSS, free.
GEEKNEWS_FEED = "https://feeds.hada.io/rss/news"

Fetcher = Callable[[str], str]


def _urllib_fetcher(url: str) -> str:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "forgekit-sources/0.1"})
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - https sources
        return resp.read().decode("utf-8", errors="replace")


# --- repo-local (offline, real) ---------------------------------------------
class RepoLocalCollector:
    """Scan the repo for improvement signals (TODO/FIXME, large files). Offline."""

    spec = SourceSpec(
        id="repo-local", label="Repo-local scan", source_type=TYPE_REPO_LOCAL,
        cost_class=COST_FREE, freshness="on-demand", trust_level="high",
        ingest_method="repo-scan", legal_note="own repo — no external call",
    )

    def __init__(self, repo_root) -> None:
        self.repo_root = Path(repo_root)

    def collect(self, *, limit: int = 20) -> List[SourceItem]:
        items: List[SourceItem] = []
        src = self.repo_root / "apps"
        roots = [src] if src.exists() else [self.repo_root]
        for root in roots:
            for path in sorted(root.rglob("*.py")):
                if len(items) >= limit:
                    return items
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                rel = path.relative_to(self.repo_root)
                todos = text.count("TODO") + text.count("FIXME")
                nlines = text.count("\n") + 1
                if todos >= 3:
                    items.append(SourceItem(
                        "repo-local", f"{rel}: {todos} TODO/FIXME 누적", kind="gap",
                        summary="누적 TODO/FIXME — 정리/분리 후보", score=float(todos)))
                elif nlines > 1000:
                    items.append(SourceItem(
                        "repo-local", f"{rel}: {nlines} 줄 (분리 검토)", kind="gap",
                        summary="1000 줄 초과 — 책임 분리 검토(가드레일)", score=nlines / 1000.0))
        return items


# --- generic network collectors (injectable fetcher) ------------------------
class RssCollector:
    """Parse an RSS/Atom feed → items (stdlib xml). Network via *fetcher*."""

    def __init__(self, spec: SourceSpec, url: str, fetcher: Optional[Fetcher] = None) -> None:
        self.spec = spec
        self.url = url
        self._fetch = fetcher or _urllib_fetcher

    def collect(self, *, limit: int = 20) -> List[SourceItem]:
        import xml.etree.ElementTree as ET

        try:
            raw = self._fetch(self.url)
            root = ET.fromstring(raw)
        except Exception:  # noqa: BLE001 - unreachable / malformed → empty (honest)
            return []
        items: List[SourceItem] = []
        # RSS <item> and Atom <entry>
        for node in root.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag not in ("item", "entry"):
                continue
            title = link = ""
            for child in node:
                ctag = child.tag.rsplit("}", 1)[-1]
                if ctag == "title":
                    title = (child.text or "").strip()
                elif ctag == "link":
                    link = (child.text or child.get("href") or "").strip()
            if title:
                items.append(SourceItem(self.spec.id, title, url=link, kind="feed-entry"))
            if len(items) >= limit:
                break
        return items


class JsonListCollector:
    """Fetch JSON and map it to items via *extract* (for HN/Reddit/GitHub APIs)."""

    def __init__(self, spec: SourceSpec, url: str, extract: Callable[[dict], List[SourceItem]],
                 fetcher: Optional[Fetcher] = None) -> None:
        self.spec = spec
        self.url = url
        self._extract = extract
        self._fetch = fetcher or _urllib_fetcher

    def collect(self, *, limit: int = 20) -> List[SourceItem]:
        try:
            data = json.loads(self._fetch(self.url))
        except Exception:  # noqa: BLE001 - unreachable / bad json → empty (honest)
            return []
        try:
            return self._extract(data)[:limit]
        except Exception:  # noqa: BLE001
            return []


def _hn_extract(data: dict) -> List[SourceItem]:
    out = []
    for hit in data.get("hits", []):
        title = hit.get("title") or hit.get("story_title") or ""
        if title:
            out.append(SourceItem("hackernews", title, url=hit.get("url", "") or "",
                                  kind="post", score=float(hit.get("points", 0) or 0)))
    return out


def _reddit_extract(data: dict) -> List[SourceItem]:
    out = []
    for child in (data.get("data", {}) or {}).get("children", []):
        d = child.get("data", {})
        title = d.get("title", "")
        if title:
            out.append(SourceItem("reddit", title, url="https://reddit.com" + d.get("permalink", ""),
                                  kind="post", score=float(d.get("ups", 0) or 0)))
    return out


def _github_extract(data: dict) -> List[SourceItem]:
    out = []
    for repo in data.get("items", []):
        full = repo.get("full_name", "")
        if full:
            out.append(SourceItem("github", full, url=repo.get("html_url", ""),
                                  kind="repo", summary=(repo.get("description") or "")[:120],
                                  score=float(repo.get("stargazers_count", 0) or 0)))
    return out


def hackernews_collector(query: str, fetcher: Optional[Fetcher] = None) -> JsonListCollector:
    spec = SourceSpec("hackernews", "Hacker News", TYPE_HACKERNEWS, cost_class=COST_FREE,
                      freshness="hourly", trust_level="medium", ingest_method="api",
                      legal_note="Algolia HN API — free, attribution friendly")
    url = f"https://hn.algolia.com/api/v1/search?query={query}&tags=story"
    return JsonListCollector(spec, url, _hn_extract, fetcher)


def reddit_collector(subreddit: str, fetcher: Optional[Fetcher] = None) -> JsonListCollector:
    spec = SourceSpec("reddit", f"r/{subreddit}", TYPE_REDDIT, cost_class=COST_FREE,
                      freshness="realtime", trust_level="low", ingest_method="public-json",
                      legal_note="public .json — respect rate limits / no auth-walled data")
    url = f"https://www.reddit.com/r/{subreddit}/top.json?limit=20"
    return JsonListCollector(spec, url, _reddit_extract, fetcher)


def geeknews_collector(feed: str = GEEKNEWS_FEED, fetcher: Optional[Fetcher] = None) -> "RssCollector":
    """GeekNews (news.hada.io) — free dev/startup radar via its public RSS feed.

    A signal/radar source (like HN/Reddit): parsed with the stdlib RSS collector, so a
    fake fetcher exercises parsing offline and a missing feed yields [] (honest, no fake).
    """

    spec = SourceSpec("geeknews", "GeekNews", TYPE_GEEKNEWS, cost_class=COST_FREE,
                      freshness="daily", trust_level="medium", ingest_method="rss",
                      legal_note="news.hada.io 공개 RSS — 무료, 출처 표기 권장")
    return RssCollector(spec, feed, fetcher)


def github_collector(query: str, fetcher: Optional[Fetcher] = None) -> JsonListCollector:
    spec = SourceSpec("github", "GitHub search", TYPE_GITHUB, cost_class=COST_LOW,
                      freshness="on-demand", trust_level="high", ingest_method="api",
                      legal_note="GitHub REST search — unauth rate-limited; respect ToS")
    url = f"https://api.github.com/search/repositories?q={query}&sort=stars&per_page=20"
    return JsonListCollector(spec, url, _github_extract, fetcher)


# --- planned seams (NEVER fake live) -----------------------------------------
class PlannedCollector:
    """A not-yet-live source seam — ALWAYS returns []. Never fabricates data."""

    def __init__(self, spec: SourceSpec) -> None:
        self.spec = spec

    def collect(self, *, limit: int = 20) -> List[SourceItem]:
        return []  # planned: no live ingestion, no fake data — honest empty


__all__ = (
    "Fetcher", "RepoLocalCollector", "RssCollector", "JsonListCollector",
    "PlannedCollector", "hackernews_collector", "reddit_collector", "github_collector",
    "geeknews_collector", "GEEKNEWS_FEED",
)
