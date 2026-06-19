"""Source contract — a vendor-neutral spec for where signals come from (WT2).

Cost-first by design: free / low-cost sources (GitHub, repo-local, Reddit, RSS, HN)
are the live tier; paid / ToS-heavy sources (YouTube, Instagram, paid Google) are
``planned`` seams that must NEVER fake live data. Each source declares its cost
class, trust, freshness, ingest method, and a legal/policy note so the operator can
see exactly what is live and why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

# cost class — drives the "free first" ordering -------------------------------
COST_FREE = "free"
COST_LOW = "low"
COST_PAID = "paid"
COST_UNKNOWN = "unknown"
_COST_ORDER = {COST_FREE: 0, COST_LOW: 1, COST_UNKNOWN: 2, COST_PAID: 3}

# live status — planned/not-live sources must not return fake data ------------
STATUS_LIVE = "live"
STATUS_PLANNED = "planned"
STATUS_NOT_LIVE = "not_live"

# source types ----------------------------------------------------------------
TYPE_GITHUB = "github"
TYPE_REPO_LOCAL = "repo-local"
TYPE_REDDIT = "reddit"
TYPE_RSS = "rss"
TYPE_HACKERNEWS = "hackernews"
TYPE_OPERATOR_CURATED = "operator-curated"
TYPE_YOUTUBE = "youtube"
TYPE_INSTAGRAM = "instagram"
TYPE_GOOGLE = "google"


@dataclass(frozen=True)
class SourceSpec:
    """What a source IS — cost / trust / freshness / ingest / legal / live status."""

    id: str
    label: str
    source_type: str
    cost_class: str = COST_UNKNOWN
    freshness: str = "unknown"        # e.g. realtime / daily / on-demand
    trust_level: str = "medium"       # high / medium / low
    ingest_method: str = ""           # api / rss / scrape / manual / repo-scan
    legal_note: str = ""              # ToS / rate-limit / attribution note
    status: str = STATUS_LIVE

    @property
    def is_live(self) -> bool:
        return self.status == STATUS_LIVE

    @property
    def cost_rank(self) -> int:
        return _COST_ORDER.get(self.cost_class, 9)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label, "source_type": self.source_type,
            "cost_class": self.cost_class, "freshness": self.freshness,
            "trust_level": self.trust_level, "ingest_method": self.ingest_method,
            "legal_note": self.legal_note, "status": self.status,
        }


@dataclass(frozen=True)
class SourceItem:
    """One collected signal (vendor-neutral)."""

    source_id: str
    title: str
    url: str = ""
    summary: str = ""
    kind: str = "signal"     # signal / repo / issue / post / feed-entry / gap
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id, "title": self.title, "url": self.url,
            "summary": self.summary, "kind": self.kind, "score": self.score,
        }


__all__ = (
    "COST_FREE", "COST_LOW", "COST_PAID", "COST_UNKNOWN",
    "STATUS_LIVE", "STATUS_PLANNED", "STATUS_NOT_LIVE",
    "TYPE_GITHUB", "TYPE_REPO_LOCAL", "TYPE_REDDIT", "TYPE_RSS", "TYPE_HACKERNEWS",
    "TYPE_OPERATOR_CURATED", "TYPE_YOUTUBE", "TYPE_INSTAGRAM", "TYPE_GOOGLE",
    "SourceSpec", "SourceItem",
)
