# Forum Follow-up + Query Canonicalization + Typing Pre-Parse (P0-F)

> **Status**: 단일 PR — 라이브 Discord UX 의 3가지 잔존 회귀 한 묶음 해결. 코드 변경 전 lock down.

## 0. 문제 정리

세 가지 사용자 보고 사항:

1. **`#운영-리서치` 가 사용자와 대화형 follow-up surface 로 동작하지 않음.** thread 에 "지금 뭐하고 있어?", "RAG 말고 CAG 얘기야", "이 링크도 참고해" 라고 써도 무반응.
2. **`dRAG` / `cag` 같은 오타·소문자 변형이 그대로 research query 로 들어감.** mock fallback 일 때는 엉뚱한 canned 결과가 authoritative reference 처럼 forum 에 올라감.
3. **member bot typing indicator 가 "생각 시작" 이 아니라 "보내기 직전" 에만 켜짐.** expensive handler (deliberation, synthesis) 가 도는 동안 indicator 비어 있어 사용자가 "봇이 죽었나" 의심.

## 1. 인지 부조화 (원인 요약)

### 문제 1 — forum thread fallthrough
- `src/yule_orchestrator/discord/forum_message_adapter.py:103` 의 `route_forum_message` 가 처리하는 intent 는 **2개뿐**:
  1. Branch 1 (line 141) — Obsidian save request.
  2. Branch 2 (line 193) — role-change request.
- 일반 follow-up 질문은 line 202 의 `parse_role_change_request(text) is None` 분기에서 `handled=False` 로 빠진다.
- 그 다음 `bot.py:1685` 가 falsy 결과를 받고 engineering channel router 로 넘기지만, engineering router 는 `#업무-접수` (intake channel) 만 engineering channel 로 인식. forum thread 는 owner 가 없는 상태.
- 결과: 사용자 follow-up 메시지가 어디서도 처리되지 않음.

### 문제 2 — raw prompt query
- `src/yule_orchestrator/agents/research/collector.py:570` `build_query_for_role` 가 prompt 첫 줄을 그대로 query 토큰화. 케이스 정규화, alias 보정, 도메인 lexicon 매칭 모두 없음.
- 결과: `dRAG memory 구조 비교` → query `dRAG memory 구조 비교` 그대로. 도메인 용어 (`RAG`/`CAG`/`LLM`/`JWT`/`CI/CD`) 의 대소문자·variant 가 그대로 collector 에 도달.
- mock fallback (provider/key 미설정 시) 은 query 토큰 매칭으로 hit bucket 을 고르므로, 정규화 안 된 query 는 잘못된 bucket 의 canned 결과를 끌어옴 → 사용자 눈엔 "엉뚱한 답" 으로 보임.

### 문제 3 — typing 시작 시점
- `src/yule_orchestrator/discord/member_bot.py:284` `handle_research_turn_message(...)` 호출이 **typing wrap 바깥**. 이 핸들러는 cheap 하지 않음 — `load_session`, deliberation re-render, synthesis 큐잉 수행 (`engineering_team_runtime.py:523` 이하).
- typing_keepalive 진입은 line 318 (handler 결과가 non-None 인 다음). 즉 expensive computation 의 절반이 끝난 다음에야 typing 시작.

## 2. 5 가지 의심 spot (이번 PR 처리 범위)

| # | 위치 | 처리 commit |
| --- | --- | --- |
| A1 | `forum_message_adapter.route_forum_message` — branch 3 (follow-up) 추가 | commit 4 |
| A2 | `forum_message_adapter._resolve_session_for_forum_thread` — 재사용 (기존 helper) | commit 4 |
| B1 | `agents/research/query_canonicalizer.py` 신규 모듈 — single source of truth | commit 2 |
| B2 | `collector.build_query_for_role` + recall observation — canonicalizer wire-in | commit 3 |
| C1 | `member_bot._dispatch_member_message` — pre-parse 후 typing_keepalive 진입 | commit 5 |

## 3. 설계 요약

### A. Forum thread follow-up (commit 4)
- 새 helper `forum_conversation_adapter.handle_forum_followup(message, text, session, ...)`:
  - 기존 session anchor 를 `_resolve_session_for_forum_thread` (이미 forum_message_adapter 에 존재) 로 찾는다.
  - 그 session 의 `prompt`, `extra`, `played_roles`, `active_research_roles` 를 컨텍스트로 `build_engineering_conversation_response` 호출 (이미 status query / clarification / general help 처리).
  - 결과의 `is_status_query=True` 또는 `intent_id=GENERAL_ENGINEERING_HELP` 등은 직접 thread 에 응답.
  - `ready_to_intake=True` 가 나와도 **새 intake 만들지 않는다**. 그 분기는 silent skip (forum 은 새 작업 제출처가 아님).
  - correction/append-context 같은 특수 directive 는 별도 light parser (`parse_forum_correction_directive`) 로 식별 → session.extra 에 메모만 남기고 응답.
- `route_forum_message` 에 branch 4 로 wire — save/role-change 처리 안 되면 follow-up 으로 fall-in. session anchor 없으면 silent fall-through (현행 fallthrough 와 동일).

### B. Query canonicalization (commit 2 + 3)
- 새 모듈 `agents/research/query_canonicalizer.py`:
  - `canonicalize_query(raw: str) -> CanonicalQuery` — 단일 진입점.
  - `CanonicalQuery` dataclass: `raw`, `canonical`, `applied: tuple[Replacement]`, `confidence: float` (0.0~1.0).
  - 처리 단계 순서:
    1. whitespace/case 정규화 (`  Drag ` → `drag`).
    2. **engineering domain lexicon 매핑** — exact case-insensitive 매칭. 우선 `RAG`, `CAG`, `LLM`, `JWT`, `CI/CD`, `OSI`, `TCP`, `UDP`, `IP`, `HTTP`, `HTTPS`, `OAuth`, `REST`, `gRPC`, `SQL`, `NoSQL`, `MQTT`, `ETL`, `ML`, `AI` ~30 개.
    3. **bounded fuzzy correction** — edit distance ≤1, lexicon 단어 길이 ≥3, 알파벳 prefix 조건. (`dRAG`→`RAG`, `cAg`→`CAG`, `llm`→`LLM`).
    4. korean alias normalization (`알엠`→`LLM`, `씨아이씨디`→`CI/CD` 등 — 보수적, 5~10개).
    5. confidence 산정: 모든 정규화가 exact case-insensitive 매핑이면 1.0, fuzzy 가 섞이면 0.6, 한국어 alias 가 섞이면 0.7.
- `collector.build_query_for_role`:
  - prompt 첫 줄 추출 직후 `canonicalize_query` 호출.
  - canonical token 을 query 에 사용. raw 는 metadata 로 `collection_outcome.metadata['raw_query']` 에 보존.
  - confidence < 0.5 이면 `low_confidence_query=True` 표시.
- `collector.run_research_collection` 의 mock fallback 분기:
  - `low_confidence_query=True` + mock fallback 조합 → `auto_publish=False` (collector 가 게시 보류, gateway 가 사용자에게 clarification 요청).

### C. Typing pre-parse (commit 5)
- `_dispatch_member_message` 의 흐름 재정렬:
  1. **cheap pre-parse** (현재는 handler 안에서 발생):
     - `parse_research_dispatch_marker(text)` 또는 `parse_research_open_marker(text)` 로 marker + session_id + role 추출 (단순 regex, microseconds).
     - profile.role 과 marker role 비교 → 다르면 즉시 silent return (typing 안 켜짐).
     - active_research_roles 빠른 조회 (이미 commit 2 of P0-E 의 `_resolve_active_roles_for_typing_gate` 가 있음).
     - 통과한 marker 류가 있으면 → 다음 단계로.
  2. **typing_keepalive 진입** (expensive handler 까지 감쌈).
  3. `handle_research_turn_message` / `handle_team_turn_message` 실행 + `_post_*` 수행.
- 결과: expensive handler 가 도는 5~15s 동안 typing 유지. inactive role / 잘못된 marker 는 typing 진입 X.

## 4. Commit 시퀀스 (단일 PR, 6 commits)

| commit | 내용 | risk |
| --- | --- | --- |
| 1 | 본 audit doc (코드 변경 0) | 0 |
| 2 | `agents/research/query_canonicalizer.py` 신규 모듈 + unit test | LOW (순수 모듈) |
| 3 | collector + recall observation 의 query 빌드 site 에 canonicalizer 와이어. low-confidence + mock fallback 보류 가드 | MEDIUM (production 분기 변화) |
| 4 | `forum_conversation_adapter` + `route_forum_message` branch 4 (follow-up) | MEDIUM (forum thread routing 추가) |
| 5 | `_dispatch_member_message` 의 pre-parse + typing_keepalive 진입 순서 재정렬 | MEDIUM (typing UX 시점 변경) |
| 6 | regression test 추가 + 전체 pytest pass 확인 + PR | LOW |

## 5. Acceptance Criteria 매핑

| AC | 처리 commit |
| --- | --- |
| 1. 운영-리서치 thread 에서 status question 응답 | 4 |
| 2. forum thread correction/append/summarize 가 새 intake 만들지 않고 처리 | 4 |
| 3. `dRAG/CAG` canonicalization | 2 + 3 |
| 4. low-confidence + mock fallback → auto-publish 보류 | 3 |
| 5. member bot typing 이 expensive handler 시작 시점부터 유지 | 5 |
| 6. ignore/no-op/inactive role typing 미진입 | 5 |
| 7. save request / role-change / approval / intake 무회귀 | 전 commit 회귀 test |

## 6. 남은 리스크

- `forum_conversation_adapter` 의 status query 응답이 `#봇-상태` 채널의 기존 status diagnostic 과 표현이 달라질 수 있음 — text 만 공유, 별도 alarm 은 미관여.
- canonicalizer 의 fuzzy correction 이 사용자 의도가 아닐 가능성 (예: 진짜 `Drag` 를 의도). confidence < 1.0 인 경우 결과 메시지에 `📝 "dRAG" → "RAG" 로 정규화함` 같은 audit 1 줄 노출 검토.
- pre-parse 가 marker 식별을 잘못하면 typing 이 누락될 위험 — cheap regex 이므로 위양성 risk 는 낮으나, 회귀 test 로 보호.
