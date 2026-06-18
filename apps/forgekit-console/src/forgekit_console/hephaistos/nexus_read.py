"""Nexus read foundation (Hephaistos PR1) — read source refs, honestly. Pure/stdlib.

Nexus is an EXTERNAL knowledge source. Hephaistos reads its markdown (when connected)
and forges a BOUNDED projection to attach to a plan/packet — it never copies Nexus into
ForgeKit and never claims to have read what it couldn't:

  * no Nexus root configured → every ref is ``not_connected`` (no docs fabricated).
  * connected but path absent → ``missing``. unreadable (permission/TCC) → ``blocked``.
  * restricted source → raw read is gated; non-allowed roles get a ``projection_only``
    view (title/why), never the raw body.

``normalize_markdown`` is bounded (capped chars/points/snippets) — it extracts the few
points a resolver needs, never dumps the whole document. This is the foundation PR2's
``/resolve``/``/sources`` surface projects; no UI / command registration here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Tuple

from .models import (
    SRC_BLOCKED,
    SRC_EXISTS,
    SRC_MISSING,
    SRC_NOT_CONNECTED,
    SRC_RESTRICTED,
    NexusSourceRef,
)

ENV_NEXUS_ROOT = "FORGEKIT_NEXUS_ROOT"

# read modes for a single ref
READ_RAW = "raw"                 # the bounded normalized body was read
READ_PROJECTION = "projection_only"   # restricted → only a title/why projection
READ_NONE = "none"               # nothing read (missing / blocked / not_connected)

# path prefixes / tokens that mark a source restricted (design/privacy/contract/secret).
_RESTRICTED_TOKENS = ("restricted", "design-private", "privacy", "contract", "secret",
                      "70-restricted")
# roles allowed to read restricted raw (others get projection only).
DEFAULT_RESTRICTED_ROLES = ("design-lead", "ux-ui-designer", "privacy-officer",
                            "contract-reviewer", "security-engineer")


@dataclass(frozen=True)
class NexusDocument:
    source_ref: NexusSourceRef
    title: str = ""
    summary: str = ""
    key_points: Tuple[str, ...] = ()
    rules: Tuple[str, ...] = ()
    snippets: Tuple[str, ...] = ()
    troubleshooting_signals: Tuple[str, ...] = ()
    decision_notes: Tuple[str, ...] = ()
    read_status: str = SRC_NOT_CONNECTED
    read_mode: str = READ_NONE

    def to_dict(self) -> dict:
        return {"source_ref": self.source_ref.to_dict(), "title": self.title,
                "summary": self.summary, "key_points": list(self.key_points),
                "rules": list(self.rules), "snippets": list(self.snippets),
                "troubleshooting_signals": list(self.troubleshooting_signals),
                "decision_notes": list(self.decision_notes),
                "read_status": self.read_status, "read_mode": self.read_mode}


@dataclass(frozen=True)
class NexusReadResult:
    requested_refs: Tuple[NexusSourceRef, ...] = ()
    resolved_docs: Tuple[NexusDocument, ...] = ()
    missing_refs: Tuple[NexusSourceRef, ...] = ()
    blocked_refs: Tuple[NexusSourceRef, ...] = ()
    restricted_refs: Tuple[NexusSourceRef, ...] = ()
    not_connected: bool = False
    read_mode: str = READ_NONE
    evidence_lines: Tuple[str, ...] = ()

    @property
    def connected(self) -> bool:
        return not self.not_connected

    def to_dict(self) -> dict:
        return {"requested": len(self.requested_refs), "resolved": len(self.resolved_docs),
                "missing": len(self.missing_refs), "blocked": len(self.blocked_refs),
                "restricted": len(self.restricted_refs), "not_connected": self.not_connected,
                "read_mode": self.read_mode, "evidence_lines": list(self.evidence_lines)}


def nexus_root(env: Optional[Mapping[str, str]] = None,
               config: Optional[Mapping] = None) -> Optional[Path]:
    """The configured Nexus root (env or config). None → not connected (honest)."""

    env = os.environ if env is None else env
    raw = str(env.get(ENV_NEXUS_ROOT, "") or (config or {}).get("nexus_root", "") or "").strip()
    return Path(raw) if raw else None


def _is_restricted(path: str) -> bool:
    p = (path or "").lower()
    return any(tok in p for tok in _RESTRICTED_TOKENS)


def resolve_ref(ref: NexusSourceRef, root: Optional[Path]) -> NexusSourceRef:
    """Resolve a ref's exists_status against the (maybe-absent) Nexus root. No read."""

    from dataclasses import replace

    restricted = ref.restricted or _is_restricted(ref.ref)
    if root is None:
        return replace(ref, status=SRC_NOT_CONNECTED, restricted=restricted)
    target = root / ref.ref
    try:
        exists = target.exists()
    except OSError:
        return replace(ref, status=SRC_BLOCKED, restricted=restricted)
    if not exists:
        return replace(ref, status=SRC_MISSING, restricted=restricted)
    if not os.access(target, os.R_OK):
        return replace(ref, status=SRC_BLOCKED, restricted=restricted)
    return replace(ref, status=SRC_RESTRICTED if restricted else SRC_EXISTS, restricted=restricted)


def _capped(items, n):
    return tuple(items[:n])


def normalize_markdown(text: str, *, max_chars: int = 500, max_points: int = 8,
                       max_snippet_chars: int = 300) -> dict:
    """Bounded extraction — title / summary / key points / snippet / signals. NOT a dump."""

    lines = (text or "").splitlines()
    title = ""
    headings, points, signals, decisions = [], [], [], []
    summary_parts, snippet, in_code = [], [], False
    for ln in lines:
        s = ln.rstrip()
        if s.startswith("```"):
            in_code = not in_code
            continue
        if in_code and len("\n".join(snippet)) < max_snippet_chars:
            snippet.append(s)
            continue
        if s.startswith("# ") and not title:
            title = s[2:].strip()
        elif s.startswith("## "):
            headings.append(s[3:].strip())
        elif s.lstrip().startswith(("- ", "* ")):
            points.append(s.lstrip()[2:].strip())
        elif not s.startswith("#") and s.strip() and len(" ".join(summary_parts)) < max_chars:
            summary_parts.append(s.strip())
        low = s.lower()
        if any(k in low for k in ("error", "issue", "주의", "troubleshoot", "fail", "exception")):
            signals.append(s.strip()[:120])
        if any(k in low for k in ("decision", "결정", "trade-off", "트레이드오프")):
            decisions.append(s.strip()[:120])
    title = title or (lines[0].strip() if lines else "")
    return {
        "title": title,
        "summary": " ".join(summary_parts)[:max_chars],
        "key_points": _capped(points, max_points),
        "rules": _capped([p for p in points if any(k in p for k in ("금지", "필수", "must", "주의", "정책"))], max_points),
        "snippets": (("\n".join(snippet)[:max_snippet_chars],) if snippet else ()),
        "troubleshooting_signals": _capped(signals, 4),
        "decision_notes": _capped(decisions, 3),
    }


def read_ref(ref: NexusSourceRef, root: Optional[Path], *, role: str = "",
             restricted_roles: Tuple[str, ...] = DEFAULT_RESTRICTED_ROLES) -> NexusDocument:
    """Read ONE ref → a bounded NexusDocument. Never fabricates content for a status
    that isn't readable; restricted + non-allowed role → projection_only (no raw)."""

    resolved = resolve_ref(ref, root)
    st = resolved.status
    if st in (SRC_NOT_CONNECTED, SRC_MISSING, SRC_BLOCKED):
        # honest: no content, status preserved (no fake-read).
        return NexusDocument(resolved, read_status=st, read_mode=READ_NONE,
                             title=resolved.title_hint)
    if st == SRC_RESTRICTED and role not in restricted_roles:
        # restricted → projection only: title/why, NEVER the raw body.
        return NexusDocument(resolved, read_status=SRC_RESTRICTED, read_mode=READ_PROJECTION,
                             title=resolved.title_hint or resolved.ref,
                             summary=f"restricted source — {resolved.kind} (raw 비공개, projection only)")
    # exists (or restricted+allowed) → read + bounded normalize.
    try:
        text = (root / resolved.ref).read_text(encoding="utf-8")
    except OSError:
        return NexusDocument(resolved, read_status=SRC_BLOCKED, read_mode=READ_NONE)
    nm = normalize_markdown(text)
    return NexusDocument(
        resolved, title=nm["title"] or resolved.title_hint, summary=nm["summary"],
        key_points=nm["key_points"], rules=nm["rules"], snippets=nm["snippets"],
        troubleshooting_signals=nm["troubleshooting_signals"], decision_notes=nm["decision_notes"],
        read_status=st, read_mode=READ_RAW,
    )


def read_refs(refs, root: Optional[Path], *, role: str = "",
              restricted_roles: Tuple[str, ...] = DEFAULT_RESTRICTED_ROLES) -> NexusReadResult:
    """Read a batch of refs into a structured, honest NexusReadResult."""

    refs = tuple(refs)
    docs, missing, blocked, restricted = [], [], [], []
    for ref in refs:
        doc = read_ref(ref, root, role=role, restricted_roles=restricted_roles)
        if doc.read_status == SRC_MISSING:
            missing.append(doc.source_ref)
        elif doc.read_status == SRC_BLOCKED:
            blocked.append(doc.source_ref)
        elif doc.read_status == SRC_RESTRICTED:
            restricted.append(doc.source_ref)
            docs.append(doc)         # restricted doc is included as projection_only
        elif doc.read_status == SRC_EXISTS:
            docs.append(doc)
        # not_connected → neither resolved nor missing; surfaced via not_connected flag
    not_connected = root is None
    mode = READ_NONE if not_connected else (READ_RAW if docs else READ_NONE)
    ev = [f"nexus root: {root if root else '(not configured → not_connected)'}",
          f"requested {len(refs)} · read {len([d for d in docs if d.read_mode==READ_RAW])} "
          f"· restricted {len(restricted)} · missing {len(missing)} · blocked {len(blocked)}"]
    return NexusReadResult(
        requested_refs=refs, resolved_docs=tuple(docs), missing_refs=tuple(missing),
        blocked_refs=tuple(blocked), restricted_refs=tuple(restricted),
        not_connected=not_connected, read_mode=mode, evidence_lines=tuple(ev))


def read_plan_sources(plan, *, env: Optional[Mapping[str, str]] = None,
                      config: Optional[Mapping] = None, role: str = "") -> NexusReadResult:
    """Resolver seam — read the Nexus refs a ResolvedForgePlan declared (PR2 surfaces this)."""

    return read_refs(getattr(plan, "nexus_refs", ()), nexus_root(env, config), role=role)


def connection_status(env: Optional[Mapping[str, str]] = None,
                      config: Optional[Mapping] = None) -> dict:
    """Live Nexus connection status — connected only when a root is set AND readable.

    This is what a `/nexus` surface shows so the operator knows whether Nexus is live
    and, if not, why (not_connected / missing / blocked). Never claims a fake connection."""

    root = nexus_root(env, config)
    if root is None:
        return {"status": SRC_NOT_CONNECTED, "root": "", "connected": False,
                "reason": f"{ENV_NEXUS_ROOT} (env) / nexus_root (config) 미설정"}
    try:
        exists = root.exists()
        readable = exists and os.access(root, os.R_OK)
    except OSError:
        exists = readable = False
    if not exists:
        return {"status": SRC_MISSING, "root": str(root), "connected": False,
                "reason": "설정된 root 경로가 존재하지 않음"}
    if not readable:
        return {"status": SRC_BLOCKED, "root": str(root), "connected": False,
                "reason": "root 읽기 불가(permission/TCC)"}
    return {"status": SRC_EXISTS, "root": str(root), "connected": True, "reason": "연결됨"}


__all__ = (
    "ENV_NEXUS_ROOT", "READ_RAW", "READ_PROJECTION", "READ_NONE", "DEFAULT_RESTRICTED_ROLES",
    "NexusDocument", "NexusReadResult", "nexus_root", "resolve_ref", "normalize_markdown",
    "read_ref", "read_refs", "read_plan_sources", "connection_status",
)
