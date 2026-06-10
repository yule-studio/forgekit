# Engineering Agent — Context compression policy (Hermes-inspired, issue #59)

본 정책은 Yule 의 **context / trajectory compression** 을 정한다. Hermes Agent 의 `agent/context_compressor.py` 가 보여주는 token threshold + 보호 영역 + tool output 압축 패턴을 Yule 의 lifecycle 안에 결합하되, **원문 prompt / decision / synthesis 본문은 절대 압축 / 삭제하지 않는다.**

이 문서는 issue #59 ([Hermes 흡수 결정 D-4](#)) 의 구현물이다.

## 1. 핵심 원칙

1. **audit traceability 는 모든 효율보다 우선.** 원문 prompt / synthesis consensus / decision body / agent_ops_audit entries 는 압축 / 요약 / 절단 대상이 아니다.
2. **압축은 *전송 비용* 통제 수단**. SQLite / Obsidian 영속 저장은 원본을 그대로. 압축은 LLM runner 입력 / `work_report.executive_summary` / Discord 메시지 표시 영역에만 적용.
3. **threshold 는 모델 context window 비율로 정의** — 절대 토큰 수가 아니라 모델별 비율. 모델 교체 시 자동으로 따라감.
4. **운영자 명시 압축이 최우선** — `/compress <topic>` 같은 명령 흐름 (Hermes 패턴) 이 정책에 들어와야 자동 압축이 잘못 가도 수동 복구 가능.

## 2. 압축 적용 영역 (4 surface)

| surface | 압축 적용? | 무엇이 압축되나 | 보호 영역 |
|---|---|---|---|
| **role-runner dispatch input** | 적용 — token threshold 시 | prior turns 의 middle 영역 | 첫 N=3 메시지 + 가장 최근 `tail_token_budget` 메시지 |
| **`work_report.executive_summary`** (already-final) | 적용 — `len > 1500` 시 자동 head + tail (knowledge writer 패턴) | 본문 중간 | 헤더 + 첫 단락 + 마지막 단락 |
| **Discord 메시지 표시** | 적용 — `> DISCORD_MESSAGE_LENGTH_LIMIT` 시 starter + reply chunks | 일부 | 구분 표시 (`(continued in reply)`) |
| **Obsidian export 파일 본문** | **압축 금지** — 영속 저장은 원본 | (해당 없음) | 전체 |
| **session.extra (SQLite)** | **압축 금지** — JSON-safe round-trip 필요 | (해당 없음) | 전체 |
| **agent_ops_audit entries** | **압축 금지** — 200 entries cap 만 | (해당 없음) | 전체 |

## 3. role-runner dispatch input 압축 (정책)

[M11b RoleRunner dispatcher](../../../../apps/engineering-agent/src/yule_engineering/agents/runners/role_runner.py) 가 `RoleRunnerInput.previous_decisions` 를 받아 LLM runner 에 보낼 때.

### 3.1 threshold

| 모델 family | context window (입력 한도) | threshold | 비고 |
|---|---|---|---|
| Claude Sonnet 4.x | 200K | 50% (100K input) | Hermes 기본값과 동일 |
| Claude Opus 4.x | 200K | 50% (100K input) | |
| Codex / GPT-5.x | 128K | 40% (51K input) | 출력 budget 여유 확보 |
| Ollama 로컬 (qwen-72b 등) | 32K~128K | 35% | 로컬 latency 고려 |
| **Yule deterministic fallback** | (해당 없음) | 압축 안 함 | template-driven, prompt 가 짧음 |

threshold 도달 시 LLM 기반 자동 요약 발동. **Yule deterministic fallback 은 본 정책에서 제외** — 정해진 짧은 template 만 사용.

### 3.2 보호 영역

압축 시 다음은 절대 손대지 않는다:

- 첫 3 개 메시지 (system prompt + 원문 prompt + 첫 합의 메시지)
- 가장 최근 5 개 메시지 (last 5 turns)
- 모든 `kind=decision` 또는 `role=tech-lead` synthesis 메시지
- Yule 의 `RoleProfile.output_sections` 가 박은 헤더 (`핵심 판단` / `다음 액션` 등)

### 3.3 압축된 turn 표현 형태

middle turn → 한 줄 placeholder:

```
[role-take@<role>] <문장 요약 ≤ 80자> (생략된 본문 N자, audit_id=<id>)
```

audit_id 는 `agent_ops_audit` 의 entry_id 를 가리킴. 압축 후에도 원문 retrieval 가능.

### 3.4 압축 트리거 (read-side only)

압축은 *전송 시점에만* — SQLite / Obsidian 의 영속 저장 본문은 원본 그대로.

## 4. work_report 압축 (정책)

`agents/work_report.py` 의 `executive_summary` 는 이미 부분 요약 — 하지만 long body 는 그대로 들고 다님.

| 단계 | 본문 처리 |
|---|---|
| `compute_report_status() == INSUFFICIENT` | 본문 불생성 — placeholder 만 |
| `compute_report_status() == INTERIM` | `executive_summary` (≤ 800자) + 정해진 섹션 |
| `compute_report_status() == READY` | full body 생성 — 압축 없음 |
| `compute_report_status() == FINAL` | full body + audit reference 부착 |

본 정책은 *INTERIM 단계의 800자 cap* 을 명시 source of truth 로 삼는다. 코드 (`work_report.py`) 와 본 정책이 같은 한계를 본다.

## 5. Discord 메시지 압축 (이미 구현됨)

`discord/research_forum.py` 의 `truncate_for_starter_message` + `split_forum_starter_and_replies` 가 이미 처리. 본 정책은 *원본 보존* 만 재확인:

- starter 가 limit 초과 시 reply chunk 로 분할.
- 원본은 Obsidian export 에 그대로 들어감.
- forum-starter 안 truncation marker (`_본문이 길어 ...`) 는 사용자 가시 표식 — 원본은 vault.

## 6. 절대 금지 (압축 대상 제외)

다음은 어느 단계에서도 압축 / 절단 / 요약하지 않는다:

| 영역 | 이유 |
|---|---|
| `WorkflowSession.prompt` 원문 | audit 의 root |
| `TechLeadSynthesis.consensus` | 결정 본문 |
| `TechLeadSynthesis.user_decisions_needed` | 사용자 승인 표면 |
| Obsidian frontmatter 의 `original_prompt` | session.prompt 의 vault mirror |
| `agent_ops_audit[].outcome` / `summary` | 의사결정 traceability |
| approval card body | 운영자 승인 게이트의 가시성 |
| commit message | git 의 truth |
| PR description body (G3 `render_pr_body`) | GitHub 의 contract |

위 영역에 압축이 들어가면 lifecycle gate 자체가 의미를 잃는다.

## 7. 운영자 명시 압축 (Hermes /compress 패턴)

수동 복구 / 명시 압축이 가능해야 자동 압축이 잘못 가도 운영 가능.

후속 milestone 의 CLI 후보:
- `yule engineer compress --session <id> --topic <topic>` — 특정 세션의 prior turns 를 LLM 으로 명시 요약 + 보고. 영속 저장은 새 task-log note 로.
- `yule engineer usage --session <id>` — 현재 세션의 token / cost 누적량 표시 (Hermes `/usage` 대응).

본 phase 는 정책까지. 코드는 별도 issue.

## 8. 후속 milestone

1. **role-runner dispatch input 압축 wiring** — `agents/runners/bootstrap.py` 가 `previous_decisions` 길이를 보고 [§3.1 threshold](#31-threshold) 도달 시 LLM 압축 호출. 우선순위 P1.
2. **`yule engineer compress` / `yule engineer usage` CLI** — 우선순위 P2.
3. **work_report INTERIM cap 코드화** — 800자 cap 을 `agents/work_report.py` 상수로 박고 본 정책과 cross-link. 우선순위 P3.

## 9. 변경 이력

| 일자 | 변경 |
| --- | --- |
| 2026-05-08 | 초기 작성 — issue #59 의 Hermes 흡수 결정 D-4 구현물. 4 surface 별 압축 적용 / 모델별 threshold / 보호 영역 / 절대 금지 영역 / 후속 milestone 정리 |
