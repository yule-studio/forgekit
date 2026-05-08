# Planning Agent

`agents/planning-agent/` 의 정책에 따라 캘린더 일정 / 캘린더 할 일 / GitHub open issue / reminder JSON 을 받아 daily plan 을 만든다.

## 핵심 거동

- 설명 가능한 규칙 기반 우선순위, 추천 시간 블록, 이벤트 설명 기반 세부 실행 블록, 10 분 전 / 5 분 전 체크포인트 생성에 집중.
- 기본 출력은 짧은 `discord_briefing` 과 상세한 `morning_briefing`, `time_block_briefings`, `checkpoints` 를 함께 제공.
- snapshot 에 저장되는 scheduled briefing 은 `morning`, `work_start`, `lunch`, `evening` 4 개. Discord 자동 발송도 같은 4 개 슬롯.
- 아침 브리핑은 기상 / 출근 준비 / 권장 출발 시간 / 업무 시작 시간을 구분해서 안내.
- 추천 집중 작업은 기본적으로 `YULE_WORK_START_TIME` 이후 시간대에 배치.
- 일정 이벤트가 없으면 전체 일정 작성 안내 포함.
- 설명이 있는 일정 이벤트는 시작 10 분 전에 다음 일정으로 전환하는 재브리핑 체크포인트를 생성. 설명이 비어 있는 일정은 시작 10 분 전에 세부 계획 작성 체크포인트를 생성.
- `yule planning snapshot` 은 daily-plan 결과 자체를 SQLite snapshot 으로 저장.
- `yule daily warmup` 은 캘린더 동기화 / GitHub issue 캐시 / daily-plan snapshot 생성을 순서대로 실행하고 단계별 소요 시간을 `runtime-metrics` 에 남긴다.

## 명령

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

## 세부 시간표 인식

이벤트 설명에 다음과 같이 적으면 Planning Agent 가 실행 블록과 체크포인트를 생성한다.

```text
- 9시 ~ 10시 : 할일 목록 정리
- 10 ~ 1시 : 업무 수행 (회의 없음)
```

기본적으로 각 세부 블록이 끝나기 10 분 전과 5 분 전에 체크포인트를 만들며, `--reminder-lead-minutes 15,5` 처럼 조절할 수 있다.

## 곧 울릴 체크포인트 미리 보기

실제 알림 전송 전에, 지금 시각 기준으로 곧 울려야 하는 체크포인트만 뽑는다.

```bash
yule planning checkpoints --at 2026-04-22T09:50:00+09:00 --window-minutes 10 --json
```

## 우선순위 보정 (요약)

- GitHub 이슈 제목에 도메인 / 엔티티 / 스키마 / 마이그레이션 / infrastructure 같은 기반 키워드 → 우선순위 +. UI / 디자인 / 댓글 / 색상 같은 표면 키워드 → −.
- 라벨 정책: `policies/runtime/agents/planning-agent/github-label-policy.json` (env 로 override 가능). 자세한 키 이름과 기본값은 [configuration.md](configuration.md) §8.
- 네이버 카테고리 정책: `policies/runtime/agents/planning-agent/naver-category-policy.md`. `flexible: true` 는 시간 블록에 자동 배정되지 않고 추천 작업 목록에만 노출되는 상시 작업 분류용.
- PR 도 작업 후보로 다룬다 — ready 면 +10, draft 면 -10.

## Ollama 통합 (옵션)

`OLLAMA_PLANNING_ENABLED=true` 이면 `planning daily` / `planning snapshot` / `daily warmup` 에서 Ollama 가 아침 브리핑 문장을 다듬는다. Discord 응답을 다른 모델로 분리하려면 `OLLAMA_DISCORD_*` 키를 사용. 자세한 env 키는 [configuration.md](configuration.md) §4.
