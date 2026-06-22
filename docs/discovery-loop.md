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

## 누적 (ledger-backed accumulation)
한 번의 sweep 은 ephemeral — 두 번 돌리면 같은 아이디어가 매번 "새 것"으로 다시 뜬다.
**discovery ledger**(`discovery/ledger.py`)가 이걸 개인 비서형 메모리로 만든다: 표면화된 모든
아이디어를 problem 텍스트 fingerprint 로 dedup 해 **영속**(`state_dir()/discovery_ledger.json`,
vault 아님 — 별도 evidence 트랙)하고, 각 아이디어에 lifecycle status 와 누적 근거를 붙인다.

```
sweep → ledger.record_sweep(now)         # 새 fingerprint=new, 기존=seen(seen_count++)
  → DiscoveryLedger { fingerprint: LedgerIdea(status, first_seen, last_seen, seen_count, ...) }
  → /discovery digest: 총 N · 결정대기 · promoted · saved · parked + 이번 sweep 새/다시
```
- lifecycle: `new → seen`(다시 관측) → operator 결정 `promoted`(PM handoff) / `saved`(vault note) /
  `parked`(보류). 결정된 아이디어는 다시 sweep 돼도 **new/pending 으로 부활하지 않음**(last_seen 만 갱신).
- `pending()` = 결정 대기 큐(score 내림차순, 결정적). `/discovery pending` 의 번호 = promote/save/park
  의 `<n>` (re-sweep drift 없이 영속 큐 기준).
- 결정 시 `LedgerIdea.rebuild_brief()` 로 저장된 brief dict 를 IdeaBrief 로 복원 → 재수집 없이 승격/영속.
- best-effort I/O: store 실패해도 sweep 은 안 죽고 in-memory view 반환.

## current live vs planned
| 단계 | 상태 | surface | evidence |
| --- | --- | --- | --- |
| free-first 수집 (repo-local/HN/Reddit/GitHub/RSS) | **live** (network=injectable fetcher) | `/sources` | `examples/sources/` |
| **operator-tunable 수집 토픽** (HN query/subreddits/GitHub query/RSS) | **live** | config `discovery` 블록 → `registry_from_config` | `test_discovery_ledger` |
| 수집 → idea-discovery 한 패스 연결 | **live** | `/discovery` | `examples/discovery/sweep-digest.json` |
| **아이디어 누적/dedup/lifecycle (ledger)** | **live** | `/discovery` · `/discovery pending` | `examples/discovery/ledger-accumulation.json` |
| operator digest (왜/다음 질문) | **live** | `/discovery` | `sweep-digest.json` `entries[].why/next_questions` |
| brief → PM handoff packet | **live** (제안 only) | `/discovery promote <n>` | `test_discovery_ledger` |
| brief → authored vault note (retrieval-friendly) | **live** (연결 시) | `/discovery save <n>` | `examples/discovery/idea-brief-note.md` |
| 아이디어 보류 | **live** | `/discovery park <n>` | `test_discovery_ledger` |
| YouTube / Instagram / paid Google | **planned** (collect()→[] 항상) | `/sources` | `registry.py` status=planned |

## collector usability (operator-tunable)
수집원의 기본 쿼리는 operator 관심사로 바꿀 수 있다. config 의 `discovery` 블록:
```json
{ "discovery": {
    "hackernews_query": "AI agents OR devtools",
    "subreddits": ["SaaS", "startups", "selfhosted"],
    "github_query": "tui+dashboard",
    "rss_feeds": [["lobsters", "https://lobste.rs/rss"]] } }
```
`registry_from_config(repo_root, config)` 가 이걸 읽어 수집원을 구성한다. 빈 쿼리/빈 리스트는 해당
수집원을 **그냥 끈다**(fake source 안 만듦). `discovery` 블록이 없으면 안전한 기본값(`DEFAULT_HN_QUERY`
등)으로 떨어진다. 여러 subreddit 은 각각 별도 collector 가 된다.

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
1. `/discovery` — 수집 sweep + **누적 digest**: 총 추적/결정대기/promoted/saved/parked + 이번 sweep
   의 새 아이디어(왜 올라왔는지·다음 질문) + vault 연결 상태 힌트.
2. `/discovery pending` — 결정 대기 아이디어 큐(score 순, 번호 부여).
3. `/discovery promote <n>` — n 번을 PM handoff packet 으로 승격(제안) → ledger status `promoted`.
4. `/discovery save <n>` — 연결된 Nexus vault 에 authored idea-brief note 영속 → status `saved`(note_path 기록).
5. `/discovery park <n>` — 보류 → status `parked`(다시 안 올라옴).

반복 실행하면 ledger 가 쌓여 "지난번엔 새거 3개였는데 이번엔 0개 새거·5개 다시 관측, 2개는 이미 promoted"
처럼 누적 상태를 보여준다 — 개인 비서형 수집·정리·아이디어화 루프.

## 재생성/검증
- `python -m unittest tests.forgekit.test_discovery_ledger` (누적·dedup·lifecycle·config 토픽·surface)
- `python -m unittest tests.forgekit.test_discovery_sweep` (루프·digest·note·승격 코어)
- `python -m unittest tests.forgekit.test_discovery_e2e` (전체 discovery 프로그램 체인)

## planned seam 붙이는 법 (YouTube/Google/Instagram)
`registry.default_registry` 의 planned 블록은 `PlannedCollector(_planned_spec(...))` 로 등록돼
있고 `collect()` 가 항상 빈 결과다. 실제 연결 시: (1) `collectors.py` 에 해당 어댑터(injectable
fetcher) 추가 → (2) spec `status=STATUS_LIVE` + cost/legal 갱신 → (3) 회귀에 fake fetcher 파싱
테스트 추가. 그 전까지는 planned 로 두어 digest 에 "미연결" 로 정직하게 보인다.
