# Git write safety — repo-local hard rail (home-git-accident 방지)

> 실제 사고: 자동화가 잘못된 작업 디렉터리(HOME)에서 git write 를 실행하고
> 광범위한 `git add .` 로 홈 트리 전체를 staging 한 적이 있다. 본 문서는 그
> 사고 유형을 **코드로 불가능하게** 만드는 repo-local hard rail 의 SSoT 다.

코드 SSoT — [`agents/governance/git_path_safety.py`](../apps/engineering-agent/src/yule_engineering/agents/governance/git_path_safety.py)
(runtime guard) + [`git_source_audit.py`](../apps/engineering-agent/src/yule_engineering/agents/governance/git_source_audit.py)
(static guard) + [`worktree_hygiene.py`](../apps/engineering-agent/src/yule_engineering/agents/governance/worktree_hygiene.py)
(worktree root hygiene).
회귀 — [`tests/governance/test_git_path_safety.py`](../tests/governance/test_git_path_safety.py)
+ [`test_worktree_hygiene.py`](../tests/governance/test_worktree_hygiene.py).

## 1. 규칙 (자동화의 모든 git write 에 적용)

1. **모든 git write 는 `git -C <검증된 repo path>` 형태**로만 실행한다.
   호출자의 cwd 에 의존하지 않는다(`cwd=` 로 git write 금지).
2. **write target path 검증** (`assert_safe_git_repo_path`) — 다음을 거부:
   - 빈 문자열 / `.` / `~` / 상대경로 (ambiguous)
   - 존재하지 않는 경로 / `.git` 없는 경로 (git repo 아님)
   - `$HOME` 자체 / `$HOME` 의 상위(`/`, `/Users` 등) — 너무 광범위
3. **broad stage 금지** (`assert_not_broad_stage`) — `git add .` / `-A` /
   `--all` / `:/` 및 `git commit -a/--all` 거부. 자동화는 **명시 pathspec**
   (`git add -- <path>`) 으로만 staging 한다.
4. **dry-run 은 미리보기** — 실제 write 가 없으므로 full guard 는 write 직전에만.
   dry-run 은 light sanity(절대경로/존재)만 본다.
5. **global/system 불변** — `~/.gitconfig` / `~/.config/git` 등은 건드리지 않는다.
   본 rail 은 *write target path* 만 검증한다.

## 2. 사용법

```python
from yule_engineering.agents.governance.git_path_safety import (
    assert_safe_git_repo_path, run_safe_git, safe_git_argv,
)

run_safe_git(repo_root, ["status", "--porcelain"])          # git -C <검증> status ...
run_safe_git(repo_root, ["add", "--", "notes/vault-mirror"]) # 명시 pathspec only
# run_safe_git(repo_root, ["add", "."])  -> BroadStageError
# run_safe_git("~",       ["status"])    -> UnsafeGitPathError
```

## 3. 적용 현황 (wiring)

| call site | 상태 |
| --- | --- |
| `agents/obsidian/git.py` `commit_single_file` | ✅ 단일 파일 stage + `git -C` + HOME guard(defense-in-depth) |
| `agents/obsidian/vault_auto_push.py` | ✅ `run_safe_git`(`git -C`) + write 직전 HOME guard + **scoped staging**(`notes/vault-mirror`, broad `add .` 제거) |
| `scripts/migrate_obsidian_filenames.py` `git_mv` | ✅ `run_safe_git` 경유 |

회귀 테스트가 **broad `git add .` / `-A` 재도입을 source-scan 으로 차단**한다
(`test_no_broad_git_add_literal` + `git_source_audit.scan_source_for_broad_stage`).
정적 스캐너는 argv-list / shell-string 양식을 모두 잡고, 백틱 prose / 주석은 건너뛴다.

## 3b. Worktree root hygiene (stale 정리 + 관측)

자동화 worktree 는 root 아래에 임시 디렉터리로 생성된다 — 코딩 executor 는
`/tmp/yule-coding-executor-worktrees`(env `YULE_CODING_EXECUTOR_WORKTREE_ROOT`),
self-improve 는 `<repo>/.cache/yule/self-improve-worktrees`(env
`YULE_SELF_IMPROVEMENT_WORKTREE_ROOT`). 런이 중간에 죽으면 child dir 가 샌다.

`worktree_hygiene` 가 이를 **파일시스템에서** 탐지하고 안전하게 정리한다:
- `detect_stale_worktree_dirs(root, stale_after_seconds, active_paths)` — root 의
  직속 child 중 mtime 이 임계 초과 + active 아님 → stale. mtime 못 읽으면 표면화.
- `plan_worktree_cleanup(...)` — **기본 dry-run**. `apply=True` 일 때만 삭제하고,
  매 타깃을 `assert_safe_cleanup_target` 으로 재검증 — allowlist root 의 직속 child
  가 아니거나 HOME / HOME 상위 / repo / `.git` / root 자체면 **거부**(삭제 안 함).
- `summarize_disk_usage(...)` — repo `.git` / `.cache` / `runs` + worktree root
  사용량 read-only 관측. 아무것도 변경하지 않는다.

운영 표면: `yule harness worktree-hygiene [--stale-hours N] [--execute --yes] [--json]`
— 기본 dry-run, `--execute --yes` 없으면 절대 삭제하지 않는다. repo 밖 정리는
**worktree-root allowlist** 를 통해서만, global/system / 홈 디렉터리는 건드리지 않는다.

## 4. 관련
- 전역 안전: [`/CLAUDE.md`](../CLAUDE.md) "Core Safety Rules"
- 거버넌스 hard rail: [`engineering-agent-governance.md`](engineering-agent-governance.md)
- commit/issue/PR 메시지 검증(별개 SSoT): `agents/governance/repo_write_policy.py`
