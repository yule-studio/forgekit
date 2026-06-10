"""Research sufficiency scoring (Part 3 scaffold).

The full collector-loop redesign — multiple per-role queries, dedup,
budget-aware iteration — is a follow-up phase. This module lays the
deterministic foundation that loop will rely on:

- A per-role minimum bar (how many distinct sources of which types we
  expect before deliberation can really start).
- A scoring function that takes a :class:`ResearchPack` and returns
  which roles are still under-covered.
- A summary report so the gateway can either trigger another query
  round or surface "이 역할의 자료가 부족합니다" to the user verbatim.

The thresholds below are intentionally modest — operators can tune
them with confidence as the collector loop matures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence, Tuple

from ..deliberation import (
    SOURCE_TYPE_CODE_CONTEXT,
    SOURCE_TYPE_COMMUNITY_SIGNAL,
    SOURCE_TYPE_DESIGN_REFERENCE,
    SOURCE_TYPE_GITHUB_ISSUE,
    SOURCE_TYPE_GITHUB_PR,
    SOURCE_TYPE_IMAGE_REFERENCE,
    SOURCE_TYPE_OFFICIAL_DOCS,
    SOURCE_TYPE_URL,
    SOURCE_TYPE_WEB_RESULT,
    source_type,
)
from .pack import ResearchPack


@dataclass(frozen=True)
class RoleSufficiencyTarget:
    """Minimum bar for one role's research coverage.

    ``min_sources`` is the absolute floor; ``required_types`` is the set
    of source_type values at least one source must satisfy.
    """

    role: str
    min_sources: int
    required_types: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RoleCoverage:
    """How well a single role is covered right now."""

    role: str
    distinct_sources: int
    matched_types: Tuple[str, ...]
    missing_types: Tuple[str, ...]
    sufficient: bool


@dataclass(frozen=True)
class ResearchSufficiencyScore:
    """Aggregate outcome of evaluating a pack against role targets."""

    sufficient: bool
    distinct_url_count: int
    role_coverage: Tuple[RoleCoverage, ...] = field(default_factory=tuple)
    notes: Tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Per-role targets (deterministic baseline)
# ---------------------------------------------------------------------------


_ANY_GITHUB = (SOURCE_TYPE_GITHUB_ISSUE, SOURCE_TYPE_GITHUB_PR)


DEFAULT_ROLE_TARGETS: Tuple[RoleSufficiencyTarget, ...] = (
    RoleSufficiencyTarget(
        role="tech-lead",
        min_sources=1,
        required_types=(SOURCE_TYPE_OFFICIAL_DOCS,),
    ),
    RoleSufficiencyTarget(
        role="ai-engineer",
        min_sources=2,
        required_types=(SOURCE_TYPE_OFFICIAL_DOCS, SOURCE_TYPE_WEB_RESULT),
    ),
    RoleSufficiencyTarget(
        role="product-designer",
        min_sources=2,
        required_types=(SOURCE_TYPE_IMAGE_REFERENCE, SOURCE_TYPE_DESIGN_REFERENCE),
    ),
    RoleSufficiencyTarget(
        role="backend-engineer",
        min_sources=2,
        required_types=(SOURCE_TYPE_OFFICIAL_DOCS, SOURCE_TYPE_CODE_CONTEXT),
    ),
    RoleSufficiencyTarget(
        role="frontend-engineer",
        min_sources=1,
        required_types=(SOURCE_TYPE_DESIGN_REFERENCE,),
    ),
    RoleSufficiencyTarget(
        role="qa-engineer",
        min_sources=2,
        required_types=_ANY_GITHUB,
    ),
    RoleSufficiencyTarget(
        role="devops-engineer",
        min_sources=2,
        required_types=(SOURCE_TYPE_OFFICIAL_DOCS,) + _ANY_GITHUB,
    ),
)


# ---------------------------------------------------------------------------
# Scoring entry point
# ---------------------------------------------------------------------------


def score_research_sufficiency(
    pack: Optional[ResearchPack],
    *,
    role_targets: Sequence[RoleSufficiencyTarget] = DEFAULT_ROLE_TARGETS,
) -> ResearchSufficiencyScore:
    """Return a :class:`ResearchSufficiencyScore` for *pack*.

    A pack is considered sufficient when every role target's
    ``min_sources`` is met *and* at least one of its ``required_types``
    is present (either match counts as a pass — operators care that
    *some* canonical source exists, not all of them).
    """

    if pack is None or not getattr(pack, "sources", None):
        coverage = tuple(
            RoleCoverage(
                role=t.role,
                distinct_sources=0,
                matched_types=(),
                missing_types=tuple(t.required_types),
                sufficient=False,
            )
            for t in role_targets
        )
        return ResearchSufficiencyScore(
            sufficient=False,
            distinct_url_count=0,
            role_coverage=coverage,
            notes=("research_pack 없음 — 자료 수집을 시작해야 합니다.",),
        )

    distinct_urls: set[str] = set()
    seen_types: set[str] = set()
    type_counts: dict[str, int] = {}
    for source in pack.sources:
        url = (getattr(source, "source_url", None) or "").strip()
        if url:
            distinct_urls.add(url)
        st = source_type(source) or SOURCE_TYPE_URL
        seen_types.add(st)
        type_counts[st] = type_counts.get(st, 0) + 1

    coverage_list: list[RoleCoverage] = []
    notes: list[str] = []
    overall_sufficient = True
    for target in role_targets:
        # min_sources is measured against distinct URLs, not raw source
        # rows, so duplicate captures of the same URL don't inflate the
        # bar artificially.
        meets_count = len(distinct_urls) >= target.min_sources
        matched = tuple(
            t for t in target.required_types if t in seen_types
        )
        missing = tuple(
            t for t in target.required_types if t not in seen_types
        )
        type_ok = bool(matched) if target.required_types else True
        sufficient = meets_count and type_ok
        coverage_list.append(
            RoleCoverage(
                role=target.role,
                distinct_sources=len(distinct_urls),
                matched_types=matched,
                missing_types=missing,
                sufficient=sufficient,
            )
        )
        if not sufficient:
            overall_sufficient = False
            if not meets_count:
                notes.append(
                    f"{target.role}: 자료 {len(distinct_urls)}/{target.min_sources}건 — 추가 수집 필요"
                )
            if not type_ok:
                notes.append(
                    f"{target.role}: 필요한 source_type({', '.join(target.required_types)}) 중 어느 것도 없음"
                )

    return ResearchSufficiencyScore(
        sufficient=overall_sufficient,
        distinct_url_count=len(distinct_urls),
        role_coverage=tuple(coverage_list),
        notes=tuple(notes),
    )


def under_covered_roles(
    score: ResearchSufficiencyScore,
) -> Tuple[str, ...]:
    """Convenience accessor — roles that are not yet sufficient."""

    return tuple(c.role for c in score.role_coverage if not c.sufficient)
