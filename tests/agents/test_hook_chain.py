"""HookChain dispatch tests (F11.1 / #107)."""

from __future__ import annotations

import types
import unittest
from typing import Mapping

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.extension.hook_chain import (
    HookLevel,
    HookResult,
    invoke_hook,
)
from yule_orchestrator.agents.extension.manifest import HookEvent, PluginManifest
from yule_orchestrator.agents.extension.plugin_registry import PluginRegistry


def _manifest(
    plugin_id: str,
    *,
    hooks=("OUTBOUND_LLM",),
    risk_class: str = "LOW",
    module_path: str = "tests.fake.module",
) -> PluginManifest:
    return PluginManifest(
        id=plugin_id,
        name=plugin_id.title(),
        version="0.1.0",
        kind="guard",
        hooks_provided=tuple(hooks),
        hooks_consumed=(),
        env_keys=(),
        autonomy_level="advisory",
        paste_guard_required=False,
        risk_class=risk_class,
        module_path=module_path,
    )


def _module_with(**attrs) -> types.SimpleNamespace:
    return types.SimpleNamespace(**attrs)


def _loader_for(modules: dict) -> "callable":
    """Build a deterministic module_loader from plugin_id -> module."""

    def loader(manifest: PluginManifest):
        if manifest.id not in modules:
            raise ImportError(f"no fake module for {manifest.id}")
        return modules[manifest.id]

    return loader


class HookChainTests(unittest.TestCase):
    def test_no_providers_returns_ok_with_original_payload(self) -> None:
        reg = PluginRegistry()
        payload = {"prompt": "hello"}

        result = invoke_hook(
            HookEvent.OUTBOUND_LLM,
            payload,
            plugin_registry=reg,
            module_loader=_loader_for({}),
        )

        self.assertIs(result.level, HookLevel.OK)
        self.assertEqual(result.modified_payload, payload)
        self.assertEqual(result.plugin_id, "")

    def test_chain_runs_providers_in_deterministic_order(self) -> None:
        reg = PluginRegistry()
        # Register in reverse id order to prove sort happens.
        reg.register(_manifest("zeta"))
        reg.register(_manifest("alpha"))

        order: list[str] = []

        def make_handler(plugin_id: str):
            def handler(payload: Mapping):
                order.append(plugin_id)
                return HookResult(level=HookLevel.OK, plugin_id=plugin_id)

            return handler

        modules = {
            "alpha": _module_with(on_outbound_llm=make_handler("alpha")),
            "zeta": _module_with(on_outbound_llm=make_handler("zeta")),
        }

        invoke_hook(
            HookEvent.OUTBOUND_LLM,
            {"x": 1},
            plugin_registry=reg,
            module_loader=_loader_for(modules),
        )

        self.assertEqual(order, ["alpha", "zeta"])

    def test_payload_propagates_through_chain(self) -> None:
        reg = PluginRegistry()
        reg.register(_manifest("a"))
        reg.register(_manifest("b"))

        def handler_a(payload: Mapping):
            return HookResult(
                level=HookLevel.OK,
                modified_payload={**payload, "via_a": True},
            )

        def handler_b(payload: Mapping):
            # b must see the modification a produced.
            assert payload.get("via_a") is True
            return HookResult(
                level=HookLevel.OK,
                modified_payload={**payload, "via_b": True},
            )

        modules = {
            "a": _module_with(on_outbound_llm=handler_a),
            "b": _module_with(on_outbound_llm=handler_b),
        }

        result = invoke_hook(
            HookEvent.OUTBOUND_LLM,
            {"start": True},
            plugin_registry=reg,
            module_loader=_loader_for(modules),
        )

        self.assertIs(result.level, HookLevel.OK)
        self.assertEqual(
            result.modified_payload,
            {"start": True, "via_a": True, "via_b": True},
        )

    def test_block_short_circuits_chain(self) -> None:
        reg = PluginRegistry()
        reg.register(_manifest("blocker"))
        reg.register(_manifest("never-runs"))

        call_log: list[str] = []

        def blocker(payload: Mapping):
            call_log.append("blocker")
            return HookResult(
                level=HookLevel.BLOCK,
                modified_payload=payload,
                blocker_reason="secret detected",
                signature="paste_guard.secret_detected",
            )

        def never_runs(payload: Mapping):
            call_log.append("never-runs")
            return HookResult(level=HookLevel.OK)

        modules = {
            "blocker": _module_with(on_outbound_llm=blocker),
            "never-runs": _module_with(on_outbound_llm=never_runs),
        }

        result = invoke_hook(
            HookEvent.OUTBOUND_LLM,
            {"prompt": "x"},
            plugin_registry=reg,
            module_loader=_loader_for(modules),
        )

        self.assertEqual(call_log, ["blocker"])
        self.assertIs(result.level, HookLevel.BLOCK)
        self.assertEqual(result.plugin_id, "blocker")
        self.assertEqual(result.signature, "paste_guard.secret_detected")

    def test_handler_exception_becomes_error_result(self) -> None:
        reg = PluginRegistry()
        reg.register(_manifest("broken"))
        reg.register(_manifest("never-runs"))

        def broken(payload: Mapping):
            raise RuntimeError("kaboom")

        def never_runs(payload: Mapping):
            self.fail("chain must stop after ERROR")

        modules = {
            "broken": _module_with(on_outbound_llm=broken),
            "never-runs": _module_with(on_outbound_llm=never_runs),
        }

        result = invoke_hook(
            HookEvent.OUTBOUND_LLM,
            {"x": 1},
            plugin_registry=reg,
            module_loader=_loader_for(modules),
        )

        self.assertIs(result.level, HookLevel.ERROR)
        self.assertEqual(result.plugin_id, "broken")
        self.assertIn("kaboom", result.blocker_reason)
        self.assertEqual(result.signature, "hook_chain.handler.exception")

    def test_high_risk_plugin_skipped_without_allow_list(self) -> None:
        reg = PluginRegistry()
        reg.register(_manifest("paste-guard", risk_class="HIGH"))

        called: list[str] = []

        def handler(payload: Mapping):
            called.append("paste-guard")
            return HookResult(level=HookLevel.OK)

        modules = {"paste-guard": _module_with(on_outbound_llm=handler)}

        result = invoke_hook(
            HookEvent.OUTBOUND_LLM,
            {"x": 1},
            plugin_registry=reg,
            module_loader=_loader_for(modules),
        )

        self.assertEqual(called, [])
        self.assertIs(result.level, HookLevel.SKIP)
        self.assertEqual(result.plugin_id, "paste-guard")
        self.assertEqual(result.signature, "hook_chain.skip.high_risk_not_allowed")

    def test_high_risk_runs_when_explicitly_allowed(self) -> None:
        reg = PluginRegistry()
        reg.register(_manifest("paste-guard", risk_class="HIGH"))

        called: list[str] = []

        def handler(payload: Mapping):
            called.append("paste-guard")
            return HookResult(level=HookLevel.OK, modified_payload={"sanitised": True})

        modules = {"paste-guard": _module_with(on_outbound_llm=handler)}

        result = invoke_hook(
            HookEvent.OUTBOUND_LLM,
            {"x": 1},
            plugin_registry=reg,
            module_loader=_loader_for(modules),
            allow_high_risk=("paste-guard",),
        )

        self.assertEqual(called, ["paste-guard"])
        self.assertIs(result.level, HookLevel.OK)
        self.assertEqual(result.modified_payload, {"sanitised": True})

    def test_handler_returning_mapping_is_treated_as_modified_payload(self) -> None:
        reg = PluginRegistry()
        reg.register(_manifest("rewriter"))

        def handler(payload: Mapping):
            return {**payload, "augmented": True}

        modules = {"rewriter": _module_with(on_outbound_llm=handler)}

        result = invoke_hook(
            HookEvent.OUTBOUND_LLM,
            {"x": 1},
            plugin_registry=reg,
            module_loader=_loader_for(modules),
        )

        self.assertIs(result.level, HookLevel.OK)
        self.assertEqual(result.modified_payload, {"x": 1, "augmented": True})

    def test_missing_handler_results_in_skip(self) -> None:
        # Order: id-sorted -> "alpha-real" before "zeta-missing", so the
        # working plugin runs first and the misconfigured one becomes the
        # terminal SKIP result. The hook chain must surface that SKIP so
        # operators see the broken plugin instead of an apparent OK.
        reg = PluginRegistry()
        reg.register(_manifest("alpha-real"))
        reg.register(_manifest("zeta-missing"))

        called: list[str] = []

        def real_handler(payload: Mapping):
            called.append("alpha-real")
            return HookResult(level=HookLevel.OK)

        modules = {
            "alpha-real": _module_with(on_outbound_llm=real_handler),
            "zeta-missing": _module_with(),  # no handler attrs at all
        }

        result = invoke_hook(
            HookEvent.OUTBOUND_LLM,
            {"x": 1},
            plugin_registry=reg,
            module_loader=_loader_for(modules),
        )

        # Both plugins were visited (alpha-real ran, zeta-missing skipped).
        self.assertEqual(called, ["alpha-real"])
        self.assertIs(result.level, HookLevel.SKIP)
        self.assertEqual(result.plugin_id, "zeta-missing")
        self.assertEqual(result.signature, "hook_chain.handler.missing")

    def test_module_loader_import_failure_returns_error(self) -> None:
        reg = PluginRegistry()
        reg.register(_manifest("missing"))

        def loader(manifest: PluginManifest):
            raise ImportError("nope")

        result = invoke_hook(
            HookEvent.OUTBOUND_LLM,
            {"x": 1},
            plugin_registry=reg,
            module_loader=loader,
        )

        self.assertIs(result.level, HookLevel.ERROR)
        self.assertEqual(result.plugin_id, "missing")
        self.assertEqual(result.signature, "hook_chain.module.load_failed")

    def test_hook_handlers_mapping_is_supported(self) -> None:
        reg = PluginRegistry()
        reg.register(_manifest("mapper"))

        def handler(payload: Mapping):
            return HookResult(level=HookLevel.OK, modified_payload={"via_mapping": True})

        modules = {
            "mapper": _module_with(HOOK_HANDLERS={HookEvent.OUTBOUND_LLM: handler}),
        }

        result = invoke_hook(
            HookEvent.OUTBOUND_LLM,
            {"x": 1},
            plugin_registry=reg,
            module_loader=_loader_for(modules),
        )

        self.assertEqual(result.modified_payload, {"via_mapping": True})

    def test_invoke_hook_rejects_non_enum_event(self) -> None:
        reg = PluginRegistry()
        with self.assertRaises(TypeError):
            invoke_hook("OUTBOUND_LLM", {}, plugin_registry=reg)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
