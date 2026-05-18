#!/usr/bin/env python3
"""P1-N D — commit-msg hook entry point.

git ``commit-msg`` hook 으로 등록되면 사용자/봇이 만드는 모든 commit 이
``repo_write_policy.validate_commit_message`` 를 거친다. 위반 시 exit
code 1 + reason / detail 을 stderr 로 출력 → git 가 commit 거부.

설치:
  ln -sf ../../scripts/validate_commit_msg.py .git/hooks/commit-msg
  chmod +x scripts/validate_commit_msg.py

`is_initial` 판별은 ``is_initial_commit_context`` 가 git 디렉터리 자동
감지.  애매하면 ``initial_commit_detection_ambiguous`` 로 reject.

CI 환경에서도 동일하게 동작 가능 — `python3 scripts/validate_commit_msg.py
<message_file>` 으로 호출.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _detect_repo_root() -> str:
    # commit-msg hook 은 보통 .git 디렉터리의 부모에서 실행됨.
    cwd = Path.cwd()
    while True:
        if (cwd / ".git").exists():
            return str(cwd)
        if cwd.parent == cwd:
            return str(Path.cwd())
        cwd = cwd.parent


def main(argv) -> int:
    if len(argv) < 2:
        print(
            "validate_commit_msg: usage: validate_commit_msg.py <message-file>",
            file=sys.stderr,
        )
        return 2

    message_file = Path(argv[1])
    try:
        text = message_file.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"validate_commit_msg: cannot read {message_file}: {exc}", file=sys.stderr)
        return 2

    # ``git commit`` 자체적으로 leading "# ..." 코멘트 라인을 자른 후
    # COMMIT_EDITMSG 를 쓰지만 ``--message`` / amend 등은 raw 텍스트.
    # 코멘트 라인 제거 (safety).
    cleaned_lines = [
        ln for ln in text.splitlines() if not ln.lstrip().startswith("#")
    ]
    cleaned = "\n".join(cleaned_lines).strip("\n")

    if not cleaned.strip():
        print("validate_commit_msg: empty commit message", file=sys.stderr)
        return 1

    # Ensure src/ in path
    repo_root = _detect_repo_root()
    src_dir = Path(repo_root) / "src"
    if src_dir.is_dir():
        sys.path.insert(0, str(src_dir))

    try:
        from yule_orchestrator.agents.governance.repo_write_policy import (  # type: ignore
            is_initial_commit_context,
            validate_commit_message,
            validate_initial_commit_decision,
        )
    except Exception as exc:
        # validator 가 import 안되면 hook 이 정책을 강제할 수 없으므로 안전
        # 측 reject 보다는 통과 (env 가 잘못된 dev 환경에서 commit 자체가
        # 막히는 사고 방지).  대신 stderr 에 큰 경고.
        print(
            f"validate_commit_msg: WARNING — repo_write_policy import failed "
            f"({exc}); skipping policy check",
            file=sys.stderr,
        )
        return 0

    # initial commit 자동 감지.  CI / dev shell 모두에서 동작하도록 hint
    # env (YULE_COMMIT_IS_INITIAL=1/0) 도 지원.
    env_hint = os.environ.get("YULE_COMMIT_IS_INITIAL", "").strip().lower()
    explicit_hint = None
    if env_hint in ("1", "true", "yes"):
        explicit_hint = True
    elif env_hint in ("0", "false", "no"):
        explicit_hint = False
    decision = is_initial_commit_context(
        repo_root=repo_root, explicit_hint=explicit_hint
    )
    decision_check = validate_initial_commit_decision(decision)
    if not decision_check.ok:
        print(
            f"validate_commit_msg: {decision_check.reason}: {decision_check.detail}",
            file=sys.stderr,
        )
        print(
            "  hint: set YULE_COMMIT_IS_INITIAL=1 (or 0) to disambiguate.",
            file=sys.stderr,
        )
        return 1

    result = validate_commit_message(cleaned, is_initial=decision.is_initial)
    if result.ok:
        return 0

    print("─" * 60, file=sys.stderr)
    print(f"commit rejected — policy: {result.reason}", file=sys.stderr)
    print(f"  detail: {result.detail}", file=sys.stderr)
    if result.fields:
        for k, v in result.fields.items():
            print(f"  {k}: {v}", file=sys.stderr)
    print(
        "  SSoT: policies/reference/COMMIT_CONVENTION.md",
        file=sys.stderr,
    )
    print(
        "  Initial commit exception: title must be exactly `:tada: initial commit`",
        file=sys.stderr,
    )
    print("─" * 60, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
