# Skill — prioritisation-rice (Execution)

## When to use
Backlog 5-50 개 → 우선순위. roadmap-quarterly 의 prioritisation 단계.

## Inputs
- Item list (PRD draft / idea / 버그)
- 평가 기준 합의 (RICE / ICE / Cost of Delay 중 선택)

## Steps
1. **RICE 정의**:
   - **Reach**: 분기 내 영향 받는 사용자 수.
   - **Impact**: per-user 영향 (3 / 2 / 1 / 0.5 / 0.25 등 정성 → 정량).
   - **Confidence**: 추정 신뢰도 (100 / 80 / 50%).
   - **Effort**: person-month.
   - **Score = (R × I × C) / E**.
2. **확신 없는 effort — engineering 합의**: PM 단독 추정 X.
3. **bucket grouping**: 점수 — high / medium / low 3 분류.
4. **scope unbundle**: 큰 item — sub-task 로 쪼개서 재평가.
5. **stakeholder review**: top 5 + bottom 5 — sales / marketing / engineering 합의.
6. **결정 기록**: decision record (왜 X 를 위 / Y 를 아래로).

## Output
- RICE sheet (markdown table):
  - Item / Reach / Impact / Confidence / Effort / Score / Bucket
- 결정 사항 (top 5 commit / bottom 5 dropped / unbundled)

## Quality bar
- Effort — engineering 합의
- Confidence — 정직 (overconfidence 금지)
- top 5 — 명시적 rationale
- dropped 이유 기록 (재논의 input)
