# loadtest-runner

> 이 레포의 **runtime / memory / agent backend** 에 부하를 거는 테스트 러너.
> 기본은 **MOCK / stub endpoint** 대상이며, 실제/유료 LLM API 를 무제한
> 호출하지 않는다.

## 부하 대상 (중요)

- 부하 대상은 **Agent Town / Agent Lab UI 가 아니다.** 부하 대상은 이
  레포의 runtime / memory / agent backend 다.
- 기본 대상은 **MOCK / stub endpoint** (`__ENV.BASE_URL`, 기본
  `http://localhost:8787`). mock 서버는 **기본으로 띄워지지 않는다** — 사용자가
  직접 띄워야 한다.
- **실제/유료 LLM API 무제한 호출 금지.** LLM gateway 부하는 반드시 stub /
  rate-limited mock 으로만 측정한다.

## 실행 방법 (k6)

[k6](https://k6.io) 설치 후:

```bash
# 1) mock 서버를 먼저 띄운다 (별도, 기본으로 자동 기동되지 않음).
#    예: BASE_URL 이 가리키는 곳에 아래 placeholder 엔드포인트가 있어야 한다.
#    GET  /mock/memory/search?q=...
#    GET  /mock/runtime/status
#    POST /mock/discord/inbound

# 2) 시나리오 실행
export BASE_URL=http://localhost:8787

k6 run apps/loadtest-runner/k6/memory-search.js
k6 run apps/loadtest-runner/k6/runtime-status.js
k6 run apps/loadtest-runner/k6/discord-burst.js
```

리포트는 `apps/loadtest-runner/reports/` 에 떨어뜨린다 (예:
`k6 run --summary-export apps/loadtest-runner/reports/memory-search.json ...`).

### Locust (선택)

`locust/locustfile.py` 는 memory-search 를 미러링한 **선택적** stub 이다.

```bash
# 선택: locust UI
locust -f apps/loadtest-runner/locust/locustfile.py --host "$BASE_URL"
```

## 측정 지표

| 지표 | 시나리오 | 대상 엔드포인트(MOCK) |
| --- | --- | --- |
| **memory search latency** | `k6/memory-search.js` | `GET /mock/memory/search` |
| **runtime status latency** | `k6/runtime-status.js` | `GET /mock/runtime/status` |
| **job queue throughput** | (runtime-status 와 묶어 관찰) | `GET /mock/runtime/status` |
| **Discord message burst handling** | `k6/discord-burst.js` | `POST /mock/discord/inbound` |
| **LLM gateway 요청 제한 동작** | (stub/rate-limit mock 으로만) | `GET /mock/runtime/status` 등 |

> LLM gateway 지표는 항상 stub/rate-limited mock 으로만 측정한다. 실제 토큰을
> 태우는 경로로 부하를 걸지 않는다.
