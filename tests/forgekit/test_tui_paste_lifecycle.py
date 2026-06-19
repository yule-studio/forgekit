"""Large-paste lifecycle — raw payload preserved + expand / resend / copy by id.

A long paste is shown compactly (`[Pasted #1 · N lines]`) but its RAW text is preserved
(paste_store), so it is a real retrievable payload, not a dead placeholder:
`/paste expand <id>` shows the body, `/paste resend <id>` re-submits the raw,
`/copy paste <id>` copies the raw (real OS round-trip on macOS). Provider always gets
the FULL text — never the placeholder.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

_TEXTUAL = importlib.util.find_spec("textual") is not None


# --------------------------------------------------------------------------- #
# pure: the paste store
# --------------------------------------------------------------------------- #
class PasteStorePureTests(unittest.TestCase):
    def test_preserves_raw_and_addressable_by_id(self):
        from forgekit_console.tui.paste_store import PasteStore, is_large
        s = PasteStore()
        raw = "\n".join(f"line {i}" for i in range(50))
        p = s.add(raw)
        self.assertEqual(p.id, 1)
        self.assertEqual(p.line_count, 50)
        self.assertEqual(s.get(1).raw_text, raw)         # raw preserved, not a placeholder
        self.assertIsNone(s.get(99))
        self.assertIn("Pasted #1", p.compact_label())
        self.assertTrue(is_large(raw))
        self.assertFalse(is_large("one\ntwo"))


# --------------------------------------------------------------------------- #
# pilot: the real composer paste lifecycle
# --------------------------------------------------------------------------- #
@unittest.skipUnless(_TEXTUAL, "textual 필요")
class PasteLifecyclePilotTests(unittest.IsolatedAsyncioTestCase):
    def _svc(self):
        from forgekit_console.chat import models as m

        class Svc:
            def __init__(s): s.got = []
            def submit(s, t, **_):
                s.got.append(t)
                return m.SubmitResult(ok=True, mode=m.MODE_LIVE, category=m.CAT_OK, text="ok",
                                      provider_id="ollama", provider_label="Ollama",
                                      source=m.SOURCE_LOCAL_DEFAULT, model="g")
        return Svc()

    def _app(self, svc):
        from forgekit_console.commands.registry import load_agents, load_commands
        from forgekit_console.commands.router import ConsoleContext
        from forgekit_console.tui.app import ForgekitConsoleApp
        ctx = ConsoleContext(repo_root=Path("/tmp/repo"), agents=load_agents(), commands=load_commands())
        return ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=ctx, submit_service=svc)

    def _body(self, app):
        from forgekit_console.tui.transcript import Transcript
        return "\n".join("".join(seg.text for seg in s) for s in app.query_one(Transcript).lines)

    async def test_large_paste_preserved_compact_and_full_submit(self):
        from textual import events
        from forgekit_console.tui import clipboard
        raw = "\n".join(f"코드 {i} mix {i}" for i in range(60))
        clipboard.copy_text(raw)
        svc = self._svc()
        app = self._app(svc)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.post_message(events.Paste("[Pasted text #7 +59 lines]"))
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(len(app._pastes.items), 1)
            self.assertEqual(app._pastes.get(1).line_count, 60)              # raw preserved
            self.assertEqual(len(svc.got[-1].splitlines()), 60)             # FULL text submitted
            self.assertNotIn("[Pasted", svc.got[-1])                         # not the placeholder
            body = self._body(app)
            self.assertIn("Pasted #1", body)                                # compact block
            self.assertIn("/paste expand 1", body)                          # lifecycle seam shown

    async def test_expand_and_resend(self):
        from textual import events
        from forgekit_console.tui import clipboard
        raw = "\n".join(f"row {i}" for i in range(30))
        clipboard.copy_text(raw)
        svc = self._svc()
        app = self._app(svc)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.post_message(events.Paste("[Pasted text #2 +29 lines]"))
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause(); await app.workers.wait_for_complete(); await pilot.pause()
            n = len(svc.got)
            app._paste_dispatch(["expand", "1"])
            await pilot.pause()
            self.assertIn("row 29", self._body(app))                         # expand shows raw body
            app._paste_dispatch(["resend", "1"])
            await pilot.pause(); await app.workers.wait_for_complete(); await pilot.pause()
            self.assertEqual(len(svc.got[-1].splitlines()), 30)             # resend = full raw again
            self.assertGreater(len(svc.got), n)

    async def test_copy_paste_copies_raw_not_placeholder(self):
        from textual import events
        from forgekit_console.tui import clipboard
        raw = "\n".join(f"line {i} 한글" for i in range(40))
        clipboard.copy_text(raw)
        captured = {}
        orig = clipboard.copy_text
        clipboard.copy_text = lambda t: (captured.__setitem__("t", t) or (True, "stub"))
        try:
            app = self._app(self._svc())
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                # restore real copy_text only for the paste-rehydrate read; stub for /copy
                clipboard.copy_text = orig
                app.post_message(events.Paste("[Pasted text #3 +39 lines]"))
                await pilot.pause()
                await pilot.press("enter"); await pilot.pause()
                await app.workers.wait_for_complete(); await pilot.pause()
                clipboard.copy_text = lambda t: (captured.__setitem__("t", t) or (True, "stub"))
                app._copy_dispatch(["paste", "1"])
                await pilot.pause()
                self.assertEqual(captured["t"], raw)                         # RAW, not placeholder
        finally:
            clipboard.copy_text = orig


# --------------------------------------------------------------------------- #
# real OS round-trip (macOS): /copy paste → pbpaste matches the raw payload
# --------------------------------------------------------------------------- #
@unittest.skipUnless(
    _TEXTUAL and sys.platform == "darwin" and shutil.which("pbcopy") and shutil.which("pbpaste"),
    "pbcopy/pbpaste 필요",
)
class PasteCopyReadbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_copy_paste_real_readback(self):
        from textual import events
        from forgekit_console.tui import clipboard
        from forgekit_console.commands.registry import load_agents, load_commands
        from forgekit_console.commands.router import ConsoleContext
        from forgekit_console.tui.app import ForgekitConsoleApp
        from forgekit_console.chat import models as m

        class Svc:
            def submit(self, t, **_):
                return m.SubmitResult(ok=True, mode=m.MODE_LIVE, category=m.CAT_OK, text="ok",
                                      provider_id="ollama", provider_label="Ollama",
                                      source=m.SOURCE_LOCAL_DEFAULT, model="g")

        raw = "\n".join(f"raw line {i}" for i in range(45))
        clipboard.copy_text(raw)
        ctx = ConsoleContext(repo_root=Path("/tmp/repo"), agents=load_agents(), commands=load_commands())
        app = ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=ctx, submit_service=Svc())
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.post_message(events.Paste("[Pasted text #1 +44 lines]"))
            await pilot.pause()
            await pilot.press("enter"); await pilot.pause()
            await app.workers.wait_for_complete(); await pilot.pause()
            app._copy_dispatch(["paste", "1"])
            await pilot.pause()
            self.assertEqual(clipboard.read_text(), raw)   # real pbcopy→pbpaste of the RAW paste


if __name__ == "__main__":
    unittest.main()
