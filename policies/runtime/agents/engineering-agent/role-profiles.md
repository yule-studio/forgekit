# Engineering Agent — Role Profiles & Selector Policy

본 문서는 Engineering Agent의 **역할 시스템 동작 규칙**을 정리한다. 셀렉터, 멤버 봇 runtime, deliberation, work_report, status diagnostic, Obsidian export 가 동일한 source of truth(`agents/role_profiles.py` + `role_profiles_data.py`)로부터 역할 정의를 읽고, 이 문서는 그 정의의 **운영자 대상 요약**이다.

이 문서 자체도 `policies/runtime/agents/engineering-agent/` 트리 안에 있어 `yule memory reindex` 가 SOURCE_POLICY 로 자동 인덱싱한다. 따라서 검색·요약·retrieval 흐름에서 별도 wiring 없이 노출된다(아래 *Obsidian Vault 반영* 섹션 참조).

## 핵심 원칙

- 역할 시스템은 **일반 정책 엔진**이지, 특정 도메인(쿠버네티스/RAG/디자인 시스템 등) 전용 분기를 셀렉터에 두지 않는다.
- 도메인을 추가하려면 해당 역할 프로필의 `activation_keywords` / `explicit_patterns` 만 수정한다. 셀렉터 코드는 손대지 않는다.
- 모든 작업에서 tech-lead 는 항상 **required**.
- 역할 참여는 단순한 selected/excluded 가 아니라 5단계로 구분한다.

## 참여 수준 (`ParticipationLevel`)

| 수준 | 의미 |
| --- | --- |
| `required` | 반드시 참여. tech-lead 는 항상 이 수준 |
| `primary` | 해당 요청의 핵심 담당. take 우선 검토 |
| `reviewer` | 핵심 담당은 아니지만 검토가 필요 (cross-cut 영향) |
| `optional` | 예산/시간/맥락에 따라 합류 가능 |
| `excluded` | 이번 요청에서는 제외. 사유는 `reason_by_role` 에 기록 |

`session.extra` 에는 다음 키로 영속:

- `active_research_roles` — 참여 4단계(required/primary/reviewer/optional) 합집합
- `excluded_research_roles` — 제외 역할
- `role_participation` — role → 참여 수준 매핑
- `role_selection_primary` / `role_selection_reviewer` / `role_selection_optional` — 단계별 목록
- `role_selection_keywords` — 역할별 firing keyword
- `role_selection_fallback_policy` — fallback 분기 발동 시 정책 id
- `role_selection_source` — `user_explicit` / `tech_lead_rule` / `fallback`

## 역할별 mission 요약

| 역할 | mission |
| --- | --- |
| **tech-lead** | 사용자 요청을 정확히 이해하고 부서 전체가 합의한 작은 실행 가능한 결론으로 정리. 작업 분해/역할 배정/결과 통합 |
| **ai-engineer** | AI/LLM/RAG/agent 관점 판단. 모델·프롬프트·메모리·평가·비용을 운영 가능한 흐름으로 |
| **backend-engineer** | 도메인 모델/API/데이터 계층. 트랜잭션·동시성·인증·마이그레이션 안전성 |
| **frontend-engineer** | UI/사용자 흐름/상태/접근성. 디자인 결정을 운영 가능한 코드 구조로 |
| **devops-engineer** | 런타임 환경/배포/관측/장애 대응. 운영 가능한 형태로 변경이 떨어지게 |
| **qa-engineer** | 인수 조건/회귀 범위/테스트 우선순위. 변경이 망가지지 않게 |
| **product-designer** | 사용자 문제·흐름·UX copy·디자인 시스템. UI 비용 인식 + MVP 범위 |

각 역할의 `required_context` / `must_review` / `forbidden_actions` / `output_sections` / `escalation_rules` / `done_criteria` 전체는 `src/yule_orchestrator/agents/role_profiles_data.py` 가 단일 source of truth.

## Selector 동작

1. **user_explicit** — prompt 에 역할 이름이 명시되면 (`백엔드 엔지니어`, `ai-engineer 관점`) 해당 역할은 `primary`. tech-lead 는 자동으로 `required`. 명시되지 않은 역할은 `excluded`.
2. **tech_lead_rule** — 역할별 `activation_keywords` 점수화. 최고 점수 = `primary`, 그 외 점수 보유 역할 = `reviewer`. 0 점 = `excluded`. `matched_keywords_by_role` 에 firing keyword 그대로 기록.
3. **fallback** — 1·2 모두 미스히트일 때 도메인 hint 로 좁은 팀을 깨운다 (다음 섹션).

## Fallback 정책

| `fallback_policy` | 발동 조건 | 깨워지는 팀 |
| --- | --- | --- |
| `empty_prompt` | prompt 비어 있음 | tech-lead only |
| `vague_infra` | "서버" / "프로덕션" / "스테이징" 등 인프라 broad hint | tech-lead + devops + backend |
| `vague_ai_research` | "기계학습" / "데이터셋" 등 broad hint | tech-lead + ai + backend |
| `vague_product` | "사용자 경험" / "온보딩 흐름" 등 broad hint | tech-lead + product-designer + frontend |
| `vague_engineering` | "개발" / "코드" / "버그" 등 broad hint | tech-lead + backend + qa |
| `legacy_quartet` | 위 모두 미스히트 (예: "안녕하세요") | tech-lead + ai + backend + qa (안전망) |

Hint vocabulary 는 `RoleProfile.activation_keywords` 와 의도적으로 겹치지 않는다 — profile keyword 가 hit 하면 `tech_lead_rule` 분기가 먼저 발동.

## 역할 출력 템플릿

각 역할의 take 는 자기 `output_sections` 를 따른다. 멤버 봇 runtime preface 가 이 템플릿을 prompt 에 끼워 넣어 deterministic 답변과 LLM-backed 답변 모두 같은 섹션 구조를 갖게 한다.

| 역할 | 섹션 (요약) |
| --- | --- |
| tech-lead | 요청 해석 / 작업 범위 / 선택된 역할 / 제외된 역할 / 핵심 결정 / 다음 액션 |
| ai-engineer | AI 관점의 판단 / 모델·프롬프트 전략 / RAG·Memory 정책 / 리스크와 안전장치 / 다음 액션 |
| backend-engineer | 핵심 판단 / API 영향 / DB 영향 / 트랜잭션·동시성 / 예외 케이스 / 구현 제안 |
| frontend-engineer | 핵심 판단 / 컴포넌트 구조 / 상태·API 흐름 / UX 상태 처리 / 접근성·성능 리스크 / 구현 제안 |
| devops-engineer | 실행 환경 영향 / 배포 영향 / 환경변수·시크릿 / 모니터링·로그 / 장애 대응·롤백 / 구현 제안 |
| qa-engineer | 핵심 판단 / 인수 조건 / 회귀 범위 / 테스트 우선순위 / 리스크 / 다음 액션 |
| product-designer | 사용자 관점 판단 / 정보 구조·흐름 / UX copy·상태 처리 / 디자인 시스템·톤 / MVP 범위 제안 / 다음 액션 |

## TechLeadAggregator 정책

`agents/tech_lead_aggregator.py` 가 두 helper 를 제공한다.

- `build_tech_lead_summary_context(role_notes, selection, canonical_prompt)` — 합의안 도출 입력을 JSON-friendly dict 로 묶는다. role_notes / selected_roles / excluded_roles / excluded_reasons / forbidden_actions_by_role / fallback_policy 를 한 곳에서 노출.
- `aggregate_role_outputs(role_notes, selection, canonical_prompt, research_only)` — `AggregateResult` 반환. 다음 규칙을 강제:
  - **research-only 인 경우** 어떤 역할의 next_action 이 "구현"/"수정"을 포함해도 `requires_executor=False`. 코딩 자동 전환 금지.
  - **사용자 결정 키워드** ("사용자 결정", "승인 필요" 등) → `requires_user_decision=True`.
  - **의문문 decision** → `open_questions` 로 분리.
  - **구현 vs 구현 보류 충돌** / **수정 vs 수정 위험 충돌** → `conflicts` 에 "우선순위 결정" 메시지.
  - `risks` / `next_actions` 는 역할 순서 dedup union.

## 라이브 회귀 테스트 예시

| 입력 | 기대 |
| --- | --- |
| `오늘은 k8s 쿠버네티스에 대해서 다루고 싶어. 자료 수집이 목표.` | tech-lead required, devops primary, backend reviewer/primary, ai/qa/product/frontend excluded |
| `RAG/CAG memory 구조를 조사해줘. tech-lead / ai-engineer / backend-engineer / qa-engineer 관점` | user_explicit; tech-lead required, 나머지 3개 primary; devops/frontend/product-designer excluded |
| `Spring Security API 인증 흐름 + 회귀 테스트 + 운영 모니터링` | tech_lead_rule; backend/qa/devops 참여, frontend/product-designer excluded |
| `React 랜딩 hero 컴포넌트 디자인 + 카피 + 접근성 회귀 테스트` | tech_lead_rule; frontend/product-designer/qa 참여, backend excluded |
| `안녕하세요` | fallback (legacy_quartet); tech-lead + ai + backend + qa |
| `버그 좀 봐줘` | fallback (vague_engineering); tech-lead + backend + qa |

## 쿠버네티스가 전용 패치 없이 일반 role profile 로 해결되는 이유

- 쿠버네티스 키워드(`k8s`, `kubernetes`, `쿠버네티스`, `cluster`, `helm`, `ingress`, `service mesh` …) 가 devops profile 의 `activation_keywords` 에 한 번 등록돼 있고, 동일 키워드의 일부가 backend profile 에도 등록돼 있다. 셀렉터는 일반 점수화 알고리즘만 돌리면 자연스럽게 devops primary + backend reviewer 가 나온다.
- 셀렉터에는 `if "k8s" in prompt:` 같은 분기가 없다. 새 도메인을 추가하려면 해당 역할 프로필의 키워드 목록만 수정한다.

## Obsidian Vault 반영 흐름

본 문서가 자동으로 vault 에 노출되는 경로는 다음과 같다.

1. **`yule memory reindex`** (`src/yule_orchestrator/cli/memory.py`) 가 `policies/` 트리를 재귀로 스캔해 `SOURCE_POLICY` 로 인덱싱한다. 본 문서는 별도 wiring 없이 자동 픽업.
2. **memory retrieval** (`src/yule_orchestrator/memory/retrieval.py`) 의 priority chain 에 `SOURCE_POLICY` 가 포함돼 있어 역할별 retrieval 호출이 본 문서를 후보로 잡는다.
3. **`agents/engineering-agent/agent.json`** 의 `policies` 배열에 본 문서 경로를 추가했다 — `context_loader` 가 부서 정책을 불러올 때 본 문서가 함께 적재된다.
4. **work_report / status / role_runtime preface** 는 `RoleProfile.output_sections` 등을 통해 본 문서와 동일 데이터를 코드 차원에서 소비한다(데이터는 `role_profiles_data.py`, 본 문서는 그 데이터의 운영자 요약).

새 역할 / 새 도메인을 추가했을 때 점검 순서:

1. `role_profiles_data.py` 수정.
2. 본 문서의 mission · 출력 섹션 표 갱신.
3. `python3 -m unittest discover -s tests` 통과.
4. `yule memory reindex` 실행해 vault 인덱스 갱신.

## 변경 이력

| 일자 | 변경 |
| --- | --- |
| 2026-05-07 | Phase 1~7 — RoleProfile/ParticipationLevel 도입, 7개 역할 상세 정의, 셀렉터 프로필 기반 전환, fallback 정책 5종 도입, output_sections runtime preface 합류, TechLeadAggregator 정책 helper, 본 문서 작성 + agent.json 등록 |
