"""Clipboard copy — real OS path, honest unsupported. Pure stdlib (subprocess).

macOS ``pbcopy`` / Windows ``clip`` / Linux ``xclip``|``xsel``. Returns (ok, reason) so
the console surfaces a real success/failure — never a "copy supported (예정)" claim.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Optional, Tuple


def _cmd() -> Optional[list]:
    if sys.platform == "darwin" and shutil.which("pbcopy"):
        return ["pbcopy"]
    if sys.platform.startswith("win"):
        return ["clip"]
    if shutil.which("xclip"):
        return ["xclip", "-selection", "clipboard"]
    if shutil.which("xsel"):
        return ["xsel", "--clipboard", "--input"]
    return None


def copy_text(text: str) -> Tuple[bool, str]:
    """Copy *text* to the OS clipboard. (ok, reason). Honest unsupported/failure.

    An EMPTY payload is treated as a failure — copying nothing is never a success the
    operator would want reported as "copied"."""

    text = text or ""
    if not text.strip():
        return False, "복사할 내용이 비어 있습니다 (empty payload)"
    cmd = _cmd()
    if cmd is None:
        return False, "clipboard 도구 없음 (macOS=pbcopy / Linux=xclip|xsel / Win=clip) — copy 미지원"
    try:
        p = subprocess.run(cmd, input=text.encode("utf-8"), timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"copy 실패: {type(exc).__name__}: {exc}"
    if p.returncode != 0:
        return False, f"copy 실패: {cmd[0]} rc={p.returncode}"
    return True, f"{len(text)}자 복사됨 ({cmd[0]})"


def _read_cmd() -> Optional[list]:
    """The OS clipboard READ command (paste), mirroring :func:`_cmd`. None if absent."""

    if sys.platform == "darwin" and shutil.which("pbpaste"):
        return ["pbpaste"]
    if shutil.which("xclip"):
        return ["xclip", "-selection", "clipboard", "-o"]
    if shutil.which("xsel"):
        return ["xsel", "--clipboard", "--output"]
    return None


def read_text() -> Optional[str]:
    """Read the OS clipboard back (paste). None when no reader is available / on error.

    Used to VERIFY a copy actually landed (readback), not to assume pbcopy success."""

    cmd = _read_cmd()
    if cmd is None:
        return None
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    return p.stdout.decode("utf-8", errors="replace")


# --- clipboard IMAGE (attachment staging) ----------------------------------
# macOS: pngpaste (if installed) writes the clipboard image straight to a file;
# otherwise an osascript snippet writes the «class PNGf» clipboard flavour. Linux:
# xclip can read image/png. We NEVER pretend an image exists — None reason explains.

_OSA_WRITE_PNG = (
    'on run argv\n'
    '  set outFile to POSIX file (item 1 of argv)\n'
    '  try\n'
    '    set pngData to (the clipboard as «class PNGf»)\n'
    '  on error\n'
    '    return "no-image"\n'
    '  end try\n'
    '  set fh to open for access outFile with write permission\n'
    '  set eof fh to 0\n'
    '  write pngData to fh\n'
    '  close access fh\n'
    '  return "ok"\n'
    'end run'
)


def read_image(dest_path: str) -> Tuple[bool, str]:
    """Write the clipboard IMAGE to *dest_path*. (ok, mime|reason).

    Real OS read (pngpaste → osascript on macOS, xclip on Linux). Honest failure when
    there is no image on the clipboard or no reader is available — never a fake stage.
    """

    dest = str(dest_path)
    if sys.platform == "darwin":
        if shutil.which("pngpaste"):
            try:
                p = subprocess.run(["pngpaste", dest], timeout=5,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError) as exc:
                return False, f"pngpaste 실패: {exc}"
            return (True, "image/png") if p.returncode == 0 else (False, "클립보드에 이미지 없음 (pngpaste)")
        if shutil.which("osascript"):
            try:
                p = subprocess.run(["osascript", "-e", _OSA_WRITE_PNG, dest],
                                   capture_output=True, timeout=8)
            except (OSError, subprocess.SubprocessError) as exc:
                return False, f"osascript 실패: {exc}"
            out = (p.stdout or b"").decode("utf-8", errors="replace").strip()
            return (True, "image/png") if out == "ok" else (False, "클립보드에 이미지 없음 (osascript)")
        return False, "이미지 reader 없음 (pngpaste 설치 권장: brew install pngpaste)"
    if shutil.which("xclip"):
        try:
            p = subprocess.run(["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
                               capture_output=True, timeout=5)
            if p.returncode == 0 and p.stdout:
                with open(dest, "wb") as fh:
                    fh.write(p.stdout)
                return True, "image/png"
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"xclip image 실패: {exc}"
        return False, "클립보드에 이미지 없음 (xclip)"
    return False, "이미지 clipboard reader 없음 (macOS=pngpaste/osascript, Linux=xclip)"


__all__ = ("copy_text", "read_text", "read_image")
