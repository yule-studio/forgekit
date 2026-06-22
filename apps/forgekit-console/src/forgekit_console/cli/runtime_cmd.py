"""`forgekit runtime` — the long-running bounded daemon (WT4).

  forgekit runtime serve [--interval N] [--max-ticks N] [--repo-root P]
  forgekit runtime once  [--repo-root P]
  forgekit runtime status
  forgekit runtime stop      # write the kill switch (a running serve exits next tick)

serve runs a REAL local loop (observe → bounded always-on cycle → wait), heartbeating
each tick and notifying the operator on approval-needed. It is bounded autonomy — no
privileged action; deploy/secret/infra stay runbook+approval. macOS sleep (lid close)
SUSPENDS the process (honest — see docs); homeserver/Linux+systemd is the 1st-class
always-on path.
"""

from __future__ import annotations

import argparse

EXIT_OK = 0
EXIT_ERROR = 1


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    rt = subparsers.add_parser("runtime", help="long-running bounded 운영 데몬")
    rsub = rt.add_subparsers(dest="runtime_command", required=True)
    serve = rsub.add_parser("serve", help="장시간 loop 시작 (observe→cycle→wait)")
    serve.add_argument("--interval", type=float, default=60.0, help="poll 간격(초, 기본 60)")
    serve.add_argument("--max-ticks", type=int, default=0, help="최대 tick (0=kill/신호까지)")
    serve.add_argument("--repo-root", default="", help="관측 대상 repo 경로")
    once = rsub.add_parser("once", help="단일 tick 실행")
    once.add_argument("--repo-root", default="")
    rsub.add_parser("status", help="heartbeat 상태 출력")
    rsub.add_parser("stop", help="kill switch 설정 (running serve 가 다음 tick 에 종료)")
    inst = rsub.add_parser(
        "install-unit",
        help="always-on supervisor unit(launchd/systemd) 렌더 + 설치",
    )
    grp = inst.add_mutually_exclusive_group()
    grp.add_argument("--launchd", action="store_true", help="macOS LaunchAgent 강제")
    grp.add_argument("--systemd", action="store_true", help="Linux systemd --user 강제")
    inst.add_argument("--dry-run", action="store_true",
                      help="렌더된 unit + 설치 명령만 출력 (아무것도 실행/기록 안 함)")
    inst.add_argument("--repo-root", default="", help="데몬이 관측할 repo 경로")
    inst.add_argument("--interval", type=int, default=300,
                      help="serve poll 간격(초, 기본 300)")


def _repo_root(args) -> str:
    from ..app.main import resolve_repo_root

    return str(resolve_repo_root(getattr(args, "repo_root", "") or None))


def _build_tick_fn(repo_root: str):
    """A bounded tick that DRIVES autopilot execution + approved-goal execution.

    Two bounded passes per tick, composed so ``forgekit runtime serve`` reaches both:

    1. **autopilot pass (WT2 #241)** — observe repo-local → internal chain → safe-class
       real mutation (BoundedMutator) → verify → record, with dedupe + cooldown.
    2. **goal-exec pass (G1)** — load ACTIVE (operator-approved) goals from the GoalStore
       and physically run their linked safe-class packets via ``apply_approved_packet``
       (3-gate + BoundedMutator; risky/destructive recorded-not-executed; idempotent).

    The combined ``TickOutcome`` merges both so heartbeat/status surface what each did.
    """

    from pathlib import Path

    from ..runtime.autopilot_tick import AutopilotTicker
    from ..runtime.goal_exec_tick import GoalExecTicker

    autopilot = AutopilotTicker(repo_root=Path(repo_root)).tick_fn()
    goal_exec = GoalExecTicker(repo_root=Path(repo_root)).tick_fn()

    def _combined(n: int):
        from ..runtime.daemon import TickOutcome

        a = autopilot(n)
        g = goal_exec(n)
        summary = a.summary
        if g.summary:
            summary = f"{summary} | {g.summary}" if summary else g.summary
        return TickOutcome(
            summary=summary,
            waiting=a.waiting or g.waiting,
            blocked_count=a.blocked_count + g.blocked_count,
            executed=a.executed + g.executed,
            executed_paths=tuple(a.executed_paths) + tuple(g.executed_paths),
            skipped_reason=a.skipped_reason,
            next_eligible_tick=a.next_eligible_tick,
        )

    return _combined


def handle(args: argparse.Namespace) -> int:
    import os

    from ..runtime import heartbeat as HB
    from ..runtime.daemon import BoundedDaemon

    cmd = getattr(args, "runtime_command", None)

    if cmd == "status":
        hb = HB.read_heartbeat()
        print(f"forgekit runtime: {hb.status} · tick {hb.tick} · ts {hb.ts or '-'} · pid {hb.pid}")
        if hb.note:  # last tick: exec/propose/skip + written paths (WT2 #241)
            print(f"  last tick: {hb.note}")
        print(f"  kill switch: {'SET' if HB.is_killed() else 'clear'}")
        return EXIT_OK

    if cmd == "stop":
        HB.request_kill()
        print("kill switch 설정됨 — running serve 가 다음 tick 에 종료합니다.")
        return EXIT_OK

    if cmd == "install-unit":
        return _handle_install_unit(args)

    repo = _repo_root(args)
    notifier = _build_notifier()
    daemon = BoundedDaemon(
        poll_interval=getattr(args, "interval", 60.0),
        max_ticks=getattr(args, "max_ticks", 0) or 0,
        notifier=notifier, pid=os.getpid())
    tick_fn = _build_tick_fn(repo)

    if cmd == "once":
        out = daemon.once(tick_fn)
        print(f"runtime once: {out.summary} · executed={out.executed} · waiting={out.waiting}")
        if out.executed_paths:
            print("  wrote: " + ", ".join(out.executed_paths))
        return EXIT_OK

    if cmd == "serve":
        HB.clear_kill()   # fresh start
        print(f"forgekit runtime serve — interval {daemon.poll_interval}s, "
              f"max_ticks {daemon.max_ticks or '∞'} (Ctrl-C 또는 `forgekit runtime stop` 으로 종료)")
        res = daemon.serve(tick_fn)
        print(f"stopped: {res.stopped_reason} · ticks {res.ticks} · executed {res.executed} "
              f"· waits {res.waits} · notified {res.notified}")
        return EXIT_OK

    return EXIT_ERROR


def _handle_install_unit(args: argparse.Namespace) -> int:
    """Render + install the always-on supervisor unit (lane B / axis 4).

    Rendering is pure (`unit_install.build_plan`); the install side-effect runs
    through an injectable runner. Default-safe: `--dry-run` prints the rendered
    unit + exact commands and executes NOTHING.
    """

    import os
    import shutil
    from pathlib import Path

    from . import unit_install as U

    if getattr(args, "launchd", False):
        backend = U.LAUNCHD
    elif getattr(args, "systemd", False):
        backend = U.SYSTEMD
    else:
        backend = U.detect_backend()

    repo_root = Path(_repo_root(args))
    user_home = Path.home()
    forgekit_home = Path(
        os.environ.get("FORGEKIT_HOME") or (user_home / ".forgekit")
    )
    forgekit_bin = shutil.which("forgekit") or "forgekit"

    try:
        plan = U.build_plan(
            backend=backend,
            repo_root=repo_root,
            forgekit_bin=forgekit_bin,
            forgekit_home=forgekit_home,
            user_home=user_home,
            interval=int(getattr(args, "interval", 300)),
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"install-unit 실패: {exc}")
        return EXIT_ERROR

    return U.apply_plan(plan, dry_run=bool(getattr(args, "dry_run", False)))


def _build_notifier():
    try:
        from ..lifecycle.failure_escalation import notify_enabled
        from ..notify.service import NotificationService

        return NotificationService(desktop_enabled=notify_enabled())
    except Exception:  # noqa: BLE001
        return None


__all__ = ("add_parser", "handle")
