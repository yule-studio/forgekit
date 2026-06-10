# Skill — feature-spec (Execution)

## When to use
PRD 가 land 된 후 — engineering 이 바로 구현할 수 있는 spec.

## Inputs
- PRD (prd-draft output)
- 디자인 (Figma / wireframe / mock)
- 기술 제약 (legacy / 의존 시스템)

## Steps
1. **scope re-confirm**: PRD 의 user story → 이번 ticket 의 scope subset 명시.
2. **state model**: 입력 / 상태 / 출력 — state machine 혹은 flowchart.
3. **edge case**: error / empty / overflow / concurrent / network failure / permission.
4. **API contract**: endpoint / request / response / error code (gRPC 라면 .proto).
5. **acceptance criteria**: GIVEN / WHEN / THEN — testable.
6. **rollout plan**: feature flag / canary / 100% — 단계.
7. **observability**: 어떤 metric / log / trace 를 추가하는지.

## Output
- Feature spec 1-3 페이지:
  - Scope (refer to PRD section)
  - State / flow
  - API contract
  - Acceptance criteria
  - Edge case
  - Rollout + observability

## Quality bar
- Edge case 5+ 명시
- Acceptance criteria — GIVEN/WHEN/THEN 형식
- Rollout 단계 명시 (flag / canary / 100%)
- Observability — alert threshold 까지
