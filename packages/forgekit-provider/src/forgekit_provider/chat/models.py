"""Chat submit models — the result of a free-text live-submit. Pure, stdlib-only.

The console's free-text path resolves a provider and submits. The outcome is one
honest :class:`SubmitResult` that the TUI appends to the transcript. It is never a
"works-like" stub: ``mode`` and ``category`` make the FOUR provider states distinct
so the operator always knows what really happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

# How the provider was resolved.
SOURCE_CONFIGURED = "configured"      # from ~/.forgekit/config.json (operator chose it)
SOURCE_LOCAL_DEFAULT = "local-default"  # no config, but a local ollama is reachable
SOURCE_NONE = "none"                  # nothing configured / reachable

# mode — what actually happened (live vs not).
MODE_LIVE = "live"      # a real provider answered
MODE_SETUP = "setup"    # setup incomplete — operator action required
MODE_ERROR = "error"    # a configured provider failed / is unsupported
MODE_HELD = "held"      # the runtime policy held the action (no provider call)

# category — WHY (distinct provider states; drives the operator message).
CAT_OK = "ok"
CAT_NO_PROVIDER = "no_provider_configured"
CAT_AUTH_MISSING = "auth_missing"
CAT_UNSUPPORTED = "unsupported_in_console"
CAT_UNREACHABLE = "endpoint_unreachable"
CAT_TRANSPORT = "transport_error"
CAT_POLICY_HELD = "policy_held"            # approval-wait / hold-all held the submit
CAT_BUDGET_THROTTLED = "budget_throttled"  # budget posture throttled the submit

# usage_basis — how the token numbers were obtained (NEVER mix live + estimate).
USAGE_LIVE = "live"          # provider reported real usage
USAGE_ESTIMATE = "estimate"  # forgekit estimated from text length (heuristic)
USAGE_PROXY = "proxy"        # a price/usage proxy
USAGE_UNKNOWN = "unknown"    # not measured


@dataclass(frozen=True)
class ProviderUsage:
    """Native usage as the provider reported it (WT1 #239). ``usable`` only when the
    provider actually gave a positive token total — otherwise the caller degrades to
    an honest estimate (never faked live). ``raw_json`` keeps the original block for
    evidence."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    raw_json: str = ""

    @property
    def usable(self) -> bool:
        return self.total_tokens > 0


@dataclass(frozen=True)
class ChatResult:
    """A transport's reply: assistant text + the provider's native usage (if any).

    The transport returns BOTH from the SAME response so usage is real (same call that
    produced the text), not a second guess. ``usage=None`` → no native usage block →
    the submit path records ``usage_basis=estimate``."""

    text: str = ""
    usage: Optional[ProviderUsage] = None


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
    # --- runtime-teeth (WT1): the policy posture that produced this result ---
    runtime_mode: str = ""    # the forgekit runtime mode (interactive/approval-wait/…)
    usage_basis: str = USAGE_UNKNOWN
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    throttled: bool = False
    # --- provider fallback (WT1 teeth): set when the declared/routed provider was
    # unusable and the operator's EXPLICIT fallback order produced this provider. ---
    fallback_used: bool = False
    routed_from: str = ""     # the originally-intended provider (the chain head)

    @property
    def is_live(self) -> bool:
        return self.ok and self.mode == MODE_LIVE

    @property
    def held(self) -> bool:
        return self.mode == MODE_HELD

    def receipt(self) -> str:
        """A one-line operator-facing execution receipt (mode / provider / usage)."""

        who = self.provider_label or self.provider_id or "—"
        tag = "live" if self.is_live else self.mode
        extra = f" · {self.model}" if self.model else ""
        mode = f" · mode={self.runtime_mode}" if self.runtime_mode else ""
        usage = ""
        if self.total_tokens or self.usage_basis not in ("", USAGE_UNKNOWN):
            usage = f" · {self.total_tokens}tok({self.usage_basis})"
        thr = " · throttled" if self.throttled else ""
        # honest fallback note: show the operator the declared→actual hop so a routed
        # fallback is never silent ("looks like claude answered" when gemini did).
        fb = f" · fallback {self.routed_from}→{self.provider_id}" if self.fallback_used else ""
        return f"[dim]↳ {who}{extra} · {tag} · {self.category}{mode}{usage}{thr}{fb}[/dim]"

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
    "MODE_LIVE", "MODE_SETUP", "MODE_ERROR", "MODE_HELD",
    "CAT_OK", "CAT_NO_PROVIDER", "CAT_AUTH_MISSING", "CAT_UNSUPPORTED",
    "CAT_UNREACHABLE", "CAT_TRANSPORT", "CAT_POLICY_HELD", "CAT_BUDGET_THROTTLED",
    "USAGE_LIVE", "USAGE_ESTIMATE", "USAGE_PROXY", "USAGE_UNKNOWN",
    "ProviderUsage", "ChatResult", "SubmitResult",
)
