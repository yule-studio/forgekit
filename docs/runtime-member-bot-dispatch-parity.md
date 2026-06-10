# Runtime Member Bot Dispatch Parity + Typing Keepalive (P0-C v2 + P0-D)

> **Status**: 합본 작업 (issue #132 P0-C v2 + issue #134 P0-D). 본 doc 은 5 commit 시퀀스의 single source of truth — 코드 변경 전에 lock down.

## 1. 인지 부조화

P0-C v1 (#133 머지) 이후:
- `yule runtime up` 으로 spawn 된 member bot 이 Discord 멤버 리스트에 visible. on_ready 로그 / 권한 진단 다 됨.
- **그러나** `[research-open:*]` / `[research-turn:*]` / `[team-turn:*]` directive 에 반응 X.
- 사용자 보고: "회사처럼 안 보임" — 봇이 살아 있는지 죽었는지 typing 으로도 판단 어려움.

증거:
- `apps/engineering-agent/src/yule_engineering/discord/member_bot.py:312-340` — `_dispatch_member_message(profile, bot, message)` 본문이 `return None`. P0-C v1 의 minimum viable 의 placeholder.
- `build_member_bot.MemberBot.on_message` (line 244-250) — runtime path 의 bot 의 유일한 message handler. placeholder 만 호출.
- 반면 sync `run_member_bot` (line 95-176) — dev/test path 의 closure 가 80+ 라인의 실제 dispatch (research-open / research-turn / team-turn 매칭 + typing + post + error fallback).

## 2. dispatch 차이 (양 path 비교)

| 경로 | bot factory | on_message |
| --- | --- | --- |
| `yule discord up` (dev/test) | sync `run_member_bot` 안의 closure `MemberBot` | 80+ 라인 inline dispatch (research/team-turn 게시) |
| `yule runtime up` (production) | `build_member_bot` (P0-C v1) | placeholder `_dispatch_member_message` → `return None` |

closure 가 capture 하는 변수: 오직 `profile` (그 외 모두 module-level import). bot 인스턴스는 `self.user` / `message.author` 비교용 (이미 on_message shim 에서 가드 가능). 즉 — **closure 전체를 그대로 module-level async 함수로 hoist** 하면 양 path 가 같은 dispatcher 공유.

## 3. typing keepalive 끊김 의심 spot (5 곳)

| # | 위치 | 증상 |
| --- | --- | --- |
| 1 | `engineering_channel_router.route_engineering_message` 의 `conversation_fn(... auto_collect=True)` 호출 (line ~562) | `_maybe_run_auto_collect` 가 long-running. 현재 keepalive wrap 없음 — 사용자 수 초 침묵 관측. |
| 2 | `_run_runtime_preflight` 의 `_handle_join_or_append` → `thread_continuation_fn(...)` (Discord thread lookup + resume) | 무 wrap. |
| 3 | `_run_research_loop_hook` 이후 — 이미 wrap 있음 ✓ (현재 OK) | (참고) |
| 4 | member bot `_post_research_turn` / `_post_team_turn` (member_bot.py:530-575, 516-527) 의 `typing_context` (1-shot) | `handle_team_turn_message` 의 deliberation 재 render 가 wrap 시작 *전에* 도는 시점. 8-15s 길이 의 chained synthesis 시 ~10s typing 만료. |
| 5 | `should_type_for_*` helper 가 production 에서 호출되지 X — inactive role 의 silence 는 outcome-is-None 우연. defense-in-depth 결여. | P0-E 후속 (본 PR scope 밖) |

## 4. 5-commit 시퀀스 (단일 PR)

| commit | 내용 | risk |
| --- | --- | --- |
| 1 | 본 audit doc (코드 변경 0) | 0 |
| 2 | `_dispatch_member_message` 의 placeholder 를 실제 dispatch 로 (sync closure 의 80 line 을 모듈-레벨로 hoist). `run_member_bot` 의 closure 도 같은 helper 호출. 양 launcher 가 동일 dispatcher 공유. | MEDIUM |
| 3 | `route_engineering_message` 의 `conversation_fn` 호출 + `_handle_join_or_append` 의 thread continuation 호출을 `typing_keepalive` 로 wrap. ignored / non-actionable / bot-echo refusal 분기는 wrap 진입 *전*에 분기되므로 silence 보존. | LOW |
| 4 | `member_bot._post_research_turn` / `_post_team_turn` 호출 위치를 `typing_context` → `typing_keepalive(interval=6.0)`. 8s → 6s rationale 명시 (Discord ~10s 만료 vs 4s 버퍼). | LOW |
| 5 | 회귀 test 추가 (runtime path dispatch parity / typing keepalive keepalive 유지 / ignored typing off) + docs 정리 | LOW |

## 5. 회귀 위험

- **duplicate post**: `_mark_team_turn_persisted` (session.played_roles) + `_was_recently_handled` (in-process recency cache) 둘 다 commit 2 의 hoist 후에도 보존 — 한 bot/token 은 한 번만 로그인 가능 (Discord 4004) 이므로 양 launcher 동시 실행 시도 자체가 token-level 차단됨. risk: LOW.
- **interval 8→6s**: Discord typing rate-limit (5/5s per channel). 5 active role 이 한 forum thread 에서 동시 typing 시 rate limit 검증 필요 — 본 PR 의 commit 5 에 sanity test 명시.
- **`should_type_for_*` defense-in-depth**: 본 PR scope 밖. P0-E 후속 issue.
- **dev/test 회귀 0**: closure 의 capture 가 `profile` 단 1개라 module-level 으로 옮겨도 동작 동일. 기존 `tests/discord/test_member_bots.py` 회귀 test 통과 예상.

## 6. Acceptance Criteria

PR 머지 시점:
1. `yule runtime up` spawn 한 member bot 이 `[research-open:*]` / `[research-turn:*]` / `[team-turn:*]` 모두 실제 게시
2. `yule discord up` (dev/test) 회귀 X — 같은 dispatcher 공유
3. gateway 의 `conversation_fn` long-running path 에 typing keepalive 유지
4. member bot 의 chained synthesis 8-15s 동안 typing 끊김 X (interval 6s)
5. ignored / non-actionable / bot-echo / inactive role 에서 typing 안 켜짐
6. duplicate post 가드 보존 (played_roles + recency cache)
7. 회귀: `pytest tests` 전체 PASS

## 7. 후속 (P0-E)

- `should_type_for_member_research` / `should_type_for_gateway_action` 의 production wiring (defense-in-depth)
- runtime lock (fcntl + SQLite row) — `runtime up` + `discord up` 동시 실행 차단
- heartbeat 통일 + `HEALTH_DISABLED` label
- `build_member_bot_env_overrides` — gateway intake channel leak 방지

## 8. 참고

- P0-C v1: PR #133 (merged 2026-05-13)
- P0-D issue: #134
- P0-C original issue: #132
