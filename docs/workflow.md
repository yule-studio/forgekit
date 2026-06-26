# Development Workflow

> ForgeKit 의 **AI-assisted development workflow** SSoT. ForgeKit 은 Claude Code,
> Codex CLI, Gemini CLI, OpenCode, Aider 등 여러 AI 코딩 에이전트 + 사람 기여자가
> 함께 개발한다. 폴더 재구조화나 Hephaistos / Hermes / OpenClaw / Nexus / Armory
> 구현보다 **먼저**, AI 작업과 사람 작업이 추적 가능하도록 고정된 워크플로를 둔다.
> 단계별 세부 규칙은 아래 문서에 위임한다.

## 1. 전체 흐름

```text
Issue → Milestone → Branch → Commit → PR → AI Review → Human Code Review → QA → Merge → Release
```

각 화살표는 **게이트**다. 앞 단계의 산출물이 없으면 다음 단계로 넘어가지 않는다.

| 단계 | 산출물 | 게이트 | 세부 문서 |
| --- | --- | --- | --- |
| **Issue** | 작업 이슈 (`task` / `ai-task`) | 명확한 완료 조건 + 라벨 | [github-labels](github-labels.md) |
| **Milestone** | 이슈가 마일스톤에 연결 | 릴리스 / 목표 버전 그룹에 귀속 | [release](release.md) |
| **Branch** | `<issue>-<actor>-<type>-<desc>` 브랜치 | 이슈·actor·type 식별 | [branching](branching.md) |
| **Commit** | gitmoji + 3섹션 + AI trailer | 형식 + 출처 trailer | [commits](commits.md) · [ai-attribution](ai-attribution.md) |
| **PR** | 템플릿 채운 PR | 이슈 linkage + actor 식별 | [review-and-qa](review-and-qa.md) |
| **AI Review** | AI review 결과 코멘트 | 지적 반영 or 사유 | [review-and-qa](review-and-qa.md) |
| **Human Code Review** | 사람 리뷰 승인 | 최소 1 approve (사람) | [review-and-qa](review-and-qa.md) |
| **QA** | 테스트 + QA 체크 통과 | 회귀 라인 존재 | [review-and-qa](review-and-qa.md) |
| **Merge** | main 머지 | **사람 owner 가 최종 머지** | [release](release.md) |
| **Release** | tag + release note | milestone 기준 발행 | [release](release.md) |

## 2. 단계별 핵심

### Issue
- **모든 작업은 Issue 에서 시작한다.** 임의 코드 변경부터 시작하지 않는다.
- 사람 주도 작업은 `task`, AI 주도 작업은 `ai-task` 템플릿을 쓴다.
- 완료 조건(Completion criteria)과 QA 체크를 이슈에 명시한다.

### Milestone
- 마일스톤은 **릴리스 / 목표 버전 그룹** 을 나타낸다 — 임의의 작업 상태(`in-progress`
  같은 것)를 마일스톤으로 쓰지 않는다. 그건 라벨의 일이다.
- 이슈를 마일스톤에 연결해 릴리스 범위에 귀속시킨다.

### Branch
- `main` 에서 분기. 형식 `<issue-number>-<actor>-<type>-<short-description>`.
- 브랜치 이름만으로 **이슈 번호 · actor · type** 을 식별할 수 있어야 한다 → [branching.md](branching.md).

### Commit
- 레포 enforced 형식 = **gitmoji + 한국어 3섹션** (SSoT `COMMIT_CONVENTION.md`, CI 검사). AI 가
  작성·보조한 커밋은 `AI-*` trailer 로 출처를 남긴다.
- 형식과 trailer 는 [commits.md](commits.md), 출처 추적 전체 정책은 [ai-attribution.md](ai-attribution.md).

### PR → AI Review → Human Code Review
- PR 템플릿에 linked issue, actor type, 사용한 AI agent, scope, QA 결과를 채운다.
- **AI Review 와 Human Code Review 는 별개 단계다.** AI Review 가 선행하고, 그 위에
  사람 Code Review 가 올라간다. AI Review 는 사람 리뷰를 대체하지 않는다.

### QA → Merge → Release
- **QA 는 머지 전 필수.** 새 기능엔 새 회귀 테스트 라인이 있어야 한다.
- **AI 는 변경을 준비할 수 있지만 최종 머지는 사람 owner 가 수행한다.** AI 단독
  self-merge 금지 (운영자 명시 인가가 있는 좁은 예외 제외).
- **Release 는 milestone 기준.** tag / release note 는 [release.md](release.md).

## 3. AI-assisted 원칙

- **모든 작업은 Issue 에서 시작한다.**
- **AI 작업과 사람 작업은 구별 가능해야 한다** — branch / PR 필드 / label / commit trailer
  (단기), 전용 AI bot 계정 / GitHub App (장기). 공식 author identity 위장 금지.
- **AI 가 만든 변경도 사람 리뷰 게이트를 그대로 통과한다** — AI 라서 면제되는 단계 없음.
- **AI Review 와 Human Code Review 는 분리된 단계다.**
- **QA 는 머지 전 필수**, **Release 는 milestone 기반**.

## 4. 관련 문서

- [branching.md](branching.md) — 브랜치 네이밍 (`<issue>-<actor>-<type>-<desc>`)
- [commits.md](commits.md) — Conventional Commits + AI trailer
- [ai-attribution.md](ai-attribution.md) — 단기/장기 AI 출처 추적
- [review-and-qa.md](review-and-qa.md) — AI review / 사람 review / QA / merge readiness
- [release.md](release.md) — milestone 기반 릴리스
- [github-labels.md](github-labels.md) — 라벨 체계
