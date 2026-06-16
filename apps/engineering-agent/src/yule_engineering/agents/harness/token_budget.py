"""Token-efficiency core — the consistent estimator + deterministic slimming transforms.

This is the heart of the "토큰 효율 코어": pure, deterministic functions that
(a) estimate token cost with ONE consistent method (so baseline vs after are
comparable) and (b) shrink the three heaviest runner-fed payloads without
touching audit/governance:

  * :func:`estimate_tokens` — single estimator (chars/4 ceil), reused everywhere
    measurement happens. Token efficiency work is about *fewer chars carried*,
    not model choice, so a char-based estimate is the right common ruler.
  * :func:`build_policy_bundle` — instead of shipping every policy full-text,
    render a *digest* (heading + first paragraph + pointer path). Full text
    stays available for debug/diagnostic; the runner-fed bundle is the digest.
  * :func:`compact_decisions` — fold old ``previous_decisions`` to one-line refs
    when the channel exceeds a token threshold, preserving the protected region
    (most-recent K + any decision/synthesis-kind entry). Mirrors
    ``context-compression.md`` 3.2 protected regions.
  * :func:`reference_sources` — carry source_context as title+pointer+snippet
    references instead of full bodies.

Every transform returns a small ``*Result`` with ``pre_tokens`` / ``post_tokens``
/ ``saved_tokens`` so the benchmark + receipt can show measurable evidence.

Hard rails: these transforms NEVER drop a protected item — they fold non-recent,
non-decision middle entries to references that still carry a back-pointer. They
do not write memory, do not modify policy, and are off the live path unless a
caller opts in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional, Sequence, Tuple

# One ruler for every measurement. Matches context_compaction._estimate_tokens
# so the compaction core and the budget core agree.
_CHARS_PER_TOKEN: int = 4

# Kinds whose entries are never folded out of previous_decisions
# (context-compression.md 3.2).
PROTECTED_DECISION_KINDS: frozenset[str] = frozenset({"decision", "synthesis"})


def estimate_tokens(text: Optional[str]) -> int:
    """Consistent token estimate: ceil(len/4). The single ruler for evidence."""

    return (len(text or "") + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def estimate_mapping_tokens(payload: Mapping[str, Any]) -> int:
    """Estimate tokens for a small structured payload (keys + str values)."""

    total = 0
    for key, value in (payload or {}).items():
        total += estimate_tokens(str(key))
        if isinstance(value, Mapping):
            total += estimate_mapping_tokens(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                total += estimate_tokens(str(item))
        else:
            total += estimate_tokens(str(value))
    return total


# ---------------------------------------------------------------------------
# Policy bundle digest (B)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BundleDoc:
    label: str
    path: str
    full_tokens: int
    fed_tokens: int
    fed_text: str


@dataclass(frozen=True)
class PolicyBundle:
    mode: str  # "full" | "digest"
    docs: Tuple[BundleDoc, ...]
    full_tokens: int
    fed_tokens: int

    @property
    def saved_tokens(self) -> int:
        return max(0, self.full_tokens - self.fed_tokens)

    @property
    def doc_count(self) -> int:
        return len(self.docs)


def digest_text(full_text: str, *, max_chars: int = 280) -> str:
    """First heading + first non-empty paragraph, capped to *max_chars*.

    Deterministic: no model call. Keeps a pointer-quality summary so a reader
    knows what the doc covers without carrying the whole body.
    """

    lines = (full_text or "").splitlines()
    heading = ""
    for line in lines:
        s = line.strip()
        if s.startswith("#"):
            heading = s.lstrip("#").strip()
            break
    para = ""
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or s.startswith(("-", "|", ">", "```")):
            continue
        para = s
        break
    parts = [p for p in (heading, para) if p]
    out = " — ".join(parts) if parts else (full_text or "").strip()[:max_chars]
    if len(out) > max_chars:
        out = out[:max_chars].rstrip() + "…"
    return out


def build_policy_bundle(
    documents: Sequence[Any],
    *,
    mode: str = "digest",
    max_chars: int = 280,
    repo_root: Optional[Any] = None,
) -> PolicyBundle:
    """Build a runner-fed policy bundle from context documents.

    *documents* are objects with ``.label`` / ``.path`` / ``.content`` (the
    :class:`ContextDocument` shape). ``mode="full"`` reproduces the heavy
    baseline (every policy full-text); ``mode="digest"`` ships a pointer+summary
    per doc. Both report full vs fed tokens so the delta is measurable.
    """

    docs: List[BundleDoc] = []
    full_total = 0
    fed_total = 0
    for doc in documents:
        content = getattr(doc, "content", "") or ""
        label = str(getattr(doc, "label", "doc"))
        path_obj = getattr(doc, "path", "")
        path = _display_path(path_obj, repo_root)
        full = estimate_tokens(content)
        if mode == "full":
            fed_text = content
        else:
            fed_text = f"[{label}] {path}\n{digest_text(content, max_chars=max_chars)}"
        fed = estimate_tokens(fed_text)
        docs.append(BundleDoc(label=label, path=path, full_tokens=full, fed_tokens=fed, fed_text=fed_text))
        full_total += full
        fed_total += fed
    return PolicyBundle(mode=mode, docs=tuple(docs), full_tokens=full_total, fed_tokens=fed_total)


def _display_path(path_obj: Any, repo_root: Optional[Any]) -> str:
    try:
        from pathlib import Path

        p = Path(str(path_obj))
        if repo_root is not None:
            try:
                return str(p.resolve().relative_to(Path(repo_root).resolve()))
            except ValueError:
                return str(path_obj)
        return p.name
    except Exception:  # noqa: BLE001
        return str(path_obj)


# ---------------------------------------------------------------------------
# previous_decisions compaction (D)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionCompaction:
    decisions: Tuple[Mapping[str, Any], ...]
    pre_tokens: int
    post_tokens: int
    applied: bool
    folded_count: int

    @property
    def saved_tokens(self) -> int:
        return max(0, self.pre_tokens - self.post_tokens)


_PLACEHOLDER_MAX = 90


def _decision_text(d: Mapping[str, Any]) -> str:
    return str(d.get("summary") or d.get("message") or "")


def _decision_kind(d: Mapping[str, Any]) -> str:
    return str(d.get("kind") or "").strip().lower()


def _fold_decision(d: Mapping[str, Any]) -> Mapping[str, Any]:
    role = str(d.get("role") or "?")
    flat = re.sub(r"\s+", " ", _decision_text(d)).strip()
    head = flat[:_PLACEHOLDER_MAX]
    if len(flat) > _PLACEHOLDER_MAX:
        head = head.rstrip() + "…"
    ref = d.get("audit_id") or d.get("entry_id") or "-"
    return {
        "role": role,
        "summary": f"{head} (생략 {len(flat)}자, ref={ref})",
        "folded": True,
    }


def compact_decisions(
    decisions: Sequence[Mapping[str, Any]],
    *,
    threshold_tokens: int = 1200,
    keep_recent: int = 4,
) -> DecisionCompaction:
    """Fold old ``previous_decisions`` entries when the channel is over budget.

    Preserved verbatim: the most-recent *keep_recent* entries and any entry
    whose ``kind`` is decision/synthesis. Everything else (older role takes) is
    folded to a one-line reference that keeps role + a back-pointer. No-op when
    the channel is already under *threshold_tokens*.
    """

    ordered = list(decisions)
    pre = sum(estimate_tokens(_decision_text(d)) for d in ordered)
    if pre <= threshold_tokens or len(ordered) <= keep_recent:
        return DecisionCompaction(
            decisions=tuple(ordered), pre_tokens=pre, post_tokens=pre, applied=False, folded_count=0
        )

    n = len(ordered)
    keep_idx = set(range(max(0, n - keep_recent), n))
    for i, d in enumerate(ordered):
        if _decision_kind(d) in PROTECTED_DECISION_KINDS:
            keep_idx.add(i)

    out: List[Mapping[str, Any]] = []
    folded = 0
    for i, d in enumerate(ordered):
        if i in keep_idx:
            out.append(d)
        else:
            out.append(_fold_decision(d))
            folded += 1

    post = sum(estimate_tokens(_decision_text(d)) for d in out)
    return DecisionCompaction(
        decisions=tuple(out), pre_tokens=pre, post_tokens=post, applied=True, folded_count=folded
    )


# ---------------------------------------------------------------------------
# source_context reference-mode (C/D)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceReference:
    slim: Mapping[str, Any]
    pre_tokens: int
    post_tokens: int

    @property
    def saved_tokens(self) -> int:
        return max(0, self.pre_tokens - self.post_tokens)


def reference_sources(
    source_context: Mapping[str, Any],
    *,
    max_items: int = 5,
    max_summary_chars: int = 240,
) -> SourceReference:
    """Carry source_context as title + capped summary + source references.

    Replaces a long inlined body with a pointer-quality reference. Deterministic.
    """

    pre = estimate_mapping_tokens(source_context or {})
    slim: dict[str, Any] = {}
    title = str((source_context or {}).get("title") or "").strip()
    if title:
        slim["title"] = title
    summary = str((source_context or {}).get("summary") or "").strip()
    if summary:
        slim["summary"] = summary[:max_summary_chars] + ("…" if len(summary) > max_summary_chars else "")
    sources = (source_context or {}).get("sources") or ()
    if isinstance(sources, (list, tuple)) and sources:
        slim["sources"] = [str(s) for s in list(sources)[:max_items]]
    post = estimate_mapping_tokens(slim)
    return SourceReference(slim=slim, pre_tokens=pre, post_tokens=post)


__all__ = (
    "estimate_tokens",
    "estimate_mapping_tokens",
    "PROTECTED_DECISION_KINDS",
    "BundleDoc",
    "PolicyBundle",
    "digest_text",
    "build_policy_bundle",
    "DecisionCompaction",
    "compact_decisions",
    "SourceReference",
    "reference_sources",
)
