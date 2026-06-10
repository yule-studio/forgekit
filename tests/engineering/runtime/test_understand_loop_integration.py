"""Phase 2 — runtime loop with the deterministic classifier wired in.

The classifier is the only stage that has been replaced from default;
recall / research / decide / act / record stay at their Phase 1
no-op defaults. This pins the contract that gateway / member bots
will use in Phase 4: ``run_runtime_loop(input, understand_fn=
make_understand_fn())`` produces a usable intent without any other
stage doing IO.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.runtime import (
    INTENT_APPEND_CONTEXT,
    INTENT_CONTINUE_EXISTING_WORK,
    INTENT_DIAGNOSTIC_QUESTION,
    INTENT_EXECUTE_EXISTING_STEP,
    INTENT_NEW_WORK_REQUEST,
    INTENT_STATUS_QUESTION,
    INTENT_SUMMARIZE_PREVIOUS_WORK,
    RuntimeInput,
    make_understand_fn,
    run_runtime_loop,
)


class LoopWithClassifierTests(unittest.TestCase):
    def _run(self, message: str, *, last_proposed: str | None = None):
        understand = make_understand_fn()
        return run_runtime_loop(
            RuntimeInput(
                role_id="gateway",
                message_text=message,
                last_proposed_prompt=last_proposed,
            ),
            understand_fn=understand,
        )

    def test_summarize_routes_through_classifier(self) -> None:
        result = self._run("어제 작업 이어서 요약해줘")
        self.assertEqual(result.intent.intent_id, INTENT_SUMMARIZE_PREVIOUS_WORK)

    def test_continue_routes_through_classifier(self) -> None:
        result = self._run("헤르메스 작업 이어서 가자")
        self.assertEqual(result.intent.intent_id, INTENT_CONTINUE_EXISTING_WORK)

    def test_status_routes_through_classifier(self) -> None:
        result = self._run("지금 뭐 하는 중이야?")
        self.assertEqual(result.intent.intent_id, INTENT_STATUS_QUESTION)

    def test_diagnostic_routes_through_classifier(self) -> None:
        result = self._run("운영 리서치는 안 열어?")
        self.assertEqual(result.intent.intent_id, INTENT_DIAGNOSTIC_QUESTION)

    def test_execute_existing_step_routes_through_classifier(self) -> None:
        result = self._run("Obsidian에 정리해줘")
        self.assertEqual(result.intent.intent_id, INTENT_EXECUTE_EXISTING_STEP)

    def test_append_context_routes_through_classifier(self) -> None:
        result = self._run("이 자료만 기존 작업에 참고로 붙여줘")
        self.assertEqual(result.intent.intent_id, INTENT_APPEND_CONTEXT)

    def test_explicit_new_work_routes_through_classifier(self) -> None:
        result = self._run("새 작업으로 진행")
        self.assertEqual(result.intent.intent_id, INTENT_NEW_WORK_REQUEST)

    def test_typical_implementation_request_is_new_work(self) -> None:
        result = self._run(
            "결제 모듈 멱등성 검증 흐름 백엔드에 추가하는 작업 만들어줘"
        )
        self.assertEqual(result.intent.intent_id, INTENT_NEW_WORK_REQUEST)

    def test_loop_default_research_stage_does_not_run(self) -> None:
        # Classifier wired in, every other stage default. The default
        # research stage must not return run=True regardless of intent.
        result = self._run("새 작업으로 진행")
        self.assertFalse(result.research_plan.run)

    def test_loop_records_no_error_for_classifier_path(self) -> None:
        result = self._run("어제 작업 이어서 요약해줘")
        self.assertIsNone(result.error)


if __name__ == "__main__":
    unittest.main()
