# WT2 token-ledger — evidence

토큰 사용량을 operator 가 볼 수 있는 **append-only JSONL ledger(정본)** + rollup + budget alert.

| 파일 | 무엇 |
| --- | --- |
| `usage-ledger.jsonl` | 정본 — 1 submit = 1 line (ts/session/mode/provider/model/category/in·out·total/usage_basis/throttled) |
| `summary.txt` | operator 한눈 숫자(today/provider/mode/live·estimate/top) |
| `summary.md` | 설명 가능한 구조(관측/많이 쓴 곳/이상 징후) |
| `summary.json` | 기계 재사용용 rollup |

## 정직성
- **live vs estimate 분리** — 합치지 않음(report 의 live/estimate 별도).
- **vendor-native usage (#239, WT1)**: openai-compatible transport 가 응답의 `usage` 블록
  (`prompt_tokens`/`completion_tokens`/`total_tokens`)을 파싱 → **`usage_basis=live`**.
  ollama `/v1/chat/completions` 가 실제로 usage 를 주므로 zero-config 로 live 기록됨
  (실측: `native-usage-live/` 참조). usage 블록이 없거나 malformed 면 **honest estimate 로 degrade**
  (길이 기반) — 둘은 절대 합산하지 않음. CLI provider(claude/codex)는 콘솔 live-submit 미연결이라 항상 estimate 대상조차 아님. **fake-live 없음.**
- cost_usd 는 price proxy 없으면 `null`(정직).

## teeth (WT1 gate 연결)
- `/usage` 가 today rollup 표면 + report 파일 생성(`runs/forgekit/usage/`).
- budget(`config.daily_token_budget`) 임계 70/85/100% crossing → **console + operator inbox 2 surface** 알림.
- ledger 의 today 합계가 WT1 submit gate 의 UsageSnapshot 으로 들어가 **budget posture throttle 가 실제 발화**
  (provider reserve floor 초과 시 submit held, provider 미호출 — 실측 검증).
