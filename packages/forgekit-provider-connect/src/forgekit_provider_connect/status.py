"""Connection status — the honest verdict for "is this provider actually connected?".

Distinct from routing/policy: this is the *onboarding/control-plane* answer (gh-auth-like),
separating brain participation from live-submit transport. Never fakes a connection — if a
state can't be verified it is reported as ``unknown``/``missing``, never ``connected``.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- connection states (honest, mutually exclusive) -------------------------
STATE_CONNECTED = "connected"                  # verified reachable/authed for its transport
STATE_MISSING_KEY = "missing_key"              # api-key provider, key absent
STATE_MISSING_CLI_AUTH = "missing_cli_auth"    # CLI provider, no detected login/session
STATE_DAEMON_DOWN = "daemon_down"              # local daemon (ollama) unreachable
STATE_MODEL_MISSING = "model_missing"          # daemon up but no (selected) model installed
STATE_UNSUPPORTED_IN_CONSOLE = "unsupported_in_console"  # routable brain, no console live-submit
STATE_BLOCKED = "blocked"                       # present but blocked (permission/sandbox/policy)
STATE_NOT_LINKED = "not_linked"                # provider not in the operator's config
STATE_UNKNOWN = "unknown"                       # could not be determined (honest)

ALL_STATES = (
    STATE_CONNECTED, STATE_MISSING_KEY, STATE_MISSING_CLI_AUTH, STATE_DAEMON_DOWN,
    STATE_MODEL_MISSING, STATE_UNSUPPORTED_IN_CONSOLE, STATE_BLOCKED, STATE_NOT_LINKED,
    STATE_UNKNOWN,
)

# transport class (how a submit would actually be carried)
TRANSPORT_CLI = "cli"            # claude/codex — routable brain, no console live-submit yet
TRANSPORT_OPENAI = "openai"      # gemini/ollama/openai-compat — the live console lane
TRANSPORT_NONE = "none"

_OK = {STATE_CONNECTED}


@dataclass(frozen=True)
class ConnectionStatus:
    """One provider's honest connection verdict."""

    provider_id: str
    state: str
    transport: str = TRANSPORT_NONE
    # live_capable = can carry a console live-submit RIGHT NOW (gemini/ollama keyed/up).
    # A CLI brain can be `connected` (attached) yet NOT live_capable (routing participant).
    live_capable: bool = False
    detail: str = ""
    next_action: str = ""

    @property
    def connected(self) -> bool:
        return self.state in _OK

    @property
    def ok_word(self) -> str:
        """A short status word for the surface (never green-washes 'not installed')."""
        if self.state == STATE_CONNECTED:
            return "live" if self.live_capable else "connected · routing only"
        return self.state

    def to_dict(self) -> dict:
        return {
            "provider_id": self.provider_id, "state": self.state, "transport": self.transport,
            "live_capable": self.live_capable, "detail": self.detail, "next_action": self.next_action,
        }


__all__ = (
    "STATE_CONNECTED", "STATE_MISSING_KEY", "STATE_MISSING_CLI_AUTH", "STATE_DAEMON_DOWN",
    "STATE_MODEL_MISSING", "STATE_UNSUPPORTED_IN_CONSOLE", "STATE_BLOCKED", "STATE_NOT_LINKED",
    "STATE_UNKNOWN", "ALL_STATES", "TRANSPORT_CLI", "TRANSPORT_OPENAI", "TRANSPORT_NONE",
    "ConnectionStatus",
)
