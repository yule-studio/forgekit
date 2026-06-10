"""F13 digest CLI — 수동 probe.

사용 예:
  yule digest collect --role backend-engineer
  yule digest collect --role tech-lead --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Optional, Sequence

from .crawler import crawl_role
from .dedup_ledger import DigestDedupLedger
from .dispatcher import build_dispatch_plan
from .scheduler import SchedulerConfig, run_one_cycle
from .source_catalog import ROLE_SOURCE_CATALOG


logger = logging.getLogger(__name__)


def _parse_argv(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="yule digest")
    subparsers = parser.add_subparsers(dest="action", required=True)

    collect = subparsers.add_parser("collect", help="역할 카탈로그 1회 크롤")
    collect.add_argument(
        "--role",
        action="append",
        help="크롤할 role. 여러 번 지정 가능. 미지정 시 7 역할 전체.",
    )
    collect.add_argument("--dry-run", action="store_true", help="dispatch plan 만 출력")
    collect.add_argument("--retention-days", type=int, default=14)

    list_cmd = subparsers.add_parser("list-sources", help="카탈로그 출력")
    list_cmd.add_argument("--role", default=None)

    return parser.parse_args(list(argv) if argv is not None else None)


async def _run_collect(args) -> int:
    roles = args.role or list(ROLE_SOURCE_CATALOG.keys())
    ledger = DigestDedupLedger(retention_days=args.retention_days)

    report = await run_one_cycle(
        roles=roles,
        ledger=ledger,
        env=dict(os.environ),
        post_fn=None,  # CLI 는 dry-run / plan only
    )

    print(f"\n=== F13 digest collect ===")
    print(f"roles: {roles}")
    print(f"sources_attempted: {report.sources_attempted}")
    print(f"cards_total: {report.cards_total}")
    print(f"skipped_duplicates: {report.skipped_duplicates}")
    print(f"blocked_sources: {report.blocked_sources}")
    print(f"dispatch.targets: {len(report.dispatch.targets)}")
    print(f"dispatch.skipped_no_channel: {report.dispatch.skipped_no_channel}")
    print(f"dispatch.research_forum_threads: {len(report.dispatch.research_forum_threads)}")

    if args.dry_run:
        print("\n--- targets (preview) ---")
        for t in report.dispatch.targets[:20]:
            print(f"  [{t.target_kind}] {t.channel_name} ({t.channel_id})")
            print(f"    {t.card.title[:80]}")
            print(f"    {t.card.url}")
            print(f"    primary={t.card.dept_primary}, affected={t.card.affected_depts}, meeting={t.card.meeting_trigger}")

    return 0


def _run_list_sources(args) -> int:
    target_roles = [args.role] if args.role else list(ROLE_SOURCE_CATALOG.keys())
    for role in target_roles:
        sources = ROLE_SOURCE_CATALOG.get(role, ())
        print(f"\n=== {role} ({len(sources)} sources) ===")
        for src in sources:
            print(f"  - {src.host} [{src.kind}] trust={src.trust} → {src.feed_url}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_argv(argv)
    if args.action == "collect":
        return asyncio.run(_run_collect(args))
    if args.action == "list-sources":
        return _run_list_sources(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = ("main",)
