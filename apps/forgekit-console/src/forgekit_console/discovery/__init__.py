"""Discovery (WT3) — idea-discovery pipeline + manual/low-cost video-watch ingest."""

from __future__ import annotations

from .models import (
    CompetitorGapMap,
    DiscoveryResult,
    IdeaBrief,
    OpportunitySignal,
    ReferenceBundle,
)
from .pipeline import (
    build_gap_map,
    build_idea_briefs,
    build_reference_bundle,
    promote_to_handoff,
    run_idea_discovery,
    shape_signals,
)
from .video_watch import VideoIngest, VideoWatchResult, summarize_ingest

__all__ = (
    "CompetitorGapMap", "DiscoveryResult", "IdeaBrief", "OpportunitySignal",
    "ReferenceBundle",
    "build_gap_map", "build_idea_briefs", "build_reference_bundle",
    "promote_to_handoff", "run_idea_discovery", "shape_signals",
    "VideoIngest", "VideoWatchResult", "summarize_ingest",
)
