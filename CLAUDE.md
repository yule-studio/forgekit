# Yule Studio Agent — 전역 규칙

> 진입점은 [`AGENTS.md`](AGENTS.md). 본 파일은 **모든 에이전트 / 모든 작업
> 에 적용되는 전역 공통 규칙** 만 둔다. 도메인 한정 규칙은
> `agents/<agent>/CLAUDE.md` 또는 `docs/<topic>.md` 에 둔다.

## Purpose
이 레포지토리는 여러 GitHub 프로젝트의 이슈, 문서, 작업 흐름을 관리하는
**개인 에이전트 플랫폼**이다. 현재 우선순위는 `engineering-agent` MVP.

## Platform Direction
- 이 레포는 여러 역할별 전문 에이전트를 포함하는 플랫폼이다.
- 각 에이전트는 **명확하고 좁은 책임 범위** 를 가진다.
- 명시적 필요가 없으면 에이전트 간 책임을 섞지 않는다.
- 공통 원칙은 본 파일 (root `CLAUDE.md`).
- 에이전트별 세부 규칙은 각 에이전트 디렉터리의 `CLAUDE.md`.
- 모듈 책임 / 파일 분리 기준은 각 에이전트 디렉터리의 `CODE_LAYOUT.md`.

## Core Safety Rules
- **secret / 자격 정보 / 개인 키 / 로컬 runtime state 절대 커밋 금지.**
- **파괴적 명령 / 프로덕션 배포 / 민감 자격 접근 전 사람 승인 필수.**
- 자동 결정의 자율 등급 / 승인 매트릭스는 [`docs/autonomy-policy.md`](docs/autonomy-policy.md),
  [`docs/approval-matrix.md`](docs/approval-matrix.md) 가 SSoT.

## Operator Action Inbox
사람 응답이 필요한 모든 순간은 `#승인-대기` 카드로 표면화한다 — 조용히
멈추는 것 금지. 5 가지 request_type (APPROVAL/INFO/ACCESS/SECRET/DECISION)
는 [`docs/approval-matrix.md`](docs/approval-matrix.md) §6 참조.

## 읽기 우선순위 (요약)
| 항상 | `AGENTS.md` → 본 파일 |
| --- | --- |
| 코드 구조 작업 | + `agents/<agent>/CLAUDE.md` + `agents/<agent>/CODE_LAYOUT.md` |
| 브랜치/커밋/PR | + `policies/reference/*` |
| 승인/운영 | + `docs/autonomy-policy.md` / `docs/approval-matrix.md` / `docs/operations.md` |

전체 매핑은 `AGENTS.md` §2.

## 전역 코딩 컨벤션 (요약)

> 본 섹션은 **자주 강제해야 하는** 항목만 둔다. 도메인 한정 규칙은
> 각 `CODE_LAYOUT.md` / `policies/reference/*` 에 위임한다.

### 파일 크기 / 책임 분리
- **700 줄 초과** → 책임 분리 검토 warning. PR 본문에 "왜 한 파일에
  남겨야 하는지" 적지 않으면 검토자가 분리를 요청해도 됨.
- **1000 줄 초과** → 기본적으로 분리 대상.
- **1000 줄 초과 + 책임 2개 이상** → 분리 필수 (별도 PR 또는 동일 PR
  의 첫 commit 으로).
- 예외 (아래 중 하나면 분리 미루기 가능):
  - generated file (`*.pb.py` / `_pb2.py` 같은 코드 생성물)
  - fixture / snapshot / test data
  - 큰 registry / mapping 성격 파일 (선언만 있고 분기 로직 없음)
  - in-flight refactor 중인 `_legacy.py` 등 명시적 임시 파일
- 예외를 적용할 때는 해당 파일 상단 docstring 또는 모듈 옆
  `CODE_LAYOUT.md` 에 **이유** 를 남긴다.

### 책임 분리 신호
다음이 한 파일에서 동시에 보이면 분리 후보다 — 길이보다 우선한다.

- intake / intent classification / routing / state persistence /
  formatting / external integration 중 **3 가지 이상** 이 한 파일에 섞임
- 같은 phrase / regex 가 여러 함수에서 반복 patch 됨
- 한 함수가 다른 도메인의 dataclass 를 직접 mutate
- "임시" / "TODO" / "FIXME" 가 같은 파일에 5개 이상 누적

### 모듈 형태
- `router` 는 **얇은 orchestration** 으로 유지 — 토큰 점수 / 캐시 read-write /
  collector 는 다른 모듈에 위임.
- `conversation` 은 **intent / response shaping** 중심.
- 도메인 로직 (deliberation / report 작성 / vault write) 은 자체 모듈.
- 같은 도메인의 helper 가 늘면 패키지 (`<name>/__init__.py`) 로 승격.

### Commit / PR / 브랜치
- 한국어 commit 메시지 + gitmoji 1개 + `변경 이유 / 주요 변경 사항 / 비고`
  3 섹션. 자세한 형식은 [`policies/reference/COMMIT_CONVENTION.md`](policies/reference/COMMIT_CONVENTION.md).
- PR 1 개당 **최소 3 commit** 으로 논리 분할. squash 는 docs-only 1-commit
  chore 에만.
- 브랜치 / 라벨 / assignee 규칙은 [`policies/reference/BRANCH_STRATEGY.md`](policies/reference/BRANCH_STRATEGY.md) +
  [`docs/engineering-agent-governance.md`](docs/engineering-agent-governance.md).

### 테스트
- 새 기능 추가 시 **새 테스트** 우선 작성. 기존 테스트가 다 통과하는데
  새 회귀 라인이 비어있으면 PR 검토 거부.
- 회귀 라인이 어느 디렉터리에 떨어져야 하는지는 각 `CODE_LAYOUT.md` 의
  "tests/ 매핑" 표 참조.
- 자세한 테스트 가이드는 [`docs/testing.md`](docs/testing.md).

## 동기화 규칙
새 규칙을 추가하거나 옮기면 다음 중 영향받는 곳만 갱신한다 (중복 회피).

- `AGENTS.md` §2 표 — 작업 맥락 → 문서 매핑이 바뀌면
- 본 파일의 "읽기 우선순위" / "코딩 컨벤션" 요약 — 전역 규칙이 추가되면
- `agents/<agent>/CLAUDE.md` — 도메인 한정 규칙이면
- `agents/<agent>/CODE_LAYOUT.md` — 모듈 책임 / 파일 분리 기준이 바뀌면

> 같은 규칙을 두 곳에 복제하면 한쪽만 갱신돼 silently 어긋난다. 한 곳에만
> 두고 나머지는 cross-link.
