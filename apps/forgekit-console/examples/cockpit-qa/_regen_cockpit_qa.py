"""Regenerate ``cockpit-qa.txt`` — operator cockpit lane evidence.

Deterministic + hermetic: exercises this lane's new behavior (1) multi-command submit
split (closes "하나만 인식") and renders (2) the ForgeKit 도입 효율 검토(adoption-efficiency)
forcing rule on the existing `decision_lane.adoption` SSoT. Mirrors
``test_multi_command.py`` + ``test_company_governance_upgrade.py``.

Run: ``python apps/forgekit-console/examples/cockpit-qa/_regen_cockpit_qa.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
for _rel in ("packages/forgekit-runtime/src", "packages/forgekit-config/src",
             "apps/forgekit-console/src"):
    _p = str(_ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from forgekit_console.commands.parser import split_command_lines
from forgekit_runtime.decision_lane import (
    ADOPT_NOW, COLLECT_FIRST, HOLD,
    AdoptionReview, can_equip, equip_block_reason, validate_adoption_review,
)

OUT = Path(__file__).resolve().parent / "cockpit-qa.txt"

_FIELDS = dict(
    current_pain="discovery 수집 토픽을 매번 수동 튜닝해 1차 분류가 느리다",
    expected_benefit="RSS 자동 큐레이션으로 1차 분류 시간을 절반으로 줄인다",
    overlap_with_existing="기존 discovery sweep 와 부분 중복 — normalizer 재사용 가능",
    operational_cost="runtime tick 1개 추가 + state_dir 용량 소폭 증가",
    maintenance_risk="외부 RSS 스키마 변경 시 파서가 깨질 수 있다",
    provider_runtime_fit="provider 무관, runtime tick 에 bounded 로 배선 가능",
    governance_security_impact="outbound fetch only, secret 없음, redaction 불필요",
    why_adopt_now="pain 명확 + 기존 normalizer 재사용 → adopt-now",
)


def _r(cid, kind, verdict, **over):
    base = dict(candidate_id=cid, candidate_kind=kind, adoption_verdict=verdict,
                proposed_by="user-researcher", reviewed_by_pm="product-manager",
                reviewed_by_tech_lead="tech-lead", specialist_consulted=("backend-engineer",),
                ponytail_verdict="기존 normalizer 재사용 — 새 wrapper 불필요", **_FIELDS)
    base.update(over)
    return AdoptionReview(**base)


def main() -> None:
    out = ["forgekit operator cockpit QA — multi-command submit + 도입 효율 검토",
           "=" * 74, ""]

    # 1. multi-command submit split (close 하나만 인식)
    out.append("[multi-command submit] '하나만 인식' 차단 — 한 submit 의 여러 /명령을 순차 실행")
    cases = [
        ("/goal show 3\n/goal awaiting", "stack of commands"),
        ("/whoami", "single command"),
        ("first line\nplease explain", "free text (multiline)"),
        ("/goal list\nalso explain this", "1 slash + prose"),
    ]
    for raw, label in cases:
        parts = split_command_lines(raw)
        shown = " | ".join(p.replace("\n", "⏎") for p in parts)
        out.append(f"  [{label}] → {len(parts)} 실행: {shown}")
    out.append("")

    # 2. adoption-efficiency review forcing rule (decision_lane.adoption — main SSoT)
    out.append("[도입 효율 검토] 외부 후보는 8점 검토 + proposer/PM/tech-lead/specialist 3축 → adopt-now/collect-first/hold")
    reviews = [
        _r("rss-collector", "collector", ADOPT_NOW,
           follow_up_owner="backend-engineer", verification=("RSS 파싱 회귀 + bounded tick 측정",)),
        _r("ponytail-cli", "tool", COLLECT_FIRST,
           nexus_evidence_ref="00-inbox/discovery/ponytail-cli.md"),
        _r("big-framework", "plugin", HOLD),
    ]
    for r in reviews:
        valid = not validate_adoption_review(r)
        block = equip_block_reason(r) or "(장착 가능)"
        out.append(f"  · {r.candidate_id} [{r.candidate_kind}] → {r.adoption_verdict} "
                   f"(valid={valid} can_equip={can_equip(r)} | {block})")
    out.append("")
    out.append("honesty rails: 멀티커맨드는 모든 줄이 /로 시작할 때만 분리(free text 무변경) · "
               "adopted(결정)≠equipped(실장착, decision_lane.adoption.can_equip) · "
               "collect-first=Nexus 근거만/hold=보류 → 장착 금지 · adopt-now 만 검증 후 equip · fake adoption 차단.")

    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
