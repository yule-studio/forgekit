"""Hephaistos resolver — request → equip plan. Rule-first, deterministic, explainable.

Infers domain/language/framework/topic from the request, scores Armory skills (facet
match + signal hits) with **language gating** (a java skill is excluded for a python
request), picks the loadout by selection signals + recommended-skill overlap, and drafts
a Work Packet. Nexus refs keep their honest status (read by nexus_read, surfaced by PR2).
Uncovered requests resolve shallow — never faked.
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

_LANGUAGE = (("kotlin", "kotlin"), ("java", "java"), ("python", "python"), ("fastapi", "python"),
             ("typescript", "typescript"), ("javascript", "typescript"),
             ("react", "typescript"), ("next", "typescript"), ("nest", "typescript"))
_FRAMEWORK = (("spring boot", "spring-boot"), ("springboot", "spring-boot"), ("spring", "spring-boot"),
              ("fastapi", "fastapi"), ("nestjs", "nestjs"), ("nest", "nestjs"),
              ("next.js", "nextjs"), ("nextjs", "nextjs"), ("vite", "vite"), ("react", "react"))
_DOMAIN = (("figma", "design"), ("디자인", "design"), ("design system", "design"),
           ("frontend", "frontend"), ("ui", "frontend"), ("레이아웃", "frontend"),
           ("devops", "devops"), ("terraform", "devops"), ("kubernetes", "devops"), ("ecs", "devops"),
           ("보안", "security"), ("security", "security"), ("auth review", "security"),
           ("llm", "ai"), ("rag", "ai"), ("agent eval", "ai"), ("embedding", "ai"),
           ("database", "database"), ("sql", "database"),
           ("backend", "backend"), ("api", "backend"))
_TOPIC = (("refresh token", "auth-jwt"), ("jwt", "auth-jwt"), ("oauth", "oauth"), ("oidc", "oauth"),
          ("secret", "secret"), ("rate limit", "rate-limit"), ("worker", "worker"),
          ("redis", "redis"), ("cache", "cache"), ("mysql", "mysql"), ("postgres", "postgres"),
          ("terraform", "terraform"), ("kubernetes", "k8s"), ("ecs", "ecs"), ("docker", "docker"),
          ("figma", "figma"), ("design system", "design-system"), ("디자인 시스템", "design-system"),
          ("spacing", "spacing"), ("간격", "spacing"), ("레이아웃", "layout"),
          ("rag", "rag"), ("llm", "llm"), ("eval", "eval"), ("transaction", "transaction"))

_DOMAIN_AGENT = {"backend": "backend-engineer", "frontend": "frontend-engineer",
                 "devops": "devops-engineer", "security": "security-engineer",
                 "database": "backend-engineer", "ai": "ai-engineer", "design": "ux-ui-designer"}


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
    if framework in ("spring-boot",) and not language:
        language = "java"
    if framework in ("spring-boot", "fastapi", "nestjs") and not domain:
        domain = "backend"
    if framework in ("nextjs", "react", "vite") and not domain:
        domain = "frontend"
    if topic in ("auth-jwt", "oauth") and not domain:
        domain = "security" if "review" in blob else "backend"
    return domain, language, framework, topic, blob


def resolve(request: str, *, preferred_role: str = "") -> ResolvedForgePlan:
    """Forge an equip plan for *request* (suggestion-only; no install performed)."""

    domain, language, framework, topic, blob = _infer(request)

    scored = []
    for sk in armory.all_skills():
        # LANGUAGE GATE: a language-specific skill is excluded for a different language.
        if language and sk.languages and language not in sk.languages:
            continue
        score = sk.matches(domain=domain, language=language, framework=framework, topic=topic)
        score += sk.signal_score(blob)
        score += sum(1 for t in sk.topics if t in blob)
        if score > 0:
            scored.append((score, sk))
    scored.sort(key=lambda x: (-x[0], x[1].id))
    selected = tuple(sk for _, sk in scored)

    roles = [r for sk in selected for r in sk.related_roles]
    candidates = tuple(dict.fromkeys(roles))
    selected_agent = (preferred_role if preferred_role in candidates else "") \
        or _DOMAIN_AGENT.get(domain, "") or (candidates[0] if candidates else "")

    chosen_loadout = _pick_loadout(blob, selected, selected_agent)
    lo_spec = armory.loadout(chosen_loadout) if chosen_loadout else None

    weapons = list(lo_spec.required_weapons) if lo_spec else []
    for sk in selected:
        for w in sk.related_weapons:
            if w not in weapons:
                weapons.append(w)

    refs = []
    for sk in selected:
        for ref in sk.nexus_refs:
            refs.append(NexusSourceRef(ref.kind, ref.ref, SRC_NOT_CONNECTED, note=f"for skill {sk.id}"))

    verif = list(lo_spec.verify_commands) if lo_spec else []
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


def _pick_loadout(blob: str, selected, agent: str) -> str:
    """Score loadouts by selection signals + recommended-skill overlap (explainable)."""

    sel_ids = {sk.id for sk in selected}
    best, best_score = "", 0
    for lo in armory.all_loadouts():
        score = sum(1 for s in lo.selection_signals if s and s in blob)
        score += sum(1 for s in lo.recommended_skills if s in sel_ids) * 2
        if agent in lo.intended_roles:
            score += 1
        if score > best_score:
            best, best_score = lo.id, score
    return best


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
        scope=tuple(dict.fromkeys(rules)) or ("선택 skill 의 규칙을 따른다",),
        forbidden_scope=tuple(dict.fromkeys(forbidden)),
        required_areas=tuple(r.ref for r in refs),
        commands=tuple(commands), verification=tuple(verif),
        acceptance=("선택 skill 의 verification 통과", "unsafe 영역 미변경(승인 필요)"),
        approval_level="L2_internal_approve", evidence_path="runs/forgekit/hephaistos/",
        nexus_refs=tuple(refs),
    )


def explain_lines(plan: ResolvedForgePlan) -> Tuple[str, ...]:
    lines = [f"hephaistos resolve — {plan.request[:60]}",
             f"  domain/lang/fw/topic: {plan.domain or '-'}/{plan.language or '-'}/"
             f"{plan.framework or '-'}/{plan.topic or '-'}",
             f"  agent  : {plan.selected_agent or '(미정)'}",
             f"  skills : {', '.join(plan.selected_skills) or '(매칭 없음 — armory 얕음)'}",
             f"  loadout: {plan.selected_loadout or '-'}",
             f"  weapons: {', '.join(plan.required_weapons) or '-'}"]
    return tuple(lines)


__all__ = ("resolve", "explain_lines")
