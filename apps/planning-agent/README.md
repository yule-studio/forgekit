# planning-agent

> 일정/계획/브리핑을 담당하는 앱. 코드는 `apps/planning-agent/src/yule_planning/`
> 로 **실제 이동 완료**. 옛 경로 `apps/engineering-agent/src/yule_engineering/planning/**` 는 `yule_planning`
> 을 가리키는 compat shim(`sys.modules` alias / public re-export) 으로만 남아 기존
> import 가 그대로 동작한다. 신규 코드는 `yule_planning` 직접 import.
>
> 과도기 부채: `yule_planning` 은 아직 `yule_engineering.{core,integrations,storage}`
> 공유 인프라를 import 한다(apps → monolith). 이 인프라가 `packages/*` 로 추출되면
> 해당 edge 는 사라진다.

## 책임 범위

- **일정/계획** — 작업/이벤트를 calendar 기반으로 정리.
- **브리핑** — 일간/주간 브리핑 생성.
- **calendar 기반 작업 정리** — day profile / schedule / category 정책에
  맞춰 작업을 배치.

> 범위 밖: 코드 작업 실행(=engineering-agent), Discord transport
> (=discord-gateway), memory 인덱싱(=memory-worker).

## 의존 패키지 (필요한 `packages/*` 만)

- `packages/agent-contracts` — 다른 agent / gateway 와의 command / event /
  status. **agent 간 직접 import 금지.**
- `packages/llm-gateway` — 브리핑 생성 등 LLM 요청 (예정).

> 규칙: `apps/* → packages/*` 만 허용, 역방향 금지.

## 현재 위치 → 이전 대상

| 현재 위치 | 이전 대상 |
| --- | --- |
| `apps/engineering-agent/src/yule_engineering/planning/**` | `apps/planning-agent/**` |

## migration TODO

- [ ] `planning/{planner,schedule,briefings,tasks,...}.py` 를 본 앱으로 이전.
- [ ] LLM 호출(`ollama*.py`)을 `packages/llm-gateway` 경유로 교체.
- [ ] engineering-agent 와의 연계를 `packages/agent-contracts` command/event 로 정의.

(실제 코드 이동은 본 브랜치 범위 아님 — 후속 PR.)
