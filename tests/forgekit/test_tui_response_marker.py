"""Transcript turn vocabulary — free-text assistant responses get a role marker.

A slash result is headed by ``»`` and a user turn by ``›``, but a free-text LLM response
had NO role marker — its body landed straight after the echo, so the response start was
hard to scan in a long session. ``render.mark_response_chunks`` now prefixes the response's
first non-empty line with the assistant marker (magenta ``●``). Pure render proof + a live
pilot proof that exactly one marker appears at the response start (not on every line).
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.tui import render

_TEXTUAL = importlib.util.find_spec("textual") is not None


class MarkResponseChunksTests(unittest.TestCase):
    def test_marks_first_nonempty_line_once(self) -> None:
        chunks = render.chunk_result_lines(["첫 줄입니다.", "", "둘째 문단."])
        marked = render.mark_response_chunks(chunks)
        flat = [ln for ch in marked for ln in ch]
        joined = "\n".join(flat)
        self.assertEqual(joined.count(render.RESPONSE_MARKER), 1)   # exactly one marker
        self.assertTrue(flat[0].startswith(render.RESPONSE_MARKER))  # on the first line
        self.assertIn("첫 줄입니다.", flat[0])

    def test_leading_blank_lines_untouched_marker_on_first_content(self) -> None:
        chunks = (("", "", "본문 시작"),)
        marked = render.mark_response_chunks(chunks)
        flat = [ln for ch in marked for ln in ch]
        self.assertEqual(flat[0], "")                               # blank stays blank
        self.assertTrue(any(ln.startswith(render.RESPONSE_MARKER) and "본문 시작" in ln
                            for ln in flat))

    def test_empty_input_no_marker(self) -> None:
        self.assertEqual(render.mark_response_chunks(()), ())
        only_blank = render.mark_response_chunks((("", ""),))
        self.assertNotIn(render.RESPONSE_MARKER, "\n".join(only_blank[0]))

    def test_marker_is_brand_secondary(self) -> None:
        from forgekit_console.tui import theme
        self.assertIn(theme.ACCENT_SECONDARY, render.RESPONSE_MARKER)


def _svc():
    """A live provider stub returning a 2-paragraph body → real chunk reveal."""

    from forgekit_console.chat import models as m

    body = "응답 첫 문단입니다.\n\n응답 둘째 문단입니다."

    class Svc:
        def submit(self, t, **_):
            return m.SubmitResult(
                ok=True, mode=m.MODE_LIVE, category=m.CAT_OK, text=body,
                provider_id="ollama", provider_label="Ollama",
                source=m.SOURCE_LOCAL_DEFAULT, model="g",
            )
    return Svc()


@unittest.skipUnless(_TEXTUAL, "textual 필요")
class ResponseMarkerPilotTests(unittest.IsolatedAsyncioTestCase):
    def _app(self):
        from forgekit_console.commands.registry import load_agents, load_commands
        from forgekit_console.commands.router import ConsoleContext
        from forgekit_console.tui.app import ForgekitConsoleApp
        ctx = ConsoleContext(repo_root=Path("/tmp/repo"), agents=load_agents(),
                             commands=load_commands())
        return ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=ctx, submit_service=_svc(),
                                  config={"primary_provider": "ollama", "linked_providers": ["ollama"]})

    async def test_free_text_response_has_one_marker(self) -> None:
        app = self._app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.query_one("#prompt").value = "설명해줘"
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            for _ in range(40):
                await pilot.pause(0.02)
            # the response really landed (copyable store has the body, marker-free)
            resp = app._store.last_response() or ""
            self.assertIn("응답 첫 문단", resp)
            self.assertNotIn("●", resp)                 # marker is render-only, not in /copy
            # the rendered transcript shows the marker glyph exactly once (response start)
            self.assertEqual(_rendered_text(app).count("●"), 1)


def _rendered_text(app) -> str:
    """Best-effort plain text of the transcript RichLog (joined strip text)."""

    out = []
    for strip in app._transcript.lines:
        try:
            out.append(strip.text)
        except AttributeError:
            out.append(str(strip))
    return "\n".join(out)


if __name__ == "__main__":
    unittest.main()
