# Engineering-Agent — Role Research Profiles

각 역할이 자료를 수집할 때 어떤 source type을 우선시하고, 어떤 검색 쿼리 템플릿을 쓰며, 어떤 reference 카테고리를 참고해야 하는지 정의한다. 자료 수집은 `research_pack.ResearchPack`이 그릇이고, 본 문서는 "그릇을 누가 무엇으로 채우는가"의 정책표다.

## 모듈 위치

- 코드: `apps/engineering-agent/src/yule_engineering/agents/research_profiles.py`
- 테스트: `tests/test_research_profiles.py`
- 입력 task_type: `dispatcher.TaskType` 값 문자열 (예: `backend-feature`, `landing-page`).

## 지원 자료 유형 (source_type)

| source_type | 의미 |
| --- | --- |
| `user_message` | 사용자가 직접 쓴 요구사항/메시지 |
| `url` | 사용자가 붙인 임의 링크 |
| `web_result` | 검색으로 발견한 웹 자료 |
| `image_reference` | 이미지/스크린샷/디자인 레퍼런스 |
| `file_attachment` | Discord 첨부 파일 (PDF/Figma export 등) |
| `github_issue` | GitHub issue |
| `github_pr` | GitHub PR |
| `code_context` | 현재 레포의 코드/문서에서 찾은 맥락 |
| `official_docs` | 공식 문서 (API, framework, DB, infra) |
| `community_signal` | Reddit, Hacker News, Stack Overflow 등 커뮤니티 신호 |
| `design_reference` | Pinterest, Notefolio, Behance, Awwwards, Canva, Wix Templates, Mobbin, Page Flows 등 디자인 참고 |
| `research_paper` | arXiv, NeurIPS, ICML 등 학술 논문 |
| `model_docs` | Anthropic/OpenAI/Google/Hugging Face 모델 카드와 API 사양 |
| `ai_framework_docs` | LangChain, LlamaIndex, DSPy, Ragas, TruLens, vector DB(pgvector/Qdrant/Chroma/Weaviate) 등 AI/RAG 프레임워크 공식 문서 |

상수는 `research_profiles.SOURCE_TYPE_*`에 박혀 있으며, `ALL_SOURCE_TYPES` 튜플로 순서가 고정된다. 새 유형이 필요하면 본 표와 모듈의 상수를 함께 갱신한다.

## 역할별 기본 프로필

각 역할에는 `RoleResearchProfile(role, preferred_source_types, suggested_queries, reference_categories, weight_hints)`이 정의돼 있다. `weight_hints`는 0~10 정수이며 0/미지정은 "특별히 우선하지 않음"을 의미한다.

### tech-lead

- 우선 source_type (상위): `user_message`, `github_issue`, `github_pr`, `official_docs`, `code_context`, `url`
- 쿼리 템플릿: `{topic} architecture overview`, `{topic} dependency map`, `{topic} risk and tradeoffs`, `{topic} rollout plan`
- reference: 내부 docs, ADR/RFC, GitHub history
- 핵심: 결정/순서/리스크/승인 여부 종합

### ai-engineer

- 우선 source_type (상위): `official_docs`, `research_paper`, `model_docs`, `ai_framework_docs`, `code_context`, `community_signal`
- 쿼리 템플릿: `{topic} prompt engineering best practice`, `{topic} RAG retrieval evaluation`, `{topic} embedding / vector store options`, `{topic} hallucination grounding strategy`, `{topic} agent evaluation metric`, `{topic} model routing latency cost`
- reference: 공식 모델 docs(Anthropic/OpenAI/Google), Hugging Face model cards, arXiv/research papers, RAG framework docs(LangChain/LlamaIndex), vector DB docs(pgvector/Qdrant/Chroma/Weaviate), agent eval docs(Ragas/TruLens)
- 핵심: autonomous research collector 설계, LLM/RAG/memory 자문, hallucination 방지와 source grounding, token/cost/latency 최적화, agent evaluation 기준

### product-designer

- 우선 source_type (상위): `image_reference`, `design_reference`, `file_attachment`, `url`, `user_message`, `web_result`
- 쿼리 템플릿: `{topic} UI examples`, `{topic} moodboard`, `{topic} accessibility checklist`, `{topic} onboarding flow patterns`
- reference: Pinterest Trends, Notefolio, Behance, Awwwards, Canva Design School, Wix Templates, Mobbin, Page Flows
- 핵심: 무드보드/플로우/UI 레퍼런스/접근성 체크리스트

### backend-engineer

- 우선 source_type (상위): `official_docs`, `code_context`, `github_issue`, `github_pr`, `url`, `user_message`
- 쿼리 템플릿: `{topic} API reference`, `{topic} data model`, `{topic} authentication flow`, `{topic} migration plan`, `{topic} infra/deployment notes`
- reference: 공식 API docs, DB engine docs, auth provider docs, 내부 repo 코드
- 핵심: 데이터 모델, 인증/권한, infra/migration

### frontend-engineer

- 우선 source_type (상위): `official_docs`, `code_context`, `design_reference`, `url`, `user_message`, `web_result`
- 쿼리 템플릿: `{topic} component example`, `{topic} accessibility WCAG`, `{topic} browser compatibility`, `{topic} framework guide`, `{topic} design system mapping`
- reference: MDN, framework 공식 docs, design system docs, Awwwards, Mobbin, Page Flows
- 핵심: 컴포넌트 구조, 접근성, 브라우저 호환성, 디자인 시스템 매핑

### qa-engineer

- 우선 source_type (상위): `user_message`, `github_issue`, `code_context`, `official_docs`, `github_pr`, `community_signal`
- 쿼리 템플릿: `{topic} acceptance criteria`, `{topic} regression scenarios`, `{topic} edge cases`, `{topic} bug reports`, `{topic} test strategy`
- reference: 내부 test plan, bug 라벨 GitHub issues, postmortems, regression suites
- 핵심: 수용 기준, 회귀 시나리오, 엣지 케이스

## task_type 보정 규칙

`build_role_query_hints(role, task_type, topic=...)`가 기본 프로필에 task_type 신호를 더해 가중치를 미세 조정한다. 매칭 없으면 보정 없이 기본 프로필 가중치 그대로 반환한다.

| task_type 그룹 | 매칭 task_type | 영향받는 역할 | 가중치 상향 |
| --- | --- | --- | --- |
| design-heavy | `landing-page`, `visual-polish`, `onboarding-flow`, `email-campaign` | product-designer | `image_reference`, `design_reference` |
| backend-heavy | `backend-feature`, `platform-infra` | backend-engineer | `official_docs`, `code_context` |
| frontend-heavy | `frontend-feature`, `landing-page`, `onboarding-flow`, `visual-polish`, `email-campaign` | frontend-engineer | `code_context`, `official_docs`, `design_reference` (소폭) |
| qa-heavy | `qa-test` | qa-engineer | `github_issue`, `code_context` |

규칙:

- 디자인 task일 때 product-designer의 `image_reference`/`design_reference`가 1위/2위로 올라간다.
- 백엔드 task일 때 backend-engineer의 `official_docs`/`code_context`가 상위로 올라간다.
- 프론트 task일 때 frontend-engineer의 `code_context`(컴포넌트 예시)와 `official_docs`(framework·MDN·접근성)가 상위로 올라간다.
- QA task일 때 qa-engineer의 `github_issue`(과거 사례)와 `code_context`(테스트 대상 코드)가 상위로 올라간다.
- 매칭되지 않은 역할/task 조합은 기본 프로필이 그대로 적용된다 (`notes`도 빈 튜플).

## 출력 — `RoleQueryHints`

```
RoleQueryHints(
    role="product-designer",
    task_type="landing-page",
    weighted_source_types=(
        ("image_reference", 11),
        ("design_reference", 10),
        ("file_attachment", 7),
        ...
    ),
    suggested_queries=("hero UI examples", "hero moodboard", ...),
    reference_categories=("Pinterest Trends", "Notefolio", ...),
    notes=("design-heavy task (landing-page) → image_reference / design_reference 가중치 상향",),
)
```

호출자는 `weighted_source_types`를 그대로 자료 수집기 우선순위 큐로, `suggested_queries`를 검색 쿼리로, `reference_categories`를 reference 추천 카드로, `notes`를 Discord 인테이크 메시지에 그대로 노출할 수 있다.

## 운영 가드

- 본 모듈은 I/O를 하지 않는다. 검색/페치/Discord 호출은 호출자의 책임이다.
- `_DEFAULT_PROFILES`는 모듈 전역 상수다. 런타임에 직접 mutate하지 말 것 — 테스트 시 override가 필요하면 `replace_role_profile_for_tests`를 쓰면 된다.
- task_type은 `dispatcher.TaskType.value`와 동일한 문자열 키를 가정한다. 새 task_type을 추가했다면 위 표와 모듈의 frozenset 4종을 함께 갱신한다.

## 후속 마일스톤

- ResearchPack과 본 프로필의 연결: 자료가 들어올 때 `collected_by_role`과 `source_type`을 보고 본 프로필 가중치로 자동 정렬.
- 사용자 task 신호(예: prompt에 "moodboard", "API reference" 같은 단어)가 있을 때 task_type 분류 외에 추가 가중치를 주는 보정.
- 가중치를 외부 정책 JSON으로 빼서 운영자가 코드 수정 없이 조정 가능하게.

## 부록 A — 역할별 SourceAxis 매트릭스 (engineering_intelligence)

`engineering_intelligence.SourceAxis`는 master plan §9.1에서 명시한 역할별 상시 수집 대상을 enum으로 굳힌 것이다. 위 §역할별 기본 프로필은 사용자 발화 직후 retrieval에서 `source_type` 가중치를 결정하지만, 본 부록은 background ingestion이 어떤 axis를 항상 채워두어야 하는지를 결정한다. 코드 진실 소스는 `source_registry.required_axes_for_role(role_id)` + `axes_for_role(role_id)`.

| 역할 | 필수 SourceAxis (operational seed) | 핵심 source 종류 |
| --- | --- | --- |
| tech-lead | `architecture_adr_tradeoff`, `official_docs` | RFC editor, ISO/IEC 25010, ADR repo, Cloudflare/Stripe engineering blog |
| backend-engineer | `official_docs`, `api_schema_auth`, `release_notes_changelog`, `security` | Spring docs/blog, FastAPI/PostgreSQL/Redis release, OWASP Top 10, NIST CVE |
| frontend-engineer | `web_platform_framework`, `official_docs`, `release_notes_changelog` | React blog, Next.js releases, TypeScript what's new, web.dev, MDN, WCAG |
| devops-engineer | `ci_cd_infra_observability`, `release_notes_changelog`, `security` | Docker/Kubernetes/Argo CD/Terraform release, GitHub Actions changelog, NIST CVE |
| qa-engineer | `regression_test_plan`, `security` | Playwright/Testing Library/Cypress, ISO 29119, Google Testing Blog, NIST CVE |
| ai-engineer | `ai_framework` | OpenAI/Anthropic news, Hugging Face blog, LangChain blog, pgvector, Ragas |
| product-designer | `design_system` | Apple HIG, Material Design, Fluent, Atlassian, Carbon, GOV.UK Patterns |

축가 하나라도 빠지면 `tests/engineering_intelligence/test_source_registry.py::AxisCoverageTests::test_every_role_meets_required_axes` 가 실패한다. 새 source seed를 추가하거나 기존 seed에서 axis tag를 빼면 본 표와 `_ROLE_REQUIRED_AXES` 둘 다 같이 갱신할 것.

## 부록 B — task_type → axis hint matrix

discussion request-time retrieval은 위의 `_ROLE_RESEARCH_PROFILES` 가중치 외에 task_type에서 유도한 axis hint를 추가 보너스로 사용한다. 코드 진실 소스는 `source_registry.axis_hints_for_task_type(task_type)`.

| task_type | 부스트되는 axis |
| --- | --- |
| `backend-feature` | `api_schema_auth`, `official_docs`, `security` |
| `frontend-feature` | `web_platform_framework`, `official_docs` |
| `landing-page` | `design_system`, `web_platform_framework` |
| `onboarding-flow` | `design_system`, `web_platform_framework` |
| `visual-polish` | `design_system` |
| `email-campaign` | `design_system` |
| `qa-test` | `regression_test_plan`, `security` |
| `platform-infra` | `ci_cd_infra_observability`, `architecture_adr_tradeoff` |
| (그 외 / `unknown` / `None`) | (없음 — 기본 role 가중치만 사용) |

이 표를 변경하면 해당 매트릭스를 코드(`_TASK_TYPE_AXIS_HINTS`)와 함께 업데이트해야 한다 — 두 위치가 따로 놀면 retrieval에서 생기는 미스매치를 디버그하기가 까다로워진다.

## 부록 C — knowledge note share_scope 와 evidence surface

`engineering_intelligence.KnowledgeShareScope` 는 한 knowledge note 가 외부 surface(Discord digest, PR body, 합성 응답)에 어디까지 인용될 수 있는지를 결정하는 boundary 플래그다. 본 부록은 retrieval surface 에 share_scope 가 어떻게 노출되는지를 정리한다 — 운영자 관점에서 "이 자료가 chat 응답에 그대로 인용되어도 되는가?" 를 빠르게 판단하기 위한 표.

### C.1 share_scope 별 surface 동작

| share_scope | retrieval surface (`relevant_knowledge` 블록) | Discord 일별 digest |
| --- | --- | --- |
| `public` | 제목 + 출처 링크 + 요약 1줄 + 점수/근거 표시 | 제목 + importance badge + source link |
| `team_internal` | 제목 + 출처 링크 + 점수/근거 표시 (요약 자동 차단) + `🔒 team-internal` 라벨 | 제목 + source link + `🔒 team-internal` 라벨 |
| `restricted` | `🔒 공개 제한된 자료` + `share_scope_reason` 만 노출 (제목/URL/요약 모두 마스킹) | `🔒 공개 제한된 자료 (topic_key)` 라벨만 |

코드 진실 소스: `discussion.context_pack.format_knowledge_evidence_block` + `engineering_intelligence.discord_summary._format_line`.

### C.2 retrieval evidence 라벨

`KnowledgeMatch.evidence_labels()` 가 score signal 토큰을 사람이 읽을 수 있는 한국어 라벨로 변환한다. 합성 응답이나 Obsidian decision note 의 "근거 자료" 블록은 본 라벨을 그대로 사용한다.

| signal token | 한국어 라벨 |
| --- | --- |
| `role_primary_match` | 요청 역할과 정확히 일치 |
| `role_secondary_match` | 요청 역할이 보조 역할로 등록됨 |
| `axis_overlap:<axes>` | task_type 축 일치 (`<axes>`) |
| `topic_overlap:<n>` | 질문 토큰 겹침 (+`n`) |
| `importance_critical` / `importance_high` | 중요도 critical / high |
| `importance_low` | 중요도 low (감점) |
| `fresh_7d` / `fresh_30d` | 최근 7일 / 30일 이내 수집 |
| `empty_body_penalty` | 본문/태그 비어 있음 (감점) |

새 signal 을 추가하면 `engineering_intelligence.retrieval._KNOWN_SIGNAL_LABELS` 에 한국어 라벨을 함께 넣을 것 — 비어 있으면 surface 가 raw token 을 그대로 노출한다.

### C.3 운영 가드

- ContextPack 빌더는 retriever 가 `with_signals` 를 노출하면 우선 사용해서 score + signals 를 surface 에 끌고 들어온다. 라이트한 fallback retriever 만 줘도 되지만 score/signal 트레이스가 없으면 evidence 블록은 점수/근거 없이 제목/요약만 보인다.
- `share_scope=restricted` 자료가 `relevant_knowledge` 에 들어 있으면 합성 응답은 자동으로 본문을 마스킹한다. 운영자가 직접 본문을 합성 응답에 넣는 코드를 추가할 일이 생기면 `format_knowledge_evidence_block` 결과만 사용하거나 `obsidian.shareable_external_payload` 페이로드만 사용한다 — raw `EngineeringKnowledgeRef.summary` 를 직접 인용하지 않는다.
