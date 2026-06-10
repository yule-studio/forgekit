---
id: engineer-show
title: 세션 진단 (engineer show)
surface: both
slash: /engineer_show
cli: yule engineer show
allowed_roles:
  - tech-lead
  - user                      # read-only 진단이라 모든 호출자 허용
autonomy_level: L0_AUTO_RECORD_OPTIONAL
required_approval: false
references:
  - docs/engineering.md
  - apps/engineering-agent/src/yule_engineering/cli/engineer.py
  - policies/runtime/agents/engineering-agent/lifecycle-mvp.md
related_skills:
  - skills/research-collect.md
related_hooks:
  - hooks/research-first-gate.md
---

# Command: engineer-show

> **현재 단계:** 본 markdown 은 기존에 이미 동작하는 slash + CLI 의 *문서화 layer*. 새 코드 없음.
> **단일 owner:** 진단 결과의 작성·서술 주체는 `engineering-agent / tech-lead` (자동 응답이 아니라 tech-lead 의 status diagnostic 응답).

## When to Use

- 사용자가 "지금 어떻게 진행 중?" / "현재 상태 알려줘" / "왜 멈췄어?" 같이 물을 때.
- 운영자가 specific session id 의 lifecycle / role 활동 / research_pack / work_report 상태를 한 화면에 보고 싶을 때.
- gateway 가 routing 결정 (`decide_routing`) 후 사용자 요청이 status 응답으로 분기됐을 때.

## Trigger phrases (Korean)

- "지금 누가 어디까지 했어?"
- "현재 상태 알려줘"
- "진행 상황 어떻게 되고 있어?"
- "왜 멈췄어?"
- "이 세션 기준으로 운영 리서치 어디까지 됐어?"
- 명시적 호출: `/engineer_show session_id:<id>` 또는 `yule engineer show --session <id>`

## Inputs

| field | type | required | source |
| --- | --- | --- | --- |
| `session_id` | str | conditional | slash arg / CLI flag / 또는 자연어 본문에서 추출 |
| `current_channel` | int | optional | Discord 채널에서 호출 시 — 채널과 매칭되는 열린 session 자동 선택 |

`session_id` 가 명시되지 않으면 `decide_routing` 이 현재 채널의 열린 session 또는 가장 최근 active session 을 매칭. 매칭 실패 시 친절 안내.

## Outputs

| 항목 | 형태 | 출처 |
| --- | --- | --- |
| 활성 role 목록 | `- 활성 role: …` | `session.extra.active_research_roles` |
| 역할 활동 기록 | `- 역할 활동 기록:` 블록 | `session.extra.team_conversation.played_roles` |
| 역할 연구 결과 | `- 역할 연구 결과:` 블록 (provider / source_count / 핵심 미리보기) | `session.extra.role_research_results` |
| 활동 로그 요약 | `- 활동 로그: research_completed=N, …` | session 활동 카운터 |
| research_loop 보고 / synthesis | "기록됨" / "아직 기록되지 않음" | `session.extra.research_synthesis` |
| coding_job 상태 (있는 경우) | `- coding_job: pending-approval / ready` | `session.extra.coding_job` |
| obsidian write 여부 | (있으면) vault path | `session.extra.obsidian_writes` |

## Edge Cases

- **세션 매칭 실패** — "현재 채널에 매칭되는 열린 engineering-agent 세션이 보이지 않아요" 출력. 새 작업 만들기 안내.
- **research_pack missing** — "research_pack 미수집 (자료 0건)" 라인 노출.
- **work_report.status = interim / insufficient** — 헤더에 INTERIM / INSUFFICIENT 라벨.
- **fallback_audits 가 있음** — `- 마지막 fallback: …` 라인 추가.
- **persistence_error / research_pack_error / forum_publish_error** — `- 마지막 실패: …` 라인 추가 (lifecycle-mvp §10 참조).

## Examples

### Discord slash

```
/engineer_show session_id:abc123def456
```

응답 (요약):

```
**[engineering-agent] session abc123def456 진행 상황 — by tech-lead**

- 활성 role: tech-lead, devops-engineer, backend-engineer
- 역할 활동 기록:
  · devops-engineer — posted (open, 2026-05-08T09:32+09:00)
  · backend-engineer — posted (open, 2026-05-08T09:34+09:00)
- 역할 연구 결과:
  · devops-engineer — provider: tavily / 5 건 (핵심: rolling update + canary 전략)
  · backend-engineer — provider: brave / 4 건 (핵심: API 멱등성 패턴)
- 활동 로그: research_completed=2, research_started=2, sufficiency_passed=1
  · 마지막 이벤트: synthesis_recorded (2026-05-08T09:41+09:00)
- research_loop 보고 / synthesis: 기록됨
- work_report.status: ready
- coding_job: 없음
```

### CLI

```bash
yule engineer show --session abc123def456
yule engineer show --session abc123def456 --json
```

JSON 출력은 `session.extra` 의 핵심 키를 그대로 노출 (autonomy_policy 의 redact_secret_like 통과).

## Hard rails

- **secret 노출 금지.** Discord 채널 / CLI 출력 / log 어디에도 token / pem / Authorization 헤더 출력 금지. `redact_secret_like` 가 1 차 책임.
- **read-only.** 본 command 는 어떤 SQLite write / Discord post 도 일으키지 않는다. side_effects 0.
- **actor 라벨 = tech-lead.** 응답 헤더에 "by tech-lead" 표기 (단일 주체 정책).

## 검증 / 회귀

| 테스트 | 위치 | 상태 |
| --- | --- | --- |
| status diagnostic 본문 | `tests/discord/test_status_diagnostic.py` (기존) | ✅ 통과 |
| Phase 5 의 역할 연구 결과 / 활동 로그 블록 | `tests/discord/test_status_diagnostic_role_research.py` (기존) | ✅ 통과 |
| markdown frontmatter ↔ 코드 매핑 검증 | (후속 PR 의 loader) | ⏳ 대기 |
