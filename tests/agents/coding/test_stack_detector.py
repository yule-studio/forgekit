"""P0-J commit 2 — stack lexicon + full-stack detector tests."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.coding.stack_detector import (
    StackDetection,
    TIER_AUTH,
    TIER_BACKEND,
    TIER_DATABASE,
    TIER_FRONTEND,
    TIER_INFRA,
    classify_full_stack,
    detect_stacks,
    has_write_intent,
)


# ---------------------------------------------------------------------------
# detect_stacks
# ---------------------------------------------------------------------------


class DetectStacksTests(unittest.TestCase):
    def test_empty_input(self) -> None:
        d = detect_stacks("")
        self.assertEqual(d.stacks, ())
        self.assertFalse(d.has_any)
        self.assertFalse(d.is_full_stack)

    def test_naver_search_clone_full_stack_scenario(self) -> None:
        text = (
            "Next.js + NestJS + PostgreSQL + Docker Compose 기반 "
            "회원가입/로그인/검색 앱 구현"
        )
        d = detect_stacks(text)
        self.assertIn("Next.js", d.stacks)
        self.assertIn("NestJS", d.stacks)
        self.assertIn("PostgreSQL", d.stacks)
        self.assertIn("Docker Compose", d.stacks)
        # Distinct application tiers — frontend / backend / database.
        self.assertIn(TIER_FRONTEND, d.tiers_present)
        self.assertIn(TIER_BACKEND, d.tiers_present)
        self.assertIn(TIER_DATABASE, d.tiers_present)
        self.assertIn(TIER_INFRA, d.tiers_present)
        self.assertTrue(d.is_full_stack)
        # Critical: NOT infra-only — has frontend + backend + db too.
        self.assertFalse(d.is_infra_only)

    def test_pure_infra_request_classified_correctly(self) -> None:
        d = detect_stacks("terraform module + github actions only")
        self.assertIn("Terraform", d.stacks)
        self.assertIn("GitHub Actions", d.stacks)
        self.assertTrue(d.is_infra_only)
        self.assertFalse(d.is_full_stack)

    def test_docker_alone_is_infra_only(self) -> None:
        d = detect_stacks("docker 컨테이너 설정")
        self.assertEqual(d.stacks, ("Docker",))
        self.assertTrue(d.is_infra_only)
        self.assertFalse(d.is_full_stack)

    def test_react_only_is_not_full_stack(self) -> None:
        d = detect_stacks("React 컴포넌트 리팩터링")
        self.assertEqual(d.stacks, ("React",))
        self.assertFalse(d.is_full_stack)

    def test_react_plus_postgres_is_full_stack(self) -> None:
        d = detect_stacks("React 화면 + PostgreSQL 스키마")
        self.assertTrue(d.is_full_stack)


# ---------------------------------------------------------------------------
# Alias matching
# ---------------------------------------------------------------------------


class AliasMatchingTests(unittest.TestCase):
    def test_nextjs_alias_variants(self) -> None:
        for alias in ("Next.js", "nextjs", "next js"):
            d = detect_stacks(alias)
            self.assertEqual(d.stacks, ("Next.js",))

    def test_nestjs_alias_variants(self) -> None:
        for alias in ("Nest.js", "NestJS", "nest js"):
            d = detect_stacks(alias)
            self.assertEqual(d.stacks, ("NestJS",))

    def test_postgresql_alias_variants(self) -> None:
        for alias in ("PostgreSQL", "postgres", "psql"):
            d = detect_stacks(alias)
            self.assertEqual(d.stacks, ("PostgreSQL",))

    def test_docker_compose_alias_variants(self) -> None:
        for alias in ("Docker Compose", "docker-compose"):
            d = detect_stacks(alias)
            self.assertIn("Docker Compose", d.stacks)


# ---------------------------------------------------------------------------
# classify_full_stack convenience
# ---------------------------------------------------------------------------


class ClassifyFullStackTests(unittest.TestCase):
    def test_full_stack_combo_true(self) -> None:
        self.assertTrue(
            classify_full_stack(
                "Next.js + NestJS + PostgreSQL + Docker Compose 회원가입/로그인"
            )
        )

    def test_infra_only_false(self) -> None:
        self.assertFalse(classify_full_stack("docker compose stack 만"))

    def test_single_tier_false(self) -> None:
        self.assertFalse(classify_full_stack("React 화면만"))


# ---------------------------------------------------------------------------
# has_write_intent
# ---------------------------------------------------------------------------


class HasWriteIntentTests(unittest.TestCase):
    def test_build_korean(self) -> None:
        self.assertTrue(has_write_intent("회원가입 화면 만들어줘"))

    def test_implement_english(self) -> None:
        self.assertTrue(has_write_intent("implement the search endpoint"))

    def test_review_intent_excluded(self) -> None:
        self.assertFalse(has_write_intent("이 코드 검토해줘"))

    def test_review_overrides_build_keyword(self) -> None:
        # "리뷰" 가 명시되면 write_intent 가 아님 (defense-in-depth)
        self.assertFalse(has_write_intent("만들어진 거 리뷰해줘"))


if __name__ == "__main__":
    unittest.main()
