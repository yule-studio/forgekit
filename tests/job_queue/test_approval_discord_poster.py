"""approval_discord_poster — A-M6.1b-1 unit tests.

Pin every error path the production post_fn must surface as a
distinct, **token-free** error string:

  * 200 → success returns ``posted_message_id`` + ``channel_id``
  * 401 → ``approval_post_unauthorized``
  * 403 → ``approval_post_forbidden``
  * 404 → ``approval_post_channel_not_found``
  * 429 → ``approval_post_rate_limited`` (with retry_after suffix
    when Discord supplies it)
  * any other 4xx/5xx → ``approval_post_http_<status>``
  * timeout → ``approval_post_timeout``
  * connection-level URL error → ``approval_post_url_error: <name>``

Token / channel resolvers are tested for env priority so a
single-bot dev env (only ``DISCORD_BOT_TOKEN`` set) still works.

No real Discord network calls — every test injects a stub
``http_post``.
"""

from __future__ import annotations

import asyncio
import unittest
import urllib.error
from typing import List, Mapping
from unittest import mock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.approval_discord_poster import (
    DEFAULT_POST_TIMEOUT_SECONDS,
    DISCORD_API_BASE,
    ERROR_CHANNEL_NOT_FOUND,
    ERROR_FORBIDDEN,
    ERROR_RATE_LIMITED,
    ERROR_TIMEOUT,
    ERROR_TOKEN_MISSING,
    ERROR_UNAUTHORIZED,
    ApprovalPostError,
    PostResponse,
    build_production_post_fn,
    resolve_approval_channel_id,
    resolve_discord_bot_token,
)
from yule_orchestrator.agents.job_queue.approval_worker import (
    APPROVAL_KIND_OBSIDIAN_WRITE,
    ApprovalRequest,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _request() -> ApprovalRequest:
    return ApprovalRequest(
        session_id="sess-post-1",
        approval_kind=APPROVAL_KIND_OBSIDIAN_WRITE,
        title="결정 노트",
        summary="x",
        requested_action="vault 저장",
        created_by="tech-lead",
        source_thread_id=4242,
    )


class TokenResolverTests(unittest.TestCase):
    def test_priority_picks_engineering_gateway_token_first(self) -> None:
        env = {
            "ENGINEERING_AGENT_BOT_GATEWAY_TOKEN": "gw-token",
            "DISCORD_BOT_TOKEN": "planning-token",
        }
        self.assertEqual(resolve_discord_bot_token(env), "gw-token")

    def test_falls_back_to_planning_bot_token_when_gateway_unset(self) -> None:
        env = {"DISCORD_BOT_TOKEN": "planning-token"}
        self.assertEqual(resolve_discord_bot_token(env), "planning-token")

    def test_returns_none_when_both_unset(self) -> None:
        self.assertIsNone(resolve_discord_bot_token({}))

    def test_strips_whitespace_and_treats_empty_as_unset(self) -> None:
        env = {
            "ENGINEERING_AGENT_BOT_GATEWAY_TOKEN": "   ",
            "DISCORD_BOT_TOKEN": "real-token",
        }
        # Empty string in priority slot must NOT shadow a populated
        # fallback — operator clearing the dedicated env should keep
        # the dev fallback working.
        self.assertEqual(resolve_discord_bot_token(env), "real-token")


class ChannelResolverTests(unittest.TestCase):
    def test_returns_int_when_env_set(self) -> None:
        env = {"DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID": "1234567890"}
        self.assertEqual(resolve_approval_channel_id(env), 1234567890)

    def test_returns_none_when_unset_or_blank(self) -> None:
        self.assertIsNone(resolve_approval_channel_id({}))
        self.assertIsNone(
            resolve_approval_channel_id(
                {"DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID": "  "}
            )
        )

    def test_returns_none_when_garbage(self) -> None:
        self.assertIsNone(
            resolve_approval_channel_id(
                {"DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID": "not-a-number"}
            )
        )


class PostFnSuccessTests(unittest.TestCase):
    def test_success_returns_message_id_and_channel(self) -> None:
        captured: List[tuple] = []

        def http_post(url, headers, body, timeout):
            captured.append((url, dict(headers), bytes(body), timeout))
            return PostResponse(
                status=200,
                body_text='{"id": "9999999999", "channel_id": "8888"}',
                headers={},
            )

        post_fn = build_production_post_fn(
            channel_resolver=lambda: 8888,
            token_resolver=lambda: "tok-secret",
            http_post=http_post,
        )
        outcome = _run(post_fn(_request(), "rendered card text"))
        self.assertEqual(outcome["posted_message_id"], 9999999999)
        self.assertEqual(outcome["channel_id"], 8888)
        self.assertEqual(
            outcome["approval_kind"], APPROVAL_KIND_OBSIDIAN_WRITE
        )
        # URL targets the right channel, headers carry Bot auth +
        # JSON content-type, body is the rendered text in JSON.
        url, headers, body, timeout = captured[0]
        self.assertEqual(url, f"{DISCORD_API_BASE}/channels/8888/messages")
        self.assertEqual(headers["Authorization"], "Bot tok-secret")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(timeout, DEFAULT_POST_TIMEOUT_SECONDS)
        # Body is JSON-encoded with the rendered text.
        import json as _json

        decoded = _json.loads(body.decode("utf-8"))
        self.assertEqual(decoded["content"], "rendered card text")

    def test_success_with_non_json_body_still_returns_outcome(self) -> None:
        # Discord normally returns JSON; any decoder failure must
        # NOT bubble — the worker still considers the post a success
        # because Discord returned 2xx.
        def http_post(url, headers, body, timeout):
            return PostResponse(
                status=204, body_text="", headers={}
            )

        post_fn = build_production_post_fn(
            channel_resolver=lambda: 8888,
            token_resolver=lambda: "tok",
            http_post=http_post,
        )
        outcome = _run(post_fn(_request(), "card text"))
        # Body had no id; we record None message id but still mark
        # the post complete.
        self.assertIsNone(outcome["posted_message_id"])
        self.assertEqual(outcome["channel_id"], 8888)


class PostFnHttpErrorTests(unittest.TestCase):
    """Each Discord error code maps to a distinct constant string —
    the queue's audit trail can grep for the constant without
    parsing free-form messages."""

    def _make_post_fn(self, status: int, *, headers: Mapping[str, str] = ()):
        def http_post(url, headers_in, body, timeout):
            return PostResponse(
                status=status,
                body_text="{}",
                headers=dict(headers),
            )

        return build_production_post_fn(
            channel_resolver=lambda: 8888,
            token_resolver=lambda: "tok",
            http_post=http_post,
        )

    def test_401_maps_to_unauthorized(self) -> None:
        post_fn = self._make_post_fn(401)
        with self.assertRaises(ApprovalPostError) as ctx:
            _run(post_fn(_request(), "x"))
        self.assertEqual(str(ctx.exception), ERROR_UNAUTHORIZED)

    def test_403_maps_to_forbidden(self) -> None:
        post_fn = self._make_post_fn(403)
        with self.assertRaises(ApprovalPostError) as ctx:
            _run(post_fn(_request(), "x"))
        self.assertEqual(str(ctx.exception), ERROR_FORBIDDEN)

    def test_404_maps_to_channel_not_found(self) -> None:
        post_fn = self._make_post_fn(404)
        with self.assertRaises(ApprovalPostError) as ctx:
            _run(post_fn(_request(), "x"))
        self.assertEqual(str(ctx.exception), ERROR_CHANNEL_NOT_FOUND)

    def test_429_includes_retry_after_when_present(self) -> None:
        post_fn = self._make_post_fn(429, headers={"Retry-After": "3.5"})
        with self.assertRaises(ApprovalPostError) as ctx:
            _run(post_fn(_request(), "x"))
        # Constant prefix + retry_after info — caller can parse
        # without losing the constant grep.
        self.assertIn(ERROR_RATE_LIMITED, str(ctx.exception))
        self.assertIn("retry_after=3.5", str(ctx.exception))

    def test_500_maps_to_generic_http_constant(self) -> None:
        post_fn = self._make_post_fn(503)
        with self.assertRaises(ApprovalPostError) as ctx:
            _run(post_fn(_request(), "x"))
        self.assertEqual(str(ctx.exception), "approval_post_http_503")


class PostFnTransportErrorTests(unittest.TestCase):
    def test_token_missing_raises_constant_error(self) -> None:
        post_fn = build_production_post_fn(
            channel_resolver=lambda: 8888,
            token_resolver=lambda: None,
            http_post=lambda *_: None,  # never invoked
        )
        with self.assertRaises(ApprovalPostError) as ctx:
            _run(post_fn(_request(), "x"))
        self.assertEqual(str(ctx.exception), ERROR_TOKEN_MISSING)

    def test_url_error_describes_reason_without_token(self) -> None:
        # Connection-level failure (DNS / refused). The error must
        # map to a sanitised constant + mention the reason class —
        # never the request body / token.
        def http_post(*_args, **_kwargs):
            raise urllib.error.URLError(
                ConnectionRefusedError("connection refused")
            )

        post_fn = build_production_post_fn(
            channel_resolver=lambda: 8888,
            token_resolver=lambda: "tok-do-not-leak",
            http_post=http_post,
        )
        with self.assertRaises(ApprovalPostError) as ctx:
            _run(post_fn(_request(), "x"))
        msg = str(ctx.exception)
        self.assertIn("approval_post_url_error", msg)
        # Token must NEVER appear in the error message — that's the
        # security contract worth pinning.
        self.assertNotIn("tok-do-not-leak", msg)

    def test_timeout_maps_to_timeout_constant(self) -> None:
        def http_post(*_args, **_kwargs):
            raise TimeoutError("read timeout")

        post_fn = build_production_post_fn(
            channel_resolver=lambda: 8888,
            token_resolver=lambda: "tok",
            http_post=http_post,
        )
        with self.assertRaises(ApprovalPostError) as ctx:
            _run(post_fn(_request(), "x"))
        self.assertEqual(str(ctx.exception), ERROR_TIMEOUT)

    def test_unknown_transport_error_is_sanitised(self) -> None:
        # Any other exception class — must be wrapped with a
        # constant prefix + class name only. No stack traces, no
        # message body that could carry the token.
        class _Boom(Exception):
            pass

        def http_post(*_args, **_kwargs):
            raise _Boom("token=tok-do-not-leak should NEVER appear here")

        post_fn = build_production_post_fn(
            channel_resolver=lambda: 8888,
            token_resolver=lambda: "tok-do-not-leak",
            http_post=http_post,
        )
        with self.assertRaises(ApprovalPostError) as ctx:
            _run(post_fn(_request(), "x"))
        msg = str(ctx.exception)
        self.assertIn("approval_post_transport_error", msg)
        self.assertIn("_Boom", msg)
        # Token must NOT appear in the wrapped error.
        self.assertNotIn("tok-do-not-leak", msg)


class IntegrationWithApprovalWorkerTests(unittest.TestCase):
    """End-to-end against ApprovalWorker.process_job — proves the
    production post_fn slots into the worker contract without any
    glue changes (the worker already accepts an async callable).
    """

    def test_worker_drives_production_post_fn_to_saved(self) -> None:
        import tempfile
        from pathlib import Path

        from yule_orchestrator.agents.job_queue.approval_worker import (
            ApprovalWorker,
        )
        from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
        from yule_orchestrator.agents.job_queue.state_machine import JobState
        from yule_orchestrator.agents.job_queue.store import JobQueue

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "queue.sqlite3"
            queue = JobQueue(db_path=db)
            heartbeats = HeartbeatStore(db_path=db)

            captured: List[bytes] = []

            def http_post(url, headers, body, timeout):
                captured.append(bytes(body))
                return PostResponse(
                    status=200,
                    body_text='{"id": "1234"}',
                    headers={},
                )

            post_fn = build_production_post_fn(
                channel_resolver=lambda: 9999,
                token_resolver=lambda: "tok",
                http_post=http_post,
            )
            worker = ApprovalWorker(
                queue=queue,
                heartbeats=heartbeats,
                post_fn=post_fn,
                channel_resolver=lambda: 9999,
            )
            outcome = _run(worker.run_one(_request()))
            self.assertIsNone(outcome.skipped_reason)
            assert outcome.job is not None
            self.assertEqual(outcome.job.state, JobState.SAVED)
            # Captured message id flowed into the queue row's result.
            self.assertEqual(
                outcome.job.result.get("posted_message_id"), 1234
            )
            self.assertEqual(len(captured), 1)


if __name__ == "__main__":
    unittest.main()
