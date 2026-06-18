"""Cross-platform desktop notification dispatch — Python-first (macOS + Windows).

Python-first by design: the standard library's ``subprocess`` drives the native
notifier on each platform, so there is NO new language / runtime to install.

  * macOS   → ``osascript -e 'display notification …'`` (built in).
  * Windows → PowerShell toast (``New-BurntToastNotification`` when the module is
    present, else a Windows.UI.Toast / balloon fallback) — built via PowerShell,
    no extra dependency required to *attempt* it.
  * other / headless → no desktop channel; returns ``(False, "none")`` honestly
    (the inbox record in :mod:`notify.service` is the durable surface).

The command BUILDERS are pure (``_macos_cmd`` / ``_windows_cmd``) so a test can
assert the right native command per platform WITHOUT running it; ``dispatch`` takes
an injectable ``runner`` for the same reason. Nothing here raises to the caller.

> Why not a new language (e.g. a Rust/Swift notifier binary)? The native CLIs
> (osascript / PowerShell) already give reliable, dependency-free delivery from
> Python. A compiled helper would add build/distribution cost for no reliability
> gain — so Python-first is the right call. (Revisit only if a platform needs rich
> actionable buttons the CLIs can't express.)
"""

from __future__ import annotations

import platform as _platform
import shutil
from typing import Callable, List, Optional, Tuple

PLATFORM_MACOS = "macos"
PLATFORM_WINDOWS = "windows"
PLATFORM_OTHER = "other"

CHANNEL_NONE = "none"


def detect_platform(system: Optional[str] = None) -> str:
    name = (system or _platform.system() or "").strip().lower()
    if name == "darwin":
        return PLATFORM_MACOS
    if name == "windows":
        return PLATFORM_WINDOWS
    return PLATFORM_OTHER


def _sanitize(text: str) -> str:
    # keep the native command string safe (no embedded quotes that break the shell)
    return (text or "").replace('"', "'").replace("\n", " ").strip()


def _macos_cmd(title: str, body: str) -> List[str]:
    t, b = _sanitize(title), _sanitize(body)
    return ["osascript", "-e", f'display notification "{b}" with title "{t}"']


def _windows_cmd(title: str, body: str) -> List[str]:
    t, b = _sanitize(title), _sanitize(body)
    # Prefer BurntToast if present; otherwise a built-in toast via WinRT. The script
    # is best-effort and self-contained (no install step inside the command).
    script = (
        "if (Get-Module -ListAvailable -Name BurntToast) { "
        f"New-BurntToastNotification -Text '{t}', '{b}' "
        "} else { "
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null; "
        f"Write-Output '{t}: {b}' }}"
    )
    return ["powershell", "-NoProfile", "-Command", script]


def build_command(title: str, body: str, *, platform: Optional[str] = None) -> Optional[List[str]]:
    """The native notifier command for *platform* (None for headless/other)."""

    plat = platform or detect_platform()
    if plat == PLATFORM_MACOS:
        return _macos_cmd(title, body)
    if plat == PLATFORM_WINDOWS:
        return _windows_cmd(title, body)
    return None


def _default_runner(cmd: List[str]) -> bool:
    import subprocess

    exe = cmd[0]
    if not shutil.which(exe):
        return False
    try:
        subprocess.run(cmd, check=False, capture_output=True, timeout=8)
        return True
    except Exception:  # noqa: BLE001 - delivery must never break the caller
        return False


def dispatch(
    title: str,
    body: str,
    *,
    platform: Optional[str] = None,
    runner: Optional[Callable[[List[str]], bool]] = None,
) -> Tuple[bool, str]:
    """Dispatch a desktop notification. Returns ``(delivered, channel)``.

    ``channel`` is the platform name when a native command was attempted, else
    ``"none"`` (headless / unsupported) — honest, never a fake success.
    """

    plat = platform or detect_platform()
    cmd = build_command(title, body, platform=plat)
    if cmd is None:
        return False, CHANNEL_NONE
    run = runner or _default_runner
    return bool(run(cmd)), plat


__all__ = (
    "PLATFORM_MACOS", "PLATFORM_WINDOWS", "PLATFORM_OTHER", "CHANNEL_NONE",
    "detect_platform", "build_command", "dispatch",
)
