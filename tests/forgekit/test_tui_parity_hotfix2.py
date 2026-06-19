"""Claude-Code parity hotfix 2 — docked composer, copy targets, no scrollbar gutter.

Real pilot render + real OS clipboard round-trip (no CSS-only assertions). Proves:
- the composer is DOCKED at the viewport bottom; opening the palette grows it upward
  (palette above the input) while the input bar STAYS docked,
- `/copy [last|turn N|block N|all]` copy the right PLAIN text (pbpaste readback on macOS),
- SessionFlow is the single scroll owner with NO scrollbar gutter,
- the `● palette` mode-pill clutter is gone while the palette is open.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

_TEXTUAL = importlib.util.find_spec("textual") is not None


def _ctx():
    from forgekit_console.commands.registry import load_agents, load_commands
    from forgekit_console.commands.router import ConsoleContext
    return ConsoleContext(repo_root=Path("/tmp/repo"), agents=load_agents(), commands=load_commands())


def _app(submit_service=None):
    from forgekit_console.tui.app import ForgekitConsoleApp
    return ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=_ctx(), submit_service=submit_service)


# --------------------------------------------------------------------------- #
# pure: the copyable transcript store
# --------------------------------------------------------------------------- #
class TranscriptStoreTests(unittest.TestCase):
    def _store(self):
        from forgekit_console.tui.transcript_store import TranscriptStore
        s = TranscriptStore()
        s.add_user("질문 1")
        s.add_response("[b]답변[/b] 1")     # markup must be stripped
        s.add_user("질문 2")
        s.add_response("답변 2")
        return s

    def test_strip_markup_and_last_response(self):
        s = self._store()
        self.assertEqual(s.last_response(), "답변 2")
        self.assertEqual(s.block(2).text, "답변 1")   # [b]…[/b] stripped

    def test_turn_groups_user_and_response(self):
        self.assertEqual(self._store().turn(1), "질문 1\n답변 1")
        self.assertEqual(self._store().turn(2), "질문 2\n답변 2")

    def test_all_and_bounds(self):
        s = self._store()
        self.assertIn("질문 1", s.all_text())
        self.assertIn("답변 2", s.all_text())
        self.assertIsNone(s.block(99))
        self.assertIsNone(s.turn(99))

    def test_empty_response_not_recorded(self):
        from forgekit_console.tui.transcript_store import TranscriptStore
        s = TranscriptStore()
        self.assertIsNone(s.add_response("   "))
        self.assertIsNone(s.last_response())


# --------------------------------------------------------------------------- #
# docked composer + palette-above + no gutter (real render)
# --------------------------------------------------------------------------- #
@unittest.skipUnless(_TEXTUAL, "textual 필요")
class DockedLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_input_docked_and_palette_pushes_flow_up(self) -> None:
        from forgekit_console.tui.composer import Composer
        from forgekit_console.tui.session_flow import SessionFlow
        from forgekit_console.tui.palette import CommandPalette

        app = _app()
        async with app.run_test(size=(100, 28)) as pilot:
            await pilot.pause()
            H = app.size.height
            comp = app.query_one(Composer)
            bar = app.query_one("#composer-input-shell")
            flow = app.query_one(SessionFlow)
            self.assertGreaterEqual(comp.region.bottom, H - 1)          # docked at bottom
            flow_h_before = flow.region.height
            # open palette → composer grows, flow region shrinks; palette is DIRECTLY
            # BELOW the input bar (Claude), inside the bottom-docked composer zone.
            await pilot.press("slash", "h", "e")
            await pilot.pause()
            pal = app.query_one(CommandPalette)
            self.assertGreaterEqual(pal.region.y, bar.region.bottom)        # palette BELOW input
            self.assertLessEqual(pal.region.y - bar.region.bottom, 1)       # flush (gap ≈ 0)
            self.assertGreaterEqual(comp.region.bottom, H - 1)          # composer still docked
            self.assertLess(flow.region.height, flow_h_before)         # transcript zone shrank

    async def test_session_flow_is_sole_owner_no_gutter(self) -> None:
        from forgekit_console.tui.session_flow import SessionFlow
        from forgekit_console.tui.transcript import Transcript
        from forgekit_console.tui.composer import Composer

        app = _app()
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            tr = app._transcript
            for i in range(80):
                tr.write(f"line {i} ----------------------------------")
            await pilot.pause()
            flow = app.query_one(SessionFlow)
            self.assertTrue(flow.allow_vertical_scroll)                 # still the scroll owner
            self.assertEqual(int(flow.styles.scrollbar_size_vertical), 0)  # no gutter drawn
            self.assertFalse(app.query_one(Transcript).allow_vertical_scroll)
            self.assertFalse(app.query_one(Composer).allow_vertical_scroll)

    async def test_no_palette_modepill_clutter(self) -> None:
        from textual.widgets import Static

        app = _app()
        async with app.run_test(size=(100, 28)) as pilot:
            await pilot.pause()
            await pilot.press("slash", "h")
            await pilot.pause()
            self.assertTrue(app._palette.is_open)
            # the `● palette` pill must NOT be shown (the list above the input is enough)
            self.assertFalse(app.query_one("#modepill", Static).display)


# --------------------------------------------------------------------------- #
# /copy variants — real wiring (+ pbpaste readback on macOS)
# --------------------------------------------------------------------------- #
@unittest.skipUnless(_TEXTUAL, "textual 필요")
class CopyVariantsTests(unittest.IsolatedAsyncioTestCase):
    def _svc(self, replies):
        from forgekit_console.chat import models as m

        class Svc:
            def __init__(s): s.r = list(replies)
            def submit(s, text, **_):
                body = s.r.pop(0) if s.r else "ok"
                return m.SubmitResult(ok=True, mode=m.MODE_LIVE, category=m.CAT_OK, text=body,
                                      provider_id="ollama", provider_label="Ollama",
                                      source=m.SOURCE_LOCAL_DEFAULT, model="g")
        return Svc()

    async def _two_turns(self, app, pilot):
        for q in ("질문A", "질문B"):
            app.query_one("#prompt").value = q
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            for _ in range(12):
                await pilot.pause(0.02)

    async def test_copy_targets_capture_right_plaintext(self) -> None:
        from forgekit_console.tui import clipboard

        captured = {}
        orig = clipboard.copy_text
        clipboard.copy_text = lambda t: (captured.__setitem__("t", t) or (True, "stub"))
        try:
            app = _app(self._svc(["답변A", "답변B"]))
            async with app.run_test(size=(100, 28)) as pilot:
                await pilot.pause()
                await self._two_turns(app, pilot)
                app._copy_dispatch([]);            await pilot.pause()
                self.assertEqual(captured["t"], "답변B")           # last response
                app._copy_dispatch(["turn", "1"]); await pilot.pause()
                self.assertEqual(captured["t"], "질문A\n답변A")     # turn 1 = user+response
                app._copy_dispatch(["block", "1"]); await pilot.pause()
                self.assertEqual(captured["t"], "질문A")            # block 1
                app._copy_dispatch(["all"]);       await pilot.pause()
                self.assertIn("답변B", captured["t"])               # whole transcript
        finally:
            clipboard.copy_text = orig

    async def test_copy_empty_is_failure(self) -> None:
        from forgekit_console.tui import clipboard

        called = {"n": 0}
        orig = clipboard.copy_text
        clipboard.copy_text = lambda t: (called.__setitem__("n", called["n"] + 1) or (True, "stub"))
        try:
            app = _app()
            async with app.run_test() as pilot:
                await pilot.pause()
                app._copy_dispatch([])   # nothing recorded yet → failure, no clipboard write
                await pilot.pause()
                self.assertEqual(called["n"], 0)
        finally:
            clipboard.copy_text = orig

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("pbcopy") and shutil.which("pbpaste"),
                         "pbcopy/pbpaste 필요 (macOS)")
    async def test_copy_real_pbpaste_readback(self) -> None:
        from forgekit_console.tui import clipboard

        app = _app(self._svc(["진짜 응답입니다"]))
        async with app.run_test(size=(100, 28)) as pilot:
            await pilot.pause()
            app.query_one("#prompt").value = "질문"
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            for _ in range(12):
                await pilot.pause(0.02)
            app._copy_dispatch([])
            await pilot.pause()
            self.assertEqual(clipboard.read_text(), "진짜 응답입니다")   # real OS round-trip


if __name__ == "__main__":
    unittest.main()
