# Agent Organization Naming Convention

> 본 문서는 ForgeKit company 모델([`forgekit-company-model.md`](forgekit-company-model.md))
> 의 office / division / team / role(agent) **네이밍 규칙 SSoT** 다. 식별자가
> 흩어지면 dispatch·문서·테스트가 silently 어긋나므로 한 곳에 고정한다.
>
> 식별자(역할) 정규화의 코드 SSoT 는 기존
> `forgekit_config.identity.registry`(`be`→`backend-engineer`) — 본 문서는 그
> 위의 조직 레벨 명명 규칙을 더한다. 충돌 시 registry 가 우선.

## 1. 계층별 명명 규칙

| 계층 | 형식 | 예 | 비고 |
|---|---|---|---|
| office | `<C-level> / <role-noun>-director` | `CTO / engineering-director` | C-level 약어 + 사람용 director 명 |
| office id | `<kebab>-office` 또는 부서 id | `engineering-agent` | 현재 부서 id 와 호환 유지 |
| director | `<domain>-director` | `operations-director`, `forge-master`(CEO 예외) | CEO 만 `forge-master` |
| division | `<domain>-division` | `platform-runtime-division` | office 내부 큰 묶음 |
| team | `<domain>-team` 또는 `<domain>-lab` | `forgekit-core-team`, `revenue-product-lab` | 실험 성격은 `-lab` 허용 |
| role (agent) | `<domain>-<function>` | `backend-engineer`, `tech-lead`, `reality-checker` | registry 정규화 대상 |

### 규칙 디테일
- **office**: 사람용 표기는 `CTO / engineering-director` 처럼 약어+director 병기.
  머신 식별자는 기존 부서 id(`engineering-agent`)를 그대로 쓴다 — 폴더를 안 옮기므로.
- **CEO 예외**: 최상위는 `forge-master`(브랜드명). director 접미사 안 씀.
- **team**: 기본 `-team`. 탐색/실험 성격이 본질인 팀만 `-lab`(예: `revenue-product-lab`).
  이미 `team_topology` 에 박힌 id 가 SSoT — 새로 만들 때만 본 규칙 적용.
- **role**: 소문자 kebab, `<domain>-<function>`. 1 역할 = 1 책임 axis(corporate-org-chart 단일책임 원칙).

## 2. 예약/특수 식별자

| 식별자 | 의미 | 규칙 |
|---|---|---|
| `forge-master` | CEO | 유일. director 접미사 없음 |
| `tech-lead` | engineering technical 승인자 | `-engineer` 안 붙임(lead 역할) |
| `gateway` | 부서 operator/라우팅 표면 | role 이 아니라 표면. council seat 아님 |
| `reality-checker` | reality-check 실행 role | reality-check-team 소속(미래) |
| `ops-observer` | cross-cutting 관측 | council seat 아님(auxiliary) |

## 3. 충돌 회피 규칙

- 같은 도메인 단어를 office/team/role 에 동시에 쓸 때 **접미사로 계층을 구분**한다:
  `platform-runtime-division`(div) vs `platform-runtime-team`(team) vs
  `platform-runtime-engineer`(role).
- team 과 division 이 1:1 로 승격되는 경우(Growth→Target) id 접미사만 `-team`→`-division`.
  내용 중복 정의 금지 — `team_topology.growth_path` 가 4→8→12 승격을 표현한다(SSoT).
- 부서 id(`<x>-agent`)와 office 표기(`<x>-director`)는 **다른 레이어** — 혼용 금지.

## 4. 머신 vs 사람 표기

| 맥락 | 표기 |
|---|---|
| 폴더/manifest/코드 식별자 | 머신 id (`engineering-agent`, `backend-engineer`, `forgekit-core-team`) |
| 문서/리포트/대화 | 사람용 (`CTO / engineering-director`, `백엔드 엔지니어`) |
| decision log / trail | registry 정규화 id (`tech-lead`, `backend-engineer`) |

## 5. 신규 식별자 추가 절차

1. 본 문서 §1 형식 확인.
2. 머신 id 가 기존 `team_topology` / `forgekit_config.identity.registry` 와 충돌 안 하는지 확인.
3. office/team 추가면 [`agent-office-map.md`](agent-office-map.md) 매핑표 갱신.
4. role 추가면 해당 부서 manifest `members` + corporate-org-chart 매트릭스 갱신.

## 6. 동기화

- 네이밍 규칙 변경 → 본 문서 + [`forgekit-company-model.md`](forgekit-company-model.md) §1.
- 머신 id 규칙은 `forgekit_config.identity.registry` 가 코드 SSoT — 본 문서는 조직 레이어만.
