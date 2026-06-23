"""Console parity — live progress motion + selection visibility + transcript readability.

This lane closes the operator-cockpit gaps with REAL structure (no fake parity):

1. **Live "진행중" motion** — the active process-feed step renders an amber braille spinner
   that advances by ``frame`` AND a live elapsed ``(X.Xs)`` from the real clock. The app's
   ``_motion_tick`` advances the frame ONLY while a step is genuinely running (no idle
   animation, no fake typing). Idle → the tick is a no-op.
2. **Selection visibility** — the saturated-blue selection token pops against the near-black
   background (covered in depth by ``test_tui_selection_contrast`` /
   ``test_tui_transcript_selection``); here we pin that it is its own token, not accent-dim.
3. **Transcript readability** — the user prompt head is bold so each turn's question is a
   scannable anchor.
4. **Inline non-regression** — inline stays the default mode and the motion render is
   mode-agnostic (pure), so the live feed works identically inline and full.

Pure where possible; the motion-tick test mounts the app (textual) to drive the real loop.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

_TEXTUAL = importlib.util.find_spec("textual") is not None


class MotionRenderTests(unittest.TestCase):
    """The pure render contract behind the live motion (no terminal needed)."""

    def _running_feed(self):
        from forgekit_console.tui import process_events as pe
        feed = pe.ProcessFeed(clock=lambda: 3.0)
        feed.start(pe.KIND_GENERATE_START, "Generating")
        return feed, pe

    def test_spinner_frames_are_a_real_cycle(self):
        from forgekit_console.tui import render
        self.assertGreaterEqual(len(render.SPINNER_FRAMES), 4)
        self.assertEqual(len(set(render.SPINNER_FRAMES)), len(render.SPINNER_FRAMES))

    def test_motion_is_mode_agnostic_pure(self):
        # the render is a pure function of (events, now, frame) — identical regardless of
        # inline/full mode, so motion can't be a full-mode-only effect.
        from forgekit_console.tui import render
        feed, _ = self._running_feed()
        a = render.process_feed_lines(feed.recent(), now=5.0, frame=2)
        b = render.process_feed_lines(feed.recent(), now=5.0, frame=2)
        self.assertEqual(a, b)

    def test_live_elapsed_is_real_not_fabricated(self):
        from forgekit_console.tui import render
        feed, _ = self._running_feed()
        # elapsed = now - started_at = 5.0 - 3.0 = 2.0s (REAL), never a constant fake
        self.assertIn("(2.0s)", render.process_feed_lines(feed.recent(), now=5.0)[0])
        # no clock → no elapsed at all (honest), not a fabricated value
        self.assertNotIn("s)", render.process_feed_lines(feed.recent())[0])


class TranscriptReadabilityTests(unittest.TestCase):
    def test_user_prompt_head_is_bold_anchor(self):
        from forgekit_console.tui import render, theme
        lines = render.you_echo_lines("what is the status?")
        head = lines[0]
        self.assertIn(f"[{theme.ACCENT_PRIMARY}]›", head)   # the you-marker
        self.assertIn("[b]what is the status?[/b]", head)    # bold question = turn anchor

    def test_multiline_continuation_is_dim_indented(self):
        from forgekit_console.tui import render
        lines = render.you_echo_lines("line one\nline two")
        self.assertIn("[b]line one[/b]", lines[0])
        self.assertIn("[dim]line two[/dim]", lines[1])      # continuation stays quiet


class SelectionTokenTests(unittest.TestCase):
    def test_selection_is_its_own_saturated_token(self):
        from forgekit_console.tui import theme
        self.assertNotEqual(theme.SELECTION_BG, theme.ACCENT_DIM)
        # one token drives both the composer and cross-widget selection (uniform)
        v = theme.css_variables()
        self.assertEqual(v["selection-background"], v["screen-selection-background"])


class InlineModeNonRegressionTests(unittest.TestCase):
    def test_inline_remains_default_mode(self):
        from forgekit_console.tui import ui_mode
        # default (no cli, no env) stays inline — the lane must not flip the default mode.
        self.assertEqual(ui_mode.resolve_ui_mode(env={}), ui_mode.MODE_INLINE)
        self.assertEqual(ui_mode.resolve_ui_mode(env={}, cli="auto"), ui_mode.MODE_INLINE)

    def test_inline_run_kwargs_unchanged(self):
        from forgekit_console.tui import ui_mode
        kw = ui_mode.run_kwargs(ui_mode.MODE_INLINE)
        self.assertTrue(kw.get("inline"))
        self.assertFalse(kw.get("mouse"))   # terminal owns native selection in inline


@unittest.skipUnless(_TEXTUAL, "textual 필요")
class MotionTickTests(unittest.IsolatedAsyncioTestCase):
    def _app(self):
        from forgekit_console.commands.registry import load_agents, load_commands
        from forgekit_console.commands.router import ConsoleContext
        from forgekit_console.tui.app import ForgekitConsoleApp
        ctx = ConsoleContext(repo_root=Path("/tmp/repo"), agents=load_agents(),
                             commands=load_commands())
        return ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=ctx)

    async def test_tick_advances_only_while_running(self):
        from forgekit_console.tui import process_events as pe
        app = self._app()
        async with app.run_test(size=(90, 28)) as pilot:
            await pilot.pause()
            # idle: no active event → tick is a no-op (no idle animation)
            f0 = app._motion_frame
            app._motion_tick()
            app._motion_tick()
            self.assertEqual(app._motion_frame, f0)
            # a running step → tick advances the spinner frame (real motion)
            app._feed.begin_turn()
            app._feed.start(pe.KIND_GENERATE_START, "Generating")
            app._motion_tick()
            self.assertEqual(app._motion_frame, f0 + 1)
            # finishing the step → idle again → tick stops advancing
            app._feed.finish(app._feed.active(), pe.ST_DONE)
            f1 = app._motion_frame
            app._motion_tick()
            self.assertEqual(app._motion_frame, f1)


if __name__ == "__main__":
    unittest.main()
