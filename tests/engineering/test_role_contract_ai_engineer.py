"""ai-engineer role contract v1 — 강화된 필드 회귀 보장.

ai-engineer profile은 LLM/RAG/CAG/memory/prompt/evaluation/agent runtime을
설계·검증하는 AI 시스템 엔지니어로 계약되어 있다. retrieval/memory/
evaluation 표준과 hallucination/safety 가드가 누락되지 않도록 회귀로 고정.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE = (
    REPO_ROOT / "agents" / "engineering-agent" / "ai-engineer" / "agent.json"
)


def _load() -> dict:
    return json.loads(PROFILE.read_text(encoding="utf-8"))


class CorePrincipleAndModeFieldsTests(unittest.TestCase):
    def test_core_principles_cover_llm_necessity_and_evaluation(self) -> None:
        principles = _load().get("core_principles") or []
        self.assertGreaterEqual(len(principles), 6)
        joined = " ".join(principles)
        for needle in ("LLM", "RAG", "출처", "hallucination", "비용"):
            self.assertIn(needle, joined, f"core_principles missing: {needle}")

    def test_operating_modes_cover_design_lifecycle(self) -> None:
        modes = _load().get("operating_modes") or {}
        for mode in (
            "ai_architecture_design",
            "prompt_design",
            "rag_design",
            "memory_design",
            "evaluation_design",
            "failure_analysis",
            "model_provider_selection",
        ):
            self.assertIn(mode, modes, f"operating_modes missing: {mode}")
            entry = modes[mode]
            self.assertGreaterEqual(len(entry["required_output"]), 1)

    def test_phase_advanced_past_skeleton(self) -> None:
        self.assertEqual(_load().get("phase"), "role-contract-v1")


class StandardFieldsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = _load()

    def _std(self, name: str) -> dict:
        v = self.profile.get(name)
        self.assertIsInstance(v, dict, f"{name} not a dict")
        return v

    def test_prompt_contract_standard_includes_schema_and_version(self) -> None:
        std = self._std("prompt_contract_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("input_schema", "output_schema", "version_id"):
            self.assertIn(needle, items, f"prompt_contract missing: {needle}")

    def test_retrieval_contract_standard_includes_chunking_and_grounding(self) -> None:
        std = self._std("retrieval_contract_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("chunking_policy", "embedding_model", "retrieval_top_k", "grounding_assertion"):
            self.assertIn(needle, items, f"retrieval_contract missing: {needle}")

    def test_memory_policy_standard_requires_expiration_and_privacy(self) -> None:
        std = self._std("memory_policy_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("expiration_or_decay", "privacy_classification", "write_trigger"):
            self.assertIn(needle, items, f"memory_policy missing: {needle}")

    def test_evaluation_standard_includes_dataset_and_regression(self) -> None:
        std = self._std("evaluation_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("evaluation_dataset", "regression_set", "human_review_protocol"):
            self.assertIn(needle, items, f"evaluation missing: {needle}")

    def test_safety_standard_blocks_pii_leakage(self) -> None:
        std = self._std("safety_standard")
        joined = " ".join(std.get("must_check", []) + std.get("rules", []))
        for needle in ("PII", "마스킹", "권한"):
            self.assertIn(needle, joined, f"safety missing: {needle}")

    def test_cost_latency_standard_caps_tokens_and_rate(self) -> None:
        std = self._std("cost_latency_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("max_tokens_per_request", "max_requests_per_minute", "p95_latency_target"):
            self.assertIn(needle, items, f"cost_latency missing: {needle}")

    def test_hallucination_review_standard_requires_citations(self) -> None:
        std = self._std("hallucination_review_standard")
        joined = " ".join(std.get("must_check", []) + std.get("rules", []))
        for needle in ("citation", "confidence", "fallback"):
            self.assertIn(needle, joined, f"hallucination_review missing: {needle}")


class DefaultResponseTemplateTests(unittest.TestCase):
    def test_template_includes_evaluation_and_handoff(self) -> None:
        template = _load().get("default_response_template") or []
        joined = " ".join(template)
        for needle in (
            "AI 적용 필요성",
            "입력/출력 계약",
            "RAG",
            "평가 기준",
            "hallucination",
            "비용",
            "handoff",
        ):
            self.assertIn(needle, joined, f"template missing: {needle}")


class StopConditionAndEscalationTests(unittest.TestCase):
    def test_stop_conditions_block_unsafe_llm_use(self) -> None:
        stops = _load().get("stop_conditions") or []
        self.assertGreaterEqual(len(stops), 5)
        joined = " ".join(stops)
        # LLM 끼워 넣기 차단, output_schema 없는 응답 차단, 비용/latency
        # 한도, 마스킹 없는 외부 호출 차단, evaluation 없는 release 차단
        for needle in ("LLM", "output_schema", "비용", "마스킹", "evaluation"):
            self.assertIn(needle, joined, f"stop_conditions missing: {needle}")

    def test_escalation_triggers_cover_member_roles(self) -> None:
        triggers = _load().get("escalation_triggers") or {}
        for target in (
            "tech-lead",
            "backend-engineer",
            "frontend-engineer",
            "qa-engineer",
            "devops-engineer",
            "product-designer",
        ):
            self.assertIn(target, triggers, f"escalation missing: {target}")


class RagMemoryEvaluationCoverageTests(unittest.TestCase):
    """RAG/CAG/memory 요청에서 ai-engineer가 핵심 표준을 모두 가지고 응답할 수 있어야 한다."""

    def test_profile_supplies_rag_memory_evaluation_standards(self) -> None:
        profile = _load()
        for required in (
            "retrieval_contract_standard",
            "memory_policy_standard",
            "evaluation_standard",
        ):
            self.assertIn(required, profile, f"missing standard: {required}")

    def test_profile_does_not_claim_infra_only_primary(self) -> None:
        # Activation/explicit pattern은 AI 도메인 키워드만 다룬다.
        # infra-only 요청에서는 ai-engineer가 primary가 되지 않도록
        # description/decision_criteria가 AI 적용 필요성을 강제한다.
        principles = " ".join(_load().get("core_principles") or [])
        self.assertIn("규칙 기반", principles)
        self.assertIn("필요한", principles)


if __name__ == "__main__":
    unittest.main()
