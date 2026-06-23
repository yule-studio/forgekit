"""Collect — wire the free-first ``nexus.sources`` collectors with tooling seeds.

This is the ONLY module here that does (optional) network IO, and it does NOT add a
new collector or fetcher abstraction: it reuses the existing ``nexus.sources``
collectors (GitHub / HN / Reddit / RSS, free-first) with *tooling-focused* query
seeds and the existing ``PlannedCollector`` seams (YouTube / Instagram / Google /
Figma-community / GeekNews — always empty, never fake live). The collected
``SourceItem`` s are handed to the pure extract → curate pipeline.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from ..sources import collectors as C
from ..sources import contract as SC
from ..sources.registry import SourceRegistry
from . import curate as CU
from . import extract as EX
from .candidate import ExternalCandidate

# tooling-focused search seeds (free). ONE collector per source type — combined
# queries (not N collectors), because the registry keys results by spec.id, so
# duplicate-id collectors would silently overwrite each other in collect_all.
# "+" = space (GitHub REST convention, matches default_registry seeds).
_GITHUB_SEED = "topic:mcp-server+OR+topic:claude-code+OR+topic:llm-tools+OR+topic:ai-agent"
_HN_SEED = "AI tooling OR devtools OR MCP OR agent"
_REDDIT_MULTI = "LocalLLaMA+devtools+selfhosted"  # reddit multi-subreddit syntax


def _planned(sid: str, label: str, stype: str, why: str):
    spec = SC.SourceSpec(sid, label, stype, cost_class=SC.COST_PAID, freshness="n/a",
                         trust_level="medium", ingest_method="future-adapter",
                         legal_note=why, status=SC.STATUS_PLANNED)
    return C.PlannedCollector(spec)


def intake_source_registry(
    repo_root,
    *,
    fetcher: Optional[C.Fetcher] = None,
    rss_feeds: Sequence[Tuple[str, str]] = (),
) -> SourceRegistry:
    """A free-first registry seeded for *tooling* discovery (not product ideas).

    LIVE (free-first): repo-local · GitHub tooling searches · HN tooling query ·
    Reddit tool subs · operator RSS. PLANNED (never fake): YouTube · Instagram ·
    paid Google · Figma community · GeekNews scraping.
    """

    reg = SourceRegistry()
    reg.register(C.RepoLocalCollector(repo_root))
    reg.register(C.github_collector(_GITHUB_SEED, fetcher))
    reg.register(C.hackernews_collector(_HN_SEED, fetcher))
    reg.register(C.reddit_collector(_REDDIT_MULTI, fetcher))
    for sid, url in rss_feeds:
        spec = SC.SourceSpec(sid, sid, SC.TYPE_RSS, cost_class=SC.COST_FREE,
                             freshness="daily", trust_level="medium",
                             ingest_method="rss", legal_note="operator-curated feed")
        reg.register(C.RssCollector(spec, url, fetcher))
    # planned seams — adapter/runbook only, ALWAYS empty, never fake live.
    reg.register(_planned("youtube", "YouTube", SC.TYPE_YOUTUBE,
                          "Data API 비용/쿼터 + ToS — 미연결"))
    reg.register(_planned("instagram", "Instagram", SC.TYPE_INSTAGRAM,
                          "Graph API 승인/ToS — 미연결"))
    reg.register(_planned("google", "Google search (paid)", SC.TYPE_GOOGLE,
                          "유료 search/API 비용 — 무료 소스 우선, 미연결"))
    reg.register(_planned("figma-community", "Figma community", "figma-community",
                          "community scraping ToS — planned seam, fake live 금지"))
    reg.register(_planned("geeknews", "GeekNews scrape", "geeknews",
                          "스크래핑 정책 — planned seam (RSS 가 있으면 RSS 로 대체)"))
    return reg


def collect_candidates(
    repo_root,
    *,
    fetcher: Optional[C.Fetcher] = None,
    rss_feeds: Sequence[Tuple[str, str]] = (),
    enrich: Sequence[ExternalCandidate] = (),
    limit_per: int = 10,
) -> Tuple[Tuple[ExternalCandidate, ...], Tuple[dict, ...]]:
    """Collect from the live tooling sources → extracted candidates + source status."""

    reg = intake_source_registry(repo_root, fetcher=fetcher, rss_feeds=rss_feeds)
    collected = reg.collect_all(limit_per=limit_per)
    items = [it for bucket in collected.values() for it in bucket]
    cands = EX.extract_candidates(items, enrich=enrich)
    return cands, reg.status_rows()


def run_intake(
    repo_root,
    *,
    fetcher: Optional[C.Fetcher] = None,
    rss_feeds: Sequence[Tuple[str, str]] = (),
    enrich: Sequence[ExternalCandidate] = (),
    blocklist_fingerprints: Sequence[str] = (),
    limit_per: int = 10,
) -> CU.IntakePacket:
    """Full lane: free-first collect → extract → curation gate → IntakePacket."""

    cands, status = collect_candidates(
        repo_root, fetcher=fetcher, rss_feeds=rss_feeds, enrich=enrich,
        limit_per=limit_per)
    return CU.curate_all(cands, blocklist_fingerprints=blocklist_fingerprints,
                         source_status=status)


__all__ = ("intake_source_registry", "collect_candidates", "run_intake")
