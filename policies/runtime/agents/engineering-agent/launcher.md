# Discord Launcher (`yule discord up`) — dev / 로컬 부트스트랩

planning-bot, engineering-agent gateway, engineering-agent member 봇을 한 번에 띄우는 supervisor 진입점. **개발용 / 단일 호스트 부트스트랩 도구**다. 제거하거나 deprecation 하지 않으며 dev 워크플로에서는 계속 쓰인다.

상시 운영 (always-on, production) 경로는 standalone runtime 이다:

- 단일 호스트 / dev: `yule runtime up --profile engineering`
- production: systemd template unit 이 워커별로 `yule run-service <service-id>` 호출
- 운영자 가시성: `yule runtime status [--post-discord]`, `yule runtime circuit reset <id>`

자세한 운영 절차는 [docs/operations.md](../../../../docs/operations.md). `yule discord up` 은 multiprocessing 으로 9 프로세스를 한 번에 띄워 Discord 표면 전반 (planning + engineering 게이트웨이 + 멤버 봇) 을 로컬에서 빠르게 검증하기 위한 편의 launcher 다.

## 사용

```bash
# 활성/비활성 봇 목록만 확인 (Discord 연결 없음)
yule discord up --dry-run

# 실제 실행
yule discord up

# 다른 부서 에이전트도 한 번에 (예: 추후 marketing-agent 등이 추가될 때)
yule discord up --agents engineering-agent,marketing-agent
```

`--agents` 미지정 시 기본은 `engineering-agent` 하나입니다. planning-bot은 부서 에이전트와 무관하게 항상 inventory에 포함됩니다.

## Inventory 산출 규칙

`build_inventory(repo_root, agent_ids=...)`가 다음 순서로 봇 목록을 만듭니다.

1. **planning-bot** — `DISCORD_BOT_TOKEN` 환경변수 사용. `discord/bot.py:run_discord_bot`로 실행.
2. **부서 에이전트별로 (입력 순서대로):**
   - **gateway** 봇 — `<AGENT>_BOT_GATEWAY_TOKEN`
   - **각 멤버** 봇 — `<AGENT>_BOT_<ROLE>_TOKEN` (`manifest.json`의 `members` 순서 그대로)

각 봇은 토큰 유무에 따라 다음 상태:

- 토큰 있음 → `active`
- 토큰 없음/공백 → `skipped (token missing)`

## 출력 양식

```
discord launcher inventory:
  - planning-bot: active [DISCORD_BOT_TOKEN]
  - engineering-agent (gateway): active [ENGINEERING_AGENT_BOT_GATEWAY_TOKEN]
  - engineering-agent/tech-lead: skipped (token missing) [ENGINEERING_AGENT_BOT_TECH_LEAD_TOKEN]
  - engineering-agent/backend-engineer: skipped (token missing) [ENGINEERING_AGENT_BOT_BACKEND_ENGINEER_TOKEN]
  ...
summary: 2 active / 5 skipped
```

`--dry-run`일 때는 위 inventory만 출력하고 즉시 0으로 종료합니다.

실제 실행 시에는 inventory 출력 후 active 봇별로 `started: <bot_id>` / 실패 시 `failed: <bot_id> — <error>` / skip된 봇은 `skipped: <bot_id> (...)`를 stderr로 추가 출력합니다.

## 종료 코드

| 코드 | 의미 |
| --- | --- |
| `0` | dry-run 성공, 또는 실 실행에서 최소 한 봇이 시작됨 |
| `2` | 모든 봇이 토큰 부재로 `skipped` |
| `3` | 시작한 봇이 하나도 없고 spawn 단계에서 예외가 발생함 |

## 프로세스 관리 방식 (MVP)

- 봇 한 개당 별도 `multiprocessing.Process` 한 개를 띄웁니다.
- 자식 프로세스는 `fork`/`spawn` 시점의 부모 환경변수를 그대로 상속받습니다 — 토큰을 부모가 갖고 있어야 합니다.
- 자식 프로세스가 죽어도 supervisor 부모 프로세스는 그대로 살아 있습니다 (자동 재기동은 후속 마일스톤).
- 부모 종료 시 자식 종료 보장은 추후 보강 — 현재는 자식이 데몬이 아니므로 명시적 `kill`이 필요할 수 있습니다.

운영 권고: tmux/launchd/systemd 같은 외부 supervisor 아래에서 본 명령을 돌리는 것을 가정합니다.

## 건드리지 않은 것

- `discord/bot.py`, `discord/member_bot.py`, `discord/commands.py`, `agents/workflow.py`는 그대로 유지합니다.
- supervisor는 위 모듈의 공개 진입점만 호출합니다.
- planning-bot 단독 실행 경로(`yule discord bot`)와 단일 멤버 실행 경로(`yule discord member --role`)도 그대로 유지됩니다 — 이 launcher는 추가 진입점이지 대체가 아닙니다.

## 마이그레이션 상태 (M7-final 기준)

원래 후속 마일스톤이었던 항목 대부분은 standalone runtime 경로 (`yule runtime up` / `yule run-service`) 에서 닫혔다. dev launcher 자체가 모든 기능을 흡수할 필요는 없으므로 dev launcher 는 가벼운 multiprocessing 부트스트랩으로 유지하고, 운영 안전장치는 standalone runtime 쪽에서만 구현한다.

| 항목 | 상태 | 위치 |
|---|---|---|
| 자식 프로세스 재기동 + backoff | ✅ standalone | `runtime/subprocess_supervisor.py` (M6.0) |
| circuit-break (5분 5회) + persistence + reset | ✅ standalone | `runtime/circuit_breaker.py`, `runtime/circuit_cli.py` (M7 / M7-final) |
| `#봇-상태` 채널 자동 갱신 | ✅ standalone | `runtime/status_poster.py` + `eng-supervisor-watch` 주기 게시 (M7.1 / M7-final) |
| 봇별 stdout/stderr 라우팅 | ✅ standalone | `runtime/subprocess_supervisor._forward_with_prefix` (M6.0) |
| 부모 종료 시 SIGTERM 전파 | ✅ standalone | `runtime.run_service.run_engineering_gateway_until_shutdown` (M6.2) |
| 동일 토큰 충돌 가드 | 현재 정책 | 토큰 단위 1 프로세스 — 변동 없음 |

## Discord 채널 운영 원칙

`yule discord up`이 띄우는 봇들은 다음 채널 분담을 따릅니다 (운영자/사용자 모두 같은 약속을 사용).

| 채널 | 역할 | 사용 키 | 상태 |
|---|---|---|---|
| `#일정-관리` (기존 CONVERSATION) | planning-bot 자유 대화 | `DISCORD_CONVERSATION_CHANNEL_*` | 활성 |
| `#업무-접수` | engineering-agent 자유 대화 + 작업 접수 | `DISCORD_ENGINEERING_INTAKE_CHANNEL_*` | 활성 |
| `#승인-대기` | write 승인 UX (ApprovalWorker 게시 + 답신 라우팅) | `DISCORD_ENGINEERING_APPROVAL_CHANNEL_*` | 활성 (M5a-2 / M6.1b) |
| `#봇-상태` | runtime status / circuit / fallback 알림 | `DISCORD_ENGINEERING_STATUS_CHANNEL_*` | 활성 (M7.1 / M7-final) |
| `#실험실` | 워크플로·프롬프트 테스트 | `DISCORD_ENGINEERING_LAB_CHANNEL_*` | 예약 |
| `#운영-리서치` (Forum) | 부서 공통 research/deliberation inbox | `DISCORD_AGENT_RESEARCH_FORUM_CHANNEL_*` | 활성 |

규약
- planning-bot과 engineering-agent gateway는 **다른 채널을 본다**. 같은 채널을 보면 두 봇이 동일 메시지에 응답하므로 분리 운영을 강제한다.
- `#승인-대기` / `#봇-상태` 둘 다 ID env 우선, 미설정 시 NAME env + `DISCORD_GUILD_ID` 로 Discord REST GET fallback. 자세한 흐름은 [docs/operations.md §9](../../../../docs/operations.md#9-m7-final-운영-가이드--fallback--degrade--circuit--status-posting).
- 운영-리서치 forum은 engineering 전용이 아니라 부서 공통 inbox이므로 키 prefix가 `DISCORD_AGENT_RESEARCH_*`다. 게시 규약과 댓글 양식, Obsidian export contract는 `research-forum.md` 참조.
- 운영-리서치 역할별 댓글은 `ENGINEERING_RESEARCH_FORUM_COMMENT_MODE`가 제어한다. 기본 권장값 `member-bots`에서는 gateway가 첫 `[research-turn:...]` directive만 남기고 멤버 봇들이 자기 계정으로 이어 말한다. `gateway`로 두면 gateway가 모든 역할 코멘트를 대리 게시한다.
- 채널 정의의 자세한 매트릭스는 `discord-workflow.md` §1.1을 참고한다.
