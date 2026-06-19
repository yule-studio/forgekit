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
    from ..cli import brain_cmd, provider_cmd, runtime_cmd

    return {"brain": brain_cmd, "provider": provider_cmd, "runtime": runtime_cmd}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forgekit",
        description="Forgekit provider-agnostic operator console + setup.",
    )
    parser.add_argument("--version", action="store_true", help="버전 출력 후 종료")
    # UI mode is accepted on the bare `forgekit` AND `forgekit console`.
    _add_ui_mode_flags(parser)
    sub = parser.add_subparsers(dest="command")
    console = sub.add_parser("console", help="운영자 콘솔 TUI 열기 (기본)")
    console.add_argument("--repo-root", help="status surface 의 기준 repo 경로")
    _add_ui_mode_flags(console)
    for module in _cli_modules().values():
        module.add_parser(sub)
    return parser


def _add_ui_mode_flags(p: argparse.ArgumentParser) -> None:
    """`--inline` / `--full` (also via FORGEKIT_UI_MODE=full|inline|auto)."""

    g = p.add_mutually_exclusive_group()
    g.add_argument("--inline", dest="ui_mode", action="store_const", const="inline",
                   help="inline 터미널 모드 (alt-screen 미사용 · native scrollback/선택 친화)")
    g.add_argument("--full", dest="ui_mode", action="store_const", const="full",
                   help="full-screen TUI (기본)")


def launch_console(*, repo_root: Path, ui_mode: Optional[str] = None) -> int:
    """Construct and run the Textual app in the resolved UI mode. Returns an exit code."""

    try:
        from ..tui.app import ForgekitConsoleApp
        from ..tui import ui_mode as uim
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

    mode = uim.resolve_ui_mode(os.environ, cli=ui_mode)
    app = ForgekitConsoleApp(repo_root=repo_root, inline=uim.is_inline(mode))
    app.run(**uim.run_kwargs(mode))
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
        return launch_console(repo_root=repo_root, ui_mode=getattr(args, "ui_mode", None))

    modules = _cli_modules()
    if args.command in modules:
        return modules[args.command].handle(args)

    parser.print_help()
    return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
