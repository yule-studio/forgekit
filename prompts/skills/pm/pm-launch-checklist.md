# Skill — launch-checklist (GTM)

## When to use
새 기능 / 제품 / segment 의 launch 직전. cross-functional sign-off.

## Inputs
- PRD + feature spec
- 마케팅 / 영업 / 고객지원 / 법무 의존 항목
- Rollout plan (canary 단계)

## Steps
1. **Engineering ready**: 모든 acceptance criteria 통과 + observability + rollback.
2. **QA / 보안 ready**: 회귀 / 부하 / pen test (필요 시).
3. **Marketing ready**: positioning / 메시지 / 블로그 / SEO key / 채널 일정.
4. **Sales / CS ready**: 영업 자료 / FAQ / 시나리오 트레이닝.
5. **Legal ready**: ToS / privacy / 약관 / DPA.
6. **Support ready**: docs / runbook / 사고 대응 절차.
7. **Metric ready**: dashboard / alert / experiment (있으면).
8. **Comms plan**: 내부 announcement + 외부 announcement (timing).
9. **Rollback plan**: 어떤 signal 보이면 rollback / 누가 결정 / 어떻게.

## Output
- Launch checklist (1 페이지):
  - 9 단계 — owner + status (red / yellow / green)
  - go/no-go meeting timestamp
  - rollback trigger / criteria

## Quality bar
- 9 단계 모두 owner 명시
- Rollback trigger 명확 (정성적 신호 + 정량적 threshold)
- Cross-functional sign-off (engineering / marketing / sales / legal)
- Internal + external comms plan
