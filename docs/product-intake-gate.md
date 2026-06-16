# Product intake gate — raw ask → product packet → tech-lead handoff (SSoT)

> product-manager 가 **engineering gateway 앞단** 에서 동작하는 방식. 부서 계약은
> [`agents/product-agent/CLAUDE.md`](../agents/product-agent/CLAUDE.md), 코드 SSoT 는
> [`agents/product_intake/`](../apps/engineering-agent/src/yule_engineering/agents/product_intake/).

## 1. 언제 끼어드는가

`gate.should_intercept(raw_text)` 가 **제품/기능 요청**(feature family 검출 또는 구현/만들/
추가/기능/서비스 등 build verb)일 때만 가로챈다. 일정·잡담·단순 질문 등은 그대로 통과 —
기존 engineering clarification flow 는 **무변경**(gate 는 additive).

## 2. 흐름

```
raw ask ──should_intercept?──┬─ no ─→ (기존 engineering flow 그대로)
                             └─ yes ─→ shape_product_intent
                                          │
                                          ├─ feature family gap audit (implied 보강)
                                          ├─ 안전 기본값 자동 채움(assumption)
                                          ├─ 중요 결정만 ≤3 질문 선별(옵션+추천)
                                          ├─ acceptance / non-goals / roles
                                          └─ readiness 판정
                                          ▼
   state ── clarification_needed ─→ 사용자에게 ≤3 결정 질문(번호+옵션+추천)
         ── implementation_candidate / spec_ready ─→ tech-lead 에 ProductIntentPacket handoff
         ── research_only ─→ 조사/분석 경로
         ── blocked ─→ 진행 불가 사유
```

## 3. 자동 보강 vs 사용자 질문

| 자동 채움 (assumption / recommended_default) | 사용자에게 질문 (≤3) |
| --- | --- |
| loading / empty / error 상태 | visibility(공개 정책) |
| 입력 검증(validation) | permission / role(주체·권한) |
| permission·visibility guard | billing(과금 모델·환불) |
| 반응형/모바일 | publish(발행·draft) |
| 민감 작업 audit/observability | ordering(노출 순서) |
| feature family 의 safe default | external integration / destructive |

**요청에 이미 명시된 결정**(예 "관리자만, 비공개 후 공개, 최신순")은 질문하지 않고
assumption 으로 resolve → 결정이 다 차면 바로 `implementation_candidate`.

## 4. feature family checklist (1차 8종)

`media_upload · admin_crud · auth_and_permission · list_detail_catalog · notification ·
payment_or_billing · search_filter · scheduling_or_publish`.

각 family 는 **implied features**(서비스에 필수) · **ask**(결정 질문) · **recommended
defaults**(안전 기본값) 를 데이터로 선언. 예 `media_upload`:
- implied: processing_state · failure_retry · thumbnail_fallback · visibility_state · ordering_display
- ask: 업로드/조회 주체 · 공개 정책 · 노출 순서
- recommend: 관리자만 업로드 · 비공개 후 공개 · 관리자 surface 있으면 수동 정렬

## 5. 질문 budget 규칙

- 최대 **3개**, open-ended 금지 — 모든 질문은 옵션 + **추천안 1개**.
- 우선순위: destructive → billing → permission → visibility → publish → ordering →
  external_integration. budget 초과분은 **추천 기본값으로 assumption** 처리.

## 6. handoff 에 담기는 것

acceptance criteria · user decisions(미정) · implied features · non-goals · core flow ·
suggested roles. tech-lead 는 이 packet 을 받아 분해하고 engineering roles 는 packet 기준으로 움직인다.

## 7. 예시 — "영상 업로드 서비스 구현해줘"

- 검출: `media_upload` → state `clarification_needed`.
- 질문(3): 업로드/조회 주체 · 공개 정책 · 노출 순서 (각 옵션 + 추천).
- implied 자동 보강: 처리 상태 · 실패/재시도 · 썸네일 fallback · 비공개 상태 · 노출 순서.
- acceptance: 처리상태 표시 · 업로드 실패 재시도 · 썸네일 대체 · 비공개 비노출 …
- non-goals: 결제/알림/그대로 클론은 범위 밖.

## 9. Acceptance checklist (이슈 "PM agents 추가" 종료 기준)

- [x] product-agent 계약 문서(`agents/product-agent/CLAUDE.md`) + planning-agent 와의 차이 명시.
- [x] product-manager = engineering 앞단 intake gate 로 정의(받아쓰기 아님).
- [x] 순수 코어 `ProductIntentPacket` / `DecisionQuestion` / `FeatureGap` / `FeatureFamily` / `ProductReadinessVerdict`.
- [x] feature family 8종 + implied/ask/recommended + 답변 resolve.
- [x] 질문 budget ≤3 · open-ended 금지 · 옵션+추천 · 안전 기본값 자동 채움.
- [x] "영상 업로드" → visibility/ordering/permission 질문 + processing/failure/thumbnail implied + acceptance + non-goals.
- [x] gate seam(`should_intercept`/`run_product_gate`) — 제품 요청만 가로채고 기존 engineering flow additive.
- [x] state 분기 clarification_needed / spec_ready / implementation_candidate / research_only.
- [x] handoff 에 acceptance / user decisions / implied / non-goals 포함, operator status 가 PM clarification ↔ handoff ready 구분.
- [x] forgekit `/pm-agent` 가 stub 문구 대신 intake gate 책임 표시.
- [x] 회귀: `test_product_intake.py` + `test_product_gate.py` + forgekit `/pm-agent` 테스트, 전체 sweep new regression 0.

## 10. 관련
- [`agents/product-agent/CLAUDE.md`](../agents/product-agent/CLAUDE.md) ·
  [`approval-matrix.md`](approval-matrix.md) · [`autonomy-policy.md`](autonomy-policy.md) ·
  [`forgekit-console.md`](forgekit-console.md)(/pm-agent surface)
