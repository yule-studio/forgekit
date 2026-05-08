"""Agent-ops audit log — A-M10a.

Companion to :mod:`agents.lifecycle.autonomy_policy`. Whenever an
:class:`AutonomyDecision` says ``audit_required=True`` (i.e. L1 or
above), the producer writes an :class:`AgentOpsEntry` here so a
human can later answer "왜 이게 사용자 승인 없이 진행됐어?" /
"이 thread 에서 같은 주제 두 번째 저장 요청은 어디로 갔지?"

Persistence model — A-M10a stage:

  * Entries land in ``session.extra['agent_ops_audit']`` as a list
    of payloads (JSON-friendly dicts), so a single SQLite write of
    the existing session row carries the audit forward.
  * **No Obsidian write yet** — that's M10b's job (vault
    ``40-agent-ops/`` folder + research-log auto-save). Keeping the
    audit in-session-extra first means the M10a commit closes the
    "every dedup decision is observable" loop without touching the
    vault layout.

The :func:`render_agent_ops_log_markdown` helper is M10b-shaped: it
renders the in-session list to the markdown a future Obsidian
writer will splice into a daily ``40-agent-ops/<date>.md`` page.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple


SESSION_EXTRA_KEY: str = "agent_ops_audit"
AGENT_OPS_VAULT_FOLDER: str = "40-agent-ops"


# ---------------------------------------------------------------------------
# Entry dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentOpsEntry:
    """One audit row.

    ``action`` matches the :mod:`autonomy_policy` action constant.
    ``autonomy_level`` is the string form of
    :class:`AutonomyLevel` so the entry survives a JSON round-trip.

    ``outcome`` describes what actually happened ("approval_card_queued",
    "skipped:topic_already_saved", "research_log_saved",
    "failure:approval_worker_raised"). It's distinct from
    ``reason`` — the reason is *why the agent had the autonomy to
    do this without a human gate*; the outcome is *what the agent
    actually did*.
    """

    entry_id: str
    session_id: str
    action: str
    autonomy_level: str
    summary: str
    reasoning: str
    outcome: str
    references: Tuple[str, ...] = ()
    topic_key: Optional[str] = None
    job_id: Optional[str] = None
    decision_id: Optional[str] = None
    actor: str = "engineering-agent"
    recorded_at: str = ""

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "entry_id": self.entry_id,
            "session_id": self.session_id,
            "action": self.action,
            "autonomy_level": self.autonomy_level,
            "summary": self.summary,
            "reasoning": self.reasoning,
            "outcome": self.outcome,
            "references": list(self.references),
            "topic_key": self.topic_key,
            "job_id": self.job_id,
            "decision_id": self.decision_id,
            "actor": self.actor,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_payload(cls, data: Optional[Mapping[str, Any]]) -> Optional["AgentOpsEntry"]:
        if not isinstance(data, Mapping):
            return None
        entry_id = data.get("entry_id")
        if not isinstance(entry_id, str) or not entry_id:
            return None
        refs_raw = data.get("references") or ()
        references = tuple(
            str(r) for r in refs_raw if isinstance(r, str) and r
        )
        return cls(
            entry_id=entry_id,
            session_id=str(data.get("session_id") or ""),
            action=str(data.get("action") or ""),
            autonomy_level=str(data.get("autonomy_level") or ""),
            summary=str(data.get("summary") or ""),
            reasoning=str(data.get("reasoning") or ""),
            outcome=str(data.get("outcome") or ""),
            references=references,
            topic_key=_optional_str(data.get("topic_key")),
            job_id=_optional_str(data.get("job_id")),
            decision_id=_optional_str(data.get("decision_id")),
            actor=str(data.get("actor") or "engineering-agent"),
            recorded_at=str(data.get("recorded_at") or ""),
        )


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# ---------------------------------------------------------------------------
# Builder + persistence helpers
# ---------------------------------------------------------------------------


def build_agent_ops_entry(
    *,
    decision: Any,
    outcome: str,
    summary: Optional[str] = None,
    references: Sequence[str] = (),
    job_id: Optional[str] = None,
    actor: str = "engineering-agent",
    recorded_at: Optional[str] = None,
) -> AgentOpsEntry:
    """Build an :class:`AgentOpsEntry` from an
    :class:`AutonomyDecision` plus the producer's outcome string.

    *summary* defaults to ``decision.summary`` when present.
    *recorded_at* defaults to the current UTC ISO-8601 timestamp.
    """

    autonomy_level = getattr(getattr(decision, "autonomy_level", None), "value", "")
    return AgentOpsEntry(
        entry_id=_new_entry_id(),
        session_id=str(getattr(decision, "session_id", "") or ""),
        action=str(getattr(decision, "action", "") or ""),
        autonomy_level=str(autonomy_level or ""),
        summary=str(summary or getattr(decision, "summary", "") or "").strip(),
        reasoning=str(getattr(decision, "reason", "") or "").strip(),
        outcome=str(outcome or "").strip(),
        references=tuple(
            str(r) for r in references if isinstance(r, str) and r
        ),
        topic_key=_optional_str(getattr(decision, "topic_key", None)),
        job_id=_optional_str(job_id or getattr(decision, "job_id", None)),
        decision_id=_optional_str(getattr(decision, "decision_id", None)),
        actor=actor,
        recorded_at=recorded_at or _utc_now_iso(),
    )


def append_agent_ops_audit(
    extra: Optional[Mapping[str, Any]],
    entry: AgentOpsEntry,
    *,
    max_entries: int = 200,
) -> dict:
    """Return a new ``session.extra`` dict with *entry* appended to
    the agent-ops audit list. Original is not mutated.

    The list is capped at *max_entries* — older entries fall off
    the head. The session row stays small even after many decisions
    on a long-lived research thread.
    """

    new_extra: dict = dict(extra or {})
    raw = new_extra.get(SESSION_EXTRA_KEY)
    existing: list[Mapping[str, Any]] = []
    if isinstance(raw, list):
        existing = [item for item in raw if isinstance(item, Mapping)]
    existing.append(dict(entry.to_payload()))
    if len(existing) > max_entries:
        existing = existing[-max_entries:]
    new_extra[SESSION_EXTRA_KEY] = existing
    return new_extra


def read_agent_ops_audit(source: Any) -> Tuple[AgentOpsEntry, ...]:
    """Read agent-ops entries out of either a session-shaped object
    (with ``.extra``) or a raw extra mapping. Returns oldest-first.
    """

    if source is None:
        return ()
    if isinstance(source, Mapping):
        extra = source
    else:
        extra = getattr(source, "extra", None)
    if not isinstance(extra, Mapping):
        return ()
    raw = extra.get(SESSION_EXTRA_KEY)
    if not isinstance(raw, list):
        return ()
    out: list[AgentOpsEntry] = []
    for item in raw:
        entry = AgentOpsEntry.from_payload(item)
        if entry is not None:
            out.append(entry)
    return tuple(out)


# ---------------------------------------------------------------------------
# Markdown renderers
# ---------------------------------------------------------------------------


def render_agent_ops_entry_markdown(entry: AgentOpsEntry) -> str:
    """Render *entry* as a single audit row.

    Compact format, one fenced block per entry, every field on its
    own line so a future log scanner can grep by key without parsing
    JSON.
    """

    lines = [
        f"**[{entry.autonomy_level}] {entry.action}**",
        "",
        f"entry: `{entry.entry_id}`",
        f"세션: `{entry.session_id}` · 작업: `{entry.job_id or '-'}`",
    ]
    if entry.topic_key:
        lines.append(f"topic: `{entry.topic_key}`")
    if entry.decision_id:
        lines.append(f"decision: `{entry.decision_id}`")
    if entry.summary:
        lines.append(f"요약: {entry.summary}")
    if entry.reasoning:
        lines.append(f"사유: {entry.reasoning}")
    if entry.outcome:
        lines.append(f"결과: {entry.outcome}")
    if entry.references:
        lines.append("참조:")
        for ref in entry.references:
            lines.append(f"  - {ref}")
    lines.append(f"기록 시각: {entry.recorded_at}")
    lines.append(f"행위자: {entry.actor}")
    return "\n".join(lines)


def render_agent_ops_log_markdown(
    entries: Iterable[AgentOpsEntry],
    *,
    title: str = "agent-ops 로그",
) -> str:
    """Render a series of entries as the markdown a future M10b
    Obsidian writer will splice into ``40-agent-ops/<date>.md``.
    """

    materialized = list(entries)
    if not materialized:
        return f"# {title}\n\n_기록 없음_"
    blocks = [f"# {title}", ""]
    for entry in materialized:
        blocks.append("---")
        blocks.append(render_agent_ops_entry_markdown(entry))
        blocks.append("")
    return "\n".join(blocks).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def _new_entry_id() -> str:
    return f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:12]}"


__all__ = (
    "AGENT_OPS_VAULT_FOLDER",
    "AgentOpsEntry",
    "SESSION_EXTRA_KEY",
    "append_agent_ops_audit",
    "build_agent_ops_entry",
    "read_agent_ops_audit",
    "render_agent_ops_entry_markdown",
    "render_agent_ops_log_markdown",
)
