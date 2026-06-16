# Product Agent — CODE_LAYOUT

순수 코어는 [`apps/engineering-agent/src/yule_engineering/agents/product_intake/`](../../apps/engineering-agent/src/yule_engineering/agents/product_intake/).
textual / gateway 무관 — 전부 stdlib, 결정형.

## 모듈 책임 분리

| 모듈 | 책임 | 섞지 말 것 |
| --- | --- | --- |
| `models.py` | dataclass 계약(ProductIntentPacket / DecisionQuestion / FeatureGap / FeatureFamily / ProductReadinessVerdict) + 상수 | 로직 금지 |
| `families.py` | feature-family 도메인 지식(데이터): implied/ask/recommended, 질문 템플릿, keyword 검출, 답변 resolve, baseline 보강 | 오케스트레이션 금지 |
| `question_policy.py` | 질문 budget ≤3 · open-ended 금지 · 우선순위 정렬 · deferred → assumption | 도메인 데이터 금지 |
| `shaping.py` | raw ask → ProductIntentPacket 오케스트레이션(보강/질문선별/acceptance/non-goals/readiness) | UI/gateway 금지 |
| `gate.py` | engineering 앞단 seam: should_intercept + run_product_gate(state 분기) | 표현 문자열 금지 |
| `presenter.py` | clarification / handoff_summary / operator_status_line 렌더 | 정책 결정 금지 |

## tests/ 매핑

| 모듈 | 테스트 |
| --- | --- |
| models/families/question_policy/shaping | `tests/agents/test_product_intake.py` |
| gate/presenter | `tests/agents/test_product_gate.py` |

## 파일 크기

모두 ≤200 LOC 유지. 새 feature family 는 `families.py` 데이터 추가로 끝나야 한다(분기 로직 증식 금지).
새 질문 템플릿도 `families.py` 의 `_TEMPLATES` 데이터로만 추가.
