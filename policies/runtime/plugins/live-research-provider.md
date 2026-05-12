# Live Research Provider (`live-research-provider`)

## 도입 이유
F5 (#148) 의 live research fetcher. 부서별 collector 가 정해진 host
allow-list 안에서만 외부 docs / RSS / API 를 호출하도록 제한. F13 의
자동 자료 수집 파이프라인 (digest / dispatch) 의 입력 사이드.

## 동작
- kind: `delivery`
- hooks_provided: `OUTBOUND_VAULT`
- hooks_consumed: `PREFLIGHT`
- risk_class: `MEDIUM` — 외부 호출 rate / 차단 host 위반 위험
- autonomy_level: `advisory`

## 환경변수
- `YULE_RESEARCH_LIVE_ENABLED` — `1` 시 활성 (default OFF)
- `YULE_RESEARCH_LIVE_ALLOW_HOSTS` — comma-separated host (강제 화이트리스트)
- `YULE_RESEARCH_LIVE_DISABLE_HOSTS` — 일시 차단 host (운영자 incident 대응)
- `YULE_RESEARCH_LIVE_KINDS` — fetch 허용 종류 (docs / rss / api / blog)
- `YULE_RESEARCH_LIVE_RATE_LIMIT_PER_SEC` — host 별 호출 상한

## 운영 가이드
- 끄는 방법: ENABLED=0 시 모든 collector 가 캐시된 fixture 만 사용 (안전 모드).
- allow-host 미설정 시 모든 호출 차단 (fail-safe).
- vault 작성 시 PasteGuard 가 outbound 페이로드 마스킹.

## 관련 정책 / 이슈
- F5 (#148) live research provider
- F13 부서 자동 자료 수집 (DispatchPlan)
- `policies/runtime/agents/engineering-agent/scheduled-automation.md`
