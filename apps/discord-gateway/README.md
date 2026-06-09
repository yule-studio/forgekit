# discord-gateway

> Discord 메시지를 받고 보내는 **transport 게이트웨이**. agent runtime 의
> input/output 채널을 연결한다. 본 디렉터리는 현재 **책임 문서(scaffold)**
> 만 두며, 코드는 아직 `src/yule_orchestrator/discord/**` 에 있다.

## 책임 범위

- **Discord 메시지 수신/전송** — 채널/포럼/멤버 메시지 I/O.
- **agent runtime input/output 채널 연결** — 들어온 메시지를 command/event 로
  변환해 agent 로 넘기고, agent 가 낸 event/status 를 Discord 로 내보낸다.

> **agent 내부 로직 직접 수행 금지.** gateway 는 deliberation / 코드 작업 /
> 계획 같은 의사결정을 절대 수행하지 않는다. 오직 **command/event 로만**
> agent 와 연결한다. 의사결정은 engineering-agent / planning-agent 의 몫.

## 의존 패키지 (필요한 `packages/*` 만)

- `packages/agent-contracts` — Discord 메시지 ↔ command/event/status 변환의
  유일한 계약면. **agent 모듈 직접 import 금지.**

> 규칙: `apps/* → packages/*` 만 허용, 역방향 금지. gateway 는
> command/event 로만 agent 와 연결.

## 현재 위치 → 이전 대상

| 현재 위치 | 이전 대상 |
| --- | --- |
| `src/yule_orchestrator/discord/**` | `apps/discord-gateway/**` (transport) |
| `discord/engineering_channel_router/**` 의 의사결정 부분 | `apps/engineering-agent` (gateway 가 아님) |

## migration TODO

- [ ] `discord/{bot,commands,conversation,forum,member,...}` 의 transport 부분을 본 앱으로 이전.
- [ ] agent 의사결정 로직이 섞인 router 부분을 분리해 해당 agent 앱으로 보냄.
- [ ] gateway↔agent 연결을 `packages/agent-contracts` command/event/status 로 일원화 (직접 함수 호출 제거).

(실제 코드 이동은 본 브랜치 범위 아님 — 후속 PR.)
