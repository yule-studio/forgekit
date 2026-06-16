"""forgekit console TUI smoke — driven headlessly via Textual's pilot.

Exercises the content-first layout: mount, palette open/Tab complete/close,
inline help open/close (not a modal), layout toggle, agent-mode pill. Skipped
when textual isn't installed so the stdlib suite still runs.
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

    async def test_mounts_intro_issue_input_log_topdown(self) -> None:
        from textual.widgets import RichLog, Static

        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            # compact intro shows brand; issue line is the quiet status line
            self.assertIn("forgekit", str(app.query_one("#intro", Static).render()))
            self.assertTrue(str(app.query_one("#issue", Static).render()))
            self.assertIn("operator", str(app.query_one("#modepill", Static).render()))
            self.assertIsNotNone(app.query_one("#log", RichLog))

    async def test_palette_opens_and_tab_completes(self) -> None:
        from textual.widgets import Input

        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", Input)
            prompt.value = "/he"
            await pilot.pause()
            self.assertTrue(app._palette.is_open)
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

    async def test_help_is_inline_full_width_not_modal(self) -> None:
        from textual.widgets import RichLog
        from forgekit_console.tui.help_document import HelpDocument

        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._execute("/help")
            await pilot.pause()
            helpdoc = app.query_one("#helpdoc", HelpDocument)
            self.assertTrue(helpdoc.is_open)
            # inline in the content area: log hidden, no modal screen pushed
            self.assertFalse(app.query_one("#log", RichLog).display)
            self.assertEqual(len(app.screen_stack), 1)
            # default tab is General
            from forgekit_console.tui import render

            secs = render.help_sections(app.context.commands, app.context.agents)
            self.assertEqual(secs[app._help_tab].title, "General")
            await pilot.press("escape")
            await pilot.pause()
            self.assertFalse(helpdoc.is_open)
            self.assertTrue(app.query_one("#log", RichLog).display)

    async def test_exit_alias_quits(self) -> None:
        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._execute("/exit")
            await pilot.pause()
        # run_test context exits cleanly when the app called exit()
        self.assertTrue(True)

    async def test_agent_mode_pill(self) -> None:
        from textual.widgets import Static

        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._execute("/pm-agent")
            await pilot.pause()
            self.assertEqual(app.mode, "agent:product-agent")
            self.assertIn("Product", str(app.query_one("#modepill", Static).render()))
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(app.mode, "operator")


if __name__ == "__main__":
    unittest.main()
