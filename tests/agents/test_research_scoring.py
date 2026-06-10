"""Tests for :mod:`yule_engineering.agents.research.scoring`.

F5 / issue #92. TrustScore / FreshnessScore / rank_for_request 회귀.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from yule_engineering.agents.research.providers.live import (
    KIND_ATOM,
    KIND_GITHUB_RELEASE,
    KIND_RSS,
    LiveEvidence,
    LiveSource,
)
from yule_engineering.agents.research.scoring import (
    FreshnessScore,
    TrustScore,
    freshness_score,
    rank_for_request,
    role_boost_for,
    trust_score_for_source,
)


def _src(host: str, kind: str = KIND_RSS, **kw) -> LiveSource:
    return LiveSource(
        host=host,
        kind=kind,
        allow_listed=kw.get("allow_listed", True),
        robots_compliant=kw.get("robots_compliant", True),
        rate_limit_per_sec=1.0,
        url=kw.get("url", f"https://{host}/feed"),
    )


def _ev(host: str, *, kind=KIND_RSS, title="t", age_days: float | None = 0) -> LiveEvidence:
    if age_days is None:
        published = None
    else:
        published = datetime(2026, 5, 11, 0, 0, 0, tzinfo=timezone.utc) - timedelta(
            days=age_days
        )
    return LiveEvidence(
        source=_src(host, kind=kind),
        title=title,
        url=f"https://{host}/{title}",
        summary="",
        published_at=published,
    )


# ---------------------------------------------------------------------------
# trust_score_for_source
# ---------------------------------------------------------------------------


def test_trust_score_uses_baseline_for_known_host() -> None:
    score = trust_score_for_source(_src("python.org"))
    assert isinstance(score, TrustScore)
    assert score.baseline == 10
    assert score.penalties == ()
    assert score.value == 10


def test_trust_score_penalises_non_allowlisted_and_robots_violation() -> None:
    src = _src("python.org", allow_listed=False, robots_compliant=False)
    score = trust_score_for_source(src)
    assert score.baseline == 10
    pen_kinds = {name for name, _ in score.penalties}
    assert pen_kinds == {"not_allowlisted", "robots_violation"}
    # 10 - 3 - 5 = 2
    assert score.value == 2


def test_trust_score_defaults_unknown_host_to_five() -> None:
    score = trust_score_for_source(_src("random.example.com"))
    assert score.baseline == 5
    assert score.value == 5


def test_trust_score_clips_to_zero() -> None:
    src = _src("random.example.com", allow_listed=False, robots_compliant=False)
    # 5 - 3 - 5 = -3 → clip 0
    score = trust_score_for_source(src)
    assert score.value == 0


# ---------------------------------------------------------------------------
# freshness_score
# ---------------------------------------------------------------------------


def test_freshness_unknown_when_published_at_none() -> None:
    now = datetime(2026, 5, 11, tzinfo=timezone.utc)
    fs = freshness_score(None, now=now)
    assert fs.age_seconds == -1
    assert fs.value == 3


def test_freshness_buckets_match_thresholds() -> None:
    now = datetime(2026, 5, 11, tzinfo=timezone.utc)
    assert freshness_score(now - timedelta(hours=1), now=now).value == 10
    assert freshness_score(now - timedelta(days=3), now=now).value == 8
    assert freshness_score(now - timedelta(days=15), now=now).value == 6
    assert freshness_score(now - timedelta(days=90), now=now).value == 4
    assert freshness_score(now - timedelta(days=300), now=now).value == 2
    assert freshness_score(now - timedelta(days=800), now=now).value == 1


def test_freshness_future_published_at_scored_zero() -> None:
    now = datetime(2026, 5, 11, tzinfo=timezone.utc)
    fs = freshness_score(now + timedelta(hours=1), now=now)
    assert fs.value == 0
    assert fs.age_seconds < 0


# ---------------------------------------------------------------------------
# role_boost_for
# ---------------------------------------------------------------------------


def test_role_boost_known_pair_and_unknown_role_default_zero() -> None:
    assert role_boost_for("backend-engineer", KIND_GITHUB_RELEASE) == 2
    assert role_boost_for("frontend-engineer", KIND_ATOM) == 2
    assert role_boost_for("nobody", KIND_RSS) == 0


# ---------------------------------------------------------------------------
# rank_for_request
# ---------------------------------------------------------------------------


def test_rank_for_request_orders_by_priority_desc_with_deterministic_tiebreak() -> None:
    now = datetime(2026, 5, 11, tzinfo=timezone.utc)
    high = _ev("python.org", kind=KIND_RSS, title="A", age_days=0)
    mid = _ev("github.blog", kind=KIND_RSS, title="B", age_days=10)
    low = _ev("random.example.com", kind=KIND_RSS, title="C", age_days=400)
    out = rank_for_request(
        [low, mid, high],
        role="backend-engineer",
        task_type="backend-feature",
        now=now,
    )
    assert [r.evidence.title for r in out] == ["A", "B", "C"]
    # priority 가 정렬 키 — 첫 결과가 가장 높아야 한다.
    assert out[0].priority >= out[-1].priority


def test_rank_for_request_limit_truncates_results() -> None:
    now = datetime(2026, 5, 11, tzinfo=timezone.utc)
    evs = [_ev("python.org", title=f"t{i}", age_days=i) for i in range(5)]
    out = rank_for_request(
        evs,
        role="backend-engineer",
        task_type="backend-feature",
        now=now,
        limit=2,
    )
    assert len(out) == 2
