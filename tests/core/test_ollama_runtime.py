from __future__ import annotations

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

import unittest
from unittest.mock import patch

from yule_orchestrator.planning.ollama import (
    generate_ollama_text,
    validate_briefing_response,
    validate_conversation_response,
)


class OllamaResponseValidatorsTestCase(unittest.TestCase):
    def test_validate_briefing_response_flags_iso_datetime(self) -> None:
        text = "오늘 9시부터 시작입니다.\n2026-04-22T09:00:00+09:00 이라는 시각에 진행하세요."
        self.assertEqual(validate_briefing_response(text), "ISO datetime leaked")

    def test_validate_briefing_response_flags_internal_score_keyword(self) -> None:
        text = "이 작업이 가장 중요합니다. 내부 점수: 95"
        violation = validate_briefing_response(text)
        assert violation is not None
        self.assertIn("internal-score", violation)

    def test_validate_briefing_response_flags_markdown_heading(self) -> None:
        text = "# 오늘의 브리핑\n\n오늘은 9시에 시작합니다."
        self.assertEqual(validate_briefing_response(text), "markdown heading leaked")

    def test_validate_briefing_response_passes_clean_prose(self) -> None:
        text = "오늘은 9시부터 'A 작업'을 먼저 진행하는 것을 추천합니다.\n끝나면 'B 작업'으로 이어가면 좋습니다."
        self.assertIsNone(validate_briefing_response(text))

    def test_validate_conversation_response_passes_markdown_heading(self) -> None:
        text = "# 추천\n\nA 작업을 먼저 해 주세요."
        self.assertIsNone(validate_conversation_response(text))


class GenerateOllamaTextRetryTestCase(unittest.TestCase):
    def test_retries_when_validator_reports_violation_and_succeeds_on_retry(self) -> None:
        with patch(
            "yule_orchestrator.planning.ollama._ollama_request_once"
        ) as request_mock:
            request_mock.side_effect = [
                "잘못된 출력 2026-04-22T09:00:00+09:00",
                "올바른 한국어 본문입니다.",
            ]

            result = generate_ollama_text(
                "prompt",
                model="primary",
                validate_response=validate_briefing_response,
                retry_count=1,
            )

        self.assertEqual(result, "올바른 한국어 본문입니다.")
        self.assertEqual(request_mock.call_count, 2)
        # both attempts on the primary model
        for call in request_mock.call_args_list:
            self.assertEqual(call.kwargs["model"], "primary")

    def test_falls_back_to_secondary_model_when_primary_keeps_failing(self) -> None:
        with patch(
            "yule_orchestrator.planning.ollama._ollama_request_once"
        ) as request_mock:
            request_mock.side_effect = [
                "잘못된 출력 2026-04-22T09:00:00+09:00",
                "여전히 잘못 2026-04-22T10:00:00+09:00",
                "fallback 모델이 잘 작성한 본문입니다.",
            ]

            result = generate_ollama_text(
                "prompt",
                model="primary",
                fallback_model="secondary",
                validate_response=validate_briefing_response,
                retry_count=1,
            )

        self.assertEqual(result, "fallback 모델이 잘 작성한 본문입니다.")
        self.assertEqual(request_mock.call_count, 3)
        primary_calls = [c for c in request_mock.call_args_list if c.kwargs["model"] == "primary"]
        fallback_calls = [c for c in request_mock.call_args_list if c.kwargs["model"] == "secondary"]
        self.assertEqual(len(primary_calls), 2)
        self.assertEqual(len(fallback_calls), 1)

    def test_raises_when_primary_and_fallback_both_fail_due_to_request_errors(self) -> None:
        with patch(
            "yule_orchestrator.planning.ollama._ollama_request_once"
        ) as request_mock:
            request_mock.side_effect = ValueError("Ollama request request failed: boom")

            with self.assertRaises(ValueError):
                generate_ollama_text(
                    "prompt",
                    model="primary",
                    fallback_model="secondary",
                    retry_count=1,
                )

        # 2 primary attempts + 1 fallback
        self.assertEqual(request_mock.call_count, 3)

    def test_does_not_call_fallback_when_validator_passes_on_first_try(self) -> None:
        with patch(
            "yule_orchestrator.planning.ollama._ollama_request_once"
        ) as request_mock:
            request_mock.return_value = "깨끗한 한국어 본문입니다."

            result = generate_ollama_text(
                "prompt",
                model="primary",
                fallback_model="secondary",
                validate_response=validate_briefing_response,
                retry_count=2,
            )

        self.assertEqual(result, "깨끗한 한국어 본문입니다.")
        self.assertEqual(request_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
