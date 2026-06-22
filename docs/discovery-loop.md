# Discovery loop (Nexus knowledge plane)

free-first 수집 → idea brief → operator digest → PM packet/vault note 로 이어지는
discovery 루프의 SSoT. 코드: `apps/forgekit-console/src/forgekit_console/discovery/`
(`sweep.py` = 이 루프의 배선). 수집원 레지스트리/collector 는 `packages/nexus/src/nexus/sources/`,
authored vault note 는 `packages/nexus/src/nexus/vault/`.

## 한 패스 (run_discovery_sweep)
```
default_registry(repo_root, fetcher, rss_feeds)   # 수집원 레지스트리 (WT2)
  → collect_all(limit_per)                         # LIVE 무료 우선만, planned 은 빈 결과
  → flatten + operator extra_signals
  → run_idea_discovery(items)                      # signals → gap map + briefs (WT3)
  → DiscoveryDigest                                # 왜 올라왔는지 + 다음에 물을 질문
```
결과 `DiscoverySweep` = `result`(DiscoveryResult) + `digest`(DiscoveryDigest) +
`source_rows`(레지스트리 health). 순수·결정적 — `fetcher` 미지정 시 network collector 는
정직하게 빈 결과라 CI 에서 repo-local 만으로 굴러간다.

## current live vs planned
| 단계 | 상태 | surface | evidence |
| --- | --- | --- | --- |
| free-first 수집 (repo-local/HN/Reddit/GitHub/RSS) | **live** (network=injectable fetcher) | `/sources` | `examples/sources/` |
| 수집 → idea-discovery 한 패스 연결 | **live** (이번 루프) | `/discovery` | `examples/discovery/sweep-digest.json` |
| operator digest (왜/다음 질문) | **live** | `/discovery` | `sweep-digest.json` `entries[].why/next_questions` |
| brief → PM handoff packet | **live** (제안 only) | `/discovery promote <n>` | `test_discovery_sweep` |
| brief → authored vault note (retrieval-friendly) | **live** (연결 시) | `/discovery save <n>` | `examples/discovery/idea-brief-note.md` |
| YouTube / Instagram / paid Google | **planned** (collect()→[] 항상) | `/sources` | `registry.py` status=planned |

## honesty rails (이 루프에서 강제)
- **planned 수집원은 절대 fake-live 아님** — `collect()` 가 항상 `[]`, digest 에 정직 표기.
- **색은 retrieval 핵심이 아님** — authored note 의 `cssclasses`/`agent_color` 는 누가 썼는지
  *시각 구분* 용 보조 메타일 뿐, 검색/스코어 로직에 쓰이지 않는다. 실제 색 메커니즘은 사용자가
  vault snippet 을 깔 때만 작동(`vault_css_snippet`), Obsidian 이 임의 텍스트를 칠한다고 속이지 않음.
- **vault note 는 hollow 금지** — `brief_to_authored_note` 는 표준 frontmatter(tags/related) +
  5 섹션(핵심 요약/문제·근거/차별화 가설/다음 실험/참고) 을 채운다. `persist_brief` 는 `00-inbox/
  discovery` (raw intake) 에 쓰고 status=draft — curated 라고 위조하지 않는다(curated 승격은
  eval gate 통과 후 별도).
- **승격은 제안일 뿐** — `/discovery promote` 는 PM→gateway→tech-lead handoff packet 을 만들고
  멈춘다. 실행은 승인 게이트 통과 후.
- **vault 미연결 시 정직 실패** — `/discovery save` 는 `FORGEKIT_NEXUS_ROOT`/config 없으면
  fake-write 없이 에러를 돌려준다.

## operator 흐름
1. `/discovery` — 수집 sweep + digest. 각 top brief 의 "왜 올라왔는지(신호 kind·출처·score)" 와
   "다음에 물어볼 질문(target_user/경쟁 검증/pricing/실험 실행)" 을 본다.
2. `/discovery promote <n>` — n 번 brief 를 PM handoff packet 으로 승격(제안).
3. `/discovery save <n>` — 연결된 Nexus vault 에 authored idea-brief note 영속(retrieval-friendly).

## 재생성/검증
- `python -m unittest tests.forgekit.test_discovery_sweep` (루프·digest·note·승격·surface)
- `python -m unittest tests.forgekit.test_discovery_e2e` (전체 discovery 프로그램 체인)

## planned seam 붙이는 법 (YouTube/Google/Instagram)
`registry.default_registry` 의 planned 블록은 `PlannedCollector(_planned_spec(...))` 로 등록돼
있고 `collect()` 가 항상 빈 결과다. 실제 연결 시: (1) `collectors.py` 에 해당 어댑터(injectable
fetcher) 추가 → (2) spec `status=STATUS_LIVE` + cost/legal 갱신 → (3) 회귀에 fake fetcher 파싱
테스트 추가. 그 전까지는 planned 로 두어 digest 에 "미연결" 로 정직하게 보인다.
