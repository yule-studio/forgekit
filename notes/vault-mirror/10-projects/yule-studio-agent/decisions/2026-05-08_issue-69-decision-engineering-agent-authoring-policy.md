---
title: "engineering-agent governance — 14 결정 (D-69-1 ~ D-69-14)"
kind: decision
issue: 69
parent_issue: 20
session_id: issue-69-governance
project: yule-studio-agent
created_at: 2026-05-08T00:00:00+09:00
status: decided
sources_inputs:
  - "issue #25 (PR #68): ECC 4-layer + research-first + tech-lead orchestration"
  - "issue #48 (PR #66): Harness 6 패턴 + tech-lead 단일 write 주체"
  - "issue #59 (PR #67): Hermes memory/recall/compression/self-improvement/scheduled-automation"
tags: [decision, governance, integration, engineering-agent]
---

# 목표

#25 / #48 / #59 의 결과물을 통합하면서 발생한 *충돌·갭* 을 해소할 14 개 결정을 단일 노트로 고정한다. 각 결정은 본 통합이 land 한 *정책 어디에 어떻게* 박혔는지 위치를 명시한다. raw 분석은 [[2026-05-08_issue-69-research-engineering-agent-governance-synthesis]].

# 결정

## A. Write ownership (3-mode)

| ID | 결정 | 위치 |
| --- | --- | --- |
| **D-69-1** | engineering-agent 의 write surface 는 **3-mode** 로 분리: `role-owned` / `tech-lead-mediated` / `gateway-mediated`. 모드별 trigger 조건은 정책 표로 명시. | `policies/.../engineering-agent/write-ownership.md` §1 |
| **D-69-2** | role-owned write 의 acceptable surface = (a) issue comment 에서 자기 deliverable 의 진행 보고, (b) 자기 작업 PR 의 commit author, (c) 자기 노트 author subject. PR body 의 final author 는 *통합 commit 의 owner role* 또는 tech-lead. | `write-ownership.md` §2 |
| **D-69-3** | tech-lead-mediated write 의 trigger = (a) 다역할 합의 / 충돌 조율, (b) 부서 차원 결정, (c) 외부 회신 통합. | `write-ownership.md` §3 |
| **D-69-4** | gateway-mediated write 의 trigger = (a) 부서 intake 응답, (b) supervisor / 운영 status, (c) 외부 사용자 notification. | `write-ownership.md` §4 |
| **D-69-5** | mode 선택은 deterministic — 정책 표에 명시한 trigger 조건이 작업 분류를 결정한다. ad-hoc 결정 금지. | `write-ownership.md` §5 |

#48 의 "tech-lead 단일 write 주체" 정책은 *gateway-mediated + tech-lead-mediated* 로 분해해 흡수. role-owned write 는 별도 첫 번째 mode 로 신설.

## B. Obsidian governance

| ID | 결정 | 위치 |
| --- | --- | --- |
| **D-69-6** | 모든 새 노트는 `## 관련 문서` 섹션 + wikilink 4 항목 이상 강제. | `policies/.../engineering-agent/obsidian-governance.md` §3 |
| **D-69-7** | 신규 노트 naming = `YYYY-MM-DD_issue-<n>-{research,decision,task-log,report}-<slug>.md`. 기존 노트는 rename 금지. | `obsidian-governance.md` §2 |
| **D-69-8** | wikilink 의 target basename 은 실제 파일 basename 과 정확히 일치. mirror 노트가 repo 안에 있으면 `notes/vault-mirror/...` 경로의 basename 을 사용. 사용자 vault 외부 노트는 사용자가 합의한 basename 을 그대로 인용. | `obsidian-governance.md` §4 |
| **D-69-9** | research / decision / task-log / report 노트는 같은 `issue-<n>` 안에서 *반드시* 서로 wikilink 로 연결. 외부 입력 (선행 issue 의 노트) 도 `## 관련 문서` 에 포함. | `obsidian-governance.md` §5 |

## C. GitHub Issue / PR / Label / progress

| ID | 결정 | 위치 |
| --- | --- | --- |
| **D-69-10** | issue body = `.github/ISSUE_TEMPLATE/-feature--issue-template.md` 4 섹션 (`어떤 기능인가요?` / `작업 상세 내용` / `참고할만한 자료(선택)`) 엄격 준수 + sub-issue 면 본문 1 줄에 `Parent: #N` 명시. | `policies/.../engineering-agent/github-workflow.md` §1 |
| **D-69-11** | PR body = `.github/PULL_REQUEST_TEMPLATE` 4 섹션 (`📌 관련 이슈` / `✨ 과제 내용` / `:camera_with_flash: 스크린샷(선택)` / `📚 레퍼런스`) + 그 뒤 `🤖 Agent WorkOS Audit` 블록 자동 append. PR title = `<gitmoji> <요약> (#<issue>)`. | `github-workflow.md` §2 |
| **D-69-12** | Label = repo 의 실재 label 만 부착. `🎯 Core` / `🏗 Infrastructure` 등 미생성 label 은 추천만. 라벨 부착 이유는 issue comment 에 1 행씩 명시. | `github-workflow.md` §3 |
| **D-69-13** | Progress comment 형식 = (1) 이번 라운드 목표 / (2) 변경 파일 표 (path / 변경 종류 / 사유) / (3) 테스트·검증 결과 / (4) Obsidian 노트 경로 / (5) 다음 액션. 5 섹션 모두 필수. | `github-workflow.md` §4 |

## D. 커밋 / push / GitHub Apps

| ID | 결정 | 위치 |
| --- | --- | --- |
| **D-69-14** | 커밋 분할 = 최소 3 개, 권장 5 개의 논리 단위. COMMIT_CONVENTION 의 한국어 3-section format 엄격 준수. PR push 는 GitHub Apps 우선, 불가능 시 `git push origin <current-branch>`. force push / protected branch / merge / production deploy 영구 금지. | `github-workflow.md` §5 + `policies/runtime/agents/engineering-agent/governance.md` §6 |

# 통합 시 입력 매핑

각 결정의 *입력 출처* (어느 선행 issue/PR 가 그 결정의 근거가 됐는지):

| 결정 ID | 입력 #25 | 입력 #48 | 입력 #59 |
| --- | --- | --- | --- |
| D-69-1 ~ D-69-5 (3-mode write) | tech-lead orchestration 보강 (정책 §4) | tech-lead 단일 write 주체 정책 → 분해 흡수 | (직접 입력 없음 — 부서 contract pluralism 패턴만 차용) |
| D-69-6 ~ D-69-9 (Obsidian) | ECC vault mirror 3 노트 (실재 backlink 대상) | (사용자 vault 외부 노트 인용 패턴) | (사용자 vault 외부 노트 인용 패턴) |
| D-69-10 ~ D-69-13 (GitHub) | smoke-pr PR template 준수 (`a19b718` fix 결과 차용) | 정책 회귀 test 패턴 차용 | (직접 입력 없음) |
| D-69-14 (커밋/push) | (직접 입력 없음 — 본 통합 작업의 자체 결정) | (직접 입력 없음) | (직접 입력 없음) |

# 보류 / 후속 결정

| 항목 | 결정 | 후속 |
| --- | --- | --- |
| 사용자 vault 의 #48/#59 노트 backlink 자동 추가 | 보류 | 사용자 직접 vault 편집 |
| `🎯 Core` / `🏗 Infrastructure` label GitHub 신설 | 보류 | 운영자 결정 시 신설 |
| 정책 markdown loader / dispatcher | 비범위 | 후속 PR (#48 e2e harness 후속과 합쳐서) |
| autonomy_policy 와 governance 의 cross-link | 비범위 | M10d 후속 |

# 검증 / 회귀

본 통합 정책의 회귀 보호:

- `tests/engineering/test_engineering_agent_governance_doc.py` — #48 의 `test_team_architecture_patterns_doc.py` 와 동일 lint-style 패턴.
- 검사 항목: 4 정책 문서 모두 존재 + 필수 섹션 헤더 / 모드 ID / role 누락 / wikilink 형식 / sub-issue parent 패턴 검출.
- 정책 변경 시 test fail 로 운영자 인지.

# 왜 회사형 시니어 팀 운영에 필요한가

본 14 결정이 land 되면 부서 안의 *write 인지부조화* 가 사라진다 — "이 PR body 누가 써?" / "이 노트 author 가 누구야?" / "이 commit 작성자가 어떻게 책임을 지나?" 같은 질문이 매트릭스 한 번 조회로 끝난다. 시니어 팀이 새 역할 / 새 부서를 추가할 때 정책 매트릭스에 행 1 개만 추가하면 되며, 기존 코드 / lifecycle / autonomy_policy 는 변경되지 않는다.

## 관련 문서

- [[CLAUDE]]
- [[2026-05-08_issue-69-research-engineering-agent-governance-synthesis]]
- [[2026-05-08_issue-69-task-log-governance-integration]]
- [[2026-05-08_research_ecc-foundation]]
- [[2026-05-08_decision_ecc-foundation]]
- [[2026-05-08_task-log_25-ecc]]
- [[2026-05-08_research-harness-team-patterns]]
- [[2026-05-08_decision-tech-lead-single-write-subject]]
- [[2026-05-08_task-log-issue-48-harness]]
- [[2026-05-08_hermes-agent-architecture-deep-dive]]
- [[2026-05-08_hermes-yule-integration-decisions]]
- [[2026-05-08_59-hermes-tech-lead]]
