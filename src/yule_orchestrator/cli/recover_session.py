"""P1-M E — `python3 -m yule_orchestrator.cli.recover_session <id>` CLI.

특정 session 한정 해킹 금지 — 모든 session 에 동일하게 동작하는
``recover_session_full`` 를 호출하는 얇은 wrapper.

사용 예시:

  # mode 만 prompt 에서 재파싱 + backlog seed
  python3 -m yule_orchestrator.cli.recover_session fe5eedc65196

  # mode + pr_merge 메타 같이 stamp (operator 가 PR 정보를 알 때)
  python3 -m yule_orchestrator.cli.recover_session fe5eedc65196 \\
      --mode autonomous_merge \\
      --pr-number 4 \\
      --pr-url https://github.com/yule-studio/naver-search-clone/pull/4 \\
      --head-sha abc1234 \\
      --repo yule-studio/naver-search-clone
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="yule-recover-session",
        description=(
            "Recover a session.extra to the latest wiring shape — re-parse "
            "mode tokens from prompt, seed coding_backlog, optionally stamp "
            "pr_merge_* metadata. Idempotent."
        ),
    )
    p.add_argument("session_id", help="target session id")
    p.add_argument(
        "--mode",
        choices=("autonomous_merge", "approval_required"),
        default=None,
        help="explicit work_mode override (prompt parse 결과 무시)",
    )
    p.add_argument(
        "--topology",
        choices=("single_repo", "multi_repo"),
        default=None,
    )
    p.add_argument(
        "--scope",
        choices=(
            "single_scope",
            "full_stack_single_repo",
            "layer_scoped",
            "cross_repo_program",
        ),
        default=None,
    )
    p.add_argument("--pr-number", type=int, default=None)
    p.add_argument("--pr-url", default=None)
    p.add_argument("--head-sha", default=None)
    p.add_argument("--repo", dest="repo_full_name", default=None)
    p.add_argument("--base-branch", default="main")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)

    from ..agents.lifecycle.session_recovery import recover_session_full

    report = recover_session_full(
        session_id=args.session_id,
        explicit_work_mode=args.mode,
        explicit_topology=args.topology,
        explicit_scope=args.scope,
        pr_number=args.pr_number,
        pr_url=args.pr_url,
        head_sha=args.head_sha,
        repo_full_name=args.repo_full_name,
        base_branch=args.base_branch,
    )

    print(f"session_id: {report.session_id}")
    print(f"found: {report.found}")
    if report.found:
        print(f"work_mode: {report.work_mode}")
        print(f"topology: {report.topology}")
        print(f"scope: {report.scope}")
        print(f"mode_persisted: {report.mode_persisted}")
        print(f"backlog_seeded_count: {report.backlog_seeded_count}")
        print(f"pr_merge_stamped: {report.pr_merge_stamped}")
        print("notes:")
        for note in report.notes:
            print(f"  - {note}")
        return 0
    else:
        print("notes:")
        for note in report.notes:
            print(f"  - {note}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
