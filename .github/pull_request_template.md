<!-- 워크플로: docs/workflow.md · 리뷰/QA: docs/review-and-qa.md · 출처: docs/ai-attribution.md -->
<!-- 이 템플릿은 레포 기존 PR 템플릿 섹션(관련 이슈/과제 내용/스크린샷/레퍼런스)을 유지하면서
     AI-assisted workflow 섹션(actor/AI agent/리뷰/머지 체크)을 더한 것이다. -->

## 📌 관련 이슈
<!-- 관련 이슈 번호(#000). 머지와 함께 닫으려면 `Closes #000` -->

## ✨ 과제 내용
<!-- 무엇을 · 왜. 변경 범위(Scope)와 의도 -->

## 📸 스크린샷(선택)
<!-- 필요하면 첨부 -->

## 📚 레퍼런스 (선택)
<!-- 참고 자료 / 근거 -->

---

## Actor type
<!-- human / ai-assisted -->

## AI agent used
<!-- AI 작업이면: claude-code / codex-cli / gemini-cli / opencode / aider. 사람 단독이면: none -->

## Non-goals
<!-- 이 PR 이 의도적으로 다루지 않는 것 -->
-

## Test / QA result
<!-- 실행한 테스트와 결과. 새 회귀 라인 위치 -->
-

## AI review checklist
- [ ] AI Review 수행됨 (정확성 / 회귀 / 과설계 / 테스트 / 보안)
- [ ] AI 지적 반영 or 미반영 사유 기록
- [ ] AI 는 자기 PR 을 스스로 approve 하지 않음

## Human review checklist
- [ ] 사람 reviewer approve ≥ 1
- [ ] 의도 / 범위 / 리스크가 이슈·마일스톤과 일치
- [ ] AI Review 지적이 적절히 처리됨

## Merge checklist
- [ ] linked issue 완료 조건 충족
- [ ] QA 통과 (새 회귀 라인 포함)
- [ ] 마일스톤 연결됨
- [ ] 커밋 형식 (gitmoji + 3섹션) + AI-* trailer 정상 (docs/commits.md)
- [ ] **최종 머지는 사람 owner 가 수행** (AI self-merge 금지)
