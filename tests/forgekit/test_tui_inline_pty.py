"""Inline vs full — REAL pseudo-terminal probe (not headless assumption).

The headless harness can't prove what control bytes the driver emits. This spawns the
actual app under a PTY in both modes and inspects the raw output: full mode MUST emit
the alternate-screen + mouse sequences; inline mode MUST emit NEITHER. This is the
runtime evidence behind the "inline avoids alt-screen + mouse capture" claim.

POSIX-only (uses os.fork/pty) and requires textual — skipped otherwise.
"""

from __future__ import annotations

import importlib.util
import os
import select
import sys
import time
import unittest

_TEXTUAL = importlib.util.find_spec("textual") is not None
_POSIX = hasattr(os, "fork") and sys.platform != "win32"

_CHILD = """
import sys, threading
sys.path.insert(0, "apps/forgekit-console/src")
from pathlib import Path
from forgekit_console.tui.app import ForgekitConsoleApp
from forgekit_console.commands.registry import load_agents, load_commands
from forgekit_console.commands.router import ConsoleContext
ctx = ConsoleContext(repo_root=Path("/tmp/repo"), agents=load_agents(), commands=load_commands())
app = ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=ctx, inline=%(inline)s)
def _quit():
    import time; time.sleep(1.0); app.call_from_thread(app.exit)
threading.Thread(target=_quit, daemon=True).start()
app.run(**%(kwargs)r)
"""


def _capture(inline: bool, kwargs: dict) -> bytes:
    import pty

    code = _CHILD % {"inline": inline, "kwargs": kwargs}
    pid, fd = pty.fork()
    if pid == 0:  # child
        os.execv(sys.executable, [sys.executable, "-c", code])
    buf = b""
    t0 = time.time()
    while time.time() - t0 < 5:
        r, _, _ = select.select([fd], [], [], 0.3)
        if r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            buf += data
    try:
        os.waitpid(pid, 0)
    except Exception:  # noqa: BLE001
        pass
    return buf


@unittest.skipUnless(_TEXTUAL and _POSIX, "POSIX pty + textual 필요")
class InlinePtyTests(unittest.TestCase):
    def test_full_enters_altscreen_and_mouse(self):
        out = _capture(False, {})
        self.assertIn(b"\x1b[?1049h", out)   # alt-screen enter
        self.assertIn(b"\x1b[?1000h", out)   # mouse tracking

    def test_inline_avoids_altscreen_and_mouse(self):
        out = _capture(True, {"inline": True, "inline_no_clear": True, "mouse": False})
        self.assertNotIn(b"\x1b[?1049h", out)   # NO alt-screen → native scrollback preserved
        self.assertNotIn(b"\x1b[?1000h", out)   # NO mouse capture → terminal owns selection
        self.assertNotIn(b"\x1b[?1006h", out)   # NO SGR mouse


if __name__ == "__main__":
    unittest.main()
