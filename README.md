# Yule Studio Agent

Yule Studio Agent는 개인 홈서버에서 여러 AI 에이전트와 개발 도구를 조율하기 위한 오케스트레이터입니다.

GitHub 이슈, 일정 데이터, 에이전트 정책, 실행 흐름을 하나의 작업 체계로 연결해 개인 비서이자 개인 개발팀처럼 운영하는 것을 목표로 합니다.

## 현재 포함된 기능

- 에이전트 컨텍스트 로드
- 로컬 실행 환경 점검(`doctor`)
- GitHub의 열린 이슈 읽기
- Naver CalDAV 일정/할 일 읽기 및 구조화된 데이터(JSON) 변환
- Planning Agent 기반 daily plan 생성
- 시간 블록 브리핑과 체크포인트 생성
- Discord 슬래시 명령 기반 최소 봇 실행
- Discord 대화형 Planning 응답과 Ollama 기반 자연어 응답

## 디렉토리 구조

```text
.
├── AGENTS.md
├── CLAUDE.md
├── GEMINI.md
├── README.md
├── agents/
│   ├── engineering-agent/
│   │   ├── CLAUDE.md
│   │   └── agent.json
│   └── planning-agent/
│       ├── CLAUDE.md
│       └── agent.json
├── policies/
│   ├── reference/
│   └── runtime/
├── scripts/
│   └── bootstrap
└── src/
    └── yule_orchestrator/
        ├── cli/
        ├── core/
        ├── diagnostics/
        ├── discord/
        ├── integrations/
        └── planning/
```

## 설치

### 빠른 설치

macOS + Homebrew 기준:

```bash
./scripts/bootstrap
```

이 스크립트는 아래 작업을 수행합니다.

- Homebrew 확인
- `gh`와 Python 확인
- `.venv` 생성
- `pip`, `setuptools`, `wheel` 업그레이드
- 프로젝트 editable install
- `.env.example`이 있으면 `.env.local` 템플릿 생성
- 기존 `.env.local`이 있으면 덮어쓰지 않고, `.env.example` 대비 빠진 키만 안내

선택 AI CLI까지 함께 설치하려면:

```bash
./scripts/bootstrap --all
```

### 수동 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .
```

## 수동 인증

아래 로그인은 자동화하지 않습니다.

```bash
gh auth login
claude
codex
gemini
copilot
```

Ollama는 필요할 때 실행합니다.

```bash
open -a Ollama
# 또는
ollama serve
```

## 환경 변수

캘린더 연동은 루트의 `.env.local`에 값을 넣어 사용합니다.

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
# YULE_GITHUB_LABEL_POLICY_FILE=policies/runtime/agents/planning-agent/github-label-policy.json
# YULE_GITHUB_LABEL_POLICY_JSON=
# YULE_SQLITE_BUSY_TIMEOUT_MS=30000
# PLANNING_DAILY_SNAPSHOT_SECONDS=21600
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
# YULE_WAKE_TIME=06:00
# YULE_WORK_START_TIME=09:00
# YULE_COMMUTE_MINUTES=45
# YULE_DEPARTURE_BUFFER_MINUTES=10
# YULE_HOME_AREA=신정동
# YULE_WORK_AREA=마곡
# YULE_WORK_MODE_ENABLED=true
# YULE_LUNCH_DURATION_MINUTES=60
# YULE_TIMEZONE=Asia/Seoul

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

# Autonomous research collector
# ENGINEERING_RESEARCH_AUTO_COLLECT_ENABLED=false
# ENGINEERING_RESEARCH_PROVIDER=mock
# ENGINEERING_RESEARCH_MAX_RESULTS=5
# TAVILY_API_KEY=
# BRAVE_SEARCH_API_KEY=
# ENGINEERING_RESEARCH_FORUM_COMMENT_MODE=member-bots

# GITHUB_ISSUES_CACHE_SECONDS=300
# GITHUB_PULL_REQUESTS_CACHE_SECONDS=300
```

- 실제 값은 `.env.local`에 넣습니다.
- 예시는 `.env.example`에 둡니다.
- `.env.local`은 Git에 올리지 않습니다.
- `./scripts/bootstrap`은 기존 `.env.local`을 덮어쓰지 않습니다. 새 설정 키가 추가되면 빠진 키만 알려줍니다.
- 응답이 오래 걸리면 `NAVER_CALDAV_TIMEOUT_SECONDS`로 요청 타임아웃을 조절할 수 있습니다.
- `NAVER_CALDAV_CACHE_SECONDS`를 지정하면 해당 TTL을 우선 사용합니다.
- 값을 지정하지 않으면 오늘이 포함된 범위는 5분, 미래 범위는 30분, 과거 범위는 24시간 동안 SQLite 로컬 캐시를 재사용합니다.
- 캐시 저장소 기본 위치는 `.cache/yule/cache.sqlite3`입니다.
- 원격 fetch가 `network`, `query`, `unknown` 성격의 오류로 실패하면 오래된 stale cache를 임시 fallback 으로 사용할 수 있습니다.
- 같은 SQLite 안에 캘린더 항목 상태(`calendar_item_states`)도 함께 동기화합니다.
- `YULE_NAVER_CATEGORY_POLICY_FILE`로 네이버 범주 색상별 Planning 우선순위 정책을 지정할 수 있습니다.
- `YULE_NAVER_CATEGORY_POLICY_JSON`을 설정하면 파일을 읽지 않고 환경 변수에 담긴 JSON 본문을 바로 정책으로 사용합니다. CI나 컨테이너 환경처럼 파일을 두기 어려울 때 사용합니다.
- `YULE_SQLITE_BUSY_TIMEOUT_MS`로 Discord Bot, warmup, snapshot이 같은 SQLite를 만질 때 잠금 대기 시간을 조정할 수 있습니다. 기본값은 30000ms입니다.
- `PLANNING_DAILY_SNAPSHOT_SECONDS`로 daily-plan snapshot 유효 시간을 조정할 수 있습니다. 기본값은 6시간입니다.
- `OLLAMA_PLANNING_ENABLED=true`를 설정하면 `planning daily`, `planning snapshot`, `daily warmup`에서 Ollama가 아침 브리핑 문장을 다듬습니다.
- `OLLAMA_ENDPOINT`, `OLLAMA_MODEL`, `OLLAMA_TIMEOUT_SECONDS`로 로컬 또는 서버 Ollama 연결 정보를 조정할 수 있습니다.
- `OLLAMA_FALLBACK_MODEL`을 지정하면 Planning Ollama 호출이 실패하거나 응답 검증에 실패했을 때 fallback 모델로 한 번 더 시도합니다.
- `OLLAMA_RETRY_COUNT`로 Planning Ollama 호출의 재시도 횟수를 조정할 수 있습니다. 기본값은 1입니다.
- `OLLAMA_DISCORD_ENABLED=true`를 설정하면 Discord 대화형 응답도 snapshot 기반으로 Ollama를 사용합니다.
- `OLLAMA_DISCORD_ENDPOINT`, `OLLAMA_DISCORD_MODEL`, `OLLAMA_DISCORD_TIMEOUT_SECONDS`, `OLLAMA_DISCORD_FALLBACK_MODEL`, `OLLAMA_DISCORD_RETRY_COUNT`를 따로 넣으면 Discord 대화형 응답만 다른 Ollama 모델/엔드포인트/재시도 정책으로 분리할 수 있습니다. 미지정 시 Planning 측 설정을 그대로 따릅니다.
- Ollama 응답은 모델이 단일 줄바꿈으로 문단을 끊어도 Discord 표시용으로 문단 사이 빈 줄을 자동 보장합니다.
- CLI에서 일회성으로 켜고 끄려면 `--use-ollama`, `--no-ollama`를 사용합니다.
- `YULE_WAKE_TIME`, `YULE_WORK_START_TIME`, `YULE_LUNCH_START_TIME`, `YULE_WORK_END_TIME`, `YULE_COMMUTE_MINUTES`, `YULE_DEPARTURE_BUFFER_MINUTES`로 Planning Agent의 하루 리듬과 브리핑 시각 기준을 조정할 수 있습니다.
- `YULE_HOME_AREA`, `YULE_WORK_AREA`는 아침 브리핑 문구에 사용하는 출발/도착 지역 이름입니다.
- `YULE_WORK_MODE_ENABLED`는 회사 업무 우선 모드 토글입니다. 기본값은 `true`이며, `true`일 때는 `업무 수행` 일정 시간 안에는 네이버 카테고리 `회사 업무`(기본 색상 코드 27) todo만 배치되고, 그 외 todo는 점심·퇴근 후 같은 비업무 시간으로 분배됩니다. `false`로 두면 자유 모드로 동작해 `업무 수행` 일정을 무시하고 todo 우선순위 기준으로 하루 전체 시간을 자유롭게 분배합니다.
- `YULE_LUNCH_DURATION_MINUTES`는 점심 시간 길이(분)입니다. 기본값은 60이며, `YULE_LUNCH_START_TIME`부터 이 길이만큼은 가상의 차단 블록으로 처리되어 어떤 focus block도 배치되지 않습니다. 점심에 매번 산책처럼 고정 활동이 있어 일정 잡지 않고 비워두고 싶을 때 사용합니다.
- `YULE_TIMEZONE`은 Planning Agent와 Discord 자동 브리핑이 사용할 IANA 타임존 이름입니다(`Asia/Seoul`, `America/New_York` 등). 비워두면 시스템 로컬 타임존을 그대로 사용합니다. 여행이나 원격 근무로 시스템 타임존이 바뀌어도 브리핑 시간을 한국 기준에 고정하고 싶을 때 사용합니다.
- GitHub 이슈는 제목에 도메인/엔티티/스키마/마이그레이션/infrastructure 같은 기반 키워드가 있으면 우선순위가 추가로 올라가고, ui/디자인/댓글/색상 같은 표면 키워드가 있으면 낮아집니다. 실제 개발 순서(예: 도메인 모델 → 회원가입 기능 → UI)에 맞춰 자동으로 정렬되도록 돕는 휴리스틱입니다.
- GitHub 이슈에 라벨이 붙어 있으면 `policies/runtime/agents/planning-agent/github-label-policy.json` 정책에 따라 추가 우선순위 보정이 적용됩니다. 기본 매핑은 `infrastructure: +30`, `domain: +25`, `bug: +25`, `feature: +10`, `chore: -5`, `ui: -10` 등이며, `YULE_GITHUB_LABEL_POLICY_FILE` 또는 `YULE_GITHUB_LABEL_POLICY_JSON`으로 정책을 덮어쓸 수 있습니다.
- GitHub 이슈는 fetch 시 라벨, 본문(body), 담당자, 마지막 갱신 시각까지 함께 가져와 캐시에 저장되며, 정책 기반 우선순위와 향후 확장(라벨 그룹별 알림 등)에 활용됩니다.
- 네이버 카테고리 정책에 `"flexible": true`를 추가하면 해당 색상 코드가 붙은 todo는 시간 블록에 자동 배정되지 않고 추천 작업 목록에만 노출됩니다. mail-mail 정리 같이 정해진 시간 없이 자유롭게 처리하는 상시 작업 분류용입니다.
- 기본 동작은 요청한 날짜 범위 안의 일정과 할 일만 읽습니다.
- 할 일 캘린더는 전체 캘린더 목록에서 `할 일`, `todo`, `task`가 들어간 이름을 자동 탐지합니다.
- 자동 탐지된 할 일 캘린더가 여러 개일 때는 `NAVER_CALDAV_TODO_CALENDAR` 설정을 우선합니다.
- 자동 탐지 결과가 없으면 일반 일정 조회 대상 캘린더를 기준으로 fallback 합니다.
- `NAVER_CALDAV_INCLUDE_ALL_TODOS=true`는 서버가 날짜 범위 검색으로 할 일을 제대로 주지 않을 때만 사용하는 느린 마지막 보강 옵션입니다.
- `NAVER_CALDAV_INCLUDE_ALL_TODOS=true`를 써도 같은 범위 재실행은 캐시 덕분에 더 빠르게 응답할 수 있습니다.
- 캐시를 무시하고 새로 가져오려면 `--force-refresh`를 사용합니다.
- Discord Bot 실행에는 `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`가 필요합니다.
- `DISCORD_APPLICATION_ID`는 선택값입니다. 비워두면 토큰 기준으로 실제 Discord 애플리케이션 ID를 자동 사용합니다.
- `DISCORD_DAILY_CHANNEL_ID`, `DISCORD_CHECKPOINT_CHANNEL_ID`, `DISCORD_CONVERSATION_CHANNEL_ID`에는 애플리케이션 ID가 아니라 메시지를 보낼 Discord 텍스트 채널 ID를 넣습니다.
- `DISCORD_DAILY_CHANNEL_NAME`, `DISCORD_CHECKPOINT_CHANNEL_NAME`, `DISCORD_CONVERSATION_CHANNEL_NAME`을 같이 넣으면 채널 ID가 바뀌었거나 잘못 들어간 경우 이름 기반 fallback 으로 채널을 다시 찾을 수 있습니다.
- `DISCORD_DEBUG_CHANNEL_ID` 또는 `DISCORD_DEBUG_CHANNEL_NAME`을 넣으면 자동 준비 단계(`calendar sync`, `github sync`, `planning snapshot`)의 성공/실패 결과를 Discord 메시지로도 확인할 수 있습니다.
- `DISCORD_CONVERSATION_CHANNEL_ID` 또는 `DISCORD_CONVERSATION_CHANNEL_NAME`은 대화 채널 지정용입니다.
- `DISCORD_CONVERSATION_REPLY_MODE=mention-only`가 기본값이며, 이때는 봇 멘션이 있을 때만 응답합니다.
- `DISCORD_CONVERSATION_REPLY_MODE=plain-message-or-mention`으로 바꾸면 지정한 대화 채널에서 평문 메시지에도 응답합니다.
- `DISCORD_CONVERSATION_REPLY_MODE=disabled`로 두면 대화형 응답을 완전히 끌 수 있습니다.
- 별도 대화 채널을 지정하지 않으면 `DISCORD_DAILY_CHANNEL_ID` 또는 `DISCORD_DAILY_CHANNEL_NAME`이 대화 채널 fallback 으로도 사용됩니다.
- `DISCORD_DAILY_CHANNEL_ID`(또는 NAME)와 `DISCORD_CONVERSATION_CHANNEL_ID`(또는 NAME)를 **다르게** 설정하면 DAILY 채널은 자동 브리핑 전용 broadcast 채널로 잠기고, 사용자가 그곳에서 메시지를 보내거나 봇을 멘션해도 응답하지 않습니다. 채팅은 오직 CONVERSATION 채널에서만 이루어집니다. DAILY와 CONVERSATION을 같은 채널로 두거나 CONVERSATION을 비워 fallback으로 두면, 같은 채널 안에서 자동 브리핑과 채팅이 함께 이루어집니다.
- engineering-agent는 planning-bot과 **다른 채널**을 사용합니다. 운영 표준 분담은 다음과 같습니다:
  - `#일정-관리` (= `DISCORD_CONVERSATION_CHANNEL_*`) — planning 자유 대화.
  - `#업무-접수` (= `DISCORD_ENGINEERING_INTAKE_CHANNEL_*`) — engineering 자유 대화 + 작업 접수. 런타임에서 직접 읽는 활성 키.
  - `#승인-대기` (= `DISCORD_ENGINEERING_APPROVAL_CHANNEL_*`) — write 승인 UX. 현재 예약 슬롯.
  - `#봇-상태` (= `DISCORD_ENGINEERING_STATUS_CHANNEL_*`) — 상태/오류/헬스체크. 현재 예약 슬롯.
  - `#실험실` (= `DISCORD_ENGINEERING_LAB_CHANNEL_*`) — 워크플로/프롬프트 테스트. 현재 예약 슬롯.
  - `#운영-리서치` Forum (= `DISCORD_AGENT_RESEARCH_FORUM_CHANNEL_*`) — 부서 공통 research/deliberation inbox. 자료 수집 → 역할별 검토 → tech-lead 종합 → Obsidian 후보 선정. 현재 런타임에서 게시 대상으로 사용하며 게시 규약/댓글 양식/Obsidian export contract는 `policies/runtime/agents/engineering-agent/research-forum.md` 참조.
- intake 채널은 ID와 NAME 중 하나만 매치돼도 라우팅됩니다. 둘 다 비어 있으면 engineering 라우터가 비활성으로 떨어져 모든 메시지는 기존 planning 흐름으로 처리됩니다. 자세한 매트릭스는 `policies/runtime/agents/engineering-agent/discord-workflow.md` §1.1 참고.
- `yule discord up`이 두 봇을 띄우면 **planning-bot과 engineering-agent gateway가 별도 프로세스**로 분리됩니다:
  - planning-bot은 자식 프로세스에서 `DISCORD_ENGINEERING_INTAKE_CHANNEL_*`를 빈 값으로 덮어써 `#업무-접수`에 응답하지 않습니다.
  - engineering-agent gateway는 `ENGINEERING_AGENT_BOT_GATEWAY_TOKEN`을 자기 토큰으로 들고 별도 프로세스로 기동되며, 자식 환경에서 `DISCORD_DAILY_*`/`DISCORD_CHECKPOINT_*`/`DISCORD_CONVERSATION_*`/`DISCORD_DEBUG_*`/`DISCORD_NOTIFY_USER_ID`를 비워 planning 채널 동작을 차단합니다 (`DISCORD_CONVERSATION_REPLY_MODE=disabled`).
  - 결과: 두 봇이 같은 메시지에 동시에 응답하지 않고, 서로 자기 채널만 본다는 약속이 강제됩니다. 자세한 분리 동작은 `policies/runtime/agents/engineering-agent/launcher.md`와 supervisor의 `BOT_RUNNER_ENGINEERING_GATEWAY` 분기 참조.
- engineering-agent gateway가 작업 thread를 만들면 thread id가 `WorkflowSession.thread_id`로 영속화됩니다. 작업 thread는 진행 메모와 결과 회신 공간이며, research forum의 멤버 봇 발화는 별도의 open-call 프로토콜로 시작됩니다.
- 사용자가 `새로 등록하지 말고`, `기존 스레드`, `열려 있는 thread`, `이어가` 같은 표현으로 확인하면 gateway는 새 세션을 만들기 전에 같은 사용자/채널의 열린 engineering thread를 찾아 이어 붙입니다. 찾지 못하면 새 작업 세션을 만들지 않고 재지시를 요청합니다.
- `ENGINEERING_RESEARCH_FORUM_COMMENT_MODE=member-bots`가 기본 권장값입니다. 이 모드에서는 gateway가 `#운영-리서치` 포럼 post와 `[research-open:<session_id>]` open-call directive를 남기고, 각 멤버 봇이 자기 정책에 맞게 추가 조사한 뒤 자기 계정으로 독립 take를 남깁니다. `gateway`로 바꾸면 멤버 봇 토큰이 없을 때처럼 gateway가 역할별 코멘트를 대리 게시하는 fallback 모드로 동작합니다. 값을 바꾼 뒤에는 `yule discord up` 프로세스를 재시작해야 합니다.
- 멤버 봇이 실제로 의견을 이어 쓰려면 **각 멤버 봇 앱마다** Discord Developer Portal에서 `Message Content Intent`를 켜고, 서버/채널 권한으로 `View Channel`, `Read Message History`, `Send Messages`, `Send Messages in Threads`를 부여해야 합니다. 대상은 `#운영-리서치` Forum과 `#업무-접수`의 작업 thread parent 둘 다입니다.
- 멤버 봇은 로그인 직후 위 채널 권한을 점검해 stderr에 `permissions OK` 또는 `missing ... permissions` 경고를 남깁니다. 단, Developer Portal의 `Message Content Intent` 토글은 Discord 런타임 API로 검증할 수 없어서 로그에는 확인 안내만 표시됩니다.
- 자동 브리핑 시각은 Discord Bot이 아니라 Planning Agent가 관리합니다.
- 봇은 `YULE_WAKE_TIME`, `YULE_WORK_START_TIME`, `YULE_LUNCH_START_TIME`, `YULE_WORK_END_TIME` 기준으로 snapshot 안의 `morning/work_start/lunch/evening` 4개 브리핑을 자동 전송합니다.
- 자동 브리핑 본문은 `/plan_today` 슬래시 명령과 동일한 포맷을 사용하고, 슬롯별 헤더(`**[아침 브리핑]**`, `**[업무 시작 브리핑]**`, `**[점심 브리핑]**`, `**[퇴근 후 브리핑]**`)가 맨 위에 붙습니다.
- 아침 준비 작업은 `YULE_WAKE_TIME` 기준으로 자동 수행되며, `10분 전 calendar sync`, `5분 전 github sync`, `2분 전 planning snapshot` 순서로 진행합니다.
- `DISCORD_DAILY_BRIEFING_TIME`은 더 이상 사용하지 않으며, 설정되어 있어도 경고만 출력하고 무시합니다.
- 준비 단계가 실패하면 `DISCORD_PREPARATION_RETRY_COUNT`와 `DISCORD_PREPARATION_RETRY_DELAY_SECONDS` 기준으로 자동 재시도합니다.
- 채널 ID가 잘못되었어도 해당 이름 설정이 있으면 이름 기반 fallback 을 먼저 시도하고, 시작 로그와 런타임 경고에서 그 사실을 알려줍니다.
- `DISCORD_DAILY_CHANNEL_NAME`만 넣어도 자동 브리핑 채널로 사용할 수 있습니다.
- `DISCORD_NOTIFY_USER_ID`를 넣으면 브리핑과 체크포인트 메시지 앞에 해당 사용자 멘션을 붙입니다.
- Discord 대화형 MVP는 현재 브리핑 재요청, 우선순위 추천, 체크포인트 조회, 일정 조정 proposal 응답을 지원합니다.
- 체크포인트 알림은 응답 안내 푸터를 함께 보내며, 사용자가 같은 채널에서 `완료/yes/네/응` 또는 `건너뛰기/skip/아니/ㄴㄴ`처럼 답하면 해당 체크포인트는 done/skipped 상태로 닫혀 다시 알리지 않습니다. 한국어 정중/반말, 영어 변형, 채팅 자모(ㅇㅇ/ㄴㄴ)까지 인식하며, 좌우 공백과 끝 문장부호는 자동으로 정규화합니다.
- 닫힌 응답은 SQLite `task_completion_events` 테이블에 누적 저장되어, 같은 종류의 작업을 자주 미루거나 빠르게 끝내는 패턴을 다음 우선순위/소요 시간 추정에 자동 반영합니다(skip 비율 ≥ 50% 면 우선순위 -최대 15, done 비율 ≥ 70% 면 +5, 평균 block_minutes 가 기본값과 15분 이상 차이나면 estimated_minutes 교체).
- snapshot이 없을 때 동작은 모든 경로(`/plan_today`, 자동 브리핑, 채팅)에서 동일하게 처리됩니다. 즉시 "브리핑 데이터를 준비하고 있습니다" ack를 보낸 뒤, 백그라운드에서 캘린더 sync, GitHub 이슈, planning snapshot을 자동으로 만들고 follow-up 메시지로 실제 브리핑을 이어 보냅니다.
- 같은 날짜에 동시 요청이 들어와도 per-date 잠금 덕분에 자동 재생성 파이프라인은 한 번만 실행됩니다.
- 일정/상태 변경 요청은 아직 실제로 실행하지 않고 proposal 형태로만 답합니다.
- 슬래시 명령 동기화를 빠르게 하기 위해 현재 최소 봇은 guild 단위 명령 등록을 사용합니다.
- 슬래시 명령(`/plan_today`, `/checkpoints_now`)은 interaction 토큰이 만료된 상황(`Unknown interaction`)을 만나면 traceback 대신 한 줄 경고만 남기고 graceful 하게 종료합니다.
- `GITHUB_ISSUES_CACHE_SECONDS`를 지정하면 GitHub open issue 조회 결과를 해당 TTL 동안 재사용합니다. 기본값은 300초입니다.
- `GITHUB_PULL_REQUESTS_CACHE_SECONDS`를 지정하면 GitHub open PR 조회 결과를 해당 TTL 동안 재사용합니다. 기본값은 300초입니다.
- Planning Agent는 open issue뿐 아니라 open pull request도 함께 fetch해서 작업 후보로 다룹니다. PR은 ready 상태면 +10, draft면 -10이 우선순위에 반영되고 라벨 정책도 동일하게 적용됩니다. 자동 준비 단계의 `github_sync` 결과 metadata에 `pull_request_count`도 함께 기록됩니다.
- 자동 준비 단계는 표준 출력에 구조화된 JSON 로그를 남기고, debug 채널을 지정한 경우 Discord에서도 같은 흐름을 확인할 수 있습니다.

## 실행

```bash
yule doctor
yule context engineering-agent
yule context planning-agent
yule github issues --limit 30
yule github issues --limit 30 --force-refresh
yule calendar events --json
yule calendar sync --force-refresh --json
yule calendar categories --json
yule calendar cache inspect --json
yule calendar cache cleanup --json
yule planning daily --json
yule planning snapshot --json
yule daily warmup --json
yule planning checkpoints --at 2026-04-22T09:50:00+09:00 --json
yule discord bot
yule discord up --dry-run
yule discord member --role tech-lead --dry-run
yule engineer intake --prompt "랜딩 hero 정리" --write
yule engineer show --session <session_id>
```

로컬 환경에 따라 엔트리포인트 설치가 덜 맞물려 있을 때는 아래처럼 모듈 실행 방식으로 동일하게 사용할 수 있습니다.

```bash
PYTHONPATH=src python3 -m yule_orchestrator doctor
PYTHONPATH=src python3 -m yule_orchestrator context planning-agent
PYTHONPATH=src python3 -m yule_orchestrator calendar events --json
PYTHONPATH=src python3 -m yule_orchestrator calendar sync --json
PYTHONPATH=src python3 -m yule_orchestrator calendar cache cleanup --json
PYTHONPATH=src python3 -m yule_orchestrator planning daily --json
PYTHONPATH=src python3 -m yule_orchestrator planning snapshot --json
PYTHONPATH=src python3 -m yule_orchestrator daily warmup --json
PYTHONPATH=src python3 -m yule_orchestrator discord bot
PYTHONPATH=src python3 -m yule_orchestrator discord up --dry-run
PYTHONPATH=src python3 -m yule_orchestrator engineer show --session <session_id>
```

기간을 지정해서 일정 데이터를 읽을 수도 있습니다.

```bash
yule calendar events --start-date 2026-04-21 --end-date 2026-04-25 --json
```

## 캘린더 연동 메모

- 현재는 Naver CalDAV를 통해 일정 이벤트(`VEVENT`)와 CalDAV로 노출되는 할 일(`VTODO`)을 함께 읽습니다.
- 네이버 웹 화면의 할 일이 항상 CalDAV `VTODO`로 제공되는지는 계정 상태와 클라이언트 설정에 따라 달라질 수 있습니다.
- `todo_count`가 0이면 현재 CalDAV 응답에 할 일이 포함되지 않았을 가능성이 큽니다.
- `VTODO`는 기본적으로 지정한 기간 안에 해당하는 항목만 출력합니다.
- `yule calendar events --json` 실행 중 실패가 발생하면 `error.code`, `error.category`, `retryable`, `manual_action_required`, `alert_recommended`를 포함한 구조화된 에러 JSON을 반환합니다.
- 현재 에러 분류는 `configuration`, `validation`, `authentication`, `network`, `query`, `parsing`, `dependency`, `unknown` 범주를 사용합니다.
- `retry_strategy`는 `none` 또는 `backoff`를 사용하며, 이후 Planning Agent / Discord 알림 흐름에서 그대로 재사용할 수 있습니다.
- 세부 운영 기준은 [policies/runtime/common/calendar-error-handling.md](policies/runtime/common/calendar-error-handling.md)에 정리합니다.
- 같은 날짜 범위와 같은 캘린더 설정 요청은 SQLite 캐시를 재사용합니다.
- stale cache는 기본적으로 만료 후 7일 동안 남겨두고, `yule calendar cache cleanup`에서 정리합니다.
- 이 캐시 구조는 이후 daily-plan, Planning Agent, Discord 브리핑이 같은 저장소를 재사용할 수 있도록 설계되었습니다.
- 조회 결과를 동기화할 때 일정/할 일 항목 단위 상태를 upsert 하므로, 이후 완료 여부 변화와 최근 본 항목을 기준으로 다음 작업 추천 로직을 붙일 수 있습니다.
- `yule calendar sync`는 원격 캘린더를 읽어 캐시와 상태 DB를 채우는 운영용 명령입니다.
- CalDAV 원격 조회 시 `calendar_fetch_seconds`, `todo_fetch_seconds`, `calendar_discovery_seconds`를 결과 metrics에 남깁니다.
- `yule calendar categories`는 상태 DB에 저장된 `category_color` 숫자 코드와 항목을 보여줍니다.
- 범주 색상 정책은 [policies/runtime/agents/planning-agent/naver-category-policy.md](policies/runtime/agents/planning-agent/naver-category-policy.md)에 정리합니다.
- Discord 봇을 오래 켜둘 때는 먼저 `yule calendar sync`로 상태 DB를 채워두면, Planning Agent가 원격 조회보다 로컬 상태를 우선 사용합니다.

## Planning Agent

- `agents/planning-agent/agent.json`과 `agents/planning-agent/CLAUDE.md`를 추가했습니다.
- Planning Agent는 캘린더 일정, 캘린더 할 일, GitHub open issue, reminder JSON을 받아 daily plan을 만듭니다.
- 현재 버전은 설명 가능한 규칙 기반 우선순위, 추천 시간 블록, 이벤트 설명 기반 세부 실행 블록, 10분 전/5분 전 체크포인트 생성에 집중합니다.
- 기본 출력은 짧은 `discord_briefing`과 상세한 `morning_briefing`, `time_block_briefings`, `checkpoints`를 함께 제공합니다.
- snapshot에 저장되는 scheduled briefing은 `morning`, `work_start`, `lunch`, `evening` 4개입니다. Discord 자동 발송도 같은 4개 슬롯을 사용합니다.
- 아침 브리핑은 기상, 출근 준비, 권장 출발 시간, 업무 시작 시간을 구분해서 안내합니다.
- 추천 집중 작업은 기본적으로 `YULE_WORK_START_TIME` 이후 시간대에 배치합니다.
- 일정 이벤트가 없으면 전체 일정 작성 안내를 포함합니다.
- 설명이 있는 일정 이벤트는 시작 10분 전에 다음 일정으로 전환하는 재브리핑 체크포인트를 생성합니다.
- 설명이 비어 있는 일정 이벤트는 시작 10분 전에 세부 계획 작성 체크포인트를 생성합니다.
- `yule planning snapshot`은 daily-plan 결과 자체를 SQLite snapshot으로 저장합니다.
- `yule daily warmup`은 캘린더 동기화, GitHub issue 캐시, daily-plan snapshot 생성을 순서대로 실행하고 단계별 소요 시간을 `runtime-metrics`에 남깁니다.

```bash
yule planning daily --json
yule planning daily --date 2026-04-22 --github-limit 10
yule planning daily --reminders-file reminders.json --json
yule planning daily --use-ollama --json
yule planning snapshot --json
yule planning snapshot --use-ollama --json
yule daily warmup --json
yule daily warmup --use-ollama --json
yule planning checkpoints --at 2026-04-22T09:50:00+09:00 --json
```

이벤트 설명에 아래처럼 세부 시간표를 적으면 Planning Agent가 실행 블록과 체크포인트를 생성합니다.

```text
- 9시 ~ 10시 : 할일 목록 정리
- 10 ~ 1시 : 업무 수행 (회의 없음)
```

기본적으로 각 세부 블록이 끝나기 10분 전과 5분 전에 체크포인트를 만들며, `--reminder-lead-minutes 15,5`처럼 조절할 수 있습니다.

실제 알림 전송 전에, 지금 시각 기준으로 곧 울려야 하는 체크포인트만 뽑아내는 명령도 사용할 수 있습니다.

```bash
yule planning checkpoints --at 2026-04-22T09:50:00+09:00 --window-minutes 10 --json
```

## Engineering Agent

Engineering Agent는 `#업무-접수` 채널에서 자유 대화로 작업을 정리하고, 확정되면 workflow session과 작업 thread를 만듭니다. CLI로도 같은 workflow를 직접 조작할 수 있습니다.

```bash
yule engineer intake --prompt "Obsidian 기반 에이전트 지식 저장 구조 설계" --write
yule engineer approve --session <session_id>
yule engineer progress --session <session_id> --note "운영-리서치에 1차 자료 정리"
yule engineer complete --session <session_id> --summary "설계안 정리 완료"
yule engineer reject --session <session_id> --reason "요구사항 재정의 필요"
yule engineer show --session <session_id>
```

- `intake`는 dispatcher 계획, 참여 후보, 실행 후보, reference 제안을 포함한 접수 메시지를 생성합니다.
- `--write`를 붙인 세션은 승인 전까지 쓰기 작업이 차단됩니다.
- `complete --references-used refs.json`을 쓰면 완료 보고에 실제 반영한 reference를 함께 남길 수 있습니다.
- Discord 자유 대화에서 `새로 등록하지 말고 기존 스레드에서 이어가`처럼 말하면 열린 thread를 찾아 이어 붙이고, 새 세션은 만들지 않습니다.
- gateway는 매 요청마다 `decide_routing()`으로 현재 열려 있는 workflow session과 prompt를 비교해 4가지 action 중 하나로 라우팅합니다 — `join_existing_work`(유사한 미종료 작업이 있음), `create_new_work`(없음), `ask_for_clarification`(후보가 비슷해 결정이 위험함), `append_context_only`(`이 자료만 ... 참고로 붙여줘`처럼 명시적 부착 요청). `기존 맥락 참고` 같은 표현이 단독으로 쓰이면 자동 join을 강제하지 않고 similarity로만 판정합니다 — 옛 휴리스틱이 모든 "참고/이어가" 표현을 최신 thread에 강제로 붙이던 동작과 다릅니다. 사용자가 명시적으로 `이어가/새로 등록하지 말고`라고 적었지만 매칭되는 open thread가 없으면 새 세션을 만들지 않고 어느 작업에 합류할지 다시 묻습니다.
- Research 자료 수집은 한 번 호출하고 끝나지 않습니다. `auto_collect_or_request_more_input()`이 `score_research_sufficiency()`로 역할별 coverage(tech-lead·ai-engineer·backend·frontend·design·qa)를 평가하고, 부족하면 `ENGINEERING_RESEARCH_MAX_PROVIDER_CALLS` 예산 안에서 부족한 역할 위주로 추가 query를 돌립니다. 같은 URL/title이면 dedupe하고, 새 자료가 늘지 않는 round가 연속 4번이면 안전 종료합니다. 결과 `CollectionOutcome.sufficiency`에는 부족한 역할/source_type이 그대로 남아 운영자가 어디가 비어 있는지 확인할 수 있습니다.

Discord slash command는 `yule discord bot` 또는 `yule discord up` 실행 시 guild 단위로 등록됩니다.

```text
/engineer_intake prompt:"..." task_type:"landing-page" write_requested:true
/engineer_show session_id:"..."
/engineer_approve session_id:"..."
/engineer_reject session_id:"..." reason:"..."
/engineer_progress session_id:"..." note:"..."
/engineer_complete session_id:"..." summary:"..."
/engineer_review session_id:"..." summary:"..." severity:"medium"
/engineer_review_reply session_id:"..." feedback_id:"..." applied:"..."
```

- `/engineer_review`는 PR 리뷰, Copilot, 외부 에이전트, 사용자 피드백을 기존 session에 연결하고 역할별 재검토로 라우팅합니다.
- `/engineer_review_reply`는 적용/제안/남은 이슈를 같은 review cycle에 회신합니다.
- Discord slash command의 `complete`는 inline `references_used`를 받지 않으므로, reference 인용까지 닫으려면 CLI `yule engineer complete --references-used <json>`을 사용합니다.

### Obsidian 로컬 동기화

ResearchPack을 개인 Obsidian vault에 Markdown 파일로 저장하려면 `OBSIDIAN_VAULT_PATH`에 vault 절대경로를 설정합니다. **실제 절대경로는 git에 커밋되는 `.env.example`이 아니라 로컬 전용 `.env.local`에 둡니다** — `.gitignore`가 `.env*`는 제외하고 `.env.example`만 화이트리스트로 추적하기 때문입니다.

```bash
# .env.local 예시
OBSIDIAN_VAULT_PATH=/Users/<MY_USER>/local-dev/yule-agent-vault/obsidian-vault

# 사용
yule obsidian sync --session <session_id>            # 실제 쓰기 (overwrite 금지가 기본)
yule obsidian sync --session <session_id> --dry-run  # 경로/내용만 검증
yule obsidian sync --session <session_id> --overwrite
yule obsidian sync --session <session_id> --kind reference
```

vault 안에는 exporter가 정한 `Agents/Engineering/<kind>/YYYY-MM-DD_<slug>.md` 경로로 떨어집니다. 예: `$OBSIDIAN_VAULT_PATH/Agents/Engineering/Research/2026-04-30_stripe-pricing.md`. 같은 날짜·같은 slug로 sync가 반복되면 같은 폴더 안에서 `..._2.md`, `..._3.md` 식으로 자동 suffix가 붙어 기존 노트는 silently 덮이지 않고, `--overwrite`를 명시하면 suffix 없이 원래 파일을 그대로 교체합니다. 자세한 contract와 안전 정책은 `policies/runtime/agents/engineering-agent/obsidian-memory.md`를 참고하세요. `yule doctor`는 `obsidian vault` 체크를 자동 수행합니다.

vault를 git으로 관리한다면 `--git-commit` 옵션으로 sync 직후 자동 commit까지 남길 수 있습니다. 대상은 코드 저장소가 아니라 **Obsidian vault repo**이고, 기본 동작은 **opt-in**, **이번 sync가 만든 그 note 파일 하나만 stage/commit**, **push는 절대 하지 않음**입니다. vault repo에 이미 staged 변경이 있거나 vault가 git repo가 아니면 fail-loud로 종료합니다. `--dry-run --git-commit`은 파일/commit 모두 시뮬레이션만 합니다.

```bash
yule obsidian sync --session <session_id> --git-commit
yule obsidian sync --session <session_id> --git-commit --git-message "obsidian sync: hero 회의"
yule obsidian sync --session <session_id> --git-commit --dry-run
```

게이트웨이가 deliberation을 끝내면 `TechLeadSynthesis`(합의안/해야 할 일/더 조사할 것/사용자 결정 필요/승인 여부)도 session에 함께 저장되어, sync는 이 값을 복원해 `Agents/Engineering/Decisions/...` 아래에 5개 섹션을 갖춘 결정 노트로 떨어뜨립니다. synthesis 키가 없는 오래된 session은 안전하게 fallback해 `Research` 폴더의 자료 노트로만 떨어집니다.

## Discord Bot

- 단일 Discord Bot은 `yule discord bot`으로 실행합니다.
- planning-bot, engineering gateway, 멤버 봇을 한 번에 띄우려면 `yule discord up`을 사용합니다.
- 현재 slash command는 `/ping`, `/plan_today`, `/checkpoints_now`, `/engineer_intake`, `/engineer_show`, `/engineer_review`, `/engineer_review_reply`, `/engineer_approve`, `/engineer_reject`, `/engineer_progress`, `/engineer_complete` 입니다.
- `/plan_today`는 외부 API를 직접 기다리지 않고 저장된 daily-plan snapshot을 Discord 메시지로 정리해 보여줍니다.
- `/checkpoints_now`는 지금 시각 기준으로 다가오는 체크포인트를 빠르게 확인할 때 사용합니다.
- `--use-ollama`와 같은 세부 옵션은 아직 slash command 전체에 다 노출하지 않았고, 먼저 안정적인 최소 흐름에 집중한 상태입니다.
- snapshot이 없으면 `/plan_today`도 채팅 경로와 동일하게 즉시 "브리핑 데이터를 준비하고 있습니다" 안내 후 백그라운드에서 snapshot을 만들고 followup으로 브리핑을 이어 보냅니다.
- snapshot이 만료된 상태면 "마지막 동기화 기준 브리핑입니다" 문구를 붙입니다.
- `/plan_today` 응답과 자동 브리핑 메시지 상단에는 표시 시점의 실제 현재 시각(`_지금 YYYY-MM-DD HH:MM 기준_`)이 자동으로 추가되어, 6시간 캐시된 snapshot을 한참 뒤에 보더라도 사용자가 보는 시각과 메시지의 "지금" 표기가 어긋나지 않습니다.
- 옛 snapshot에 남아 있을 수 있는 "현재 X시 Y분입니다" 형태의 환각 시각 줄은 표시 직전에 자동으로 제거됩니다.
- 자동 브리핑 전송 시간은 `runtime-metrics`에 `discord_send` 단계로 저장됩니다.

```bash
yule discord bot
yule discord up --dry-run
yule discord up
yule discord member --role tech-lead --dry-run
```

아침 브리핑 운영 흐름은 먼저 snapshot을 만든 뒤 Discord 봇이 그 결과만 읽는 방식을 권장합니다.

```bash
yule daily warmup --json
yule discord bot
```

더 잘게 나누어 운영하고 싶다면 아래 순서로 스케줄링할 수 있습니다.

```text
05:50 yule calendar sync --force-refresh --json
05:55 yule github issues --limit 30 --force-refresh
05:58 yule planning snapshot --json
06:00 Discord bot scheduled morning briefing
13:00 Discord bot scheduled lunch briefing
18:00 Discord bot scheduled evening briefing
```

이 구조에서는 Discord 봇이 브리핑 시점에 캘린더나 GitHub API 응답을 기다리지 않습니다.

## Memory 인덱스

`yule memory`는 Obsidian vault, 저장소 정책 문서(`policies/`, agent `CLAUDE.md`, `README.md`), 최근 workflow session artifact(research_pack/synthesis)를 로컬 SQLite FTS5에 색인하고 검색하는 layer입니다. 네트워크 의존이 없고, 결정적이며, 인덱스 파일은 `.cache/yule/memory.sqlite3` (또는 `YULE_MEMORY_DB_PATH`)에 떨어집니다.

```bash
yule memory reindex                                  # 모두 재색인
yule memory reindex --skip-obsidian                  # vault 미설정 시 부분 재색인
yule memory search "Obsidian sync 정책" --limit 5
yule memory search "hero copy" --source-kind obsidian --note-kind decision
yule memory search "운영-리서치" --role engineering-agent/tech-lead --json
```

검색 결과는 `[source_kind] title` + 경로 + role/task_type/note_kind/tags/score + snippet으로 출력합니다. `--json`은 retrieval 파이프라인에서 그대로 소비할 수 있는 평면 객체 배열을 반환합니다. reindex는 idempotent하며 source_kind별로 기존 슬라이스를 비우고 다시 채우므로 삭제된 노트도 깔끔히 사라집니다.

## 테스트

기본 자동 테스트는 표준 라이브러리 `unittest`로 실행합니다.

```bash
python3 -m unittest discover -s tests -t .
```

### 집중 테스트 묶음

영역별로 좁혀서 돌리고 싶을 때 사용하는 묶음입니다. 라우터 파일이 관심사별로 3개로 쪼개져 있어, 회귀 디버깅 시 실패 위치가 곧 영역을 가리킵니다.

- engineering-agent (라우팅/persistence/forum + 라우팅 결정 단위 테스트):

  ```bash
  python3 -m unittest \
    tests.test_engineering_channel_router_routing \
    tests.test_engineering_channel_router_persistence \
    tests.test_engineering_channel_router_forum \
    tests.test_engineering_routing
  ```

- 리서치 sufficiency / collector 루프:

  ```bash
  python3 -m unittest \
    tests.test_research_collector_sufficiency \
    tests.test_research_sufficiency
  ```

- memory 인덱스 / retrieval:

  ```bash
  python3 -m unittest \
    tests.test_memory_index \
    tests.test_memory_retrieval
  ```

- Obsidian 동기화:

  ```bash
  python3 -m unittest \
    tests.test_obsidian_export \
    tests.test_obsidian_writer \
    tests.test_cli_obsidian_sync
  ```

### 공유 fixture

라우터 테스트에서 반복되는 가짜 Discord 채널/메시지/세션 객체는 `tests/_helpers.py`에 모여 있습니다. 새 라우터 테스트를 추가할 때는 `tests._helpers`에서 `FakeChannel`, `FakeMessage`, `FakeSession` 등을 import해 사용하세요.

### Optional dependency skip

`tests/test_calendar_category_color.py`는 `icalendar` 패키지가 설치돼 있어야 의미 있게 돌아갑니다. 패키지를 설치하지 않은 dev 환경에서는 자동으로 skip 처리되도록 가드가 들어 있어 전체 `unittest discover`가 실패하지 않습니다. 실제로 캘린더 경로를 확인하려면 `pip install -e .`로 프로젝트 의존성을 설치해 주세요.

## 로컬 전용 파일

아래 파일과 폴더는 로컬 실행 상태이거나 민감 정보가 포함될 수 있으므로 Git에 올리지 않습니다.

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

`src/yule_studio_agent.egg-info/`는 로그인 정보가 아니라 Python 패키지 설치 과정에서 생성되는 메타데이터입니다.
