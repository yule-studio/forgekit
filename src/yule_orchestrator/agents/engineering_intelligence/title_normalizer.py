"""Canonical visible-title scrub for engineering-knowledge surfaces.

Scope (intentionally narrow):

  * engineering-knowledge note rendering (frontmatter ``title:`` / H1 /
    Obsidian write-request ``title``).
  * GeekNews / Hacker News / RSS / sitemap aggregator collectors that
    feed those notes (raw headlines often arrive with ``[GeekNews]``
    / ``[HN]`` / trailing site names).
  * Discord 이슈방 / 운영-리서치 forum-style messages reused as
    knowledge candidates (``Re:`` / ``이슈:`` / ``공유:`` boilerplate
    + dates squashed into the headline + occasional multi-line
    prompt-shaped bodies pasted as titles).

Out of scope: generic ``task-log`` / ``decision-record`` / ``research``
note kinds — those keep their own rules in
:mod:`agents.obsidian.note_kinds`. Filename date prefixes produced by
``recommend_path`` are also untouched — the caller still sees
``YYYY-MM-DD_engineering-knowledge-<slug>.md`` on disk. Only the
*visible* title (frontmatter ``title:`` / Markdown H1 / Discord digest
bullet / Obsidian write-request ``title``) is rewritten.

The canonical title:

  * Has no date prefix/suffix.
  * Has no aggregator label (``[GeekNews]`` / ``[HN]`` / ``Re:`` /
    ``이슈:`` / trailing ``— GeekNews`` / ``| Hacker News``).
  * Has no inline URL.
  * Has any obvious secret token redacted (defence in depth).
  * Has at most :data:`DEFAULT_TITLE_MAX_CHARS` characters with a
    sentence/word-boundary truncation.

The function is pure, deterministic, side-effect free.
"""

from __future__ import annotations

import re
from typing import Any, Optional


DEFAULT_TITLE_MAX_CHARS: int = 70

#: Hard upper bound. Anything past this is treated as a pasted prompt
#: body and gets sliced to its first sentence before truncation.
_PROMPT_LIKE_LENGTH_THRESHOLD: int = 200


# ---------------------------------------------------------------------------
# Secret redaction (mirrors renderer._SECRET_PATTERNS — we duplicate the
# small list here so the title scrub has no import dependency on the
# renderer module and runs even when the caller bypasses the renderer).
# ---------------------------------------------------------------------------


_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}", re.I), "[redacted-github-pat]"),
    (re.compile(r"\bgh[psor]_[A-Za-z0-9]{20,}"), "[redacted-github-token]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "[redacted-slack-token]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), "[redacted-api-key]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted-aws-key]"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"),
        "[redacted-jwt]",
    ),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]{20,}"), "Bearer [redacted-bearer]"),
)


def _redact_secrets(text: str) -> str:
    out = text
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


# ---------------------------------------------------------------------------
# Scrub patterns
# ---------------------------------------------------------------------------


_DATE_ISO_RE = re.compile(
    r"\d{4}[./\-]\d{1,2}[./\-]\d{1,2}"
)
_DATE_KO_RE = re.compile(
    r"\d{4}\s*년\s*\d{1,2}\s*월(?:\s*\d{1,2}\s*일)?"
)
_DATE_EN_RE = re.compile(
    r"(?i)\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*"
    r"\s+\d{1,2}(?:,\s*\d{4})?"
)


_LEADING_DATE_RE = re.compile(
    r"^\s*[\[\(\{]?\s*(?:" + _DATE_ISO_RE.pattern + r"|"
    + _DATE_KO_RE.pattern + r"|" + _DATE_EN_RE.pattern + r")"
    r"\s*[\]\)\}]?\s*[-—–:·|]?\s*"
)
_TRAILING_DATE_RE = re.compile(
    r"\s*[-—–·|(\[\{]?\s*(?:" + _DATE_ISO_RE.pattern + r"|"
    + _DATE_KO_RE.pattern + r"|" + _DATE_EN_RE.pattern + r")"
    r"\s*[\]\)\}]?\s*$"
)


# Aggregator / forum label prefixes. Korean colon-style labels (이슈: ...)
# are also stripped so a Discord 이슈방 paste reads as the actual
# headline once it lands as a knowledge title.
_LABEL_PREFIX_RE = re.compile(
    r"^\s*\[(?:"
    r"GeekNews|GN|HN|Hacker\s*News|Hacker-News|HN\s*Top|"
    r"news|뉴스|notice|알림|"
    r"issue|Issue|이슈|"
    r"discuss|discussion|토론|"
    r"discord|"
    r"forum|포럼|"
    r"research|리서치|"
    r"share|공유|"
    r"질문|업데이트|공지"
    r")\]\s*[-—–:·|]?\s*",
    re.IGNORECASE,
)

_KO_COLON_PREFIX_RE = re.compile(
    r"^\s*(?:이슈|공유|리서치|질문|공지|업데이트|알림|뉴스|운영\s*리서치)"
    r"\s*[:：]\s*"
)

_REPLY_PREFIX_RE = re.compile(
    r"^\s*(?:re|fwd?|fw|답글|회신|전달)\s*[:：]\s*",
    re.IGNORECASE,
)


# Aggregator / publication suffixes. The site name often ships as
# ``— GeekNews`` / `` | Hacker News``. Drop them so the H1 is the
# article topic instead of a citation tail.
_SUFFIX_NAMES = (
    "GeekNews",
    "Geek\\s*News",
    "GN",
    "HN",
    "Hacker\\s*News",
    "Hacker-News",
    "The\\s*Verge",
    "Ars\\s*Technica",
    "InfoQ",
    "TechCrunch",
    "Reuters",
)
_TRAILING_SITE_RE = re.compile(
    r"\s*[-—–·|]\s*(?:" + "|".join(_SUFFIX_NAMES) + r")\s*$",
    re.IGNORECASE,
)
_TRAILING_PARENS_SITE_RE = re.compile(
    r"\s*[\[\(]\s*(?:" + "|".join(_SUFFIX_NAMES) + r")\s*[\]\)]\s*$",
    re.IGNORECASE,
)


# Inline URLs in titles get stripped — keep the "보기 좋은 한 줄" goal.
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


# Wrapping quotes we strip once prefix/suffix labels are gone. We
# intentionally do NOT strip ``[ ]`` / ``( )`` here — aggregator
# labels like ``[GeekNews]`` / ``(2026-05-08)`` depend on the
# bracket characters to be matched by the dedicated label / date
# regexes. If we stripped them as outer pairs first, ``[GeekNews]``
# would collapse to the bare word ``GeekNews`` which then sails
# through every label regex.
_OUTER_QUOTE_PAIRS: tuple[tuple[str, str], ...] = (
    ("\"", "\""),
    ("'", "'"),
    ("“", "”"),
    ("‘", "’"),
    ("「", "」"),
    ("『", "』"),
    ("《", "》"),
)


_WHITESPACE_RE = re.compile(r"\s+")


# Markers that strongly suggest the title field actually carries a
# pasted prompt body. When any of these match, the scrub takes only
# the first sentence before the length cap.
_PROMPT_MARKERS_RE = re.compile(
    r"(?:다음과\s*같이|다음\s*사항을|아래\s*내용을|please\s+(?:answer|review|explain)"
    r"|how\s+(?:should|do)\s+we|could\s+you|can\s+you|"
    r"검토\s*(?:해|부탁)|정리\s*해\s*(?:줘|주세요)|설명\s*해\s*(?:줘|주세요))",
    re.IGNORECASE,
)


_SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[.!?。])\s+|(?<=다\.)\s+|(?<=요\.)\s+|\n+"
)


def _strip_outer_pairs(text: str) -> str:
    changed = True
    while changed and text:
        changed = False
        for left, right in _OUTER_QUOTE_PAIRS:
            if text.startswith(left) and text.endswith(right) and len(text) >= 2:
                text = text[len(left): len(text) - len(right)].strip()
                changed = True
                break
    return text


def _strip_known_prefixes(text: str) -> str:
    """Repeatedly strip date / label / reply prefixes until stable."""

    previous: Optional[str] = None
    while previous != text:
        previous = text
        text = _LEADING_DATE_RE.sub("", text)
        text = _LABEL_PREFIX_RE.sub("", text)
        text = _KO_COLON_PREFIX_RE.sub("", text)
        text = _REPLY_PREFIX_RE.sub("", text)
        text = text.lstrip(" -—–·|:：")
    return text


def _strip_known_suffixes(text: str) -> str:
    """Repeatedly strip aggregator / date suffixes until stable."""

    previous: Optional[str] = None
    while previous != text:
        previous = text
        text = _TRAILING_SITE_RE.sub("", text)
        text = _TRAILING_PARENS_SITE_RE.sub("", text)
        text = _TRAILING_DATE_RE.sub("", text)
        text = text.rstrip(" -—–·|,.;:")
    return text


def _take_first_sentence(text: str) -> str:
    parts = _SENTENCE_SPLIT_RE.split(text, maxsplit=1)
    if not parts:
        return text
    return parts[0].strip(" \t.!?。·,;")


def _truncate(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    # Sentence-boundary cut first.
    for sep in (". ", "! ", "? ", "。 ", "다. ", "다 ", " — ", " – ", " - "):
        idx = text.find(sep)
        if 0 < idx <= max_chars:
            return text[:idx].rstrip(" ,.;:")
    head = text[:max_chars]
    pivot = head.rfind(" ")
    if pivot >= max_chars // 2:
        head = head[:pivot]
    return head.rstrip(" ,.;:") + "…"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def canonical_engineering_title(
    raw: str,
    *,
    max_chars: int = DEFAULT_TITLE_MAX_CHARS,
) -> str:
    """Return *raw* normalised for an engineering-knowledge visible title.

    Behaviour:

      * ``""`` / ``None`` → ``""``.
      * Strips date prefixes/suffixes (``2026-05-08 Spring 6.2`` →
        ``Spring 6.2``).
      * Strips aggregator labels (``[GeekNews] foo`` → ``foo``,
        ``foo — Hacker News`` → ``foo``).
      * Strips Discord 이슈방 / 리서치 boilerplate (``이슈: foo`` /
        ``Re: foo`` → ``foo``).
      * Removes inline URLs and collapses whitespace.
      * Redacts obvious secret tokens (defence in depth — collectors
        already redact, but a malformed source must not slip through).
      * Caps to *max_chars* with sentence-boundary truncation. Pasted
        prompt-shaped bodies (very long, multi-line, or carrying
        prompt markers like ``다음과 같이`` / ``please review``) are
        sliced to the first sentence before the length cap.

    The function is pure — it returns the new string and never mutates
    the input or any external state.
    """

    if not raw:
        return ""

    # Remove inline URLs first so date/label patterns inside an
    # otherwise URL-bearing title don't confuse the scrub.
    text = _URL_RE.sub(" ", str(raw))

    # Multi-line pastes (Discord 이슈방 → "headline\n\n본문\n추가 메모"
    # style) keep only the first non-empty line. The remaining text
    # is what we'd treat as the headline; downstream sentence/length
    # caps still trim if it's a single very long sentence.
    multi_line = "\n" in text or "\r" in text
    if multi_line:
        for chunk in text.replace("\r", "\n").split("\n"):
            chunk = chunk.strip()
            if chunk:
                text = chunk
                break
        else:
            text = ""
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if not text:
        return ""

    text = _redact_secrets(text)

    # Strip wrapping quotes / brackets, prefixes, and suffixes — the
    # three steps run in a stable loop because stripping a prefix can
    # expose a wrapping quote and vice versa.
    previous: Optional[str] = None
    while previous != text:
        previous = text
        text = _strip_outer_pairs(text).strip()
        text = _strip_known_prefixes(text).strip()
        text = _strip_known_suffixes(text).strip()

    if not text:
        return ""

    prompt_like = (
        multi_line
        or len(text) > _PROMPT_LIKE_LENGTH_THRESHOLD
        or bool(_PROMPT_MARKERS_RE.search(text))
    )
    if prompt_like:
        text = _take_first_sentence(text)
        text = _strip_known_suffixes(text).strip(" -—–·|,.;:")

    text = _truncate(text, max_chars=max_chars)
    return text.strip()


def display_title_for(
    item: Any,
    *,
    max_chars: int = DEFAULT_TITLE_MAX_CHARS,
    fallback: str = "(제목 미정)",
) -> str:
    """Return the canonical visible title for *item* (or any object with
    a ``title`` attribute).

    Falls back to *fallback* when the scrub leaves nothing readable so
    the H1 / digest line never goes blank — the renderer's hard
    contract still rejects fully empty titles upstream, but this guard
    covers the intermediate "scrubbed everything away" case for
    surfaces that already accepted the item (Discord digest, audit
    payloads).
    """

    raw = getattr(item, "title", "") or ""
    cleaned = canonical_engineering_title(raw, max_chars=max_chars)
    return cleaned or fallback


__all__ = [
    "DEFAULT_TITLE_MAX_CHARS",
    "canonical_engineering_title",
    "display_title_for",
]
