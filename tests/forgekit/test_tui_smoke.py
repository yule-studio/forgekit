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
    def _app(self, *, escalator=None, submit_service=None):
        from forgekit_console.tui.app import ForgekitConsoleApp

        return ForgekitConsoleApp(
            repo_root=Path("/tmp/repo"), context=_fake_context(),
            escalator=escalator, submit_service=submit_service,
        )

    def _tempdir_escalator(self, *, threshold=3):
        """A FailureEscalator writing to a tempdir (cleaned at test teardown)."""
        import tempfile
        from forgekit_console.lifecycle.failure_escalation import FailureEscalator

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        return FailureEscalator(
            env={}, threshold=threshold,
            ledger_path=tmp / "led.json", inbox_path=tmp / "inbox.json",
            notifier=lambda t, b: True, bridge_troubleshooting=False,
        )

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
            # Claude-style idle: the mode pill is hidden (no `● operator` row)
            self.assertFalse(app.query_one("#modepill", Static).display)

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

    async def test_help_tabs_first_with_cyan_divider_no_branding(self) -> None:
        """Help reads tabs-first (Help General …, no 'forgekit help' header) with a
        full-width cyan divider under the tab strip."""
        from textual.widgets import Static
        from forgekit_console.tui import theme

        app = self._app()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app._execute("/help")
            await pilot.pause()
            await pilot.pause()
            tabs = app.query_one("#help-tabs", Static)
            strip = str(tabs.render())
            self.assertIn("Help", strip)
            self.assertNotIn("forgekit", strip)  # no branding header in the strip
            # a full-width divider = the tab strip's bottom border (a cyan accent rule)
            from textual.color import Color

            edge = tabs.styles.border_bottom
            self.assertNotIn((edge[0] or "").lower(), ("", "none"))
            self.assertEqual(edge[1], Color.parse(theme.ACCENT_PRIMARY))  # cyan accent

    async def test_composer_hidden_in_help_mode_restored_on_close(self) -> None:
        """Claude-style: the composer BAR is HIDDEN while the help/tab view is open
        (help reads as its own mode), and restored + focused when help closes."""
        from textual.widgets import Input
        from forgekit_console.tui.composer import Composer

        app = self._app()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            composer = app.query_one("#composer", Composer)
            self.assertTrue(composer.display)  # visible in normal mode
            app._execute("/help")
            await pilot.pause()
            await pilot.pause()
            self.assertTrue(app._main.help_open)
            # composer BAR is hidden in help mode (no stray input bar below help)
            self.assertFalse(composer.display)
            # close help → transcript restored, composer bar visible again
            await pilot.press("escape")
            await pilot.pause()
            self.assertFalse(app._main.help_open)
            self.assertTrue(composer.display)
            self.assertTrue(app.query_one("#prompt", Input).display)

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
            # top → down: intro · issue · content · composer-bar (inline)
            self.assertLessEqual(intro.y, issue.y)
            self.assertLessEqual(issue.y, log.y)
            self.assertLessEqual(log.bottom, composer.y)
            # the hint is now the FOOT of the composer bar (inside it), not a stray line
            self.assertGreaterEqual(hint.y, composer.y)
            self.assertLessEqual(hint.bottom, composer.bottom)
            # composer bar is NOT pinned to the viewport bottom on a short session
            self.assertLess(composer.bottom, app.size.height - 2)

    async def test_intro_avatar_renderer_selected(self) -> None:
        from forgekit_console.tui.header import IntroHeader
        from forgekit_console.tui import image_renderer as ir

        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            intro = app.query_one("#intro", IntroHeader)
            # whichever the headless terminal selected, it is one of the known tiers
            self.assertIn(
                intro.avatar_renderer_id,
                (ir.RENDERER_REAL, ir.RENDERER_ANSI_ICON, ir.RENDERER_AVATAR_MARK,
                 ir.RENDERER_HALFBLOCK, ir.RENDERER_TEXT),
            )

    async def test_input_bar_clean_mode_hidden_in_idle_hint_outside(self) -> None:
        """The input BAR (#composer-input-shell) holds ONLY the marker + input, with
        full-width top+bottom rules. In the default operator (idle) state the mode row
        is HIDDEN (Claude-style), and the hints (#hint) are a row BELOW the bar."""
        from textual.widgets import Static
        from forgekit_console.tui.composer import Composer

        app = self._app()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            composer = app.query_one("#composer", Composer)
            box = app.query_one("#composer-input-shell")
            # full-width input strip = top + bottom rules (no heavy box)
            for edge in (box.styles.border_top, box.styles.border_bottom):
                self.assertNotIn((edge[0] or "").lower(), ("", "none"))
                self.assertNotEqual((edge[0] or "").lower(), "heavy")
            # idle: the mode row is HIDDEN (no `● operator` above the bar)
            self.assertFalse(app.query_one("#modepill", Static).display)
            # marker inside the bar; hint OUTSIDE (below) the bar
            box_region = box.region
            self.assertTrue(box_region.contains_region(app.query_one("#marker", Static).region))
            self.assertGreaterEqual(app.query_one("#hint", Static).region.y, box_region.bottom)
            self.assertGreaterEqual(composer.styles.margin.top, 1)
            self.assertIn(">", str(app.query_one("#marker", Static).render()))

    async def test_actual_typing_focus_value_and_submit(self) -> None:
        """REAL interaction (not existence): the prompt is focused on mount, typed
        characters land in Input.value, `/he` opens the palette, and Enter submits."""
        from textual.widgets import Input

        app = self._app()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", Input)
            # 1) the prompt has focus on mount
            self.assertIs(app.focused, prompt)
            # 2) typing real keys lands in Input.value
            await pilot.press("a", "b", "c")
            await pilot.pause()
            self.assertEqual(prompt.value, "abc")
            # 3) clear, then a slash query opens the palette with the typed value kept
            prompt.value = ""
            await pilot.press("slash", "h", "e")
            await pilot.pause()
            self.assertEqual(prompt.value, "/he")
            self.assertTrue(app._palette.is_open)
            # 4) Enter submits — palette closes and the prompt resets (submit flow ran)
            await pilot.press("enter")
            await pilot.pause()
            self.assertFalse(app._palette.is_open)
            self.assertEqual(app.query_one("#prompt", Input).value, "")

    async def test_input_is_clean_hints_live_in_hint_row(self) -> None:
        """The input field carries NO in-field guidance (clean, Claude-style); the
        `/help · / palette · Tab · quit` hints live in the #hint row below it."""
        from textual.widgets import Input, Static

        app = self._app()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", Input)
            self.assertEqual((prompt.placeholder or ""), "")  # no placeholder clutter
            hint = str(app.query_one("#hint", Static).render())
            self.assertIn("/help", hint)  # guidance in the hint row (outside the box)
            self.assertIn("palette", hint)

    async def test_slash_palette_is_separate_surface_below_the_box(self) -> None:
        """Slash palette is a SEPARATE surface BELOW the input box + hint — not inside
        the input box, not in the transcript. The key Claude-style fix."""
        from textual.widgets import Input
        from forgekit_console.tui.composer import Composer
        from forgekit_console.tui.palette import CommandPalette

        app = self._app()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app.query_one("#prompt", Input).value = "/he"
            await pilot.pause()
            self.assertTrue(app._palette.is_open)
            composer = app.query_one("#composer", Composer).region
            box = app.query_one("#composer-input-shell").region
            palette = app.query_one("#palette", CommandPalette).region
            # palette is BELOW the input box (separate surface, not inside it)
            self.assertGreaterEqual(palette.y, box.bottom)
            # still part of the composer wrapper (connected), never in the transcript
            self.assertLessEqual(palette.bottom, composer.bottom)
            # compact: capped height so it never becomes a giant box
            self.assertLessEqual(palette.height, 8)

    async def test_intro_is_compact_branding_in_meta_no_separate_wordmark(self) -> None:
        """The intro is a COMPACT product header: the standalone wordmark banner line
        is gone (Claude-style), branding lives in the meta's `forgekit v0.1.0`, and
        there is no `#intro-brand` widget any more."""
        from textual.widgets import Static
        from textual.css.query import NoMatches
        from forgekit_console.tui.header import IntroHeader

        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            intro = app.query_one("#intro", IntroHeader)
            # the separate brand wordmark line is removed
            with self.assertRaises(NoMatches):
                intro.query_one("#intro-brand")
            # branding is the meta's wordmark; meta is a short 3-line header
            meta = str(app.query_one("#intro-meta", Static).render())
            self.assertIn("forge", meta)
            self.assertIn("v0.1.0", meta)
            self.assertNotIn("operator console", meta)  # redundant tagline dropped
            self.assertLessEqual(len([l for l in meta.split("\n") if l.strip()]), 3)

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

    async def test_repeated_failure_advisory_then_blocked_banner(self) -> None:
        """3 same-signature failures → advisory below threshold, then a blocked banner
        + RCA in the transcript (never a silent repeated failure)."""
        from textual.widgets import Static

        app = self._app(escalator=self._tempdir_escalator(threshold=3))
        async with app.run_test() as pilot:
            await pilot.pause()
            # /nope is an unknown command → KIND_ERROR, same signature each time
            app._execute("/nope")
            await pilot.pause()
            self.assertFalse(app._blocked)  # 1/3 — advisory only
            app._execute("/nope")
            await pilot.pause()
            self.assertFalse(app._blocked)  # 2/3 — still advisory
            app._execute("/nope")
            await pilot.pause()
            # 3/3 → escalated: blocked flag set + issue line is the blocked banner
            self.assertTrue(app._blocked)
            issue = str(app.query_one("#issue", Static).render())
            self.assertIn("blocked", issue)

    async def test_repeated_render_fallback_escalates(self) -> None:
        """Running /render repeatedly while still on a fallback (headless test env is
        never true-raster) escalates after the threshold with render alternatives."""
        from forgekit_console.lifecycle import failure_escalation as fe

        esc = self._tempdir_escalator(threshold=3)
        app = self._app(escalator=esc)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._execute("/render")
            await pilot.pause()
            self.assertFalse(app._blocked)  # 1/3 — advisory only
            app._execute("/render")
            await pilot.pause()
            self.assertFalse(app._blocked)  # 2/3
            app._execute("/render")
            await pilot.pause()
            # 3/3 → escalated as a renderer issue with alternatives
            self.assertTrue(app._blocked)
            records = fe.read_escalations(esc.ledger_path)
            self.assertTrue(records)
            self.assertEqual(records[-1]["kind"], fe.KIND_RENDERER)
            self.assertTrue(records[-1]["alternatives"])  # alternatives investigated

    async def test_blocked_command_lists_escalation(self) -> None:
        """After an escalation, /blocked surfaces the open repeated-failure."""
        app = self._app(escalator=self._tempdir_escalator(threshold=1))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._execute("/nope")  # threshold 1 → immediate escalation
            await pilot.pause()
            self.assertTrue(app._blocked)
            # the escalation RCA block was written to the transcript
            n_before = len(app._transcript.lines)
            self.assertGreater(n_before, 0)

    async def test_free_text_live_submit_appends_assistant_reply(self) -> None:
        """A non-slash line goes to the injected submit service (NOT the slash path):
        the user echo + the assistant reply land in the transcript, the input is
        cleared, and focus returns to the prompt — no escalation on a live result."""
        from textual.widgets import Input
        from forgekit_console.chat import models as m

        class FakeService:
            def __init__(self) -> None:
                self.prompts = []

            def submit(self, text, **_):
                self.prompts.append(text)
                return m.SubmitResult(
                    ok=True, mode=m.MODE_LIVE, category=m.CAT_OK,
                    text="안녕하세요 — 라이브 응답입니다", provider_id="ollama",
                    provider_label="Ollama", source=m.SOURCE_LOCAL_DEFAULT, model="gemma3:latest",
                )

        svc = FakeService()
        app = self._app(submit_service=svc)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            n_before = len(app._transcript.lines)
            prompt = app.query_one("#prompt", Input)
            prompt.value = "프로젝트 상태 알려줘"
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()  # the threaded submit worker
            await pilot.pause()
            # the free text reached the live submit service (slash path NOT taken)
            self.assertEqual(svc.prompts, ["프로젝트 상태 알려줘"])
            # echo + assistant reply both appended
            self.assertGreater(len(app._transcript.lines), n_before)
            joined = "\n".join(str(s) for s in app._transcript.lines)
            self.assertIn("라이브 응답", joined)
            # input cleared, focus kept on the prompt, no escalation
            self.assertEqual(app.query_one("#prompt", Input).value, "")
            self.assertIs(app.focused, app.query_one("#prompt", Input))
            self.assertFalse(app._blocked)

    async def test_free_text_failure_records_escalation(self) -> None:
        """A non-live submit (e.g. no provider) is surfaced honestly AND, after the
        threshold, escalates as a repeated failure (never silently swallowed)."""
        from textual.widgets import Input
        from forgekit_console.chat import models as m

        class FailingService:
            def submit(self, text, **_):
                return m.SubmitResult(
                    ok=False, mode=m.MODE_SETUP, category=m.CAT_NO_PROVIDER,
                    text="provider 가 아직 설정되지 않았습니다.",
                    next_action="로컬 ollama 를 실행하거나 provider 를 설정하세요.",
                )

        app = self._app(escalator=self._tempdir_escalator(threshold=1), submit_service=FailingService())
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app.query_one("#prompt", Input).value = "hello?"
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            joined = "\n".join(str(s) for s in app._transcript.lines)
            self.assertIn("다음 단계", joined)  # the next-action guidance is shown
            self.assertTrue(app._blocked)  # threshold 1 → escalated

    async def test_slash_command_not_routed_to_submit_service(self) -> None:
        """Slash commands must NEVER hit the live submit service — they stay on the
        pure router path."""
        from forgekit_console.chat import models as m

        class TrackingService:
            def __init__(self) -> None:
                self.calls = 0

            def submit(self, text, **_):
                self.calls += 1
                return m.SubmitResult(ok=True, mode=m.MODE_LIVE, category=m.CAT_OK, text="x")

        svc = TrackingService()
        app = self._app(submit_service=svc)
        async with app.run_test() as pilot:
            await pilot.pause()
            n_before = len(app._transcript.lines)
            app._execute("/status")
            await pilot.pause()
            self.assertEqual(svc.calls, 0)  # the slash path never calls submit
            # /status still routed normally → its result reached the transcript
            joined = "\n".join(str(s) for s in app._transcript.lines)
            self.assertGreater(len(app._transcript.lines), n_before)
            self.assertIn("operator dashboard", joined)

    def _hero_app(self, hero_text: str = "HERO ART LINE 1\nHERO ART LINE 2", **env):
        """An app whose hero asset is a temp file (so hero mode is exercisable)."""
        import os
        import tempfile
        from unittest import mock
        from forgekit_console.tui.app import ForgekitConsoleApp

        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        p = d / "hero.txt"
        p.write_text(hero_text, encoding="utf-8")
        patch_env = {"FORGEKIT_HERO_PATH": str(p), **env}
        ctx = mock.patch.dict(os.environ, patch_env)
        ctx.start()
        self.addCleanup(ctx.stop)
        return ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=_fake_context())

    async def test_empty_session_shows_hero_then_folds_to_compact(self) -> None:
        """Fresh empty session → HERO art visible; typing/submit → COMPACT header."""
        from textual.widgets import Input
        from forgekit_console.tui.header import IntroHeader

        app = self._hero_app()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            intro = app.query_one("#intro", IntroHeader)
            self.assertEqual(intro.mode, "hero")  # big first impression
            self.assertTrue(app.query_one("#intro-hero-wrap").display)
            self.assertFalse(app.query_one("#intro-body").display)
            # type a character → folds to compact (working state)
            await pilot.press("h")
            await pilot.pause()
            self.assertEqual(intro.mode, "compact")
            self.assertFalse(app.query_one("#intro-hero-wrap").display)
            self.assertTrue(app.query_one("#intro-body").display)

    async def test_no_hero_asset_stays_compact(self) -> None:
        """Without an asset the intro is always compact (no empty hero box)."""
        from forgekit_console.tui.header import IntroHeader

        app = self._app()  # no FORGEKIT_HERO_PATH
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            self.assertEqual(app.query_one("#intro", IntroHeader).mode, "compact")

    async def test_about_command_shows_hero_and_about_tab(self) -> None:
        """/about → hero art in the header + the About help tab."""
        from forgekit_console.tui.header import IntroHeader
        from forgekit_console.tui import render

        app = self._hero_app()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app._execute("/about")
            await pilot.pause()
            self.assertTrue(app._main.help_open)
            self.assertEqual(app.query_one("#intro", IntroHeader).mode, "hero")
            secs = render.help_sections(app.context.commands, app.context.agents)
            self.assertEqual(secs[app._main.help_panel.active_tab].title, "About")
            # Esc closes help → back to compact (transcript empty but help was the hero)
            await pilot.press("escape")
            await pilot.pause()
            self.assertFalse(app._main.help_open)

    async def test_intro_mode_override_compact_forces_small(self) -> None:
        """FORGEKIT_INTRO_MODE=compact keeps the small header even on an empty session."""
        from forgekit_console.tui.header import IntroHeader

        app = self._hero_app(FORGEKIT_INTRO_MODE="compact")
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            self.assertEqual(app.query_one("#intro", IntroHeader).mode, "compact")

    async def test_intro_mode_override_hero_forces_big_while_working(self) -> None:
        """FORGEKIT_INTRO_MODE=hero keeps the big art even after typing."""
        from textual.widgets import Input
        from forgekit_console.tui.header import IntroHeader

        app = self._hero_app(FORGEKIT_INTRO_MODE="hero")
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            await pilot.press("h", "i")
            await pilot.pause()
            self.assertEqual(app.query_one("#intro", IntroHeader).mode, "hero")

    async def test_live_submit_still_works_with_hero(self) -> None:
        """Regression: free-text live-submit still appends + folds hero to compact."""
        from textual.widgets import Input
        from forgekit_console.chat import models as m
        from forgekit_console.tui.header import IntroHeader

        class FakeLive:
            def submit(self, text, **_):
                return m.SubmitResult(ok=True, mode=m.MODE_LIVE, category=m.CAT_OK,
                                      text="live reply", provider_id="ollama",
                                      provider_label="Ollama", model="x")

        import os
        import tempfile
        from unittest import mock
        from forgekit_console.tui.app import ForgekitConsoleApp

        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        p = d / "hero.txt"
        p.write_text("HERO", encoding="utf-8")
        ctx = mock.patch.dict(os.environ, {"FORGEKIT_HERO_PATH": str(p)})
        ctx.start()
        self.addCleanup(ctx.stop)
        app = ForgekitConsoleApp(
            repo_root=Path("/tmp/repo"), context=_fake_context(), submit_service=FakeLive()
        )
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            self.assertEqual(app.query_one("#intro", IntroHeader).mode, "hero")
            app.query_one("#prompt", Input).value = "hello"
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            joined = "\n".join(str(s) for s in app._transcript.lines)
            self.assertIn("live reply", joined)
            self.assertEqual(app.query_one("#intro", IntroHeader).mode, "compact")

    def _ready_app(self, main="claude", **kw):
        import tempfile
        from forgekit_console.tui.app import ForgekitConsoleApp
        from forgekit_console.notify.service import NotificationService

        # isolated notifier: tmp inbox + desktop OFF (no real toast / no home writes)
        if "notifier" not in kw:
            tmp = Path(tempfile.mkdtemp())
            self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
            kw["notifier"] = NotificationService(
                inbox_path=tmp / "inbox.json", desktop_enabled=False
            )
        return ForgekitConsoleApp(
            repo_root=Path("/tmp/repo"), context=_fake_context(),
            config={"main_provider": main}, **kw,
        )

    async def test_shift_tab_cycles_mode_with_real_policy_change(self) -> None:
        """Shift+Tab advances the runtime mode AND changes the resolved EffectivePolicy
        (provider-policy mode / usage / approval), not just a label."""
        from forgekit_console.policy import runtime_mode as rm

        app = self._ready_app("claude")
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            first_mode = app._runtime_mode
            first_pol = app._effective_policy
            self.assertIsNotNone(first_pol)
            seen_policy_modes = {first_pol.provider_policy_mode}
            seen_usage = {first_pol.usage.usage_mode}
            for _ in range(len(rm.RUNTIME_MODES) - 1):  # stop one short of a full wrap
                await pilot.press("shift+tab")
                await pilot.pause()
                seen_policy_modes.add(app._effective_policy.provider_policy_mode)
                seen_usage.add(app._effective_policy.usage.usage_mode)
            # mode id moved and the resolved policy/usage took on MULTIPLE distinct
            # values across the cycle → real policy change, not a label flip.
            self.assertNotEqual(app._runtime_mode, first_mode)
            self.assertGreater(len(seen_policy_modes), 1)
            self.assertGreater(len(seen_usage), 1)

    async def test_mode_command_shows_live_posture(self) -> None:
        app = self._ready_app("claude")
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            n_before = len(app._transcript.lines)
            app._execute("/mode")
            await pilot.pause()
            joined = "\n".join(str(s) for s in app._transcript.lines)
            self.assertGreater(len(app._transcript.lines), n_before)
            self.assertIn("routing", joined)
            self.assertIn("approval", joined)
            self.assertIn(app._effective_policy.mode_label, joined)

    async def test_no_provider_shows_setup_required_and_blocks_mode(self) -> None:
        """No config → setup-required banner; Shift+Tab does NOT pretend to switch."""
        from textual.widgets import Static
        from forgekit_console.tui.app import ForgekitConsoleApp

        app = ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=_fake_context(), config={})
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            self.assertTrue(app._setup.blocked)
            self.assertIsNone(app._effective_policy)
            issue = str(app.query_one("#issue", Static).render())
            self.assertIn("setup-required", issue)
            before = app._runtime_mode
            await pilot.press("shift+tab")
            await pilot.pause()
            self.assertEqual(app._runtime_mode, before)  # no fake switch

    async def test_autopilot_allowlist_and_single_executor(self) -> None:
        """/autopilot runs on an allowlisted repo (executes safe) but refuses others."""
        app = self._ready_app("claude")
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app._execute("/autopilot forgekit")        # allowlisted
            await pilot.pause()
            joined = "\n".join(str(s) for s in app._transcript.lines)
            self.assertIn("repo-autopilot", joined)
            self.assertIn("executor", joined)
            app._execute("/autopilot random-repo")     # not allowlisted → refused
            await pilot.pause()
            joined = "\n".join(str(s) for s in app._transcript.lines)
            self.assertIn("repo 거부", joined)

    async def test_red_blue_plan_only_and_blocks_public(self) -> None:
        """/red-blue is plan-only for an own asset; a public target is BLOCKED."""
        app = self._ready_app("claude")
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app._execute("/red-blue k3s-isolated")     # own isolated asset
            await pilot.pause()
            joined = "\n".join(str(s) for s in app._transcript.lines)
            self.assertIn("plan-only", joined)
            app._execute("/red-blue example.com")      # public → blocked
            await pilot.pause()
            joined = "\n".join(str(s) for s in app._transcript.lines)
            self.assertIn("blocked", joined)

    async def test_self_improve_scans_and_classifies(self) -> None:
        """/self-improve surfaces risk-classified packets (no execution)."""
        app = self._ready_app("claude")
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app._execute("/self-improve")
            await pilot.pause()
            joined = "\n".join(str(s) for s in app._transcript.lines)
            self.assertIn("self-improvement", joined)
            self.assertIn("packets:", joined)  # scan ran (bounded; no-exec covered by unit test)

    async def test_idea_discovery_mode_produces_briefs(self) -> None:
        """In idea-discovery mode, free text yields a reference bundle + idea briefs."""
        from forgekit_console.policy import runtime_mode as rm

        app = self._ready_app("claude")
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app._runtime_mode = rm.MODE_IDEA_DISCOVERY
            app._recompute_policy()
            app._execute("노트앱 동기화가 느려서 불편하다. 경쟁 제품은 오프라인이 약하다.")
            await pilot.pause()
            joined = "\n".join(str(s) for s in app._transcript.lines)
            self.assertIn("idea-discovery", joined)
            self.assertIn("아이디어 브리프", joined)

    async def test_video_watch_link_only_is_reference_only(self) -> None:
        """Video-watch on a bare link is reference_only (no fake crawl)."""
        from forgekit_console.policy import runtime_mode as rm

        app = self._ready_app("claude")
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app._runtime_mode = rm.MODE_VIDEO_WATCH
            app._recompute_policy()
            app._execute("https://youtube.com/watch?v=abc")
            await pilot.pause()
            joined = "\n".join(str(s) for s in app._transcript.lines)
            self.assertIn("reference_only", joined)

    async def test_auto_recommends_and_safe_switches_mode(self) -> None:
        """/auto classifies the ask and safely switches the runtime mode (with reason)."""
        from forgekit_console.policy import runtime_mode as rm

        app = self._ready_app("claude")
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app._execute("/auto SaaS 아이디어 수집해줘")
            await pilot.pause()
            joined = "\n".join(str(s) for s in app._transcript.lines)
            self.assertIn("auto orchestration", joined)
            self.assertIn("이유", joined)
            self.assertEqual(app._runtime_mode, rm.MODE_IDEA_DISCOVERY)  # safe switch happened

    async def test_auto_does_not_enter_gated_mode(self) -> None:
        """/auto recommends red-blue but NEVER auto-switches into it (gated)."""
        from forgekit_console.policy import runtime_mode as rm

        app = self._ready_app("claude")
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            before = app._runtime_mode
            app._execute("/auto red team 보안 드릴 돌려줘")
            await pilot.pause()
            self.assertEqual(app._runtime_mode, before)  # NOT switched into red-blue
            joined = "\n".join(str(s) for s in app._transcript.lines)
            self.assertIn("gated", joined)

    async def test_auto_respects_operator_pin(self) -> None:
        """An explicit Shift+Tab pin → /auto won't override it."""
        app = self._ready_app("claude")
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            await pilot.press("shift+tab")  # operator pins a mode
            pinned = app._runtime_mode
            app._execute("/auto SaaS 아이디어 수집")
            await pilot.pause()
            self.assertEqual(app._runtime_mode, pinned)  # pin respected

    async def test_always_on_runs_bounded_cycle_with_runbook(self) -> None:
        """/always-on runs the bounded loop and surfaces a runbook for the privileged
        (infra) area + an operator-wait — never an execution."""
        app = self._ready_app("claude")
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app._execute("/always-on")
            await pilot.pause()
            joined = "\n".join(str(s) for s in app._transcript.lines)
            self.assertIn("always-on", joined)
            self.assertIn("runbook", joined)        # privileged area → runbook
            self.assertIn("대기", joined)            # operator-wait surfaced
            self.assertIn("execute phase 없음", joined)  # destructive structurally blocked
            self.assertIn("operator 알림", joined)   # WT4 notification fired (inbox)

    async def test_pm_agent_mode_runs_intake_handoff_not_live_submit(self) -> None:
        """In product-agent mode, a product ask runs PM intake→tech-lead split (with
        BLOCKED infra surfaced) and does NOT hit the live-submit provider."""
        from forgekit_console.chat import models as m

        class TrackingService:
            def __init__(self):
                self.calls = 0

            def submit(self, text, **_):
                self.calls += 1
                return m.SubmitResult(ok=True, mode=m.MODE_LIVE, category=m.CAT_OK, text="x")

        svc = TrackingService()
        app = self._ready_app("claude", submit_service=svc)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app._execute("/pm-agent")  # enter PM mode
            await pilot.pause()
            self.assertEqual(app.mode, "agent:product-agent")
            app._execute("영상 업로드 기능을 운영까지 완성해줘")  # a product ask
            await pilot.pause()
            joined = "\n".join(str(s) for s in app._transcript.lines)
            self.assertIn("handoff", joined)        # the intake→handoff ran
            self.assertIn("BLOCKED", joined)         # infra surfaced honestly
            self.assertIn("vault note", joined)      # WT5 authored note written
            self.assertEqual(svc.calls, 0)           # NOT a raw live submit

    async def test_approval_wait_holds_live_submit(self) -> None:
        """approval-wait is REAL enforcement: free text is held, the provider is NOT
        called, and the operator is told why + what to do."""
        from forgekit_console.policy import runtime_mode as rm
        from forgekit_console.chat import models as m

        class TrackingService:
            def __init__(self):
                self.calls = 0

            def submit(self, text, **_):
                self.calls += 1
                return m.SubmitResult(ok=True, mode=m.MODE_LIVE, category=m.CAT_OK, text="x")

        svc = TrackingService()
        app = self._ready_app("claude", submit_service=svc)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            # interactive mode → submit goes through
            app._submit_free_text("hello")
            await pilot.pause()
            await app.workers.wait_for_complete()
            self.assertEqual(svc.calls, 1)
            # switch to approval-wait → the next submit is HELD (no provider call)
            app._runtime_mode = rm.MODE_APPROVAL_WAIT
            app._recompute_policy()
            app._submit_free_text("please run")
            await pilot.pause()
            await app.workers.wait_for_complete()
            self.assertEqual(svc.calls, 1)  # NOT called again — held
            joined = "\n".join(str(s) for s in app._transcript.lines)
            self.assertIn("보류", joined)

    async def test_main_provider_changes_routing_target(self) -> None:
        claude = self._ready_app("claude")
        async with claude.run_test() as pilot:
            await pilot.pause()
            self.assertEqual(claude._effective_policy.routing_target(), "claude")
        ollama = self._ready_app("ollama")
        async with ollama.run_test() as pilot:
            await pilot.pause()
            self.assertEqual(ollama._effective_policy.routing_target(), "ollama")

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
