---
title: "engineering-agent governance — #25/#48/#59 결과물 통합 분석"
kind: research
issue: 69
parent_issue: 20
session_id: issue-69-governance
project: yule-studio-agent
created_at: 2026-05-08T00:00:00+09:00
sources:
  - https://github.com/yule-studio/yule-studio-agent/issues/25
  - https://github.com/yule-studio/yule-studio-agent/issues/48
  - https://github.com/yule-studio/yule-studio-agent/issues/59
  - https://github.com/yule-studio/yule-studio-agent/issues/20
  - https://github.com/yule-studio/yule-studio-agent/pull/66
  - https://github.com/yule-studio/yule-studio-agent/pull/67
  - https://github.com/yule-studio/yule-studio-agent/pull/68
tags: [research, governance, integration, engineering-agent]
---

# 목표

완료된 #25 (everything-claude-code) / #48 (Harness) / #59 (Hermes) 의 산출물을 *통합 입력* 으로 받아 `engineering-agent` 부서 전체가 따를 공통 GitHub / Obsidian 운영 규칙으로 정착시킨다. 본 노트는 그 통합 결정의 *raw 분석* 이며, 결정 자체는 [[decision-engineering-agent-authoring-policy-issue-69]] 에서 책임진다.

# 현재 Yule 기준선

- Engineering department 7 역할 (tech-lead / backend / frontend / devops / qa / ai-engineer / product-designer) + gateway. 기존 `team-structure.md` 가 정의.
- Lifecycle MVP 13 단계 + session.extra persistence — `policies/runtime/agents/engineering-agent/lifecycle-mvp.md`.
- Obsidian export contract v0 — `obsidian-memory.md` (kind: research / decision / task-log / meeting / report / references / knowledge).
- GitHub WorkOS G1~G6 — `docs/github-agent-workos.md` (App config / triage / executor / Discord bridge / e2e harness / CLI).
- Label policy — `policies/runtime/agents/planning-agent/github-label-policy.md`.
- COMMIT_CONVENTION + BRANCH_STRATEGY — `policies/reference/`.

이미 강제되는 것: lifecycle 13 단계, agent.json contract-v1, autonomy_policy L0~L4, agent_ops_audit, secret redaction, protected branch 거부.

비어 있는 것: **부서 차원에서 "누가 어디에 무엇을 쓰는가" 의 통합 매트릭스.** 각 후속 PR (#25/#48/#59) 이 부분적으로 채웠지만 한 곳에 모인 결론이 없다.

# 입력 1 — #25 ECC foundation (PR #68)

핵심 land:

- `policies/runtime/agents/engineering-agent/ecc-foundation.md` — 4 layer (skills / hooks / commands / agents) 정의 + research-first 게이트 + tech-lead orchestration 보강 + Appendix A.
- `agents/engineering-agent/skills/{README, research-collect}.md`
- `agents/engineering-agent/hooks/{README, research-first-gate}.md`
- `agents/engineering-agent/commands/{README, engineer-show}.md`
- `docs/agent-company-ecc.md` — 운영자용 비교 매트릭스 (영역 × 도입 여부 × 이유 × 위치 × 리스크 7 컬럼).

본 통합에 흡수할 결론:

1. 외부 변경 layer 는 markdown frontmatter 단위. dispatcher 자동화는 후속.
2. tech-lead = 모든 GitHub WorkOS write 의 단일 actor (정책 §4 명시).
3. research-first 게이트 = `compute_lifecycle_status` 가 enforcement, 정책은 표로 격상.
4. ECC 의 GateGuard 류 강성 hook 은 후속 PR (autonomy_policy 새 action_id 와 합쳐 도입).

선행 노트 (실제 vault mirror):
- [[research-ecc-foundation]] (`notes/vault-mirror/.../research/`)
- [[decision-ecc-foundation]] (`notes/vault-mirror/.../decisions/`)
- [[task-log-25-ecc]] (`notes/vault-mirror/.../task-logs/`)

# 입력 2 — #48 Harness (PR #66)

핵심 land:

- `policies/runtime/agents/engineering-agent/team-architecture-patterns.md` — Harness 의 6 팀 운영 패턴을 Yule 의 7 역할 / 13 lifecycle 에 매핑.
- `tests/engineering/test_team_architecture_patterns_doc.py` — 정책 문서 회귀 test (lint-style: 필수 섹션 / 패턴 ID 존재 / role 누락 검출).

본 통합에 흡수할 결론:

1. 부서 차원의 *write 단일 주체 정책* — tech-lead 가 부서 외부 회신을 대표하지만, 내부 deliverable 의 author 는 각 role 이 owner.
2. **정책 문서에 회귀 테스트** 가 가능 — 문서가 코드 옆에 있고 lint-style 검증을 받는다. 본 통합도 같은 패턴 답습.
3. team-architecture-patterns.md 의 6 패턴은 본 governance 의 *설명 모델* 로 재사용 (인용만, 재정의 X).

선행 노트 (사용자 vault 에 존재 — 본 repo 에는 없음):
- [[2026-05-08_research-harness-team-patterns]]
- [[2026-05-08_decision-tech-lead-single-write-subject]]
- [[2026-05-08_task-log-issue-48-harness]]

# 입력 3 — #59 Hermes (PR #67)

핵심 land:

- `policies/runtime/agents/engineering-agent/memory-policy.md`
- `policies/runtime/agents/engineering-agent/recall-policy.md`
- `policies/runtime/agents/engineering-agent/context-compression.md`
- `policies/runtime/agents/engineering-agent/self-improvement-flow.md`
- `policies/runtime/agents/engineering-agent/scheduled-automation.md`
- `agents/engineering-agent/agent.json` — 5 정책을 부서 contract 로 등록.

본 통합에 흡수할 결론:

1. 부서 단위 *contract pluralism* — agent.json 에 다수 정책을 등록하는 패턴이 정착. 본 통합도 같은 자리에 새 정책을 등록.
2. self-improvement / recall / scheduled-automation 은 *부서 단위* 로만 정의. 개별 role 마다 별도 정의하지 않음 — 본 governance 도 동일 원칙.
3. context-compression 은 retrieval / memory 의 일부. write surface 정의는 별도 영역 — 본 governance 가 책임.

선행 노트 (사용자 vault 에 존재):
- [[2026-05-08_hermes-agent-architecture-deep-dive]]
- [[2026-05-08_hermes-yule-integration-decisions]]
- [[2026-05-08_59-hermes-tech-lead]]

# 통합 시 충돌 / 갭 분석

## C1. tech-lead 단일 write vs. role-owned write

**#25** 는 "모든 GitHub WorkOS write 의 actor 라벨 = tech-lead" 라고 강하게 명시. **#48** 는 "tech-lead 단일 write 주체" 정책을 land. **#59** 는 별도 write 주체 정의 없음.

문제: 너무 강성. backend 가 자기 API 변경을 PR body 에 자기 voice 로 적는 것을 차단할 이유가 없다. 시니어 팀에서는 **owner = 그 deliverable 을 만든 role**, **mediator = tech-lead** 가 자연.

해결 (본 통합):
- 3-mode 분리 — `role-owned` / `tech-lead-mediated` / `gateway-mediated`.
- 각 mode 의 trigger 조건을 명시 (어떤 write 가 어디에 떨어지는가).

## C2. Obsidian wikilink 관련 문서 강제 vs. 기존 노트 무수정 원칙

ECC foundation 노트는 자체 cross-link 만 갖고, 사용자 vault 의 #48/#59 노트와 backlink 가 없다. 사용자 요구: "기존 노트명을 바꾸지 말고, 새 통합 note 에서 링크를 걸고 필요 시 기존 note 에도 backlink 추가".

해결:
- 본 통합 노트는 #25 vault mirror 3 노트 (실재) + #48/#59 vault 노트 (사용자 vault) 를 모두 wikilink.
- repo 에 mirror 가 있는 #25 의 3 노트만 backlink 를 코드로 추가 — `## 관련 문서` 섹션에 본 통합 노트 추가.
- #48/#59 의 vault 노트는 repo 에 없으므로 backlink 코드 변경 불가. progress comment 에 "사용자 vault 측 backlink 는 운영자 직접 추가 권장" 으로 surface.

## C3. Label 정책 — 실재 label 만 사용 vs. 추천 label

repo 의 실재 label 은 12 종 (`✨ Feature`, `📃 Docs`, `✅ Test`, `🔨 Refactor`, `🐞 BugFix`, `⚙ Setting`, `🌏 Deploy`, `📬 API`, `🥰 Accessibility`, `🙋‍♂️ Question`, `💻 CrossBrowsing`, `🎨 Html&css`).

planning-agent 의 label policy 는 **5 종 추천 label** (`🏗 Infrastructure`, `📦 Domain`, `🗄 Schema`, `🎯 Core`, `🔐 Auth`) 을 명시했지만 GitHub 에 미생성.

해결 (본 통합 정책):
- 부착 = 실재 label 만.
- 추천 = comment 의 별도 섹션.
- 새 label 자동 생성 금지. 라벨 신설은 운영자 결정.

## C4. PR body 의 PR template vs. issue template 혼용

#66 (#48) / #67 (#59) 의 PR body 가 issue template 형태로 작성된 흔적 (사용자 spec 에서 명시 — "현재 #66, #67처럼 issue template 형태가 PR body에 섞였는지 확인하고, 이번 통합 PR에서는 반드시 PR template 형식으로 맞춰라").

해결:
- 본 통합 PR body 는 `.github/PULL_REQUEST_TEMPLATE` 4 섹션 (`📌 관련 이슈` / `✨ 과제 내용` / `:camera_with_flash: 스크린샷` / `📚 레퍼런스`) 엄격 준수.
- PR template fix (`a19b718`) 의 결과를 그대로 사용 (이미 G6 smoke-pr 에서 검증).

## C5. 커밋 분할 — 한 commit 에 모두 vs. 논리 단위 분할

#68 (ecc) 는 1 commit, #66 (harness) 는 1 commit, #67 (hermes) 는 정책 5 종을 한 묶음으로 commit. 본 통합 작업은 *명시적으로* 논리 단위 분할 요구.

해결:
- 본 PR 은 5 개 commit 으로 분할:
  1. 선행 #25/#48/#59 산출물 분석 (Obsidian research + decision 노트)
  2. Obsidian governance (wikilink / naming / 관련 문서 정책)
  3. write ownership (3-mode authoring + surface 매트릭스)
  4. GitHub workflow (issue / PR / label / progress 규칙 + umbrella doc)
  5. 정책 문서 회귀 test

# 도입 / 보류 결정 요약

[[decision-engineering-agent-authoring-policy-issue-69]] 가 14 개 결정 (D-69-1 ~ D-69-14) 을 정리. 본 노트는 *그 결정의 입력 분석* 만 책임.

핵심 하이라이트:

- ✅ 3-mode write ownership 도입 (role-owned / tech-lead-mediated / gateway-mediated)
- ✅ Obsidian `## 관련 문서` 섹션 강제 + naming 컨벤션
- ✅ PR body 4 섹션 + Audit append 강제
- ✅ Label = 실재 label only, 추천은 comment
- ✅ 커밋 ≥3 분할 강제
- ✅ 정책 문서 회귀 test (#48 패턴 답습)
- ⏳ 사용자 vault backlink 자동 추가 — 본 repo 외부라 수동
- ❌ 기존 노트 rename — 금지

# 왜 시니어 개발팀형 회사 구현에 필요한가

본 governance 가 land 되면:

1. **부서 차원 인지부조화 제거** — "누가 작성하는가" 의 단일 매트릭스가 7 역할 + gateway 에 일관 적용.
2. **새 역할 추가 비용** = 매트릭스에 1 행 추가. 기존 정책 변경 없음.
3. **외부 protocol** (Discord / GitHub Apps / Obsidian) 이 같은 정책을 인용 → 운영자가 한 곳만 본다.
4. **진단 가능성** — 정책 문서 자체가 회귀 test 를 갖는다 (#48 패턴 답습). 정책 위반 = test fail.

# 구현 위치

| 산출물 | 위치 |
| --- | --- |
| 정책 본문 (umbrella) | `policies/runtime/agents/engineering-agent/governance.md` |
| Obsidian governance 정책 | `policies/runtime/agents/engineering-agent/obsidian-governance.md` |
| Write ownership 정책 | `policies/runtime/agents/engineering-agent/write-ownership.md` |
| GitHub workflow 정책 | `policies/runtime/agents/engineering-agent/github-workflow.md` |
| 운영자 통합 가이드 | `docs/engineering-agent-governance.md` |
| 정책 회귀 test | `tests/engineering/test_engineering_agent_governance_doc.py` |
| Obsidian mirror notes | `notes/vault-mirror/10-projects/yule-studio-agent/{research,decisions,task-logs}/2026-05-08_issue-69-*.md` |

# 리스크 + 다음 액션

리스크:
- 사용자 vault 의 #48/#59 노트 backlink 추가 = 자동 불가 (외부 vault). 사용자 직접 액션 필요.
- 정책 markdown 인플레이션 — `yule memory reindex` 가 모두 SOURCE_POLICY 로 픽업. 본 PR 은 4 markdown 신규로 제한.
- #20 의 다른 sub-issue (#29 PM agents 등) 와 정책 충돌 — 본 governance 는 engineering-agent 만, planning-agent 와 cross-cut 없음.

다음 액션 (본 세션):
1. decision note 작성.
2. 4 정책 문서 작성 (umbrella + Obsidian + write-ownership + github-workflow).
3. 운영자 통합 docs 작성.
4. 정책 회귀 test 작성.
5. ECC vault mirror 3 노트에 본 통합 노트 backlink 추가.
6. 5 개 commit 으로 분할 + push + draft PR.

## 관련 문서

- [[CLAUDE]]
- [[research-ecc-foundation]]
- [[decision-ecc-foundation]]
- [[task-log-25-ecc]]
- [[2026-05-08_research-harness-team-patterns]]
- [[2026-05-08_decision-tech-lead-single-write-subject]]
- [[2026-05-08_task-log-issue-48-harness]]
- [[2026-05-08_hermes-agent-architecture-deep-dive]]
- [[2026-05-08_hermes-yule-integration-decisions]]
- [[2026-05-08_59-hermes-tech-lead]]
- [[decision-engineering-agent-authoring-policy-issue-69]]
- [[task-log-governance-integration-issue-69]]
