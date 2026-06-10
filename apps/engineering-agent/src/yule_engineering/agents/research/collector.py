"""Autonomous first-pass research collector for engineering-agent.

When a user posts a free-form request and the gateway needs reference
material to drive deliberation, this module runs a metadata-only
collection step **before** asking the user for links/screenshots:

1. Build a role-aware search query from the prompt + task_type.
2. Hand it to a :class:`ResearchCollector` (Mock by default; Tavily/Brave
   when their API keys are present and the operator opted in).
3. Wrap the results into typed :class:`ResearchSource` instances and
   compose a :class:`ResearchPack` together with the original user
   message and any user-supplied links/attachments.
4. Return a :class:`CollectionOutcome` that tells the conversation
   layer whether to:
   - run deliberation immediately (``AUTO_COLLECTED`` / ``USER_PROVIDED``), or
   - ask the user for more input (``NEEDS_USER_INPUT``).

Operating principles (matches policy / design rules):

- **Metadata-only.** We never download an image, copy body text, or
  bypass auth. Each :class:`ResearchSource` keeps title/url/domain/
  thumbnail_url/description/snippet — and that's it.
- **Mock fallback.** When auto-collect is disabled or the chosen
  provider has no API key, the factory returns a deterministic mock
  collector so tests run without a network and operators can preview
  the contract before paying for a search API.
- **Role-aware.** Each role's research profile (already centralised in
  ``deliberation.ROLE_RESEARCH_PROFILES``) drives query boosters and
  result ranking. The mock collector returns canned domains per role
  so different roles see different first-pass material.

The collector itself never touches Discord, never writes files, and
never persists. Storage and forum posting belong to upstream wiring.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from ..deliberation import KNOWN_SOURCE_TYPES, ROLE_RESEARCH_PROFILES
from .pack import (
    ResearchAttachment,
    ResearchFinding,
    ResearchPack,
    ResearchRequest,
    ResearchSource,
    SourceType,
    extract_urls,  # re-exported so callers don't need to know research_pack
    make_research_request,
    pack_from_request,
    source_from_user_message,
)


# ---------------------------------------------------------------------------
# Env config (extracted → collector_config.py)
# ---------------------------------------------------------------------------
#
# The env var names, provider identifiers, default lists, forum-comment-mode
# resolver, the :class:`CollectorConfig` dataclass + ``from_env`` parser and
# the env-coercion helpers live in ``collector_config`` (a dependency-free
# leaf). They're re-exported here so callers / tests / the sibling modules
# keep importing them from ``collector``.
from .collector_config import (  # noqa: F401
    DEFAULT_AUTO_PROVIDERS,
    DEFAULT_FORUM_COMMENT_MODE,
    DEFAULT_MAX_PROVIDER_CALLS,
    DEFAULT_MAX_RESULTS,
    DEFAULT_MAX_RESULTS_PER_ROLE,
    DEFAULT_ROLE_PROVIDER_POLICY,
    ENV_AUTO_COLLECT_ENABLED,
    ENV_BRAVE_API_KEY,
    ENV_FORUM_COMMENT_MODE,
    ENV_MAX_PROVIDER_CALLS,
    ENV_MAX_RESULTS,
    ENV_MAX_RESULTS_PER_ROLE,
    ENV_PROVIDER,
    ENV_PROVIDERS,
    ENV_TAVILY_API_KEY,
    EXTERNAL_PROVIDERS,
    FORUM_COMMENT_MODE_GATEWAY,
    FORUM_COMMENT_MODE_MEMBER_BOTS,
    FORUM_COMMENT_MODES,
    KNOWN_PROVIDERS,
    PROVIDER_AUTO,
    PROVIDER_BRAVE,
    PROVIDER_MOCK,
    PROVIDER_MULTI,
    PROVIDER_TAVILY,
    SINGLE_PROVIDER_MODES,
    CollectorConfig,
    _parse_provider_list,
    _positive_int,
    _strip_or_none,
    _truthy,
    resolve_forum_comment_mode,
)

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"


# ---------------------------------------------------------------------------
# GitHub URL parsing (network-free)
# ---------------------------------------------------------------------------
#
# Legacy thin wrapper. The canonical parser now lives in
# ``agents/git/github_url.py`` and supports the full set of shapes
# (repo / issue / PR / commit / compare / tree / blob). Issue/PR
# callers keep the old ``{kind, owner, repo, number}`` shape via the
# delegated wrapper below.


def parse_github_url(url: Optional[str]) -> Optional[Mapping[str, Any]]:
    """Issue/PR URL parser preserved for collector callers.

    Delegates to ``agents.git.github_url.parse_github_url`` which keeps
    the historical ``{kind, owner, repo, number}`` shape for issue and
    pull_request URLs. Returns ``None`` for any other shape so the
    surrounding source-classification fall-through is unchanged.
    """

    from yule_vcs.github_url import parse_github_url as _delegate

    return _delegate(url)


# ---------------------------------------------------------------------------
# Confidence scoring (deterministic)
# ---------------------------------------------------------------------------


def compute_confidence(
    *,
    source_type: SourceType,
    role: str,
    has_url: bool,
    has_snippet: bool,
    has_thumbnail: bool = False,
    provider_score: Optional[float] = None,
) -> str:
    """Return ``"high"`` / ``"medium"`` / ``"low"`` from cheap signals.

    Signals (additive):
    - URL present → +1.
    - Snippet/summary present → +1.
    - Thumbnail present → +0.5 (rounded into ``score`` later).
    - source_type matches role's research profile slot:
      - rank 0  → +3 (prime)
      - rank 1-2 → +2 (still preferred)
      - rank 3+ → +1 (acceptable)
    - High-trust source_type baseline:
      - OFFICIAL_DOCS / GITHUB_ISSUE / GITHUB_PR → +2
      - DESIGN_REFERENCE / IMAGE_REFERENCE / FILE_ATTACHMENT / CODE_CONTEXT → +1
      - COMMUNITY_SIGNAL → 0
      - WEB_RESULT / URL → -1 (generic, less trustworthy)
    - provider_score in [0.0, 1.0] (Tavily/Brave): adds ``round(score * 2)``.

    Cutoffs:
    - score ≥ 5  → high
    - score ≥ 3  → medium
    - else       → low

    Stays deterministic so unit tests can pin the label.
    """

    score = 0.0
    if has_url:
        score += 1
    if has_snippet:
        score += 1
    if has_thumbnail:
        score += 0.5

    short = short_role(role)
    profile = ROLE_RESEARCH_PROFILES.get(short, ())
    type_value = (
        source_type.value
        if isinstance(source_type, SourceType)
        else str(source_type)
    )
    if profile and type_value in profile:
        rank = profile.index(type_value)
        if rank == 0:
            score += 3
        elif rank <= 2:
            score += 2
        else:
            score += 1

    high_trust = {
        SourceType.OFFICIAL_DOCS,
        SourceType.GITHUB_ISSUE,
        SourceType.GITHUB_PR,
    }
    medium_trust = {
        SourceType.DESIGN_REFERENCE,
        SourceType.IMAGE_REFERENCE,
        SourceType.FILE_ATTACHMENT,
        SourceType.CODE_CONTEXT,
    }
    if source_type in high_trust:
        score += 2
    elif source_type in medium_trust:
        score += 1
    elif source_type == SourceType.COMMUNITY_SIGNAL:
        pass
    elif source_type in {SourceType.WEB_RESULT, SourceType.URL}:
        score -= 1

    if provider_score is not None:
        try:
            normalized = max(0.0, min(1.0, float(provider_score)))
            score += round(normalized * 2)
        except (TypeError, ValueError):
            pass

    if score >= 5:
        return CONFIDENCE_HIGH
    if score >= 3:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


# ---------------------------------------------------------------------------
# Budget guard (per collection run)
# ---------------------------------------------------------------------------


@dataclass
class BudgetTracker:
    """Per-run guard for provider calls and result count.

    Mutable on purpose so the same instance can be threaded through one
    ``collect_research_pack`` call. ``can_call()`` reports whether the
    next provider invocation is allowed; ``record_call()`` increments
    the counter; ``trim_results(results)`` slices to the per-role cap.
    """

    max_provider_calls: int = DEFAULT_MAX_PROVIDER_CALLS
    max_results_per_role: int = DEFAULT_MAX_RESULTS_PER_ROLE
    calls_made: int = 0
    truncated: bool = False

    def can_call(self) -> bool:
        return self.calls_made < self.max_provider_calls

    def record_call(self) -> None:
        self.calls_made += 1

    def trim_results(self, results: Sequence[ResearchSource]) -> Tuple[ResearchSource, ...]:
        if len(results) > self.max_results_per_role:
            self.truncated = True
            return tuple(results[: self.max_results_per_role])
        return tuple(results)

    def limit_note(self) -> Optional[str]:
        if self.calls_made >= self.max_provider_calls and self.calls_made > 0:
            return (
                f"provider call budget exhausted ({self.calls_made}/"
                f"{self.max_provider_calls}); 추가 수집은 다음 turn에서 진행"
            )
        if self.truncated:
            return (
                f"수집 결과를 역할당 {self.max_results_per_role}건으로 잘랐습니다 — "
                "필요하면 다음 turn에서 더 깊이 봅니다"
            )
        return None


# ---------------------------------------------------------------------------
# Collector interface
# ---------------------------------------------------------------------------


class CollectorError(RuntimeError):
    """Raised when the chosen provider failed (network, auth, parse)."""


class ProviderUnavailable(CollectorError):
    """Raised when the provider can't run (missing API key / wrong shape)."""


@dataclass(frozen=True)
class CollectorQuery:
    """Input shape consumed by :meth:`ResearchCollector.search`."""

    query: str
    role: str
    max_results: int
    task_type: Optional[str] = None
    extra: Mapping[str, Any] = field(default_factory=dict)


class ResearchCollector(ABC):
    """Provider-agnostic search interface.

    Implementations must return a sequence of :class:`ResearchSource`
    instances tagged with the right :class:`SourceType` and metadata
    (title / url / domain / snippet / thumbnail / why_relevant). They
    must never raise on empty results — return an empty tuple instead.
    """

    name: str = "abstract"

    @abstractmethod
    def search(self, query: CollectorQuery) -> Sequence[ResearchSource]:
        ...


class NoOpCollector(ResearchCollector):
    """Used when auto-collect is disabled. Always returns ``()``."""

    name = "noop"

    def search(self, query: CollectorQuery) -> Sequence[ResearchSource]:
        return ()


# ---------------------------------------------------------------------------
# Role-aware query construction
# ---------------------------------------------------------------------------


# Boost terms appended to the user prompt for each role to nudge the search
# engine (or mock) toward role-relevant material. Kept short so providers
# like Tavily/Brave that respect natural-language queries still rank
# user keywords highly.
ROLE_QUERY_BOOSTS: Mapping[str, Tuple[str, ...]] = {
    "tech-lead": ("architecture", "decision", "RFC"),
    "product-designer": ("UI reference", "UX pattern", "design"),
    "backend-engineer": ("official docs", "API", "schema"),
    "frontend-engineer": ("MDN", "framework docs", "accessibility"),
    "qa-engineer": ("regression", "test plan", "e2e"),
    "devops-engineer": (
        "CI/CD",
        "GitHub Actions",
        "deployment",
        "rollback",
        "observability",
    ),
}


def short_role(role: str) -> str:
    """Strip ``<agent>/`` prefix so we can reuse role-keyed mappings."""

    return role.split("/", 1)[1] if "/" in role else role


def build_query_for_role(
    *,
    role: str,
    prompt: str,
    task_type: Optional[str] = None,
    extra_keywords: Sequence[str] = (),
) -> str:
    """Build a search query string from the user prompt + role + task_type.

    Strategy:
    - Take the first line of the prompt (avoid runaway sentences).
    - **P0-F**: Run the first line through the engineering-domain
      ``canonicalize_query`` so typos / case variants / aliases
      (``dRAG`` → ``RAG``, ``ci cd`` → ``CI/CD``, ``알엠`` → ``LLM``)
      get rewritten before the collector and recall pipelines see them.
      Raw prompt is left untouched; only the query token is normalized.
    - Append task_type as a keyword (e.g. ``landing-page``).
    - Append role-specific booster terms (`UI reference`, `official docs`).
    - Dedup tokens to keep the query short.

    See :func:`build_canonical_query_for_role` for the version that
    also returns the :class:`CanonicalQuery` audit envelope.
    """

    return build_canonical_query_for_role(
        role=role,
        prompt=prompt,
        task_type=task_type,
        extra_keywords=extra_keywords,
    )[0]


def build_canonical_query_for_role(
    *,
    role: str,
    prompt: str,
    task_type: Optional[str] = None,
    extra_keywords: Sequence[str] = (),
) -> Tuple[str, Any]:
    """Build the role-aware query *plus* return the canonicalization audit.

    Returns ``(query_str, CanonicalQuery)``. Callers that want to log
    or surface normalization metadata (raw vs canonical, confidence,
    applied replacements) use this; thin shims that just need the
    query string use :func:`build_query_for_role`.
    """

    from .query_canonicalizer import canonicalize_query

    short = short_role(role)
    base = (prompt or "").strip().splitlines()[0:1]
    base_text = base[0].strip() if base else ""
    canonical = canonicalize_query(base_text)

    parts: list[str] = []
    if canonical.canonical:
        parts.append(canonical.canonical)
    if task_type:
        parts.append(task_type.strip())
    parts.extend(s for s in (extra_keywords or ()) if s and s.strip())
    parts.extend(ROLE_QUERY_BOOSTS.get(short, ()))

    seen: dict[str, None] = {}
    for token in parts:
        cleaned = (token or "").strip()
        if cleaned and cleaned.lower() not in seen:
            seen[cleaned.lower()] = None

    return " ".join(seen.keys()).strip(), canonical


# ---------------------------------------------------------------------------
# Mock collector (extracted → collector_mock.py)
# ---------------------------------------------------------------------------
#
# The deterministic role-aware canned collector (``_MockHit`` /
# ``_MOCK_BUCKETS`` / ``MockSearchCollector``) lives in ``collector_mock``.
# It's re-exported here so the factory (in ``collector_providers``) and
# tests keep importing it from ``collector``. This import lands after the
# core base interface / ``compute_confidence`` / ``short_role`` it depends
# on, and *before* the providers re-export that returns ``MockSearchCollector``.
from .collector_mock import (  # noqa: E402,F401
    _MOCK_BUCKETS,
    _MockHit,
    MockSearchCollector,
)

# ---------------------------------------------------------------------------
# Provider adapters (extracted → collector_providers.py)
# ---------------------------------------------------------------------------
#
# The per-provider fetch/parse logic (Tavily/Brave skeletons), generic
# result coercion, domain → SourceType classification, the multi-provider
# composite, the env-driven factory (``build_collector`` /
# ``_build_auto_collector``) and the HTTP helpers were extracted to
# ``collector_providers``. They're re-exported here so the public surface
# of ``collector`` is unchanged for callers and tests. ``collector_providers``
# imports the core interface/config/mock collector defined *above* — this
# import lands after those definitions so there's no import-time cycle.
from .collector_providers import (  # noqa: E402,F401
    BraveSearchCollector,
    MultiProviderCollector,
    TavilySearchCollector,
    _build_auto_collector,
    _classify_remote_source_type,
    _dedupe_sources,
    _first_provider_score,
    _first_string,
    _first_thumbnail,
    _http_get_json,
    _http_post_json,
    _normalize_url,
    _result_dict_to_source,
    _with_provider_rank,
    build_collector,
    extract_domain,
)

# ---------------------------------------------------------------------------
# Pack assembly
# ---------------------------------------------------------------------------


def collect_research_pack(
    *,
    collector: ResearchCollector,
    role: str,
    prompt: str,
    task_type: Optional[str] = None,
    user_links: Sequence[str] = (),
    user_attachments: Sequence[ResearchAttachment] = (),
    request_id: Optional[str] = None,
    session_id: Optional[str] = None,
    max_results: int = DEFAULT_MAX_RESULTS,
    extra_keywords: Sequence[str] = (),
    budget: Optional[BudgetTracker] = None,
) -> ResearchPack:
    """Run one collection pass and assemble a :class:`ResearchPack`.

    The pack always contains a USER_MESSAGE source mirroring *prompt*.
    User-supplied links become URL sources, user-supplied attachments
    become FILE_ATTACHMENT (or IMAGE_REFERENCE if the metadata says so)
    sources, and collector hits are appended on top with role-aware
    typing.
    """

    request = make_research_request(
        topic=prompt,
        role=role,
        session_id=session_id,
        request_id=request_id,
        context={"task_type": task_type or "unknown"},
    )

    sources: list[ResearchSource] = [
        source_from_user_message(
            content=prompt,
            collected_by_role=role,
        )
    ]

    for url in user_links:
        cleaned = (url or "").strip()
        if not cleaned:
            continue
        gh_meta = parse_github_url(cleaned)
        # GitHub issue/PR URL은 user-provided이더라도 정확한 source_type으로 분류.
        if gh_meta is not None:
            user_source_type = (
                SourceType.GITHUB_ISSUE
                if gh_meta["kind"] == "issue"
                else SourceType.GITHUB_PR
            )
            extra: dict[str, Any] = {
                "domain": extract_domain(cleaned),
                "query": "<user-provided>",
                "github": dict(gh_meta),
            }
        else:
            user_source_type = SourceType.URL
            extra = {
                "domain": extract_domain(cleaned),
                "query": "<user-provided>",
            }
        sources.append(
            ResearchSource(
                source_type=user_source_type,
                source_url=cleaned,
                title=cleaned,
                summary=None,
                collected_by_role=role,
                why_relevant="사용자 제공 링크 — 1순위 reference",
                confidence=CONFIDENCE_HIGH,
                collected_at=datetime.utcnow(),
                extra=extra,
            )
        )

    for att in user_attachments:
        # Honour the user's actual attachment shape; we only surface metadata.
        sources.append(
            ResearchSource(
                source_type=(
                    SourceType.IMAGE_REFERENCE
                    if (att.kind or "").lower() == "image"
                    else SourceType.FILE_ATTACHMENT
                ),
                source_url=att.url or None,
                title=att.filename or att.kind or "(attachment)",
                summary=att.description,
                collected_by_role=role,
                why_relevant="사용자 첨부 — 1순위 reference",
                confidence="high",
                collected_at=datetime.utcnow(),
                attachments=(att,),
                attachment_id=att.attachment_id,
                extra={"query": "<user-provided>"},
            )
        )

    if budget is None:
        budget = BudgetTracker()

    query = build_query_for_role(
        role=role,
        prompt=prompt,
        task_type=task_type,
        extra_keywords=extra_keywords,
    )
    if query and budget.can_call():
        budget.record_call()
        try:
            web_hits = collector.search(
                CollectorQuery(
                    query=query,
                    role=role,
                    max_results=max_results,
                    task_type=task_type,
                )
            )
        except CollectorError:
            web_hits = ()
        except Exception:  # noqa: BLE001 - never crash the conversation flow
            web_hits = ()
        # Order role-preferred source_type buckets first, then the rest,
        # then trim to the per-role budget.
        ranked = _rank_sources_for_role(web_hits, role=role)
        ranked = budget.trim_results(ranked)
        sources.extend(ranked)

    pack_extra: dict[str, Any] = {}
    limit_note = budget.limit_note()
    if limit_note:
        pack_extra["budget_note"] = limit_note
    # Surface skipped-provider reasons (auto mode only) so the conversation
    # layer can render "Tavily skipped — TAVILY_API_KEY not set" instead of
    # silently dropping a provider.
    if isinstance(collector, MultiProviderCollector):
        skipped = collector.skipped_providers
        if skipped:
            pack_extra["auto_skipped_providers"] = dict(skipped)
        active = collector.active_providers
        if active:
            pack_extra["auto_active_providers"] = list(active)

    return pack_from_request(
        request=request,
        sources=tuple(sources),
        tags=("auto-collected",) if any(s.extra.get("provider") for s in sources if s.extra) else (),
        extra=pack_extra,
    )


def _rank_sources_for_role(
    sources: Sequence[ResearchSource],
    *,
    role: str,
) -> Tuple[ResearchSource, ...]:
    """Order *sources* using ``deliberation.ROLE_RESEARCH_PROFILES``."""

    profile = ROLE_RESEARCH_PROFILES.get(short_role(role), ())
    if not profile:
        return tuple(sources)
    rank_index: dict[str, int] = {value: idx for idx, value in enumerate(profile)}
    fallback = len(profile) + len(KNOWN_SOURCE_TYPES)

    def key(source: ResearchSource) -> int:
        type_value = (
            source.source_type.value
            if isinstance(source.source_type, SourceType)
            else str(source.source_type)
        )
        return rank_index.get(type_value, fallback)

    return tuple(sorted(sources, key=key))


# ---------------------------------------------------------------------------
# Outcome flow — collect first, ask user only when nothing
# ---------------------------------------------------------------------------


class CollectionMode(str, Enum):
    AUTO_COLLECTED = "auto_collected"
    USER_PROVIDED = "user_provided"
    NEEDS_USER_INPUT = "needs_user_input"


@dataclass(frozen=True)
class CollectionOutcome:
    """What the conversation layer should do next.

    - ``AUTO_COLLECTED`` — collector produced ≥1 web result. Run deliberation.
    - ``USER_PROVIDED`` — user already supplied links/attachments. Run deliberation.
    - ``NEEDS_USER_INPUT`` — nothing usable. Reply with *user_prompt*.

    ``sufficiency`` and ``iterations`` are filled when the collector loop
    iterated to satisfy per-role coverage (Phase 4). Both default to
    safe values so existing callers and round-trips don't break.
    """

    mode: CollectionMode
    pack: Optional[ResearchPack]
    user_prompt: Optional[str]
    collector_name: str
    query: str
    auto_collected_count: int
    sufficiency: Optional[Any] = None
    iterations: int = 1
    budget_tier: Optional[str] = None
    max_provider_calls: int = 0
    max_results_per_role: int = 0
    role_targets: Tuple[Tuple[str, int], ...] = ()
    stop_reason: Optional[str] = None
    under_covered_roles: Tuple[str, ...] = ()
    # Roles the tech-lead picked for this task. Empty when the caller
    # didn't pass ``active_roles`` (legacy "all 7 roles" behaviour).
    # Surfaced so the gateway / Discord preview / Obsidian work-report
    # can show *who* participated without re-running role_selection.
    active_roles: Tuple[str, ...] = ()
    # P0-F query canonicalization metadata.
    raw_query: str = ""
    canonical_query: str = ""
    normalization_applied: bool = False
    normalization_confidence: float = 1.0
    # P0-F guard: True when a low-confidence canonicalization landed
    # on a mock-fallback collector. Gateway treats this as "do not
    # publish to forum without user clarification" — the canned
    # mock result for a typo'd query is almost never what the user
    # actually wanted.
    suppress_auto_publish: bool = False


def auto_collect_or_request_more_input(
    *,
    role: str,
    prompt: str,
    task_type: Optional[str] = None,
    user_links: Sequence[str] = (),
    user_attachments: Sequence[ResearchAttachment] = (),
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    config: Optional[CollectorConfig] = None,
    collector: Optional[ResearchCollector] = None,
    active_roles: Sequence[str] = (),
) -> CollectionOutcome:
    """Top-level entry point for the conversation layer.

    *collector* is an injection seam for tests; production callers can
    pass ``None`` and let the env-driven factory decide.

    *active_roles* — role-selection result from
    :func:`agents.lifecycle.role_selection.recommend_active_roles`. When passed,
    the budget policy and per-role sufficiency targets are filtered to
    that set so the loop only chases coverage for the roles the
    tech-lead actually picked. Empty / unset preserves the legacy
    "all roles" behaviour.
    """

    cfg = config if config is not None else CollectorConfig.from_env()
    user_supplied = bool(user_links) or bool(user_attachments)

    # Task-aware budget policy. The hard caps come from CollectorConfig
    # (env-driven cost gate); the policy never asks for more than that.
    # The result feeds both BudgetTracker and the per-role sufficiency
    # targets used by the iterative loop.
    from .budget import (
        decide_budget,
        role_targets_to_sufficiency_targets,
    )

    policy = decide_budget(
        prompt=prompt,
        task_type=task_type,
        role_sequence=(),
        active_roles=active_roles,
        hard_cap_provider_calls=cfg.max_provider_calls,
        hard_cap_results_per_role=cfg.max_results_per_role,
    )
    budget = BudgetTracker(
        max_provider_calls=policy.max_provider_calls,
        max_results_per_role=policy.max_results_per_role,
    )
    chosen = collector or build_collector(cfg, budget=budget)
    pack = collect_research_pack(
        collector=chosen,
        role=role,
        prompt=prompt,
        task_type=task_type,
        user_links=user_links,
        user_attachments=user_attachments,
        session_id=session_id,
        request_id=request_id,
        max_results=cfg.max_results,
        budget=budget,
    )

    # Iterate until sufficiency target is met, the budget is exhausted,
    # or two consecutive rounds add no new URLs (canned/stale provider).
    sufficiency_targets = role_targets_to_sufficiency_targets(policy)
    pack, iterations, sufficiency, stop_reason = _extend_pack_until_sufficient(
        pack=pack,
        collector=chosen,
        budget=budget,
        prompt=prompt,
        task_type=task_type,
        primary_role=role,
        max_results=cfg.max_results,
        sufficiency_targets=sufficiency_targets,
    )

    role_targets_tuple = tuple(
        (target.role, target.min_sources) for target in policy.role_targets
    )
    under_covered: Tuple[str, ...] = ()
    if sufficiency is not None:
        try:
            from .sufficiency import under_covered_roles as _under

            under_covered = tuple(_under(sufficiency))
        except Exception:  # noqa: BLE001 - defensive
            under_covered = ()

    # Count sources stamped by *some* provider (mock/tavily/brave/live).
    # User-supplied URLs/attachments use ``provider`` ∉ extra, so they don't
    # count even though they're valid reference material.
    auto_collected_count = sum(
        1 for source in pack.sources if (source.extra or {}).get("provider")
    )

    query, canonical = build_canonical_query_for_role(
        role=role, prompt=prompt, task_type=task_type
    )

    # P0-F: mock-fallback + low-confidence typo correction = do not
    # auto-publish to forum. The mock provider returns canned hits
    # keyed off the query token, so a fuzzy-rewritten typo will
    # surface plausible-looking but unrelated references.
    collector_is_mock = chosen.name == "mock"
    suppress_auto_publish = (
        collector_is_mock
        and canonical.normalization_applied
        and canonical.confidence < 0.7
    )

    common_extras = {
        "budget_tier": policy.tier,
        "max_provider_calls": policy.max_provider_calls,
        "max_results_per_role": policy.max_results_per_role,
        "role_targets": role_targets_tuple,
        "stop_reason": stop_reason,
        "under_covered_roles": under_covered,
        "active_roles": tuple(r for r in (active_roles or ()) if r),
        "raw_query": canonical.raw,
        "canonical_query": canonical.canonical,
        "normalization_applied": canonical.normalization_applied,
        "normalization_confidence": canonical.confidence,
        "suppress_auto_publish": suppress_auto_publish,
    }

    if auto_collected_count > 0:
        return CollectionOutcome(
            mode=CollectionMode.AUTO_COLLECTED,
            pack=pack,
            user_prompt=None,
            collector_name=chosen.name,
            query=query,
            auto_collected_count=auto_collected_count,
            sufficiency=sufficiency,
            iterations=iterations,
            **common_extras,
        )
    if user_supplied:
        return CollectionOutcome(
            mode=CollectionMode.USER_PROVIDED,
            pack=pack,
            user_prompt=None,
            collector_name=chosen.name,
            query=query,
            auto_collected_count=0,
            sufficiency=sufficiency,
            iterations=iterations,
            **common_extras,
        )
    return CollectionOutcome(
        mode=CollectionMode.NEEDS_USER_INPUT,
        pack=None,
        user_prompt=_format_user_input_request(role=role, task_type=task_type),
        collector_name=chosen.name,
        query=query,
        auto_collected_count=0,
        sufficiency=sufficiency,
        iterations=iterations,
        **common_extras,
    )


# ---------------------------------------------------------------------------
# Sufficiency-driven follow-up collection (extracted → collector_loop.py)
# ---------------------------------------------------------------------------
#
# The iterative "keep collecting until coverage is good enough" loop and
# its helpers live in ``collector_loop``. They're re-exported here so the
# orchestration entry points below (``auto_collect_or_request_more_input``)
# can call them and tests keep their import paths. ``collector_loop``
# imports the core query/rank/base symbols defined *above*, so this import
# lands after them with no import-time cycle.
from .collector_loop import (  # noqa: E402,F401
    _FOLLOWUP_ROLE_ORDER,
    _append_sources,
    _extend_pack_until_sufficient,
    _format_user_input_request,
    _next_followup_role,
)

# ---------------------------------------------------------------------------
# Forum-friendly summary + user-facing labels (extracted → collector_format.py)
# ---------------------------------------------------------------------------
#
# The centralised Korean labels, the ``pretty_*`` translators (and their
# backwards-compatible ``_pretty_*`` aliases) and ``format_collection_summary``
# were extracted to ``collector_format``. They're re-exported here so callers
# that ``from ..collector import format_collection_summary`` (etc.) keep
# working. ``collector_format`` imports the core ``CONFIDENCE_*`` constants /
# ``short_role`` and the providers' ``extract_domain``; this import lands at
# the bottom of the module so those names already exist.
from .collector_format import (  # noqa: E402,F401
    CONFIDENCE_LABELS,
    PROVIDER_LABELS,
    SOURCE_TYPE_LABELS,
    TASK_TYPE_LABELS,
    _pretty_confidence,
    _pretty_provider_summary,
    _pretty_source_type,
    _summarize_topic_for_summary,
    format_collection_summary,
    pretty_confidence,
    pretty_provider,
    pretty_source_type,
    pretty_task_type,
)
