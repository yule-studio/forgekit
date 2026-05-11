"""Regression suite for :mod:`agents.exploration.repo_map` (Issue #90).

Covers the 7-role catalog, scorer composition, ranking stability,
keyword tokenisation, and safe handling of empty / unknown inputs.
"""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.exploration.repo_map import (
    ALL_ROLE_IDS,
    HOT_FILE_WEIGHT,
    KEYWORD_OVERLAP_BONUS,
    PREFIX_WEIGHT,
    RISKY_FILE_WEIGHT,
    ROLE_AI_ENGINEER,
    ROLE_BACKEND_ENGINEER,
    ROLE_DEVOPS_ENGINEER,
    ROLE_FRONTEND_ENGINEER,
    ROLE_PRODUCT_DESIGNER,
    ROLE_QA_ENGINEER,
    ROLE_TECH_LEAD,
    RepoMap,
    RoleRepoProfile,
    ScoredFile,
    build_default_repo_map,
    build_repo_map,
    rank_files_for_task,
    score_file_relevance,
)


def _map() -> RepoMap:
    return build_repo_map(Path("/tmp/fake-repo"))


class RoleCatalogTests(unittest.TestCase):
    """Each of the 7 canonical roles must appear in the registry."""

    def test_all_seven_roles_registered(self) -> None:
        repo_map = _map()
        self.assertEqual(set(repo_map.roles), set(ALL_ROLE_IDS))
        self.assertEqual(len(repo_map.roles), 7)

    def test_role_ids_are_canonical_kebab_case(self) -> None:
        expected = {
            ROLE_BACKEND_ENGINEER,
            ROLE_FRONTEND_ENGINEER,
            ROLE_QA_ENGINEER,
            ROLE_DEVOPS_ENGINEER,
            ROLE_TECH_LEAD,
            ROLE_AI_ENGINEER,
            ROLE_PRODUCT_DESIGNER,
        }
        self.assertEqual(set(ALL_ROLE_IDS), expected)

    def test_backend_engineer_prefixes_cover_agents_and_storage(self) -> None:
        profile = _map().profile_for(ROLE_BACKEND_ENGINEER)
        self.assertIsNotNone(profile)
        assert profile is not None  # mypy / type narrowing
        self.assertIn("src/yule_orchestrator/agents", profile.preferred_prefixes)
        self.assertIn("src/yule_orchestrator/storage", profile.preferred_prefixes)
        self.assertIn(
            "src/yule_orchestrator/integrations", profile.preferred_prefixes
        )
        self.assertIn(
            "src/yule_orchestrator/github_workos", profile.preferred_prefixes
        )

    def test_frontend_engineer_is_reserved_empty_slot(self) -> None:
        # No UI package yet — profile must still exist but carry no
        # entries so downstream code doesn't have to special-case absence.
        profile = _map().profile_for(ROLE_FRONTEND_ENGINEER)
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.preferred_prefixes, ())
        self.assertEqual(profile.hot_files, ())
        self.assertEqual(profile.test_glob, ())

    def test_qa_engineer_anchors_tests(self) -> None:
        profile = _map().profile_for(ROLE_QA_ENGINEER)
        assert profile is not None
        self.assertIn("tests", profile.preferred_prefixes)
        self.assertIn("tests/engineering", profile.preferred_prefixes)
        self.assertIn("tests/**", profile.test_glob)

    def test_devops_engineer_anchors_runtime_and_workflows(self) -> None:
        profile = _map().profile_for(ROLE_DEVOPS_ENGINEER)
        assert profile is not None
        self.assertIn("src/yule_orchestrator/runtime", profile.preferred_prefixes)
        self.assertIn(".github/workflows", profile.preferred_prefixes)
        # supervisor / services / CI workflow are explicit risky entries.
        self.assertIn(
            "src/yule_orchestrator/runtime/subprocess_supervisor.py",
            profile.risky_files,
        )
        self.assertIn(
            "src/yule_orchestrator/runtime/services.py", profile.risky_files
        )
        self.assertIn(".github/workflows/ci.yml", profile.risky_files)

    def test_tech_lead_spans_repo_and_governance(self) -> None:
        profile = _map().profile_for(ROLE_TECH_LEAD)
        assert profile is not None
        self.assertIn("src", profile.preferred_prefixes)
        self.assertIn("tests", profile.preferred_prefixes)
        self.assertIn("CLAUDE.md", profile.hot_files)
        self.assertIn(
            "policies/runtime/agents/engineering-agent/governance.md",
            profile.hot_files,
        )

    def test_ai_engineer_anchors_decision_and_runners(self) -> None:
        profile = _map().profile_for(ROLE_AI_ENGINEER)
        assert profile is not None
        self.assertIn(
            "src/yule_orchestrator/agents/decision", profile.preferred_prefixes
        )
        self.assertIn(
            "src/yule_orchestrator/agents/runners", profile.preferred_prefixes
        )
        self.assertIn(
            "src/yule_orchestrator/agents/decision/classifier_factory.py",
            profile.hot_files,
        )

    def test_product_designer_anchors_docs_and_vault(self) -> None:
        profile = _map().profile_for(ROLE_PRODUCT_DESIGNER)
        assert profile is not None
        self.assertIn("docs", profile.preferred_prefixes)
        self.assertIn("notes/vault-mirror", profile.preferred_prefixes)
        self.assertIn(
            "notes/vault-mirror/10-projects/yule-studio-agent/decisions/**",
            profile.docs_glob,
        )


class ScoreCompositionTests(unittest.TestCase):
    def test_hot_file_match_yields_hot_weight(self) -> None:
        score = score_file_relevance(
            _map(),
            path="src/yule_orchestrator/agents/job_queue/store.py",
            role=ROLE_BACKEND_ENGINEER,
        )
        self.assertAlmostEqual(score, HOT_FILE_WEIGHT)

    def test_prefix_only_match_yields_prefix_weight(self) -> None:
        score = score_file_relevance(
            _map(),
            path="src/yule_orchestrator/agents/coding/foo.py",
            role=ROLE_BACKEND_ENGINEER,
        )
        self.assertAlmostEqual(score, PREFIX_WEIGHT)

    def test_risky_file_match_yields_risky_weight(self) -> None:
        score = score_file_relevance(
            _map(),
            path="src/yule_orchestrator/runtime/subprocess_supervisor.py",
            role=ROLE_DEVOPS_ENGINEER,
        )
        # subprocess_supervisor is both hot AND risky — hot wins.
        self.assertAlmostEqual(score, HOT_FILE_WEIGHT)

    def test_risky_only_match_yields_risky_weight(self) -> None:
        # Backend's risky_files include the storage directory as
        # a directory entry, while preferred_prefixes also list
        # storage — risky wins (higher weight, more specific signal).
        score = score_file_relevance(
            _map(),
            path="src/yule_orchestrator/storage/db.py",
            role=ROLE_BACKEND_ENGINEER,
        )
        self.assertAlmostEqual(score, RISKY_FILE_WEIGHT)

    def test_keyword_overlap_bumps_score(self) -> None:
        score = score_file_relevance(
            _map(),
            path="src/yule_orchestrator/agents/coding/coding_job.py",
            role=ROLE_BACKEND_ENGINEER,
            task_keywords=("coding", "job"),
        )
        self.assertAlmostEqual(score, PREFIX_WEIGHT + KEYWORD_OVERLAP_BONUS)

    def test_score_cap_does_not_exceed_one(self) -> None:
        # hot (0.8) + keyword bonus (0.2) = 1.0 — must equal cap exactly.
        score = score_file_relevance(
            _map(),
            path="src/yule_orchestrator/agents/job_queue/store.py",
            role=ROLE_BACKEND_ENGINEER,
            task_keywords=("store",),
        )
        self.assertAlmostEqual(score, 1.0)

    def test_no_match_returns_zero(self) -> None:
        score = score_file_relevance(
            _map(),
            path="docs/random.md",
            role=ROLE_BACKEND_ENGINEER,
        )
        self.assertEqual(score, 0.0)

    def test_unknown_role_returns_zero_safely(self) -> None:
        score = score_file_relevance(
            _map(),
            path="src/yule_orchestrator/agents/job_queue/store.py",
            role="totally-not-a-role",
        )
        self.assertEqual(score, 0.0)

    def test_empty_path_returns_zero(self) -> None:
        score = score_file_relevance(
            _map(), path="", role=ROLE_BACKEND_ENGINEER
        )
        self.assertEqual(score, 0.0)

    def test_keyword_overlap_does_not_apply_without_base_match(self) -> None:
        # No prefix / hot / risky match → still 0.0 even when keyword
        # tokens overlap. We do not surface zero-base files just because
        # their name happens to share a token.
        score = score_file_relevance(
            _map(),
            path="totally/unrelated/path/store.md",
            role=ROLE_BACKEND_ENGINEER,
            task_keywords=("store",),
        )
        self.assertEqual(score, 0.0)

    def test_fully_qualified_role_id_resolves(self) -> None:
        # The selector sometimes passes "engineering-agent/<role>" —
        # the scorer must accept it without normalisation upstream.
        score = score_file_relevance(
            _map(),
            path="src/yule_orchestrator/agents/job_queue/store.py",
            role="engineering-agent/backend-engineer",
        )
        self.assertAlmostEqual(score, HOT_FILE_WEIGHT)


class RoleMatrixTests(unittest.TestCase):
    """Role × path matrix sanity — each role hits its turf, misses others."""

    CASES = (
        (ROLE_BACKEND_ENGINEER, "src/yule_orchestrator/agents/workflow.py"),
        (
            ROLE_QA_ENGINEER,
            "tests/agents/test_coding_executor_worker.py",
        ),
        (
            ROLE_DEVOPS_ENGINEER,
            "src/yule_orchestrator/runtime/services.py",
        ),
        (ROLE_TECH_LEAD, "CLAUDE.md"),
        (
            ROLE_AI_ENGINEER,
            "src/yule_orchestrator/agents/decision/router.py",
        ),
        (ROLE_PRODUCT_DESIGNER, "docs/feature-x.md"),
    )

    def test_each_role_scores_its_own_turf_above_zero(self) -> None:
        repo_map = _map()
        for role, path in self.CASES:
            with self.subTest(role=role, path=path):
                self.assertGreater(
                    score_file_relevance(repo_map, path=path, role=role),
                    0.0,
                    f"{role} should claim {path}",
                )

    def test_unrelated_role_scores_zero_for_other_turf(self) -> None:
        repo_map = _map()
        # backend-engineer should not light up for product-designer docs
        self.assertEqual(
            score_file_relevance(
                repo_map,
                path="notes/vault-mirror/10-projects/yule-studio-agent/decisions/some.md",
                role=ROLE_BACKEND_ENGINEER,
            ),
            0.0,
        )
        # qa-engineer should not light up for runtime supervisor
        self.assertEqual(
            score_file_relevance(
                repo_map,
                path="src/yule_orchestrator/runtime/subprocess_supervisor.py",
                role=ROLE_QA_ENGINEER,
            ),
            0.0,
        )

    def test_frontend_engineer_yields_zero_everywhere(self) -> None:
        # Reserved empty slot — no path should score until UI lands.
        repo_map = _map()
        for _role, path in self.CASES:
            with self.subTest(path=path):
                self.assertEqual(
                    score_file_relevance(
                        repo_map, path=path, role=ROLE_FRONTEND_ENGINEER
                    ),
                    0.0,
                )


class RankingTests(unittest.TestCase):
    def test_rank_orders_by_score_descending(self) -> None:
        ranked = rank_files_for_task(
            _map(),
            role=ROLE_BACKEND_ENGINEER,
            task_keywords=("store",),
            candidates=[
                "src/yule_orchestrator/agents/job_queue/store.py",  # hot + kw
                "src/yule_orchestrator/storage/db.py",  # risky
                "src/yule_orchestrator/agents/workflow.py",  # hot
                "src/yule_orchestrator/agents/coding/foo.py",  # prefix
                "notes/vault-mirror/foo.md",  # zero — must be dropped
            ],
        )

        self.assertEqual(len(ranked), 4)  # zero entry dropped
        self.assertEqual(
            ranked[0].path,
            "src/yule_orchestrator/agents/job_queue/store.py",
        )
        # ordering by score (desc)
        scores = [sf.score for sf in ranked]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_rank_drops_zero_scored_entries(self) -> None:
        ranked = rank_files_for_task(
            _map(),
            role=ROLE_BACKEND_ENGINEER,
            candidates=["docs/foo.md", "notes/bar.md"],
        )
        self.assertEqual(ranked, ())

    def test_rank_with_empty_candidates_returns_empty_tuple(self) -> None:
        ranked = rank_files_for_task(
            _map(),
            role=ROLE_BACKEND_ENGINEER,
            candidates=(),
        )
        self.assertEqual(ranked, ())

    def test_rank_with_unknown_role_returns_empty_tuple(self) -> None:
        ranked = rank_files_for_task(
            _map(),
            role="not-a-role",
            candidates=[
                "src/yule_orchestrator/agents/job_queue/store.py",
            ],
        )
        self.assertEqual(ranked, ())

    def test_rank_is_stable_on_tie_scores(self) -> None:
        # Two prefix-only matches, same score → tie broken by path order.
        ranked = rank_files_for_task(
            _map(),
            role=ROLE_BACKEND_ENGINEER,
            candidates=[
                "src/yule_orchestrator/integrations/zeta.py",
                "src/yule_orchestrator/integrations/alpha.py",
                "src/yule_orchestrator/integrations/mu.py",
            ],
        )
        self.assertEqual(
            [sf.path for sf in ranked],
            [
                "src/yule_orchestrator/integrations/alpha.py",
                "src/yule_orchestrator/integrations/mu.py",
                "src/yule_orchestrator/integrations/zeta.py",
            ],
        )

    def test_rank_records_matched_prefix_and_keyword(self) -> None:
        ranked = rank_files_for_task(
            _map(),
            role=ROLE_BACKEND_ENGINEER,
            task_keywords=("workflow",),
            candidates=["src/yule_orchestrator/agents/workflow.py"],
        )
        self.assertEqual(len(ranked), 1)
        sf = ranked[0]
        self.assertIsInstance(sf, ScoredFile)
        self.assertEqual(
            sf.matched_prefix, "src/yule_orchestrator/agents/workflow.py"
        )
        self.assertEqual(sf.matched_keyword, "workflow")

    def test_rank_accepts_path_objects(self) -> None:
        # Candidates can be Path or str; the scorer normalises both.
        ranked = rank_files_for_task(
            _map(),
            role=ROLE_BACKEND_ENGINEER,
            candidates=[
                Path("src/yule_orchestrator/agents/job_queue/store.py"),
                Path("./src/yule_orchestrator/agents/workflow.py"),
            ],
        )
        self.assertEqual(len(ranked), 2)
        for sf in ranked:
            self.assertFalse(sf.path.startswith("./"))


class TokenisationTests(unittest.TestCase):
    """Keyword tokeniser handles whitespace / camelCase / snake_case."""

    def test_keyword_overlap_picks_camel_case_tokens(self) -> None:
        score = score_file_relevance(
            _map(),
            path="src/yule_orchestrator/agents/coding/foo.py",
            role=ROLE_BACKEND_ENGINEER,
            task_keywords=("codingExecutorWorker",),
        )
        # camelCase keyword should tokenise to 'coding', which overlaps
        # the path token 'coding' → bonus applies.
        self.assertAlmostEqual(score, PREFIX_WEIGHT + KEYWORD_OVERLAP_BONUS)

    def test_keyword_overlap_picks_snake_case_tokens(self) -> None:
        score = score_file_relevance(
            _map(),
            path="src/yule_orchestrator/agents/coding/foo.py",
            role=ROLE_BACKEND_ENGINEER,
            task_keywords=("coding_executor_worker",),
        )
        self.assertAlmostEqual(score, PREFIX_WEIGHT + KEYWORD_OVERLAP_BONUS)

    def test_keyword_overlap_picks_whitespace_separated_tokens(self) -> None:
        score = score_file_relevance(
            _map(),
            path="src/yule_orchestrator/agents/coding/foo.py",
            role=ROLE_BACKEND_ENGINEER,
            task_keywords=("coding executor worker",),
        )
        self.assertAlmostEqual(score, PREFIX_WEIGHT + KEYWORD_OVERLAP_BONUS)

    def test_single_char_tokens_are_ignored(self) -> None:
        # "a" by itself in the keyword should not trigger overlap with
        # any path. Otherwise the bonus would fire on almost every path.
        score = score_file_relevance(
            _map(),
            path="src/yule_orchestrator/agents/coding/foo.py",
            role=ROLE_BACKEND_ENGINEER,
            task_keywords=("a", "b"),
        )
        self.assertAlmostEqual(score, PREFIX_WEIGHT)  # no bonus


class PrefixBoundaryTests(unittest.TestCase):
    def test_prefix_respects_directory_boundary(self) -> None:
        # "src/yule_orchestrator/agents" must NOT match
        # "src/yule_orchestrator/agents_data/foo.py" (no boundary slash).
        score = score_file_relevance(
            _map(),
            path="src/yule_orchestrator/agents_data/foo.py",
            role=ROLE_BACKEND_ENGINEER,
        )
        self.assertEqual(score, 0.0)

    def test_prefix_matches_exact_directory(self) -> None:
        # Equality with prefix itself counts as a match.
        score = score_file_relevance(
            _map(),
            path="tests",
            role=ROLE_QA_ENGINEER,
        )
        self.assertAlmostEqual(score, PREFIX_WEIGHT)


class DefaultRepoMapHelperTests(unittest.TestCase):
    def test_build_default_repo_map_resolves_to_existing_dir(self) -> None:
        repo_map = build_default_repo_map()
        self.assertTrue(repo_map.repo_root.exists())
        self.assertEqual(len(repo_map.profiles), 7)

    def test_build_repo_map_records_repo_root_unchanged(self) -> None:
        repo_map = build_repo_map(Path("/tmp/x/y/z"))
        self.assertEqual(repo_map.repo_root, Path("/tmp/x/y/z"))


class DataclassImmutabilityTests(unittest.TestCase):
    def test_role_repo_profile_is_frozen(self) -> None:
        profile = RoleRepoProfile(role="x")
        with self.assertRaises(Exception):
            profile.role = "y"  # type: ignore[misc]

    def test_repo_map_is_frozen(self) -> None:
        repo_map = _map()
        with self.assertRaises(Exception):
            repo_map.repo_root = Path("/")  # type: ignore[misc]

    def test_scored_file_is_frozen(self) -> None:
        sf = ScoredFile(path="x", role="y", score=0.5)
        with self.assertRaises(Exception):
            sf.score = 0.9  # type: ignore[misc]


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
