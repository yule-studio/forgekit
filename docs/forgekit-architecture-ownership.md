# ForgeKit 아키텍처 ownership (WT1 감사 SSoT)

> 본 doc 은 **ForgeKit 플랫폼 경계 재정의 리팩터링(WT1~WT4)** 의 ownership SSoT 다.
> WT1 은 **감사 + 문서화** 단계로, 코드 이동은 최소다. 아래 표의 "목표 owner" 는
> 아직 이동하지 않은 항목은 **planned** 으로 정직하게 표기한다(fake migration 금지).
> 실제 이동은 WT2(runtime/provider/config/contracts) · WT3(hephaistos/nexus/armory) ·
> WT4(console 축소 + 검증)에서 진행한다.
>
> 읽기 순서: [`README.md`](../README.md) → [`docs/vision.md`](vision.md) →
> [`docs/monorepo-structure.md`](monorepo-structure.md) → 본 doc.

## 1. 현재 구조가 왜 잘못 읽히는가

ForgeKit 레포에는 **서로 어긋난 두 세계관** 이 공존한다.

- **엔지니어링 모노레포 세계관** ([`docs/monorepo-structure.md`](monorepo-structure.md),
  [`apps/README.md`](../apps/README.md)) — `packages/*` 는 `yule_*` 네이밍(`yule_core`,
  `yule_memory`, `yule_runtime`, `yule_agent_contracts` …), 앱은 engineering / planning /
  discord / memory / loadtest 5 개. **`forgekit-console` 은 이 두 문서 어디에도 등장하지
  않는다.**
- **ForgeKit 플랫폼 세계관** ([`README.md`](../README.md), [`docs/vision.md`](vision.md))
  — ForgeKit = 플랫폼, Hephaistos/Nexus/Armory/runtime/provider = **코어**. 그런데 그
  코어가 **전부 `apps/forgekit-console/src/forgekit_console/` 안에 물리적으로 들어있다**
  (`hephaistos/`, `policy/runtime_mode.py`, `policy/provider_*`, `providers/`, `runtime/`(daemon),
  `chat/`(submit), `autopilot/`, `usage/`, `sources/`+`vault/`(nexus) …).

→ 결과적으로 **`forgekit-console` 이 곧 ForgeKit 플랫폼처럼 읽힌다.** operator app 하나가
플랫폼 엔진 전체를 물리적으로 소유하고 있어서, 나머지 5 개 실행 앱은 ForgeKit 코어를
공유할 수 없다(`packages/* → apps/*` import 는 hard rail 로 금지 — 코어가 앱 안에 갇혀 있으면
다른 앱이 합법적으로 도달할 길이 없다).

### 1.1 다행인 점 — 이건 import 엉킴이 아니라 **packaging/ownership** 위반이다

WT1 import 방향 감사(아래 명령으로 재현 가능)의 결론:

```
# Q1. console 밖에서 forgekit_console.* 를 import 하는가?  → 0건 (console 은 leaf)
grep -rn forgekit_console apps/{engineering-agent,planning-agent,discord-gateway,memory-worker,loadtest-runner} packages
# Q2. console 코어 모듈이 surface(tui/commands)를 import 하는가? → 0건 (상향 누수 없음)
# Q3. surface(tui/commands)가 코어를 import 하는가? → 다수 (정상 방향: surface → core)
```

- **코어는 이미 올바르게 계층화돼 있다** — surface → core 단방향, core → surface 누수 0.
- 즉 위반은 **위치(어느 package 가 소유하는가)** 의 문제이지 **의존 방향** 의 문제가 아니다.
- 따라서 WT2/WT3 는 대부분 `git mv` + namespace + compat shim 이지, 의존 해체가 아니다.

### 1.2 남아있는 진짜 부채 — console → engineering-agent (app→app)

`forgekit-console` 은 `yule_engineering`(engineering-agent 모놀리스)을 **lazy best-effort
import** 로 직접 부른다(없으면 graceful degrade). 이것이 제거 대상 app→app edge 다.

| console 모듈 | 호출하는 yule_engineering 표면 | 용도 | 목표 대체 |
| --- | --- | --- | --- |
| `handoff/gateway.py` | `agents.product_intake.shaping` | intake packet bridge | `packages/agent-contracts` command |
| `lifecycle/failure_escalation.py` | `agents.lifecycle.troubleshooting_{ledger,record}` | 운영 메모리 capture | `packages/agent-contracts` event |
| `data/status_loader.py` | `agents.harness.operator_surface` · `diagnostics.doctor` · `agents.job_queue.*` · `runtime.status` | 대시보드/doctor 데이터 | `agent-contracts` status |

모두 try/except 로 감싼 lazy bridge라 `yule_engineering` 미설치 환경에서도 콘솔은 동작한다.
WT4 에서 "remaining debt" 로 명시 유지하고, 후속에 contracts event/status 로 역전한다.

## 2. 재정의된 경계 — ForgeKit / console / 실행 앱 / packages

```txt
ForgeKit  =  플랫폼(umbrella).  레포 전체.  "엔진은 packages/* 에 있다."

apps/                          ── 실행 유닛. packages/* 만 바라본다. app→app 직접 import 금지.
  forgekit-console/            operator app — 보여주고 조작하는 것만 (TUI/CLI/surface/render)
  engineering-agent/           개발 실행 앱 (intake/계획/deliberation/GitHub)
  planning-agent/              계획·브리핑 실행 앱 (calendar)
  discord-gateway/             Discord transport 앱 (command/event 변환)
  memory-worker/               memory sync/index/retrieval 워커
  loadtest-runner/             검증/부하테스트 앱 (MOCK)

packages/                      ── ForgeKit 코어 + 공용 라이브러리. apps/* 를 import 하지 않는다.
  forgekit-runtime/            runtime loop · daemon · approval/notify · lifecycle · autopilot · runtime_mode
  forgekit-provider/           provider/model routing · usage gate · policy · submit service · brain
  forgekit-config/             config schema · env/persistence · runtime paths · agent identity
  forgekit-contracts/          command/event/status/work-packet schema
  hephaistos/                  skill/loadout/work-packet **forging core** (slash command 아님)
  nexus/                       knowledge source — read/projection/retrieval boundary
  armory/                      skill/loadout/weapon manifest **catalog**
  (기존) core · storage · integrations · memory · llm-gateway · runtime · agent-contracts · …
```

핵심 문장: **`forgekit-console` 은 ForgeKit 을 조작하는 앱이다. ForgeKit 의 실제 엔진은
`packages/*` 아래에 있어야 한다.** 5 개 실행 앱도 서로 import 하는 집합이 아니라
ForgeKit contracts/core 를 **공유**하는 실행 유닛이다.

### 2.1 실행 앱 한 줄 정체성

| 앱 | 한 줄 책임 |
| --- | --- |
| `forgekit-console` | ForgeKit 을 보는 조종석 — operator UI/CLI surface |
| `engineering-agent` | 개발 작업 intake → 코드 작업 실행 |
| `planning-agent` | 일정/계획/브리핑 |
| `discord-gateway` | Discord 입출력 transport |
| `memory-worker` | memory sync/index/retrieval/housekeeping |
| `loadtest-runner` | runtime/memory/backend 부하 검증(MOCK) |

### 2.2 네이밍 — 기존 conventions 와의 정합

`packages/<dir>/src/<python_pkg>` 규칙은 그대로 따른다. ForgeKit 코어는 legacy `yule_*`
인프라와 **구분되는 정체성** 이므로 `forgekit_*` python 패키지로 둔다(사용자 명명 기준).

- `packages/runtime`(`yule_runtime` = circuit_breaker/subprocess 프리미티브) **≠**
  `packages/forgekit-runtime`(`forgekit_runtime` = daemon/serve loop/runtime_mode).
  후자는 전자의 **프리미티브를 의존할 수 있으나 복제하지 않는다.**
- `packages/agent-contracts`(`yule_agent_contracts` = agent↔agent 메시지) 와
  `packages/forgekit-contracts`(`forgekit_contracts` = operator command/event/work-packet)
  는 레이어가 다르다. WT2 에서 중복 여부를 재확인하고, 겹치면 forgekit-contracts 가
  agent-contracts 를 re-export/의존하는 방향으로 합친다(복제 금지).

## 3. Ownership 매트릭스 — console 모듈 → 현재/목표 owner

> 현재 owner 는 전부 `apps/forgekit-console`. "목표 owner" 가 `apps/forgekit-console` 이
> 아닌 행이 이전 대상. 상태: **planned** = 아직 미이동(WT1 시점 전부 planned).

### 3.1 console 에 **남는** surface (보여주고 조작하는 것만)

| 모듈 | LOC | 책임 | 목표 owner |
| --- | ---: | --- | --- |
| `app/` | 137 | CLI/TUI entrypoint (`forgekit`) | console (유지) |
| `cli/` | 249 | `forgekit` 서브커맨드 진입점 | console (유지, core 호출만) |
| `tui/` | 6390 | TUI 렌더 + composer/palette/transcript/process feed | console (유지) |
| `commands/` | 673 | command registry + router (**얇은** operator surface) | console (유지) |
| `assets/` | 208 | avatar/brand PNG | console (유지) |
| `uiref/` | 116 | UI reference | console (유지) |
| `proc_identity.py` | 145 | 터미널 타이틀/proc name (UI 관심사) | console (유지) |

### 3.2 console 에서 **빠지는** core → packages (WT2: runtime/provider/config/contracts)

| 모듈 | LOC | 책임 | 목표 owner | 상태 |
| --- | ---: | --- | --- | --- |
| `policy/{provider_config,provider_ops,provider_policy,provider_surface,routing,recommend,main_profile,usage_policy,setup_state,auto_mode}` | ~1700 | provider routing/usage gate/policy/setup | `packages/forgekit-provider` (`forgekit_provider.policy`) | **done** (옛 경로 shim) |
| `providers/` (builtins/contract/registry) | 519 | provider spec 카탈로그 | `packages/forgekit-provider` (`forgekit_provider.providers`) | **done** (옛 경로 shim) |
| `chat/` (service/models/policy_gate/usage_parse) | 679 | submit service (routing → 실호출) | `packages/forgekit-provider` (`forgekit_provider.chat`) | **done** (옛 경로 shim) |
| `usage/` (ledger …) | 478 | usage ledger (live vs estimate) | `packages/forgekit-provider` (`forgekit_provider.usage`) | **done** (옛 경로 shim) |
| `brain/` | 515 | brain(=primary+linked) 구성/preset | `packages/forgekit-provider` (`forgekit_provider.brain`) | **done** (옛 경로 shim) |
| `policy/runtime_mode.py` | ~300 | runtime mode → 실 routing/budget/approval | `packages/forgekit-runtime` (현재 `forgekit_provider.policy.runtime_mode` 경유) | planned (runtime 추출 시 재배치) |
| `runtime/` (daemon/loop/autopilot_tick/heartbeat/runbook/surface) | 780 | always-on bounded daemon core | `packages/forgekit-runtime` | planned |
| `autopilot/` | 1012 | safe-class autopilot core | `packages/forgekit-runtime` | planned |
| `lifecycle/` | 511 | 실패 escalation / 운영 메모리 bridge | `packages/forgekit-runtime` | planned |
| `selfimprove/` | 190 | self-improvement loop | `packages/forgekit-runtime` | planned |
| `notify/` | 336 | approval/alert inbox (operator action) | `packages/forgekit-runtime` | planned |
| `security/` | 239 | red/blue 계획(plan-only) | `packages/forgekit-runtime` | planned |
| `runtime_paths.py` | 78 | `~/.forgekit` 경로 해석 | `packages/forgekit-config` (`forgekit_config.paths`) | **done** (옛 경로 compat shim) |
| `identity/` | 409 | agent identity(git author/app) | `packages/forgekit-config` | planned |
| `data/status_loader.py` | 211 | 대시보드 데이터 bridge(→yule_engineering) | `packages/forgekit-config` adapter + console (bridge 잔존 debt) | planned |
| `models.py` | 137 | work packet/command/event 모델 | `packages/forgekit-contracts` | planned |
| `handoff/` | 396 | intake packet bridge | `packages/forgekit-contracts` (+ agent-contracts) | planned |

### 3.3 console 에서 빠지는 core → packages (WT3: hephaistos/nexus/armory)

| 모듈 | LOC | 책임 | 목표 owner | 상태 |
| --- | ---: | --- | --- | --- |
| `hephaistos/` (resolve/equip/forge/models, **minus** nexus_read·armory) | ~900 | request → equip plan **forging core** | `packages/hephaistos` | planned |
| `hephaistos/nexus_read.py` + `sources/` + `vault/` + `discovery/` + `design/` | ~1700 | knowledge source read/projection/retrieval boundary | `packages/nexus` | planned |
| `hephaistos/armory.py` (+ manifests) | ~500 | skill/loadout/weapon **catalog** | `packages/armory` | planned |

> Hephaistos 는 ForgeKit **내부 코어**이지 slash command 하나가 아니다. Nexus 는 knowledge
> **boundary**, Armory 는 **catalog**. console 은 이 셋의 **projection/surface** 만 가진다
> (`/resolve` `/hephaistos` `/nexus` `/skills` `/loadout` 렌더).

## 4. import 경계 — 현재 vs 목표

### 4.1 현재 (WT1 시점)

```
apps/forgekit-console/src/forgekit_console/
  tui, commands  ──▶  policy, providers, chat, usage, runtime, autopilot,
                      hephaistos, sources, vault, discovery, lifecycle, notify, …
                      (surface → core, 전부 console 패키지 내부)
  handoff, lifecycle, data  ──▶  yule_engineering.*   (app→app, lazy best-effort)

다른 5개 앱  ──X──▶  forgekit_console.*   (0건 — 코어가 console 안에 갇힘)
```

### 4.2 목표 (WT4 완료 시)

```
apps/forgekit-console
  ──▶ packages/forgekit-runtime, forgekit-provider, forgekit-config,
      forgekit-contracts, hephaistos, nexus, armory

apps/{engineering,planning,discord,memory,loadtest}-agent
  ──▶ packages/*   (동일 코어 공유)

app ──X──▶ app   (직접 import 제거, 불가피한 잔여는 contracts 경유 + debt 명시)
packages/* ──X──▶ apps/*   (역방향 금지, 기존 hard rail 유지)
```

## 5. Migration roadmap (WT2~WT4) + 우선순위

| WT | 범위 | 산출물 | 검증 |
| --- | --- | --- | --- |
| **WT2** | `forgekit-provider`(provider/policy/usage/chat/brain) · `forgekit-runtime`(runtime/autopilot/lifecycle/notify/runtime_mode) · `forgekit-config`(paths/identity) · `forgekit-contracts`(models/handoff) 추출 | 4 package + console 옛 경로 compat shim | import smoke + 기존 테스트 green |

> **WT2 진행:**
> - `forgekit-config`(1차) — `runtime_paths.py` → `forgekit_config.paths`. 옛 경로
>   `forgekit_console.runtime_paths` 는 `sys.modules` alias shim.
> - `forgekit-provider`(2차) — `providers/policy/chat/usage/brain`(5 dir, ~3900 LOC) →
>   `forgekit_provider.*`. 옛 5 경로는 `_compat.alias_package`(package+서브모듈 객체 동일성)
>   shim. 핵심: intra-package relative import(`policy→providers`, `chat→policy/providers`)는
>   이동만으로 그대로 동작했고, 유일한 outward dep 인 `runtime_paths` 4곳만 `forgekit_config.paths`
>   로 absolute 화. 외부 dep 은 `forgekit-config` 하나뿐.
>
> 분리 패턴(템플릿): package 생성 → `git mv` → 옛 경로 shim(`_compat.alias_package`) →
> root `pyproject` where 등록 → editable 재설치 → root `tests/` 회귀. runtime/contracts 추출에
> 동일 적용. 검증: root CI 6571 OK, console 서브셋 766 OK, provider standalone import OK.
| **WT3** | `hephaistos`(forge core) · `nexus`(sources/vault/discovery/design/nexus_read) · `armory`(catalog) 승격 | 3 package + console 은 projection 만 | resolve/nexus/armory 테스트 green |
| **WT4** | console 을 surface/TUI/CLI/render 로 축소 · app dependency 방향 최종 점검 · examples/docs/QA | import 방향 검증 스크립트 + evidence | 전체 suite green |

**이동 우선순위(가장 먼저):**
1. `forgekit-provider` — 최근 setup/persistence 작업이 집중된 표면, 변경 빈도 최고, 다른
   코어가 가장 많이 의존(Q3 에서 `policy` 9 / `usage` 6 / `chat` 6).
2. `forgekit-config` + `forgekit-contracts` — provider/runtime 의 공통 기반(paths/models).
3. `forgekit-runtime` — daemon/autopilot/lifecycle/notify, runtime_mode 정책.

## 6. 남은 debt (WT1 시점 정직 표기)

- **app→app**: `forgekit-console → yule_engineering`(§1.2 3 모듈) — lazy best-effort,
  contracts 로 대체 예정. WT4 까지 "remaining debt" 로 명시 유지.
- **packages 네이밍 정합**: `forgekit-contracts` ↔ 기존 `agent-contracts`,
  `forgekit-runtime` ↔ 기존 `runtime` 의 책임 경계 — WT2 착수 시 중복 재확인(복제 금지).
- **engineering-agent 모놀리스 분해**(`docs/monorepo-structure.md §4`)는 본 리팩터와 별
  트랙. 단, console→yule_engineering edge 제거는 두 트랙의 공통 지점.

## 7. 동기화한 문서

본 WT1 에서 같은 세계관으로 맞춘 문서:

- [`apps/README.md`](../apps/README.md) — `forgekit-console` 을 operator app 으로 추가,
  ForgeKit umbrella + 6 앱 + packages 코어 경계 명시.
- [`docs/monorepo-structure.md`](monorepo-structure.md) — forgekit-* 코어 package 목표와
  console 의 현재/목표 owner 반영.
- [`docs/vision.md`](vision.md) · [`README.md`](../README.md) — "엔진은 packages/* 에
  있다(현재 console 내부, 이전 진행 중)" 를 본 doc 으로 cross-link.

> WT1 은 문서+inventory 중심이고 코드 이동은 0 이다. 위 표의 모든 "목표 owner ≠ console"
> 항목은 **planned** 이며, 실제 `git mv` 는 WT2~WT4 에서 일어난다.
