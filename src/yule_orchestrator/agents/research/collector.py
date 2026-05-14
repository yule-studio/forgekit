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

import json
import os
import re
import urllib.parse
import urllib.request
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
# Env config
# ---------------------------------------------------------------------------


ENV_AUTO_COLLECT_ENABLED = "ENGINEERING_RESEARCH_AUTO_COLLECT_ENABLED"
ENV_PROVIDER = "ENGINEERING_RESEARCH_PROVIDER"
ENV_PROVIDERS = "ENGINEERING_RESEARCH_PROVIDERS"  # auto-mode candidate list
ENV_MAX_RESULTS = "ENGINEERING_RESEARCH_MAX_RESULTS"
ENV_MAX_PROVIDER_CALLS = "ENGINEERING_RESEARCH_MAX_PROVIDER_CALLS"
ENV_MAX_RESULTS_PER_ROLE = "ENGINEERING_RESEARCH_MAX_RESULTS_PER_ROLE"
ENV_FORUM_COMMENT_MODE = "ENGINEERING_RESEARCH_FORUM_COMMENT_MODE"


# Forum comment publishing modes:
# - "member-bots" (default): gateway posts the forum thread + one
#   research-open directive, and each member bot adds its own role
#   comment from its own account so the team feels real.
# - "gateway": legacy fallback — gateway posts every role comment
#   itself. Used during Phase 1 / when member bots aren't booted.
FORUM_COMMENT_MODE_MEMBER_BOTS = "member-bots"
FORUM_COMMENT_MODE_GATEWAY = "gateway"
FORUM_COMMENT_MODES: Tuple[str, ...] = (
    FORUM_COMMENT_MODE_MEMBER_BOTS,
    FORUM_COMMENT_MODE_GATEWAY,
)
DEFAULT_FORUM_COMMENT_MODE = FORUM_COMMENT_MODE_MEMBER_BOTS


def resolve_forum_comment_mode(env: Optional[Mapping[str, str]] = None) -> str:
    """Return ``"member-bots"`` or ``"gateway"`` from env, with safe fallback."""

    env_map: Mapping[str, str] = env if env is not None else os.environ
    raw = (env_map.get(ENV_FORUM_COMMENT_MODE) or "").strip().lower()
    if raw in FORUM_COMMENT_MODES:
        return raw
    return DEFAULT_FORUM_COMMENT_MODE

ENV_TAVILY_API_KEY = "TAVILY_API_KEY"
ENV_BRAVE_API_KEY = "BRAVE_SEARCH_API_KEY"


PROVIDER_MOCK = "mock"
PROVIDER_TAVILY = "tavily"
PROVIDER_BRAVE = "brave"
PROVIDER_AUTO = "auto"
# ``multi`` is accepted as an alias for ``auto`` so operators who think in
# "multi-provider" terms still get the same behaviour.
PROVIDER_MULTI = "multi"
KNOWN_PROVIDERS: Tuple[str, ...] = (
    PROVIDER_MOCK,
    PROVIDER_TAVILY,
    PROVIDER_BRAVE,
    PROVIDER_AUTO,
    PROVIDER_MULTI,
)
# Single-provider modes (i.e. not ``auto``/``multi``).
SINGLE_PROVIDER_MODES: Tuple[str, ...] = (PROVIDER_MOCK, PROVIDER_TAVILY, PROVIDER_BRAVE)
# External (network) providers we know how to dispatch in auto mode.
EXTERNAL_PROVIDERS: Tuple[str, ...] = (PROVIDER_TAVILY, PROVIDER_BRAVE)
# Default candidate set when ``ENGINEERING_RESEARCH_PROVIDERS`` is blank.
DEFAULT_AUTO_PROVIDERS: Tuple[str, ...] = (PROVIDER_TAVILY, PROVIDER_BRAVE)


# Per-role provider preference for ``auto`` / ``multi`` mode. The ordering
# matters: providers earlier in the tuple are queried first, and budget
# pressure stops the chain at the position the operator can afford.
#
# Trade-off:
# - Tavily ranks AI/RAG/agent material and synthesizes well — preferred for
#   tech-lead synthesis and ai-engineer.
# - Brave ranks official docs / GitHub / latest community signal — preferred
#   for backend / frontend / qa / devops / product-designer benchmarks.
DEFAULT_ROLE_PROVIDER_POLICY: Mapping[str, Tuple[str, ...]] = {
    # Gateway uses local memory; external search is opt-in. Returning ``()``
    # makes auto mode skip provider calls for this role entirely.
    "gateway": (),
    "tech-lead": (PROVIDER_TAVILY, PROVIDER_BRAVE),
    "ai-engineer": (PROVIDER_TAVILY, PROVIDER_BRAVE),
    "backend-engineer": (PROVIDER_BRAVE, PROVIDER_TAVILY),
    "frontend-engineer": (PROVIDER_BRAVE, PROVIDER_TAVILY),
    "product-designer": (PROVIDER_BRAVE, PROVIDER_TAVILY),
    "qa-engineer": (PROVIDER_BRAVE, PROVIDER_TAVILY),
    "devops-engineer": (PROVIDER_BRAVE, PROVIDER_TAVILY),
}

DEFAULT_MAX_RESULTS = 5
DEFAULT_MAX_PROVIDER_CALLS = 3
DEFAULT_MAX_RESULTS_PER_ROLE = 5

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"


@dataclass(frozen=True)
class CollectorConfig:
    """Resolved env config for the auto-collector.

    ``enabled=False`` means "skip collection entirely; jump straight to
    the user-input fallback". ``provider`` and ``max_results`` are still
    resolved so observability commands can show the operator what would
    happen if they flipped the flag.

    ``provider="auto"`` (or ``"multi"``) activates the multi-provider
    composite: each role's query is dispatched to the providers listed in
    ``providers`` (default ``("tavily", "brave")``) following the per-role
    priority defined by :data:`DEFAULT_ROLE_PROVIDER_POLICY`. Each provider
    looks up its own API key from ``api_keys`` so missing keys can be
    skipped without disturbing the rest.
    """

    enabled: bool
    provider: str
    max_results: int
    api_key: Optional[str] = None
    max_provider_calls: int = DEFAULT_MAX_PROVIDER_CALLS
    max_results_per_role: int = DEFAULT_MAX_RESULTS_PER_ROLE
    # Auto-mode candidate provider list, e.g. ``("tavily", "brave")``.
    # Empty for single-provider modes — the factory still works because it
    # only consults ``providers`` when ``provider`` is ``auto``/``multi``.
    providers: Tuple[str, ...] = ()
    # Provider name → api key mapping. Populated for every external
    # provider whose API key is set in env regardless of mode, so future
    # observability/debug surfaces can report which keys were available.
    api_keys: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_auto(self) -> bool:
        return self.provider in {PROVIDER_AUTO, PROVIDER_MULTI}

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "CollectorConfig":
        env_map: Mapping[str, str] = env if env is not None else os.environ

        enabled = _truthy(env_map.get(ENV_AUTO_COLLECT_ENABLED))
        provider_raw = (env_map.get(ENV_PROVIDER) or "").strip().lower() or PROVIDER_MOCK
        if provider_raw not in KNOWN_PROVIDERS:
            provider_raw = PROVIDER_MOCK
        max_results = _positive_int(
            env_map.get(ENV_MAX_RESULTS), default=DEFAULT_MAX_RESULTS
        )
        max_provider_calls = _positive_int(
            env_map.get(ENV_MAX_PROVIDER_CALLS), default=DEFAULT_MAX_PROVIDER_CALLS
        )
        max_results_per_role = _positive_int(
            env_map.get(ENV_MAX_RESULTS_PER_ROLE), default=DEFAULT_MAX_RESULTS_PER_ROLE
        )

        # Always collect every known external API key so auto/multi mode
        # can dispatch to whichever providers are configured. Single-provider
        # modes only need their own key but populating both is harmless.
        api_keys_raw: dict[str, str] = {}
        tavily_key = _strip_or_none(env_map.get(ENV_TAVILY_API_KEY))
        if tavily_key:
            api_keys_raw[PROVIDER_TAVILY] = tavily_key
        brave_key = _strip_or_none(env_map.get(ENV_BRAVE_API_KEY))
        if brave_key:
            api_keys_raw[PROVIDER_BRAVE] = brave_key

        # Legacy ``api_key`` field — points at the chosen single provider's
        # key. Kept so existing callers that read ``cfg.api_key`` still work.
        api_key: Optional[str] = None
        if provider_raw == PROVIDER_TAVILY:
            api_key = api_keys_raw.get(PROVIDER_TAVILY)
        elif provider_raw == PROVIDER_BRAVE:
            api_key = api_keys_raw.get(PROVIDER_BRAVE)

        # Auto/multi mode parses the ``ENGINEERING_RESEARCH_PROVIDERS``
        # candidate list. Unknown entries are dropped silently so a typo
        # doesn't disable the whole pipeline; the factory fills the gap
        # with the default list when the parsed result is empty.
        providers: Tuple[str, ...] = ()
        if provider_raw in {PROVIDER_AUTO, PROVIDER_MULTI}:
            providers = _parse_provider_list(
                env_map.get(ENV_PROVIDERS),
                allowed=EXTERNAL_PROVIDERS,
            ) or DEFAULT_AUTO_PROVIDERS

        return cls(
            enabled=enabled,
            provider=provider_raw,
            max_results=max_results,
            api_key=api_key,
            max_provider_calls=max_provider_calls,
            max_results_per_role=max_results_per_role,
            providers=providers,
            api_keys=api_keys_raw,
        )


def _parse_provider_list(
    raw: Optional[str], *, allowed: Sequence[str]
) -> Tuple[str, ...]:
    """Parse a comma-separated provider list, filtering to *allowed*.

    Trims whitespace, lowercases entries, drops blanks/duplicates and any
    name not in ``allowed``. Returns ``()`` when the result would be empty
    so callers can fall back to a default list.
    """

    if not raw:
        return ()
    seen: dict[str, None] = {}
    for token in str(raw).split(","):
        cleaned = token.strip().lower()
        if not cleaned or cleaned in seen:
            continue
        if cleaned not in allowed:
            continue
        seen[cleaned] = None
    return tuple(seen.keys())


def _truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _positive_int(value: Optional[str], *, default: int) -> int:
    if value is None or not str(value).strip():
        return default
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _strip_or_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# ---------------------------------------------------------------------------
# GitHub URL parsing (network-free)
# ---------------------------------------------------------------------------


_GITHUB_ISSUE_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[\w.\-]+)/(?P<repo>[\w.\-]+)/issues/(?P<number>\d+)",
    re.IGNORECASE,
)
_GITHUB_PR_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[\w.\-]+)/(?P<repo>[\w.\-]+)/pull/(?P<number>\d+)",
    re.IGNORECASE,
)


def parse_github_url(url: Optional[str]) -> Optional[Mapping[str, Any]]:
    """Extract ``{kind, owner, repo, number}`` from a GitHub issue/PR URL.

    Returns ``None`` for any other URL (including repo root, commit, etc.)
    so callers can fall through to generic classification.
    """

    if not url:
        return None
    text = str(url).strip()
    issue_match = _GITHUB_ISSUE_RE.match(text)
    if issue_match:
        groups = issue_match.groupdict()
        return {
            "kind": "issue",
            "owner": groups["owner"],
            "repo": groups["repo"],
            "number": int(groups["number"]),
        }
    pr_match = _GITHUB_PR_RE.match(text)
    if pr_match:
        groups = pr_match.groupdict()
        return {
            "kind": "pull_request",
            "owner": groups["owner"],
            "repo": groups["repo"],
            "number": int(groups["number"]),
        }
    return None


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
# Mock collector — deterministic role-aware canned results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _MockHit:
    title: str
    url: str
    domain: str
    snippet: str
    source_type: SourceType
    why_relevant: str
    risk_or_limit: Optional[str] = None
    thumbnail_url: Optional[str] = None


# Canned per-role hit sets. The mock cycles through these (modulated by the
# query) so different prompts get a different first hit, but the same prompt
# always returns the same ordering — handy for tests and debugging.
_MOCK_BUCKETS: Mapping[str, Tuple[_MockHit, ...]] = {
    "tech-lead": (
        _MockHit(
            title="ADR template — architecture decision record",
            url="https://github.com/joelparkerhenderson/architecture-decision-record",
            domain="github.com",
            snippet="Record context, decision, consequence — base ADR template.",
            source_type=SourceType.OFFICIAL_DOCS,
            why_relevant="작업 분해와 결정 기록 양식을 그대로 차용 가능",
        ),
        _MockHit(
            title="A Philosophy of Software Design — talk notes",
            url="https://blog.acolyer.org/2018/09/04/a-philosophy-of-software-design/",
            domain="blog.acolyer.org",
            snippet="Module 분해와 의존 순서 결정에 대한 정리 노트.",
            source_type=SourceType.COMMUNITY_SIGNAL,
            why_relevant="작업 순서 결정 시 가독성/모듈성 trade-off 참고",
            risk_or_limit="블로그 요약본 — 원문 검증 필요",
        ),
        _MockHit(
            title="GitHub Issue: 기존 hero 회귀 추적",
            url="https://github.com/example/example/issues/42",
            domain="github.com",
            snippet="Issue body — hero 카피 변경 후 모바일 그리드 깨짐 보고.",
            source_type=SourceType.GITHUB_ISSUE,
            why_relevant="과거 회귀 패턴 — 같은 영역 변경 시 재현 위험",
        ),
    ),
    "product-designer": (
        _MockHit(
            title="Mobbin — landing hero patterns",
            url="https://mobbin.com/discover/landing-page",
            domain="mobbin.com",
            snippet="실제 출시된 모바일 앱의 랜딩 hero 섹션 캡처 모음.",
            source_type=SourceType.DESIGN_REFERENCE,
            why_relevant="hero 카피·CTA 배치 패턴 차용 후보 — Mobbin 스크린숏 가이드",
            risk_or_limit="Mobbin 약관: 직접 scraping 금지, OG/검색 결과 metadata만 사용",
            thumbnail_url="https://mobbin.com/static/preview/landing.png",
        ),
        _MockHit(
            title="Behance — 브랜딩 hero 컬렉션",
            url="https://www.behance.net/search/projects/landing%20hero",
            domain="behance.net",
            snippet="Behance에서 큐레이션된 hero 시안 큐레이션.",
            source_type=SourceType.DESIGN_REFERENCE,
            why_relevant="다양한 브랜드 톤 비교 — 단순 복제 금지, 차용 패턴만 정리",
            thumbnail_url="https://www.behance.net/preview/hero.jpg",
        ),
        _MockHit(
            title="Awwwards — Site of the Day (landing 카테고리)",
            url="https://www.awwwards.com/websites/landing-page/",
            domain="awwwards.com",
            snippet="Awwwards 큐레이션 — 인터랙션·애니메이션 레퍼런스.",
            source_type=SourceType.DESIGN_REFERENCE,
            why_relevant="모바일/데스크톱 전환 인터랙션 검토 후보",
            thumbnail_url="https://www.awwwards.com/preview/landing.jpg",
        ),
        _MockHit(
            title="Notefolio — 한국 디자이너 hero 시안",
            url="https://notefolio.net/categories/branding",
            domain="notefolio.net",
            snippet="Notefolio — 지역 감성 톤 참고용 포트폴리오 큐레이션.",
            source_type=SourceType.DESIGN_REFERENCE,
            why_relevant="한국 사용자 톤 검토에 적합 — 직접 scraping 대신 사용자 제공 링크 권장",
            risk_or_limit="Notefolio 약관: 자동 수집 민감 — 메타데이터만 보존",
        ),
    ),
    "backend-engineer": (
        _MockHit(
            title="FastAPI — Security 가이드",
            url="https://fastapi.tiangolo.com/tutorial/security/",
            domain="fastapi.tiangolo.com",
            snippet="OAuth2 / API key 인증 권장 패턴 공식 문서.",
            source_type=SourceType.OFFICIAL_DOCS,
            why_relevant="인증/권한 변경 시 공식 권장 패턴 따라 위험 최소화",
        ),
        _MockHit(
            title="PostgreSQL — Concurrency Control",
            url="https://www.postgresql.org/docs/current/mvcc.html",
            domain="postgresql.org",
            snippet="PostgreSQL MVCC 락 정책 — 마이그레이션 잠금 위험 점검용.",
            source_type=SourceType.OFFICIAL_DOCS,
            why_relevant="schema 변경 시 동시 작업 충돌 점검 근거",
        ),
        _MockHit(
            title="OWASP — Authentication Cheat Sheet",
            url="https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html",
            domain="cheatsheetseries.owasp.org",
            snippet="OWASP 인증 보안 권장 항목.",
            source_type=SourceType.OFFICIAL_DOCS,
            why_relevant="email 인증 / 토큰 저장 정책의 보안 기준",
        ),
    ),
    "frontend-engineer": (
        _MockHit(
            title="MDN — Accessibility · ARIA roles",
            url="https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA",
            domain="developer.mozilla.org",
            snippet="ARIA role / state / property 표준 정의.",
            source_type=SourceType.OFFICIAL_DOCS,
            why_relevant="hero CTA 접근성 점검 — role/aria-label 적용 기준",
        ),
        _MockHit(
            title="web.dev — Performance & Accessibility",
            url="https://web.dev/learn/accessibility/",
            domain="web.dev",
            snippet="web.dev 학습 트랙 — 접근성 / 성능 best practice.",
            source_type=SourceType.OFFICIAL_DOCS,
            why_relevant="모바일 hero 렌더링 성능 점검 가이드",
        ),
        _MockHit(
            title="React — Components & Composition",
            url="https://react.dev/learn",
            domain="react.dev",
            snippet="React 공식 문서 — 컴포넌트 분해 권장 패턴.",
            source_type=SourceType.OFFICIAL_DOCS,
            why_relevant="hero 컴포넌트 props/상태 분리 기준",
        ),
    ),
    "qa-engineer": (
        _MockHit(
            title="Playwright — Best Practices",
            url="https://playwright.dev/docs/best-practices",
            domain="playwright.dev",
            snippet="Playwright e2e 작성 권장 패턴 (locator/wait/visual).",
            source_type=SourceType.OFFICIAL_DOCS,
            why_relevant="hero 회귀 e2e 시나리오 작성 기준",
        ),
        _MockHit(
            title="Testing Library — Guiding Principles",
            url="https://testing-library.com/docs/guiding-principles",
            domain="testing-library.com",
            snippet="사용자 관점 테스트 작성 원칙.",
            source_type=SourceType.OFFICIAL_DOCS,
            why_relevant="hero CTA 접근성 단위 테스트 작성 근거",
        ),
        _MockHit(
            title="GitHub Issue: 기존 hero 회귀 누적",
            url="https://github.com/example/example/issues/42",
            domain="github.com",
            snippet="과거 hero 회귀 사례 누적 — 회귀 시나리오 입력으로 활용.",
            source_type=SourceType.GITHUB_ISSUE,
            why_relevant="회귀 케이스 우선순위 결정",
        ),
    ),
}


class MockSearchCollector(ResearchCollector):
    """Deterministic role-aware canned collector.

    Returns ``min(max_results, len(_MOCK_BUCKETS[role]))`` hits drawn from
    the role's bucket. The first hit is rotated based on a stable hash of
    the query so the same prompt always sees the same first hit, but
    different prompts see different first hits — useful for showing
    operators that the collector is "alive" without ever leaving the
    process.
    """

    name = "mock"

    def search(self, query: CollectorQuery) -> Sequence[ResearchSource]:
        bucket = _MOCK_BUCKETS.get(short_role(query.role), ())
        if not bucket:
            return ()
        offset = (abs(hash(query.query)) if query.query else 0) % len(bucket)
        ordered = bucket[offset:] + bucket[:offset]
        capped = ordered[: max(1, query.max_results)]
        collected_at = datetime.utcnow()
        return tuple(
            self._hit_to_source(hit, query=query, collected_at=collected_at)
            for hit in capped
        )

    @staticmethod
    def _hit_to_source(
        hit: _MockHit,
        *,
        query: CollectorQuery,
        collected_at: datetime,
    ) -> ResearchSource:
        attachments: Tuple[ResearchAttachment, ...] = ()
        if hit.thumbnail_url:
            attachments = (
                ResearchAttachment(
                    kind="image",
                    url=hit.thumbnail_url,
                    description="thumbnail (metadata only — 이미지 원본 저장 안 함)",
                ),
            )
        gh_meta = parse_github_url(hit.url)
        extra: dict[str, Any] = {
            "domain": hit.domain,
            "snippet": hit.snippet,
            "thumbnail_url": hit.thumbnail_url,
            "query": query.query,
            "provider": "mock",
        }
        if gh_meta is not None:
            extra["github"] = dict(gh_meta)
        # Mock hits have curated metadata so we score with high signal.
        confidence = compute_confidence(
            source_type=hit.source_type,
            role=query.role,
            has_url=bool(hit.url),
            has_snippet=bool(hit.snippet),
            has_thumbnail=bool(hit.thumbnail_url),
        )
        return ResearchSource(
            source_type=hit.source_type,
            source_url=hit.url,
            title=hit.title,
            summary=hit.snippet,
            collected_by_role=query.role,
            why_relevant=hit.why_relevant,
            risk_or_limit=hit.risk_or_limit,
            collected_at=collected_at,
            confidence=confidence,
            attachments=attachments,
            extra=extra,
        )


# ---------------------------------------------------------------------------
# Provider skeletons (Tavily / Brave) — never invoked in tests
# ---------------------------------------------------------------------------


class TavilySearchCollector(ResearchCollector):
    """Skeleton Tavily collector — used when api_key is set.

    Calls ``https://api.tavily.com/search``. Tests don't exercise this
    path because :func:`build_collector` falls back to mock when keys
    are missing.
    """

    name = "tavily"
    endpoint = "https://api.tavily.com/search"

    def __init__(self, *, api_key: str, timeout_seconds: int = 10) -> None:
        if not api_key:
            raise ProviderUnavailable("tavily provider requires an api_key")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def search(self, query: CollectorQuery) -> Sequence[ResearchSource]:
        payload = {
            "api_key": self.api_key,
            "query": query.query,
            "max_results": max(1, query.max_results),
        }
        try:
            data = _http_post_json(
                self.endpoint, payload=payload, timeout_seconds=self.timeout_seconds
            )
        except Exception as exc:  # noqa: BLE001 - surface as collector error
            raise CollectorError(f"tavily search failed: {exc}") from exc
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            return ()
        collected_at = datetime.utcnow()
        return tuple(
            _result_dict_to_source(
                item, query=query, collected_at=collected_at, provider="tavily"
            )
            for item in results
            if isinstance(item, dict)
        )


class BraveSearchCollector(ResearchCollector):
    """Skeleton Brave Search collector. Auth via ``X-Subscription-Token`` header."""

    name = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, *, api_key: str, timeout_seconds: int = 10) -> None:
        if not api_key:
            raise ProviderUnavailable("brave provider requires an api_key")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def search(self, query: CollectorQuery) -> Sequence[ResearchSource]:
        url = self.endpoint + "?" + urllib.parse.urlencode(
            {"q": query.query, "count": max(1, query.max_results)}
        )
        try:
            data = _http_get_json(
                url,
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.api_key,
                },
                timeout_seconds=self.timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            raise CollectorError(f"brave search failed: {exc}") from exc
        web = data.get("web") if isinstance(data, dict) else None
        results = web.get("results") if isinstance(web, dict) else None
        if not isinstance(results, list):
            return ()
        collected_at = datetime.utcnow()
        return tuple(
            _result_dict_to_source(
                item, query=query, collected_at=collected_at, provider="brave"
            )
            for item in results
            if isinstance(item, dict)
        )


_TITLE_KEYS = ("title", "name", "headline", "heading")
_URL_KEYS = ("url", "link", "href", "web_url")
_SNIPPET_KEYS = ("snippet", "description", "content", "summary", "body", "excerpt")
_THUMBNAIL_KEYS = ("thumbnail", "image", "image_url", "favicon", "thumb")
_SCORE_KEYS = ("score", "relevance", "relevance_score", "confidence")


def _first_string(item: Mapping[str, Any], keys: Sequence[str]) -> str:
    """Return the first non-empty string under any of *keys* (or empty)."""

    for key in keys:
        value = item.get(key) if isinstance(item, Mapping) else None
        if value is None:
            continue
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return ""


def _first_thumbnail(item: Mapping[str, Any]) -> Optional[str]:
    """Robustly extract a thumbnail URL from various provider shapes.

    Handles plain strings, ``{"src": ...}``, ``{"url": ...}``, and
    ``[{"url": ...}, ...]`` lists. Returns ``None`` if nothing usable.
    """

    for key in _THUMBNAIL_KEYS:
        value = item.get(key) if isinstance(item, Mapping) else None
        if value is None:
            continue
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
        elif isinstance(value, Mapping):
            for sub in ("src", "url", "href"):
                sub_value = value.get(sub)
                if isinstance(sub_value, str) and sub_value.strip():
                    return sub_value.strip()
        elif isinstance(value, (list, tuple)) and value:
            for entry in value:
                if isinstance(entry, str) and entry.strip():
                    return entry.strip()
                if isinstance(entry, Mapping):
                    for sub in ("src", "url", "href"):
                        sub_value = entry.get(sub)
                        if isinstance(sub_value, str) and sub_value.strip():
                            return sub_value.strip()
    return None


def _first_provider_score(item: Mapping[str, Any]) -> Optional[float]:
    """Return a numeric provider score in [0, 1] when surfaced."""

    for key in _SCORE_KEYS:
        value = item.get(key) if isinstance(item, Mapping) else None
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _result_dict_to_source(
    item: Mapping[str, Any],
    *,
    query: CollectorQuery,
    collected_at: datetime,
    provider: str = "live",
) -> ResearchSource:
    """Coerce a generic provider result into our :class:`ResearchSource` shape.

    Defensive: tolerates field-name variations across providers, missing
    fields, dict/list-shaped thumbnails, and unknown extra keys. Returns
    a usable :class:`ResearchSource` even when most fields are absent —
    a placeholder title (``"(untitled)"``) keeps the pack renderable.
    """

    if not isinstance(item, Mapping):
        item = {}

    title = _first_string(item, _TITLE_KEYS) or "(untitled)"
    url = _first_string(item, _URL_KEYS)
    snippet = _first_string(item, _SNIPPET_KEYS)
    thumbnail = _first_thumbnail(item)
    provider_score = _first_provider_score(item)
    domain = extract_domain(url) if url else ""

    attachments: Tuple[ResearchAttachment, ...] = ()
    if thumbnail:
        attachments = (
            ResearchAttachment(
                kind="image",
                url=thumbnail,
                description="thumbnail (metadata only — 이미지 원본 저장 안 함)",
            ),
        )

    source_type = _classify_remote_source_type(domain, query.role, url=url or None)
    gh_meta = parse_github_url(url) if url else None

    extra: dict[str, Any] = {
        "domain": domain,
        "snippet": snippet or None,
        "thumbnail_url": thumbnail,
        "query": query.query,
        "provider": provider,
    }
    if provider_score is not None:
        extra["provider_score"] = provider_score
    if gh_meta is not None:
        extra["github"] = dict(gh_meta)

    confidence = compute_confidence(
        source_type=source_type,
        role=query.role,
        has_url=bool(url),
        has_snippet=bool(snippet),
        has_thumbnail=bool(thumbnail),
        provider_score=provider_score,
    )

    return ResearchSource(
        source_type=source_type,
        source_url=url or None,
        title=title,
        summary=snippet or None,
        collected_by_role=query.role,
        why_relevant=None,
        collected_at=collected_at,
        confidence=confidence,
        attachments=attachments,
        extra=extra,
    )


# ---------------------------------------------------------------------------
# Domain → SourceType classification (for live providers)
# ---------------------------------------------------------------------------


_DESIGN_DOMAINS = (
    "behance.net",
    "awwwards.com",
    "mobbin.com",
    "notefolio.net",
    "dribbble.com",
    "pinterest.com",
    "canva.com",
    "wix.com",
)
_OFFICIAL_HINTS = (
    "developer.mozilla.org",
    "web.dev",
    "react.dev",
    "vuejs.org",
    "angular.io",
    "nextjs.org",
    "fastapi.tiangolo.com",
    "django",
    "postgresql.org",
    "playwright.dev",
    "testing-library.com",
    "owasp.org",
    "rfc-editor.org",
)


def _classify_remote_source_type(
    domain: str,
    role: str,
    *,
    url: Optional[str] = None,
) -> SourceType:
    """Best-effort source_type based on URL/domain only (no fetch).

    GitHub issue/PR URLs are recognised explicitly; everything else falls
    back to the domain-based heuristic.
    """

    if url:
        gh = parse_github_url(url)
        if gh is not None:
            return (
                SourceType.GITHUB_ISSUE
                if gh["kind"] == "issue"
                else SourceType.GITHUB_PR
            )

    short = (domain or "").lower()
    if any(d in short for d in _DESIGN_DOMAINS):
        return SourceType.DESIGN_REFERENCE
    if any(d in short for d in _OFFICIAL_HINTS):
        return SourceType.OFFICIAL_DOCS
    if "github.com" in short:
        # repo root / commit / wiki / etc — surface as official_docs so the
        # role profile still ranks it ahead of generic web results.
        return SourceType.OFFICIAL_DOCS
    if "reddit.com" in short or "forum" in short or "stackoverflow.com" in short:
        return SourceType.COMMUNITY_SIGNAL
    return SourceType.WEB_RESULT


def extract_domain(url: Optional[str]) -> str:
    """Return ``host[:port]`` (lower-cased) for *url*, or ``""``."""

    if not url:
        return ""
    try:
        parsed = urllib.parse.urlparse(str(url))
    except Exception:  # noqa: BLE001
        return ""
    return (parsed.netloc or "").lower()


# ---------------------------------------------------------------------------
# Multi-provider composite (auto / multi mode)
# ---------------------------------------------------------------------------


class MultiProviderCollector(ResearchCollector):
    """Auto-mode composite that fans out to multiple sub-collectors.

    For each :meth:`search` call the composite walks a role-specific
    provider order (``role_policy``), invokes each available sub-collector
    in turn, and returns the deduped/merged hits. Providers whose name is
    in ``role_policy`` but absent from ``providers`` (e.g. their API key
    wasn't set) are skipped silently — :attr:`skipped_providers` lists
    them with the reason for observability.

    Budget bookkeeping:

    - The outer ``collect_research_pack`` loop calls ``budget.record_call()``
      once per role-level :meth:`search`. The composite counts that as the
      *first* inner provider's slot for free.
    - Every additional inner provider beyond the first claims another
      ``budget.record_call()`` if and only if ``budget.can_call()``. This
      keeps the operator's ``ENGINEERING_RESEARCH_MAX_PROVIDER_CALLS``
      cap as a true ceiling on total provider invocations across the
      whole collection run.
    """

    name = "auto"

    def __init__(
        self,
        *,
        providers: Sequence[ResearchCollector],
        role_policy: Optional[Mapping[str, Tuple[str, ...]]] = None,
        budget: Optional[BudgetTracker] = None,
        skipped: Optional[Mapping[str, str]] = None,
    ) -> None:
        # Map each provider's ``name`` → instance so role policy lookup is O(1).
        self._provider_map: dict[str, ResearchCollector] = {}
        for collector in providers:
            self._provider_map[collector.name] = collector
        self._role_policy: Mapping[str, Tuple[str, ...]] = (
            role_policy if role_policy is not None else DEFAULT_ROLE_PROVIDER_POLICY
        )
        self._budget = budget
        self._skipped_providers: dict[str, str] = dict(skipped or {})
        # Inner provider call counter for observability — independent of the
        # outer BudgetTracker so tests can verify both behaviours.
        self._inner_calls = 0

    @property
    def active_providers(self) -> Tuple[str, ...]:
        return tuple(self._provider_map.keys())

    @property
    def skipped_providers(self) -> Mapping[str, str]:
        return dict(self._skipped_providers)

    @property
    def inner_calls(self) -> int:
        return self._inner_calls

    def provider_order_for_role(self, role: str) -> Tuple[str, ...]:
        """Return the ordered provider names this composite would query
        for *role*. Filters to providers actually present in the composite.

        Unknown roles fall back to ``DEFAULT_AUTO_PROVIDERS`` so the chain
        still does something useful instead of silently returning empty.
        """

        short = short_role(role)
        configured = self._role_policy.get(short)
        if configured is None:
            configured = DEFAULT_AUTO_PROVIDERS
        return tuple(name for name in configured if name in self._provider_map)

    def search(self, query: CollectorQuery) -> Sequence[ResearchSource]:
        order = self.provider_order_for_role(query.role)
        if not order:
            return ()
        merged: list[ResearchSource] = []
        first_call_consumed = False
        for provider_name in order:
            provider = self._provider_map.get(provider_name)
            if provider is None:
                continue
            if first_call_consumed:
                # The outer loop only paid for one budget slot; second-and-
                # later providers in this role's policy must claim their own.
                if self._budget is not None:
                    if not self._budget.can_call():
                        break
                    self._budget.record_call()
            first_call_consumed = True
            try:
                hits = provider.search(query)
            except CollectorError:
                hits = ()
            except Exception:  # noqa: BLE001 - never crash the composite
                hits = ()
            self._inner_calls += 1
            # Stamp provider rank inside extra so downstream rendering can
            # show "1순위 검색 — Tavily" / "2순위 검색 — Brave" without
            # re-deriving the order.
            ranked_hits: list[ResearchSource] = []
            for idx, src in enumerate(hits):
                ranked_hits.append(_with_provider_rank(src, provider_name, idx))
            merged.extend(ranked_hits)
        return _dedupe_sources(merged)


def _with_provider_rank(
    source: ResearchSource, provider: str, rank: int
) -> ResearchSource:
    """Return *source* with ``provider`` and ``provider_rank`` in extra.

    Underlying collectors already stamp ``provider``; the multi composite
    re-stamps it (defensively) and adds ``provider_rank`` so downstream
    sort/UI can preserve "this came from the 1st provider in policy".
    """

    base_extra = dict(getattr(source, "extra", {}) or {})
    base_extra.setdefault("provider", provider)
    base_extra["provider_rank"] = rank
    return ResearchSource(
        source_type=source.source_type,
        source_url=source.source_url,
        title=source.title,
        summary=source.summary,
        collected_by_role=source.collected_by_role,
        why_relevant=source.why_relevant,
        risk_or_limit=source.risk_or_limit,
        confidence=source.confidence,
        collected_at=source.collected_at,
        attachments=source.attachments,
        attachment_id=source.attachment_id,
        author_role=getattr(source, "author_role", None),
        extra=base_extra,
    )


def _normalize_url(url: Optional[str]) -> str:
    """Return a canonical form for *url* used for dedupe keys.

    Lower-cases scheme+host, strips trailing slashes from the path, drops
    fragments, and ignores common tracking query keys (``utm_*``). Two
    URLs that differ only in those things end up with the same key.
    """

    if not url:
        return ""
    text = str(url).strip()
    if not text:
        return ""
    try:
        parsed = urllib.parse.urlsplit(text)
    except Exception:  # noqa: BLE001 - defensive
        return text.lower()
    scheme = (parsed.scheme or "").lower()
    netloc = (parsed.netloc or "").lower()
    path = (parsed.path or "").rstrip("/")
    # Drop tracking params; keep everything else so legitimate query
    # parameters still distinguish unique pages.
    if parsed.query:
        kept = []
        for part in parsed.query.split("&"):
            if not part:
                continue
            key = part.split("=", 1)[0].lower()
            if key.startswith("utm_") or key in {"ref", "ref_src"}:
                continue
            kept.append(part)
        query = "&".join(kept)
    else:
        query = ""
    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


def _dedupe_sources(
    sources: Sequence[ResearchSource],
) -> Tuple[ResearchSource, ...]:
    """Drop duplicate sources from *sources* in arrival order.

    Dedupe keys (any match collapses to first occurrence):

    1. Normalised URL (lowercase scheme/host, no trailing slash, no UTM).
    2. ``(domain, source_type)`` + lower-cased title prefix — catches
       providers that return the same page under slightly different URL
       shapes (mobile/desktop variants, AMP, language redirects).
    3. URL-less sources fall back to ``(title, source_type)``.
    """

    seen_urls: set[str] = set()
    seen_titles: set[Tuple[str, str]] = set()
    seen_dom_type_title: set[Tuple[str, str, str]] = set()
    deduped: list[ResearchSource] = []
    for src in sources:
        url_key = _normalize_url(src.source_url)
        type_value = (
            src.source_type.value
            if isinstance(src.source_type, SourceType)
            else str(src.source_type)
        )
        title_key = (src.title or "").strip().lower()
        domain_key = ((src.extra or {}).get("domain") or extract_domain(src.source_url)).lower()

        if url_key:
            if url_key in seen_urls:
                continue
            seen_urls.add(url_key)
        else:
            tt = (title_key, type_value)
            if tt in seen_titles:
                continue
            seen_titles.add(tt)
        # Even when the URL differs, collapse near-duplicates that share
        # domain + type + title prefix (provider returned the same article
        # under two URLs, e.g. with/without query string).
        title_prefix = title_key[:80]
        composite = (domain_key, type_value, title_prefix)
        if title_prefix and composite in seen_dom_type_title:
            continue
        if title_prefix:
            seen_dom_type_title.add(composite)
        deduped.append(src)
    return tuple(deduped)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_collector(
    config: Optional[CollectorConfig] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
    budget: Optional[BudgetTracker] = None,
) -> ResearchCollector:
    """Resolve env config and return a usable collector.

    Fallback chain:
    - ``enabled=False`` → :class:`NoOpCollector` (always returns ``()``).
    - ``provider=mock`` (default) → :class:`MockSearchCollector`.
    - ``provider=tavily`` + ``TAVILY_API_KEY`` set → :class:`TavilySearchCollector`.
    - ``provider=brave`` + ``BRAVE_SEARCH_API_KEY`` set → :class:`BraveSearchCollector`.
    - ``provider=auto`` / ``multi`` → :class:`MultiProviderCollector` with
      every external provider whose API key is set; if no key is present,
      falls back to :class:`MockSearchCollector` (so dev/test runs stay
      deterministic without leaking the auto-mode contract).
    - Provider key missing → silent fallback to :class:`MockSearchCollector`.

    Pass ``budget`` to wire a shared :class:`BudgetTracker` into the auto
    composite; without it the composite still works but the outer cap can
    be exceeded by ``len(providers) - 1`` calls per role-level search.
    """

    cfg = config if config is not None else CollectorConfig.from_env(env)
    if not cfg.enabled:
        return NoOpCollector()
    if cfg.provider == PROVIDER_TAVILY and cfg.api_key:
        try:
            return TavilySearchCollector(api_key=cfg.api_key)
        except ProviderUnavailable:
            return MockSearchCollector()
    if cfg.provider == PROVIDER_BRAVE and cfg.api_key:
        try:
            return BraveSearchCollector(api_key=cfg.api_key)
        except ProviderUnavailable:
            return MockSearchCollector()
    if cfg.is_auto:
        return _build_auto_collector(cfg, budget=budget)
    return MockSearchCollector()


def _build_auto_collector(
    cfg: CollectorConfig,
    *,
    budget: Optional[BudgetTracker] = None,
) -> ResearchCollector:
    """Construct a :class:`MultiProviderCollector` from *cfg*.

    Honours ``cfg.providers`` as the candidate list; for each candidate
    we instantiate the live provider when its API key is set and record a
    ``skipped`` reason otherwise. If no candidate ends up usable, fall
    back to :class:`MockSearchCollector` so the rest of the pipeline keeps
    working in dev environments.
    """

    candidates = cfg.providers or DEFAULT_AUTO_PROVIDERS
    instances: list[ResearchCollector] = []
    skipped: dict[str, str] = {}
    for provider_name in candidates:
        if provider_name == PROVIDER_TAVILY:
            api_key = cfg.api_keys.get(PROVIDER_TAVILY)
            if not api_key:
                skipped[PROVIDER_TAVILY] = f"{ENV_TAVILY_API_KEY} not set"
                continue
            try:
                instances.append(TavilySearchCollector(api_key=api_key))
            except ProviderUnavailable as exc:
                skipped[PROVIDER_TAVILY] = str(exc)
        elif provider_name == PROVIDER_BRAVE:
            api_key = cfg.api_keys.get(PROVIDER_BRAVE)
            if not api_key:
                skipped[PROVIDER_BRAVE] = f"{ENV_BRAVE_API_KEY} not set"
                continue
            try:
                instances.append(BraveSearchCollector(api_key=api_key))
            except ProviderUnavailable as exc:
                skipped[PROVIDER_BRAVE] = str(exc)
        # Unknown providers were filtered out at parse time; defensive
        # branch left implicit so adding a new provider is one match arm.

    if not instances:
        # Every candidate skipped → mock fallback so dev/test runs work
        # without API keys. The skipped reasons are still surfaced via
        # the outcome metadata path so operators can see *why*.
        return MockSearchCollector()

    return MultiProviderCollector(
        providers=instances,
        budget=budget,
        skipped=skipped,
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


# ---------------------------------------------------------------------------
# Forum-friendly summary (for #운영-리서치 게시 시 호출자가 사용)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Centralised user-facing labels (re-used by conversation/forum/deliberation)
# ---------------------------------------------------------------------------


SOURCE_TYPE_LABELS: Mapping[str, str] = {
    "user_message": "사용자 요청",
    "url": "사용자 링크",
    "web_result": "웹 검색",
    "image_reference": "이미지 레퍼런스",
    "file_attachment": "첨부 파일",
    "github_issue": "GitHub 이슈",
    "github_pr": "GitHub PR",
    "code_context": "코드 맥락",
    "official_docs": "공식 문서",
    "community_signal": "커뮤니티 글",
    "design_reference": "디자인 레퍼런스",
    "unknown": "기타",
}


PROVIDER_LABELS: Mapping[str, str] = {
    "mock": "기본 검색(mock)",
    "tavily": "Tavily 검색",
    "brave": "Brave 검색",
    "noop": "비활성",
    "live": "외부 검색",
    "?": "알 수 없음",
}


TASK_TYPE_LABELS: Mapping[str, str] = {
    "landing-page": "랜딩 페이지",
    "onboarding-flow": "온보딩 흐름",
    "visual-polish": "비주얼 정리",
    "email-campaign": "이메일 캠페인",
    "qa-test": "QA 테스트",
    "platform-infra": "플랫폼/인프라",
    "frontend-feature": "프론트엔드",
    "backend-feature": "백엔드",
    "unknown": "일반",
}


CONFIDENCE_LABELS: Mapping[str, str] = {
    CONFIDENCE_HIGH: "신뢰도 높음",
    CONFIDENCE_MEDIUM: "신뢰도 보통",
    CONFIDENCE_LOW: "신뢰도 낮음",
}


def pretty_source_type(source_type: Any) -> str:
    """Translate a :class:`SourceType` (or its string value) into Korean.

    Unknown values fall through unchanged so a future enum addition still
    renders something readable instead of crashing.
    """

    if source_type is None:
        return SOURCE_TYPE_LABELS["unknown"]
    if isinstance(source_type, SourceType):
        value = source_type.value
    else:
        value = str(source_type)
    return SOURCE_TYPE_LABELS.get(value, value or SOURCE_TYPE_LABELS["unknown"])


def pretty_provider(name: Optional[str]) -> str:
    """Translate a collector provider id into Korean. Unknown → passthrough."""

    if not name:
        return PROVIDER_LABELS["?"]
    return PROVIDER_LABELS.get(name, name)


def pretty_task_type(value: Optional[str]) -> str:
    """Translate a dispatcher ``TaskType.value`` into Korean.

    Falls back to "일반" for missing/blank input and to the raw value
    otherwise (so ``"design-system"`` stays readable instead of crashing).
    """

    if not value:
        return TASK_TYPE_LABELS["unknown"]
    return TASK_TYPE_LABELS.get(value, value)


def pretty_confidence(value: Optional[str]) -> str:
    """Translate a confidence label (``high|medium|low``) into Korean."""

    label = (value or CONFIDENCE_MEDIUM).lower()
    return CONFIDENCE_LABELS.get(label, CONFIDENCE_LABELS[CONFIDENCE_MEDIUM])


# Backwards-compatible aliases (used internally before centralisation).
_pretty_source_type = pretty_source_type
_pretty_confidence = pretty_confidence
_pretty_provider_summary = pretty_provider


def _summarize_topic_for_summary(text: Optional[str], max_chars: int = 60) -> str:
    cleaned = [line.strip() for line in (text or "").splitlines() if line.strip()]
    head = cleaned[0] if cleaned else ""
    if not head:
        return "(요청 본문 없음)"
    if len(head) <= max_chars:
        return head
    return head[: max(1, max_chars - 1)].rstrip() + "…"


def format_collection_summary(
    pack: ResearchPack,
    *,
    collector_name: str,
    query: str,
    role: str,
    next_steps: Sequence[str] = (),
) -> str:
    """Render the autonomous-collection block in the team-lead voice.

    Designed to be dropped into ``format_research_post_body``. Internal
    jargon (collector / query / source_type values) is translated into
    human-friendly Korean labels. The raw user prompt is summarised to a
    short topic so it doesn't bloat the forum thread.

    Sections (each keeps 2~4 sentences):
    - 1차 자료 정리 — <역할 한국어>
    - 참고 자료 (count): per-source 짧은 라벨 + URL
    - 활용 방향: why_relevant 모음
    - 유의 사항: risk_or_limit + budget note
    - 다음 단계: 역할별 검토 흐름 안내
    - 수집 정보: 수집 방식 / 수집 자료
    """

    short = short_role(role)
    request_topic = (
        getattr(pack.request, "topic", None) if pack.request is not None else None
    ) or pack.title
    topic = _summarize_topic_for_summary(request_topic)

    body_count = sum(
        1 for s in pack.sources if s.source_type != SourceType.USER_MESSAGE
    )

    lines: list[str] = []
    lines.append(f"**📚 1차 자료 정리 — {short}**")
    lines.append("")
    lines.append(f"이번 정리는 “{topic}”에 대한 검토예요.")

    # 참고 자료
    lines.append("")
    lines.append(f"**참고 자료** ({body_count}건)")
    risks: list[str] = []
    why_relevants: list[str] = []
    if body_count == 0:
        lines.append(
            "- 아직 자동 수집된 자료가 없어요. 사용자에게 자료를 요청한 뒤 다시 정리할게요."
        )
    else:
        for source in pack.sources:
            if source.source_type == SourceType.USER_MESSAGE:
                continue
            domain = (source.extra or {}).get("domain") or extract_domain(source.source_url)
            title = source.title or "(제목 없음)"
            type_label = _pretty_source_type(source.source_type)
            confidence_label = _pretty_confidence(source.confidence)
            head_bits = [f"- **{title}** · {type_label} · {confidence_label}"]
            if domain:
                head_bits.append(f" · `{domain}`")
            lines.append("".join(head_bits))
            if source.source_url:
                lines.append(f"  ↪ {source.source_url}")
            if source.why_relevant:
                why_relevants.append(f"{title}: {source.why_relevant}")
            if source.risk_or_limit:
                risks.append(f"{title}: {source.risk_or_limit}")

    # 활용 방향
    if why_relevants:
        lines.append("")
        lines.append("**활용 방향**")
        for item in why_relevants:
            lines.append(f"- {item}")

    # 유의 사항
    budget_note = (pack.extra or {}).get("budget_note") if pack.extra else None
    if risks or budget_note:
        lines.append("")
        lines.append("**유의 사항**")
        for risk in risks:
            lines.append(f"- {risk}")
        if budget_note:
            lines.append(f"- {budget_note}")

    # 다음 단계
    lines.append("")
    lines.append("**다음 단계**")
    if next_steps:
        for step in next_steps:
            lines.append(f"- {step}")
    elif body_count > 0:
        lines.append("- 각 역할이 자기 관점으로 검토 → tech-lead가 합의안 정리")
    else:
        lines.append("- 사용자에게 추가 자료를 요청한 뒤 재수집")

    # 수집 정보 (메타)
    lines.append("")
    lines.append("수집 정보:")
    lines.append(f"- 수집 방식: {_pretty_provider_summary(collector_name)}")
    lines.append(f"- 수집 자료: {body_count}건")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTTP helpers (used by Tavily/Brave skeletons; not exercised in tests)
# ---------------------------------------------------------------------------


def _http_get_json(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout_seconds: int,
) -> Any:  # pragma: no cover - real network only
    request = urllib.request.Request(url, headers=dict(headers))
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body)


def _http_post_json(
    url: str,
    *,
    payload: Mapping[str, Any],
    timeout_seconds: int,
) -> Any:  # pragma: no cover - real network only
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body)
