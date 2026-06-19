"""Process / terminal identity — make ``forgekit`` read as ``forgekit``, best-effort.

WHY this exists: the ``forgekit`` entrypoint is a Python console-script whose launcher
shebang is the venv interpreter, so the running PROCESS is ``python``. A host UI (VS
Code's terminal tab, Activity Monitor) that labels a terminal by its foreground
*process/executable* therefore shows ``Python``, not ``forgekit`` — even though the
*command name* is ``forgekit``.

There are TWO separate things, handled separately and honestly:

1. **terminal/tab title** — an OSC escape sequence (``ESC ] 0 ; <title> BEL``) that asks
   the terminal to label its window/tab. Honored when the host's tab title is
   sequence-based; ignored when the host pins the tab to the process name. TTY-only.
2. **process name** — a best-effort OS call: Linux ``prctl(PR_SET_NAME)`` (sets
   ``/proc/self/comm``, ≤15 chars), macOS ``setprogname`` (affects ``getprogname``).
   **Honest limit:** neither changes the *executable* the kernel records, so a host UI
   that reads the interpreter path (VS Code on macOS) may STILL show ``Python``.

No new dependency (stdlib ``ctypes`` only; ``setproctitle`` is intentionally NOT used).
Pure + injectable (``libc`` / ``stream`` / ``isatty`` / ``platform``) so every branch is
unit-testable without a real tty or libc.
"""

from __future__ import annotations

import sys
from typing import Optional, Tuple

APP_NAME = "forgekit"
APP_TITLE = "forgekit console"

# prctl option for the process/thread name (linux/prctl.h). Name is capped at 16 bytes
# (15 + NUL) by the kernel.
_PR_SET_NAME = 15
_COMM_MAX = 15

# keep a strong reference to the name buffer handed to setprogname (it stores the
# pointer, so the bytes must outlive the call — otherwise a dangling pointer).
_progname_ref: Optional[bytes] = None


def sanitize_title(title: str, *, max_len: int = 128) -> str:
    """Drop control characters (C0/C1, incl. ESC/BEL) and bound the length.

    A title goes to the terminal as-is, so an unsanitized one could inject escape
    sequences. We keep only printable characters and trim to *max_len*."""

    out = []
    for ch in (title or ""):
        o = ord(ch)
        if o < 0x20 or 0x7F <= o <= 0x9F:   # C0 controls, DEL, C1 controls
            continue
        out.append(ch)
    return "".join(out)[:max_len].strip()


def terminal_title_sequence(title: str) -> str:
    """The OSC sequence that sets the icon name + window title (sanitized)."""

    return f"\x1b]0;{sanitize_title(title)}\x07"


def set_terminal_title(title: str = APP_TITLE, *, stream=None, isatty: Optional[bool] = None) -> bool:
    """Write the OSC title sequence to *stream* — but ONLY when it is a real tty.

    Returns True if it was written, False if skipped (not a tty / write failed). Never
    raises. Honest: writing it does not guarantee the host honours it."""

    stream = stream if stream is not None else sys.stdout
    if isatty is None:
        try:
            isatty = bool(getattr(stream, "isatty", lambda: False)())
        except Exception:  # noqa: BLE001
            isatty = False
    if not isatty:
        return False
    try:
        stream.write(terminal_title_sequence(title))
        stream.flush()
        return True
    except Exception:  # noqa: BLE001 - a title is never worth crashing the launch
        return False


def _load_libc(platform: str):
    import ctypes
    import ctypes.util

    try:
        if platform == "darwin":
            return ctypes.CDLL("libc.dylib", use_errno=True)
        if platform.startswith("linux"):
            return ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    except Exception:  # noqa: BLE001
        return None
    return None


def set_process_name(name: str = APP_NAME, *, platform: Optional[str] = None, libc=None) -> Tuple[bool, str]:
    """Best-effort process name. (ok, via) — ``via`` names the path used or the reason.

    Linux → ``prctl(PR_SET_NAME)`` (``/proc/self/comm``); macOS → ``setprogname``. The
    *libc* is injectable for tests. Never raises."""

    global _progname_ref
    plat = platform if platform is not None else sys.platform
    lib = libc if libc is not None else _load_libc(plat)
    if lib is None:
        return False, "no libc"
    raw = (name or APP_NAME).encode("utf-8", "ignore")
    try:
        if plat == "darwin":
            _progname_ref = raw + b"\x00"        # keep alive — setprogname stores the ptr
            lib.setprogname(_progname_ref)
            return True, "setprogname"
        if plat.startswith("linux"):
            _progname_ref = raw[:_COMM_MAX] + b"\x00"
            lib.prctl(_PR_SET_NAME, _progname_ref, 0, 0, 0)
            return True, "prctl(PR_SET_NAME)"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    return False, f"unsupported platform: {plat}"


def apply_identity(*, name: str = APP_NAME, title: str = APP_TITLE, stream=None,
                   platform: Optional[str] = None, libc=None) -> dict:
    """Best-effort: set the process name AND the terminal title. Honest result dict.

    Called from the launch path; never blocks/raises. The returned dict reports exactly
    what was applied vs skipped (no fake success)."""

    pn_ok, pn_via = set_process_name(name, platform=platform, libc=libc)
    title_ok = set_terminal_title(title, stream=stream)
    return {
        "process_name_set": pn_ok,
        "process_name_via": pn_via,
        "terminal_title_set": title_ok,
    }


__all__ = (
    "APP_NAME", "APP_TITLE",
    "sanitize_title", "terminal_title_sequence", "set_terminal_title",
    "set_process_name", "apply_identity",
)
