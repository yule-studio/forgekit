# Vault Naming Convention (v.2.0.0)

| 문서 버전 | 작성일 | 작성자 | 주요 변경 사항 |
| --- | --- | --- | --- |
| v.2.0.0 | 2026-05-12 | engineering-agent/tech-lead | 날짜 prefix 제거 + dash 분리자 통일 + 본문 표 양식 |
| v.1.0.0 | 2026-05-11 | engineering-agent/tech-lead | 최초 (날짜 + underscore 형식, deprecated) |

Obsidian vault (`yule-agent-vault/obsidian-vault/10-projects/yule-studio-agent/`)
안의 노트 명명 규칙. 코드 (`filename_convention.validate_filename`) +
회귀 테스트 (`tests/engineering/test_obsidian_convention_governance.py`)
가 hard rail.

## 표준 형식

```
<kind>-<topic-kebab>[-issue-<n>].md
```

- `<kind>` — `decision` / `research` / `task-log` / `knowledge` /
  `meeting` / `work-report`
- `<topic-kebab>` — kebab-case, 영문 소문자 / 숫자 / hyphen
- `-issue-<n>` — 선택, 관련 GitHub issue 번호

**파일명에 날짜를 넣지 않는다.** 날짜는 frontmatter `created_at` +
본문 첫 줄 문서 버전 표 에서만 관리. 파일 rename 없이 새 버전 행만
추가하므로 [[link]] 가 깨지지 않음.

## 예시

| 형식 | 예 |
|---|---|
| ✅ 표준 | `decision-product-vs-marketing-cpo-cmo-separation-issue-126.md` |
| ✅ 표준 + issue | `task-log-tech-lead-runtime-loop-issue-73.md` |
| ✅ 표준 | `research-engineering-knowledge-surface-strengthening.md` |
| ❌ 날짜 prefix | `2026-05-08_decision-tech-lead-single-write-subject.md` (mistake `obsidian.filename.date-prefix`) |
| ❌ kind 누락 | `2026-05-08_59-hermes-tech-lead.md` (mistake `obsidian.filename.kind-missing`) |
| ❌ 잘못된 분리자 | `decision_tech-lead-foo.md` (mistake `obsidian.filename.topic-malformed`) |

## 폴더 매핑

| 폴더 | kind | 용도 |
|---|---|---|
| `_moc/` | knowledge | 주제별 Map of Content hub |
| `decisions/` | decision | 결정 박스 + 의미 + 비결정 + 영향 |
| `research/` | research | 외부/내부 자료 조사 + 출처 |
| `task-logs/` | task-log | issue/사이클 단위 작업 진행 |
| `knowledge/` | knowledge | 운영 지식 reference card |
| `meeting-notes/` | meeting | 운영자 회의/합의 |
| `retrospectives/` | retrospective | 사이클 회고 |

## 본문 첫 줄 표준

모든 노트는 frontmatter 다음에 문서 버전 표를 둔다:

```markdown
# <한 줄 제목>

| 문서 버전 | 작성일 | 작성자 | 주요 변경 사항 |
| --- | --- | --- | --- |
| v.1.0.0 | YYYY-MM-DD | <부서>/<역할> | 최초 ... |
```

후속 개정마다 행 추가 (위쪽이 최신). 이 표가 파일명에서 빠진 날짜·버전
정보를 보존한다.

## frontmatter 표준

```yaml
---
title: "한 줄 제목"
kind: decision | research | task-log | knowledge | meeting | work-report
project: yule-studio-agent
agent: <부서>/<역할>
status: decided | draft | superseded | current | completed | historical
created_at: 2026-MM-DDTHH:MM:SS+09:00
session_id: <짧은 식별자>   # optional
issue_number: <NN>           # optional
related:
  - ../<폴더>/<관련-노트>.md
tags:
  - <topic-tag>
---
```

## 폴더 README hub 구조 (v.2.0.0)

최상단 `yule-agent-vault/README.md` 만 README 로 유지 (오픈소스 컨벤션).
하위 폴더의 인덱스는 폴더명 (숫자 prefix 제외) 으로:

```
yule-agent-vault/README.md       (오픈소스 README, vault 진입점)
└── obsidian-vault/index.md      (10 폴더 인덱스)
    ├── 00-inbox/inbox.md
    ├── 10-projects/projects.md
    │   └── yule-studio-agent/yule-studio-agent.md
    │       ├── _moc/_moc.md
    │       ├── decisions/decisions.md
    │       ├── research/research.md
    │       ├── task-logs/task-logs.md
    │       └── knowledge/knowledge.md
    │           └── plugins/plugins.md
    ├── 20-areas/areas.md
    ├── 30-resources/resources.md
    ├── 40-patterns/patterns.md
    ├── 50-snippets/snippets.md
    ├── 60-troubleshooting/troubleshooting.md
    ├── 70-daily/daily.md
    ├── 80-templates/templates.md
    └── 90-archive/archive.md
```

이유: Obsidian 그래프에서 모든 노드가 "README" 라벨로 표시되면 구분
불가. 폴더명 토큰으로 가면 한 눈에 어떤 인덱스인지 보임.

## 자동화 에이전트 작성 규칙

1. 본 컨벤션 (파일명 + 본문 표 + frontmatter) 준수
2. PasteGuard 통과한 페이로드만 작성
3. 해당 폴더 README 에 [[link]] 추가
4. 같은 주제의 다른 단계 노트 (decision/research/task-log) 와
   frontmatter `related` 로 cross-link
5. 새 주제면 `_moc/` 안에 hub 노트도 함께 작성

## 코드 enforcement

- `src/yule_orchestrator/agents/obsidian/filename_convention.py` —
  `validate_filename` 이 단일 판정 소스
- `src/yule_orchestrator/agents/obsidian/export.py` —
  `recommend_path` 가 본 컨벤션으로 basename 생성 (날짜 제거됨)
- `src/yule_orchestrator/agents/obsidian/knowledge_writer.py` — 같음
- `tests/engineering/test_obsidian_convention_governance.py` —
  hard rail CI 게이트
- mistake_ledger signature: `obsidian.filename.date-prefix` /
  `obsidian.filename.kind-missing` / `obsidian.filename.topic-malformed` /
  `obsidian.filename.not-markdown`

## 본 컨벤션의 vault 사본

vault 안에도 같은 내용의 인덱스가 존재:
`10-projects/yule-studio-agent/yule-studio-agent.md` (프로젝트 진입점) +
`obsidian-vault/index.md` (10 폴더 인덱스). 본 repo 의 정책과 vault
인덱스가 어긋나면 본 repo 가 권위.
