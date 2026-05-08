"""engineering_intelligence — RAG/CAG knowledge collector + Obsidian renderer.

This package gives every engineering-agent role a small, deterministic
pipeline for "learn from official sources today" without any of those
sources actually being fetched in this task. The fetch transports
(RSS / sitemap / GitHub API / live HTML) are intentionally left as
adapter seams — :mod:`.collector` defines the Protocol and ships a
:class:`FakeSourceCollectorAdapter` for tests; production wiring is a
follow-up.

Pipeline:

  1. :mod:`.source_registry` — per-role source seeds + common-core
     merge + tier prioritisation + role daily-limit policy (5).
  2. :mod:`.collector` — adapter Protocol + offline orchestration:
     dedup, same-day-topic uniqueness, sort by tier/importance,
     truncate to the daily limit.
  3. :mod:`.dedup` — URL / title / topic / dedup-key collapse.
  4. :mod:`.renderer` — Obsidian markdown builder (frontmatter + 13
     mandatory sections + 학습 난이도 / 검색 질문 / CAG 컨텍스트 /
     프로젝트 적용 / 실습 검증 / 재검토).
  5. :mod:`.obsidian` — quality gate (16 contract checks) +
     ``ObsidianWriteRequest`` builder for the L1 auto-save kind
     ``engineering-knowledge``.
  6. :mod:`.github_sync` — *plan only*. Builds a
     ``docs_only_sync_plan`` that downstream G6 / G3 wiring will
     turn into a docs-only PR via the GitHub App. This module never
     pushes.
  7. :mod:`.discord_summary` — daily role markdown digest helper.

Strict offline. No GitHub / Discord / env / token / private-key access
happens in this package.
"""

from __future__ import annotations

from .collector import (
    CollectionRunResult,
    FakeSourceCollectorAdapter,
    SourceCollectorAdapter,
    collect_for_role,
    utc_now_iso,
)
from .dedup import (
    compute_dedup_key,
    dedup_items,
    enforce_same_day_topic_uniqueness,
)
from .discord_summary import (
    render_daily_role_summary,
    render_multi_role_summary,
)
from .github_sync import (
    GithubAppInterface,
    PendingGitSyncFile,
    PendingGitSyncPlan,
    build_pending_audit,
    build_pending_git_sync_plan,
)
from .models import (
    Audience,
    CagContext,
    CollectionMode,
    ENGINEERING_KNOWLEDGE_CONTRACT,
    EngineeringKnowledgeItem,
    Importance,
    KnowledgeStatus,
    LearningLevel,
    NOTE_KIND_ENGINEERING_KNOWLEDGE,
    PracticeVerification,
    ProjectApplicability,
    SourceEntry,
    SourceKind,
    SourceTier,
)
from .obsidian import (
    QualityGateResult,
    build_engineering_knowledge_write_request,
    build_rejected_quality_gate_audit,
    evaluate_quality_gate,
)
from .renderer import (
    RendererError,
    render_engineering_knowledge_note,
    render_frontmatter,
    required_sections,
)
from .source_registry import (
    COMMON_CORE_SOURCES,
    SUPPORTED_ROLES,
    auto_collectable_sources,
    daily_limit_for_role,
    find_source,
    prioritise_sources,
    role_sources,
)


__all__ = [
    # collector
    "CollectionRunResult",
    "FakeSourceCollectorAdapter",
    "SourceCollectorAdapter",
    "collect_for_role",
    "utc_now_iso",
    # dedup
    "compute_dedup_key",
    "dedup_items",
    "enforce_same_day_topic_uniqueness",
    # discord summary
    "render_daily_role_summary",
    "render_multi_role_summary",
    # github sync
    "GithubAppInterface",
    "PendingGitSyncFile",
    "PendingGitSyncPlan",
    "build_pending_audit",
    "build_pending_git_sync_plan",
    # models
    "Audience",
    "CagContext",
    "CollectionMode",
    "ENGINEERING_KNOWLEDGE_CONTRACT",
    "EngineeringKnowledgeItem",
    "Importance",
    "KnowledgeStatus",
    "LearningLevel",
    "NOTE_KIND_ENGINEERING_KNOWLEDGE",
    "PracticeVerification",
    "ProjectApplicability",
    "SourceEntry",
    "SourceKind",
    "SourceTier",
    # obsidian
    "QualityGateResult",
    "build_engineering_knowledge_write_request",
    "build_rejected_quality_gate_audit",
    "evaluate_quality_gate",
    # renderer
    "RendererError",
    "render_engineering_knowledge_note",
    "render_frontmatter",
    "required_sections",
    # source registry
    "COMMON_CORE_SOURCES",
    "SUPPORTED_ROLES",
    "auto_collectable_sources",
    "daily_limit_for_role",
    "find_source",
    "prioritise_sources",
    "role_sources",
]
