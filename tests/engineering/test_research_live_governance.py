"""Governance regression for live research providers (F5 / #92).

본 테스트는 acceptance criteria 의 hard rails 를 직접 가드한다:

1. ``YULE_RESEARCH_LIVE_ENABLED`` default 는 ``false`` — env 없는 호출은
   외부 transport 를 절대 부르면 안 된다.
2. allow-list 외 host 를 강제로 등록한 source 도 ingest 되지 않아야 한다.
3. robots.txt 위반(``robots_compliant=False``) source 는 fetch skip.
4. provider 결과는 PasteGuard 를 통과하여 raw secret / raw HTML tag 가
   외부로 새지 않는다.
5. rate-limit env 가 0 이하일 때 default(=1.0) 로 폴백한다.
6. 카탈로그 default host 는 모두 :data:`_TRUST_BASELINE` 와 정합하여
   trust score >= 5 를 받는다 (운영자 검증 host 만 등재).
"""

from __future__ import annotations

from yule_orchestrator.agents.research.providers.live import (
    KIND_GITHUB_RELEASE,
    KIND_RSS,
    LiveSource,
    RssAtomProvider,
    build_live_provider_registry_from_env,
    default_role_source_catalog,
)
from yule_orchestrator.agents.research.scoring import trust_score_for_source


def test_env_default_off_means_no_fetch_no_evidence() -> None:
    """env 없는 호출은 외부 transport 를 절대 부르지 않는다."""

    called = {"http": 0, "release": 0}

    def http(_u):  # pragma: no cover - 호출되면 안 됨
        called["http"] += 1
        return "<rss/>"

    def release(_r):  # pragma: no cover - 호출되면 안 됨
        called["release"] += 1
        return []

    reg = build_live_provider_registry_from_env(
        {},
        http_fetch=http,
        release_fetch=release,
    )
    assert reg.env_enabled is False
    assert reg.ingest_all() == ()
    assert called == {"http": 0, "release": 0}


def test_non_allowlisted_source_blocks_ingest_even_when_env_on() -> None:
    bad = LiveSource(
        host="evil.example.com",
        kind=KIND_RSS,
        allow_listed=False,
        robots_compliant=True,
        rate_limit_per_sec=1.0,
        url="https://evil.example.com/feed",
    )
    called = {"http": 0}

    def http(_u):  # pragma: no cover - 호출되면 안 됨
        called["http"] += 1
        return "<rss><channel><item><title>x</title></item></channel></rss>"

    provider = RssAtomProvider(
        sources=(bad,),
        http_fetch=http,
        env_enabled=True,
    )
    assert provider.ingest() == ()
    assert called["http"] == 0


def test_robots_violation_blocks_ingest_even_when_env_on() -> None:
    bad = LiveSource(
        host="blocked.example.com",
        kind=KIND_RSS,
        allow_listed=True,
        robots_compliant=False,
        rate_limit_per_sec=1.0,
        url="https://blocked.example.com/feed",
    )
    called = {"http": 0}

    def http(_u):  # pragma: no cover - 호출되면 안 됨
        called["http"] += 1
        return "<rss/>"

    provider = RssAtomProvider(
        sources=(bad,),
        http_fetch=http,
        env_enabled=True,
    )
    assert provider.ingest() == ()
    assert called["http"] == 0


def test_paste_guard_redacts_secret_leaking_summary() -> None:
    """raw secret (sk-ant-…) 이 summary 로 새어 나가면 안 된다."""

    body = (
        "<rss version='2.0'><channel><item>"
        "<title>leak</title>"
        "<link>https://example.com/leak</link>"
        "<description>token=sk-ant-abcdefghijklmnopqrstuvwxyz</description>"
        "</item></channel></rss>"
    )
    src = LiveSource(
        host="example.com",
        kind=KIND_RSS,
        allow_listed=True,
        robots_compliant=True,
        rate_limit_per_sec=1.0,
        url="https://example.com/feed",
    )
    provider = RssAtomProvider(
        sources=(src,),
        http_fetch=lambda _u: body,
        env_enabled=True,
    )
    out = provider.ingest()
    assert len(out) == 1
    summary = out[0].summary
    # PasteGuard 가 raw 키를 마스킹해야 한다 — 원문이 그대로 노출되면 BLOCK.
    assert "sk-ant-abcdefghijklmnopqrstuvwxyz" not in summary


def test_rate_limit_env_zero_falls_back_to_default() -> None:
    reg = build_live_provider_registry_from_env(
        {"YULE_RESEARCH_LIVE_RATE_LIMIT_PER_SEC": "0"},
    )
    assert reg.rate_limit_per_sec == 1.0


def test_default_catalog_hosts_all_pass_min_trust_threshold() -> None:
    """카탈로그의 default host 는 운영자가 검증한 source 만 들어 있다."""

    catalog = default_role_source_catalog()
    assert catalog, "catalog must not be empty"
    for entry in catalog:
        score = trust_score_for_source(entry.source)
        assert score.value >= 5, (
            f"default catalog host {entry.source.host!r} fell below trust "
            f"baseline (got {score.value}); update _TRUST_BASELINE or remove."
        )
