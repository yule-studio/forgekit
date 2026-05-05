from __future__ import annotations

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

import unittest
from unittest.mock import patch

from yule_orchestrator.planning.ollama_config import (
    load_ollama_conversation_config,
    load_ollama_planning_config,
)


class OllamaConfigTestCase(unittest.TestCase):
    def test_load_ollama_planning_config_uses_defaults(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            config = load_ollama_planning_config()

        self.assertFalse(config.enabled)
        self.assertEqual(config.endpoint, "http://localhost:11434")
        self.assertEqual(config.model, "gemma3:latest")
        self.assertEqual(config.timeout_seconds, 20)

    def test_load_ollama_planning_config_reads_environment(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "OLLAMA_PLANNING_ENABLED": "true",
                "OLLAMA_ENDPOINT": "http://ollama.local:11434",
                "OLLAMA_MODEL": "qwen2.5:3b",
                "OLLAMA_TIMEOUT_SECONDS": "45",
            },
            clear=True,
        ):
            config = load_ollama_planning_config()

        self.assertTrue(config.enabled)
        self.assertEqual(config.endpoint, "http://ollama.local:11434")
        self.assertEqual(config.model, "qwen2.5:3b")
        self.assertEqual(config.timeout_seconds, 45)

    def test_load_ollama_conversation_config_falls_back_to_shared_ollama_values(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "OLLAMA_PLANNING_ENABLED": "true",
                "OLLAMA_ENDPOINT": "http://ollama.local:11434",
                "OLLAMA_MODEL": "qwen2.5:3b",
                "OLLAMA_TIMEOUT_SECONDS": "45",
            },
            clear=True,
        ):
            config = load_ollama_conversation_config()

        self.assertTrue(config.enabled)
        self.assertEqual(config.endpoint, "http://ollama.local:11434")
        self.assertEqual(config.model, "qwen2.5:3b")
        self.assertEqual(config.timeout_seconds, 45)

    def test_load_ollama_conversation_config_reads_discord_specific_environment(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "OLLAMA_PLANNING_ENABLED": "false",
                "OLLAMA_DISCORD_ENABLED": "true",
                "OLLAMA_DISCORD_ENDPOINT": "http://discord-ollama.local:11434",
                "OLLAMA_DISCORD_MODEL": "llama3.1:8b",
                "OLLAMA_DISCORD_TIMEOUT_SECONDS": "30",
            },
            clear=True,
        ):
            config = load_ollama_conversation_config()

        self.assertTrue(config.enabled)
        self.assertEqual(config.endpoint, "http://discord-ollama.local:11434")
        self.assertEqual(config.model, "llama3.1:8b")
        self.assertEqual(config.timeout_seconds, 30)

    def test_load_ollama_planning_config_reads_fallback_and_retry(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "OLLAMA_FALLBACK_MODEL": "llama3.1:8b",
                "OLLAMA_RETRY_COUNT": "2",
            },
            clear=True,
        ):
            config = load_ollama_planning_config()

        self.assertEqual(config.fallback_model, "llama3.1:8b")
        self.assertEqual(config.retry_count, 2)

    def test_load_ollama_conversation_config_inherits_planning_fallback(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "OLLAMA_FALLBACK_MODEL": "llama3.1:8b",
                "OLLAMA_RETRY_COUNT": "2",
            },
            clear=True,
        ):
            config = load_ollama_conversation_config()

        self.assertEqual(config.fallback_model, "llama3.1:8b")
        self.assertEqual(config.retry_count, 2)

    def test_load_ollama_conversation_config_overrides_with_discord_specific_fallback(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "OLLAMA_FALLBACK_MODEL": "llama3.1:8b",
                "OLLAMA_DISCORD_FALLBACK_MODEL": "qwen2.5:14b",
                "OLLAMA_DISCORD_RETRY_COUNT": "0",
            },
            clear=True,
        ):
            config = load_ollama_conversation_config()

        self.assertEqual(config.fallback_model, "qwen2.5:14b")
        self.assertEqual(config.retry_count, 0)


if __name__ == "__main__":
    unittest.main()
