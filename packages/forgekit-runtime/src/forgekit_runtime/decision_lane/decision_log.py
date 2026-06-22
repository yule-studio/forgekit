"""Replay-able governance decision log — consult / meeting / decision / approval evidence.

The council chain is only real if its steps leave a durable, re-readable trail. This is an
append-only per-session JSONL (under the runtime state dir) of governance events; replay
reconstructs the lane readiness so "what was confirmed before execution" can be audited
after the fact, not just at the moment.

Anti-fake: an event's ``valid`` flag is set by RE-RUNNING the artifact's validator at
record time (:func:`record_lane_artifacts`), never asserted by the caller — a rubber-stamp
meeting or an unsigned decision is logged as ``valid=False`` and the replayed readiness
refuses to call the lane executable. There is no path to a "ready" replay off invalid
artifacts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple

from forgekit_config.paths import state_dir

from .readiness import (
    STAGE_DECISION_PENDING,
    STAGE_EXECUTABLE,
    STAGE_HANDOFF_PENDING,
    STAGE_MEETING_PENDING,
    STAGE_NO_PM_BRIEF,
    LaneReadiness,
)
from .schemas import CONDITIONAL, SIGNED_OFF
from .validators import (
    validate_consult,
    validate_handoff,
    validate_meeting,
    validate_pm_brief,
    validate_tech_lead_decision,
)

# governance event kinds
KIND_BRIEF = "brief"           # PM product artifact
KIND_CONSULT = "consult"       # a consult / discussion note (non-gating)
KIND_GATEWAY = "gateway"       # gateway intake verdict (approve/reject/request-more-info)
KIND_MEETING = "meeting"       # design meeting held
KIND_DECISION = "decision"     # tech-lead signoff
KIND_APPROVAL = "approval"     # operator approval
KIND_HANDOFF = "handoff"       # engineer handoff
KIND_EXECUTION = "execution"   # execution receipt
EVENT_KINDS: Tuple[str, ...] = (
    KIND_BRIEF, KIND_CONSULT, KIND_GATEWAY, KIND_MEETING, KIND_DECISION, KIND_APPROVAL,
    KIND_HANDOFF, KIND_EXECUTION,
)


@dataclass(frozen=True)
class GovernanceEvent:
    session_id: str
    kind: str
    actor: str = ""              # role that produced the artifact
    summary: str = ""
    valid: bool = True           # validator verdict at record time (anti-fake)
    ref: str = ""                # artifact id
    payload: Mapping = ()        # the artifact body (to_dict) — durable decision content
    seq: int = 0                 # assigned on replay (file order)
    at: str = ""

    def to_dict(self) -> dict:
        return {"session_id": self.session_id, "kind": self.kind, "actor": self.actor,
                "summary": self.summary, "valid": self.valid, "ref": self.ref,
                "payload": dict(self.payload or {}), "seq": self.seq, "at": self.at}

    @staticmethod
    def from_dict(d: dict, *, seq: int = 0) -> "GovernanceEvent":
        payload = d.get("payload")
        return GovernanceEvent(
            session_id=str(d.get("session_id", "")), kind=str(d.get("kind", "")),
            actor=str(d.get("actor", "")), summary=str(d.get("summary", "")),
            valid=bool(d.get("valid", True)), ref=str(d.get("ref", "")),
            payload=payload if isinstance(payload, dict) else {},
            seq=seq, at=str(d.get("at", "")))


def _safe_session(session_id: str) -> str:
    s = "".join(c if c.isalnum() or c in "-_" else "-" for c in (session_id or "").strip())
    return s or "session"


def governance_log_path(session_id: str, env: Optional[Mapping[str, str]] = None) -> Path:
    return state_dir(env) / "governance" / f"{_safe_session(session_id)}.jsonl"


def record_governance_event(event: GovernanceEvent, *, env: Optional[Mapping[str, str]] = None
                            ) -> Optional[Path]:
    """Append one governance event. Unknown kind → refused (ValueError). Best-effort I/O."""

    if event.kind not in EVENT_KINDS:
        raise ValueError(f"unknown governance event kind: {event.kind!r}")
    try:
        path = governance_log_path(event.session_id, env)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        return path
    except OSError:
        return None


def replay_governance_log(session_id: str, *, env: Optional[Mapping[str, str]] = None
                          ) -> Tuple[GovernanceEvent, ...]:
    """Read the session's events in order (seq = file position). Empty if none."""

    path = governance_log_path(session_id, env)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return ()
    events = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            events.append(GovernanceEvent.from_dict(json.loads(ln), seq=len(events)))
        except ValueError:
            continue
    return tuple(events)


def _payload(artifact) -> dict:
    """The artifact's own ``to_dict()`` if it has one — the durable decision content. A
    plain object (e.g. an approval) degrades to a minimal attribute snapshot."""

    fn = getattr(artifact, "to_dict", None)
    if callable(fn):
        try:
            d = fn()
            if isinstance(d, dict):
                return d
        except Exception:  # noqa: BLE001 — payload is evidence, never gating
            pass
    return {}


def record_lane_artifacts(
    session_id: str,
    *,
    brief=None,
    consult=None,
    gateway=None,
    meeting=None,
    decision=None,
    approval=None,
    handoff=None,
    env: Optional[Mapping[str, str]] = None,
    at: str = "",
) -> Tuple[GovernanceEvent, ...]:
    """Validate + record whichever artifacts are present (anti-fake ``valid`` flags).

    The ``valid`` flag on each event is the artifact's own validator verdict — a fake
    meeting/decision/consult is recorded ``valid=False`` so the replay can never show it
    ready. Each event also carries the artifact ``payload`` (its ``to_dict()``), so the
    durable log preserves *what was decided* (design system / coding convention / stack /
    tradeoffs / acceptance), not just that a decision happened. ``payload`` is pure
    evidence — readiness keys only on ``valid``, never on the payload, so it can't fake a
    gate. ``consult`` accepts a single :class:`ConsultNote` or an iterable of them."""

    recorded = []

    def _emit(kind, actor, summary, valid, ref, payload):
        ev = GovernanceEvent(session_id=session_id, kind=kind, actor=actor,
                             summary=summary, valid=valid, ref=ref,
                             payload=payload or {}, at=at)
        record_governance_event(ev, env=env)
        recorded.append(ev)

    if brief is not None:
        ok = not validate_pm_brief(brief)
        _emit(KIND_BRIEF, "product-manager", f"PM brief: {brief.topic}", ok,
              brief.topic, _payload(brief))
    if consult is not None:
        notes = consult if isinstance(consult, (list, tuple)) else (consult,)
        for note in notes:
            ok = not validate_consult(note)
            to = ", ".join(note.to_roles) or "-"
            _emit(KIND_CONSULT, note.by_role,
                  f"consult {note.consult_id}: {note.by_role}→[{to}] ({note.topic})",
                  ok, note.consult_id, _payload(note))
    if gateway is not None:
        # gateway 'valid' = the packet itself is well-formed AND it approved (forwarded);
        # a reject/request-more-info is a real, recorded verdict but not an advancing one.
        from .gateway import validate_gateway_packet
        ok = (not validate_gateway_packet(gateway)) and getattr(gateway, "forwarded", False)
        _emit(KIND_GATEWAY, "gateway",
              f"gateway {gateway.verdict}: {gateway.topic}", ok, gateway.topic,
              _payload(gateway))
    if meeting is not None:
        ok = not validate_meeting(meeting)
        _emit(KIND_MEETING, "tech-lead",
              f"meeting {meeting.meeting_id} ({len(meeting.participants)} 참석)", ok,
              meeting.meeting_id, _payload(meeting))
    if decision is not None:
        ok = (not validate_tech_lead_decision(decision)) and decision.status in (SIGNED_OFF, CONDITIONAL)
        _emit(KIND_DECISION, decision.signoff_by,
              f"decision {decision.decision_id} ({decision.status}/{decision.approval_level})",
              ok, decision.decision_id, _payload(decision))
    if approval is not None:
        ok = bool(getattr(approval, "approved", False))
        ap_payload = _payload(approval) or {
            "decision_ref": getattr(approval, "decision_ref", ""),
            "approver": getattr(approval, "approver", "operator"),
            "approved": bool(getattr(approval, "approved", False)),
        }
        _emit(KIND_APPROVAL, getattr(approval, "approver", "operator"),
              f"operator approval ref={getattr(approval, 'decision_ref', '')}", ok,
              getattr(approval, "decision_ref", ""), ap_payload)
    if handoff is not None:
        ok = (decision is not None) and (not validate_handoff(handoff, decision))
        _emit(KIND_HANDOFF, handoff.executor_role,
              f"handoff {handoff.handoff_id} → {handoff.executor_role}", ok,
              handoff.handoff_id, _payload(handoff))
    return tuple(recorded)


def readiness_from_log(events: Tuple[GovernanceEvent, ...]) -> LaneReadiness:
    """Reconstruct the lane readiness from a replayed event stream (audit-after-the-fact).

    Uses the same stage ladder/order as :func:`assess_lane_readiness`; a stage only counts
    as confirmed if at least one VALID event of that kind exists."""

    def _has(kind: str) -> bool:
        return any(e.kind == kind and e.valid for e in events)

    def _label(kind: str) -> str:
        for e in events:
            if e.kind == kind and e.valid:
                return e.summary
        return ""

    confirmed = []
    if not _has(KIND_BRIEF):
        return LaneReadiness(STAGE_NO_PM_BRIEF, False, (),
                             ("PM brief (유효)",), (), "PM brief 확정 — 없으면 tech-lead lane 실행 불가")
    confirmed.append(_label(KIND_BRIEF))
    if not _has(KIND_MEETING):
        return LaneReadiness(STAGE_MEETING_PENDING, False, tuple(confirmed),
                             ("실재 meeting (유효)",), (), "design meeting 소집")
    confirmed.append(_label(KIND_MEETING))
    if not _has(KIND_DECISION):
        return LaneReadiness(STAGE_DECISION_PENDING, False, tuple(confirmed),
                             ("서명된 tech-lead decision",), (),
                             "tech-lead 서명 — 없으면 specialist 실행 불가")
    confirmed.append(_label(KIND_DECISION))
    if not _has(KIND_HANDOFF):
        return LaneReadiness(STAGE_HANDOFF_PENDING, False, tuple(confirmed),
                             ("유효한 engineer handoff",), (), "engineer handoff 발행")
    confirmed.append(_label(KIND_HANDOFF))
    return LaneReadiness(STAGE_EXECUTABLE, True, tuple(confirmed), (), (),
                         "specialist 실행 인가 — replay 확인 완료")


def _trail_facts(ev: GovernanceEvent) -> str:
    """The decision content of one event, from its persisted ``payload`` — what was
    actually decided, so the operator reads 'who decided what', not just 'who acted'."""

    p = ev.payload or {}
    k = ev.kind
    if k == KIND_BRIEF:
        return (f"acceptance {len(p.get('acceptance_criteria') or [])}개 · "
                f"metric {len(p.get('success_metrics') or [])}개 · priority={p.get('priority', '')}")
    if k == KIND_CONSULT:
        to = ", ".join(p.get("to_roles") or []) or "-"
        return f"→ [{to}]: {p.get('question', '')}"
    if k == KIND_GATEWAY:
        bits = [f"verdict={p.get('verdict', '')}"]
        if p.get("info_requested"):
            bits.append(f"info {len(p['info_requested'])}개")
        if p.get("reject_reason"):
            bits.append(f"사유={p['reject_reason']}")
        return " · ".join(bits)
    if k == KIND_MEETING:
        roles = ", ".join(pp.get("role", "") for pp in (p.get("participants") or []))
        return f"참석 [{roles}]" + (" · escalated" if p.get("escalated") else "")
    if k == KIND_DECISION:
        bits = []
        if p.get("design_system"):
            bits.append(f"design={p['design_system']}")
        if p.get("coding_convention"):
            bits.append(f"convention={p['coding_convention']}")
        sd = p.get("stack_decision") or {}
        if sd.get("recommended"):
            bits.append(f"stack={sd['recommended']}")
        if p.get("approval_level"):
            bits.append(f"approval={p['approval_level']}")
        if p.get("tradeoffs"):
            bits.append(f"tradeoff {len(p['tradeoffs'])}개")
        return " · ".join(bits)
    if k == KIND_APPROVAL:
        return f"approved={p.get('approved', '')}"
    if k == KIND_HANDOFF:
        out = f"executor={p.get('executor_role', '')} · scope {len(p.get('scope') or [])}개"
        return out + (" · operator 승인 필요" if p.get("operator_required") else "")
    return ""


def decision_trail_from_log(events: Tuple[GovernanceEvent, ...]) -> Tuple[str, ...]:
    """An operator-readable 'who decided what' trail from the replayed log (time order).

    Each line = ``seq. ✓/✗ [kind] actor: summary — <decision facts from payload>``. The
    ``✗`` marks an artifact the validator rejected (anti-fake: it is recorded but does NOT
    advance the lane). Empty if the log has no events."""

    out = []
    for ev in events:
        mark = "✓" if ev.valid else "✗"
        line = f"{ev.seq + 1:>2}. {mark} [{ev.kind}] {ev.actor or '-'}: {ev.summary}"
        facts = _trail_facts(ev)
        if facts:
            line += f" — {facts}"
        out.append(line)
    return tuple(out)


__all__ = (
    "KIND_BRIEF", "KIND_CONSULT", "KIND_GATEWAY", "KIND_MEETING", "KIND_DECISION",
    "KIND_APPROVAL", "KIND_HANDOFF", "KIND_EXECUTION", "EVENT_KINDS",
    "GovernanceEvent", "governance_log_path", "record_governance_event",
    "replay_governance_log", "record_lane_artifacts", "readiness_from_log",
    "decision_trail_from_log",
)
