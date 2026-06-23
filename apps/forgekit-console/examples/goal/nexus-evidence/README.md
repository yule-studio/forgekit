# goal evidence → Nexus axis — evidence

`/goal publish <id>` 가 goal progression 의 append-only evidence 를 **Nexus evidence 축**에
schema 고정 authored note 로 mirror 한 결과. SSoT: [`docs/nexus-evidence-axis.md`](../../../../../docs/nexus-evidence-axis.md).
재생성/검증: `python -m unittest tests.forgekit.test_goal_nexus_evidence`.

| 파일 | 무엇 |
| --- | --- |
| `goal-evidence-note.md` | publish 가 쓴 evidence 노트 1건(execution). frontmatter 에 최소 스키마(goal_id/lane/packet_id/role/status/created_at/evidence_path) + role(platform-runtime-engineer) 색/visibility(`fk-platform`). 본문은 구조화 섹션(raw dump 아님) |
| `goal-store-after-publish.json` | publish 후 goal store — 원본 evidence 3건 + `nexus-note` mirror 3건(각 `ref`=노트 경로, vault-상대로 정규화). `/goal evidence` 가 보는 실제 artifact 증가 |

## 정직성
- fake vault write 없음(미연결이면 publish 가 에러) · idempotent(재실행 시 중복 노트 0) ·
  raw dump 없음(구조화 + linkage 블록만) · append-only(미러도 추가, 히스토리 보존).
- discovery automation seam: 동일 스키마로 `lane=discovery` 적재(`nexus.vault.discovery_intake_meta`).
