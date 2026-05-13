# CI Discord 알림 운영 가이드

> **현재 증상**: `.github/workflows/ci.yml` 의 `notify` job 이 `success` 로 끝나지만 Discord 채널엔 메시지가 안 옴.

## 1. 원인

`secrets.DISCORD_CI_WEBHOOK_URL` 가 비어있으면 workflow 가 다음 라인을 찍고 step 을 그대로 `success` 로 종료한다:

```
DISCORD_CI_WEBHOOK_URL is empty. Skip notification.
```

- 사람 눈에는 보이지 않는다 (Actions 탭의 step log 안에만 있음).
- `step.conclusion` 이 `success` 라서 PR 페이지에서 정상 처리된 것처럼 보인다.
- 따라서 운영자는 "알림 시스템 자체가 죽었나?" 로 오해한다.

이 PR (`chore/ci-discord-notification-fix`) 에서 workflow 자체는 다음 3 가지를 추가했지만, **실제 알림이 가려면 운영자가 webhook secret 을 설정해야 한다**:

1. `::warning::` annotation — secret 미설정 시 PR / Actions 페이지에 큰 노란 박스로 표시
2. `curl --fail-with-body` + HTTP status 검증 — webhook 이 revoked / wrong URL 인 경우 step 이 red
3. `cancelled` / `skipped` 분기 — 옛 build 가 concurrency cancel 됐을 때도 ⚠️ embed 게시
4. `workflow_dispatch` trigger — Actions 탭에서 "Run workflow" 로 PR 없이 알림 path 테스트 가능

## 2. Secret 설정 절차 (운영자 수동 작업)

### 2.1 Discord webhook URL 생성
1. Discord 서버 → 채널 (CI 알림 받을 채널) → 톱니바퀴 → 통합 (Integrations) → 웹훅 → 새 웹훅
2. 이름 (예: `yule-studio-ci`) + 채널 선택
3. **웹훅 URL 복사** — `https://discord.com/api/webhooks/XXXXXX/YYYYYY` 형태

### 2.2 GitHub repo secret 등록
1. https://github.com/yule-studio/yule-studio-agent/settings/secrets/actions
2. **New repository secret** 클릭
3. Name: `DISCORD_CI_WEBHOOK_URL`
4. Value: 2.1 에서 복사한 URL
5. Add secret

### 2.3 (선택) 이미지 변수 등록
첨부 이미지를 쓰고 싶으면 — **Settings → Secrets and variables → Actions → Variables** 탭에서:
- `DISCORD_CI_SUCCESS_IMAGE_URL` (성공 시 표시할 이미지 URL)
- `DISCORD_CI_FAIL_IMAGE_URL` (실패 시 표시할 이미지 URL)

## 3. 검증 절차

### 3.1 manual trigger 로 즉시 검증
secret 등록 후 PR 안 만들고도 검증 가능:
1. https://github.com/yule-studio/yule-studio-agent/actions/workflows/ci.yml
2. **Run workflow** → 브랜치 선택 → 실행
3. 약 1-2 분 후 — 등록한 Discord 채널에 `✅ CI Success` 메시지가 와야 함

### 3.2 step log 검증
secret 이 제대로 설정됐는지는 `Preflight — verify DISCORD_CI_WEBHOOK_URL is set` step 의 log 에서:
- 정상: `Discord webhook secret is configured. Continuing with notification.`
- 미설정: `::warning:: Discord CI notification skipped` + PR 페이지 노란 박스

### 3.3 webhook 자체의 응답 검증
Notify success / failure / cancelled step 의 마지막 라인:
```
Discord webhook responded with HTTP 204
```
- `204 No Content` — Discord 정상 수신.
- `401 / 404 / 410 / 5xx` — webhook 잘못됨 / revoked / Discord 장애. step 이 red 로 fail 처리되어 명확히 표시됨.

## 4. 실패 시 분기 동작 표

| 조건 | 본 PR 후 동작 |
| --- | --- |
| Secret 미설정 + test success | `::warning::` annotation + step skip (red 안 됨) |
| Secret 미설정 + test failure | `::warning::` annotation + step skip (test 실패만 visible) |
| Secret 설정 + webhook 정상 + test success | `✅ CI Success` 메시지 |
| Secret 설정 + webhook 정상 + test failure | `❌ CI Failure` 메시지 |
| Secret 설정 + webhook 정상 + test cancelled | `⚠️ CI cancelled` 메시지 |
| Secret 설정 + webhook revoked (HTTP 401) | step red + `::error:: Discord webhook rejected` annotation |

## 5. 본 doc 의 단일 진실

- Secret 이름: `DISCORD_CI_WEBHOOK_URL` (변경 시 `.github/workflows/ci.yml` 의 모든 step 환경 변수도 함께)
- 사용자 정책: ".env.local 에는 다 세팅되어 있을 것" — 이는 application 동작용 env 의 얘기이고, **GitHub Actions 의 repo secret 은 별개로 등록해야 함**. .env.local 은 GitHub workflow 가 읽지 않는다.
