"""backend-engineer role contract v1 — 7-phase 강화 회귀 보장.

backend-engineer profile은 단순 구현자가 아니라 API 계약, 도메인 규칙,
데이터 정합성, 권한 경계, 트랜잭션 안정성, 실패 복구, 운영 관측
가능성을 책임지는 서버 설계자로 계약되어 있다.

이 회귀는 7-phase로 점진적으로 채워진다:
  PHASE 1 — identity (description / core_principles / phase)
  PHASE 2 — operating_modes / reasoning_flow / required_questions
  PHASE 3 — contract standards (api/data/error/security/transaction/observability/test_handoff)
  PHASE 4 — stop_conditions / escalation_triggers
  PHASE 5 — default_response_template / review_checklist_by_category / required_context_catalog
  PHASE 6 — profile↔deliberation/output 연결
  PHASE 7 — 문서화

각 phase commit 시 자기 phase의 회귀만 점진적으로 활성화한다.
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
    REPO_ROOT / "agents" / "engineering-agent" / "backend-engineer" / "agent.json"
)


def _load() -> dict:
    return json.loads(PROFILE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# PHASE 1 — identity
# ---------------------------------------------------------------------------


class Phase1IdentityTests(unittest.TestCase):
    def test_phase_advanced_past_skeleton(self) -> None:
        # backend-engineer가 role-contract-v1로 승격됐는지 — selector/
        # status 진단이 phase 값을 보고 단계 표시할 수 있게 stable 키 유지.
        self.assertEqual(_load().get("phase"), "role-contract-v1")

    def test_description_emphasises_contract_and_safety(self) -> None:
        desc = _load().get("description") or ""
        # description은 단순 구현자가 아닌 "서버 설계자" 정체성을
        # 명시해야 한다 — 게이트웨이/aggregator가 description을
        # 그대로 사용자 보고에 노출할 수 있도록.
        for needle in ("서버", "API", "데이터", "권한", "운영"):
            self.assertIn(needle, desc, f"description missing: {needle}")
        self.assertNotIn("단순 API 구현자", desc, "description still treats backend as a coder only")

    def test_core_principles_focus_on_safety_and_contracts(self) -> None:
        principles = _load().get("core_principles") or []
        self.assertGreaterEqual(len(principles), 5)
        joined = " ".join(principles)
        # 핵심 원칙 키워드: 데이터 정합성, 권한 경계, 실패 복구, API 공개 계약,
        # DB 무결성, 외부 연동 timeout/retry/idempotency, 운영/테스트 가능성.
        for needle in (
            "데이터 정합성",
            "권한 경계",
            "실패",
            "공개 계약",
            "무결성",
            "timeout",
            "idempotency",
            "테스트",
        ):
            self.assertIn(needle, joined, f"core_principles missing: {needle}")


# ---------------------------------------------------------------------------
# PHASE 2 — operating_modes / reasoning_flow / required_questions
# (다음 commit에서 활성화 — 현재는 skip 표시)
# ---------------------------------------------------------------------------


class Phase2OperatingModesTests(unittest.TestCase):
    def test_operating_modes_cover_lifecycle(self) -> None:
        modes = _load().get("operating_modes")
        if modes is None:
            self.skipTest("PHASE 2 not landed yet")
        for mode in (
            "design",
            "implementation",
            "review",
            "debugging",
            "migration",
        ):
            self.assertIn(mode, modes, f"operating_modes missing: {mode}")
            self.assertGreaterEqual(len(modes[mode]["required_output"]), 1)

    def test_reasoning_flow_is_long_enough(self) -> None:
        flow = _load().get("reasoning_flow")
        if flow is None:
            self.skipTest("PHASE 2 not landed yet")
        self.assertGreaterEqual(len(flow), 8)

    def test_required_questions_cover_safety(self) -> None:
        qs = _load().get("required_questions")
        if qs is None:
            self.skipTest("PHASE 2 not landed yet")
        self.assertGreaterEqual(len(qs), 10)
        joined = " ".join(qs)
        for needle in ("권한", "동시성", "idempotency", "민감"):
            self.assertIn(needle, joined, f"required_questions missing: {needle}")


# ---------------------------------------------------------------------------
# PHASE 3 — contract standards
# ---------------------------------------------------------------------------


class Phase3StandardsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = _load()

    def _std(self, name: str):
        v = self.profile.get(name)
        if v is None:
            self.skipTest(f"{name} not landed yet (phase 3 pending)")
        self.assertIsInstance(v, dict, f"{name} not a dict")
        return v

    def test_api_contract_standard_includes_endpoint_and_errors(self) -> None:
        std = self._std("api_contract_standard")
        items = " ".join(std.get("required_fields", []))
        for needle in ("endpoint", "method", "error_responses", "idempotency_behavior"):
            self.assertIn(needle, items, f"api_contract missing: {needle}")

    def test_data_contract_standard_includes_migration_and_rollback(self) -> None:
        std = self._std("data_contract_standard")
        items = " ".join(std.get("required_fields", []))
        for needle in ("migration_required", "rollback_strategy", "primary_key_policy"):
            self.assertIn(needle, items, f"data_contract missing: {needle}")

    def test_error_contract_standard_lists_required_cases(self) -> None:
        std = self._std("error_contract_standard")
        items = " ".join(std.get("required_error_cases", []))
        for needle in ("validation_error", "permission_denied", "duplicate_request", "timeout"):
            self.assertIn(needle, items, f"error_contract missing: {needle}")

    def test_security_review_standard_includes_object_level_permission(self) -> None:
        std = self._std("security_review_standard")
        items = " ".join(std.get("must_check", []))
        for needle in ("object_level_permission", "input_validation", "secret_handling", "audit_logging"):
            self.assertIn(needle, items, f"security missing: {needle}")

    def test_transaction_review_standard_includes_idempotency(self) -> None:
        std = self._std("transaction_review_standard")
        items = " ".join(std.get("must_check", []))
        for needle in ("idempotency_key_required", "transaction_boundary", "concurrent_update_risk"):
            self.assertIn(needle, items, f"transaction missing: {needle}")

    def test_observability_standard_lists_logs_and_metrics(self) -> None:
        std = self._std("observability_standard")
        items = " ".join(std.get("must_define", []))
        for needle in ("structured_logs", "correlation_id", "metrics", "alerts"):
            self.assertIn(needle, items, f"observability missing: {needle}")

    def test_test_handoff_standard_includes_failure_axes(self) -> None:
        std = self._std("test_handoff_standard")
        items = " ".join(std.get("must_include", []))
        for needle in (
            "happy_path",
            "validation_failure",
            "authorization_failure",
            "duplicate_request",
            "concurrent_request",
            "rollback_or_recovery_case",
        ):
            self.assertIn(needle, items, f"test_handoff missing: {needle}")


# ---------------------------------------------------------------------------
# PHASE 4 — stop_conditions / escalation_triggers
# ---------------------------------------------------------------------------


class Phase4StopAndEscalationTests(unittest.TestCase):
    def test_stop_conditions_block_unsafe_writes(self) -> None:
        stops = _load().get("stop_conditions")
        if stops is None:
            self.skipTest("PHASE 4 not landed yet")
        self.assertGreaterEqual(len(stops), 5)
        joined = " ".join(stops)
        for needle in (
            "권한",
            "destructive migration",
            "민감",
            "외부",
            "idempotency",
            "error contract",
        ):
            self.assertIn(needle, joined, f"stop_conditions missing: {needle}")

    def test_escalation_triggers_cover_member_roles(self) -> None:
        triggers = _load().get("escalation_triggers")
        if triggers is None:
            self.skipTest("PHASE 4 not landed yet")
        for target in (
            "tech-lead",
            "frontend-engineer",
            "ai-engineer",
            "devops-engineer",
            "qa-engineer",
            "product-designer",
        ):
            self.assertIn(target, triggers, f"escalation missing: {target}")


# ---------------------------------------------------------------------------
# PHASE 5 — default_response_template / review_checklist_by_category /
#           required_context_catalog
# ---------------------------------------------------------------------------


class Phase5TemplateAndCatalogTests(unittest.TestCase):
    def test_default_response_template_covers_contract_and_safety(self) -> None:
        template = _load().get("default_response_template")
        if template is None:
            self.skipTest("PHASE 5 not landed yet")
        joined = " ".join(template)
        for needle in (
            "API 계약",
            "데이터 모델",
            "인증",
            "트랜잭션",
            "실패 케이스",
            "Handoff",
        ):
            self.assertIn(needle, joined, f"template missing: {needle}")

    def test_review_checklist_by_category_lists_categories(self) -> None:
        checklist = _load().get("review_checklist_by_category")
        if checklist is None:
            self.skipTest("PHASE 5 not landed yet")
        for cat in ("api", "auth", "data", "transaction", "failure", "operation", "handoff"):
            self.assertIn(cat, checklist, f"category missing: {cat}")
            self.assertGreaterEqual(len(checklist[cat]), 1)

    def test_required_context_catalog_covers_eight_categories(self) -> None:
        catalog = _load().get("required_context_catalog")
        if catalog is None:
            self.skipTest("PHASE 5 not landed yet")
        self.assertGreaterEqual(len(catalog), 8)
        for cat in (
            "project_context",
            "runtime_context",
            "api_context",
            "domain_context",
            "data_context",
            "auth_context",
            "integration_context",
            "security_context",
            "operation_context",
            "test_context",
        ):
            self.assertIn(cat, catalog, f"catalog missing: {cat}")
            self.assertGreaterEqual(len(catalog[cat]), 1)


# ---------------------------------------------------------------------------
# PHASE 6 — profile↔deliberation 연결
# ---------------------------------------------------------------------------


class Phase6ProfileWiringTests(unittest.TestCase):
    """deliberation/output에 backend output template이 노출되는지 확인 — Phase 6 commit에서 활성화."""

    def test_output_sections_align_with_response_template(self) -> None:
        # role_profiles helper가 backend-engineer의 output_sections를
        # 반환하는지 확인. 이건 phase 1-5에서도 통과 가능 — 기존
        # role_profiles_data에 이미 정의됨.
        from yule_orchestrator.agents.role_profiles import (
            output_template_for_role,
        )

        sections = output_template_for_role("backend-engineer")
        # 최소한 핵심 판단 / API 영향 / DB 영향 / 트랜잭션 / 예외 케이스 / 구현 제안.
        self.assertGreaterEqual(len(sections), 5)
        joined = " ".join(sections)
        for needle in ("API", "DB", "트랜잭션", "예외"):
            self.assertIn(needle, joined, f"output_sections missing: {needle}")


# ---------------------------------------------------------------------------
# PHASE 7 — 문서화
# ---------------------------------------------------------------------------


class Phase7DocsTests(unittest.TestCase):
    """policies 문서에 backend role contract v1 섹션이 있는지 확인."""

    def test_role_profiles_doc_mentions_backend_role_contract(self) -> None:
        doc_path = (
            REPO_ROOT
            / "policies"
            / "runtime"
            / "agents"
            / "engineering-agent"
            / "role-profiles.md"
        )
        if not doc_path.exists():
            self.skipTest("role-profiles.md missing — phase 7 pending")
        body = doc_path.read_text(encoding="utf-8")
        # phase 7 commit에서 backend section을 풀어쓴다.
        # 현재 phase에서는 skip — backend 단어만 포함되어 있으면 통과.
        if "backend role contract" not in body.lower():
            self.skipTest("backend role contract section not added yet")
        for needle in ("api_contract_standard", "stop_conditions"):
            self.assertIn(needle, body, f"doc missing: {needle}")


if __name__ == "__main__":
    unittest.main()
