"""Regenerate ``intake.txt`` — Armory intake→promotion + Hephaistos context-aware selection.

Deterministic + hermetic: drives ``armory.candidate`` + ``hephaistos.resolve`` in memory
(no repo, no store, no git). Shows (1) a discovery candidate promoting into the catalog
overlay through the non-placeholder/attach gates, (2) a rejected candidate with its reasons,
and (3) the Terraform→ECS scenario where project facts exclude EKS, runtime constraints +
harness land in the packet, and every pick/exclusion carries evidence (no fake selection).

Run: ``python apps/forgekit-console/examples/armory-intake/_regen_intake.py``
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
from armory.models import KIND_TOOL
from hephaistos import resolve
from hephaistos.projection import resolve_summary_lines, selection_evidence_lines
from hephaistos.nexus_read import read_plan_sources

OUT = Path(__file__).resolve().parent / "intake.txt"


def _section(title: str) -> str:
    return f"\n{'─' * 78}\n{title}\n{'─' * 78}"


def main() -> None:
    catalog.clear_overlay()
    out: list = ["ARMORY INTAKE → PROMOTION + HEPHAISTOS CONTEXT-AWARE SELECTION (evidence)",
                 "(hermetic regen — no repo/store/git; resolver is rule-first deterministic)"]

    # 1) a discovery candidate promotes through the gates ----------------------
    out.append(_section("1) intake candidate → Armory promotion (a CI security tool, kind=tool)"))
    cand = C.ArmoryCandidate(
        id="trivy-scan", name="Trivy (image/IaC scan)", kind=KIND_TOOL, category="security",
        summary="컨테이너 이미지·IaC 취약점 스캔", domains=("security", "devops"),
        topics=("scan", "vulnerability", "container", "iac"), signals=("trivy", "이미지 스캔"),
        when_to_use=("CI 에서 이미지/IaC 취약점 게이트",), when_not_to_use=("런타임 침투 테스트",),
        required_inputs=("스캔 대상(이미지/디렉터리)",), expected_outputs=("취약점 리포트 + 심각도 게이트"),
        unsafe_boundary=("스캔 결과 무시하고 prod push 금지",), capability_note="vulnerability scanner",
        install_requirements=("brew install trivy",), attach_requirements=("CI step 추가",),
        provider_affinity=("github",), commands=("trivy image <ref>",),
        verification=("trivy --version",), related_loadouts=("devops-cloud-local",),
        related_roles=("security-engineer", "devops-engineer"),
        source="discovery", source_ref="brief-trivy-001")
    res = C.promote_candidate(cand)
    out.append(f"candidate: {cand.id} (kind={cand.kind}, source={cand.source}:{cand.source_ref})")
    out.append(f"accepted : {res.accepted}")
    for e in res.evidence:
        out.append(f"  gate ✓ {e}")
    if res.accepted:
        catalog.register_promoted(res.spec)
        out.append(f"registered → catalog now has {len(catalog.all_skills())} skills "
                   f"(overlay: {[s.id for s in catalog.promoted_skills()]})")

    # 2) a stub candidate is rejected (no half-promotion) ----------------------
    out.append(_section("2) incomplete candidate → rejected (no placeholder enters the catalog)"))
    stub = C.ArmoryCandidate(id="halfbaked", name="Halfbaked", kind=KIND_TOOL,
                             category="devops", summary="TBD", signals=("halfbaked",))
    rej = C.promote_candidate(stub)
    out.append(f"candidate: {stub.id}  accepted: {rej.accepted}")
    for r in rej.reasons:
        out.append(f"  reject ✗ {r}")

    # 3) Terraform→ECS scenario with project context --------------------------
    out.append(_section("3) Hephaistos selection — Terraform→ECS with project facts + constraints"))
    request = "Terraform으로 ECS 배포 환경 구성해야 돼. Claude Code한테 맡길 프롬프트 만들어줘."
    facts = ("EKS는 제외", "dev 환경부터", "기존 구조 보존")
    plan = resolve(request, project_facts=facts,
                   runtime_constraints=("production apply 금지",), harness="claude-code")
    read = read_plan_sources(plan, env={})  # not_connected (hermetic) — honest source state
    out.append(f"request  : {request}")
    out.append(f"facts    : {', '.join(facts)}")
    out.extend(resolve_summary_lines(plan, read))
    out.append("")
    out.append("work packet (execution-ready draft):")
    pk = plan.packet_draft
    out.append(f"  goal       : {pk.goal}")
    out.append(f"  tools      : {', '.join(pk.selected_tools)}")
    out.append(f"  constraints: {', '.join(pk.constraints)}")
    out.append(f"  harness    : {pk.harness}")
    out.append(f"  approval   : {pk.approval_level}")
    out.append(f"  forbidden  :")
    for f in pk.forbidden_scope:
        out.append(f"    - {f}")

    catalog.clear_overlay()
    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(_ROOT)} ({len(out)} lines)")


if __name__ == "__main__":
    main()
