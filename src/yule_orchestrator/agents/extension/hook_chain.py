"""HookChain invocation runtime (F11.1 / #107).

The :class:`PluginRegistry` only stores manifests. This module turns
that static description into an actual *dispatch*: given a
:class:`~yule_orchestrator.agents.extension.manifest.HookEvent` and a
payload, walk every plugin that *provides* the hook in deterministic
order and let each transform / approve / block the payload.

Design rails (regression-tested):

  * The chain is deterministic — providers are visited in the order the
    :class:`PluginRegistry` exposes them (id ascending). The runtime
    must not depend on registration order.
  * Each plugin handler receives the *most recent* payload — the
    output of the previous plugin's ``modified_payload`` (when present)
    is the input of the next plugin. This makes the chain composable
    without any plugin needing to know about its peers.
  * ``BLOCK`` short-circuits the chain: once any plugin emits a
    :class:`HookResult` with level ``BLOCK``, no subsequent plugin is
    called and that result is returned verbatim.
  * Handlers may raise. Exceptions are converted into a synthetic
    ``ERROR`` :class:`HookResult` so the caller surfaces a structured
    failure (and the mistake ledger can pick it up) instead of seeing a
    bare traceback. The chain stops on ``ERROR`` as well — a misbehaving
    plugin must not silently let later plugins observe a half-mutated
    payload.
  * Plugin modules are *never* imported by this module directly. The
    caller supplies a ``module_loader`` (defaulting to
    :func:`~yule_orchestrator.agents.extension.loader.load_plugin_module`)
    so tests can fully isolate the chain from filesystem state.

Hard rails:

  * HIGH risk plugins are *not* auto-activated. If a registered manifest
    has ``risk_class == "HIGH"`` and the caller did not pass it in
    ``allow_high_risk`` (kwarg), the chain skips it and records an
    advisory entry in the returned trail.
  * Unknown / non-callable handler attributes are skipped with a warning
    via :mod:`logging`; they do not break the chain.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional, Tuple

from .manifest import HookEvent, PluginManifest
from .plugin_registry import PluginRegistry


_LOGGER = logging.getLogger(__name__)


class HookLevel(enum.Enum):
    """Severity / continuation signal returned by a plugin handler."""

    OK = "ok"          # plugin ran cleanly, chain continues
    WARN = "warn"      # plugin flagged something but chain continues
    BLOCK = "block"    # plugin refused; chain stops immediately
    SKIP = "skip"      # plugin opted out (e.g. autonomy mismatch)
    ERROR = "error"    # plugin raised; chain stops, surfaces failure


@dataclass(frozen=True)
class HookResult:
    """Outcome of a single plugin invocation in the chain.

    Fields:
      level: :class:`HookLevel` continuation signal.
      modified_payload: payload to forward to the next plugin. ``None``
        means "no modification" — the previous payload propagates as-is.
      blocker_reason: human-readable reason when ``level`` is ``BLOCK``
        or ``ERROR``; empty string otherwise.
      plugin_id: id of the plugin that produced the result. The synthetic
        terminal result emitted when the chain has no providers carries
        the sentinel ``""``.
      signature: mistake-ledger-style signature (e.g.
        ``"paste_guard.secret_detected"``). Empty for OK/SKIP results.
    """

    level: HookLevel
    modified_payload: Optional[Mapping[str, Any]] = None
    blocker_reason: str = ""
    plugin_id: str = ""
    signature: str = ""


#: Signature of a plugin's hook handler. Handlers receive the current
#: payload and return a :class:`HookResult`. They must not mutate the
#: input mapping.
HookHandler = Callable[[Mapping[str, Any]], HookResult]


#: Signature of a module loader (matches
#: :func:`yule_orchestrator.agents.extension.loader.load_plugin_module`).
ModuleLoader = Callable[[PluginManifest], Any]


def _resolve_handler(
    module: Any,
    hook: HookEvent,
) -> Optional[HookHandler]:
    """Pick the hook handler off a loaded plugin module.

    A plugin module may expose either:

      * ``HOOK_HANDLERS`` mapping ``HookEvent`` -> callable, or
      * a callable attribute named ``on_<lower-hook-name>`` (e.g.
        ``on_outbound_llm``).

    Returns ``None`` if no matching handler is found; the caller treats
    that as a skip.
    """

    handlers = getattr(module, "HOOK_HANDLERS", None)
    if isinstance(handlers, Mapping):
        candidate = handlers.get(hook) or handlers.get(hook.name)
        if callable(candidate):
            return candidate

    attr_name = f"on_{hook.name.lower()}"
    candidate = getattr(module, attr_name, None)
    if callable(candidate):
        return candidate
    return None


def _coerce_result(
    raw: Any,
    *,
    plugin_id: str,
    fallback_payload: Mapping[str, Any],
) -> HookResult:
    """Normalise a handler's return value into a :class:`HookResult`."""

    if isinstance(raw, HookResult):
        # Always stamp the plugin id even if the handler omitted it; the
        # chain is the source of truth for that field.
        if raw.plugin_id == plugin_id:
            return raw
        return HookResult(
            level=raw.level,
            modified_payload=raw.modified_payload,
            blocker_reason=raw.blocker_reason,
            plugin_id=plugin_id,
            signature=raw.signature,
        )
    if raw is None:
        return HookResult(level=HookLevel.OK, plugin_id=plugin_id)
    if isinstance(raw, Mapping):
        # Convenience: treat a plain dict return as an OK with a
        # modified payload (tests often go this route).
        return HookResult(
            level=HookLevel.OK,
            modified_payload=raw,
            plugin_id=plugin_id,
        )
    # Anything else is a contract violation; surface as ERROR rather than
    # silently dropping it.
    return HookResult(
        level=HookLevel.ERROR,
        modified_payload=fallback_payload,
        blocker_reason=f"plugin '{plugin_id}' returned unsupported handler value: {type(raw).__name__}",
        plugin_id=plugin_id,
        signature="hook_chain.handler.bad_return",
    )


def invoke_hook(
    event: HookEvent,
    payload: Mapping[str, Any],
    *,
    plugin_registry: PluginRegistry,
    module_loader: Optional[ModuleLoader] = None,
    allow_high_risk: Iterable[str] = (),
) -> HookResult:
    """Run every registered provider for ``event`` in deterministic order.

    Args:
      event: the :class:`HookEvent` to dispatch.
      payload: initial payload. Treated as immutable; plugins receive
        each successive ``modified_payload`` instead of mutating in place.
      plugin_registry: registry to consult for providers.
      module_loader: callable that returns a plugin's module given its
        manifest. Defaults to
        :func:`yule_orchestrator.agents.extension.loader.load_plugin_module`.
        Tests pass a fake so no real import is performed.
      allow_high_risk: iterable of plugin ids the caller has explicitly
        authorised to run despite ``risk_class == "HIGH"``. HIGH-risk
        plugins outside this allow-list are skipped (hard rail).

    Returns:
      The terminal :class:`HookResult`. When no plugin provides the
      event, returns an ``OK`` result with the original payload and an
      empty ``plugin_id`` so callers can detect the no-op case.
    """

    if not isinstance(event, HookEvent):
        raise TypeError("invoke_hook expects a HookEvent enum member")
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping")

    allow_list = frozenset(allow_high_risk)
    providers = plugin_registry.plugins_for_hook(event)

    if not providers:
        _LOGGER.debug("hook %s has no providers; returning no-op result", event.name)
        return HookResult(level=HookLevel.OK, modified_payload=payload)

    if module_loader is None:
        # Local import keeps the module dependency-free at import time
        # and lets tests that don't touch the filesystem stay cheap.
        from .loader import load_plugin_module as _default_loader

        module_loader = _default_loader

    current_payload: Mapping[str, Any] = payload
    last_result: HookResult = HookResult(level=HookLevel.OK, modified_payload=payload)

    for manifest in providers:
        if manifest.risk_class == "HIGH" and manifest.id not in allow_list:
            _LOGGER.warning(
                "skipping HIGH risk plugin '%s' for hook %s (not in allow_high_risk)",
                manifest.id,
                event.name,
            )
            last_result = HookResult(
                level=HookLevel.SKIP,
                modified_payload=current_payload,
                blocker_reason=f"plugin '{manifest.id}' is HIGH risk and was not explicitly allowed",
                plugin_id=manifest.id,
                signature="hook_chain.skip.high_risk_not_allowed",
            )
            continue

        try:
            module = module_loader(manifest)
        except Exception as exc:  # noqa: BLE001 — any import / load failure halts the chain
            _LOGGER.exception("module_loader failed for plugin '%s'", manifest.id)
            return HookResult(
                level=HookLevel.ERROR,
                modified_payload=current_payload,
                blocker_reason=f"plugin '{manifest.id}' module load failed: {exc}",
                plugin_id=manifest.id,
                signature="hook_chain.module.load_failed",
            )

        handler = _resolve_handler(module, event)
        if handler is None:
            _LOGGER.warning(
                "plugin '%s' declares hook %s but exposes no matching handler; skipping",
                manifest.id,
                event.name,
            )
            last_result = HookResult(
                level=HookLevel.SKIP,
                modified_payload=current_payload,
                blocker_reason=f"plugin '{manifest.id}' has no handler for {event.name}",
                plugin_id=manifest.id,
                signature="hook_chain.handler.missing",
            )
            continue

        try:
            raw = handler(current_payload)
        except Exception as exc:  # noqa: BLE001 — surface handler failure as ERROR
            _LOGGER.exception("handler for plugin '%s' raised", manifest.id)
            return HookResult(
                level=HookLevel.ERROR,
                modified_payload=current_payload,
                blocker_reason=f"plugin '{manifest.id}' handler raised: {exc}",
                plugin_id=manifest.id,
                signature="hook_chain.handler.exception",
            )

        result = _coerce_result(raw, plugin_id=manifest.id, fallback_payload=current_payload)
        last_result = result

        if result.modified_payload is not None:
            current_payload = result.modified_payload

        if result.level is HookLevel.BLOCK:
            return result
        if result.level is HookLevel.ERROR:
            return result

    # Normalise the terminal payload so callers always see a payload on
    # the final result even when the last plugin returned modifications
    # via earlier link.
    if last_result.modified_payload is None:
        last_result = HookResult(
            level=last_result.level,
            modified_payload=current_payload,
            blocker_reason=last_result.blocker_reason,
            plugin_id=last_result.plugin_id,
            signature=last_result.signature,
        )
    return last_result


__all__ = [
    "HookHandler",
    "HookLevel",
    "HookResult",
    "ModuleLoader",
    "invoke_hook",
]
