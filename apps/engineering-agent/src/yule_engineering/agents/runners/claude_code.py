"""Claude Code (``claude`` CLI) runner — live submit + /compact (issue #185 follow-up B).

The MVP body deferred ``submit`` to ``dry_run``. This wires a *real* headless
submit path plus a ``/compact`` token-capture, both guarded so default installs
are unchanged and any failure degrades gracefully:

  * ``submit`` shells out to ``claude -p <prompt>`` **only** when the live flag
    (:data:`ENV_LIVE`) is set *and* the CLI is on PATH. Otherwise it keeps the
    deterministic dry-run contract. Any subprocess failure / timeout returns a
    ``RunnerStatus.ERROR`` response (so the role-runner dispatcher walks to the
    next candidate — the graceful fallback) with a sanitised warning detail.
  * ``compact`` invokes ``claude`` with ``/compact`` and parses a
    ``compact_boundary`` stream event for ``pre_tokens`` / ``post_tokens``. If
    the boundary cannot be parsed (CLI shape drift, non-stream output), it
    returns a :class:`CompactBoundary` with ``None`` tokens and a clear warning
    rather than guessing — the caller records the warning on the receipt.

The actual subprocess call is injectable (``config['invoke']``) so tests cover
both success and failure without a real CLI.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from .base import (
    AgentRequest,
    AgentResponse,
    AgentRunner,
    RunnerCapability,
    RunnerStatus,
)

# Opt-in flag for the live CLI submit path. Default off → dry-run (unchanged).
ENV_LIVE: str = "YULE_CLAUDE_LIVE_ENABLED"
_DEFAULT_CLI: str = "claude"
_DEFAULT_TIMEOUT: int = 120

# (returncode, stdout, stderr)
InvokeResult = Tuple[int, str, str]
InvokeFn = Callable[[Sequence[str], Optional[str], int], InvokeResult]


@dataclass(frozen=True)
class CompactBoundary:
    """Token accounting captured from a ``/compact`` run.

    ``pre_tokens`` / ``post_tokens`` are ``None`` when the boundary could not be
    parsed; ``warning`` then explains why so the caller can surface it.
    """

    pre_tokens: Optional[int]
    post_tokens: Optional[int]
    raw: str = ""
    warning: Optional[str] = None

    @property
    def parsed(self) -> bool:
        return self.pre_tokens is not None and self.post_tokens is not None

    @property
    def saved_tokens(self) -> Optional[int]:
        if not self.parsed:
            return None
        return max(0, (self.pre_tokens or 0) - (self.post_tokens or 0))


def _default_invoke(args: Sequence[str], input_text: Optional[str], timeout: int) -> InvokeResult:
    proc = subprocess.run(
        list(args),
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


class ClaudeCodeRunner(AgentRunner):
    """Wraps the local ``claude`` CLI from Anthropic's Claude Code package.

    Live submit is opt-in (:data:`ENV_LIVE`); without it the runner keeps the
    deterministic dry-run contract so the rest of the engineering-agent can be
    exercised without consuming tokens.
    """

    runner_id = "claude"
    provider = "anthropic"
    capabilities: Sequence[RunnerCapability] = (
        RunnerCapability.EXECUTE,
        RunnerCapability.ADVISE,
        RunnerCapability.REVIEW,
        RunnerCapability.PATCH_PROPOSE,
    )

    def __init__(
        self,
        *,
        config: Optional[Mapping[str, Any]] = None,
        hooks: Optional[Any] = None,
    ) -> None:
        super().__init__(config=config, hooks=hooks)
        self._cli = str(self.config.get("cli") or _DEFAULT_CLI)
        self._timeout = int(self.config.get("timeout_seconds") or _DEFAULT_TIMEOUT)
        invoke = self.config.get("invoke")
        self._invoke: InvokeFn = invoke if callable(invoke) else _default_invoke

    def is_available(self) -> bool:
        # An injected invoke (tests) implies availability without a real CLI.
        if callable(self.config.get("invoke")):
            return True
        return shutil.which(self._cli) is not None

    def live_enabled(self) -> bool:
        cfg = self.config.get("live_enabled")
        if cfg is not None:
            return bool(cfg)
        return (os.environ.get(ENV_LIVE) or "").strip().lower() in {"1", "true", "yes", "on"}

    def submit(self, request: AgentRequest) -> AgentResponse:
        if not self.is_available():
            return AgentResponse(
                runner_id=self.runner_id,
                status=RunnerStatus.UNAVAILABLE,
                text="",
                detail="claude CLI not found on PATH",
            )
        if not self.live_enabled():
            # Default contract preserved: deterministic dry-run, no tokens spent.
            return self.dry_run(request)
        return self._live_submit(request)

    def _live_submit(self, request: AgentRequest) -> AgentResponse:
        args = [self._cli, "-p", request.prompt]
        try:
            returncode, stdout, stderr = self._invoke(args, None, self._timeout)
        except subprocess.TimeoutExpired:
            return AgentResponse(
                runner_id=self.runner_id,
                status=RunnerStatus.ERROR,
                text="",
                detail=f"claude live submit timed out after {self._timeout}s",
            )
        except Exception as exc:  # noqa: BLE001 - never propagate a wiring error
            return AgentResponse(
                runner_id=self.runner_id,
                status=RunnerStatus.ERROR,
                text="",
                detail=f"claude live submit failed: {type(exc).__name__}",
            )
        if returncode != 0:
            return AgentResponse(
                runner_id=self.runner_id,
                status=RunnerStatus.ERROR,
                text="",
                detail=f"claude exited {returncode}: {_sanitise(stderr)}",
            )
        text = (stdout or "").strip()
        if not text:
            return AgentResponse(
                runner_id=self.runner_id,
                status=RunnerStatus.ERROR,
                text="",
                detail="claude returned empty output",
            )
        return AgentResponse(
            runner_id=self.runner_id,
            status=RunnerStatus.OK,
            text=text,
            detail="claude live submit",
            metrics={"live": True},
        )

    def compact(self, *, focus: Optional[str] = None) -> CompactBoundary:
        """Invoke ``/compact`` and capture ``compact_boundary`` token metadata.

        Returns a :class:`CompactBoundary`. On any failure / unparseable output
        the tokens are ``None`` and ``warning`` explains the fallback. Live flag
        + CLI availability are required; otherwise a warning-only boundary is
        returned (deterministic estimate stays the caller's responsibility).
        """

        if not self.is_available():
            return CompactBoundary(None, None, warning="claude CLI not available — token capture skipped")
        if not self.live_enabled():
            return CompactBoundary(
                None, None, warning=f"{ENV_LIVE} not set — live /compact token capture skipped"
            )
        instruction = "/compact" + (f" {focus}" if focus else "")
        args = [self._cli, "-p", instruction, "--output-format", "stream-json", "--verbose"]
        try:
            returncode, stdout, stderr = self._invoke(args, None, self._timeout)
        except subprocess.TimeoutExpired:
            return CompactBoundary(None, None, warning=f"/compact timed out after {self._timeout}s")
        except Exception as exc:  # noqa: BLE001
            return CompactBoundary(None, None, warning=f"/compact failed: {type(exc).__name__}")
        if returncode != 0:
            return CompactBoundary(None, None, raw=stdout or "", warning=f"/compact exited {returncode}")
        return parse_compact_boundary(stdout or "")


def parse_compact_boundary(stdout: str) -> CompactBoundary:
    """Scan ``claude`` stream-json output for a ``compact_boundary`` event.

    Lenient: tolerates non-JSON lines and several key spellings
    (``pre_tokens`` / ``preTokens`` / ``pre_compaction_tokens``). Returns a
    warning-only boundary when no parsable event is found.
    """

    for line in stdout.splitlines():
        line = line.strip()
        if not line or "compact_boundary" not in line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        meta = obj.get("compact_metadata")
        if not isinstance(meta, dict):
            meta = obj
        pre = _first_int(meta, ("pre_tokens", "preTokens", "pre_compaction_tokens"))
        post = _first_int(meta, ("post_tokens", "postTokens", "post_compaction_tokens"))
        if pre is None and post is None:
            continue
        return CompactBoundary(pre_tokens=pre, post_tokens=post, raw=line)
    return CompactBoundary(
        None, None, raw=stdout, warning="no compact_boundary event in claude output"
    )


def _first_int(meta: Mapping[str, Any], keys: Sequence[str]) -> Optional[int]:
    for key in keys:
        value = meta.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def _sanitise(text: str) -> str:
    """Trim stderr to a short, non-secret-bearing snippet for a detail line."""

    flat = " ".join((text or "").split())
    return flat[:160]


__all__ = (
    "ENV_LIVE",
    "ClaudeCodeRunner",
    "CompactBoundary",
    "parse_compact_boundary",
)
