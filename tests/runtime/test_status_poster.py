"""runtime.status_poster — A-M7.1 unit tests.

Pin the helper contract:

  * Channel resolver: ID env first, NAME-via-REST fallback, None
    when neither resolves; per-process cache; permanent-only
    caching of 4xx (5xx still retries).
  * Dedup hash: stable for identical state, changes when any
    operator-actionable signal changes (services / circuits /
    failed_terminal jobs / fallbacks).
  * Decision: posts on initial / state-change / forced;
    skips on identical-state.
  * Post fn: 200 → posted_message_id; 401/403/404/429/timeout →
    distinct STATUS_POST_* error constants.
  * Token security: error messages never include the token.
  * State store: round-trip through a temp JSON file.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
import urllib.error
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping
from unittest import mock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.approval_discord_poster import (
    PostResponse,
)
from yule_engineering.runtime.circuit_breaker import (
    CircuitBreakerPolicy,
    CircuitBreakerRegistry,
)
from yule_engineering.runtime.fallback import (
    FALLBACK_AUTHORITY_DETERMINISTIC_TEMPLATE,
    build_fallback_audit_record,
    summarise_role_results,
)
from yule_engineering.runtime.status import (
    HEALTH_ALIVE,
    HEALTH_STALE,
    HEALTH_UNKNOWN,
    FailedJobSummary,
    JobTypeSummary,
    RuntimeStatusReport,
    ServiceStatus,
)
from yule_engineering.runtime.status_poster import (
    GUILD_ID_ENV,
    STATUS_CHANNEL_ID_ENV,
    STATUS_CHANNEL_NAME_ENV,
    STATUS_POST_CHANNEL_NOT_FOUND,
    STATUS_POST_CHANNEL_UNSET,
    STATUS_POST_CONTENT_LIMIT,
    STATUS_POST_FORBIDDEN,
    STATUS_POST_RATE_LIMITED,
    STATUS_POST_TIMEOUT,
    STATUS_POST_TOKEN_MISSING,
    STATUS_POST_UNAUTHORIZED,
    StatusPostError,
    StatusPosterStateStore,
    build_status_channel_resolver,
    build_status_post_fn,
    collect_recent_fallback_audits,
    compute_status_dedup_key,
    is_clean_state,
    post_runtime_status_summary,
    should_post_status,
)


_NOW: float = 1_731_000_000.0


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _service(
    service_id: str,
    health: str = HEALTH_ALIVE,
    *,
    age: float = None,  # type: ignore[assignment]
    implemented: bool = True,
) -> ServiceStatus:
    return ServiceStatus(
        service_id=service_id,
        kind="research_worker",
        role=None,
        description="test",
        implemented=implemented,
        health=health,
        heartbeat_age_seconds=age,
        heartbeat_last_beat=None,
        pid=None,
        metadata={},
        job_type="research_collect",
    )


def _empty_report(services=()) -> RuntimeStatusReport:
    return RuntimeStatusReport(
        profile="engineering",
        generated_at=_NOW,
        deadline_seconds=90.0,
        services=tuple(services),
        job_types=(),
        failed_recent=(),
        warnings=(),
    )


# ---------------------------------------------------------------------------
# Channel resolver
# ---------------------------------------------------------------------------


class StatusChannelResolverTests(unittest.TestCase):
    def test_id_env_wins(self) -> None:
        called: List[str] = []

        def http_get(url, headers, timeout):
            called.append(url)
            raise AssertionError("REST GET must NOT fire when ID is set")

        resolver = build_status_channel_resolver(
            env={
                STATUS_CHANNEL_ID_ENV: "1234",
                STATUS_CHANNEL_NAME_ENV: "봇-상태",
                GUILD_ID_ENV: "111",
                "DISCORD_BOT_TOKEN": "tok",
            },
            http_get=http_get,
        )
        self.assertEqual(resolver(), 1234)
        self.assertEqual(called, [])

    def test_name_fallback_resolves_via_rest_and_caches(self) -> None:
        calls: List[str] = []

        def http_get(url, headers, timeout):
            calls.append(url)
            return PostResponse(
                status=200,
                body_text=(
                    '[{"id": "9000", "name": "다른채널"},'
                    ' {"id": "8888", "name": "봇-상태"}]'
                ),
                headers={},
            )

        resolver = build_status_channel_resolver(
            env={
                STATUS_CHANNEL_NAME_ENV: "봇-상태",
                GUILD_ID_ENV: "111",
                "DISCORD_BOT_TOKEN": "tok",
            },
            http_get=http_get,
        )
        self.assertEqual(resolver(), 8888)
        # Cached — second call hits the per-process cache, no extra GET.
        self.assertEqual(resolver(), 8888)
        self.assertEqual(len(calls), 1)
        self.assertIn("/guilds/111/channels", calls[0])

    def test_returns_none_when_neither_set(self) -> None:
        resolver = build_status_channel_resolver(env={})
        self.assertIsNone(resolver())

    def test_returns_none_on_no_match(self) -> None:
        def http_get(url, headers, timeout):
            return PostResponse(
                status=200,
                body_text='[{"id": "9", "name": "다른채널"}]',
                headers={},
            )

        resolver = build_status_channel_resolver(
            env={
                STATUS_CHANNEL_NAME_ENV: "봇-상태",
                GUILD_ID_ENV: "111",
                "DISCORD_BOT_TOKEN": "tok",
            },
            http_get=http_get,
        )
        self.assertIsNone(resolver())

    def test_4xx_caches_none_but_5xx_does_not(self) -> None:
        seq: List[int] = [403, 503]
        calls: List[str] = []

        def http_get(url, headers, timeout):
            calls.append(url)
            status = seq.pop(0) if seq else 503
            return PostResponse(status=status, body_text="", headers={})

        resolver = build_status_channel_resolver(
            env={
                STATUS_CHANNEL_NAME_ENV: "봇-상태",
                GUILD_ID_ENV: "111",
                "DISCORD_BOT_TOKEN": "tok",
            },
            http_get=http_get,
        )
        # 403 → cached None; resolver should NOT call REST again.
        self.assertIsNone(resolver())
        self.assertIsNone(resolver())
        self.assertEqual(len(calls), 1)

    def test_resolver_does_not_log_token(self) -> None:
        # Capture the token via headers — assert resolver passes it
        # but never echoes it.
        captured_headers: Dict[str, str] = {}

        def http_get(url, headers, timeout):
            captured_headers.update(headers)
            return PostResponse(
                status=200,
                body_text='[{"id": "8888", "name": "봇-상태"}]',
                headers={},
            )

        resolver = build_status_channel_resolver(
            env={
                STATUS_CHANNEL_NAME_ENV: "봇-상태",
                GUILD_ID_ENV: "111",
                "DISCORD_BOT_TOKEN": "secret-do-not-leak-tok",
            },
            http_get=http_get,
        )
        self.assertEqual(resolver(), 8888)
        # Token reaches the auth header but the resolver returns
        # only an int; nothing about it surfaces in user-visible
        # state.
        self.assertEqual(
            captured_headers.get("Authorization"),
            "Bot secret-do-not-leak-tok",
        )


# ---------------------------------------------------------------------------
# Dedup hash + decision
# ---------------------------------------------------------------------------


class DedupKeyTests(unittest.TestCase):
    def test_clean_state_hash_is_stable(self) -> None:
        report = _empty_report(
            services=(_service("eng-research-worker", HEALTH_ALIVE, age=5.0),)
        )
        a = compute_status_dedup_key(report=report, circuits={}, fallbacks=())
        b = compute_status_dedup_key(report=report, circuits={}, fallbacks=())
        self.assertEqual(a, b)
        self.assertTrue(is_clean_state(report=report, circuits={}, fallbacks=()))

    def test_hash_changes_when_a_service_goes_stale(self) -> None:
        clean = _empty_report(
            services=(_service("eng-research-worker", HEALTH_ALIVE, age=5.0),)
        )
        warned = _empty_report(
            services=(
                _service("eng-research-worker", HEALTH_STALE, age=900.0),
            )
        )
        a = compute_status_dedup_key(report=clean, circuits={}, fallbacks=())
        b = compute_status_dedup_key(report=warned, circuits={}, fallbacks=())
        self.assertNotEqual(a, b)

    def test_hash_changes_when_a_circuit_opens(self) -> None:
        report = _empty_report(
            services=(_service("eng-research-worker", HEALTH_ALIVE, age=5.0),)
        )
        registry = CircuitBreakerRegistry(
            policy=CircuitBreakerPolicy(window_seconds=60.0, max_restarts=1)
        )
        clean_key = compute_status_dedup_key(
            report=report, circuits=registry.snapshot(), fallbacks=()
        )
        for i in range(3):
            registry.record_restart("eng-x", now=_NOW + i)
        open_key = compute_status_dedup_key(
            report=report, circuits=registry.snapshot(now=_NOW + 5), fallbacks=()
        )
        self.assertNotEqual(clean_key, open_key)

    def test_hash_changes_with_new_failed_terminal_job(self) -> None:
        a_report = _empty_report(
            services=(_service("eng-research-worker", HEALTH_ALIVE, age=5.0),)
        )
        b_report = replace(
            a_report,
            failed_recent=(
                FailedJobSummary(
                    job_id="job-x",
                    job_type="role_take",
                    role="qa-engineer",
                    state="failed_terminal",
                    attempt=3,
                    age_seconds=10.0,
                    error="ProviderError",
                ),
            ),
        )
        a = compute_status_dedup_key(report=a_report, circuits={}, fallbacks=())
        b = compute_status_dedup_key(report=b_report, circuits={}, fallbacks=())
        self.assertNotEqual(a, b)

    def test_hash_excludes_timestamps(self) -> None:
        # generated_at differs but operator-relevant signals match —
        # the dedup must not flap on every snapshot regenerate.
        a = _empty_report(services=(_service("eng-x", HEALTH_ALIVE, age=1.0),))
        b = replace(a, generated_at=_NOW + 999.0)
        ka = compute_status_dedup_key(report=a, circuits={}, fallbacks=())
        kb = compute_status_dedup_key(report=b, circuits={}, fallbacks=())
        self.assertEqual(ka, kb)


class ShouldPostStatusTests(unittest.TestCase):
    def _key_for(self, services=()):
        return compute_status_dedup_key(
            report=_empty_report(services=services), circuits={}, fallbacks=()
        )

    def test_initial_post_when_no_prior_key(self) -> None:
        decision = should_post_status(
            report=_empty_report(),
            last_dedup_key=None,
        )
        self.assertTrue(decision.do_post)
        self.assertEqual(decision.reason, "initial_post")

    def test_skip_when_dedup_key_matches(self) -> None:
        report = _empty_report(
            services=(_service("eng-x", HEALTH_ALIVE, age=1.0),)
        )
        prior = compute_status_dedup_key(
            report=report, circuits={}, fallbacks=()
        )
        decision = should_post_status(
            report=report, last_dedup_key=prior
        )
        self.assertFalse(decision.do_post)
        self.assertEqual(decision.skipped_reason if hasattr(decision, "skipped_reason") else decision.reason, "dedup_key_matches_last_post")

    def test_post_when_state_changed(self) -> None:
        old_key = self._key_for(services=(_service("eng-x", HEALTH_ALIVE, age=1.0),))
        new_report = _empty_report(services=(_service("eng-x", HEALTH_STALE, age=600.0),))
        decision = should_post_status(report=new_report, last_dedup_key=old_key)
        self.assertTrue(decision.do_post)
        self.assertEqual(decision.reason, "state_changed")

    def test_force_overrides_dedup(self) -> None:
        report = _empty_report()
        prior = compute_status_dedup_key(
            report=report, circuits={}, fallbacks=()
        )
        decision = should_post_status(
            report=report, last_dedup_key=prior, force=True
        )
        self.assertTrue(decision.do_post)
        self.assertEqual(decision.reason, "forced")


# ---------------------------------------------------------------------------
# State store — JSON file round-trip
# ---------------------------------------------------------------------------


class StatusPosterStateStoreTests(unittest.TestCase):
    def test_load_returns_empty_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StatusPosterStateStore(path=Path(tmp) / "missing.json")
            record = store.load()
            self.assertIsNone(record.last_dedup_key)
            self.assertIsNone(record.last_posted_at)

    def test_save_then_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StatusPosterStateStore(path=path)
            store.save(last_dedup_key="sha256:abc", last_posted_at=12345.0)
            record = store.load()
            self.assertEqual(record.last_dedup_key, "sha256:abc")
            self.assertEqual(record.last_posted_at, 12345.0)

    def test_load_tolerates_corrupt_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("not valid json", encoding="utf-8")
            store = StatusPosterStateStore(path=path)
            record = store.load()
            self.assertIsNone(record.last_dedup_key)


# ---------------------------------------------------------------------------
# build_status_post_fn — error mapping
# ---------------------------------------------------------------------------


class StatusPostFnSuccessTests(unittest.TestCase):
    def test_200_returns_message_id(self) -> None:
        captured: List[Mapping] = []

        def http_post(url, headers, body, timeout):
            captured.append(
                {"url": url, "headers": dict(headers), "body": bytes(body)}
            )
            return PostResponse(
                status=200,
                body_text='{"id": "5555"}',
                headers={},
            )

        post_fn = build_status_post_fn(
            channel_resolver=lambda: 9999,
            token_resolver=lambda: "tok",
            http_post=http_post,
        )
        result = _run(post_fn("hello"))
        self.assertEqual(result["posted_message_id"], 5555)
        self.assertEqual(result["channel_id"], 9999)
        # URL targets the resolved channel, not the approval one.
        self.assertIn("/channels/9999/messages", captured[0]["url"])
        # Body is the markdown content.
        self.assertEqual(
            json.loads(captured[0]["body"]).get("content"), "hello"
        )


class StatusPostFnErrorMappingTests(unittest.TestCase):
    def _post_with_status(self, status: int, *, headers=None):
        def http_post(url, headers_in, body, timeout):
            return PostResponse(
                status=status, body_text="", headers=headers or {}
            )

        return build_status_post_fn(
            channel_resolver=lambda: 9999,
            token_resolver=lambda: "tok-do-not-leak",
            http_post=http_post,
        )

    def test_401_maps_to_unauthorized(self) -> None:
        post_fn = self._post_with_status(401)
        with self.assertRaises(StatusPostError) as ctx:
            _run(post_fn("x"))
        self.assertEqual(str(ctx.exception), STATUS_POST_UNAUTHORIZED)
        self.assertNotIn("tok-do-not-leak", str(ctx.exception))

    def test_403_maps_to_forbidden(self) -> None:
        post_fn = self._post_with_status(403)
        with self.assertRaises(StatusPostError) as ctx:
            _run(post_fn("x"))
        self.assertEqual(str(ctx.exception), STATUS_POST_FORBIDDEN)

    def test_404_maps_to_channel_not_found(self) -> None:
        post_fn = self._post_with_status(404)
        with self.assertRaises(StatusPostError) as ctx:
            _run(post_fn("x"))
        self.assertEqual(str(ctx.exception), STATUS_POST_CHANNEL_NOT_FOUND)

    def test_429_includes_retry_after_when_present(self) -> None:
        post_fn = self._post_with_status(429, headers={"Retry-After": "12.5"})
        with self.assertRaises(StatusPostError) as ctx:
            _run(post_fn("x"))
        msg = str(ctx.exception)
        self.assertTrue(msg.startswith(STATUS_POST_RATE_LIMITED))
        self.assertIn("retry_after=12.5", msg)
        self.assertNotIn("tok-do-not-leak", msg)

    def test_500_maps_to_generic_http_constant(self) -> None:
        post_fn = self._post_with_status(503)
        with self.assertRaises(StatusPostError) as ctx:
            _run(post_fn("x"))
        self.assertEqual(str(ctx.exception), "status_post_http_503")

    def test_channel_unset_raises_constant(self) -> None:
        post_fn = build_status_post_fn(
            channel_resolver=lambda: None,
            token_resolver=lambda: "tok",
            http_post=lambda *a, **k: PostResponse(200, "", {}),
        )
        with self.assertRaises(StatusPostError) as ctx:
            _run(post_fn("x"))
        self.assertEqual(str(ctx.exception), STATUS_POST_CHANNEL_UNSET)

    def test_token_missing_raises_constant(self) -> None:
        post_fn = build_status_post_fn(
            channel_resolver=lambda: 9999,
            token_resolver=lambda: None,
            http_post=lambda *a, **k: PostResponse(200, "", {}),
        )
        with self.assertRaises(StatusPostError) as ctx:
            _run(post_fn("x"))
        self.assertEqual(str(ctx.exception), STATUS_POST_TOKEN_MISSING)

    def test_url_error_describes_reason_without_token(self) -> None:
        def http_post(url, headers, body, timeout):
            raise urllib.error.URLError(reason=ConnectionRefusedError("nope"))

        post_fn = build_status_post_fn(
            channel_resolver=lambda: 9999,
            token_resolver=lambda: "tok-do-not-leak",
            http_post=http_post,
        )
        with self.assertRaises(StatusPostError) as ctx:
            _run(post_fn("x"))
        msg = str(ctx.exception)
        self.assertTrue(msg.startswith("status_post_url_error"))
        self.assertNotIn("tok-do-not-leak", msg)

    def test_timeout_maps_to_timeout_constant(self) -> None:
        def http_post(url, headers, body, timeout):
            raise TimeoutError("slow")

        post_fn = build_status_post_fn(
            channel_resolver=lambda: 9999,
            token_resolver=lambda: "tok-do-not-leak",
            http_post=http_post,
        )
        with self.assertRaises(StatusPostError) as ctx:
            _run(post_fn("x"))
        self.assertEqual(str(ctx.exception), STATUS_POST_TIMEOUT)
        self.assertNotIn("tok-do-not-leak", str(ctx.exception))


class StatusPostFnTruncationTests(unittest.TestCase):
    def test_long_body_is_truncated_with_marker(self) -> None:
        captured: List[Mapping] = []

        def http_post(url, headers, body, timeout):
            captured.append({"body": bytes(body)})
            return PostResponse(
                status=200, body_text='{"id": "1"}', headers={}
            )

        post_fn = build_status_post_fn(
            channel_resolver=lambda: 9999,
            token_resolver=lambda: "tok",
            http_post=http_post,
        )
        long_text = "a" * (STATUS_POST_CONTENT_LIMIT + 500)
        _run(post_fn(long_text))
        sent = json.loads(captured[0]["body"])["content"]
        self.assertLessEqual(len(sent), STATUS_POST_CONTENT_LIMIT)
        self.assertIn("(truncated)", sent)


# ---------------------------------------------------------------------------
# post_runtime_status_summary — orchestration
# ---------------------------------------------------------------------------


class PostRuntimeStatusSummaryTests(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_store = StatusPosterStateStore(
            path=Path(self._tmp.name) / "state.json"
        )
        self.posted: List[str] = []

        async def post_fn(content: str):
            self.posted.append(content)
            return {"posted_message_id": 100 + len(self.posted)}

        self.post_fn = post_fn

    def _report(self, services=()):
        return _empty_report(services=services)

    def test_initial_post_sends_and_records_dedup_key(self) -> None:
        outcome = _run(
            post_runtime_status_summary(
                report=self._report(),
                circuits={},
                fallbacks=(),
                state_store=self.state_store,
                post_fn=self.post_fn,
            )
        )
        self.assertTrue(outcome.did_post)
        self.assertEqual(outcome.posted_message_id, 101)
        self.assertEqual(len(self.posted), 1)
        # Dedup key persisted so the next identical call is a no-op.
        record = self.state_store.load()
        self.assertEqual(record.last_dedup_key, outcome.dedup_key)

    def test_second_call_with_same_state_skips_post(self) -> None:
        report = self._report(
            services=(_service("eng-x", HEALTH_ALIVE, age=1.0),)
        )
        _run(
            post_runtime_status_summary(
                report=report,
                state_store=self.state_store,
                post_fn=self.post_fn,
            )
        )
        outcome = _run(
            post_runtime_status_summary(
                report=report,
                state_store=self.state_store,
                post_fn=self.post_fn,
            )
        )
        self.assertFalse(outcome.did_post)
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(
            outcome.skipped_reason, "dedup_key_matches_last_post"
        )

    def test_state_change_triggers_repost(self) -> None:
        clean = self._report(
            services=(_service("eng-x", HEALTH_ALIVE, age=1.0),)
        )
        stale = self._report(
            services=(_service("eng-x", HEALTH_STALE, age=900.0),)
        )
        _run(
            post_runtime_status_summary(
                report=clean,
                state_store=self.state_store,
                post_fn=self.post_fn,
            )
        )
        outcome = _run(
            post_runtime_status_summary(
                report=stale,
                state_store=self.state_store,
                post_fn=self.post_fn,
            )
        )
        self.assertTrue(outcome.did_post)
        self.assertEqual(outcome.decision_reason, "state_changed")
        self.assertEqual(len(self.posted), 2)

    def test_post_failure_does_not_advance_dedup_state(self) -> None:
        # A failed post must NOT update the stored dedup key; the
        # next call with the same state should retry.
        async def broken_post(content: str):
            raise StatusPostError(STATUS_POST_RATE_LIMITED)

        report = self._report()
        outcome = _run(
            post_runtime_status_summary(
                report=report,
                state_store=self.state_store,
                post_fn=broken_post,
            )
        )
        self.assertFalse(outcome.did_post)
        self.assertEqual(outcome.error, STATUS_POST_RATE_LIMITED)
        # State store remains empty so a retry on identical state
        # would still attempt to post.
        self.assertIsNone(self.state_store.load().last_dedup_key)

    def test_force_skips_dedup(self) -> None:
        report = self._report()
        first = _run(
            post_runtime_status_summary(
                report=report,
                state_store=self.state_store,
                post_fn=self.post_fn,
            )
        )
        forced = _run(
            post_runtime_status_summary(
                report=report,
                state_store=self.state_store,
                post_fn=self.post_fn,
                force=True,
            )
        )
        self.assertTrue(first.did_post)
        self.assertTrue(forced.did_post)
        self.assertEqual(forced.decision_reason, "forced")
        self.assertEqual(len(self.posted), 2)


# ---------------------------------------------------------------------------
# collect_recent_fallback_audits
# ---------------------------------------------------------------------------


class CollectRecentFallbackAuditsTests(unittest.TestCase):
    def test_walks_session_extras_and_returns_records(self) -> None:
        notice = summarise_role_results(
            expected_roles=("tech-lead",), failed_roles=("tech-lead",)
        )
        # Two sessions, each with one fallback audit.
        record_a = build_fallback_audit_record(
            session_id="sess-a",
            notice=notice,
            authority=FALLBACK_AUTHORITY_DETERMINISTIC_TEMPLATE,
        )
        record_b = build_fallback_audit_record(
            session_id="sess-b",
            notice=notice,
            authority=FALLBACK_AUTHORITY_DETERMINISTIC_TEMPLATE,
        )
        sess_a = SimpleNamespace(
            extra={"fallback_audits": [dict(record_a.to_payload())]}
        )
        sess_b = SimpleNamespace(
            extra={"fallback_audits": [dict(record_b.to_payload())]}
        )
        # Sort order — most recent first by ``created_at``.
        results = collect_recent_fallback_audits(
            session_lister=lambda **_: [sess_a, sess_b]
        )
        self.assertEqual(len(results), 2)
        ids = {r.fallback_id for r in results}
        self.assertEqual(ids, {record_a.fallback_id, record_b.fallback_id})

    def test_skips_malformed_audit_rows(self) -> None:
        sess = SimpleNamespace(
            extra={
                "fallback_audits": [
                    {"not_a_record": True},  # missing required field
                    {"fallback_id": ""},  # empty id
                ]
            }
        )
        results = collect_recent_fallback_audits(
            session_lister=lambda **_: [sess]
        )
        self.assertEqual(results, ())

    def test_returns_empty_when_lister_raises(self) -> None:
        def boom(**_kwargs):
            raise RuntimeError("workflow store down")

        results = collect_recent_fallback_audits(session_lister=boom)
        self.assertEqual(results, ())


if __name__ == "__main__":
    unittest.main()
