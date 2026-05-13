# PasteGuard (`paste-guard`)

## 도입 이유
LLM/Discord/GitHub/Vault 로 나가는 모든 payload 의 secret (.env / API 키 /
GitHub App private key) 과 PII (이메일/전화/이름) 를 outbound 직전에
마스킹한다. 운영자가 실수로 secret 을 prompt 에 붙여 넣어도, runtime 의
어떤 경로에서도 raw 로 외부에 흘러나가지 않는 마지막 가드.

## 동작
- kind: `guard`
- hooks_provided: `OUTBOUND_LLM`, `OUTBOUND_DISCORD`, `OUTBOUND_GITHUB`,
  `OUTBOUND_VAULT`
- hooks_consumed: 없음 (가드 전용)
- risk_class: `HIGH` — 차단 실패 시 secret 누설
- autonomy_level: `supervised` — 마스킹 룰 변경은 운영자 검토 필요
- `paste_guard_required: false` (자기 자신은 자기를 호출하지 않음)

## 환경변수
없음. 마스킹 룰은 코드에 박혀 있고 (`src/yule_orchestrator/agents/security/paste_guard.py`),
필요하면 runtime fixture 로 룰을 주입한다.

## 운영 가이드
- 끄는 방법: 절대 끄지 말 것. 비활성화 시 outbound hook 체인이 secret
  마스킹 없이 통과한다. 비상시는 해당 outbound 채널 (Discord/GitHub) 자체를
  내리는 게 안전.
- 디버그: 마스킹 후 payload 가 비정상으로 줄어들면 룰 정규식 false positive
  의심. `tests/security/test_paste_guard*` 회귀 추가.

## 관련 정책 / 이슈
- F11 PluginManifest 도입 (#102) — `paste_guard_required` 강제 필드
- `policies/runtime/common/safety.md`
