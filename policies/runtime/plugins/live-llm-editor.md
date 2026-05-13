# Live LLM Editor (`live-llm-editor`)

## 도입 이유
F4 (#148) 의 live coding executor. 운영자가 명시적으로 활성화하면
claude-cli 가 코드 변경을 실제로 디스크에 적용한다. 도입 직후 시범 단계라
default OFF + provider 화이트리스트 (claude-cli 전용) 로 잠가둔다.

## 동작
- kind: `delivery`
- hooks_provided: `OUTBOUND_LLM`, `COMPLETION`
- hooks_consumed: `PREFLIGHT`
- risk_class: `HIGH` — 실제 디스크 write
- autonomy_level: `supervised`

## 환경변수
- `YULE_LIVE_EDITOR_ENABLED` — `1` 시 활성 (default OFF)
- `YULE_LIVE_EDITOR_PROVIDER` — 허용 provider id (현재 `claude-cli` 만)
- `YULE_LIVE_EDITOR_MODEL` — provider 별 모델 식별자
- `YULE_LIVE_EDITOR_MAX_RETRIES` — retry 상한 (default 3)

## 운영 가이드
- 끄는 방법: ENABLED=0 시 모든 코딩 요청이 propose-only (draft PR) 로 fallback.
- Anthropic/OpenAI provider 직접 호출은 stub 으로 막혀 있음 (의도적 차단).
- F12 tool-call-gate 통과 + 운영자 명시 승인 (Discord 답변 또는 PR 라벨)
  이후만 실제 적용.

## 관련 정책 / 이슈
- F4 (#148) live editor
- F11 (#102) PluginManifest 화
- `policies/runtime/agents/engineering-agent/version-control.md`
