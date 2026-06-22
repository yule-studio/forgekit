"""Sources (WT2) — vendor-neutral source registry + no-cost-first collectors.

Live (free/low): repo-local · Hacker News · Reddit · GitHub · operator RSS.
Planned (adapter+runbook only, never fake live): YouTube · Instagram · paid Google.
"""

from __future__ import annotations

from .contract import (
    COST_FREE,
    COST_LOW,
    COST_PAID,
    STATUS_LIVE,
    STATUS_PLANNED,
    SourceItem,
    SourceSpec,
)
from .collectors import (
    PlannedCollector,
    RepoLocalCollector,
    RssCollector,
    github_collector,
    hackernews_collector,
    reddit_collector,
)
from .registry import SourceRegistry, default_registry, registry_from_config

__all__ = (
    "COST_FREE", "COST_LOW", "COST_PAID", "STATUS_LIVE", "STATUS_PLANNED",
    "SourceItem", "SourceSpec",
    "PlannedCollector", "RepoLocalCollector", "RssCollector",
    "github_collector", "hackernews_collector", "reddit_collector",
    "SourceRegistry", "default_registry", "registry_from_config",
)
