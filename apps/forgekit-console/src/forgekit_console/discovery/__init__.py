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
    gap_map_to_evidence_note,
    next_questions_for,
    persist_brief,
    persist_evidence,
    promote_brief,
    run_discovery_sweep,
    self_improve_to_note,
)
from .ledger import (
    DiscoveryLedger,
    LedgerIdea,
    ST_NEW,
    ST_PARKED,
    ST_PROMOTED,
    ST_SAVED,
    ST_SEEN,
    discovery_ledger_path,
    fingerprint,
)
from .loop import (
    DiscoveryLoopReport,
    LoopBudget,
    LoopTick,
    PromotionPolicy,
    age_hours,
    ask_candidates,
    candidate_reason,
    discovery_loop_tick,
    freshness_label,
    is_candidate,
    is_fresh,
    run_discovery_loop,
    stale_pending,
)
from .video_watch import VideoIngest, VideoWatchResult, summarize_ingest

__all__ = (
    "CompetitorGapMap", "DiscoveryResult", "IdeaBrief", "OpportunitySignal",
    "ReferenceBundle",
    "build_gap_map", "build_idea_briefs", "build_reference_bundle",
    "promote_to_handoff", "run_idea_discovery", "shape_signals",
    "DiscoveryDigest", "DiscoverySweep", "run_discovery_sweep", "next_questions_for",
    "promote_brief", "brief_to_authored_note", "persist_brief",
    "gap_map_to_evidence_note", "self_improve_to_note", "persist_evidence",
    "DiscoveryLedger", "LedgerIdea", "discovery_ledger_path", "fingerprint",
    "ST_NEW", "ST_SEEN", "ST_PROMOTED", "ST_SAVED", "ST_PARKED",
    "DiscoveryLoopReport", "LoopBudget", "LoopTick", "PromotionPolicy",
    "age_hours", "ask_candidates", "candidate_reason", "discovery_loop_tick",
    "freshness_label", "is_candidate", "is_fresh", "run_discovery_loop", "stale_pending",
    "VideoIngest", "VideoWatchResult", "summarize_ingest",
)
