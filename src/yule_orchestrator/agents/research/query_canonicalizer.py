"""Engineering-domain query canonicalizer — P0-F.

The autonomous collector and the recall observation pipeline both
build research queries directly from the user's raw prompt. That
worked for happy-path prompts ("RAG vs CAG memory 구조 비교") but
broke down for:

  * Typo / case variants: ``dRAG`` / ``rag`` / ``Cag`` / ``llm``
    arrived as-is at the collector, which uses lowercased-token
    dedup. With the mock-fallback provider those variants matched
    *different* canned hit buckets than the user intended.
  * Bilingual aliases: ``알엠`` / ``씨아이씨디`` never resolved to
    ``LLM`` / ``CI/CD`` so the collector silently dropped the
    domain signal.

This module is the **single source of truth** for query
normalization. Both the collector (``build_query_for_role``) and
the runtime recall observation (``recall.compute_recall_coverage``)
call :func:`canonicalize_query` so the recall pass and the
collection pass see the exact same normalized text.

Design constraints:

  * **No general spellchecker dependency.** A lexicon of ~30
    engineering acronyms + bounded fuzzy correction (edit distance
    ≤ 1, candidate length ≥ 3) is enough to catch the live
    regressions without auto-rewriting normal English/Korean.
  * **Raw text preserved.** Callers receive both ``raw`` and
    ``canonical`` and a list of replacements applied so the audit
    trail and reference metadata can show what changed.
  * **Confidence reported.** Exact case-insensitive matches earn
    1.0; fuzzy edit-distance ≤1 earns 0.6; korean alias earns 0.7.
    Multiple replacements take the min. Callers use the score to
    gate auto-publish (collector skips authoritative publish when
    confidence < 0.5 in mock-fallback mode).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Tuple


# ---------------------------------------------------------------------------
# Lexicons
# ---------------------------------------------------------------------------


# Canonical engineering acronyms. Map is case-insensitive on the key
# side; the value is the canonical rendering we emit. Add carefully —
# every entry trades a fuzzy-false-positive risk for a real-world
# typo win.
_ACRONYM_CANONICAL: Mapping[str, str] = {
    "RAG": "RAG",
    "CAG": "CAG",
    "LLM": "LLM",
    "VLM": "VLM",
    "JWT": "JWT",
    "OSI": "OSI",
    "TCP": "TCP",
    "UDP": "UDP",
    "IP": "IP",
    "HTTP": "HTTP",
    "HTTPS": "HTTPS",
    "OAUTH": "OAuth",
    "REST": "REST",
    "GRPC": "gRPC",
    "SQL": "SQL",
    "NOSQL": "NoSQL",
    "MQTT": "MQTT",
    "ETL": "ETL",
    "ML": "ML",
    "AI": "AI",
    "API": "API",
    "ADR": "ADR",
    "SSE": "SSE",
    "WS": "WS",
    "JSON": "JSON",
    "YAML": "YAML",
    "DNS": "DNS",
    "CDN": "CDN",
    "S3": "S3",
    "K8S": "K8s",
    "MCP": "MCP",
}


# Two-token canonicalizations (run *after* per-token replacements).
# We normalize whitespace + slash variants like "ci cd" / "ci-cd" /
# "ci/cd" → "CI/CD". Match is case-insensitive on the pattern; the
# value is the canonical form.
_MULTITOKEN_CANONICAL: Tuple[Tuple[str, str], ...] = (
    ("ci cd", "CI/CD"),
    ("ci-cd", "CI/CD"),
    ("ci/cd", "CI/CD"),
    ("c i / c d", "CI/CD"),
)


# Korean phonetic aliases. Conservative list — only the highest-risk
# typos seen in live operations. confidence=0.7 (lower than exact).
_KOREAN_ALIAS: Mapping[str, str] = {
    "알엠": "LLM",
    "엘엘엠": "LLM",
    "씨아이씨디": "CI/CD",
    "라그": "RAG",
    "캐그": "CAG",
    "오아우스": "OAuth",
    "오에스아이": "OSI",
    "에이피아이": "API",
    "에이아이": "AI",
}


# Confidence weights per source of replacement.
_CONFIDENCE_EXACT = 1.0
_CONFIDENCE_MIXED_CASE = 0.8
_CONFIDENCE_KOREAN = 0.7
_CONFIDENCE_FUZZY = 0.6


# Minimum candidate length for fuzzy correction. Below 3 the
# false-positive risk is too high ("dog" vs "dot" both lex-valid).
_FUZZY_MIN_LEN = 3


@dataclass(frozen=True)
class Replacement:
    """One token replacement applied during canonicalization."""

    raw: str
    canonical: str
    source: str  # "exact" | "fuzzy" | "korean" | "multitoken"
    confidence: float


@dataclass(frozen=True)
class CanonicalQuery:
    """Result of canonicalizing a raw user query.

    ``canonical`` is the rewritten query suitable for collector /
    recall. ``raw`` is the original prompt fragment (preserved so
    audit trails can show what the user actually wrote).
    ``applied`` lists every replacement; callers can render those
    to the user when confidence < 1.0. ``confidence`` is the min of
    all per-replacement confidences (1.0 when no replacements).
    """

    raw: str
    canonical: str
    applied: Tuple[Replacement, ...] = field(default_factory=tuple)
    confidence: float = 1.0

    @property
    def normalization_applied(self) -> bool:
        return self.canonical != self.raw or bool(self.applied)


def canonicalize_query(raw: str) -> CanonicalQuery:
    """Normalize *raw* for engineering-domain research queries.

    Returns a :class:`CanonicalQuery` describing what changed.

    Stages (each preserves the user's casing wherever no rewrite
    fires):

      1. multi-token rules ("ci cd" → "CI/CD") on case-folded text.
      2. korean alias substitution ("알엠" → "LLM").
      3. per-token rewrite — case-insensitive exact match → mixed-
         case uppercase-substring match ("dRAG" → "RAG") → bounded
         fuzzy (edit distance ≤1, length ≥3).

    Edge cases:
      * Empty / whitespace-only input → empty canonical.
      * Tokens already in canonical form pass through with
        confidence 1.0 and no Replacement.
    """

    if not raw or not raw.strip():
        return CanonicalQuery(raw=raw or "", canonical="", confidence=1.0)

    working = " ".join(raw.split())
    applied: list[Replacement] = []

    # Stage 1 — multi-token rules (case-insensitive). Replace on
    # the working buffer so the canonical form survives tokenization.
    for pattern, canonical in _MULTITOKEN_CANONICAL:
        idx = working.lower().find(pattern)
        while idx >= 0:
            working = working[:idx] + canonical + working[idx + len(pattern) :]
            applied.append(
                Replacement(
                    raw=pattern,
                    canonical=canonical,
                    source="multitoken",
                    confidence=_CONFIDENCE_EXACT,
                )
            )
            idx = working.lower().find(pattern, idx + len(canonical))

    # Stage 2 — korean alias substitution.
    for alias, canonical in _KOREAN_ALIAS.items():
        if alias in working:
            working = working.replace(alias, canonical)
            applied.append(
                Replacement(
                    raw=alias,
                    canonical=canonical,
                    source="korean",
                    confidence=_CONFIDENCE_KOREAN,
                )
            )

    # Stage 3 — per-token rewrite, preserving user casing.
    out_tokens: list[str] = []
    for token in working.split(" "):
        if not token:
            continue
        head, core, tail = _split_punctuation(token)
        if not core:
            out_tokens.append(token)
            continue

        # (a) Case-insensitive exact match against the lexicon.
        upper = core.upper()
        if upper in _ACRONYM_CANONICAL:
            canonical = _ACRONYM_CANONICAL[upper]
            if core != canonical:
                applied.append(
                    Replacement(
                        raw=core,
                        canonical=canonical,
                        source="exact",
                        confidence=_CONFIDENCE_EXACT,
                    )
                )
            out_tokens.append(f"{head}{canonical}{tail}")
            continue

        # (b) Mixed-case rule: the user typed an extra prefix/suffix
        # before/after a lexicon entry (e.g. "dRAG" → user kept the
        # uppercase RAG signal). Extract contiguous uppercase
        # substring of length ≥2; if it matches the lexicon, use it.
        mixed_canonical = _mixed_case_correct(core)
        if mixed_canonical is not None:
            if mixed_canonical != core:
                applied.append(
                    Replacement(
                        raw=core,
                        canonical=mixed_canonical,
                        source="mixed_case",
                        confidence=_CONFIDENCE_MIXED_CASE,
                    )
                )
            out_tokens.append(f"{head}{mixed_canonical}{tail}")
            continue

        # (c) Fuzzy — bounded edit distance 1 against lexicon, length ≥3.
        fuzzy_canonical = _fuzzy_correct(core)
        if fuzzy_canonical is not None:
            applied.append(
                Replacement(
                    raw=core,
                    canonical=fuzzy_canonical,
                    source="fuzzy",
                    confidence=_CONFIDENCE_FUZZY,
                )
            )
            out_tokens.append(f"{head}{fuzzy_canonical}{tail}")
            continue

        out_tokens.append(token)

    canonical = " ".join(out_tokens).strip()
    confidence = min((r.confidence for r in applied), default=1.0)
    return CanonicalQuery(
        raw=raw,
        canonical=canonical,
        applied=tuple(applied),
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _mixed_case_correct(token: str) -> str | None:
    """Canonicalize a token that holds one or more uppercase acronym runs.

    Two patterns:

      1. **Single canonical run with lowercase noise** (``dRAG``,
         ``aLLM``) — drop the lowercase prefix/suffix and emit the
         canonical run alone.
      2. **Multiple canonical runs joined by non-alpha separators**
         (``CAG/RAG``, ``RAG-CAG``) — preserve every separator and
         replace each run with its canonical form.

    Returns ``None`` when no uppercase run of length ≥2 matches the
    lexicon, or when an uppercase run exists but doesn't match. We
    deliberately *don't* fire on title-cased words like ``Drag``
    (only one uppercase character).
    """

    if len(token) < 3:
        return None

    # Segment the token into (segment_text, is_upper_alpha_run) pairs.
    segments: list[Tuple[str, bool]] = []
    i = 0
    while i < len(token):
        ch = token[i]
        if ch.isupper():
            j = i
            while j < len(token) and token[j].isupper():
                j += 1
            segments.append((token[i:j], True))
            i = j
        elif ch.isalpha():
            j = i
            while j < len(token) and token[j].isalpha() and not token[j].isupper():
                j += 1
            segments.append((token[i:j], False))
            i = j
        else:
            j = i
            while j < len(token) and not token[j].isalpha():
                j += 1
            segments.append((token[i:j], False))
            i = j

    canonical_runs = [run for run in (
        seg for seg, is_run in segments if is_run and len(seg) >= 2
    ) if run in _ACRONYM_CANONICAL]
    if not canonical_runs:
        return None

    # Multi-run compound (CAG/RAG, RAG-CAG, dRAG/CAG): rebuild by
    # walking segments; emit canonical for upper runs ≥2, preserve
    # non-alpha separators between any two canonical runs, drop
    # everything else (lowercase noise, single-char upper runs).
    if len(canonical_runs) >= 2:
        rebuilt: list[str] = []
        last_emitted_canonical = False
        for seg, is_run in segments:
            if is_run and len(seg) >= 2 and seg in _ACRONYM_CANONICAL:
                rebuilt.append(_ACRONYM_CANONICAL[seg])
                last_emitted_canonical = True
            elif not is_run and not seg.isalpha() and last_emitted_canonical:
                # Separator after a canonical run — keep so the
                # compound shape (CAG/RAG, RAG-CAG) is preserved.
                rebuilt.append(seg)
            else:
                # Lowercase noise or stray short upper run: drop.
                # If we trail off into noise after a canonical run,
                # strip the trailing separator we may have appended.
                last_emitted_canonical = False
        result = "".join(rebuilt).rstrip("/-_.")
        return result or None

    # Single canonical run with lowercase noise: emit canonical alone.
    return _ACRONYM_CANONICAL[canonical_runs[0]]


def _split_punctuation(token: str) -> Tuple[str, str, str]:
    """Strip leading/trailing punctuation. Returns (head, core, tail)."""

    head_end = 0
    while head_end < len(token) and not (token[head_end].isalnum() or _is_hangul(token[head_end])):
        head_end += 1
    tail_start = len(token)
    while tail_start > head_end and not (
        token[tail_start - 1].isalnum() or _is_hangul(token[tail_start - 1])
    ):
        tail_start -= 1
    return token[:head_end], token[head_end:tail_start], token[tail_start:]


def _is_hangul(ch: str) -> bool:
    return "\uAC00" <= ch <= "\uD7A3"


def _fuzzy_correct(token: str) -> str | None:
    """Return canonical lexicon entry within edit distance 1 of *token*,
    or ``None`` if no candidate qualifies.

    Constraints:
      * ``len(token) >= _FUZZY_MIN_LEN``
      * candidate length within ±1 of token length
      * exactly one canonical candidate matches (tie → bail)
      * first alpha character must match (prevents wild rewrites)
    """

    if len(token) < _FUZZY_MIN_LEN:
        return None
    first = token[0].lower() if token else ""
    if not first.isalpha():
        return None
    upper = token.upper()
    matches: list[str] = []
    for lex_key, canonical in _ACRONYM_CANONICAL.items():
        if abs(len(lex_key) - len(upper)) > 1:
            continue
        if lex_key[0] != upper[0]:
            continue
        if _edit_distance_at_most_1(lex_key, upper):
            matches.append(canonical)
    # Deduplicate (canonical may repeat if lexicon ever has aliases).
    unique = sorted(set(matches))
    if len(unique) == 1:
        return unique[0]
    return None


def _edit_distance_at_most_1(a: str, b: str) -> bool:
    """Return True iff *a* and *b* differ by at most one edit
    (insertion, deletion, or substitution).
    """

    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diffs = sum(1 for x, y in zip(a, b) if x != y)
        return diffs == 1
    short, long = (a, b) if la < lb else (b, a)
    # Try inserting one char into `short` to match `long`.
    i = j = 0
    skipped = False
    while i < len(short) and j < len(long):
        if short[i] != long[j]:
            if skipped:
                return False
            skipped = True
            j += 1
        else:
            i += 1
            j += 1
    return True


__all__ = (
    "CanonicalQuery",
    "Replacement",
    "canonicalize_query",
)
