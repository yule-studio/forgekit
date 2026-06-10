"""Live smoke fix — issue-less full-stack request classification 회귀.

Reproduces the failure observed in session ``c5278a9043f2``:
- 사용자가 ``Next.js + NestJS + PostgreSQL + Docker Compose 기반 회원가입/
  로그인/검색`` 같은 풀스택 요청을 보내면
- :func:`Dispatcher.classify` 가 docker / qa 키워드 만으로 platform-infra /
  qa-test 로 misclassify 했었다.

이 test 가 통과 = stack_detector 우선 분기가 살아있다는 뜻. silently
약해지면 가장 먼저 잡힌다.
"""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents import (
    Dispatcher,
    DispatchRequest,
    TaskType,
    build_participants_pool,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _build_dispatcher() -> Dispatcher:
    pool = build_participants_pool(REPO_ROOT, "engineering-agent", factories={})
    return Dispatcher(pool)


class FullStackClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.disp = _build_dispatcher()

    def test_session_c5278a9043f2_repro_full_stack(self) -> None:
        prompt = (
            "approval_required, single_repo, full_stack_single_repo로 진행해줘.\n"
            "repo: https://github.com/yule-studio/naver-search-clone.git\n"
            "목표: Next.js + NestJS + PostgreSQL + Docker Compose 기반 "
            "회원가입/로그인/로그아웃/검색 결과 목록 앱 구현"
        )
        result = self.disp.classify(DispatchRequest(prompt=prompt))
        self.assertEqual(
            result,
            TaskType.FULL_STACK_APP,
            f"session c5278a9043f2 repro: docker keyword 가 stack_detector 를 "
            f"우회해 잘못 매칭됨 — got {result}",
        )

    def test_full_stack_with_qa_substring_still_full_stack(self) -> None:
        # "테스트" 라는 단어가 포함돼도 stack_detector 신호가 더 강하면
        # FULL_STACK_APP 로 분류 (qa-test 회귀 방지)
        prompt = (
            "Next.js + NestJS + PostgreSQL + Docker Compose 기반 회원가입/"
            "로그인 앱 구현. 테스트도 같이 추가해줘"
        )
        self.assertEqual(
            self.disp.classify(DispatchRequest(prompt=prompt)),
            TaskType.FULL_STACK_APP,
        )

    def test_pure_qa_request_still_qa_test(self) -> None:
        # stack 신호 없이 regression / qa / 회귀 만 있으면 QA_TEST 회귀 보존
        for prompt in (
            "기존 회원가입에 regression test 추가해줘",
            "회귀 시나리오 정리",
            "QA test plan 만들어줘",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(
                    self.disp.classify(DispatchRequest(prompt=prompt)),
                    TaskType.QA_TEST,
                )

    def test_pure_infra_still_platform_infra(self) -> None:
        # stack 신호 없이 terraform / deploy 만 있으면 PLATFORM_INFRA 회귀 보존
        for prompt in (
            "terraform module 정리해줘",
            "github actions workflow 추가",
            "deploy 스크립트 정리",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(
                    self.disp.classify(DispatchRequest(prompt=prompt)),
                    TaskType.PLATFORM_INFRA,
                )

    def test_docker_keyword_no_longer_in_keyword_table(self) -> None:
        # P0-T smoke fix — `docker` 단독 keyword 가 _KEYWORD_RULES 의
        # PLATFORM_INFRA 엔트리에서 제거됐는지. stack_detector 가
        # is_infra_only 분류한 경우 외에는 docker 만으로 platform-infra
        # 분류되면 안 됨.
        from yule_engineering.agents.messaging.dispatcher import _KEYWORD_RULES

        for task_type, keywords in _KEYWORD_RULES:
            if task_type == TaskType.PLATFORM_INFRA:
                self.assertNotIn(
                    "docker",
                    keywords,
                    "_KEYWORD_RULES PLATFORM_INFRA entry 가 docker 키워드를 "
                    "다시 가졌음 — full-stack 요청이 docker 만으로 misclassify",
                )
                self.assertNotIn("k8s", keywords)
                break
        else:
            self.fail("PLATFORM_INFRA entry not found in _KEYWORD_RULES")

    def test_explicit_task_type_still_wins(self) -> None:
        # operator 가 명시한 task_type 은 stack_detector 를 무시하고 우선
        prompt = "Next.js + NestJS + Postgres 기반 회원가입"
        result = self.disp.classify(
            DispatchRequest(prompt=prompt, task_type=TaskType.QA_TEST)
        )
        self.assertEqual(result, TaskType.QA_TEST)

    def test_frontend_only_request(self) -> None:
        # tier 1개만 있으면 FULL_STACK_APP 안 됨 — keyword fallback
        result = self.disp.classify(
            DispatchRequest(
                prompt="React 컴포넌트 정리해줘 (Next.js)"
            )
        )
        # Stack detector 가 단일 tier 만 보면 is_full_stack=False — keyword
        # fallback 으로 frontend-feature
        self.assertEqual(result, TaskType.FRONTEND_FEATURE)


if __name__ == "__main__":
    unittest.main()
