"""Issue / Discord intake → :class:`WorkRequest` adapter.

The triage layer takes one canonical input shape — :class:`WorkRequest`
— so it doesn't need separate code paths for "this came from a GitHub
issue webhook" vs. "this came from a Discord work intake message".

This module produces that shape from the two intake surfaces the
agent listens on. It also performs **secret-like redaction at the
boundary**: any obvious token / API key / private-key marker is
replaced with a sanitised placeholder before the request is handed
to triage / audit. This is a defence-in-depth — the broader
engineering-agent will not write secrets to logs, but a malformed
issue body with a pasted token should never reach triage either.

Strictly offline: builds the request from a *dict* (for GitHub) or
*string* (for Discord). Never makes a network call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Tuple


class SourceKind(str, Enum):
    """Where the work request came from."""

    GITHUB_ISSUE = "github_issue"
    DISCORD_INTAKE = "discord_intake"


@dataclass(frozen=True)
class WorkRequest:
    """Canonical intake shape consumed by the triage layer.

    All free-form strings (title, body, sender) are passed through
    :func:`redact_secret_like` before they land here, so downstream
    code can quote them safely.
    """

    kind: SourceKind
    title: str
    body: str
    source_id: str  # github issue number, discord message id, etc.
    labels: Tuple[str, ...] = ()
    sender: str = ""
    raw_links: Tuple[str, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Secret-like redaction
# ---------------------------------------------------------------------------
#
# These patterns target *shapes* that look like credentials, not real
# detection of leaked tokens. The triage layer never trusts these
# strings either — anything that survives redaction is treated as
# user-supplied prose and not echoed back.

_SECRET_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\bgithub_pat_[A-Za-z0-9_]{20,}"), "[redacted-github-pat]"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}"), "[redacted-github-token]"),
    (re.compile(r"\bghs_[A-Za-z0-9]{20,}"), "[redacted-github-token]"),
    (re.compile(r"\bgho_[A-Za-z0-9]{20,}"), "[redacted-github-token]"),
    (re.compile(r"\bghr_[A-Za-z0-9]{20,}"), "[redacted-github-token]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "[redacted-slack-token]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), "[redacted-api-key]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted-aws-key]"),
    (
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----"
            r"[\s\S]+?"
            r"-----END (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----"
        ),
        "[redacted-private-key-block]",
    ),
    (
        re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"),
        "[redacted-jwt]",
    ),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]{20,}"), "Bearer [redacted-bearer]"),
    (
        re.compile(
            r"(?im)^(?P<key>(?:DISCORD|GITHUB|API|OPENAI|ANTHROPIC|TAVILY|BRAVE|SLACK)"
            r"_(?:TOKEN|KEY|SECRET|PAT))\s*=\s*\S+"
        ),
        r"\g<key>=[redacted-env-value]",
    ),
)


def redact_secret_like(text: str) -> str:
    """Return *text* with obvious secret-shaped substrings replaced.

    Always returns a string — empty input maps to empty output. The
    function is idempotent: running it twice does not double-redact.
    """

    if not text:
        return text or ""
    out = text
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


_LINK_PATTERN = re.compile(r"https?://[^\s)>\]]+")


def _extract_links(text: str) -> Tuple[str, ...]:
    if not text:
        return ()
    seen: list[str] = []
    for match in _LINK_PATTERN.finditer(text):
        url = match.group(0).rstrip(".,;)")
        if url not in seen:
            seen.append(url)
    return tuple(seen)


def build_request_from_github_issue(payload: Mapping[str, Any]) -> WorkRequest:
    """Build a :class:`WorkRequest` from a GitHub issue payload dict.

    Reads keys lazily so the function works against partial fixtures
    (only the fields we use are required). Title/body/labels/sender
    pass through :func:`redact_secret_like`.
    """

    if not isinstance(payload, Mapping):
        raise TypeError(
            "build_request_from_github_issue expects a Mapping, got "
            f"{type(payload).__name__}"
        )

    title = redact_secret_like(str(payload.get("title") or "").strip())
    body = redact_secret_like(str(payload.get("body") or "").strip())
    number = payload.get("number")
    source_id = f"issue#{number}" if number else "issue#?"

    raw_labels = payload.get("labels") or ()
    label_names: list[str] = []
    for entry in raw_labels:
        if isinstance(entry, Mapping):
            name = entry.get("name")
            if isinstance(name, str) and name:
                label_names.append(redact_secret_like(name))
        elif isinstance(entry, str) and entry:
            label_names.append(redact_secret_like(entry))

    sender = ""
    user = payload.get("user")
    if isinstance(user, Mapping):
        login = user.get("login")
        if isinstance(login, str) and login:
            sender = redact_secret_like(login)
    elif isinstance(user, str):
        sender = redact_secret_like(user)

    return WorkRequest(
        kind=SourceKind.GITHUB_ISSUE,
        title=title,
        body=body,
        source_id=source_id,
        labels=tuple(label_names),
        sender=sender,
        raw_links=_extract_links(f"{title}\n{body}"),
        extra={
            "html_url": redact_secret_like(str(payload.get("html_url") or "")),
            "state": str(payload.get("state") or ""),
        },
    )


def build_request_from_discord_intake(
    text: str,
    *,
    message_id: str = "",
    sender: str = "",
    channel: str = "",
) -> WorkRequest:
    """Build a :class:`WorkRequest` from a Discord intake message.

    ``text`` is the raw message body. The first non-empty line acts
    as the title (capped at 200 chars); the rest is the body. All
    free-form fields go through :func:`redact_secret_like`.
    """

    cleaned = redact_secret_like((text or "").strip())
    if not cleaned:
        return WorkRequest(
            kind=SourceKind.DISCORD_INTAKE,
            title="",
            body="",
            source_id=f"discord#{message_id or '?'}",
            labels=(),
            sender=redact_secret_like(sender or ""),
            raw_links=(),
            extra={"channel": redact_secret_like(channel or "")},
        )

    lines = [ln for ln in cleaned.splitlines() if ln.strip()]
    title_raw = lines[0] if lines else cleaned
    title = title_raw[:200]
    body = cleaned

    return WorkRequest(
        kind=SourceKind.DISCORD_INTAKE,
        title=title,
        body=body,
        source_id=f"discord#{message_id or '?'}",
        labels=(),
        sender=redact_secret_like(sender or ""),
        raw_links=_extract_links(cleaned),
        extra={"channel": redact_secret_like(channel or "")},
    )


__all__ = [
    "SourceKind",
    "WorkRequest",
    "build_request_from_discord_intake",
    "build_request_from_github_issue",
    "redact_secret_like",
]
