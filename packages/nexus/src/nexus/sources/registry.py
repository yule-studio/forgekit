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


# operator-tunable defaults — what the free-first sources collect by default. The
# operator overrides these (per their interests) via config; see registry_from_config.
DEFAULT_HN_QUERY = "forgekit OR devtools"
DEFAULT_SUBREDDITS: Tuple[str, ...] = ("SaaS",)
DEFAULT_GITHUB_QUERY = "operator+console+tui"
DEFAULT_GEEKNEWS = True   # GeekNews radar on by default (free RSS); operator can turn off.


def default_registry(
    repo_root,
    *,
    fetcher: Optional[C.Fetcher] = None,
    rss_feeds: Tuple[Tuple[str, str], ...] = (),
    hackernews_query: str = DEFAULT_HN_QUERY,
    subreddits: Tuple[str, ...] = DEFAULT_SUBREDDITS,
    github_query: str = DEFAULT_GITHUB_QUERY,
    geeknews: bool = DEFAULT_GEEKNEWS,
) -> SourceRegistry:
    """Build the default registry: live free/low sources + planned paid seams.

    LIVE: repo-local (offline) · Hacker News · GeekNews · Reddit(s) · GitHub · operator RSS.
    PLANNED (no live ingest, no fake): YouTube · Instagram · paid Google search.

    The HN query, GeekNews toggle, subreddit list, GitHub query and RSS feeds are
    operator-tunable so collection tracks the operator's interests (see
    :func:`registry_from_config`).
    """

    reg = SourceRegistry()
    # --- live, free-first ---
    reg.register(C.RepoLocalCollector(repo_root))
    if hackernews_query:
        reg.register(C.hackernews_collector(hackernews_query, fetcher))
    if geeknews:
        reg.register(C.geeknews_collector(fetcher=fetcher))
    for sub in subreddits:
        if sub:
            reg.register(C.reddit_collector(sub, fetcher))
    if github_query:
        reg.register(C.github_collector(github_query, fetcher))
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


def registry_from_config(
    repo_root,
    config: Optional[Mapping] = None,
    *,
    fetcher: Optional[C.Fetcher] = None,
) -> SourceRegistry:
    """Build the registry from operator config — so collection tracks THEIR interests.

    Reads the optional ``discovery`` block (all keys optional; sensible defaults):
      * ``hackernews_query`` (str) · ``subreddits`` (list[str]) · ``github_query`` (str)
      * ``rss_feeds`` (list of ``[id, url]`` pairs)
    An empty query/list simply drops that collector (no fake source). Unknown keys are
    ignored. Falls back to :func:`default_registry` defaults when ``discovery`` is absent.
    """

    disc = dict((config or {}).get("discovery", {}) or {})
    subs = disc.get("subreddits", DEFAULT_SUBREDDITS)
    feeds = tuple(tuple(pair) for pair in disc.get("rss_feeds", ()) if len(tuple(pair)) == 2)
    return default_registry(
        repo_root, fetcher=fetcher, rss_feeds=feeds,
        hackernews_query=str(disc.get("hackernews_query", DEFAULT_HN_QUERY) or ""),
        subreddits=tuple(s for s in subs if s),
        github_query=str(disc.get("github_query", DEFAULT_GITHUB_QUERY) or ""),
        geeknews=bool(disc.get("geeknews", DEFAULT_GEEKNEWS)),
    )


__all__ = (
    "SourceRegistry", "default_registry", "registry_from_config",
    "DEFAULT_HN_QUERY", "DEFAULT_SUBREDDITS", "DEFAULT_GITHUB_QUERY", "DEFAULT_GEEKNEWS",
)
