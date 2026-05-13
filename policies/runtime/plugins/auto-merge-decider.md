# Auto-merge Decider (`auto-merge-decider`)

## 도입 이유
PR 이 CI 통과 + 충분한 리뷰 + 작업 분리 정책 통과 시 자동 머지 여부를
결정. 운영자가 모든 PR 을 수동 머지하지 않아도 되지만, 매트릭스 조건을
만족 못 하면 wait/manual 로 fallback.

## 동작
- kind: `delivery`
- hooks_provided: `OUTBOUND_GITHUB`
- hooks_consumed: 없음
- risk_class: `HIGH` — 실제 머지 (비가역)
- autonomy_level: `supervised`

## 환경변수
- `YULE_AUTOMERGE_CYCLE` — cron-style 간격 (예: `*/10`) 또는 `off`

## 운영 가이드
- 끄는 방법: env=`off` 시 자동 머지 disable. 운영자가 직접 `gh pr merge`.
- 결정 로직: CI 통과 + 작업 분리 (≥3 commits or label 명시) + paste-guard
  통과 + 리뷰 자율성 매트릭스. 미충족 시 wait.
- main 브랜치 force push 류는 절대 호출 안 함 (별도 거부 룰).

## 관련 정책 / 이슈
- F13+ auto-merge gate
- `policies/runtime/agents/engineering-agent/version-control.md`
