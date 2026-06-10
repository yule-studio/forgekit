# P0-K — Command-Only 운영 문장 → research/forum thread 회귀 차단 (#148)

> **Status:** P0-K audit — `진행 해줘` / `이대로 진행` / `승인하고 진행해` 같은 command-only 운영 문장이 새 research loop + `[Reference] 진행 해줘` forum thread 를 만드는 회귀 차단. parent #138.

## 0. 사용자 보고

- 기존 세션 `4dddb86c1714` 가 계속 재사용됨.
- gateway 가 `[engineering-agent] 기존 작업 이어받음` 후 `추가 요청: 진행 해줘` 같은 메시지 남김.
- 이어서 `✅ 운영-리서치 forum 게시 완료` + `thread: [Reference] 진행 해줘` 같은 새 thread 생성.
- 사용자는 승인 / 계속 진행만 했는데 리서치 + thread 생성이 반복.

## 1. 충돌 가능 지점 (10줄)

1. `is_command_only_prompt` / `is_non_actionable_prompt` 이미 `agents/routing.py:267-339` 존재 — 23-phrase frozenset. 본 작업은 *enforcement* 만.
2. `_run_research_loop_hook` 호출 2 site (`engineering_channel_router.py:880` new-work, `:2523` continuation) 모두 prompt_text 의 command-only 가드 없음.
3. `_record_engineering_continuation` (`bot.py:2215, 2222`) 가 cleaned_prompt 의 command-only 검증 없이 `latest_continuation_prompt` / `canonical_prompt_override` 저장.
4. `derive_research_topic` / `normalize_thread_title` (`research_forum.py`) 가 pack.title → "이대로 진행" 그대로 통과 → `[Reference] 이대로 진행` thread 생성.
5. `CONFIRM_INTAKE` 분기 (`engineering_conversation.py:303`) 가 `last_proposed_prompt or message_text` 사용 — 둘 다 command-only 일 때 가드 없음.
6. P0-J 의 5 read-only intent 가 `_maybe_run_auto_collect` 차단 — 그러나 CONTINUE_EXISTING_WORK 가 continuation prompt 자체를 작성하지 않음. 새로운 hard rule 4 site 가 필요.
7. 신규 intent `approval_action` 후보 — 짧은 승인 ack 만 출력 + 새 intake / research 절대 안 만듦.
8. `resumed_thread_id` (`bot.py:2167` lookup) 가 `forum_message_adapter._resolve_session_for_forum_thread` 에 보조 lookup 미존재 — 역할 변경 fail.
9. 모든 4 사이트가 `routing.is_command_only_prompt` import 만 — 신규 모듈 0.
10. 7 commit 분할 — audit / phrase set + approval_action intent / continuation prompt 가드 / research loop 가드 / forum title 가드 / CONFIRM_INTAKE + ack + resumed thread resolve / e2e + PR.

## 2. 4 critical site 표 (enforcement points)

| # | 위치 | 가드 |
| --- | --- | --- |
| A | `bot.py:_record_engineering_continuation` (~2215, 2222) | `cleaned_prompt` 가 command-only 면 `latest_continuation_prompt` / `canonical_prompt_override` 저장 X (resumed_thread_id 는 별도 저장). |
| B | `engineering_channel_router.py:_run_research_loop_hook` 호출부 (~880 + 2523) | prompt_text 가 command-only 면 호출 X — `research_loop_skip_reason="command_only_prompt"` audit. |
| C | `research_forum.py:derive_research_topic` / `normalize_thread_title` | pack.title / summary 가 command-only 면 거부 → fallback "engineering 작업" 또는 기존 session.prompt. |
| D | `engineering_conversation.py:CONFIRM_INTAKE` 분기 | `last_proposed_prompt` 와 `message_text` 둘 다 command-only 면 `ready_to_intake=False` + 짧은 ack. |

## 3. 신규 intent

`APPROVAL_ACTION` — `승인하고 진행해` / `승인할게` / `오케이 진행` / `작업 승인` 류. P0-J 의 `READ_ONLY_INTENTS` 와 같이 묶음. 응답은:

> `✅ 승인 반영했습니다. 기존 작업 흐름을 이어갑니다. 새 리서치 thread 는 만들지 않습니다.`

CONFIRM_INTAKE 와의 차이: CONFIRM_INTAKE 는 *직전 제안* 을 intake 로 promote (`ready_to_intake=True`). APPROVAL_ACTION 은 *기존 세션 상태 ack* 만 — 새 intake 절대 안 만듦.

## 4. ack 일관성

| 시나리오 | ack |
| --- | --- |
| approval_action | `✅ 승인 반영했습니다. 기존 작업 흐름을 이어갑니다. 새 리서치 thread 는 만들지 않습니다.` |
| continue_existing_work + 세션 id 있음 | `✅ 세션 `<id>` 를 계속 진행할게요. 새 리서치 thread 는 만들지 않습니다.` |
| continue_existing_work + 세션 id 없음 | `이어갈 세션을 찾지 못했어요. session id 또는 thread 를 명시해 주세요.` |
| genuine direction update (substantive text) | 정상 same-session direction update — (별도 분기) |

## 5. 신규 / 갱신 파일 매트릭스

| 위치 | 책임 |
| --- | --- |
| `docs/p0k-command-only-research-thread-guard.md` | 본 doc. |
| `apps/engineering-agent/src/yule_engineering/agents/routing.py` | `_COMMAND_ONLY_PROMPTS` 확장 (5+ phrase). |
| `apps/engineering-agent/src/yule_engineering/discord/engineering_conversation.py` | APPROVAL_ACTION intent + matcher + formatter + READ_ONLY_INTENTS 에 추가. CONFIRM_INTAKE 가드. |
| `apps/engineering-agent/src/yule_engineering/discord/bot.py` | `_record_engineering_continuation` 가드. |
| `apps/engineering-agent/src/yule_engineering/discord/engineering_channel_router.py` | `_run_research_loop_hook` 호출부 2 site 가드. |
| `apps/engineering-agent/src/yule_engineering/discord/research_forum.py` | `derive_research_topic` / `normalize_thread_title` 가드. |
| `apps/engineering-agent/src/yule_engineering/discord/forum_message_adapter.py` | `_resolve_session_for_forum_thread` 가 `resumed_thread_id` 보조 lookup. |
| tests/discord/test_p0k_*.py | 시나리오 e2e + 무회귀. |

## 6. 변경 이력

| 일자 | 변경 |
| --- | --- |
| 2026-05-14 | 초안 — Issue #148 P0-K audit. parent #138. |
