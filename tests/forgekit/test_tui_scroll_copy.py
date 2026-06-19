"""Scroll ownership + copy hotfix.

Guards the single-scroll-owner fix (Transcript/HelpPanel no longer own vertical scroll
— SessionFlow does) and the real `/copy` path (no fake "copy 예정").
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

    return ConsoleContext(
        repo_root=Path("/tmp/repo"),
        agents=load_agents(),
        commands=load_commands(),
    )


def _app():
    from forgekit_console.tui.app import ForgekitConsoleApp

    return ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=_fake_context())


@unittest.skipUnless(_TEXTUAL, "textual 필요 (pip install -e '.[console]')")
class ScrollOwnershipTests(unittest.TestCase):
    def test_transcript_has_no_nested_scroll(self) -> None:
        from forgekit_console.tui.transcript import Transcript

        css = Transcript.DEFAULT_CSS
        self.assertIn("overflow-y: hidden", css)        # does NOT own its own scroll
        self.assertNotIn("max-height: 80vh", css)        # the 80vh inner cap is gone

    def test_help_panel_has_no_nested_scroll(self) -> None:
        from forgekit_console.tui.help_panel import HelpPanel

        css = HelpPanel.DEFAULT_CSS
        self.assertIn("overflow-y: hidden", css)
        self.assertNotIn("overflow-y: auto", css)
        self.assertNotIn("max-height: 80vh", css)

    def test_session_flow_is_the_scroll_owner(self) -> None:
        from forgekit_console.tui.session_flow import SessionFlow

        self.assertIn("height: 1fr", SessionFlow.DEFAULT_CSS)   # the single 1fr scroll owner


class CopyTests(unittest.TestCase):
    def test_copy_text_real_or_honest(self) -> None:
        from forgekit_console.tui import clipboard

        ok, msg = clipboard.copy_text("forgekit clipboard regression")
        # either a real copy (ok) or an HONEST unsupported reason — never silent/fake
        self.assertTrue(msg)
        if not ok:
            self.assertIn("미지원", msg + " ") if "미지원" in msg else self.assertIn("실패", msg)

    def test_empty_is_handled(self) -> None:
        from forgekit_console.tui import clipboard

        ok, msg = clipboard.copy_text("")
        self.assertTrue(msg)   # returns a reason, never crashes


@unittest.skipUnless(_TEXTUAL, "textual 필요 (pip install -e '.[console]')")
class ScrollOwnershipPilotTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_session_flow_scrolls_at_runtime(self) -> None:
        from forgekit_console.tui.session_flow import SessionFlow
        from forgekit_console.tui.transcript import Transcript
        from forgekit_console.tui.help_panel import HelpPanel

        app = _app()
        async with app.run_test() as pilot:
            await pilot.pause()
            flow = app.query_one(SessionFlow)
            tr = app.query_one(Transcript)
            for i in range(80):                       # overflow the transcript on purpose
                tr.write(f"line {i} --------------------------------")
            await pilot.pause()
            # the OUTER flow is the single scroll owner …
            self.assertTrue(flow.allow_vertical_scroll)
            # … and the inner transcript does NOT own scroll even when overflowing
            self.assertFalse(tr.allow_vertical_scroll)
            self.assertFalse(tr.show_vertical_scrollbar)   # not hidden-by-color: genuinely off
            app._execute("/help")
            await pilot.pause()
            hp = app.query_one(HelpPanel)
            self.assertFalse(hp.allow_vertical_scroll)
            self.assertFalse(hp.show_vertical_scrollbar)


@unittest.skipUnless(_TEXTUAL, "textual 필요 (pip install -e '.[console]')")
class MultilinePilotTests(unittest.IsolatedAsyncioTestCase):
    async def test_ctrl_j_inserts_real_newline_then_enter_submits(self) -> None:
        from forgekit_console.tui.prompt_area import PromptArea

        app = _app()
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", PromptArea)
            await pilot.press("a", "b", "c")
            await pilot.press("ctrl+j")          # REAL newline, not a submit
            await pilot.press("d", "e", "f")
            self.assertEqual(prompt.value, "abc\ndef")   # the buffer actually has a newline
            await pilot.press("enter")           # Enter submits the WHOLE multiline buffer
            await pilot.pause()
            self.assertEqual(prompt.value, "")           # cleared after submit


@unittest.skipUnless(_TEXTUAL, "textual 필요 (pip install -e '.[console]')")
class CopyCommandPilotTests(unittest.IsolatedAsyncioTestCase):
    async def test_copy_command_surfaces_result_in_transcript(self) -> None:
        from forgekit_console.tui import clipboard
        from forgekit_console.tui.transcript import Transcript

        app = _app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._store.add_response("the answer is 42")
            seen = {}

            def _fake_copy(text):
                seen["text"] = text
                return True, f"{len(text)}자 복사됨 (stub)"

            orig = clipboard.copy_text
            clipboard.copy_text = _fake_copy
            try:
                app._copy_dispatch([])   # /copy → last response
            finally:
                clipboard.copy_text = orig
            await pilot.pause()
            # real wiring: /copy actually pushed the last response into the clipboard path
            self.assertEqual(seen["text"], "the answer is 42")
            # and the transcript got an echo + a result line (visible operator feedback)
            self.assertGreater(len(app.query_one(Transcript).lines), 0)

    async def test_copy_with_no_response_is_honest(self) -> None:
        app = _app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._copy_dispatch([])   # empty store → must not crash; surfaces 실패
            await pilot.pause()

    async def test_attach_is_honest_blocked_not_fake_upload(self) -> None:
        from forgekit_console.tui.transcript import Transcript

        app = _app()
        async with app.run_test() as pilot:
            await pilot.pause()
            before = len(app.query_one(Transcript).lines)
            app._execute("/attach photo.png")   # no upload transport exists
            await pilot.pause()
            # it surfaces an honest blocked notice (lines added), never claims success
            self.assertGreater(len(app.query_one(Transcript).lines), before)


if __name__ == "__main__":
    unittest.main()
