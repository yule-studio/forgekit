# Commit Messages

> ForgeKit 워크플로의 커밋 규칙. 전체 흐름은 [workflow.md](workflow.md), AI 출처
> 추적 전체 정책은 [ai-attribution.md](ai-attribution.md).
>
> **현재 enforced 규칙의 SSoT 는 [`policies/reference/COMMIT_CONVENTION.md`](../policies/reference/COMMIT_CONVENTION.md)**
> 이며 CI(`scripts/ci_check_commit_messages.py`)가 모든 PR 커밋을 검사한다. 본 문서는
> 그 규칙을 워크플로 관점에서 요약하고 **AI attribution 레이어**를 더한다.

## 1. 형식 (gitmoji + 한국어 3섹션)

레포가 enforce 하는 커밋 형식:

```text
<gitmoji> 제목

변경 이유
- ...

주요 변경 사항
- ...

비고
- ...
```

- 제목 첫 토큰은 **허용 gitmoji 1개** (`📝` 문서 · `✨` 기능 · `🐛` 버그 · `♻️` 리팩토링 ·
  `✅` 테스트 · `🔧` 설정 등 — 전체 whitelist 는 COMMIT_CONVENTION.md).
- 본문은 `변경 이유` / `주요 변경 사항` / `비고` **3 섹션을 plain-text 헤더**로 둔다
  (markdown `##` 헤더 금지). 내용이 없어도 섹션은 생략하지 않고 `- 없음`.

## 2. AI attribution trailers

AI 가 작성·보조한 커밋은 본문 마지막에 빈 줄을 두고 **trailer** 로 출처를 남긴다.
trailer 는 3섹션 본문 뒤에 와도 governance 를 깨지 않는다 (검증 완료).

```text
📝 AI 기반 개발 워크플로 파운데이션 문서 추가

변경 이유
- AI/사람 작업 추적용 고정 워크플로가 없음

주요 변경 사항
- Issue→…→Release 흐름 + branch naming + AI attribution 문서화

비고
- 없음

AI-Agent: claude-code
AI-Mode: supervised
AI-Task: #457
AI-Reviewed-By: yuchan
```

| Trailer | 의미 | 값 예시 |
| --- | --- | --- |
| `AI-Agent` | 변경을 만든 AI 에이전트 | `claude-code` `codex-cli` `gemini-cli` `opencode` `aider` |
| `AI-Mode` | 자율 수준 | `supervised` · `autonomous` |
| `AI-Task` | 연결된 이슈 | `#457` |
| `AI-Reviewed-By` | 변경을 검토·소유한 사람 | `yuchan` |

> `Co-Authored-By` 로 공식 author 를 위장하지 않는다 (레포 governance 가 `Co-Authored-By`
> 를 금지). AI 기여는 `AI-*` trailer 로만 표시한다. 이유는 [ai-attribution.md](ai-attribution.md).

## 3. 사람 vs AI 커밋 구분

| 작성자 | 구분 방법 |
| --- | --- |
| **사람 (yuchan 등)** | `AI-*` trailer 없음. author = 본인 계정 |
| **Claude Code** | `AI-Agent: claude-code` trailer |
| **Codex CLI** | `AI-Agent: codex-cli` trailer |
| **Gemini CLI** | `AI-Agent: gemini-cli` trailer |
| **OpenCode** | `AI-Agent: opencode` trailer |
| **Aider** | `AI-Agent: aider` trailer |

- AI 가 만든 커밋은 **반드시** `AI-Agent` trailer 를 포함한다. 없으면 사람 커밋으로 간주된다.
- 단기 출처는 trailer + 브랜치 actor + PR 필드 + label, 장기 출처는 전용 bot 계정 /
  GitHub App 으로 강화한다 → [ai-attribution.md](ai-attribution.md).

## 4. Proposed evolution (follow-up)

`<type>(<scope>): <subject>` 형태의 [Conventional Commits](https://www.conventionalcommits.org/)
채택은 **별도 follow-up** 으로 검토한다. 채택하려면 `COMMIT_CONVENTION.md` 와
`ci_check_commit_messages.py` 를 함께 바꿔야 하므로 본 워크플로 PR 범위 밖이다. **현재는 위
1~2 절의 gitmoji + 3섹션 + AI-* trailer 가 적용 규칙이다.**
