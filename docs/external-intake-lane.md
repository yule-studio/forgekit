# External skill/plugin/tool intake lane (SSoT)

> 외부 생태계(Claude / Codex / Gemini / open-source tooling)에서 ForgeKit·
> Hephaistos 에 도움이 되는 **skill / plugin / tool / MCP** 후보를 free-first 로
> 수집하고, **Armory 등록 전 단계의 curated intake packet** 으로 정제하는 레인.
>
> 코드 SSoT: [`packages/nexus/src/nexus/intake/`](../packages/nexus/src/nexus/intake/).
> 분류 어휘 SSoT: [`plugin-taxonomy.md`](plugin-taxonomy.md) (tool/skill/plugin/MCP/backend).
> 소스 수집 SSoT: [`packages/nexus/src/nexus/sources/`](../packages/nexus/src/nexus/sources/)
> (free-first live vs planned). Armory 카탈로그 SSoT: [`package-topology.md`](package-topology.md).
> 회귀: `python -m unittest tests.forgekit.test_external_intake`.

## 0. 한 문장 요약

**`sources`(raw SourceItem) → `intake`(외부 후보 schema + curation gate) → Armory
candidate / raw intake / blocked.** 새 collector·scheduler·state store 를 만들지
않고, 이미 있는 free-first `nexus.sources` 수집기를 그대로 소비한다.

```
nexus.sources (GitHub / HN / Reddit / RSS, free-first; YouTube/IG/Google=planned)
        │  SourceItem (raw signal)
        ▼
nexus.intake.extract   ─ SourceItem → ExternalCandidate (heuristic 분류 + dedupe + freshness + allowlist)
        ▼
nexus.intake.curate    ─ curation gate: promote (Armory candidate) / raw / blocked
        ▼
IntakePacket           ─ operator 표면(`/discovery intake`) + JSON evidence
        ▼ forgekit_console.intake_bridge.to_armory_candidate (console 합성층)
armory.ArmoryCandidate ─ 기존 catalog 제안 모델(#417). 새 모델 만들지 않고 재사용
        ▼ armory.promote_candidate
SkillSpec 등록 — selection contract(when_to_use/signals/unsafe_boundary/install) 충족 시만.
                 discovery 가 contract 를 못 채우면 정직하게 reject → curator 보강 필요.
```

> `ExternalCandidate`(discovery vetting)와 `armory.ArmoryCandidate`(catalog 제안)는
> **같은 의미의 중복 모델이 아니라 파이프라인의 다른 단계**다. 둘을 잇는 bridge 는
> 두 패키지를 모두 import 할 수 있는 **console 합성층**(`forgekit_console.intake_bridge`)
> 에만 둔다 — `nexus` 는 `armory` 를 import 하지 않아 패키지 경계가 유지된다.

## 1. External candidate schema

[`nexus/intake/candidate.py`](../packages/nexus/src/nexus/intake/candidate.py) 의
`ExternalCandidate` (frozen dataclass, vendor-neutral). 필수 표면 9개 + 출처/지문:

| 필드 | 의미 | 어휘 |
| --- | --- | --- |
| `source` | 어디서 발견했나 | source_type (`github`/`hackernews`/`reddit`/`rss`/`repo-local`) |
| `repo_url` | repo / 홈 URL | (없으면 promote 불가 — raw 강등) |
| `name` | 후보 이름 | 자유 |
| `provider_affinity` | 어느 provider 에 붙나 | `claude` / `codex` / `gemini` / `neutral` |
| `capability_class` | 무엇을 하는가 | `retrieval` / `code-review` / `orchestration` / `memory` / `security` / `infra` / `ui` / `data` / `unknown` |
| `install_shape` | 어떻게 설치/투영되나 | `skill` / `plugin` / `mcp` / `hook` / `cli` / `lib` / `backend` (taxonomy 와 정렬) |
| `trust_risk` | 신뢰/위험 | `low` / `medium` / `high` / `unknown` |
| `maintenance_signal` | 유지보수 신호 | `active` / `stale` / `archived` / `unknown` |
| `license` | 라이선스 | SPDX-ish 문자열 (`MIT`/`Apache-2.0`/… / `unknown` / `proprietary`) |
| `why_it_matters` | ForgeKit 관련성 | 자유(없으면 promote 불가) |
| `fingerprint` | dedupe 키 | `repo_url` 정규화 → 없으면 `source_type:name` |

`install_shape` 어휘는 [taxonomy](plugin-taxonomy.md) §1 의 5 개념과 정렬한다 —
`plugin` 은 runtime-plugin / harness-plugin 모두 포괄(세부 구분은 promote 후 Armory
등록 단계에서). `backend`(LLM 엔진, 예: Ollama)는 harness projection 대상이 아니라
backend 이므로 후보로 잡혀도 **install_shape=backend** 로 정직하게 표기되고 기본
allowlist 밖이라 promote 되지 않는다.

`ExternalCandidate` 는 Armory `WeaponSpec`/`SkillSpec` 의 **중복이 아니다** — 그쪽은
*등록된 카탈로그 엔트리*(selection contract)고, candidate 는 *등록 전 intake*
(provenance + trust + license + maintenance) 다. promote = "Armory 초안이 될 자격".

## 2. Collector flow (free-first, 정직)

[`nexus/intake/collect.py`](../packages/nexus/src/nexus/intake/collect.py) 는 새
수집기를 만들지 않고 `nexus.sources` 의 기존 collector 를 **tooling 시드**로 재사용한다.

- **GitHub** — `topic:mcp`, `awesome claude skill`, `topic:claude-code` 등 도구 시드
  검색(REST search, free-low). repo → candidate.
- **Hacker News** — `AI tooling OR devtools OR MCP` (Algolia API, free).
- **Reddit** — `r/LocalLLaMA` / `r/devtools` / `r/selfhosted` (public `.json`, free).
- **RSS** — operator 가 등록한 maintained project release/blog 피드(free).
- **repo-local** — 오프라인, ForgeKit 자체 gap(참고용, 외부 후보 아님 → raw).
- **planned (절대 fake live 금지)** — YouTube / Instagram / paid Google + Figma
  community / GeekNews 스크래핑. `nexus.sources` 의 `PlannedCollector` 가 항상 `[]`
  를 반환하므로 intake 도 빈 결과만 본다. seam 은 `docs/external-intake-lane.md`
  본 절에 남기고 코드로 fake 하지 않는다.

**dedupe / freshness / allowlist** (모두 `extract.py` 의 순수 함수):
- *dedupe* — `fingerprint`(정규화 URL) 동일 후보는 점수 높은 1건만 유지.
- *freshness* — 동일 fingerprint 충돌 시 score(별/업보트/포인트) 큰 쪽 채택.
- *allowlist* — `source_type` allowlist(기본: github/hn/reddit/rss/repo-local) +
  `install_shape` allowlist(promote 대상: skill/plugin/mcp/hook/cli/lib).

persistent ledger(seen/promoted lifecycle 영속)는 **planned seam** — 이번 레인은
순수 변환 + JSON snapshot evidence 로 닫고, 영속 ledger 는 별도 레인(고알 스케줄러
배선 시)에서 추가한다(아래 ponytail consult `reduce-surface` 결정 참고).

## 3. Curation policy (Armory 승격 게이트)

[`nexus/intake/curate.py`](../packages/nexus/src/nexus/intake/curate.py) 의
`curate(candidate)` → `CurationVerdict(disposition, reasons)`. disposition 3종:

| disposition | 의미 | 조건(요약) |
| --- | --- | --- |
| **`blocked`** | 거부 | risk=high / maintenance=archived / install_shape allowlist 밖(backend 등) / source_type allowlist 밖 / blocklist 명시 / license=proprietary |
| **`promote`** | Armory candidate 승격 | repo_url 有 + name 有 + capability_class≠unknown + install_shape∈allowlist + license∈OK(미상 아님) + trust∈{low,medium} + maintenance∈{active} + why_it_matters 有 |
| **`raw`** | raw intake 보존 | blocked 도 promote 도 아닌 나머지(메타데이터 부족 — 보강 대기) |

게이트는 **순수 함수**다 — 서비스 레이어 / state adapter / 큐 없음. `IntakePacket`
은 disposition 별로 묶은 결과 dataclass(영속 store 아님). promote 된 후보가 실제
Armory 엔트리(SkillSpec)로 등록되는 것은 **승인 게이트를 통과한 별도 단계**이며 intake
는 그 직전까지만 책임진다(no auto-install).

**Armory bridge** — promote 된 `ExternalCandidate` 는
[`forgekit_console.intake_bridge`](../apps/forgekit-console/src/forgekit_console/intake_bridge.py)
의 `to_armory_candidate` 로 기존 `armory.ArmoryCandidate`(#417) 로 매핑되고
`armory.promote_candidate` 게이트를 탄다. discovery 는 catalog selection contract
(when_to_use/signals/unsafe_boundary/install)를 날조하지 않으므로 갓 발견된 후보는
**정직하게 reject**(curator 보강 필요)된다 — fake catalog 엔트리 금지. bridge 는
console 합성층에만 있고 `nexus`→`armory` import 는 만들지 않는다(패키지 경계 유지).

## 4. ponytail consult — 설계 단순성 검토 (decision log)

> autonomy / execution core 과 동일하게, 새 모듈/계층을 더하기 전에 "정말 필요한가"
> 를 ponytail lens 로 먼저 검토한다. 판정: keep / simplify / use-existing-runtime /
> reduce-surface / reject-new-dependency.

| 후보 레이어 | 판정 | 근거 |
| --- | --- | --- |
| `ExternalCandidate` schema | **keep** | `SourceItem`(raw signal) 도 `IdeaBrief`(제품 아이디어) 도 install_shape/license/provider_affinity/maintenance/trust 를 안 담는다. 별개 도메인 객체 — 중복 모델 아님. |
| 새 collector / fetcher 계층 | **reject-new-dependency** | `nexus.sources` 가 이미 GitHub/HN/Reddit/RSS(real, free-first) + planned seam(fake 금지)을 제공. intake 는 그 SourceItem 을 소비만 한다. `collect.py` 는 기존 수집기에 tooling 시드만 주입하는 thin wiring. |
| 새 scheduler / queue wrapper | **reject-new-dependency** | goal autonomy 레인의 `goal_scheduler_tick` 이 이미 스케줄 척추다. intake 는 순수 변환으로 노출하고, tick 배선은 planned seam 으로 둔다(과한 orchestration 회피). |
| curation "service" / state adapter | **reduce-surface** | 게이트는 순수 함수(`curate`) + 결과 dataclass(`IntakePacket`). 영속 store/facade 없음. |
| persistent intake ledger | **reduce-surface (defer)** | 이번 레인 완료에 영속 lifecycle 불필요 — dedupe/freshness 는 순수 함수, evidence 는 JSON snapshot. 영속 ledger 는 tick 배선과 함께 별도 레인. |
| runtime/goal 위 facade | **use-existing-runtime** | promote 결과는 기존 handoff/approval 게이트로 흘려보낸다(별도 레인). intake 가 goal/evidence 구조를 재구현하지 않는다. |
| 별도 Armory candidate 모델 | **use-existing-runtime** | 작업 중 origin/main 재확인에서 #417 이 `armory.ArmoryCandidate` + `promote_candidate` 를 이미 머지한 것을 발견. 새 catalog candidate 모델을 또 만들지 않고, console 합성층 bridge(`intake_bridge`)로 기존 모델에 연결. discovery↔catalog 는 중복이 아니라 파이프라인 두 단계. |

따르지 않은 ponytail 의견: 없음(모든 판정 적용). 유일하게 "보류(defer)"한 것은
persistent ledger — 거부가 아니라 별도 레인으로 미룬 것이며, 그 자리에 fake 영속을
넣지 않고 planned 로 정직하게 표기한다.

## 5. evidence / tests

- 회귀: [`tests/forgekit/test_external_intake.py`](../tests/forgekit/test_external_intake.py)
  — schema · extract(분류/dedupe/freshness/allowlist) · curate(promote/raw/blocked)
  · free-first/planned 정직성 · ponytail end-to-end packet. 순수 stdlib → 오프라인 CI.
- bridge 회귀: [`tests/forgekit/test_intake_armory_bridge.py`](../tests/forgekit/test_intake_armory_bridge.py)
  — install_shape→armory kind 매핑, 갓 발견 후보 정직 reject, curator 보강 후 promote.
- 예시 packet: [`apps/forgekit-console/examples/discovery/external-intake-packet.json`](../apps/forgekit-console/examples/discovery/external-intake-packet.json)
  — ponytail-like 후보 1건이 promote, 메타 부족 1건이 raw, backend 1건이 blocked.

## 6. 표면

`/discovery intake` — free-first intake sweep 를 돌려 disposition 별 후보 digest 를
보여준다(승격/raw/blocked + 사유). 새 top-level 명령을 만들지 않고 기존 `/discovery`
표면에 subcommand 로 얹는다(reduce-surface). 실제 Armory 등록은 이 표면에서 하지
않는다 — promote 후보 목록까지만(no auto-install).
