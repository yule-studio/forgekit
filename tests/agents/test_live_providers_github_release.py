"""Tests for :mod:`yule_engineering.agents.research.providers.live.github_release`.

F5 / issue #92. GithubReleaseProvider 회귀.
"""

from __future__ import annotations

from datetime import datetime, timezone

from yule_engineering.agents.research.providers.live import (
    KIND_GITHUB_RELEASE,
    GithubReleaseProvider,
    LiveSource,
)


def _src(repo: str = "tiangolo/fastapi", **kw) -> LiveSource:
    return LiveSource(
        host=kw.get("host", "github.com"),
        kind=KIND_GITHUB_RELEASE,
        allow_listed=kw.get("allow_listed", True),
        robots_compliant=kw.get("robots_compliant", True),
        rate_limit_per_sec=1.0,
        url=repo,
    )


_RELEASES_FASTAPI = [
    {
        "tag_name": "v0.110.0",
        "name": "0.110.0 — feat",
        "html_url": "https://github.com/tiangolo/fastapi/releases/tag/v0.110.0",
        "published_at": "2026-04-15T09:00:00Z",
        "body": "## Features\n- Added support for X.\n- Bumped deps.",
    },
    {
        "tag_name": "v0.109.0",
        "name": "0.109.0",
        "html_url": "https://github.com/tiangolo/fastapi/releases/tag/v0.109.0",
        "published_at": "2026-03-01T00:00:00Z",
        "body": "bugfix",
    },
]


def test_release_provider_disabled_when_env_off() -> None:
    p = GithubReleaseProvider(
        sources=(_src(),),
        release_fetch=lambda _r: _RELEASES_FASTAPI,
        env_enabled=False,
    )
    assert p.ingest() == ()


def test_release_provider_skips_non_allowlisted_source() -> None:
    p = GithubReleaseProvider(
        sources=(_src(allow_listed=False),),
        release_fetch=lambda _r: _RELEASES_FASTAPI,
        env_enabled=True,
    )
    assert p.ingest() == ()


def test_release_provider_skips_robots_violation_source() -> None:
    p = GithubReleaseProvider(
        sources=(_src(robots_compliant=False),),
        release_fetch=lambda _r: _RELEASES_FASTAPI,
        env_enabled=True,
    )
    assert p.ingest() == ()


def test_release_provider_ingests_releases_into_evidence() -> None:
    p = GithubReleaseProvider(
        sources=(_src(),),
        release_fetch=lambda _r: _RELEASES_FASTAPI,
        env_enabled=True,
    )
    out = p.ingest()
    assert len(out) == 2
    first = out[0]
    assert "fastapi" in first.title.lower()
    assert first.url.endswith("v0.110.0")
    assert first.published_at == datetime(2026, 4, 15, 9, 0, 0, tzinfo=timezone.utc)
    assert first.tags == ("v0.110.0",)
    assert first.extra["repo"] == "tiangolo/fastapi"
    assert first.extra["tag"] == "v0.110.0"


def test_release_provider_truncates_to_max_releases() -> None:
    p = GithubReleaseProvider(
        sources=(_src(),),
        release_fetch=lambda _r: _RELEASES_FASTAPI,
        env_enabled=True,
        max_releases_per_repo=1,
    )
    out = p.ingest()
    assert len(out) == 1
    assert out[0].tags == ("v0.110.0",)


def test_release_provider_isolates_fetch_exceptions() -> None:
    def boom(_r):
        raise RuntimeError("api 503")

    p = GithubReleaseProvider(
        sources=(_src(),),
        release_fetch=boom,
        env_enabled=True,
    )
    assert p.ingest() == ()


def test_release_provider_skips_invalid_repo_slug() -> None:
    bad = LiveSource(
        host="github.com",
        kind=KIND_GITHUB_RELEASE,
        url="not-a-slug",  # no slash
    )
    p = GithubReleaseProvider(
        sources=(bad,),
        release_fetch=lambda _r: _RELEASES_FASTAPI,
        env_enabled=True,
    )
    assert p.ingest() == ()


def test_release_provider_skips_release_without_tag_or_name() -> None:
    p = GithubReleaseProvider(
        sources=(_src(),),
        release_fetch=lambda _r: [{"body": "orphan"}],
        env_enabled=True,
    )
    assert p.ingest() == ()
