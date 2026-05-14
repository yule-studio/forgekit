"""P0-J commit 4 — _suggest_task_type combo 우선 회귀 (#145)."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.messaging.dispatcher import (
    TASK_ROLE_SEQUENCE,
    TaskType,
)
from yule_orchestrator.discord.engineering_conversation import (
    _suggest_task_type,
)


# ---------------------------------------------------------------------------
# Full-stack combo precedence
# ---------------------------------------------------------------------------


class FullStackComboPrecedenceTests(unittest.TestCase):
    def test_naver_search_clone_classified_as_full_stack_app(self) -> None:
        """Critical fix — was returning platform-infra due to "docker"."""

        text = (
            "Next.js + NestJS + PostgreSQL + Docker Compose 기반 "
            "회원가입/로그인/검색 앱 구현해줘"
        )
        result = _suggest_task_type(text)
        self.assertEqual(result, "full-stack-app")
        # Negative check — explicitly NOT platform-infra.
        self.assertNotEqual(result, "platform-infra")

    def test_react_plus_postgres_full_stack(self) -> None:
        result = _suggest_task_type("React 화면 + PostgreSQL 스키마 묶어줘")
        self.assertEqual(result, "full-stack-app")

    def test_full_stack_app_role_sequence_present(self) -> None:
        # Constructor wiring — TaskType.FULL_STACK_APP must have a sequence.
        self.assertIn(TaskType.FULL_STACK_APP, TASK_ROLE_SEQUENCE)
        seq = TASK_ROLE_SEQUENCE[TaskType.FULL_STACK_APP]
        # tech-lead always opens; full-stack should include both front+back+devops.
        self.assertEqual(seq[0], "tech-lead")
        for role in ("backend-engineer", "frontend-engineer", "devops-engineer"):
            self.assertIn(role, seq)


# ---------------------------------------------------------------------------
# Pure infra still classified as platform-infra
# ---------------------------------------------------------------------------


class PureInfraStillPlatformInfraTests(unittest.TestCase):
    def test_terraform_only(self) -> None:
        result = _suggest_task_type("terraform module 만들어줘")
        self.assertEqual(result, "platform-infra")

    def test_github_actions_only(self) -> None:
        result = _suggest_task_type("github actions workflow 만들어줘")
        self.assertEqual(result, "platform-infra")

    def test_k8s_only(self) -> None:
        result = _suggest_task_type("k8s manifest 만들어줘")
        self.assertEqual(result, "platform-infra")

    def test_docker_only(self) -> None:
        # Docker 단독은 infra-only로 분류 — full-stack 신호 없으면 그대로.
        result = _suggest_task_type("docker 이미지 빌드")
        self.assertEqual(result, "platform-infra")

    def test_deploy_keyword(self) -> None:
        result = _suggest_task_type("deploy script 만들어줘")
        self.assertEqual(result, "platform-infra")


# ---------------------------------------------------------------------------
# Other classifications unchanged (regression)
# ---------------------------------------------------------------------------


class ExistingClassificationsTests(unittest.TestCase):
    def test_landing_page(self) -> None:
        result = _suggest_task_type("랜딩 페이지 디자인")
        self.assertEqual(result, "landing-page")

    def test_qa_test(self) -> None:
        result = _suggest_task_type("회귀 test plan 작성")
        self.assertEqual(result, "qa-test")

    def test_frontend_feature_keyword(self) -> None:
        # frontend keyword alone (no full stack)
        result = _suggest_task_type("frontend 컴포넌트 분리")
        self.assertEqual(result, "frontend-feature")

    def test_backend_feature_keyword(self) -> None:
        result = _suggest_task_type("backend api schema")
        # backend + api triggers; could also be full-stack (api+schema=database)
        # so just check it's NOT platform-infra and IS coding-capable.
        self.assertIn(result, ("backend-feature", "full-stack-app"))

    def test_no_signal_returns_none(self) -> None:
        result = _suggest_task_type("그냥 잡담")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
