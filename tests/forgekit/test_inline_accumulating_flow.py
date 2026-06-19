"""Inline accumulating-flow guard — content-driven flow + docked composer + print-flow seam.

Real render checks via the Textual pilot (not CSS assertions): proves the inline reading
flow is content-driven (no fixed bounded box / 14-row cap), grows with output, keeps the
composer pinned at the bottom, and that the print-flow seam exists and is honest.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

_TEXTUAL = importlib.util.find_spec("textual") is not None


def _inline_app():
    from forgekit_console.commands.registry import load_agents, load_commands
    from forgekit_console.commands.router import ConsoleContext
    from forgekit_console.tui.app import ForgekitConsoleApp
    ctx = ConsoleContext(repo_root=Path("/tmp/repo"), agents=load_agents(), commands=load_commands())
    return ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=ctx,
                              config={"primary_provider": "ollama", "linked_providers": ["ollama"]},
                              inline=True)


# --------------------------------------------------------------------------- #
# print-flow seam (pure — no textual needed)
# --------------------------------------------------------------------------- #
class TranscriptSinkSeamTests(unittest.TestCase):
    def test_widget_sink_forwards_to_the_transcript(self) -> None:
        from forgekit_console.tui.transcript_sink import WidgetSink, TranscriptSink

        class Recorder:
            def __init__(self):
                self.calls = []
            def begin_turn(self):
                self.calls.append(("begin_turn",))
            def write(self, line):
                self.calls.append(("write", line))

        rec = Recorder()
        sink = WidgetSink(rec)
        self.assertIsInstance(sink, TranscriptSink)   # satisfies the protocol
        sink.begin_turn()
        sink.write_lines(["a", "b"])
        sink.finalize_turn()                          # no-op for the widget sink (no call)
        self.assertEqual(rec.calls, [("begin_turn",), ("write", "a"), ("write", "b")])

    def test_printflow_sink_is_an_honest_unwired_seam(self) -> None:
        from forgekit_console.tui.transcript_sink import PrintFlowSink

        sink = PrintFlowSink()
        with self.assertRaises(NotImplementedError):   # not faked — fails clearly
            sink.write("x")
        with self.assertRaises(NotImplementedError):
            sink.finalize_turn()


# --------------------------------------------------------------------------- #
# content-driven inline flow (real render)
# --------------------------------------------------------------------------- #
@unittest.skipUnless(_TEXTUAL, "textual 필요")
class InlineFlowGrowthTests(unittest.IsolatedAsyncioTestCase):
    async def test_flow_grows_with_output_and_composer_stays_pinned(self) -> None:
        from forgekit_console.tui.session_flow import SessionFlow

        app = _inline_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            flow = app.query_one(SessionFlow)
            comp = app.query_one("#composer-input-shell")
            empty_h = flow.region.height
            comp_bottom = comp.region.bottom
            self.assertLess(empty_h, 14)                       # compact when empty (no 14-box)
            # a long /doctor-style output
            for i in range(40):
                app._transcript.write(f"doctor {i:02d} ......................................")
            app._follow_tail()
            for _ in range(4):
                await pilot.pause()
            grown = flow.region.height
            self.assertGreater(grown, empty_h)                 # flow accumulated (grew)
            self.assertGreater(grown, 14)                      # past the old hard cap
            self.assertEqual(comp.region.bottom, comp_bottom)  # composer stayed pinned
            self.assertLessEqual(comp.region.bottom, 30)       # ... and on-screen
            # once it exceeds the viewport it stays scrollable (older output not lost)
            self.assertTrue(flow.allow_vertical_scroll)


if __name__ == "__main__":
    unittest.main()
