"""launchd LaunchAgent auto-install (final-completion lane B) — render + place, honest.

Replaces the manual ``sed`` flow in ``deploy/launchd/README.md``: resolve the four
template placeholders from the live environment, render the COMMITTED template (single
source — no embedded duplicate that could drift), write it to the per-user
``~/Library/LaunchAgents`` dir, and emit the EXACT ``launchctl`` command.

Honest boundaries:
- The daemon START stays operator-gated. By default this installs the FILE ONLY and
  prints the load command — it does NOT run ``launchctl`` (no unapproved background
  start). ``load=True`` is the operator opt-in that runs + verifies it.
- Never a fake "installed": an unsubstituted placeholder aborts, a write error is
  surfaced, and a load is confirmed via ``launchctl`` (not assumed).
- macOS only (the placeholder template is launchd); the Linux/systemd path stays the
  existing manual template. The macOS lid-close suspend limit is restated, not hidden.

Pure-ish: rendering / resolution / path logic is unit-testable; the file write + the
optional ``launchctl`` run are injectable (``dest_path`` / ``runner``).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping, Optional, Tuple

LABEL = "com.forgekit.runtime"
_PLIST_REL = ("deploy", "launchd", "com.forgekit.runtime.plist")
_PLACEHOLDER_RE = re.compile(r"__[A-Z_]+__")


def template_path(repo_root) -> Path:
    """The committed launchd template (single source of truth)."""

    return Path(repo_root).joinpath(*_PLIST_REL)


def resolve_bin(bin_path: Optional[str] = None) -> str:
    """Absolute path to the ``forgekit`` entrypoint — explicit, else PATH, else venv bin."""

    if bin_path:
        return str(bin_path)
    found = shutil.which("forgekit")
    if found:
        return found
    return str(Path(sys.executable).parent / "forgekit")


def resolve_values(*, repo_root, env: Optional[Mapping[str, str]] = None,
                   bin_path: Optional[str] = None, user_home: Optional[str] = None) -> dict:
    """Resolve the four template placeholders from the live environment (honest paths)."""

    env = env or {}
    home = str(user_home or env.get("HOME") or Path.home())
    fk_home = str(env.get("FORGEKIT_HOME") or (Path(home) / ".forgekit"))
    return {
        "__FORGEKIT_BIN__": resolve_bin(bin_path),
        "__REPO_ROOT__": str(Path(repo_root).resolve()),
        "__FORGEKIT_HOME__": fk_home,
        "__USER_HOME__": home,
    }


def render(template_text: str, values: Mapping[str, str]) -> Tuple[str, Tuple[str, ...]]:
    """Substitute placeholders → ``(rendered, leftover_placeholders)``. Leftovers are an
    error signal (never ship a half-rendered unit)."""

    out = template_text
    for key, val in values.items():
        out = out.replace(key, val)
    missing = tuple(sorted(set(_PLACEHOLDER_RE.findall(out))))
    return out, missing


def target_path(user_home: str) -> Path:
    return Path(user_home) / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def load_command(plist_path) -> str:
    return f"launchctl bootstrap gui/$(id -u) {plist_path}"


def _default_runner(argv) -> Tuple[bool, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        return p.returncode == 0, (p.stderr or p.stdout or "").strip()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def install(*, repo_root, env: Optional[Mapping[str, str]] = None,
            user_home: Optional[str] = None, bin_path: Optional[str] = None,
            dest_path: Optional[str] = None, load: bool = False,
            runner: Optional[Callable[[list], Tuple[bool, str]]] = None,
            platform: Optional[str] = None) -> Tuple[bool, Tuple[str, ...]]:
    """Render + place the LaunchAgent. ``load=True`` also runs + verifies launchctl.

    Returns ``(ok, lines)``. ``ok`` is False on any honest failure (non-macOS, missing
    template, leftover placeholder, write error, launchctl failure) — never a fake success.
    """

    platform = platform or sys.platform
    env = dict(env or {})
    home = str(user_home or env.get("HOME") or Path.home())
    if platform != "darwin":
        return False, (
            "launchd 자동설치는 macOS 전용입니다 — Linux 는 systemd 경로(`deploy/systemd/`, 수동)를 쓰세요.",
        )
    tpl = template_path(repo_root)
    if not tpl.exists():
        return False, (f"launchd 템플릿 없음: {tpl} — `--repo-root` 를 ForgeKit 체크아웃으로 지정하세요.",)
    values = resolve_values(repo_root=repo_root, env=env, bin_path=bin_path, user_home=home)
    rendered, missing = render(tpl.read_text(encoding="utf-8"), values)
    if missing:
        return False, (f"치환되지 않은 placeholder: {', '.join(missing)} — 설치 중단(가짜 설치 금지).",)
    dest = Path(dest_path) if dest_path else target_path(home)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        return False, (f"unit 파일 쓰기 실패: {exc}",)

    lines = [
        f"✓ LaunchAgent 작성됨: {dest}",
        f"  bin={values['__FORGEKIT_BIN__']}  repo={values['__REPO_ROOT__']}",
        f"  FORGEKIT_HOME={values['__FORGEKIT_HOME__']}",
    ]
    cmd = load_command(dest)
    if not load:
        lines.append(f"  load(operator 승인 후): {cmd}")
        lines.append("  자동 load 안 함 — daemon 시작은 operator 결정입니다 (`--load` 로 지금 로드).")
        lines.append("  honest: macOS lid-close 는 host 를 suspend — 24h 는 caffeinate/pmset 또는 systemd 경로.")
        return True, tuple(lines)

    run = runner or _default_runner
    uid = os.getuid() if hasattr(os, "getuid") else 0
    ok, out = run(["launchctl", "bootstrap", f"gui/{uid}", str(dest)])
    if not ok:
        lines.append(f"  ⚠ launchctl bootstrap 실패: {out or '(no output)'} — 수동: {cmd}")
        return False, tuple(lines)
    verified, _ = run(["launchctl", "print", f"gui/{uid}/{LABEL}"])
    lines.append("✓ launchctl bootstrap 완료" + (" · 등록 확인됨" if verified
                 else " · (print 확인 실패 — `launchctl list | grep forgekit` 로 점검)"))
    lines.append("  honest: macOS lid-close suspend 한계는 동일합니다.")
    return True, tuple(lines)


__all__ = (
    "LABEL", "template_path", "resolve_bin", "resolve_values", "render",
    "target_path", "load_command", "install",
)
