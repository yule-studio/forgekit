"""Live-ready ``claude -p`` subprocess adapter — Round 4-ter of #73.

Round 4-bis landed the deterministic / record-only / live-ready 3-tier
decision seam (`claude_decision_seam.py`). The ``ExternalDecisionPort``
in that file is intentionally a thin adapter around an *injected*
callable so the seam never imports a live HTTP / API client.

This module is the first concrete implementation that callable can
point at: a bounded subprocess invocation of the locally installed
``claude`` CLI in ``-p`` (one-shot prompt) mode. It exists so an
operator who has the ``claude`` CLI on the host can opt the runtime
into a *real* judgement layer without the runtime ever talking to a
network library directly.

Hard rails (in priority order):

  * **Off by default.** :func:`claude_subprocess_factory_from_env`
    only returns a callable when ``YULE_CLAUDE_DECISION_LIVE_ENABLED``
    is truthy. Composing the env factory with this adapter therefore
    leaves every supervisor that hasn't opted in on the deterministic
    fallback.
  * **No live SDK / HTTP client import.** This module spawns a
    subprocess; nothing else. The CLI binary is responsible for any
    live API call.
  * **Bounded wall-clock.** Every invocation goes through
    :func:`subprocess.run` with ``timeout=...``; on
    :class:`subprocess.TimeoutExpired` we kill the child and return a
    non-actionable verdict. The default timeout is short
    (:data:`DEFAULT_LIVE_TIMEOUT_SECONDS`) and clamped to a safe band
    so an operator typo can't stall an autonomy tick.
  * **Quiet failures.** Every failure mode (binary missing, non-zero
    exit, empty stdout, malformed JSON, raise inside the runner)
    surfaces as a non-actionable :class:`DecisionResponse` with a
    descriptive ``metadata['subprocess_outcome']`` string. The
    composer above (:func:`compose_decision_port`) then falls through
    to the next port (record-only / deterministic) so the runtime
    never stalls on a misbehaving live tier.
  * **No prompt persistence.** The adapter writes to stdin only; it
    never spills the rendered prompt to a file. Audit goes through the
    record-only port (see ``claude_decision_seam.py``).

Output protocol:

  The adapter writes a single JSON document to the CLI's stdin
  containing the :class:`DecisionRequest` payload + a small contract
  reminder. It expects the CLI to reply with one JSON object on stdout
  matching the :class:`DecisionResponse` schema (or a compatible
  Mapping that the seam's :class:`ExternalDecisionPort` knows how to
  normalise). Anything else falls back.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Tuple

from .claude_decision_seam import (
    DEFAULT_EXTERNAL_TIMEOUT_SECONDS,
    DecisionRequest,
    DecisionResponse,
    coerce_decision_request,
)


logger = logging.getLogger(__name__)


__all__ = (
    "ClaudeSubprocessConfig",
    "DEFAULT_LIVE_BINARY",
    "DEFAULT_LIVE_TIMEOUT_SECONDS",
    "ENV_LIVE_BINARY",
    "ENV_LIVE_ENABLED",
    "ENV_LIVE_EXTRA_ARGS",
    "ENV_LIVE_MODEL",
    "ENV_LIVE_TIMEOUT",
    "SUBPROCESS_OUTCOME_BAD_JSON",
    "SUBPROCESS_OUTCOME_BINARY_MISSING",
    "SUBPROCESS_OUTCOME_DISABLED",
    "SUBPROCESS_OUTCOME_EMPTY",
    "SUBPROCESS_OUTCOME_NONZERO_EXIT",
    "SUBPROCESS_OUTCOME_OK",
    "SUBPROCESS_OUTCOME_RUNNER_RAISED",
    "SUBPROCESS_OUTCOME_TIMEOUT",
    "SUBPROCESS_OUTCOME_UNSUPPORTED_PAYLOAD",
    "build_claude_subprocess_callable",
    "claude_subprocess_factory_from_env",
    "render_subprocess_prompt",
)


# ---------------------------------------------------------------------------
# Env contract
# ---------------------------------------------------------------------------


# Master opt-in. Composes with ``YULE_CLAUDE_DECISION_PROVIDER`` (which
# selects which tiers participate at all): even with the provider chain
# set to ``external,deterministic`` the live tier stays inactive until
# this flag is also truthy. Two-key opt-in keeps a typo on either side
# from surfacing a live shell call.
ENV_LIVE_ENABLED: str = "YULE_CLAUDE_DECISION_LIVE_ENABLED"

# CLI binary name. Defaults to ``claude``; operators with a pinned
# install at e.g. ``/opt/anthropic/claude`` can override here.
ENV_LIVE_BINARY: str = "YULE_CLAUDE_DECISION_LIVE_BINARY"

# Optional model override (e.g. ``claude-haiku-4-5``). Forwarded as
# ``--model`` when set; the CLI's default model is used otherwise.
ENV_LIVE_MODEL: str = "YULE_CLAUDE_DECISION_LIVE_MODEL"

# Per-call timeout. Clamped to ``[0.5, 30.0]`` seconds; live decisions
# are tactical, not synthesis — anything above 30 s is a typo.
ENV_LIVE_TIMEOUT: str = "YULE_CLAUDE_DECISION_LIVE_TIMEOUT_SECONDS"

# Comma-separated extra CLI args to forward verbatim. Useful for
# ``--allowedTools`` lockdown or ``--no-update`` in production.
ENV_LIVE_EXTRA_ARGS: str = "YULE_CLAUDE_DECISION_LIVE_EXTRA_ARGS"


DEFAULT_LIVE_BINARY: str = "claude"
DEFAULT_LIVE_TIMEOUT_SECONDS: float = float(DEFAULT_EXTERNAL_TIMEOUT_SECONDS)


# ---------------------------------------------------------------------------
# Subprocess outcome vocabulary
# ---------------------------------------------------------------------------


# Surfaced through ``DecisionResponse.metadata['subprocess_outcome']``.
# Kept as plain strings so dashboards / tests / audit JSONL grep on
# a stable surface.
SUBPROCESS_OUTCOME_OK: str = "ok"
SUBPROCESS_OUTCOME_TIMEOUT: str = "timeout"
SUBPROCESS_OUTCOME_BINARY_MISSING: str = "binary_missing"
SUBPROCESS_OUTCOME_NONZERO_EXIT: str = "nonzero_exit"
SUBPROCESS_OUTCOME_EMPTY: str = "empty_stdout"
SUBPROCESS_OUTCOME_BAD_JSON: str = "malformed_json"
SUBPROCESS_OUTCOME_UNSUPPORTED_PAYLOAD: str = "unsupported_payload"
SUBPROCESS_OUTCOME_RUNNER_RAISED: str = "runner_raised"
SUBPROCESS_OUTCOME_DISABLED: str = "disabled_by_env"


# ---------------------------------------------------------------------------
# Config model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaudeSubprocessConfig:
    """Resolved configuration for one ``claude -p`` invocation.

    Frozen so the callable closure can rely on immutable args. ``runner``
    is left off the dataclass because tests inject a stub at the
    :func:`build_claude_subprocess_callable` level rather than per
    config instance.
    """

    binary: str = DEFAULT_LIVE_BINARY
    model: Optional[str] = None
    timeout_seconds: float = DEFAULT_LIVE_TIMEOUT_SECONDS
    extra_args: Tuple[str, ...] = ()

    def render_argv(self) -> Tuple[str, ...]:
        argv: list[str] = [self.binary, "-p"]
        if self.model:
            argv.extend(["--model", self.model])
        argv.extend(self.extra_args)
        return tuple(argv)


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def render_subprocess_prompt(request: DecisionRequest) -> str:
    """Render the JSON-on-stdin payload the CLI will see.

    Kept tiny on purpose — the live provider's prompt template lives
    on the CLI side. The runtime hands over the typed
    :class:`DecisionRequest` plus a contract reminder telling the live
    side which JSON shape to reply with. The live side is free to
    ignore the contract; the adapter normalises whatever it gets into a
    :class:`DecisionResponse` (or a non-actionable fallback).
    """

    payload = {
        "kind": request.kind,
        "summary": request.summary,
        "facts": dict(request.facts or {}),
        "session_id": request.session_id,
        "job_id": request.job_id,
        "requested_at": request.requested_at,
    }
    contract = (
        "You are the Yule autonomy decision layer. Return one JSON "
        'object on stdout with the schema {"skip": bool, "advance": '
        'bool, "reason": str, "confidence": "low"|"medium"|"high", '
        '"metadata": object}. Skip when the runtime should not act on '
        "this request. Advance when the runtime should keep going on "
        "the deterministic fast-path. No prose around the JSON."
    )
    return contract + "\n\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# Adapter callable
# ---------------------------------------------------------------------------


# Type alias for the runner the callable uses. Defaults to
# :func:`subprocess.run`; tests inject a fake.
SubprocessRunner = Callable[..., subprocess.CompletedProcess]


def build_claude_subprocess_callable(
    config: ClaudeSubprocessConfig,
    *,
    runner: Optional[SubprocessRunner] = None,
    binary_resolver: Optional[Callable[[str], Optional[str]]] = None,
) -> Callable[..., DecisionResponse]:
    """Return an :class:`ExternalDecisionPort`-compatible callable.

    ``runner`` is the function used to invoke the subprocess. Defaults
    to :func:`subprocess.run`; tests pass a stub returning a fake
    :class:`subprocess.CompletedProcess` so no real ``claude`` binary
    has to exist on the test host.

    ``binary_resolver`` is the function used to verify the configured
    binary exists on PATH. Defaults to :func:`shutil.which`; tests can
    pass ``lambda name: None`` to simulate a missing binary without
    having to clear ``PATH``.

    The returned callable has the signature
    :class:`claude_decision_seam.ExternalDecisionCallable`: it accepts
    ``request: DecisionRequest`` (and an optional
    ``timeout_seconds`` override) and returns a
    :class:`DecisionResponse`. It NEVER raises — every failure mode
    becomes a non-actionable response so the seam composer falls
    through to the next port.
    """

    real_runner: SubprocessRunner = runner or subprocess.run
    real_resolver = binary_resolver or shutil.which

    def _call(
        *,
        request: DecisionRequest,
        timeout_seconds: Optional[float] = None,
    ) -> DecisionResponse:
        coerced = coerce_decision_request(request)
        effective_timeout = _clamp_timeout(
            timeout_seconds if timeout_seconds is not None else config.timeout_seconds
        )

        resolved_binary = _resolve_binary(config.binary, real_resolver)
        if resolved_binary is None:
            return _non_actionable(
                outcome=SUBPROCESS_OUTCOME_BINARY_MISSING,
                detail=f"binary not found on PATH: {config.binary!r}",
            )

        prompt = render_subprocess_prompt(coerced)
        argv = (resolved_binary,) + config.render_argv()[1:]

        try:
            completed = real_runner(
                argv,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return _non_actionable(
                outcome=SUBPROCESS_OUTCOME_TIMEOUT,
                detail=f"claude subprocess timed out after {effective_timeout:.2f}s",
            )
        except FileNotFoundError:
            # Race vs ``which``: binary disappeared between resolve + spawn.
            return _non_actionable(
                outcome=SUBPROCESS_OUTCOME_BINARY_MISSING,
                detail=f"binary vanished during spawn: {resolved_binary!r}",
            )
        except Exception as exc:  # noqa: BLE001 - never crash the runtime
            logger.warning(
                "claude subprocess runner raised: %s", exc, exc_info=True
            )
            return _non_actionable(
                outcome=SUBPROCESS_OUTCOME_RUNNER_RAISED,
                detail=f"runner raised: {type(exc).__name__}: {exc}",
            )

        return _interpret_completed_process(completed)

    return _call


def _interpret_completed_process(
    completed: subprocess.CompletedProcess,
) -> DecisionResponse:
    """Translate one :class:`subprocess.CompletedProcess` into a verdict.

    Split out so tests can drive the parser directly with hand-built
    completed processes (the runner stub assembles them).
    """

    if completed.returncode != 0:
        stderr_excerpt = (completed.stderr or "").strip().splitlines()
        first_line = stderr_excerpt[0] if stderr_excerpt else ""
        return _non_actionable(
            outcome=SUBPROCESS_OUTCOME_NONZERO_EXIT,
            detail=(
                f"exit={completed.returncode}; "
                f"stderr_first_line={first_line[:200]!r}"
            ),
        )

    raw_stdout = (completed.stdout or "").strip()
    if not raw_stdout:
        return _non_actionable(
            outcome=SUBPROCESS_OUTCOME_EMPTY,
            detail="claude subprocess returned empty stdout",
        )

    parsed = _parse_first_json_object(raw_stdout)
    if parsed is None:
        return _non_actionable(
            outcome=SUBPROCESS_OUTCOME_BAD_JSON,
            detail=f"could not parse JSON object out of stdout (len={len(raw_stdout)})",
        )

    if not isinstance(parsed, Mapping):
        return _non_actionable(
            outcome=SUBPROCESS_OUTCOME_UNSUPPORTED_PAYLOAD,
            detail=f"top-level JSON is {type(parsed).__name__}, expected object",
        )

    skip = bool(parsed.get("skip", False))
    advance = bool(parsed.get("advance", False))
    if not skip and not advance:
        # Live tier explicitly declined to commit — surface a
        # non-actionable response so the chain falls through. We DO
        # propagate the upstream metadata so the operator can see
        # whatever the live side wanted to say.
        return _non_actionable(
            outcome=SUBPROCESS_OUTCOME_OK,
            detail=str(parsed.get("reason") or "live tier non-actionable"),
            extra_metadata=dict(parsed.get("metadata") or {}),
        )

    return DecisionResponse(
        skip=skip,
        advance=advance,
        reason=str(parsed.get("reason") or "claude_subprocess"),
        confidence=str(parsed.get("confidence") or "medium"),
        metadata={
            **(dict(parsed.get("metadata") or {})),
            "provider": "claude_subprocess",
            "subprocess_outcome": SUBPROCESS_OUTCOME_OK,
        },
        decided_at=str(parsed.get("decided_at") or _now_iso()),
    )


def _parse_first_json_object(stdout: str) -> Any:
    """Best-effort JSON extraction.

    The CLI may print log lines around the JSON (e.g. tip-of-day
    chatter). We first try a strict parse; on failure we look for the
    first ``{...}`` span and try again. Anything else returns None so
    the caller can produce a malformed-payload fallback.
    """

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        pass

    start = stdout.find("{")
    end = stdout.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = stdout[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Env factory
# ---------------------------------------------------------------------------


def claude_subprocess_factory_from_env(
    env: Mapping[str, str],
    *,
    runner: Optional[SubprocessRunner] = None,
    binary_resolver: Optional[Callable[[str], Optional[str]]] = None,
) -> Optional[Callable[..., DecisionResponse]]:
    """Compose the live subprocess callable from ``env``.

    Returns ``None`` (= live tier disabled) when:

      * :data:`ENV_LIVE_ENABLED` is unset / falsy, or
      * the configured binary cannot be resolved on PATH.

    The two-key opt-in (provider chain *and* this flag) keeps the live
    tier dormant until the operator deliberately opts in. Misconfig
    (typo in the binary name, etc.) falls back to ``None`` so the env
    factory above logs the tier as ``skipped``.
    """

    raw_enabled = (env.get(ENV_LIVE_ENABLED) or "").strip().lower()
    if raw_enabled not in {"1", "true", "yes", "on"}:
        return None

    config = _config_from_env(env)
    real_resolver = binary_resolver or shutil.which
    if real_resolver(config.binary) is None:
        # Don't surface the callable at all when the binary is missing.
        # Composing the env factory above will record the tier as
        # ``skipped`` so the operator sees one clear "live tier off"
        # line in the supervisor log.
        logger.warning(
            "claude subprocess adapter: binary %r not on PATH — "
            "live tier disabled",
            config.binary,
        )
        return None

    return build_claude_subprocess_callable(
        config, runner=runner, binary_resolver=real_resolver
    )


def _config_from_env(env: Mapping[str, str]) -> ClaudeSubprocessConfig:
    binary = (env.get(ENV_LIVE_BINARY) or "").strip() or DEFAULT_LIVE_BINARY
    model_raw = (env.get(ENV_LIVE_MODEL) or "").strip()
    model = model_raw or None

    raw_timeout = (env.get(ENV_LIVE_TIMEOUT) or "").strip()
    timeout = DEFAULT_LIVE_TIMEOUT_SECONDS
    if raw_timeout:
        try:
            timeout = float(raw_timeout)
        except ValueError:
            timeout = DEFAULT_LIVE_TIMEOUT_SECONDS
    timeout = _clamp_timeout(timeout)

    raw_extras = (env.get(ENV_LIVE_EXTRA_ARGS) or "").strip()
    extras: Tuple[str, ...] = ()
    if raw_extras:
        extras = tuple(arg for arg in (a.strip() for a in raw_extras.split(",")) if arg)

    return ClaudeSubprocessConfig(
        binary=binary,
        model=model,
        timeout_seconds=timeout,
        extra_args=extras,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_binary(
    binary: str, resolver: Callable[[str], Optional[str]]
) -> Optional[str]:
    if not binary:
        return None
    if os.sep in binary or binary.startswith("./"):
        # Already an absolute / explicit path — let the spawn step
        # surface a FileNotFoundError if it doesn't exist.
        return binary
    return resolver(binary)


def _clamp_timeout(value: float) -> float:
    try:
        floated = float(value)
    except (TypeError, ValueError):
        floated = DEFAULT_LIVE_TIMEOUT_SECONDS
    if floated < 0.5:
        return 0.5
    if floated > 30.0:
        return 30.0
    return floated


def _non_actionable(
    *,
    outcome: str,
    detail: str,
    extra_metadata: Optional[Mapping[str, Any]] = None,
) -> DecisionResponse:
    metadata = {
        "provider": "claude_subprocess",
        "subprocess_outcome": outcome,
        "fallback": True,
    }
    if extra_metadata:
        for key, value in extra_metadata.items():
            metadata.setdefault(key, value)
    return DecisionResponse(
        skip=False,
        advance=False,
        reason=detail,
        confidence="none",
        metadata=metadata,
        decided_at=_now_iso(),
    )


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()
