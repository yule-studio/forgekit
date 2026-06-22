"""Render + install the ForgeKit always-on supervisor unit (lane B / axis 4).

Automates the previously-manual `sed` + `launchctl bootstrap` (macOS) /
`cp` + `systemctl --user enable` (Linux) dance behind one command:

    forgekit runtime install-unit [--launchd|--systemd] [--dry-run]
                                  [--repo-root PATH] [--interval N]

Design: the **rendering** (template → concrete unit text + the exact install
commands + the target path) is a PURE function (`build_plan`), with NO side
effects, so it is fully testable without touching the real system. The
**side-effecting** install (`apply_plan`) writes the unit file and runs the
supervisor commands through an *injectable* runner — tests inject a fake runner
and assert that dry-run constructs the right command strings WITHOUT executing
launchctl/systemctl.

Default-safe: `--dry-run` prints the rendered unit + the exact commands and
executes NOTHING. A real install requires the explicit non-dry-run invocation.

HONEST macOS limit: a closed lid suspends the host — a LaunchAgent does NOT keep
running through clamshell sleep. For true 24h, run lid-open with `caffeinate -s`
(or `sudo pmset -c sleep 0`), or use the Linux/systemd 1급 always-on path. See
deploy/launchd/README.md.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Sequence

# Supervisor backends.
LAUNCHD = "launchd"
SYSTEMD = "systemd"

# Template locations, relative to the repo root.
_LAUNCHD_TEMPLATE = ("deploy", "launchd", "com.forgekit.runtime.plist")
_SYSTEMD_TEMPLATE = ("deploy", "systemd", "forgekit-runtime.service")

_LAUNCHD_LABEL = "com.forgekit.runtime"
_SYSTEMD_UNIT = "forgekit-runtime.service"

# Runner type: takes an argv list, returns exit code. Injected so tests can
# assert "no real launchctl/systemctl was executed".
Runner = Callable[[Sequence[str]], int]


# --------------------------------------------------------------------------- #
# Pure rendering (no side effects — safe to call in tests)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class InstallPlan:
    """Everything needed to install the unit, computed purely.

    ``rendered`` is the substituted unit text; ``target`` is where it must be
    written; ``install_commands`` are the supervisor commands to run AFTER the
    file is in place (load / enable / start); ``mkdirs`` are directories that
    must exist first (e.g. the launchd log dir, LaunchAgents dir).
    """

    backend: str
    target: Path
    rendered: str
    install_commands: List[Sequence[str]] = field(default_factory=list)
    restart_commands: List[Sequence[str]] = field(default_factory=list)
    mkdirs: List[Path] = field(default_factory=list)


def detect_backend() -> str:
    """Default supervisor for the current platform: macOS→launchd, else systemd."""

    return LAUNCHD if sys.platform == "darwin" else SYSTEMD


def templates_root() -> Path:
    """Repo root that holds the checked-in deploy/ templates.

    Resolved from THIS module's location (the package source lives inside the
    checkout), NOT from the daemon's ``--repo-root`` — those can differ, and the
    templates ship with the source, not with whatever repo the daemon observes.
    """

    # …/apps/forgekit-console/src/forgekit_console/cli/unit_install.py → repo root
    return Path(__file__).resolve().parents[5]


def _read_template(templates_dir: Path, parts: Sequence[str]) -> str:
    path = templates_dir.joinpath(*parts)
    if not path.exists():
        raise FileNotFoundError(f"template not found: {path}")
    return path.read_text(encoding="utf-8")


def _substitute(text: str, mapping: dict) -> str:
    """sed-equivalent placeholder substitution, in Python."""

    for token, value in mapping.items():
        text = text.replace(token, value)
    return text


def render_launchd(
    *,
    repo_root: Path,
    forgekit_bin: str,
    forgekit_home: Path,
    user_home: Path,
    interval: int,
    templates_dir: Path | None = None,
) -> str:
    """Render the launchd plist template with the given substitutions (pure)."""

    template = _read_template(templates_dir or templates_root(), _LAUNCHD_TEMPLATE)
    return _substitute(
        template,
        {
            "__FORGEKIT_BIN__": forgekit_bin,
            "__REPO_ROOT__": str(repo_root),
            "__FORGEKIT_HOME__": str(forgekit_home),
            "__USER_HOME__": str(user_home),
            "__INTERVAL__": str(interval),
        },
    )


def render_systemd(
    *,
    repo_root: Path,
    forgekit_bin: str,
    forgekit_home: Path,
    interval: int,
    templates_dir: Path | None = None,
) -> str:
    """Render the systemd unit template with the given substitutions (pure)."""

    template = _read_template(templates_dir or templates_root(), _SYSTEMD_TEMPLATE)
    return _substitute(
        template,
        {
            "__FORGEKIT_BIN__": forgekit_bin,
            "__REPO_ROOT__": str(repo_root),
            "__FORGEKIT_HOME__": str(forgekit_home),
            "__INTERVAL__": str(interval),
        },
    )


def build_plan(
    *,
    backend: str,
    repo_root: Path,
    forgekit_bin: str,
    forgekit_home: Path,
    user_home: Path,
    interval: int,
    templates_dir: Path | None = None,
) -> InstallPlan:
    """Compute the full install plan (rendered unit + commands + target). PURE."""

    if backend == LAUNCHD:
        rendered = render_launchd(
            repo_root=repo_root,
            forgekit_bin=forgekit_bin,
            forgekit_home=forgekit_home,
            user_home=user_home,
            interval=interval,
            templates_dir=templates_dir,
        )
        target = user_home / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"
        domain = f"gui/{os.getuid() if hasattr(os, 'getuid') else 0}"
        log_dir = user_home / "Library" / "Logs" / "forgekit"
        return InstallPlan(
            backend=LAUNCHD,
            target=target,
            rendered=rendered,
            mkdirs=[target.parent, log_dir],
            # bootout-then-bootstrap = idempotent reload of an already-loaded unit.
            install_commands=[
                ["launchctl", "bootout", domain, str(target)],
                ["launchctl", "bootstrap", domain, str(target)],
            ],
            restart_commands=[
                ["launchctl", "kickstart", "-k", f"{domain}/{_LAUNCHD_LABEL}"],
            ],
        )

    if backend == SYSTEMD:
        rendered = render_systemd(
            repo_root=repo_root,
            forgekit_bin=forgekit_bin,
            forgekit_home=forgekit_home,
            interval=interval,
            templates_dir=templates_dir,
        )
        target = user_home / ".config" / "systemd" / "user" / _SYSTEMD_UNIT
        return InstallPlan(
            backend=SYSTEMD,
            target=target,
            rendered=rendered,
            mkdirs=[target.parent],
            install_commands=[
                ["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "enable", "--now", _SYSTEMD_UNIT],
            ],
            restart_commands=[
                ["systemctl", "--user", "restart", _SYSTEMD_UNIT],
            ],
        )

    raise ValueError(f"unknown backend: {backend!r}")


# --------------------------------------------------------------------------- #
# Side-effecting install (injectable runner — testable)
# --------------------------------------------------------------------------- #
def _default_runner(argv: Sequence[str]) -> int:
    import subprocess

    return subprocess.run(list(argv), check=False).returncode


def _fmt_cmd(argv: Sequence[str]) -> str:
    return " ".join(argv)


def apply_plan(
    plan: InstallPlan,
    *,
    dry_run: bool,
    out=print,
    runner: Runner | None = None,
    writer: Callable[[Path, str], None] | None = None,
    mkdir: Callable[[Path], None] | None = None,
) -> int:
    """Print the plan; when not dry-run, write the unit + run install commands.

    ``runner`` / ``writer`` / ``mkdir`` are injectable so tests can assert that
    dry-run touches NOTHING and a real run issues exactly the planned commands.
    Idempotent: writing the same rendered unit + bootout-then-bootstrap (launchd)
    / enable --now (systemd) is safe to repeat.
    """

    runner = runner or _default_runner

    out(f"# backend: {plan.backend}")
    out(f"# target:  {plan.target}")
    out("# --- rendered unit ---")
    out(plan.rendered.rstrip("\n"))
    out("# --- install commands ---")
    for d in plan.mkdirs:
        out(f"mkdir -p {d}")
    out(f"write -> {plan.target}")
    for cmd in plan.install_commands:
        out(_fmt_cmd(cmd))
    out("# --- restart (after `git pull` + reinstall) ---")
    for cmd in plan.restart_commands:
        out(_fmt_cmd(cmd))

    if dry_run:
        out("# DRY-RUN: nothing written, no launchctl/systemctl executed.")
        return 0

    # Real install.
    _mkdir = mkdir or (lambda p: p.mkdir(parents=True, exist_ok=True))
    _write = writer or (lambda p, text: p.write_text(text, encoding="utf-8"))
    for d in plan.mkdirs:
        _mkdir(d)
    _write(plan.target, plan.rendered)
    out(f"wrote {plan.target}")
    rc = 0
    for cmd in plan.install_commands:
        out(f"$ {_fmt_cmd(cmd)}")
        rc = runner(cmd)
        if rc != 0:
            out(f"command failed (exit {rc}): {_fmt_cmd(cmd)}")
            return rc
    out("installed. status: forgekit runtime status")
    return rc


__all__ = (
    "LAUNCHD",
    "SYSTEMD",
    "InstallPlan",
    "Runner",
    "apply_plan",
    "build_plan",
    "detect_backend",
    "render_launchd",
    "render_systemd",
)
