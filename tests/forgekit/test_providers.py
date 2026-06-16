"""forgekit provider layer — contract validation, built-ins, enterprise seam."""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.providers import builtins, registry
from forgekit_console.providers.contract import (
    AUTH_API_KEY,
    AUTH_ENDPOINT,
    AUTH_NONE,
    AUTH_OAUTH,
    CAP_EXECUTION,
    CAP_LOCAL,
    CAP_RESEARCH,
    CAP_SYNTHESIS,
    HEALTH_CLI_PRESENT,
    HEALTH_ENDPOINT_REACHABLE,
    KIND_CLOUD_CLI,
    KIND_ENTERPRISE,
    KIND_LOCAL,
    SUBMIT_CLI,
    SUBMIT_CUSTOM_HTTP,
    USAGE_API,
    USAGE_ENTERPRISE,
    USAGE_LOCAL,
    USAGE_SUBSCRIPTION,
    ProviderSpec,
    validate_provider_spec,
)


class ContractTests(unittest.TestCase):
    def test_good_spec_validates(self) -> None:
        spec = ProviderSpec(
            id="x", label="X", kind=KIND_CLOUD_CLI, auth_kind=AUTH_OAUTH,
            usage_mode=USAGE_SUBSCRIPTION, submit_compat=SUBMIT_CLI,
            health_contract=HEALTH_CLI_PRESENT, capability_flags=(CAP_SYNTHESIS,),
        )
        self.assertEqual(validate_provider_spec(spec), ())

    def test_unknown_enum_fields_flagged(self) -> None:
        spec = ProviderSpec(
            id="x", label="X", kind="bogus", auth_kind="nope",
            usage_mode="weird", submit_compat="??", health_contract="??",
        )
        errors = validate_provider_spec(spec)
        self.assertTrue(any("kind" in e for e in errors))
        self.assertTrue(any("auth_kind" in e for e in errors))
        self.assertTrue(any("usage_mode" in e for e in errors))

    def test_local_requires_endpoint(self) -> None:
        spec = ProviderSpec(
            id="loc", label="Loc", kind=KIND_LOCAL, auth_kind=AUTH_NONE,
            usage_mode=USAGE_LOCAL, submit_compat=SUBMIT_CLI,
            health_contract=HEALTH_ENDPOINT_REACHABLE,
        )
        errors = validate_provider_spec(spec)
        self.assertTrue(any("endpoint" in e for e in errors))

    def test_none_auth_only_local(self) -> None:
        spec = ProviderSpec(
            id="c", label="C", kind=KIND_CLOUD_CLI, auth_kind=AUTH_NONE,
            usage_mode=USAGE_SUBSCRIPTION, submit_compat=SUBMIT_CLI,
            health_contract=HEALTH_CLI_PRESENT,
        )
        errors = validate_provider_spec(spec)
        self.assertTrue(any("none" in e for e in errors))

    def test_usage_kind_coherence(self) -> None:
        spec = ProviderSpec(
            id="c", label="C", kind=KIND_CLOUD_CLI, auth_kind=AUTH_OAUTH,
            usage_mode=USAGE_LOCAL, submit_compat=SUBMIT_CLI,
            health_contract=HEALTH_CLI_PRESENT,
        )
        errors = validate_provider_spec(spec)
        self.assertTrue(any("맞지 않" in e for e in errors))

    def test_empty_id_label_flagged(self) -> None:
        spec = ProviderSpec(
            id="", label="", kind=KIND_CLOUD_CLI, auth_kind=AUTH_OAUTH,
            usage_mode=USAGE_SUBSCRIPTION, submit_compat=SUBMIT_CLI,
            health_contract=HEALTH_CLI_PRESENT,
        )
        errors = validate_provider_spec(spec)
        self.assertTrue(any("id" in e for e in errors))
        self.assertTrue(any("label" in e for e in errors))


class BuiltinTests(unittest.TestCase):
    def test_all_builtins_valid(self) -> None:
        for pid in builtins.BUILTIN_IDS:
            spec = builtins.BUILTIN_PROVIDERS[pid]
            self.assertEqual(validate_provider_spec(spec), (), f"{pid} invalid")

    def test_expected_four_builtins(self) -> None:
        self.assertEqual(set(builtins.BUILTIN_IDS), {"claude", "codex", "gemini", "ollama"})

    def test_capability_lean_per_provider(self) -> None:
        self.assertTrue(builtins.CLAUDE.has_capability(CAP_SYNTHESIS))
        self.assertTrue(builtins.CODEX.has_capability(CAP_EXECUTION))
        self.assertTrue(builtins.GEMINI.has_capability(CAP_RESEARCH))
        self.assertTrue(builtins.OLLAMA.has_capability(CAP_LOCAL))

    def test_ollama_local_no_auth_endpoint(self) -> None:
        self.assertEqual(builtins.OLLAMA.kind, KIND_LOCAL)
        self.assertEqual(builtins.OLLAMA.auth_kind, AUTH_NONE)
        self.assertEqual(builtins.OLLAMA.usage_mode, USAGE_LOCAL)
        self.assertTrue(builtins.OLLAMA.endpoint)

    def test_builtin_lookup(self) -> None:
        self.assertIs(builtins.builtin("claude"), builtins.CLAUDE)
        self.assertIsNone(builtins.builtin("nope"))


class RegistrySeamTests(unittest.TestCase):
    def test_build_from_builtin_id(self) -> None:
        spec = registry.build_provider({"id": "codex"})
        self.assertIs(spec, builtins.CODEX)

    def test_openai_compatible_config(self) -> None:
        cfg = {
            "id": "vllm", "label": "Local vLLM", "shape": "openai-compatible",
            "endpoint": "http://gpu:8000/v1", "capability_flags": ["chat", "cheap"],
        }
        self.assertEqual(registry.validate_config(cfg), ())
        spec = registry.build_provider(cfg)
        self.assertEqual(spec.submit_compat, "openai_compatible")
        self.assertEqual(spec.auth_kind, AUTH_API_KEY)

    def test_custom_http_config(self) -> None:
        cfg = {
            "id": "acme", "label": "Acme HTTP", "shape": "custom-http",
            "endpoint": "https://llm.acme.internal", "capability_flags": ["chat"],
        }
        self.assertEqual(registry.validate_config(cfg), ())
        spec = registry.build_provider(cfg)
        self.assertEqual(spec.kind, KIND_ENTERPRISE)
        self.assertEqual(spec.submit_compat, SUBMIT_CUSTOM_HTTP)
        self.assertTrue(spec.enterprise)

    def test_internal_enterprise_config(self) -> None:
        cfg = {
            "id": "acme-gw", "label": "Acme Gateway", "shape": "internal-enterprise",
            "endpoint": "https://gw.acme.internal/v1", "auth_kind": "endpoint",
            "capability_flags": ["chat", "execution"],
        }
        self.assertEqual(registry.validate_config(cfg), ())
        spec = registry.build_provider(cfg)
        self.assertEqual(spec.kind, KIND_ENTERPRISE)
        self.assertEqual(spec.auth_kind, AUTH_ENDPOINT)
        self.assertEqual(spec.usage_mode, USAGE_ENTERPRISE)

    def test_enterprise_missing_endpoint_rejected(self) -> None:
        cfg = {"id": "acme", "label": "Acme", "shape": "internal-enterprise"}
        errors = registry.validate_config(cfg)
        self.assertTrue(any("endpoint" in e for e in errors))
        with self.assertRaises(registry.ProviderConfigError):
            registry.build_provider(cfg)

    def test_unknown_shape_rejected(self) -> None:
        cfg = {"id": "x", "label": "X", "shape": "telepathy", "endpoint": "x"}
        errors = registry.validate_config(cfg)
        self.assertTrue(any("shape" in e for e in errors))

    def test_missing_id_rejected(self) -> None:
        errors = registry.validate_config({"label": "X", "shape": "custom-http", "endpoint": "x"})
        self.assertTrue(any("id" in e for e in errors))

    def test_no_provider_configured_signal(self) -> None:
        self.assertTrue(registry.no_provider_configured(None))
        self.assertTrue(registry.no_provider_configured({}))
        self.assertTrue(registry.no_provider_configured({"brain": "x"}))
        self.assertFalse(registry.no_provider_configured({"main_provider": "claude"}))
        self.assertFalse(registry.no_provider_configured({"providers": [{"id": "codex"}]}))


if __name__ == "__main__":
    unittest.main()
