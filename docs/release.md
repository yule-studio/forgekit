# Release

> milestone 기반 릴리스 관리. 전체 흐름은 [workflow.md](workflow.md).

## 1. Milestone = 릴리스 그룹

- 마일스톤은 **릴리스 / 목표 버전** 을 나타낸다 (예: `v0.1`, `forge-foundation`).
- `in-progress` 같은 **작업 상태는 마일스톤이 아니라 label** 로 표현한다
  ([github-labels.md](github-labels.md)).
- 모든 작업 이슈는 하나의 마일스톤에 귀속된다 — 그 마일스톤이 곧 릴리스 단위다.

## 2. 릴리스 조건

마일스톤을 릴리스로 닫으려면:

- [ ] 마일스톤의 모든 이슈가 머지되거나 명시적으로 다음 마일스톤으로 이월됨
- [ ] main 이 green (테스트·QA 통과)
- [ ] 릴리스 노트 작성됨 (아래 §3)
- [ ] tag 발행은 repo 의 `tag_policy` 를 따른다 — **정책 없으면 자동 발행 금지**

> AI 는 릴리스를 **단독으로 발행하지 않는다.** 변경 준비·노트 초안까지가 AI 의 범위이고,
> tag/release 발행은 사람 owner 가 수행한다.

## 3. 릴리스 노트 기대치

릴리스 노트는 마일스톤 단위로 작성하며 최소 다음을 담는다:

- **버전 / 마일스톤** — 무엇을 닫는 릴리스인가
- **Highlights** — 주요 변경 (사용자/운영자 관점)
- **Changes by type** — `feat` / `fix` / `refactor` / `docs` / `infra` 등으로 묶음
- **AI-assisted 기여** — AI 가 주도한 항목 표시 (commit trailer / label 기반,
  [ai-attribution.md](ai-attribution.md))
- **Breaking changes / 마이그레이션** — 있으면 명시
- **Known issues / 이월** — 다음 마일스톤으로 넘긴 항목

## 4. 태그

```text
v<major>.<minor>.<patch>
```

- 태그는 마일스톤 닫힘과 함께 발행한다.
- `tag_policy` 가 정의돼 있을 때만 발행하며, 정책이 없으면 사람 판단을 기다린다.
