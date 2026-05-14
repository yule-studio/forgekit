# Growth Loop — engineering-agent 부서 공통 (P0-G)

> **소유:** `engineering-agent` 부서 전체. 학습 / 회고 / 재사용 가능한 패턴이 *Obsidian 메모* 에서 *정책 SSoT* 로 승격되는 라이프사이클 정의.
> **목적:** 같은 아쉬움 / 같은 실수 / 같은 패턴이 반복되는데 매번 ad-hoc 으로 해결되는 회귀 차단. 반복 = 신호. 신호 = 정책 승격 후보.
> **출처:** Issue #139 (parent #138) — P0-G 1차.

본 정책은 [`obsidian-governance.md`](obsidian-governance.md) §0 (GitHub vs Obsidian role separation) 의 *학습 미러* 영역을 책임진다.

## 1. 3-구조 lifecycle

Obsidian vault 의 *모든* 메모는 다음 3 구조 중 한 곳에 속한다 (PARA 의 영향 + Yule 운영 특수성):

| 구조 | 의미 | 위치 (vault mirror) | 예시 |
| --- | --- | --- | --- |
| **resources** | 재사용 가능한 reference / 외부 자료 / 일반화된 학습 | `20-resources/` | 외부 repo / docs / PR 의 학습 정리. RAG vs CAG memory 구조 노트. |
| **projects** | 진행 중인 프로젝트 단위 기록 — issue / PR / decision / task-log | `10-projects/<project>/` | 본 레포의 issue #139 task-log. |
| **daily** | 그 날의 짧은 기록 — 아쉬움 / 회고 / off-the-cuff 생각 | `00-daily/YYYY-MM-DD.md` | 2026-05-14 의 daily 메모. |

(folder root prefix `00-` / `10-` / `20-` / `30-` 등 의 정확한 path 는 [`obsidian-memory.md`](obsidian-memory.md) 의 v0 contract 가 SSoT. 본 정책은 *역할 분담* 만 책임.)

## 2. 흐름 — daily / projects / resources 간 이동

1. **daily 가 시작점.** 새 생각 / 회고 / 아쉬움은 daily 에 먼저 쓴다. 임계값 (정책 § 4) 을 넘지 않는 한 다른 위치로 옮기지 않는다.
2. **projects 로 promote** — daily 의 메모가 *특정 issue / PR / decision* 에 직접 묶이면 projects 폴더의 해당 issue 노트로 이동 (full content 또는 wikilink). daily 노트는 backlink 유지.
3. **resources 로 promote** — projects 의 학습 / 패턴 / 외부 자료가 *다른 프로젝트에도 재사용 가능* 하면 resources 로 추출. projects 노트는 backlink 유지.

승격은 **항상 위 방향만** — resources → projects → daily 방향으로 *되돌리지* 않는다. 만약 resources 의 노트가 오래되어 사라져야 하면 [`obsidian-governance.md`](obsidian-governance.md) §7 backlink 정책에 따라 supersede 표기.

## 3. 재사용 신호 — 정책 승격 후보 식별

다음 신호가 *둘 이상* 동시에 잡히면 *개인 메모* 단계를 넘어 **정책 SSoT 로 승격 검토**:

| 신호 | 어디서 감지 |
| --- | --- |
| 같은 아쉬움 / 같은 실수가 daily 에 3 회 이상 반복 | grep `## 아쉬움` / `## 회고` 키워드. |
| 동일 패턴이 projects 노트의 §회고 / §결정 에 등장 | 다른 issue 의 노트인데 결정이 거의 같음. |
| PR review 에서 같은 reviewer feedback 이 반복 | GitHub PR review API / Obsidian 의 PR review 노트. |
| failure-postmortem 의 root cause 가 동일 | `agent-ops/postmortems/` 의 5 건 중 2 회 이상 같은 cause. |
| 외부 repo 작업 시 같은 RepoContract 결정이 반복 | [`repo-contract-discovery.md`](repo-contract-discovery.md) 의 fallback hit 가 자주 발생. |

**최소 2 신호** 가 잡혀야 승격 검토. 한 가지만으로는 패턴이라고 단정하지 않는다.

## 4. 정책 승격 워크플로

신호가 잡힌 후의 *명시적 단계*:

1. **회고 / decision 노트 작성** — `projects/<project>/decisions/` 또는 `resources/patterns/` 에 신호의 근거를 정리. 신호 출처 (어느 daily / 어느 PR / 어느 postmortem) 를 모두 인용.
2. **정책 SSoT 후보 식별** — 본 레포의 어느 정책 문서가 이 패턴을 안에 박을지 결정 (예: 외부 repo 패턴 → `repo-contract-discovery.md`, 머지 흐름 패턴 → `github-workflow.md`).
3. **정책 PR 작성** — 정책 PR 은 *대부분 docs-only* (예외 4 종 중 하나 — [`github-workflow.md`](github-workflow.md) §5.1) 라 C/R/U/D 분류 강제 없음. 단 회귀 test 는 동행 권장.
4. **승급 audit 기록** — 정책이 land 되면 originating Obsidian 노트의 frontmatter / 본문에 "정책 #PR-N 으로 승격" backlink. 같은 패턴이 다시 daily 에 등장하면 운영자가 즉시 "정책 §X.Y 참조" 로 답할 수 있어야 한다.

## 5. 승격 안 함 — 개인 메모로 남기는 케이스

다음은 정책으로 *승격하지 않는다*. 학습 미러는 운영 규칙이 아니어도 가치가 있다.

- 일회성 회고 / 단발성 아쉬움 (반복 X).
- 사용자의 *취향* / *선호* (mode 결정 같은 운영 결정은 예외 — [`docs/autonomy-policy.md`](../../../../docs/autonomy-policy.md) §0 가 책임).
- 검증되지 않은 가설 / 실험 진행 중 메모.
- 보안 / credential / secret — 어느 surface 든 SSoT 로 박지 않는다.

## 6. 본 정책의 코드 land 단계

본 정책 자체는 *문서* 가 SSoT. 코드 자동화는 후속:

- **승격 신호 감지 자동 helper** — `agents/lifecycle/self_improvement.py` (이미 존재, M10c) 가 *failed_retryable 누적 / 동일 topic 중복 approval / 빈 hydration / stale heartbeat* 신호를 잡는다. 본 정책이 추가한 5 신호 (daily 반복 / PR review 반복 등) 의 감지 wiring 은 #140 stage 2 scope.
- **정책 PR template 보조** — 정책 승급용 PR template (Audit 블록 + 신호 출처) 은 stage 2 의 후속 작업.

## 7. 검증

`tests/engineering/test_policy_stack_completeness.py` (P0-G commit 7 신설) 가 본 정책 파일 존재 + §1 3-구조 / §2 흐름 / §3 신호 / §4 승급 워크플로 키워드를 lint.

## 8. 변경 이력

| 일자 | 변경 |
| --- | --- |
| 2026-05-14 | 초안 (Issue #139 — P0-G stage 1 정책 8종 1차 land. parent #138.) |

## 관련 문서

- [[CLAUDE]]
- [[governance]]
- [[obsidian-governance]]
- [[obsidian-memory]]
- [[repo-contract-discovery]]
