# Commit Messages

> ForgeKit 워크플로의 커밋 규칙. 전체 흐름은 [workflow.md](workflow.md), AI 출처
> 추적 전체 정책은 [ai-attribution.md](ai-attribution.md).

## 1. Conventional Commits

커밋 제목은 [Conventional Commits](https://www.conventionalcommits.org/) 를 따른다.

```text
<type>(<scope>): <subject>
```

예시:

```text
docs(workflow): define ai-assisted development lifecycle
feat(forge): add forge-plan approval gate
fix(nexus): handle empty retrieval result
refactor(registry): split agent registry by role
```

| 토큰 | 규칙 |
| --- | --- |
| `<type>` | `feat` `fix` `docs` `refactor` `test` `chore` `infra` `workflow` `research` 등 (브랜치 type 과 정렬) |
| `<scope>` | 변경 영역 (선택, 권장). 예: `workflow` `forge` `nexus` |
| `<subject>` | 명령형·소문자 시작·마침표 없음, 한 줄 요약 |

본문(선택)은 빈 줄 뒤에 **무엇을·왜** 를 적는다.

## 2. AI attribution trailers

AI 가 작성하거나 보조한 커밋은 본문 마지막에 빈 줄을 두고 **trailer** 로 출처를 남긴다.

```text
docs(workflow): define ai-assisted development lifecycle

Issue → Merge → Release 단계와 게이트를 한 곳에 고정한다.

AI-Agent: claude-code
AI-Mode: supervised
AI-Task: #12
AI-Reviewed-By: yuchan
```

| Trailer | 의미 | 값 예시 |
| --- | --- | --- |
| `AI-Agent` | 변경을 만든 AI 에이전트 | `claude-code` `codex-cli` `gemini-cli` `opencode` `aider` |
| `AI-Mode` | 자율 수준 | `supervised` (사람 감독) · `autonomous` (자율) |
| `AI-Task` | 연결된 이슈 | `#12` |
| `AI-Reviewed-By` | 변경을 검토·소유한 사람 | `yuchan` |

> trailer 는 **Git trailer 형식**(`Key: value`, 본문 끝 블록)이라 `git interpret-trailers`
> 와 GitHub UI 가 그대로 파싱한다. `Co-Authored-By` 로 공식 author 를 위장하지 않는다 —
> AI 기여는 `AI-*` trailer 로만 표시한다. 이유는 [ai-attribution.md](ai-attribution.md).

## 3. 사람 vs AI 커밋 구분

| 작성자 | 구분 방법 |
| --- | --- |
| **사람 (yuchan 등)** | `AI-*` trailer 없음. author = 본인 계정 |
| **Claude Code** | `AI-Agent: claude-code` trailer |
| **Codex CLI** | `AI-Agent: codex-cli` trailer |
| **Gemini CLI** | `AI-Agent: gemini-cli` trailer |
| **OpenCode** | `AI-Agent: opencode` trailer |
| **Aider** | `AI-Agent: aider` trailer |

규칙:

- AI 가 만든 모든 커밋은 **반드시** `AI-Agent` trailer 를 포함한다. trailer 가 없으면
  사람 커밋으로 간주된다 — 그래서 AI 커밋이 trailer 를 빠뜨리면 출처가 사람으로 오인된다.
- `AI-Mode: autonomous` 커밋도 결국 사람 owner 의 리뷰·머지 게이트를 통과한다
  ([review-and-qa.md](review-and-qa.md)).
- 단기 출처는 trailer + 브랜치 actor + PR 필드 + label 로, 장기 출처는 전용 bot 계정 /
  GitHub App 으로 강화한다 → [ai-attribution.md](ai-attribution.md).
