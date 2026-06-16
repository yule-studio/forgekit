"""`forgekit brain` — init / pack build / status.

Thin argparse glue over :mod:`brain.service`. Prints human output and returns an
exit code; all logic lives in the brain package.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

EXIT_OK = 0
EXIT_ERROR = 1


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    brain = subparsers.add_parser("brain", help="개인 브레인 / starter pack 관리")
    bsub = brain.add_subparsers(dest="brain_command", required=True)
    bsub.add_parser("init", help="개인 브레인 자동 생성 (idempotent)")
    pack = bsub.add_parser("pack", help="starter pack 관리")
    psub = pack.add_subparsers(dest="pack_command", required=True)
    build = psub.add_parser("build", help="source vault 로부터 read-only starter pack 빌드")
    build.add_argument("--source", required=True, help="source vault 경로(로컬 전체 vault 가능)")
    bsub.add_parser("status", help="브레인/팩 상태 출력")


def handle(args: argparse.Namespace) -> int:
    from ..brain import service

    now = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()
    command = getattr(args, "brain_command", None)

    if command == "init":
        brain = service.ensure_personal_brain(created_at=now)
        print(f"personal brain ready: {brain.base_dir}")
        print("write target = personal (starter/shared 는 read-only)")
        return EXIT_OK

    if command == "pack":
        if getattr(args, "pack_command", None) == "build":
            from ..brain.pack import PackBuildError

            try:
                manifest = service.build_pack_from(args.source, built_at=now)
            except PackBuildError as exc:
                print(f"pack build 실패: {exc}")
                return EXIT_ERROR
            print(f"starter pack built (read-only): {manifest.doc_count} docs, "
                  f"{manifest.total_bytes} bytes from {manifest.source_path}")
            return EXIT_OK
        return EXIT_ERROR

    if command == "status":
        st = service.brain_status()
        print(f"personal: {st['personal_path']}")
        print(f"  initialized={st['personal_initialized']} notes={st['personal_notes']}")
        print(f"starter:  {st['starter_path']}")
        if st["starter_built"]:
            s = st["starter"]
            print(f"  built={s['built_at']} docs={s['doc_count']} source={s['source_path']} (read-only)")
        else:
            print("  not built (forgekit brain pack build --source <vault>)")
        return EXIT_OK

    return EXIT_ERROR


__all__ = ("add_parser", "handle")
