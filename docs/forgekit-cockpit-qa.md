# ForgeKit operator cockpit QA (console parity lane SSoT)

> 본 doc 은 **operator cockpit upgrade** lane 의 QA SSoT 다 — Claude Code 근접 cockpit 을
> **fake parity 없이** 구조적으로 점검하고, 이번 wave 의 **도입 효율 검토(adoption-efficiency)
> forcing rule** 을 기록한다. fake typing / CSS-only parity / 문구만 보고 금지.
>
> 코드: `commands/parser.py`(split_command_lines) + `tui/app.py`(submit loop). 도입 효율
> 검토는 기존 `decision_lane/adoption.py`(`AdoptionReview`) 채택(§3). 회귀: `test_multi_command`(11)
> + `test_company_governance_upgrade`(adoption). 증거: `examples/cockpit-qa/cockpit-qa.txt`.

## 1. cockpit parity 현황 — what is real / partial / structural (정직)

> 출처: 코드 실측 + 기존 parity lane(merged PR #337/#361/#373) + 본 lane.
> "now real" = 이번/이전 라운드에서 코드+테스트로 닫힘. "partial" = 동작하나 범위 한정.
> "structural" = 아직 구조적으로 Claude Code 와 다른 부분(정직 표기, fake 안 함).

| 영역(우선순위) | 상태 | 근거 |
| --- | --- | --- |
| 1. transcript / copy (`/copy last\|all\|turn\|block`) | **now real** | plain-text block store, markup strip, turn 재구성. `transcript_store.py`, `test_tui_scroll_copy` |
| 1. drag-selection contrast (transcript+prompt) | **now real** | cross-widget `screen--selection`=accent-dim+밝은 FG, 런타임 4.75:1 WCAG AA. `test_tui_transcript_selection`·`test_tui_selection_contrast` (PR #373) |
| 1. paste (large paste staging) | **now real** | `/paste`, paste id 분리 저장. `test_tui_paste` |
| 2. slash palette 위치/scroll | **now real** | 입력바 **바로 아래**(composer child), SessionFlow 단일 scroll owner, ≤8 rows. `test_tui_palette_below` |
| 3. multiline (Ctrl+J) / long paste | **now real** | 멀티라인 버퍼 1 submit, palette 는 첫 줄만. `test_tui_scroll_copy` |
| 3. image staging | **partial** | `H_ATTACH` surface 존재(이미지 staging), 터미널 의존 — 불가 시 정직 표기 |
| 4. process feed (Reading/Routing/Submit/Done) | **now real** | **실 event timeline**(monotonic 측정), fake label 0. route_start→route_done / submit_start→submit_sent→generate_start→done. `test_tui_process_feed` |
| 5. /goal UX (show/evidence/awaiting/approve/deny) | **now real** | surface CRUD + in-console approve/deny→GW4-B. `test_goal_surface`·`test_goal_approval` |
| 5. **multi-command submit (하나만 인식)** | **now real (THIS lane)** | `split_command_lines` + submit loop — 여러 `/명령` 한 번에 순차 실행. `test_multi_command` |
| cockpit status line (mode/awaiting/budget) | **now real** | 매 turn refresh, 실 store/ledger 카운트. `test_tui_cockpit_status` |
| — terminal-native print-flow (above-region emit) | **structural** | sink turn-boundary 는 배선(`_begin_turn`/`_finalize_turn`), PrintFlowSink above-region emit 은 미연결(정직). 별도 후속 |
| — claude/codex live submit (CLI transport) | **structural** | `unsupported_in_console` 정직 — 본 lane 범위 아님(provider lane) |

## 2. named bug 처리(정직 disposition)

operator 가 지목한 3 건을 **코드 실측**으로 재판정:

| 지목 | 실측 결과 | 처리 |
| --- | --- | --- |
| "하나만 인식하는 문제" | **BUG 확인** — `parse_input` 이 멀티라인 버퍼 전체를 1 명령으로 파싱(첫 토큰=name, 나머지 줄=garbage args). 여러 `/명령` 중 하나만 실행됨 | **닫음** — `split_command_lines`(모든 줄이 `/`로 시작할 때만 분리, free text 무변경) + submit loop. `test_multi_command` |
| "drag highlight 구분 문제" | **already real** — transcript+prompt 선택 모두 `screen--selection`=accent-dim+밝은 FG, 4.75:1 대비. PR #373 merged | 추가 변경 없음(fake CSS parity 금지). QA 표에 "now real" 로 기록 |
| "transcript copy 문제" | **already real** — `/copy last\|all\|turn\|block`, plain-text, markup strip. PR 이전 라운드 merged | 추가 변경 없음. "now real" 로 기록 |

> 정직: 3 건 중 **실제 미해결은 multi-command 1 건**이었고 그것만 코드로 닫았다. 나머지 2 건은
> 이미 real 이라 fake 로 덧칠하지 않는다([[feedback_console_parity_minimal_ui]] 준수 — 새 UI
> layer/fake animation 금지, read-only line projection만).

## 3. 도입 효율 검토 forcing rule (wave 공통 강제)

> 외부 plugin/skill/collector/rule/workflow/tool 후보는 "좋아 보인다"만으로 도입 금지.
> 코드 SSoT: `decision_lane.adoption` (`AdoptionReview`/`validate_adoption_review`/
> `can_equip`/`equip_block_reason`). 회귀 `test_company_governance_upgrade`.
> **정직:** 본 lane 작업 중 병렬 pane 이 동등한 검토 게이트를 `adoption.py` 로 main 에 먼저
> 머지해, 내가 만들던 중복 `adoption_review.py` 는 **제거하고 기존 SSoT 를 채택**했다
> (작업 전 origin/main 재확인 교훈, 중복 모델 금지). 본 lane 고유 기여는 multi-command submit.

각 후보 artifact (8점, `AdoptionReview`):
1. current pain · 2. expected benefit · 3. overlap with existing · 4. operational cost ·
5. maintenance risk · 6. provider/runtime fit · 7. governance/security impact ·
8. why adopt-now vs collect-first vs hold.

강제 (`adoption.py`):
- **3축 검토** — proposer + PM(canonical product-manager) + tech-lead + ≥1 specialist(engineering) 가 resolve(registry SSoT)돼야 `adopt-now` 가능. `ponytail_verdict`(최소-구조) 도 필수.
- **adopted ≠ equipped** (Hephaistos 구분) — `can_equip` 게이트(valid + adopt-now)만 장착. adopt-now 는 추가로 `follow_up_owner` + `verification` 필수.
- **collect-first** = Nexus 에 근거만 누적(`nexus_evidence_ref`), **장착 금지**. **hold** = 도입 보류. `equip_block_reason` 이 정직한 차단 사유 표면.
- **no fake adoption** — collect-first/hold 는 `can_equip=False`, 검증 미통과 review 도 장착 불가.
- dependency/abstraction 변경의 채택 근거는 이 review 가 제공 → consult merge gate(`decision_lane.consult_gate`)의 `design_refs` 와 연결([[project_forgekit_integration_qa_wave]]).

## 4. "실제로 ForgeKit 효율이 올라갔는가" (merge 전 별도 검증)

| 항목 | before | after | 효율 변화 |
| --- | --- | --- | --- |
| 멀티 `/명령` 입력 | 하나만 실행, 나머지 garbage args | 한 submit 으로 N 명령 순차 실행 | operator round-trip 감소(실 동작, fake 아님) |
| 외부 도구 채택 결정 | ad-hoc "좋아 보임" | 8점+3축+verdict artifact, fake adoption 차단 | 잘못된 도입/유지비 리스크 사전 차단(governance teeth) |
| adopt-now 도구의 cockpit 표기 | (해당 없음) | `equipped` 만 실제 장착으로 표기 — adopted-but-not-equipped 가 misleading 하게 "설치됨"으로 안 보임 | 정직 표면(no misleading) |

> adopt-now 된 도구가 console/operator surface 에서 misleading 하게 보이지 않음: `equipped`
> 플래그가 False 면 "결정만 됨(미장착)" 으로, 가짜 "설치됨" 표기 0.

## 5. regression / merge readiness

- 전체 회귀: `python3 -m unittest discover -s tests -t .` → **green** (신규: multi-command 11; 도입 효율 검토는 기존 `adoption.py` 회귀 재사용, 중복 추가 안 함).
- 이번 wave 다른 lane 과 충돌: parser/app submit loop 는 additive. **중복 발견·해소** — 도입 효율 검토는 main `adoption.py` 로 먼저 머지돼 내 중복 모듈 제거(§3).
- 정직 경계: terminal-native above-region emit 은 structural(미연결, §1), image staging 은 partial.

## 6. merge-prep 체크리스트

- [x] 멀티커맨드 gap 코드+회귀(`test_multi_command`)
- [x] 도입 효율 검토 forcing rule — 기존 `decision_lane.adoption`(`test_company_governance_upgrade`) 채택(중복 제거)
- [x] cockpit real/partial/structural 매트릭스(§1) + named-bug disposition(§2)
- [x] 효율 향상 별도 검증(§4) + misleading 표기 점검
- [x] 전체 회귀 green(7132), cross-lane 충돌 없음
- [x] evidence(`examples/cockpit-qa/cockpit-qa.txt` + regen)
- [ ] CI green(PR 후)
- [ ] operator-authorized merge(no-auto-merge)
- [ ] branch cleanup

## 7. blocked / 후속(숨기지 않음)

- **structural (미해결, 본 lane 밖):** terminal-native PrintFlowSink above-region emit 미연결
  (owner=console parity 후속, unblock=Textual print-region 배선, 다음 행동=별도 issue).
- **partial:** image staging 은 터미널 의존(불가 환경 정직 실패).
- 현재 **본 lane blocker 없음** — CI/merge 는 operator 인가 대기.
