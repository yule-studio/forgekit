"""`forgekit provider` — list built-in providers / show slot resolution.

Thin argparse glue over :mod:`providers` + :mod:`policy`. Read-only: prints the
provider contract and how a policy mode resolves slots. No live submit.
"""

from __future__ import annotations

import argparse

EXIT_OK = 0
EXIT_ERROR = 1


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    provider = subparsers.add_parser("provider", help="provider 계약 / slot 정책 조회")
    psub = provider.add_subparsers(dest="provider_command", required=True)
    psub.add_parser("list", help="built-in provider 목록 출력")
    slots = psub.add_parser("slots", help="main provider + mode 의 slot 해석 출력")
    slots.add_argument("main", help="main provider id (claude/codex/gemini/ollama)")
    slots.add_argument(
        "--mode",
        default="hybrid",
        choices=("strict-single", "hybrid", "optimized"),
        help="policy mode (기본: hybrid)",
    )
    slots.add_argument(
        "--available",
        default="",
        help="콤마 구분 available provider id 목록 (optimized auto-pick 용)",
    )


def handle(args: argparse.Namespace) -> int:
    from ..policy import main_profile, provider_policy
    from ..providers import builtins

    command = getattr(args, "provider_command", None)

    if command == "list":
        for pid in builtins.BUILTIN_IDS:
            spec = builtins.BUILTIN_PROVIDERS[pid]
            flags = ",".join(spec.capability_flags)
            print(f"{spec.id:8} {spec.label:14} kind={spec.kind:10} "
                  f"auth={spec.auth_kind:8} usage={spec.usage_mode:12} [{flags}]")
        return EXIT_OK

    if command == "slots":
        main = args.main
        available = tuple(p.strip() for p in args.available.split(",") if p.strip())
        profile = main_profile.profile_for(main)
        mapping = provider_policy.resolve_slots(main, args.mode, available=available)
        print(f"main={main} mode={args.mode} lean={profile.agent_lean} "
              f"default_usage={profile.default_usage_mode}")
        for slot, pid in mapping.items():
            print(f"  {slot:14} → {pid}")
        for warning in profile.warnings:
            print(f"  ! {warning}")
        return EXIT_OK

    return EXIT_ERROR


__all__ = ("add_parser", "handle")
