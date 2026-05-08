# Engineering Agent — Memory policy (Hermes-inspired, issue #59)

본 정책은 Yule 의 **persistent memory 운영 규칙** 을 정한다. Hermes Agent 의 `memory_manager` 가 보여주는 "어떤 산출물이 향후 작업의 1 차 input 인가" 의 hint 흐름을 Yule 의 lifecycle gate 안에 결합하되, **자율 memory write 는 절대 도입하지 않는다.**

이 문서는 issue #59 ([Hermes 흡수 결정 D-2](#)) 의 구현물이다. 단일 source-of-truth — supervisor / status / `yule memory search` 가 본 정책을 따른다.

## 1. 핵심 원칙

1. **자율 memory write 금지.** 모든 memory write 는 lifecycle gate (`agents/lifecycle_status.can_write_obsidian_record`) 를 통과한 산출물만 한다. agent 가 자기 결정으로 SQLite/Obsidian 에 새 항목을 만들지 못한다.
2. **재사용성 hint 는 운영자 가시성 영역에 둔다.** "이 산출물이 향후 작업 input 으로 유용하다" 는 표식은 *retrieval 우선순위 boost* 형태로만 작동 — write 트리거가 아니다.
3. **원문 prompt / decision / synthesis 는 절대 압축 / 삭제하지 않는다.** audit traceability 가 모든 효율보다 우선.
4. **multi-tenant memory 도입 안 함.** Yule 은 single-operator 환경 — Hermes 의 `MemoryProvider` 추상화 같은 다중 백엔드 안 둔다.

## 2. 메모리 영역 (Yule 의 4 surface)

| surface | 위치 | 책임 |
|---|---|---|
| **session.extra** (workflow_sessions row) | SQLite `.cache/yule/cache.sqlite3` | 진행 중 lifecycle 상태 — `active_research_roles` / `research_pack` / `work_report` / `coding_proposal` / `agent_ops_audit` 등 |
| **memory FTS5 index** | SQLite `documents` table | `yule memory reindex` 가 `policies/` + Obsidian vault 를 SOURCE_POLICY / SOURCE_KNOWLEDGE 로 인덱싱 |
| **Obsidian vault** | 운영자 vault root (절대경로 `OBSIDIAN_VAULT_PATH`) | 사람 가독 자산 — research / decisions / task-logs / reports / knowledge / retrospectives |
| **topic ledger** | session.extra `research_topic` 키 | `agents/lifecycle/research_topic.py` 가 thread/topic 단위 dedup + status transition |

위 4 surface 를 동시에 갖는 게 Hermes 와 Yule 의 차이 — Hermes 는 `memory_manager` 가 단일 추상화로 묶지만, Yule 은 surface 별 책임 분리.

## 3. memory write trigger (단일 source)

**모든 memory write 는 다음 trigger 만 인정한다**:

| Trigger | source code | 영향받는 surface |
|---|---|---|
| `persist_research_artifacts` (intake / forum hook) | `agents/research_persistence.py` | session.extra `research_pack` / `research_synthesis` |
| `merge_session_extra` (lifecycle persistence) | `agents/lifecycle_persistence.py` | session.extra (lifecycle keys) |
| `yule obsidian sync` (사용자 명시 + lifecycle gate 통과) | `cli/obsidian.py` | Obsidian vault file |
| `yule memory reindex` (사용자 명시) | `cli/memory.py` | memory FTS5 index |
| `transition_topic_ledger` (sync 후) | `agents/lifecycle/research_topic.py` | session.extra `research_topic` |
| `append_agent_ops_audit` (의사결정 record) | `agents/lifecycle/agent_ops_log.py` | session.extra `agent_ops_audit` |

**위 6 가지 외의 자율 write 는 금지.** 새 trigger 추가는 본 정책 §7 절차 따라 PR + decision note.

## 4. 재사용성 hint 정책 (read-side boost only)

특정 산출물이 향후 작업의 1 차 input 으로 자주 쓰일 때 *retrieval 우선순위* 를 올린다. **새 write 트리거가 아니다 — 이미 영속화된 데이터의 검색 가중치 변경.**

| 표식 | 어디서 박힘 | retrieval boost |
|---|---|---|
| `kind=decision` (frontmatter) | obsidian_export 자동 | 기본 +1 — 결정은 다음 작업의 가장 강한 input |
| `status=decided` 또는 `status=approval-pending` | obsidian_export 자동 | 기본 +0.5 |
| `frontmatter.reusable=true` | 운영자 명시 | +1 — "이 자료는 같은 영역 작업에 자주 쓰임" 운영자 표식 |
| `frontmatter.canonical=true` | 운영자 명시 | +2 — "이 영역의 1 차 source" 운영자 표식 (회고 / runbook) |
| `kind=retrospective` | obsidian_export 자동 | +0.5 — 다음 작업의 input 후보 |

`yule memory search` / role-runner retrieval 이 본 boost 를 적용. 본 정책은 *어떤 boost 가 어떤 표식에서 오는지* 의 단일 source — 코드 구현은 [후속 milestone — `agents/memory/retrieval.py` boost wiring](#7-후속-milestone) 에서.

## 5. memory 만료 / pruning 정책

| 영역 | 만료 정책 | 근거 |
|---|---|---|
| `session.extra` (SQLite) | 무기한 — explicit `delete-session` 만 | audit traceability 우선 |
| memory FTS5 index | `yule memory reindex` 가 **stale path 자동 제거** | 파일 삭제 후 재인덱싱 시 자동 정리 |
| Obsidian vault | 만료 없음 — 운영자가 git 으로 관리 | vault 가 git repo 인 경우 history 보존 |
| `research_topic` ledger | 무기한 — `STATUS_SUPERSEDED` 표식만 | 후속 revision 추적 가능하도록 |
| `agent_ops_audit` | session.extra 안 — 200 entries cap | 이미 구현됨 (`agent_ops_log._max_entries`) |

**Hermes 의 curator 가 하는 자동 archive / consolidation 은 도입하지 않는다.** 운영자 명시 commit 만 vault 변경.

## 6. supervisor / status 노출 형태

session 진단 시 (`yule supervisor run --once` 또는 `yule status`) 본 정책 영역의 신호:

- session.extra 에 *재사용성 hint* 표식이 있는 산출물은 status 출력에서 별표 / `📌` 마커.
- `yule memory search "query"` 가 retrieval 결과를 boost 순으로 정렬 (boost 값 함께 출력 — 운영자가 왜 그 순서인지 확인 가능).
- topic ledger 의 `STATUS_SAVED` + `revision > 1` 산출물은 supervisor 진단에서 "revision 누적 중 — 회고 후보" 신호.

## 7. 후속 milestone

본 정책 적용을 위한 코드 작업 — 본 phase 범위 밖, 명시적으로 다음 milestone:

1. **retrieval boost wiring** — `agents/memory/retrieval.py` 에 frontmatter 기반 boost 적용. 우선순위 P1.
2. **status surface 의 hint 마커** — `agents/session_status.py` 가 frontmatter `reusable=true` / `canonical=true` 산출물을 별도 라인으로 노출. 우선순위 P2.
3. **`yule memory search` boost 출력** — 검색 결과에 boost 값 표시. 우선순위 P2.

각 milestone 은 별도 issue + decision note 로 관리.

## 8. 변경 절차

본 정책 변경 시:
1. issue + decision note 작성.
2. memory write trigger §3 추가 / 변경은 보안 영향 검토 — 자율 write 가 새로 들어가면 반드시 lifecycle gate 와 결합 증명.
3. `yule memory reindex` 실행하여 vault 인덱스 갱신.
4. supervisor 진단 출력 변경 시 회귀 테스트 (`tests/engineering/test_session_status.py` 또는 신규).

## 변경 이력

| 일자 | 변경 |
| --- | --- |
| 2026-05-08 | 초기 작성 — issue #59 의 Hermes 흡수 결정 D-2 구현물. memory write trigger / 재사용성 hint / 만료 정책 / supervisor 노출 / 후속 milestone 정리 |
