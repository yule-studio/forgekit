# P0-J — Coding Bootstrap + Read-only Intents (#145 + #146)

> **Status:** P0-J audit doc — 두 회귀 (coding flow 오분류 + 모든 요청 → research/intake) 를 단일 PR / 8-commit 으로 해결.
> **Issues:** #145 (coding bootstrap) + #146 (read-only intents). parent #138.

## 0. 사용자 보고

### Bug 1 (#145)

repo + issue + 명시적 coding request 가 들어와도 gateway 가 coding 작업으로 이해 못 하고:
1. `_suggest_task_type` 이 단일 `docker` 매칭으로 PLATFORM_INFRA 분류.
2. `official_docs / code_context 부족` 으로 막음 ("자료 더 주세요").

사례: `https://github.com/yule-studio/naver-search-clone/issues/1` — Next.js + NestJS + PostgreSQL + Docker Compose 회원가입/로그인/검색.

### Bug 2 (#146)

gateway 가 너무 많은 요청을 새 research / intake 처럼 처리:
- "지금 열려 있는 세션 몇 개?" → auto-collect 수행
- "왜 멈췄어?" → research fallback
- "방향 수정이야" → 새 작업처럼 흐름

## 1. 충돌 가능 지점 (10줄)

1. `_suggest_task_type` (`engineering_conversation.py:926-932`) 의 `_TASK_TYPE_KEYWORDS` 가 단순 `in` 매칭 → `docker` 한 단어로 PLATFORM_INFRA. Next.js/NestJS/Postgres 조합 인식 불가.
2. TASK_INTAKE_CANDIDATE 분기에서 `_maybe_run_auto_collect` 호출 → collector 가 `NEEDS_USER_INPUT` 반환 → "자료 부족" 노출. PLATFORM_INFRA 는 official_docs 요구가 더 가혹.
3. STATUS_DIAGNOSTIC 은 이미 분리 (line 178) — 그러나 "세션 몇 개?" / "왜 멈췄어?" / "방향 수정" 같은 세분화된 intent **부재**. 모호 입력 → TASK_INTAKE_CANDIDATE fallback → auto_collect.
4. `collector.py:1859-1893` 의 `NEEDS_USER_INPUT` mode. `user_links` 없거나 `auto_collected_count=0` 시 발화. repo URL 만 있어도 `user_supplied=True` 인정 안 됨.
5. `session.extra["github_target"]` 는 `_persist_coding_session_context` (intake *후*) — `_maybe_run_auto_collect` 시점엔 미존재.
6. `prepare_coding_session_context` 를 conversation 입구에서도 호출하거나 user_links 만으로 추론해야 함.
7. 세션 list/count query 데이터 source: `agents/workflow_state.list_sessions(limit=N)` 이미 존재.
8. `understand.py` 의 RuntimeIntent 9개 — conversation layer 의 6개와 다른 layer. 본 작업은 conversation layer 만 확장 (runtime layer 무회귀).
9. `change_direction` / `continue_existing_work` — `session.extra` 업데이트 helper 필요. 새 intake 절대 금지.
10. 8 commit 분할 — audit / stack lexicon / official docs seed / TaskType.FULL_STACK_APP + suggest_task_type / coding bootstrap 우회 / 신규 intent 5 + responders / hard rule (auto_collect 차단) / 통합 wiring + e2e.

## 2. 신규 / 갱신 파일 매트릭스

| 위치 | C/U | 책임 |
| --- | --- | --- |
| `docs/p0j-coding-bootstrap-and-readonly-intents.md` | C | 본 doc. |
| `apps/engineering-agent/src/yule_engineering/agents/coding/stack_detector.py` | C | STACK_LEXICON + detect_stacks + classify_full_stack. |
| `apps/engineering-agent/src/yule_engineering/agents/coding/official_docs_seed.py` | C | STACK_TO_DOCS + seed_official_docs. |
| `apps/engineering-agent/src/yule_engineering/agents/messaging/dispatcher.py` | U | TaskType.FULL_STACK_APP 추가 + TASK_ROLE_SEQUENCE 매핑. |
| `apps/engineering-agent/src/yule_engineering/discord/engineering_conversation.py` | U | `_suggest_task_type` 가 combo 우선 / 5 신규 intent 분류 + responders / hard rule (auto_collect 차단) / wire-in. |
| `apps/engineering-agent/src/yule_engineering/agents/coding/coding_bootstrap.py` | C | should_bypass_insufficiency(text, user_links). |
| `tests/agents/coding/test_stack_detector.py` | C | (+ 4 추가 test 파일). |

## 3. 신규 conversation intents

| intent | trigger 예시 | response 경로 |
| --- | --- | --- |
| `session_count_query` | "지금 열려 있는 세션 몇 개?" | open session count 만 |
| `session_list_query` | "오픈 세션 목록 보여줘" | recent open sessions (id / state / updated_at / thread/PR) |
| `blocked_reason_query` | "왜 멈췄어?" / "뭐가 막혔어?" | diagnose_session 의 signals + blocked_reason surface |
| `continue_existing_work` | "이전 작업 이어서" / "그 세션 계속" | session 의 latest continuation_prompt 로 재개 (새 intake X) |
| `change_direction` | "자료 추가 X, 방향 수정" / "검색 말고 로그인 먼저" | 기존 session.extra 의 prompt/scope 갱신 (새 intake X) |

## 4. 신규 task_type

`TaskType.FULL_STACK_APP = "full-stack-app"` — repo + 다중 stack tier (frontend + backend / backend + database / 등) 발견 + write intent 시 분류. `TASK_ROLE_SEQUENCE` 에 (tech-lead / ai-engineer / backend-engineer / frontend-engineer / devops-engineer / qa-engineer) 추가.

## 5. acceptance criteria 매핑

| AC (Bug 1 / 2) | 처리 commit |
| --- | --- |
| repo+issue+full-stack → not platform-infra | 4 |
| repo+issue+write intent → no insufficiency follow-up | 5 |
| stack mention → official docs seed | 3 |
| repo/local clone → code_context bootstrap signal | 5 |
| genuine infra → still platform-infra | 4 |
| session count query → no auto_collect | 6 + 7 |
| session list query → list response | 6 + 7 |
| blocked_reason query → diagnostic | 6 + 7 |
| continue_existing_work / change_direction → no new intake | 6 + 7 |
| genuine new work → still intake/coding path | 8 (e2e) |
| 기존 4559 PASS 무회귀 | 매 commit |

## 6. 남은 미결정 (후속 PR)

- repo 의 local clone 자동 활성화 (현재는 contract 만 활성화) — vault repo workspace 와 같은 정책으로 deferred.
- code_context bootstrap 의 실제 컨텐츠 (실 파일 트리 / README excerpt) — repo clone 시 wire.

## 7. 변경 이력

| 일자 | 변경 |
| --- | --- |
| 2026-05-14 | 초안 — Issue #145 + #146. parent #138. |
