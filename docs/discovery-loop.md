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

## 24h bounded loop (host-driven accumulation)
한 번의 sweep 은 한 패스다. "24시간 동안 자료를 모은다"는 진짜 비서는 **bounded loop** 이
필요하다 — wall-clock budget 에 걸쳐 sweep 을 반복하고 매번 ledger 에 병합해 신호가
누적(dedup + freshness)되게 한다. driver 는 `discovery/loop.py`.

```
run_discovery_loop(repo_root, clock, budget, ...)   # clock = INJECTED 타임스탬프 시퀀스
  → 각 tick: discovery_loop_tick → run_discovery_sweep → ledger.record_sweep(now)
  → 중단: window-exhausted(window_hours 초과) | max-ticks | clock-exhausted
  → min_interval_minutes 보다 촘촘한 타임스탬프는 skip (소스 과다 호출 방지)
  → DiscoveryLoopReport { ticks[], new_total, seen_total, candidates[], stopped_reason }
```
- **clock 은 주입** — core 는 절대 sleep 하거나 시간을 위조하지 않는다. host(daemon /
  goal-scheduler)가 실제 wall-clock 으로 tick 을 공급한다. 테스트는 결정적 타임스탬프를 준다.
- `LoopBudget(window_hours=24, max_ticks=24, min_interval_minutes=30)` 가 기본 bound.
- offline-safe: fetcher 없으면 collector 가 정직하게 빈 결과 → repo-local 만으로 CI 에서 굴러감.
- evidence: `examples/discovery/loop-report.json` (4 tick·6h 간격, tick0 new 2 → tick1~3 dedup).
- **daemon seam(planned wiring)**: always-on runtime 이 `discovery_loop_tick` 을 자기 cadence 로
  호출하면 24h 윈도가 실제로 굴러간다. 이 wave 는 loop core + tick 을 닫고, serve 배선은
  autonomy lane(goal-scheduler)이 잇는다 — fake 24h 러너를 surface 에 만들지 않는다.

## freshness · promotion 기준 (ask-me-later)
누적만으로는 부족하다 — 어떤 아이디어를 **operator 에게 물어볼지** 기준이 필요하다.
`discovery/loop.py` 의 promotion policy(순수 함수, LedgerIdea 위):
- **corroboration** — `seen_count >= min_seen_count`(기본 2). 한 sweep 단발은 noise, 여러 sweep 에
  걸쳐 다시 관측돼야 신호. (대안: score 만 보면 단발 spike 가 operator 를 귀찮게 함 → 기각.)
- **score** — `score >= min_score`(기본 2.0).
- **freshness** — `last_seen` 이 `fresh_within_hours`(기본 36h) 내. 관심이 식은 건 제외(`stale_pending`).
  age 계산 불가(타임스탬프 파싱 실패)면 정직하게 **penalise 안 함**.
- `ask_candidates(ledger, now)` = 위 3 기준 통과 pending 을 교차 관측 많은 순으로. 각 후보는
  사람용 근거 문자열(`N회 교차 관측 · score X · 방금 관측`)을 단다.

## evidence track (idea brief 너머)
sweep 은 idea brief 만 내지 않는다 — **경쟁/gap map** 과 **forgekit self-improve 신호** 도 나온다.
raw 면 증발하므로 authored evidence note 로 영속해 구조적으로 누적한다(`sweep.py`):
- `gap_map_to_evidence_note` — 경쟁 지형 + 관측 gap → `kind: evidence`, `competitor-gap` 태그.
- `self_improve_to_note` — 자체 개선 신호 → `kind: improvement-signal`, handoff→tech-lead.
- `persist_evidence(sweep, vault_root)` → `{gap, self_improve}` 경로. 기록할 게 없으면 **None**
  (hollow note 금지), vault 미연결이면 정직 실패. 둘 다 `00-inbox/discovery`(raw intake, status draft).
- evidence: `examples/discovery/evidence-competitor-gap.md` · `evidence-self-improve.md`.

## 도입 효율 검토 (adoption-efficiency review)
"많이 모으기"가 목적이 아니라 "도입 가치 판단 가능한 근거 만들기"가 목적이다. 수집한 후보
(plugin/skill/collector/rule/tool/idea)는 `좋아 보인다`만으로 도입하지 않고 **도입 효율 검토**를
거친다. 코드: `discovery/adoption.py` (한 모듈, ponytail verdict 주석).

**6-class 분류** (`classify_candidate`): `signal_only` / `tool_candidate` / `idea_candidate` /
`competitor_signal` / `implementation_reference` / `risk_or_constraint`. 후보의 제목·문제 텍스트로
결정(boilerplate 가설 텍스트는 제외 — false competitor 방지).

**8축 검토** (`AdoptionReview`, `build_adoption_review`):
1 current pain · 2 expected benefit · 3 overlap(기존 capability 겹침; armory catalog signals 로 검사) ·
4 operational cost · 5 maintenance risk · 6 provider/runtime fit(provider-neutral) ·
7 governance/security impact · 8 adopt-now vs collect-first vs hold.

**disposition (세 결말만)** — fake adoption 금지:
- 기본 **collect-first**: 근거 누적만, **즉시 활성화 안 함**. Nexus vault 에 evidence note 로 영속.
- **hold**: `risk_or_constraint` 분류이거나 기존 capability 와 겹치면 보류(추적 대상).
- **adopt-now**: `build_adoption_review` 는 **절대 자동으로 주지 않는다**. 3축(PM/tech-lead/specialist)
  검토 후 operator 의 명시 결정(`resolve_review(adopt=True)`)으로만. 그래서 모든 후보가 만들 때
  collect-first/hold 로 나오고, adopt-now 는 사람 결정의 결과다.

**3축 검토 강제**: `build_adoption_review` 는 매 후보마다 **실제 `ConsultNote`**(decision_lane)를 만든다 —
`by_role=user-researcher → to_roles=[product-manager, tech-lead, <분류별 specialist>]` + 실질 question.
`validate_consult` 통과하는 진짜 artifact(빈 consult 위조 아님).

**adopted ≠ equipped** (Hephaistos/armory 연결): adopt-now 결정 후 `adoption_to_armory_candidate` 가
armory intake 게이트(`promote_candidate`)로 연결한다. **adopted** = 계약 검증 통과한 catalog spec.
**equipped** = `catalog.register_promoted`(resolver 가 실제로 고를 수 있게) — **별도 명시 단계, 여기서 안 함**.
raw 아이디어는 contract(summary/signals/when_to_use/unsafe_boundary/capability_note/commands) 가 없어
intake 가 **정직하게 reject**(fake available 방지) → specialist 가 채운 뒤 재시도.

evidence: `examples/discovery/adoption-review.md`(8축 authored note) · `adoption-packet.json`
(collect-first 검토 → 3축 결정 → armory intake 의 머신 리더블 패킷).

## current live vs planned
| 단계 | 상태 | surface | evidence |
| --- | --- | --- | --- |
| free-first 수집 (repo-local/HN/**GeekNews**/Reddit/GitHub/RSS) | **live** (network=injectable fetcher) | `/sources` | `examples/sources/`, `test_discovery_adoption` |
| **operator-tunable 수집 토픽** (HN query/subreddits/GitHub query/RSS/geeknews 토글) | **live** | config `discovery` 블록 → `registry_from_config` | `test_discovery_ledger`·`test_discovery_adoption` |
| 수집 → idea-discovery 한 패스 연결 | **live** | `/discovery` | `examples/discovery/sweep-digest.json` |
| **아이디어 누적/dedup/lifecycle (ledger)** | **live** | `/discovery` · `/discovery pending` | `examples/discovery/ledger-accumulation.json` |
| **24h bounded loop (누적 driver, injected clock)** | **live** (core/tick) · serve 배선=planned | `run_discovery_loop`/`discovery_loop_tick` | `examples/discovery/loop-report.json` |
| **freshness·promotion 기준 (ask-me-later 후보)** | **live** | `/discovery candidates` | `test_discovery_loop` |
| **evidence track (경쟁gap·self-improve → vault note)** | **live** (연결 시) | `/discovery evidence` | `examples/discovery/evidence-*.md` |
| **도입 효율 검토 (6-class 분류 + 8축 + 3축 consult, 기본 collect-first)** | **live** | `/discovery review <n>` | `examples/discovery/adoption-review.md`, `test_discovery_adoption` |
| **adopt-now → armory intake (adopted ≠ equipped)** | **live** (adopted 판정; equipped=별도 단계) | `/discovery adopt <n>` | `examples/discovery/adoption-packet.json`, `test_discovery_adoption` |
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
    "geeknews": true,
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
- **vault 미연결 시 정직 실패** — `/discovery save`·`/discovery review`·`evidence` 는
  `FORGEKIT_NEXUS_ROOT`/config 없으면 fake-write 없이 에러/메모리만.
- **fake adoption 금지** — `/discovery review` 는 절대 자동 adopt-now 를 주지 않는다(기본
  collect-first/hold). adopt-now 는 3축 검토 후 operator 결정뿐. `/discovery adopt` 는 raw 아이디어를
  계약 없이 catalog 에 넣지 않는다 — armory intake 가 정직하게 reject(adopted ≠ equipped).
- **collect-first 는 활성화 아님** — collect-first 후보는 Nexus 에 evidence 만 누적, resolver/Hephaistos
  에 즉시 노출되지 않는다.

## operator 흐름
1. `/discovery` — 수집 sweep + **누적 digest**: 총 추적/결정대기/promoted/saved/parked + 이번 sweep
   의 새 아이디어(왜 올라왔는지·다음 질문) + vault 연결 상태 힌트.
2. `/discovery pending` — 결정 대기 아이디어 큐(score 순, 번호 부여).
3. `/discovery candidates` — **물어볼 후보**(교차 관측·신선도·score 통과만). read-only 표면 —
   번호로 결정하려면 `/discovery pending` 의 번호를 쓴다.
4. `/discovery review <n>` — n 번을 **도입 효율 검토**(6-class 분류 + 8축 + 3축 consult)로 만든다.
   기본 collect-first(즉시 활성화 안 함), 연결 vault 에 adoption-review evidence note 영속.
5. `/discovery adopt <n>` — 3축 검토 후 operator adopt-now 결정 → armory intake. adopted(검증된 spec)
   여부만 판정, 장착(equipped)은 별도. raw 아이디어면 계약 미완성으로 정직 reject.
6. `/discovery evidence` — 이번 sweep 의 경쟁/gap·self-improve 신호를 vault evidence note 로 영속
   (미연결이면 정직 실패, 기록할 게 없으면 hollow note 안 만듦).
7. `/discovery promote <n>` — n 번을 PM handoff packet 으로 승격(제안) → ledger status `promoted`.
8. `/discovery save <n>` — 연결된 Nexus vault 에 authored idea-brief note 영속 → status `saved`(note_path 기록).
9. `/discovery park <n>` — 보류 → status `parked`(다시 안 올라옴).

반복 실행하면 ledger 가 쌓여 "지난번엔 새거 3개였는데 이번엔 0개 새거·5개 다시 관측, 2개는 이미 promoted"
처럼 누적 상태를 보여준다 — 개인 비서형 수집·정리·아이디어화 루프.

## 재생성/검증
- `python -m unittest tests.forgekit.test_discovery_ledger` (누적·dedup·lifecycle·config 토픽·surface)
- `python -m unittest tests.forgekit.test_discovery_sweep` (루프·digest·note·승격 코어)
- `python -m unittest tests.forgekit.test_discovery_loop` (24h bounded loop·freshness/promotion·evidence·surface)
- `python -m unittest tests.forgekit.test_discovery_adoption` (6-class 분류·8축 검토·3축 consult·armory bridge·GeekNews)
- `python -m unittest tests.forgekit.test_discovery_e2e` (전체 discovery 프로그램 체인)

## planned seam 붙이는 법 (YouTube/Google/Instagram)
`registry.default_registry` 의 planned 블록은 `PlannedCollector(_planned_spec(...))` 로 등록돼
있고 `collect()` 가 항상 빈 결과다. 실제 연결 시: (1) `collectors.py` 에 해당 어댑터(injectable
fetcher) 추가 → (2) spec `status=STATUS_LIVE` + cost/legal 갱신 → (3) 회귀에 fake fetcher 파싱
테스트 추가. 그 전까지는 planned 로 두어 digest 에 "미연결" 로 정직하게 보인다.
