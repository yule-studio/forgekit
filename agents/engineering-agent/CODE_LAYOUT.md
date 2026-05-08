# Engineering Agent — code layout & module ownership

이 문서는 engineering-agent lifecycle 의 각 단계가 어느 모듈에서 책임지는지 빠르게 찾기 위한 지도입니다. 코드 변경 전에 "이 책임이 어디에 있어야 하지?" 를 먼저 확인하는 용도이고, 큰 rename / 분할은 별도 브랜치로 진행합니다.

## Lifecycle stages → 책임 모듈

| stage | module(s) | 핵심 함수 / 클래스 |
|---|---|---|
| 1. intake / triage | `discord/engineering_channel_router.py` + `discord/engineering_conversation.py` | `route_engineering_message`, `build_engineering_conversation_response`, `is_non_actionable_prompt`, clarification cache |
| 2. role_selection | `agents/role_selection.py` | `recommend_active_roles`, `apply_role_selection_to_extra`, `active_roles_from_extra` |
| 3. research_planning | `agents/research_budget.py` | `decide_budget(active_roles=…)`, `_filter_targets_by_active_roles`, `ResearchBudgetPolicy` |
| 4. role-scoped research | `agents/research_collector.py` | `auto_collect_or_request_more_input(active_roles=…)`, `CollectionOutcome.active_roles` |
| 5. sufficiency_check | `agents/research_sufficiency.py` | `RoleSufficiencyTarget`, `_FOLLOWUP_ROLE_ORDER` (active 기반 좁히기는 phase 2 의 budget filter 가 처리) |
| 6. deliberation | `agents/deliberation.py` + `discord/engineering_team_runtime.py` | `run_deliberation_loop`, `TechLeadSynthesis`, `synthesis_to_dict`, `handle_team_turn_message` |
| 7. tech_lead_synthesis | `agents/deliberation.py` | `TechLeadSynthesis` (consensus / open_research / user_decisions_needed / approval_required) |
| 8. work_report | `agents/work_report.py` | `WorkReport`, `build_work_report`, `format_work_report_markdown` |
| 9. meeting_minutes | `agents/meeting_minutes.py` | `MeetingMinutes`, `build_meeting_minutes`, `format_meeting_minutes_markdown` |
| 10. obsidian_record | `agents/obsidian_export.py` + `agents/obsidian_approval.py` + `agents/knowledge_writer.py` | `render_research_note`, `render_work_report_note`, `recommend_path`, `is_obsidian_save_request`, `execute_pending_proposal` |
| 11. coding_authorization (optional) | `agents/coding_authorization.py` + `agents/coding_job.py` | `recommend_authorization`, `CodingAuthorizationProposal`, `build_coding_job_from_proposal`, `CodingJob` |
| 12. supervisor / status | `agents/session_status.py` + `discord/engineering_conversation.format_status_diagnostic_response` | `diagnose_session`, `SessionStatusReport`, `SessionStatusSignal` |

## Discord 라우팅 모듈 책임 정리

`discord/engineering_channel_router.py` (≈3.1k LOC) 는 orchestration 전용으로 유지됩니다.

직접 들어가도 되는 책임:

- 채널/스레드 식별 (`is_engineering_channel`, `_thread_id_for_runtime`)
- gate 진입점 (`_run_coding_authorization_gate`, `_run_obsidian_approval_gate`)
- 클래리피케이션 캐시 (`_GATEWAY_CLARIFICATION_CONTEXT`, `_remember_clarification_candidates`, `_recall_clarification_canonical_prompt`)
- explicit 기존 세션 / 새 작업 follow-up 분기 (`_drive_clarification_create_new_work`, `_handle_explicit_session_id_join`)
- intake → kickoff → research_loop → work_report 순서 호출
- session.extra 영속화 (`_persist_extra_keys`, `_persist_role_selection`, `_emit_work_report_preview`)

들어가면 안 되는 책임:

- token-overlap 점수 / 후보 매칭 → `agents/routing.py` (`decide_routing`, `_score_one`)
- 세션 lookup / 캐시 read-write → `agents/workflow_state.py`
- intent classifier / runtime loop → `agents/runtime/`
- 자료 수집 / 검색 → `agents/research_collector.py`
- 발화별 토의 → `agents/deliberation.py`
- 보고서 / 회의록 / Obsidian render → 각 agents/ 모듈

## tests/ 디렉터리 매핑

| 디렉터리 | 커버 영역 |
|---|---|
| `tests/engineering/` | router 라우팅 / gate / 라이프사이클 / role_selection / work_report / meeting_minutes / coding_authorization |
| `tests/research/` | collector / budget / sufficiency / loop / pack / persistence |
| `tests/obsidian/` | export path / writer / approval / git / knowledge_writer / work-report kind |
| `tests/discord/` | bot.py / dispatcher / member_bot 등 Discord 진입부 |
| `tests/memory/` | retrieval / indexer / search |

새 테스트는 책임이 가장 좁게 떨어지는 디렉터리에 두세요. lifecycle test (multi-module orchestration) 은 `tests/engineering/test_*_lifecycle.py` 로 명명합니다 (예: `test_work_report_lifecycle.py`).

## 큰 rename 을 예고하는 신호

다음이 동시에 나타나면 별도 refactor 브랜치를 띄웁니다 — 한 PR 에 섞으면 검토가 어려워집니다.

- `engineering_channel_router.py` 가 4k LOC 를 넘는다 → forum publishing / status helpers 추출 검토
- `agents/research_collector.py` 가 다중 provider 어댑터를 다 품게 됨 → `agents/research/` 패키지로 분할 검토
- session.extra dict 의 키가 30 개를 넘는다 → 명명규약 + nested namespace 도입 검토 (`session.extra['research'] = {...}`)

## Phase 1-5 변경 요약 (이 문서가 만들어진 배경)

- `agents/role_selection.py` 신설 — tech-lead 가 task 별 active role 을 결정하고 reason 을 기록
- `agents/research_budget.py` — `decide_budget(active_roles=…)` 으로 active role 만 target
- `agents/research_collector.py` — `CollectionOutcome.active_roles` + `auto_collect_or_request_more_input(active_roles=…)`
- `agents/meeting_minutes.py` 신설 — research + synthesis + role_takes 에서 회의록 deterministic 생성
- `agents/work_report.py` 신설 — 동일 데이터에서 업무 보고서 생성, 코드 수정 권고 / 승인 CTA 포함
- `agents/obsidian_export.py` — `work-report` kind 추가, `render_work_report_note` 신설
- `discord/engineering_channel_router.py` — `_persist_role_selection` + `_emit_work_report_preview` 두 helper 가 lifecycle 끝에서 자동 호출
- `discord/engineering_conversation.format_status_diagnostic_response` — 활성 role / 업무 보고서 상태 라인 추가
