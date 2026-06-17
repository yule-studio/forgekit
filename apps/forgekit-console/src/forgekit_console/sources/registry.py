"""Source registry — what's live (free-first) vs planned, with health surfaced.

The registry holds collectors keyed by spec id, partitions them into LIVE vs
PLANNED, orders the live ones cost-first (free → low → paid), and collects only
from live sources (planned ones return nothing — never fake data). It is the single
operator-facing answer to "where do signals come from and which are actually on".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple

from . import collectors as C
from .contract import (
    STATUS_LIVE,
    STATUS_NOT_LIVE,
    STATUS_PLANNED,
    SourceItem,
    SourceSpec,
    TYPE_GOOGLE,
    TYPE_INSTAGRAM,
    TYPE_RSS,
    TYPE_YOUTUBE,
    COST_PAID,
    COST_UNKNOWN,
)


@dataclass
class SourceRegistry:
    """A registry of source collectors with live/planned partition + cost order."""

    collectors: List[object] = field(default_factory=list)

    def register(self, collector) -> None:
        self.collectors.append(collector)

    def specs(self) -> Tuple[SourceSpec, ...]:
        return tuple(c.spec for c in self.collectors)

    def live(self) -> Tuple[object, ...]:
        return tuple(c for c in self.collectors if c.spec.is_live)

    def planned(self) -> Tuple[object, ...]:
        return tuple(c for c in self.collectors if not c.spec.is_live)

    def cost_ordered_live(self) -> Tuple[object, ...]:
        """Live collectors, free-cost first (the 'no-cost source first' policy)."""

        return tuple(sorted(self.live(), key=lambda c: c.spec.cost_rank))

    def collect_all(self, *, limit_per: int = 10) -> Dict[str, List[SourceItem]]:
        """Collect from LIVE sources only (free-first). Planned → skipped (no fake)."""

        out: Dict[str, List[SourceItem]] = {}
        for collector in self.cost_ordered_live():
            try:
                out[collector.spec.id] = collector.collect(limit=limit_per)
            except Exception:  # noqa: BLE001 - a flaky source never breaks the sweep
                out[collector.spec.id] = []
        return out

    def status_rows(self) -> Tuple[dict, ...]:
        rows = []
        for c in self.collectors:
            s = c.spec
            rows.append({
                "id": s.id, "type": s.source_type, "cost": s.cost_class,
                "status": s.status, "trust": s.trust_level, "ingest": s.ingest_method,
                "legal": s.legal_note,
            })
        return tuple(rows)

    def to_dict(self) -> dict:
        return {
            "live": [c.spec.to_dict() for c in self.cost_ordered_live()],
            "planned": [c.spec.to_dict() for c in self.planned()],
        }


# planned (NOT live) seams — adapter + runbook only, never fake data.
def _planned_spec(sid: str, label: str, stype: str, why: str) -> SourceSpec:
    return SourceSpec(sid, label, stype, cost_class=COST_PAID, freshness="n/a",
                      trust_level="medium", ingest_method="future-adapter",
                      legal_note=why, status=STATUS_PLANNED)


def default_registry(
    repo_root,
    *,
    fetcher: Optional[C.Fetcher] = None,
    rss_feeds: Tuple[Tuple[str, str], ...] = (),
) -> SourceRegistry:
    """Build the default registry: live free/low sources + planned paid seams.

    LIVE: repo-local (offline) · Hacker News · Reddit · GitHub · any operator RSS.
    PLANNED (no live ingest, no fake): YouTube · Instagram · paid Google search.
    """

    reg = SourceRegistry()
    # --- live, free-first ---
    reg.register(C.RepoLocalCollector(repo_root))
    reg.register(C.hackernews_collector("forgekit OR devtools", fetcher))
    reg.register(C.reddit_collector("SaaS", fetcher))
    reg.register(C.github_collector("operator+console+tui", fetcher))
    for sid, url in rss_feeds:
        spec = SourceSpec(sid, sid, TYPE_RSS, cost_class="free", freshness="daily",
                          trust_level="medium", ingest_method="rss",
                          legal_note="operator-curated feed")
        reg.register(C.RssCollector(spec, url, fetcher))
    # --- planned seams (status=planned; never fake live) ---
    reg.register(C.PlannedCollector(_planned_spec(
        "youtube", "YouTube", TYPE_YOUTUBE,
        "Data API 비용/쿼터 + ToS — 이번 단계 미연결, video-watch 는 수동 ingest")))
    reg.register(C.PlannedCollector(_planned_spec(
        "instagram", "Instagram", TYPE_INSTAGRAM,
        "Graph API 승인/ToS 제약 — 미연결")))
    reg.register(C.PlannedCollector(_planned_spec(
        "google", "Google search (paid)", TYPE_GOOGLE,
        "유료 search/API 비용 — 미연결, 무료 소스 우선")))
    return reg


__all__ = ("SourceRegistry", "default_registry")
