# 캘린더 운영 메모

CalDAV 연동 거동과 캐시 운영 노하우를 정리한다.

## 거동

- 현재는 Naver CalDAV 를 통해 일정 이벤트(`VEVENT`) 와 CalDAV 로 노출되는 할 일(`VTODO`) 을 함께 읽는다.
- 네이버 웹 화면의 할 일이 항상 CalDAV `VTODO` 로 제공되는지는 계정 상태와 클라이언트 설정에 따라 달라질 수 있다.
- `todo_count` 가 0 이면 현재 CalDAV 응답에 할 일이 포함되지 않았을 가능성이 크다.
- `VTODO` 는 기본적으로 지정한 기간 안에 해당하는 항목만 출력한다.
- `yule calendar events --json` 실행 중 실패가 발생하면 `error.code`, `error.category`, `retryable`, `manual_action_required`, `alert_recommended` 를 포함한 구조화된 에러 JSON 을 반환한다.
- 현재 에러 분류: `configuration`, `validation`, `authentication`, `network`, `query`, `parsing`, `dependency`, `unknown`.
- `retry_strategy` 는 `none` 또는 `backoff`. 이후 Planning Agent / Discord 알림 흐름에서 그대로 재사용 가능.
- 세부 운영 기준: [policies/runtime/common/calendar-error-handling.md](../policies/runtime/common/calendar-error-handling.md).

## 캐시

- 같은 날짜 범위와 같은 캘린더 설정 요청은 SQLite 캐시를 재사용한다.
- stale cache 는 기본적으로 만료 후 7 일 동안 남겨두고, `yule calendar cache cleanup` 에서 정리.
- 이 캐시 구조는 이후 daily-plan, Planning Agent, Discord 브리핑이 같은 저장소를 재사용할 수 있도록 설계되었다.
- 조회 결과를 동기화할 때 일정 / 할 일 항목 단위 상태를 upsert 하므로, 이후 완료 여부 변화와 최근 본 항목을 기준으로 다음 작업 추천 로직을 붙일 수 있다.
- `yule calendar sync` 는 원격 캘린더를 읽어 캐시와 상태 DB 를 채우는 운영용 명령.
- CalDAV 원격 조회 시 `calendar_fetch_seconds`, `todo_fetch_seconds`, `calendar_discovery_seconds` 를 결과 metrics 에 남긴다.
- `yule calendar categories` 는 상태 DB 에 저장된 `category_color` 숫자 코드와 항목을 보여준다.
- 범주 색상 정책: [policies/runtime/agents/planning-agent/naver-category-policy.md](../policies/runtime/agents/planning-agent/naver-category-policy.md).
- Discord 봇을 오래 켜둘 때는 먼저 `yule calendar sync` 로 상태 DB 를 채워두면, Planning Agent 가 원격 조회보다 로컬 상태를 우선 사용한다.

## 권장 운영 흐름

```bash
05:50 yule calendar sync --force-refresh --json
05:55 yule github issues --limit 30 --force-refresh
05:58 yule planning snapshot --json
06:00 Discord bot scheduled morning briefing
```

이 구조에서는 Discord 봇이 브리핑 시점에 캘린더나 GitHub API 응답을 기다리지 않는다.

## Snapshot push to hompage

`yule-studio/hompage` 의 Calendar 페이지가 오늘 플랜을 표시하려면 agent 가
`DailyPlan.to_dict()` 결과를 `hompage/public/plan-snapshot.json` 로 push 해야
한다. hompage 는 그 정적 JSON 만 읽는다 (agent 24/7 배포 불필요).

### 흐름

```
yule planning daily --json    →   plan-snapshot.json (envelope)
                                  ↓ git commit / push
                          hompage repo main
                                  ↓ GitHub Pages deploy
                          https://yule-studio.github.io/hompage/plan-snapshot.json
                                  ↓ usePlanSnapshot()
                          Calendar 페이지의 "오늘 플랜" 섹션
```

### 일회성 수동 푸시

```bash
HOMPAGE_DIR=$HOME/local-dev/hompage \
  scripts/push_plan_snapshot.sh
```

스크립트는 (1) hompage repo rebase pull → (2) `yule planning daily --json`
→ (3) `public/plan-snapshot.json` 에 write → (4) diff 있으면 commit + push.
변경 없으면 zero-op.

### 주기 푸시 — systemd timer

```bash
sudo cp deploy/systemd/yule-plan-snapshot.service /etc/systemd/system/
sudo cp deploy/systemd/yule-plan-snapshot.timer   /etc/systemd/system/

# 환경변수 — HOMPAGE_DIR 등
sudo tee /etc/yule/yule-plan-snapshot.conf <<'EOF'
HOMPAGE_DIR=/opt/hompage
HOMPAGE_REMOTE=origin
HOMPAGE_BRANCH=main
YULE_BIN=/opt/yule-studio-agent/.venv/bin/yule
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now yule-plan-snapshot.timer
```

기본 cadence: 30분 간격 (`OnCalendar=*:00,30`). 더 빠르게 / 느리게 하려면
`yule-plan-snapshot.timer` 의 `OnCalendar` 수정 후 `systemctl daemon-reload
&& systemctl restart yule-plan-snapshot.timer`.

### 상태 확인

```bash
systemctl list-timers yule-plan-snapshot.timer    # 다음 트리거 시각
journalctl -u yule-plan-snapshot.service -n 50     # 최근 실행 로그
```

### git 인증

스크립트가 `git push` 까지 하려면 user `yule` 의 git 자격이 필요:

- **HTTPS + PAT**: `git config --global credential.helper store` + 한 번
  `git push` 로 PAT 입력하면 `~/.git-credentials` 저장.
- **SSH key**: `/etc/yule/yule-plan-snapshot.conf` 에
  `GIT_SSH_COMMAND="ssh -i /home/yule/.ssh/hompage_deploy_key"` 같이 주입.

### 실패 모드

| 증상 | 원인 / 해결 |
| --- | --- |
| exit 2 (pull rebase 실패) | hompage 에 충돌 — 사람이 main 정리 |
| exit 3 (`yule planning daily` 실패) | CalDAV / GitHub 일시 장애 — timer 재시도 시 자동 회복 |
| snapshot 이 stale (어제 날짜) | `yule planning daily` 의 plan_date 가 오늘이 아님 — 시간대 확인 |
| hompage 측에서 안 보임 | hompage Pages deploy 가 끝났는지 + browser hard refresh |

### hompage 측 (참고)

hompage 의 `src/pages/Calendar/Calendar.tsx` 가 `usePlanSnapshot()` 으로
fetch. `plan_date` 가 오늘 (Asia/Seoul) 아니면 자동 숨김 → stale 데이터 노출
X. receiver 구조는 hompage 의 `src/data/planSnapshot.ts` 참고.
