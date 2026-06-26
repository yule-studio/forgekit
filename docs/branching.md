# Branch Naming

> 브랜치 네이밍 규칙. 전체 흐름은 [workflow.md](workflow.md). 기존 main-based 전략은
> [`policies/reference/BRANCH_STRATEGY.md`](../policies/reference/BRANCH_STRATEGY.md).

## 1. 형식

모든 작업 브랜치는 `main` 에서 분기하고 다음 형식을 따른다:

```text
<issue-number>-<actor>-<type>-<short-description>
```

브랜치 이름만으로 **이슈 번호 · 누가(actor) · 무슨 종류(type)** 를 알 수 있어야 한다.

| 토큰 | 의미 |
| --- | --- |
| `<issue-number>` | 작업 이슈 번호 (추적 anchor) |
| `<actor>` | 작업 주체 — 사람 핸들 또는 AI 에이전트 |
| `<type>` | 작업 종류 |
| `<short-description>` | 소문자 + 하이픈, 3~5 단어 |

## 2. Actor

작업 주체. 사람은 핸들, AI 는 에이전트 이름을 쓴다.

| Actor | 주체 |
| --- | --- |
| `yuchan` | 사람 기여자 (핸들) |
| `claude` | Claude Code |
| `codex` | Codex CLI |
| `gemini` | Gemini CLI |
| `opencode` | OpenCode |
| `aider` | Aider |
| `agent` | 그 외 / 미지정 자동화 에이전트 |

> actor 는 **누가 변경을 만들었는지** 를 표시할 뿐, GitHub 의 공식 author identity 를
> 위장하지 않는다. 출처 추적 정책은 [ai-attribution.md](ai-attribution.md).

## 3. Type

| Type | 용도 |
| --- | --- |
| `docs` | 문서 |
| `feat` | 신규 기능 |
| `fix` | 버그 수정 |
| `refactor` | 구조 개선 (동작 불변) |
| `test` | 테스트 추가·수정 |
| `chore` | 설정 / 스크립트 / 운영 보조 |
| `infra` | 인프라 / CI / 빌드 환경 |
| `workflow` | 개발 워크플로 / 프로세스 |
| `research` | 조사 / 후보 평가 / 스파이크 |

## 4. 예시

```text
12-claude-docs-ai-workflow
13-codex-feat-forge-plan
14-yuchan-refactor-agent-registry
15-gemini-research-cli-agents
```

## 5. 금지

- **protected branch 직접 작업 금지** — `main` / `master` / `develop` / `release/*` 에
  직접 커밋하지 않는다. 항상 작업 브랜치 → PR.
- 이슈 없는 브랜치 지양. 부득이하면 PR 에 사유를 남긴다.

## 6. 흐름

```bash
git checkout main
git pull origin main
git checkout -b 12-claude-docs-ai-workflow
```

이후 [commits.md](commits.md) 형식으로 커밋하고, PR 은 [review-and-qa.md](review-and-qa.md) 를 따른다.
