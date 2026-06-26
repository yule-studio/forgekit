# Staged Agent Organization Roadmap

> 본 문서는 ForgeKit company 모델([`forgekit-company-model.md`](forgekit-company-model.md))
> 을 **무리 없이 단계적으로** 실재화하는 로드맵 SSoT 다. 핵심 원칙:
> **logical-first, physical-deferred** — 데이터/문서/계약을 먼저 고정하고,
> dispatch 코드와 폴더 이전은 그 위에 마지막으로.
>
> 각 stage 는 독립 issue 로 쪼갠다. 본 문서(I-1)는 **Stage 1 의 문서 산출물** 이다.

## 0. 원칙

- **선언 ≠ 동작.** topology/office 를 문서에 쓴 것은 "정의" 일 뿐, dispatch 코드가
  해석하기 전까지 런타임은 모른다.
- **reduce-surface.** 기존 council/lane/goal_governance 위에 얇게 얹는다. 신규
  dispatch 엔진/registry 프레임워크/validator 금지.
- **reality-check first.** "구현했다" 보다 "동작을 evidence 로 증명했다" 가 우선.

## 1. Stage 개요

| Stage | 이름 | 산출물 | 코드 변경 | 상태 |
|---|---|---|---|---|
| 0 | Inventory | 현재 agents/manifest/policy/test 목록 | 없음 | ✅ 완료(design report) |
| 1 | Company model design | 본 4개 문서(company-model/office-map/naming/roadmap) | 없음(문서만) | ⏳ **이 issue (I-1)** |
| 2 | Agent contract model | trigger/input/output/permission/gate 계약 문서 | 없음(문서만) | 예정(I-3) |
| 3 | Team topology alignment | role→team 매핑 데이터 확장(engineering 8/12) | 데이터만 | 부분(PR #454=MVP 4) |
| 4 | Runtime dispatch | office→team→role resolver + work packet + 테스트 | 코드(얇게) | 예정(I-5/I-6) |
| 5 | Physical folder migration | 폴더 이전 **제안서** → 승인 시 이전 | 별도 PR | 보류(dispatch 동작 후) |

## 2. Issue 목록 (I-1 ~ I-8)

| # | issue | stage | office | 선행 | 종류 |
|---|---|---|---|---|---|
| **I-1** | Company model 블루프린트 land(본 4개 문서) | 1 | CEO | — | **docs (현재)** |
| I-2 | 비-engineering 6개 부서 manifest 채우기(type/members) | 3 | 각 office | I-1 | data |
| I-3 | Agent contract model 문서(trigger/input/output/permission/gate) | 2 | CTO | I-1 | docs |
| I-4 | engineering 8/12 topology 데이터 확장 | 3 | CTO | PR #454 | data |
| I-5 | team→role resolver + TeamWorkPacket + 테스트 | 4 | CTO | I-4 | code |
| I-6 | office→team 라우팅(goal_governance 위에 얹기) | 4 | COO/CEO | I-5 | code |
| I-7 | reality-check-team 계약 + goal 9단계 closed-loop evidence 감사 | 2/4 | qa-gov | I-3 | docs+audit |
| I-8 | 폴더 physical migration 제안서 | 5 | CTO | I-5 동작 후 | proposal |

## 3. Stage 1 (I-1) 완료 기준

본 issue 가 land 되면:

- [x] operator → CEO → C-level → Office → Division → Team → Agent 계층 문서화
      ([`forgekit-company-model.md`](forgekit-company-model.md) §1).
- [x] 10개 C-level office mission/owned/out-of-scope/escalation 정의(§2).
- [x] office↔부서 매핑 + 현재 reality 정직 표시
      ([`agent-office-map.md`](agent-office-map.md)).
- [x] office/division/team/role 네이밍 규칙([`naming-convention.md`](naming-convention.md)).
- [x] Engineering 4→8→12 성장 경로는 **링크(중복 금지)** — manifest `team_topology` + PR #454.
- [x] reality-check-team 을 **required future team** 으로 정의(company-model §3, 본 문서 I-7).
- [x] goal closed-loop 을 **target behavior 로 기술**(구현 아님 — company-model §5).
- [x] PR #454 는 **logical-only** 로 유지, 확장하지 않음.

## 4. reality-check-team (required future team)

Stage 2/4(I-7)에서 계약을 확정할 **필수 미래 팀**. company-model §5 의 goal
closed-loop 을 기준선으로 쓴다.

- **소속**: 출발은 qa-governance-team, Target(12팀)에서 독립 가능.
- **mission**: surface-level 구현 차단, user intent vs 실제 runtime behavior 대조.
- **hard rule**: "stored-not-executed" 거부, evidence 없으면 done 불가.
- **output**: `RealityVerdict{intent, observed_behavior, gap[], evidence_required[], verdict: real/surface/stored-not-executed}`.
- **첫 과제**: goal 9단계 중 실제로 어디까지 closed-loop 인지 evidence 로 증명
  (코드 `goal_governance.py`/`goal_scheduler_tick.py`/`goal_exec_tick.py` 존재 ≠ 동작).

## 5. 이 단계에서 하지 않는 것 (명시)

- ❌ 런타임 dispatch 구현(Stage 4).
- ❌ 폴더 이전(Stage 5, 별도 제안서).
- ❌ CODEOWNERS / validator / ownership engine / registry 프레임워크.
- ❌ goal runtime 수정.
- ❌ PR #454 확장.

## 6. 동기화

- stage/issue 가 바뀌면 본 문서 + [`forgekit-company-model.md`](forgekit-company-model.md) §4.
- 새 SSoT 등록은 [`AGENTS.md`](../../AGENTS.md) §2 + root [`CLAUDE.md`](../../CLAUDE.md) 읽기 우선순위.
