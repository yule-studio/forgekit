# WT1 runtime-teeth — evidence

EffectivePolicy 가 **표시만** 되던 것을 free-text submit 경로에 **실제 강제**한다.
seam: `chat/policy_gate.py`(enforcement) + `chat/service.py`(gate 적용 + usage) — app.py 로직 몰지 않음.

## gate decisions (`gate-decisions.json`)
| mode | gate | routing_target | 효과 |
| --- | --- | --- | --- |
| Interactive | allow | ollama | provider 호출, mode 의 routing target 으로 라우팅 |
| Approval-wait | **hold** | — | provider **미호출**(hold-all), held result |
| Cost-save (over budget) | **throttle** | — | budget posture 진입 → throttle, provider 미호출 |

## 실측 live 검증 (real ollama)
- approval-wait → `held / policy_held`, transport.calls=0 (provider 미호출).
- interactive → `live`, `usage_basis=estimate`, tokens 기록, receipt:
  `↳ Ollama · gemma3:latest · live · ok · mode=Interactive · 4tok(estimate)`

## honest boundary
- usage_basis 는 **estimate**(길이 기반) — ollama openai transport 가 usage 블록을 안 줌. **fake-live 아님**.
  실제 vendor-native token accounting 은 후속(provider usage 파싱).
- budget snapshot 은 WT1 에선 app 이 빈 값(spent/budget 0) 제공 → throttle 미발화. **gate 자체는 실제 동작**
  (위 표의 over-budget snapshot 으로 throttle 증명). 실 usage 주입은 **WT2 token-ledger** 에서.
- CLI provider(claude/codex)는 routing 돼도 `unsupported_in_console` 로 정직 surface(가짜 성공 없음).
