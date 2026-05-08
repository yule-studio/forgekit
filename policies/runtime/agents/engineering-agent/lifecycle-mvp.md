# Engineering Agent — Lifecycle MVP policy

이 문서는 Engineering Agent MVP 가 Discord 입장에서 한 번의 사용자 요청을 어떻게 받아서 어떻게 끝내는지를 정의한다. 라이브 테스트에서 발견된 회귀를 반복하지 않기 위해 lifecycle 13 단계, persistence 키, lifecycle gate, typing/Obsidian 정책을 함께 둔다.

## 1. 목표

- 실제 회사 개발팀처럼 동작한다 — 누가 참여하고, 왜 참여하고, 언제 멈추고, 무엇을 보고하고, 어디에 기록할지를 스스로 관리한다.
- 모든 role 이 항상 움직이지 않는다. 필요한 role 만 참여한다.
- 모든 중요한 판단과 산출물은 SQLite + Obsidian 에 기록된다.
- 사용자에게 보이는 모든 신호 (typing / 메시지 / supervisor) 는 실제 lifecycle 상태와 일치한다.

## 2. 책임 분리

| 단계 | 책임 모듈 | 핵심 surface |
|---|---|---|
| gateway/router (오케스트레이터) | `discord/engineering_channel_router.py` | `route_engineering_message`, gate 진입점, 흐름 결정만 |
| 세션 resolve | `agents/session_resolver.py` | `resolve_session_for_message`, `extract_explicit_session_id` |
| role 선정 | `agents/role_selection.py` | `recommend_active_roles`, `apply_role_selection_to_extra` |
| lifecycle persistence | `agents/lifecycle_persistence.py` | `merge_session_extra`, `persist_thread_link`, `persist_research_forum_link`, `persist_research_pack_state`, `persist_work_report_state`, `to_json_safe` |
| research_pack persistence | `agents/research_persistence.py` | `persist_research_artifacts` |
| research pipeline | `agents/research_collector.py`, `agents/research_budget.py`, `agents/research_sufficiency.py` | `auto_collect_or_request_more_input`, `decide_budget(active_roles=…)` |
| deliberation / member-bot | `discord/engineering_team_runtime.py`, `discord/member_bot.py`, `agents/deliberation.py` | `handle_research_turn_message`, `deliberation_research_role_sequence` |
| lifecycle status | `agents/lifecycle_status.py` | `compute_lifecycle_status`, `can_generate_final_work_report`, `can_write_obsidian_record` |
| work_report / meeting_minutes | `agents/work_report.py`, `agents/meeting_minutes.py` | `build_work_report` (status gate 포함), `build_meeting_minutes` |
| Obsidian pipeline | `agents/obsidian_export.py`, `agents/obsidian_approval.py` | `render_research_note`, `render_work_report_note`, `execute_pending_proposal` |
| supervisor / status | `agents/session_status.py`, `discord/engineering_conversation.format_status_diagnostic_response` | `diagnose_session`, status 라인 |
| typing | `discord/typing_indicator.py` | `typing_context`, `should_type_for_gateway_action`, `should_type_for_member_research` |

라우터는 위 모듈을 호출하는 흐름만 결정한다. 새 책임이 router 에 들어가면 즉시 helper 로 빠진다.

## 3. lifecycle 상태 13 단계

```
intake
→ triage
→ role_selection
→ research_planning
→ role_scoped_research
→ sufficiency_check
→ deliberation
→ synthesis
→ interim_report     ← work_report.status="interim"
→ insufficient_report ← work_report.status="insufficient"
→ final_report        ← work_report.status="ready" / "final"
→ obsidian_preview    ← obsidian_approval.build_save_proposal
→ obsidian_recorded   ← execute_pending_proposal 성공
   (optional)
→ coding_authorization_pending
→ coding_job_ready
```

## 4. session.extra persistence policy

모든 lifecycle 상태는 `session.extra` 의 명시 키로 박힌다. SQLite `json_valid` 가 True 를 유지해야 하므로 모든 값은 `agents.lifecycle_persistence.to_json_safe` 를 거쳐야 한다.

| key | writer | meaning |
|---|---|---|
| `session.thread_id` (top-level) | `persist_thread_link` | Discord 작업 thread id |
| `active_research_roles` | `apply_role_selection_to_extra` | tech-lead 가 선정한 role 리스트 |
| `excluded_research_roles` | `apply_role_selection_to_extra` | 의도적으로 제외된 role |
| `role_selection_source` / `role_selection_reasons` | 같음 | user_explicit / tech_lead_rule / fallback + 사유 |
| `research_pack` | `persist_research_pack_state` (또는 `research_persistence.persist_research_artifacts`) | 자료 dict snapshot |
| `research_status` | 같음 | "ready" / "insufficient" / "missing" |
| `research_source_count` | 같음 | sources 길이 |
| `research_stop_reason` | 같음 | sufficient / budget_exhausted / no_progress / role_rotation_exhausted / no_initial_provider_hit / missing_required_source_type / user_input_needed |
| `research_missing_roles` | 같음 | active 중 자료 부족한 role |
| `research_active_roles` | 같음 | collection_outcome.active_roles 의 mirror |
| `research_pack_error` | 같음 (실패 시) | { step, reason } |
| `research_synthesis` | `agents/research_persistence` | { v, consensus, todos, open_research, user_decisions_needed, approval_required, approval_reason } |
| `research_forum_thread_id` / `research_forum_thread_url` | `persist_research_forum_link` | forum thread 링크 |
| `research_open_call_posted` / `research_open_call_error` | 같음 | open-call directive 게시 결과 |
| `forum_comment_mode` | 같음 | "member-bots" / "gateway" |
| `forum_kickoff_posted` / `forum_kickoff_error` | 같음 (legacy mirror) | 위 두 개의 backward-compat |
| `played_roles` / `team_conversation.played_roles` | `engineering_team_runtime` | 실제로 발화한 role |
| `work_report` | `persist_work_report_state` 또는 router `_emit_work_report_preview` | 보고서 dict (title / status / executive_summary / risks / proposed_next_steps / requires_code_change / participants / reference_count / research_stop_reason / under_covered_roles) |
| `work_report_status` | 같음 | "interim" / "insufficient" / "ready" / "final" |
| `coding_proposal` | router coding gate | proposal payload |
| `coding_job` | 같음 | approved coding job |
| `canonical_prompt_override` | router | continuation 시 보존되는 원문 |
| `latest_continuation_prompt` | 같음 | 가장 최근 continuation 본문 |
| `resumed_thread_id` | 같음 | continuation 으로 이어붙인 thread id |
| `persistence_error` | `lifecycle_persistence.merge_session_extra` (실패 시) | { step, reason, keys } |

### 정책

- silent failure 금지. 어떤 helper 도 예외를 삼키지 않는다 — 모두 `persistence_error` / `research_pack_error` / `work_report_error` 같은 구조화된 stamp 를 남긴다.
- 성공 후에는 stale error stamp 를 자동 제거한다.
- `update_session` 실패는 caller 에게 `PersistenceResult(ok=False, step, reason, keys)` 로 전달된다.

## 5. work_report lifecycle gate

`agents.lifecycle_status.compute_report_status(session)` 가 단일 출처. 결과는 다음 표를 따른다.

| 조건 | 결과 |
|---|---|
| research_pack 없음 OR source_count=0 OR explicit research_status="insufficient" | INSUFFICIENT — final 금지 |
| missing_roles 있음 OR synthesis 없음 | INTERIM — final 금지 |
| 위 둘 다 통과 | READY — final 가능 |
| (caller 가 사용자 승인 후 명시적으로 promote) | FINAL |

`format_work_report_markdown` 은 status 라벨을 헤더에 출력하고, INSUFFICIENT/INTERIM 인 경우 "코드 수정 필요" CTA 를 차단한다 (대신 "코드 수정 후보 (lifecycle 완료 후 권한 제안 가능)" placeholder).

## 6. Obsidian write gate

`agents.lifecycle_status.can_write_obsidian_record(session)` 가 단일 출처. 차단 조건:

- session 이 None
- research_status != "ready" AND source_count == 0 → "research_pack 미수집 (자료 0건)"
- work_report.status ∈ {"insufficient", "interim"}
  - missing_roles 있음 → "역할 토의 미완료 (...) 라 저장할 수 없어요"
  - 그 외 → "work_report status=<X> 라 final 저장 단계가 아니에요"

차단 시 router 는 사용자에게 한국어 사유를 그대로 노출한다. forum thread 미연결만 있는 경우는 통과한다 (status diagnostic 에서 경고만).

## 7. Obsidian path layout

- `10-projects/<project>/research/` ← ResearchPack
- `10-projects/<project>/decisions/` ← TechLeadSynthesis
- `10-projects/<project>/meeting-notes/` ← MeetingMinutes
- `10-projects/<project>/reports/` ← WorkReport (Phase 5 신규)
- `10-projects/<project>/references/` ← URL/이미지 참고
- `10-projects/<project>/task-logs/` ← 운영자 직접 기록
- `10-projects/<project>/knowledge/` ← knowledge writer

`<project>` 우선순위: 명시 `--project` → `session.extra["project"]` → `OBSIDIAN_DEFAULT_PROJECT` env → `yule-studio-agent`.

## 8. typing policy

| 상황 | typing | 이유 |
|---|---|---|
| on_message 진입 직후 | OFF | 처리 대상 확정 전이므로 |
| bot author / slash command / 빈 content / non-engineering 채널 | OFF | `should_type_for_gateway_action` returns False |
| engineering 채널의 처리 대상 메시지 | ON (응답 branch 안에서) | 응답 본문이 곧 나간다는 신호 |
| inactive member bot 의 research-open | OFF | `handle_research_turn_message` 가 None 반환 → `should_type_for_member_research(will_post=False)` |
| active member bot 의 research-open | ON | outcome 이 non-None |
| typing 안에서 예외 발생 | typing OFF + `⚠️ <bot> ... 실패: <reason>` 응답 전송 | silent failure 금지 |

## 9. 라이브 테스트 절차

새 작업 / 후속 작업 / Obsidian 저장 / status 확인까지 한 번에 도는 시나리오:

1. **새 Research 요청**: 업무-접수 채널에서 입력
   ```
   [Research] 결제 모듈 멱등성 검증 — backend / qa / devops 관점에서 정리해줘
   ```
2. gateway 가 후보를 보여주면 `새 작업으로 진행` 으로 답.
3. SQLite 확인 (CLI: `sqlite3 .yule-cache/yule_cache.sqlite`):
   ```sql
   SELECT prompt, thread_id,
          json_extract(extra, '$.active_research_roles'),
          json_extract(extra, '$.excluded_research_roles'),
          json_extract(extra, '$.research_status'),
          json_extract(extra, '$.research_source_count'),
          json_extract(extra, '$.work_report.status'),
          json_extract(extra, '$.research_forum_thread_id')
     FROM workflow_sessions
     WHERE session_id = ?;
   ```
   - `prompt` 는 `[Research] 결제 …` 그대로
   - `thread_id` 가 채워짐
   - `active_research_roles` 에 `["tech-lead", "backend-engineer", "qa-engineer", "devops-engineer"]` 정도
   - `research_status` 는 `ready` 또는 `insufficient`, `research_source_count` 는 일치
   - `work_report.status` 는 lifecycle 진행 단계에 따라 `interim` / `insufficient` / `ready`
4. 같은 thread 안에서 `이 세션 기준으로 운영 리서치 어디까지 됐어?` → status 응답이 활성 role / 업무 보고서 / 자료 N건 / stop_reason 표시.
5. 임의 채널에서 `세션 abc123def456 기준으로 Obsidian에 저장해줘` → lifecycle 통과면 저장, 통과 안 되면 차단 사유 표시.
6. 비활성 role 의 member bot 은 forum 의 `[research-open:<sid>]` 를 봐도 typing/댓글 둘 다 발생하지 않음 (Discord 에서 사용자 입장 관찰).
7. typing context 안에서 에러 발생 시 `⚠️ <bot> ... 실패: ...` 메시지가 보임.

## 10. 다음 단계

- forum 자료 수집 query 자체를 active role 로 좁히기 (`feat/forum-active-role-query`)
- `persistence_error` / `research_pack_error` / `forum_publish_error` 를 status diagnostic 본문에 surface (`feat/status-surface-persistence-errors`)
- member-bot 댓글 후 `played_roles` 자동 영속화 → work_report status 가 자동 `interim → ready` graduate (`feat/played-roles-write-back`)
