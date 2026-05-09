"""classifier_factory — Round 2 of #73.

Pins the env contract + JSON parser + provider priority of
:mod:`yule_orchestrator.agents.decision.classifier_factory`.

The hard rails this suite enforces:

  * No classifier auto-enables on key-detection alone — both the
    key/endpoint *and* the matching ``YULE_DECISION_<provider>_ENABLED``
    flag must be set.
  * Anthropic / OpenAI adapters route through the blocked stub
    until operator authorization (D-73-10) lands.
  * Network failures of the live Ollama classifier degrade to
    ``clarification_needed`` rather than blocking the gateway.
  * JSON parser tolerates LLM verbosity — direct JSON, embedded
    JSON, malformed JSON, unknown modes all degrade safely.
"""

from __future__ import annotations

import unittest
from typing import Any, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.decision.classifier_factory import (
    AnthropicClassifier,
    BlockedClassifierError,
    ClassifierResolution,
    ENV_ANTHROPIC_ENABLED,
    ENV_ANTHROPIC_API_KEY,
    ENV_ANTHROPIC_API_KEY_ALT,
    ENV_OLLAMA_ENABLED,
    ENV_OLLAMA_ENDPOINT,
    ENV_OLLAMA_MODEL,
    ENV_OLLAMA_TIMEOUT,
    ENV_OPENAI_API_KEY,
    ENV_OPENAI_ENABLED,
    OllamaClassifier,
    OllamaClassifierConfig,
    OpenAIClassifier,
    build_classifier_from_env,
)
from yule_orchestrator.agents.decision.classifier_factory import (
    _BlockedAdapter,
    _build_classification_prompt,
    _extract_json,
    _is_truthy,
    _parse_classifier_response,
    _safe_int,
)
from yule_orchestrator.agents.decision.router import (
    DecisionRequest,
    MODE_CLARIFICATION_NEEDED,
    MODE_DISCUSSION,
    MODE_IMPLEMENTATION_CANDIDATE,
    MODE_RESEARCH_ONLY,
    SOURCE_CLASSIFIER,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _RecordingHttpPoster:
    """Records the last call + returns a programmable response."""

    def __init__(self, response: Mapping[str, Any]) -> None:
        self.response = response
        self.calls: list = []

    def post_json(
        self,
        *,
        url: str,
        body: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout: int,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "url": url,
                "body": dict(body),
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        return self.response


class _FailingHttpPoster:
    """Raises :class:`BlockedClassifierError` to simulate network failure."""

    def __init__(self, reason: str = "url_error: ConnectionRefused") -> None:
        self.reason = reason

    def post_json(self, **_: Any) -> Mapping[str, Any]:
        raise BlockedClassifierError(self.reason)


# ---------------------------------------------------------------------------
# _is_truthy / _safe_int
# ---------------------------------------------------------------------------


class IsTruthyTests(unittest.TestCase):
    def test_recognised_true_strings(self) -> None:
        for value in ("1", "true", "True", "TRUE", "yes", "YES", "on", " on "):
            with self.subTest(value=value):
                self.assertTrue(_is_truthy(value))

    def test_falsey_strings(self) -> None:
        for value in (None, "", "0", "false", "no", "off", "True false", "maybe"):
            with self.subTest(value=value):
                self.assertFalse(_is_truthy(value))


class SafeIntTests(unittest.TestCase):
    def test_returns_default_for_none(self) -> None:
        self.assertEqual(_safe_int(None, 20), 20)

    def test_returns_default_for_garbage(self) -> None:
        self.assertEqual(_safe_int("abc", 20), 20)

    def test_parses_numeric_string(self) -> None:
        self.assertEqual(_safe_int("45", 20), 45)


# ---------------------------------------------------------------------------
# JSON extraction + parsing
# ---------------------------------------------------------------------------


class ExtractJsonTests(unittest.TestCase):
    def test_direct_json_object_parses(self) -> None:
        result = _extract_json('{"mode": "discussion", "confidence": 0.7}')
        assert result is not None
        self.assertEqual(result["mode"], "discussion")

    def test_embedded_json_in_prose(self) -> None:
        text = (
            "Sure, here is my answer:\n"
            "```json\n"
            '{"mode": "research_only", "confidence": 0.9, "reason": "explicit"}\n'
            "```\n"
            "Hope that helps!"
        )
        result = _extract_json(text)
        assert result is not None
        self.assertEqual(result["mode"], "research_only")

    def test_no_json_returns_none(self) -> None:
        self.assertIsNone(_extract_json("totally unstructured prose"))
        self.assertIsNone(_extract_json(""))

    def test_malformed_json_returns_none(self) -> None:
        self.assertIsNone(_extract_json('{"mode": "discussion", "confidence": '))


class ParseClassifierResponseTests(unittest.TestCase):
    def test_valid_mode_yields_decision_result(self) -> None:
        result = _parse_classifier_response(
            text='{"mode": "discussion", "confidence": 0.82, "reason": "open question"}',
            context_pack_id="ctx-1",
            provider="ollama",
            model="gemma3:latest",
        )
        self.assertEqual(result.mode, MODE_DISCUSSION)
        self.assertAlmostEqual(result.confidence, 0.82)
        self.assertEqual(result.reason, "open question")
        self.assertEqual(result.source, SOURCE_CLASSIFIER)
        self.assertEqual(result.context_pack_id, "ctx-1")

    def test_unknown_mode_falls_back_to_clarification(self) -> None:
        result = _parse_classifier_response(
            text='{"mode": "totally_invented", "confidence": 0.9}',
            context_pack_id=None,
            provider="ollama",
            model="gemma3:latest",
        )
        self.assertEqual(result.mode, MODE_CLARIFICATION_NEEDED)
        self.assertIn("unknown mode", result.reason)
        self.assertIn("totally_invented", result.reason)

    def test_non_json_response_falls_back_to_clarification(self) -> None:
        result = _parse_classifier_response(
            text="No idea what you want",
            context_pack_id=None,
            provider="ollama",
            model="gemma3:latest",
        )
        self.assertEqual(result.mode, MODE_CLARIFICATION_NEEDED)
        self.assertIn("non-JSON", result.reason)

    def test_confidence_is_clamped_to_unit_interval(self) -> None:
        too_high = _parse_classifier_response(
            text='{"mode": "discussion", "confidence": 9.99}',
            context_pack_id=None,
            provider="ollama",
            model="m",
        )
        self.assertEqual(too_high.confidence, 1.0)
        too_low = _parse_classifier_response(
            text='{"mode": "discussion", "confidence": -1.5}',
            context_pack_id=None,
            provider="ollama",
            model="m",
        )
        self.assertEqual(too_low.confidence, 0.0)

    def test_missing_confidence_defaults_to_07(self) -> None:
        result = _parse_classifier_response(
            text='{"mode": "implementation_candidate"}',
            context_pack_id=None,
            provider="ollama",
            model="m",
        )
        self.assertEqual(result.mode, MODE_IMPLEMENTATION_CANDIDATE)
        self.assertEqual(result.confidence, 0.7)

    def test_string_confidence_is_tolerated(self) -> None:
        # malformed but recoverable — string number gets coerced.
        result = _parse_classifier_response(
            text='{"mode": "research_only", "confidence": "0.55"}',
            context_pack_id=None,
            provider="ollama",
            model="m",
        )
        self.assertEqual(result.mode, MODE_RESEARCH_ONLY)
        self.assertAlmostEqual(result.confidence, 0.55)

    def test_garbage_confidence_falls_back_to_07(self) -> None:
        result = _parse_classifier_response(
            text='{"mode": "research_only", "confidence": "not-a-number"}',
            context_pack_id=None,
            provider="ollama",
            model="m",
        )
        self.assertEqual(result.confidence, 0.7)


class BuildClassificationPromptTests(unittest.TestCase):
    def test_prompt_contains_modes_and_user_input(self) -> None:
        prompt = _build_classification_prompt(
            DecisionRequest(prompt="이 버그 고쳐줘")
        )
        self.assertIn("discussion", prompt)
        self.assertIn("research_only", prompt)
        self.assertIn("implementation_candidate", prompt)
        self.assertIn("clarification_needed", prompt)
        self.assertIn("이 버그 고쳐줘", prompt)


# ---------------------------------------------------------------------------
# OllamaClassifier
# ---------------------------------------------------------------------------


class OllamaClassifierTests(unittest.TestCase):
    def _config(self) -> OllamaClassifierConfig:
        return OllamaClassifierConfig(
            endpoint="http://localhost:11434",
            model="gemma3:latest",
            timeout_seconds=20,
        )

    def test_classify_posts_to_generate_endpoint_with_model(self) -> None:
        poster = _RecordingHttpPoster(
            response={"response": '{"mode":"discussion","confidence":0.8}'}
        )
        classifier = OllamaClassifier(config=self._config(), http_poster=poster)
        result = classifier.classify(
            request=DecisionRequest(prompt="open question"),
            context_pack_id="ctx-9",
        )
        self.assertEqual(len(poster.calls), 1)
        call = poster.calls[0]
        self.assertEqual(call["url"], "http://localhost:11434/api/generate")
        self.assertEqual(call["body"]["model"], "gemma3:latest")
        self.assertFalse(call["body"]["stream"])
        self.assertIn("open question", call["body"]["prompt"])
        self.assertEqual(call["timeout"], 20)
        self.assertEqual(result.mode, MODE_DISCUSSION)
        self.assertEqual(result.context_pack_id, "ctx-9")
        self.assertEqual(result.source, SOURCE_CLASSIFIER)

    def test_endpoint_trailing_slash_is_normalised(self) -> None:
        poster = _RecordingHttpPoster(
            response={"response": '{"mode":"discussion","confidence":0.8}'}
        )
        config = OllamaClassifierConfig(
            endpoint="http://localhost:11434/",
            model="m",
            timeout_seconds=5,
        )
        OllamaClassifier(config=config, http_poster=poster).classify(
            request=DecisionRequest(prompt="hi")
        )
        self.assertEqual(
            poster.calls[0]["url"], "http://localhost:11434/api/generate"
        )

    def test_http_failure_falls_back_to_clarification(self) -> None:
        poster = _FailingHttpPoster(reason="url_error: ConnectionRefused")
        classifier = OllamaClassifier(config=self._config(), http_poster=poster)
        result = classifier.classify(
            request=DecisionRequest(prompt="something"),
            context_pack_id="ctx-failed",
        )
        self.assertEqual(result.mode, MODE_CLARIFICATION_NEEDED)
        self.assertEqual(result.source, SOURCE_CLASSIFIER)
        self.assertIn("ollama classifier failed", result.reason)
        self.assertIn("ConnectionRefused", result.reason)
        self.assertEqual(result.context_pack_id, "ctx-failed")

    def test_response_field_fallback_to_raw(self) -> None:
        # When the HTTP poster captured non-JSON, it returns {"_raw": "..."}.
        poster = _RecordingHttpPoster(
            response={"_raw": '{"mode":"research_only","confidence":0.9}'}
        )
        classifier = OllamaClassifier(config=self._config(), http_poster=poster)
        result = classifier.classify(request=DecisionRequest(prompt="fetch refs only"))
        self.assertEqual(result.mode, MODE_RESEARCH_ONLY)


# ---------------------------------------------------------------------------
# Blocked adapters (Anthropic / OpenAI)
# ---------------------------------------------------------------------------


class BlockedAdapterTests(unittest.TestCase):
    def test_anthropic_returns_clarification_with_blocker(self) -> None:
        classifier = AnthropicClassifier(api_key="sk-ant-test")
        result = classifier.classify(
            request=DecisionRequest(prompt="이 버그 고쳐줘"),
            context_pack_id="ctx-7",
        )
        self.assertEqual(result.mode, MODE_CLARIFICATION_NEEDED)
        self.assertEqual(result.source, SOURCE_CLASSIFIER)
        self.assertIn("anthropic classifier blocked", result.reason)
        self.assertIn("operator authorization", result.reason)
        self.assertEqual(result.context_pack_id, "ctx-7")

    def test_openai_returns_clarification_with_blocker(self) -> None:
        classifier = OpenAIClassifier(api_key="sk-openai-test")
        result = classifier.classify(
            request=DecisionRequest(prompt="implement search"),
            context_pack_id=None,
        )
        self.assertEqual(result.mode, MODE_CLARIFICATION_NEEDED)
        self.assertIn("openai classifier blocked", result.reason)

    def test_blocked_adapter_never_emits_api_key(self) -> None:
        # Hard rail: the api_key must not leak into reason/payload.
        classifier = AnthropicClassifier(api_key="sk-ant-supersecret-NEVERLEAK")
        result = classifier.classify(
            request=DecisionRequest(prompt="hi"), context_pack_id=None
        )
        payload = result.to_payload()
        for value in payload.values():
            self.assertNotIn("NEVERLEAK", str(value))


# ---------------------------------------------------------------------------
# build_classifier_from_env
# ---------------------------------------------------------------------------


class BuildClassifierFromEnvTests(unittest.TestCase):
    def test_empty_env_returns_none_provider(self) -> None:
        resolution = build_classifier_from_env(env={})
        self.assertIsNone(resolution.classifier)
        self.assertEqual(resolution.provider, "none")
        self.assertIn("no classifier env detected", resolution.blocked_reason)
        self.assertEqual(resolution.detected_keys, ())

    def test_ollama_endpoint_without_enable_flag_is_blocked(self) -> None:
        resolution = build_classifier_from_env(
            env={ENV_OLLAMA_ENDPOINT: "http://localhost:11434"}
        )
        self.assertIsNone(resolution.classifier)
        self.assertEqual(resolution.provider, "none")
        self.assertIn("Ollama endpoint detected", resolution.blocked_reason)
        self.assertIn(ENV_OLLAMA_ENABLED, resolution.blocked_reason)

    def test_ollama_endpoint_with_enable_flag_yields_live_classifier(self) -> None:
        poster = _RecordingHttpPoster(response={"response": "{}"})
        resolution = build_classifier_from_env(
            env={
                ENV_OLLAMA_ENDPOINT: "http://localhost:11434",
                ENV_OLLAMA_ENABLED: "true",
                ENV_OLLAMA_MODEL: "llama3:latest",
                ENV_OLLAMA_TIMEOUT: "30",
            },
            http_poster=poster,
        )
        self.assertIsNotNone(resolution.classifier)
        self.assertEqual(resolution.provider, "ollama")
        self.assertIn("ollama", resolution.detected_keys)
        # Ensure the model + timeout flowed through.
        assert resolution.classifier is not None
        resolution.classifier.classify(request=DecisionRequest(prompt="hi"))
        self.assertEqual(poster.calls[0]["body"]["model"], "llama3:latest")
        self.assertEqual(poster.calls[0]["timeout"], 30)

    def test_ollama_default_model_when_env_unset(self) -> None:
        poster = _RecordingHttpPoster(response={"response": "{}"})
        resolution = build_classifier_from_env(
            env={
                ENV_OLLAMA_ENDPOINT: "http://localhost:11434",
                ENV_OLLAMA_ENABLED: "1",
            },
            http_poster=poster,
        )
        assert resolution.classifier is not None
        resolution.classifier.classify(request=DecisionRequest(prompt="hi"))
        # Default is gemma3:latest, default timeout 20.
        self.assertEqual(poster.calls[0]["body"]["model"], "gemma3:latest")
        self.assertEqual(poster.calls[0]["timeout"], 20)

    def test_anthropic_key_without_flag_is_blocked(self) -> None:
        resolution = build_classifier_from_env(
            env={ENV_ANTHROPIC_API_KEY: "sk-ant-test"}
        )
        self.assertIsNone(resolution.classifier)
        self.assertEqual(resolution.provider, "none")
        self.assertIn("anthropic", resolution.detected_keys)
        self.assertIn("Anthropic key detected", resolution.blocked_reason)
        self.assertIn(ENV_ANTHROPIC_ENABLED, resolution.blocked_reason)

    def test_anthropic_alt_key_alias_detected(self) -> None:
        # CLAUDE_API_KEY alias works the same as ANTHROPIC_API_KEY.
        resolution = build_classifier_from_env(
            env={ENV_ANTHROPIC_API_KEY_ALT: "sk-ant-test"}
        )
        self.assertIn("anthropic", resolution.detected_keys)

    def test_anthropic_key_with_flag_yields_blocked_adapter(self) -> None:
        # The factory wires the adapter contract; the adapter itself
        # is currently a blocked stub. The detection succeeds, but
        # `classify()` returns clarification_needed.
        resolution = build_classifier_from_env(
            env={
                ENV_ANTHROPIC_API_KEY: "sk-ant-test",
                ENV_ANTHROPIC_ENABLED: "true",
            }
        )
        self.assertEqual(resolution.provider, "anthropic")
        assert resolution.classifier is not None
        verdict = resolution.classifier.classify(
            request=DecisionRequest(prompt="hi")
        )
        self.assertEqual(verdict.mode, MODE_CLARIFICATION_NEEDED)
        self.assertIn("blocked", verdict.reason)

    def test_openai_key_without_flag_is_blocked(self) -> None:
        resolution = build_classifier_from_env(
            env={ENV_OPENAI_API_KEY: "sk-openai-test"}
        )
        self.assertEqual(resolution.provider, "none")
        self.assertIn("openai", resolution.detected_keys)
        self.assertIn("OpenAI key detected", resolution.blocked_reason)

    def test_openai_key_with_flag_yields_blocked_adapter(self) -> None:
        resolution = build_classifier_from_env(
            env={
                ENV_OPENAI_API_KEY: "sk-openai-test",
                ENV_OPENAI_ENABLED: "yes",
            }
        )
        self.assertEqual(resolution.provider, "openai")
        assert resolution.classifier is not None
        verdict = resolution.classifier.classify(
            request=DecisionRequest(prompt="hi")
        )
        self.assertEqual(verdict.mode, MODE_CLARIFICATION_NEEDED)

    def test_provider_priority_anthropic_over_openai_over_ollama(self) -> None:
        # All three present + enabled. Anthropic wins.
        resolution = build_classifier_from_env(
            env={
                ENV_ANTHROPIC_API_KEY: "sk-ant-test",
                ENV_ANTHROPIC_ENABLED: "true",
                ENV_OPENAI_API_KEY: "sk-openai-test",
                ENV_OPENAI_ENABLED: "true",
                ENV_OLLAMA_ENDPOINT: "http://localhost:11434",
                ENV_OLLAMA_ENABLED: "true",
            }
        )
        self.assertEqual(resolution.provider, "anthropic")

    def test_priority_openai_over_ollama_when_anthropic_absent(self) -> None:
        resolution = build_classifier_from_env(
            env={
                ENV_OPENAI_API_KEY: "sk-openai-test",
                ENV_OPENAI_ENABLED: "true",
                ENV_OLLAMA_ENDPOINT: "http://localhost:11434",
                ENV_OLLAMA_ENABLED: "true",
            }
        )
        self.assertEqual(resolution.provider, "openai")

    def test_falls_through_to_ollama_when_higher_providers_unauthorised(self) -> None:
        # Anthropic key present but flag missing → fall through to ollama.
        poster = _RecordingHttpPoster(response={"response": "{}"})
        resolution = build_classifier_from_env(
            env={
                ENV_ANTHROPIC_API_KEY: "sk-ant-test",  # detected, not enabled
                ENV_OLLAMA_ENDPOINT: "http://localhost:11434",
                ENV_OLLAMA_ENABLED: "true",
            },
            http_poster=poster,
        )
        self.assertEqual(resolution.provider, "ollama")
        self.assertIn("anthropic", resolution.detected_keys)
        self.assertIn("ollama", resolution.detected_keys)


# ---------------------------------------------------------------------------
# ClassifierResolution
# ---------------------------------------------------------------------------


class ClassifierResolutionTests(unittest.TestCase):
    def test_default_fields(self) -> None:
        resolution = ClassifierResolution(classifier=None, provider="none")
        self.assertEqual(resolution.blocked_reason, "")
        self.assertEqual(resolution.detected_keys, ())


if __name__ == "__main__":
    unittest.main()
