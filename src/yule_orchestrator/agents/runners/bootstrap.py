"""Env-driven role-runner dispatcher factory — A-M11b.

Bridges :mod:`agents.runners.role_runner` (interface + dispatcher
algorithm — A-M11) and the gateway/run-service bootstrap path. The
M11 dispatcher worked but ``set_role_runner_dispatch(...)`` was never
called by the actual gateway, so the engineering bot ran with the
in-process deterministic body even when Claude/Codex/Ollama were
configured.

Responsibilities of this module:

  * Resolve which providers the operator opted into via env, **without
    reading or printing secret values** (only key names + sanitised
    reasons go into the trace).
  * Wrap the existing :class:`AgentRunner` backends (Claude / Codex /
    Ollama) into the :class:`RoleRunner` priority chain in spec order.
  * Always append a :class:`DeterministicRoleRunner` so an
    unconfigured environment still produces a take.
  * Hand back a dispatch callable suitable for
    :func:`agents.runtime.engineering_team_runtime.set_role_runner_dispatch`,
    plus a :class:`RunnerWiringTrace` an operator can dump in
    ``yule supervisor status`` to answer "왜 deterministic으로 떨어졌어?".

The module is **import-light**: it pulls in the existing runner
classes lazily so a partial install (e.g. claude_code missing) cannot
crash the gateway bootstrap. Any failure during runner construction
is recorded in the trace as ``status="error"`` with a sanitised
reason, and the chain proceeds with the remaining candidates.

The audit writer registered with the dispatcher persists a
``role_runner_dispatch`` row onto ``session.extra['agent_ops_audit']``
via :func:`agents.lifecycle.agent_ops_log.append_agent_ops_audit` — the
same bucket M10 uses for autonomy decisions, so an operator can grep a
single key to see "이 role take 누가 썼고 왜 떨어졌어?".
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    Any,
    Callable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from .role_runner import (
    DEFAULT_PROVIDER_PRIORITY,
    DeterministicRoleRunner,
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    PROVIDER_DETERMINISTIC,
    PROVIDER_OLLAMA,
    RoleRunner,
    RoleRunnerInput,
    RoleRunnerOutput,
    build_role_runner_dispatcher,
    claude_role_runner,
    codex_role_runner,
    ollama_role_runner,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Env keys
# ---------------------------------------------------------------------------


# Operator opts in to specific providers via a comma-separated list.
# Default (key unset) is "no provider configured" → deterministic only.
ENV_PROVIDERS: str = "YULE_ROLE_RUNNER_PROVIDERS"
# Optional Ollama endpoint override (only the URL — no token).
ENV_OLLAMA_ENDPOINT: str = "YULE_ROLE_RUNNER_OLLAMA_ENDPOINT"


_KNOWN_PROVIDERS: Tuple[str, ...] = (
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    PROVIDER_OLLAMA,
)


# Sanitised reason strings — these are the only operator-visible
# explanations the trace exposes. We keep them as constants so a test
# can assert exact text without coupling to log lines.
REASON_NOT_OPTED_IN: str = "not opted in (env {} unset or empty)".format(
    ENV_PROVIDERS
)
REASON_UNKNOWN_PROVIDER: str = "unknown provider"
REASON_CLI_NOT_FOUND: str = "CLI not found on PATH"
REASON_ENDPOINT_UNREACHABLE: str = "endpoint unreachable"
REASON_CONSTRUCTOR_RAISED: str = "runner constructor raised"
REASON_AVAILABILITY_RAISED: str = "is_available() raised"
REASON_OPTED_IN_AVAILABLE: str = "opted in and available"
REASON_OPTED_IN_UNAVAILABLE: str = "opted in but not available"


# ---------------------------------------------------------------------------
# Trace + result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunnerWiringEntry:
    """One per-provider line in the bootstrap trace.

    ``configured`` — operator listed this provider in
    :data:`ENV_PROVIDERS`.
    ``available`` — the runner's ``is_available()`` returned True at
    bootstrap time. Note this is a snapshot — runtime availability is
    re-checked by the dispatcher on every call.
    ``reason`` — sanitised, env-key-only explanation. Never carries
    secret values or absolute paths from env vars.
    """

    provider: str
    configured: bool
    available: bool
    reason: str


@dataclass(frozen=True)
class RunnerWiringTrace:
    """Result of :func:`build_role_runner_candidates` / :func:`build_role_runner_dispatch_from_env`.

    ``entries`` is in the order providers are tried (priority order).
    ``deterministic_fallback_only`` is True when no configured provider
    was available — operator-facing summary surfaces use this for the
    "fallback 사용" audit line.
    """

    entries: Tuple[RunnerWiringEntry, ...]
    deterministic_fallback_only: bool

    def as_audit_payload(self) -> Mapping[str, Any]:
        """JSON-friendly snapshot for an agent-ops audit entry."""

        return {
            "kind": "role_runner_bootstrap",
            "deterministic_fallback_only": self.deterministic_fallback_only,
            "entries": [
                {
                    "provider": e.provider,
                    "configured": e.configured,
                    "available": e.available,
                    "reason": e.reason,
                }
                for e in self.entries
            ],
        }


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------


def _read_opted_in_providers(env: Mapping[str, str]) -> Tuple[str, ...]:
    """Parse :data:`ENV_PROVIDERS` into an ordered tuple.

    Unknown tokens are dropped silently from the active set but kept in
    the trace as ``unknown provider`` so an operator can spot a typo.
    Order is preserved so the caller can override the default
    Claude → Codex → Ollama priority by listing
    ``ollama,claude,codex`` etc.
    """

    raw = (env.get(ENV_PROVIDERS) or "").strip()
    if not raw:
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for token in raw.split(","):
        normalized = token.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return tuple(out)


def _build_claude_runner() -> Tuple[Optional[RoleRunner], bool, str]:
    """Construct the Claude RoleRunner adapter.

    Returns ``(runner, available, reason)``. ``runner`` is ``None``
    only when constructor itself failed; otherwise we return the
    adapter even if ``is_available()`` is False so the dispatcher's
    own retry semantics still kick in (a CLI installed mid-session is
    picked up on the next call).
    """

    try:
        from .claude_code import ClaudeCodeRunner
    except Exception as exc:  # noqa: BLE001 - partial install
        return None, False, _sanitised_reason(REASON_CONSTRUCTOR_RAISED, exc)
    try:
        backend = ClaudeCodeRunner()
        adapter = claude_role_runner(backend)
    except Exception as exc:  # noqa: BLE001
        return None, False, _sanitised_reason(REASON_CONSTRUCTOR_RAISED, exc)
    available, reason = _safe_availability(
        adapter, configured_reason_when_missing=REASON_CLI_NOT_FOUND
    )
    return adapter, available, reason


def _build_codex_runner() -> Tuple[Optional[RoleRunner], bool, str]:
    try:
        from .codex import CodexRunner
    except Exception as exc:  # noqa: BLE001
        return None, False, _sanitised_reason(REASON_CONSTRUCTOR_RAISED, exc)
    try:
        backend = CodexRunner()
        adapter = codex_role_runner(backend)
    except Exception as exc:  # noqa: BLE001
        return None, False, _sanitised_reason(REASON_CONSTRUCTOR_RAISED, exc)
    available, reason = _safe_availability(
        adapter, configured_reason_when_missing=REASON_CLI_NOT_FOUND
    )
    return adapter, available, reason


def _build_ollama_runner(env: Mapping[str, str]) -> Tuple[Optional[RoleRunner], bool, str]:
    try:
        from .ollama import OllamaRunner
    except Exception as exc:  # noqa: BLE001
        return None, False, _sanitised_reason(REASON_CONSTRUCTOR_RAISED, exc)
    config: dict = {}
    endpoint = (env.get(ENV_OLLAMA_ENDPOINT) or "").strip()
    if endpoint:
        config["endpoint"] = endpoint
    try:
        backend = OllamaRunner(config=config) if config else OllamaRunner()
        adapter = ollama_role_runner(backend)
    except Exception as exc:  # noqa: BLE001
        return None, False, _sanitised_reason(REASON_CONSTRUCTOR_RAISED, exc)
    available, reason = _safe_availability(
        adapter, configured_reason_when_missing=REASON_ENDPOINT_UNREACHABLE
    )
    return adapter, available, reason


def _safe_availability(
    adapter: RoleRunner,
    *,
    configured_reason_when_missing: str,
) -> Tuple[bool, str]:
    try:
        available = bool(adapter.is_available())
    except Exception as exc:  # noqa: BLE001 - is_available must never crash bootstrap
        logger.warning(
            "role-runner bootstrap: provider=%s is_available raised; "
            "treating as unavailable",
            getattr(adapter, "provider", "?"),
            exc_info=True,
        )
        return False, _sanitised_reason(REASON_AVAILABILITY_RAISED, exc)
    if available:
        return True, REASON_OPTED_IN_AVAILABLE
    return False, configured_reason_when_missing


def _sanitised_reason(prefix: str, exc: BaseException) -> str:
    """Return ``"prefix: ExceptionType"`` — never the exception message
    itself, since stack frames or env-derived strings can contain
    secrets. We keep only the type name + a short cap.
    """

    name = type(exc).__name__
    return f"{prefix}: {name}"[:200]


# ---------------------------------------------------------------------------
# Candidate factory
# ---------------------------------------------------------------------------


def build_role_runner_candidates(
    env: Optional[Mapping[str, str]] = None,
) -> Tuple[Tuple[RoleRunner, ...], RunnerWiringTrace]:
    """Construct the per-provider RoleRunner chain from *env*.

    Returns ``(candidates, trace)``. ``candidates`` is the priority-
    ordered tuple to feed :func:`build_role_runner_dispatcher`; it is
    never empty — an unconfigured env yields an empty tuple plus a
    deterministic-only trace so the dispatcher's own
    ``_ensure_terminal_fallback`` adds the safety-net runner.

    The returned ``candidates`` excludes runners whose constructor
    raised (those are recorded in the trace with ``configured=True,
    available=False`` so the operator can still see them in
    ``yule supervisor status``).
    """

    env_map: Mapping[str, str] = env if env is not None else os.environ
    opted_in = _read_opted_in_providers(env_map)
    entries: List[RunnerWiringEntry] = []
    candidates: List[RoleRunner] = []

    # Walk in the operator's listed order so an explicit
    # "ollama,claude" reverses the default priority.
    seen_known: set[str] = set()
    for name in opted_in:
        if name not in _KNOWN_PROVIDERS:
            entries.append(
                RunnerWiringEntry(
                    provider=name,
                    configured=True,
                    available=False,
                    reason=REASON_UNKNOWN_PROVIDER,
                )
            )
            continue
        seen_known.add(name)
        if name == PROVIDER_CLAUDE:
            adapter, available, reason = _build_claude_runner()
        elif name == PROVIDER_CODEX:
            adapter, available, reason = _build_codex_runner()
        else:
            adapter, available, reason = _build_ollama_runner(env_map)
        entries.append(
            RunnerWiringEntry(
                provider=name,
                configured=True,
                available=available,
                reason=reason,
            )
        )
        if adapter is not None:
            candidates.append(adapter)

    # Record providers the operator did NOT opt into so the trace
    # shows the full priority surface.
    for name in _KNOWN_PROVIDERS:
        if name in seen_known:
            continue
        entries.append(
            RunnerWiringEntry(
                provider=name,
                configured=False,
                available=False,
                reason=REASON_NOT_OPTED_IN,
            )
        )

    deterministic_only = not any(e.configured and e.available for e in entries)
    trace = RunnerWiringTrace(
        entries=tuple(entries),
        deterministic_fallback_only=deterministic_only,
    )
    return tuple(candidates), trace


# ---------------------------------------------------------------------------
# Audit writer — session.extra['agent_ops_audit']
# ---------------------------------------------------------------------------


def _build_session_audit_writer() -> Callable[[Any, Mapping[str, Any]], None]:
    """Return ``write(session, record)`` that appends the role-runner
    audit row onto the session via
    :func:`agents.lifecycle.agent_ops_log.append_agent_ops_audit`.

    Failure is swallowed — the audit is observability and must not
    block the gateway.
    """

    def _write(session: Any, record: Mapping[str, Any]) -> None:
        if session is None:
            return
        try:
            from ..lifecycle.agent_ops_log import (
                AgentOpsEntry,
                append_agent_ops_audit,
            )
            from dataclasses import replace as _replace
        except Exception:  # noqa: BLE001 - partial install fallback
            return

        try:
            entry = AgentOpsEntry(
                entry_id=_new_entry_id(),
                session_id=str(record.get("session_id") or "")
                or str(getattr(session, "session_id", "") or ""),
                action="role_runner_dispatch",
                autonomy_level="L0",
                summary=_dispatch_summary(record),
                reasoning="",
                outcome=str(record.get("status") or ""),
                references=tuple(),
                topic_key=None,
                job_id=None,
                decision_id=None,
                actor="engineering-agent",
                recorded_at=str(record.get("recorded_at") or _utc_now_iso()),
            )
        except Exception:  # noqa: BLE001
            logger.debug(
                "role-runner audit writer: AgentOpsEntry construction failed",
                exc_info=True,
            )
            return

        extra_in = getattr(session, "extra", None) or {}
        try:
            new_extra = append_agent_ops_audit(extra_in, entry)
        except Exception:  # noqa: BLE001
            return
        # Try the dataclass-replace path first (workflow_state session).
        try:
            updated = _replace(session, extra=new_extra)
        except TypeError:
            # SimpleNamespace / dict-shaped session in tests — mutate
            # the existing extra so the caller can observe the audit.
            current = getattr(session, "extra", None)
            if isinstance(current, dict):
                current.clear()
                current.update(new_extra)
            elif hasattr(session, "extra"):
                try:
                    setattr(session, "extra", dict(new_extra))
                except Exception:  # noqa: BLE001
                    pass
            return
        # workflow_state.update_session optional; persist when available.
        try:
            from ..workflow_state import update_session as _default_update
        except Exception:  # noqa: BLE001
            return
        try:
            _default_update(updated, now=datetime.now(tz=timezone.utc))
        except Exception:  # noqa: BLE001
            logger.debug(
                "role-runner audit writer: update_session raised", exc_info=True
            )

    return _write


def _dispatch_summary(record: Mapping[str, Any]) -> str:
    role = str(record.get("role") or "")
    provider = str(record.get("provider") or "")
    status = str(record.get("status") or "")
    used_fallback = bool(record.get("used_fallback"))
    suffix = " (fallback)" if used_fallback else ""
    return f"role={role} provider={provider} status={status}{suffix}"[:300]


def _new_entry_id() -> str:
    import uuid

    return uuid.uuid4().hex[:12]


def _utc_now_iso() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


# ---------------------------------------------------------------------------
# Bootstrap entrypoint
# ---------------------------------------------------------------------------


def build_role_runner_dispatch_from_env(
    env: Optional[Mapping[str, str]] = None,
    *,
    audit_writer: Optional[Callable[[Any, Mapping[str, Any]], None]] = None,
) -> Tuple[Callable[[Any, RoleRunnerInput], RoleRunnerOutput], RunnerWiringTrace]:
    """Build a session-aware dispatch callable from *env*.

    The returned callable matches the
    :func:`agents.runtime.engineering_team_runtime.set_role_runner_dispatch`
    signature ``(session, input_) → RoleRunnerOutput``. Each invocation
    builds a fresh per-call dispatcher so the dispatcher's audit writer
    can capture the session passed to *that* call — the dispatcher
    itself takes a record-only writer, so we wrap session capture
    around it.

    *audit_writer* receives ``(session, record)`` and is responsible
    for persisting the audit row. Defaults to
    :func:`_build_session_audit_writer` which appends to
    ``session.extra['agent_ops_audit']``. Tests inject a stub.
    """

    candidates, trace = build_role_runner_candidates(env)
    record_writer = audit_writer or _build_session_audit_writer()

    # Build one dispatcher per call so the inner record-only writer
    # can close over the current session. Dispatcher construction is
    # cheap (just closures); avoiding it would require a contextvar
    # which is harder to reason about during shutdown.
    def _session_aware_dispatch(
        session: Any, input_: RoleRunnerInput
    ) -> RoleRunnerOutput:
        def _record_only(record: Mapping[str, Any]) -> None:
            try:
                record_writer(session, record)
            except Exception:  # noqa: BLE001 - audit must never break dispatch
                logger.debug(
                    "role-runner audit writer raised; dropping record",
                    exc_info=True,
                )

        dispatch = build_role_runner_dispatcher(
            candidates=candidates,
            audit_writer=_record_only,
        )
        return dispatch(session, input_)

    return _session_aware_dispatch, trace


# ---------------------------------------------------------------------------
# Optional: install + log helper for bot.py / run_service.py
# ---------------------------------------------------------------------------


def install_engineering_role_runner_dispatch(
    *,
    env: Optional[Mapping[str, str]] = None,
    audit_writer: Optional[Callable[[Any, Mapping[str, Any]], None]] = None,
    on_install_failure: Optional[Callable[[BaseException], None]] = None,
) -> Optional[RunnerWiringTrace]:
    """Wire the engineering gateway role-runner dispatcher.

    Best-effort entrypoint used by bot.py + run_service.py. Imports
    :func:`agents.runtime.engineering_team_runtime.set_role_runner_dispatch`
    lazily so a partial install (no Discord plumbing) doesn't crash
    the bootstrap.

    Returns the :class:`RunnerWiringTrace` on success so the caller
    can log the configured priority chain. Returns ``None`` when
    installation could not happen at all (no engineering runtime
    module — the gateway just runs without a dispatcher and the
    in-process deterministic body keeps working).

    *on_install_failure* receives any exception raised by the wiring
    so the caller can log/audit it without re-raising.
    """

    try:
        from ...discord.engineering_team_runtime import (
            set_role_runner_dispatch,
        )
    except Exception as exc:  # noqa: BLE001
        if on_install_failure is not None:
            try:
                on_install_failure(exc)
            except Exception:  # noqa: BLE001
                pass
        else:
            logger.warning(
                "role-runner bootstrap: engineering runtime import failed; "
                "skipping set_role_runner_dispatch",
                exc_info=True,
            )
        return None

    try:
        dispatch, trace = build_role_runner_dispatch_from_env(
            env=env, audit_writer=audit_writer
        )
        set_role_runner_dispatch(dispatch)
    except Exception as exc:  # noqa: BLE001
        # Failure here must not bring the gateway down; the
        # in-process deterministic body keeps the role-runner contract
        # satisfied even when the dispatcher is missing.
        if on_install_failure is not None:
            try:
                on_install_failure(exc)
            except Exception:  # noqa: BLE001
                pass
        else:
            logger.warning(
                "role-runner bootstrap: dispatch install raised; "
                "gateway continues with deterministic in-process body",
                exc_info=True,
            )
        return None
    return trace


__all__ = (
    "ENV_OLLAMA_ENDPOINT",
    "ENV_PROVIDERS",
    "REASON_AVAILABILITY_RAISED",
    "REASON_CLI_NOT_FOUND",
    "REASON_CONSTRUCTOR_RAISED",
    "REASON_ENDPOINT_UNREACHABLE",
    "REASON_NOT_OPTED_IN",
    "REASON_OPTED_IN_AVAILABLE",
    "REASON_OPTED_IN_UNAVAILABLE",
    "REASON_UNKNOWN_PROVIDER",
    "RunnerWiringEntry",
    "RunnerWiringTrace",
    "build_role_runner_candidates",
    "build_role_runner_dispatch_from_env",
    "install_engineering_role_runner_dispatch",
)
