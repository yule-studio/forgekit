---
title: "하이브리드 배포 plane 분리 정리 — 작업 로그"
kind: task-log
status: in-progress
created_at: 2026-05-31T00:00:00+09:00
tags: [task-log, hybrid-deployment, c4, vault-curation]
related:
  - "[[research-hybrid-deployment-control-execution-memory-plane]]"
  - "[[decision-hybrid-deployment-plane-split]]"
  - "[[decision-tech-lead-runtime-loop-issue-73]]"
  - "[[decision-integration-polish-issue-81]]"
  - "[[decision-ecc-foundation]]"
home_hub: "[[decision-hybrid-deployment-plane-split]]"
---

# 핵심 요약

C1~C4 까지 land 한 council runtime 위에서 "왜 시스템을 로컬과 퍼블릭 클라우
드로 *분리 가능* 하게 짜고 있는지" 를 미래의 나도 다시 알 수 있게 정리한
작업. 코드 변경은 cross-link 1 개만 보탰고, 핵심 산출물은 vault triad 3 개:
[[research-hybrid-deployment-control-execution-memory-plane]] /
[[decision-hybrid-deployment-plane-split]] / 본 노트.

# 내 해석

## 1) 무엇을 읽었나

순서대로 읽고 본 결정에 반영했다.

| 순서 | 파일 | 본 작업에 미친 영향 |
|---|---|---|
| 1 | `AGENTS.md` | 문서 계층 + 작업 맥락 매핑. *문서 지도* 의 첫 행이 항상 이 파일이라는 결정 |
| 2 | `CLAUDE.md` (전역 hard rail) | secret / deploy / merge / protected branch hard rail. **plane 분리해도 동일 정책 적용** 결정의 근거 |
| 3 | `docs/memory.md` (curated 규칙) | frontmatter 7 키 / 본문 5 섹션 / home_hub + related 의 1+ 최소 강제. 본 triad 의 형식 결정 |
| 4 | `docs/engineering-agent-governance.md` | 3-mode write ownership + runtime governance hard rail. control plane 에서도 그대로 적용 |
| 5 | `docs/engineering-company-runtime-master-plan.md` §4-§5, §10-§11, §16 시리즈 | 시스템 5 레이어 / 4 루프 / CI/CD 분리 → **plane 분리와 1:1 대응** 인사이트 |
| 6 | `docs/engineering-role-council-runtime.md` §3-§6 | 부서 안 council 흐름. plane 분리와 직교 — 본 노트가 두 문서의 역할 분리를 명문화 |
| 7 | `docs/approval-matrix.md` §2-§3 + `docs/autonomy-policy.md` §0 | L0~L4 + work mode + scope + topology. control plane 자동·로컬 승인 경계 결정의 SSoT |
| 8 | `policies/runtime/agents/engineering-agent/lifecycle-mvp.md` / `team-conversation.md` / `review-loop.md` / `message-protocol.md` | council 어휘 (`tech_lead_signoff`, `peer_review`, `council_synthesis`, execution_review). plane 경계가 어떤 인터페이스로 좁혀져 있는지 확인 |
| 9 | `notes/vault-mirror/.../research-tech-lead-runtime-loop-issue-73.md` + decision + task-log | execution plane 의 4 단계 (coding executor / completion hook / decision layer / 검증) — execution plane 의 hard rail 위치 |
| 10 | `notes/vault-mirror/.../decision-integration-polish-issue-81.md` | 3 축 (gateway / autonomy-execution / knowledge-geeknews) 통합. 본 plane 분리 단위와 거의 일치 |
| 11 | `notes/vault-mirror/.../decision-ecc-foundation.md` | skills/hooks/commands markdown spec layer — control plane 의 외부 변경 layer 가 cloud-portable 한 이유 |
| 12 | `notes/vault-mirror/.../decision-engineering-agent-authoring-policy-issue-69.md` | 3-mode write ownership + Obsidian / GitHub governance — plane 분리 후에도 그대로 적용 |
| 13 | `src/yule_orchestrator/agents/council.py` + `council_bootstrap.py` + `council_approval.py` + `lifecycle/council_substage.py` + `lifecycle/council_status_signals.py` + `discord/engineering_channel_router/council_flow.py` | C1-C4 의 실제 코드 모양. **JSON-safe payload round-trip 이 plane 경계 인터페이스 그 자체** |
| 14 | OCI Always Free 공식 문서 | 무료 한도 — control plane 비용 0 결정의 근거. AMD micro 2 + A1.Flex 4 OCPU/24 GB + 200 GB block + 20 GB object |

## 2) 지금 구조를 왜 이렇게 해석했나

1. **C4 의 코드가 이미 plane-split 형 인터페이스로 land 되어 있다.**
   council state 가 모두 `session.extra` JSON, `gateway_surface_payload` 가
   문자열 두 줄로 분리, approval matrix 는 ownership 이 명시 — control plane
   만 외부 호스트로 옮겨도 코드 변경이 거의 없다.
2. **"분리 가능" 이라는 표현이 핵심.** 지금 한 머신에서 다 돌아가는 것이
   문제가 아니라, *옮기려 할 때 옮길 수 있는 구조* 가 이미 만들어져 있다는
   것이 본 결정의 가치다. 다음 단계 (C5 / C6) 가 그 옮김을 *실제로 시작*
   하는 단계라서 그 전에 이유를 굳혀 두는 것이 옳다.
3. **단일 profile 선택은 시스템 안의 가장 약한 제약을 모두에게 강제한다.**
   "최소비용형 으로 다 가자" 든, "확장형 으로 다 가자" 든 *컴포넌트마다*
   필요한 조건이 다른데 한 답을 강요하는 결정. 컴포넌트별 mix-and-match 가
   설계 의도와 일치.
4. **OCI Always Free 한도가 control plane 에 충분하다.** 공식 문서 기준
   E2.1.Micro 2 개 + A1.Flex 4 OCPU/24 GB + 200 GB block 로 Discord bot +
   GitHub App webhook + 가벼운 SQLite + research collector + retrieval
   indexer 까지 **0 USD** 로 always-on 확보 가능. 그래서 *최소비용형* 이
   default 가 된 결정 인자.
5. **로컬-only 와 cloud-only 사이 어느 한쪽으로 몰아가면 hard rail 이
   깨진다.** 로컬-only 면 Discord 이벤트 손실. cloud-only 면 secret blast
   radius 폭발 + Obsidian GUI 단절. 즉 hybrid 가 *유일하게 hard rail 을
   유지한 채 always-on 을 사는* 답이다.

## 3) 작업 흐름

| 시점 | 단계 | 산출 |
|---|---|---|
| 2026-05-31 kickoff | 필수 문서 14 종 + Always Free 한도 정리 | 본 task-log §1 |
| 2026-05-31 step-1 | 3 plane 정의 + 비교표 작성 | [[research-hybrid-deployment-control-execution-memory-plane]] §3 |
| 2026-05-31 step-2 | 3 profile mix 기준 작성 | research §5 + decision §3 |
| 2026-05-31 step-3 | C4 split-readiness 표 작성 | research §6 — 어디까지 옮길 준비 됐는지 |
| 2026-05-31 step-4 | 12 결정 (D-H-1 ~ D-H-12) 고정 | [[decision-hybrid-deployment-plane-split]] §2 |
| 2026-05-31 step-5 | 정리 필요 결정 10 개 + 후속 decision 8 항목 추출 | decision §5 + 후속 decision 필요 |
| 2026-05-31 step-6 | cross-link 1 개 추가 — master plan 의 본 vault note 가리킴 | `docs/engineering-company-runtime-master-plan.md` |

## 4) 앞으로 이어서 정리할 후속 질문

- [ ] OCI 위에서 Discord bot / GitHub App / SQLite 의 *구체 배치 IaC*. Terraform 또는 `oci-cli` 의 reproducible 설정.
- [ ] vault git mirror 의 control plane 자동 fetch 주기 + 충돌 처리.
- [ ] secret 분리: 어떤 secret 은 OCI Vault, 어떤 secret 은 로컬 keychain
      에만. 코드 enforcement 위치.
- [ ] `vault_remote_push` 의 control plane 자동화가 안전한 시점 (autonomy-
      policy.md §0.1 의 mode 결정과 연동).
- [ ] live LLM runner 가 들어왔을 때 provider × seat × availability 의
      실 ramp-up 전략 (C5).
- [ ] BLOCKED signoff resolution path 의 명시적 vocabulary.
- [ ] retrospective candidate 자동 stamp 의 trigger (C6).
- [ ] OCI 외 다른 클라우드 (AWS Free / Fly.io / Cloudflare Workers 등) 의
      Always-on 비교. 본 결정의 hard rail 이 바뀌어야 하는지.

## 5) 문서 지도 — 다음에 무엇을 읽을지

1. [[decision-hybrid-deployment-plane-split]] §2 — 12 결정 (D-H-1 ~ D-H-12).
2. [[research-hybrid-deployment-control-execution-memory-plane]] §3 — plane
   정의 + 비교표.
3. [[research-hybrid-deployment-control-execution-memory-plane]] §5 — 3
   profile mix.
4. [[research-hybrid-deployment-control-execution-memory-plane]] §6 — C4
   기준 split-readiness.
5. [[decision-hybrid-deployment-plane-split]] §4 — settled / provisional /
   deferred 구분.
6. [[decision-hybrid-deployment-plane-split]] §5 — 정리 필요 기술 결정 10
   개.
7. `docs/engineering-company-runtime-master-plan.md` §4-§5 — gateway vs
   tech-lead 경계 + 5 레이어.
8. `docs/engineering-role-council-runtime.md` §3-§6 — 부서 안 council 어휘
   (plane 분리와 직교).
9. `docs/approval-matrix.md` §2-§3 + `docs/autonomy-policy.md` §0 — 자동·
   승인 매트릭스 + work mode.

# 적용 맥락

- 본 노트는 C4 가 closed 된 시점의 *지식 정리* 다. C5 의 첫 PR (live LLM
  runner 또는 execution_review wiring) 이 들어오기 전에 본 결정 노트가
  hub 가 되어 plane 결정이 흐트러지지 않도록 한다.
- 코드 변경은 master plan 에 cross-link 1 줄만 추가. 그 외 모두 vault
  curation.

# 관련 노트

- [[research-hybrid-deployment-control-execution-memory-plane]]
- [[decision-hybrid-deployment-plane-split]]
- [[decision-tech-lead-runtime-loop-issue-73]]
- [[decision-integration-polish-issue-81]]
- [[decision-ecc-foundation]]
- [[decision-engineering-agent-authoring-policy-issue-69]]

# 참고

- `docs/memory.md` — curated note 규칙 (7 키 frontmatter / 5 섹션 본문).
- `docs/engineering-agent-governance.md` §4.1 — runtime governance hard
  rail.
- `docs/engineering-company-runtime-master-plan.md` §4-§5, §10-§16 시리즈.
- `docs/engineering-role-council-runtime.md` §3-§6.
- Oracle Cloud Always Free 공식 문서: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
