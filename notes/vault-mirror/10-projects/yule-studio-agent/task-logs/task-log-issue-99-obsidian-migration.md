---
title: "issue #99 — Obsidian 파일명 컨벤션 마이그레이션 + vault auto-push 작업 로그"
kind: task-log
issue: 99
parent_issue: 81
session_id: issue-99-obsidian-migration
project: yule-studio-agent
created_at: 2026-05-11T00:00:00+09:00
status: in-progress
branch: feature/obsidian-convention-issue-99-v2
mode: integration-mediated (마이그레이션 + 신규 모듈 + governance regression)
tags: [task-log, issue-99, obsidian, migration, vault-auto-push, F8]
---

# 목표

policies/runtime/agents/engineering-agent/issue-pr-conventions.md §4.1 의 새 Obsidian 파일명 컨벤션 (`<kind>-<topic-slug>[-issue-<n>].md`) 을 vault-mirror 노트에 일괄 적용하고, 작업 완료 시점에서 vault repo 로 자동 push 하는 hook 의 1차 모듈을 land 한다.

자세한 결정은 [[decision-engineering-agent-authoring-policy-issue-69]] § §4 — 본 마이그레이션은 그 결정의 직접적 후속이다.

# 본 PR scope (slim — 2단계)

## 8.1 마이그레이션 스크립트

`scripts/migrate_obsidian_filenames.py` — dry-run default, `--apply` 명시 시 실 변경.

변환 규칙:

| 입력 형태 | 출력 형태 | 비고 |
| --- | --- | --- |
| `YYYY-MM-DD_<rest>.md` | 정규화된 `<rest>.md` | 날짜 prefix 제거 |
| `_` (underscore) | `-` (hyphen) | kebab-case 통일 |
| `issue-<n>-<kind>-<topic>` | `<kind>-<topic>-issue-<n>` | issue 접미사 재배열 |
| `report-*` | `work-report-*` | legacy kind alias 정규화 |

자동 동작:

- frontmatter `created_at` 이 없는 경우 stem 의 날짜 prefix 에서 ISO8601 KST 시각을 추출하여 주입한다 (있으면 건드리지 않음).
- wikilink (`[[YYYY-MM-DD_xxx]]`) 일괄 치환 — pipe alias (`[[a|b]]`) 와 anchor (`[[a#h]]`) 의 tail 은 보존.
- git mv 로 rename 하여 history 를 유지한다.

Hard rails:

- `--apply` 없이는 어떤 디스크 변경도 일어나지 않는다.
- 보호 브랜치 (`main` / `master`) 에서 `--apply` 시도 시 blocker 반환.
- target 충돌 시 SKIP + 사유 기록 (덮어쓰기 금지).

## 8.2 vault auto-push 모듈

`src/yule_orchestrator/agents/obsidian/vault_auto_push.py`:

- `AutoPushVerdict(performed, branch, commit_hash, skipped_reason, blocked_reason)`
- `push_vault_if_ready(*, completion_event, vault_repo_root=None, dry_run=True, env=None) -> AutoPushVerdict`
- env:
  - `YULE_VAULT_AUTOPUSH_ENABLED=false` (default OFF)
  - `YULE_VAULT_REPO_ROOT=`
  - `YULE_VAULT_BRANCH=auto/notes-sync`

Hard rails:

- env 가 `true` 가 아니면 무조건 skip.
- 보호 브랜치 직접 push 차단.
- commit message 는 PasteGuard 의 VAULT 채널 통과 후에만 사용.
- `dry_run=True` (default) 면 git 명령 실행 없음.

`src/yule_orchestrator/agents/obsidian/filename_convention.py` — §4.1 validator (≤80 줄). mistake_ledger signature 와 1:1 매핑:

| signature | 시그니처 의미 |
| --- | --- |
| `obsidian.filename.date-prefix` | 날짜 prefix 사용 |
| `obsidian.filename.kind-missing` | 허용 kind prefix 누락 |
| `obsidian.filename.topic-malformed` | topic-slug 가 kebab-case 아님 |
| `obsidian.filename.not-markdown` | `.md` 확장자 아님 |

# 마이그레이션 실행 결과 (2026-05-11)

| 지표 | 값 |
| --- | --- |
| rename 파일 수 | 13 |
| wikilink 갱신 wikilink 카운트 | 79 |
| wikilink 갱신된 파일 수 | 13 |
| frontmatter `created_at` 자동 주입 발동 | 0 (모든 파일에 이미 존재) |
| 충돌 SKIP | 0 |

본 commit 들은 분리되어 land:

1. 모듈 + 테스트 + .env.example — 코드만.
2. rename — `git mv` 만.
3. wikilink + 스크립트 순서 버그픽스 — 본문 갱신만.
4. (본 노트) 마이그레이션 task-log.

# 회귀

- `tests/agents/test_obsidian_filename_migration.py` — 13 케이스 PASS (≤15 budget)
- `tests/agents/test_vault_auto_push.py` — 10 케이스 PASS (≤12 budget)
- `tests/engineering/test_obsidian_convention_governance.py` — 8 케이스 PASS (≤8 budget) — vault-mirror 회귀 어설션 포함.

# 후속

- 본 PR 머지 후 `vault_auto_push.push_vault_if_ready` 를 `completion_hook` 옆에 wiring (env OFF default 유지). 별도 PR.
- Obsidian `writer.py` 에서 신규 노트가 컨벤션을 만족하도록 filename_convention validator 호출. 별도 PR.

# 참조

- policies/runtime/agents/engineering-agent/issue-pr-conventions.md §4.1
- [[decision-engineering-agent-authoring-policy-issue-69]]
- [[task-log-tech-lead-runtime-loop-issue-73]]
