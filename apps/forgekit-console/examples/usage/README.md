# WT2 token-ledger — evidence

토큰 사용량을 operator 가 볼 수 있는 **append-only JSONL ledger(정본)** + rollup + budget alert.

| 파일 | 무엇 |
| --- | --- |
| `usage-ledger.jsonl` | 정본 — 1 submit = 1 line (ts/session/mode/provider/model/category/in·out·total/usage_basis/throttled) |
| `summary.txt` | operator 한눈 숫자(today/provider/mode/live·estimate/top) |
| `summary.md` | 설명 가능한 구조(관측/많이 쓴 곳/이상 징후) |
| `summary.json` | 기계 재사용용 rollup |

## 정직성
- **live vs estimate 분리** — 합치지 않음(report 의 live/estimate 별도). ollama openai transport 는
  usage 블록을 안 줘서 `usage_basis=estimate`(길이 기반). gemini 등 usage 반환 provider 는 `live`.
  **fake-live 없음.**
- cost_usd 는 price proxy 없으면 `null`(정직).

## teeth (WT1 gate 연결)
- `/usage` 가 today rollup 표면 + report 파일 생성(`runs/forgekit/usage/`).
- budget(`config.daily_token_budget`) 임계 70/85/100% crossing → **console + operator inbox 2 surface** 알림.
- ledger 의 today 합계가 WT1 submit gate 의 UsageSnapshot 으로 들어가 **budget posture throttle 가 실제 발화**
  (provider reserve floor 초과 시 submit held, provider 미호출 — 실측 검증).
