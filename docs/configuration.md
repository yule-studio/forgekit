# Configuration

이 문서는 `.env.local` 키를 카테고리별로 정리한다. 실제 값은 `.env.local` 에만 두고 git 에 올리지 않는다 (`.env.example` 만 추적). 부트스트랩 스크립트는 `.env.local` 을 덮어쓰지 않으며, 새 키가 추가되면 빠진 키만 알려준다.

## 1. CalDAV / 캘린더

```bash
NAVER_CALDAV_URL=https://caldav.calendar.naver.com
NAVER_ID=
NAVER_APP_PASSWORD=
# NAVER_CALDAV_CALENDAR=
# NAVER_CALDAV_TODO_CALENDAR=내 할 일
# NAVER_CALDAV_TIMEOUT_SECONDS=15
# NAVER_CALDAV_CACHE_SECONDS=300
# NAVER_CALDAV_INCLUDE_ALL_TODOS=false
YULE_NAVER_CATEGORY_POLICY_FILE=policies/runtime/agents/planning-agent/naver-category-policy.json
# YULE_NAVER_CATEGORY_POLICY_JSON=
```

- `NAVER_CALDAV_TIMEOUT_SECONDS` — 응답이 오래 걸릴 때 요청 타임아웃 조절.
- `NAVER_CALDAV_CACHE_SECONDS` — 지정 시 해당 TTL 우선. 미지정 시 오늘 포함 범위는 5 분, 미래는 30 분, 과거는 24 시간 SQLite 로컬 캐시 재사용.
- 캐시 저장소 기본 위치: `.cache/yule/cache.sqlite3`. 같은 SQLite 안에 캘린더 항목 상태(`calendar_item_states`) 도 함께 동기화.
- 원격 fetch 가 `network`, `query`, `unknown` 오류로 실패하면 stale cache 를 임시 fallback 으로 사용한다.
- `YULE_NAVER_CATEGORY_POLICY_FILE` / `YULE_NAVER_CATEGORY_POLICY_JSON` 으로 색상별 Planning 우선순위 정책을 지정. CI / 컨테이너에서는 JSON 본문을 env 로 직접 둘 수 있다.
- 할 일 캘린더는 전체 캘린더 목록에서 `할 일`, `todo`, `task` 가 들어간 이름을 자동 탐지. 여러 개일 때는 `NAVER_CALDAV_TODO_CALENDAR` 를 우선.
- `NAVER_CALDAV_INCLUDE_ALL_TODOS=true` 는 서버가 날짜 범위 검색으로 할 일을 제대로 주지 않을 때만 사용하는 느린 마지막 보강 옵션.
- 캐시를 무시하고 새로 가져오려면 `--force-refresh`.
- 운영 노하우: [calendar-notes.md](calendar-notes.md).

## 2. SQLite / 공용 캐시

```bash
# YULE_SQLITE_BUSY_TIMEOUT_MS=30000
```

- Discord Bot, warmup, snapshot 이 같은 SQLite 를 만질 때 잠금 대기 시간 (ms). 기본 30000.

## 3. Planning Agent / 하루 리듬

```bash
# YULE_WAKE_TIME=06:00
# YULE_WORK_START_TIME=09:00
# YULE_LUNCH_START_TIME=12:00
# YULE_WORK_END_TIME=18:00
# YULE_COMMUTE_MINUTES=45
# YULE_DEPARTURE_BUFFER_MINUTES=10
# YULE_HOME_AREA=신정동
# YULE_WORK_AREA=마곡
# YULE_WORK_MODE_ENABLED=true
# YULE_LUNCH_DURATION_MINUTES=60
# YULE_TIMEZONE=Asia/Seoul
# PLANNING_DAILY_SNAPSHOT_SECONDS=21600
```

- `YULE_WORK_MODE_ENABLED=true` — `업무 수행` 일정 시간 안에는 네이버 카테고리 `회사 업무`(기본 색상 코드 27) todo 만 배치되고, 그 외 todo 는 점심·퇴근 후 같은 비업무 시간으로 분배. `false` 로 두면 자유 모드.
- `YULE_LUNCH_DURATION_MINUTES` — 점심 시간 길이 (분). 해당 구간은 가상 차단 블록으로 처리되어 어떤 focus block 도 배치되지 않는다.
- `YULE_TIMEZONE` — Planning Agent 와 Discord 자동 브리핑이 사용할 IANA 타임존. 비워두면 시스템 로컬 타임존. 여행 / 원격 근무 환경에서 한국 기준 고정용.
- `PLANNING_DAILY_SNAPSHOT_SECONDS` — daily-plan snapshot 유효 시간. 기본 6 시간.

## 4. Ollama (옵션)

```bash
# OLLAMA_PLANNING_ENABLED=false
# OLLAMA_ENDPOINT=http://localhost:11434
# OLLAMA_MODEL=gemma3:latest
# OLLAMA_TIMEOUT_SECONDS=20
# OLLAMA_FALLBACK_MODEL=
# OLLAMA_RETRY_COUNT=1
# OLLAMA_DISCORD_ENABLED=false
# OLLAMA_DISCORD_ENDPOINT=http://localhost:11434
# OLLAMA_DISCORD_MODEL=gemma3:latest
# OLLAMA_DISCORD_TIMEOUT_SECONDS=20
# OLLAMA_DISCORD_FALLBACK_MODEL=
# OLLAMA_DISCORD_RETRY_COUNT=1
```

- `OLLAMA_PLANNING_ENABLED=true` — `planning daily` / `planning snapshot` / `daily warmup` 에서 Ollama 가 아침 브리핑 문장을 다듬는다.
- `OLLAMA_FALLBACK_MODEL` — Planning Ollama 호출이 실패하거나 응답 검증 실패 시 fallback 모델로 재시도.
- `OLLAMA_DISCORD_*` — Discord 대화형 응답을 Planning 과 다른 모델 / endpoint / 재시도 정책으로 분리. 미지정 시 Planning 측 설정을 그대로 따른다.
- CLI 일회성 토글: `--use-ollama`, `--no-ollama`.

## 5. Discord (Planning bot 단독)

```bash
DISCORD_BOT_TOKEN=
# DISCORD_APPLICATION_ID=
DISCORD_GUILD_ID=
# DISCORD_DAILY_CHANNEL_ID=
# DISCORD_DAILY_CHANNEL_NAME=
# DISCORD_DEBUG_CHANNEL_ID=
# DISCORD_DEBUG_CHANNEL_NAME=
# DISCORD_CHECKPOINT_CHANNEL_ID=
# DISCORD_CHECKPOINT_CHANNEL_NAME=
# DISCORD_CONVERSATION_CHANNEL_ID=
# DISCORD_CONVERSATION_CHANNEL_NAME=
# DISCORD_NOTIFY_USER_ID=
# DISCORD_CHECKPOINT_PREFETCH_MINUTES=5
# DISCORD_PREPARATION_RETRY_COUNT=2
# DISCORD_PREPARATION_RETRY_DELAY_SECONDS=15
# DISCORD_CONVERSATION_REPLY_MODE=mention-only
```

- `DISCORD_APPLICATION_ID` 비워두면 토큰 기준으로 자동 사용.
- `DISCORD_*_CHANNEL_NAME` 을 같이 넣으면 채널 ID 가 잘못된 경우 이름 기반 fallback 으로 다시 찾는다.
- `DISCORD_CONVERSATION_REPLY_MODE` — `mention-only` (기본, 멘션 시만 응답), `plain-message-or-mention` (지정 채널의 평문 메시지에도 응답), `disabled` (대화형 응답 끔).
- 별도 대화 채널을 지정하지 않으면 `DISCORD_DAILY_CHANNEL_ID` / `DISCORD_DAILY_CHANNEL_NAME` 이 fallback. DAILY 와 CONVERSATION 을 다른 채널로 두면 DAILY 는 broadcast 전용으로 잠긴다.

## 6. Engineering Agent Discord 채널

```bash
# Engineering Agent Discord channels
# DISCORD_ENGINEERING_INTAKE_CHANNEL_ID=
# DISCORD_ENGINEERING_INTAKE_CHANNEL_NAME=업무-접수
# DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID=
# DISCORD_ENGINEERING_APPROVAL_CHANNEL_NAME=승인-대기
# DISCORD_ENGINEERING_STATUS_CHANNEL_ID=
# DISCORD_ENGINEERING_STATUS_CHANNEL_NAME=봇-상태
# DISCORD_ENGINEERING_LAB_CHANNEL_ID=
# DISCORD_ENGINEERING_LAB_CHANNEL_NAME=실험실

# Cross-agent research forum
# DISCORD_AGENT_RESEARCH_FORUM_CHANNEL_ID=
# DISCORD_AGENT_RESEARCH_FORUM_CHANNEL_NAME=운영-리서치

# Engineering Agent member bots
# ENGINEERING_AGENT_BOT_GATEWAY_TOKEN=
# ENGINEERING_AGENT_BOT_TECH_LEAD_TOKEN=
# ENGINEERING_AGENT_BOT_AI_ENGINEER_TOKEN=
# ENGINEERING_AGENT_BOT_PRODUCT_DESIGNER_TOKEN=
# ENGINEERING_AGENT_BOT_BACKEND_ENGINEER_TOKEN=
# ENGINEERING_AGENT_BOT_FRONTEND_ENGINEER_TOKEN=
# ENGINEERING_AGENT_BOT_QA_ENGINEER_TOKEN=
# ENGINEERING_AGENT_BOT_DEVOPS_ENGINEER_TOKEN=
```

채널 운영·토큰 분리·intent 권한 등 세부는 [discord.md](discord.md) 와 `policies/runtime/agents/engineering-agent/discord-workflow.md` 참고.

## 7. 자율 리서치 / Research budget

```bash
# Autonomous research collector
# ENGINEERING_RESEARCH_AUTO_COLLECT_ENABLED=false
# ENGINEERING_RESEARCH_PROVIDER=mock
# ENGINEERING_RESEARCH_MAX_RESULTS=5
# ENGINEERING_RESEARCH_MAX_PROVIDER_CALLS=8
# ENGINEERING_RESEARCH_MAX_RESULTS_PER_ROLE=3
# TAVILY_API_KEY=
# BRAVE_SEARCH_API_KEY=
# ENGINEERING_RESEARCH_FORUM_COMMENT_MODE=member-bots
# ENGINEERING_RESEARCH_PROVIDERS=tavily,brave
```

- 자세한 budget tier 정의 / multi-provider 거동 / role × provider 표: [research-budget.md](research-budget.md).
- `ENGINEERING_RESEARCH_FORUM_COMMENT_MODE=member-bots` 가 기본 권장값. `gateway` 는 멤버 봇 토큰이 없을 때 fallback.

## 8. GitHub

```bash
# YULE_GITHUB_LABEL_POLICY_FILE=policies/runtime/agents/planning-agent/github-label-policy.json
# YULE_GITHUB_LABEL_POLICY_JSON=
# GITHUB_ISSUES_CACHE_SECONDS=300
# GITHUB_PULL_REQUESTS_CACHE_SECONDS=300
```

- `YULE_GITHUB_LABEL_POLICY_*` 로 라벨별 우선순위 보정 정책을 덮어쓸 수 있다. 기본 매핑은 `infrastructure: +30`, `domain: +25`, `bug: +25`, `feature: +10`, `chore: -5`, `ui: -10` 등.
- 이슈 fetch 시 라벨 / 본문 / 담당자 / 마지막 갱신 시각까지 함께 가져와 캐시. 이슈 제목에 도메인 / 엔티티 / 스키마 / 마이그레이션 / infrastructure 같은 기반 키워드가 있으면 우선순위가 추가로 올라가고, ui / 디자인 / 댓글 / 색상 같은 표면 키워드가 있으면 낮아진다.
- Planning Agent 는 open issue 뿐 아니라 open PR 도 함께 fetch 해서 작업 후보로 다룬다. PR 은 ready 면 +10, draft 면 -10.

## 9. Obsidian vault

```bash
# OBSIDIAN_VAULT_PATH=/Users/<MY_USER>/local-dev/yule-agent-vault/obsidian-vault
# OBSIDIAN_EXPORT_LAYOUT=yule-agent-vault
# OBSIDIAN_DEFAULT_PROJECT=yule-studio-agent
```

- vault 절대경로는 git 에 올라가는 `.env.example` 이 아니라 `.env.local` 에 둔다.
- export 경로 / kind 매핑 / overwrite 정책: `policies/runtime/agents/engineering-agent/obsidian-memory.md` + [engineering.md](engineering.md) 의 Obsidian 섹션.

## 10. 로컬 전용 파일

git 에 올리지 않는 파일과 폴더:

```text
.claude/
.codex/
.gemini/
.env
.env.local
.venv/
.cache/
runs/*
*.egg-info/
```

`src/yule_studio_agent.egg-info/` 는 로그인 정보가 아니라 Python 패키지 설치 과정에서 생성되는 메타데이터다.
