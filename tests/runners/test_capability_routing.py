"""Capability-aware backend routing (provider-capability-matrix.md §5)."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.runners.capability_routing import (
    build_capability_provider_router,
    capability_from_input,
    capability_routing_enabled,
    order_providers,
)
from yule_engineering.agents.runners.role_runner import (
    PROVIDER_DETERMINISTIC,
    RoleRunner,
    RoleRunnerInput,
    RoleRunnerOutput,
    STATUS_OK,
    build_role_runner_dispatcher,
)


@dataclass
class _Session:
    session_id: str = "s1"
    extra: dict = field(default_factory=dict)


def _input(capability=None, task_type=None):
    md = {}
    if capability:
        md["capability_class"] = capability
    if task_type:
        md["task_type"] = task_type
    return RoleRunnerInput(role="qa-engineer", session_id="s1", prompt="p", metadata=md)


class OrderProvidersTests(unittest.TestCase):
    def test_classification_prefers_ollama(self) -> None:
        out = order_providers("classification", ["claude", "codex", "ollama", "deterministic"])
        self.assertEqual(out[0], "ollama")
        self.assertEqual(out[-1], "deterministic")  # terminal pinned last

    def test_execution_prefers_codex(self) -> None:
        self.assertEqual(order_providers("execution", ["claude", "codex", "ollama"])[0], "codex")

    def test_security_prefers_claude(self) -> None:
        self.assertEqual(order_providers("security_gate", ["codex", "ollama", "claude"])[0], "claude")

    def test_unknown_capability_is_noop(self) -> None:
        avail = ["claude", "codex", "ollama"]
        self.assertEqual(order_providers("wat", avail), avail)
        self.assertEqual(order_providers(None, avail), avail)

    def test_lossless(self) -> None:
        avail = ["claude", "codex", "ollama", "deterministic"]
        out = order_providers("research", avail)
        self.assertEqual(sorted(out), sorted(avail))

    def test_preference_skips_absent_providers(self) -> None:
        # research prefers gemini→claude; gemini absent → claude first
        self.assertEqual(order_providers("research", ["codex", "claude"])[0], "claude")


class CapabilityInferenceTests(unittest.TestCase):
    def test_explicit_capability(self) -> None:
        self.assertEqual(capability_from_input(_input(capability="research")), "research")

    def test_task_type_inference(self) -> None:
        self.assertEqual(capability_from_input(_input(task_type="coding")), "execution")
        self.assertEqual(capability_from_input(_input(task_type="summarize")), "summarization")

    def test_none_when_absent(self) -> None:
        self.assertIsNone(capability_from_input(_input()))

    def test_flag_default_off(self) -> None:
        self.assertFalse(capability_routing_enabled({}))
        self.assertTrue(capability_routing_enabled({"YULE_CAPABILITY_ROUTING_ENABLED": "true"}))


class _Scripted(RoleRunner):
    def __init__(self, provider: str, log: list) -> None:
        self.provider = provider
        self._log = log

    def is_available(self) -> bool:
        return True

    def generate(self, input_: RoleRunnerInput) -> RoleRunnerOutput:
        self._log.append(self.provider)
        return RoleRunnerOutput(provider=self.provider, status=STATUS_OK, text="t")


class DispatcherRoutingTests(unittest.TestCase):
    def _runners(self, log):
        return [_Scripted("claude", log), _Scripted("codex", log), _Scripted("ollama", log)]

    def test_router_reorders_first_contacted(self) -> None:
        log: list = []
        dispatch = build_role_runner_dispatcher(
            candidates=self._runners(log),
            provider_router=build_capability_provider_router(),
        )
        out = dispatch(_Session(), _input(capability="classification"))
        self.assertEqual(out.provider, "ollama")  # routed first, returns ok
        self.assertEqual(log[0], "ollama")

    def test_no_router_keeps_priority_order(self) -> None:
        log: list = []
        dispatch = build_role_runner_dispatcher(candidates=self._runners(log))
        dispatch(_Session(), _input(capability="classification"))
        self.assertEqual(log[0], "claude")  # original order

    def test_no_capability_keeps_order(self) -> None:
        log: list = []
        dispatch = build_role_runner_dispatcher(
            candidates=self._runners(log),
            provider_router=build_capability_provider_router(),
        )
        dispatch(_Session(), _input())  # no capability declared
        self.assertEqual(log[0], "claude")

    def test_buggy_router_degrades_gracefully(self) -> None:
        log: list = []

        def _boom(inp, avail):
            raise RuntimeError("router bug")

        dispatch = build_role_runner_dispatcher(
            candidates=self._runners(log), provider_router=_boom
        )
        out = dispatch(_Session(), _input(capability="classification"))
        self.assertEqual(out.status, STATUS_OK)
        self.assertEqual(log[0], "claude")  # fell back to default order


if __name__ == "__main__":
    unittest.main()
