# Engineering-agent Governance — 부서 공통 운영 규칙 umbrella (Issue #69)

> **소유:** `engineering-agent` 부서 전체에 적용. 7 역할 (`tech-lead` / `backend-engineer` / `frontend-engineer` / `devops-engineer` / `qa-engineer` / `ai-engineer` / `product-designer`) + `gateway` surface 가 동일 정책을 따른다.
> **목적:** Obsidian / write ownership / GitHub workflow 의 부서 공통 정책을 한 곳에서 cross-link 한다.
> **출처:** Issue #69 (parent #20). 통합 입력 = 완료된 #25 / #48 / #59. 14 개 결정 (D-69-1 ~ D-69-14).

본 문서는 *umbrella* — 실제 정책 본문은 다음 3 문서가 책임진다.

| layer | 문서 | 책임 영역 |
| --- | --- | --- |
| Obsidian | [`obsidian-governance.md`](obsidian-governance.md) | 노트 naming / `## 관련 문서` 강제 / wikilink 정확성 / cross-link 정책 |
| Write ownership | [`write-ownership.md`](write-ownership.md) | 3-mode authoring (`role-owned` / `tech-lead-mediated` / `gateway-mediated`) + 결정 트리 + 7 역할 surface 매트릭스 |
| GitHub workflow | [`github-workflow.md`](github-workflow.md) | issue / PR template / label / progress comment / 커밋 분할 / GitHub Apps / push 정책 |

## 1. 부서 governance 의 4 원칙

1. **deterministic.** 누가·어디에·무엇을 쓸지의 결정은 정책 표 한 번으로 끝난다. ad-hoc 결정 금지.
2. **부서 공통.** 7 역할 + gateway 가 같은 trigger / 같은 결정 트리 / 같은 surface 정책을 따른다.
3. **graph-aware.** 모든 산출물은 GitHub issue / PR ↔ Obsidian 노트 ↔ 정책 본문이 cross-link 로 이어진다.
4. **회귀 보호.** 정책 본문은 lint-style test (`tests/engineering/test_engineering_agent_governance_doc.py`) 가 보호. 정책 위반 = test fail.

## 2. 선행 / 입력 정책

본 governance 가 의존하는 기존 부서 정책 (변경 없음):

- [`team-structure.md`](team-structure.md) — 7 역할 + gateway 정의
- [`role-profiles.md`](role-profiles.md) — 역할별 mission / activation_keywords / output_sections
- [`lifecycle-mvp.md`](lifecycle-mvp.md) — 13 단계 lifecycle / session.extra persistence
- [`obsidian-memory.md`](obsidian-memory.md) — Obsidian export contract v0
- [`team-architecture-patterns.md`](team-architecture-patterns.md) — Harness 6 패턴 (#48)
- [`ecc-foundation.md`](ecc-foundation.md) — ECC 4 layer + research-first 게이트 (#25)
- `memory-policy.md` / `recall-policy.md` / `context-compression.md` / `self-improvement-flow.md` / `scheduled-automation.md` — Hermes 5 정책 (#59)

본 governance 는 위 정책을 **변경하지 않는다**. 위 정책 위에 *write 표면 / Obsidian graph / GitHub 표면* 의 일관성을 얹는다.

## 3. 핵심 매트릭스 — write ↔ surface 결정 트리

```
write 발생 시:
  Q1: 부서 intake / status / 외부 사용자 notification 인가?
        Yes → gateway-mediated     → gateway 가 author
  Q2: 다역할 합의·충돌·통합·외부 회신·cross-role 영향 인가?
        Yes → tech-lead-mediated   → tech-lead 가 author
  Q3: 특정 역할의 자기 deliverable 인가?
        Yes → role-owned           → 그 역할이 author
  default → tech-lead-mediated
```

surface 별 최종 author subject:

- GitHub issue body / comment → 결정 트리에 따라.
- GitHub PR body → 같음 (PR Audit 블록의 `mode` / `role` 필드).
- Obsidian 노트 frontmatter `author_role` → 같음.
- Discord `#업무-접수` 응답 → gateway.
- Discord forum thread 의 role take → role-owned (각 역할의 멤버 봇).
- supervisor / status / `#봇-상태` → gateway.
- `/engineer_show` 응답 → gateway.

자세한 trigger 표 + 7 역할 surface 매트릭스는 [`write-ownership.md`](write-ownership.md) §2~§5.

## 4. Obsidian graph

신규 노트의 부서 공통 의무:

1. naming = `YYYY-MM-DD_issue-<n>-<kind>-<slug>.md` ([`obsidian-governance.md`](obsidian-governance.md) §2).
2. `## 관련 문서` 섹션 + 최소 4 wikilink (§3).
3. wikilink target 은 실제 basename 정확 일치 (§4).
4. issue-<n> 단위 cross-link (§5).
5. 선행 issue 의 노트 모두 인용 (§6).
6. repo mirror 가 있는 선행 노트는 자동 backlink (§7) — 사용자 vault 외부 노트는 수동.

기존 노트 rename 금지. 본 정책은 신규 / 갱신 노트에만 강제.

## 5. GitHub 표면

- issue body = ISSUE_TEMPLATE 4 섹션 + sub-issue 면 `Parent: #<n>` ([`github-workflow.md`](github-workflow.md) §1).
- PR body = PULL_REQUEST_TEMPLATE 4 섹션 + Audit 블록 (§2).
- Label = repo 실재 label 만 부착, 미생성은 추천만 (§3).
- Progress comment = 5 섹션 (§4).
- 커밋 = ≥3 분할 (§5.1). COMMIT_CONVENTION 의 한국어 3 섹션 엄격 준수.
- push = 현재 작업 브랜치만, force / protected branch / auto merge / deploy 영구 금지 (§5.3).
- GitHub Apps = G6 LiveGithubAppClient 우선 사용 (§5.2).

## 6. push / merge / deploy 영구 hard rail

본 governance 는 다음을 **부서 정책 수준** 에서 영구 금지로 박는다:

- protected branch (`main`/`master`/`dev`/`prod`/`release`) 직접 push.
- force push (`git push --force` 또는 그에 준하는 GitHub Apps 호출).
- auto merge.
- production deploy 자동화.
- secret / token / pem / Authorization 헤더 출력 (모든 surface — Discord / GitHub / Obsidian / log).
- 사용자 기존 변경 덮어쓰기.

위 항목은 사용자 명시 승인이 있어도 **본 정책 자체로는 풀리지 않는다** — 별도의 hard-rail 변경 PR + 사용자 결정이 필요.

## 7. 검증

| 검증 | 위치 |
| --- | --- |
| Obsidian governance 회귀 | `tests/engineering/test_engineering_agent_governance_doc.py` (본 PR 신설) |
| Write ownership 매트릭스 | 같음 |
| GitHub workflow 헤딩 / 섹션 | 같음 |
| 정책 markdown 의 cross-link | 같음 |

`python3 -m unittest discover -s tests -t .` 가 본 정책 회귀를 자동 차단.

## 8. 변경 이력

| 일자 | 변경 |
| --- | --- |
| 2026-05-08 | 초안 (Issue #69 — D-69-1 ~ D-69-14 통합. #25/#48/#59 산출물을 부서 governance 로 정착.) |
