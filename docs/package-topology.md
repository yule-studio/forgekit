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
  │  Armory     = 카탈로그: skill/loadout/weapon/tool registry       │  packages/armory (RWT2)
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

## 3. 현재 package 분류표 (19개)

> 의존(dep)은 **실측 import 기준**(선언 dep 아님). 상태: keep / merge-into / rename /
> deprecate / compat-shim. 위험도는 정리 시 import 깨질 위험.

### 3.1 ForgeKit platform core — 전부 **keep**
| package | python | 역할 | 의존 | 주 소비자 |
| --- | --- | --- | --- | --- |
| `forgekit-config` | `forgekit_config` | runtime paths + agent identity | (leaf) | console, 모든 forgekit-* core |
| `forgekit-contracts` | `forgekit_contracts` | command-result·work-packet schema | (leaf) | forgekit-runtime |
| `forgekit-provider` | `forgekit_provider` | provider routing·usage·policy·chat·brain | forgekit-config | console, forgekit-runtime |
| `forgekit-provider-connect` | `forgekit_provider_connect` | provider **onboarding/connect** (CLI attach·API key·daemon 진단 + `/setup` wizard) — control-plane P0 | forgekit-provider·forgekit-config | console (`/setup`·`/provider connect`) |
| `forgekit-toolchain` | `forgekit_toolchain` | language/runtime **버전 전환** (repo-local 감지·loadout→profile·mise switch/verify/drift, global/install 승인 게이트) — control-plane P0 | forgekit-config·armory | console (`/toolchain`) |
| `forgekit-runtime` | `forgekit_runtime` | bounded loop·daemon·autopilot·lifecycle·notify | config·contracts·provider·nexus | console |
| `forgekit-goal` | `forgekit_goal` | long-term **goal plane** (goal model·status transition·append-only evidence·packet/child linkage·persistent store) — control-plane spine (GW1) | forgekit-config | forgekit-runtime (tick — GW4)·console (`/goal` — GW5) |

### 3.2 ForgeKit named cores — **keep**
| package | python | 역할 | 의존 | 주 소비자 |
| --- | --- | --- | --- | --- |
| `hephaistos` | `hephaistos` | skill-forge / resolve / loadout selection / work-packet (forge-output 타입 소유) | forgekit-config, **armory** | console |
| `nexus` | `nexus` | knowledge source read / vault / discovery | forgekit-config | console, forgekit-runtime |
| `armory` | `armory` | skill/loadout/weapon **catalog** + spec vocabulary(`armory.models`/`armory.catalog`). **leaf** | (leaf) | hephaistos (console via shim) |

### 3.3 shared infra — **keep** (역할 명확화)
| package | python | 역할 | 의존 | 주 소비자 |
| --- | --- | --- | --- | --- |
| `core` | `yule_core` | env / timezone / TLS / context util (pure leaf) | (leaf) | eng·planning·discord |
| `storage` | `yule_storage` | sqlite json-cache / calendar-state / task-history (**persistence leaf**) | (leaf) | eng·planning·discord |
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
| `runtime-primitives` | `yule_runtime_primitives` | runtime **primitives** (circuit-breaker/subprocess/service). **renamed** from `runtime`/`yule_runtime` (compat shim kept) | (leaf) | eng (옛 `yule_runtime` 경로 경유, 점진 전환) |
| `learning` | `yule_learning` | mistake_ledger / preflight (운영 메모리) | (leaf) | eng |

## 4. 네이밍 충돌 — 이름만 보면 헷갈리는 지점 (정직)

세 묶음이 이름이 겹쳐 "무엇이 무엇인지" 모호했다. **현재 라운드에서 rename 하지 않고**
의미를 문서로 못박는다(rename 은 §5 후속 candidate).

| 충돌 | 패키지들 | 실제 의미 (다름) |
| --- | --- | --- |
| **3× runtime** (해소됨) | `forgekit-runtime` / `agent-runtime` / ~~`runtime`(`yule_runtime`)~~ → **`runtime-primitives`(`yule_runtime_primitives`)** | ForgeKit 실행 코어 / 에이전트 decide-recall 루프 / 저수준 primitive(circuit-breaker·subprocess). shared-infra 쪽을 rename 해 이름만으로 구분됨(옛 `yule_runtime` 은 compat shim) |
| **2× contracts** | `forgekit-contracts` / `agent-contracts` | operator/console command-result·work-packet / agent↔agent command·event·status |
| **2× memory** | `memory`(`yule_memory`) / `agent-memory`(`yule_agent_memory`) | SQLite/FTS5 검색 index / 에이전트 long-term relevance·topic |

## 5. Migration matrix — 조치 / 위험도 / 후속

| package | 조치(이번) | 후속 candidate | 위험도 | 근거 |
| --- | --- | --- | --- | --- |
| forgekit-config/contracts/provider/runtime | **keep** | — | — | ForgeKit core, 깨끗한 DAG |
| hephaistos | **keep** | — (armory 분리 완료) | — | forge-output 타입만 보유; catalog vocab 은 armory |
| armory | **created (done)** | console importer 점진 전환(현재 shim 경유) | 중 | `models.py` 분할로 hephaistos↔armory 순환 회피, hephaistos→armory 단방향 |
| nexus | **keep** | discovery/design/nexus_read 흡수 | 중 | handoff/uiref/selfimprove 분리 선행 |
| armory | **create(planned)** | hephaistos.armory → packages/armory | 중 | hephaistos↔armory 순환 회피 위해 models 분할 선행 |
| core/security/llm-gateway | **keep** | — | 낮 | leaf, 역할 명확 |
| vcs | **keep** | pyproject.toml 추가(TWT2) | 낮 | 유일하게 pyproject.toml 없음 — standalone 빌드 불가 |
| storage / integrations | **keep** (decoupled) | — | — | **cycle 제거됨(RWT3)**: storage 가 `calendar_contract` 의 structural Protocol 을 선언 → integrations 의존 0. `integrations→storage` 단방향 |
| memory | **keep** | `agent-memory` 와 역할 문구 분리 유지 | 낮 | 이름 충돌만, 책임은 다름 |
| runtime → **runtime-primitives** (`yule_runtime_primitives`) | **renamed (done)** | engineering-agent importer 점진 전환(현재 compat shim 경유) | 중 | 3× runtime 충돌 해소. `yule_runtime` compat shim(sys.modules alias) 유지 |
| agent-contracts/agent-memory/agent-runtime/learning | **keep (transitional)** | engineering-agent 코어 분해와 함께 정리 | 낮 | engineering-agent 산하 추출 infra, 아직 활발히 사용 |

> **TWT 라운드(topology audit) rename/merge/deprecate = 0** — 전부 keep + 문서 명확화.
> **후속 RWT 라운드(2차 개편)에서 실제 rename 시작:** `runtime`→`runtime-primitives`
> (`yule_runtime`→`yule_runtime_primitives`) 완료, compat shim(`yule_runtime`) 유지 +
> engineering-agent importer 점진 전환. broad mechanical rename 은 여전히 금지(compat-first).

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
 │     └─ skill/tool catalog            → packages/armory (armory.catalog / armory.models)
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
- ~~**storage ↔ integrations soft cycle**~~ — **해소됨(RWT3)**: storage 의 TYPE_CHECKING
  integrations import 를 `yule_storage.calendar_contract` 의 structural Protocol(interface
  extraction)로 대체. storage 는 persistence leaf(의존 0), integrations 는 외부 adapter 로
  storage 를 단방향 의존.
- **agent-\* / learning / runtime(yule_runtime)** — engineering-agent 코어 분해
  ([`monorepo-structure.md §4`](monorepo-structure.md))와 함께 정리될 transitional 군.
- **2× contracts / 2× memory 네이밍 충돌** — 문서로 못박았고, rename 은 후속.
  (**3× runtime 은 RWT 라운드에서 해소** — `runtime`→`runtime-primitives`.)

## 8. 라운드 요약 (정직)

**TWT 라운드 (topology audit):**
- 실제 정리된 package: 없음(코드 이동 0) — audit + 분류표 + migration matrix.

**RWT 라운드 (2차 개편, 진행 중):**
- **실제 rename 된 package**: `runtime`→`runtime-primitives`
  (`yule_runtime`→`yule_runtime_primitives`), `yule_runtime` compat shim 유지.
- transitional/debt 로 남긴 package: agent-contracts/agent-memory/agent-runtime/learning + §7 debt.
- **새로 독립한 package**: `armory`(`hephaistos.armory` + catalog vocab → `packages/armory`,
  hephaistos→armory 단방향, compat shim 유지).
- **경계 정리된 shared infra**: `storage ↔ integrations` cycle 제거(storage=persistence leaf,
  integrations=adapter, 단방향). interface extraction(`calendar_contract` Protocol).

## 9. `forge-*` vs `forgekit-*` 네이밍 결정 (#462)

> #461(1차 폴더링)이 `packages/forge-*` 5개 + `apps/forge-*` 3개 **placeholder** 를 추가하면서
> 플랫폼 package prefix 가 **이원화**됐다 — 기존 real prefix 는 `forgekit-*`(7 package +
> `forgekit-console`). 본 절이 그 단일 기준 SSoT 다. **이번 작업은 문서 결정만 —
> 실제 rename/delete/코드 이동은 하지 않는다(후속 candidate).**

### 9.1 결정 — canonical prefix = `forgekit-*`
- 플랫폼 package/app 의 정식 식별자 prefix 는 **`forgekit-*` 하나**다. 이미 7개 real package +
  `forgekit-console` 가 그 이름으로 populated 다.
- `forge-*` 를 정식 prefix 로 채택하려면 real package 7개를 mechanical rename 해야 하는데,
  이는 본 doc 의 **broad mechanical rename 금지**(§5·§8, compat-first)에 정면 위배된다.
  비용 큰 쪽(7 real)을 건드리는 대신 비용 0 쪽(8 placeholder)을 정리하는 게 lazy·correct.
- 따라서 **`forge-*` placeholder 는 그 이름으로 구현하지 않는다.** 구현 시 `forgekit-*` 로
  만들거나 기존 package/`forgekit-console`/`armory` 로 fold 한다.
- `forge`/대장간 메타포는 **컨셉·문서·디렉터리 서사**(예: [foldering.md](foldering.md))에는 계속
  쓰되, **package/app 식별자 prefix 로는 `forgekit-*` 만** 쓴다.

### 9.2 placeholder disposition 표
> 실제 rename/delete 는 후속 PR. 지금은 placeholder + 본 표(결정 근거)만.

**packages/forge-\***
| placeholder | 의도 역할 | 기존 등가물 | disposition | canonical 목표 |
| --- | --- | --- | --- | --- |
| `forge-core` | 플랫폼 코어 골격 | `forgekit-config` + `forgekit-contracts` (base) | **redundant** | 없음(config/contracts 가 base) |
| `forge-policy` | autonomy/approval 정책 | (분산: governance 코드) | **net-new role** | `forgekit-policy` |
| `forge-workspace` | workspace 추상화 | (`forgekit-config` paths 일부) | **net-new role** | `forgekit-workspace` |
| `forge-registry` | skill/tool registry | **`armory`** (catalog) | **overlap → armory fold** | `armory` |
| `forge-runtime` | 실행 런타임 | **`forgekit-runtime`** (real) | **collision/redundant** | `forgekit-runtime` |

**apps/forge-\***
| placeholder | 의도 역할 | 기존 등가물 | disposition |
| --- | --- | --- | --- |
| `forge-cli` | operator CLI | `forgekit-console` (CLI/TUI surface) | **overlap → console** |
| `forge-daemon` | always-on daemon | `forgekit-runtime` daemon + `memory-worker` | **overlap** |
| `forge-dashboard` | operator dashboard | `forgekit-console` | **redundant → console** |

### 9.3 후속 구현 전 규칙 (지금부터 적용)
- **새 package/app 은 `forgekit-*` 로만 만든다.** `forge-*` 신규 생성 금지.
- net-new 역할(`policy`/`workspace`)이 실제로 필요해지면 `forgekit-policy`/`forgekit-workspace`
  로 만들고, 대응 `forge-*` placeholder 는 그때 삭제(후속 PR).
- redundant placeholder(`forge-core`/`forge-runtime`/`forge-registry`/`forge-cli`/`forge-daemon`/
  `forge-dashboard`)는 **구현 코드를 placeholder 에 넣지 않는다** — 기존 등가물로 fold.
- [foldering.md](foldering.md) 의 서사적 `forge-*` 언급은 컨셉 레벨이며, **식별자 기준은 본 절이
  우선**한다.
