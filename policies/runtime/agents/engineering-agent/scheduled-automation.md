# Engineering Agent — Scheduled automation policy (Hermes-inspired, issue #59)

본 정책은 Yule 의 **반복 작업 / scheduled work** 운영 규칙을 정한다. Hermes Agent 의 `cron/scheduler.py` + `jobs.json` 패턴을 Yule 환경에 맞게 흡수하되, secret 노출 표면 / destructive 작업 / multi-platform 안전 가드를 강하게 둔다.

이 문서는 issue #59 ([Hermes 흡수 결정 D-6](#)) 의 구현물이다.

## 1. 핵심 원칙

1. **destructive 작업 금지.** push to main / merge / deploy / `rm -rf` / git reset 류는 어떤 scheduled job 도 호출 못한다.
2. **secret 미주입.** scheduled job 자체가 secret 을 들고 있지 않는다 — 인증된 Yule worker 환경 (`.env.local`) 만 secret 을 본다.
3. **단일 platform.** Discord 외 새 messenger 로 결과 전송 금지 ([decision D-1 비도입](#)).
4. **운영자 명시 등록.** scheduled job 은 운영자가 정책 따라 등록 — agent 자율 등록 금지.
5. **결과 audit 가시.** 모든 job 실행은 `agent_ops_audit` row 1 건 + supervisor 진단 노출.

## 2. 적용 영역

| 영역 | 도입 형태 | 비고 |
|---|---|---|
| **반복 작업 (cron-style)** | 본 정책 신규 | Hermes `jobs.json` 패턴 흡수 |
| 일 단위 briefing (이미 구현) | `discord/bot.py._run_daily_briefing_loop` 그대로 유지 | 본 정책의 special case |
| 일 단위 preparation (이미 구현) | `discord/bot.py._run_daily_preparation_loop` 그대로 | 본 정책의 special case |
| checkpoint notification (이미 구현) | `discord/bot.py._run_checkpoint_notification_loop` 그대로 | 본 정책의 special case |
| systemd timer / `yule run-service` (always-on worker) | 별도 — worker pool, 본 정책 영역 아님 | `docs/operations.md` |

## 3. job storage 형태 (contract)

후속 milestone 의 storage 는 다음 contract 를 따른다 — Hermes `jobs.json` 패턴 + Yule 안전 가드.

### 3.1 storage 경로

`.cache/yule/scheduled-jobs.json` (gitignored, `.env*` 와 같은 디렉토리 정책)
또는 SQLite `scheduled_jobs` table — 후속 milestone 에서 결정. 본 정책은 *contract* 만.

### 3.2 job entry schema

```json
{
  "job_id": "<stable id, 12 hex>",
  "name": "<운영자 친화 이름>",
  "schedule": {
    "kind": "interval | weekly | monthly | once",
    "spec": "...",
    "tz": "Asia/Seoul"
  },
  "trigger": {
    "kind": "engineer_intake | research_collect | retrospective_check | memory_reindex | obsidian_sync_check",
    "payload": { "...": "..." }
  },
  "deliver": {
    "channel": "discord:<channel_id_env_key>",
    "format": "summary"
  },
  "safety": {
    "destructive_allowed": false,
    "max_runtime_seconds": 600,
    "approval_required": false,
    "approval_action": null
  },
  "audit": {
    "owner": "engineering-agent/tech-lead",
    "registered_by": "<operator>",
    "registered_at": "<ISO>",
    "last_run_at": "<ISO|null>",
    "last_outcome": "ok | failed | skipped | denied"
  }
}
```

### 3.3 trigger.kind whitelist

다음 5 가지만 인정. 새 trigger 는 본 정책 §8 절차 따라.

| trigger.kind | 의미 | safety constraint |
|---|---|---|
| `engineer_intake` | `yule engineer intake --prompt "..."` 와 동일 — 새 세션 생성 | `--write` 자동 부여 금지. write 는 항상 운영자 승인 |
| `research_collect` | 특정 topic 으로 자료 수집만 — 결과 영속화 yes | 코드 변경 / git commit 금지 |
| `retrospective_check` | 회고 후보 안내 ([self-improvement-flow §3.1](./self-improvement-flow.md#31-lifecycle-단계--회고-후보-알림)) — supervisor 진단으로 surface | read-only |
| `memory_reindex` | `yule memory reindex` — vault 인덱싱 갱신 | secret 영역 인덱싱 안 함 (이미 구현됨) |
| `obsidian_sync_check` | 미동기화 세션 안내 — 실제 sync 자동 실행 안 함 | read-only — sync 는 사용자 명시 |

## 4. safety 가드 (필수)

### 4.1 destructive 작업 금지 (코드 차원에서 차단)

후속 milestone 의 scheduler 는 다음을 *호출 자체* 가 안 되게 만든다:

- `git push` / `git reset --hard` / `git checkout --` / `git rebase -i` / `git commit --amend`
- `gh pr merge` / `gh pr close --delete-branch`
- `rm -rf` / `find -delete` / DB drop 류
- `yule github smoke-pr --live` (운영자 명시만)
- secret rotation 류

위 호출이 trigger.payload 안에 들어 있어도 scheduler 가 실행 거부 + audit "denied: destructive_command_in_payload" 기록.

### 4.2 max runtime cap

job entry `safety.max_runtime_seconds` (기본 600 = 10 분) 초과 시 strict timeout. timeout 은 `last_outcome=failed` 로 기록.

### 4.3 secret 미주입

scheduled job payload 에 `${ENV_VAR}` 같은 expansion 은 허용하지 않는다 — payload 는 plain string 만. secret 이 필요한 작업(예: GitHub App write) 은 *Yule worker 환경* 에서만 실행되고 worker 가 자기 env 에서 secret 을 읽는다.

### 4.4 approval 필요한 작업

`safety.approval_required: true` 인 job 은 trigger 직후 `#승인-대기` 카드 게시까지만 — 실행은 운영자 승인 후 (G6 approval routed runner 와 동일 path).

## 5. delivery — Discord 단일

`deliver.channel` 은 다음 형태만 인정:
- `discord:<env_key>` — `.env.local` 의 channel_id env key 이름. 직접 hardcode 금지.

새 platform (Telegram / Slack / Email) 추가는 본 정책 §8 절차 + decision note + secret 정책 검토 필수.

`deliver.format`:
- `summary` — 한 줄 요약 + 자세한 내용은 supervisor 진단 / Obsidian.
- `full` — 결과 markdown 그대로 (max 2000 chars per Discord 메시지 — `discord/research_forum.split_forum_starter_and_replies` 패턴).

## 6. audit 흐름

모든 scheduled job 실행은 1 건의 `agent_ops_audit` entry:

```json
{
  "action": "scheduled_job",
  "autonomy_level": "L1",
  "summary": "job=<job_id> name=<name> outcome=<ok|failed|denied> duration=<ms>",
  "outcome": "ok | failed | denied",
  "references": ["<vault path or url if any>"],
  "actor": "engineering-agent/scheduler"
}
```

`yule supervisor run --once` 가 본 entry 를 cross-section 으로 노출 — "지난 7 일 scheduled jobs" 라인.

## 7. Hermes vs Yule 차이 (명시)

| Hermes | Yule |
|---|---|
| `jobs.json` 운영자 + LLM agent 모두 등록 가능 | 운영자만 등록 — agent 자율 등록 금지 |
| `deliver: "telegram:chat_id"` | `deliver: "discord:<env_key>"` 만 |
| 자연어 schedule parse | 명시 schedule.kind + spec 만 (운영자 명시) |
| LLM 으로 자체 prompt 실행 | trigger.kind whitelist 5 종 |
| script 필드로 임의 Python 실행 | script 필드 없음 — trigger.kind 로만 |
| in-process tick() | systemd timer 또는 Yule worker (후속 결정) |

Yule 은 single-operator 환경 + audit-traceable 정책 — 본 차이가 안전 가드의 핵심.

## 8. 후속 milestone

1. **scheduler 코드** — `agents/lifecycle/scheduler.py` (정책 §3 contract 구현). 우선순위 P1.
2. **CLI** — `yule engineer schedule add | list | remove` — 운영자 등록 / 조회 / 해제. 우선순위 P1.
3. **`#봇-상태` 자동 보고** — 매일 1 회 scheduled job summary. 우선순위 P2.
4. **systemd timer 통합** — Yule run-service 와 결합. 우선순위 P3.

각 milestone 별 issue + decision note.

## 9. 변경 절차

1. 새 trigger.kind 추가 — issue + decision note + 본 정책 §3.3 갱신.
2. 새 platform delivery 추가 — issue + decision note + secret 정책 검토 + 본 정책 §5 갱신.
3. destructive 작업 가드 변경 — 회사 정책 / supervisor 검토 필수. 본 정책 §4.1 의 차단 목록은 단일 source-of-truth.

## 10. 변경 이력

| 일자 | 변경 |
| --- | --- |
| 2026-05-08 | 초기 작성 — issue #59 의 Hermes 흡수 결정 D-6 구현물. job storage contract / trigger whitelist 5 종 / safety 가드 / Discord 단일 delivery / audit 흐름 / 후속 milestone 정리 |
