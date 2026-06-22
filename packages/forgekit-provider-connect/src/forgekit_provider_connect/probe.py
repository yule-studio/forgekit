"""Connection probes — the only IO of the connect layer (injectable, fake-able).

The probe answers narrow factual questions about the local machine (is a CLI logged in?
is an API key present? is the ollama daemon up? which models are installed?). The
diagnosis layer (:mod:`diagnose`) turns those facts into an honest
:class:`status.ConnectionStatus`. Splitting IO out keeps diagnosis pure and unit-testable
with a :class:`FakeProbe`, and keeps the honesty rule enforceable: a probe NEVER claims a
connection it can't observe — ``cli_authenticated`` returns ``None`` when it genuinely
cannot tell, and the diagnosis treats unknown as not-connected (never green-washed).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Mapping, Optional, Protocol, Tuple


class ConnectionProbe(Protocol):
    """Narrow factual probes about the local environment."""

    def cli_authenticated(self, provider_id: str) -> Optional[bool]:
        """True if a CLI login/session is detected, False if the CLI is present but not
        logged in, None if it cannot be determined (CLI absent / undetectable)."""

    def api_key(self, provider_id: str, env: Optional[Mapping[str, str]] = None) -> str:
        """The provider's API key from the environment (``<ID>_API_KEY``); '' if absent."""

    def daemon_reachable(self, endpoint: str) -> bool:
        """True if a local openai-compatible daemon (ollama) answers at *endpoint*."""

    def installed_models(self, endpoint: str) -> Tuple[str, ...]:
        """Model names the daemon advertises (best-effort; empty on failure)."""


# CLI providers → (binary name, config markers under $HOME) used for best-effort login
# detection. Presence of a config marker is a HEURISTIC (a prior login), surfaced honestly
# as such — we never run the CLI or assert a live session we didn't observe.
_CLI_MARKERS = {
    "claude": ("claude", (".claude.json", ".claude", ".config/claude")),
    "codex": ("codex", (".codex.json", ".codex", ".config/codex")),
}


class DefaultProbe:
    """Real probe — stdlib only (no new dependency). Injectable for tests."""

    def __init__(self, *, home: Optional[Path] = None, probe_timeout: float = 2.0) -> None:
        self._home = home or Path.home()
        self._timeout = probe_timeout

    def cli_authenticated(self, provider_id: str) -> Optional[bool]:
        marker = _CLI_MARKERS.get((provider_id or "").strip())
        if marker is None:
            return None
        binary, paths = marker
        has_config = any((self._home / p).exists() for p in paths)
        on_path = shutil.which(binary) is not None
        if has_config:
            return True        # heuristic: a config marker → a prior login exists
        if on_path:
            return False       # CLI installed but no login marker → needs `<cli> login`
        return None            # CLI absent + no marker → cannot determine (not installed)

    def api_key(self, provider_id: str, env: Optional[Mapping[str, str]] = None) -> str:
        environ = os.environ if env is None else env
        key_name = f"{(provider_id or '').strip().upper()}_API_KEY"
        return str(environ.get(key_name, "") or "").strip()

    def daemon_reachable(self, endpoint: str) -> bool:
        import urllib.request
        try:
            req = urllib.request.Request(endpoint.rstrip("/") + "/api/tags")
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return 200 <= resp.status < 300
        except Exception:  # noqa: BLE001
            return False

    def installed_models(self, endpoint: str) -> Tuple[str, ...]:
        import json
        import urllib.request
        try:
            req = urllib.request.Request(endpoint.rstrip("/") + "/api/tags")
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return tuple(str(m.get("name", "")) for m in data.get("models", []) if m.get("name"))
        except Exception:  # noqa: BLE001
            return ()


__all__ = ("ConnectionProbe", "DefaultProbe")
