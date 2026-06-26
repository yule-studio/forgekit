# AI Attribution

> AI 작업과 사람 작업을 **추적 가능** 하게 만드는 정책. ForgeKit 은 여러 AI 코딩
> 에이전트 + 사람이 함께 개발하므로, 누가 무엇을 만들었는지 흔적이 남아야 한다.
> 전체 흐름은 [workflow.md](workflow.md).

## 1. 왜 필요한가

- AI 가 생성한 변경과 사람이 작성한 변경을 **구별** 할 수 있어야 책임·리뷰·디버깅이 가능하다.
- 출처가 흐려지면 AI 의 실수를 사람의 의도로 오인하거나, 사람의 결정을 AI 탓으로 돌리게 된다.
- 그래서 ForgeKit 은 **단기**(즉시 적용 가능한 신호)와 **장기**(계정 수준 정체성) 두 층으로
  출처를 남긴다.

## 2. 단기 attribution (지금 적용)

코드를 바꾸지 않고 메타데이터만으로 출처를 남기는 4개 신호:

| 신호 | 형태 | 예시 |
| --- | --- | --- |
| **Branch name** | `<issue>-<actor>-<type>-<desc>` 의 actor | `12-claude-docs-ai-workflow` |
| **Commit trailer** | `AI-Agent` / `AI-Mode` / `AI-Task` / `AI-Reviewed-By` | `AI-Agent: claude-code` |
| **Label** | `ai:<agent>` | `ai:claude`, `ai:codex` |
| **PR template 필드** | Actor type / AI agent used | "AI agent: Claude Code" |

네 신호는 서로 **교차 검증** 한다 — 브랜치 actor 가 `claude` 면 커밋 trailer 도
`claude-code`, label 도 `ai:claude`, PR 필드도 Claude Code 여야 일관적이다.

세부:
- 브랜치 actor → [branching.md](branching.md)
- commit trailer → [commits.md](commits.md)
- label → [github-labels.md](github-labels.md)
- PR 필드 → [`.github/pull_request_template.md`](../.github/pull_request_template.md)

## 3. 장기 attribution (권장 방향)

메타데이터는 지우거나 빠뜨릴 수 있으므로, 장기적으로는 **계정 수준 정체성** 으로 강화한다:

- **전용 AI bot 계정** — 각 에이전트가 자기 GitHub 계정으로 커밋·PR 을 만든다
  (예: `forgekit-claude-bot`).
- **GitHub App** — 에이전트별 App 으로 인증해 author / committer 가 자동으로 봇 정체성을
  갖게 한다. commit 서명·권한 범위도 App 단위로 관리된다.

이렇게 하면 trailer 를 빠뜨려도 author 자체가 봇이라 출처가 보존된다.

## 4. Author 위장 금지

- AI 커밋의 author/committer 를 **사람의 이름·이메일로 위장하지 않는다.**
- `Co-Authored-By` 로 AI 를 공식 공동 저자처럼 끼워 넣지 않는다 — AI 기여는 `AI-*`
  trailer 와 봇 정체성으로만 표시한다.
- 이유: 공식 author identity 는 **사람의 책임 서명** 이다. AI 가 그걸 빌려 쓰면 누가 변경을
  소유·승인했는지가 무너진다.

## 5. Claude Code 작업 식별

Claude Code 가 만든 작업은 다음으로 식별된다:

| 층 | 값 |
| --- | --- |
| Branch actor | `claude` |
| Commit trailer | `AI-Agent: claude-code` |
| Label | `ai:claude` |
| PR 필드 | AI agent used: Claude Code |
| (장기) 계정 | 전용 bot 계정 / GitHub App |

이 다섯 신호 중 단기 4개는 지금 즉시 적용하고, 계정 층은 봇 계정 / App 도입 시 추가한다.
