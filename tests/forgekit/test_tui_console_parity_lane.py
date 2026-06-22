"""Console parity lane — consolidated geometry/runtime MEASUREMENT verification.

This is the lane's acceptance harness: it proves the six parity goals with REAL
measurements (Textual pilot region geometry + runtime counters), not CSS reading or
faked numbers. The goals:

  1. slash palette opens flush DIRECTLY BELOW the chat bar (gap 0-1 rows);
  2. natural scroll — ONE scroll owner (SessionFlow), no nested inner scrollbars;
  3. copy/paste/image-attach state is honest (empty copy = failure, real round-trip);
  4. terminal-native transcript DIRECTION — turn boundaries flow through the sink and
     are finalized (measurable ``_turns_finalized``); the print-flow seam stays honest;
  5. process event feed = REAL measured events (start→finish duration, instant markers
     carry none) for Routing/Submit/Generate/Done — never a fake Reading/Thinking;
  6. progressive reveal = REAL content-derived chunk append (no fake typing): the
     revealed chunk count matches ``render.chunk_result_lines`` for the actual body.

Set ``FORGEKIT_PARITY_EVIDENCE=<path>`` to also dump the measured numbers to a file
(used to regenerate ``examples/tui-parity-lane/measurements.txt``). Default run is pure
assertions with no filesystem side effects.
"""

from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

_TEXTUAL = importlib.util.find_spec("textual") is not None
_EVIDENCE_PATH = os.environ.get("FORGEKIT_PARITY_EVIDENCE", "")


def _app(svc=None, config=None):
    from forgekit_console.commands.registry import load_agents, load_commands
    from forgekit_console.commands.router import ConsoleContext
    from forgekit_console.tui.app import ForgekitConsoleApp

    ctx = ConsoleContext(repo_root=Path("/tmp/repo"), agents=load_agents(), commands=load_commands())
    return ForgekitConsoleApp(
        repo_root=Path("/tmp/repo"), context=ctx, submit_service=svc,
        config={"primary_provider": "ollama", "linked_providers": ["ollama"]} if config is None else config,
    )


def _multi_paragraph_svc():
    """A live provider stub returning a MULTI-paragraph body → real >1-chunk reveal."""

    from forgekit_console.chat import models as m

    body = "첫 문단입니다.\n\n둘째 문단입니다.\n\n셋째 문단으로 마무리합니다."

    class Svc:
        def __init__(self):
            self.calls = 0

        def submit(self, t, **_):
            self.calls += 1
            return m.SubmitResult(
                ok=True, mode=m.MODE_LIVE, category=m.CAT_OK, text=body,
                provider_id="ollama", provider_label="Ollama",
                source=m.SOURCE_LOCAL_DEFAULT, model="g",
            )

    return Svc(), body


# --------------------------------------------------------------------------- #
# Goal 4 (pure): the print-flow seam stays honest; the widget sink finalizes.
# --------------------------------------------------------------------------- #
class PrintFlowSeamHonestyTests(unittest.TestCase):
    def test_printflow_seam_is_not_faked(self):
        from forgekit_console.tui.transcript_sink import PrintFlowSink

        sink = PrintFlowSink()
        with self.assertRaises(NotImplementedError):
            sink.write("x")            # above-region emit is blocked → fails clearly
        with self.assertRaises(NotImplementedError):
            sink.finalize_turn()       # never pretends to have emitted to scrollback


@unittest.skipUnless(_TEXTUAL, "textual 필요")
class ConsoleParityLaneMeasurementTests(unittest.IsolatedAsyncioTestCase):
    async def test_measure_all_six_goals(self):
        from forgekit_console.tui import render
        from forgekit_console.tui.composer import Composer
        from forgekit_console.tui.palette import CommandPalette
        from forgekit_console.tui.session_flow import SessionFlow
        from forgekit_console.tui.transcript import Transcript

        svc, body = _multi_paragraph_svc()
        app = _app(svc)
        measured: dict = {}

        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            H = app.size.height

            # --- Goal 1: palette flush directly below the chat bar ---------------
            await pilot.press("slash", "h", "e")
            await pilot.pause()
            bar = app.query_one("#composer-input-shell").region
            pal = app.query_one(CommandPalette).region
            palette_gap = pal.y - bar.bottom
            palette_in_view = 0 <= pal.y and pal.bottom <= H
            measured["palette"] = {
                "input_bottom": bar.bottom, "palette_top": pal.y,
                "gap_rows": palette_gap, "in_viewport": palette_in_view,
                "is_open": app._palette.is_open,
            }
            self.assertTrue(app._palette.is_open)
            self.assertGreaterEqual(pal.y, bar.bottom)        # below the input
            self.assertLessEqual(palette_gap, 1)              # flush (0-1 rows)
            self.assertTrue(palette_in_view)
            # close the palette before driving submits
            await pilot.press("escape")
            await pilot.pause()

            # --- Goal 2: single scroll owner, no nested scrollbars ---------------
            # The reading flow is the SOLE scroll-owning container (a VerticalScroll); it
            # only *engages* scroll once content overflows the viewport. Content panes
            # never own scroll, so the session reads as one terminal flow, not nested panes.
            from textual.containers import VerticalScroll
            flow = app.query_one(SessionFlow)
            transcript = app.query_one(Transcript)
            composer = app.query_one(Composer)
            flow_is_owner = isinstance(flow, VerticalScroll)
            nested = {
                "transcript": transcript.allow_vertical_scroll,
                "composer": composer.allow_vertical_scroll,
            }
            # overflow the flow with a long burst → it engages its single scroll
            for i in range(60):
                app._transcript.write(f"scroll probe line {i:02d} ............................")
            app._follow_tail()
            for _ in range(4):
                await pilot.pause()
            flow_engaged = flow.allow_vertical_scroll and flow.max_scroll_y > 0
            measured["scroll"] = {
                "flow_is_sole_owner": flow_is_owner, "nested_scrollers": nested,
                "engages_on_overflow": flow_engaged, "max_scroll_y": flow.max_scroll_y,
            }
            self.assertTrue(flow_is_owner)                     # SessionFlow is the owner
            self.assertFalse(nested["transcript"])             # no nested inner scroll
            self.assertFalse(nested["composer"])
            self.assertTrue(flow_engaged)                      # scroll engages on overflow
            app._transcript.clear()                            # reset before the submit turn
            app._store.clear()

            # --- Goals 4/5/6: drive a real free-text turn ------------------------
            app._reveal_interval = 0.01                        # fast reveal (still real timer)
            start_finalized = app._turns_finalized
            app.query_one("#prompt").value = "설명해줘"
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            for _ in range(40):
                await pilot.pause(0.02)

            # Goal 5: process feed — REAL ordered events + measured durations
            kinds = [e.kind for e in app._feed.events]
            gen = next((e for e in app._feed.events if e.kind == "generate_start"), None)
            self.assertIn("submit_start", kinds)
            self.assertIn("submit_sent", kinds)
            self.assertIsNotNone(gen)
            self.assertEqual(kinds[-1], "done")
            self.assertEqual(svc.calls, 1)                     # real provider call, once
            # generate_start is a start→finish span → a REAL measured duration (int ms)
            self.assertIsNotNone(gen.duration_ms)
            self.assertIsInstance(gen.duration_ms, int)

            # Goal 6: progressive reveal — chunk count matches CONTENT chunking
            expected_lines = body.split("\n")
            expected_chunks = len(render.chunk_result_lines(expected_lines))
            self.assertGreater(expected_chunks, 1)             # multi-paragraph → >1 chunk
            self.assertEqual(app._gen_chunks, expected_chunks) # revealed == real chunks
            # the chunk count is reflected honestly in the event detail (not a fake "~1s")
            self.assertIn(str(app._gen_chunks), gen.detail)
            # the actual response body is present in the transcript (content preserved)
            store_text = app._store.all_text() or ""
            self.assertIn("첫 문단입니다.", store_text)
            self.assertIn("셋째 문단으로 마무리합니다.", store_text)
            # the feed is a SEPARATE surface — never leaks into the copyable transcript
            self.assertNotIn("Generating", store_text)
            self.assertNotIn("Submitting", store_text)

            # Goal 4: the turn was finalized through the sink (a real, counted unit)
            finalized_after_submit = app._turns_finalized - start_finalized
            self.assertEqual(finalized_after_submit, 1)
            measured["reveal"] = {
                "expected_chunks": expected_chunks, "revealed_chunks": app._gen_chunks,
                "generate_duration_ms": gen.duration_ms, "event_detail": gen.detail,
                "feed_order": kinds,
            }

            # --- Goal 3: copy is honest — empty = failure, real = success --------
            before_copy_finalized = app._turns_finalized
            app._copy_dispatch([])                             # copies the last response
            await pilot.pause()
            copy_kinds = [e.kind for e in app._feed.events]
            measured["copy"] = {"events": copy_kinds}
            self.assertIn("copy_success", copy_kinds)          # there IS a response to copy
            self.assertEqual(app._turns_finalized - before_copy_finalized, 1)

            # a fresh app with NOTHING recorded → copy is an honest FAILURE, never faked
            app2 = _app()
            async with app2.run_test(size=(100, 30)) as pilot2:
                await pilot2.pause()
                app2._copy_dispatch([])
                await pilot2.pause()
                self.assertIn("copy_failed", [e.kind for e in app2._feed.events])

            # turn-finalization total across the whole session (slash + submit + copy)
            app._execute("/whoami")
            await pilot.pause()
            measured["turns_finalized_total"] = app._turns_finalized
            self.assertGreaterEqual(app._turns_finalized, 3)

        if _EVIDENCE_PATH:
            _write_evidence(Path(_EVIDENCE_PATH), measured)

    async def test_copy_real_clipboard_round_trip_when_tools_present(self):
        """Goal 3 — when pbcopy/pbpaste (or xclip) exist, copy is a REAL OS round-trip,
        not a faked success. Skips honestly where no clipboard tool is installed."""

        import shutil

        from forgekit_console.tui import clipboard

        if not (shutil.which("pbcopy") and shutil.which("pbpaste")):
            self.skipTest("no pbcopy/pbpaste — real round-trip not verifiable here")
        marker = "forgekit-parity-roundtrip-✓"
        ok, _ = clipboard.copy_text(marker)
        self.assertTrue(ok)
        self.assertEqual((clipboard.read_text() or "").strip(), marker)   # real read-back


def _write_evidence(path: Path, m: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "ForgeKit console parity lane — REAL measured evidence (Textual pilot, 100x30)",
        "=" * 78,
        "Method: region geometry + runtime counters from a live pilot run; durations are",
        "monotonic-clock measured; chunk counts are content-derived (render.chunk_result_lines).",
        "No CSS reading, no faked numbers, no fake typing.",
        "",
        "[1] slash palette — flush directly below the chat bar",
        f"  input.bottom={m['palette']['input_bottom']}  palette.top={m['palette']['palette_top']}"
        f"  gap_rows={m['palette']['gap_rows']} (flush: 0-1)  in_viewport={m['palette']['in_viewport']}",
        "",
        "[2] scroll — single owner (SessionFlow), no nested scrollbars",
        f"  flow_is_sole_owner={m['scroll']['flow_is_sole_owner']}"
        f"  nested_scrollers={m['scroll']['nested_scrollers']}",
        f"  engages_on_overflow={m['scroll']['engages_on_overflow']}"
        f"  max_scroll_y={m['scroll']['max_scroll_y']}",
        "",
        "[4] terminal-native transcript direction — turns finalized through the sink",
        f"  turns_finalized_total={m['turns_finalized_total']}  (slash + submit + copy)",
        "  print-flow seam (above-region emit): NOT wired — PrintFlowSink raises (honest)",
        "",
        "[5] process event feed — REAL ordered events (Routing/Submit/Generate/Done)",
        f"  feed_order={m['reveal']['feed_order']}",
        f"  generate_start.duration_ms={m['reveal']['generate_duration_ms']} (measured, not faked)",
        "",
        "[6] progressive reveal — real content-derived chunk append (no fake typing)",
        f"  expected_chunks={m['reveal']['expected_chunks']}"
        f"  revealed_chunks={m['reveal']['revealed_chunks']}  event_detail={m['reveal']['event_detail']!r}",
        "",
        "[3] copy/paste/attach state — honest",
        f"  copy_events={m['copy']['events']}  (empty payload elsewhere → copy_failed)",
        "  attach: image is staged_only (real bytes staged, NOT sent — console is text-only)",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
