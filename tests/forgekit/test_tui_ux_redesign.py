"""Claude-Code UX redesign — palette-above-input, auto-reveal, progressive render,
copy readback, turn cadence, avatar silhouette.

Real render checks via the Textual pilot (not CSS assertions): the palette opens
ABOVE the input bar, opening it reveals the composer, the chat body reveals in
chunks (thinking → generating → receipt), /copy round-trips through the OS clipboard,
turns get vertical breathing room, and the compact avatar reads as a figure.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import threading
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

_TEXTUAL = importlib.util.find_spec("textual") is not None


def _fake_context():
    from forgekit_console.commands.registry import load_agents, load_commands
    from forgekit_console.commands.router import ConsoleContext

    return ConsoleContext(repo_root=Path("/tmp/repo"), agents=load_agents(), commands=load_commands())


def _app(submit_service=None, config=None):
    from forgekit_console.tui.app import ForgekitConsoleApp

    return ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=_fake_context(),
                              submit_service=submit_service, config=config)


def _visible(flow, w) -> bool:
    return w.region.y >= flow.region.y and w.region.bottom <= flow.region.bottom + 1


# --------------------------------------------------------------------------- #
# Pure: progressive-reveal chunking
# --------------------------------------------------------------------------- #
class ChunkTests(unittest.TestCase):
    def test_paragraph_and_line_group_chunking(self) -> None:
        from forgekit_console.tui import render

        lines = ["a1", "a2", "", "b1", "b2", "b3", "b4"]
        chunks = render.chunk_result_lines(lines, max_lines=3)
        # paragraph boundary (blank) ends a chunk; long paragraph sub-chunks at max_lines
        self.assertEqual(chunks[0], ("a1", "a2", ""))
        self.assertEqual(chunks[1], ("b1", "b2", "b3"))
        self.assertEqual(chunks[2], ("b4",))
        # reveal is lossless — every line appears exactly once, in order
        self.assertEqual([ln for c in chunks for ln in c], lines)


# --------------------------------------------------------------------------- #
# A + B: palette opens directly below the input bar; composer stays docked
# --------------------------------------------------------------------------- #
@unittest.skipUnless(_TEXTUAL, "textual 필요")
class PaletteAboveAndRevealTests(unittest.IsolatedAsyncioTestCase):
    async def test_palette_opens_directly_below_input_bar(self) -> None:
        from forgekit_console.tui.palette import CommandPalette

        app = _app()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            await pilot.press("slash", "h", "e")
            await pilot.pause()
            pal = app.query_one(CommandPalette).region
            bar = app.query_one("#composer-input-shell").region
            self.assertTrue(app._palette.is_open)
            self.assertGreaterEqual(pal.y, bar.bottom)        # palette is BELOW the bar
            self.assertLessEqual(pal.y - bar.bottom, 1)       # flush (gap ≈ 0)

    async def test_composer_docked_visible_even_while_browsing(self) -> None:
        """Parity hotfix 2: the composer is DOCKED, so the operator NEVER has to scroll
        to see the input/palette. Even after scrolling up to browse a long history,
        pressing `/` shows the palette directly below the (still-docked, still-visible)
        input — no manual scroll-down needed."""
        from forgekit_console.tui.composer import Composer
        from forgekit_console.tui.palette import CommandPalette
        from forgekit_console.tui.session_flow import SessionFlow

        app = _app()
        async with app.run_test(size=(90, 22)) as pilot:
            await pilot.pause()
            tr = app._transcript
            for i in range(120):
                tr.write(f"history {i} ....................................")
            await pilot.pause()
            flow = app.query_one(SessionFlow)
            comp = app.query_one(Composer)
            flow.scroll_to(y=8, animate=False)       # scroll UP to browse history
            await pilot.pause()
            # docked → composer is STILL fully on-screen while browsing (no fold)
            self.assertGreaterEqual(comp.region.bottom, app.size.height - 1)
            await pilot.press("slash", "h")          # command-entry while scrolled up
            await pilot.pause()
            self.assertGreaterEqual(comp.region.bottom, app.size.height - 1)
            self.assertGreaterEqual(
                app.query_one(CommandPalette).region.y,
                app.query_one("#composer-input-shell").region.bottom,   # palette below bar
            )

    async def test_reopen_after_close_keeps_palette_below_input(self) -> None:
        """close→reopen regression: after Esc-close then reopen, the palette still opens
        directly below the docked input (no stuck state)."""
        from forgekit_console.tui.composer import Composer
        from forgekit_console.tui.palette import CommandPalette

        app = _app()
        async with app.run_test(size=(90, 22)) as pilot:
            await pilot.pause()
            tr = app._transcript
            for i in range(120):
                tr.write(f"history {i} ....................................")
            await pilot.pause()
            comp = app.query_one(Composer)
            await pilot.press("slash", "h")          # open …
            await pilot.pause()
            await pilot.press("escape")              # … close
            await pilot.pause()
            await pilot.press("slash", "p")          # reopen
            await pilot.pause()
            self.assertTrue(app._palette.is_open)
            self.assertGreaterEqual(comp.region.bottom, app.size.height - 1)
            self.assertGreaterEqual(
                app.query_one(CommandPalette).region.y,
                app.query_one("#composer-input-shell").region.bottom,
            )


# --------------------------------------------------------------------------- #
# D: progressive rendering (thinking → generating → receipt)
# --------------------------------------------------------------------------- #
@unittest.skipUnless(_TEXTUAL, "textual 필요")
class ProgressiveRenderTests(unittest.IsolatedAsyncioTestCase):
    def _live_service(self, body: str):
        from forgekit_console.chat import models as m

        class Svc:
            def submit(self, text, **_):
                return m.SubmitResult(ok=True, mode=m.MODE_LIVE, category=m.CAT_OK, text=body,
                                      provider_id="ollama", provider_label="Ollama",
                                      source=m.SOURCE_LOCAL_DEFAULT, model="g")
        return Svc()

    async def test_body_reveals_in_chunks_then_receipt(self) -> None:
        from textual.widgets import Static

        body = "문단 하나.\n\n문단 둘은\n여러 줄로.\n\n문단 셋 끝."
        app = _app(self._live_service(body))
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            app._reveal_interval = 0.3   # slow enough to OBSERVE the stages
            app.query_one("#prompt").value = "설명"
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause(0.05)
            partial = len(app._transcript.lines)
            gen = str(app.query_one("#livestatus", Static).render())
            self.assertIn("Generating", gen)                 # GENERATING event visible in the feed
            for _ in range(25):
                await pilot.pause(0.1)
            full = len(app._transcript.lines)
            joined = "\n".join(str(s) for s in app._transcript.lines)
            self.assertGreater(full, partial)                # grew progressively (chunked)
            self.assertTrue(all(p in joined for p in ["문단 하나", "문단 둘", "문단 셋"]))
            self.assertIn("Ollama", joined)                  # RECEIPT at the end (transcript)
            # the feed ends with a Done event (real timeline), not the old transient string
            kinds = [e.kind for e in app._feed.events]
            self.assertIn("generate_start", kinds)
            self.assertIn("done", kinds)

    async def test_submitting_event_shows_while_provider_runs(self) -> None:
        from textual.widgets import Static
        from forgekit_console.chat import models as m

        gate = threading.Event()
        released = threading.Event()

        class BlockingSvc:
            def submit(self, text, **_):
                released.set()
                gate.wait(2.0)   # hold the worker so the running Submit event is observable
                return m.SubmitResult(ok=True, mode=m.MODE_LIVE, category=m.CAT_OK,
                                      text="끝", provider_id="ollama", provider_label="Ollama",
                                      source=m.SOURCE_LOCAL_DEFAULT, model="g")

        app = _app(BlockingSvc())
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            app.query_one("#prompt").value = "질문"
            await pilot.press("enter")
            for _ in range(20):
                await pilot.pause(0.02)
                if released.is_set():
                    break
            # while the provider call runs, the feed shows a RUNNING "Submitting…" event
            feed = str(app.query_one("#livestatus", Static).render())
            active = app._feed.active()
            gate.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertIn("Submitting", feed)
            self.assertIsNotNone(active)                  # a real running event, not a fake string


# --------------------------------------------------------------------------- #
# C: turn cadence
# --------------------------------------------------------------------------- #
@unittest.skipUnless(_TEXTUAL, "textual 필요")
class TurnCadenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_begin_turn_inserts_blank_between_turns(self) -> None:
        # configured provider → no setup-required explanation block → empty transcript start
        app = _app(config={"primary_provider": "ollama", "linked_providers": ["ollama"]})
        async with app.run_test() as pilot:
            await pilot.pause()
            tr = app._transcript
            self.assertEqual(len(tr.lines), 0)
            tr.begin_turn()                # no separator on an empty transcript
            self.assertEqual(len(tr.lines), 0)
            tr.write("첫 턴")
            n = len(tr.lines)
            tr.begin_turn()                # …but a blank separator once content exists
            self.assertEqual(len(tr.lines), n + 1)


# --------------------------------------------------------------------------- #
# E: copy plain-text + readback
# --------------------------------------------------------------------------- #
class CopyReadbackTests(unittest.TestCase):
    def test_empty_payload_is_failure(self) -> None:
        from forgekit_console.tui import clipboard

        ok, msg = clipboard.copy_text("   ")
        self.assertFalse(ok)
        self.assertIn("비어", msg)

    @unittest.skipUnless(
        sys.platform == "darwin" and shutil.which("pbcopy") and shutil.which("pbpaste"),
        "pbcopy/pbpaste 필요 (macOS)",
    )
    def test_copy_then_pbpaste_readback_matches(self) -> None:
        from forgekit_console.tui import clipboard

        payload = "forgekit redesign readback — 한글 123"
        ok, _ = clipboard.copy_text(payload)
        self.assertTrue(ok)
        self.assertEqual(clipboard.read_text(), payload)   # real OS round-trip


# --------------------------------------------------------------------------- #
# F: avatar silhouette
# --------------------------------------------------------------------------- #
class AvatarTests(unittest.TestCase):
    def test_fallback_badge_reads_as_a_figure(self) -> None:
        from forgekit_console.tui import image_renderer as ir
        from forgekit_console.tui import theme

        out = "\n".join(ir.avatar_mark_lines())
        # the three avatar cues: a frame, the brand split (f/k), and the ear-cup glyphs
        self.assertIn("f", out)
        self.assertIn("k", out)
        self.assertIn(theme.ACCENT_PRIMARY, out)
        self.assertIn(theme.ACCENT_SECONDARY, out)
        self.assertTrue(any(g in out for g in ("◖", "◗")))   # headphone ear-cups
        self.assertNotIn("▀", out)                            # not a dithered raster

    @unittest.skipUnless(importlib.util.find_spec("PIL") is not None, "Pillow 필요")
    def test_compact_avatar_is_higher_res_braille(self) -> None:
        from forgekit_console.tui import image_renderer as ir

        out = ir.HalfBlockRenderer().renderable()
        plain = out.plain
        self.assertTrue(any(0x2800 <= ord(ch) <= 0x28FF for ch in plain))  # braille
        # 16-col render → each row is wider than the old 12-col blob
        widest = max(len(line) for line in plain.splitlines())
        self.assertGreaterEqual(widest, 16)


if __name__ == "__main__":
    unittest.main()
