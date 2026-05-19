"""P1-Z5 — tech-lead implementation strategy 가 manifest scope 를 대체.

배경
----
canonical session ``000f13fb121b`` 의 backend manifest 고정 write_scope
(``src/<service>/api/**``) 가 target repo (``apps/`` monorepo) 와 0
매칭으로 ``write_scope_resolved_empty`` 회귀.

본 회귀 라인은 다음을 lock:

1. apps/ + packages/ monorepo fixture → STRATEGY_MONOREPO_APPS,
   first_slice_owner = backend-engineer, scope = ['apps/api/**',
   'packages/**'].  ``<service>`` 등 placeholder literal 없음.
2. frontend/+backend/ split → STRATEGY_FRONTEND_BACKEND_SPLIT,
   backend-engineer first slice, scope = ['backend/**', ...].
3. classic ``src/`` only monolith → STRATEGY_CLASSIC_SRC_LAYOUT.
4. empty repo → STRATEGY_GREENFIELD_EMPTY, scope 포함.
5. unknown layout → STRATEGY_UNRESOLVED, resolved=False.
6. user 가 "UI 부터" 요청 → frontend-engineer first slice.
7. ``recommend_authorization`` 가 strategy.resolved=True 일 때 manifest
   write_scope 무시 + scope_source='strategy'.
8. strategy 없을 때 옛 keyword-based behavior 보존 (no regression).
"""

from __future__ import annotations

import unittest
from typing import Any, Mapping

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.coding.authorization import recommend_authorization
from yule_orchestrator.agents.coding.implementation_strategy import (
    ImplementationStrategy,
    ROLE_BACKEND,
    ROLE_FRONTEND,
    STRATEGY_CLASSIC_SRC_LAYOUT,
    STRATEGY_FRONTEND_BACKEND_SPLIT,
    STRATEGY_GREENFIELD_EMPTY,
    STRATEGY_MONOREPO_APPS,
    STRATEGY_UNRESOLVED,
    detect_repo_layout_signals,
    detect_request_signals,
    synthesize_implementation_strategy,
)


# ---------------------------------------------------------------------------
# Request / layout signal extraction
# ---------------------------------------------------------------------------


class RequestSignalDetectionTests(unittest.TestCase):
    def test_fullstack_request_detected(self) -> None:
        signals = detect_request_signals(
            "네이버 검색형 풀스택 MVP 구축 (인증/검색/블로그/메일)"
        )
        self.assertTrue(signals["full_stack"])
        self.assertIn("auth", signals["scope_hints"])
        self.assertIn("search", signals["scope_hints"])
        self.assertIn("blog", signals["scope_hints"])
        self.assertIn("mail", signals["scope_hints"])

    def test_backend_first_hint(self) -> None:
        signals = detect_request_signals("백엔드 먼저 인증 API 구현")
        self.assertTrue(signals["backend_first_hints"])

    def test_frontend_first_hint(self) -> None:
        signals = detect_request_signals("UI 부터 만들어줘 — 디자인 시스템 먼저")
        self.assertTrue(signals["frontend_first_hints"])


class RepoLayoutSignalTests(unittest.TestCase):
    def test_apps_packages_monorepo(self) -> None:
        signals = detect_repo_layout_signals(
            ("apps/web", "apps/api", "packages/ui", "README.md")
        )
        self.assertTrue(signals["has_apps_dir"])
        self.assertTrue(signals["has_packages_dir"])

    def test_frontend_backend_split(self) -> None:
        signals = detect_repo_layout_signals(
            ("frontend/src", "backend/api", "README.md")
        )
        self.assertTrue(signals["has_frontend_dir"])
        self.assertTrue(signals["has_backend_dir"])

    def test_client_server_split(self) -> None:
        signals = detect_repo_layout_signals(("client/src", "server/main"))
        self.assertTrue(signals["has_client_dir"])
        self.assertTrue(signals["has_server_dir"])

    def test_classic_src_monolith(self) -> None:
        signals = detect_repo_layout_signals(("src/", "tests/", "pyproject.toml"))
        self.assertTrue(signals["has_src_dir"])
        self.assertFalse(signals["has_apps_dir"])

    def test_empty(self) -> None:
        signals = detect_repo_layout_signals(())
        self.assertTrue(signals["is_empty_or_unknown"])


# ---------------------------------------------------------------------------
# Strategy synthesis — canonical shapes
# ---------------------------------------------------------------------------


class SynthesizeStrategyTests(unittest.TestCase):
    def test_naver_search_clone_like_monorepo_apps(self) -> None:
        """실제 ``naver-search-clone`` 류: apps/web + apps/api → monorepo,
        backend first slice."""

        strategy = synthesize_implementation_strategy(
            user_request="네이버 검색형 풀스택 MVP 구축 (인증/검색/블로그/메일)",
            toplevel_paths=("apps", "packages", "README.md"),
        )
        self.assertEqual(strategy.strategy_id, STRATEGY_MONOREPO_APPS)
        self.assertTrue(strategy.resolved)
        self.assertEqual(strategy.first_slice_owner, ROLE_BACKEND)
        self.assertIn("apps/api/**", strategy.first_slice_scope)
        self.assertIn("packages/**", strategy.first_slice_scope)
        # placeholder literal 절대 없음
        for path in strategy.first_slice_scope:
            self.assertNotIn("<", path)
            self.assertNotIn(">", path)
        # frontend 영역 작업 금지 (slice 분리)
        self.assertIn("apps/web/**", strategy.first_slice_forbidden)

    def test_frontend_first_user_intent_overrides_default(self) -> None:
        strategy = synthesize_implementation_strategy(
            user_request="UI 부터 만들어줘 — 디자인 시스템 먼저",
            toplevel_paths=("apps", "packages"),
        )
        self.assertEqual(strategy.first_slice_owner, ROLE_FRONTEND)
        self.assertIn("apps/web/**", strategy.first_slice_scope)
        self.assertIn("apps/api/**", strategy.first_slice_forbidden)

    def test_frontend_backend_split_layout(self) -> None:
        strategy = synthesize_implementation_strategy(
            user_request="인증 API 부터 만들어줘 — 풀스택",
            toplevel_paths=("frontend/", "backend/", "tests/"),
        )
        self.assertEqual(strategy.strategy_id, STRATEGY_FRONTEND_BACKEND_SPLIT)
        self.assertEqual(strategy.first_slice_owner, ROLE_BACKEND)
        self.assertIn("backend/**", strategy.first_slice_scope)

    def test_classic_src_monolith(self) -> None:
        strategy = synthesize_implementation_strategy(
            user_request="검색 API 구현 풀스택",
            toplevel_paths=("src/", "tests/", "pyproject.toml"),
        )
        self.assertEqual(strategy.strategy_id, STRATEGY_CLASSIC_SRC_LAYOUT)
        self.assertTrue(strategy.resolved)
        self.assertIn("src/**", strategy.first_slice_scope)
        for path in strategy.first_slice_scope:
            self.assertNotIn("<service>", path)

    def test_empty_repo_is_greenfield(self) -> None:
        strategy = synthesize_implementation_strategy(
            user_request="풀스택 구현",
            toplevel_paths=(),
        )
        self.assertEqual(strategy.strategy_id, STRATEGY_GREENFIELD_EMPTY)
        self.assertTrue(strategy.resolved)

    def test_unknown_layout_is_unresolved(self) -> None:
        strategy = synthesize_implementation_strategy(
            user_request="풀스택 구현",
            toplevel_paths=("docs", "README.md", "LICENSE"),
        )
        self.assertEqual(strategy.strategy_id, STRATEGY_UNRESOLVED)
        self.assertFalse(strategy.resolved)
        self.assertEqual(strategy.fallback_reason, "repo_layout_unclassified")
        # placeholder scope 안 내려보냄
        self.assertEqual(strategy.first_slice_scope, ())

    def test_research_only_lifecycle_is_unresolved(self) -> None:
        strategy = synthesize_implementation_strategy(
            user_request="apps/api 어떻게 동작하는지 조사",
            toplevel_paths=("apps",),
            lifecycle_mode="research_only",
        )
        self.assertEqual(strategy.strategy_id, STRATEGY_UNRESOLVED)
        self.assertFalse(strategy.resolved)

    def test_participants_include_opposite_role(self) -> None:
        strategy = synthesize_implementation_strategy(
            user_request="검색 API 구현",
            toplevel_paths=("apps",),
        )
        self.assertIn(ROLE_FRONTEND, strategy.participant_roles)


# ---------------------------------------------------------------------------
# recommend_authorization 가 strategy 우선
# ---------------------------------------------------------------------------


class RecommendAuthorizationStrategyIntegrationTests(unittest.TestCase):
    def test_strategy_resolved_overrides_manifest_write_scope(self) -> None:
        strategy = synthesize_implementation_strategy(
            user_request="네이버 검색형 풀스택 MVP",
            toplevel_paths=("apps", "packages"),
        )
        prop = recommend_authorization(
            user_request="네이버 검색형 풀스택 MVP",
            implementation_strategy=strategy,
        )
        # executor = strategy.first_slice_owner
        self.assertEqual(prop.executor_role, ROLE_BACKEND)
        # write_scope = strategy.first_slice_scope (manifest 무시)
        self.assertIn("apps/api/**", prop.write_scope)
        # placeholder literal 절대 없음
        for path in prop.write_scope:
            self.assertNotIn("<", path)
        # audit
        self.assertEqual(prop.metadata.get("scope_source"), "strategy")
        self.assertEqual(
            prop.metadata.get("implementation_strategy", {}).get("strategy_id"),
            STRATEGY_MONOREPO_APPS,
        )

    def test_strategy_resolved_overrides_keyword_score_zero(self) -> None:
        """prompt 가 keyword scorer 에 매칭 0건이라도 strategy 가 살리면
        backend-engineer 가 executor."""

        strategy = synthesize_implementation_strategy(
            user_request="(prompt-without-any-keyword)",
            toplevel_paths=("apps", "packages"),
            explicit_first_slice_owner=ROLE_BACKEND,
        )
        prop = recommend_authorization(
            user_request="(prompt-without-any-keyword)",
            implementation_strategy=strategy,
        )
        self.assertEqual(prop.executor_role, ROLE_BACKEND)
        self.assertEqual(prop.metadata.get("scope_source"), "strategy")

    def test_strategy_unresolved_falls_back_to_manifest_with_audit(self) -> None:
        strategy = synthesize_implementation_strategy(
            user_request="auth 구현",
            toplevel_paths=("docs",),
        )
        # docs only → unresolved
        self.assertFalse(strategy.resolved)
        prop = recommend_authorization(
            user_request="auth 구현 백엔드 API DB 인증 회원가입",
            implementation_strategy=strategy,
        )
        # manifest fallback 사용 + scope_source 가 unresolved 명시
        self.assertEqual(
            prop.metadata.get("scope_source"), "tech_lead_strategy_unresolved"
        )

    def test_no_strategy_preserves_legacy_keyword_path(self) -> None:
        """strategy=None 일 때 옛 keyword 기반 동작 그대로."""

        prop = recommend_authorization(
            user_request="회원가입 API 구현 — 백엔드 Spring 인증 OAuth2 보안",
        )
        # manifest scope 가 그대로 (placeholder 가 있을 수 있음 — 옛 동작
        # 보존)
        self.assertEqual(prop.metadata.get("scope_source"), "manifest_default")

    def test_strategy_resolved_review_and_participants_strategy_driven(self) -> None:
        strategy = synthesize_implementation_strategy(
            user_request="네이버 검색형 풀스택 MVP",
            toplevel_paths=("apps", "packages"),
        )
        prop = recommend_authorization(
            user_request="네이버 검색형 풀스택 MVP",
            implementation_strategy=strategy,
        )
        # backend first slice → participants 에 frontend + devops
        self.assertIn(ROLE_FRONTEND, prop.participant_roles)


# ---------------------------------------------------------------------------
# downstream write_scope_resolved_empty 회귀 가드
# ---------------------------------------------------------------------------


class StrategyDoesNotProducePlaceholderScopeTests(unittest.TestCase):
    """strategy 가 만든 scope 는 worker 의 write_scope mismatch 검사 (P1-Z4 D)
    를 통과 (실제 apps/api 같은 path 가 worktree 와 매칭) — 가짜 fixture
    수준에서라도 placeholder 가 끼면 안 됨."""

    def test_apps_layout_strategy_scope_has_no_placeholder(self) -> None:
        strategy = synthesize_implementation_strategy(
            user_request="네이버 검색형 풀스택 MVP",
            toplevel_paths=("apps", "packages"),
        )
        for path in strategy.first_slice_scope:
            self.assertNotIn("<", path, f"placeholder literal 남음: {path}")
            self.assertNotIn("<service>", path)

    def test_unresolved_strategy_yields_empty_scope_not_placeholder(self) -> None:
        strategy = synthesize_implementation_strategy(
            user_request="구현",
            toplevel_paths=("docs",),
        )
        self.assertEqual(strategy.first_slice_scope, ())


# ---------------------------------------------------------------------------
# Wiring guards
# ---------------------------------------------------------------------------


class WiringGuardTests(unittest.TestCase):
    def test_authorization_module_imports_strategy(self) -> None:
        import inspect

        from yule_orchestrator.agents.coding import authorization as auth_mod

        source = inspect.getsource(auth_mod.recommend_authorization)
        self.assertIn("implementation_strategy", source)
        self.assertIn("scope_source", source)

    def test_slash_intake_synthesizes_strategy(self) -> None:
        import inspect

        from yule_orchestrator.discord import commands as cmd_mod

        source = inspect.getsource(cmd_mod._ensure_coding_proposal_on_session)
        self.assertIn("synthesize_implementation_strategy", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
