# forgekit-provider-connect

> ForgeKit **provider onboarding / control-plane connect** 계층 — `gh auth` 처럼 "연결·검증·저장"
> 경험. provider policy/routing core(`forgekit-provider`) 위에 onboarding 을 얹는다.

[`docs/control-plane-architecture.md`](../../docs/control-plane-architecture.md) §5.1 의 P0 구현.

## 모듈
- `status` — `ConnectionStatus` + 상태(connected/missing_key/missing_cli_auth/daemon_down/
  model_missing/unsupported_in_console/blocked/not_linked/unknown). brain vs live transport 분리.
- `probe` — `ConnectionProbe`(injectable IO) + `DefaultProbe`(CLI 로그인 감지 heuristic / API 키 /
  ollama 데몬·모델). 검증 못 하면 None/missing — **connected 거짓 금지**.
- `diagnose` — provider 유형별(CLI attach·API key·local daemon) 정직한 진단(pure).
- `wizard` — `/setup` bootstrap: `assess`(연결 점검 + 추천 preset) / `apply_recommended`(저장+검증).
- `surface` — 콘솔용 순수 line builder(`/setup`·`/provider connect|test|recommended`).

## 정직 원칙
- claude/codex = 기존 CLI 세션 **attach**(새 OAuth 발급 안 함), routing/brain participant,
  console live-submit `unsupported_in_console`.
- gemini = API key 검사 → live. ollama = 데몬+모델 검사 → live.
- "설치 안 됨" 을 green 으로 보이게 하지 않는다.

## 의존
`forgekit-provider` · `forgekit-config` 만. `apps/*` import 금지.
