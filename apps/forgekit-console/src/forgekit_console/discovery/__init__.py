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
from .sweep import (
    DiscoveryDigest,
    DiscoverySweep,
    brief_to_authored_note,
    next_questions_for,
    persist_brief,
    promote_brief,
    run_discovery_sweep,
)
from .video_watch import VideoIngest, VideoWatchResult, summarize_ingest

__all__ = (
    "CompetitorGapMap", "DiscoveryResult", "IdeaBrief", "OpportunitySignal",
    "ReferenceBundle",
    "build_gap_map", "build_idea_briefs", "build_reference_bundle",
    "promote_to_handoff", "run_idea_discovery", "shape_signals",
    "DiscoveryDigest", "DiscoverySweep", "run_discovery_sweep", "next_questions_for",
    "promote_brief", "brief_to_authored_note", "persist_brief",
    "VideoIngest", "VideoWatchResult", "summarize_ingest",
)
