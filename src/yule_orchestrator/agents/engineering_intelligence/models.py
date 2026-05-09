"""Shared dataclasses for the engineering_intelligence surface.

Three concerns live here so other modules don't grow a tangle of
private dataclass copies:

  1. ``SourceEntry`` — the row shape carried by the source registry
     (per-role catalogues + common-core list).
  2. ``EngineeringKnowledgeItem`` — the canonical knowledge unit
     produced by collectors and consumed by the renderer / Obsidian
     bridge / RAG ingest. Carries everything the spec requires for
     RAG/CAG metadata, quality gate, learning materials, and
     practice verification.
  3. ``CagContext`` / ``PracticeVerification`` / ``ProjectApplicability``
     — small composite structs the knowledge item references by
     value so the JSON projection stays flat-ish.

Everything is a frozen dataclass so the items are hashable (the dedup
layer keys on the dataclass directly when convenient) and trivially
JSON-serialisable via :meth:`to_payload`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping, Optional, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SourceKind(str, Enum):
    STANDARD = "standard"
    DOCS = "docs"
    CHANGELOG = "changelog"
    RELEASE_NOTES = "release_notes"
    ENGINEERING_BLOG = "engineering_blog"
    REPO = "repo"
    ISSUE_TRACKER = "issue_tracker"
    SECURITY_ADVISORY = "security_advisory"
    DESIGN_SYSTEM = "design_system"
    COMMUNITY = "community"


class CollectionMode(str, Enum):
    RSS = "rss"
    SITEMAP = "sitemap"
    HTML_LIST = "html_list"
    HTML_DETAIL = "html_detail"
    GITHUB_API = "github_api"
    MANUAL = "manual"


class Importance(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Audience(str, Enum):
    JUNIOR = "junior"
    INTERMEDIATE = "intermediate"
    SENIOR = "senior"


class LearningLevel(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class KnowledgeStatus(str, Enum):
    """Lifecycle for a stored EngineeringKnowledgeItem."""

    COLLECTED = "collected"
    REVIEWED = "reviewed"
    PROMOTED = "promoted"
    DEPRECATED = "deprecated"


# Tier 1 = official spec/docs; Tier 4 = community. Auto collection
# defaults to Tier 1~2; Tier 3~4 requires review_required=True or low
# trust_weight.
class SourceTier(str, Enum):
    TIER_1 = "tier_1_official_docs"
    TIER_2 = "tier_2_official_release"
    TIER_3 = "tier_3_official_repo"
    TIER_4 = "tier_4_community"


class SourceAxis(str, Enum):
    """역할별 자료 축. 한 source는 여러 axis에 속할 수 있다.

    이 enum은 master plan §9.1의 "역할별 상시 수집 대상" 항목들을
    1:1로 흡수한다. 각 source가 어떤 axis를 cover하는지 태깅해두면
    request-time retrieval에서 task_type → axes hint matrix로 정렬할
    수 있고, scheduler는 한 axis가 누락되지 않도록 감시할 수 있다.
    """

    OFFICIAL_DOCS = "official_docs"
    API_SCHEMA_AUTH = "api_schema_auth"
    WEB_PLATFORM_FRAMEWORK = "web_platform_framework"
    REGRESSION_TEST_PLAN = "regression_test_plan"
    CI_CD_INFRA_OBSERVABILITY = "ci_cd_infra_observability"
    ARCHITECTURE_ADR_TRADEOFF = "architecture_adr_tradeoff"
    AI_FRAMEWORK = "ai_framework"
    DESIGN_SYSTEM = "design_system"
    SECURITY = "security"
    RELEASE_NOTES_CHANGELOG = "release_notes_changelog"


# 기본 refresh 주기 (분 단위). source_kind를 보고 상식적인 값을 박는다.
# scheduler가 SourceEntry.refresh_interval_minutes 미설정인 경우의
# fallback으로 사용한다.
_DEFAULT_REFRESH_INTERVAL_MINUTES_BY_KIND: "Mapping[SourceKind, int]" = {
    # 보안 권고는 신선도가 핵심 — 30분 cadence
    SourceKind.SECURITY_ADVISORY: 30,
    # 릴리스/체인지로그는 시간 단위
    SourceKind.RELEASE_NOTES: 60,
    SourceKind.CHANGELOG: 60,
    # 엔지니어링 블로그는 6시간
    SourceKind.ENGINEERING_BLOG: 360,
    # 공식 문서는 사이트맵을 자주 돌릴 필요가 없다 — 24시간
    SourceKind.DOCS: 1440,
    SourceKind.DESIGN_SYSTEM: 1440,
    # 깃허브 레포 watch는 1시간
    SourceKind.REPO: 60,
    SourceKind.ISSUE_TRACKER: 60,
    # 표준 / 매뉴얼 검토 대상은 주 단위
    SourceKind.STANDARD: 10080,
    SourceKind.COMMUNITY: 720,
}


def default_refresh_interval_for_kind(kind: "SourceKind") -> int:
    """``SourceKind`` 기준 기본 refresh interval (분)."""

    return _DEFAULT_REFRESH_INTERVAL_MINUTES_BY_KIND.get(kind, 1440)


# Note kind for vault writes — distinct from canonical "knowledge"
# (which is the L3-approval long-term record). The L1 auto-saved
# "collected & learning" note kind is "engineering-knowledge".
NOTE_KIND_ENGINEERING_KNOWLEDGE: str = "engineering-knowledge"


# Contract id stamped on every rendered note so consumers (memory
# indexer, archive tools) can pin the schema they expect.
ENGINEERING_KNOWLEDGE_CONTRACT: str = "engineering-knowledge/v0"


# ---------------------------------------------------------------------------
# Source registry row
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceEntry:
    """One row in the role-source registry.

    A ``role_tags`` tuple lets a single source belong to several roles
    (e.g. an OWASP advisory feed is both backend and devops). The
    ``content_policy`` field captures the operator-facing reminder
    (``"link + summary only"``, ``"do not store full text"``) so the
    renderer can refuse to produce body text that violates it.
    """

    source_id: str
    name: str
    base_url: str
    role_tags: Tuple[str, ...]
    stack_tags: Tuple[str, ...]
    source_kind: SourceKind
    collection_mode: CollectionMode
    tier: SourceTier
    trust_weight: float = 0.5  # 0.0–1.0
    freshness_weight: float = 0.5  # 0.0–1.0
    auto_collect: bool = True
    review_required: bool = False
    content_policy: str = (
        "link + short summary + light quotation only — no full-text reproduction"
    )
    max_items_per_day: int = 5
    # 한 source가 여러 axis에 속할 수 있다. 비어 있으면 collector는 axis 정렬에서
    # "not classified" bucket으로 본다 — retrieval에서는 보너스 점수 없음.
    axes: Tuple["SourceAxis", ...] = ()
    # 기본은 SourceKind 기반 fallback. 0/None은 fallback 사용 의도.
    refresh_interval_minutes: int = 0

    def is_official(self) -> bool:
        return self.tier in (
            SourceTier.TIER_1,
            SourceTier.TIER_2,
            SourceTier.TIER_3,
        )

    def effective_refresh_interval_minutes(self) -> int:
        """SourceEntry-level override가 있으면 그 값, 없으면 kind 기본값."""

        if self.refresh_interval_minutes and self.refresh_interval_minutes > 0:
            return self.refresh_interval_minutes
        return default_refresh_interval_for_kind(self.source_kind)

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "source_id": self.source_id,
            "name": self.name,
            "base_url": self.base_url,
            "role_tags": list(self.role_tags),
            "stack_tags": list(self.stack_tags),
            "source_kind": self.source_kind.value,
            "collection_mode": self.collection_mode.value,
            "tier": self.tier.value,
            "trust_weight": self.trust_weight,
            "freshness_weight": self.freshness_weight,
            "auto_collect": self.auto_collect,
            "review_required": self.review_required,
            "content_policy": self.content_policy,
            "max_items_per_day": self.max_items_per_day,
            "axes": [axis.value for axis in self.axes],
            "refresh_interval_minutes": self.effective_refresh_interval_minutes(),
        }


# ---------------------------------------------------------------------------
# Composite structs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CagContext:
    """When/why this item should be retrieved + decision hint."""

    when_to_use: str
    constraints: Tuple[str, ...] = ()
    decision_hint: str = ""
    avoid_if: Tuple[str, ...] = ()

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "when_to_use": self.when_to_use,
            "constraints": list(self.constraints),
            "decision_hint": self.decision_hint,
            "avoid_if": list(self.avoid_if),
        }


@dataclass(frozen=True)
class PracticeVerification:
    """How a junior verifies their practice run was correct."""

    expected_result: str
    command_to_run: Optional[str] = None
    failure_symptoms: Tuple[str, ...] = ()
    troubleshooting_hint: str = ""

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "expected_result": self.expected_result,
            "command_to_run": self.command_to_run,
            "failure_symptoms": list(self.failure_symptoms),
            "troubleshooting_hint": self.troubleshooting_hint,
        }


@dataclass(frozen=True)
class ProjectApplicability:
    """How / where this item could land in a real repo."""

    related_repo: Optional[str] = None
    related_module: Optional[str] = None
    possible_issue_title: Optional[str] = None
    implementation_risk: str = "low"

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "related_repo": self.related_repo,
            "related_module": self.related_module,
            "possible_issue_title": self.possible_issue_title,
            "implementation_risk": self.implementation_risk,
        }


# ---------------------------------------------------------------------------
# EngineeringKnowledgeItem
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EngineeringKnowledgeItem:
    """Canonical knowledge unit — RAG/CAG-friendly, learning-friendly.

    The fields mirror the G-task spec exactly. "Optional" fields have
    safe defaults so a partially-built item can still flow through
    the dedup layer; the quality gate (in :mod:`.obsidian`) is what
    enforces the strict contract before vault save.
    """

    # Identity
    item_id: str
    topic_key: str
    title: str
    role: str
    stack_tags: Tuple[str, ...]

    # Source provenance
    source_name: str
    source_url: str
    source_kind: SourceKind
    collected_at: str  # ISO-8601 datetime
    published_at: Optional[str] = None

    # Importance + audience
    importance: Importance = Importance.MEDIUM
    audience: Audience = Audience.INTERMEDIATE

    # Learning surface
    summary: str = ""
    why_it_matters: str = ""
    what_changed: str = ""
    practical_impact: str = ""
    recommended_action: str = ""

    # Practice surface
    practice_topic: str = ""
    practice_goal: str = ""
    practice_steps: Tuple[str, ...] = ()
    practice_checklist: Tuple[str, ...] = ()
    expected_output: str = ""
    common_mistakes: Tuple[str, ...] = ()
    practice_verification: Optional[PracticeVerification] = None

    # RAG/CAG
    rag_tags: Tuple[str, ...] = ()
    cag_context_key: str = ""
    cag_context: Optional[CagContext] = None
    retrieval_queries: Tuple[str, ...] = ()
    retrieval_summary: str = ""

    # Long-term learning quality
    learning_level: LearningLevel = LearningLevel.INTERMEDIATE
    prerequisites: Tuple[str, ...] = ()
    next_topics: Tuple[str, ...] = ()
    estimated_practice_time: str = ""

    # Lifecycle
    knowledge_status: KnowledgeStatus = KnowledgeStatus.COLLECTED
    review_after_days: int = 90
    staleness_reason: str = ""

    # Project applicability
    project_applicability: Optional[ProjectApplicability] = None

    # Refs / confidence / dedup
    references: Tuple[str, ...] = ()
    confidence: float = 0.5
    dedup_key: str = ""

    # Audit of items rejected for this topic_key (so we don't lose
    # provenance — the producer kept this slot empty by default).
    rejected_candidates: Tuple[Mapping[str, Any], ...] = field(default_factory=tuple)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def to_payload(self) -> Mapping[str, Any]:
        """Stable dict projection used by audit / RAG ingest / tests."""

        return {
            "item_id": self.item_id,
            "topic_key": self.topic_key,
            "title": self.title,
            "role": self.role,
            "stack_tags": list(self.stack_tags),
            "source_name": self.source_name,
            "source_url": self.source_url,
            "source_kind": self.source_kind.value,
            "published_at": self.published_at,
            "collected_at": self.collected_at,
            "importance": self.importance.value,
            "audience": self.audience.value,
            "summary": self.summary,
            "why_it_matters": self.why_it_matters,
            "what_changed": self.what_changed,
            "practical_impact": self.practical_impact,
            "recommended_action": self.recommended_action,
            "practice_topic": self.practice_topic,
            "practice_goal": self.practice_goal,
            "practice_steps": list(self.practice_steps),
            "practice_checklist": list(self.practice_checklist),
            "expected_output": self.expected_output,
            "common_mistakes": list(self.common_mistakes),
            "practice_verification": (
                self.practice_verification.to_payload()
                if self.practice_verification is not None
                else None
            ),
            "rag_tags": list(self.rag_tags),
            "cag_context_key": self.cag_context_key,
            "cag_context": (
                self.cag_context.to_payload()
                if self.cag_context is not None
                else None
            ),
            "retrieval_queries": list(self.retrieval_queries),
            "retrieval_summary": self.retrieval_summary,
            "learning_level": self.learning_level.value,
            "prerequisites": list(self.prerequisites),
            "next_topics": list(self.next_topics),
            "estimated_practice_time": self.estimated_practice_time,
            "knowledge_status": self.knowledge_status.value,
            "review_after_days": self.review_after_days,
            "staleness_reason": self.staleness_reason,
            "project_applicability": (
                self.project_applicability.to_payload()
                if self.project_applicability is not None
                else None
            ),
            "references": list(self.references),
            "confidence": self.confidence,
            "dedup_key": self.dedup_key,
            "rejected_candidates": [dict(r) for r in self.rejected_candidates],
        }

    def with_dedup_key(self, dedup_key: str) -> "EngineeringKnowledgeItem":
        return replace(self, dedup_key=dedup_key)


__all__ = [
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
    "SourceAxis",
    "SourceEntry",
    "SourceKind",
    "SourceTier",
    "default_refresh_interval_for_kind",
]
