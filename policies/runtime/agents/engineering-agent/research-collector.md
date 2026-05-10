# Autonomous Research Collector — v0 (MVP)

이 문서는 engineering-agent가 사용자에게 링크/이미지를 묻기 **전에** 1차 자료를 자동 수집하는 collector MVP의 정책 기준선이다. 코드 진실 소스는 `src/yule_orchestrator/agents/research_collector.py`.

## 1. 흐름

```
user message
   │
   ▼
auto_collect_or_request_more_input(role, prompt, task_type, user_links, user_attachments)
   │
   ├── enabled=False  → NoOpCollector → user_links/attachments 있으면 USER_PROVIDED, 없으면 NEEDS_USER_INPUT
   ├── enabled=True   → MockSearchCollector / TavilySearchCollector / BraveSearchCollector
   │                    └── 결과 ≥ 1 → AUTO_COLLECTED
   │                    └── 결과 0 + user 자료 있음 → USER_PROVIDED
   │                    └── 결과 0 + user 자료 없음 → NEEDS_USER_INPUT
   ▼
CollectionOutcome
   ├── pack: ResearchPack | None  (None은 needs_user_input 한정)
   ├── user_prompt: str | None    (needs_user_input일 때 사용자에게 보낼 안내)
   ├── collector_name: str        ("mock" / "tavily" / "brave" / "noop")
   ├── query: str                 (실제 사용한 검색 쿼리)
   └── auto_collected_count: int  (자동 수집 source 수, USER_MESSAGE 제외)
```

원칙: **사용자에게 자료를 묻기 전에 자동 수집을 먼저 시도한다.** 자동 수집이 비어 있을 때만 사용자에게 자료를 요청한다.

## 2. 환경변수

| 키 | 기본값 | 의미 |
|---|---|---|
| `ENGINEERING_RESEARCH_AUTO_COLLECT_ENABLED` | `false` | 자동 수집 on/off. opt-in이라 명시적으로 켜야 동작. |
| `ENGINEERING_RESEARCH_PROVIDER` | `mock` | `mock` / `tavily` / `brave`. 알 수 없는 값은 `mock`으로 fallback. |
| `ENGINEERING_RESEARCH_MAX_RESULTS` | `5` | provider에 요청할 최대 결과 수. 음수/비숫자는 기본값 사용. |
| `ENGINEERING_RESEARCH_MAX_PROVIDER_CALLS` | 안전 기본값 | 한 번의 요청에서 provider 호출을 제한한다. 무한 호출/비용 폭주 방지용. |
| `ENGINEERING_RESEARCH_MAX_RESULTS_PER_ROLE` | 안전 기본값 | 역할별로 사용할 수집 결과 수를 제한한다. 한 역할이 결과를 독점하지 않게 한다. |
| `ENGINEERING_RESEARCH_FORUM_COMMENT_MODE` | `member-bots` | `member-bots`면 gateway는 포스트와 첫 `[research-turn:...]`만 남기고 멤버 봇들이 자기 계정으로 댓글을 쓴다. `gateway`면 gateway가 역할별 코멘트를 대리 게시한다. |
| `TAVILY_API_KEY` | (없음) | provider=tavily일 때 필요. 없으면 mock으로 silent fallback. |
| `BRAVE_SEARCH_API_KEY` | (없음) | provider=brave일 때 필요. 없으면 mock으로 silent fallback. |

규약
- truthy 값: `1` / `true` / `yes` / `on` (대소문자 무시).
- API key는 `.env.local`에만 두고, 절대 출력/로깅하지 않는다.
- `enabled=False`일 때는 어떤 provider 호출도 일어나지 않는다 — `NoOpCollector`가 즉시 빈 튜플을 반환.
- `ENGINEERING_RESEARCH_FORUM_COMMENT_MODE` 변경은 런타임 프로세스 시작 시 읽히므로, 값을 바꾼 뒤에는 `yule discord up`을 재시작한다.

## 3. 메타데이터-only 수집 원칙

수집은 **외부 사이트의 본문/이미지를 다운로드하거나 복제하지 않는다**. 각 source에는 다음만 저장한다.

- `title` — 제목
- `url` 또는 `attachment_id` — 식별자
- `domain` — `extra["domain"]` (URL 호스트만)
- `source_type` — `SourceType` enum 값
- `collected_by_role` — 수집한 역할
- `query` — `extra["query"]` (실제 사용한 검색 쿼리)
- `summary` / `snippet` — 본문은 안 내려받고 검색결과의 snippet만
- `thumbnail_url` — `extra["thumbnail_url"]` (이미지 메타데이터 URL만; 본 모듈은 fetch하지 않음)
- `why_relevant` — 역할 관점에서 왜 관련 있는지 한 줄
- `risk_or_limit` — 약관/한계 (Notefolio·Mobbin 등 자동 수집 민감 사이트는 명시)
- `collected_at` — 시점
- `confidence` — high|medium|low
- `provider` — `extra["provider"]` ("mock"/"tavily"/"brave")

이미지 첨부는 `ResearchAttachment(kind="image", description="thumbnail (metadata only ...)" )` 형태로 메타데이터만 저장. **원본 이미지 파일은 다운로드하지 않는다.**

## 4. 역할별 수집 전략

`build_query_for_role`이 prompt + task_type에 역할별 booster를 붙여 검색 쿼리를 만든다.

| 역할 | booster terms |
|---|---|
| tech-lead | architecture, decision, RFC |
| product-designer | UI reference, UX pattern, design |
| backend-engineer | official docs, API, schema |
| frontend-engineer | MDN, framework docs, accessibility |
| qa-engineer | regression, test plan, e2e |

수집 결과는 `deliberation.ROLE_RESEARCH_PROFILES`의 우선순위로 정렬되어 ResearchPack에 들어간다. 즉:
- product-designer: image_reference / design_reference / file_attachment 우선
- backend-engineer: official_docs / code_context / github_pr 우선
- frontend-engineer: official_docs / design_reference / code_context 우선
- qa-engineer: github_issue / community_signal / official_docs 우선
- tech-lead: user_message / url / official_docs 우선

## 5. Mock collector

외부 네트워크 없이 결정적으로 동작하는 fallback. 각 역할별로 canned bucket을 가지고 있고, 같은 query는 항상 같은 결과 순서를 반환한다 (다른 query는 다른 첫 hit). 테스트와 운영자 dry-run에 동일하게 사용 가능.

| 역할 | mock domain |
|---|---|
| product-designer | mobbin.com / behance.net / awwwards.com / notefolio.net |
| backend-engineer | fastapi.tiangolo.com / postgresql.org / cheatsheetseries.owasp.org |
| frontend-engineer | developer.mozilla.org / web.dev / react.dev |
| qa-engineer | playwright.dev / testing-library.com / github.com |
| tech-lead | github.com / blog.acolyer.org / github.com (issue) |

**왜 mock이 기본값인가**: API key가 없거나 enabled=false 상태에서도 conversation 흐름은 멈추지 않아야 하므로. 운영자는 prod에서 Tavily/Brave key를 채우고 `ENGINEERING_RESEARCH_AUTO_COLLECT_ENABLED=true` + `ENGINEERING_RESEARCH_PROVIDER=tavily` 같은 식으로 활성화한다.

## 6. 외부 provider skeleton

`TavilySearchCollector`와 `BraveSearchCollector`는 MVP 단계에서 **호출만 가능한 형태로 정의**되어 있다. 실제 호출 본문은 `urllib.request` 기반으로 구현됐지만 테스트에서는 build_collector가 key 부재로 mock에 fallback하기 때문에 한 번도 실행되지 않는다.

API 응답을 ResearchSource로 변환하는 `_result_dict_to_source`는 다음 키를 인식한다:
- `title` / `name`
- `url` / `link`
- `snippet` / `description`
- `thumbnail` / `image` / `favicon` (객체면 `src` / `url` 값을 사용)

도메인 → SourceType 매핑은 `_classify_remote_source_type`이 담당한다 (behance/awwwards/mobbin/notefolio → DESIGN_REFERENCE, mdn/web.dev/react/fastapi → OFFICIAL_DOCS 등).

## 7. 자동 수집을 끄지 않는 장면 vs 끄는 장면

**기본 켜기 권장** (`ENGINEERING_RESEARCH_AUTO_COLLECT_ENABLED=true`):
- 일반 운영. 사용자가 한 줄로 요청해도 collector가 첫 단계 자료를 채워 deliberation으로 바로 넘어간다.

**끄기 권장** (`ENGINEERING_RESEARCH_AUTO_COLLECT_ENABLED=false`):
- API key 비용 이슈 / outage.
- Tavily/Brave 서비스 장애 시 mock으로도 안정적이지 않은 경우.
- conversation 디버깅 — 사용자 입력만으로 흐름이 동작하는지 검증할 때.

## 8. Discord 통합 가이드 (호출자 영역)

`auto_collect_or_request_more_input`은 conversation/router 코드가 사용자 메시지를 받은 직후에 호출하도록 설계되었다. 응답 envelope의 분기 처리:

| outcome.mode | conversation 응답 |
|---|---|
| `AUTO_COLLECTED` | 1차 자료 수집 완료 메시지 + `format_collection_summary` 결과를 운영-리서치 forum 게시. deliberation으로 바로 진행. |
| `USER_PROVIDED` | 사용자 자료를 그대로 사용해 deliberation 진행. forum에는 사용자 제공 source 출처를 함께 게시. |
| `NEEDS_USER_INPUT` | `outcome.user_prompt` 그대로 사용자에게 답변. deliberation은 보류. |

운영-리서치 forum 게시는 `format_collection_summary(pack, collector_name, query, role)`이 출처 / 요약 / 활용 가능성 / 한계를 한 블록으로 만들어 주므로, 호출자는 이걸 forum thread post body나 댓글에 그대로 끼워 넣으면 된다.

## 9. 외부 사이트 정책 (재확인)

- **Notefolio / Pinterest / Canva / Wix / Mobbin** 은 직접 scraping 금지. Mock canned data + 사용자 제공 링크 + 검색 결과 metadata + 공식 API만 사용.
- **로그인/권한이 필요한 사이트**는 자동 수집 대상에서 제외.
- **이미지 본문**은 다운로드하지 않는다. thumbnail URL만 ResearchAttachment metadata에 보존.
- **GitHub**는 공식 REST API (gh CLI 또는 토큰)를 통한 read-only만 허용. issue/PR fetch는 별도 마일스톤.

## 10. 변경 절차

- 새 provider 추가 시 `KNOWN_PROVIDERS` 튜플과 `build_collector` factory 분기, env 키 명시까지 같은 PR로.
- canned mock bucket을 바꿀 때는 본 정책 §5 표도 같이 갱신.
- `_classify_remote_source_type` 도메인 매핑이 늘어나면 `research-pack.md` §3의 SourceType 표와 일치 검증.
- max_results 기본값을 바꾸면 본 §2 표와 `.env.example` 둘 다 갱신.

## 11. 후속 작업

1. discord conversation/router 통합 — `outcome.mode`에 따른 응답 분기 wire-up (다른 Claude 영역).
2. forum publisher가 `format_collection_summary`를 thread body에 끼워 넣어 자동 게시.
3. Tavily/Brave 실 호출 검증 — 실제 API key 환경에서 응답 shape이 `_result_dict_to_source`와 일치하는지 확인.
4. provider별 rate limit / 비용 관리 정책.
5. 자동 수집 결과의 `confidence` 보강 — provider score / domain trust / role profile 일치도를 합쳐 high/medium/low 결정.

## 12. Background ingestion scheduler (engineering_intelligence)

본 §1~§11은 **요청이 들어왔을 때** intake 가 부르는 collector를 다룬다. master plan §6.1의 background knowledge loop는 별개 모듈이며, 코드 진실 소스는 `src/yule_orchestrator/agents/engineering_intelligence/scheduler.py`.

### 12.1 흐름

```
runtime tick (every N minutes)
   │
   ▼
compute_refresh_plan(role_id, now, states, tick_quota)
   │
   ├── never attempted        → due (reason=never_attempted)
   ├── interval 경과           → due (reason=due 혹은 retry_after_X_failures)
   ├── interval 미경과         → skipped_fresh (next_eligible_at 명시)
   ├── failure backoff 미경과  → skipped_backoff (×1 / ×2 / ×4 / ×8, 24h cap)
   ├── review_required=True   → skipped_review_required
   ├── auto_collect=False     → skipped_auto_collect_disabled
   └── tick quota 초과        → skipped_quota
   ▼
RefreshPlan(role, due, skipped, tick_quota)
   │
   ▼
collect_for_role_with_schedule(role_id, adapter, states, now)
   │
   ├── plan.due_source_ids() 만 adapter 호출
   ├── adapter 실패 → record_refresh_outcome(success=False, notes=...)
   ├── adapter 성공 → record_refresh_outcome(success=True, items_collected)
   └── returns (CollectionRunResult, updated_states)
```

### 12.2 운영 의도

- **per-tick quota = role daily limit (5)** 가 기본. 한 번의 tick이 한 역할의 모든 source를 동시에 때리지 않게 하려는 가드.
- **exponential backoff**는 source 자체 interval에 anchor 한다. 6h 주기 RSS는 두 번 실패하면 12h를 기다리고, 30분 주기 보안 advisory는 1h 만 기다린다 — 신선도 우선순위가 작동한다.
- **state persistence는 본 모듈 책임이 아니다.** `SourceRefreshState` 가 caller가 sqlite/vault 사이드카에 직렬화해 보관하고 다음 tick에 다시 넣어준다. 본 모듈은 pure function.
- `overdue_axes_for_role(role, states, grace_factor=2.0)` 는 "이 역할의 어떤 axis가 통째로 새 자료가 안 들어오고 있는가" 를 surface한다. 운영자 dashboard에 그대로 노출하면 되고, alert 임계값은 운영자가 결정.

### 12.3 운영 환경변수 (Round 4 예약)

scheduler 자체는 env를 읽지 않는다. 다만 **runtime 가 본 모듈을 부를 때** 다음 env가 의미를 가지게 될 예정이다 (현 commit 시점에는 wiring 부재).

| 키 | 의미 |
| --- | --- |
| `YULE_ENG_INTEL_REFRESH_TICK_INTERVAL_MINUTES` | scheduler tick 간격. 기본 15분 권장. |
| `YULE_ENG_INTEL_REFRESH_GLOBAL_QUOTA` | 한 tick에서 모든 역할 합쳐 호출할 수 있는 source 상한. dispatcher 부하 방지용. |
| `YULE_ENG_INTEL_REFRESH_DRY_RUN` | true면 plan만 계산하고 adapter 호출은 하지 않는다. 운영자가 cadence를 검증할 때 사용. |

이 env는 후속 PR에서 runtime side에 wire한다 — 본 PR scope에는 포함되지 않는다.

## 13. Live provider seam (engineering_intelligence)

코드 진실 소스: `src/yule_orchestrator/agents/engineering_intelligence/providers.py`.

### 13.1 디스패치

`provider_spec_for(SourceEntry) -> LiveProviderSpec` 가 단일 진입점이다. `SourceEntry.collection_mode` + base_url heuristic으로 transport를 결정한다.

| CollectionMode | URL hint | ProviderTransport | parser | rate_limit_per_minute |
| --- | --- | --- | --- | --- |
| `MANUAL` | 무관 | `manual` | `manual` | 0 (호출 안 함) |
| `GITHUB_API` | `github.com/...` | `github_api_repo_activity` | `github_api` | 30 |
| `RSS` | `github.com/.../releases` | `github_releases_atom` | `atom` | 20 |
| `RSS` | `*.atom` | `atom` | `atom` | 30 |
| `RSS` | 그 외 | `rss` | `rss` | 30 |
| `SITEMAP` | — | `sitemap` | `sitemap` | 10 |
| `HTML_LIST` | — | `html_list` | `html_list` | 15 |
| `HTML_DETAIL` | — | `html_detail` | `html_detail` | 5 |

### 13.2 보안/secret 정책

- `LiveProviderSpec.requires_auth_env` 는 **env key 이름**만 든다 — 실제 토큰 값은 절대 들어가지 않는다.
- GitHub API spec의 `requires_auth_env=("YULE_GITHUB_APP_ID",)` 는 "App env 3종 중 하나라도 비어 있으면 fetch 시도 자체를 차단해야 한다" 는 신호다. fetcher impl 가 아직 land 하지 않았으므로, 본 PR scope 에서는 `StubLiveSourceFetcher` 만 제공한다.
- request_headers_seed에는 token 헤더가 없다. fetcher impl 가 env를 읽어 별도로 합성해야 한다.

### 13.3 share_scope — 외부 surface 인용 boundary

`engineering_intelligence.KnowledgeShareScope` 가 한 자료 항목이 어디까지 외부 surface(Discord digest, PR body, 합성 응답)에 인용 가능한지를 결정한다. 코드 진실 소스: `models.KnowledgeShareScope` + `obsidian.shareable_external_payload` + `discord_summary._format_line`.

| share_scope | 외부 surface 노출 | Obsidian note 본문 | 사용 예 |
| --- | --- | --- | --- |
| `public` | 제목 + 요약 + source link 인용 가능 | 14 섹션 전부 렌더 | 공식 docs / release notes / 엔지니어링 블로그 |
| `team_internal` | 제목 + source link 만 노출 (요약 차단) | 14 섹션 전부 렌더, 다만 외부 합성 응답에 본문은 사용 금지 | 사내 ADR / private repo 의 PR / 사내 advisory |
| `restricted` | "🔒 공개 제한된 자료" 신호만 노출 | 학습 본문 섹션은 placeholder, 메타데이터 + share_scope_reason 만 보존 | 보안 incident / 고객 데이터가 섞인 자료 |

규칙:

- 기본값은 `public`. collector 가 명시하지 않으면 `public` 으로 처리한다.
- `restricted` 자료는 반드시 `share_scope_reason` 을 채워야 한다 — quality gate 가 막는다 (`missing:share_scope_reason_for_restricted`).
- `team_internal` 자료는 chat 합성 응답의 본문 인용에 사용하지 않는다. ContextPack `relevant_knowledge` 의 `summary` 가 `team_internal` 인 경우 evidence 블록은 자동으로 요약 라인을 생략한다.
- `restricted` 자료는 ContextPack evidence 블록에서도 제목/링크가 모두 마스킹되고 `topic_key` + `share_scope_reason` 만 surface 에 남는다.

운영자 관점:

- vault 안에서는 share_scope 와 무관하게 본문(자료 형태에 따라 placeholder 가 박힌 채로) 이 다 보인다 — Obsidian 은 운영 채널이지 외부 surface 가 아니다.
- 외부 surface 로 보낼 페이로드를 만들 때는 `obsidian.shareable_external_payload(item)` 결과만 사용한다. raw `EngineeringKnowledgeItem.to_payload()` 는 vault 전용.

### 13.4 후속 wiring (별도 PR)

1. `providers_live.py` — urllib 기반 fetcher 구현 (RSS / Atom / Sitemap / HTML_LIST / GitHub Releases / GitHub API).
2. fetcher rate limiter — `LiveProviderSpec.rate_limit_per_minute` 를 token bucket으로 강제.
3. fetcher → adapter 어댑터 — `SourceCollectorAdapter` Protocol 에 fit하도록 (spec, source) → items 형태를 (source) → items 로 변환.
4. runtime service 등록 — `eng-research-collector` 서비스가 본 PR 의 scheduler + 별도 fetcher 를 합쳐 tick 마다 돌도록.

위 4번의 runtime wiring 은 본 worktree 의 write scope 밖이다. 본 PR 는 데이터 모델 + 정책 + 스케줄러 + 어댑터 seam 까지만 land 한다.
