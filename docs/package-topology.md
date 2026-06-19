# Package topology (packages/* 분류 + migration matrix)

> 본 doc 은 `packages/*` 의 **책임 분류·네이밍·의존 경계·migration 방향** SSoT 다.
> "어떤 게 ForgeKit 코어이고, 어떤 게 공용 infra 이고, 어떤 게 과도기 호환층인지"를
> 한 화면에 정리한다. WT1(topology audit) 단계의 산출물로 **코드 이동은 최소**이며,
> rename/merge 는 전부 후속 candidate 로 표기한다(거짓 정리 금지).
>
> 읽기 순서: [`README.md`](../README.md) → [`docs/vision.md`](vision.md) →
> [`docs/monorepo-structure.md`](monorepo-structure.md) →
> [`docs/forgekit-architecture-ownership.md`](forgekit-architecture-ownership.md) → 본 doc.

## 1. app vs package — 무엇을 어디에 두는가

| | `apps/*` | `packages/*` |
| --- | --- | --- |
| 정체 | **실행 가능한 product/app/worker** (진입점·런타임 보유) | **재사용 코어/라이브러리** (진입점 없음) |
| import 방향 | `apps/* → packages/*` 가능 | **`packages/* → apps/*` 금지**(hard rail) |
| 서로 | app→app 직접 import 지양(가능하면 packages 경유) | package→package 는 단방향 DAG |
| 예 | forgekit-console, engineering-agent, planning-agent, discord-gateway, memory-worker, loadtest-runner | forgekit-config, hephaistos, nexus, core, storage … |

**새 기능을 어디에 둘지 판단 기준** (§6 결정 트리 참조 요약):
- 실행 진입점/데몬/워커/operator surface → **app**.
- 여러 app·package 가 공유하는 순수 로직/스키마/클라이언트 → **package**.
- ForgeKit 플랫폼 중심(provider/mode/runtime/setup/forge/knowledge) → **forgekit-\* / named core package**.
- engineering-agent 한정 도메인 → 당장은 `apps/engineering-agent`, 공유성이 생기면 package 승격.

## 2. ForgeKit / Hephaistos / Nexus / Armory / shared infra 관계

```
ForgeKit = 플랫폼(대장간).  packages/* 코어 + apps/* 실행 유닛의 조합.
           forgekit-console 은 그 operator app 일 뿐, 플랫폼 전체가 아니다.

  ┌─ ForgeKit platform core ──────────────────────────────┐
  │  forgekit-config  (paths·identity)                     │  ← 가장 낮은 base
  │  forgekit-contracts (command-result·work-packet schema)│
  │  forgekit-provider (routing·usage·policy·chat·brain)   │
  │  forgekit-runtime  (loop·daemon·autopilot·lifecycle)   │
  └────────────────────────────────────────────────────────┘
  ┌─ ForgeKit named cores (대장간의 도구) ─────────────────┐
  │  Hephaistos = 대장장이: resolve/skill-forge/loadout/work-packet  │
  │  Nexus      = 광산/도서관: read/retrieval/source ref/projection  │
  │  Armory     = 카탈로그: skill/loadout/weapon/tool registry       │  ← 아직 hephaistos.armory
  └────────────────────────────────────────────────────────┘
  ┌─ shared infra (플랫폼 아래 공용 기반) ─────────────────┐
  │  core · storage · integrations · vcs · security · llm-gateway · memory │
  └────────────────────────────────────────────────────────┘
  ┌─ transitional / agent-coupled (engineering-agent 산하 추출 infra) ─┐
  │  agent-contracts · agent-memory · agent-runtime · learning · runtime │
  └────────────────────────────────────────────────────────┘

소비 관계(실측):
  forgekit-console → forgekit-config·forgekit-provider·hephaistos·nexus (+runtime/contracts via shim)
  engineering-agent → (legacy yule_*) agent-contracts·agent-memory·agent-runtime·core·
                       integrations·learning·memory·runtime·security·storage·vcs
  planning-agent   → core·integrations·storage
  discord-gateway  → agent-runtime·core·integrations·storage·vcs
```

> **핵심:** ForgeKit 코어(`forgekit-*` / `hephaistos` / `nexus`)는 **operator app(console)** 이
> 쓰고, legacy `yule_*` 패키지는 **engineering-agent 계열**이 쓴다. 둘 다 `packages/*` 에 같이
> 살고 이름이 겹쳐서(아래 §4) "무엇이 ForgeKit 코어인지" 모호했던 것이다.

## 3. 현재 package 분류표 (18개)

> 의존(dep)은 **실측 import 기준**(선언 dep 아님). 상태: keep / merge-into / rename /
> deprecate / compat-shim. 위험도는 정리 시 import 깨질 위험.

### 3.1 ForgeKit platform core — 전부 **keep**
| package | python | 역할 | 의존 | 주 소비자 |
| --- | --- | --- | --- | --- |
| `forgekit-config` | `forgekit_config` | runtime paths + agent identity | (leaf) | console, 모든 forgekit-* core |
| `forgekit-contracts` | `forgekit_contracts` | command-result·work-packet schema | (leaf) | forgekit-runtime |
| `forgekit-provider` | `forgekit_provider` | provider routing·usage·policy·chat·brain | forgekit-config | console, forgekit-runtime |
| `forgekit-runtime` | `forgekit_runtime` | bounded loop·daemon·autopilot·lifecycle·notify | config·contracts·provider·nexus | console |

### 3.2 ForgeKit named cores — **keep** (armory 는 planned)
| package | python | 역할 | 의존 | 주 소비자 |
| --- | --- | --- | --- | --- |
| `hephaistos` | `hephaistos` | skill-forge / resolve / loadout / work-packet (+ `armory` 내부) | forgekit-config | console |
| `nexus` | `nexus` | knowledge source read / vault / discovery | forgekit-config | console, forgekit-runtime |
| `armory` | (planned) | skill/loadout/weapon **catalog** | — | (현재 `hephaistos.armory`; §5) |

### 3.3 shared infra — **keep** (역할 명확화)
| package | python | 역할 | 의존 | 주 소비자 |
| --- | --- | --- | --- | --- |
| `core` | `yule_core` | env / timezone / TLS / context util (pure leaf) | (leaf) | eng·planning·discord |
| `storage` | `yule_storage` | sqlite json-cache / calendar-state / task-history | integrations(lazy) | eng·planning·discord |
| `integrations` | `yule_integrations` | Naver CalDAV calendar + GitHub | storage | eng·planning·discord |
| `security` | `yule_security` | paste guard / secret 차단 | (leaf) | eng |
| `vcs` | `yule_vcs` | git/VCS 헬퍼 ⚠ **pyproject.toml 아예 없음**(유일, standalone 빌드 불가 — TWT2 추가) | (leaf) | eng·discord |
| `llm-gateway` | `yule_llm_gateway` | LLM provider 호출 게이트웨이 + token budget | (leaf) | (저사용; eng 후속 배선) |
| `memory` | `yule_memory` | local-first SQLite/FTS5 memory **index** | (leaf) | eng |

### 3.4 transitional / agent-coupled — **keep (transitional 표기)**
| package | python | 역할 | 의존 | 주 소비자 |
| --- | --- | --- | --- | --- |
| `agent-contracts` | `yule_agent_contracts` | **agent↔agent** command/event/status/message | (leaf) | eng |
| `agent-memory` | `yule_agent_memory` | 에이전트 long-term memory (relevance/topic-index) | (leaf) | eng |
| `agent-runtime` | `yule_agent_runtime` | 에이전트 runtime 루프 (decide/recall/understand) | (leaf) | eng·discord |
| `runtime` | `yule_runtime` | runtime **primitives** (circuit-breaker/subprocess/service) | (leaf) | eng |
| `learning` | `yule_learning` | mistake_ledger / preflight (운영 메모리) | (leaf) | eng |

## 4. 네이밍 충돌 — 이름만 보면 헷갈리는 지점 (정직)

세 묶음이 이름이 겹쳐 "무엇이 무엇인지" 모호했다. **현재 라운드에서 rename 하지 않고**
의미를 문서로 못박는다(rename 은 §5 후속 candidate).

| 충돌 | 패키지들 | 실제 의미 (다름) |
| --- | --- | --- |
| **3× runtime** | `forgekit-runtime` / `agent-runtime` / `runtime`(`yule_runtime`) | ForgeKit 실행 코어 / 에이전트 decide-recall 루프 / 저수준 primitive(circuit-breaker·subprocess) |
| **2× contracts** | `forgekit-contracts` / `agent-contracts` | operator/console command-result·work-packet / agent↔agent command·event·status |
| **2× memory** | `memory`(`yule_memory`) / `agent-memory`(`yule_agent_memory`) | SQLite/FTS5 검색 index / 에이전트 long-term relevance·topic |

## 5. Migration matrix — 조치 / 위험도 / 후속

| package | 조치(이번) | 후속 candidate | 위험도 | 근거 |
| --- | --- | --- | --- | --- |
| forgekit-config/contracts/provider/runtime | **keep** | — | — | ForgeKit core, 깨끗한 DAG |
| hephaistos | **keep** | `armory` 분리 시 models 분할 | 중 | `models.py` = armory-type + forge-output-type 혼재 |
| nexus | **keep** | discovery/design/nexus_read 흡수 | 중 | handoff/uiref/selfimprove 분리 선행 |
| armory | **create(planned)** | hephaistos.armory → packages/armory | 중 | hephaistos↔armory 순환 회피 위해 models 분할 선행 |
| core/security/llm-gateway | **keep** | — | 낮 | leaf, 역할 명확 |
| vcs | **keep** | pyproject.toml 추가(TWT2) | 낮 | 유일하게 pyproject.toml 없음 — standalone 빌드 불가 |
| storage / integrations | **keep** | storage→integrations lazy edge 제거 | 낮 | 둘 사이 soft cycle(storage 쪽은 함수내 lazy) |
| memory | **keep** | `agent-memory` 와 역할 문구 분리 유지 | 낮 | 이름 충돌만, 책임은 다름 |
| runtime (`yule_runtime`) | **keep** | **rename candidate** → `runtime-primitives` 류 | 중 | 3× runtime 충돌의 핵심. importer 마이그레이션 + compat shim 필요 |
| agent-contracts/agent-memory/agent-runtime/learning | **keep (transitional)** | engineering-agent 코어 분해와 함께 정리 | 낮 | engineering-agent 산하 추출 infra, 아직 활발히 사용 |

> **이번 라운드 rename/merge/deprecate = 0.** 전부 keep + 문서 명확화. 실제 rename(특히
> `yule_runtime`)은 compat shim + importer 전환을 동반하는 후속 PR 로만 진행한다(broad
> mechanical rename 금지).

## 6. 어디에 새 기능을 추가하는가 — 결정 트리

```
새 기능/모듈이 필요하다
 ├─ 실행 진입점(CLI/daemon/worker/TUI)인가?            → apps/<app>
 ├─ operator 가 보고/조작하는 surface 인가?            → apps/forgekit-console
 ├─ provider/mode/usage/setup/forge/knowledge 등
 │   ForgeKit 플랫폼 중심 코어인가?
 │     ├─ provider/policy/usage/submit  → packages/forgekit-provider
 │     ├─ loop/daemon/autopilot/notify  → packages/forgekit-runtime
 │     ├─ paths/identity/config         → packages/forgekit-config
 │     ├─ command-result/work-packet    → packages/forgekit-contracts
 │     ├─ resolve/skill/loadout/packet  → packages/hephaistos
 │     ├─ source read/retrieval/vault   → packages/nexus
 │     └─ skill/tool catalog            → (armory; 현재 hephaistos.armory)
 ├─ 여러 app 이 공유하는 순수 infra(http/sqlite/git/계산)인가? → packages/<shared infra>
 └─ engineering-agent 한정 도메인인가?
       └─ 당장은 apps/engineering-agent, 공유성이 생기면 package 승격
```
규칙: **packages 는 apps 를 import 하지 않는다.** app 연결이 필요하면 contracts(이벤트/커맨드)
또는 seam(주입)으로 푼다 — 예: forgekit-runtime 의 handoff seam(app 이 runner 주입).

## 7. 남은 transitional debt (숨기지 않음)

- **forgekit-runtime → yule_engineering(app)** — `lifecycle._bridge_troubleshooting` 의 운영
  메모리 미러. best-effort lazy(import-clean, 없으면 no-op). → agent-contracts event 로 역전 예정.
- **forgekit-console → yule_engineering(app)** — handoff intake / status·doctor dashboard
  bridge(lazy). → agent-contracts command/status 로 역전 예정.
- **storage ↔ integrations soft cycle** — storage 쪽 edge 는 함수내 lazy. 제거 후속.
- **agent-\* / learning / runtime(yule_runtime)** — engineering-agent 코어 분해
  ([`monorepo-structure.md §4`](monorepo-structure.md))와 함께 정리될 transitional 군.
- **3× runtime / 2× contracts / 2× memory 네이밍 충돌** — 문서로 못박았고, rename 은 후속.

## 8. 이번 라운드 요약 (정직)

- **실제 정리된 package**: 없음(코드 이동 0). 본 라운드는 **audit + 분류표 + migration matrix**.
- **transitional/debt 로 남긴 package**: agent-contracts/agent-memory/agent-runtime/learning/
  runtime(yule_runtime) + 위 §7 debt.
- **다음 라운드 merge/rename 후보**: `runtime`(`yule_runtime`)→`runtime-primitives`(rename),
  `armory` 분리(create), storage↔integrations cycle 제거(deprecate edge).
