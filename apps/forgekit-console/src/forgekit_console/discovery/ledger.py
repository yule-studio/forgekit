"""Discovery ledger — make swept ideas **accumulate** across runs (the 누적 seam).

A single `/discovery` sweep is ephemeral: run it twice and the same ideas resurface as
"new" every time. The ledger makes the loop a real personal-assistant memory — a
persisted, deduplicated store of every idea ever surfaced, each carrying a lifecycle
status (new → seen → promoted / saved / parked) and accumulation evidence
(first_seen / last_seen / seen_count).

Stored as ONE JSON object under the runtime state dir (NOT the vault — that's the
separate evidence/curated track), keyed by a stable fingerprint of the idea's problem
text so re-seeing an idea updates it in place rather than duplicating it. Best-effort
I/O: a store failure never crashes a sweep; the in-memory view is always returned.

This mirrors the forge-receipt ledger's "make the decision log accumulate" posture,
but ideas mutate state (an idea gets promoted), so it's a keyed store, not append-only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from forgekit_config.paths import state_dir

from . import models as M
from .sweep import next_questions_for

_LEDGER_NAME = "discovery_ledger.json"

# idea lifecycle ---------------------------------------------------------------
ST_NEW = "new"            # surfaced this sweep, never seen before
ST_SEEN = "seen"          # surfaced again in a later sweep, still undecided
ST_PROMOTED = "promoted"  # promoted to a PM handoff packet
ST_SAVED = "saved"        # persisted as an authored vault note
ST_PARKED = "parked"      # operator set aside (won't resurface as pending)

_PENDING = (ST_NEW, ST_SEEN)
_DECIDED = (ST_PROMOTED, ST_SAVED, ST_PARKED)


def discovery_ledger_path(env: Optional[Mapping[str, str]] = None) -> Path:
    """The accumulating discovery ledger (JSON) under the runtime state dir."""

    return state_dir(env) / _LEDGER_NAME


def fingerprint(problem: str) -> str:
    """Stable dedup key for an idea — normalised problem text → short sha1."""

    norm = " ".join((problem or "").lower().split())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


@dataclass
class LedgerIdea:
    """One tracked idea + its lifecycle state and accumulation evidence."""

    fingerprint: str
    title: str
    problem: str
    score: float = 0.0
    source_id: str = ""
    why: str = ""
    status: str = ST_NEW
    first_seen: str = ""
    last_seen: str = ""
    seen_count: int = 1
    next_questions: Tuple[str, ...] = ()
    note_path: str = ""               # set when saved to vault
    brief: dict = field(default_factory=dict)  # full IdeaBrief.to_dict() for rebuild

    @property
    def pending(self) -> bool:
        return self.status in _PENDING

    def to_dict(self) -> dict:
        return {
            "fingerprint": self.fingerprint, "title": self.title, "problem": self.problem,
            "score": self.score, "source_id": self.source_id, "why": self.why,
            "status": self.status, "first_seen": self.first_seen,
            "last_seen": self.last_seen, "seen_count": self.seen_count,
            "next_questions": list(self.next_questions), "note_path": self.note_path,
            "brief": self.brief,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LedgerIdea":
        return cls(
            fingerprint=d.get("fingerprint", ""), title=d.get("title", ""),
            problem=d.get("problem", ""), score=float(d.get("score", 0.0) or 0.0),
            source_id=d.get("source_id", ""), why=d.get("why", ""),
            status=d.get("status", ST_NEW), first_seen=d.get("first_seen", ""),
            last_seen=d.get("last_seen", ""), seen_count=int(d.get("seen_count", 1) or 1),
            next_questions=tuple(d.get("next_questions", ()) or ()),
            note_path=d.get("note_path", ""), brief=d.get("brief", {}) or {},
        )

    def rebuild_brief(self) -> M.IdeaBrief:
        """Reconstruct the IdeaBrief for promotion / note authoring (no re-sweep)."""

        if self.brief:
            return M.IdeaBrief.from_dict(self.brief)
        return M.IdeaBrief(title=self.title or self.problem[:40], problem=self.problem,
                           score=self.score)


@dataclass
class DiscoveryLedger:
    """The persisted, deduplicated store of every surfaced idea + its lifecycle."""

    ideas: Dict[str, LedgerIdea] = field(default_factory=dict)

    # --- load / save ----------------------------------------------------------
    @classmethod
    def load(cls, env: Optional[Mapping[str, str]] = None) -> "DiscoveryLedger":
        try:
            raw = discovery_ledger_path(env).read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError):
            return cls()
        ideas = {fp: LedgerIdea.from_dict(d)
                 for fp, d in (data.get("ideas", {}) or {}).items()}
        return cls(ideas=ideas)

    def save(self, env: Optional[Mapping[str, str]] = None) -> Optional[Path]:
        try:
            path = discovery_ledger_path(env)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"ideas": {fp: i.to_dict() for fp, i in self.ideas.items()}}
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
            return path
        except OSError:
            return None

    # --- accumulation ---------------------------------------------------------
    def record_sweep(self, sweep, *, now: str = "") -> Tuple[List[LedgerIdea], List[LedgerIdea]]:
        """Merge a sweep's briefs into the ledger. Returns (new, updated) ideas.

        New fingerprint → tracked as ST_NEW. Re-seen pending idea → bumped (still
        pending, status normalised to ST_SEEN) so it doesn't masquerade as brand-new.
        Already-decided ideas (promoted/saved/parked) → last_seen bumped only; they
        never resurface as new/pending."""

        entries = {e.get("problem"): e for e in sweep.digest.entries}
        new: List[LedgerIdea] = []
        updated: List[LedgerIdea] = []
        for brief in sweep.briefs:
            fp = fingerprint(brief.problem)
            enr = entries.get(brief.problem, {})
            refs = brief.references or ()
            source_id = (refs[0].get("source_id") if refs else "") or "operator"
            why = enr.get("why") or f"score {brief.score} · 출처 {source_id}"
            nq = tuple(enr.get("next_questions") or next_questions_for(brief))
            existing = self.ideas.get(fp)
            if existing is None:
                idea = LedgerIdea(
                    fingerprint=fp, title=brief.title, problem=brief.problem,
                    score=brief.score, source_id=source_id, why=why, status=ST_NEW,
                    first_seen=now, last_seen=now, seen_count=1, next_questions=nq,
                    brief=brief.to_dict())
                self.ideas[fp] = idea
                new.append(idea)
            else:
                existing.last_seen = now or existing.last_seen
                existing.seen_count += 1
                existing.score = brief.score
                existing.why = why
                existing.next_questions = nq
                if not existing.brief:
                    existing.brief = brief.to_dict()
                if existing.status == ST_NEW:
                    existing.status = ST_SEEN
                if existing.pending:
                    updated.append(existing)
        return new, updated

    def mark(self, fp: str, status: str, *, note_path: str = "") -> Optional[LedgerIdea]:
        idea = self.ideas.get(fp)
        if idea is None:
            return None
        idea.status = status
        if note_path:
            idea.note_path = note_path
        return idea

    # --- views ----------------------------------------------------------------
    def pending(self) -> List[LedgerIdea]:
        """Undecided ideas, highest score first (the operator's decision queue)."""

        return sorted((i for i in self.ideas.values() if i.pending),
                      key=lambda i: (-i.score, i.first_seen, i.fingerprint))

    def by_status(self, status: str) -> List[LedgerIdea]:
        return [i for i in self.ideas.values() if i.status == status]

    def summary(self) -> dict:
        counts: Dict[str, int] = {}
        for i in self.ideas.values():
            counts[i.status] = counts.get(i.status, 0) + 1
        return {
            "total": len(self.ideas),
            "pending": len(self.pending()),
            "promoted": counts.get(ST_PROMOTED, 0),
            "saved": counts.get(ST_SAVED, 0),
            "parked": counts.get(ST_PARKED, 0),
            "by_status": counts,
        }


__all__ = (
    "ST_NEW", "ST_SEEN", "ST_PROMOTED", "ST_SAVED", "ST_PARKED",
    "discovery_ledger_path", "fingerprint", "LedgerIdea", "DiscoveryLedger",
)
