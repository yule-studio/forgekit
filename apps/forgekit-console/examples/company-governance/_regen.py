"""Regenerate `adoption-merge-lane.json` evidence from the live company-governance lane.

Run from the repo root:
``python3 apps/forgekit-console/examples/company-governance/_regen.py``.
Real artifact traces (no hand-authoring) for adopt-now / collect-first / hold + a merge
receipt + the replayed governance decision trail — so the example can't drift from code.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[4]
for _rel in (
    "packages/forgekit-runtime/src", "packages/forgekit-config/src",
    "packages/forgekit-provider/src", "packages/forgekit-contracts/src",
    "packages/forgekit-goal/src", "packages/hephaistos/src",
    "packages/armory/src", "packages/nexus/src", "apps/forgekit-console/src",
):
    sys.path.insert(0, str(_ROOT / _rel))

import forgekit_runtime.decision_lane as DL
from forgekit_runtime.decision_lane import AdoptionReview, MergeReceipt, RejectedOption


def _adopt_now():
    return AdoptionReview(
        candidate_id="ruff", candidate_kind="tool",
        current_pain="flake8 기반 lint 가 대형 repo 에서 느려 CI 병목",
        expected_benefit="rust 기반 ruff 로 lint 10x+ 가속, autofix 통합",
        overlap_with_existing="flake8 + isort 대체 (중복 제거)",
        operational_cost="낮음 — toolchain 핀 1줄, 기존 설정 마이그레이션",
        maintenance_risk="중간 — rule 호환성 추적 필요, 활발한 upstream",
        provider_runtime_fit="python toolchain(mise)와 적합, provider 무관",
        governance_security_impact="로컬 전용 실행, secret 미접근, 네트워크 없음",
        why_adopt_now="즉시 체감되는 CI 가속 + 낮은 비용 + 안전 — adopt-now",
        proposed_by="backend-engineer", reviewed_by_pm="product-manager",
        reviewed_by_tech_lead="tech-lead",
        specialist_consulted=("backend-engineer", "devops-engineer"),
        ponytail_verdict="wrapper 불필요 — toolchain 핀으로 직접 호출, 추상화 레이어 없음",
        adoption_verdict="adopt-now", follow_up_owner="devops-engineer",
        verification=("ruff --version", "pre-commit run --all-files"),
        rejected_alternatives=(RejectedOption(name="flake8 유지", why_not="속도 병목 미해결"),))


def _collect_first():
    return AdoptionReview(
        candidate_id="external-mcp-server", candidate_kind="plugin",
        current_pain="일부 워크플로에서 외부 데이터 조회 수작업",
        expected_benefit="MCP 로 외부 소스 자동 조회 가능성",
        overlap_with_existing="기존 nexus collector 와 부분 중복 가능",
        operational_cost="높음 — 인증/네트워크/레이트리밋 운영",
        maintenance_risk="높음 — 외부 서버 의존, 버전 드리프트",
        provider_runtime_fit="미검증 — 실측 필요",
        governance_security_impact="외부 네트워크 + 자격 — 공급망/보안 검토 필요",
        why_adopt_now="기대값은 있으나 비용/리스크 미검증 — 지금 도입 위험",
        proposed_by="backend-engineer", reviewed_by_pm="product-manager",
        reviewed_by_tech_lead="tech-lead", specialist_consulted=("security-engineer",),
        ponytail_verdict="과한 통합 추상화 우려 — 먼저 근거 수집",
        adoption_verdict="collect-first", nexus_evidence_ref="nexus://ideas/external-mcp-server")


def _hold():
    return AdoptionReview(
        candidate_id="heavy-workflow-engine", candidate_kind="workflow",
        current_pain="복잡한 다단계 작업 수동 조율",
        expected_benefit="선언적 워크플로 엔진",
        overlap_with_existing="기존 /goal + decision-lane 과 강하게 중복",
        operational_cost="매우 높음 — 별도 런타임/상태 저장소",
        maintenance_risk="매우 높음 — 대형 의존성",
        provider_runtime_fit="기존 control-plane 과 충돌",
        governance_security_impact="새 실행 표면 — 승인 게이트 재구현 위험",
        why_adopt_now="기존 capability 와 중복 + 비용 과다 — hold",
        proposed_by="backend-engineer", reviewed_by_pm="product-manager",
        reviewed_by_tech_lead="tech-lead", specialist_consulted=("devops-engineer",),
        ponytail_verdict="명백한 over-engineering — 기존 레인으로 충분",
        adoption_verdict="hold")


def _merge():
    return MergeReceipt(
        pr_ref="PR-COMPANY-GOV", issue_ref="ISSUE-COMPANY-GOV", branch="feat/forgekit-company-governance-upgrade",
        base="main", merge_commit="<merge-sha>", executor="tech-lead",
        decision_ref="decision:company-gov", approval_metadata="decision=company-gov;level=L2_internal_approve;signoff=tech-lead",
        commit_trailers=("Forgekit-Agent: tech-lead", "Forgekit-Role: TechLead",
                         "Forgekit-Approval: decision=company-gov;level=L2_internal_approve"),
        ci_status="passing", qa_status="passing", outcome="merged")


def main() -> None:
    reviews = [_adopt_now(), _collect_first(), _hold()]
    merge = _merge()
    candidates = []
    for r in reviews:
        candidates.append({
            "review": r.to_dict(),
            "valid": not DL.validate_adoption_review(r),
            "can_equip": DL.can_equip(r),
            "equip_block_reason": DL.equip_block_reason(r),
        })
    out = {
        "lane": "company-governance",
        "doc": "docs/pm-techlead-lane.md (7.6/7.7)",
        "adoption_verdicts": list(DL.ADOPTION_VERDICTS),
        "candidates": candidates,
        "merge_receipt": {"receipt": merge.to_dict(),
                          "valid": not DL.validate_merge_receipt(merge)},
    }
    path = Path(__file__).resolve().parent / "adoption-merge-lane.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", path)


if __name__ == "__main__":
    main()
