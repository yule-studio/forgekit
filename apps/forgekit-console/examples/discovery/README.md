# discovery + self-improve + red/blue — evidence bundle

forgekit 의 discovery 프로그램(무료 우선 수집 → 아이디어 → 자체 개선 → 보안 드릴)이
실제로 닫히고, 위험한 것은 전부 gated/planned 라는 evidence. 재생성/검증:
`python -m unittest tests.forgekit.test_discovery_e2e tests.forgekit.test_discovery_sweep`.
discovery 루프 SSoT: [`docs/discovery-loop.md`](../../../../docs/discovery-loop.md).

| 파일 | 단계 | 무엇 |
| --- | --- | --- |
| `../sources/registry.json` | sources(WT2) | live(무료 우선) vs planned(YouTube/IG/Google — fake 아님) |
| `idea-brief.json` | idea-discovery(WT3) | ReferenceBundle + CompetitorGapMap + IdeaBrief + self-improve 신호 분리 |
| `sweep-digest.json` | discovery loop | 수집→brief→operator digest 한 패스. `entries[].why`(왜 올라왔는지) + `entries[].next_questions`(다음 질문) |
| `ledger-accumulation.json` | 누적(ledger) | 2회 sweep: sweep1 new 3 → sweep2 new 0/dedup 3(seen_count++) → promote/park lifecycle 영속 |
| `idea-brief-note.md` | knowledge plane | brief → retrieval-friendly authored vault note (author/role/color/cssclass + 5 섹션) |
| `../selfimprove/scan.json` | self-improvement(WT4) | repo gap → risk-classified 패킷(safe만 자동) |
| `../security/drill-plan.json` | red/blue(WT5) | 내 자산 plan-only 드릴(dry-run, 승인 필요) |
| `../security/blocked-public.json` | red/blue(WT5) | 공용 대상 → BLOCKED(공격 흐름 거부) |
| `../security/k3s-isolation-runbook.md` | red/blue(WT5) | 격리 k3s 환경 runbook |

## 정직성 요약 (직접 확인용)
- 무료/저비용 소스만 live, YouTube/Instagram/Google 은 **planned**(`collect()` 항상 빈 결과).
- video-watch 는 transcript/notes 만 live 요약, bare 링크는 `reference_only`(크롤 없음).
- self-improvement 는 관측/분류/패킷화만 자동, mutation 은 승인/runbook.
- red/blue 는 내 자산 allowlist + plan-first, active 는 operator 승인 필수, 공용/3rd-party 거부.
