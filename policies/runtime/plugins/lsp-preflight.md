# LSP-style Preflight (`lsp-preflight`)

## 도입 이유
역할이 코드 변경을 시작하기 전에 정적 분석 (mypy / eslint / ruff / 등)
runner 를 한 번 돌려 명백한 타입 / lint / 미사용 import 같은 저비용
오류를 사전에 잡는다. LLM 의 비싼 reasoning 사이클 전에 차단.

## 동작
- kind: `guard`
- hooks_provided: `PREFLIGHT`
- hooks_consumed: 없음
- risk_class: `MEDIUM` — 잘못된 차단이 작업 정지로 이어질 수 있음
- autonomy_level: `advisory`

## 환경변수
- `YULE_LSP_PREFLIGHT_ENABLED` — `1` 시 동작 (default OFF)
- `YULE_LSP_RUNNERS` — comma-separated runner id (예: `mypy,eslint`)
- `YULE_LSP_TIMEOUT_SECONDS` — 단일 runner timeout (default 30s)

## 운영 가이드
- 끄는 방법: ENABLED 를 비워두면 비활성. preflight 가 통과로 처리.
- 켜는 방법: 위 3 변수 셋업 + runner CLI 가 PATH 에 있어야 함.
- timeout 발생 시 작업은 진행 (advisory) 하되 경고를 hookify ledger 에 기록.

## 관련 정책 / 이슈
- F9 (#150) 정적 분석 도입
- `policies/runtime/agents/engineering-agent/testing.md`
