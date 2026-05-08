# Obsidian Governance — engineering-agent 부서 공통 (Issue #69)

> **소유:** 본 정책은 `engineering-agent` 부서 전체에 적용된다. 7 역할 (`tech-lead` / `backend-engineer` / `frontend-engineer` / `devops-engineer` / `qa-engineer` / `ai-engineer` / `product-designer`) 과 `gateway` surface 가 동일하게 따른다.
> **목적:** 부서가 만드는 모든 Obsidian 노트가 *그래프* 로 이어지고, 외부 (GitHub issue / PR / Discord) 가 일관된 wikilink 로 인용 가능하도록 한다.
> **출처:** Issue #69 D-69-6 ~ D-69-9. raw 분석 / 결정 노트는 `notes/vault-mirror/10-projects/yule-studio-agent/{research,decisions}/2026-05-08_issue-69-*.md`.

본 정책은 `policies/runtime/agents/engineering-agent/obsidian-memory.md` (v0 contract) 의 *상위 운영 규칙* 이다. v0 contract 가 정의한 path / frontmatter 는 그대로 유지되며, 본 정책은 *어떻게 노트를 서로 연결할지* 만 추가한다.

## 1. 적용 범위

- 모든 신규 / 수정 노트 (research / decision / task-log / report / reference / meeting / knowledge).
- 모든 7 역할 + gateway 가 동일하게 따른다.
- 본 정책 위반 = `tests/engineering/test_engineering_agent_governance_doc.py` 의 lint-style test 가 fail.

## 2. Naming 컨벤션

신규 노트의 파일명:

```
YYYY-MM-DD_issue-<n>-<kind>-<slug>.md
```

| token | 규칙 |
| --- | --- |
| `YYYY-MM-DD` | 노트 작성일 (UTC 기준 권장 — 크로스 타임존 안정성) |
| `issue-<n>` | GitHub issue 번호. issue 가 없는 운영 노트면 `op-YYYYMMDD` 등 명시 prefix 사용 |
| `<kind>` | `research` / `decision` / `task-log` / `report` / `reference` / `meeting` / `knowledge` 중 하나 |
| `<slug>` | kebab-case, 최대 60 자, 한글 또는 ASCII |

기존 노트는 **rename 금지**. 본 컨벤션은 신규 노트에만 강제. v0 contract 의 `<YYYY-MM-DD>_<slug>.md` 와 호환 — `issue-<n>-<kind>` prefix 추가일 뿐.

예시:

```
2026-05-08_issue-69-research-engineering-agent-governance-synthesis.md
2026-05-08_issue-69-decision-engineering-agent-authoring-policy.md
2026-05-08_issue-69-task-log-governance-integration.md
```

## 3. `## 관련 문서` 섹션 강제

모든 신규 / 갱신 노트의 본문 끝에 다음 섹션이 **반드시** 포함된다.

```markdown
## 관련 문서

- [[CLAUDE]]
- [[관련 선행 note 1]]
- [[관련 선행 note 2]]
- [[현재 통합 note]]
```

| 항목 | 규칙 |
| --- | --- |
| `[[CLAUDE]]` | 항상 첫 줄. 부서 안내 문서 anchor. |
| 선행 노트 | 본 노트의 *입력* 이 된 노트들. issue 단위 chain 의 모든 이전 단계 포함. |
| 같은 issue 의 동료 노트 | research / decision / task-log / report 가 같은 `issue-<n>` 안에 있으면 **반드시** 모두 wikilink. |
| 외부 vault 노트 | 사용자 vault 에만 존재해 repo 에 mirror 가 없을 때, **사용자가 합의한 basename** 그대로 wikilink. backlink 는 운영자 수동. |

최소 4 wikilink 가 필수. 부족하면 governance test 가 fail.

## 4. Wikilink target 정확성

- target basename 은 **확장자 없이 정확히 일치**. (`.md` 미포함, 공백·대소문자·하이픈까지 정확히.)
- repo 에 mirror 가 있는 노트 = `notes/vault-mirror/...` 경로의 basename 사용.
- 사용자 vault 외부 노트 = 사용자가 합의한 basename 그대로 인용.
- 잘못된 link 는 graph 단절을 만든다. test 가 mirror 노트의 link target 이 실제 파일과 일치하는지 검사.

## 5. issue-<n> 단위 cross-link 강제

같은 `issue-<n>` 의 noted 가 여러 종류 존재할 때 (research / decision / task-log / report) **반드시 서로 wikilink**.

예: issue #69 의 노트 3 종은:

- `2026-05-08_issue-69-research-engineering-agent-governance-synthesis` 가 decision + task-log 모두 link
- decision 이 research + task-log 모두 link
- task-log 가 research + decision 모두 link

## 6. 선행 issue 의 노트 인용

본 노트가 *선행 issue 의 산출* 을 입력으로 받으면, 선행 issue 의 모든 핵심 노트를 `## 관련 문서` 에 포함.

issue #69 의 경우 선행:

- #25 → vault mirror 3 노트 (실재): `2026-05-08_research_ecc-foundation`, `2026-05-08_decision_ecc-foundation`, `2026-05-08_task-log_25-ecc`
- #48 → 사용자 vault 노트: `2026-05-08_research-harness-team-patterns`, `2026-05-08_decision-tech-lead-single-write-subject`, `2026-05-08_task-log-issue-48-harness`
- #59 → 사용자 vault 노트: `2026-05-08_hermes-agent-architecture-deep-dive`, `2026-05-08_hermes-yule-integration-decisions`, `2026-05-08_59-hermes-tech-lead`

## 7. backlink 추가 정책

**repo 안에 mirror 가 있는 선행 노트** 는 본 통합 노트가 land 될 때 **자동으로 `## 관련 문서` 섹션에 backlink 추가**.

- 가능 (본 PR 적용): #25 의 vault mirror 3 노트 → `## 관련 문서` 섹션에 본 통합 노트 3 종 추가.
- 불가능 (사용자 vault 외부): #48 / #59 의 노트 → 운영자 직접 vault 편집. progress comment 에서 안내.

backlink 는 **추가만**. 기존 링크 / 문구는 보존.

## 8. role 별 author subject

본 정책은 부서 전체에 적용되지만 *author subject* 표기 책임은 [`write-ownership.md`](write-ownership.md) 가 결정한다. 본 정책은 graph 연결만 책임.

## 9. 검증

- `tests/engineering/test_engineering_agent_governance_doc.py` 가 본 정책의 핵심 항목을 lint-style 로 검증:
  - `## 관련 문서` 섹션 존재
  - wikilink 4 항목 이상
  - 같은 `issue-<n>` 노트 간 cross-link
  - mirror 노트의 link target 이 실제 파일과 일치
- 정책 위반 시 test fail. 운영자 인지.

## 10. 변경 이력

| 일자 | 변경 |
| --- | --- |
| 2026-05-08 | 초안 (Issue #69 — D-69-6 ~ D-69-9 결정 반영) |
