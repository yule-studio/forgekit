# ForgeKit 완성 세션 — coordinator 계약 (gw0 SSoT)

> 본 doc 은 이번 세션의 **총괄 coordinator(gw0)** 가 유지하는 운영 SSoT 다. 새 기능 설계가
> 아니라 **pane 분리 / 완료 조건 / handoff packet / merge order / next-step queue** 의 계약이다.
> 척추 로드맵은 [`forgekit-goal-roadmap.md`](forgekit-goal-roadmap.md), 방향은
> [`control-plane-architecture.md`](control-plane-architecture.md) 가 SSoT — 본 doc 은 그 **남은 seam**
> 만 실행 pane 에 배분한다(중복 정의 금지, 참조만).
>
> **상태:** live coordinator 가동. gw0 은 일회성이 아니라 3 lane 이 끝날 때까지 계속 돈다.
> §LIVE 섹션이 실측 SSoT — 아래 §0/§1 의 *초기 가정*(GW4-B/GW2-B/approve-deny seam)은 pane 들이
> 실제로는 다른 작업을 골라 **실측으로 대체됨**(아래 §LIVE 참조, fake 금지).

## 🟢 LIVE 운영 표면 = `.claude/shared/forgekit-wave/` (WAVE-2 OPEN)

> 운영은 이제 worktree 공유 디렉터리에서 돈다(4 worktree 가 절대경로로 접근):
> `master-goal.md`(최종 목표+completion def) · `acceptance.md`(A~E 체크리스트) ·
> `gw{1,2,3}-status.md`(lane 자가 갱신, gw0 수집) · `blockers.md` · `merge-order.md` · `next-wave.md`.
> 본 git doc 은 **커밋된 narrative 이력**. 최종 목표 = ForgeKit 을 *에이전트 회사+대장간+장기목표
> control plane+멀티브레인 코어* 로 수렴(no fake parity).
>
> **WAVE-2 ✅ CLOSED (main eabcb08, forgekit 973 OK):** gw1 #340(GW4-B in-loop 실행 teeth) · gw3 #342
> (in-console approve/deny UI + INTEG-1) · gw2 #343(per-provider budget + mode→slot 분리). 각 CI green +
> coordinator 직접 verify 후 머지. OVERLAP-WATCH-1 은 gw2 가 provider 로 pivot 해 해소(3 lane disjoint).
>
> **acceptance(A~E):** A multi-brain ✅ · C 경계 ✅ · E cockpit parity ✅ · D 기능 거의 ✅(unit autoinstall
> minor) · B 승인 teeth ✅ — **잔여 = B 의 GW2-B agent-identity↔git-author CI 바인딩**(core 마감).
>
> **✅ CORE COMPLETION (정직판, main de622f7, forgekit 998 OK):** #348(execute bridge)가 자기관리 루프를
> 폐합했다 — `execute_approved_packet`(3중 게이트 safe-class 인가 → execution/verification evidence
> write-back)이 구현·export 되어 console approve 가 실제 게이트 실행을 호출(코드로 검증). #346 identity
> 검증 + 트레일러 builder 동반. **acceptance A~E 충족(no-fake, 코드+테스트+evidence+직접 final QA).**
> 명시적 design 경계(incompleteness 아님): 물리 autonomous mutation=BoundedMutator-gated · live claude/
> codex=policy-gated. 잔여 non-core: /provider budget CLI·unit 자동설치·BoundedMutator 물리 mutation·
> app→app paydown. **디자인/Figma wave 이제 open 가능.** SSoT = `.claude/shared/forgekit-wave/`.

## ✅ WAVE-1 COMPLETE (3 lane main 머지)

> poll5 (실측): STABILIZE 통과 → operator 인가 하에 issue+PR→CI green→merge 전부 실행 완료.

| lane | PR | issue | branch | CI | 결과 |
| --- | --- | --- | --- | --- | --- |
| gw3 console parity | **#337** | #336 | feat/forgekit-console-parity | governance+test+notify **pass** | ✅ MERGED 03:00:35Z |
| gw2 setup bootstrap | **#334** | #238(umbrella) | feat/forgekit-setup-bootstrap | governance+test+notify **pass** | ✅ MERGED 03:00:38Z |
| gw1 decision-lane+enforcement | **#338** | #335 | feat/forgekit-pm-techlead-lane | governance+test+notify **pass** | ✅ MERGED 03:01:49Z |

- **merged main = `bbf311d`.** 최종 QA: forgekit 전체 suite **945 tests OK (skipped=116, env-gated)** —
  coordinator 가 origin/main detached worktree 에서 직접 실행(fake 아님).
- **honesty note:** 1차 QA 에서 `apps/engineering-agent/src` 를 PYTHONPATH 에 잘못 포함해 engineering-agent
  의 env-dependent 테스트 1개가 실패로 잡혔으나, CI 설치 scope(=forgekit packages)로 재실행 시 사라짐 →
  forgekit 회귀 아님. 각 PR CI 의 full `tests/` discover 도 green.
- **머지 중 사고/수정(troubleshooting):** gw1 worktree 가 기본 `worktree-gw1-pm-techlead`(0e4062e 빈
  브랜치)가 아니라 `feat/forgekit-pm-techlead-lane`(abdc311)에서 작업 → 처음 빈 브랜치를 push 해 "No
  commits" PR 실패. 빈 ref 삭제 후 올바른 브랜치 push 로 정정(#338). gw3 는 worktree 브랜치에서 작업해
  정상. gw2 는 기존 draft #334 가 이미 현재 HEAD(90b938a)로 push 돼 있어 ready 전환만.

## NEXT-WAVE queue (wave-1 done → 다음 세부 목표)
ForgeKit core completion 남은 seam(머지된 main 기준):
1. **INTEG-1 마무리** — `tests/forgekit/__init__.py` `_PKG_SRCS` 에 provider/provider-connect/toolchain/
   nexus/hephaistos/armory 추가(현 contracts/config/goal/runtime 만). CI 비차단이나 pre-install 로컬
   경로 정직성. **소형, 단독 PR 후보.**
2. **GW2-B commit identity 바인딩** — author/committer ↔ `forgekit_config.identity` 대조 + trailer
   approval-metadata. (기존 미착수 seam.)
3. **in-console approve/deny UI** — gw1 의 runtime enforcement(승인 게이트)가 들어왔으니, console 에서
   awaiting_approval → approve/deny 표면 + 그 게이트 호출. (gw3 parity 위에 얹음.)
4. push 후 정리: merged feat 브랜치 정리(선택), reality matrix(`forgekit-goal-roadmap.md` §1) 갱신.

> wave-1 은 closed. 위 4 개는 다음 wave 의 lane 후보 — operator 가 어느 것부터 열지 정하면 gw0 가
> 동일 패턴(분리/acceptance/handoff/merge runbook)으로 orchestrate.

---

## (이력) poll 4 — 3 pane 재가동, runbook armed

> poll 4 (실측): **3 pane 전부 아직 작업 중 — "마무리" 조건 미충족.** operator 가 "모두 마무리되면
> 이슈+PR→CI green→머지" 를 인가([[feedback_no_auto_merge]] 를 이 3 lane 한정 override). 따라서 머지
> runbook 은 **armed(무장)** 상태이되, 아래 STABILIZE 게이트가 충족될 때까지 push/PR/merge 보류(fake
> 완료·moving-target 머지 금지).

### poll4 활동 신호 (모두 active)
| pane | HEAD | commits | uncommitted | last commit | 비고 |
| --- | --- | --- | --- | --- | --- |
| gw1 | `abdc311` | 6 | 0 | ~2분 전 | decision-lane → **runtime enforcement(실행 binding)** 까지 확장(≈GW4-B 흡수). 빠르게 진행 중 |
| gw2 | `01b749a` | 4 | **9** | ~12분 전 | draft **PR #334**(`feat/forgekit-setup-bootstrap`) 존재. 9 uncommitted = 재개·편집 중 |
| gw3 | `40840fc` | 4 | 0 | ~5분 전 | parity harness 에 paste/clipboard/multiline/image staged 검증 추가. 진행 중 |

### STABILIZE 게이트 (이게 충족돼야 "마무리" → runbook 발사)
각 pane 이 **전부** 만족해야 머지 진입:
1. uncommitted 0 (clean tree).
2. 직전 poll 대비 **새 commit 0** (= 한 poll 사이클 ≥약 quiet — commit 간격이 2~5분이라 최소
   1 사이클 정적 확인. 애매하면 2 사이클).
3. 타깃 테스트 green (재검증) + commit-governance pass (재검증 — HEAD 가 바뀌었으므로).
4. 파일 overlap 재확인(현재 1:1 disjoint).

### 머지 runbook (armed — STABILIZE 후 deterministic 실행)
> 세 lane 모두 main(0e4062e) 독립 분기 + 파일 disjoint → **stacked 아님, 충돌 0 예상**, merge order 무관.
> 머지는 각 PR **CI green 확인 후에만**. [[feedback_no_auto_merge]] 는 이 3 lane operator-override 로만 해제.
1. **gw1** — issue(✨Feature+🤖Agent-runtime) → `git -C gw1 push origin worktree-gw1-pm-techlead:feat/forgekit-decision-lane`
   → PR(issue linkage + 5섹션 + audit block, `--assignee codwithyc --label "✨ Feature" --label "🤖 Agent-runtime"`) → CI green → merge.
2. **gw3** — issue(✨Feature+✅Test) → push `:feat/forgekit-console-parity` → PR → CI green → merge.
3. **gw2** — 이미 draft **PR #334** 존재. 9 uncommitted 커밋 후 `feat/forgekit-setup-bootstrap` 갱신 push
   → issue linkage 추가 + `gh pr ready 334` + label/assignee 확인 → CI green → merge.
- 머지 방식: `--merge`(merge commit) 우선([[feedback_commit_splitting_policy]]). 브랜치 미삭제(child PR 없음이라
  auto-close 위험은 없으나 관성 유지). 머지 후 gw0 가 root CI(`unittest discover`) 최종 green 확인.

### 사전 검증 완료 (poll4 시점 HEAD 기준, 머지 시 재확인 필요)
- commit-governance: gw1 6 / gw2 4 / gw3 4 commit **전부 OK**(validator 직접 실행). gw2 의 9 uncommitted 는
  커밋 후 재검증 필요(콜론-후-섹션헤더/Co-Authored-By 금지 재확인).
- CI 구조: `pip install -e .` 가 모든 packages/* 등록(pyproject find) → **INTEG-1 은 CI 비차단**(설치 후 import OK).
  INTEG-1 = can-fix-later(격리/clean-checkout 로컬 경로만, gw3 `_PKG_SRCS` 확장으로 해소 가능).
- 테스트: gw1 19 green / gw2 11 green(전 src) / gw3 seam green + env-gated skip. **HEAD 변경분(gw1 enforcement,
  gw3 paste, gw2 WIP)은 STABILIZE 시 재실행**.

> poll 3 (이력): 3 pane 커밋·clean·done 후보였으나 poll4 에서 전부 재가동. 아래는 poll3 기준 표(이력).

| pane | 실제 scope (실측) | 상태 | latest commit | acceptance (직접 검증) |
| --- | --- | --- | --- | --- |
| **gw1** | `forgekit_runtime/decision_lane/` PM·Tech-Lead 결정 레인(schema+validators+lane) + `docs/pm-techlead-lane.md` + AGENTS/CLAUDE cross-link + tests + evidence | ✅ **done** | `a229818` (3 commit) | **19 tests green** ✅ |
| **gw2** | `/setup` control-plane bootstrap — `bootstrap.py`(provider·provider-connect·toolchain·hephaistos/nexus 정직 집계) + `H_SETUP` registry/router + examples + docs + test. **draft PR #334 CI green**([[project_forgekit_setup_bootstrap]]) | ✅ **done (CI), 🟡 local test-path gap** | `01b749a` (4 commit) | **11 tests green (전 src 주입 시)** ✅ — 단 격리 실행 시 ImportError(아래 INTEG-1) |
| **gw3** | console parity lane — `tui/app.py` TranscriptSink turn 경계 + `tests/forgekit/__init__.py` 패키지 src 주입 + parity geometry/runtime harness + evidence + docs | ✅ **done (seam), 🟡 geometry env-gated** | `d05da66` (3 commit) | anti-fake seam test green ✅ + 2 skipped(`textual`/`pbcopy` 부재 — env-gated, CI 엔 존재). 6-goal geometry 는 textual 없으면 미검증(honest) |

### INTEG-1 (cross-pane 발견, must-fix before merge) — test-path 패키지 src 불완전
- **증상:** gw2 `test_bootstrap` 가 격리(pre-install) 실행 시 `ModuleNotFoundError: forgekit_provider` →
  실 원인 = `tests/forgekit/__init__.py` 가 bootstrap 이 import 하는 패키지 src 를 충분히 주입 안 함.
- **현 상태:** gw3 의 `__init__.py`(345c284)는 `contracts/config/goal/runtime` 만 주입. gw2 bootstrap 은
  추가로 **provider · provider-connect · toolchain · nexus · hephaistos · armory** 가 필요.
- **검증:** 그 6개 src 를 PYTHONPATH 에 넣으면 gw2 **11 tests OK**. 즉 gw2 로직은 green, CI #334 도 real
  (CI 는 `pip install -e .` 라 영향 없음). **격리/clean-checkout 로컬 실행만 깨짐.**
- **handoff:** gw3 가 `__init__.py` 의 `_PKG_SRCS` 튜플을 위 6개로 **확장**(소유=gw3). gw2 는 확장본에서
  `test_bootstrap` green 재확인. → 머지 전에 닫아야 root CI `unittest discover` pre-install 경로가 정직.

**충돌/overlap 감시 (실측 — 매우 깨끗):**
- 공유 파일 touch 가 pane 별로 **1:1 분리** → **머지 충돌 0 예상**:
  - gw1 → `AGENTS.md`, `CLAUDE.md` (only)
  - gw2 → `commands/registry.py`, `commands/router.py` (only)
  - gw3 → `tui/app.py`, `tests/forgekit/__init__.py` (only)
- gw2·gw3 둘 다 `apps/forgekit-console` 안이지만 **파일 disjoint**(registry/router vs tui/app.py).
- 단 INTEG-1 해소로 gw3 가 `__init__.py` 를 확장하면 그 파일은 여전히 gw3 단독 → 충돌 없음.

**revised merge order (실측 기반):**
1. **gw3** — `__init__.py` src 주입(INTEG-1 확장 포함) + TUI parity. 테스트 인프라가 먼저 들어가야
   gw1·gw2 테스트가 clean-checkout/pre-install 에서 green.
2. **gw1** — decision_lane(runtime, 격리·이미 green). gw3 인프라 위에서 clean.
3. **gw2** — `/setup` bootstrap. INTEG-1 이 gw3 에서 닫힌 뒤 머지해야 pre-install 테스트 green.
   - 세 lane 독립이라 순서는 권장. **머지는 operator 승인 시에만**([[feedback_no_auto_merge]]).

**next-wave queue:**
- 지금 wave 가 닫히면(3 머지 + INTEG-1) 남은 ForgeKit core seam = GW4-B 실행 bridge(승인 packet→
  실제 게이트 실행). gw1 decision_lane 이 그 PM/tech-lead 결정 표면을 이미 제공 → 다음 세부 목표 후보.

---


## 0. 왜 이 세션이 열렸나 (남은 seam = fake 아님, 코드로 확인됨)

GW1~GW8 은 main 머지 완료([[project_forgekit_goal_control_plane]]). reality matrix 에 남은 척추
seam 3 개가 자기관리 루프를 **아직 닫지 못한** 지점이다. 각 seam 은 코드로 실재 확인했다:

| seam | 현 상태(코드 확인) | 빠진 것 | pane |
| --- | --- | --- | --- |
| **GW4-B 실행 bridge** | `autopilot.chain.run_internal_chain(RepoFinding)`·`can_specialist_execute`·`execution.validate_execution(decision, split)` 존재. `RepoImprovementPacket`(selfimprove/packet.py)·`RepoFinding`(autopilot/artifacts.py) 존재. `goal_tick` 는 route/record 까지만. | 승인된 packet → finding 변환 → 게이트된 실행 → verify → evidence write-back | **gw1** |
| **GW2-B identity 바인딩** | `forgekit_config.identity.{registry,models}`(`resolve_identity`/`AgentIdentity`) + `repo_write_policy.validate_commit_message`(GW2-A CI guard) 존재. | commit author/committer ↔ identity registry 대조 + trailer 기반 approval-metadata 강제 | **gw2** |
| **in-console approve/deny** | `/daemon` 은 status-only(registry.py:108), console "NEVER approves active"(tui/app.py:789), goal `awaiting_approval` 상태(forgekit_goal)·`/goal` surface 존재. | awaiting_approval goal/packet 을 console 에서 approve/deny → goal 상태전이 + (gw1 bridge 있으면) 실행 트리거 | **gw3** |

이 3 개가 닫히면 DoD #3(실행 게이트 재사용)·#4(goal 자율 루프 실행 teeth)·#5(import 경계는 이미 done)
중 **실행 teeth + operator 승인 표면**이 완성된다.

## 1. Pane 분리 (non-overlap 계약)

> 경계가 겹치면 한쪽만 갱신돼 silently 어긋난다. 각 pane 은 **자기 owner 파일만** 만지고, 공유
> 인터페이스는 아래 "shared seam" 으로 고정 — 다른 pane 의 owner 파일을 수정하면 coordinator 에게
> 보고 후 재배분.

### gw1 — `gw1-pm-techlead` · GW4-B 실행 bridge (teeth)
- **owns:** `packages/forgekit-runtime/src/forgekit_runtime/selfimprove/` 의 **신규** `execution_bridge.py`
  (또는 `goal_execute.py`). `goal_tick.py` 는 read-only 참조(linkage 모델 재사용), 수정 금지.
- **scope:** 승인된 `RepoImprovementPacket` → `RepoFinding`(artifacts) 변환기 → 기존
  `run_internal_chain` → `can_specialist_execute` 게이트 → `validate_execution` → 결과를 goal 에
  `execution`/`verification` evidence 로 write-back. **승인 게이트 우회 절대 금지**(기존 chain 재사용,
  재구현 금지).
- **does NOT touch:** console(gw3), commit/identity(gw2), `goal_tick.py` 본문, autopilot chain 본문.
- **shared seam (gw3 가 의존):** 공개 함수 시그니처를 **먼저 고정**해서 gw3 가 그 위에 코딩할 수 있게.
  계약: `execute_approved_packet(goal, packet_id, repo_root, *, approver) -> ExecutionOutcome`.

### gw2 — `gw2-provider-runtime` · GW2-B commit identity 바인딩
- **owns:** `packages/forgekit-config/src/forgekit_config/identity/` 확장 + `scripts/ci_check_commit_messages.py`
  (기존, GW2-A) 에 identity-binding 검사 추가 + `.github/workflows/ci.yml` `commit-governance` job.
- **scope:** commit author/committer 가 `resolve_identity` 로 매핑되는 canonical agent 와 일치하는지
  검증(불일치 시 CI fail) + trailer 기반 approval-metadata(`Approved-By:` 등) 파싱·강제. Co-Authored-By
  금지 규칙([[feedback_commit_format]]) 재사용.
- **does NOT touch:** runtime selfimprove(gw1), console(gw3), goal 모델.
- **honest boundary:** CI guard 는 push 후에만 동작. 본 라운드는 로컬 커밋까지 → guard 는 **로컬
  dry-run 재현 가능**해야 함(`scripts/ci_check_commit_messages.py --dry-run` 류). provider/runtime
  budget(#2 gap)는 본 seam 밖 — 욕심내지 말 것(범위 폭주 방지).

### gw3 — `gw3-console-parity` · in-console approve/deny UI
- **owns:** `apps/forgekit-console/src/forgekit_console/` 의 approve/deny surface(신규 `approval_surface.py`
  또는 `goal_surface.py` 확장) + registry/router 배선 + (가능하면) TUI app 의 approve/deny 키 바인딩.
- **scope:** `awaiting_approval` goal + 그 linked packet 을 console 에서 나열 → operator 가
  approve/deny → goal 상태전이(`transitions.apply`) + evidence(`decision`) append. approve 시
  gw1 의 `execute_approved_packet` 호출(gw1 미머지면 **feature-flag/seam stub** 으로 정직 표기,
  fake 실행 금지).
- **does NOT touch:** runtime(gw1), config/identity(gw2), goal 모델 본문(read/transition 만).
- **honest boundary:** 실제 실행 teeth 는 gw1 소유 — gw3 는 **표면 + 상태전이**만. gw1 bridge 없으면
  "approved (실행 대기 — bridge 미머지)" 로 정직 표기.

## 2. Pane 별 완료 조건 (acceptance checklist — 끝나기 전에 먼저 제시)

각 pane 은 PR/handoff 전 아래를 **전부** 체크. 하나라도 ⬜ 면 fake 완료 — coordinator 가 reject.

### gw1 acceptance
- [ ] `execution_bridge.py` 신규 — packet→finding 변환 + 게이트 호출. 700 줄 룰 준수(변환/실행/evidence 분리).
- [ ] 승인 없는 실행 경로 **없음**(`can_specialist_execute` False → 실행 안 함 테스트로 증명).
- [ ] goal 에 `execution`/`verification` evidence write-back, 실패 시 goal `blocked` 전이(done 금지).
- [ ] `tests/forgekit/test_goal_execution_bridge.py` — 승인→실행→evidence / 미승인→차단 / 실패→blocked / safe-class 한정.
- [ ] evidence `apps/forgekit-console/examples/goal/execution.txt` (실 dry-run 트레이스).
- [ ] 한국어 commit 3+ 분할([[feedback_commit_splitting_policy]]): 변환기 / 게이트 배선 / 테스트+evidence.
- [ ] 공개 시그니처 `execute_approved_packet(...)` 를 §1 계약대로 고정(gw3 unblock).
- [ ] 로컬 커밋까지만. push/PR/merge 금지([[feedback_no_auto_merge]]).

### gw2 acceptance
- [ ] identity-binding 검사 추가 — author/committer ↔ `resolve_identity` 일치 검증, 불일치 fail.
- [ ] trailer approval-metadata 파싱 + Co-Authored-By 금지 재사용(중복 구현 금지).
- [ ] 로컬 dry-run 재현 경로(`--dry-run`) — push 없이 검증 가능.
- [ ] `tests/forgekit/test_commit_identity_binding.py` — 일치 통과 / 불일치 거부 / trailer 누락 거부 / Co-Authored-By 거부.
- [ ] evidence `examples/commit-governance/identity-binding.txt` (합성 commit 통과+거부).
- [ ] 한국어 commit 3+ 분할: 검사 core / CI job 배선 / 테스트+evidence.
- [ ] 로컬 커밋까지만.

### gw3 acceptance
- [ ] approve/deny surface — awaiting_approval goal+packet 나열, approve/deny 액션.
- [ ] approve → `transitions.apply`(legal 전이만) + `decision` evidence. deny → 정직 상태 유지/blocked.
- [ ] gw1 bridge seam: 있으면 `execute_approved_packet` 호출, 없으면 "실행 대기" 정직 표기(fake 금지).
- [ ] router 순수성 유지(surface 는 render/CRUD/transition 만, 실행 로직 미소유).
- [ ] `tests/forgekit/test_approval_surface.py` — list / approve→전이+evidence / deny / missing / bridge-absent 정직.
- [ ] evidence `examples/goal/approval-surface.txt`.
- [ ] operator-surfaces matrix 에 approve/deny 행 추가.
- [ ] 한국어 commit 3+ 분할. 로컬 커밋까지만.

## 3. Handoff packet (pane 시작 시 복사해서 전달)

```
PANE: gw1-pm-techlead
GOAL: GW4-B 실행 bridge — 승인된 packet 을 게이트된 실행으로 닫는다.
READ FIRST: docs/forgekit-goal-roadmap.md §6 GW4, control-plane §6,
            packages/forgekit-runtime/.../autopilot/{chain,execution,artifacts}.py,
            selfimprove/{goal_tick,packet}.py
OWN: selfimprove/execution_bridge.py (신규) + tests + evidence
CONTRACT: execute_approved_packet(goal, packet_id, repo_root, *, approver) -> ExecutionOutcome
GATE: 기존 run_internal_chain/can_specialist_execute/validate_execution 재사용 (우회 금지)
DONE: §2 gw1 acceptance 전부 ✅ + 로컬 커밋. push 금지.
```
```
PANE: gw2-provider-runtime
GOAL: GW2-B commit identity 바인딩 — author/trailer 를 identity registry 에 묶는다.
READ FIRST: forgekit-goal-roadmap §6 GW2-B, forgekit_config/identity/*,
            scripts/ci_check_commit_messages.py, [[project_forgekit_agent_identity_ssot]]
OWN: identity 검사 확장 + ci_check_commit_messages.py + ci.yml + tests + evidence
DONE: §2 gw2 acceptance 전부 ✅ + 로컬 커밋. push 금지.
```
```
PANE: gw3-console-parity
GOAL: in-console approve/deny — awaiting_approval 을 operator 가 console 에서 처리.
READ FIRST: forgekit-goal-roadmap §6 GW5, goal_surface.py, commands/{registry,router}.py,
            tui/app.py:742-789, forgekit_goal transitions
OWN: approval_surface.py (또는 goal_surface 확장) + 배선 + tests + evidence
SEAM: gw1.execute_approved_packet (없으면 정직 stub)
DONE: §2 gw3 acceptance 전부 ✅ + 로컬 커밋. push 금지.
```

## 4. Merge order (의존 기준 — 머지는 operator 승인 시에만)

> [[feedback_no_auto_merge]] / 로드맵 §4: 본 라운드는 **로컬 커밋까지만**. 아래는 operator 가
> 머지를 승인할 때의 **권장 순서**이지 자율 머지 인가가 아니다.

```
1. gw1 (teeth)        — gw3 의 실행 의존성. 먼저 머지돼야 gw3 의 approve 가 실제 동작.
2. gw3 (surface)      — gw1 bridge 위에 approve→execute 배선. gw1 머지 후 seam stub 제거.
3. gw2 (governance)   — commit identity guard 강화. 마지막에 머지 → 형제 PR 들을 mid-flight 로
                        막지 않음(guard 강화는 모든 후속 commit 에 영향). 단 개발은 병렬 가능.
```

- **병렬 개발 OK, 머지만 직렬.** 3 pane 모두 `0e4062e` 독립 분기라 동시 진행 가능.
- **gw3 는 gw1 없이도 PR 가능**(seam stub) — 단 stub 인 채 머지하면 "approve 가 실행 안 함"을
  정직 표기해야 함. gw1 머지 후 stub 제거 follow-up.
- gw8 식 최종 QA(root CI unittest discover green + reality matrix 갱신)는 3 pane 머지 후 coordinator 가 수행.

## 5. Next-step queue (coordinator 가 유지·갱신)

| # | 단계 | 담당 | 상태 |
| --- | --- | --- | --- |
| 1 | pane 분리 + 완료조건 + handoff 계약 확정(본 doc) | gw0 | ✅ done |
| 2 | gw1/gw2/gw3 pane 에 handoff packet 전달, 착수 | operator → 각 pane | ⬜ 대기 |
| 3 | gw1 `execute_approved_packet` 시그니처 고정 → gw3 에 공유 | gw1→gw0→gw3 | ⬜ blocked on #2 |
| 4 | 각 pane acceptance checklist 검수(coordinator review) | gw0 | ⬜ blocked on #2 |
| 5 | merge order 대로 operator 승인 머지(gw1→gw3→gw2) | operator | ⬜ blocked on #4 |
| 6 | 최종 QA(root CI green) + reality matrix/operator-surfaces 갱신 | gw0 | ⬜ blocked on #5 |

## 6. 승인 / blocked / merge 결정 지점 (operator 액션만 짧게)

- **operator 액션 필요(지금):** 3 pane 에 §3 handoff packet 전달 + 착수 허가. (coordinator 는 pane 을
  직접 spawn 하지 않음 — 분리/계약/검수만.)
- **머지 결정점:** 각 pane acceptance ✅ 확인 후, §4 순서대로 operator 가 머지 승인. 자율 머지 없음.
- **blocked 예상:** gw3 가 gw1 시그니처를 기다림(#3). gw1 이 시그니처만 먼저 확정하면 gw3 병렬 진행 가능.

## 7. honest boundaries (이 coordinator 가 안 하는 것)

- **직접 큰 코드 구현 안 함.** gw0 은 orchestration / review / merge-prep / evidence 관리. 실제
  teeth/identity/surface 코드는 각 pane.
- **자율 머지/ push 안 함**([[feedback_no_auto_merge]]). 로컬 커밋·로컬 검수까지.
- **fake 완료 금지.** acceptance 미충족 pane 을 done 으로 보고하지 않음. seam stub 은 stub 으로 표기.
- **범위 폭주 금지.** provider budget(#2)·모놀리스 분해·외부 connector(P2)는 본 세션 밖.

> 본 doc 은 pane 진행에 따라 §5 queue 와 §6 결정점을 **갱신**한다(coordinator SSoT 유지).

## (final-completion wave) main e1e0c7e — 4축 거의 닫힘, 잔여 2건 operator 결정 의존
- 머지: #353(BoundedMutator 물리 safe-class 실행 + evidence→vault) · #354(/provider budget CLI) · #355(unit 자동설치). final QA forgekit OK.
- 축2 ✅(meeting/decision/handoff LaneTrace end-to-end VERIFIED) · 축4 ✅ · 축3 ✅(경계 강제; app→app paydown=별도 트랙) · 축1 🟡(budget CLI·routing·brain↔transport ✅, live claude/codex transport 남음).
- **잔여 2건(내가 임의 종결 불가):** ① live claude/codex transport = no-fake-live 정책 결정 ② app→app 51-edge paydown = 모놀리스 분해 별도 트랙. SSoT=`.claude/shared/forgekit-master/acceptance.md`.

## ✅ FORGEKIT 최종 완성 (main e1e0c7e) — 4축 ALL CLOSED, operator 결정 반영
잔여 2건이 operator 결정으로 종결됨(2026-06-22): live claude/codex transport=no-fake-live 정책 유지(closed-with-boundary) · app→app paydown=별도 트랙 deferred. 따라서 4축 acceptance 전부 closed(✅ 또는 operator-confirmed terminal). 코드+테스트+evidence+직접 final QA, fake 없음. SSoT=`.claude/shared/forgekit-master/acceptance.md`.
