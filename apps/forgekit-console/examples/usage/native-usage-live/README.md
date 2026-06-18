# vendor-native usage — live evidence (#239, WT1)

실측 캡처: openai-compatible transport 가 provider 응답의 `usage` 블록을 파싱해
`usage_basis=live` 로 기록되는 것을 보여준다. estimate 와 합산되지 않는다.

| 파일 | 무엇 |
| --- | --- |
| `usage-ledger.jsonl` | 2 row — live(ollama 실 응답) + estimate(usage 블록 없는 응답) |
| `summary.txt` / `summary.json` | rollup — live_tokens vs estimate_tokens **분리** + live_ratio |

## 어떻게 캡처했나 (재현)
```
SubmitService(transport=DefaultTransport(), config={}).submit("...")
```
- 로컬 ollama(`/v1/chat/completions`)가 `{"usage":{"prompt_tokens":...,"completion_tokens":...,"total_tokens":...}}`
  를 반환 → `chat/usage_parse.parse_openai_usage` 가 파싱 → `SubmitResult(usage_basis="live", total_tokens=24)`.
- 같은 코드로 usage 블록이 없는 응답은 `usage_basis="estimate"`(길이 기반)로 degrade.

## 정직 경계
- **live = 실제 provider 가 보고한 토큰**. 추정/날조 아님.
- ollama 는 auth 없이 live 가능. OpenAI/gemini(openai-compat)도 같은 파서로 live 가능하나
  본 evidence 는 zero-config ollama 로 캡처.
- CLI provider(claude/codex)는 콘솔 live-submit 자체가 미연결 → live/estimate 대상 아님(unsupported).
- 숫자는 캡처 시점 모델(`gemma3:latest`) 기준이며 프롬프트에 따라 달라진다.
