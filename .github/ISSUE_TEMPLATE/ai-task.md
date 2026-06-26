---
name: "AI Task"
about: "AI 에이전트 주도 작업 이슈. 워크플로는 docs/workflow.md"
title: "[AI Task] "
labels: ""
assignees: ""
---

<!-- 워크플로: docs/workflow.md · 출처 추적: docs/ai-attribution.md · 라벨: docs/github-labels.md -->

## AI agent
<!-- claude-code / codex-cli / gemini-cli / opencode / aider -->
<!-- 해당 ai:* label 도 함께 부착 -->

## Expected autonomy level
<!-- supervised (사람 감독, 단계마다 확인) / autonomous (자율, 결과 일괄 리뷰) -->

## Required approvals
<!-- 머지 전 필요한 사람 승인. 최소 1명 사람 approve. AI 단독 승인 불가 (docs/review-and-qa.md) -->
-

## Allowed files / areas
<!-- 에이전트가 만질 수 있는 경로/범위 -->
-

## Forbidden actions
<!-- 하지 말아야 할 것 (예: dependency 추가, build 변경, protected branch 직접 작업, self-merge, author 위장) -->
-

## Completion criteria
<!-- 끝났다고 판단할 객관적 조건 -->
- [ ]

## Review checklist
- [ ] AI Review 통과 (지적 반영 or 사유 기록)
- [ ] 사람 Code Review approve ≥ 1
- [ ] QA 통과 (회귀 라인 포함)
- [ ] 커밋에 `AI-Agent` / `AI-Mode` / `AI-Task` / `AI-Reviewed-By` trailer (docs/commits.md)
- [ ] 최종 머지는 사람 owner 수행
