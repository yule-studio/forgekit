# Armory intake — 외부 후보 도입 효율 검토 (SSoT)

> 외부 plugin / skill / MCP / tool 후보를 "좋아 보인다"만으로 들이지 않는다. ForgeKit
> 관점의 **8축 도입 효율 artifact** + **PM / tech-lead / specialist 3축 검토**를 거쳐
> **adopt-now / collect-first / hold** 중 하나로 귀결시키고, 그 결과를 Hephaistos 가
> 읽는 catalog 와 연결한다.
>
> 코드 SSoT: [`packages/armory/src/armory/adoption.py`](../packages/armory/src/armory/adoption.py)
> (모델 + 게이트) · [`adoption_registry.py`](../packages/armory/src/armory/adoption_registry.py)
> (평가된 후보 데이터). 카탈로그 promotion 게이트는 [`armory.candidate`](../packages/armory/src/armory/candidate.py),
> 카탈로그 자체는 [`armory.catalog`](../packages/armory/src/armory/catalog.py).
> 사람용 카탈로그 SSoT: [`armory.md`](armory.md). 회귀: `python -m unittest tests.forgekit.test_armory_adoption`.

## 0. 한 문장 요약

**intake 후보 → 8축 도입 검토 + 3축 review → adopt-now / collect-first / hold.**
adopt-now 만 catalog 에 올리고(adopted=available), **장착/설치(equipped)는 별개**다.

```
외부 후보 (ponytail / Context7 / MCP servers / Vale / textlint / alex / write-good / proselint / browser-use)
        ▼ armory.adoption.AdoptionReview (8축 artifact + 3축 검토)
        ▼ validate_review (honesty gate)
   ┌───────────────┬────────────────────┬──────────────────┐
 adopt-now       collect-first          hold
   │               │                     │
 catalog 등록     Nexus 근거만 누적       제외(governance/overlap/security 사유)
 (available,      (즉시 활성화 X)
  미설치)
        ▼ (adopt-now) armory.catalog (SkillSpec/WeaponSpec/LoadoutSpec)
        ▼ Hephaistos resolver = equip plan (suggestion-only, **설치 안 함**)
```

## 1. 8축 도입 효율 artifact (후보마다 필수)

`AdoptionReview` 가 후보마다 다음 8개를 **빈칸/placeholder 없이** 채운다(`validate_review` 강제):

1. `current_pain` — 지금 무엇이 아픈가
2. `expected_benefit` — 도입 시 기대 효과
3. `overlap` — 기존 Armory/Nexus/Hephaistos capability 와의 겹침
4. `operational_cost` — install/runtime 부담
5. `maintenance_risk` — 유지보수 리스크
6. `provider_runtime_fit` — provider/runtime 적합성
7. `governance_security` — governance/security 영향
8. `verdict` — adopt-now / collect-first / hold

## 2. 3축 검토 (최소 PM / tech-lead / specialist)

각 후보는 `reviewers` 에 PM·tech-lead·specialist 세 축의 `ReviewerVerdict`(verdict + rationale)
를 담는다. **adopt-now 는 3축 합의 필수** — 한 축이라도 collect-first/hold 면 adopt-now 불가
(`validate_review` 가 거부). specialist 축은 후보 성격에 따라 knowledge-engineer(문서 도구) /
platform-runtime(MCP) / security-engineer(fetch·browser·git) 로 달라진다.

## 3. adopted ≠ equipped (fake adoption 금지)

- **adopt-now** = 카탈로그에 올린다(available). **설치/장착이 아니다.**
- attach 류(tool/plugin/mcp)는 `install_plan` 으로 설치 경로만 **선언**한다 — install_plan
  없는 attach 류는 adopt-now 될 수 없다(`validate_review` 거부). WeaponSpec 도 `install_hint`/
  `verify_command` 로 "어떻게 설치/확인하는가"만 담고 실제 설치는 하지 않는다.
- Hephaistos resolver 는 equip plan 을 **제안**할 뿐 설치하지 않는다 → "installed" 로 보고 금지.
- **collect-first** 는 Nexus 에 근거(evidence)만 누적하고 즉시 활성화하지 않는다.

## 4. 이번 라운드 verdict (13 후보)

| 후보 | kind | verdict | 핵심 사유 |
| --- | --- | --- | --- |
| **Vale** | tool | **adopt-now** | 단일 바이너리·config 기반 prose lint, vault 문서 품질과 정합, 위험 낮음. doc-quality loadout anchor. |
| proselint | tool | collect-first | Vale 규칙과 겹침 + Python 의존 — 한계 드러나면 보강. |
| write-good | tool | collect-first | naive·유지보수 정체 + Node 의존. loadout optional(약). |
| alex | tool | collect-first | 포용적 글쓰기 — 영어 한정, Vale inclusive 팩과 겹침. |
| textlint | tool | collect-first | 플러그인으로 한국어 커버 가능성 — 설정 부담 커 PoC 먼저. |
| ponytail | skill | collect-first | 단순성 렌즈 — 이미 council/consult 와 겹침, 외부 repo 의존보다 내부 스킬화. |
| Context7 | mcp | collect-first | 라이브러리 docs 주입(환각↓) — 호스티드 의존 + outbound/키 가드 선행. 가장 유망. |
| MCP Fetch | mcp | collect-first | 공식·고품질이나 SSRF allowlist/sandbox 설계가 adopt 전제. |
| MCP Memory | mcp | collect-first | Nexus/ledger 와 중복 큼 — 한계 드러날 때 재평가. |
| MCP Filesystem | mcp | **hold** | broad FS 접근이 경로 안전 hard rail 우회 — governance 충돌 + 중복. |
| MCP Git | mcp | **hold** | git-write hard rail(`git -C`+pathspec)/commit-governance 우회 — 금지. |
| MCP Sequential Thinking | mcp | **hold** | decision_lane/모델 추론과 중복, 효용 낮음. |
| browser-use | tool | **hold** | 실 브라우저 구동 = 자격증명/exfiltration 고위험 + 무거운 의존. sandbox 설계 후 재평가. |

분포: adopt-now 1 · collect-first 8 · hold 4. (인지도만으로 adopt-now 로 올리지 않는다 —
대부분 collect-first/hold.) evidence: [`apps/forgekit-console/examples/armory-intake/adoption-catalog.json`](../apps/forgekit-console/examples/armory-intake/adoption-catalog.json).

## 5. doc-quality 묶음 = 하나의 curated loadout

문서 품질 도구(Vale/proselint/write-good/alex/textlint)는 **하나의 loadout** 으로 묶었다 —
catalog 의 `doc-quality-review-local`:
- `required_weapons=("vale",)` (adopt-now anchor) + `optional_weapons=(proselint/write-good/alex/textlint)` (collect-first).
- `recommended_skills=("doc-quality-review",)` — style-guide 기반 prose 린트 스킬(vendor-neutral).
- adopted=카탈로그 등록일 뿐 — weapon `install_hint` 가 설치 경로만 선언(미설치).

Hephaistos resolver 가 "문서 품질/문체/prose/vale" 신호에 이 loadout 을 equip plan 으로 제안한다.

## 6. ponytail consult — 설계 단순성 검토 (decision log)

> 새 모듈/계층을 더하기 전에 ponytail lens 로 점검. 판정: keep / simplify /
> use-existing-runtime / reduce-surface / reject-new-dependency.

| 후보 레이어 | 판정 | 근거 |
| --- | --- | --- |
| `armory.adoption`(AdoptionReview + gate) | **keep** | `armory.candidate`(catalog contract 검증)도 `ExternalCandidate`(discovery metadata)도 "ForgeKit 이 들일지/언제" 전략 판단(8축+3축)을 안 담는다. 별개 단계 — 중복 아님. |
| 새 review/council 프레임워크 | **reject-new-dependency** | 3축 검토는 plain data(ReviewerVerdict)로 표현하고 gate 가 존재/합의만 강제. decision_lane(forgekit-runtime) import 안 함 — armory 는 leaf 유지. 실제 council 배선이 필요하면 console 합성층. |
| 별도 catalog candidate 모델 | **use-existing-runtime** | adopt-now 의 catalog 실현은 기존 `armory.catalog`(SkillSpec/WeaponSpec/LoadoutSpec) + `register_promoted`/`promote_candidate` 사용 — 새 catalog 모델 안 만듦. |
| collect-first 영속 store | **reduce-surface (defer)** | 이번 라운드는 registry(데이터) + evidence JSON 으로 닫음. Nexus 영속 ledger 배선은 별도(자동 활성화 금지 규칙과 정합). |
| `/armory` 표면 | **keep (minimal)** | 카탈로그는 `/skills`·`/loadout`·`/resolve` 가 본다. intake **결정**(adopt/collect/hold)은 그것들과 다른 표면이라 `/armory` 하나 추가(요약 + `review <id>` 상세). |

따르지 않은 ponytail 의견: 없음. collect-first 영속 ledger 만 defer(거부 아님) — fake 영속 대신
evidence JSON 으로 정직 표기.

## 7. evidence / tests / 표면

- 회귀: [`tests/forgekit/test_armory_adoption.py`](../tests/forgekit/test_armory_adoption.py)
  — registry 전부 valid·3 verdict 분포·adopt-now 합의/install-plan 게이트·doc-quality loadout·
  render·evidence 일치.
- evidence: `examples/armory-intake/adoption-catalog.json` (13 review + verdict 요약 + doc-quality loadout). Hephaistos/Nexus-readable.
- 표면: `/armory` (verdict별 요약 + doc-quality loadout) · `/armory review <id>` (8축 + 3축 상세).
- catalog 자체: `/skills` · `/loadout doc-quality-review-local` · `/resolve <문서 품질 요청>`.
