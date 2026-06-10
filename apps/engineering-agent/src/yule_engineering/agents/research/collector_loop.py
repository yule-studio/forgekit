"""Sufficiency-driven follow-up collection loop (extracted from collector.py).

This module owns the **iterative collection** responsibility (Part 4):
after the first single-pass collection, it rotates through under-covered
roles issuing follow-up queries until coverage is "good enough", the
budget is exhausted, or progress stalls. Kept out of ``collector.py`` so
that file holds the thin orchestration entry point (``collect_research_pack``
/ ``auto_collect_or_request_more_input``) and this file holds the loop
mechanics.

Public-ish surface (re-exported by ``collector`` so callers/tests keep
their existing import paths):

- :func:`_extend_pack_until_sufficient` — the loop driver.
- :func:`_next_followup_role` — role rotation picker.
- :func:`_append_sources` — immutable pack rebuild on new hits.
- :func:`_format_user_input_request` — the NEEDS_USER_INPUT reply text.

Import direction is one-way: this module imports the collector *core*
(base types, ``build_query_for_role`` / ``_rank_sources_for_role`` /
``short_role``). The core re-exports + calls these — collector core →
loop is the legal call direction.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence, Tuple

from .pack import ResearchPack, ResearchSource, pack_from_request
from .collector import (
    BudgetTracker,
    CollectorError,
    CollectorQuery,
    ResearchCollector,
    _rank_sources_for_role,
    build_query_for_role,
    short_role,
)


# ---------------------------------------------------------------------------
# Sufficiency-driven follow-up collection (Part 4)
# ---------------------------------------------------------------------------


_FOLLOWUP_ROLE_ORDER: Tuple[str, ...] = (
    "ai-engineer",
    "backend-engineer",
    "product-designer",
    "frontend-engineer",
    "qa-engineer",
    "tech-lead",
)


def _extend_pack_until_sufficient(
    *,
    pack: ResearchPack,
    collector: ResearchCollector,
    budget: BudgetTracker,
    prompt: str,
    task_type: Optional[str],
    primary_role: str,
    max_results: int,
    sufficiency_targets: Sequence[Any] = (),
):
    """Drive role-aware follow-up queries until coverage is "good enough".

    Returns ``(pack, iterations, sufficiency, stop_reason)``. ``sufficiency``
    may be ``None`` when the deliberation/sufficiency module isn't
    importable (defensive for partial installs); the caller falls back
    to single-pass behaviour. ``stop_reason`` is one of:
    ``"sufficient"``, ``"budget_exhausted"``, ``"no_progress"``,
    ``"role_rotation_exhausted"``, ``"no_initial_provider_hit"``, or
    ``"no_sufficiency_module"``.
    """

    try:
        from .sufficiency import (
            DEFAULT_ROLE_TARGETS,
            score_research_sufficiency,
            under_covered_roles,
        )
    except Exception:  # noqa: BLE001 - module optional during partial installs
        return pack, 1, None, "no_sufficiency_module"

    targets = tuple(sufficiency_targets) if sufficiency_targets else DEFAULT_ROLE_TARGETS

    iterations = 1
    score = score_research_sufficiency(pack, role_targets=targets)
    if score.sufficient:
        return pack, iterations, score, "sufficient"

    # The follow-up loop expands coverage, it doesn't bootstrap from
    # zero. If the first pass returned no provider hits at all (unknown
    # role / disabled provider / canned-empty mock), fall through with
    # the same shape as before so the caller can route to NEEDS_USER_INPUT.
    has_provider_hit = any(
        (s.extra or {}).get("provider") for s in pack.sources
    )
    if not has_provider_hit:
        return pack, iterations, score, "no_initial_provider_hit"

    seen_urls: set[str] = {
        (s.source_url or "").strip()
        for s in pack.sources
        if (s.source_url or "").strip()
    }

    visited_role_queries: set[tuple[str, str]] = set()
    consecutive_no_gain = 0
    stop_reason = "budget_exhausted"  # default if while-loop exits via budget

    while budget.can_call():
        next_role = _next_followup_role(
            score=score,
            primary_role=primary_role,
            visited=visited_role_queries,
            under_covered_fn=under_covered_roles,
        )
        if next_role is None:
            stop_reason = "role_rotation_exhausted"
            break

        query = build_query_for_role(
            role=next_role, prompt=prompt, task_type=task_type
        )
        marker = (next_role, query)
        if not query or marker in visited_role_queries:
            visited_role_queries.add(marker)
            continue
        visited_role_queries.add(marker)

        budget.record_call()
        iterations += 1
        try:
            hits = collector.search(
                CollectorQuery(
                    query=query,
                    role=next_role,
                    max_results=max_results,
                    task_type=task_type,
                )
            )
        except CollectorError:
            hits = ()
        except Exception:  # noqa: BLE001 - never crash the conversation flow
            hits = ()

        ranked = _rank_sources_for_role(hits, role=next_role)
        ranked = budget.trim_results(ranked)
        new_sources = []
        for src in ranked:
            url = (getattr(src, "source_url", None) or "").strip()
            if not url:
                # Sources without URL — key off title to avoid
                # exact-duplicate canned hits inflating coverage.
                key = f"title:{(getattr(src, 'title', '') or '').strip()}"
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                new_sources.append(src)
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            new_sources.append(src)

        if new_sources:
            pack = _append_sources(pack, new_sources)
            consecutive_no_gain = 0
        else:
            consecutive_no_gain += 1
            # Bail out only after a generous run of zero-gain rounds —
            # role rotation legitimately produces a few duplicates before
            # finding a fresh source. Cap so canned providers can't
            # infinitely loop, but trust visited_role_queries + budget
            # for the actual termination work.
            if consecutive_no_gain >= 4:
                stop_reason = "no_progress"
                break

        score = score_research_sufficiency(pack, role_targets=targets)
        if score.sufficient:
            stop_reason = "sufficient"
            break

    return pack, iterations, score, stop_reason


def _next_followup_role(
    *,
    score,
    primary_role: str,
    visited: set,
    under_covered_fn,
) -> Optional[str]:
    """Pick the next role to query. Returns ``None`` when nothing's left."""

    short_primary = short_role(primary_role)
    under_covered = list(under_covered_fn(score))
    # Prefer the originally-asked role when still under-covered so the
    # follow-up doesn't drift away from the user's actual request.
    ordered: list[str] = []
    if short_primary in under_covered:
        ordered.append(short_primary)
    for candidate in _FOLLOWUP_ROLE_ORDER:
        if candidate in under_covered and candidate not in ordered:
            ordered.append(candidate)
    for candidate in under_covered:
        if candidate not in ordered:
            ordered.append(candidate)
    for candidate in ordered:
        # Skip roles whose query we already tried this run.
        if any(role == candidate for role, _q in visited):
            continue
        return candidate
    return None


def _append_sources(
    pack: ResearchPack, new_sources: Sequence[ResearchSource]
) -> ResearchPack:
    """Return *pack* with ``new_sources`` appended (immutable rebuild)."""

    if not new_sources:
        return pack
    merged = tuple(list(pack.sources) + list(new_sources))
    auto_provider = any(s.extra.get("provider") for s in merged if s.extra)
    tags = pack.tags
    if auto_provider and "auto-collected" not in tags:
        tags = tuple(list(tags) + ["auto-collected"])
    return pack_from_request(
        request=pack.request,
        sources=merged,
        tags=tags,
        extra=dict(pack.extra or {}),
    )


def _format_user_input_request(
    *,
    role: str,
    task_type: Optional[str],
) -> str:
    short = short_role(role)
    role_hint = {
        "product-designer": "참고할 화면이나 무드보드, Mobbin·Behance 링크",
        "frontend-engineer": "참고할 컴포넌트 사례나 MDN·web.dev 문서",
        "backend-engineer": "관련 공식 문서나 API 스펙, 보안 정책 링크",
        "qa-engineer": "기존 회귀 사례, 테스트 시나리오, GitHub 이슈 링크",
        "tech-lead": "관련 ADR / RFC / 의사결정 기록 또는 GitHub PR",
    }.get(short, "관련 자료")
    if task_type:
        return (
            f"{role_hint} 한두 개를 붙여 주시면, 그걸 1차 자료로 두고 "
            f"{task_type} 흐름으로 정리해 드릴게요."
        )
    return (
        f"{role_hint} 한두 개를 붙여 주시면, 그걸 1차 자료로 두고 진행해 볼게요."
    )
