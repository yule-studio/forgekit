# Memory 인덱스

`yule memory` 는 다음 출처를 로컬 SQLite FTS5 에 색인하고 검색하는 layer 다.

- Obsidian vault
- 저장소 정책 문서 (`policies/`, agent `CLAUDE.md`, `README.md`)
- 최근 workflow session artifact (research_pack / synthesis)

네트워크 의존이 없고, 결정적이며, 인덱스 파일은 `.cache/yule/memory.sqlite3` (또는 `YULE_MEMORY_DB_PATH`) 에 떨어진다.

## 명령

```bash
yule memory reindex                                   # 모두 재색인
yule memory reindex --skip-obsidian                   # vault 미설정 시 부분 재색인
yule memory search "Obsidian sync 정책" --limit 5
yule memory search "hero copy" --source-kind obsidian --note-kind decision
yule memory search "운영-리서치" --role engineering-agent/tech-lead --json
```

## 출력 형식

검색 결과는 다음과 같이 출력한다.

```text
[source_kind] title
  path: ...
  role: ...
  task_type: ...
  note_kind: ...
  tags: ...
  score: ...
  snippet: ...
```

`--json` 은 retrieval 파이프라인에서 그대로 소비할 수 있는 평면 객체 배열을 반환한다.

## 거동

- reindex 는 idempotent 하며 source_kind 별로 기존 슬라이스를 비우고 다시 채운다 — 삭제된 노트도 깔끔히 사라진다.
- `source_kind` 옵션: `obsidian`, `policy`, `agent_md`, `readme`, `workflow_artifact` (현재 활성 카테고리는 코드의 `MemoryIndexer` 참조).
- `--note-kind`, `--role`, `--task-type` 으로 좁힐 수 있고, 이 값들은 frontmatter 에 정의된 메타데이터를 그대로 쓴다.
