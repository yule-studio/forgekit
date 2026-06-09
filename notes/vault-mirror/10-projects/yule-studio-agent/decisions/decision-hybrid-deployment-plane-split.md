---
title: "yule-studio-agent 하이브리드 배포 결정 — control plane 우선 클라우드 + execution plane 로컬 + memory plane 혼합"
kind: decision
status: decided
created_at: 2026-05-31T00:00:00+09:00
tags: [decision, hybrid-deployment, control-plane, execution-plane, memory-plane, oci, deployment-profile, c4]
related:
  - "[[research-hybrid-deployment-control-execution-memory-plane]]"
  - "[[task-log-hybrid-deployment-plane-split]]"
  - "[[decision-tech-lead-runtime-loop-issue-73]]"
  - "[[decision-integration-polish-issue-81]]"
  - "[[decision-ecc-foundation]]"
  - "[[decision-engineering-agent-authoring-policy-issue-69]]"
home_hub: "[[decision-tech-lead-runtime-loop-issue-73]]"
---

# 핵심 요약

**한 문장 결론:** *이 시스템은 control plane 은 퍼블릭 클라우드 (OCI Always
Free 위주) 에 우선 두고, execution plane 은 로컬/온프레미스에 두며, memory /
data plane 은 컴포넌트별 민감도·접근 패턴에 따라 혼합하는 하이브리드 운영을
기본값으로 삼는다. 최소비용형 / 표준형 / 확장형 3 profile 은 택일이 아니라
컴포넌트별로 섞어 쓰는 deployment mode 다.*

분석 근거는 [[research-hybrid-deployment-control-execution-memory-plane]] 에,
정리 작업 기록은 [[task-log-hybrid-deployment-plane-split]] 에 있다.

# 내 해석

## 1) 왜 이렇게 나누는가 — 결정 인자 8 개

| # | 인자 | 결정에 미친 영향 |
|---|---|---|
| 1 | **Always-on 요구** | Discord async 이벤트 / GitHub webhook 은 *사용자 PC 와 무관* 하게 받아야 함 → control plane 은 OCI |
| 2 | **로컬 파일 / GUI / Obsidian / Ollama 접근** | 코드 편집, `git push`, Obsidian.app GUI, Ollama 추론은 사용자 머신 자원에 *직접* 닿아야 함 → execution plane 은 로컬 |
| 3 | **Secret blast radius** | `.env`, GitHub App private key, Discord bot 토큰을 모두 한 호스트에 두면 사고 시 전 시스템 노출. 작업 민감도별로 *분리 보관* 가능한 hybrid 가 가장 안전 |
| 4 | **운영 안정성** | 사용자 PC 가 sleep/재부팅/VPN 단절되어도 *대기열 + 승인 카드 + status 진단* 은 살아 있어야 함 — control plane 의 OCI 배치가 충족 |
| 5 | **비용** | OCI Always Free 한도 안에 control plane 이 들어감 ([공식 문서](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)) → **0 원으로 always-on 확보**. Ollama / live LLM 은 로컬이 비용 효율적 |
| 6 | **Latency** | Discord 응답성을 위해 control plane 은 한 region 에 고정. execution plane 은 사용자 머신이라 자동으로 사용자 근접 |
| 7 | **사용자 작업 습관** | Obsidian GUI, IDE, ssh-agent, Ollama 모두 사용자가 자기 머신에서 쓰는 도구 — execution / memory plane 일부는 사용자 동선에 맞춰야 함 |
| 8 | **운영자 가시성 hard rail** | `CLAUDE.md` 의 secret/deploy/merge hard rail. 자동화는 plane 경계에서도 약화되면 안 됨 — 분리해도 동일 정책 적용 |

## 2) 핵심 결정 (D-H-1 ~ D-H-12)

### A. Plane 단위 배치 원칙

| ID | 결정 | 근거 |
|---|---|---|
| **D-H-1** | **Control plane 은 OCI Always Free 우선.** Discord bot, GitHub App webhook, supervisor, job_queue, approval surface (gateway_surface_payload renderer), council status signals 게시 모듈은 OCI 에 배치한다. | always-on 요구 + 비용 0 + 사용자 PC 독립성 |
| **D-H-2** | **Execution plane 은 로컬/온프레미스 우선.** `coding_executor_worker`, worktree, live LLM editor, `git push`, smoke 테스트, Ollama, secret 접근 작업은 사용자 머신에 둔다. | secret blast radius / 사용자 자격증명 / 로컬 자원 접근 |
| **D-H-3** | **Memory / data plane 은 컴포넌트별 혼합.** Obsidian vault GUI 는 로컬, vault git mirror 는 양쪽, SQLite (`workflow_sessions`) 는 control plane 옆, `agent_ops_audit` 는 양쪽 보관, role_councils / approval_packet payload 는 JSON 으로 모두 round-trip 가능. | 컴포넌트별 민감도 / 접근 패턴이 다름 |

### B. 3 profile 의 의미

| ID | 결정 | 근거 |
|---|---|---|
| **D-H-4** | **최소비용형 / 표준형 / 확장형은 mix-and-match deployment mode 다.** 한 시점에 하나만 골라 시스템 전체를 옮기는 게 아니라, 컴포넌트별로 어느 profile 에 두는지 결정한다. | research note §5.4 참조. 단일 profile 강제는 자유도 손실. |
| **D-H-5** | **현재 default = "최소비용형 (control plane만 OCI)"** 로 시작. C5/C6 의 live wiring 이 land 됨에 따라 컴포넌트별로 표준형/확장형으로 옮긴다. | C4 까지의 split-readiness 가 control plane / memory plane 에 집중되어 있음 |
| **D-H-6** | **profile 변경 트리거 — 4 가지 조건 중 하나라도 충족하면 재검토.** (i) always-on 요구 증가, (ii) secret blast radius 변화, (iii) 비용 한도 초과 임박, (iv) GUI 의존성 변경. | 변경 결정의 deterministic 트리거를 박아 둠 — ad-hoc 이동 금지 |

### C. 컴포넌트별 우선 배치

| ID | 결정 | 위치 |
|---|---|---|
| **D-H-7** | **우선 OCI 로 이동할 컴포넌트.** Discord bot, GitHub App webhook receiver, job_queue supervisor, approval card renderer, status diagnostic 일부, council status signal 게시. | always-on + 가벼움 + secret 노출 적음 |
| **D-H-8** | **로컬/온프레미스 유지.** coding executor + live LLM editor + Ollama + Obsidian.app GUI + ssh-agent 사용 worker. C5/C6 에서도 *완전 클라우드 이동 안 함*. | secret blast radius + 사용자 자격증명 + 데이터 민감도 |
| **D-H-9** | **양쪽 모두 가능한 컴포넌트.** vault git mirror (control plane 옆에 read-only mirror + 사용자 머신에 master), research collector (cloud-safe fetcher), retrieval index. | 데이터 민감도와 접근 패턴이 컴포넌트 안에서도 갈림 |

### D. C4 기준 운영 hard rail (그대로 유지)

| ID | 결정 | 근거 |
|---|---|---|
| **D-H-10** | **`CLAUDE.md` hard rail (secret / deploy / merge / protected branch / no auto-merge / single executor) 는 plane 분리와 무관하게 유지.** OCI 위에서도 동일 정책 적용. | hard rail 은 plane 경계가 아니라 행동 단위 — `agents/governance/runtime_policy.py` 가 코드 SSoT |
| **D-H-11** | **Approval matrix L3/L4 의 owner 는 gateway 가 그대로 보유.** OCI 의 control plane 이 approval card 를 *렌더링* 하지만, 실제 승인 자체는 사용자 reply 에 따른다. | C4 의 gateway_surface_payload 가 plane 경계 무관하게 동일하게 동작하도록 설계됨 |
| **D-H-12** | **plane 경계 인터페이스는 JSON-safe payload + git + Discord API + GitHub App API 만.** plane 간 사용자 자격증명 공유 / 직접 RPC / cross-plane FS 마운트 금지. | 이미 C1-C4 가 이 형태로 land 되어 있음 — 새 인터페이스 추가 시 본 hard rail 다시 적용 |

## 3) 3 profile 상세 — 컴포넌트별 혼합 기준

상세 비교는 [[research-hybrid-deployment-control-execution-memory-plane]] §5
참조. 본 결정 노트는 *현재 시점의 권장 mix* 만 고정한다.

### 3.1 최소비용형 (현재 default)

| 컴포넌트 | 위치 | 이유 |
|---|---|---|
| Discord bot | OCI E2.1.Micro | always-on |
| GitHub App webhook receiver | OCI E2.1.Micro | always-on |
| job_queue + supervisor | OCI block storage 위 SQLite | 가벼움 + 상태 보존 |
| approval surface renderer | OCI | always-on |
| Obsidian vault GUI | 로컬 | 사용자 도구 |
| vault git mirror | 로컬 master + OCI read-only fetch | 동기화 + 백업 |
| coding executor | 로컬 | secret / 자격증명 |
| Ollama | 로컬 | 비용 / GPU |
| live LLM editor | 미land | C5+ |

비용: **0 USD/month** (Always Free 안).

### 3.2 표준형 (C5 진입 시 권장)

최소비용형 +

| 추가 컴포넌트 | 위치 | 이유 |
|---|---|---|
| research collector (urllib fetcher) | OCI A1.Flex | 사용자 머신 무관, always-on 수집 |
| retrieval indexer | OCI A1.Flex | 가벼운 batch, 비용 0 |
| vault remote push worker | OCI control plane | approval matrix §3 의 mode 결정 따라 자동 |
| status / `#봇-상태` 정기 게시 | OCI Notifications 또는 Email Delivery | 운영 가시성 |

비용: **0~수 USD/month** (Always Free 한도 안 또는 살짝 초과).

### 3.3 확장형 (C6 이후)

표준형 +

| 추가 컴포넌트 | 위치 | 이유 |
|---|---|---|
| cloud execution worker (격리 worktree, docs/lint 자동) | OCI paid compute | 사용자 머신 무관 cloud-safe 작업 |
| approval audit 외부 archive | OCI Object Storage | 장기 보관 / 외부 감사 |
| multi-user gateway | OCI paid + 추가 인증 | 팀 / 외부 사용자 운영 |

비용: paid tier.

## 4) C4 기준 구현 성숙도 (settled / provisional / deferred)

### 4.1 settled (지금 이미 가능)

- Plane 경계가 *JSON-safe payload + git + Discord API + GitHub App API* 로
  이미 좁혀져 있음 — control plane 만 OCI 로 옮겨도 코드 변경 거의 없음.
- council state (`task_brief`, `role_work_orders`, `role_councils`,
  `approval_packet`, `tech_lead_signoff`, `gateway_surface_payload`,
  `council_escalation_aggregate`) 모두 `session.extra` JSON 으로 round-trip
  ([[research-hybrid-deployment-control-execution-memory-plane]] §6 표).
- `engineering_channel_router` 의 helper 들이 silent-swallow + 진단 stamp
  패턴이라 plane 경계 실패에 안전.
- Approval matrix L3/L4 가 owner = gateway 로 고정 — plane 분리해도 정책
  변동 없음.

### 4.2 provisional (옮길 수 있지만 추가 정리 필요)

- **canonical role cleanup 잔여** — `_persist_role_selection` 에서 list 값만
  canonical. `role_selection_reasons` / `role_participation` dict key 가 short
  형 → cloud reader 가 list 와 dict 짝 lookup 시 silent miss 가능.
- **provider availability freshness** — `bootstrap_council` 한 번에만 stamp.
  Ollama on/off 같은 런타임 토글이 반영 안 됨. cloud / local 둘이 동시에
  보는 데이터라 stale-aware 필요.
- **ApprovalPacket → operator surface 재생성** — packet 갱신 시 surface
  payload 자동 재계산 없음. control plane 이 packet 갱신 받았을 때 surface
  도 다시 만들어야 함.

### 4.3 deferred (C5/C6)

- **execution_review live wiring** — type + substage 있지만 CI signal /
  council recheck / review_loop reroute 진입점 없음. execution plane 의 live
  feedback 루프 핵심.
- **retrospective candidate 자동 stamp** — C6 scope. memory plane 의 자산
  축적 루프.
- **live LLM provider runner** — provider × seat matrix + availability
  metadata 까지만. 실제 cloud LLM 호출 / Ollama 호출 wiring 은 미land.
- **packet archive / retention 정책** — `ApprovalPacketStatus.ARCHIVED`
  전이 producer 미land. 장기 보관 위치 (OCI Object Storage) 결정 후.
- **multi-role escalation UX** — C4 의 aggregate payload 까지만. Discord
  embed 분리 / tech-lead 카드 분리 같은 surface UX 는 미land.
- **BLOCKED signoff resolution path** — `apply_signoff_to_session` 가
  BLOCKED 에서 substage advance 안 함. resolution 경로 (재토의 / 재signoff)
  의 명시적 vocabulary 부재.

## 5) 정리 필요한 기술 결정 목록 (C5/C6 전에 닫을 것)

1. **canonical role cleanup 잔여** — `role_selection_reasons` /
   `role_participation` dict key 의 canonical 정합성.
2. **provider availability freshness 정책** — runtime poll 주기 / TTL.
3. **ApprovalPacket → operator surface 재생성 producer** — packet 변경
   감지 → surface 자동 재계산.
4. **BLOCKED signoff resolution path** — 재signoff / 재토의 / escalate-
   tech-lead 의 명시적 분기.
5. **execution_review live wiring** — CI signal → council recheck → review_
   loop reroute 의 1차 진입점.
6. **retrospective candidate 자동 stamp** — `RetrospectiveCandidate.source`
   별 trigger.
7. **packet archive / retention 정책** — `ARCHIVED` 전이 + OCI Object
   Storage 위치.
8. **multi-role escalation UX** — Discord embed 의 `[기술]` / `[운영]` /
   `[escalate]` 시각 분리.
9. **plane 경계 secret 정책** — control plane 에 두는 최소 secret 목록
   (Discord token / GitHub App private key) vs 절대 안 두는 secret 목록
   (사용자 GitHub PAT, prod DB credential).
10. **OCI 구체 배치 IaC** — Terraform / oci-cli 의 reproducible 설정
    문서화 (수동 클릭 금지 hard rail).

# 적용 맥락

## 6) 어떤 기준이 바뀌면 결정을 다시 봐야 하는가

- **Discord 이벤트 받기가 사용자 PC 만으로도 충분해진다면** (예: 사용자가
  서버급 머신을 always-on 으로 운영) → 최소비용형도 비-OCI 로 옮겨갈 수 있음.
- **secret 정책이 control plane 노출을 허용하지 않는 방향으로 강화되면** →
  D-H-7 의 일부 컴포넌트가 로컬로 복귀.
- **다인 사용 / 외부 사용자 도입** → 최소비용형 → 표준형 → 확장형 진행이
  강제됨.
- **OCI Always Free 정책 변경** — Oracle 의 free tier 한도가 줄어들면 D-H-1
  의 OCI 우선 결정 비용 가정이 깨짐. 그때는 paid tier 전환 vs 다른 공급자
  검토.

## 7) `engineering-company-runtime-master-plan` vs `engineering-role-council-runtime` 의 역할 분리

- **`engineering-company-runtime-master-plan.md`** — *시스템 전체* 의 5
  레이어 (surface / coordination / intelligence / execution / memory), 4 루프
  (background knowledge / discussion / execution / improvement), gateway vs
  tech-lead 경계, CI/CD 분리, post-test hardening. **하이브리드의 plane 분리
  와 1:1 대응되는 게 이 문서**.
- **`engineering-role-council-runtime.md`** — *engineering-agent 부서 안*
  의 role × seat (owner/challenger/reviewer), peer review, tech-lead signoff,
  ApprovalPacket, execution_review, retrospective candidate. **본 부서 안의
  의사결정 흐름**.

본 결정의 hybrid 분리는 master plan 의 5 레이어를 *호스트 단위로* 재구성한
것이고, council runtime 은 그 안에서 *어느 plane 에 둘지와 무관한 부서 의사
결정 vocabulary* 다 — 두 문서는 직교한다.

# 관련 노트

- [[research-hybrid-deployment-control-execution-memory-plane]] — 본 결정의
  근거 자료.
- [[task-log-hybrid-deployment-plane-split]] — 본 결정에 도달한 정리 작업.
- [[decision-tech-lead-runtime-loop-issue-73]] — execution plane hard rail
  의 SSoT.
- [[decision-integration-polish-issue-81]] — 3 plane cross-axis 통합 검증.
- [[decision-ecc-foundation]] — control plane 의 markdown spec layer.
- [[decision-engineering-agent-authoring-policy-issue-69]] — 3-mode write
  ownership.

# 후속 decision 필요

- [ ] OCI IaC (Terraform / oci-cli) 구체 결정.
- [ ] secret 분리 정책의 코드 enforcement 위치.
- [ ] vault remote push 의 control plane 자동화 시점 (현재 mode 결정에 따라
      L2/L3 — autonomy-policy.md §0.1 참조).
- [ ] retrospective candidate 자동 stamp 의 trigger (C6).
- [ ] live LLM runner wiring 시 provider × seat 의 실 ramp-up (C5).
- [ ] BLOCKED signoff → 재signoff 의 vocabulary.
- [ ] `ApprovalPacket.ARCHIVED` 전이 producer + OCI Object Storage 보관.
- [ ] multi-user 도입 시 control plane 인증 layer (확장형).

# 참고

- [[research-hybrid-deployment-control-execution-memory-plane]] §1-§7
- [`docs/engineering-company-runtime-master-plan.md`](../../../../../docs/engineering-company-runtime-master-plan.md)
  §4-§5, §10-§11, §16 시리즈
- [`docs/engineering-role-council-runtime.md`](../../../../../docs/engineering-role-council-runtime.md)
  §3-§6
- [`docs/approval-matrix.md`](../../../../../docs/approval-matrix.md)
  §2-§3
- [`docs/autonomy-policy.md`](../../../../../docs/autonomy-policy.md)
  §0.1
- [`docs/engineering-agent-governance.md`](../../../../../docs/engineering-agent-governance.md)
  §4.1
- Oracle Cloud Infrastructure Always Free 공식 문서: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
