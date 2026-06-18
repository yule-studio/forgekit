"""Chat submit models — the result of a free-text live-submit. Pure, stdlib-only.

The console's free-text path resolves a provider and submits. The outcome is one
honest :class:`SubmitResult` that the TUI appends to the transcript. It is never a
"works-like" stub: ``mode`` and ``category`` make the FOUR provider states distinct
so the operator always knows what really happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

# How the provider was resolved.
SOURCE_CONFIGURED = "configured"      # from ~/.forgekit/config.json (operator chose it)
SOURCE_LOCAL_DEFAULT = "local-default"  # no config, but a local ollama is reachable
SOURCE_NONE = "none"                  # nothing configured / reachable

# mode — what actually happened (live vs not).
MODE_LIVE = "live"      # a real provider answered
MODE_SETUP = "setup"    # setup incomplete — operator action required
MODE_ERROR = "error"    # a configured provider failed / is unsupported

# category — WHY (distinct provider states; drives the operator message).
CAT_OK = "ok"
CAT_NO_PROVIDER = "no_provider_configured"
CAT_AUTH_MISSING = "auth_missing"
CAT_UNSUPPORTED = "unsupported_in_console"
CAT_UNREACHABLE = "endpoint_unreachable"
CAT_TRANSPORT = "transport_error"


@dataclass(frozen=True)
class SubmitResult:
    """The honest outcome of one free-text submit (assistant reply OR why-not)."""

    ok: bool
    mode: str                 # MODE_*
    category: str             # CAT_*
    text: str = ""            # assistant reply (live) or the operator message (else)
    provider_id: str = ""
    provider_label: str = ""
    source: str = SOURCE_NONE
    model: str = ""
    next_action: str = ""     # what the operator should do next (non-live cases)

    @property
    def is_live(self) -> bool:
        return self.ok and self.mode == MODE_LIVE

    def receipt(self) -> str:
        """A one-line operator-facing execution receipt (which provider / live?)."""

        who = self.provider_label or self.provider_id or "—"
        tag = "live" if self.is_live else self.mode
        extra = f" · {self.model}" if self.model else ""
        return f"[dim]↳ {who}{extra} · {tag} · {self.category}[/dim]"

    def to_lines(self) -> Tuple[str, ...]:
        """Transcript lines for this result (assistant reply + receipt, or why-not)."""

        if self.is_live:
            # the assistant reply, then a quiet receipt of which provider answered.
            body = [ln for ln in self.text.split("\n")] or [""]
            return (*body, self.receipt())
        # non-live: a clear why-not block + the next action, then the receipt.
        lines = [f"[b]{self.text}[/b]"] if self.text else []
        if self.next_action:
            lines.append(f"[dim]다음 단계: {self.next_action}[/dim]")
        lines.append(self.receipt())
        return tuple(lines)


__all__ = (
    "SOURCE_CONFIGURED", "SOURCE_LOCAL_DEFAULT", "SOURCE_NONE",
    "MODE_LIVE", "MODE_SETUP", "MODE_ERROR",
    "CAT_OK", "CAT_NO_PROVIDER", "CAT_AUTH_MISSING", "CAT_UNSUPPORTED",
    "CAT_UNREACHABLE", "CAT_TRANSPORT",
    "SubmitResult",
)
