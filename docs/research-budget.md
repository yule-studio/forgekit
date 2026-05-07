# Research Budget & Multi-provider

이 문서는 자율 리서치 수집기의 budget tier / provider 정책 / 안전장치를 정리한다.

## 1. Budget tier

Reference budget 은 task 내용에 따라 4 단계로 자동 분류된다 (`agents/research_budget.py`).

| Tier | provider 호출 | results / role | 분류 키워드 |
|---|---|---|---|
| `small` | 4 | 2 | `버그 / typo / quick fix / 간단` |
| `medium` (기본) | 8 | 3 | (분류 미스 시 fallback) |
| `large` | 16 | 5 | `architecture / RAG / multi-agent / infra / 리서치 / 조사 / 설계 / 아키텍처 / 검토` 또는 `task_type=platform-infra` |
| `deep_research` | 28 | 8 | `깊게 / deep dive / 리서치 먼저` |

**`ENGINEERING_RESEARCH_MAX_PROVIDER_CALLS` / `ENGINEERING_RESEARCH_MAX_RESULTS_PER_ROLE` 는 hard cap.** 분류기가 large/deep tier 를 골라도 env 값이 더 작으면 그 값으로 클램프된다 (비용 안전). 기본 운영값은 medium 이하로 시작하고, 큰 리서치를 정말로 돌리려면 env 값을 같이 12~20 정도로 올려야 large/deep tier 가 실제로 작동한다.

결과 `CollectionOutcome` 에 `budget_tier / max_provider_calls / max_results_per_role / role_targets / stop_reason / under_covered_roles` 가 노출되고, 운영-리서치 forum body 에도 `### 수집 예산 / 종료 조건` 섹션으로 자동 렌더링된다.

## 2. Multi-provider 검색 (auto / multi 모드)

`ENGINEERING_RESEARCH_PROVIDER=auto` (또는 `multi`) 로 두면 Tavily + Brave 를 함께 사용한다. `ENGINEERING_RESEARCH_PROVIDERS=tavily,brave` 로 후보를 좁힐 수 있고, API key 가 비어 있는 provider 는 `outcome.pack.extra["auto_skipped_providers"]` 에 skipped reason 과 함께 남고 silent skip 된다 — 즉 **한쪽 키만 있어도 나머지 한쪽으로 폴백**한다.

| 역할 | 1순위 provider | 2순위 provider | 비고 |
| --- | --- | --- | --- |
| `gateway` | (없음) | (없음) | 기본은 local memory 만 사용 — 라우팅 결정에 외부 검색 미사용 |
| `tech-lead` | Tavily | Brave | 합의 / 요약 / 비교 (Tavily) + 공식 문서 / GitHub / 최신성 (Brave) |
| `ai-engineer` | Tavily | Brave | RAG / CAG, agent 아키텍처, prompt / context, retrieval |
| `backend-engineer` | Brave | Tavily | 공식 API 문서, DB / 보안 / 아키텍처, GitHub issue, release note |
| `frontend-engineer` | Brave | Tavily | MDN, web.dev, browser support, UI library docs |
| `product-designer` | Brave | Tavily | 경쟁 서비스, UX 사례, 디자인 패턴, benchmark |
| `qa-engineer` | Brave | Tavily | 테스트 전략, regression, GitHub issue, release note |
| `devops-engineer` | Brave | Tavily | GitHub Actions, Docker, CI/CD, observability, incident |

## 3. 설정 예시

`.env.local` 에만 두고 git 에는 커밋하지 않는다.

```bash
ENGINEERING_RESEARCH_AUTO_COLLECT_ENABLED=true
ENGINEERING_RESEARCH_PROVIDER=auto
ENGINEERING_RESEARCH_PROVIDERS=tavily,brave
ENGINEERING_RESEARCH_MAX_RESULTS=5
# 비용 안전장치: 모든 provider 호출의 합계 상한이므로 낮게 시작.
ENGINEERING_RESEARCH_MAX_PROVIDER_CALLS=3
ENGINEERING_RESEARCH_MAX_RESULTS_PER_ROLE=2
TAVILY_API_KEY=<발급받은 Tavily key>
BRAVE_SEARCH_API_KEY=<발급받은 Brave key>
```

Multi 모드에서는 `ENGINEERING_RESEARCH_MAX_PROVIDER_CALLS` 가 **모든 provider 호출의 합계** 상한 — Tavily 1 회 + Brave 1 회 = 2 슬롯. 비용을 천천히 검증하기 위해 처음에는 3 정도로 시작하고, 결과 품질을 본 뒤에 단계적으로 올리는 것을 권장한다. 예산을 모두 소진하면 `pack.extra["budget_note"]` 에 알림이 남는다.

## 4. Dedupe

같은 자료가 두 provider 에서 모두 잡혀도 한 건만 남기도록 다음 3 단계로 dedupe 한다.

1. URL 정규화 — scheme / host 소문자 + trailing slash + UTM 파라미터 제거.
2. 동일 도메인 + source_type + title prefix 일치.
3. URL 없는 항목은 title + type 조합 일치.

## 5. 회귀 / 진단

- Phase 4 stab 은 각 역할 봇의 collection 결과를 `session.extra['role_research_results'][<role>]` 에 적재하고, `session.extra['role_activity_log']` 에 audit trail 을 남긴다. 자세한 거동: `policies/runtime/agents/engineering-agent/lifecycle-mvp.md` + `docs/engineering.md`.
- Phase 6 stab 은 work_report 가 `ready` 로 graduate 하려면 적어도 한 role 이 `status="ok"` 인 record 를 남겨야 한다는 게이트를 추가했다.
- 회귀 검증: `policies/runtime/agents/engineering-agent/live-regression.md` §1 (k8s research-only).
