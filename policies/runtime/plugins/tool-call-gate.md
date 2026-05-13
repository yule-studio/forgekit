# Tool Call Gate (`tool-call-gate`)

## 도입 이유
F12 (#151) 의 5×5 autonomy × risk_class 매트릭스를 모든 tool 호출 (LLM
tool use / Discord post / GitHub write / vault write / shell) 진입 단계에서
강제한다. "auto-mode 인데 secret 노출 위험 호출이 그냥 통과" 같은 사고
방지.

## 동작
- kind: `guard`
- hooks_provided: `PREFLIGHT`
- hooks_consumed: 없음
- risk_class: `HIGH` — 차단 실패 시 비가역 호출 우회 가능
- autonomy_level: `supervised`
- `paste_guard_required: false` (자기는 PasteGuard 와 같은 가드 계층)

## 환경변수
- `YULE_TOOL_GATE_ENABLED` — `1` 시 활성 (default OFF, 도입 직후 시범)
- `YULE_TOOL_GATE_DEFAULT_AUTONOMY` — 호출에 명시되지 않을 시 사용할
  기본 autonomy (advisory / supervised / autonomous)

## 운영 가이드
- 끄는 방법: ENABLED=0 시 매트릭스 통과 처리. 단 HIGH risk 호출이 무방비
  실행될 수 있어 운영 환경에서는 끄지 말 것.
- autonomy 매트릭스: F12 정책 문서 참조. autonomous + HIGH 는 운영자 명시
  승인 없이 통과 불가.

## 관련 정책 / 이슈
- F12 (#151) tool call gate 도입
- `policies/runtime/agents/engineering-agent/self-improvement-flow.md`
- `policies/runtime/common/safety.md`
