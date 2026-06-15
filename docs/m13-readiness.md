# M13 Readiness — M8~M12 Post-Landing Gap Audit

본 문서는 M8~M12 병렬 작업을 M13(LLM 통합 / 라이브 자기개선 루프) 진입 전에 한 번 감사한 결과다. 목표는 **수정이 아니라 readiness 판정**이다. 즉시 패치한 것은 P0/P1 뿐이고, 나머지는 M13 통합 범위 또는 M14 backlog 로 분류했다.

본 문서 작성 도중 다른 worktree 에서 M11b / M12 / F-M13 세 커밋이 합류했다 (`1d3fbfe` ✨ M11b RoleRunner dispatcher gateway/run-service bootstrap wiring, `d083446` ✨ M12 self-improvement detect → proposal planner + supervisor dispatch hook, `8812500` ✨ F-M13 Senior-Agent MVP 통합 — research order 단일 루프). 본 §2 gap matrix 의 "현 상태" 컬럼은 이 세 커밋이 들어간 직후의 HEAD 를 기준으로 한다.

연관 문서:
- 운영 가이드 §0.1/0.2/0.3 (런타임 경로 + 서비스 인벤토리 + 라이브 스모크) — `docs/operations.md`
- 운영 가이드 §11 (P0 Secret Hygiene + Token Rotation) — `docs/operations.md`
- 운영 가이드 §12 (F-M13 Senior-Agent MVP 통합) — `docs/operations.md`
- 라이브 회귀 §0.4 (Secret Hygiene 미완료 시 진행 금지) — `policies/runtime/agents/engineering-agent/live-regression.md`
- 라이브 회귀 §7 (F-M13 Senior-Agent MVP 통합 시나리오) — `policies/runtime/agents/engineering-agent/live-regression.md`

## 1. 입력 커밋 + 감사 대상 범위

| Milestone | 핵심 커밋 | 핵심 산출물 |
| --- | --- | --- |
| M7-final | 17c4cb1 | fallback / degrade / circuit-break + status posting |
| A-M7.5 / A-M7.5b / A-M7.5d~f | b92fe3e / 534a8c7 / 78fbde1 / a1096f6 / e2f1b3b | forum 토의 + role-change + Obsidian handoff |
| A-M7.6 | f989606 | research topic ledger (`agents/lifecycle/research_topic.py`) |
| A-M10a | 26dee50 | autonomy policy L0~L4 + agent-ops audit log |
| A-M10b | 90559fa, 86a2c00 | autonomous note kinds + Obsidian routing/hydration |
| A-M10c | 25730df | research-log auto trigger + self-improvement signal skeleton |
| A-M10a (Knowledge Ops) | 5aaa079 | canonical 5 종 note kind + folder mapping |
| A-M11 | 2063965 | RoleRunner ABC + provider-priority dispatcher |
| M8 | 19b4f52 | runtime up 1 급 운영 경로 + status operator hint |
| P0 Secret | 5966ef3 | 토큰 rotation 운영 절차 |
| 회귀 팩 | 74e9324 | M9/M10 Topic+Hydration regression pack |

감사 대상은 위 커밋이 만든 **현재 working tree 상태** 다 (M11/M12 일부 wiring 은 아직 commit 전 working-tree 변경 형태로 남아 있음을 명시).

## 2. Gap Matrix

P0 = M13 진입 차단(즉시 패치). P1 = M13 통합 안에서 반드시 닫아야 함. P2 = M13 이후 회귀 위험은 낮으나 정리 필요. P3 = M14+ backlog / 명명 정리 / 문서화.

| ID | 영역 | 발견 | 분류 | 처리 |
| --- | --- | --- | --- | --- |
| G-M9-01 | M9 / topic ledger module 명 | canonical 경로가 `agents/lifecycle/research_topic.py` 로 이미 안정화됐고 forum_obsidian_handoff / standalone_runners 가 이를 참조 중. "topic_ledger" 로 rename 하자는 사전 제안은 reject. | P3 (accepted decision) | rename 하지 않음. 본 §3 에 결정 사항으로 못박음. |
| G-M10c-01 | M10c / Obsidian research-log writer | 현재 `tests/obsidian/` 에 `test_research_log_writer.py` 가 존재하지 않음. 커버리지는 `tests/obsidian/test_obsidian_writer_worker.py` + `tests/engineering/test_autonomous_producers.py` 로 간접 확보. | P3 (test 명시화) | M14 backlog. |
| G-M11-01 | M11 / role-runner gateway bootstrap | M11b 커밋 `1d3fbfe` 에서 `agents/runners/bootstrap.py` (env-driven dispatcher factory + sanitised reason 8 종) 가 신설되고, `discord/bot.py` `_install_engineering_role_runner_dispatch_for_gateway` 와 `runtime/run_service.py` `_install_role_runner_dispatch_for_run_service` 양쪽이 호출. install 실패는 swallow + `type(exc).__name__` 만 노출. test 회귀 16 OK (`tests/runners/test_runner_bootstrap.py` 11 + `tests/discord/test_runner_bootstrap_wiring.py` 5). | **CLOSED** (M11b 합류로 해소) | 추가 작업 없음. 라이브 회귀 시 stdout/stderr 의 `role-runner dispatch installed: …` 1 줄 + `agent_ops_audit` 의 `role_runner_dispatch` 행 한 번 점검. |
| G-M12-01 | M12 / supervisor self-improvement detect+dispatch hook | M12 커밋 `d083446` 에서 `run_supervisor_watch_loop` 가 `self_improvement_detect_fn` / `self_improvement_dispatch_fn` / `self_improvement_interval_seconds` 3 인자를 받고, `_run_self_improvement_tick` 이 detect → `plan_self_improvement_proposal` → dispatch 로 묶임. **후속(WT2)에서 `runtime/run_service.py` `_run_async` supervisor 분기가 `_build_self_improvement_loop` 로 detect/dispatch/interval 3 인자를 실제로 넘기도록 닫힘** — `YULE_SELF_IMPROVEMENT_ENABLED` opt-in 시 production 스케줄러가 tick 한다. 추가로 `build_queue_executor_enqueue_fn` + `YULE_SELF_IMPROVEMENT_ENQUEUE_ENABLED` 2차 opt-in 으로 detected proposal 이 실제 `coding_execute` job(draft-PR only)으로 큐에 enqueue 됨 (`draft_pr_only` 없으면 enqueue 거부). | **CLOSED (runtime level)** | 단위/통합 회귀: `test_self_improvement_queue_handoff.py` (tick → coding_execute enqueue, non-draft 거부, 2차 flag opt-in). 라이브 회귀 시 supervisor stdout 의 `self-improvement runtime loop enabled (... queue_handoff=on)` 1 줄 점검. |
| G-M12-02 | M12 / F-M13 / 신호→proposal→note 종단 점검 | F-M13 커밋 `8812500` 에서 `agents/lifecycle/senior_agent.py` 신설. `handle_research_order` 가 topic ledger + L1 audit + role-runner dispatch + research-log enqueue 를 한 함수에 묶고, `emit_self_improvement_proposal` 이 detect → planner → enqueue 종단을 한다. `tests/runtime/test_senior_agent_mvp.py` 11 OK. 라이브 e2e (스케줄러 tick → 승인 카드 → vault) 는 G-M12-01 의 `run_service` wiring 이 닫혀야 검증 가능. | P1 (G-M12-01 와 묶임) | G-M12-01 와 같은 후속 커밋에서 닫는다. 라이브 회귀 §7 시나리오 F 가 이 종단 검증을 정의한다. |
| G-M8-01 | M8 / runtime up 운영 경로 라벨 | `cli/main.py` runtime up / discord up 분기에 [PRODUCTION]/[DEV-ONLY] 라벨, `runtime/services.py` 12 개 서비스 description 강화, `runtime/status.py` 에 STALE/UNKNOWN 워닝 + 6-step smoke checklist 가 합류. `tests/runtime/test_status_m8_operator_hints.py` 215 OK. | CLOSED | 추가 작업 없음. |
| G-Sec-01 | P0 Secret Hygiene | `docs/operations.md` §11 + `docs/discord.md` §1.1 + `policies/runtime/agents/engineering-agent/live-regression.md` §0.4 가 토큰 rotation 절차와 라이브 회귀 차단 게이트를 명문화. 9 개 봇 토큰 rotation 은 운영자 직접 액션. | P0 (운영자 액션 대기) | 라이브 회귀 / production restart 는 §11 완료 전까지 시작 금지. 자동화 회귀(`python3 -m unittest discover -s tests -t .`) 는 secret 없이 동작하므로 계속 가능. |

## 3. 명시 결정 사항 — M9 module rename 거부

- **결정**: `agents/lifecycle/research_topic.py` 경로를 그대로 둔다. `topic_ledger.py` 등 다른 이름으로 옮기지 않는다.
- **이유**:
  1. 현 호출자 (`forum_obsidian_handoff.py:593,1093`, `standalone_runners.py:791`) 가 안정적으로 import 중. rename 은 import 표면 churn 만 발생시킨다.
  2. M7.6 / M9 회귀 (`tests/engineering/test_research_topic_ledger.py` 21 OK + `tests/job_queue/test_forum_obsidian_handoff.py` 18 OK) 가 현 경로를 fixture path 로 직접 사용 중.
  3. 모듈 안 객체 이름 (`TopicLedgerRecord`, `read_topic_ledger`, `write_topic_ledger`, `compose_record_for_session`) 자체는 이미 의미 명확.
- **반영**: 본 문서가 명시적으로 "no rename" 을 선언하므로 향후 review 에서 다시 거론하지 않는다. M14 에서도 backlog 에 올리지 않는다.

## 4. M13 Readiness 판정 — Conditional Go

- **Go**:
  - M8 운영 경로 / status operator hint / smoke checklist 통과.
  - M9 topic ledger 안정 + 회귀 21 OK.
  - M10a~c knowledge ops + autonomy policy + research-log + self-improvement skeleton 합류.
  - M11 RoleRunner dispatcher (M11b `1d3fbfe`) 가 gateway 양쪽 (`bot.py`, `run_service.py`) 에서 idempotent 설치 + sanitised reason audit. M13 LLM 통합 진입점 열림.
  - M12 detector + planner + supervisor 훅 (M12 `d083446`) + Senior-Agent MVP 코디네이터 (F-M13 `8812500`) 가 합류. tests/runtime/test_senior_agent_mvp.py 11 OK.
  - 회귀 통계: 2373 OK / 0 FAIL (M11b/M12/F-M13 합류 직전 기준). 합류 직후 회귀는 후속 PR 에서 다시 한 번 전체 discover 권장.
- **Conditional**:
  1. **G-M12-01 (runtime level) — CLOSED (WT2)** — `runtime/run_service.py` `_run_async` supervisor 분기가 `_build_self_improvement_loop` 로 detect/dispatch/interval 를 실제로 넘긴다. `YULE_SELF_IMPROVEMENT_ENABLED` opt-in 시 스케줄러 tick, `YULE_SELF_IMPROVEMENT_ENQUEUE_ENABLED` 2차 opt-in 시 proposal 이 `coding_execute` job(draft-PR only)으로 enqueue. 남은 건 라이브 회귀 §7 시나리오 F 의 실제 e2e 관찰(자동화 회귀는 `test_self_improvement_queue_handoff.py` 로 확보).
  2. **G-Sec-01** — 9 개 봇 토큰 rotation + runtime restart + 위생 점검(`docs/operations.md` §11.1~§11.4) 이 끝나기 전까지 라이브 검증 시작 금지. 자동화 테스트는 무관하게 계속 돌림.
- **No-go 사유 없음**: M11 dispatcher install 미연결 의심 항목은 M11b 커밋으로 해소. M12 detector → planner → supervisor 훅 / Senior-Agent 코디네이터 / 라이브 회귀 §7 시나리오까지 합류. 남은 건 production scheduler tick 한 줄.
- **요약**: **M13 진입 가능. 단 G-M12-01 의 run_service 한 줄 wiring 과 G-Sec-01 운영자 액션 두 개를 M13 통합 후속 PR / 라이브 회귀 직전에 닫는다.**

## 5. M13 통합 후속 PR 가 다뤄야 할 항목 (in-scope)

| 항목 | 위치 | 비고 |
| --- | --- | --- |
| supervisor self-improvement scheduler wiring | `runtime/run_service.py` `_run_async` supervisor 분기 → `run_supervisor_watch_loop(..., self_improvement_detect_fn=…, self_improvement_dispatch_fn=…, self_improvement_interval_seconds=…)` | detect_fn 은 `agents.lifecycle.senior_agent.emit_self_improvement_proposal` 로 묶는 게 가장 가깝다 (M12 planner + ObsidianWriteRequest enqueue 까지 한 번에). interval 기본값 600s 권장, env override (`YULE_SELF_IMPROVEMENT_INTERVAL_SECONDS`) 검토. dispatch_fn 은 enqueue 헬퍼. |
| 라이브 회귀 §7 시나리오 F 점검 진입 | `policies/runtime/agents/engineering-agent/live-regression.md` §7.6 | tick 발생 → `#승인-대기` 카드 (L3 위험 verb 일 때) → vault `self-improvement-proposal` 파일 종단 검증. 위 wiring 이 들어가야 라이브에서 자동으로 발생. |
| RoleRunner 라이브 trace 확인 | 기동 시 stdout 에 `role-runner dispatch installed: …` 1 줄 / supervisor 로그 점검 | 본 문서 §2 G-M11-01 의 추가 점검 항목. M11b 의 sanitised reason 라벨 확인. |

## 6. M14+ Backlog

| 항목 | 분류 | 메모 |
| --- | --- | --- |
| `tests/obsidian/test_research_log_writer.py` 분리 신설 | P3 | 현재 간접 커버리지로 충분. M14 documentation pass 때 정리. |
| `engineering_team_runtime.py:1205` `set_role_runner_dispatch` 모듈 hook 의 module-global 상태 → DI 주입 형태로 정리 | P3 | M11b 정리 작업 (테스트 외 호출자가 실제 production 에 한 곳 더 생기는 시점에 검토). |
| status posting `#봇-상태` 의 dedup-key 충돌 회귀 자동화 | P3 | M7.1 시점부터의 백로그. M14 운영 안정화 패스. |

## 7. 회귀 명령 + 라이브 검증 차단 게이트

자동화 회귀 (secret 없이 가능):

```bash
python3 -m unittest discover -s tests -t .
```

라이브 회귀는 다음 두 게이트를 모두 통과해야 시작:

1. `docs/operations.md` §11 (P0 Secret Hygiene) 6 단계 모두 ✅.
2. 본 문서 §5 G-M12-01 wiring 이 commit 되어 있고 supervisor stdout 에 `role-runner dispatch installed: …` 1 줄 + supervisor watch loop 가 self-improvement tick 을 실행한다는 로그가 보임.

라이브 회귀 진입 시 따라가는 체크리스트:

- `policies/runtime/agents/engineering-agent/live-regression.md` §0.4 (Secret Hygiene 게이트)
- `policies/runtime/agents/engineering-agent/live-regression.md` §6 (M7.5 forum + handoff)
- M13 통합 PR 가 추가할 §7 (self-improvement tick → 승인 → vault)

## 8. 문서 cross-link 위치

- `docs/operations.md` §0.3 끝부분에서 본 문서로 한 줄 link.
- `policies/runtime/agents/engineering-agent/live-regression.md` §0 끝부분에서 본 문서로 한 줄 link.
