"""`forgekit` console entrypoint.

``forgekit`` (bare) and ``forgekit console`` both open the operator console TUI.
The textual import is deferred to launch time and guarded, so a missing TUI
dependency yields a one-line install hint (exit 3) rather than a traceback — and
so importing this module for tests never requires textual.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Optional, Sequence

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISSING_TUI = 3

_INSTALL_HINT = (
    "forgekit console 는 textual 이 필요합니다.\n"
    "  설치: pip install -e '.[console]'   (또는: pip install textual)\n"
)


def resolve_repo_root(explicit: Optional[str] = None) -> Path:
    """Repo root for status surfaces: explicit > $YULE_REPO_ROOT > cwd."""

    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("YULE_REPO_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd().resolve()


# CLI subcommand modules — each exposes add_parser(subparsers) + handle(args).
# Registered here so `app.main` stays a thin dispatcher.
def _cli_modules() -> dict:
    from ..cli import brain_cmd, provider_cmd

    return {"brain": brain_cmd, "provider": provider_cmd}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forgekit",
        description="Forgekit provider-agnostic operator console + setup.",
    )
    parser.add_argument("--version", action="store_true", help="버전 출력 후 종료")
    sub = parser.add_subparsers(dest="command")
    console = sub.add_parser("console", help="운영자 콘솔 TUI 열기 (기본)")
    console.add_argument("--repo-root", help="status surface 의 기준 repo 경로")
    for module in _cli_modules().values():
        module.add_parser(sub)
    return parser


def launch_console(*, repo_root: Path) -> int:
    """Construct and run the Textual app. Returns an exit code."""

    try:
        from ..tui.app import ForgekitConsoleApp
    except ModuleNotFoundError as exc:
        # only swallow the *textual* import miss; re-raise anything else.
        if "textual" in str(exc).lower():
            import sys

            sys.stderr.write(_INSTALL_HINT)
            return EXIT_MISSING_TUI
        raise

    # Prime textual-image's backend BEFORE Textual starts: its sixel/TGP probe only
    # works while stdin is free, so this is what lets a capable terminal resolve to a
    # true raster instead of halfcell. Best-effort — never blocks launch.
    try:
        from ..tui import image_renderer

        image_renderer.prime_image_backend()
    except Exception:  # noqa: BLE001 - diagnostics priming must never break launch
        pass

    ForgekitConsoleApp(repo_root=repo_root).run()
    return EXIT_OK


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if getattr(args, "version", False):
        from .. import __version__

        print(f"forgekit {__version__}")
        return EXIT_OK

    # bare `forgekit` or `forgekit console` → open the console
    if args.command in (None, "console"):
        repo_root = resolve_repo_root(getattr(args, "repo_root", None))
        return launch_console(repo_root=repo_root)

    modules = _cli_modules()
    if args.command in modules:
        return modules[args.command].handle(args)

    parser.print_help()
    return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
