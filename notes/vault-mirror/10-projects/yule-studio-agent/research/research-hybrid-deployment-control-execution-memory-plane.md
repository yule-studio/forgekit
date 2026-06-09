---
title: "하이브리드 배포 구조 — control / execution / memory plane 분리와 3 profile 비교"
kind: research
status: captured
created_at: 2026-05-31T00:00:00+09:00
tags: [research, hybrid-deployment, control-plane, execution-plane, memory-plane, oci, deployment-profile]
related:
  - "[[decision-hybrid-deployment-plane-split]]"
  - "[[task-log-hybrid-deployment-plane-split]]"
  - "[[decision-tech-lead-runtime-loop-issue-73]]"
  - "[[decision-integration-polish-issue-81]]"
  - "[[decision-ecc-foundation]]"
home_hub: "[[decision-hybrid-deployment-plane-split]]"
---

# 핵심 요약

`yule-studio-agent` 는 **하나의 머신에 다 두기 어려운 시스템**이다. Discord
gateway·GitHub App·approval surface 는 *항상 켜져* 있어야 하고, 코드 편집·
git push·테스트 실행은 사용자의 로컬 git/IDE/secret 에 *닿아 있어야* 하며,
Obsidian vault·SQLite·retrieval index 는 *데이터 민감도와 사용자 습관* 양쪽
을 동시에 만족해야 한다. 본 노트는 그래서 **control plane / execution plane
/ memory plane 3 축으로 시스템을 잘라 두는 이유** 와, *최소비용형 / 표준형 /
확장형* 3 profile 을 **택일이 아니라 기능별로 섞어 쓰는 배포 매트릭스** 로
정리한 분석이다.

핵심 결론은 결정 노트 [[decision-hybrid-deployment-plane-split]] 가 책임지며,
본 research 는 그 결정의 근거 자료다.

# 내 해석

## 1) 왜 split 이 필요한가

C1~C4 까지 구현되면서 시스템 안에 *성격이 분명히 다른* 부하가 함께 자랐다.

- **Discord gateway · GitHub App webhook · approval surface · job queue
  supervisor** — Discord async 이벤트와 GitHub 콜백을 *항상 수신* 해야 한다.
  사용자의 노트북이 꺼져 있어도 동작해야 함. 사람 손이 가지 않는 분.
- **coding executor · worktree · `git push` · 테스트 실행 · 로컬 IDE 연동 ·
  Ollama** — 사용자의 git 자격증명 / 로컬 파일시스템 / GPU·CPU·메모리·전기
  /` ssh-agent` 가 필요. 클라우드에서 보안적으로 흉내내려면 비용·복잡도가
  급격히 오른다.
- **Obsidian vault · curated note · retrieval index · SQLite session state ·
  agent_ops_audit** — 사용자가 GUI 로 직접 보기도 하고 (Obsidian.app),
  RAG·status diagnostic 이 머신 백그라운드에서도 읽는다. 데이터 민감도와
  접근 패턴이 *각기 다르다*.

이 세 영역은 *항상 켜져 있어야 하는가* / *사용자 머신 자원에 닿아야 하는가*
/ *데이터 민감도* 가 서로 다르기 때문에, 한 그릇에 담으면 그릇의 가장 약한
제약이 시스템 전체를 결정한다. 분리해 두면 영역별로 가장 잘 맞는 호스트에
배치할 수 있다.

## 2) 3 plane 정의

| Plane | 책임 | 대표 컴포넌트 (C1~C4 기준) |
|---|---|---|
| **Control plane** | 외부 인입 / 상태 머신 / 승인 surface / 디스패치 / supervisor | Discord bot, GitHub App webhook receiver, `engineering_channel_router`, `agents/job_queue/*`, `agents/lifecycle/session_status.py`, approval-matrix 카드, council 의 `gateway_surface_payload` |
| **Execution plane** | 실제 코드 편집 / git 명령 / 테스트 / LLM 호출 | `coding_executor_worker`, `worktree` provisioner, live LLM editor, Ollama, Claude/Codex/Gemini API client, smoke / lint 실행 |
| **Memory / data plane** | 운영 메모리 / 결정 기록 / 자료 / 메트릭 | Obsidian vault (사용자 GUI + git mirror), `notes/vault-mirror/`, retrieval index, SQLite (`workflow_sessions`, `job_queue`), `agent_ops_audit`, role_councils/approval_packet payload |

3 plane 은 코드상에선 같은 Python 프로세스 안에서도 돌아갈 수 있지만, *배치
선택의 단위* 는 plane 이다. 한 plane 안의 컴포넌트는 같은 호스트로 묶여도 큰
사고가 없고, plane 을 가로지를 때는 명시적 인터페이스 (HTTP / SQLite WAL / git
push / Discord API) 가 이미 존재한다 — 이게 split-friendly 구조의 핵심.

## 3) 비교표 — 로컬-only / cloud-only / hybrid

| 항목 | 로컬-only | cloud-only | hybrid |
|---|---|---|---|
| **장점** | 데이터 100% 자기 머신, 비용 0, 로컬 GUI/Ollama 자유 사용 | 24/7 가동, 사용자 머신 무관, scale-out 용이 | plane 별로 강점만 따다 씀, blast radius 통제, 비용 최소 |
| **단점** | 사용자가 켜야만 동작, Discord/GitHub 이벤트 놓침, 노트북 sleep / VPN 단절에 취약 | 비용 ↑, 로컬 GUI·Obsidian·ssh-agent·Ollama 와 단절, secret 을 cloud 에 모두 박아야 함 | 운영 복잡도 ↑ (2 곳 동시 관리), plane 경계 정합성 유지 부담 |
| **보안** | 사용자 머신 보안 = 시스템 보안. secret 외부 노출 없음 | cloud 자격증명 + 로그 노출이 새 위험 영역 | secret 을 작업 민감도별로 *분리 보관* 가능 (가장 안전한 선택지) |
| **운영 난이도** | 낮음 — 자기 PC 켜기만 | 중간 — IaC / 모니터링 / 비용 알람 필요 | 중간-높음 — 두 plane 사이 동기화·실패 모드 설계 필요 |
| **비용** | 전기·인터넷만 | 상시 인스턴스·전송·스토리지 | OCI Always Free 위주면 control plane 0원, 로컬 executor 0원 |
| **항상 켜져 있어야 하는가** | ❌ (사용자 의존) | ✅ (전 컴포넌트) | ✅ (control plane 만) |
| **GUI / 로컬 리소스 접근** | ✅ 모두 가능 | ❌ 불가능 또는 어려움 | ✅ execution / memory plane 에서 가능 |
| **추천 use case** | PoC, 짧은 데모, 오프라인 작업 | 다인 운영, 기업/팀 production | 1-2 인 + always-on + 민감 데이터 혼재 — **본 프로젝트의 현실 모드** |

핵심 관찰: 본 프로젝트는 *Discord/GitHub 이벤트 받기* + *사용자 머신의 코드/
GUI 닿기* + *데이터 민감도가 일관되지 않음* 을 동시에 요구한다. 단일 호스트
선택은 이 셋 중 하나를 반드시 희생시킨다.

## 4) OCI 가 적합한 역할 (공식 Always Free 기준)

[Always Free 공식 문서](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
의 무료 자원 한도가 control plane 에 *충분* 한지 검증한 결과:

| OCI Always Free 자원 | 한도 | 본 프로젝트에서의 용도 |
|---|---|---|
| `VM.Standard.E2.1.Micro` (AMD) | 1/8 OCPU · 1 GB RAM · 최대 2 인스턴스 | Discord bot 1 + supervisor/webhook 1 (가벼움) |
| `VM.Standard.A1.Flex` (ARM Ampere) | 총 4 OCPU · 24 GB RAM (3000 OCPU-hour/month, 18000 GB-hour/month) | research collector / retrieval indexer / cloud-safe worker 풀 |
| Block storage | boot + block 합산 200 GB · 백업 5 회 | SQLite + vault git mirror + 로그 보관 |
| Object Storage | 20 GB · API 50000/month | vault snapshot / approval packet archive |
| Autonomous DB | 2 개 × 20 GB · 1 CPU · serverless | (선택) SQLite 대안 — 본 시스템은 SQLite 로 충분, DB 까지는 옵션 |
| Outbound 전송 | 10 TB/month | Discord/GitHub 트래픽으로는 과한 한도 |
| Email Delivery | 3000 통/month | 운영 알림 / approval card 보강 (옵션) |
| Notifications | HTTPS 1M/month · 이메일 1000/month | `#봇-상태` mirror 또는 escalation 알림 |
| Site-to-Site VPN | 50 IPSec | 가정 NAS / 온프레미스 vault 회로가 필요해질 때 |

**결론:** control plane + cloud-safe worker 일부는 OCI Always Free 한도 안에
들어간다. **추가 비용 0 원으로 always-on 을 살 수 있다는 점이 본 프로젝트의
하이브리드 결정의 결정적 근거다.** 단, Ollama 같은 LLM 추론 / live coding
executor / `git push` 자격증명 작업 / Obsidian GUI 는 OCI 가 *적합하지
않다* — execution / memory plane 의 로컬-우선 결정과 일치한다.

## 5) 3 deployment profile — 택1 이 아니라 mix 기준

본 프로젝트의 *현실적인 시작점* 3 가지. 단계적으로 layer 를 더하는 형태이며,
한 profile 만 골라야 하는 것이 아니다 — 컴포넌트별로 적합한 profile 을 섞는다.

### 5.1 최소비용형 (Minimal-cost)

- OCI: control plane 만 — Discord bot + GitHub App webhook + 가벼운 SQLite
- 로컬: 모든 execution + memory (Obsidian + worktree + git + Ollama 옵션)
- 비용: OCI Always Free 한도 안 → **0 원**
- 트레이드오프: 사용자 머신 꺼지면 coding executor / live editor 정지.
  Discord 이벤트는 OCI 에서 큐잉 — 사용자가 머신 켜면 재개.
- 적합 단계: 1 인 개발자 / 사이드 프로젝트 / C4 직후 — **본 프로젝트의 현재
  default 위치**.

### 5.2 표준형 (Standard)

- OCI: control plane + cloud-safe worker 풀 (research collector, retrieval
  indexer, vault git fetch, approval surface renderer)
- 로컬: secret 필요 작업 / live coding executor / Obsidian GUI / Ollama
- 비용: 여전히 Always Free 한도 위주, A1.Flex 일부 사용 — **0~수 USD/month**
- 트레이드오프: 사용자 머신이 꺼져도 *지식 수집 / curated 정형화 / status
  surface 갱신* 은 계속 진행. coding executor 만 사용자 의존.
- 적합 단계: 1-2 인 + always-on 자료 루프 도입 — C5-C6 진입 시점에 적합.

### 5.3 확장형 (Extended)

- OCI: control plane + cloud execution worker (격리된 worktree, docs 편집,
  CI lint, GitHub PR 댓글 자동화)
- 로컬: 사용자 자격증명 필요한 sensitive worker (secret 접근, prod push 직전
  단계, GUI 검토) 만
- 비용: Always Free 초과 — paid compute / paid storage 일부 사용
- 트레이드오프: 운영 복잡도 ↑, audit 표면 ↑. multi-user 가능.
- 적합 단계: 팀 운영 / 외부 사용자 운영 — 본 프로젝트의 *장기 종착점*.

### 5.4 mix 기준

profile 은 *컴포넌트 단위로* 섞는다. 같은 시스템 안에서:

- "Discord bot 은 표준형 (OCI), Obsidian 은 최소비용형 (로컬), research
  collector 는 표준형 (OCI), live coding editor 는 확장형 으로 *언젠가*
  올려본다" 식으로 혼합 가능.
- 한 컴포넌트의 profile 을 바꾸는 기준은 [[decision-hybrid-deployment-plane-split]]
  §4 의 트리거 (always-on 필요성 / secret blast radius / GUI 의존 / 비용
  한도) 를 따른다.

## 6) C4 까지 구현된 현재 구조의 split-readiness

| 컴포넌트 | 현재 위치 | split-ready? | 비고 |
|---|---|---|---|
| Discord gateway / approval surface | 로컬 프로세스 | ✅ — env 변수 + SQLite path 만 옮기면 OCI VM 으로 이동 가능 | `engineering_channel_router` 가 Discord API client 만 추상화 |
| Job queue + supervisor | 로컬 SQLite | ✅ — SQLite 파일을 block storage 에 두고 같이 이동 | round-trip 모두 JSON-safe |
| Coding executor | 로컬 worktree | ⚠️ — git 자격증명 / live LLM editor 미land. 격리된 cloud worker 가 가능하려면 secret 분리 필요 | C5/C6 의 live wiring 이후에 OCI 격리 worker 검토 가능 |
| Research collector | 로컬 fetcher (urllib `BytesFetcher` 미land) | ⚠️ — fetcher 가 land 되면 OCI A1.Flex 에 자연 적합 | 마스터 플랜 §9 의 남은 일과 일치 |
| Obsidian vault | 로컬 GUI + git remote | ✅ (분리됨) — vault remote (yule-agent-vault) push 만 control plane 에서 자동화 가능 | approval matrix §3 의 `vault_remote_push` 가 mode 따라 결정 |
| ApprovalPacket / gateway_surface_payload (C4) | `session.extra` payload | ✅ — payload 자체가 JSON. control plane 어디서 렌더해도 동일 | C4 wiring 의 부수 효과 |
| Council escalation aggregate (C3/C4) | `session.extra` payload | ✅ — multi-role 집계는 순수 helper | 외부 surface 가 cloud 든 local 이든 무관 |
| Ollama / Claude live runner | 미wired | ❌ (현 시점) — provider 선택 로직 자체가 C5+ | provider availability matrix (C3) 가 cross-check 메타만 보유 |

핵심 관찰: **C4 까지의 구조는 control plane / memory plane 을 cloud 로 옮길
준비가 거의 끝난 상태**. execution plane 은 live LLM editor 가 미land 라
*아직 옮길 게 없다*. 즉 본 시점에 하이브리드를 결정해도 *코드 변경 거의 없이*
운영 분리만 시작 가능하다 — 이게 본 노트가 *지금* 작성되는 이유.

# 적용 맥락

## 6) 문서 지도 (이걸 이해하려면 무엇을 어떤 순서로)

1. `AGENTS.md` §1-2 — 문서 계층과 작업 맥락별 매핑.
2. `CLAUDE.md` 의 "Runtime governance hard rails (P0-T)" — secret /
   deploy / merge 의 영구 hard rail. 본 분리는 이 hard rail 을 그대로 둔 채
   진행하는 것이다.
3. `docs/engineering-company-runtime-master-plan.md` §4 (gateway vs tech-
   lead 경계) + §5 (5 레이어) + §10 (CI/CD 분리) — **시스템 전체** 의 운영
   루프 정의.
4. `docs/engineering-role-council-runtime.md` §3-§6 — **부서 안** 의 role
   council / approval flow / execution review 정의. ①은 시스템, ②는 부서
   안의 의사결정 흐름. 둘이 서로 다른 축이다.
5. `docs/approval-matrix.md` §2-§3 — code / vault 의 L0~L4 자동·승인 매트릭
   스. 어디까지 control plane 이 자동, 어디부터 사용자 승인인지의 SSoT.
6. `docs/autonomy-policy.md` §0 — work mode (`approval_required` /
   `autonomous_merge`) + scope + topology 의 세션-단위 frame.
7. `notes/vault-mirror/.../decisions/decision-ecc-foundation.md` — 외부
   변경 layer 표준화 (skills/hooks/commands). 어디서나 동작 가능한 markdown
   spec layer 가 hybrid 와 가장 자연스럽게 만난다.
8. `notes/vault-mirror/.../decisions/decision-tech-lead-runtime-loop-issue-73.md`
   — coding executor / next-task selector / decision layer 의 4 단계 결정.
   execution plane 의 hard rail 그대로 살아 있다.
9. `notes/vault-mirror/.../decisions/decision-integration-polish-issue-81.md`
   — gateway / autonomy-execution / knowledge-geeknews 3 축 통합 결과.
   hybrid 분리의 *실제 작업 분기 단위* 가 그대로 plane 분리 단위와 거의
   같다.
10. 본 노트 (research) + [[decision-hybrid-deployment-plane-split]] (결정)
    + [[task-log-hybrid-deployment-plane-split]] (작업 기록).

## 7) issue 계열 노트의 축 매핑

| issue | 노트 triad | 본 분리 안에서의 축 |
|---|---|---|
| #73 (tech-lead runtime loop) | [[research-tech-lead-runtime-loop-issue-73]] / [[decision-tech-lead-runtime-loop-issue-73]] / [[task-log-tech-lead-runtime-loop-issue-73]] | execution plane — coding executor / next-task selector / completion hook |
| #81 (integration polish) | [[research-integration-polish-issue-81]] / [[decision-integration-polish-issue-81]] / [[task-log-integration-polish-issue-81]] | 3 plane cross-axis — 모든 plane 의 회귀가 충돌 없이 결합되는지 검증 |
| #25 (ECC foundation) | [[research-ecc-foundation]] / [[decision-ecc-foundation]] / [[task-log-25-ecc]] | control plane — skills/hooks/commands markdown spec layer (cloud-portable) |
| #69 (engineering-agent governance) | [[research-engineering-agent-governance-synthesis-issue-69]] / [[decision-engineering-agent-authoring-policy-issue-69]] / [[task-log-governance-integration-issue-69]] | control plane — write ownership 3-mode + Obsidian / GitHub governance |

# 관련 노트

- [[decision-hybrid-deployment-plane-split]] — 본 분석에 기반한 운영 결정.
- [[task-log-hybrid-deployment-plane-split]] — 본 정리 작업의 기록.
- [[decision-tech-lead-runtime-loop-issue-73]] — execution plane hard rail
  SSoT.
- [[decision-integration-polish-issue-81]] — 3 축 통합 검증 결과.
- [[decision-ecc-foundation]] — control plane 의 외부 변경 layer.
- [[decision-engineering-agent-authoring-policy-issue-69]] — 3-mode write
  ownership 정책.

# 참고

- 코드 SSoT
  - `src/yule_orchestrator/agents/council.py`
  - `src/yule_orchestrator/agents/council_bootstrap.py`
  - `src/yule_orchestrator/agents/council_approval.py`
  - `src/yule_orchestrator/agents/lifecycle/council_substage.py`
  - `src/yule_orchestrator/agents/lifecycle/council_status_signals.py`
  - `src/yule_orchestrator/discord/engineering_channel_router/council_flow.py`
- 마스터 문서
  - [`docs/engineering-company-runtime-master-plan.md`](../../../../../docs/engineering-company-runtime-master-plan.md)
  - [`docs/engineering-role-council-runtime.md`](../../../../../docs/engineering-role-council-runtime.md)
  - [`docs/approval-matrix.md`](../../../../../docs/approval-matrix.md)
  - [`docs/autonomy-policy.md`](../../../../../docs/autonomy-policy.md)
  - [`docs/memory.md`](../../../../../docs/memory.md)
  - [`docs/engineering-agent-governance.md`](../../../../../docs/engineering-agent-governance.md)
- 정책
  - [`policies/runtime/agents/engineering-agent/lifecycle-mvp.md`](../../../../../policies/runtime/agents/engineering-agent/lifecycle-mvp.md)
  - [`policies/runtime/agents/engineering-agent/team-conversation.md`](../../../../../policies/runtime/agents/engineering-agent/team-conversation.md)
  - [`policies/runtime/agents/engineering-agent/review-loop.md`](../../../../../policies/runtime/agents/engineering-agent/review-loop.md)
  - [`policies/runtime/agents/engineering-agent/message-protocol.md`](../../../../../policies/runtime/agents/engineering-agent/message-protocol.md)
- 외부 (Oracle 공식만 사용)
  - https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
