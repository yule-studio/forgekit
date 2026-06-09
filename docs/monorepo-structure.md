# Monorepo 구조 (현황 + 로드맵)

> 본 doc 은 monorepo 의 **달성된 구조** 와 **남은 로드맵** 의 운영자 SSoT 다.
> Agent Town UI 레포 분리에 따라 본 레포를 "에이전트 백엔드 플랫폼" 으로
> 정리하는 작업의 현재 상태를 담는다.

## 1. 현재 구조 (달성)

```
packages/    공용 라이브러리 — apps/* 와 서로가 의존. apps/* 를 import 하지 않는다.
  agent-contracts/   command/event/status/message/role/task 계약 (stdlib-only)
  core/              env / timezone / tls / context-loading 유틸 (leaf)
  storage/           SQLite 캐시 / calendar-state / task-history
  integrations/      naver calendar / github(gh) 통합  → storage 의존(런타임 단방향)
  memory/            검색 / 인덱싱 / FTS5 / vault 문서 처리
  llm-gateway/       LLM 요청 게이트웨이 + token budget + prompt cache (최소 인터페이스)
  runtime/           circuit_breaker / services / subprocess_supervisor 프리미티브

apps/        실행 단위 — 좁은 책임. packages/* 를 의존. 다른 app 을 직접 import 하지 않는다(목표).
  planning-agent/    일정 / 계획 / 브리핑          → 실코드 이전 완료 (yule_planning)
  discord-gateway/   Discord transport / 게이트웨이 → 실코드 이전 완료 (yule_discord)
  engineering-agent/ 개발 intake / 계획 / deliberation / GitHub 연동 → scaffold (코드는 src/agents)
  memory-worker/     reindex / retrieval eval / vault sync          → scaffold
  loadtest-runner/   runtime/memory/agent backend 부하(MOCK)         → k6 샘플

src/yule_orchestrator/   아직 분해되지 않은 모놀리스 + 옛 경로 compat shim
  agents/      ~106k LOC — engineering-agent 코어 (장기 분해 대상, §4)
  runtime/     status / run_service 등 오케스트레이션 (프리미티브는 packages/runtime 로 빠짐)
  cli/         `yule` CLI 진입점 (서브커맨드)
  github_app/  GitHub App 연동
  observability/ diagnostics/  소형 유틸
  memory/ planning/ core/ storage/ integrations/ discord/  → 전부 compat shim(아래 §3)
```

설치/임포트: 루트 `pyproject.toml` 의 `[tool.setuptools.packages.find].where` 가
`src` + 모든 `packages/*/src` + `apps/*/src` 를 포함한다. **코드 이동/패키지 추가
후에는 `pip install -e .` 를 재실행** 해 editable `.pth` 를 갱신한다.

## 2. 의존 방향 규칙 (hard rail)

- `apps/* → packages/*` 가능, **`packages/* → apps/*` 금지**.
- **agent 간 직접 import 금지** — `packages/agent-contracts` 의 command/event/status 로만 연결(목표).
- **discord-gateway 는 agent 내부 로직 직접 호출 금지** — command/event 로만 연결(목표).
- `packages/*` 는 stdlib / 선언된 third-party / 다른 package 만 의존. `yule_orchestrator`(앱) import 금지.
- Agent Town UI / Phaser / 웹 프론트 / 타운 에셋은 **이 레포 책임 아님**.

### 과도기 부채 (acyclic, shim 으로 동작 — §4 에서 정리)
모놀리스에서 점진 추출하는 동안 일부 edge 는 임시로 남는다.
- **apps → monolith**: `apps/planning-agent`·`apps/discord-gateway` 가 아직 `yule_orchestrator.{agents,runtime,...}` 를 import. agents 는 이들을 역import 하지 않으므로 **순환 아님**.
- **app → app(via shim)**: `discord-gateway → yule_orchestrator.planning`(= planning-agent shim). 후속에 agent-contracts event 로 대체.
- 이 부채는 `src` 의 공유 인프라/코어가 packages/* 로 더 빠지고, agent 호출이 contracts event 로 바뀌면 사라진다.

## 3. Compat shim 카탈로그 (옛 경로 → 새 위치)

기존 `from yule_orchestrator.X import ...` 호출부를 깨지 않기 위해 옛 경로에 shim 을 둔다.
모두 `sys.modules` 별칭(또는 eager-walk) 으로 **객체 identity 보존**.

| 옛 경로 | 실제 위치 |
| --- | --- |
| `yule_orchestrator.agents.messaging.message` | `packages/agent-contracts` (`yule_agent_contracts`) |
| `yule_orchestrator.memory` | `packages/memory` (`yule_memory`) |
| `yule_orchestrator.core` | `packages/core` (`yule_core`) |
| `yule_orchestrator.storage` | `packages/storage` (`yule_storage`) |
| `yule_orchestrator.integrations` | `packages/integrations` (`yule_integrations`) |
| `yule_orchestrator.runtime.{circuit_breaker,services,subprocess_supervisor}` | `packages/runtime` (`yule_runtime`) |
| `yule_orchestrator.planning` | `apps/planning-agent` (`yule_planning`) |
| `yule_orchestrator.discord` | `apps/discord-gateway` (`yule_discord`) |

> agents↔discord 순환은 완전히 제거됨: `discord/` 에 잘못 있던 agent 로직
> (`engineering_team_runtime`/`engineering_conversation`/`research_forum`/`help_surface`/
> `proposal_to_dict`)을 `agents/` 로 relocate 하고 agent-side importer 를 새 경로로
> 재배선해 `agents → discord` import 를 0 으로 만들었다.

## 4. 남은 로드맵

1. **engineering-agent 코어 분해 (최대 과제)** — `src/yule_orchestrator/agents/`(~106k LOC, 258 파일)
   가 코어 도메인이다. 통째 이동 금지 — thoughtful 분해:
   - 공유 성격(`job_queue` / `lifecycle` / `governance` / `role_profiles` 등) → `packages/*` 후보.
   - 순수 orchestration → `apps/engineering-agent`.
   - 의존 그래프/위험도/PR 분할 계획을 먼저 세운다.
2. **과도기 edge 제거** — apps→monolith / app→app(via shim) 호출을 `agent-contracts` command/event/status 로 역전. 특히 `discord-gateway`/`planning-agent` 가 agent 를 직접 호출하는 부분.
3. **shim 제거(src 실질 축소)** — 호출부를 새 경로(`yule_memory`/`yule_core`/… ) 직접 import 로 전환한 뒤 옛 경로 shim 삭제.
4. **잔여 인프라 패키지화(선택)** — `observability`(소형, storage 의존) / `config` 등 target 트리 항목.
5. **runtime 잔여 분해** — `status.py`(대형, P0-Q split-pending) / `run_service.py` / `heartbeats`(HeartbeatStore 의존 분리 후) 이전.
6. **app 진입점/CLI 정리** — `cli/` 를 각 app 진입점으로, llm-gateway 뒤로 provider runner self-register.

## 5. 새 package / app 추가 방법

1. `packages/<name>/`(또는 `apps/<name>/`) 에 자체 `pyproject.toml`(name, deps, `where=["src"]`).
2. 코드는 `src/<python_pkg>/` 에. 모놀리스에서 옮길 땐 `git mv` 로 이력 보존 + 옛 경로에 `sys.modules` 별칭 compat shim.
3. 루트 `pyproject.toml` `where` 에 `<dir>/src` 추가.
4. `packages/<name>/tests/` 에 smoke(공개 surface + 옛 경로 identity).
5. 검증: `PYTHONPATH="src:<모든 packages/*/src>:<모든 apps/*/src>" .venv/bin/python -m pytest tests <pkg>/tests` + `compileall`.
6. 머지 후 `pip install -e .` 재실행.

> 진행 이력은 git(머지된 `refactor/*` PR #187~#197) 과 운영 메모리에 있다.
