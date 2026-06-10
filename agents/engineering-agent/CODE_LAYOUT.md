# Engineering Agent — code layout & module ownership

이 문서는 engineering-agent lifecycle 의 각 단계가 어느 모듈에서 책임지는지 빠르게 찾기 위한 지도입니다. 코드 변경 전에 "이 책임이 어디에 있어야 하지?" 를 먼저 확인하는 용도이고, 큰 rename / 분할은 별도 브랜치로 진행합니다.

> 본 문서는 **engineering-agent 모듈 ownership + 파일 분리 기준** 의 SSoT.
> 전역 코딩 컨벤션 요약은 [`/CLAUDE.md`](../../CLAUDE.md), 작업 맥락별 읽기
> 가이드는 [`CLAUDE.md`](CLAUDE.md) §"작업 맥락별 읽기 가이드" 참조.

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
| `tests/agents/` | harness 브리지 (`agents/harness/`: slash-command grant 로더 / compact→vault / 투영 drift-guard, #185) 외 다수 |

> **`agents/harness/` 패키지 (#185)** — 레지스트리 SSoT(`agents/<agent>/{skills,commands,hooks}` + `agents/grants/slash-command-grants.json`)를 Claude Code/Codex harness 아티팩트로 잇는 bridge. `slash_command_grants.py`(grant 로더+검증), `context_compaction.py`(compact→vault 결정형 코어). harness 디렉터리(`.claude/`·`.agents/`·`*-plugin/`)는 `scripts/sync_harness_skills.py` 생성물 — 손 편집 금지. 상세 `docs/agent-slash-commands.md`.

새 테스트는 책임이 가장 좁게 떨어지는 디렉터리에 두세요. lifecycle test (multi-module orchestration) 은 `tests/engineering/test_*_lifecycle.py` 로 명명합니다 (예: `test_work_report_lifecycle.py`).

## 큰 rename 을 예고하는 신호

다음이 동시에 나타나면 별도 refactor 브랜치를 띄웁니다 — 한 PR 에 섞으면 검토가 어려워집니다.

- `engineering_channel_router.py` 가 4k LOC 를 넘는다 → forum publishing / status helpers 추출 검토
- `agents/research_collector.py` 가 다중 provider 어댑터를 다 품게 됨 → `agents/research/` 패키지로 분할 검토
- session.extra dict 의 키가 30 개를 넘는다 → 명명규약 + nested namespace 도입 검토 (`session.extra['research'] = {...}`)

## 파일 크기 / 책임 분리 규칙

> 길이보다 더 중요한 것은 **책임 수** 다. 단, 1000 줄은 강한 분리 신호다.
> 본 규칙은 [`/CLAUDE.md`](../../CLAUDE.md) §"전역 코딩 컨벤션" 의 engineering-agent
> 측 적용 가이드.

### 등급별 액션

| LOC | 액션 | 비고 |
| --- | --- | --- |
| ~700 | 안전 | — |
| 700 ~ 1000 | **warning** — 분리 검토 | PR 본문에 "왜 한 파일에 남기나" 한 줄 적기. 검토자가 분리 요청 가능 |
| 1000 초과 | **default split** | 별도 브랜치 또는 같은 PR 의 첫 commit 으로 분리 |
| 1000 초과 + 책임 2개 이상 | **분리 필수** | 분리 없이 머지하지 않음 |

### 책임 분리 신호

다음이 한 파일에서 동시에 보이면 길이와 무관하게 분리 후보:

- intake / intent classification / routing / state persistence / formatting /
  external integration 중 **3 가지 이상** 이 한 파일에 섞임
- 같은 phrase / regex 패턴이 여러 함수에서 반복 patch 됨 ("phrase patch
  반복 파일")
- 한 함수가 다른 도메인의 dataclass 를 직접 mutate
- "임시" / "TODO" / "FIXME" 가 같은 파일에 5 개 이상 누적
- 한 파일의 import top-level 이 30 줄을 초과

### 분리 방식

| 상황 | 권장 분리 방식 |
| --- | --- |
| 모듈이 한 도메인 + 여러 helper 로 커짐 | 같은 패키지 안의 sub-module 로 추출 (`foo/__init__.py` + `foo/<sub>.py`) |
| 여러 도메인이 섞임 | 도메인별 모듈로 분할 후 패키지화 |
| 모놀리스 → 점진 분해 중 | 임시 `_legacy.py` 사용 (파일 상단에 분해 플랜 docstring 필수) |
| 큰 registry / data table | 분리 대신 데이터/로직 분리 (`_data.py` + `_logic.py`) |

### 예외 정책

다음은 분리 미루기 가능. 단, **이유 docstring 필수**:

- generated file (`*.pb.py`, `*_pb2.py` 등 코드 생성물)
- fixture / snapshot / test data
- 큰 registry / mapping 성격 파일 (선언만, 분기 로직 없음)
- in-flight refactor 중인 `_legacy.py` (해체 진행 중인 모놀리스)

예외를 적용한 파일은 본 문서의 "현재 예외" 표에 추가:

#### 현재 예외 (in-flight refactor 등)

| 파일 | LOC | 이유 |
| --- | --- | --- |
| `apps/engineering-agent/src/yule_engineering/discord/bot/_legacy.py` | ~2700 | P0-Q discord/ 분해 진행 중 — 의미 그룹 추출 후 점진 제거 |
| `apps/engineering-agent/src/yule_engineering/discord/engineering_team_runtime/_legacy.py` | ~2150 | P0-Q discord/ 분해 진행 중 — 같은 사이클 |
| `apps/engineering-agent/src/yule_engineering/runtime/status.py` | ~2000 | 라이브 상태 표면화 — 도메인 단일, 단계별 helper 추출 후속 작업 |
| `apps/engineering-agent/src/yule_engineering/agents/research/collector.py` | ~2400 | 다중 provider adapter — `agents/research/<provider>` 패키지화 후속 |
| `apps/engineering-agent/src/yule_engineering/agents/deliberation.py` | ~1750 | TechLead 합의 단일 도메인 — open_research/synthesis 분리 검토 |

새 파일이 1000 줄을 넘었는데 위에 없으면 분리 PR 을 먼저 띄우거나,
이 표에 이유와 함께 추가해야 한다. silently 자라는 것을 막기 위함.

### Router / conversation / runtime 의 별도 가드

| 영역 | 한도 신호 |
| --- | --- |
| `discord/engineering_channel_router/main.py` | 1000 줄 근처가 되면 forum publishing / status helper / preflight 추출 |
| `discord/engineering_conversation/research_bootstrap.py` | 1000 줄 근처가 되면 intent / formatter / bootstrap 분리 |
| `agents/research/collector.py` | provider 어댑터별 sub-module 로 패키지화 |
| `agents/deliberation.py` | open_research / synthesis 별 sub-module |

### 자동 점검 훅과의 관계

- 정책 / 프롬프트 / 템플릿 크기는 [`tests/governance/test_prompt_size_ceiling.py`](../../tests/governance/test_prompt_size_ceiling.py)
  가 단일 파일 / 전체 preamble / `.tmpl` ceiling 을 강제.
- 본 문서의 1000 줄 규칙은 governance smoke test
  [`tests/engineering/test_engineering_agent_governance_doc.py`](../../tests/engineering/test_engineering_agent_governance_doc.py)
  의 docstring 검증 라인이 핵심 섹션 존재를 지킨다.
- runtime hard rail (branch / PR / tag / curated note / retrieval eval /
  post-test hardening) 은 [`tests/governance/test_runtime_policy.py`](../../tests/governance/test_runtime_policy.py)
  가 검사 — 코드 SSoT 는 [`apps/engineering-agent/src/yule_engineering/agents/governance/runtime_policy.py`](../../apps/engineering-agent/src/yule_engineering/agents/governance/runtime_policy.py).
- 세 자동 가드와 본 규칙은 **상호 보완** — ceiling 은 토큰/회귀 보호,
  본 규칙은 책임 분리 보호, runtime_policy 는 git/vault/eval drift 보호.

## Phase 1-5 변경 요약 (이 문서가 만들어진 배경)

- `agents/role_selection.py` 신설 — tech-lead 가 task 별 active role 을 결정하고 reason 을 기록
- `agents/research_budget.py` — `decide_budget(active_roles=…)` 으로 active role 만 target
- `agents/research_collector.py` — `CollectionOutcome.active_roles` + `auto_collect_or_request_more_input(active_roles=…)`
- `agents/meeting_minutes.py` 신설 — research + synthesis + role_takes 에서 회의록 deterministic 생성
- `agents/work_report.py` 신설 — 동일 데이터에서 업무 보고서 생성, 코드 수정 권고 / 승인 CTA 포함
- `agents/obsidian_export.py` — `work-report` kind 추가, `render_work_report_note` 신설
- `discord/engineering_channel_router.py` — `_persist_role_selection` + `_emit_work_report_preview` 두 helper 가 lifecycle 끝에서 자동 호출
- `discord/engineering_conversation.format_status_diagnostic_response` — 활성 role / 업무 보고서 상태 라인 추가
