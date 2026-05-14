# Repo Contract Discovery — engineering-agent 부서 공통 (P0-G)

> **소유:** `engineering-agent` 부서 전체. 외부 repo (본 레포 외) 에서 코딩 작업을 받으면 그 repo 자체의 운영 규칙을 먼저 수집한 뒤 작업을 시작한다.
> **목적:** "Yule 기본 규칙" 을 다른 repo 에 강제 적용해 사용자가 원치 않는 산출물을 만드는 회귀를 막는다.
> **출처:** Issue #139 (parent #138) — 정책 8 종 1차 land.

본 정책은 [`governance.md`](governance.md) umbrella 가 묶는 부서 정책 중 **외부 repo 표면** 을 책임진다. 외부 repo 가 자체 규칙을 갖고 있으면 그 규칙이 우선. 없으면 Yule 기본 규칙 (본 레포의 [`github-workflow.md`](github-workflow.md), [`obsidian-governance.md`](obsidian-governance.md)) fallback.

## 1. 적용 범위

- 외부 repo 의 issue / PR / branch / commit 작업 직전 (작업 첫 commit 또는 첫 PR draft 직전).
- 본 레포 (`yule-studio-agent`) 자체에는 적용되지 않는다 — 본 레포는 SSoT.
- vault repo 가 있다면 (vault repo 도 외부 repo 로 취급) 동일 정책 적용.

## 2. RepoContract — discovery 결과 표현

수집 결과는 단일 dataclass-shape 으로 표현한다 (코드 land 는 stage 2 / #140 의 scope).

```text
RepoContract:
  owner: str                            # GitHub owner
  repo: str                             # GitHub repo name
  primary_branch: str                   # 보통 "main" / "master"
  issue_templates: tuple[Path, ...]     # .github/ISSUE_TEMPLATE/*
  pr_templates:    tuple[Path, ...]     # .github/PULL_REQUEST_TEMPLATE*
  contributing:   Optional[Path]        # CONTRIBUTING.md / .github/CONTRIBUTING.md
  readme:         Optional[Path]        # README*.md (root)
  codeowners:     Optional[Path]        # CODEOWNERS / .github/CODEOWNERS / docs/CODEOWNERS
  workflows:      tuple[Path, ...]      # .github/workflows/*.yml
  branch_protection_hint: Optional[str] # 워크플로 파일 / docs 에서 발견한 보호 규칙
  branch_strategy: Optional[str]        # "git-flow" / "trunk-based" / "github-flow" / "custom"
  commit_convention: Optional[str]      # CONTRIBUTING / commitlint / .gitmessage 에서 발견
  ssot_paths: tuple[Path, ...]          # 위 파일들이 발견된 path 목록
  fallback: bool                        # 위 contract 가 거의 없을 때 True
```

`fallback=True` 면 Yule 기본 규칙으로 작업하지만, **PR body 의 §📚 레퍼런스** 섹션에 "이 repo 에 자체 컨벤션이 없어 Yule 기본 규칙 사용 — 운영자 검토 요청" 한 줄을 명시.

## 3. 수집 우선순위

다음 순서로 탐색하고, 발견되는 즉시 `RepoContract` 의 해당 필드를 채운다. 모든 path 는 *해당 repo 의 base sha 기준*.

| 우선순위 | 경로 / 패턴 | 채워지는 필드 |
| --- | --- | --- |
| 1 | `.github/ISSUE_TEMPLATE/*.md`, `.github/ISSUE_TEMPLATE.md` | `issue_templates` |
| 2 | `.github/PULL_REQUEST_TEMPLATE*`, `PULL_REQUEST_TEMPLATE.md` | `pr_templates` |
| 3 | `CONTRIBUTING.md`, `.github/CONTRIBUTING.md`, `docs/CONTRIBUTING.md` | `contributing`, 종속적으로 `branch_strategy` / `commit_convention` |
| 4 | `README*.md` (repo root) | `readme`. branch strategy / merge 정책 hint 추출. |
| 5 | `CODEOWNERS`, `.github/CODEOWNERS`, `docs/CODEOWNERS` | `codeowners` |
| 6 | `.github/workflows/*.yml` | `workflows`. CI/CD 정책 + 보호 branch 신호 추출. |
| 7 | `.git-flow.cfg`, `.gitmessage`, `commitlint.config.*`, `.github/branch-rules.md` 등 | `branch_strategy`, `commit_convention` 보강 |
| 8 | repo 의 GitHub Settings API (branch protection) — 권한 있을 때만 | `branch_protection_hint` |

> **권한 부재 시:** GitHub Settings API 에 접근 불가능하면 `branch_protection_hint=None` 으로 두고, PR body 에 "branch protection 미확인" 명시. 추측 금지.

## 4. 우선순위 규칙 — repo vs Yule 기본

| 조건 | 따라야 할 규칙 |
| --- | --- |
| issue template 발견 | repo template 그대로 |
| PR template 발견 | repo template 그대로. Yule 의 PR body 4 섹션은 *추가 정보 nested* 로만 사용. |
| CONTRIBUTING 의 branch strategy 발견 | repo strategy 우선 |
| commit convention 발견 (CONTRIBUTING / commitlint / gitmessage) | repo convention 우선. Yule 의 한국어 3 섹션은 *비고* 로만 추가 가능. |
| CODEOWNERS 발견 | review 요청 시 그 owner 자동 mention |
| workflows 파일 발견 | CI 가 통과하지 못하면 머지하지 않음 (Yule 의 5-step merge gate 와 OR 가 아닌 AND) |
| 위 모두 없음 → `fallback=True` | Yule 기본 규칙 적용. PR 에 명시. |

본 표는 [`github-workflow.md`](github-workflow.md) §1~§5 와 충돌 시 **본 표가 외부 repo 에 한해 우선**. 본 레포 작업에는 영향 없음.

## 5. 산출물 위치

`RepoContract` 가 채워지면 다음 두 surface 에 기록:

1. **GitHub issue progress comment** — `### 0. Repo Contract` 섹션으로 1 회 명시 (해당 issue 의 첫 progress comment 만).
2. **세션 메모리** — `session.extra["repo_contract"]` 에 dict 로 round-trip. 추후 동일 repo 의 후속 issue 시 재사용.

## 6. discovery 가 실패할 수 있는 경우 + 대응

| 실패 모드 | 대응 |
| --- | --- |
| repo 접근 권한 없음 (private + token 없음) | 작업 중단. progress comment 에 "권한 없음" 명시 + 사용자에게 token 요청. fake success 금지. |
| `.github/` 미존재 | `fallback=True` 로 진행. PR body 에 명시. |
| 충돌하는 컨벤션 (예: README 와 CONTRIBUTING 이 다름) | CONTRIBUTING 이 우선. README 의 다른 hint 는 PR body 의 §📚 에 메모. |
| repo 의 default branch 가 `master` | primary_branch 그대로 따름. Yule 의 "main fallback" 가정 금지. |

## 7. 본 정책의 코드 land 단계

본 정책 자체는 *문서* 가 SSoT. 코드는 후속 (#140 stage 2) 가 land:

- `agents/git/repo_contract.py` (예정) — `RepoContract` dataclass + GitHub Apps + 로컬 git 클론 두 backend 모두 지원.
- `agents/engineering_conversation` → 세션 시작 시 RepoContract 수집 helper 호출.
- `policies/runtime/agents/engineering-agent/github-workflow.md` 의 PR body 가 RepoContract 결과를 PR Audit 블록에 surface.

## 8. 검증

`tests/engineering/test_policy_stack_completeness.py` (본 PR 신설) 가 본 정책 파일 존재 + 핵심 섹션 (§3 우선순위, §4 우선순위 규칙) 키워드를 lint.

## 9. 변경 이력

| 일자 | 변경 |
| --- | --- |
| 2026-05-14 | 초안 (Issue #139 — P0-G stage 1 정책 8종 1차 land. parent #138.) |

## 관련 문서

- [[CLAUDE]]
- [[governance]]
- [[github-workflow]]
- [[obsidian-governance]]
