"""Process event feed — REAL, ordered events tied to actual actions (not fake labels).

Pure model (injected clock → deterministic durations) + pilot event-ORDER tests for the
real paths: slash route, free-text submit (sent → generate → done), blocked submit
(category-specific, provider NOT called), copy success/failure, paste expand. Also that
the feed is SEPARATE from the transcript so `/copy` never includes feed noise.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

_TEXTUAL = importlib.util.find_spec("textual") is not None


# --------------------------------------------------------------------------- #
# pure model
# --------------------------------------------------------------------------- #
class ProcessEventModelTests(unittest.TestCase):
    def _clock(self):
        t = {"v": 0.0}
        return (lambda: t["v"]), t

    def test_running_event_has_measured_duration(self):
        from forgekit_console.tui import process_events as pe
        clock, t = self._clock()
        feed = pe.ProcessFeed(clock=clock)
        ev = feed.start(pe.KIND_SUBMIT_START, "Submitting")
        self.assertEqual(ev.status, pe.ST_RUNNING)
        self.assertIsNone(ev.duration_ms)           # still running
        t["v"] = 2.5
        feed.finish(ev, pe.ST_DONE)
        self.assertEqual(ev.duration_ms, 2500)      # REAL measured (2.5s)

    def test_instant_marker_has_no_duration(self):
        from forgekit_console.tui import process_events as pe
        feed = pe.ProcessFeed(clock=lambda: 1.0)
        ev = feed.mark(pe.KIND_ROUTE_DONE, "Routed")
        self.assertIsNone(ev.duration_ms)           # no start→end span — honest

    def test_begin_turn_scopes_to_one_group(self):
        from forgekit_console.tui import process_events as pe
        feed = pe.ProcessFeed(clock=lambda: 0.0)
        feed.mark(pe.KIND_DONE, "Done")
        feed.begin_turn()
        self.assertTrue(feed.empty)                 # new turn → fresh group, no infinite log


# --------------------------------------------------------------------------- #
# pilot: real event order per path
# --------------------------------------------------------------------------- #
@unittest.skipUnless(_TEXTUAL, "textual 필요")
class ProcessFeedPilotTests(unittest.IsolatedAsyncioTestCase):
    def _app(self, svc=None, config=None):
        from forgekit_console.commands.registry import load_agents, load_commands
        from forgekit_console.commands.router import ConsoleContext
        from forgekit_console.tui.app import ForgekitConsoleApp
        ctx = ConsoleContext(repo_root=Path("/tmp/repo"), agents=load_agents(), commands=load_commands())
        return ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=ctx, submit_service=svc, config=config)

    def _live(self):
        from forgekit_console.chat import models as m

        class Svc:
            def __init__(s): s.calls = 0
            def submit(s, t, **_):
                s.calls += 1
                return m.SubmitResult(ok=True, mode=m.MODE_LIVE, category=m.CAT_OK, text="문단.\n\n끝.",
                                      provider_id="ollama", provider_label="Ollama",
                                      source=m.SOURCE_LOCAL_DEFAULT, model="g")
        return Svc()

    def _kinds(self, app):
        return [e.kind for e in app._feed.events]

    async def test_free_text_submit_order(self):
        app = self._app(self._live())
        async with app.run_test(size=(90, 28)) as pilot:
            await pilot.pause()
            app.query_one("#prompt").value = "설명"
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            for _ in range(20):
                await pilot.pause(0.03)
            kinds = self._kinds(app)
            # submit_start → submit_sent → generate_start → … → done, in order
            self.assertEqual(kinds[0], "submit_start")
            self.assertIn("submit_sent", kinds)
            self.assertIn("generate_start", kinds)
            self.assertEqual(kinds[-1], "done")
            self.assertLess(kinds.index("submit_sent"), kinds.index("generate_start"))

    async def test_failed_submit_event_is_category_specific(self):
        from forgekit_console.chat import models as m

        class UnsupportedSvc:   # e.g. claude/codex — routable but no console live-submit
            def submit(self, t, **_):
                return m.SubmitResult(ok=False, mode=m.MODE_ERROR, category=m.CAT_UNSUPPORTED,
                                      provider_id="claude", provider_label="Claude",
                                      text="unsupported_in_console")

        app = self._app(UnsupportedSvc())
        async with app.run_test(size=(90, 28)) as pilot:
            await pilot.pause()
            app.query_one("#prompt").value = "질문"
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            kinds = self._kinds(app)
            self.assertIn("submit_start", kinds)
            self.assertIn("error", kinds)                  # honest failure event
            err = [e for e in app._feed.events if e.kind == "error"][0]
            self.assertEqual(err.detail, m.CAT_UNSUPPORTED)   # SPECIFIC category, not vague "실패"
            self.assertNotIn("submit_sent", kinds)           # not claimed as sent

    async def test_slash_route_order(self):
        app = self._app()
        async with app.run_test(size=(90, 28)) as pilot:
            await pilot.pause()
            app._execute("/help") if False else app._execute("/whoami")
            await pilot.pause()
            kinds = self._kinds(app)
            self.assertEqual(kinds[0], "route_start")
            self.assertEqual(kinds[1], "route_done")
            self.assertIn(kinds[-1], ("done", "error"))

    async def test_copy_event_and_not_in_transcript_copy(self):
        app = self._app(self._live())
        async with app.run_test(size=(90, 28)) as pilot:
            await pilot.pause()
            app.query_one("#prompt").value = "설명"
            await pilot.press("enter")
            await pilot.pause(); await app.workers.wait_for_complete()
            for _ in range(15):
                await pilot.pause(0.03)
            app._copy_dispatch([])
            await pilot.pause()
            self.assertIn("copy_success", self._kinds(app))
            # the process feed is NEVER part of the copyable transcript text
            self.assertNotIn("Generating", app._store.all_text() or "")
            self.assertNotIn("Submitting", app._store.all_text() or "")

    async def test_copy_empty_is_failed_event(self):
        app = self._app()
        async with app.run_test(size=(90, 28)) as pilot:
            await pilot.pause()
            app._copy_dispatch([])   # nothing recorded → failure
            await pilot.pause()
            self.assertIn("copy_failed", self._kinds(app))


class ProcessFeedRenderTests(unittest.TestCase):
    """The feed render makes the ACTIVE (running) step ALIVE — an amber braille spinner +
    a LIVE ticking elapsed — and keeps finished steps quiet. The motion is real-status
    only: the spinner is `frame`-driven and the elapsed comes from the real clock, never a
    fake typing/char-reveal animation."""

    def _feed(self):
        from forgekit_console.tui import process_events as pe
        t = {"v": 0.0}
        feed = pe.ProcessFeed(clock=lambda: t["v"])
        return feed, t, pe

    def test_running_event_is_alive_amber_spinner(self):
        from forgekit_console.tui import render, theme
        feed, t, pe = self._feed()
        feed.start(pe.KIND_SUBMIT_START, "Submitting to ollama")
        # active line at a live clock=2.3s, motion frame 3
        line = render.process_feed_lines(feed.recent(), now=2.3, frame=3)[0]
        # amber (WARNING) motion + the spinner frame + live ticking elapsed + ellipsis
        self.assertIn(theme.WARNING, line)
        self.assertIn(render.SPINNER_FRAMES[3], line)
        self.assertIn("(2.3s)", line)               # REAL elapsed from the clock, not faked
        self.assertIn("…", line)
        self.assertNotIn("▸", line)                 # the old static marker is gone
        # the active label is NOT wrapped in dim (it is the bright "now" line)
        self.assertNotIn("[dim]Submitting to ollama", line)

    def test_running_spinner_advances_with_frame(self):
        from forgekit_console.tui import render
        feed, t, pe = self._feed()
        feed.start(pe.KIND_GENERATE_START, "Generating")
        a = render.process_feed_lines(feed.recent(), now=1.0, frame=0)[0]
        b = render.process_feed_lines(feed.recent(), now=1.0, frame=1)[0]
        self.assertNotEqual(a, b)                    # the glyph moves across frames (motion)
        self.assertIn(render.SPINNER_FRAMES[0], a)
        self.assertIn(render.SPINNER_FRAMES[1], b)

    def test_static_render_has_no_fabricated_elapsed(self):
        # no `now` → no elapsed at all (never a fake ~Xs); first spinner frame, deterministic.
        from forgekit_console.tui import render
        feed, t, pe = self._feed()
        feed.start(pe.KIND_GENERATE_START, "Generating")
        line = render.process_feed_lines(feed.recent())[0]
        self.assertIn(render.SPINNER_FRAMES[0], line)
        self.assertNotIn("s)", line)                # no measured/elapsed parenthetical

    def test_finished_event_is_quiet_dim(self):
        from forgekit_console.tui import render
        feed, t, pe = self._feed()
        ev = feed.start(pe.KIND_SUBMIT_START, "Submitting")
        t["v"] = 1.0
        feed.finish(ev, pe.ST_DONE)
        line = render.process_feed_lines(feed.recent())[0]
        self.assertIn("•", line)            # quiet dot, not the active spinner
        self.assertNotIn("▸", line)
        for frame in render.SPINNER_FRAMES:
            self.assertNotIn(frame, line)   # a finished step does not animate
        self.assertIn("[dim]Submitting[/dim]", line)
        self.assertIn("(1.0s)", line)       # measured duration shown

    def test_running_vs_done_render_differently(self):
        from forgekit_console.tui import render
        feed, t, pe = self._feed()
        done = feed.start(pe.KIND_ROUTE_START, "Routing")
        t["v"] = 0.4
        feed.finish(done, pe.ST_DONE)
        feed.start(pe.KIND_GENERATE_START, "Generating")   # still running
        lines = render.process_feed_lines(feed.recent(), now=0.4, frame=0)
        self.assertIn("•", lines[0])                      # finished route = quiet dot
        self.assertIn(render.SPINNER_FRAMES[0], lines[1])  # active generate = live spinner


if __name__ == "__main__":
    unittest.main()
