# Skill — experiment-design (Execution)

## When to use
A/B test / multivariate / holdout 실험 설계.

## Inputs
- 가설 1 문장 (예상 변화 + 크기)
- 북극성 / KR metric
- 트래픽 / 사용자 수

## Steps
1. **가설**: "X 변경 → Y metric Z% 변화 (95% confidence)".
2. **MDE (Minimum Detectable Effect)**: 의미 있는 최소 변화 크기.
3. **sample size**: power analysis (1-β=0.8, α=0.05) → 필요한 사용자 수.
4. **duration**: sample / weekly traffic + 주기적 효과 (요일 / 결제 cycle).
5. **guardrail metric**: primary metric 외 — 손상 되면 안 되는 metric (latency / churn / revenue).
6. **decision rule**: pre-register — 어떤 결과면 ship / kill / iterate.
7. **stratification / segmentation**: 의도된 segment 분석 (new vs returning).

## Output
- Experiment plan (1 페이지):
  - 가설 + MDE + 필요 sample
  - Primary metric + guardrail
  - Duration
  - Decision rule (pre-registered)
  - Segmentation plan

## Quality bar
- Pre-registered decision rule (사후 cherry-pick 방지)
- Guardrail metric 명시
- Power analysis 완료
- Multiple test 시 — correction (Bonferroni / Benjamini-Hochberg)
