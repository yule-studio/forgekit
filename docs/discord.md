# Discord 운영

이 문서는 Discord 봇 (planning + engineering gateway + 7 멤버 봇) 의 채널 / 토큰 / slash command / 권한을 정리한다.

## 1. 봇 인스턴스

| 봇 | 토큰 env | 책임 |
|---|---|---|
| Planning bot | `DISCORD_BOT_TOKEN` | daily plan / scheduled briefing / checkpoint / planning 자유 대화 |
| Engineering gateway | `ENGINEERING_AGENT_BOT_GATEWAY_TOKEN` | `#업무-접수` 라우팅 / 작업 thread 생성 / 운영-리서치 forum 발행 |
| tech-lead member bot | `ENGINEERING_AGENT_BOT_TECH_LEAD_TOKEN` | tech-lead 역할 turn / synthesis |
| ai-engineer | `ENGINEERING_AGENT_BOT_AI_ENGINEER_TOKEN` | AI / RAG / agent 런타임 시각 |
| product-designer | `ENGINEERING_AGENT_BOT_PRODUCT_DESIGNER_TOKEN` | UX / 디자인 |
| backend-engineer | `ENGINEERING_AGENT_BOT_BACKEND_ENGINEER_TOKEN` | API / DB / Spring / 보안 |
| frontend-engineer | `ENGINEERING_AGENT_BOT_FRONTEND_ENGINEER_TOKEN` | React / UI / web |
| qa-engineer | `ENGINEERING_AGENT_BOT_QA_ENGINEER_TOKEN` | 회귀 / 테스트 / acceptance |
| devops-engineer | `ENGINEERING_AGENT_BOT_DEVOPS_ENGINEER_TOKEN` | CI/CD / Docker / 배포 / 운영 |

## 2. 채널 운영 표준

- **`#일정-관리`** (= `DISCORD_CONVERSATION_CHANNEL_*`) — planning 자유 대화.
- **`#업무-접수`** (= `DISCORD_ENGINEERING_INTAKE_CHANNEL_*`) — engineering 자유 대화 + 작업 접수. 런타임 활성 키.
- **`#승인-대기`** (= `DISCORD_ENGINEERING_APPROVAL_CHANNEL_*`) — write 승인 UX. M5a-2 + M6.1b 이후 런타임 활성: ApprovalWorker 가 카드를 게시하고 `#승인-대기` 답신을 `handle_approval_reply` 가 ObsidianWriteRequest 로 변환한다. NAME→ID fallback (`DISCORD_ENGINEERING_APPROVAL_CHANNEL_NAME` + `DISCORD_GUILD_ID`) 지원.
- **`#봇-상태`** (= `DISCORD_ENGINEERING_STATUS_CHANNEL_*`) — runtime status / circuit / fallback 알림. M7-final 이후 런타임 활성: `eng-supervisor-watch` 가 `ENGINEERING_STATUS_POST_ENABLED=true` 로 켜지면 주기적으로 markdown 요약을 게시하고, `yule runtime status --post-discord` 로 즉시 게시도 가능. NAME fallback 동일.
- **`#실험실`** (= `DISCORD_ENGINEERING_LAB_CHANNEL_*`) — 워크플로 / 프롬프트 테스트. 현재 예약 슬롯.
- **`#운영-리서치`** Forum (= `DISCORD_AGENT_RESEARCH_FORUM_CHANNEL_*`) — 부서 공통 research / deliberation inbox. 런타임 활성: 자료 수집 → 역할별 검토 → tech-lead 종합 → Obsidian 후보 선정.

각 채널의 게시 규약 / 댓글 양식 / Obsidian export contract 는 `policies/runtime/agents/engineering-agent/research-forum.md` 와 `policies/runtime/agents/engineering-agent/discord-workflow.md` 참고.

## 3. 채널 라우팅 거동

- intake 채널은 ID 와 NAME 중 하나만 매치돼도 라우팅. 둘 다 비어 있으면 engineering 라우터가 비활성으로 떨어져 모든 메시지는 기존 planning 흐름으로 처리. 자세한 매트릭스는 `policies/runtime/agents/engineering-agent/discord-workflow.md` §1.1.
- DAILY 와 CONVERSATION 을 다른 채널로 두면 DAILY 는 자동 브리핑 전용 broadcast 채널로 잠긴다. 사용자가 그곳에서 메시지를 보내거나 봇을 멘션해도 응답하지 않는다. 같은 채널로 두면 자동 브리핑과 채팅이 함께 이루어진다.
- engineering-agent 는 planning-bot 과 다른 채널을 사용한다. `yule discord up` 이 두 봇을 띄울 때 자식 환경에서 서로의 채널을 비워 응답이 충돌하지 않게 강제한다.

## 4. `yule discord up` (dev only)

```bash
yule discord up --dry-run        # 인벤토리 확인
yule discord up                  # 9 봇 일괄 기동 (planning + gateway + 7 멤버)
yule discord member --role tech-lead --dry-run   # 단일 멤버 봇만
yule discord bot                 # planning 단독
```

**상시 운영은 `yule discord up` 이 아니라 systemd 기반 service unit 을 권장한다.** 자세한 운영 가이드는 [operations.md](operations.md).

자식 프로세스 분리 거동:

- planning-bot 자식 프로세스에서 `DISCORD_ENGINEERING_INTAKE_CHANNEL_*` 를 빈 값으로 덮어써 `#업무-접수` 에 응답하지 않는다.
- engineering-agent gateway 자식 프로세스는 `DISCORD_DAILY_*` / `DISCORD_CHECKPOINT_*` / `DISCORD_CONVERSATION_*` / `DISCORD_DEBUG_*` / `DISCORD_NOTIFY_USER_ID` 를 비워 planning 채널 동작을 차단 (`DISCORD_CONVERSATION_REPLY_MODE=disabled`).
- supervisor 의 `BOT_RUNNER_ENGINEERING_GATEWAY` 분기와 `policies/runtime/agents/engineering-agent/launcher.md` 가 분리 약속을 강제한다.

## 5. 멤버 봇 권한 / Intent

각 멤버 봇 앱마다 Discord Developer Portal 에서 **Message Content Intent 를 켜야** 한다. 서버 / 채널 권한으로 다음을 부여:

- `View Channel`
- `Read Message History`
- `Send Messages`
- `Send Messages in Threads`

대상은 `#운영-리서치` Forum 과 `#업무-접수` 의 작업 thread parent 둘 다. 멤버 봇은 로그인 직후 stderr 에 `permissions OK` 또는 `missing ... permissions` 경고를 남긴다. 단, Developer Portal 의 Message Content Intent 토글은 Discord 런타임 API 로 검증할 수 없어 로그에는 안내만 표시된다.

## 6. Forum comment mode

`ENGINEERING_RESEARCH_FORUM_COMMENT_MODE=member-bots` 가 기본 권장값. 이 모드에서는 gateway 가 `#운영-리서치` 포럼 post 와 `[research-open:<session_id>]` open-call directive 를 남기고, 각 멤버 봇이 자기 정책으로 추가 조사한 뒤 자기 계정으로 독립 take 를 남긴다.

`gateway` 로 바꾸면 멤버 봇 토큰이 없을 때처럼 gateway 가 역할별 코멘트를 대리 게시하는 fallback 모드. 값을 바꾼 뒤에는 `yule discord up` 프로세스를 재시작해야 한다.

## 7. Slash command

```text
/ping
/plan_today
/checkpoints_now
/engineer_intake prompt:"..." task_type:"landing-page" write_requested:true
/engineer_show session_id:"..."
/engineer_approve session_id:"..."
/engineer_reject session_id:"..." reason:"..."
/engineer_progress session_id:"..." note:"..."
/engineer_complete session_id:"..." summary:"..."
/engineer_review session_id:"..." summary:"..." severity:"medium"
/engineer_review_reply session_id:"..." feedback_id:"..." applied:"..."
```

- `/plan_today` 는 외부 API 를 직접 기다리지 않고 저장된 daily-plan snapshot 을 Discord 메시지로 정리해 보여준다. snapshot 이 없으면 즉시 안내 후 백그라운드에서 만들고 followup.
- `/plan_today` 응답과 자동 브리핑 메시지 상단에는 표시 시점의 실제 현재 시각(`_지금 YYYY-MM-DD HH:MM 기준_`) 이 자동 추가된다.
- `/checkpoints_now` 는 지금 시각 기준으로 다가오는 체크포인트 확인용.
- `/engineer_review` 는 PR 리뷰 / Copilot / 외부 에이전트 / 사용자 피드백을 기존 session 에 연결하고 역할별 재검토로 라우팅한다.
- `/engineer_review_reply` 는 적용 / 제안 / 남은 이슈를 같은 review cycle 에 회신.
- Discord slash command 의 `complete` 는 inline `references_used` 를 받지 않으므로, reference 인용까지 닫으려면 CLI `yule engineer complete --references-used <json>` 을 사용.
- 슬래시 명령 동기화를 빠르게 하기 위해 현재 guild 단위 명령 등록을 사용. interaction 토큰이 만료된 상황(`Unknown interaction`) 을 만나면 traceback 대신 한 줄 경고만 남기고 graceful 종료.

## 8. 자동 브리핑

- 자동 브리핑 시각은 Discord Bot 이 아니라 Planning Agent 가 관리한다.
- 봇은 `YULE_WAKE_TIME`, `YULE_WORK_START_TIME`, `YULE_LUNCH_START_TIME`, `YULE_WORK_END_TIME` 기준으로 snapshot 안의 `morning` / `work_start` / `lunch` / `evening` 4 개 브리핑을 자동 전송.
- 자동 브리핑 본문은 `/plan_today` 와 동일 포맷이며, 슬롯별 헤더(`**[아침 브리핑]**`, `**[업무 시작 브리핑]**`, `**[점심 브리핑]**`, `**[퇴근 후 브리핑]**`) 가 맨 위에 붙는다.
- 아침 준비 작업은 `YULE_WAKE_TIME` 기준으로 `10 분 전 calendar sync`, `5 분 전 github sync`, `2 분 전 planning snapshot` 순서로 진행.
- 준비 단계 실패 시 `DISCORD_PREPARATION_RETRY_COUNT` / `DISCORD_PREPARATION_RETRY_DELAY_SECONDS` 기준 자동 재시도.
- 옛 snapshot 에 남아 있을 수 있는 "현재 X 시 Y 분입니다" 형태의 환각 시각 줄은 표시 직전에 자동 제거.
- `DISCORD_NOTIFY_USER_ID` 를 넣으면 브리핑과 체크포인트 메시지 앞에 해당 사용자 멘션이 붙는다.

권장 운영 흐름 — 먼저 snapshot 을 만든 뒤 Discord 봇이 그 결과만 읽는다:

```bash
yule daily warmup --json
yule discord bot
```

더 잘게 나누어 운영하고 싶다면:

```text
05:50 yule calendar sync --force-refresh --json
05:55 yule github issues --limit 30 --force-refresh
05:58 yule planning snapshot --json
06:00 Discord bot scheduled morning briefing
13:00 Discord bot scheduled lunch briefing
18:00 Discord bot scheduled evening briefing
```

이 구조에서는 Discord 봇이 브리핑 시점에 캘린더나 GitHub API 응답을 기다리지 않는다.

## 9. 체크포인트 응답

체크포인트 알림은 응답 안내 푸터를 함께 보내며, 사용자가 같은 채널에서 `완료/yes/네/응` 또는 `건너뛰기/skip/아니/ㄴㄴ` 처럼 답하면 해당 체크포인트는 done/skipped 상태로 닫혀 다시 알리지 않는다. 한국어 정중 / 반말 / 영어 변형 / 채팅 자모 (ㅇㅇ/ㄴㄴ) 까지 인식하며, 좌우 공백과 끝 문장부호는 자동 정규화.

닫힌 응답은 SQLite `task_completion_events` 테이블에 누적 저장되어, 같은 종류의 작업을 자주 미루거나 빠르게 끝내는 패턴을 다음 우선순위 / 소요 시간 추정에 자동 반영한다 (skip ≥ 50% → priority -최대 15, done ≥ 70% → +5, 평균 block_minutes 가 기본값과 15 분 이상 차이나면 estimated_minutes 교체).

## 10. Discord 운영 smoke test

브랜치를 실제 Discord 에 띄우기 전에 다음 5 단계를 순서대로 확인한다. 모두 dry-run / 관찰 작업이고 secret 을 읽지 않는다.

1. **봇 인벤토리 확인** — 9 개 봇이 모두 active 로 잡히는지.

   ```bash
   yule discord up --dry-run
   ```

   기대 출력: `summary: 9 active / 0 skipped` (planning-bot 1 + engineering gateway 1 + 멤버 7).

2. **typing indicator 확인** — 사용자가 봇이 살아 있는지 본다.
   - `#업무-접수` 채널에서 새 요청 메시지를 보내면 gateway 가 `입력 중...` 으로 표시.
   - 작업 thread / forum 에서 멤버 봇이 자기 차례를 처리할 때 해당 봇 계정이 `입력 중...` 으로 표시.
   - 일반 conversation 채널은 기존대로 typing 이 보이고 동작은 변하지 않아야 한다.

3. **diagnostic intent 확인** — 새 작업 접수 대신 상태 설명으로 응답하는지. `#업무-접수` 에 다음을 보냈을 때 새 session 이 만들어지지 않고 현재 열린 session 상태가 답변으로 와야 한다.
   - "운영 리서치는 안 열어?", "어떻게 됐어?", "왜 실패했어?", "진행상황 좀", "Obsidian 왜 안 들어갔어?"
   - 답변에 session id 가 포함되고, "1 차 자료를 모아볼게요" 같은 intake 템플릿이 다시 나오면 안 된다. 열린 session 이 없으면 안전 안내 문구.

4. **Forum long body 확인** — starter 는 짧게, 상세 자료는 thread 댓글로 분할. 매우 긴 리서치 요청에도 운영-리서치 forum 게시가 50035("Must be 4000 or fewer in length") 로 실패하지 않아야 한다. starter 메시지에 `_본문이 길어 상세 자료는 아래 댓글로 이어집니다. 원본은 Obsidian export 에 보존됩니다._` 안내, 1900 자 이하 chunk 후속 댓글, 일부 chunk 실패 시 `⚠️ 상세 자료 댓글 N건 중 K건 게시에 실패했습니다…` 안내.

5. **Obsidian sync 확인** — 원본은 vault 에 보존.

   ```bash
   yule obsidian sync --session <session_id> --dry-run
   ```

   `.env.local` 의 `OBSIDIAN_VAULT_PATH` 사용. dry-run 출력에 예상 markdown 경로와 Forum starter 에서 잘려나간 본문 / 출처 / synthesis 가 포함되어야 한다.

자세한 라이브 회귀 절차: `policies/runtime/agents/engineering-agent/live-regression.md`.
