"""Process / terminal identity — title sanitize, OSC sequence, tty gating, best-effort
process-name per platform (fake libc), and entrypoint wiring.

Pure / injectable: no real tty or libc needed. The honest limit (host UIs may still
show the interpreter) is a behaviour fact, documented — not asserted here.
"""

from __future__ import annotations

import importlib.util
import io
import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import proc_identity as pi


class FakeLibc:
    def __init__(self):
        self.calls = []

    def setprogname(self, buf):
        self.calls.append(("setprogname", bytes(buf)))

    def prctl(self, option, buf, *rest):
        self.calls.append(("prctl", option, bytes(buf)))


class TitleTests(unittest.TestCase):
    def test_sanitize_strips_control_chars(self):
        # ESC + BEL (the active title delimiters) and newlines must be removed
        out = pi.sanitize_title("forge\x1b]0;evil\x07kit\nx")
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\x07", out)
        self.assertNotIn("\n", out)
        self.assertIn("forge", out)

    def test_sanitize_bounds_length(self):
        self.assertLessEqual(len(pi.sanitize_title("a" * 500, max_len=20)), 20)

    def test_osc_sequence_shape(self):
        seq = pi.terminal_title_sequence("forgekit console")
        self.assertTrue(seq.startswith("\x1b]0;"))
        self.assertTrue(seq.endswith("\x07"))
        self.assertIn("forgekit console", seq)

    def test_title_written_only_on_tty(self):
        buf = io.StringIO()
        self.assertFalse(pi.set_terminal_title("x", stream=buf, isatty=False))   # no-op off-tty
        self.assertEqual(buf.getvalue(), "")
        buf2 = io.StringIO()
        self.assertTrue(pi.set_terminal_title("forgekit", stream=buf2, isatty=True))
        self.assertEqual(buf2.getvalue(), "\x1b]0;forgekit\x07")


class ProcessNameTests(unittest.TestCase):
    def test_macos_uses_setprogname(self):
        fake = FakeLibc()
        ok, via = pi.set_process_name("forgekit", platform="darwin", libc=fake)
        self.assertTrue(ok)
        self.assertEqual(via, "setprogname")
        self.assertEqual(fake.calls[0][0], "setprogname")
        self.assertIn(b"forgekit", fake.calls[0][1])

    def test_linux_uses_prctl_set_name_capped(self):
        fake = FakeLibc()
        ok, via = pi.set_process_name("forgekit-very-long-name", platform="linux", libc=fake)
        self.assertTrue(ok)
        self.assertIn("prctl", via)
        kind, option, buf = fake.calls[0]
        self.assertEqual(kind, "prctl")
        self.assertEqual(option, 15)                       # PR_SET_NAME
        self.assertLessEqual(len(buf.rstrip(b"\x00")), 15)  # comm capped at 15

    def test_no_libc_is_honest_failure(self):
        ok, via = pi.set_process_name("forgekit", platform="linux", libc=None)
        # on a box without a loadable libc this is False; with one it's True — either way
        # `via` is a non-empty reason/path, never a fake claim.
        self.assertTrue(via)

    def test_unsupported_platform(self):
        ok, via = pi.set_process_name("forgekit", platform="sunos", libc=FakeLibc())
        self.assertFalse(ok)
        self.assertIn("unsupported", via)

    def test_apply_identity_reports_honestly(self):
        fake = FakeLibc()
        res = pi.apply_identity(stream=io.StringIO(), platform="linux", libc=fake)
        self.assertTrue(res["process_name_set"])
        self.assertFalse(res["terminal_title_set"])        # StringIO is not a tty
        self.assertIn("prctl", res["process_name_via"])


@unittest.skipUnless(importlib.util.find_spec("textual") is not None, "textual 필요")
class EntrypointWiringTests(unittest.TestCase):
    def test_launch_console_applies_identity(self):
        from forgekit_console.app import main as appmain
        from forgekit_console import proc_identity
        from forgekit_console.tui.app import ForgekitConsoleApp
        from pathlib import Path

        called = {"n": 0}
        orig_apply = proc_identity.apply_identity
        orig_run = ForgekitConsoleApp.run
        proc_identity.apply_identity = lambda **kw: (called.__setitem__("n", called["n"] + 1) or {})
        ForgekitConsoleApp.run = lambda self, **kw: 0
        try:
            appmain.launch_console(repo_root=Path("/tmp/repo"))
        finally:
            proc_identity.apply_identity = orig_apply
            ForgekitConsoleApp.run = orig_run
        self.assertEqual(called["n"], 1)   # identity applied on the launch path


if __name__ == "__main__":
    unittest.main()
