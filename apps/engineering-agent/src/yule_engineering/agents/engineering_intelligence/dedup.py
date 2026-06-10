"""Dedup logic for collected EngineeringKnowledgeItem instances.

Three concerns:

  1. Compute a stable :func:`compute_dedup_key` from
     (role + topic_key + normalized_url + normalized_title) so two
     collectors that find the same item produce the same key.
  2. Apply :func:`dedup_items` over a collected batch — drop
     duplicates by key, by url, by topic_key, by normalized title.
  3. Apply :func:`enforce_same_day_topic_uniqueness` — once we've
     stored an item for ``topic_key`` *today*, refuse to add another
     for the same topic on the same date.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Iterable, List, Mapping, Sequence, Tuple

from .models import EngineeringKnowledgeItem


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


_TRACKING_PARAMS = (
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref",
    "ref_src",
    "fbclid",
    "gclid",
)


def _normalize_url(raw: str) -> str:
    if not raw:
        return ""
    text = raw.strip().lower()
    if "#" in text:
        text = text.split("#", 1)[0]
    if "?" in text:
        head, _, tail = text.partition("?")
        kept_pairs: List[str] = []
        for pair in tail.split("&"):
            if not pair:
                continue
            key, _, _ = pair.partition("=")
            if key in _TRACKING_PARAMS:
                continue
            kept_pairs.append(pair)
        text = head + ("?" + "&".join(kept_pairs) if kept_pairs else "")
    text = re.sub(r"/+$", "", text)
    return text


def _normalize_title(raw: str) -> str:
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", raw).lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w가-힣 ]+", "", text)
    return text.strip()


def _date_only(iso_datetime: str) -> str:
    if not iso_datetime:
        return ""
    return iso_datetime.split("T", 1)[0]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_dedup_key(item: EngineeringKnowledgeItem) -> str:
    """Stable key for *item*.

    Uses (role, topic_key, normalized_url, normalized_title,
    sorted(stack_tags)) hashed with sha1 so the key is short enough
    for log lines / audit rows. Different items pointing at the same
    URL but different titles still collide — they describe the same
    source page.
    """

    parts = (
        item.role,
        item.topic_key,
        _normalize_url(item.source_url),
        _normalize_title(item.title),
        ",".join(sorted(item.stack_tags)),
    )
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"eng-knowledge:{item.role}:{digest}"


def dedup_items(
    items: Iterable[EngineeringKnowledgeItem],
) -> Tuple[Tuple[EngineeringKnowledgeItem, ...], Tuple[Mapping[str, str], ...]]:
    """Drop duplicates from *items*.

    Returns ``(kept, rejected)`` where ``rejected`` is a tuple of
    rejection reason payloads. The first occurrence of any
    (key | url | topic_key | normalized_title) wins; later collisions
    are recorded in ``rejected`` with the colliding identifier so
    the caller can surface the audit.
    """

    seen_keys: set[str] = set()
    seen_urls: set[str] = set()
    seen_topics: set[str] = set()
    seen_titles: set[str] = set()
    kept: List[EngineeringKnowledgeItem] = []
    rejected: List[Mapping[str, str]] = []

    for item in items:
        key = item.dedup_key or compute_dedup_key(item)
        url_n = _normalize_url(item.source_url)
        title_n = _normalize_title(item.title)
        topic = item.topic_key

        if key in seen_keys:
            rejected.append({"reason": "dedup_key_collision", "key": key})
            continue
        if url_n and url_n in seen_urls:
            rejected.append({"reason": "url_collision", "key": key, "url": url_n})
            continue
        if topic and topic in seen_topics:
            rejected.append({"reason": "topic_collision", "key": key, "topic_key": topic})
            continue
        if title_n and title_n in seen_titles:
            rejected.append({"reason": "title_collision", "key": key, "title": title_n})
            continue

        if not item.dedup_key:
            item = item.with_dedup_key(key)
        kept.append(item)
        seen_keys.add(key)
        if url_n:
            seen_urls.add(url_n)
        if topic:
            seen_topics.add(topic)
        if title_n:
            seen_titles.add(title_n)

    return tuple(kept), tuple(rejected)


def enforce_same_day_topic_uniqueness(
    candidates: Sequence[EngineeringKnowledgeItem],
    *,
    already_stored: Sequence[EngineeringKnowledgeItem] = (),
) -> Tuple[Tuple[EngineeringKnowledgeItem, ...], Tuple[Mapping[str, str], ...]]:
    """Refuse a candidate when a same-(date, topic) item was already saved.

    *already_stored* represents whatever the operator's persistence
    layer remembers for "today". Tests pass the most recent vault
    write list; production wires this to the memory indexer / vault
    listing.
    """

    blocked_pairs: set[Tuple[str, str]] = {
        (_date_only(s.collected_at), s.topic_key)
        for s in already_stored
        if s.topic_key
    }

    kept: List[EngineeringKnowledgeItem] = []
    rejected: List[Mapping[str, str]] = []

    seen_pairs_in_batch: set[Tuple[str, str]] = set()
    for item in candidates:
        date = _date_only(item.collected_at)
        topic = item.topic_key
        pair = (date, topic) if topic else None

        if pair is not None and pair in blocked_pairs:
            rejected.append(
                {
                    "reason": "same_day_topic_already_stored",
                    "topic_key": topic,
                    "date": date,
                }
            )
            continue
        if pair is not None and pair in seen_pairs_in_batch:
            rejected.append(
                {
                    "reason": "same_day_topic_duplicate_in_batch",
                    "topic_key": topic,
                    "date": date,
                }
            )
            continue
        kept.append(item)
        if pair is not None:
            seen_pairs_in_batch.add(pair)

    return tuple(kept), tuple(rejected)


__all__ = [
    "compute_dedup_key",
    "dedup_items",
    "enforce_same_day_topic_uniqueness",
]
