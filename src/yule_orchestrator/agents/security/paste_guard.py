"""PasteGuard — outbound secret redaction preflight (F1 / #88).

Every outbound payload — LLM prompts, Discord webhook posts,
GitHub issue / PR comments, Obsidian vault writes — must pass
through :func:`guard_outbound` before the bytes leave the agent
process. The guard runs three stages:

  1. :func:`scan_payload` walks a deterministic catalogue of
     :class:`SecretPattern` regexes and returns every match as
     a :class:`SecretFinding`.
  2. :func:`redact_payload` rewrites each finding inline using
     a stable ``head4 + mask + tail4`` shape so the redaction
     is round-trip safe and replay-friendly for ops review.
  3. :func:`guard_outbound` composes 1 + 2, attaches the channel
     and a sha256 hash of the *original* payload, and returns a
     :class:`GuardVerdict`. When ``fail_closed=True`` (the
     default) any internal exception causes the payload to be
     dropped (``redacted = ""``) and ``blocked = True``.

Hard rails (regression-tested in
``tests/engineering/test_paste_guard_governance.py``):

  * The raw secret never appears in ``GuardVerdict.findings``,
    ``SecretFinding.suggested_redaction``, repr / str output, or
    in any log line emitted by this module.
  * ``guard_outbound`` is fail-closed by default — degraded
    state must drop the payload, not leak it.
  * No outbound channel is ever exempted (LLM, Discord, GitHub,
    and Vault all hit the same wrapper).
"""

from __future__ import annotations

import enum
import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Pattern catalogue
# ---------------------------------------------------------------------------


class SecretPattern(str, enum.Enum):
    """Catalogue of secret shapes PasteGuard recognises.

    Stored as a string enum so logs / payloads can reference
    ``finding.pattern.value`` without leaking the literal regex.
    """

    ANTHROPIC_API_KEY = "anthropic_api_key"
    OPENAI_API_KEY = "openai_api_key"
    GITHUB_PAT = "github_pat"
    DISCORD_BOT_TOKEN = "discord_bot_token"
    PEM_BLOCK = "pem_block"
    AWS_ACCESS_KEY = "aws_access_key"
    DB_URL_WITH_PASSWORD = "db_url_with_password"
    GENERIC_HIGH_ENTROPY = "generic_high_entropy"


# Risk levels — ``critical`` triggers fail-closed redaction
# semantics for governance regressions; ``advisory`` is best-
# effort high-entropy detection (false-positive prone).
RISK_CRITICAL: str = "critical"
RISK_HIGH: str = "high"
RISK_ADVISORY: str = "advisory"


# Each pattern is paired with its (compiled regex, risk level).
# Ordering matters: more specific patterns run first so a generic
# match never preempts a structured one (e.g. ``sk-ant-*`` is
# matched by anthropic, not by openai).
_PATTERN_RULES: Tuple[Tuple[SecretPattern, "re.Pattern[str]", str], ...] = (
    (
        SecretPattern.ANTHROPIC_API_KEY,
        re.compile(r"sk-ant-[a-zA-Z0-9_\-]{20,}"),
        RISK_CRITICAL,
    ),
    (
        SecretPattern.GITHUB_PAT,
        re.compile(r"gh[pousr]_[a-zA-Z0-9]{36,}"),
        RISK_CRITICAL,
    ),
    (
        SecretPattern.AWS_ACCESS_KEY,
        re.compile(r"AKIA[0-9A-Z]{16}"),
        RISK_CRITICAL,
    ),
    (
        SecretPattern.PEM_BLOCK,
        re.compile(
            r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----"
        ),
        RISK_CRITICAL,
    ),
    (
        SecretPattern.DISCORD_BOT_TOKEN,
        re.compile(r"[MN][A-Za-z0-9]{23,}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{27,}"),
        RISK_CRITICAL,
    ),
    (
        SecretPattern.DB_URL_WITH_PASSWORD,
        re.compile(
            r"(?:postgres|postgresql|mysql|mongodb)(?:\+\w+)?://[^\s:/@]+:[^\s:/@]+@[^\s/]+"
        ),
        RISK_CRITICAL,
    ),
    (
        SecretPattern.OPENAI_API_KEY,
        # Match sk-... but NOT sk-ant-... (handled by anthropic
        # entry above). Accept sk-proj-... variants.
        re.compile(r"sk-(?!ant-)[a-zA-Z0-9_\-]{20,}"),
        RISK_CRITICAL,
    ),
    (
        SecretPattern.GENERIC_HIGH_ENTROPY,
        # Conservative shape: 32+ char base64-ish blob. Tagged as
        # advisory because false positives are possible.
        re.compile(r"\b[A-Za-z0-9+/]{32,}={0,2}\b"),
        RISK_ADVISORY,
    ),
)


# ---------------------------------------------------------------------------
# Dataclasses + enums
# ---------------------------------------------------------------------------


class OutboundChannel(str, enum.Enum):
    """Outbound destinations PasteGuard fronts."""

    LLM = "llm"
    DISCORD = "discord"
    GITHUB = "github"
    VAULT = "vault"


class PasteGuardError(RuntimeError):
    """Raised when an internal PasteGuard invariant is violated.

    Public callers should let :func:`guard_outbound` fail-close
    instead of catching this directly.
    """


@dataclass(frozen=True)
class SecretFinding:
    """A single secret hit inside a payload.

    ``suggested_redaction`` carries the masked replacement only
    (never the raw secret) so downstream logs / audit trails can
    quote it without leaking sensitive bytes. ``span`` is the
    (start, end) index pair into the *original* payload string.
    """

    pattern: SecretPattern
    span: Tuple[int, int]
    risk_level: str
    suggested_redaction: str

    def __post_init__(self) -> None:
        start, end = self.span
        if start < 0 or end < start:
            raise PasteGuardError(
                f"invalid finding span ({start}, {end}) for pattern "
                f"{self.pattern.value}"
            )

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            "SecretFinding("
            f"pattern={self.pattern.value!r}, "
            f"span={self.span}, "
            f"risk_level={self.risk_level!r}, "
            f"suggested_redaction={self.suggested_redaction!r})"
        )


@dataclass(frozen=True)
class GuardVerdict:
    """Outcome of a :func:`guard_outbound` call.

    * ``channel`` — which outbound surface ran the check.
    * ``original_hash`` — sha256 of the *original* payload bytes
      (utf-8 encoded). Lets ops cross-check audit logs without
      ever needing the raw bytes again.
    * ``findings`` — tuple of :class:`SecretFinding`. Each entry's
      ``suggested_redaction`` is already masked.
    * ``redacted`` — the payload safe to send. Empty string when
      ``blocked`` is True.
    * ``blocked`` — True iff PasteGuard refused the send. When a
      caller passes ``fail_closed=True`` (default) and redaction
      throws, the verdict comes back blocked + empty.
    """

    channel: OutboundChannel
    original_hash: str
    findings: Tuple[SecretFinding, ...]
    redacted: str
    blocked: bool

    def has_critical(self) -> bool:
        """True if any finding is at ``critical`` risk level."""

        return any(f.risk_level == RISK_CRITICAL for f in self.findings)


# ---------------------------------------------------------------------------
# Scan / redact primitives
# ---------------------------------------------------------------------------


def scan_payload(text: str) -> Tuple[SecretFinding, ...]:
    """Return every secret pattern hit in ``text``.

    Hits are de-duplicated by span — if two patterns claim
    overlapping ranges, the higher-priority pattern (earlier in
    :data:`_PATTERN_RULES`) wins. Generic high-entropy hits are
    suppressed when a more specific hit already covers the same
    range to avoid double-counting (e.g. an AWS key would also
    look base64-ish).
    """

    if not isinstance(text, str) or not text:
        return ()

    findings: List[SecretFinding] = []
    claimed_spans: List[Tuple[int, int]] = []

    for pattern, regex, risk_level in _PATTERN_RULES:
        for match in regex.finditer(text):
            span = (match.start(), match.end())
            if _overlaps_any(span, claimed_spans):
                continue
            raw = match.group(0)
            findings.append(
                SecretFinding(
                    pattern=pattern,
                    span=span,
                    risk_level=risk_level,
                    suggested_redaction=_mask(raw),
                )
            )
            claimed_spans.append(span)

    findings.sort(key=lambda f: f.span[0])
    return tuple(findings)


def redact_payload(text: str, *, mask: str = "***") -> str:
    """Replace every secret in ``text`` with ``head4 + mask + tail4``.

    Round-trip stable: calling :func:`redact_payload` on already-
    redacted output is a no-op (the mask body itself does not match
    any pattern). For very short secrets (< 8 chars) the entire run
    collapses to ``mask`` so we never accidentally surface the
    bulk of the original.
    """

    if not isinstance(text, str) or not text:
        return text or ""

    findings = scan_payload(text)
    if not findings:
        return text

    out_parts: List[str] = []
    cursor = 0
    for finding in findings:
        start, end = finding.span
        if start < cursor:
            # Defensive: overlapping spans shouldn't happen because
            # scan_payload suppresses them. Skip rather than corrupt.
            continue
        out_parts.append(text[cursor:start])
        out_parts.append(_mask(text[start:end], mask=mask))
        cursor = end
    out_parts.append(text[cursor:])
    return "".join(out_parts)


# ---------------------------------------------------------------------------
# Guard wrapper
# ---------------------------------------------------------------------------


def guard_outbound(
    *,
    channel: OutboundChannel,
    payload: str,
    fail_closed: bool = True,
) -> GuardVerdict:
    """Run PasteGuard on ``payload`` for the given outbound ``channel``.

    Returns a :class:`GuardVerdict` carrying the masked payload
    plus an audit-grade ``original_hash``. On any internal error
    the wrapper honours ``fail_closed``:

      * ``fail_closed=True`` (default) — discard the payload
        (``redacted=""``) and set ``blocked=True``. Findings are
        cleared so we never echo back partially-masked data.
      * ``fail_closed=False`` — re-raise the underlying exception.
        Callers must catch it before sending.
    """

    if not isinstance(channel, OutboundChannel):
        raise PasteGuardError(
            f"channel must be OutboundChannel, got {type(channel).__name__}"
        )
    if payload is None:
        payload = ""
    if not isinstance(payload, str):
        raise PasteGuardError(
            f"payload must be str, got {type(payload).__name__}"
        )

    original_hash = _hash_payload(payload)

    try:
        findings = scan_payload(payload)
        redacted = redact_payload(payload)
    except Exception as exc:  # noqa: BLE001 - fail-closed boundary
        if fail_closed:
            return GuardVerdict(
                channel=channel,
                original_hash=original_hash,
                findings=(),
                redacted="",
                blocked=True,
            )
        raise PasteGuardError(
            f"redact failed for channel={channel.value}: {type(exc).__name__}"
        ) from exc

    blocked = False
    # Hard rail: if any critical finding survives into the
    # redacted payload, something is wrong — drop the payload.
    if fail_closed and findings:
        if not _is_safely_redacted(redacted):
            return GuardVerdict(
                channel=channel,
                original_hash=original_hash,
                findings=(),
                redacted="",
                blocked=True,
            )

    return GuardVerdict(
        channel=channel,
        original_hash=original_hash,
        findings=findings,
        redacted=redacted,
        blocked=blocked,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _mask(raw: str, *, mask: str = "***") -> str:
    """Return ``head4 + mask + tail4`` (or just ``mask`` if too short).

    The ``head4 + tail4`` shape lets operators eyeball-match the
    masked entry against an inventory key without recovering the
    full secret. PEM blocks (which always contain newlines and
    boilerplate) collapse to the bare mask so we never emit the
    BEGIN/END headers either.
    """

    if not isinstance(raw, str) or len(raw) < 8:
        return mask
    if "\n" in raw or raw.startswith("-----BEGIN"):
        return mask
    return f"{raw[:4]}{mask}{raw[-4:]}"


def _overlaps_any(span: Tuple[int, int], claimed: Iterable[Tuple[int, int]]) -> bool:
    start, end = span
    for c_start, c_end in claimed:
        if start < c_end and c_start < end:
            return True
    return False


def _hash_payload(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    return f"sha256:{digest}"


def _is_safely_redacted(text: str) -> bool:
    """Sanity check: redacted text must not still match a critical pattern."""

    for pattern, regex, risk_level in _PATTERN_RULES:
        if risk_level != RISK_CRITICAL:
            continue
        if regex.search(text):
            return False
    return True


__all__ = (
    "GuardVerdict",
    "OutboundChannel",
    "PasteGuardError",
    "RISK_ADVISORY",
    "RISK_CRITICAL",
    "RISK_HIGH",
    "SecretFinding",
    "SecretPattern",
    "guard_outbound",
    "redact_payload",
    "scan_payload",
)
