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

## Curated 정책 (P0-T)

핵심 원칙: `inbox` 는 원천 자료 저장소다. **승격은 파일 이동이 아니라
원천을 해석해 linked curated note 를 새로 만드는 행위**다. 좋은 노트의
기준은 "많이 썼는가" 가 아니라 "retrieval eval 에서 잘 꺼내지는가" 다.

코드 SSoT: [`apps/engineering-agent/src/yule_engineering/agents/governance/runtime_policy.py`](../apps/engineering-agent/src/yule_engineering/agents/governance/runtime_policy.py).

### Inbox 규칙
- `00-inbox` 는 raw 수집 / 임시 관찰 / source bookmark 용도.
- `00-inbox` 문서는 품질 보증 대상이 아니라 **참고 근거**.
- `00-inbox` 문서는 target note 로 직접 승격하지 않음 — caller 가
  `is_inbox_path(path)` 로 검사.

### Curated note 규칙
- 일일 자동 생성/갱신 curated note: **20~30 개** 제한.
- 일일 raw reference 수집: 최대 **100 개**.
- 필수 frontmatter 7 키: `title / kind / status / created_at / tags /
  related / home_hub`.
- 필수 본문 섹션 5 종: `핵심 요약 / 내 해석 / 적용 맥락 / 관련 노트 /
  참고`. 단순 복붙 / 링크 목록 / 요약 없는 자료는 curated 로 인정 안 함.
- 모든 curated note 는 ① `home_hub` 1 개 + ② `related` 최소 1 개.
- 연결할 hub 가 없으면 노트 늘리기 전에 **hub 를 먼저 생성**.

### 검증 함수
- `validate_curated_note(path, frontmatter, body)` — frontmatter / 본문
  / inbox path 한 번에 검사.
- `is_inbox_path(path)` — inbox 분류.
- `detect_orphan_note(note_path, home_hub, related, hub_paths)` — orphan
  검출 (push 금지).
- `detect_broken_links(body, available_paths)` — 끊긴 wikilink 시퀀스.

## Retrieval eval (P0-T)

vault 구조 변경 / 대량 노트 추가 **전후로** retrieval eval 을 실행한다.
note 를 많이 추가했는데 점수가 떨어지면 "지식 추가 성공" 이 아니라
**regression** 으로 본다 — 추가 중단 후 hub / link / alias / tag 를
먼저 수정.

평가셋 스키마 (each entry):

```yaml
question: "JWT 와 session 차이가 뭐야?"
expected_notes:
  - 20-areas/auth/jwt-vs-session.md
allowed_alternatives:
  - 40-patterns/auth/token-strategy.md
failure_reason: ""  # eval 결과 분석용. 빈 값 허용 (키 존재 강제)
```

수치 정책:
- 최소 fixture **50 문항** (이하 → regression 차단).
- 목표 fixture **100 문항** (50~99 사이는 warning).
- **top-5** 기준 평가 — 기대 note 가 top-5 에 없으면 fail.
- 새 hub / 중요 decision 추가 시 관련 retrieval question 최소 1 개 추가.
- 검증: `validate_retrieval_eval_fixture(entries)`.

> eval 없이 대량 curated generation push 금지.
