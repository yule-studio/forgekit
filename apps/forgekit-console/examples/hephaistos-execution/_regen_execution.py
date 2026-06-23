"""Regenerate ``execution.txt`` — Hephaistos execution core across 3 scenarios + adoption.

Hermetic + deterministic: ``which`` is injected (terraform/docker present, awscli/gh absent),
Nexus root is empty (not_connected — honest). Shows, per scenario: selected skills/tools,
rejected candidates (+why), constraints, verification plan, expected outputs, runtime/approval
implications, and the ponytail anti-overbuild verdict. Plus a ForgeKit 도입 효율 검토 artifact
(8 fields + 3-axis review → adopt-now/collect-first/hold) for an external tool candidate.

Run: ``python apps/forgekit-console/examples/hephaistos-execution/_regen_execution.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
for _rel in ("packages/armory/src", "packages/hephaistos/src", "packages/forgekit-config/src",
             "packages/nexus/src"):
    _p = str(_ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from armory import candidate as C
from armory import catalog
from armory.models import KIND_MCP
from hephaistos import forge_execution_plan
from hephaistos.projection import execution_lines

OUT = Path(__file__).resolve().parent / "execution.txt"
_PRESENT = {"terraform", "docker", "node", "npm", "git", "vscode"}
_WHICH = lambda b: ("/usr/bin/" + b) if b in _PRESENT else None  # noqa: E731


def _hr(t: str) -> str:
    return f"\n{'─' * 78}\n{t}\n{'─' * 78}"


def main() -> None:
    catalog.clear_overlay()
    out = ["HEPHAISTOS EXECUTION CORE — equip · Nexus · loadout/packet · ponytail (evidence)",
           "(hermetic: which 주입[terraform/docker 존재·awscli/gh 부재], Nexus not_connected)"]

    scenarios = [
        ("1) Terraform + ECS (project facts + runtime constraints + harness)",
         "Terraform 으로 ECS 배포 환경 구성. Claude Code 한테 맡길 프롬프트 만들어줘.",
         ("EKS는 제외", "dev 환경부터", "기존 구조 보존"), ("production apply 금지",), "claude-code"),
        ("2) Frontend design system / UI refactor",
         "Next.js 디자인 시스템 토큰 간격 일관성 UI refactor 해줘.",
         ("기존 토큰 유지", "컴포넌트 단위로"), (), "claude-code"),
        ("3) Docs quality / prose cleanup (built-in, tool-less)",
         "README 문서 품질 개선하고 prose 다듬어줘.", (), (), "claude-code"),
    ]
    for title, req, facts, rc, h in scenarios:
        ep = forge_execution_plan(req, project_facts=facts, runtime_constraints=rc, harness=h,
                                  env={}, which=_WHICH)
        out.append(_hr(title))
        out.append(f"request : {req}")
        if facts:
            out.append(f"facts   : {', '.join(facts)}")
        rej = ep.plan.rejected_candidates
        out.append("rejected candidates (why-not): " +
                   (", ".join(f"{r.target}[{r.category}]" for r in rej) or "(없음)"))
        out.extend(execution_lines(ep))

    # ForgeKit 도입 효율 검토 — external tool candidate (8 fields + 3-axis review).
    out.append(_hr("도입 효율 검토 artifact — 외부 후보 'trivy-scan' (adopt-now vs collect-first vs hold)"))
    review = C.AdoptionReview(
        candidate_id="trivy-scan",
        current_pain="이미지/IaC 취약점을 사람이 수동 점검 — 누락 위험",
        expected_benefit="CI 단계에서 자동 게이트 → 취약점 조기 차단",
        overlap_with_existing="web-security-review 와 일부 겹침(정적 점검). 런타임 이미지 스캔은 신규 능력",
        operational_cost="CI 시간 +~30s, DB 캐시 관리",
        maintenance_risk="취약점 DB 업데이트 의존, false positive 튜닝 필요",
        provider_runtime_fit="github-actions 와 적합(provider_affinity=github)",
        governance_security_impact="읽기 전용, secret 불필요 — governance 위험 낮음",
        adopt_timing_reason="pain 크고 overlap 제한적 → adopt-now (단, 3축 합의 전제)",
        axis_reviews=(C.AxisReview(C.AXIS_PM, "pm", C.ADOPT_NOW, "보안 가치 높음"),
                      C.AxisReview(C.AXIS_TECH_LEAD, "tech-lead", C.ADOPT_NOW, "유지비 수용 가능"),
                      C.AxisReview(C.AXIS_SPECIALIST, "security-engineer", C.ADOPT_NOW, "OWASP 커버 확대")))
    for f in C.AdoptionReview._FIELDS:
        out.append(f"  {f}: {getattr(review, f)}")
    for a in review.axis_reviews:
        out.append(f"  axis[{a.axis}] {a.reviewer}: {a.position} — {a.rationale}")
    out.append(f"  → disposition: {review.disposition()}  (gaps: {', '.join(review.review_gaps()) or '없음'})")
    out.append("  주: disposition=adopt-now 라도 console 은 projection only — 실제 등록은 register_promoted,")
    out.append("     장착(equipped)은 install/attach 충족 후. adopted≠equipped 분리 유지.")

    out.append(_hr("hold 예시 — specialist 축 누락 시 강제 hold (no fake adoption)"))
    held = C.AdoptionReview(candidate_id="x", current_pain="p", expected_benefit="b",
                            overlap_with_existing="o", operational_cost="c", maintenance_risk="m",
                            provider_runtime_fit="f", governance_security_impact="g",
                            adopt_timing_reason="t",
                            axis_reviews=(C.AxisReview(C.AXIS_PM, "pm", C.ADOPT_NOW),))
    out.append(f"  axes={held.axes_present()} → disposition: {held.disposition()}")
    out.append(f"  gaps: {', '.join(held.review_gaps())}")

    catalog.clear_overlay()
    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(_ROOT)} ({len(out)} lines)")


if __name__ == "__main__":
    main()
