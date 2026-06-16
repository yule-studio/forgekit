"""Static source guard — block a broad ``git add`` from re-entering the tree.

:mod:`git_path_safety` stops a broad stage at *runtime* (``assert_not_broad_stage``).
But a runtime guard only fires on code paths that actually call it; a new call
site that shells out to ``git add .`` directly would bypass it. This module is the
*static* complement: it scans source files for a broad-stage pattern so a re-
introduction is caught by a regression test / pre-merge sweep, not in production.

Scope: argv-list form (an ``add`` token followed by ``.`` / ``-A`` / ``--all``,
or a ``commit`` token followed by ``-a`` / ``--all``) and shell-string form
(``git add .`` / ``git add -A`` / ``git commit --all``). Prose mentions are
ignored — comment lines and lines that quote the command in backticks
(docstrings/comments explaining the ban) are skipped, so the guard flags
*executable* re-introductions only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence, Tuple

# argv-list form: "add", "." / 'add', '-A' / "commit", "--all"
_ARGV_ADD = re.compile(r"""["']add["']\s*,\s*["'](\.|-A|--all|:/|\./|\*)["']""")
_ARGV_COMMIT = re.compile(r"""["']commit["']\s*,\s*["'](-a|--all)["']""")
# shell-string form: git add . / git add -A / git commit -a
_SHELL_ADD = re.compile(r"\bgit\s+add\s+(\.|-A|--all)(\s|$|['\"])")
_SHELL_COMMIT = re.compile(r"\bgit\s+commit\s+(-a|--all)(\s|$|['\"])")

# Files allowed to *name* the pattern (the guards/docs themselves).
_ALLOWLISTED_BASENAMES: frozenset[str] = frozenset(
    {"git_source_audit.py", "git_path_safety.py"}
)


@dataclass(frozen=True)
class BroadStageFinding:
    """One source line that looks like an executable broad stage."""

    path: Path
    lineno: int
    text: str
    kind: str  # "argv_add" | "argv_commit" | "shell_add" | "shell_commit"


def _is_prose(line: str) -> bool:
    """True when a line is a comment or quotes the command in backticks.

    Docstrings/comments that explain the ban (e.g. ``no `git add .` here``)
    must not trip the guard — only executable usage should.
    """

    stripped = line.lstrip()
    if stripped.startswith("#"):
        return True
    if "`" in line:  # backtick-quoted command reference => prose
        return True
    return False


def scan_text_for_broad_stage(path: Path, text: str) -> Tuple[BroadStageFinding, ...]:
    if path.name in _ALLOWLISTED_BASENAMES:
        return ()
    findings: list[BroadStageFinding] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        if _is_prose(line):
            continue
        for kind, pat in (
            ("argv_add", _ARGV_ADD),
            ("argv_commit", _ARGV_COMMIT),
            ("shell_add", _SHELL_ADD),
            ("shell_commit", _SHELL_COMMIT),
        ):
            if pat.search(line):
                findings.append(
                    BroadStageFinding(
                        path=path, lineno=idx, text=line.strip(), kind=kind
                    )
                )
                break
    return tuple(findings)


def scan_source_for_broad_stage(
    roots: Iterable[Path],
    *,
    suffixes: Sequence[str] = (".py",),
    skip_dir_names: Sequence[str] = (".venv", ".git", "node_modules", "__pycache__"),
) -> Tuple[BroadStageFinding, ...]:
    """Walk *roots* and return every executable broad-stage finding.

    A regression test asserts this returns empty on a clean tree, and a CI /
    pre-merge sweep can fail when it does not.
    """

    skip = set(skip_dir_names)
    findings: list[BroadStageFinding] = []
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        for file in sorted(root.rglob("*")):
            if not file.is_file() or file.suffix not in suffixes:
                continue
            if any(part in skip for part in file.parts):
                continue
            try:
                text = file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            findings.extend(scan_text_for_broad_stage(file, text))
    return tuple(findings)


def render_findings(findings: Sequence[BroadStageFinding]) -> str:
    if not findings:
        return "no broad-stage source findings"
    lines = [f"{len(findings)} broad-stage source finding(s):"]
    for f in findings:
        lines.append(f"  {f.path}:{f.lineno} [{f.kind}] {f.text}")
    return "\n".join(lines)


__all__ = (
    "BroadStageFinding",
    "scan_text_for_broad_stage",
    "scan_source_for_broad_stage",
    "render_findings",
)
