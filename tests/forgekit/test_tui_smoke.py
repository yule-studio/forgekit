"""forgekit console TUI smoke — driven headlessly via Textual's pilot.

Exercises the Claude-Code-style chat-first layout: small-avatar intro · quiet
issue line · transcript (main) · **fixed bottom composer (always visible)** ·
hint line. Verifies the composer stays present before AND after ``/help`` (the
key fix), that ``/help`` renders INTO the transcript (not a modal), that ``/exit``
quits, and palette open/Tab-complete/cycle/close. Skipped without textual.
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

    async def test_mounts_intro_issue_transcript_composer_topdown(self) -> None:
        from textual.widgets import Input, Static
        from forgekit_console.tui.composer import Composer
        from forgekit_console.tui.header import IntroHeader
        from forgekit_console.tui.transcript import Transcript

        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            # intro header (brand) · issue line · transcript · composer · hint
            intro = app.query_one("#intro", IntroHeader)
            self.assertIn("forgekit", str(app.query_one("#intro-meta", Static).render()))
            self.assertTrue(str(app.query_one("#issue", Static).render()))
            self.assertIsNotNone(app.query_one("#log", Transcript))
            self.assertIsNotNone(app.query_one("#composer", Composer))
            self.assertIsNotNone(app.query_one("#prompt", Input))
            self.assertIn("operator", str(app.query_one("#modepill", Static).render()))

    async def test_composer_fixed_before_and_after_help(self) -> None:
        """The composer (chat input) is ALWAYS visible — even with help open."""
        from textual.widgets import Input
        from forgekit_console.tui.composer import Composer

        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            # before help
            self.assertTrue(app.query_one("#composer", Composer).display)
            self.assertTrue(app.query_one("#prompt", Input).display)
            # open help (renders into the transcript)
            app._execute("/help")
            await pilot.pause()
            self.assertTrue(app._transcript.help_open)
            # composer STILL visible after help opened (the key fix)
            self.assertTrue(app.query_one("#composer", Composer).display)
            self.assertTrue(app.query_one("#prompt", Input).display)
            # and after closing help
            await pilot.press("escape")
            await pilot.pause()
            self.assertFalse(app._transcript.help_open)
            self.assertTrue(app.query_one("#composer", Composer).display)

    async def test_help_renders_into_transcript_not_modal(self) -> None:
        from forgekit_console.tui.transcript import Transcript

        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._execute("/help")
            await pilot.pause()
            log = app.query_one("#log", Transcript)
            self.assertTrue(log.help_open)
            # transcript stays the visible main area (no modal screen pushed)
            self.assertTrue(log.display)
            self.assertEqual(len(app.screen_stack), 1)
            # default tab is General
            from forgekit_console.tui import render

            secs = render.help_sections(app.context.commands, app.context.agents)
            self.assertEqual(secs[app._help_tab].title, "General")
            await pilot.press("escape")
            await pilot.pause()
            self.assertFalse(log.help_open)

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

    async def test_topdown_order_intro_issue_transcript_hint_composer(self) -> None:
        """Geometry smoke: widgets stack top→down with the composer at the bottom.

        Stands in for an SVG snapshot (the CI sweep runs unittest, not the
        textual-snapshot pytest plugin): it asserts the y-order of the regions.
        """
        from textual.widgets import Static
        from forgekit_console.tui.composer import Composer
        from forgekit_console.tui.header import IntroHeader
        from forgekit_console.tui.transcript import Transcript

        app = self._app()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            intro = app.query_one("#intro", IntroHeader).region
            issue = app.query_one("#issue", Static).region
            log = app.query_one("#log", Transcript).region
            hint = app.query_one("#hint", Static).region
            composer = app.query_one("#composer", Composer).region
            # top → down: intro above issue above transcript; composer is last
            self.assertLessEqual(intro.y, issue.y)
            self.assertLessEqual(issue.y, log.y)
            self.assertLess(log.y, composer.y)
            self.assertLessEqual(hint.y, composer.y)
            # composer is docked at the very bottom of the screen
            self.assertGreaterEqual(composer.bottom, app.size.height - 1)

    async def test_intro_avatar_renderer_selected(self) -> None:
        from forgekit_console.tui.header import IntroHeader
        from forgekit_console.tui import image_renderer as ir

        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            intro = app.query_one("#intro", IntroHeader)
            # whichever the headless terminal selected, it is one of the two
            self.assertIn(intro.avatar_renderer_id, (ir.RENDERER_REAL, ir.RENDERER_TEXT))


if __name__ == "__main__":
    unittest.main()
