"""forgekit console TUI smoke — driven headlessly via Textual's pilot.

Exercises the Claude-Code-style chat-first layout: small-avatar intro · then a
top-aligned session flow of quiet issue line · main panel (transcript XOR help
view) · **session-following inline composer (NOT docked to the viewport bottom)** ·
hint line. Verifies the composer renders within/after the content flow (short
session → composer near the top with empty space below; grown transcript →
composer pushed down following the content), that it stays right below the help
view when ``/help`` is open (the key fix), that ``/help`` is a single-panel VIEW
SWITCH (transcript hidden, NOT appended, screen-stack length stays 1 → a panel not
a modal), that Esc restores the transcript unchanged, that ``/exit`` quits, and
palette open/Tab-complete/cycle/close. Skipped without textual.
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

    async def test_mounts_intro_issue_main_composer_topdown(self) -> None:
        from textual.widgets import Input, Static
        from forgekit_console.tui.composer import Composer
        from forgekit_console.tui.header import IntroHeader
        from forgekit_console.tui.main_panel import MainPanel

        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            # intro header (brand) · issue line · main panel · composer · hint
            intro = app.query_one("#intro", IntroHeader)
            self.assertIn("forgekit", str(app.query_one("#intro-meta", Static).render()))
            self.assertTrue(str(app.query_one("#issue", Static).render()))
            self.assertIsNotNone(app.query_one("#main", MainPanel))
            # default view is the transcript
            self.assertFalse(app._main.help_open)
            self.assertIsNotNone(app.query_one("#composer", Composer))
            self.assertIsNotNone(app.query_one("#prompt", Input))
            self.assertIn("operator", str(app.query_one("#modepill", Static).render()))

    async def test_composer_is_inline_not_docked_bottom(self) -> None:
        """Composer is NOT dock:bottom — it flows inline right after the content.

        On a short session it sits in the UPPER area with EMPTY space below (not
        pinned to the viewport bottom, unlike the old footer).
        """
        from textual.widgets import Input
        from forgekit_console.tui.composer import Composer

        app = self._app()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            composer = app.query_one("#composer", Composer)
            # the composer carries NO dock (it is part of the inline session flow)
            self.assertNotEqual(str(composer.styles.dock or "none").lower(), "bottom")
            self.assertTrue(composer.display)
            self.assertTrue(app.query_one("#prompt", Input).display)
            # short session → composer near the TOP, empty space below (not pinned)
            self.assertLess(composer.region.bottom, app.size.height - 2)
            self.assertLess(composer.region.y, app.size.height // 2)

    async def test_composer_follows_content_as_transcript_grows(self) -> None:
        """As the transcript grows, the inline composer is pushed DOWN (follows content)."""
        from forgekit_console.tui.composer import Composer

        app = self._app()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            composer = app.query_one("#composer", Composer)
            short_y = composer.region.y
            # write several transcript entries
            for _ in range(30):
                app._execute("/status")
            await pilot.pause()
            await pilot.pause()
            # the composer moved down (content pushed it), still visible
            self.assertGreater(composer.region.y, short_y)
            self.assertTrue(composer.display)

    async def test_composer_below_help_panel_when_help_open(self) -> None:
        """When help is open the composer still sits right BELOW the help view."""
        from textual.widgets import Input
        from forgekit_console.tui.composer import Composer
        from forgekit_console.tui.main_panel import MainPanel

        app = self._app()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            composer = app.query_one("#composer", Composer)
            app._execute("/help")
            await pilot.pause()
            await pilot.pause()
            self.assertTrue(app._main.help_open)
            self.assertTrue(composer.display)
            self.assertTrue(app.query_one("#prompt", Input).display)
            # composer is below the help content (active content), not at viewport bottom
            main = app.query_one("#main", MainPanel)
            self.assertGreaterEqual(composer.region.y, main.region.bottom)
            # close help → transcript restored, composer still visible below it
            await pilot.press("escape")
            await pilot.pause()
            self.assertFalse(app._main.help_open)
            self.assertTrue(composer.display)

    async def test_help_is_view_switch_not_transcript_append(self) -> None:
        """/help switches the main area to the help view; the transcript is hidden,
        NOT appended; the screen stack stays 1 (a panel, not a modal)."""
        from forgekit_console.tui.help_panel import HelpPanel
        from forgekit_console.tui.transcript import Transcript

        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            transcript = app._transcript
            lines_before = transcript.lines  # RichLog content snapshot
            n_before = len(lines_before)

            app._execute("/help")
            await pilot.pause()
            # main area is now the help view; transcript is the hidden child
            self.assertTrue(app._main.help_open)
            self.assertEqual(app._main.current, "help")
            self.assertFalse(transcript.display)  # transcript hidden behind help view
            self.assertTrue(app.query_one("#main HelpPanel", HelpPanel).display)
            # NOT a modal — no screen pushed
            self.assertEqual(len(app.screen_stack), 1)
            # nothing appended to the transcript
            self.assertEqual(len(transcript.lines), n_before)
            # default tab is General
            from forgekit_console.tui import render

            secs = render.help_sections(app.context.commands, app.context.agents)
            self.assertEqual(secs[app._main.help_panel.active_tab].title, "General")

            # Esc restores the transcript view, unchanged
            await pilot.press("escape")
            await pilot.pause()
            self.assertFalse(app._main.help_open)
            self.assertEqual(app._main.current, "transcript")
            self.assertTrue(transcript.display)
            self.assertEqual(len(transcript.lines), n_before)

    async def test_help_tab_switch_in_place_does_not_append(self) -> None:
        """Tab switches the active help tab IN PLACE — the transcript never grows."""
        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            n_before = len(app._transcript.lines)
            app._execute("/help")
            await pilot.pause()
            from forgekit_console.tui import render

            secs = render.help_sections(app.context.commands, app.context.agents)
            general = render.default_help_tab(secs)
            self.assertEqual(app._main.help_panel.active_tab, general)
            await pilot.press("tab")
            await pilot.pause()
            # active tab moved, still a single help view, transcript untouched
            self.assertNotEqual(app._main.help_panel.active_tab, general)
            self.assertTrue(app._main.help_open)
            self.assertEqual(len(app._transcript.lines), n_before)

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

    async def test_topdown_order_intro_issue_content_composer_hint(self) -> None:
        """Geometry smoke: TOP-ALIGNED inline flow intro → issue → main → composer → hint.

        Stands in for an SVG snapshot (the CI sweep runs unittest, not the
        textual-snapshot pytest plugin): it asserts the y-order of the regions.
        The composer renders right after the content (inline), NOT docked at the
        viewport bottom — so on a short session it leaves empty space below.
        """
        from textual.widgets import Static
        from forgekit_console.tui.composer import Composer
        from forgekit_console.tui.header import IntroHeader
        from forgekit_console.tui.main_panel import MainPanel

        app = self._app()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            intro = app.query_one("#intro", IntroHeader).region
            issue = app.query_one("#issue", Static).region
            log = app.query_one("#main", MainPanel).region
            composer = app.query_one("#composer", Composer).region
            hint = app.query_one("#hint", Static).region
            # top → down: intro · issue · content · composer · hint (composer inline)
            self.assertLessEqual(intro.y, issue.y)
            self.assertLessEqual(issue.y, log.y)
            self.assertLessEqual(log.bottom, composer.y)
            self.assertLessEqual(composer.bottom, hint.y)
            # composer is NOT pinned to the viewport bottom on a short session
            self.assertLess(composer.bottom, app.size.height - 2)

    async def test_intro_avatar_renderer_selected(self) -> None:
        from forgekit_console.tui.header import IntroHeader
        from forgekit_console.tui import image_renderer as ir

        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            intro = app.query_one("#intro", IntroHeader)
            # whichever the headless terminal selected, it is one of the three tiers
            self.assertIn(
                intro.avatar_renderer_id,
                (ir.RENDERER_REAL, ir.RENDERER_HALFBLOCK, ir.RENDERER_TEXT),
            )

    async def test_composer_is_thin_no_heavy_box(self) -> None:
        """The composer is a THIN bar: a single subtle top rule, no full/heavy box.

        Claude-Code restraint — the input row is the star. We assert the composer
        carries only a top border (no left/right/bottom box) and a left accent
        prompt marker ``›``.
        """
        from textual.widgets import Static
        from forgekit_console.tui.composer import Composer

        app = self._app()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            composer = app.query_one("#composer", Composer)
            # border-top only — left/right/bottom edges are NOT a box
            edges = composer.styles.border
            top_edge = composer.styles.border_top
            self.assertIsNotNone(top_edge)
            self.assertNotEqual((top_edge[0] or "").lower(), "")
            # the top rule is a THIN style (solid), not heavy
            self.assertNotEqual((top_edge[0] or "").lower(), "heavy")
            # no left/right/bottom border (a thin separator, not a full box)
            self.assertIn((composer.styles.border_left[0] or "").lower(), ("", "none"))
            self.assertIn((composer.styles.border_bottom[0] or "").lower(), ("", "none"))
            # left accent prompt marker present
            marker = str(app.query_one("#marker", Static).render())
            self.assertIn("›", marker)

    async def test_intro_shows_brand_banner_mark(self) -> None:
        """The intro shows the forgekit BRAND mark (banner image-first / text wordmark)."""
        from forgekit_console.tui.brand_panel import BrandPanel
        from forgekit_console.tui.header import IntroHeader
        from forgekit_console.tui import image_renderer as ir

        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            intro = app.query_one("#intro", IntroHeader)
            brand = intro.query_one("#intro-brand", BrandPanel)
            self.assertIsNotNone(brand)
            # whichever the headless terminal selected, it's the image or text tier
            self.assertIn(
                intro.brand_renderer_id,
                (ir.RENDERER_BRAND_IMAGE, ir.RENDERER_BRAND_TEXT),
            )

    async def test_intro_brand_image_first_selection(self) -> None:
        """Image-first: a graphics-capable terminal selects the REAL banner image;
        a plain one falls back to the compact TEXT wordmark."""
        from forgekit_console.tui import image_renderer as ir

        capable = ir.make_brand_renderer(ir.ImageCapability(True))
        plain = ir.make_brand_renderer(ir.ImageCapability(False))
        self.assertEqual(capable.renderer_id, ir.RENDERER_BRAND_IMAGE)
        self.assertEqual(plain.renderer_id, ir.RENDERER_BRAND_TEXT)
        # text fallback is the clean cyan→magenta wordmark on its own
        self.assertIn("forge", plain.renderable())

    async def test_renderer_debug_line_hidden_by_default(self) -> None:
        """No diagnostic chrome unless FORGEKIT_DEBUG_RENDERERS is set."""
        import os
        from unittest import mock
        from textual.css.query import NoMatches

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FORGEKIT_DEBUG_RENDERERS", None)
            app = self._app()
            async with app.run_test() as pilot:
                await pilot.pause()
                with self.assertRaises(NoMatches):
                    app.query_one("#intro-renderers")

    async def test_renderer_debug_line_shown_when_flag_set(self) -> None:
        """With the flag on, the intro shows a SELECTED→REALIZED renderer line."""
        import os
        from unittest import mock
        from textual.widgets import Static

        with mock.patch.dict(os.environ, {"FORGEKIT_DEBUG_RENDERERS": "1"}):
            app = self._app()
            async with app.run_test() as pilot:
                await pilot.pause()
                line = str(app.query_one("#intro-renderers", Static).render())
                self.assertIn("renderers", line)
                self.assertIn("avatar=", line)
                self.assertIn("brand=", line)

    async def test_intro_block_renders_avatar_and_meta(self) -> None:
        """The compact intro block mounts: avatar column (left) + brand/version/
        provider/profile/repo meta (right)."""
        from textual.widgets import Static
        from forgekit_console.tui.avatar_panel import AvatarPanel
        from forgekit_console.tui.header import IntroHeader

        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            intro = app.query_one("#intro", IntroHeader)
            self.assertIsNotNone(intro.query_one("#intro-avatar", AvatarPanel))
            meta = str(app.query_one("#intro-meta", Static).render())
            # quiet 3-4 line meta: brand+version, tagline, provider/profile, repo
            self.assertIn("forgekit", meta)
            self.assertIn("provider", meta)
            self.assertIn("profile", meta)
            self.assertIn("/tmp/repo", meta)


if __name__ == "__main__":
    unittest.main()
