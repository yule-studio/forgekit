"""RSS / Atom feed live provider (F5 / #92).

XML 파싱은 stdlib ``xml.etree.ElementTree`` 만 사용 — 외부 의존 X.
실제 HTTP fetch 는 호출자가 주입한 ``http_fetch`` 콜러블이 담당하고,
본 provider 는 (1) 정책 가드 → (2) fetch → (3) 파싱 → (4) PasteGuard
정규화 → (5) :class:`LiveEvidence` 변환 까지를 책임진다.

Hard rails:
  * env ``YULE_RESEARCH_LIVE_ENABLED`` 가 ``true`` 가 아니면 fetch 호출
    자체를 skip 하고 빈 튜플 반환.
  * source ``allow_listed=False`` 또는 ``robots_compliant=False`` 면
    fetch skip.
  * fetch 결과 본문은 항상 :func:`guard_outbound` (channel=LLM) 으로
    redact 한 후 summary 로 노출 (raw HTML 절대 노출 금지).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Mapping, Optional, Sequence, Tuple

from ...security_compat import guard_text  # noqa: E402  (see module note)
from . import KIND_ATOM, KIND_RSS, LiveEvidence, LiveSource


HttpFetcher = Callable[[str], str]
"""url → body(str). Provider 는 본 콜러블에만 의존 — 실제 transport 는
주입측 책임. 테스트 / mock 은 in-memory dict 로 대체."""


@dataclass(frozen=True)
class RssAtomProvider:
    """RSS 2.0 / Atom 1.0 feed ingest provider.

    * ``sources`` — 이번 ingest 라운드의 :class:`LiveSource` 시퀀스.
    * ``http_fetch`` — url → body 콜러블. 비활성 시 None 가능.
    * ``env_enabled`` — ``YULE_RESEARCH_LIVE_ENABLED`` 결과 bool.
      False 면 어떤 source 도 fetch 하지 않는다.
    * ``max_entries_per_feed`` — 단일 feed 당 최대 항목 수 (default 10).
    """

    sources: Tuple[LiveSource, ...]
    http_fetch: Optional[HttpFetcher] = None
    env_enabled: bool = False
    max_entries_per_feed: int = 10

    name: str = "rss_atom"

    def ingest(self) -> Tuple[LiveEvidence, ...]:
        """정책 가드 후 모든 source 에서 evidence 를 수집해 반환.

        env OFF / allow-list 위반 / robots 위반 source 는 skip.
        fetch 실패 (예외) 한 source 도 skip — caller 로 예외 전파 X.
        """

        if not self.env_enabled or self.http_fetch is None:
            return ()

        out: list[LiveEvidence] = []
        for src in self.sources:
            if src.kind not in (KIND_RSS, KIND_ATOM):
                continue
            if not src.allow_listed or not src.robots_compliant:
                continue
            try:
                body = self.http_fetch(src.url or _url_for(src))
            except Exception:  # noqa: BLE001 - 외부 fetch 격리
                continue
            if not body:
                continue
            try:
                entries = parse_feed(body, kind=src.kind)
            except Exception:  # noqa: BLE001 - 파싱 실패 격리
                continue
            for entry in entries[: self.max_entries_per_feed]:
                out.append(_to_evidence(src, entry))
        return tuple(out)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeedEntry:
    """원시 feed entry — provider 내부 표현."""

    title: str
    url: str
    summary: str
    published_at: Optional[datetime]
    tags: Tuple[str, ...]


_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def parse_feed(body: str, *, kind: str) -> Tuple[FeedEntry, ...]:
    """RSS 2.0 / Atom 1.0 body 를 :class:`FeedEntry` 튜플로 파싱.

    잘못된 XML 은 :class:`ET.ParseError` 를 던지며, 호출자가 격리한다.
    """

    if kind not in (KIND_RSS, KIND_ATOM):
        raise ValueError(f"unsupported feed kind: {kind!r}")

    root = ET.fromstring(body)

    if kind == KIND_RSS:
        return _parse_rss(root)
    return _parse_atom(root)


def _parse_rss(root: ET.Element) -> Tuple[FeedEntry, ...]:
    channel = root.find("channel")
    if channel is None:
        return ()
    out: list[FeedEntry] = []
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()
        pub_raw = item.findtext("pubDate")
        published_at = _parse_rss_date(pub_raw) if pub_raw else None
        tags = tuple(
            (c.text or "").strip()
            for c in item.findall("category")
            if c.text
        )
        out.append(
            FeedEntry(
                title=title,
                url=url,
                summary=description,
                published_at=published_at,
                tags=tags,
            )
        )
    return tuple(out)


def _parse_atom(root: ET.Element) -> Tuple[FeedEntry, ...]:
    out: list[FeedEntry] = []
    for entry in root.findall(f"{_ATOM_NS}entry"):
        title = (entry.findtext(f"{_ATOM_NS}title") or "").strip()

        link_url = ""
        link_el = entry.find(f"{_ATOM_NS}link")
        if link_el is not None:
            link_url = (link_el.attrib.get("href") or "").strip()

        summary = (entry.findtext(f"{_ATOM_NS}summary") or "").strip()
        if not summary:
            summary = (entry.findtext(f"{_ATOM_NS}content") or "").strip()

        updated_raw = entry.findtext(f"{_ATOM_NS}updated") or entry.findtext(
            f"{_ATOM_NS}published"
        )
        published_at = _parse_atom_date(updated_raw) if updated_raw else None

        tags = tuple(
            (c.attrib.get("term") or "").strip()
            for c in entry.findall(f"{_ATOM_NS}category")
            if c.attrib.get("term")
        )

        out.append(
            FeedEntry(
                title=title,
                url=link_url,
                summary=summary,
                published_at=published_at,
                tags=tags,
            )
        )
    return tuple(out)


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """tag 제거 + 연속 공백 정리. raw HTML 노출 방지용."""

    if not text:
        return ""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", text)).strip()


def _to_evidence(src: LiveSource, entry: FeedEntry) -> LiveEvidence:
    """:class:`FeedEntry` → :class:`LiveEvidence`. PasteGuard 통과."""

    safe_title = guard_text(entry.title)
    safe_summary = guard_text(_strip_html(entry.summary))
    return LiveEvidence(
        source=src,
        title=safe_title,
        url=entry.url,
        summary=safe_summary,
        published_at=entry.published_at,
        tags=entry.tags,
        extra={"kind": src.kind},
    )


def _parse_rss_date(raw: str) -> Optional[datetime]:
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_atom_date(raw: str) -> Optional[datetime]:
    raw = raw.strip()
    if not raw:
        return None
    # Python 3.9 의 fromisoformat 은 'Z' 를 못 읽으므로 보정.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _url_for(src: LiveSource) -> str:
    if src.url:
        return src.url
    # 기본은 https://host (구체적 path 는 caller 가 url 로 명시 주입).
    return f"https://{src.host}"


__all__ = (
    "FeedEntry",
    "HttpFetcher",
    "RssAtomProvider",
    "parse_feed",
)
