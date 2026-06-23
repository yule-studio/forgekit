"""Hephaistos resolver — request → equip plan. Rule-first, deterministic, explainable.

Infers domain/language/framework/topic from the request, scores Armory skills (facet
match + signal hits) with **language gating** (a java skill is excluded for a python
request), picks the loadout by selection signals + recommended-skill overlap, and drafts
a Work Packet. Nexus refs keep their honest status (read by nexus_read, surfaced by PR2).
Uncovered requests resolve shallow — never faked.

Selection is **evidence-bearing**: every pick (and every fact-driven exclusion) records a
``SelectionEvidence`` row saying WHAT drove it (matched signal / facet / project fact).
A "smart" choice with no evidence is a fake and does not ship. ``project_facts`` (Nexus /
operator context) and ``runtime_constraints`` (provider / runtime) are folded into the
packet as constraints + exclusions — NOT a new routing layer, just data on the existing
selection surface.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from . import armory
from .models import (
    SRC_NOT_CONNECTED,
    NexusSourceRef,
    ResolvedForgePlan,
    SelectionEvidence,
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
           ("github actions", "devops"), ("ci/cd", "devops"), ("파이프라인", "devops"),
           ("보안", "security"), ("security", "security"), ("auth review", "security"),
           ("llm", "ai"), ("rag", "ai"), ("agent eval", "ai"), ("embedding", "ai"),
           ("database", "database"), ("sql", "database"),
           ("backend", "backend"), ("api", "backend"))
_TOPIC = (("refresh token", "auth-jwt"), ("jwt", "auth-jwt"), ("oauth", "oauth"), ("oidc", "oauth"),
          ("secret", "secret"), ("rate limit", "rate-limit"), ("worker", "worker"),
          ("redis", "redis"), ("cache", "cache"), ("mysql", "mysql"), ("postgres", "postgres"),
          ("terraform", "terraform"), ("kubernetes", "k8s"), ("ecs", "ecs"), ("docker", "docker"),
          ("github actions", "ci"), ("ci/cd", "ci"), ("파이프라인", "pipeline"),
          ("figma", "figma"), ("design system", "design-system"), ("디자인 시스템", "design-system"),
          ("spacing", "spacing"), ("간격", "spacing"), ("레이아웃", "layout"),
          ("rag", "rag"), ("llm", "llm"), ("eval", "eval"), ("transaction", "transaction"))

_DOMAIN_AGENT = {"backend": "backend-engineer", "frontend": "frontend-engineer",
                 "devops": "devops-engineer", "security": "security-engineer",
                 "database": "backend-engineer", "ai": "ai-engineer", "design": "ux-ui-designer"}

# markers that turn a project fact into an *exclusion* ("EKS는 제외", "k8s 빼고").
_EXCLUDE_MARKERS = ("제외", "빼고", "말고", "없이", "제거", "exclude", "skip", "no ", "not ")


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


def _skill_why(sk, *, domain, language, framework, topic, blob) -> Tuple[str, Tuple[str, ...]]:
    """Explain (reason, matched signals) for selecting *sk* — the anti-fake trail."""

    facets = []
    if domain and domain in sk.domains:
        facets.append(f"domain={domain}")
    if language and language in sk.languages:
        facets.append(f"lang={language}")
    if framework and framework in sk.frameworks:
        facets.append(f"fw={framework}")
    if topic and topic in sk.topics:
        facets.append(f"topic={topic}")
    hits = tuple(s for s in sk.signals if s and s in blob)
    if hits:
        facets.append(f"signals={'/'.join(hits)}")
    return (", ".join(facets) or "related", hits)


def _fact_targets(fact_low: str) -> Tuple[str, ...]:
    """Catalog skill ids a project fact refers to (by id / signal / topic match)."""

    out = []
    for sk in armory.all_skills():
        keys = (sk.id, *sk.signals, *sk.topics)
        if any(k and k in fact_low for k in keys):
            out.append(sk.id)
    return tuple(dict.fromkeys(out))


def _apply_project_facts(selected_ids, facts: Sequence[str]):
    """Split facts into exclusions (drop matching skills) and constraints. Explainable.

    Returns (kept_ids, excluded_ids, constraint_texts, evidence_rows). An exclusion fact
    records evidence even when its target was not selected — the constraint is documented,
    not silently dropped.
    """

    kept = list(selected_ids)
    excluded: list = []
    constraints: list = []
    evidence: list = []
    for fact in facts:
        f = (fact or "").strip()
        if not f:
            continue
        low = f.lower()
        is_excl = any(m in low for m in _EXCLUDE_MARKERS)
        if is_excl:
            targets = _fact_targets(low)
            if targets:
                for t in targets:
                    if t in kept:
                        kept.remove(t)
                    if t not in excluded:
                        excluded.append(t)
                    evidence.append(SelectionEvidence(
                        target=t, kind="skill", decision="excluded",
                        reason=f"project fact: {f}", signals=(f,)))
            # the exclusion is also a hard constraint regardless of catalog match
            constraints.append(f)
        else:
            constraints.append(f)
            evidence.append(SelectionEvidence(
                target=f[:48], kind="constraint", decision="selected",
                reason="project fact (Nexus/operator context)", signals=(f,)))
    return tuple(kept), tuple(excluded), tuple(dict.fromkeys(constraints)), tuple(evidence)


def resolve(request: str, *, preferred_role: str = "",
            project_facts: Sequence[str] = (), runtime_constraints: Sequence[str] = (),
            harness: str = "") -> ResolvedForgePlan:
    """Forge an equip plan for *request* (suggestion-only; no install performed).

    ``project_facts`` (Nexus/operator context, e.g. "EKS 제외", "dev 환경부터") and
    ``runtime_constraints`` (provider/runtime, e.g. "no prod apply") shape selection +
    the packet's constraints. ``harness`` (e.g. "claude-code") is recorded as the intended
    executor. Every pick/exclusion is backed by a ``SelectionEvidence`` row.
    """

    domain, language, framework, topic, blob = _infer(request)

    scored = []
    evidence: list = []
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
    pre_ids = tuple(sk.id for _, sk in scored)
    by_id = {sk.id: sk for _, sk in scored}

    # fold in project facts (exclusions drop skills; non-exclusions become constraints).
    kept_ids, excluded_ids, constraints, fact_ev = _apply_project_facts(pre_ids, project_facts)
    selected = tuple(by_id[i] for i in kept_ids)

    for sk in selected:
        reason, hits = _skill_why(sk, domain=domain, language=language,
                                  framework=framework, topic=topic, blob=blob)
        evidence.append(SelectionEvidence(target=sk.id, kind="skill", decision="selected",
                                          reason=reason, signals=hits))
    evidence.extend(fact_ev)

    roles = [r for sk in selected for r in sk.related_roles]
    candidates = tuple(dict.fromkeys(roles))
    selected_agent = (preferred_role if preferred_role in candidates else "") \
        or _DOMAIN_AGENT.get(domain, "") or (candidates[0] if candidates else "")
    if selected_agent:
        evidence.append(SelectionEvidence(
            target=selected_agent, kind="agent", decision="selected",
            reason=(f"preferred_role" if selected_agent == preferred_role
                    else f"domain={domain}" if _DOMAIN_AGENT.get(domain) == selected_agent
                    else "first candidate role")))

    chosen_loadout, lo_reason, lo_signals = _pick_loadout(blob, selected, selected_agent)
    lo_spec = armory.loadout(chosen_loadout) if chosen_loadout else None
    if chosen_loadout:
        evidence.append(SelectionEvidence(target=chosen_loadout, kind="loadout",
                                          decision="selected", reason=lo_reason, signals=lo_signals))

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

    # runtime constraints (provider/runtime) are recorded as constraints + evidence.
    rc = tuple(c.strip() for c in runtime_constraints if c and c.strip())
    for c in rc:
        evidence.append(SelectionEvidence(target=c[:48], kind="constraint", decision="selected",
                                          reason="runtime/provider constraint", signals=(c,)))
    all_constraints = tuple(dict.fromkeys(constraints + rc))

    packet = _packet(request, selected, lo_spec, refs, verif, weapons, all_constraints,
                     excluded_ids, harness)
    return ResolvedForgePlan(
        request=request, domain=domain, language=language, framework=framework, topic=topic,
        candidate_agents=candidates, selected_agent=selected_agent,
        selected_skills=tuple(sk.id for sk in selected), selected_loadout=chosen_loadout,
        required_weapons=tuple(weapons), nexus_refs=tuple(refs),
        verification_commands=tuple(verif), packet_draft=packet,
        selection_evidence=tuple(evidence), excluded_skills=excluded_ids,
        project_facts=tuple(f for f in project_facts if f and f.strip()),
        runtime_constraints=rc,
    )


def _pick_loadout(blob: str, selected, agent: str) -> Tuple[str, str, Tuple[str, ...]]:
    """Score loadouts by selection signals + recommended-skill overlap (explainable)."""

    sel_ids = {sk.id for sk in selected}
    best, best_score, best_reason, best_signals = "", 0, "", ()
    for lo in armory.all_loadouts():
        sig_hits = tuple(s for s in lo.selection_signals if s and s in blob)
        rec_hits = tuple(s for s in lo.recommended_skills if s in sel_ids)
        score = len(sig_hits) + len(rec_hits) * 2
        if agent in lo.intended_roles:
            score += 1
        if score > best_score:
            reason_parts = []
            if sig_hits:
                reason_parts.append(f"signals={'/'.join(sig_hits)}")
            if rec_hits:
                reason_parts.append(f"recommends={'/'.join(rec_hits)}")
            if agent in lo.intended_roles:
                reason_parts.append(f"role={agent}")
            best, best_score = lo.id, score
            best_reason = ", ".join(reason_parts) or "best overlap"
            best_signals = sig_hits
    return best, best_reason, best_signals


def _packet(request, selected, lo_spec, refs, verif, weapons, constraints,
            excluded_ids, harness) -> WorkPacketDraft:
    rules = [r for sk in selected for r in sk.rules]
    forbidden = [f for sk in selected for f in sk.forbidden] or \
        ["승인 없는 schema/auth/deploy 변경 금지"]
    # excluded skills become explicit forbidden-scope lines (no fake "we considered it").
    for ex in excluded_ids:
        sp = armory.skill(ex)
        label = sp.name if sp else ex
        forbidden.append(f"{label} 미사용(프로젝트 제약으로 제외)")
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
        selected_tools=tuple(weapons), constraints=tuple(constraints), harness=harness,
    )


def explain_lines(plan: ResolvedForgePlan) -> Tuple[str, ...]:
    lines = [f"hephaistos resolve — {plan.request[:60]}",
             f"  domain/lang/fw/topic: {plan.domain or '-'}/{plan.language or '-'}/"
             f"{plan.framework or '-'}/{plan.topic or '-'}",
             f"  agent  : {plan.selected_agent or '(미정)'}",
             f"  skills : {', '.join(plan.selected_skills) or '(매칭 없음 — armory 얕음)'}",
             f"  loadout: {plan.selected_loadout or '-'}",
             f"  weapons: {', '.join(plan.required_weapons) or '-'}"]
    if plan.excluded_skills:
        lines.append(f"  excluded: {', '.join(plan.excluded_skills)} (project fact)")
    return tuple(lines)


__all__ = ("resolve", "explain_lines")
