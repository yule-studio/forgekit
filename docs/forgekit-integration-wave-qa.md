# ForgeKit 통합 웨이브 QA / merge gate (integration lane SSoT)

> 본 doc 은 이번 웨이브의 **integration / QA / merge lane** 결과의 SSoT 다. 각 lane
> (gw1~gw5) 이 따로 놀지 않도록 **전체 경로를 통합 검증**하고, **consult merge gate** 를
> 머지 기준에 포함하며, 실제 issue/PR/QA/merge 가 마감됐음을 기록한다.
>
> 코드 SSoT: `tests/forgekit/test_integration_wave_e2e.py` (cross-lane E2E) +
> `tests/forgekit/test_consult_gate.py` (consult merge gate) +
> `packages/forgekit-runtime/src/forgekit_runtime/decision_lane/consult_gate.py`.
> 증거: `apps/forgekit-console/examples/integration-wave/e2e.txt`.

## 1. 통합 대상 (이번 웨이브 lane)

| lane | 책임 | 주요 표면 / 코드 |
| --- | --- | --- |
| gw1 intake/curation | 도구·아이디어 후보 수집 → ledger (new→seen→promoted), dedup·영속 | `forgekit_console.discovery` (sweep/ledger) |
| gw2 Armory/Hephaistos | 요청 → 큐레이트 카탈로그 loadout/skills/weapons + forge governance | `armory.catalog`, `hephaistos.resolve`, `forgekit_runtime.forge` |
| gw3 Nexus/discovery | plan 지식 attachment (미연결 정직), brief → authored note | `hephaistos.nexus_read`, `discovery.persist_brief` |
| gw4 provider projection | primary/linked/slot routing 영속·reload, declared→actual, 3-state live | `forgekit_console.policy.provider_surface` |
| gw5 runtime/governance | 승인 체인 → execution receipt + ledger, install safety | `forgekit_runtime.forge`, `decision_lane`, `deploy/launchd` |

## 2. Cross-lane regression (반드시 할 일 1)

- **전체 회귀:** `python3 -m unittest discover -s tests -t .` → **7044 + 신규 → green**
  (신규 29: E2E 13 + consult-gate 16). baseline OK (skipped=5).
- **신규 통합 회귀:** `test_integration_wave_e2e` 13 케이스 — 각 lane 단독이 아니라
  **한 흐름**으로 intake→Armory→Hephaistos→Nexus→provider→receipt 를 대표 시나리오에서 검증.

## 3. Docs drift check (반드시 할 일 2)

- `operator-surfaces.md` reality matrix 의 wave 관련 행 (Hephaistos resolve / Nexus read /
  discovery ledger / provider route / forge ledger / goal) 의 `evidence` 참조 파일 **전부 실존**
  (spot-check: `examples/hephaistos`, `examples/nexus-live-read`,
  `examples/discovery/{sweep-digest,ledger-accumulation}.json`, `examples/forge-ledger`,
  `examples/goal/approval.txt`, `examples/provider-connect`, `examples/toolchain` — 모두 OK).
- 본 웨이브 통합은 기존 `examples/integration/scenarios.txt`(3-axis: provider+nexus+usage+daemon)
  를 **cross-lane 전 경로 + governance receipt + consult gate** 로 확장 →
  `examples/integration-wave/e2e.txt` 신규. operator-surfaces 에 통합 행 추가.
- **drift 없음** — reality matrix 가 코드/테스트보다 앞서가는(fake-green) 행 미발견.

## 4. Examples / evidence 존재 (반드시 할 일 3)

- 신규: `apps/forgekit-console/examples/integration-wave/e2e.txt` (+ `_regen_e2e.py`,
  deterministic·hermetic·network-free). 대표 3 시나리오 + provider 영속 + receipt ledger +
  consult gate 를 한 transcript 로.
- 기존 lane evidence (위 spot-check) 유지.

## 5. 대표 시나리오 통합 검증 (반드시 할 일 4)

각 시나리오가 **전 경로**를 통과하며 honest seam 이 유지됨 (`e2e.txt` 참조):

| 시나리오 | Armory loadout | Nexus | receipt | 정직 결과 |
| --- | --- | --- | --- | --- |
| Spring Boot JWT (safe eng) | backend-java-local | not_connected(정직) | **authorized/executed** | trailer+approval metadata, valid |
| Next.js 디자인 시스템 | design-review-local | not_connected | **blocked** | ux-ui-designer 는 exec slot 없음 (정직) |
| Terraform+ECS+GHA 배포 | devops-cloud-local | not_connected | **blocked** | deploy=destructive/L4 차단 (정직) |
| discovery signal → packet | — | brief→authored note(frontmatter) | — | ledger new→promoted, dedup·영속 |
| ponytail-like OSS CLI 후보 | (intake only) | — | — | Armory 카탈로그 런타임 무변경 (curated) |

> 단계 매핑: 도구 후보 수집(gw1 ledger) → Armory 승격(resolver↔curated catalog) →
> Hephaistos resolve/loadout → Nexus knowledge attachment → provider projection(영속) →
> runtime approval/execution receipt(+ledger). 전 단계 `test_integration_wave_e2e` 로 회귀.

## 6. Consult merge gate (추가 QA / merge 기준)

> 목적: 필요한 설계/리뷰인데 **consult 가 빠진 채 머지**되는 것을 차단.
> 코드: `decision_lane.consult_gate` (`ChangeUnderReview`/`adjudicate_consult`/`consult_gate_report`).
> consult 내용 유효성은 기존 `validate_consult` 에 위임 — fake "consult 했음" 은 satisfy 못 함.

**판정 규칙 (재판정 — 반드시 할 일 1·2):**

- `change_kinds` 에 design/architecture/stack/api-contract/schema/data-model/security/ux/…
  중 하나라도 있으면 **consult required**.
- required 이면 아래 중 하나가 있어야 통과:
  - **consult verdict** (`ConsultNote`, 유효), 또는
  - **design/decision log 근거** (`design_refs`), 또는
  - **waive reason** (명시·기록된 사유).
- required 인데 셋 다 없으면 **missing = blocker (merge 금지)**.
- pure verification/QA/docs/test → **not required = 통과 가능**.

**머지 규칙 (SSoT):**

| 조건 | 결과 |
| --- | --- |
| consult required + artifact missing | **merge 금지** |
| consult required + artifact 있음 (verdict/design-log/waive) | 통과 가능 |
| consult not required | 통과 가능 |

## 7. 이번 웨이브 변경에 대한 consult 재판정 (QA 보고 split — 반드시 할 일 4)

이번 웨이브에서 실제로 머지되는 변경은 **이 integration/QA lane** 하나다(다른 lane 은 이미
main 머지 완료 — section 9). 각 변경의 consult 판정:

| 변경 (lane/PR) | change kinds | consult required? | artifact | 판정 |
| --- | --- | --- | --- | --- |
| integration-qa-lane (본 PR: E2E 테스트 + consult-gate 코드 + 문서/증거) | integration, test, docs | **아니오** | — | **satisfied (not required)** |

- **consult satisfied:** (이번 웨이브에 design/review-bearing 머지 변경 없음 → 해당 0)
- **consult waived:** 0
- **consult missing (blocker):** **0** → **merge 차단 없음**

> 본 lane 은 순수 검증/문서/증거 변경(설계 결정·공개 인터페이스 변경 아님)이므로 consult
> not-required. `consult_gate` 자체는 **신규 코드**지만 기존 `ConsultNote`/`validate_consult`
> 모델 위 additive 표면(공개 계약 변경 없음)이라 design consult 대상 아님 — 판정을 여기 기록해
> 숨기지 않는다.

## 8. Merge-prep 체크리스트 (반드시 할 일 6, + ponytail consult 항목)

- [x] cross-lane 전체 회귀 green (`unittest discover`, 7044+신규)
- [x] 신규 통합 회귀 추가 (`test_integration_wave_e2e`, 13)
- [x] consult merge gate 코드+회귀 (`consult_gate.py` + `test_consult_gate`, 16)
- [x] docs reality matrix drift 점검 — drift 없음, evidence 실존
- [x] examples/evidence 신규 (`integration-wave/e2e.txt` + regen)
- [x] operator-surfaces 통합 행 + 본 QA doc SSoT
- [x] **ponytail consult required?** — 각 머지 변경 재판정 (section 7)
- [x] **consult artifact present?** — required 변경 0 → N/A (missing 0)
- [x] **waived? if yes, reason recorded?** — waive 0
- [x] commit governance 통과 (gitmoji+3섹션, Co-Authored-By 금지, identity binding)
- [x] PR 5 섹션 + audit block
- [ ] CI green (PR 후 확인)
- [ ] operator-authorized merge (no-auto-merge 정책 — operator 명시 지시 필요)
- [ ] branch cleanup

## 9. main 상태 (완료 정의)

- 이번 웨이브 이전 lane 은 **이미 전부 main 머지 완료** (open PR 0, open issue 0 — 본 lane 착수 시점):
  6-pane wave (#379/#383/#385/#384/#387/#389) + 후속 (#391 routing fake-live 차단 /
  #395 nexus discovery ledger / #397 specialist briefing / #399 goal autonomy /
  #403 process-feed / #405 transcript marker). main tip = `ff9afd7` 위.
- 본 lane = 그 위에 **통합 검증 + consult merge gate** 를 얹어 웨이브를 **end-to-end 로 닫는다**.
- merge 후 main 상태: 통합 회귀 + consult gate 가 회귀 라인에 상주 → 다음 웨이브가
  "lane 따로 놀기" / "consult 누락 머지" 를 재발 못 하도록 가드.

## 10. 정직 경계 / blocker (숨기지 않음)

- **Armory 런타임 승격 없음:** Armory 카탈로그는 Python-seeded **curated read-only**.
  "도구 후보 → Armory candidate" 는 discovery ledger 로 **intake/표면화**까지이고, 카탈로그
  추가는 큐레이션(코드 변경)이다 — 런타임 자동 추가 경로는 의도적으로 없음(JSON manifest loader
  는 후속 seam). 본 lane 은 이를 fake 하지 않고 그대로 검증.
- **Nexus 미연결:** 기본 not_connected 를 날조하지 않음 (`read_plan_sources.not_connected`).
- **non-engineering / deploy 차단:** ux-ui-designer 는 exec slot 없음, deploy=destructive/L4 →
  authorized 0. safe engineering 만 receipt.
- **consult gate 적용 범위:** 본 gate 는 **머지되는 변경의 design/review 표면**을 판정한다.
  현재 wave 의 머지 변경(본 lane)은 not-required 이며, 이는 누락이 아니라 정직한 판정이다.
- 현재 **미해결 blocker 없음.** (CI/merge 는 operator 인가 대기 — 정책상 자동 머지 금지.)
