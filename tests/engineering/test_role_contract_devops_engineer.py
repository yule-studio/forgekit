"""devops-engineer role contract v1 — 강화된 필드 회귀 보장.

devops-engineer profile은 배포/CI-CD/k8s/secret/관측/장애 대응/롤백을
책임지는 운영 엔지니어로 계약되어 있다. rollback/observability/secret
가드가 누락되어 production 사고로 이어지지 않도록 회귀로 고정.
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
    REPO_ROOT / "agents" / "engineering-agent" / "devops-engineer" / "manifest.json"
)


def _load() -> dict:
    return json.loads(PROFILE.read_text(encoding="utf-8"))


class CorePrincipleAndModeFieldsTests(unittest.TestCase):
    def test_core_principles_cover_runtime_secret_observability(self) -> None:
        principles = _load().get("core_principles") or []
        self.assertGreaterEqual(len(principles), 5)
        joined = " ".join(principles)
        for needle in ("운영", "secret", "rollback", "관측", "Kubernetes"):
            self.assertIn(needle, joined, f"core_principles missing: {needle}")

    def test_operating_modes_cover_lifecycle(self) -> None:
        modes = _load().get("operating_modes") or {}
        for mode in (
            "local_runtime_review",
            "ci_cd_design",
            "deployment_design",
            "kubernetes_design",
            "observability_design",
            "incident_response",
            "rollback_planning",
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

    def test_runtime_environment_standard_distinguishes_envs(self) -> None:
        std = self._std("runtime_environment_standard")
        items = " ".join(std.get("must_include", []))
        self.assertIn("local_vs_staging_vs_prod_diffs", items)
        self.assertIn("reproducibility_recipe", items)

    def test_deployment_standard_requires_health_and_rollback(self) -> None:
        std = self._std("deployment_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("health_checks", "readiness_probe", "rollback_plan", "verification_steps"):
            self.assertIn(needle, items, f"deployment missing: {needle}")

    def test_ci_cd_standard_requires_test_gating(self) -> None:
        std = self._std("ci_cd_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("pipeline_stages", "test_gating", "secret_scope"):
            self.assertIn(needle, items, f"ci_cd missing: {needle}")

    def test_kubernetes_standard_requires_limits_and_probes(self) -> None:
        std = self._std("kubernetes_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("resource_requests_and_limits", "probes", "ingress_and_tls", "scaling_policy"):
            self.assertIn(needle, items, f"kubernetes missing: {needle}")

    def test_secret_config_standard_blocks_plaintext(self) -> None:
        std = self._std("secret_config_standard")
        joined = " ".join(std.get("must_include", []) + std.get("rules", []))
        self.assertIn("rotation_policy", joined)
        self.assertIn("env_example_sync", joined)
        self.assertIn("평문", joined)

    def test_observability_standard_requires_alerts_and_logs(self) -> None:
        std = self._std("observability_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("structured_logs", "correlation_id", "metrics", "alerts", "alert_threshold"):
            self.assertIn(needle, items, f"observability missing: {needle}")

    def test_incident_response_standard_includes_playbook(self) -> None:
        std = self._std("incident_response_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("incident_severity_levels", "playbook_per_severity", "postmortem_template"):
            self.assertIn(needle, items, f"incident missing: {needle}")

    def test_rollback_recovery_standard_includes_data_plan(self) -> None:
        std = self._std("rollback_recovery_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("rollback_trigger", "rollback_procedure", "data_recovery_plan", "verification_steps"):
            self.assertIn(needle, items, f"rollback missing: {needle}")


class DefaultResponseTemplateTests(unittest.TestCase):
    def test_template_includes_rollback_and_observability(self) -> None:
        template = _load().get("default_response_template") or []
        joined = " ".join(template)
        for needle in (
            "실행 환경",
            "배포",
            "Kubernetes",
            "환경변수",
            "로그",
            "rollback",
            "handoff",
        ):
            self.assertIn(needle, joined, f"template missing: {needle}")


class StopConditionAndEscalationTests(unittest.TestCase):
    def test_stop_conditions_block_unsafe_deployment(self) -> None:
        stops = _load().get("stop_conditions") or []
        self.assertGreaterEqual(len(stops), 5)
        joined = " ".join(stops)
        for needle in ("승인", "rollback", "secret", "health check", "관측"):
            self.assertIn(needle, joined, f"stop_conditions missing: {needle}")

    def test_escalation_triggers_cover_member_roles(self) -> None:
        triggers = _load().get("escalation_triggers") or {}
        for target in (
            "tech-lead",
            "backend-engineer",
            "frontend-engineer",
            "ai-engineer",
            "qa-engineer",
            "product-designer",
        ):
            self.assertIn(target, triggers, f"escalation missing: {target}")


class DesignerExclusionForInfraTests(unittest.TestCase):
    """UX/디자인 요청에서 devops가 primary가 되거나 infra-only 요청에서
    designer가 끼어들지 않도록, 활성화 키워드와 결정 기준이 운영 도메인에
    집중되어 있어야 한다."""

    def test_default_executor_priority_is_ops_focused(self) -> None:
        priority = _load().get("default_executor_priority") or {}
        joined = " ".join(priority.get("high", [])).lower()
        for needle in ("ci", "deploy", "docker", "supervisor"):
            self.assertIn(needle, joined, f"default_executor_priority.high missing: {needle}")

    def test_decision_criteria_focuses_on_runtime_safety(self) -> None:
        criteria = _load().get("decision_criteria") or []
        joined = " ".join(criteria)
        # devops 결정은 secret/rollback/runtime 중심이라 UI/copy 도메인이
        # primary가 되는 일이 없도록 한다.
        self.assertTrue(
            any(needle in joined for needle in ("secret", "rollback", "운영", "deploy", "배포")),
            f"decision_criteria 운영 도메인 회귀 실패: {criteria}",
        )


if __name__ == "__main__":
    unittest.main()
