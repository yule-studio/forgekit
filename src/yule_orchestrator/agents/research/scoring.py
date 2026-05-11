"""Trust / Freshness / Priority scoring + request-time ranking (F5 / #92).

본 모듈은 외부 live source 에서 ingest 된 :class:`LiveEvidence` 들에
대해 신뢰도(TrustScore) / 신선도(FreshnessScore) 를 부여하고, 요청
시점(``now``)과 역할(``role``)/태스크(``task_type``)에 따라 우선순위
ranking 을 산출한다.

설계 원칙:
- I/O 없음. 순수 dataclass + 산술 + frozenset 룩업.
- 점수는 0~10 정수 또는 0~10 float 척도 (직관/디버깅).
- ``rank_for_request`` 는 deterministic: 동일 입력 → 동일 ranking.
- 외부 fetch / 캐시 / robots 검증 책임은 본 모듈 영역 밖.

본 모듈은 :mod:`yule_orchestrator.agents.research.providers.live` 의
:class:`LiveEvidence` / :class:`LiveSource` 와 협업하며, 그 dataclass
정의는 ``providers/live/__init__.py`` 에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterable, Mapping, Sequence, Tuple

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .providers.live import LiveEvidence, LiveSource


# ---------------------------------------------------------------------------
# Trust 기준
# ---------------------------------------------------------------------------

# host → 기준 trust 점수(0~10). 운영자가 검증한 vendor / standards-body /
# 공식 docs 만 9~10 으로 박는다. 그 외 host 는 기본 5.
_TRUST_BASELINE: Mapping[str, int] = {
    # backend / language
    "python.org": 10,
    "fastapi.tiangolo.com": 9,
    "docs.sqlalchemy.org": 9,
    "owasp.org": 10,
    # frontend
    "developer.mozilla.org": 10,
    "react.dev": 9,
    "vuejs.org": 9,
    "typescriptlang.org": 9,
    # qa
    "playwright.dev": 9,
    "docs.cypress.io": 9,
    "vitest.dev": 9,
    # devops
    "docs.github.com": 10,
    "kubernetes.io": 10,
    "prometheus.io": 9,
    # tech-lead / industry signal
    "github.blog": 8,
    "cncf.io": 9,
    # github release source
    "github.com": 7,
}

# allow_listed 가 False 면 -3, robots_compliant 가 False 면 -5.
_TRUST_PENALTY_NOT_ALLOWLISTED: int = 3
_TRUST_PENALTY_ROBOTS_VIOLATION: int = 5


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrustScore:
    """Host / 정책 기반 신뢰도 점수.

    ``baseline`` 은 host 카탈로그에서 가져온 기준점 (없으면 5).
    ``penalties`` 는 (사유, 감점) 튜플 시퀀스. ``value`` 는 둘을 합산해
    0~10 으로 clip 된 최종 점수.
    """

    host: str
    baseline: int
    penalties: Tuple[Tuple[str, int], ...]
    value: int


@dataclass(frozen=True)
class FreshnessScore:
    """published_at vs now 기반 신선도 점수 (0~10).

    하루 미만 = 10, 7일 미만 = 8, 30일 미만 = 6, 180일 미만 = 4,
    365일 미만 = 2, 그 외 = 1. published_at 이 None 이면 3 (unknown).
    미래 시점은 0 (clock skew / 조작 의심).
    """

    age_seconds: int  # 음수 = 미래, -1 = unknown
    value: int


@dataclass(frozen=True)
class RankedEvidence:
    """:class:`LiveEvidence` + 점수 + 우선순위 결정 메타.

    ``priority`` 는 (trust + freshness + role_boost) / 3 의 round.
    동일 priority 일 때 (trust desc → freshness desc → published_at desc
    → host asc → title asc) 로 deterministic tie-break.
    """

    evidence: "LiveEvidence"
    trust: TrustScore
    freshness: FreshnessScore
    role_boost: int
    priority: int
    notes: Tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Trust scoring
# ---------------------------------------------------------------------------


def trust_score_for_source(source: "LiveSource") -> TrustScore:
    """:class:`LiveSource` 에 대한 :class:`TrustScore` 산출.

    baseline 은 :data:`_TRUST_BASELINE` 룩업 (없으면 5).
    penalties:
      * ``allow_listed`` 가 False → ``-3`` (allow-list 외 host)
      * ``robots_compliant`` 가 False → ``-5`` (robots Disallow 위반)

    최종 ``value`` 는 0~10 으로 clip.
    """

    host = (source.host or "").lower().strip()
    baseline = _TRUST_BASELINE.get(host, 5)

    penalties: list[Tuple[str, int]] = []
    if not source.allow_listed:
        penalties.append(("not_allowlisted", _TRUST_PENALTY_NOT_ALLOWLISTED))
    if not source.robots_compliant:
        penalties.append(("robots_violation", _TRUST_PENALTY_ROBOTS_VIOLATION))

    raw = baseline - sum(p for _, p in penalties)
    value = max(0, min(10, raw))
    return TrustScore(
        host=host,
        baseline=baseline,
        penalties=tuple(penalties),
        value=value,
    )


# ---------------------------------------------------------------------------
# Freshness scoring
# ---------------------------------------------------------------------------


def freshness_score(
    published_at: datetime | None,
    *,
    now: datetime,
) -> FreshnessScore:
    """published_at vs now 기반 :class:`FreshnessScore`.

    ``published_at`` 이 None 이면 unknown(-1, 3 점).
    naive datetime 은 UTC 로 간주.
    """

    if published_at is None:
        return FreshnessScore(age_seconds=-1, value=3)

    pub = _as_utc(published_at)
    nw = _as_utc(now)
    age_seconds = int((nw - pub).total_seconds())

    if age_seconds < 0:
        # 미래 시점 — clock skew / 조작 의심. 점수 0.
        return FreshnessScore(age_seconds=age_seconds, value=0)

    day = 86400
    if age_seconds < day:
        value = 10
    elif age_seconds < 7 * day:
        value = 8
    elif age_seconds < 30 * day:
        value = 6
    elif age_seconds < 180 * day:
        value = 4
    elif age_seconds < 365 * day:
        value = 2
    else:
        value = 1
    return FreshnessScore(age_seconds=age_seconds, value=value)


# ---------------------------------------------------------------------------
# Role boost — 역할별 source kind 가산점
# ---------------------------------------------------------------------------

# kind 는 :class:`LiveSource.kind` 와 동일 ("rss", "atom", "github_release",
# "sitemap"). 역할에 잘 맞는 kind 일수록 +1~+2 boost.
_ROLE_KIND_BOOST: Mapping[str, Mapping[str, int]] = {
    "backend-engineer": {
        "rss": 1,
        "atom": 1,
        "github_release": 2,
    },
    "frontend-engineer": {
        "rss": 1,
        "atom": 2,
        "github_release": 1,
    },
    "qa-engineer": {
        "github_release": 2,
        "rss": 1,
    },
    "devops-engineer": {
        "github_release": 2,
        "rss": 1,
        "atom": 1,
    },
    "tech-lead": {
        "rss": 1,
        "atom": 1,
        "github_release": 1,
    },
    "ai-engineer": {
        "rss": 1,
        "github_release": 2,
    },
    "product-designer": {
        "rss": 1,
        "atom": 1,
    },
}


def role_boost_for(role: str, kind: str) -> int:
    """역할/kind 조합의 boost 점수 (0~2)."""

    return _ROLE_KIND_BOOST.get(role, {}).get(kind, 0)


# ---------------------------------------------------------------------------
# Request-time ranking
# ---------------------------------------------------------------------------


def rank_for_request(
    evidences: Sequence["LiveEvidence"],
    *,
    role: str,
    task_type: str,
    now: datetime,
    limit: int | None = None,
) -> Tuple[RankedEvidence, ...]:
    """요청 시점 ranking 산출.

    각 evidence 에 대해 (trust, freshness, role_boost) → priority 를 계산
    하고, deterministic tie-break 으로 정렬한다.

    ``task_type`` 은 현재 ranking 계산에서는 직접 쓰이지 않고 ``notes``
    에 기록되어 디버깅/observability 용도로 노출된다. (역할별 task heavy
    매핑은 :mod:`profiles` 가 별도로 담당.)
    """

    ranked: list[RankedEvidence] = []
    for ev in evidences:
        trust = trust_score_for_source(ev.source)
        fresh = freshness_score(ev.published_at, now=now)
        boost = role_boost_for(role, ev.source.kind)
        priority = round((trust.value + fresh.value + boost) / 3)
        notes = (
            f"role={role}",
            f"task={task_type}",
            f"kind={ev.source.kind}",
        )
        ranked.append(
            RankedEvidence(
                evidence=ev,
                trust=trust,
                freshness=fresh,
                role_boost=boost,
                priority=priority,
                notes=notes,
            )
        )

    ranked.sort(key=_ranking_key, reverse=False)
    if limit is not None and limit >= 0:
        ranked = ranked[:limit]
    return tuple(ranked)


def _ranking_key(r: RankedEvidence) -> Tuple[int, int, int, float, str, str]:
    """Deterministic tie-break.

    sort 는 ascending 이므로 desc 가 필요한 필드는 음수화한다.
    """

    pub_ts = -_published_ts(r.evidence)
    return (
        -r.priority,
        -r.trust.value,
        -r.freshness.value,
        pub_ts,
        (r.evidence.source.host or "").lower(),
        (r.evidence.title or "").lower(),
    )


def _published_ts(ev: "LiveEvidence") -> float:
    if ev.published_at is None:
        return 0.0
    return _as_utc(ev.published_at).timestamp()


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


__all__ = (
    "FreshnessScore",
    "RankedEvidence",
    "TrustScore",
    "freshness_score",
    "rank_for_request",
    "role_boost_for",
    "trust_score_for_source",
)
