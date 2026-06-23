"""Ponytail — the anti-overbuild review lens for a Hephaistos execution plan. Pure/stdlib.

"Ponytail" is the skeptical reviewer who asks *"do we actually need all this?"* BEFORE a
plan is executed. It is a **lens, not an approver**: it never authorizes work and never
replaces the tech-lead — it emits one of three verdicts so the execution surface knows
whether to escalate:

  * ``review-required``     — overbuild / prod-risk smell → a tech-lead must review.
  * ``consult-required``    — a NEW capability or an unconstrained broad loadout → get a
                              cross-role consult before proceeding.
  * ``waived-with-reason``  — proportionate; built-ins suffice → proceed, reason recorded.

Every verdict carries the findings that drove it (no silent pass, no fake clearance). The
checks are deterministic and explainable; the most-friction finding wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

REVIEW_REQUIRED = "review-required"
CONSULT_REQUIRED = "consult-required"
WAIVED_WITH_REASON = "waived-with-reason"
PONYTAIL_VERDICTS = (REVIEW_REQUIRED, CONSULT_REQUIRED, WAIVED_WITH_REASON)

# friction order — the strongest finding sets the verdict.
_RANK = {WAIVED_WITH_REASON: 0, CONSULT_REQUIRED: 1, REVIEW_REQUIRED: 2}

# tokens in forbidden-scope that signal a production / irreversible touch.
_PROD_TOKENS = ("prod", "production", "운영", "apply", "배포")
# tokens that show a dev-first / plan-first guard is already in place (de-risks prod smell).
_DEV_GUARD_TOKENS = ("dev", "plan-first", "plan 먼저", "승인", "dry-run", "preview")


@dataclass(frozen=True)
class PonytailFinding:
    check: str            # which check fired
    verdict: str          # the verdict this finding argues for
    detail: str

    def to_dict(self) -> dict:
        return {"check": self.check, "verdict": self.verdict, "detail": self.detail}


@dataclass(frozen=True)
class PonytailVerdict:
    verdict: str
    reason: str
    findings: Tuple[PonytailFinding, ...] = ()

    @property
    def needs_escalation(self) -> bool:
        return self.verdict in (REVIEW_REQUIRED, CONSULT_REQUIRED)

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, "reason": self.reason,
                "needs_escalation": self.needs_escalation,
                "findings": [f.to_dict() for f in self.findings]}


def ponytail_review(*, request: str, selected_skills: Sequence[str],
                    selected_tools: Sequence[str], constraints: Sequence[str],
                    forbidden_scope: Sequence[str] = (),
                    new_adoptions: Sequence[str] = (),
                    domains: Sequence[str] = ()) -> PonytailVerdict:
    """Review an assembled plan for overbuild. Returns a verdict + the findings behind it.

    ``new_adoptions`` = capability ids being adopted that are NOT already built-in (a new
    dependency). ``domains`` = the distinct domains the selected skills span (breadth).
    """

    findings: list = []
    skills = [s for s in selected_skills if s]
    tools = [t for t in selected_tools if t]
    doms = [d for d in domains if d]

    # 1) prod / irreversible touch without a dev-first guard → tech-lead review.
    fb = " ".join(forbidden_scope).lower()
    cons = " ".join(constraints).lower()
    if any(t in fb for t in _PROD_TOKENS):
        guarded = any(g in cons for g in _DEV_GUARD_TOKENS) or any(g in fb for g in _DEV_GUARD_TOKENS)
        if not guarded:
            findings.append(PonytailFinding(
                "prod-touch-unguarded", REVIEW_REQUIRED,
                "prod/배포/apply 경계가 보이는데 dev-first/plan-first/승인 제약이 없음"))
        else:
            findings.append(PonytailFinding(
                "prod-touch-guarded", WAIVED_WITH_REASON,
                "prod 경계가 dev-first/plan-first/승인 제약으로 가드됨 (proportionate)"))

    # 2) tool sprawl — more tools than skills warrant → overbuild review.
    if len(tools) > max(2, len(skills) + 1):
        findings.append(PonytailFinding(
            "tool-sprawl", REVIEW_REQUIRED,
            f"tools {len(tools)} > skills {len(skills)} — task 대비 과장비 의심"))

    # 3) a NEW capability is being equipped → cross-role consult before adding the dep.
    if new_adoptions:
        findings.append(PonytailFinding(
            "new-adoption", CONSULT_REQUIRED,
            f"신규 능력 장착 시도: {', '.join(new_adoptions)} — 기존 능력으로 충분한지 consult"))

    # 4) unconstrained broad loadout — many skills across domains, no task constraints.
    if not constraints and len(skills) >= 4 and len(set(doms)) >= 2:
        findings.append(PonytailFinding(
            "unconstrained-broad", CONSULT_REQUIRED,
            f"제약 없는 광범위 loadout(skills {len(skills)}, domains {len(set(doms))}) — 만능 loadout 금지"))

    if not findings:
        return PonytailVerdict(
            WAIVED_WITH_REASON,
            "proportionate — 기존 내장 능력으로 task 를 덮고, 과장비/신규 dep/prod 위험 신호 없음",
            ())

    top = max(findings, key=lambda f: _RANK[f.verdict])
    reason = "; ".join(f.detail for f in findings if f.verdict == top.verdict) or top.detail
    return PonytailVerdict(top.verdict, reason, tuple(findings))


__all__ = ("REVIEW_REQUIRED", "CONSULT_REQUIRED", "WAIVED_WITH_REASON", "PONYTAIL_VERDICTS",
           "PonytailFinding", "PonytailVerdict", "ponytail_review")
