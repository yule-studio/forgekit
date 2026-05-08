"""Discord REST production post_fn for ApprovalWorker — A-M6.1b-1.

Replaces the M6.0 ``_no_post_fn_yet`` placeholder with a real HTTP
poster that calls the Discord ``POST /channels/{id}/messages``
endpoint with the engineering gateway's bot token. Stays at the
HTTP layer — does **not** spin up a discord.py client — because:

  * a queue worker that posts one card every few minutes doesn't
    need a persistent gateway WebSocket connection, and
  * the standalone ``eng-approval-worker`` process is meant to be
    restartable on its own (``systemctl restart``); a bare HTTP
    POST has no connection state to lose.

Security contract:

  * The bot token is read from env at call time, never stored on
    a worker attribute, never logged. The error helpers below
    explicitly redact it from any captured traceback / response.
  * 401 / 403 are non-retryable in the queue's retry sense (token
    or permission is wrong — retrying won't help). They land as
    ``failed_retryable`` for now (M6.1b-2 may flip them to
    ``failed_terminal`` once the gateway has a clearer recovery
    UX), but with a distinct error string the supervisor can
    surface to ``#봇-상태`` later.

Error mapping:

  * ``approval_post_token_missing`` — bot token env unset.
  * ``approval_post_unauthorized`` — Discord 401.
  * ``approval_post_forbidden`` — Discord 403 (intent / channel ACL).
  * ``approval_post_channel_not_found`` — Discord 404.
  * ``approval_post_rate_limited`` — Discord 429.
  * ``approval_post_http_<status>`` — any other 5xx / 4xx.
  * ``approval_post_timeout`` — request timeout.

The post_fn returned by :func:`build_production_post_fn` matches
the ``ApprovalPostFn`` shape from M5a — a coroutine taking
``(ApprovalRequest, rendered_text)``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Optional

from .approval_worker import ApprovalRequest


logger = logging.getLogger(__name__)


DISCORD_API_BASE: str = "https://discord.com/api/v10"
DEFAULT_POST_TIMEOUT_SECONDS: float = 15.0


# Env keys. Approval worker posts under the engineering gateway's
# bot identity (``ENGINEERING_AGENT_BOT_GATEWAY_TOKEN``); we fall
# back to the legacy planning ``DISCORD_BOT_TOKEN`` only when the
# dedicated env is unset, so dev environments running a single bot
# still see a reply.
TOKEN_ENV_PRIORITY: tuple[str, ...] = (
    "ENGINEERING_AGENT_BOT_GATEWAY_TOKEN",
    "DISCORD_BOT_TOKEN",
)


# Error constants — kept in sync with the M6.0 supervisor surface so
# a future status diagnostic can grep for them.
ERROR_TOKEN_MISSING: str = "approval_post_token_missing"
ERROR_UNAUTHORIZED: str = "approval_post_unauthorized"
ERROR_FORBIDDEN: str = "approval_post_forbidden"
ERROR_CHANNEL_NOT_FOUND: str = "approval_post_channel_not_found"
ERROR_RATE_LIMITED: str = "approval_post_rate_limited"
ERROR_TIMEOUT: str = "approval_post_timeout"


# Sync HTTP request shape — the production poster wraps urllib in
# ``asyncio.to_thread`` so the worker stays async without pulling
# in httpx/aiohttp. Tests inject a stub fn so they never hit
# Discord's real API.
HttpPostFn = Callable[[str, Mapping[str, str], bytes, float], "PostResponse"]


@dataclass(frozen=True)
class PostResponse:
    """One round-trip's outcome.

    ``status`` is the HTTP status code. ``body_text`` is the raw
    response body — useful for surfacing Discord's structured
    error message (``{"message": "...", "code": ...}``) into the
    queue's ``result_json`` for operator triage. Bot tokens never
    enter here because the helpers below build the request.
    """

    status: int
    body_text: str
    headers: Mapping[str, str]


# ---------------------------------------------------------------------------
# Token + channel resolution
# ---------------------------------------------------------------------------


def resolve_discord_bot_token(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Return the bot token from env, in priority order. Never logs.

    Tests pass an explicit *env* mapping (typically
    ``{"DISCORD_BOT_TOKEN": "tok-test"}``) so the resolver can be
    exercised without mutating ``os.environ``.
    """

    source = env if env is not None else os.environ
    for key in TOKEN_ENV_PRIORITY:
        value = source.get(key)
        if value is None:
            continue
        text = value.strip()
        if text:
            return text
    return None


def resolve_approval_channel_id(
    env: Optional[Mapping[str, str]] = None,
) -> Optional[int]:
    """Return ``DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID`` from env.

    Mirrors :func:`agents.job_queue.approval_worker.env_approval_channel_resolver`
    so the production wrapper can call it without taking a
    discord.py client. The id-only path stays the supervisor's
    happy path; A-M6.2 adds a NAME-fallback resolver
    (:func:`build_approval_channel_resolver`) that talks Discord
    REST when only ``DISCORD_ENGINEERING_APPROVAL_CHANNEL_NAME``
    + ``DISCORD_GUILD_ID`` are set.
    """

    source = env if env is not None else os.environ
    raw = source.get("DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID")
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


# Sync HTTP GET shape — same injection seam as :data:`HttpPostFn`
# but for read-only Discord REST calls (channel listing). Tests
# pass a fake fn returning a canned :class:`PostResponse`.
HttpGetFn = Callable[[str, Mapping[str, str], float], "PostResponse"]


def build_approval_channel_resolver(
    *,
    env: Optional[Mapping[str, str]] = None,
    token_resolver: Optional[Callable[[], Optional[str]]] = None,
    http_get: Optional[HttpGetFn] = None,
    timeout_seconds: float = DEFAULT_POST_TIMEOUT_SECONDS,
) -> Callable[[], Optional[int]]:
    """Return a channel-id resolver with NAME-based fallback.

    Resolution order, top-down — first match wins:

      1. ``DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID`` (numeric).
      2. ``DISCORD_ENGINEERING_APPROVAL_CHANNEL_NAME`` matched
         against ``GET /guilds/{DISCORD_GUILD_ID}/channels`` —
         resolved once per process and cached so we don't re-call
         Discord every job.

    The resolver returns ``None`` (not raise) when neither path
    works — :class:`ApprovalWorker.process_job` already maps None
    to ``approval_channel_unset`` (failed_retryable) so the
    operator can fix env without losing the queued card.

    Token / HTTP / env are injectable so unit tests can drive
    every branch without hitting Discord. The returned closure
    captures a small per-process cache for the resolved id —
    invalidating it requires a worker restart, which is the right
    granularity (channel renames are rare and the alternative is
    a REST call per approval card).
    """

    source: Mapping[str, str] = env if env is not None else os.environ
    token_fn = token_resolver or (lambda: resolve_discord_bot_token(env=source))
    get_fn = http_get or _default_http_get
    cache: dict[str, Optional[int]] = {}

    def _resolve() -> Optional[int]:
        # Fast path — id env wins.
        id_value = resolve_approval_channel_id(env=source)
        if id_value is not None:
            return id_value

        name_raw = source.get("DISCORD_ENGINEERING_APPROVAL_CHANNEL_NAME")
        guild_raw = source.get("DISCORD_GUILD_ID")
        if not name_raw or not name_raw.strip():
            return None
        if not guild_raw or not guild_raw.strip():
            return None

        cache_key = f"{guild_raw.strip()}::{name_raw.strip()}"
        if cache_key in cache:
            return cache[cache_key]

        token = token_fn()
        if not token:
            return None

        try:
            guild_id = int(guild_raw.strip())
        except ValueError:
            return None

        url = f"{DISCORD_API_BASE}/guilds/{guild_id}/channels"
        headers = {
            "Authorization": f"Bot {token}",
            "User-Agent": (
                "yule-runtime (eng-approval-worker; "
                "+https://github.com/codwithyc)"
            ),
        }
        try:
            response = get_fn(url, headers, timeout_seconds)
        except Exception:  # noqa: BLE001 - never let resolver crash worker
            logger.warning(
                "approval channel resolver: REST GET raised — falling back to None",
                exc_info=True,
            )
            return None

        if response.status < 200 or response.status >= 300:
            # Non-2xx — log + cache None *only* on permanent shapes
            # (404 guild missing). Transient 5xx / 429 stays uncached
            # so the next job retries.
            if response.status in (401, 403, 404):
                cache[cache_key] = None
            return None

        try:
            channels = json.loads(response.body_text or "[]")
        except json.JSONDecodeError:
            return None
        if not isinstance(channels, list):
            return None

        target = name_raw.strip().lstrip("#")
        for entry in channels:
            if not isinstance(entry, dict):
                continue
            channel_name = str(entry.get("name") or "").strip()
            if channel_name == target:
                try:
                    resolved = int(entry["id"])
                except (KeyError, TypeError, ValueError):
                    continue
                cache[cache_key] = resolved
                return resolved

        # Found channel list but no name match — cache None so we
        # don't keep paying the GET cost; operator must restart
        # after fixing the env.
        cache[cache_key] = None
        return None

    return _resolve


def _default_http_get(
    url: str,
    headers: Mapping[str, str],
    timeout: float,
) -> PostResponse:
    request = urllib.request.Request(
        url, headers=dict(headers), method="GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            return PostResponse(
                status=int(response.status),
                body_text=text,
                headers=dict(response.headers.items()),
            )
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - best-effort body capture
            pass
        return PostResponse(
            status=int(exc.code),
            body_text=body_text,
            headers=dict(exc.headers.items()) if exc.headers else {},
        )


# ---------------------------------------------------------------------------
# Sync HTTP layer (urllib) — wrapped in to_thread by the async post_fn
# ---------------------------------------------------------------------------


def _default_http_post(
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout: float,
) -> PostResponse:
    request = urllib.request.Request(
        url, data=body, headers=dict(headers), method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            return PostResponse(
                status=int(response.status),
                body_text=text,
                headers=dict(response.headers.items()),
            )
    except urllib.error.HTTPError as exc:
        # Non-2xx — capture body + status so the caller can map to
        # an error constant. Headers can include rate-limit info.
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - best-effort body capture
            pass
        return PostResponse(
            status=int(exc.code),
            body_text=body_text,
            headers=dict(exc.headers.items()) if exc.headers else {},
        )


# ---------------------------------------------------------------------------
# Production post_fn factory
# ---------------------------------------------------------------------------


def build_production_post_fn(
    *,
    channel_resolver: Optional[Callable[[], Optional[int]]] = None,
    token_resolver: Optional[Callable[[], Optional[str]]] = None,
    http_post: Optional[HttpPostFn] = None,
    timeout_seconds: float = DEFAULT_POST_TIMEOUT_SECONDS,
) -> Callable[[ApprovalRequest, str], Awaitable[Mapping[str, Any]]]:
    """Return an ``ApprovalPostFn`` that POSTs the rendered
    approval card to Discord.

    Resolvers / http hook all default to env-backed real
    implementations. Tests inject stubs to exercise the error
    mapping without touching Discord.

    Each call resolves the channel id + token at request time —
    so an env change (operator fixing
    ``DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID`` mid-run) takes
    effect on the next ``approval_post`` job without a worker
    restart.
    """

    channel_resolver = channel_resolver or resolve_approval_channel_id
    token_resolver = token_resolver or (lambda: resolve_discord_bot_token())
    http_post = http_post or _default_http_post

    async def _post_fn(
        request: ApprovalRequest, rendered_text: str
    ) -> Mapping[str, Any]:
        channel_id = channel_resolver()
        if channel_id is None:
            # ApprovalWorker.process_job already converts a None
            # channel into ``approval_channel_unset``; we still
            # raise here so the worker's exception path lands the
            # row in failed_retryable with a clear error if the
            # worker ever skips its own resolver.
            raise ApprovalPostError(
                ERROR_TOKEN_MISSING
                if not token_resolver()
                else "approval_channel_unset"
            )

        token = token_resolver()
        if not token:
            raise ApprovalPostError(ERROR_TOKEN_MISSING)

        url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
        headers = {
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "yule-runtime (eng-approval-worker; +https://github.com/codwithyc)",
        }
        body = json.dumps(
            {"content": rendered_text}, ensure_ascii=False
        ).encode("utf-8")

        try:
            response: PostResponse = await asyncio.to_thread(
                http_post, url, headers, body, timeout_seconds
            )
        except urllib.error.URLError as exc:
            # Connection-level error (no route to host, DNS, …). The
            # token might still be in the request object's headers —
            # build a sanitised exception that never carries it.
            raise ApprovalPostError(_describe_url_error(exc)) from None
        except TimeoutError:
            raise ApprovalPostError(ERROR_TIMEOUT) from None
        except Exception as exc:  # noqa: BLE001 - any other transport issue
            raise ApprovalPostError(
                f"approval_post_transport_error: {type(exc).__name__}"
            ) from None

        return _map_response(response, request=request, channel_id=channel_id)

    return _post_fn


def _map_response(
    response: PostResponse,
    *,
    request: ApprovalRequest,
    channel_id: int,
) -> Mapping[str, Any]:
    """Translate a Discord HTTP response to the worker's contract.

    Success → dict with ``posted_message_id`` / ``channel_id`` so
    :class:`ApprovalWorker.process_job` can stash them on the
    queue row's ``result_json``. Anything else raises
    :class:`ApprovalPostError` with a constant the queue's audit
    surface can grep for.
    """

    if 200 <= response.status < 300:
        message_id: Optional[int] = None
        try:
            payload = json.loads(response.body_text or "{}")
            raw = payload.get("id")
            if raw is not None:
                message_id = int(raw)
        except Exception:  # noqa: BLE001 - non-fatal; the post still succeeded
            logger.warning(
                "approval post: response body not JSON; cannot capture message id"
            )
        return {
            "posted_message_id": message_id,
            "channel_id": channel_id,
            "approval_kind": request.approval_kind,
        }

    if response.status == 401:
        raise ApprovalPostError(ERROR_UNAUTHORIZED)
    if response.status == 403:
        raise ApprovalPostError(ERROR_FORBIDDEN)
    if response.status == 404:
        raise ApprovalPostError(ERROR_CHANNEL_NOT_FOUND)
    if response.status == 429:
        # Discord includes ``retry_after`` in the body / headers.
        # We pass it through as part of the error string so a
        # future requeue helper can read it; we never expose token.
        retry_after = (
            response.headers.get("Retry-After")
            or response.headers.get("retry-after")
            or ""
        )
        suffix = f" retry_after={retry_after}" if retry_after else ""
        raise ApprovalPostError(f"{ERROR_RATE_LIMITED}{suffix}")
    raise ApprovalPostError(f"approval_post_http_{response.status}")


def _describe_url_error(exc: urllib.error.URLError) -> str:
    """Sanitised description for connection-level urllib failures.

    Never includes the request body or headers (token) — only the
    reason (DNS error, refused connection, etc).
    """

    reason = getattr(exc, "reason", None)
    if reason is None:
        return "approval_post_url_error"
    return f"approval_post_url_error: {type(reason).__name__}"


class ApprovalPostError(RuntimeError):
    """Raised by the production post_fn on any non-2xx outcome.

    The error message is one of the ``ERROR_*`` constants above
    so the worker's ``_short_error`` helper produces a stable
    string for the queue audit trail. Bot token never enters the
    message.
    """


__all__ = (
    "ApprovalPostError",
    "DEFAULT_POST_TIMEOUT_SECONDS",
    "DISCORD_API_BASE",
    "ERROR_CHANNEL_NOT_FOUND",
    "ERROR_FORBIDDEN",
    "ERROR_RATE_LIMITED",
    "ERROR_TIMEOUT",
    "ERROR_TOKEN_MISSING",
    "ERROR_UNAUTHORIZED",
    "HttpGetFn",
    "HttpPostFn",
    "PostResponse",
    "TOKEN_ENV_PRIORITY",
    "build_approval_channel_resolver",
    "build_production_post_fn",
    "resolve_approval_channel_id",
    "resolve_discord_bot_token",
)
