"""F13 — RSS 크롤러. 카탈로그 host 만 fetch + dedup-aware.

사용자 design (2026-05-12):
> "검색 API 없이도 가능하지만, '전문 크롤러' 보다 '소스 어댑터' 구조가 좋습니다."
> "소스별로 ``RSS 가능``, ``목록 페이지 HTML 파싱``, ``상세 페이지 메타 추출``
> 3단계 우선순위로 접근합니다."

본 PR 은 RSS/Atom + GitHub Release 만 (1순위). HTML list 파서는 후속 PR.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from .dedup_ledger import DigestDedupLedger
from .dept_router import DeptClassification, classify_evidence
from .formatter import DigestCard, format_card
from .source_catalog import (
    AuthoritativeSource,
    all_allowed_hosts,
    sources_for_role,
)


# robots.txt / rate-limit / timeout 정책
_DEFAULT_TIMEOUT = 15
_USER_AGENT = "yule-engineering-agent-digest/0.1"


@dataclass(frozen=True)
class CrawlOutcome:
    """1회 fetch 의 결과."""

    role: str
    source_host: str
    entries_fetched: int
    cards: Tuple[DigestCard, ...]
    skipped_duplicates: int
    blocker_reason: Optional[str] = None


class HttpPoster:
    """fetch seam. 테스트는 fake 주입."""

    def fetch(self, url: str, *, timeout: int = _DEFAULT_TIMEOUT) -> str:
        req = urllib.request.Request(
            url=url,
            headers={"User-Agent": _USER_AGENT, "Accept": "*/*"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")


def _parse_rss_atom(xml_text: str) -> list:
    """RSS 2.0 + Atom feed 파서. 본 PR 은 표준 라이브러리만 사용 (feedparser 의존 회피).

    Return: list of {title, link, summary, published, tags}.
    """

    items: list = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "dc": "http://purl.org/dc/elements/1.1/",
        "content": "http://purl.org/rss/1.0/modules/content/",
    }
    # RSS 2.0
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or item.findtext("content:encoded", "", ns) or "").strip()
        pub = (item.findtext("pubDate") or item.findtext("dc:date", "", ns) or "").strip()
        tags = [c.text for c in item.findall("category") if c.text]
        if title and link:
            items.append({
                "title": title, "link": link, "summary": desc, "published": pub, "tags": tags,
            })
    # Atom
    if not items:
        for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title_el = entry.find("atom:title", ns)
            link_el = entry.find("atom:link", ns)
            summary_el = entry.find("atom:summary", ns) or entry.find("atom:content", ns)
            updated_el = entry.find("atom:updated", ns) or entry.find("atom:published", ns)
            link = link_el.get("href") if link_el is not None else ""
            title = (title_el.text or "").strip() if title_el is not None else ""
            summary = (summary_el.text or "").strip() if summary_el is not None else ""
            published = (updated_el.text or "").strip() if updated_el is not None else ""
            cat_els = entry.findall("atom:category", ns)
            tags = [c.get("term") for c in cat_els if c.get("term")]
            if title and link:
                items.append({
                    "title": title, "link": link, "summary": summary,
                    "published": published, "tags": tags,
                })
    return items


def _parse_github_releases(json_text: str) -> list:
    """GitHub Releases API → entry list."""

    try:
        releases = json.loads(json_text)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(releases, list):
        return []
    items: list = []
    for rel in releases[:10]:  # cap 10
        if not isinstance(rel, dict):
            continue
        if rel.get("draft") or rel.get("prerelease"):
            continue
        tag = rel.get("tag_name") or rel.get("name") or "release"
        body = (rel.get("body") or "").strip()
        url = rel.get("html_url") or ""
        published = rel.get("published_at") or rel.get("created_at") or ""
        if not url:
            continue
        items.append({
            "title": tag,
            "link": url,
            "summary": body[:400],
            "published": published,
            "tags": [],
        })
    return items


def _parse_published(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    # 표준 ISO 8601
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(raw[:32], fmt)
        except ValueError:
            continue
    # RFC 2822 (RSS pubDate)
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(raw)
    except Exception:  # noqa: BLE001
        return None


def fetch_source(
    source: AuthoritativeSource,
    *,
    role: str,
    ledger: DigestDedupLedger,
    http_poster: Optional[HttpPoster] = None,
    max_cards_per_source: int = 5,
) -> CrawlOutcome:
    """단일 source 에서 RSS/Atom/GitHub release 1회 fetch + dedup 적용."""

    if source.host not in all_allowed_hosts():
        return CrawlOutcome(role, source.host, 0, (), 0, blocker_reason="not in allow-list")

    poster = http_poster or HttpPoster()
    cards: list = []
    skipped = 0

    try:
        if source.kind == "github_release":
            # ``feed_url`` = ``owner/repo``
            api_url = f"https://api.github.com/repos/{source.feed_url}/releases"
            raw = poster.fetch(api_url)
            entries = _parse_github_releases(raw)
        else:
            raw = poster.fetch(source.feed_url)
            entries = _parse_rss_atom(raw)
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        return CrawlOutcome(
            role, source.host, 0, (), 0,
            blocker_reason=f"http {type(exc).__name__}",
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, never crash supervisor
        return CrawlOutcome(
            role, source.host, 0, (), 0,
            blocker_reason=f"unexpected {type(exc).__name__}: {str(exc)[:60]}",
        )

    for entry in entries[:max_cards_per_source]:
        verdict = classify_evidence(
            host=source.host,
            title=entry.get("title", ""),
            summary=entry.get("summary", ""),
            primary_role=role,
        )
        if ledger.is_duplicate(
            url=entry["link"],
            title=entry["title"],
            host=source.host,
            dept=verdict.primary,
        ):
            skipped += 1
            continue
        card = format_card(
            title=entry["title"],
            url=entry["link"],
            summary=entry.get("summary", ""),
            source_host=source.host,
            published_at=_parse_published(entry.get("published", "")),
            tags=entry.get("tags", ()),
            dept_primary=verdict.primary,
            affected_depts=verdict.affected,
            meeting_trigger=verdict.meeting_trigger,
            role_hint=role,
        )
        cards.append(card)

    return CrawlOutcome(
        role=role,
        source_host=source.host,
        entries_fetched=len(entries),
        cards=tuple(cards),
        skipped_duplicates=skipped,
    )


def crawl_role(
    role: str,
    *,
    ledger: DigestDedupLedger,
    http_poster: Optional[HttpPoster] = None,
    max_cards_per_source: int = 5,
) -> Tuple[CrawlOutcome, ...]:
    """역할 카탈로그의 모든 source 1회 순회."""

    outcomes: list = []
    for source in sources_for_role(role):
        outcomes.append(
            fetch_source(
                source,
                role=role,
                ledger=ledger,
                http_poster=http_poster,
                max_cards_per_source=max_cards_per_source,
            )
        )
    return tuple(outcomes)


__all__ = (
    "CrawlOutcome",
    "HttpPoster",
    "crawl_role",
    "fetch_source",
)
