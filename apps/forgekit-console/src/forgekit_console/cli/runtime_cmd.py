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


def _repo_root(args) -> str:
    from ..app.main import resolve_repo_root

    return str(resolve_repo_root(getattr(args, "repo_root", "") or None))


def _build_tick_fn(repo_root: str):
    """A bounded tick: observe repo-local → always-on loop → TickOutcome."""

    from ..runtime.daemon import TickOutcome
    from ..runtime.loop import AUTONOMY_BOUNDED, BoundedRuntimeLoop, CAT_INFRA, Finding
    from ..sources import RepoLocalCollector

    def tick(n: int) -> TickOutcome:
        findings = []
        try:
            for it in RepoLocalCollector(repo_root).collect(limit=4):
                findings.append(Finding("forgekit", it.title,
                                        category="product", privileged=False))
        except Exception:  # noqa: BLE001
            pass
        # a representative privileged finding to prove the wait/notify path is bounded
        findings.append(Finding("forgekit", "운영/배포 준비 점검", category=CAT_INFRA, privileged=True))
        result = BoundedRuntimeLoop(autonomy=AUTONOMY_BOUNDED, max_iterations=8).run(findings)
        return TickOutcome(summary=f"tick {n}: {len(findings)} findings, {result.blocked_count} blocked",
                           waiting=result.waiting, blocked_count=result.blocked_count)

    return tick


def handle(args: argparse.Namespace) -> int:
    import os

    from ..runtime import heartbeat as HB
    from ..runtime.daemon import BoundedDaemon

    cmd = getattr(args, "runtime_command", None)

    if cmd == "status":
        hb = HB.read_heartbeat()
        print(f"forgekit runtime: {hb.status} · tick {hb.tick} · ts {hb.ts or '-'} · pid {hb.pid}")
        print(f"  kill switch: {'SET' if HB.is_killed() else 'clear'}")
        return EXIT_OK

    if cmd == "stop":
        HB.request_kill()
        print("kill switch 설정됨 — running serve 가 다음 tick 에 종료합니다.")
        return EXIT_OK

    repo = _repo_root(args)
    notifier = _build_notifier()
    daemon = BoundedDaemon(
        poll_interval=getattr(args, "interval", 60.0),
        max_ticks=getattr(args, "max_ticks", 0) or 0,
        notifier=notifier, pid=os.getpid())
    tick_fn = _build_tick_fn(repo)

    if cmd == "once":
        out = daemon.once(tick_fn)
        print(f"runtime once: {out.summary} · waiting={out.waiting}")
        return EXIT_OK

    if cmd == "serve":
        HB.clear_kill()   # fresh start
        print(f"forgekit runtime serve — interval {daemon.poll_interval}s, "
              f"max_ticks {daemon.max_ticks or '∞'} (Ctrl-C 또는 `forgekit runtime stop` 으로 종료)")
        res = daemon.serve(tick_fn)
        print(f"stopped: {res.stopped_reason} · ticks {res.ticks} · waits {res.waits} · notified {res.notified}")
        return EXIT_OK

    return EXIT_ERROR


def _build_notifier():
    try:
        from ..lifecycle.failure_escalation import notify_enabled
        from ..notify.service import NotificationService

        return NotificationService(desktop_enabled=notify_enabled())
    except Exception:  # noqa: BLE001
        return None


__all__ = ("add_parser", "handle")
