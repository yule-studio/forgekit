"""TWT4 merge-prep guard — locks the packages/* topology into CI.

Pure / CI-safe (no textual). Enforces the structural invariants established by the
ForgeKit package-topology cleanup so they cannot silently regress:

1. **packages → apps hard rail**: no module under ``packages/*/src`` may import an app
   (``yule_engineering`` / ``forgekit_console`` / ``apps.*``) — with ONE allow-listed,
   documented best-effort lazy bridge (forgekit-runtime lifecycle → yule_engineering
   troubleshooting ledger). Any NEW package→app edge fails here.
2. **topology doc ↔ tree**: every real ``packages/<name>`` appears in
   ``docs/package-topology.md`` (no stale / missing classification).
3. **every package builds standalone**: each has its own ``pyproject.toml``.

This is the keystone that protects the WT/TWT extraction work from drift.
"""

from __future__ import annotations

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
PKGS = REPO / "packages"

# The only sanctioned packages→apps edge: forgekit-runtime mirrors failures into the
# heavy engineering-agent troubleshooting ledger as a best-effort, lazy, try/excepted
# call that degrades to a no-op when the app is absent. Documented in
# docs/package-topology.md §7 and docs/forgekit-architecture-ownership.md. To be
# replaced by an agent-contracts event (WT4 of the ownership doc).
_ALLOWED_APP_EDGE = "forgekit-runtime/src/forgekit_runtime/lifecycle/failure_escalation.py"

_APP_IMPORT = re.compile(r"^\s*(?:from|import)\s+(yule_engineering|forgekit_console|apps\.)")


class PackagesToAppsRailTests(unittest.TestCase):
    def test_no_unsanctioned_package_imports_an_app(self) -> None:
        # Production code only — a package's own tests/ legitimately import old
        # ``yule_engineering.X`` paths to verify the back-compat shims (object identity)
        # from the engineering-agent monolith extraction; that is not a runtime edge.
        offenders = []
        for py in PKGS.glob("*/src/**/*.py"):
            if "__pycache__" in str(py):
                continue
            rel = py.relative_to(REPO).as_posix().replace("packages/", "", 1)
            text = py.read_text(encoding="utf-8", errors="ignore")
            if any(_APP_IMPORT.match(line) for line in text.splitlines()):
                if rel != _ALLOWED_APP_EDGE:
                    offenders.append(rel)
        self.assertEqual(
            offenders, [],
            f"packages/* must not import apps/* (hard rail). New offenders: {offenders}. "
            f"Use a seam/injection or agent-contracts event instead.",
        )

    def test_the_one_allowed_edge_still_exists_and_is_lazy(self) -> None:
        # guard the allow-list itself: if the bridge is removed/cleaned, tighten this test.
        f = PKGS / _ALLOWED_APP_EDGE
        self.assertTrue(f.exists(), "allow-listed bridge file moved — update the guard")
        text = f.read_text(encoding="utf-8")
        # the app import must be inside a function (indented), not module top-level
        top_level = [l for l in text.splitlines()
                     if l.startswith("from yule_engineering") or l.startswith("import yule_engineering")]
        self.assertEqual(top_level, [], "the bridge must stay lazy (in-function), never top-level")


class TopologyDocConsistencyTests(unittest.TestCase):
    def test_every_package_is_classified_in_the_topology_doc(self) -> None:
        doc = (REPO / "docs" / "package-topology.md").read_text(encoding="utf-8")
        real = sorted(p.name for p in PKGS.iterdir()
                      if p.is_dir() and (p / "src").exists())
        missing = [name for name in real if f"`{name}`" not in doc]
        self.assertEqual(
            missing, [],
            f"docs/package-topology.md is missing these packages: {missing}",
        )

    def test_every_package_has_its_own_pyproject(self) -> None:
        missing = [p.name for p in PKGS.iterdir()
                   if p.is_dir() and (p / "src").exists() and not (p / "pyproject.toml").exists()]
        self.assertEqual(missing, [], f"packages without pyproject.toml (standalone-unbuildable): {missing}")


class ForgekitCoreImportSmokeTests(unittest.TestCase):
    def test_core_packages_import_standalone(self) -> None:
        import importlib

        for mod in ("forgekit_config", "forgekit_contracts", "forgekit_provider",
                    "forgekit_runtime", "hephaistos", "nexus"):
            self.assertTrue(importlib.import_module(mod))


if __name__ == "__main__":
    unittest.main()
