"""Security agent layer — PasteGuard outbound redaction (F1 / #88).

This package houses the outbound secret preflight that every
LLM / Discord / GitHub / Obsidian write must pass through.

  * :mod:`paste_guard` — pattern catalogue + scan + redact +
    ``guard_outbound`` wrapper. The wrapper is fail-closed: if
    redaction throws for any reason, the payload is replaced with
    an empty string and ``GuardVerdict.blocked = True``.

The catalogue must stay loud about hard rails: API keys, PEM
blocks, OAuth tokens, DB URLs with credentials, and other high-
entropy secrets are stripped *before* the bytes leave the agent
process — never logged, never echoed back in
``GuardVerdict.findings``.
"""

from .paste_guard import (
    GuardVerdict,
    OutboundChannel,
    PasteGuardError,
    SecretFinding,
    SecretPattern,
    guard_outbound,
    redact_payload,
    scan_payload,
)


__all__ = (
    "GuardVerdict",
    "OutboundChannel",
    "PasteGuardError",
    "SecretFinding",
    "SecretPattern",
    "guard_outbound",
    "redact_payload",
    "scan_payload",
)
