"""기술 자율 vs 외부 사실 — authorization 가드 테스트 (P0-S).

엔지니어링 에이전트가 단순 기술 선택 (JWT/DB/Docker 구조) 은 자율로
정하고, 서버 IP / SSH / secret 값 같은 외부 사실은 반드시 사람에게
요청해야 한다는 경계를 코드 레벨로 고정.

  1. JWT vs session 같은 기술 선택은 ``classify_user_request_facts``
     가 어떤 외부 사실 카드도 요구하지 않는다.
  2. 서버 IP / SSH / secret 키워드가 나오면 INFO/ACCESS/SECRET 버킷에
     키워드가 잡힌다.
  3. ``_DEFAULT_SAFETY_RULES`` 에 secret 자율/사람 경계와 "카드 없이
     멈추는 것 금지" 룰이 들어있다.
  4. ``recommend_authorization`` 의 기존 흐름은 회귀 없이 그대로 동작
     (JWT 같은 단순 기술 요청도 executor 만 정하고 사람에게 카드를
     떠넘기지 않는다).
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.coding.authorization import (
    EXTERNAL_FACT_HUMAN_REQUIRED,
    TECH_DECISION_AUTONOMOUS,
    _DEFAULT_SAFETY_RULES,
    classify_user_request_facts,
    recommend_authorization,
    reset_role_profile_cache,
)
from yule_engineering.agents.operator_action import OperatorActionType


class TechAutonomousVsExternalFactBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_role_profile_cache()

    def test_jwt_vs_session_is_pure_tech_choice(self) -> None:
        result = classify_user_request_facts(
            "JWT 로 갈지 session 으로 갈지 결정해서 인증 흐름 만들어줘"
        )
        # 어떤 외부 사실 버킷도 채워지면 안 됨 — 단순 기술 선택은 자율
        self.assertEqual(result[OperatorActionType.INFO_REQUIRED.value], ())
        self.assertEqual(result[OperatorActionType.ACCESS_REQUIRED.value], ())
        self.assertEqual(result[OperatorActionType.SECRET_REQUIRED.value], ())

    def test_db_name_choice_is_pure_tech_choice(self) -> None:
        result = classify_user_request_facts(
            "이 서비스 DB 이름 어떻게 잡으면 좋을까?"
        )
        self.assertEqual(result[OperatorActionType.INFO_REQUIRED.value], ())
        self.assertEqual(result[OperatorActionType.ACCESS_REQUIRED.value], ())
        self.assertEqual(result[OperatorActionType.SECRET_REQUIRED.value], ())

    def test_server_ip_request_triggers_info_bucket(self) -> None:
        result = classify_user_request_facts(
            "배포 대상 서버 IP 가 필요한 deploy 스크립트 작성해줘"
        )
        self.assertIn("서버 ip", result[OperatorActionType.INFO_REQUIRED.value])

    def test_ssh_request_triggers_access_bucket(self) -> None:
        result = classify_user_request_facts(
            "prod 서버에 ssh 접근 가능한 deploy 자동화 만들어줘"
        )
        self.assertTrue(
            len(result[OperatorActionType.ACCESS_REQUIRED.value]) >= 1,
            f"expected ACCESS bucket non-empty, got {result}",
        )

    def test_secret_value_request_triggers_secret_bucket(self) -> None:
        result = classify_user_request_facts(
            "JWT_SECRET 값을 등록해서 wiring 마무리해줘"
        )
        self.assertTrue(
            len(result[OperatorActionType.SECRET_REQUIRED.value]) >= 1,
            f"expected SECRET bucket non-empty, got {result}",
        )

    def test_empty_request_returns_empty_buckets(self) -> None:
        result = classify_user_request_facts("")
        for bucket in result.values():
            self.assertEqual(bucket, ())


class SafetyRulesContentTests(unittest.TestCase):
    def test_secret_boundary_rule_present(self) -> None:
        joined = "\n".join(_DEFAULT_SAFETY_RULES)
        # secret 자율 가능 / 사람 요청 경계 명시
        self.assertIn("agent 자율 가능", joined)
        self.assertIn(".env.example", joined)
        self.assertIn("SECRET_REQUIRED 카드", joined)

    def test_silent_stop_forbidden_rule_present(self) -> None:
        joined = "\n".join(_DEFAULT_SAFETY_RULES)
        self.assertIn("카드 없이 세션이 멈추는", joined)

    def test_external_fact_rule_present(self) -> None:
        joined = "\n".join(_DEFAULT_SAFETY_RULES)
        self.assertIn("INFO_REQUIRED", joined)
        self.assertIn("ACCESS_REQUIRED", joined)


class StaticBoundaryDocumentationTests(unittest.TestCase):
    """경계 목록이 코드에 박혀있어 docs 와 동기화 검증 가능한지 확인."""

    def test_tech_autonomous_includes_jwt_session(self) -> None:
        joined = " | ".join(TECH_DECISION_AUTONOMOUS)
        self.assertIn("JWT vs session", joined)
        self.assertIn("DB 이름", joined)
        self.assertIn("Docker Compose", joined)

    def test_external_fact_includes_server_ip_and_secret(self) -> None:
        joined = " | ".join(EXTERNAL_FACT_HUMAN_REQUIRED)
        self.assertIn("실제 서버 IP", joined)
        self.assertIn("SSH user", joined)
        self.assertIn("실제 secret 값", joined)


class AuthorizationRecommenderRegressionTests(unittest.TestCase):
    """기존 recommender 흐름이 회귀 없이 동작."""

    def setUp(self) -> None:
        reset_role_profile_cache()

    def test_jwt_request_still_picks_backend_engineer(self) -> None:
        # JWT 같은 단순 기술 선택은 사람 입력 없이 backend-engineer 가
        # executor 로 자동 선정. operator action 카드를 강제하지 않는다.
        proposal = recommend_authorization(
            user_request="JWT 기반 API 인증 흐름 추가해줘",
        )
        self.assertEqual(proposal.executor_role, "backend-engineer")
        self.assertTrue(proposal.approval_required)


if __name__ == "__main__":
    unittest.main()
