"""Hephaistos execution core — request → equipped, Nexus-enriched, reviewed work packet.

The forge floor. ``resolve`` picks skills/loadout/tools (the *selection* engine); this
module turns that into an **execution plan** ready to hand to an executor:

  1. **equip** — split *adopted* (in the plan) from *equipped* (the loadout's tools are
     actually present locally, via the verifier). A tool can be adopted but NOT equipped;
     that gap is surfaced, never hidden.
  2. **Nexus enrich** — read the plan's Nexus refs (honest: not_connected/missing stay so)
     and fold the real project rules/points into the packet → project-specific, not generic.
  3. **ponytail** — run the anti-overbuild lens → verdict (review/consult/waived) so the
     surface knows whether to escalate. Ponytail is a lens, never the approver.
  4. **assemble** — goal / selected skills+tools / rejected candidates / constraints /
     verification plan / expected outputs / runtime+approval implications.

Pure-ish (the verifier's ``which`` and the Nexus root are injectable → deterministic tests).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, replace
from typing import Callable, Mapping, Optional, Sequence, Tuple

from . import armory, nexus_read as nx, ponytail, resolver, verifier
from .models import ResolvedForgePlan, WorkPacketDraft


@dataclass(frozen=True)
class EquipStatus:
    """adopted (selected) vs equipped (tools present locally) — the honest gap."""

    loadout: str
    readiness: str                       # ready / partial / missing / blocked
    adopted_skills: Tuple[str, ...] = ()
    equipped_tools: Tuple[str, ...] = ()    # weapons present on the machine
    not_equipped: Tuple[str, ...] = ()      # adopted but missing → install before execute
    install_steps: Tuple[str, ...] = ()

    @property
    def fully_equipped(self) -> bool:
        return self.readiness == verifier.READY

    def to_dict(self) -> dict:
        return {"loadout": self.loadout, "readiness": self.readiness,
                "adopted_skills": list(self.adopted_skills),
                "equipped_tools": list(self.equipped_tools),
                "not_equipped": list(self.not_equipped), "install_steps": list(self.install_steps),
                "fully_equipped": self.fully_equipped}


@dataclass(frozen=True)
class ExecutionPlan:
    request: str
    plan: ResolvedForgePlan
    packet: WorkPacketDraft                 # Nexus-enriched copy of plan.packet_draft
    equip: EquipStatus
    nexus_facts: Tuple[str, ...] = ()       # project-specific rules/points read from Nexus
    nexus_connected: bool = False
    expected_outputs: Tuple[str, ...] = ()
    verification_plan: Tuple[str, ...] = ()
    runtime_approval: Tuple[str, ...] = ()
    ponytail: Optional[ponytail.PonytailVerdict] = None

    def to_dict(self) -> dict:
        return {"request": self.request, "plan": self.plan.to_dict(),
                "packet": self.packet.to_dict() if self.packet else None,
                "equip": self.equip.to_dict(), "nexus_facts": list(self.nexus_facts),
                "nexus_connected": self.nexus_connected,
                "expected_outputs": list(self.expected_outputs),
                "verification_plan": list(self.verification_plan),
                "runtime_approval": list(self.runtime_approval),
                "ponytail": self.ponytail.to_dict() if self.ponytail else None}


def _equip(plan: ResolvedForgePlan, which: Optional[Callable[[str], Optional[str]]]) -> EquipStatus:
    """adopted = selected skills; equipped = required weapons present locally.

    Drives readiness off ``plan.required_weapons`` (loadout + skill tools), so a tool-less,
    skill-only task (e.g. docs prose) is READY with nothing to install — NOT 'missing'. A
    weapon adopted but absent goes to ``not_equipped`` (adopted ≠ equipped, surfaced honestly).
    """

    probe = which or shutil.which
    present, missing, steps = [], [], []
    for wid in plan.required_weapons:
        w = armory.weapon(wid)
        binary = (w.verify_command or "").strip().split(" ")[0] if w else wid
        if binary and probe(binary):
            present.append(wid)
        else:
            missing.append(wid)
            if w and w.install_hint:
                steps.append(f"{w.display_name} 설치: {w.install_hint}")
    if not plan.selected_skills:
        readiness = verifier.MISSING          # nothing adopted → nothing to equip (shallow)
    elif not missing:
        readiness = verifier.READY            # tool-less or all tools present
    elif present:
        readiness = verifier.PARTIAL
    else:
        readiness = verifier.MISSING
    return EquipStatus(loadout=plan.selected_loadout, readiness=readiness,
                       adopted_skills=plan.selected_skills, equipped_tools=tuple(present),
                       not_equipped=tuple(missing), install_steps=tuple(steps))


def _nexus_facts(read: nx.NexusReadResult) -> Tuple[str, ...]:
    """Project-specific rules/points actually read from Nexus (honest — empty if unconnected)."""

    facts: list = []
    for doc in read.resolved_docs:
        if doc.read_mode != nx.READ_RAW:
            continue
        for rule in doc.rules[:3]:
            facts.append(f"{doc.title or doc.source_ref.ref}: {rule}")
        if not doc.rules:
            for pt in doc.key_points[:2]:
                facts.append(f"{doc.title or doc.source_ref.ref}: {pt}")
    return tuple(dict.fromkeys(facts))


def _expected_outputs(plan: ResolvedForgePlan) -> Tuple[str, ...]:
    out: list = []
    lo = armory.loadout(plan.selected_loadout) if plan.selected_loadout else None
    if lo and lo.goal:
        out.append(f"loadout 목표: {lo.goal}")
    for sid in plan.selected_skills:
        sk = armory.skill(sid)
        if sk:
            out.extend(sk.expected_outputs)
    return tuple(dict.fromkeys(out))


def _verification_plan(plan: ResolvedForgePlan, equip: EquipStatus) -> Tuple[str, ...]:
    steps: list = []
    if not equip.fully_equipped and equip.install_steps:
        steps.append("0) 장비 설치 — " + "; ".join(equip.install_steps))
    lo = armory.loadout(plan.selected_loadout) if plan.selected_loadout else None
    for v in (lo.default_verify_flow if lo else ()):
        steps.append(f"verify: {v}")
    for v in plan.verification_commands:
        line = f"verify: {v}"
        if line not in steps:
            steps.append(line)
    return tuple(steps)


def _runtime_approval(plan: ResolvedForgePlan, equip: EquipStatus,
                      pony: ponytail.PonytailVerdict) -> Tuple[str, ...]:
    impl: list = [f"approval level: {plan.packet_draft.approval_level if plan.packet_draft else '-'}"]
    if not equip.fully_equipped:
        impl.append(f"장비 미충족({equip.readiness}) — 실행 전 설치 필요(adopted≠equipped): "
                    f"{', '.join(equip.not_equipped) or '-'}")
    else:
        impl.append("loadout 장비 ready — 즉시 실행 가능(승인 게이트는 별개)")
    fb = " ".join(plan.packet_draft.forbidden_scope).lower() if plan.packet_draft else ""
    if any(t in fb for t in ("prod", "apply", "배포", "운영")):
        impl.append("prod/배포/apply 경계 존재 — operator 승인 필수(자동 실행 금지)")
    impl.append(f"ponytail: {pony.verdict} — {pony.reason}")
    if pony.needs_escalation:
        impl.append("→ ponytail 비-waived: tech-lead review / cross-role consult 후 진행"
                    " (ponytail 은 승인자 아님)")
    return tuple(impl)


def _enrich_packet(packet: WorkPacketDraft, nexus_facts: Sequence[str]) -> WorkPacketDraft:
    if not packet or not nexus_facts:
        return packet
    scope = tuple(dict.fromkeys(tuple(packet.scope) + tuple(f"[nexus] {f}" for f in nexus_facts)))
    return replace(packet, scope=scope)


def forge_execution_plan(request: str, *, preferred_role: str = "",
                         project_facts: Sequence[str] = (), runtime_constraints: Sequence[str] = (),
                         harness: str = "",
                         env: Optional[Mapping[str, str]] = None,
                         config: Optional[Mapping] = None, role: str = "",
                         which: Optional[Callable[[str], Optional[str]]] = None) -> ExecutionPlan:
    """Forge a full execution plan: resolve → equip → Nexus enrich → ponytail → assemble."""

    plan = resolver.resolve(request, preferred_role=preferred_role, project_facts=project_facts,
                            runtime_constraints=runtime_constraints, harness=harness)
    equip = _equip(plan, which)
    read = nx.read_plan_sources(plan, env=env, config=config, role=role)
    nexus_facts = _nexus_facts(read)

    # new adoptions = selected skills that came from the runtime overlay (promoted, not base
    # seed) — the genuinely *new* capabilities ponytail should scrutinise before equipping.
    overlay_ids = {s.id for s in armory.promoted_skills()}
    new_adoptions = tuple(s for s in plan.selected_skills if s in overlay_ids)
    domains = tuple(dict.fromkeys(
        d for sid in plan.selected_skills for d in (armory.skill(sid).domains if armory.skill(sid) else ())))

    pk = plan.packet_draft
    pony = ponytail.ponytail_review(
        request=request, selected_skills=plan.selected_skills,
        selected_tools=pk.selected_tools if pk else plan.required_weapons,
        constraints=pk.constraints if pk else (),
        forbidden_scope=pk.forbidden_scope if pk else (),
        new_adoptions=new_adoptions, domains=domains)

    enriched = _enrich_packet(pk, nexus_facts)
    return ExecutionPlan(
        request=request, plan=plan, packet=enriched, equip=equip, nexus_facts=nexus_facts,
        nexus_connected=read.connected, expected_outputs=_expected_outputs(plan),
        verification_plan=_verification_plan(plan, equip),
        runtime_approval=_runtime_approval(plan, equip, pony), ponytail=pony)


__all__ = ("EquipStatus", "ExecutionPlan", "forge_execution_plan")
