# Git write safety — repo-local hard rail (home-git-accident 방지)

> 실제 사고: 자동화가 잘못된 작업 디렉터리(HOME)에서 git write 를 실행하고
> 광범위한 `git add .` 로 홈 트리 전체를 staging 한 적이 있다. 본 문서는 그
> 사고 유형을 **코드로 불가능하게** 만드는 repo-local hard rail 의 SSoT 다.

코드 SSoT — [`agents/governance/git_path_safety.py`](../apps/engineering-agent/src/yule_engineering/agents/governance/git_path_safety.py).
회귀 — [`tests/governance/test_git_path_safety.py`](../tests/governance/test_git_path_safety.py).

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
(`test_no_broad_git_add_literal`).

## 4. 관련
- 전역 안전: [`/CLAUDE.md`](../CLAUDE.md) "Core Safety Rules"
- 거버넌스 hard rail: [`engineering-agent-governance.md`](engineering-agent-governance.md)
- commit/issue/PR 메시지 검증(별개 SSoT): `agents/governance/repo_write_policy.py`
