# GitHub Labels

> ForgeKit 워크플로 라벨 체계. 네임스페이스 prefix (`ai:` / `status:` / `type:` /
> `risk:`) 로 그룹을 구분한다. 전체 흐름은 [workflow.md](workflow.md).

## 1. `ai:` — 작업한 AI 에이전트

AI 가 주도한 작업에 붙인다 (사람 작업은 `ai:` label 없음). 출처 추적의 한 신호 →
[ai-attribution.md](ai-attribution.md).

| Label | 의미 |
| --- | --- |
| `ai:claude` | Claude Code |
| `ai:codex` | Codex CLI |
| `ai:gemini` | Gemini CLI |
| `ai:opencode` | OpenCode |
| `ai:aider` | Aider |

## 2. `status:` — 워크플로 단계

작업이 흐름의 어디에 있는지 표시한다 (마일스톤이 아니라 **상태**는 label 로).

| Label | 단계 |
| --- | --- |
| `status:planning` | 계획 중 |
| `status:in-progress` | 작업 중 |
| `status:ai-review` | AI Review 중 |
| `status:code-review` | 사람 Code Review 중 |
| `status:qa` | QA 중 |
| `status:ready-to-merge` | 머지 준비 완료 (사람 머지 대기) |
| `status:released` | 릴리스됨 |

## 3. `type:` — 작업 종류

브랜치 / 커밋 type 과 정렬된다.

| Label | 종류 |
| --- | --- |
| `type:docs` | 문서 |
| `type:feature` | 신규 기능 |
| `type:bug` | 버그 |
| `type:refactor` | 리팩토링 |
| `type:infra` | 인프라 / CI / 빌드 |
| `type:workflow` | 워크플로 / 프로세스 |
| `type:research` | 조사 / 스파이크 |

## 4. `risk:` — 리스크 수준

| Label | 수준 |
| --- | --- |
| `risk:low` | 낮음 — 문서 / 격리된 변경 |
| `risk:medium` | 중간 — 동작 변경, 회귀 가능 |
| `risk:high` | 높음 — 광범위 / 되돌리기 어려움 / 신뢰 경계 |

## 5. 사용 규칙

- 모든 작업 이슈/PR 에 최소 `type:*` + `risk:*` 1개씩.
- AI 주도면 해당 `ai:*` 추가.
- `status:*` 는 단계 이동에 맞춰 갱신 — 동시에 하나만 유지한다.
