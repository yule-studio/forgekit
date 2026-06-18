# per-provider usage breakdown — evidence (WT3)

`/usage` 가 today total 을 넘어 **provider / model / mode 단위 + live vs estimate 분리**로 읽히는 실측.

| 파일 | 무엇 |
| --- | --- |
| `breakdown.txt` | operator 라인(by provider/model/mode, live/est 분리) |
| `breakdown.json` | 기계 재사용용 |

## 정직성
- live 와 estimate 는 **합산 안 함**(`live N / est M [basis]`). basis = live / estimate / live+estimate / unknown.
- in/out/total 토큰 dimension 별 집계. unsupported provider 는 estimate(콘솔 live-submit 미연결) 로 정직.
- 코드 SSoT: `usage/breakdown.py`. `/usage` 가 rollup 요약 + 이 breakdown 을 함께 출력.
