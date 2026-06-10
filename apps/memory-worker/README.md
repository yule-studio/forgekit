# memory-worker

> memory 인덱싱 / retrieval eval / vault sync / knowledge housekeeping 를
> 담당하는 백그라운드 워커. 본 디렉터리는 현재 **책임 문서(scaffold)** 만
> 두며, 코드는 아직 `src/yule_orchestrator/memory/**` 와 `cli/memory.py`
> (= `src/yule_orchestrator/cli/memory.py`) 에 있다.

## 책임 범위

- **memory reindex** — 노트/문서 인덱스 재생성.
- **retrieval eval** — fixture 기반 top-5 검색 품질 평가 (regression 감시).
- **vault sync** — vault 노트 동기화.
- **knowledge housekeeping** — orphan/broken link 정리, 중복 정돈.

> 범위 밖: agent 의사결정(=engineering/planning-agent), Discord transport
> (=discord-gateway).

## 의존 패키지 (필요한 `packages/*` 만)

- `packages/memory` — 검색/인덱싱 코어 (예정). 워커는 이 코어를 구동만 한다.
- `packages/agent-contracts` — reindex/eval 작업을 트리거하는 command, 결과
  status 보고. **agent 간 직접 import 금지.**

> 규칙: `apps/* → packages/*` 만 허용, 역방향 금지.

## 현재 위치 → 이전 대상

| 현재 위치 | 이전 대상 |
| --- | --- |
| `src/yule_orchestrator/memory/**` | 검색/인덱싱 코어는 `packages/memory`, 워커 구동부는 `apps/memory-worker` |
| `src/yule_orchestrator/cli/memory.py` | `apps/memory-worker` CLI 진입점 |

## migration TODO

- [ ] `memory/{indexer,retrieval,search}.py` 의 코어 로직을 `packages/memory` 로 추출.
- [ ] `cli/memory.py` 를 본 앱의 워커 진입점으로 이전.
- [ ] reindex/eval 트리거를 `packages/agent-contracts` command/status 로 연결.
- [ ] retrieval eval fixture(최소 50/목표 100) 회귀 감시를 워커 루프에 포함.

(실제 코드 이동은 본 브랜치 범위 아님 — 후속 PR.)
