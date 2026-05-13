# Hookify (`hookify`)

## 도입 이유
모든 작업의 lifecycle (preflight / completion / postmortem) 에 공통
관측·기록·중단 지점을 박는다. 각 역할이 "비슷한 mistake 를 두 번 반복"
하지 않도록 mistake ledger 에 기록·조회·사전경고를 자동화.

## 동작
- kind: `learning`
- hooks_provided: `PREFLIGHT`, `COMPLETION`, `POSTMORTEM`
- hooks_consumed: `COMPLETION` (다른 플러그인의 완료 시점도 기록)
- risk_class: `MEDIUM` — 잘못된 mistake 기록은 운영 판단 흐림
- autonomy_level: `advisory` — 차단은 안 하고 경고/기록만

## 환경변수
- `YULE_CACHE_DB_PATH` — mistake ledger SQLite DB 경로 (없으면 default)

## 운영 가이드
- 끄는 방법: 끌 수 있지만 권장 안 함. mistake ledger 가 비면 같은 실수
  반복 시 사전 차단 신호가 없다.
- 정리: ledger 가 너무 커지면 cycle 별 archive (관련 운영 SOP 미정 — 도입
  대기).

## 관련 정책 / 이슈
- F2 Hookify 도입 commit (pre-F11) — F11 (#102) 에서 manifest 화
- `policies/runtime/agents/engineering-agent/self-improvement-flow.md`
