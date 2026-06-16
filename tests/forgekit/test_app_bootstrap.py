"""forgekit console entrypoint + bootstrap smoke."""

from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import __version__
from forgekit_console.app.main import (
    EXIT_MISSING_TUI,
    EXIT_OK,
    main,
    resolve_repo_root,
)
from forgekit_console.tui import render

_TEXTUAL_INSTALLED = importlib.util.find_spec("textual") is not None


class EntrypointTests(unittest.TestCase):
    def test_version(self) -> None:
        self.assertEqual(main(["--version"]), EXIT_OK)

    def test_resolve_repo_root_explicit(self) -> None:
        self.assertEqual(resolve_repo_root("/tmp"), Path("/tmp").resolve())

    def test_resolve_repo_root_env(self) -> None:
        prev = os.environ.get("YULE_REPO_ROOT")
        os.environ["YULE_REPO_ROOT"] = "/tmp"
        try:
            self.assertEqual(resolve_repo_root(), Path("/tmp").resolve())
        finally:
            if prev is None:
                os.environ.pop("YULE_REPO_ROOT", None)
            else:
                os.environ["YULE_REPO_ROOT"] = prev

    @unittest.skipIf(_TEXTUAL_INSTALLED, "textual installed — would launch a live TUI")
    def test_console_without_textual_degrades(self) -> None:
        # No textual → friendly install hint + EXIT_MISSING_TUI, never a traceback.
        self.assertEqual(main(["console", "--repo-root", "/tmp"]), EXIT_MISSING_TUI)


class RenderHelperTests(unittest.TestCase):
    def test_welcome_banner_has_brand_and_quick_commands(self) -> None:
        lines = render.welcome_banner("/tmp/repo", "operator")
        joined = "\n".join(lines)
        self.assertIn("forgekit", joined)
        self.assertIn("/help", joined)
        self.assertIn("/quit", joined)

    def test_agent_pane_lines(self) -> None:
        from forgekit_console.commands.registry import load_agents

        lines = "\n".join(render.agent_pane_lines(load_agents()))
        self.assertIn("agents", lines)
        self.assertIn("Engineering", lines)


class AppModuleImportTests(unittest.TestCase):
    @unittest.skipUnless(_TEXTUAL_INSTALLED, "textual not installed")
    def test_app_class_constructs(self) -> None:
        from forgekit_console.tui.app import ForgekitConsoleApp

        app = ForgekitConsoleApp(repo_root=Path("/tmp"))
        self.assertIsNotNone(app.context)


if __name__ == "__main__":
    unittest.main()
