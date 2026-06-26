# Review & QA

> PR 이후 머지까지의 게이트: AI Review → Human Code Review → QA → Merge readiness.
> 전체 흐름은 [workflow.md](workflow.md).

## 1. AI Review

PR 이 열리면 **먼저** AI Review 를 거친다.

- AI 에이전트(Claude Code 등)가 diff 를 검토하고 코멘트로 지적 사항을 남긴다.
- 점검 대상: 정확성 버그, 회귀 위험, 과설계 / 중복, 테스트 누락, 보안·신뢰 경계.
- 작성자는 각 지적을 **반영하거나, 반영하지 않는 사유** 를 남긴다.
- AI Review 는 **자동 게이트가 아니다** — 통과 자체가 머지 승인을 의미하지 않는다.

## 2. Human Code Review

AI Review 위에 **사람 리뷰** 가 올라간다. 둘은 별개 단계다.

- 최소 **1명의 사람** approve 가 필요하다.
- 리뷰어는 의도·범위·리스크가 이슈/마일스톤과 맞는지, AI Review 지적이 적절히 처리됐는지 확인한다.
- 사람 리뷰는 AI Review 를 **대체하지 않고 보완** 한다.

## 3. QA

**머지 전 필수.**

- [ ] 기존 테스트 전부 통과
- [ ] 새 기능 / 버그 수정에 **새 회귀 테스트 라인** 추가
- [ ] 이슈의 완료 조건(Completion criteria) 충족
- [ ] 문서 변경이면 링크·앵커 깨짐 없음
- [ ] 리스크에 비례한 수동 확인 (필요 시)

> 새 기능인데 새 회귀 라인이 비어 있으면 QA 미통과로 본다.

## 4. Merge readiness

아래가 **모두** 충족돼야 머지 가능:

- [ ] linked issue 존재 + 완료 조건 충족
- [ ] AI Review 지적 반영 or 사유 기록
- [ ] 사람 approve ≥ 1
- [ ] QA 체크 통과
- [ ] 마일스톤 연결됨
- [ ] 커밋 형식 + AI trailer 정상 ([commits.md](commits.md))

## 5. AI 가 단독으로 할 수 없는 것

- **AI 는 자신의 PR 을 스스로 approve 할 수 없다.** 사람 approve 가 별도로 필요하다.
- **AI 는 최종 머지를 수행하지 않는다.** AI 는 변경을 준비할 수 있지만, **머지는 사람
  owner 가 수행** 한다. (운영자가 명시 인가한 좁은 예외 외 self-merge 금지.)
- **AI Review 가 사람 Code Review 를 대체할 수 없다.**
- **AI 는 릴리스를 단독 발행하지 않는다** ([release.md](release.md)).

> 요약: AI 는 *준비·검토·제안* 까지. *승인·머지·릴리스* 의 최종 권한은 사람에게 있다.
