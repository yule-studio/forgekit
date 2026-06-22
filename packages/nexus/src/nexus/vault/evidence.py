"""Goal evidence → Nexus vault accumulation (final-completion 축4 — "evidence 가 Nexus/vault 누적").

`forgekit_goal` keeps append-only :class:`EvidenceRecord` entries in the goal store; this bridge
writes each as a **curated, authored vault note** under the connected Nexus root, so evidence
accumulates in the knowledge plane (Obsidian-readable), not only in the goal store. It reuses the
existing authored-note writer (:mod:`nexus.vault.note`) — no new vault format.

Honesty rails (no fake):
- **no fake nexus connection** — with no ``vault_root`` it returns ``not_connected`` and writes
  nothing; a write that fails returns the failure, never a pretended note.
- **append-only / idempotent** — an evidence note path is deterministic from (goal, index); an
  existing note is left untouched (skipped), mirroring the append-only evidence contract.
- ``forgekit_goal`` is imported **lazily / best-effort** so ``nexus`` keeps its single
  ``forgekit-config`` dependency; if the goal package is absent the bridge degrades honestly.

Pure given (records, vault_root) → unit-testable; the goal-store adapter is exercised end-to-end
with a tempdir store + tempdir vault.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Optional, Sequence, Tuple

from . import note

# evidence notes live under one project subtree (Obsidian-friendly, one folder per goal).
EVIDENCE_DIR = "10-projects/forgekit/evidence"
DEFAULT_AGENT = "knowledge-engineer"   # vault authorship identity for accumulated evidence

# honest connection verdicts for an accumulation run.
STATUS_CONNECTED = "connected"
STATUS_NOT_CONNECTED = "not_connected"   # no vault root → nothing written (no fake)
STATUS_NO_GOAL = "no_goal"               # goal store / goal id unresolved (best-effort)


def _slug(text: str, *, limit: int = 32) -> str:
    s = re.sub(r"[^0-9a-zA-Z가-힣]+", "-", (text or "").strip()).strip("-").lower()
    return (s[:limit] or "x")


def evidence_subpath(goal_id: str, index: int, record) -> str:
    """Deterministic, stable note path for one evidence record (goal folder + zero-padded index)."""

    kind = _slug(getattr(record, "kind", "") or "note", limit=16)
    return f"{EVIDENCE_DIR}/{_slug(goal_id, limit=24)}/{index:03d}-{kind}.md"


def evidence_note_content(goal_id: str, goal_title: str, index: int, record,
                          *, agent_id: str = DEFAULT_AGENT) -> str:
    """Render one :class:`EvidenceRecord` (or any obj with ts/kind/summary/ref) as an authored note."""

    ts = getattr(record, "ts", "") or ""
    kind = getattr(record, "kind", "") or "note"
    summary = getattr(record, "summary", "") or ""
    ref = getattr(record, "ref", None)
    body = "\n".join((
        "## 핵심 요약",
        f"- {summary}",
        "",
        "## 적용 맥락",
        f"- goal: {goal_title} (`{goal_id}`)",
        f"- evidence #{index} · kind: {kind} · ts: {ts or '(미기록)'}",
        "",
        "## 참고 (ref)",
        f"- {ref}" if ref else "- (외부 artifact ref 없음)",
        "",
        "## 관련 노트",
        f"- [[{_slug(goal_id, limit=24)}]] (goal evidence 누적)",
    ))
    return note.build_authored_note(
        agent_id,
        title=f"evidence — {goal_title[:40]} #{index} ({kind})",
        body=body, kind="evidence", status="recorded", created_at=ts,
        tags=("forgekit", "evidence", _slug(kind, limit=16)),
        related=(_slug(goal_id, limit=24),),
    )


def write_evidence_note(goal_id: str, goal_title: str, index: int, record,
                        *, vault_root, agent_id: str = DEFAULT_AGENT,
                        overwrite: bool = False) -> Optional[Path]:
    """Write ONE evidence record as a vault note. Idempotent: an existing note is skipped
    (append-only) unless *overwrite*. Returns the path, or None when no vault / write failed."""

    if not vault_root:
        return None
    subpath = evidence_subpath(goal_id, index, record)
    target = Path(vault_root) / subpath
    if target.exists() and not overwrite:
        return target                       # already accumulated — append-only, no rewrite
    return note.write_note(evidence_note_content(goal_id, goal_title, index, record,
                                                 agent_id=agent_id), vault_root, subpath)


@dataclass(frozen=True)
class AccumulationResult:
    """Honest summary of one evidence→vault accumulation run."""

    goal_id: str
    status: str
    vault_root: str = ""
    written: Tuple[str, ...] = ()          # newly written note subpaths
    skipped: Tuple[str, ...] = ()          # already-present (append-only) subpaths
    evidence_count: int = 0

    @property
    def connected(self) -> bool:
        return self.status == STATUS_CONNECTED

    def to_dict(self) -> dict:
        return {"goal_id": self.goal_id, "status": self.status, "vault_root": self.vault_root,
                "written": list(self.written), "skipped": list(self.skipped),
                "evidence_count": self.evidence_count}


def accumulate_records(goal_id: str, goal_title: str, records: Sequence,
                       *, vault_root, agent_id: str = DEFAULT_AGENT) -> AccumulationResult:
    """Accumulate a sequence of evidence records into the vault. Pure given the records —
    no goal-store dependency. Honest ``not_connected`` (writes nothing) when no vault root."""

    if not vault_root:
        return AccumulationResult(goal_id, STATUS_NOT_CONNECTED, evidence_count=len(records))
    written: List[str] = []
    skipped: List[str] = []
    for i, rec in enumerate(records, start=1):
        sub = evidence_subpath(goal_id, i, rec)
        existed = (Path(vault_root) / sub).exists()
        p = write_evidence_note(goal_id, goal_title, i, rec, vault_root=vault_root, agent_id=agent_id)
        if p is None:
            continue                         # write failed → honest omission (not counted as written)
        (skipped if existed else written).append(sub)
    return AccumulationResult(goal_id, STATUS_CONNECTED, str(vault_root),
                              tuple(written), tuple(skipped), len(records))


def accumulate_goal_evidence(goal_id: str, *, vault_root, env: Optional[Mapping[str, str]] = None,
                             agent_id: str = DEFAULT_AGENT, store=None) -> AccumulationResult:
    """Read a goal's append-only evidence from the goal store and accumulate it into the vault.

    ``forgekit_goal`` is imported lazily (nexus keeps its lone forgekit-config dep). Honest:
    ``not_connected`` when no vault, ``no_goal`` when the goal/store can't be resolved."""

    if not vault_root:
        return AccumulationResult(goal_id, STATUS_NOT_CONNECTED)
    if store is None:
        try:
            from forgekit_goal.store import GoalStore  # lazy / best-effort
        except Exception:  # noqa: BLE001 - goal package absent → honest degrade
            return AccumulationResult(goal_id, STATUS_NO_GOAL, str(vault_root))
        store = GoalStore(env=env)
    goal = store.get(goal_id)
    if goal is None:
        return AccumulationResult(goal_id, STATUS_NO_GOAL, str(vault_root))
    return accumulate_records(goal.id, goal.title, goal.evidence,
                              vault_root=vault_root, agent_id=agent_id)


__all__ = (
    "EVIDENCE_DIR", "DEFAULT_AGENT", "STATUS_CONNECTED", "STATUS_NOT_CONNECTED", "STATUS_NO_GOAL",
    "evidence_subpath", "evidence_note_content", "write_evidence_note",
    "AccumulationResult", "accumulate_records", "accumulate_goal_evidence",
)
