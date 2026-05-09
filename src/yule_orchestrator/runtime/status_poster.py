"""``#봇-상태`` Discord poster — A-M7.1.

CLI hook + pure helpers that take a :class:`RuntimeStatusReport`
(M6.3) plus circuit / fallback context (M7) and post the rendered
markdown summary to the engineering ``#봇-상태`` channel.

Scope of this milestone:

  * **Pure helpers** — channel resolver (ID env first, NAME via
    Discord REST GET fallback), state-hash dedup, async post_fn
    that POSTs to ``/channels/{id}/messages``. All HTTP / token
    primitives are reused from
    :mod:`agents.job_queue.approval_discord_poster` so the
    operator-facing token security guarantees and error mapping
    stay consistent across the engineering bot's two REST callers.

  * **CLI hook** — ``yule runtime status --post-discord`` invokes
    the helper. The same dedup state store the CLI uses lets a
    future periodic loop (M7.x) skip identical posts without
    re-implementing the decision.

Out of scope (per A-M7.1 spec): supervisor-driven periodic
posting loop, in-process gateway synthesis wiring, circuit-break
reset CLI.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Any,
    Awaitable,
    Callable,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from ..agents.job_queue.approval_discord_poster import (
    DEFAULT_POST_TIMEOUT_SECONDS,
    DISCORD_API_BASE,
    HttpGetFn,
    HttpPostFn,
    PostResponse,
    _default_http_get,
    _default_http_post,
    resolve_discord_bot_token,
)
from .circuit_breaker import CircuitSnapshot
from .fallback import FallbackAuditRecord
from .status import (
    AUTONOMY_OUTCOME_ERROR,
    AUTONOMY_OUTCOME_LOCKED,
    CompletionFunnelSummary,
    RuntimeStatusReport,
    render_autonomy_summary_markdown,
)
from .status_summary import render_status_summary_markdown


logger = logging.getLogger(__name__)


# Channel env keys — kept distinct from the approval ones so an
# operator's diagnostic / .env audit can see at a glance which
# channel each REST caller owns.
STATUS_CHANNEL_ID_ENV: str = "DISCORD_ENGINEERING_STATUS_CHANNEL_ID"
STATUS_CHANNEL_NAME_ENV: str = "DISCORD_ENGINEERING_STATUS_CHANNEL_NAME"
GUILD_ID_ENV: str = "DISCORD_GUILD_ID"


# Error constants — kept in the same shape as
# ``approval_discord_poster.ERROR_*`` so a status diagnostic can
# match prefix-by-feature ("status_post_*" vs "approval_post_*").
STATUS_POST_TOKEN_MISSING: str = "status_post_token_missing"
STATUS_POST_CHANNEL_UNSET: str = "status_post_channel_unset"
STATUS_POST_UNAUTHORIZED: str = "status_post_unauthorized"
STATUS_POST_FORBIDDEN: str = "status_post_forbidden"
STATUS_POST_CHANNEL_NOT_FOUND: str = "status_post_channel_not_found"
STATUS_POST_RATE_LIMITED: str = "status_post_rate_limited"
STATUS_POST_TIMEOUT: str = "status_post_timeout"


# Discord caps message content at 2000 chars. We trim slightly
# below to leave room for the dedup-key footer the post adds, so
# a heavily-degraded snapshot is still posted (truncated tail is
# better than no post at all).
STATUS_POST_CONTENT_LIMIT: int = 1900


_USER_AGENT: str = (
    "yule-runtime (eng-status-poster; +https://github.com/codwithyc)"
)


class StatusPostError(RuntimeError):
    """Raised by the production post_fn on any non-2xx outcome.

    The error message is one of the ``STATUS_POST_*`` constants
    above (with optional retry_after suffix on 429). Bot tokens
    never enter the message.
    """


# ---------------------------------------------------------------------------
# Channel resolver — ID env first, NAME-via-REST fallback.
# ---------------------------------------------------------------------------


def build_status_channel_resolver(
    *,
    env: Optional[Mapping[str, str]] = None,
    token_resolver: Optional[Callable[[], Optional[str]]] = None,
    http_get: Optional[HttpGetFn] = None,
    timeout_seconds: float = DEFAULT_POST_TIMEOUT_SECONDS,
) -> Callable[[], Optional[int]]:
    """Resolve the status channel id with NAME-fallback.

    Resolution order, top-down — first match wins:

      1. ``DISCORD_ENGINEERING_STATUS_CHANNEL_ID`` (numeric).
      2. ``DISCORD_ENGINEERING_STATUS_CHANNEL_NAME`` matched
         against ``GET /guilds/{DISCORD_GUILD_ID}/channels``,
         resolved once per process and cached so we don't re-call
         Discord every post.

    Returns ``None`` (not raise) when neither path resolves — the
    poster maps that to :data:`STATUS_POST_CHANNEL_UNSET` so the
    operator sees a clear error instead of a generic crash.
    Mirror of :func:`agents.job_queue.approval_discord_poster.build_approval_channel_resolver`
    so the two REST callers behave identically toward Discord.
    """

    source: Mapping[str, str] = env if env is not None else os.environ
    token_fn = token_resolver or (
        lambda: resolve_discord_bot_token(env=source)
    )
    get_fn = http_get or _default_http_get
    cache: dict[str, Optional[int]] = {}

    def _resolve() -> Optional[int]:
        # Fast path — id env wins.
        id_value = _read_int_env(source, STATUS_CHANNEL_ID_ENV)
        if id_value is not None:
            return id_value

        name_raw = source.get(STATUS_CHANNEL_NAME_ENV)
        guild_raw = source.get(GUILD_ID_ENV)
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
            "User-Agent": _USER_AGENT,
        }
        try:
            response = get_fn(url, headers, timeout_seconds)
        except Exception:  # noqa: BLE001 - never let resolver crash poster
            logger.warning(
                "status channel resolver: REST GET raised — falling back to None",
                exc_info=True,
            )
            return None

        if response.status < 200 or response.status >= 300:
            # Permanent shapes cache None; transient 5xx / 429 stay
            # uncached so the next post retries the lookup.
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
            if str(entry.get("name") or "").strip() == target:
                try:
                    resolved = int(entry["id"])
                except (KeyError, TypeError, ValueError):
                    continue
                cache[cache_key] = resolved
                return resolved

        cache[cache_key] = None
        return None

    return _resolve


def _read_int_env(
    source: Mapping[str, str], key: str
) -> Optional[int]:
    raw = source.get(key)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Dedup — state-hash so identical posts don't repeat.
# ---------------------------------------------------------------------------


def compute_status_dedup_key(
    *,
    report: RuntimeStatusReport,
    circuits: Optional[Mapping[str, CircuitSnapshot]] = None,
    fallbacks: Optional[Sequence[FallbackAuditRecord]] = None,
) -> str:
    """SHA-256 over the operator-relevant signal bits.

    What goes into the hash (sorted, JSON-serialised so order is
    irrelevant):

      * profile (so a future multi-profile run posts per profile)
      * service_id list of services NOT in the alive state
        (stale + unknown implemented services)
      * service_id list of currently circuit-open services
      * job_id list of failed_terminal rows in ``failed_recent``
      * fallback_id list across the supplied fallback records
      * Round 4: autonomy producer tick_ids that errored, sources
        that landed on locked_by_other (persistent lock contention),
        and the set of session_ids parked on a non-tick funnel
        status (``blocked`` / ``needs_approval``). Tick *ids* are
        stable strings, so a tick that's already been reported once
        won't re-trigger a post.

    What does NOT go in: timestamps, ages, last_reason text,
    successful dispatches. The point of the dedup is "did the
    operator-actionable state change?", not "is the snapshot
    byte-identical?".
    """

    circuit_map = dict(circuits) if circuits else {}
    fallback_seq = list(fallbacks) if fallbacks else []
    not_alive = sorted(
        s.service_id
        for s in report.services
        if s.implemented and s.health != "alive"
    )
    open_circuits = sorted(
        c.service_id for c in circuit_map.values() if c.is_open
    )
    failed_terminal_jobs = sorted(
        f.job_id for f in report.failed_recent if f.state == "failed_terminal"
    )
    fallback_ids = sorted(record.fallback_id for record in fallback_seq)
    autonomy_errored_ticks = sorted(
        t.tick_id for t in report.autonomy_recent if t.error
    )
    autonomy_locked = sorted({
        f"{d.source}:{d.session_id or d.executor_role or d.branch_hint}"
        for t in report.autonomy_recent
        for d in t.dispatches
        if d.outcome == AUTONOMY_OUTCOME_LOCKED
    })
    funnel_parked = sorted({
        f"{c.completion_status}:{c.session_id or c.job_id}"
        for c in report.completion_funnel_recent
        if c.completion_status in {"blocked", "needs_approval"}
    })

    payload = {
        "profile": report.profile,
        "not_alive_services": not_alive,
        "circuit_open_services": open_circuits,
        "failed_terminal_jobs": failed_terminal_jobs,
        "fallback_ids": fallback_ids,
        "autonomy_errored_ticks": autonomy_errored_ticks,
        "autonomy_locked_dispatches": autonomy_locked,
        "funnel_parked_sessions": funnel_parked,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_clean_state(
    *,
    report: RuntimeStatusReport,
    circuits: Optional[Mapping[str, CircuitSnapshot]] = None,
    fallbacks: Optional[Sequence[FallbackAuditRecord]] = None,
) -> bool:
    """True when nothing operator-actionable is going on.

    Used by the "post on transition only" rule — see
    :func:`should_post_status`. A clean snapshot is still a valid
    post (operator wants to see "we recovered"), but only when
    the previous post wasn't already clean.
    """

    circuit_map = dict(circuits) if circuits else {}
    fallback_seq = list(fallbacks) if fallbacks else []
    not_alive = [
        s for s in report.services if s.implemented and s.health != "alive"
    ]
    if not_alive:
        return False
    if any(c.is_open for c in circuit_map.values()):
        return False
    if any(f.state == "failed_terminal" for f in report.failed_recent):
        return False
    if fallback_seq:
        return False
    if any(t.error for t in report.autonomy_recent):
        return False
    if any(
        d.outcome in {AUTONOMY_OUTCOME_ERROR, AUTONOMY_OUTCOME_LOCKED}
        for t in report.autonomy_recent
        for d in t.dispatches
    ):
        return False
    if any(
        c.completion_status in {"blocked", "needs_approval"}
        for c in report.completion_funnel_recent
    ):
        return False
    return True


@dataclass(frozen=True)
class StatusPostDecision:
    """Outcome of :func:`should_post_status`.

    ``do_post`` is the actionable bit. ``current_key`` is the
    dedup hash for the current snapshot (always populated so the
    caller can record it after a successful post). ``reason`` is
    a short string for logs / CLI stderr.
    """

    do_post: bool
    current_key: str
    reason: str


def should_post_status(
    *,
    report: RuntimeStatusReport,
    circuits: Optional[Mapping[str, CircuitSnapshot]] = None,
    fallbacks: Optional[Sequence[FallbackAuditRecord]] = None,
    last_dedup_key: Optional[str] = None,
    force: bool = False,
) -> StatusPostDecision:
    """Decide whether to post and emit the dedup key for the next call.

    Rules (matching the A-M7.1 spec):

      * ``force=True`` always posts.
      * If the current dedup key matches *last_dedup_key*, skip
        — same operator-actionable state.
      * Otherwise, post.

    "Clean state too often" (per spec) is naturally handled because
    a clean state always hashes to the same key.
    """

    current_key = compute_status_dedup_key(
        report=report, circuits=circuits, fallbacks=fallbacks
    )
    if force:
        return StatusPostDecision(
            do_post=True, current_key=current_key, reason="forced"
        )
    if last_dedup_key and last_dedup_key == current_key:
        return StatusPostDecision(
            do_post=False,
            current_key=current_key,
            reason="dedup_key_matches_last_post",
        )
    if last_dedup_key is None:
        return StatusPostDecision(
            do_post=True,
            current_key=current_key,
            reason="initial_post",
        )
    return StatusPostDecision(
        do_post=True,
        current_key=current_key,
        reason="state_changed",
    )


# ---------------------------------------------------------------------------
# Persistent dedup state store — JSON file in the cache dir.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatusPosterStateRecord:
    """One ``status_poster_state.json`` row.

    Tiny by design — the operator doesn't need a history; the
    last successful post is enough to decide whether the next
    snapshot says anything new.
    """

    last_dedup_key: Optional[str]
    last_posted_at: Optional[float]


class StatusPosterStateStore:
    """JSON-file backed dedup state.

    Default path is ``<cache_dir>/runtime_status_poster.json``,
    where ``cache_dir`` follows the same resolution order as the
    SQLite store (``YULE_CACHE_DB_PATH`` parent → ``YULE_REPO_ROOT
    /.cache/yule`` → ``cwd/.cache/yule``). Tests inject an
    explicit path so they don't touch operator state.
    """

    def __init__(self, *, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path is not None else _default_state_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> StatusPosterStateRecord:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return StatusPosterStateRecord(
                last_dedup_key=None, last_posted_at=None
            )
        except Exception:  # noqa: BLE001 - corrupt file shouldn't crash CLI
            logger.warning(
                "status poster state load raised; treating as empty",
                exc_info=True,
            )
            return StatusPosterStateRecord(
                last_dedup_key=None, last_posted_at=None
            )
        try:
            data = json.loads(text or "{}")
        except json.JSONDecodeError:
            return StatusPosterStateRecord(
                last_dedup_key=None, last_posted_at=None
            )
        if not isinstance(data, dict):
            return StatusPosterStateRecord(
                last_dedup_key=None, last_posted_at=None
            )
        return StatusPosterStateRecord(
            last_dedup_key=_optional_str(data.get("last_dedup_key")),
            last_posted_at=_optional_float(data.get("last_posted_at")),
        )

    def save(
        self, *, last_dedup_key: str, last_posted_at: Optional[float] = None
    ) -> None:
        when = float(last_posted_at) if last_posted_at is not None else time.time()
        payload = {
            "last_dedup_key": last_dedup_key,
            "last_posted_at": when,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001 - persistence is observability
            logger.warning(
                "status poster state save raised; dedup may repost next time",
                exc_info=True,
            )


def _default_state_path() -> Path:
    db_path = os.getenv("YULE_CACHE_DB_PATH", "").strip()
    if db_path:
        return Path(db_path).expanduser().parent / "runtime_status_poster.json"
    repo_root = os.getenv("YULE_REPO_ROOT", "").strip()
    base = Path(repo_root).expanduser() if repo_root else Path.cwd()
    return base / ".cache" / "yule" / "runtime_status_poster.json"


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Production post_fn — POST markdown to the status channel.
# ---------------------------------------------------------------------------


StatusPostFn = Callable[[str], Awaitable[Mapping[str, Any]]]


def build_status_post_fn(
    *,
    channel_resolver: Optional[Callable[[], Optional[int]]] = None,
    token_resolver: Optional[Callable[[], Optional[str]]] = None,
    http_post: Optional[HttpPostFn] = None,
    timeout_seconds: float = DEFAULT_POST_TIMEOUT_SECONDS,
) -> StatusPostFn:
    """Return a coroutine that POSTs *content* to the status channel.

    Resolves the channel id + token at every call so an operator
    fixing env mid-run sees the next post pick up the change
    without a process restart.

    Token never appears in the raised :class:`StatusPostError` —
    only in the request header. Connection-level urllib failures
    are sanitised through :func:`_describe_url_error`.
    """

    channel_resolver = channel_resolver or build_status_channel_resolver()
    token_resolver = token_resolver or (lambda: resolve_discord_bot_token())
    http_post = http_post or _default_http_post

    async def _post_fn(content: str) -> Mapping[str, Any]:
        channel_id = channel_resolver()
        if channel_id is None:
            raise StatusPostError(STATUS_POST_CHANNEL_UNSET)
        token = token_resolver()
        if not token:
            raise StatusPostError(STATUS_POST_TOKEN_MISSING)

        # Discord 2000-char hard cap — trim lossily so a heavily
        # degraded summary still goes out. Adds a "(truncated)" tail
        # so the operator can tell a slice happened.
        body_text = _maybe_truncate(content)

        url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
        headers = {
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        }
        body = json.dumps(
            {"content": body_text}, ensure_ascii=False
        ).encode("utf-8")

        try:
            response: PostResponse = await asyncio.to_thread(
                http_post, url, headers, body, timeout_seconds
            )
        except urllib.error.URLError as exc:
            raise StatusPostError(_describe_url_error(exc)) from None
        except TimeoutError:
            raise StatusPostError(STATUS_POST_TIMEOUT) from None
        except Exception as exc:  # noqa: BLE001
            raise StatusPostError(
                f"status_post_transport_error: {type(exc).__name__}"
            ) from None

        return _map_status_response(response, channel_id=channel_id)

    return _post_fn


def _map_status_response(
    response: PostResponse, *, channel_id: int
) -> Mapping[str, Any]:
    if 200 <= response.status < 300:
        message_id: Optional[int] = None
        try:
            payload = json.loads(response.body_text or "{}")
            raw = payload.get("id")
            if raw is not None:
                message_id = int(raw)
        except Exception:  # noqa: BLE001 - non-fatal; the post still succeeded
            logger.warning(
                "status post: response body not JSON; cannot capture message id"
            )
        return {
            "posted_message_id": message_id,
            "channel_id": channel_id,
        }

    if response.status == 401:
        raise StatusPostError(STATUS_POST_UNAUTHORIZED)
    if response.status == 403:
        raise StatusPostError(STATUS_POST_FORBIDDEN)
    if response.status == 404:
        raise StatusPostError(STATUS_POST_CHANNEL_NOT_FOUND)
    if response.status == 429:
        retry_after = (
            response.headers.get("Retry-After")
            or response.headers.get("retry-after")
            or ""
        )
        suffix = f" retry_after={retry_after}" if retry_after else ""
        raise StatusPostError(f"{STATUS_POST_RATE_LIMITED}{suffix}")
    raise StatusPostError(f"status_post_http_{response.status}")


def _describe_url_error(exc: urllib.error.URLError) -> str:
    reason = getattr(exc, "reason", None)
    if reason is None:
        return "status_post_url_error"
    return f"status_post_url_error: {type(reason).__name__}"


def _maybe_truncate(content: str) -> str:
    if len(content) <= STATUS_POST_CONTENT_LIMIT:
        return content
    keep = STATUS_POST_CONTENT_LIMIT - len("\n_(truncated)_")
    if keep <= 0:
        keep = STATUS_POST_CONTENT_LIMIT
    return content[:keep] + "\n_(truncated)_"


# ---------------------------------------------------------------------------
# High-level orchestration — what the CLI hook calls.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatusPostOutcome:
    """What :func:`post_runtime_status_summary` decided + did.

    ``did_post`` is True iff Discord actually got a request.
    ``skipped_reason`` is set when the dedup said "no change".
    ``error`` carries the :class:`StatusPostError` message when
    posting raised — the orchestrator logs it but does not
    re-raise so a periodic loop can keep ticking.
    """

    did_post: bool
    decision_reason: str
    dedup_key: str
    skipped_reason: Optional[str] = None
    error: Optional[str] = None
    posted_message_id: Optional[int] = None


async def post_runtime_status_summary(
    *,
    report: RuntimeStatusReport,
    circuits: Optional[Mapping[str, CircuitSnapshot]] = None,
    fallbacks: Optional[Sequence[FallbackAuditRecord]] = None,
    state_store: Optional[StatusPosterStateStore] = None,
    post_fn: Optional[StatusPostFn] = None,
    force: bool = False,
    profile_label: Optional[str] = None,
) -> StatusPostOutcome:
    """Build the markdown, decide via dedup, optionally post.

    Returns a :class:`StatusPostOutcome` describing what happened.
    Posting errors are caught and embedded in the outcome's
    ``error`` field — the caller (CLI / future periodic loop)
    decides whether to escalate.
    """

    store = state_store or StatusPosterStateStore()
    last = store.load()
    decision = should_post_status(
        report=report,
        circuits=circuits,
        fallbacks=fallbacks,
        last_dedup_key=last.last_dedup_key,
        force=force,
    )
    if not decision.do_post:
        return StatusPostOutcome(
            did_post=False,
            decision_reason=decision.reason,
            dedup_key=decision.current_key,
            skipped_reason=decision.reason,
        )

    markdown = render_status_summary_markdown(
        report=report,
        circuits=circuits,
        fallbacks=fallbacks,
        profile_label=profile_label,
    )
    autonomy_markdown = render_autonomy_summary_markdown(report)
    if autonomy_markdown:
        markdown = markdown + "\n\n" + autonomy_markdown
    sender = post_fn or build_status_post_fn()

    try:
        result = await sender(markdown)
    except StatusPostError as exc:
        # Constant from the error map — no token leak risk.
        return StatusPostOutcome(
            did_post=False,
            decision_reason=decision.reason,
            dedup_key=decision.current_key,
            error=str(exc),
        )

    posted_message_id: Optional[int] = None
    if isinstance(result, Mapping):
        raw = result.get("posted_message_id")
        if isinstance(raw, int):
            posted_message_id = raw

    # Record only on success — a failed post must NOT update the
    # dedup key (otherwise an outage would mask the next legitimate
    # state change).
    store.save(last_dedup_key=decision.current_key)
    return StatusPostOutcome(
        did_post=True,
        decision_reason=decision.reason,
        dedup_key=decision.current_key,
        posted_message_id=posted_message_id,
    )


# ---------------------------------------------------------------------------
# Fallback audit aggregator — pull recent fallback events from session.extra.
# ---------------------------------------------------------------------------


def collect_recent_fallback_audits(
    *,
    session_lister: Optional[Callable[..., Sequence[Any]]] = None,
    session_limit: int = 50,
    audit_limit: int = 10,
) -> Tuple[FallbackAuditRecord, ...]:
    """Scan recent sessions and return their fallback audits.

    The CLI hook uses this to populate the ``fallbacks`` section
    of the status markdown without depending on a runtime-local
    aggregate (each session's bucket is the source of truth via
    A-M7's ``persist_fallback_audit``). Latest first; capped to
    *audit_limit* entries so a busy month doesn't push a huge
    post body.

    Tests inject *session_lister* to feed a controlled list.
    Production wiring uses ``agents.workflow_state.list_sessions``
    via lazy import so this module stays importable without the
    SQLite cache layer.
    """

    if session_lister is None:
        try:
            from ..agents.workflow_state import list_sessions as _ls

            session_lister = _ls
        except Exception:  # noqa: BLE001 - partial install fallback
            return ()

    try:
        sessions = session_lister(limit=session_limit) if session_lister else ()
    except TypeError:
        # Lister doesn't take ``limit`` kwarg (test stub) — fall back
        # to no-arg invocation.
        sessions = session_lister() if session_lister else ()
    except Exception:  # noqa: BLE001 - never let aggregation crash CLI
        logger.warning(
            "status poster: session_lister raised; no fallback audits in summary",
            exc_info=True,
        )
        return ()

    records: list[Tuple[float, FallbackAuditRecord]] = []
    for session in sessions or ():
        extra = getattr(session, "extra", None) or {}
        bucket = extra.get("fallback_audits") if isinstance(extra, dict) else None
        if not isinstance(bucket, list):
            continue
        for entry in bucket:
            if not isinstance(entry, dict):
                continue
            try:
                record = FallbackAuditRecord(
                    fallback_id=str(entry.get("fallback_id") or ""),
                    session_id=str(entry.get("session_id") or ""),
                    expected_roles=tuple(entry.get("expected_roles") or ()),
                    failed_roles=tuple(entry.get("failed_roles") or ()),
                    missing_roles=tuple(entry.get("missing_roles") or ()),
                    fallback_authority=str(
                        entry.get("fallback_authority") or ""
                    ),
                    reason=str(entry.get("reason") or ""),
                    human_approval_required=bool(
                        entry.get("human_approval_required", False)
                    ),
                    created_at=str(entry.get("created_at") or ""),
                )
            except Exception:  # noqa: BLE001 - skip malformed audit rows
                continue
            if not record.fallback_id:
                continue
            # Sortable key: ISO created_at lexicographically orders
            # fine; fall back to 0 for missing timestamps so they
            # land at the bottom.
            sort_key = record.created_at or ""
            records.append((sort_key, record))

    records.sort(key=lambda pair: pair[0], reverse=True)
    return tuple(record for _key, record in records[: max(0, int(audit_limit))])


# ---------------------------------------------------------------------------
# Completion funnel aggregator — pulls recent funnel decisions from
# session.extra so the supervisor's status post can surface "what the
# runtime decided to do after each completion" alongside the producer
# tick view. Mirrors :func:`collect_recent_fallback_audits` so the two
# read paths share the same shape and tolerate the same "session_lister
# missing" / "extra bucket malformed" failure modes without crashing
# the status post.
# ---------------------------------------------------------------------------


def collect_recent_completion_funnel(
    *,
    session_lister: Optional[Callable[..., Sequence[Any]]] = None,
    session_limit: int = 50,
    funnel_limit: int = 8,
) -> Tuple[CompletionFunnelSummary, ...]:
    """Scan recent sessions and return their funnel-decision history.

    Latest first; capped to *funnel_limit* entries so the markdown
    footer doesn't grow unbounded for a session with many turns.
    Tests inject *session_lister* to feed a controlled list. Any
    error during scanning is logged and degraded into "no funnel
    data" so the status post still goes out.
    """

    if session_lister is None:
        try:
            from ..agents.workflow_state import list_sessions as _ls

            session_lister = _ls
        except Exception:  # noqa: BLE001 - partial install fallback
            return ()

    try:
        sessions = session_lister(limit=session_limit) if session_lister else ()
    except TypeError:
        sessions = session_lister() if session_lister else ()
    except Exception:  # noqa: BLE001 - never crash status post
        logger.warning(
            "status poster: session_lister raised; no funnel rows in summary",
            exc_info=True,
        )
        return ()

    rows: list[Tuple[str, CompletionFunnelSummary]] = []
    for session in sessions or ():
        extra = getattr(session, "extra", None) or {}
        block = extra.get("completion_funnel") if isinstance(extra, dict) else None
        if not isinstance(block, Mapping):
            continue
        history = block.get("history")
        if not isinstance(history, list):
            continue
        for entry in history:
            if not isinstance(entry, dict):
                continue
            try:
                summary = CompletionFunnelSummary(
                    session_id=str(
                        entry.get("session_id")
                        or getattr(session, "session_id", "")
                        or ""
                    ),
                    job_id=str(entry.get("job_id") or ""),
                    job_type=str(entry.get("job_type") or ""),
                    completion_status=str(
                        entry.get("completion_status") or ""
                    ),
                    ticked=bool(entry.get("ticked", False)),
                    reason=str(entry.get("reason") or ""),
                    recommended_source=(
                        str(entry["recommended_source"])
                        if entry.get("recommended_source")
                        else None
                    ),
                    producer_summary=(
                        str(entry["producer_summary"])
                        if entry.get("producer_summary")
                        else None
                    ),
                    at=str(entry.get("at") or ""),
                )
            except Exception:  # noqa: BLE001 - skip malformed rows
                continue
            if not summary.completion_status:
                continue
            sort_key = summary.at or ""
            rows.append((sort_key, summary))

    rows.sort(key=lambda pair: pair[0], reverse=True)
    return tuple(record for _key, record in rows[: max(0, int(funnel_limit))])


__all__ = (
    "GUILD_ID_ENV",
    "STATUS_CHANNEL_ID_ENV",
    "STATUS_CHANNEL_NAME_ENV",
    "STATUS_POST_CHANNEL_NOT_FOUND",
    "STATUS_POST_CHANNEL_UNSET",
    "STATUS_POST_CONTENT_LIMIT",
    "STATUS_POST_FORBIDDEN",
    "STATUS_POST_RATE_LIMITED",
    "STATUS_POST_TIMEOUT",
    "STATUS_POST_TOKEN_MISSING",
    "STATUS_POST_UNAUTHORIZED",
    "StatusPostDecision",
    "StatusPostError",
    "StatusPostFn",
    "StatusPostOutcome",
    "StatusPosterStateRecord",
    "StatusPosterStateStore",
    "build_status_channel_resolver",
    "build_status_post_fn",
    "collect_recent_completion_funnel",
    "collect_recent_fallback_audits",
    "compute_status_dedup_key",
    "is_clean_state",
    "post_runtime_status_summary",
    "should_post_status",
)
