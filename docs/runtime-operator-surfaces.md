# Runtime operator surfaces — provider runtime / self-improvement / eval gate

> "운영 고도화" 3 축(live provider 실전화 · supervisor self-improvement loop · 정량 eval
> gate)이 operator 가 바로 쓸 수 있는 표면으로 닫힌 결과를 한 곳에 모은다. 코드 SSoT 는
> 각 모듈, 본 문서는 *어디서 무엇을 보고 무엇을 하는가* 의 진입점.

## 1. 한 화면 — `yule harness status`

provider runtime / self-improvement / eval / token-efficiency 를 한 대시보드로 접고
**"what to do next"** 를 규칙으로 도출한다.

```
yule harness status [--receipts <execution-receipts.json>] [--session <id>] [--json]
```

- **Provider runtime** — live runs, fallback rate, avg latency, cost(proxy), rule-first %, live-LLM avoided %.
- **Self-improvement loop** — detected / delegated / waiting_operator / blocked (problem ledger 기반).
- **Eval gate** — 최신 `runs/evals/*/comparison.json` 의 variant 비교 요약.
- **Token efficiency** — 누적 절감.
- **What to do next** — waiting proposal 응답, provider fallback 과다/CLI 부재/endpoint 불가, eval 증거 부재 등 actionable 힌트. 없으면 "all clear".

코드: [`agents/harness/operator_surface.py`](../apps/engineering-agent/src/yule_engineering/agents/harness/operator_surface.py) · 회귀 `tests/agents/test_operator_surface.py`.

## 2. Live provider runtime (WT1)

dispatch 마다 execution receipt 의 `provider_runtime` 블록이 *어느 provider 가 실제로
돌았고(live), usage/cost/latency 는 얼마고, 어느 provider 에서 fallback 했는지(표준 failure
class)* 를 남긴다. insights 가 fallback rate / cost / latency / failure 분포로 roll-up.

- 코드: [`provider_runtime.py`](../apps/engineering-agent/src/yule_engineering/agents/harness/provider_runtime.py) (failure taxonomy 8 class), [`cost_model.py`](../apps/engineering-agent/src/yule_engineering/agents/harness/cost_model.py) (vendor-neutral token→USD proxy).
- 현실: Claude=실제 live submit(opt-in `YULE_CLAUDE_LIVE_ENABLED`), Codex/Ollama=현재 availability-only stub. receipt 가 이를 정직히(`usage_basis=estimate`, `live` 판정) 반영.
- cost 는 proxy(공개 list price 근사) — 청구원장 아님. eval gate cost 축에 재사용.
- live 불가 시 deterministic 으로 graceful fallback + 실패 candidate 분류.

## 3. Supervisor self-improvement loop (WT2)

`YULE_SELF_IMPROVEMENT_ENABLED` opt-in 시 supervisor 가 주기적으로 detect → triage →
delegated/operator 로 proposal 을 surface. `YULE_SELF_IMPROVEMENT_ENQUEUE_ENABLED` 2 차
opt-in 시 proposal 이 실제 `coding_execute` job(**draft-PR only**)으로 큐에 enqueue 된다.

- 코드: [`runtime_self_improvement_wiring.py`](../apps/engineering-agent/src/yule_engineering/agents/lifecycle/runtime_self_improvement_wiring.py) (`build_queue_executor_enqueue_fn`), run_service `_build_self_improvement_loop`.
- 안전: 자동 머지/푸시는 delegated_operator escalation + `draft_pr_only` 이중 차단. `draft_pr_only` 없는 payload 는 enqueue 거부.
- 라이브 확인: supervisor stdout `self-improvement runtime loop enabled (... queue_handoff=on)`.
- readiness: [`m13-readiness.md`](m13-readiness.md) §2 G-M12-01 (CLOSED).

## 4. 정량 eval gate (WT3)

고정 task-set 을 실제 resolution 정책으로 결정형 실행해 success / tokens / cost / latency
/ rule-first ratio / provider 분포를 variant(baseline·current·cheap_llm) 간 비교한다.

```
yule harness eval [--slug <slug>] [--date <YYYY-MM-DD>] [--json]
  → runs/evals/<date>-<slug>/ (comparison md+json + per-variant json)
```

- 코드: [`eval_harness.py`](../apps/engineering-agent/src/yule_engineering/agents/harness/eval_harness.py) · 회귀 `tests/agents/test_eval_harness.py`.
- **일반화**: dimension 레지스트리(`DEFAULT_DIMENSIONS`)에 scorer 를 등록하면 새 평가축이
  schema 변경 없이 추가된다 — 후속 디자인 품질 축(reference adherence 등)을 여기 붙인다.
- cost/latency 는 proxy/fixture — 변형 간 *상대* 비교용.

## 4.1 always-on runtime — goal-continuity 가시성 (#372)

`forgekit runtime status` / `/daemon`(`runtime/surface.daemon_status_lines`)은 daemon heartbeat
뿐 아니라 **goal-driven continuity** 도 표면한다(`runtime/goal_status.goal_continuity_lines`):
`goal-loop : active N(serve 자동 진행) · awaiting N(operator 승인 필요) · blocked · done` + awaiting
시 **action-needed**(`/goal approve <id>`) + **last work**(실제 execution/verification evidence).
goal store 를 직접 읽어 정직(없으면 "활성 goal 없음" / store 없음 — fake 진행 표기 없음). 즉 always-on
loop 이 goal 을 진행하는지·무엇이 승인 대기인지 operator 가 runtime status 한 곳에서 본다. 회귀
`test_goal_status_visibility`, evidence `examples/goal-visibility/`.

## 5. 관련
- [`provider-capability-matrix.md`](provider-capability-matrix.md) · [`llm-minimization-policy.md`](llm-minimization-policy.md) · [`operations.md`](operations.md) · [`m13-readiness.md`](m13-readiness.md)
