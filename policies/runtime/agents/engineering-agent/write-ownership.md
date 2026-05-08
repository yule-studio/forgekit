# Write Ownership — engineering-agent 3-mode authoring policy (Issue #69)

> **소유:** 본 정책은 `engineering-agent` 부서 전체에 적용된다. 7 역할 (`tech-lead` / `backend-engineer` / `frontend-engineer` / `devops-engineer` / `qa-engineer` / `ai-engineer` / `product-designer`) 과 `gateway` surface 가 동일하게 따른다.
> **목적:** "누가 어디에 무엇을 쓰는가" 를 부서 한 매트릭스로 deterministic 하게 결정한다.
> **출처:** Issue #69 D-69-1 ~ D-69-5. raw 분석은 `notes/vault-mirror/10-projects/yule-studio-agent/research/2026-05-08_issue-69-research-engineering-agent-governance-synthesis.md`.

본 정책은 #48 의 "tech-lead 단일 write 주체" 정책 (선행 PR #66) 을 *3-mode 분해* 형태로 흡수한다. tech-lead 만 author 인 강성 정책은 부서 운영의 자연스러움을 떨어뜨려, 본 정책에서 **role-owned** 모드를 첫 시민으로 신설하고 **tech-lead-mediated** 와 **gateway-mediated** 를 trigger 조건과 함께 분리한다.

## 1. 3-mode 정의

| mode | 의미 | 대표 surface |
| --- | --- | --- |
| **`role-owned`** | 특정 역할이 자기 deliverable 의 owner 로 직접 author 가 되는 write. | role 자기 PR 의 commit author / 자기 take 의 issue comment / 자기 노트 author subject |
| **`tech-lead-mediated`** | 다역할 합의·조율·통합·충돌 해결이 필요한 write. tech-lead 가 author subject 가 되며 다른 역할의 의견은 *분석 입력* 으로 인용. | 다역할 PR 의 body author / 합의 결정 노트 / cross-role conflict resolution |
| **`gateway-mediated`** | 부서 차원의 intake / status / 외부 회신 / 운영 supervisor write. gateway 가 author subject. | `#업무-접수` 응답 / `#봇-상태` 게시 / `/engineer_show` status / runtime 알림 |

세 mode 는 **상호 배타적** 이다. 한 write 는 정확히 한 mode 에 속한다. 모드 결정은 deterministic — §5 의 trigger 표를 따른다.

## 2. role-owned write — surface 매트릭스

| 역할 | acceptable surface (role-owned) | 비고 |
| --- | --- | --- |
| `tech-lead` | tech-lead 의 정책 PR / contract 변경 / synthesis note | tech-lead 도 자기 deliverable 의 owner 일 때는 role-owned. 다역할 mediation 일 때만 mediated. |
| `backend-engineer` | API / DB / 도메인 변경 PR / 자기 PR body author / 자기 변경 commit author / 자기 take note | API contract 의 cross-role 영향이 발견되면 mediated 로 escalate. |
| `frontend-engineer` | UI / state / a11y / 컴포넌트 PR / 자기 PR body / 자기 take note | 디자인 결정 충돌 시 mediated. |
| `devops-engineer` | infra / CI / runtime / 배포 PR / 자기 PR body / runtime 변경 commit | secret 변경은 별도 L4 승인 — role-owned 도 자동 진행 금지. |
| `qa-engineer` | 회귀 / acceptance / smoke test PR / 자기 PR body / qa 보고 note | qa 가 detect 한 cross-role 회귀는 mediated. |
| `ai-engineer` | RAG / runner / agent eval / model prompt 정책 PR / 자기 PR body | LLM runner contract 의 cross-cut 영향은 mediated. |
| `product-designer` | UX copy / 디자인 토큰 / 사용자 흐름 문서 PR / 자기 PR body | UI 비용·MVP 범위의 cross-role 충돌은 mediated. |

role-owned 의 author subject 라벨:

- commit author / committer email = `<role>[bot]@yule-studio.local` (또는 운영자 결정에 따른 GitHub App identity).
- PR / issue comment 의 첫 줄에 `> **author: engineering-agent/<role>**` 명시.
- Obsidian 노트 frontmatter `author_role: <role>`.

## 3. tech-lead-mediated write — trigger 조건

다음 중 **하나 이상** 만족 시 자동 escalate.

| trigger | 예 |
| --- | --- |
| 다역할 합의 / 결정 | tech-lead synthesis, role-take aggregation |
| 다역할 충돌 / 우선순위 결정 | "구현 vs 보류" / "수정 vs 위험" 결정 |
| 부서 차원 결정 (계약·정책·우선순위) | 본 governance.md 본문, ecc-foundation, team-architecture-patterns |
| 외부 부서 / 외부 사용자에 대한 회신 (단일 role 의 voice 가 부적절) | 사용자 클레임 회신, 다른 부서 cross-cut 응답 |
| cross-role 회귀 / 영향 평가 | qa 의 회귀 보고가 backend + frontend 모두에 영향 |

mediated write 의 author subject 라벨:

- commit / PR body / issue comment 의 author = `engineering-agent/tech-lead`.
- 다른 역할의 의견은 본문 **`## 역할별 검토 (분석 입력)`** 또는 **`## 역할 take`** 섹션으로 인용. write actor 는 tech-lead.
- Obsidian 노트 frontmatter `author_role: tech-lead`, `analysis_inputs: [<role list>]`.

## 4. gateway-mediated write — trigger 조건

| trigger | 예 |
| --- | --- |
| 부서 intake 응답 (`#업무-접수`) | 작업 분류 / role-selection 결과 / 다음 단계 안내 |
| supervisor / runtime status (`#봇-상태`) | heartbeat / circuit / failed_terminal 알림 |
| 외부 사용자 notification (Discord 멘션 등) | typing indicator / 진행 메시지 |
| `/engineer_show` 등 status diagnostic 응답 | 세션 상태 일괄 응답 |
| kickoff / closure of issue (이번 본 governance kickoff comment 가 예) | 부서 작업 시작 / 종료 선언 |

gateway-mediated write 의 author subject 라벨:

- author = `engineering-agent/gateway`.
- 본문에 부서 차원 sign — "본 응답은 gateway 가 부서 대표로 작성".

## 5. mode 결정 트리 — deterministic

```
write 발생 시점에서:

[Q1] 부서 intake / status / 외부 사용자 notification 인가?
        → Yes → gateway-mediated
        → No  → [Q2]

[Q2] 다역할 합의·충돌·조율·외부 회신·cross-role 영향 평가 인가?
        → Yes → tech-lead-mediated
        → No  → [Q3]

[Q3] 특정 역할의 자기 deliverable 인가?
        → Yes → role-owned (해당 역할이 author)
        → No  → tech-lead-mediated (default fallback)
```

ad-hoc 결정 금지. 위 순서를 따른다. tie-break 는 항상 *더 강한 mediation* 쪽 (gateway > tech-lead > role-owned 순서로 우선).

## 6. 질문 5 종 — 명시 답

본 정책의 핵심 질문에 대한 deterministic 답:

| Q | A |
| --- | --- |
| 누가 issue comment 를 직접 달 수 있는가? | role-owned trigger 면 그 역할 / mediated 면 tech-lead / gateway trigger 면 gateway. |
| 누가 PR body author 가 될 수 있는가? | 같은 결정 트리 (§5). PR 의 변경 범위가 다역할이면 tech-lead-mediated 가 default. |
| 누가 Obsidian 노트 author subject 가 될 수 있는가? | mode 결정에 따라. frontmatter `author_role` 명시. |
| 언제 tech-lead 가 대표가 되고 / 언제 개별 role 이 owner 가 되며 / 언제 gateway 가 대표가 되는가? | §3 / §2 / §4 의 trigger 표. |
| GitHub Apps workflow 에서 어떤 write 는 role-owned 로 허용되고 어떤 write 는 mediation 이 필요한가? | §5 결정 트리. role-owned write 의 commit author email 은 그 역할의 bot identity, mediated 는 tech-lead bot, gateway 는 gateway bot. |

## 7. 선행 정책과의 관계

- `team-structure.md` (기존) — 7 역할 / gateway / 책임 정의. 본 정책은 그 위에 *write 표면* 을 정의 (역할 자체는 변경 없음).
- `team-architecture-patterns.md` (#48) — Harness 6 패턴 매핑. 본 정책의 mediated write trigger 가 그 6 패턴의 *consensus* 단계와 일치.
- `ecc-foundation.md` (#25) §4 의 "tech-lead = 모든 write actor" 는 *gateway-mediated + tech-lead-mediated* 로 분해됨. role-owned 는 본 정책이 신설.
- `obsidian-governance.md` (본 PR) — Obsidian 노트의 frontmatter `author_role` 이 본 정책에 의해 결정.
- `github-workflow.md` (본 PR) — GitHub Issue / PR / commit 의 author 라벨이 본 정책 §5 결정 트리에 의해 결정.

## 8. 검증

`tests/engineering/test_engineering_agent_governance_doc.py` 가 본 정책의 핵심 항목을 lint-style 로 검증:

- 3 mode 정의 (`role-owned` / `tech-lead-mediated` / `gateway-mediated`) 모두 본문에 존재.
- 7 역할 모두 §2 surface 매트릭스에 등장.
- §5 결정 트리의 Q1 / Q2 / Q3 모두 본문에 존재.
- 정책 위반 시 test fail.

## 9. 변경 이력

| 일자 | 변경 |
| --- | --- |
| 2026-05-08 | 초안 (Issue #69 — D-69-1 ~ D-69-5 결정 반영. #48 의 단일 write 주체 정책을 3-mode 로 분해 흡수.) |
