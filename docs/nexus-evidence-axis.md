# Nexus evidence axis (goal/discovery continuity)

goal/autopilot 결과가 **실제 Nexus artifact 로 누적**되게 하는 축의 SSoT. goal 이 있어도
`children/evidence: 0` 이면 장기 목표 운영체제처럼 보이지 않는다 — 이 축은 goal progression 의
packet/evidence 를 식별 가능한 메타데이터와 함께 Nexus evidence 노트로 적재한다.

코드 SSoT: `packages/nexus/src/nexus/vault/evidence.py` (스키마+writer) ·
`apps/forgekit-console/src/forgekit_console/goal_surface.py` (`apply_publish_evidence` 브리지).

## 고정 evidence schema (재사용 — 24h 루프 포함)
모든 누적 artifact 가 carry 하는 **최소 메타데이터** (`EvidenceMeta`). 이 키들은 노트 frontmatter 에
실제 키로 영속된다(prose 에 묻지 않음 — 쿼리 가능):

| key | 의미 |
| --- | --- |
| `goal_id` | 어느 goal 의 progression 인가 |
| `lane` | `goal` / `discovery` / `self-improve` (어느 축) |
| `packet_id` | 연결된 work packet id (없으면 빈값) |
| `role` | 산출 role(canonical id) — authored note 색/visibility 분리의 기준 |
| `source` | 출처 (`goal-progression` / `discovery-intake` …) |
| `status` | 산출 시점 goal/packet status |
| `created_at` | caller 공급 ISO (fake clock 없음) |
| `evidence_path` | 이 노트 자신의 vault-상대 경로 (self-describing, 링크백) |

role/agent **색 분리는 schema/metadata 기준** — 노트는 `role` 로 authored 되어 identity 레지스트리의
`cssclasses`(`fk-<role>`)+`agent_color` 가 frontmatter 에 박힌다. 색 자체는 사용자가 vault snippet 을
깔 때만 시각화되며(`vault_css_snippet`), 텍스트를 임의로 칠한다고 속이지 않는다.

노트 위치: `00-inbox/evidence/<lane>/<goal_id>/<slug>.md` (raw intake — curated 아님, status draft).
본문은 **구조화 섹션**(핵심 요약 + linkage 블록 + source record) — raw dump 금지.

## goal progression → Nexus 브리지 (`/goal publish <id>`)
goal 의 append-only `EvidenceRecord`(proposal/execution/verification/decision/observation/plan)를
Nexus evidence 노트로 **mirror** 하고, 각 노트 경로를 `ref` 로 갖는 `nexus-note` 레코드를 goal 에
append 한다. 그래서 `/goal evidence <id>` 가 **실제 artifact 증가**를 반영한다.

```
/goal publish <id>
  → 미러 안 된 evidence record 마다:
      EvidenceMeta(goal_id, lane=goal, packet_id, role=kind→role, status, created_at)
      → write_evidence_note(vault)  # 연결 vault 만, 미연결이면 정직 실패(no fake)
      → goal.add_evidence("nexus-note", ref=<note path>)   # 링크 영속
  → idempotent: 두 번째 publish 는 새 record 만 미러(중복 노트 없음)
```
evidence kind → role 매핑(색 분리): proposal→product-manager · plan→tech-lead ·
execution→platform-runtime-engineer · verification→qa-engineer · decision→gateway ·
observation→user-researcher · (그 외)→knowledge-engineer.

링크 체인: **goal.packets/evidence ↔ goal store(`ref`) ↔ Nexus note(`evidence_path` frontmatter)**.
goal store 의 `nexus-note.ref` = 노트 절대경로, 노트의 `evidence_path` = vault-상대경로 + `goal_id`/
`packet_id` frontmatter → 양방향으로 추적 가능.

evidence: `apps/forgekit-console/examples/goal/nexus-evidence/goal-evidence-note.md`(스키마 노트) ·
`goal-store-after-publish.json`(미러 후 goal 의 nexus-note ref).

## future discovery automation seam (명시)
외부 수집 자동화(24h 루프)는 **같은 스키마**로 이 축에 붙는다 — 별도 포맷을 만들지 않는다:
- 코드 seam: `nexus.vault.evidence.EvidenceMeta(lane=LANE_DISCOVERY, source="discovery-intake", …)`
  + `write_evidence_note(...)`. discovery intake 가 수집물을 evidence 노트로 적재할 때 이 한 쌍을
  그대로 호출하면 goal 축과 동일한 frontmatter/위치 규칙으로 누적된다.
- 위치 seam: `evidence_subdir(LANE_DISCOVERY, <source_id>)` = `00-inbox/evidence/discovery/<source_id>`.
- 현재 discovery 의 idea-brief/adoption/competitor-gap 노트(`discovery/sweep.py`·`adoption.py`)는
  자체 authored note 를 쓴다 — 후속에서 이 `EvidenceMeta` 스키마로 통일하면 goal/discovery/self-improve
  evidence 가 한 쿼리로 묶인다(`lane`/`role` frontmatter 기준). 그 전까지는 본 문서가 통일 지점을 명시한다.

## honesty rails
- **fake vault write 금지** — 미연결 vault 면 `write_evidence_note`/`/goal publish` 가 `None`/에러.
- **raw dump 금지** — 노트는 구조화 섹션 + linkage 블록(스키마)만, 원문 통째 적재 안 함.
- **idempotent** — 같은 record 를 두 번 미러하지 않음(`nexus-note` 의 `src_ts=` 마커로 dedup).
- **append-only** — goal evidence 는 모델상 추가만. 미러 레코드도 추가 → 히스토리 보존.

## 재생성/검증
- `python -m unittest tests.forgekit.test_goal_nexus_evidence` (스키마 frontmatter·writer·publish 브리지·
  idempotent·no-vault 정직·role 매핑·router surface).
