# Hephaistos Governance — forge plan → 실제 실행 게이트 → execution receipt (SSoT)

> 본 문서는 Hephaistos forging core 를 governance backbone 에 연결하는 단일
> SSoT 다. 코드 SSoT 는
> [`packages/forgekit-runtime/src/forgekit_runtime/forge/`](../packages/forgekit-runtime/src/forgekit_runtime/forge/)
> (`classify.py` / `receipt.py` / `bridge.py`), 회귀는
> [`tests/forgekit/test_hephaistos_governance.py`](../tests/forgekit/test_hephaistos_governance.py),
> evidence 는
> [`apps/forgekit-console/examples/pm-techlead-lane/hephaistos-forge-governance.json`](../apps/forgekit-console/examples/pm-techlead-lane/hephaistos-forge-governance.json).
>
> 승인 게이트는 **재구현하지 않는다** — 자가개선 경로
> ([`selfimprove/execute_bridge.py`](../packages/forgekit-runtime/src/forgekit_runtime/selfimprove/execute_bridge.py))
> 와 **동일한** `run_internal_chain` → `decision_lane.authorize_runtime_execution`
> → `autopilot.validate_execution` 를 재사용한다.

## 0. 한 줄 요약

- Hephaistos `resolve(request)` = forge plan(specialist + skills + loadout +
  weapons + work packet). 지금까지 이 plan 은 **governance/실행 경로에 미연결**
  이었다 (execute_bridge 는 self-improvement packet 만 다룸).
- 본 레인은 forge plan 을 **분류 → 실제 승인 체인 → execution receipt** 로
  묶어 "회사처럼 움직이는 에이전트 조직" 의 governance backbone 을 forging
  표면에서 닫는다.
- **fake 금지:** 차단된 plan 은 trailer 도, executed 도 없다. receipt validator
  가 위조(미승인 trailer / 승인인데 metadata 없음 / 미인가 executed)를 거부.

## 1. 흐름

```text
hephaistos.resolve(request)            ← forge: agent+skills+loadout+weapons+packet
  → classify_forge_plan                ← safe/risky/destructive (packet level + weapon safety + goal wording)
  → run_internal_chain                 ← PM → gateway → tech-lead (기존)
  → authorize_runtime_execution        ← decision-lane 런타임 게이트 (executor = forge 가 고른 specialist)
  → validate_execution                 ← autopilot 재검증
  → ForgeExecutionReceipt              ← verdict + identity + weapon class + commit trailer + outcome
```

코드 진입점: `forge_execute(request, *, operator_approval=None, weapon_safety=None)`.

## 2. 분류 — safe / risky / destructive (`classify.py`)

`classify_forge_plan` 은 다음 **가장 엄격한** 값을 취한다.

1. work packet 의 `approval_level` (L2/L3/L4),
2. **weapon safety** — `required_weapons` 중 armory `risky` 무기가 있으면 최소
   risky 로 승격. **unknown 무기도 safe 아님**(safe-by-rejection → risky),
3. **goal wording** — goal 텍스트의 deploy/secret/schema/auth → destructive
   (`autopilot.approval.classify_level` 재사용).

> **주의:** `forbidden_scope` 는 분류 입력에서 **제외**한다. 그 필드는 "deploy/
> schema/auth 를 건드리지 말라" 는 가드레일을 *명시* 하므로, 분류에 넣으면 모든
> plan 이 destructive 로 오분류된다. 작업이 *무엇을 하는가* 는 goal 이 말한다.

weapon safety resolver 는 주입 가능(기본 = armory 카탈로그 lazy 조회) — 순수/
테스트 가능.

## 3. 실제 실행 게이트 적용 (`bridge.py`)

- executor = forge 의 `selected_agent`(registry 식별자). 미상이면 chain 의
  routed owner, 그래도 미상이면 `backend-engineer` fallback(항상 `is_known`).
- `authorize_runtime_execution` 가 gateway + tech-lead + (risky 면) operator +
  실행시점 재분류를 강제.
- **honest boundary (execute_bridge 와 동일):** `authorized` 는 **safe-class +
  chain(can_execute) + decision-lane + validate_execution 전부 통과** 일 때만
  True. risky/destructive(위험/미상 무기 포함)는 `blocked` — 실행 안 됨, trailer
  없음. risky + operator 승인이어도 autopilot 체인은 safe 만 자동 실행하므로
  honest 하게 `blocked`(propose) 로 기록된다.
- 실제 파일 mutation 은 여전히 `BoundedMutator` 게이트 — 본 단계는 **승인을
  forged 작업에 묶고 그 증거(receipt)를 발급**.

## 4. Execution Receipt (`receipt.py`)

`ForgeExecutionReceipt` = forge plan 한 건이 어떤 승인 아래 실행됐는지의 증거.

| 필드 | 의미 |
|---|---|
| request / selected_agent / selected_loadout / selected_skills / required_weapons | 무엇을 누구에게 equip 했나 |
| action_class / approval_level / risky_weapons | 어떻게 분류됐나 |
| authorized / outcome | 인가 여부 + executed/blocked/awaiting/error |
| approval_metadata / chain_trace / commit_trailers | 누가 승인했나 + commit 이 무엇을 달고 나가나 |
| blocking_reasons | 차단 사유(미인가 시) |

### 4.1 anti-fake (`validate_forge_receipt`)

- `authorized` → approval_metadata 必, executor `is_known` 必, commit_trailers 必,
  destructive 면 authorized 불가.
- 미인가 → blocking_reasons 必, commit_trailers **금지**(있으면 fake approval),
  outcome=executed **금지**.
- outcome=executed → authorized 必.

→ 승인/실행이 실제로 일어나지 않은 receipt 는 **만들 수 없다**.

## 4.2 operator surface — `/resolve`

governance 는 라이브러리 함수에 그치지 않고 **operator 가 실제로 본다**.
`/resolve <요청>` 출력 끝에 `── governance ──` 섹션이 붙어 forged plan 의
class(safe/risky/destructive) · 인가 여부 · approval metadata 를 보여준다
(코드: `forgekit_console.commands.router._forge_governance_lines`, best-effort —
forgekit_runtime 부재 시 resolve 요약만 그대로). 이로써 forging 표면이 동일
승인 게이트에 **런타임에서** 연결된다.

## 4.3 decision log 영속 (`ledger.py`)

receipt 가 콘솔 출력으로 끝나면 "decision log" 가 아니다. `forge/ledger.py` 가
append-only JSONL(`state_dir/forge_receipts.jsonl`, **vault 아님** — 별도 트랙)에
receipt 를 누적해 결정 로그를 **영속** 시킨다.

- `forge_execute(..., persist=True)` → 발급된 receipt 를 ledger 에 1줄 append.
  `/resolve` 미리보기는 `persist=False`(읽기 전용).
- `record_forge_receipt` 는 persistence 경계에서 `validate_forge_receipt` 를 다시
  돌려 **fake receipt 를 거부**(`FakeReceiptRefused`) — 위조 승인/실행은 durable
  로그에 절대 못 들어간다. I/O 실패는 best-effort(결정 유실 없음), fake 는 hard 거부.
- `read_forge_receipts(env=, limit=)` 로 audit 재생.

## 5. 무엇을 닫았나 (axis2/axis3)

- PM→gateway→tech-lead→**specialist** chain 을 forge plan 의 실제 specialist
  (`selected_agent`)로 runtime 에 연결.
- meeting/decision/handoff/approval(decision-lane) + safe/risky/destructive +
  approval chain 을 forging 실행 경로에 적용.
- commit trailer / agent identity / approval metadata / **execution receipt** 를
  forged 작업에 바인딩.
- ForgeKit 내부 축 정렬: Hephaistos = execution core 가 governance 게이트 아래에
  정렬됨(별도 경로 아님 — 동일 게이트 재사용).

## 6. 한계 / 비목표

- 실제 파일 mutation 은 BoundedMutator 경로(별도). 본 레인은 승인+receipt.
- risky 의 autonomous 실행은 열지 않는다(autopilot safe-only hard rail 유지).
- self-improvement 자동 promote 금지(기존 hard rail).

## 7. 동기화

- [`AGENTS.md`](../AGENTS.md) §2 — Hephaistos governance 행.
- [`CLAUDE.md`](../CLAUDE.md) — runtime governance hard rails cross-link.
- 승인 게이트 SSoT 는 [`pm-techlead-lane.md`](pm-techlead-lane.md), 본 문서는
  forging 표면 적용으로 cross-link.
