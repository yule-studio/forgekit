"""forgekit console TUI smoke — driven headlessly via Textual's pilot.

Exercises the real widget wiring (mount, palette open/close, Tab autocomplete,
help overlay, agent-mode badge). Skipped entirely when textual isn't installed so
the stdlib suite still runs.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

_TEXTUAL = importlib.util.find_spec("textual") is not None


def _fake_context():
    from forgekit_console.commands.registry import load_agents, load_commands
    from forgekit_console.commands.router import ConsoleContext
    from forgekit_console.models import StatusSummary, StatusSection

    summary = StatusSummary(
        title="operator dashboard",
        sections=(StatusSection("provider runtime", ("live runs: 1 / 2",)),),
    )
    return ConsoleContext(
        repo_root=Path("/tmp/repo"),
        agents=load_agents(),
        commands=load_commands(),
        load_operator=lambda: summary,
        load_runtime=lambda: summary,
        load_doctor=lambda: summary,
    )


@unittest.skipUnless(_TEXTUAL, "textual not installed")
class TuiSmokeTests(unittest.IsolatedAsyncioTestCase):
    def _app(self):
        from forgekit_console.tui.app import ForgekitConsoleApp

        return ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=_fake_context())

    async def test_mounts_with_operator_badge_and_agents(self) -> None:
        from textual.widgets import Static

        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            badge = app.query_one("#modebadge", Static)
            self.assertIn("OPERATOR", str(badge.render()))
            agents = app.query_one("#agents", Static)
            self.assertIn("Engineering", str(agents.render()))

    async def test_palette_opens_and_tab_completes(self) -> None:
        from textual.widgets import Input

        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", Input)
            prompt.value = "/he"
            await pilot.pause()
            self.assertTrue(app._palette.is_open)
            self.assertEqual([c.name for c in app._palette.matches], ["help"])
            await pilot.press("tab")
            await pilot.pause()
            self.assertEqual(prompt.value, "/help ")

    async def test_palette_cycles_multiple(self) -> None:
        from textual.widgets import Input

        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", Input)
            prompt.value = "/p"
            await pilot.pause()
            await pilot.press("tab")
            await pilot.pause()
            self.assertEqual(prompt.value, "/pm-agent ")
            await pilot.press("tab")
            await pilot.pause()
            self.assertEqual(prompt.value, "/planning-agent ")

    async def test_escape_closes_palette(self) -> None:
        from textual.widgets import Input

        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#prompt", Input).value = "/st"
            await pilot.pause()
            self.assertTrue(app._palette.is_open)
            await pilot.press("escape")
            await pilot.pause()
            self.assertFalse(app._palette.is_open)

    async def test_help_overlay_opens_and_dismisses(self) -> None:
        from forgekit_console.tui.help_view import HelpScreen

        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._execute("/help")
            await pilot.pause()
            self.assertIsInstance(app.screen, HelpScreen)
            await pilot.press("escape")
            await pilot.pause()
            self.assertNotIsInstance(app.screen, HelpScreen)

    async def test_agent_mode_sets_badge(self) -> None:
        from textual.widgets import Static

        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._execute("/pm-agent")
            await pilot.pause()
            self.assertEqual(app.mode, "agent:product-agent")
            self.assertIn("AGENT", str(app.query_one("#modebadge", Static).render()))
            # Esc returns to operator mode
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(app.mode, "operator")


if __name__ == "__main__":
    unittest.main()
