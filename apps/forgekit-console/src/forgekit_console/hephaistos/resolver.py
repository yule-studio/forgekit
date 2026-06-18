"""Hephaistos resolver — request → equip plan. Rule-first, deterministic, explainable.

Reads the request, infers domain/language/framework/topic, then selects the agent +
skills + loadout + weapons from the armory and drafts a Work Packet. Nexus source refs
are attached from the selected skills but carry their honest ``status`` — Nexus is not
live-connected here, so they stay ``not_connected`` (never faked as read).
"""

from __future__ import annotations

from typing import Optional, Tuple

from . import armory
from .models import (
    SRC_NOT_CONNECTED,
    NexusSourceRef,
    ResolvedForgePlan,
    WorkPacketDraft,
)

# keyword → inferred facet (first match wins; deterministic ordering).
_LANGUAGE = (("kotlin", "kotlin"), ("java", "java"), ("python", "python"),
             ("typescript", "typescript"), ("javascript", "typescript"))
_FRAMEWORK = (("spring boot", "spring-boot"), ("springboot", "spring-boot"), ("spring", "spring-boot"),
              ("fastapi", "fastapi"), ("nestjs", "nestjs"), ("next.js", "nextjs"), ("nextjs", "nextjs"),
              ("react", "react"))
_DOMAIN = (("frontend", "frontend"), ("ui", "frontend"), ("devops", "devops"), ("terraform", "devops"),
           ("kubernetes", "devops"), ("security", "security"), ("database", "database"),
           ("sql", "database"), ("backend", "backend"), ("api", "backend"))
_TOPIC = (("refresh token", "auth-jwt"), ("jwt", "auth-jwt"), ("oauth", "auth-jwt"),
          ("auth", "auth-jwt"), ("redis", "redis"), ("cache", "cache"), ("mysql", "mysql"),
          ("docker", "docker"), ("transaction", "transaction"))

_DOMAIN_AGENT = {"backend": "backend-engineer", "frontend": "frontend-engineer",
                 "devops": "devops-engineer", "security": "security-engineer",
                 "database": "backend-engineer", "ai": "ai-engineer"}


def _first(pairs, blob: str) -> str:
    for needle, value in pairs:
        if needle in blob:
            return value
    return ""


def _infer(request: str):
    blob = (request or "").lower()
    language = _first(_LANGUAGE, blob)
    framework = _first(_FRAMEWORK, blob)
    domain = _first(_DOMAIN, blob)
    topic = _first(_TOPIC, blob)
    # framework implies language/domain when not explicit.
    if framework in ("spring-boot",) and not language:
        language = "java"
    if framework in ("spring-boot", "fastapi", "nestjs") and not domain:
        domain = "backend"
    if framework in ("nextjs", "react") and not domain:
        domain = "frontend"
    if topic in ("auth-jwt",) and not domain:
        domain = "backend"
    return domain, language, framework, topic, blob


def resolve(request: str, *, preferred_role: str = "") -> ResolvedForgePlan:
    """Forge an equip plan for *request* (suggestion-only; no install performed)."""

    domain, language, framework, topic, blob = _infer(request)

    # score skills by facet match + a keyword hit on the skill's own topics.
    scored = []
    for sk in armory.all_skills():
        score = sk.matches(domain=domain, language=language, framework=framework, topic=topic)
        score += sum(1 for t in sk.topics if t in blob)
        if score > 0:
            scored.append((score, sk))
    scored.sort(key=lambda x: (-x[0], x[1].id))
    selected = tuple(sk for _, sk in scored)

    # candidate agents from the selected skills' roles; pick by preference/domain.
    roles = [r for sk in selected for r in sk.related_roles]
    candidates = tuple(dict.fromkeys(roles))
    selected_agent = (preferred_role if preferred_role in candidates else "") \
        or _DOMAIN_AGENT.get(domain, "") or (candidates[0] if candidates else "")

    # loadout: the one whose intended roles include the agent.
    chosen_loadout = ""
    for lo in armory.all_loadouts():
        if selected_agent in lo.intended_roles:
            chosen_loadout = lo.id
            break
    lo_spec = armory.loadout(chosen_loadout) if chosen_loadout else None

    # weapons = loadout required + selected skills' related weapons (dedup, order-stable).
    weapons = list(lo_spec.required_weapons) if lo_spec else []
    for sk in selected:
        for w in sk.related_weapons:
            if w not in weapons:
                weapons.append(w)

    # nexus refs from selected skills — honest status (not_connected: Nexus not wired).
    refs = []
    for sk in selected:
        for ref in sk.nexus_refs:
            refs.append(NexusSourceRef(ref.kind, ref.ref, SRC_NOT_CONNECTED,
                                       note=f"for skill {sk.id}"))

    verif = []
    if lo_spec:
        verif += list(lo_spec.verify_commands)
    for sk in selected:
        for v in sk.verification:
            if v not in verif:
                verif.append(v)

    packet = _packet(request, selected, lo_spec, refs, verif)
    return ResolvedForgePlan(
        request=request, domain=domain, language=language, framework=framework, topic=topic,
        candidate_agents=candidates, selected_agent=selected_agent,
        selected_skills=tuple(sk.id for sk in selected), selected_loadout=chosen_loadout,
        required_weapons=tuple(weapons), nexus_refs=tuple(refs),
        verification_commands=tuple(verif), packet_draft=packet,
    )


def _packet(request, selected, lo_spec, refs, verif) -> WorkPacketDraft:
    rules = [r for sk in selected for r in sk.rules]
    forbidden = [f for sk in selected for f in sk.forbidden] or \
        ["승인 없는 schema/auth/deploy 변경 금지"]
    commands = []
    for sk in selected:
        for c in sk.commands:
            if c not in commands:
                commands.append(c)
    return WorkPacketDraft(
        goal=request or "(요청 없음)",
        scope=tuple(rules) or ("선택 skill 의 규칙을 따른다",),
        forbidden_scope=tuple(forbidden),
        required_areas=tuple(r.ref for r in refs),
        commands=tuple(commands), verification=tuple(verif),
        acceptance=("선택 skill 의 verification 통과", "unsafe 영역 미변경(승인 필요)"),
        approval_level="L2_internal_approve",
        evidence_path="runs/forgekit/hephaistos/",
        nexus_refs=tuple(refs),
    )


def explain_lines(plan: ResolvedForgePlan) -> Tuple[str, ...]:
    """Operator-facing `/resolve` summary — what was equipped and why."""

    lines = [f"hephaistos resolve — {plan.request[:60]}",
             f"  domain/lang/fw/topic: {plan.domain or '-'}/{plan.language or '-'}/"
             f"{plan.framework or '-'}/{plan.topic or '-'}",
             f"  agent  : {plan.selected_agent or '(미정)'}  (후보: {', '.join(plan.candidate_agents) or '-'})",
             f"  skills : {', '.join(plan.selected_skills) or '(매칭 없음 — armory 얕음)'}",
             f"  loadout: {plan.selected_loadout or '-'}",
             f"  weapons: {', '.join(plan.required_weapons) or '-'}",
             f"  nexus  : {len(plan.nexus_refs)} ref ("
             + (", ".join(sorted({r.status for r in plan.nexus_refs})) or "none")
             + ") — Nexus 미연결이면 not_connected (읽은 척 안 함)"]
    if plan.packet_draft:
        lines.append(f"  packet : goal+{len(plan.packet_draft.scope)} scope / "
                     f"{len(plan.packet_draft.verification)} verify / "
                     f"approval={plan.packet_draft.approval_level}")
    return tuple(lines)


__all__ = ("resolve", "explain_lines")
