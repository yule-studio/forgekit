# ForgeKit Upgrade Wave 2 — integration QA & merge closure (SSoT)

> 본 doc 은 **Wave 2(control-plane 업그레이드, umbrella #427 / #441)** 의 integration/QA/merge
> closure lane 결과의 SSoT 다. gw1~gw6 산출물을 통합 QA 하고 **실제 merge 까지** 닫은 기록 +
> lane 별 acceptance + 시행착오 + 남은 gap 을 남긴다. fake-green 금지: 모든 ✅ 는 merged commit +
> CI + full-suite green 근거가 있다.

## 1. 최종 상태 (2026-06-24 기준)

- **main 반영:** Wave 2 ready PR **전부 main 머지** — tip `559941d`.
- **full suite:** `python3 -m unittest discover -s tests -t .` → **7291 OK (skipped=5)** (main tip 실측).
- **open PR:** **2개 — 둘 다 DRAFT**(#426 external intake / #421 install-safety, 타 pane 진행중). ready/non-draft open = **0**.
- **남은 blocker:** **0**(머지 차단 없음). DRAFT 2건은 blocker 아님 — 소유 pane 작업중.

## 2. lane 별 acceptance (merged 근거)

| lane | acceptance | 상태 | merge 근거 (PR → commit, issue) |
| --- | --- | --- | --- |
| goal-autopilot packet/evidence | intent goal 이 packets:0 에서 멈추지 않고 tick 에서 첫 packet seed | ✅ | #443 → `2256f96`, issue #442 |
| runtime advances active goal | continuity per-tick lane + budget + goal governance tick | ✅ | #447 → `a83026b`(owner self-merge, #446) · #451 → `9595828`(#450) |
| Nexus/evidence 누적 | discovery ledger + goal↔nexus evidence | ✅ | #445(goal-nexus-evidence, 이전) + discovery ledger(merged) |
| governance artifact 강제 | PM→gateway→tech-lead→specialist 를 goal 루프에 강제 | ✅ | #451 → `9595828`, issue #450 (`runtime/goal_governance.py`) |
| 도입 효율 검토(adoption) 강제 | 외부 후보 8축+3축 → adopt/collect/hold, adopted≠equipped | ✅ | #440 → `efdb1b0`(#432) + `decision_lane.adoption`(merged) |
| provider/runtime readiness | /daemon·/setup 에 daemon×goal×live transport join | ✅ | #439 → `eb3492a`, issue #428 |
| console parity 개선 | multi-command + live motion + selection 가시성 + readability | ✅ | #434 → `b55ba66`(#431) · #449 → `559941d`(progress/selection) |
| consult merge gate | design/review 변경 consult 없이 머지 차단 | ✅ | #422 → `be131c2`(#419, `decision_lane.consult_gate`) |

> 모든 lane 산출물이 **단일 main 트리에서 함께 green**(full suite 7291) — cross-lane regression 없음.

## 3. cross-lane regression QA (이 closure lane 이 실제로 한 일)

각 ready PR 을 **머지 전에 현재 main 위로 로컬 머지 + full suite** 로 검증(병합 후가 아니라 전에):

| 검증 배치 | 결과 |
| --- | --- |
| #440 + #439 (둘 합쳐) | 7238 OK, 충돌 0 |
| #443 (main 위) | 7255 OK, 충돌 0 |
| #451 + #449 (둘 합쳐) | 7291 OK, 충돌 0 |
| 최종 main tip `559941d` | **7291 OK** |

머지 순서: 의존/충돌 기준 disjoint 확인 후 #440→#439→#443→(#447 owner)→#451→#449. 모두 operator
per-PR 인가([[feedback_no_auto_merge]]) 후 merge-commit 으로.

## 4. 시행착오 (lessons — 숨기지 않음)

1. **CI merge-ref 충돌로 중복 발견:** cockpit lane(#434)에서 도입 효율 검토를 신규 모듈로 추가했으나,
   병렬 pane 이 동등한 `decision_lane/adoption.py` 를 main 에 **먼저 머지**. 내 branch base 엔 없어
   **local 통과/CI(merge-with-main) FAIL**. → 중복 제거 + 기존 SSoT 채택. **교훈: 신규 schema/governance
   추가 전 `origin/main` + 병렬 PR(`gh pr list`) 확인. local-only 통과는 merge-ref 충돌을 못 잡는다.**
2. **rolling closure:** closure lane 진행 중 panes 가 ready PR 을 계속 추가(#443→#451/#449). 통합
   QA→merge 루프를 **ready 가 소진될 때까지 반복**. 일부는 owner pane 이 self-merge(#447, conflict
   해소 후) — pane-self-merge 모델이 작동.
3. **conflict 은 owner 가 해소:** #447 이 #439 머지 후 `surface.py` 충돌(DIRTY)로 일시 blocked →
   closure lane 이 강제 머지하지 않고 owner pane 이 rebase+merge. **타 pane working tree 불가침.**
4. **--delete-branch 로컬 단계 실패:** sibling worktree 가 점유한 브랜치는 local 삭제 불가(remote
   merge 는 성공). 강제 삭제하지 않음(타 pane 교란 방지) — GitHub auto-delete/owner 에 위임.
5. **merge 는 operator per-PR 인가:** no-auto-merge classifier 가 매 머지를 차단 → AskUserQuestion
   으로 명시 인가받아 진행. "wave 마무리까지" 지시여도 per-PR 게이트는 유지.

## 5. 남은 gap / 후속 (정직)

- **DRAFT #426**(external intake lane — free-first 수집 + Armory 승격 전 curation gate, issue #425):
  owner pane 진행중. ready 되면 동일 통합 QA→merge.
- **DRAFT #421**(runtime install/activation safety lane — 외부 tool/skill/plugin 승인 게이트, issue
  #420): owner pane 진행중.
- **umbrella issue #427/#441:** 위 2 draft 가 닫히면 umbrella close 가능.
- **structural(이전 lane 기록):** terminal-native PrintFlowSink above-region emit 미연결, image
  staging 터미널 의존 — console parity 후속.

## 6. 완료 정의 대조 (no fake green)

| 완료 정의 | 충족 | 근거 |
| --- | --- | --- |
| goal-autopilot 이 실제 packet/evidence 생성 | ✅ | #443 merged + `test_goal_autopilot_intent` |
| runtime 이 active goal 전진 | ✅ | #447/#451 merged + continuity/governance tick |
| Nexus/evidence 축 누적 | ✅ | discovery ledger + goal-nexus evidence merged |
| governance artifact 강제 | ✅ | #451 merged + `test_goal_governance_enforcement` |
| console parity 실질 개선 | ✅ | #434/#449 merged + multi-command/progress-selection tests |
| 전부 PR/QA/merge 닫힘 | ✅(ready 전부) | open non-draft PR 0, full suite 7291 OK |

> **main 반영: 전부 머지(tip `559941d`). open PR: 2(둘 다 draft). 남은 blocker: 0.**
