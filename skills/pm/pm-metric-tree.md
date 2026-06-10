# Skill — metric-tree (Strategy)

## When to use
북극성 metric → 하위 lever metric 의 분해. KR 설계 / 분석 dashboard / 실험 설계 input.

## Inputs
- 북극성 metric (예: weekly active user, revenue)
- 사업 모델 (acquisition / activation / retention / revenue / referral)

## Steps
1. **북극성 식별**: 1 개. 회사 / 부서 — 다를 수 있음.
2. **multiplicative decomposition**: 북극성 = A × B × C (예: WAU = signup × activation × week-1 retention).
3. **leading vs lagging**: 각 lever — leading (early) / lagging (late) 분류.
4. **lever 별 owner**: 부서 / 역할 매핑 (engineering / marketing / product).
5. **현재값 / 목표값 / target**: 각 leaf metric — 베이스 / 목표.
6. **검증**: 모든 leaf 가 동시에 X% 개선 — 북극성 Y% 개선되는지 산식 검증.

## Output
- Metric tree (markdown / mermaid):
  - 북극성 (root)
  - 가지 (1-2 단계 decomposition)
  - leaf metric (정의 / 현재 / 목표 / owner)

## Quality bar
- multiplicative 또는 additive decomposition 명확
- 모든 leaf metric — 측정 가능 (데이터 source 명시)
- leading metric 최소 1 개
- 부서 별 owner 명시
