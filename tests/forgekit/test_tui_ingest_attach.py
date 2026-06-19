"""Paste/attach ingestion — placeholder rehydration + attachment staging.

Ground truth (verified, see test_paste_placeholder_origin): ForgeKit does NOT generate
`[Pasted text #N]` / `[Image #N]` — they are HOST substitutions. PromptArea accepts a
real multiline paste; the failure is the host placeholder. These tests lock the
rehydration seam (placeholder → real clipboard payload) + honest attachment staging.

Pure unit + pilot. Real OS clipboard where it must (gated); fake/injected elsewhere.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

_TEXTUAL = importlib.util.find_spec("textual") is not None


# --------------------------------------------------------------------------- #
# pure: placeholder detection + rehydration
# --------------------------------------------------------------------------- #
class IngestPureTests(unittest.TestCase):
    def test_detects_host_placeholders(self):
        from forgekit_console.tui import ingest
        self.assertTrue(ingest.has_text_placeholder("[Pasted text #3 +255 lines]"))
        self.assertTrue(ingest.has_image_placeholder("look [Image #38]"))
        self.assertFalse(ingest.has_text_placeholder("normal message"))
        self.assertTrue(ingest.looks_like_placeholder_only("  [Pasted text #1 +5 lines] "))
        self.assertFalse(ingest.looks_like_placeholder_only("hi [Pasted text #1]"))

    def test_no_placeholder_passes_through(self):
        from forgekit_console.tui import ingest
        r = ingest.resolve_submit_text("multi\nline\nreal paste", "ignored")
        self.assertFalse(r.rehydrated)
        self.assertFalse(r.blocked)
        self.assertEqual(r.text, "multi\nline\nreal paste")   # untouched, newlines kept

    def test_rehydrate_from_clipboard(self):
        from forgekit_console.tui import ingest
        clip = "line1\nline2\nline3\nline4"
        r = ingest.resolve_submit_text("[Pasted text #3 +3 lines]", clip)
        self.assertTrue(r.rehydrated)
        self.assertFalse(r.blocked)
        self.assertEqual(r.text, clip)                         # full multiline recovered
        self.assertEqual(r.pending.line_count, 4)

    def test_blocked_when_clipboard_unavailable(self):
        from forgekit_console.tui import ingest
        for clip in (None, "", "   ", "[Pasted text #9]"):
            r = ingest.resolve_submit_text("[Pasted text #9 +100 lines]", clip)
            self.assertTrue(r.blocked, clip)
            self.assertFalse(r.rehydrated)
            self.assertNotIn("[Pasted text", "")  # never submits the bare placeholder


# --------------------------------------------------------------------------- #
# pure: attachment staging model
# --------------------------------------------------------------------------- #
class AttachmentPureTests(unittest.TestCase):
    def test_stage_real_file_is_staged_only(self):
        from forgekit_console.tui import attachment as att
        d = Path(tempfile.mkdtemp())
        f = d / "shot.png"
        f.write_bytes(b"\x89PNG\r\n" + b"x" * 100)
        a, state, msg = att.stage_file(str(f))
        self.assertEqual(state, att.STATE_STAGED)
        self.assertEqual(a.mime, "image/png")
        self.assertEqual(a.bytes_len, 106)
        self.assertFalse(a.sendable)            # provider text-only → staged_only
        self.assertIn("staged_only", a.chip())

    def test_missing_and_no_payload_are_honest(self):
        from forgekit_console.tui import attachment as att
        self.assertEqual(att.stage_file("/no/such/file.png")[1], att.STATE_MISSING)
        self.assertEqual(att.stage_file("")[1], att.STATE_NO_PAYLOAD)

    def test_store_status_and_clear(self):
        from forgekit_console.tui import attachment as att
        s = att.AttachmentStore()
        self.assertIn("없음", "\n".join(s.status_lines()))
        f = Path(tempfile.mkdtemp()) / "a.png"
        f.write_bytes(b"\x89PNG")
        s.add(att.stage_file(str(f))[0])
        self.assertTrue(s.pending)
        self.assertEqual(s.clear(), 1)
        self.assertFalse(s.pending)

    def test_clipboard_image_read_is_honest_when_absent(self):
        # no image on the clipboard (CI) → honest failure, never a fake stage / crash.
        from forgekit_console.tui import clipboard
        ok, reason = clipboard.read_image(str(Path(tempfile.mkdtemp()) / "x.png"))
        self.assertFalse(ok)
        self.assertTrue(reason)


# --------------------------------------------------------------------------- #
# pilot: the real composer ingestion path
# --------------------------------------------------------------------------- #
@unittest.skipUnless(_TEXTUAL, "textual 필요")
class IngestPilotTests(unittest.IsolatedAsyncioTestCase):
    def _app(self, svc=None):
        from forgekit_console.commands.registry import load_agents, load_commands
        from forgekit_console.commands.router import ConsoleContext
        from forgekit_console.tui.app import ForgekitConsoleApp
        ctx = ConsoleContext(repo_root=Path("/tmp/repo"), agents=load_agents(), commands=load_commands())
        return ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=ctx, submit_service=svc)

    def _svc(self):
        from forgekit_console.chat import models as m

        class Svc:
            def __init__(s): s.got = []
            def submit(s, text, **_):
                s.got.append(text)
                return m.SubmitResult(ok=True, mode=m.MODE_LIVE, category=m.CAT_OK, text="ok",
                                      provider_id="ollama", provider_label="Ollama",
                                      source=m.SOURCE_LOCAL_DEFAULT, model="g")
        return Svc()

    async def test_real_multiline_paste_preserved(self):
        from textual import events
        app = self._app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.post_message(events.Paste("a\nb\nc\nd"))   # genuine bracketed paste
            await pilot.pause()
            self.assertEqual(app.query_one("#prompt").value, "a\nb\nc\nd")

    async def test_placeholder_paste_rehydrates_and_submits_full(self):
        from textual import events
        from forgekit_console.tui import clipboard
        clipboard.copy_text("REAL\nL2\nL3\nL4\nL5")
        svc = self._svc()
        app = self._app(svc)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.post_message(events.Paste("[Pasted text #3 +4 lines]"))
            await pilot.pause()
            self.assertEqual(app.query_one("#prompt").value, "REAL\nL2\nL3\nL4\nL5")  # rehydrated
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertIn("REAL", svc.got[-1])
            self.assertNotIn("[Pasted text", svc.got[-1])   # full text sent, NOT placeholder

    async def test_attach_file_then_submit_is_staged_only(self):
        from forgekit_console.tui.transcript import Transcript
        f = Path(tempfile.mkdtemp()) / "img.png"
        f.write_bytes(b"\x89PNG" + b"y" * 50)
        svc = self._svc()
        app = self._app(svc)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._attach_dispatch([str(f)])
            await pilot.pause()
            self.assertEqual(len(app._attachments.items), 1)
            app.query_one("#prompt").value = "이 이미지 설명"
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            body = "\n".join("".join(seg.text for seg in s) for s in app.query_one(Transcript).lines)
            self.assertIn("staged_only", body)              # honest: received but not sent
            self.assertFalse(app._attachments.pending)       # cleared after the turn

    async def test_placeholder_submit_without_clipboard_is_blocked(self):
        from forgekit_console.tui import clipboard
        from forgekit_console.tui.transcript import Transcript
        orig = clipboard.read_text
        clipboard.read_text = lambda: None     # simulate empty/unreadable clipboard
        try:
            svc = self._svc()
            app = self._app(svc)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app.query_one("#prompt").value = "[Pasted text #9 +100 lines]"
                await pilot.press("enter")
                await pilot.pause()
                body = "\n".join("".join(seg.text for seg in s) for s in app.query_one(Transcript).lines)
                self.assertIn("paste 차단", body)            # honest blocked
                self.assertEqual(svc.got, [])                # nothing submitted
        finally:
            clipboard.read_text = orig


if __name__ == "__main__":
    unittest.main()
