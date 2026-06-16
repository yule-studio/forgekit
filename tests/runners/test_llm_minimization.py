"""Rule-first LLM minimization policy + resolution-aware routing (Phase A/B)."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.harness.llm_minimization import (
    RESOLUTION_LLM_OPTIONAL,
    RESOLUTION_LLM_REQUIRED,
    RESOLUTION_RULE_FIRST,
    resolve,
    resolve_from_metadata,
)
from yule_engineering.agents.runners.capability_routing import (
    build_resolution_provider_router,
    llm_minimization_enabled,
    order_providers_for_resolution,
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


def _input(capability=None, mode=None, llm_allowed=None, task_type=None):
    md = {}
    if capability:
        md["capability_class"] = capability
    if mode:
        md["resolution_mode"] = mode
    if llm_allowed is not None:
        md["llm_allowed"] = llm_allowed
    if task_type:
        md["task_type"] = task_type
    return RoleRunnerInput(role="qa-engineer", session_id="s1", prompt="p", metadata=md)


class PolicyTests(unittest.TestCase):
    def test_rule_first_capabilities(self) -> None:
        for cc in ("classification", "enforcement", "security_gate", "verification", "memory"):
            d = resolve(cc)
            self.assertEqual(d.resolution_mode, RESOLUTION_RULE_FIRST, cc)
            self.assertFalse(d.llm_allowed)

    def test_optional_and_required(self) -> None:
        self.assertEqual(resolve("summarization").resolution_mode, RESOLUTION_LLM_OPTIONAL)
        self.assertTrue(resolve("summarization").llm_allowed)
        self.assertEqual(resolve("research").resolution_mode, RESOLUTION_LLM_REQUIRED)

    def test_unknown_defaults_to_required(self) -> None:
        d = resolve("totally-unknown")
        self.assertEqual(d.resolution_mode, RESOLUTION_LLM_REQUIRED)
        self.assertTrue(d.llm_allowed)
        self.assertIn("default", d.why)

    def test_explicit_override_wins(self) -> None:
        # classification is rule_first, but explicit llm_required overrides
        d = resolve_from_metadata({"capability_class": "classification", "resolution_mode": "llm_required"})
        self.assertEqual(d.resolution_mode, RESOLUTION_LLM_REQUIRED)
        self.assertIn("explicit", d.why)

    def test_explicit_llm_allowed_override(self) -> None:
        d = resolve_from_metadata({"capability_class": "classification", "llm_allowed": True})
        self.assertTrue(d.llm_allowed)  # override even though rule_first


class OrderTests(unittest.TestCase):
    def test_rule_first_pins_deterministic_first(self) -> None:
        out = order_providers_for_resolution(
            "classification", ["claude", "ollama", "deterministic"], resolution_mode="rule_first"
        )
        self.assertEqual(out[0], PROVIDER_DETERMINISTIC)

    def test_required_keeps_deterministic_last(self) -> None:
        out = order_providers_for_resolution(
            "research", ["claude", "codex", "deterministic"], resolution_mode="llm_required"
        )
        self.assertEqual(out[-1], PROVIDER_DETERMINISTIC)

    def test_flag_default_off(self) -> None:
        self.assertFalse(llm_minimization_enabled({}))
        self.assertTrue(llm_minimization_enabled({"YULE_LLM_MINIMIZATION_ENABLED": "true"}))


class _Scripted(RoleRunner):
    def __init__(self, provider, log):
        self.provider = provider
        self._log = log

    def is_available(self):
        return True

    def generate(self, input_):
        self._log.append(self.provider)
        return RoleRunnerOutput(provider=self.provider, status=STATUS_OK, text="t")


class RouterDispatchTests(unittest.TestCase):
    def _runners(self, log):
        # include a deterministic terminal so rule_first can pick it
        from yule_engineering.agents.runners.role_runner import DeterministicRoleRunner

        return [_Scripted("claude", log), _Scripted("ollama", log), DeterministicRoleRunner()]

    def test_rule_first_skips_live(self) -> None:
        log = []
        dispatch = build_role_runner_dispatcher(
            candidates=self._runners(log),
            provider_router=build_resolution_provider_router(),
        )
        out = dispatch(_Session(), _input(capability="classification"))
        self.assertEqual(out.provider, PROVIDER_DETERMINISTIC)  # rule path won
        self.assertEqual(log, [])  # no live provider contacted

    def test_llm_required_uses_live(self) -> None:
        log = []
        dispatch = build_role_runner_dispatcher(
            candidates=self._runners(log),
            provider_router=build_resolution_provider_router(),
        )
        out = dispatch(_Session(), _input(capability="research"))
        # research → gemini(absent)→claude first among live; claude wins
        self.assertEqual(out.provider, "claude")
        self.assertEqual(log[0], "claude")

    def test_explicit_override_forces_live(self) -> None:
        log = []
        dispatch = build_role_runner_dispatcher(
            candidates=self._runners(log),
            provider_router=build_resolution_provider_router(),
        )
        out = dispatch(_Session(), _input(capability="classification", mode="llm_required"))
        self.assertNotEqual(out.provider, PROVIDER_DETERMINISTIC)  # override → live


if __name__ == "__main__":
    unittest.main()
