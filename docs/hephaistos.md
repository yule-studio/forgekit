# Hephaistos — 오케스트레이터 / 판단 엔진

> ForgeKit 의 최상위 오케스트레이터. 구조 맥락은 [foldering.md](foldering.md) ·
> [architecture.md](architecture.md). 거버넌스 SSoT 는 [hephaistos-governance.md](hephaistos-governance.md).

## 1. 역할

Hephaistos 는 **무엇을·어떻게 할지 판단**하는 엔진이다 — 도구 선택자, 작업 계획자, 스킬 승격자.
직접 일을 수행하지 않고, 실행은 hermes/openclaw/runtime 에 위임한다.

## 2. Hephaistos 가 직접 해야 하는 것

- **intent-classifier** — 요청을 작업 종류/리스크로 분류한다.
- **task-planner** — 작업을 실행 가능한 단계로 분해한다.
- **tool-selector** — 어떤 CLI agent/도구/스킬을 쓸지 평가 근거(nexus/evaluations)로 고른다.
- **result-supervisor** — 실행 결과를 검수하고 재시도/에스컬레이션을 판단한다.
- **skill-promoter** — 반복 가치가 검증된 결과를 armory 로 승격 제안한다.

## 3. Hephaistos 가 직접 하지 말아야 하는 것

- **코드 저장소 노릇 금지** — 모든 구현을 hephaistos 안에 넣지 않는다.
- **외부 연결/로컬 실행 직접 수행 금지** — hermes(외부)·openclaw(로컬)에 위임.
- **승인 우회 금지** — 파괴적/외부 영향 작업은 policy 게이트를 통과시킨다.
- **fake 실행/receipt 금지** — 실제로 한 것만 결과로 보고한다.

## 4. 핵심 원칙

> **Hephaistos 는 코드 저장소가 아니라 판단 엔진이다.**

여기에는 *판단 기준*(어떤 도구를, 왜, 언제 고르는가)만 축적된다. 실제 동작 코드는
`packages/forge-runtime`·`agents/hermes`·`agents/openclaw` 가 갖고, 도구/스킬은 `armory` 에서
오며, 판단 근거는 `nexus/evaluations` 에서 읽는다.
