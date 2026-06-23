"""Regenerate ``cockpit-qa.txt`` — operator cockpit lane evidence.

Deterministic + hermetic: exercises the two NEW behaviors this lane adds —
(1) multi-command submit split (closes "하나만 인식") and (2) the ForgeKit 도입 효율
검토(adoption-efficiency) forcing rule — and renders them as a transcript. Mirrors
``test_multi_command.py`` + ``test_adoption_review.py``.

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
    VERDICT_ADOPT_NOW, VERDICT_COLLECT_FIRST, VERDICT_HOLD,
    ToolAdoptionReview, adoption_review_report, can_equip,
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
    why_now="pain 명확 + 기존 normalizer 재사용 → adopt-now",
)


def _r(cid, kind, verdict, reviewers, adopted, equipped=False):
    return ToolAdoptionReview(cid, kind, verdict=verdict, reviewers=reviewers,
                              adopted=adopted, equipped=equipped, **_FIELDS)


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

    # 2. adoption-efficiency review forcing rule
    out.append("[도입 효율 검토] 외부 후보는 8점 검토 + PM/tech-lead/specialist 3축 → adopt-now/collect-first/hold")
    reviews = [
        _r("rss-collector", "collector", VERDICT_ADOPT_NOW,
           ("product-manager", "tech-lead", "backend-engineer"), adopted=True, equipped=True),
        _r("ponytail-cli", "tool", VERDICT_COLLECT_FIRST,
           ("product-manager", "tech-lead", "devops-engineer"), adopted=False),
        _r("big-framework", "plugin", VERDICT_HOLD,
           ("product-manager", "tech-lead", "frontend-engineer"), adopted=False),
    ]
    rep = adoption_review_report(reviews)
    for r in reviews:
        out.append(f"  · {r.candidate_id} [{r.candidate_kind}] → {r.verdict} "
                   f"(adopted={r.adopted} equipped={r.equipped} can_equip={can_equip(r)})")
    out.append(f"  roll-up: adopt-now={len(rep.adopt_now)} collect-first={len(rep.collect_first)} "
               f"hold={len(rep.hold)} · equipped={len(rep.equipped)} · "
               f"fake_adoption_blocked={rep.fake_adoption_blocked}")
    out.append("")
    out.append("honesty rails: 멀티커맨드는 모든 줄이 /로 시작할 때만 분리(free text 무변경) · "
               "adopted(결정)≠equipped(실장착) · collect-first/hold 장착 금지 · "
               "adopt-now 만 3축 통과 시 equip · fake adoption 차단.")

    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
