"""Governance regression for :mod:`agents.exploration.repo_map` (Issue #90).

Hard rails the module must keep upholding:

  1. The exploration package never imports a file-system mutator
     (``os.remove`` / ``shutil`` / ``Path.unlink``). Verified by AST.
  2. The scorer only returns numbers and dataclasses — never file
     content. Verified by inspecting public symbols + smoke-testing.
  3. All 7 canonical roles must be present in the default map so a
     downstream consumer never gets a half-empty registry.
  4. An unknown role must return an empty result without raising.
  5. The :class:`RoleRepoProfile` contract surface (the field set) is
     locked so removing a field is a breaking change a reviewer must
     see in this test.
"""

from __future__ import annotations

import ast
import importlib
import unittest
from dataclasses import fields
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.exploration import repo_map as repo_map_mod
from yule_engineering.agents.exploration.repo_map import (
    ALL_ROLE_IDS,
    RepoMap,
    RoleRepoProfile,
    ScoredFile,
    build_default_repo_map,
    build_repo_map,
    rank_files_for_task,
    score_file_relevance,
)


# Method names that only make sense as filesystem mutators — flagging
# any call to these inside the exploration package is a hard rail.
# Note: "replace" is intentionally NOT here. ``str.replace`` is used
# for path normalisation (``"\\" → "/"``), so the bare attr name alone
# is too coarse a signal.
_FORBIDDEN_CALL_PATTERNS = (
    "remove",  # os.remove
    "unlink",  # Path.unlink / os.unlink
    "rmdir",
    "rmtree",  # shutil.rmtree
    "mkdir",  # Path.mkdir / os.mkdir
    "rename",  # os.rename / Path.rename
    "write_text",  # Path.write_text
    "write_bytes",  # Path.write_bytes
    "chmod",
    "symlink_to",
)

_FORBIDDEN_IMPORTS = ("shutil",)


def _iter_exploration_sources() -> list[Path]:
    pkg_root = Path(repo_map_mod.__file__).resolve().parent
    return sorted(pkg_root.rglob("*.py"))


class ReadOnlyStaticAnalysisTests(unittest.TestCase):
    """The exploration package must not call file-system mutators."""

    def test_no_forbidden_imports(self) -> None:
        for src_path in _iter_exploration_sources():
            tree = ast.parse(src_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        with self.subTest(file=src_path.name, name=alias.name):
                            self.assertNotIn(
                                alias.name,
                                _FORBIDDEN_IMPORTS,
                                f"{src_path.name} must not import "
                                f"{alias.name}",
                            )
                elif isinstance(node, ast.ImportFrom):
                    with self.subTest(file=src_path.name, name=node.module):
                        self.assertNotIn(
                            node.module or "",
                            _FORBIDDEN_IMPORTS,
                            f"{src_path.name} must not import from "
                            f"{node.module}",
                        )

    def test_no_forbidden_call_attributes(self) -> None:
        for src_path in _iter_exploration_sources():
            tree = ast.parse(src_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(
                    node.func, ast.Attribute
                ):
                    name = node.func.attr
                    with self.subTest(file=src_path.name, attr=name):
                        self.assertNotIn(
                            name,
                            _FORBIDDEN_CALL_PATTERNS,
                            f"{src_path.name} calls .{name}() — forbidden "
                            f"by repo-map read-only rail",
                        )

    def test_no_open_for_write_calls(self) -> None:
        # Defensive: even a plain `open(path, "w")` shouldn't appear in
        # this read-only module. Catch any `open(...)` call.
        for src_path in _iter_exploration_sources():
            tree = ast.parse(src_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(
                    node.func, ast.Name
                ):
                    self.assertNotEqual(
                        node.func.id,
                        "open",
                        f"{src_path.name} calls open() — forbidden",
                    )


class ScoreShapeTests(unittest.TestCase):
    """The scorer must only return numeric / dataclass surfaces."""

    def test_score_file_relevance_returns_float(self) -> None:
        repo_map = build_repo_map(Path("/tmp/repo"))
        score = score_file_relevance(
            repo_map,
            path="apps/engineering-agent/src/yule_engineering/agents/job_queue/store.py",
            role="backend-engineer",
        )
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_rank_files_for_task_returns_only_scored_files(self) -> None:
        repo_map = build_repo_map(Path("/tmp/repo"))
        ranked = rank_files_for_task(
            repo_map,
            role="backend-engineer",
            candidates=[
                "apps/engineering-agent/src/yule_engineering/agents/job_queue/store.py",
                "docs/not-mine.md",
            ],
        )
        for entry in ranked:
            self.assertIsInstance(entry, ScoredFile)
            # No content fields — only path + score + match reasons.
            self.assertFalse(
                hasattr(entry, "content"),
                "ScoredFile must not carry file content",
            )

    def test_scored_file_fields_are_locked(self) -> None:
        # Locking the field set so any new field gets reviewer eyes.
        expected = {"path", "role", "score", "matched_prefix", "matched_keyword"}
        actual = {f.name for f in fields(ScoredFile)}
        self.assertEqual(actual, expected)


class RoleCoverageTests(unittest.TestCase):
    def test_default_map_covers_all_seven_roles(self) -> None:
        repo_map = build_default_repo_map()
        self.assertEqual(set(repo_map.roles), set(ALL_ROLE_IDS))
        self.assertEqual(len(repo_map.profiles), 7)

    def test_role_repo_profile_field_set_is_locked(self) -> None:
        expected = {
            "role",
            "preferred_prefixes",
            "hot_files",
            "risky_files",
            "test_glob",
            "docs_glob",
        }
        actual = {f.name for f in fields(RoleRepoProfile)}
        self.assertEqual(actual, expected)


class UnknownRoleSafetyTests(unittest.TestCase):
    def test_unknown_role_score_is_zero_not_keyerror(self) -> None:
        repo_map = build_default_repo_map()
        # Must not raise — repo-map is consumed by best-effort hint code.
        score = score_file_relevance(
            repo_map,
            path="apps/engineering-agent/src/yule_engineering/agents/job_queue/store.py",
            role="phantom-role",
        )
        self.assertEqual(score, 0.0)

    def test_unknown_role_rank_returns_empty_tuple(self) -> None:
        repo_map = build_default_repo_map()
        ranked = rank_files_for_task(
            repo_map,
            role="phantom-role",
            candidates=[
                "apps/engineering-agent/src/yule_engineering/agents/job_queue/store.py",
            ],
        )
        self.assertEqual(ranked, ())

    def test_repo_map_profile_for_unknown_role_returns_none(self) -> None:
        repo_map = build_default_repo_map()
        self.assertIsNone(repo_map.profile_for("phantom-role"))
        self.assertIsNone(repo_map.profile_for(""))


class ModuleReimportTests(unittest.TestCase):
    """The module must import cleanly without filesystem side effects."""

    def test_import_module_has_expected_public_surface(self) -> None:
        # Importing :mod:`agents.exploration.repo_map` must succeed and
        # expose the public symbols downstream code (context_pack
        # CodeHintProvider) depends on. Listing them here makes a
        # rename a reviewer-visible event.
        mod = importlib.import_module(
            "yule_engineering.agents.exploration.repo_map"
        )
        for name in (
            "RepoMap",
            "RoleRepoProfile",
            "ScoredFile",
            "build_repo_map",
            "build_default_repo_map",
            "score_file_relevance",
            "rank_files_for_task",
            "ALL_ROLE_IDS",
        ):
            with self.subTest(symbol=name):
                self.assertTrue(
                    hasattr(mod, name),
                    f"public symbol {name} disappeared",
                )

    def test_package_exposes_repo_map_surface(self) -> None:
        # The package __init__ re-exports the public surface so
        # consumers can do ``from agents.exploration import RepoMap``.
        pkg = importlib.import_module("yule_engineering.agents.exploration")
        for name in (
            "RepoMap",
            "RoleRepoProfile",
            "ScoredFile",
            "build_repo_map",
            "build_default_repo_map",
            "score_file_relevance",
            "rank_files_for_task",
        ):
            with self.subTest(symbol=name):
                self.assertTrue(hasattr(pkg, name))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
