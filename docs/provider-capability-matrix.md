# Provider capability matrix — vendor-neutral capability → provider 배치

> 짝 문서 [`plugin-taxonomy.md`](plugin-taxonomy.md) 가 *개념 분리*를 정의한다.
> 본 문서는 *배치* 를 정의한다: 어떤 capability 를 공통으로 두고, 어떤 것을
> Claude/Codex/Gemini 로 투영하며, Ollama 를 어디에 backend 로 꽂는가.
>
> 한 단계 위 레버 — capability 를 **아예 live LLM 으로 보낼지 말지**(rule_first /
> llm_optional / llm_required)는 [`llm-minimization-policy.md`](llm-minimization-policy.md)
> 가 SSoT. 본 매트릭스는 "LLM 을 쓴다면 어느 provider" 의 배치만 정의한다.
>
> 런타임에서 *실제로 어느 provider 가 돌았고/실패했고/얼마 들었는지* 의 관측 표면(provider
> runtime telemetry · cost proxy · operator dashboard)은
> [`runtime-operator-surfaces.md`](runtime-operator-surfaces.md) 참조.

## 1. 원칙

1. **capability 는 vendor-neutral SSoT 에 한 번 정의**한다(skill/hook/plugin).
2. provider(Claude/Codex/Gemini)는 **projection target** 이다 — 같은 capability 를
   각자의 native 표현으로 투영.
3. **Ollama 는 backend slot** 이다 — plugin host 가 아니라 local inference/tool-calling
   endpoint. classification/summarization/compression/fallback 추론에 배치.
4. 배치는 "그 provider 의 native 생태계가 가장 강한 곳"을 따른다(아래 §3).

## 2. capability × provider 매트릭스

열 의미: **P**=primary(주 실행) · **prj**=projection(SSoT→투영) · **bk**=backend slot · **—**=해당 없음.

| capability class | 성격 | Claude Code | Codex | Gemini | Ollama |
| --- | --- | --- | --- | --- | --- |
| security gate / outbound redaction | hook(guard) | **P** | prj | prj | — |
| pre/post tool hook · approval gate | hook | **P** | prj | — | — |
| compaction / context-compression | skill+hook | **P** | prj | prj(cache) | bk(compress) |
| enforcement (grant/governance) | hook | **P** | prj | — | — |
| verification / smoke / LSP preflight | hook+plugin | **P** | prj | — | — |
| execution workflow (multi-step 작업 실행) | backend+tools | prj | **P** | — | — |
| doc/browser/computer-use/Figma 조작 | MCP/tool | — | **P** | prj | — |
| GitHub/Slack/Notion 연계 실행 | tool | prj | **P** | — | — |
| research / large-context reading | backend | prj | — | **P** | bk(보조) |
| cheap analysis / draft generation | backend | — | prj | **P** | bk |
| classification / routing | backend | — | — | prj | **P(bk)** |
| summarization | backend | prj | — | prj | **P(bk)** |
| cheap compression | backend | — | — | — | **P(bk)** |
| fallback reasoning (오프라인/저비용) | backend | — | — | — | **P(bk)** |

> 같은 capability 가 여러 provider 에 보이는 건 정상이다 — SSoT 1개, projection N개.
> **P** 는 "그 생태계가 가장 강해서 1차로 둔다"는 뜻이지 독점이 아니다.

## 3. provider 별 역할 배분 (왜 이렇게 두는가)

### Claude Code — 안전/감사/검증 평면
강한 plugin/hook/MCP/LSP/monitor 생태계. → **security gate, pre/post tool hook,
compaction, enforcement, verification** 의 primary. 이 레포의 harness 강제·execution
receipt·compact→vault·security gate 가 이미 Claude 쪽에 결선돼 있다(가장 성숙).

### Codex — 실행/도구 조작 평면
GitHub·Slack·Notion·Browser·Computer Use·Figma·Documents 같은 **실제 작업 도구 연계**가
강함. → **실행형 워크플로우, 문서/브라우저/도구 조작**의 primary. coding execute /
github work order 류 실행 경로의 자연스러운 target.

### Gemini — 리서치/대용량 분석 평면
MCP·custom commands·GEMINI.md·token caching·trusted folders. → **research,
large-context reading, cheap analysis, draft generation**. 현재 `GEMINI.md` 는 advisor
계약만 있고 **projection 생성물이 없다**(공백 — [taxonomy §5 Gemini projection](plugin-taxonomy.md)).

### Ollama — local inference backend
plugin 플랫폼이 아니라 **local model backend / tool-calling endpoint**. →
**classification, summarization, cheap compression, fallback reasoning** 배치.

## 4. 왜 Ollama 는 plugin host 가 아니라 backend 인가 (질문 2 답)

1. **plugin/hook/skill 은 절차이고, Ollama 는 엔진이다.** Claude Code/Codex/Gemini 는
   각자 plugin/command/hook 을 *호스팅*하는 생태계를 가진다. Ollama 는 그런 호스팅
   레이어가 없다 — `/v1/chat/completions` 류 추론 endpoint 일 뿐.
2. **투영 대상이 아니다.** skill 을 `.ollama/...` 로 투영할 곳이 없다. harness projection
   의 대상이 될 수 없으므로 `harness` 목록에 들어가지 않는다.
3. **manifest 상 위치가 backend 다.** 이미 `participants` 에 `provider: local,
   endpoint: http://localhost:11434` 로 backend 로 선언돼 있고 runner 는
   `agents/runners/ollama.py`. 구조가 backend 임을 말한다.
4. **역할이 추론이다.** classification/summarization/compression/fallback 은 "무엇을
   실행하나(절차)"가 아니라 "무엇으로 추론하나(엔진)". → backend slot.

결론: Ollama 를 plugin taxonomy 에 넣으면 개념이 섞인다. **backend slot** 으로 두고
routing 정책(아래 §5)이 어떤 작업을 Ollama 로 보낼지 정한다.

## 5. Ollama backend slot — routing 정책

`agents/runners/role_runner.py` 의 dispatch 우선순위(claude → codex → ollama →
deterministic)에 더해, **작업 성격 기반 backend 선호**를 둔다(제안):

| 작업 | 선호 backend |
| --- | --- |
| classification / intent / routing | Ollama(저비용) → Gemini(보조) |
| summarization / compression | Ollama → Gemini |
| research / long-context | Gemini → Claude |
| 실행형(도구 조작/배포) | Codex → Claude |
| 안전/감사/검증/enforcement | Claude |
| 모두 불가 시 | deterministic fallback |

**✅ capability-aware routing 결선됨**(seam): `agents/runners/capability_routing.py` 가
capability class → backend 선호(위 표)를 정의하고, `role_runner` dispatcher 의 선택적
`provider_router` 훅이 작업별로 후보를 재정렬한다(lossless, deterministic 항상 마지막).
게이트웨이 결선은 `bootstrap` 에서 `YULE_CAPABILITY_ROUTING_ENABLED`(기본 off)로 opt-in.
capability 는 `RoleRunnerInput.metadata['capability_class']` 또는 `task_type` 로 추론.
회귀 `tests/runners/test_capability_routing.py`.

> Gemini 는 아직 role-runner adapter 가 없어 후보에 없을 수 있다 — 그 경우 선호에서
> 자동으로 다음 backend 로 넘어간다(예: research → gemini 부재 시 claude).

## 6. 무엇이 공통이고 무엇이 projection 인가 (질문 1 답)

- **공통 capability(vendor-neutral SSoT)**: security gate / compaction / enforcement /
  기억(claude-mem) / skill 절차 — backend 무관하게 항상 정의되는 것. `plugins/` +
  `skills/` + grants 가 SSoT.
- **provider-specific projection**: 그 capability 를 각 harness 의 native 형태로 투영한
  생성물(`.claude-plugin`/`.codex-plugin`/후속 `.gemini`). 그리고 provider native 에만
  존재하는 것(Codex computer-use, Gemini token cache, Claude pre-tool hook)은 SSoT 는
  중립이되 `harness` 로 해당 provider 에만 투영.

## 7. 현재 repo 의 공백 (질문 3 답)

| 공백 | 근거 파일 | 다음 단계 기준 |
| --- | --- | --- |
| Gemini projection 생성물 없음 | `GEMINI.md`(계약만), `scripts/sync_harness_skills.py`(claude/codex만) | `HARNESS_TARGETS["gemini"]` + 생성 분기 |
| MCP SSoT 없음 | docs 에 개념만, 코드 없음 | `integrations/mcp/<id>.json` 도입 |
| ~~capability→backend routing 미구현~~ ✅ | `agents/runners/capability_routing.py` + `role_runner` provider_router 훅(`YULE_CAPABILITY_ROUTING_ENABLED`) | §5 정책이 코드로 |
| ~~runtime plugin capability_class 미표기~~ ✅ | `plugins/*/manifest.json` 에 `capability_class` 선언(`extension/manifest.py` `CAPABILITY_CLASSES`) | matrix 데이터 도출 가능 |
| `harness` 의미가 문서화 안 됨 | grants JSON | 본 문서 + taxonomy §5 로 고정(완료) |

## 8. 질문 4 — 승격 vs provider-specific 분배
[plugin-taxonomy §7](plugin-taxonomy.md) 표 참조. 요약:
- 안전/감사/거버넌스/기억 = **공통 승격**(현재 대부분 이미 `plugins/`·`skills/`).
- native 의존(computer-use/token-cache/pre-tool-hook) = **projection 유지**.
- Ollama = **backend 분리**.

## 9. Forgekit provider 계약/정책으로의 투영

이 매트릭스가 정의하는 capability 배치는 forgekit(설치형 제품)에서 **고정 최소
provider 계약(`ProviderSpec`) + slot 정책**으로 투영된다 —
[`forgekit-provider-policy.md`](forgekit-provider-policy.md). 본 문서가 "어느 provider
가 어느 capability 를 1차로 두나"를 정하면, forgekit 쪽은 그 capability_flags 를 각
built-in spec(claude=synthesis/safety, codex=execution/tool, gemini=research/long_context,
ollama=cheap/local/classification)에 실어 `strict-single`/`hybrid`/`optimized` slot
해석과 main-provider 기본값/usage 정책을 굴린다. 사내(enterprise) provider 도 같은
계약의 generic config seam 으로 들어온다. 코드 SSoT 는
`apps/forgekit-console/src/forgekit_console/{providers,policy}/`.

### 9a. usage 관측의 live vs estimate (forgekit #239)

usage_basis 는 `submit_compat` 에 따라 갈린다 — 코드 SSoT 는
`apps/forgekit-console/src/forgekit_console/chat/usage_parse.py`.

| submit_compat | native usage | 결과 |
| --- | --- | --- |
| `openai_compatible` (ollama·OpenAI·gemini-compat) | 응답 `usage` 블록 파싱 | **live** (없으면 estimate degrade) |
| `cli` (claude·codex) | 콘솔 live-submit 미연결 | 측정 대상 아님 (estimate 도 아님) |
| `custom_http` / `native` | 파서 미연결 | estimate |

핵심: **live = provider 가 실제 보고한 토큰**(추정/날조 아님). usage 블록이 없거나 malformed 면
honest estimate(길이 기반)로 degrade 하며 live 와 절대 합산하지 않는다. ledger 가 `usage_basis`
로 둘을 분리 기록하고 `/usage` rollup 이 live_ratio 로 표면화한다.

## 10. 관련
- [`forgekit-provider-policy.md`](forgekit-provider-policy.md) · [`plugin-taxonomy.md`](plugin-taxonomy.md) · [`agent-slash-commands.md`](agent-slash-commands.md) · `GEMINI.md`
- backend/runner: `agents/runners/` · 거버넌스 회귀: `tests/governance/test_provider_capability_taxonomy.py`
