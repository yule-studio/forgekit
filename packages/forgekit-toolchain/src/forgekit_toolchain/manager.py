"""Toolchain manager seam — the only place that touches a real version manager (mise).

``ToolchainManager`` is the injectable boundary; ``MiseManager`` is the real one. The
hard rule: it NEVER fakes a switch or a version. If ``mise`` is not installed,
``available()`` is False and every query returns "I don't know" (None) — callers then
surface ``manager_missing`` honestly instead of pretending the env changed.

``mise`` is the first-class manager (asdf-compatible ``.tool-versions`` + ``.mise.toml``).
The seam is generic so an asdf/other backend can be added without touching plan/verify.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Callable, Dict, List, Optional, Protocol, Sequence, Tuple


class ToolchainManager(Protocol):
    name: str

    def available(self) -> bool: ...
    def current(self) -> Dict[str, str]: ...                 # tool -> active version
    def run(self, argv: Sequence[str]) -> Tuple[int, str]: ...   # (exit_code, output)


class MiseManager:
    """Real mise backend. Read-only by default; ``run`` executes a given argv.

    All IO is injected (``which`` / ``check_output`` / ``runner``) so tests never shell
    out and we keep one honest code path.
    """

    name = "mise"

    def __init__(
        self,
        *,
        which: Optional[Callable[[str], Optional[str]]] = None,
        capture: Optional[Callable[[Sequence[str]], str]] = None,
        runner: Optional[Callable[[Sequence[str]], Tuple[int, str]]] = None,
    ) -> None:
        self._which = which or shutil.which
        self._capture = capture or self._default_capture
        self._runner = runner or self._default_runner

    # ── honest availability ─────────────────────────────────────────────────
    def available(self) -> bool:
        return bool(self._which(self.name))

    # ── read: active versions (`mise current`) ──────────────────────────────
    def current(self) -> Dict[str, str]:
        if not self.available():
            return {}
        try:
            raw = self._capture([self.name, "ls", "--current", "--json"])
            return self._parse_ls_json(raw)
        except Exception:
            pass
        try:
            return self._parse_current_text(self._capture([self.name, "current"]))
        except Exception:
            return {}

    # ── write: run an explicit argv (used by plan execution) ────────────────
    def run(self, argv: Sequence[str]) -> Tuple[int, str]:
        if not self.available():
            return (127, "mise not installed")
        return self._runner(list(argv))

    # ── default IO (only hit in production) ─────────────────────────────────
    @staticmethod
    def _default_capture(argv: Sequence[str]) -> str:
        return subprocess.check_output(list(argv), text=True, stderr=subprocess.DEVNULL)

    @staticmethod
    def _default_runner(argv: Sequence[str]) -> Tuple[int, str]:
        proc = subprocess.run(list(argv), capture_output=True, text=True)
        return (proc.returncode, (proc.stdout or "") + (proc.stderr or ""))

    # ── parsers ─────────────────────────────────────────────────────────────
    @staticmethod
    def _parse_ls_json(raw: str) -> Dict[str, str]:
        data = json.loads(raw)
        out: Dict[str, str] = {}
        # mise emits {tool: [{version, ...}, ...]} for `ls --current --json`.
        if isinstance(data, dict):
            for tool, entries in data.items():
                if isinstance(entries, list) and entries:
                    ver = entries[0].get("version") if isinstance(entries[0], dict) else None
                    if ver:
                        out[tool] = str(ver)
        return out

    @staticmethod
    def _parse_current_text(raw: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for line in (raw or "").splitlines():
            parts = line.split()
            if len(parts) >= 2:
                out[parts[0].strip()] = parts[1].strip()
        return out


class NullManager:
    """No manager present — every call is honest about not knowing. For tests/fallback."""

    name = ""

    def available(self) -> bool:
        return False

    def current(self) -> Dict[str, str]:
        return {}

    def run(self, argv: Sequence[str]) -> Tuple[int, str]:
        return (127, "no toolchain manager")


def default_manager() -> ToolchainManager:
    return MiseManager()


__all__ = ("ToolchainManager", "MiseManager", "NullManager", "default_manager")
