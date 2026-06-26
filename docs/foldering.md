# Foldering — ForgeKit 1차 폴더 구조

> ForgeKit 을 Local-first Agent Control Plane 으로 확장하기 위한 **디렉터리 책임 SSoT**.
> 전체 아키텍처는 [architecture.md](architecture.md), 오케스트레이터는 [hephaistos.md](hephaistos.md).

## 1. 최상위 디렉터리 목적

| 디렉터리 | 목적 | 두지 말아야 할 것 |
| --- | --- | --- |
| `apps/` | 실행 앱 (forge-cli / forge-daemon / forge-dashboard) | 플랫폼 코어 로직 |
| `packages/` | 플랫폼 코어/엔진 (forge-core/policy/workspace/registry/runtime + 기존 forgekit-*) | 앱 전용 코드 |
| `agents/` | 역할 에이전트 정의 (hephaistos / hermes / openclaw …) | 대량 런타임 구현 |
| `armory/` | 도구 창고 — 프롬프트/스킬/스크립트/어댑터/레시피 | 미검증 실험물 |
| `nexus/` | 기억 저장소 — 노트/평가/실행기록/참고자료 | 실행 코드 |
| `labs/` | 실험장 — clone coding / CLI agent 조사 / 패턴 추출 | 정식(production) 코드 |
| `docs/` | 사람용 문서 허브 | 코드/런타임 상태 |

> 참고: 최상위 `armory/`·`nexus/` 는 **데이터/자산 저장 표면**이고, `packages/armory`·
> `packages/nexus` 는 그것을 다루는 **코드**다 — 역할이 다르므로 공존한다.

## 2. 승격 흐름 (labs → armory → registry)

```text
labs/ (실험·조사)  →  armory/ (반복 가치 승격)  →  packages/forge-registry (정식 등록)
   clone coding         prompt/skill/adapter         runtime 에서 보이는 도구
   CLI agent 평가        recipe
```

- **labs**: 자유 실험. clone coding 결과, CLI agent 조사, 패턴 추출. 깨져도 되는 공간.
- **armory**: labs 결과 중 **반복적으로 쓰이고 검증된 것만** 승격. 1회성은 승격 금지.
- **forge-registry**: armory 승격물을 등록해야 런타임이 인식. **adopted ≠ equipped**.

## 3. 원칙

- **clone coding 결과는 `labs/` 에만 둔다.** 정식 코드(`packages/`·`apps/`)와 섞지 않는다 —
  실험 출처가 흐려지면 무엇이 검증됐는지 알 수 없다.
- **Hephaistos 안에 모든 코드를 넣지 않는다.** `agents/hephaistos` 에는 **판단 기준과 도구
  선택 로직만** 축적하고, 실제 실행은 hermes/openclaw/runtime 으로 위임한다 ([hephaistos.md](hephaistos.md)).
- **빈 디렉터리는 README 또는 `.gitkeep`** 으로 유지한다.
- 1차는 **scaffolding only** — runtime 구현/dependency/build tooling 은 추가하지 않는다.
- **`forge-*` 식별자는 컨셉/서사용이다.** 실제 package/app 정식 prefix 는 `forgekit-*` 가
  canonical — 위 `forge-core`/`forge-cli` 등 placeholder 의 구현명/disposition 기준은
  [package-topology.md §9](package-topology.md) 가 SSoT. 본 문서는 컨셉 지도일 뿐이다.
