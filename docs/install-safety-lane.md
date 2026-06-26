# Install/Activation Safety Lane — 외부 tool/skill/plugin → 승인 게이트 → activation receipt (SSoT)

> 본 문서는 ForgeKit runtime 이 외부 tool/skill/plugin 을 **approval chain 아래에서
> 안전하게 활성화/설치/사용/기록** 하도록 묶는 단일 SSoT 다. 코드 SSoT 는
> [`packages/forgekit-runtime/src/forgekit_runtime/activation/`](../packages/forgekit-runtime/src/forgekit_runtime/activation/)
> (`states.py` / `classify.py` / `receipt.py` / `bridge.py` / `ledger.py`), 회귀는
> [`tests/forgekit/test_install_safety_lane.py`](../tests/forgekit/test_install_safety_lane.py),
> evidence 는
> [`apps/forgekit-console/examples/install-safety/activation-lane.json`](../apps/forgekit-console/examples/install-safety/activation-lane.json).
>
> 승인 게이트는 **재구현하지 않는다** — Hephaistos forge governance
> ([`forge/bridge.py`](../packages/forgekit-runtime/src/forgekit_runtime/forge/bridge.py))
> 와 **동일한** `run_internal_chain` → `decision_lane.authorize_runtime_execution`
> 를 재사용한다. 관련: [`hephaistos-governance.md`](hephaistos-governance.md).

## 0. 한 줄 요약

- 외부 tool 은 **공급망 리스크** 가 있다. 그래서 이 레인은 **"추천됨" ≠ "설치됨" ≠
  "실행됨"** 을 lifecycle state 로 강제 분리한다.
- 후보를 catalog 에 넣는 것(추천)은 활성화가 아니다. 활성화(설치/attach/enable/
  execute)는 **반드시** 동일한 승인 체인을 통과해야 하고, 그 결과는 **activation
  receipt** 로 영속된다.
- **fake "installed" 금지:** `enabled`/`executed` outcome 은 실제 인가를 **요구**
  한다. receipt validator 와 ledger 가 위조를 거부한다.

## 1. 흐름

```text
ActivationCandidate (id/kind/source/present/needs_install/global_write/safety/why)
  → derive_readiness_state            ← collected/curated/armory-registered/attachable/install-required/approval-needed
  → classify_activation               ← safe / risky / blocked (공급망 FACTS 기준, verb 아님)
  → run_internal_chain                ← PM → gateway → tech-lead (기존)
  → authorize_runtime_execution       ← decision-lane 런타임 게이트 (executor = devops-engineer)
  → ActivationReceipt                 ← verdict + identity + supply-chain flags + commit trailer + outcome + evidence
  → record_activation_receipt         ← append-only ledger (runtime-loop 흔적, fake 거부)
```

코드 진입점: `activate(candidate, action, *, operator_approval=None, persist=False)`.

## 2. Lifecycle state model (`states.py`)

| 그룹 | state | 의미 |
| --- | --- | --- |
| 추천(recommendation) | `collected` | discovery 가 surfacing 한 raw 후보 |
| | `curated` | 사람이 검토/노트로 승격 |
| | `armory-registered` | catalog `WeaponSpec` 존재(알려진 capability) |
| 준비(readiness) | `attachable` | present+vetted+safe → **설치 없이** 사용 가능(무위험) |
| | `install-required` | 미존재 → 설치 필요(공급망 리스크) |
| | `approval-needed` | install/global-write/external/unknown → 게이트 승인 필요 |
| 결과(outcome) | `enabled` | 승인 + 활성화(설치/attach)됨 — 위조 아님 |
| | `executed` | 실제로 실행됨 |
| | `blocked` | 거부(destructive, 또는 승인 없는 risky) |

- `derive_readiness_state` 는 **절대 outcome state 를 반환하지 않는다** — outcome
  state(`enabled`/`executed`/`blocked`)는 **bridge 만** 실제 verdict 후에 set 한다.
- transition map 에는 **승인을 건너뛰고 active 로 가는 edge 가 없다**: `enabled`
  진입은 오직 `attachable` 또는 `approval-needed` 에서만, `executed` 는 `enabled`
  에서만. `install-required → enabled` 직행은 불가(반드시 `approval-needed` 경유).

## 3. 분류 — safe / risky / blocked (`classify.py`)

리스크는 **action verb 가 아니라 후보의 FACTS(공급망)** 에서 나온다. STRICTEST 가
이긴다:

- `install_required`(설치 필요 / `install` action) → 최소 risky.
- `global_write`(PATH/전역 설정/repo 외부) → 최소 risky.
- `external_source`(builtin/armory 아님) → 최소 risky(미신뢰 출처).
- `unknown_safety`(safety 미선언) → 보수적 risky(safe-by-rejection).
- wording(`deploy`/`secret`/`infra`/`rm -rf`) 또는 `forbidden=True` → **blocked**.

→ `safe` 는 오직 **present + armory-registered + safe-safety + no-global-write** 의
attach/enable/execute 뿐이다. 즉 *이미 검증된 도구를 실행하는 무위험 경로* 만 무마찰.

## 4. 게이트 바인딩 (`bridge.py`)

`activate()` 는 verdict 를 single source of truth 로 삼는다:

- **safe** attach → 내부 서명(`can_execute`)만으로 인가 → `enabled`.
- **risky** install → **실제 operator approval 이 있을 때만** 인가 → `enabled`. 없으면
  `blocked`(trailer 없음, active outcome 없음).
- **blocked** → 어떤 경우에도 인가 안 됨(operator approval 이 있어도).
- `execute` action 으로 인가되면 outcome `executed`, 그 외 인가는 `enabled`.

trailer 는 **인가된 verdict 에만** 발급(`flow="activation"`). 차단 경로엔 가짜 승인
metadata 가 없다.

## 5. Receipt / evidence (`receipt.py`)

`ActivationReceipt` 는 operator/audit 가 나중에 **"왜 이 도구를 썼는가"** 를 답하는
artifact 다: candidate/source/action/from→to state/disposition/approval_metadata/
supply_chain_flags/commit_trailers/**evidence(why)**/blocking_reasons.

anti-fake(`validate_activation_receipt`):

- `enabled`/`executed` outcome 은 `authorized` 를 **요구**(fake "installed" 금지).
- `authorized` → approval_metadata + 레지스트리-known executor + commit trailer 필수.
- 미인가 → blocking_reasons 필수, trailer/active-outcome 금지.

## 6. Runtime-loop 흔적 + 영속 (`ledger.py`)

- `record_activation_receipt` = append-only JSONL(`activation_receipts.jsonl`,
  runtime state dir). 인가/차단 **모든 verdict** 를 한 줄로 영속 → 활성화 결정이
  휘발하지 않는다. fake 는 persistence 경계에서 hard 거부.
- `latest_states(env)` = 로그를 fold 해 각 candidate 의 **현재 lifecycle state** 를
  돌려준다 = runtime 의 activation 메모리(별도 store 파일 없음).
- `activation_ledger_lines` = read-only line projection(새 UI layer 없음) — operator
  surface 가 한 helper 만 호출해 출력.

## 7. 비고

- physical 설치/attach 자체는 caller(toolchain manager / harness)의 책임. 이 레인은
  그 act 에 **승인을 바인딩** 하고 증거를 발급한다.
- executor 는 `devops-engineer`(레지스트리 engineering 역할). route/identity 는
  [`forgekit-agent-identity`](../README.md) 명명표를 따른다.
