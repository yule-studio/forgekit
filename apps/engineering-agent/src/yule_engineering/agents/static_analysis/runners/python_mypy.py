"""``mypy`` runner — Python type-check preflight (#100).

Invokes ``mypy --no-color-output --show-column-numbers`` and parses
its ``file:line:col: severity: message [code]`` line format. Mypy
does not emit JSON natively, so the parser must be tolerant of
``note``-only outputs (treated as ``info``) and stray summary
lines like ``Success: no issues found in 1 source file``.

Missing binary → advisory :class:`LspResult` (``advisory=True``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence

from ..lsp_preflight import (
    DEFAULT_TIMEOUT_SECONDS,
    LspFinding,
    LspResult,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
)
from . import mask_stderr, run_subprocess, which_binary


# Mypy diagnostic line shape:
#   path/to/file.py:42:7: error: Incompatible return value type  [return-value]
# Column may be absent — the optional group handles that.
_MYPY_LINE_RE = re.compile(
    r"^(?P<file>[^:\n]+):(?P<line>\d+)(?::(?P<col>\d+))?:\s*"
    r"(?P<severity>error|warning|note):\s*(?P<message>.+?)"
    r"(?:\s+\[(?P<code>[a-zA-Z0-9._-]+)\])?$"
)


_MYPY_SEVERITY_MAP = {
    "error": SEVERITY_ERROR,
    "warning": SEVERITY_WARNING,
    "note": SEVERITY_INFO,
}


@dataclass
class PythonMypyRunner:
    """``mypy`` text-output wrapper."""

    runner_id: str = "python_mypy"
    language: str = "python"
    binary: str = "mypy"

    def run(
        self,
        paths: Sequence[str],
        *,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        subprocess_runner: Optional[Callable[..., Any]] = None,
    ) -> LspResult:
        if which_binary(self.binary) is None:
            return LspResult(
                language=self.language,
                runner_id=self.runner_id,
                findings=(),
                exit_code=None,
                stderr_tail="",
                advisory=True,
                note="mypy binary missing — advisory only",
            )

        argv = [
            self.binary,
            "--no-color-output",
            "--show-column-numbers",
            *paths,
        ]
        exit_code, stdout, stderr = run_subprocess(
            argv,
            timeout=min(timeout, DEFAULT_TIMEOUT_SECONDS),
            subprocess_runner=subprocess_runner,
        )

        findings = _parse_mypy_output(stdout)
        return LspResult(
            language=self.language,
            runner_id=self.runner_id,
            findings=tuple(findings),
            exit_code=exit_code,
            stderr_tail=mask_stderr(stderr),
        )


def _parse_mypy_output(stdout: str) -> List[LspFinding]:
    if not stdout:
        return []
    findings: List[LspFinding] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _MYPY_LINE_RE.match(line)
        if match is None:
            continue
        severity = _MYPY_SEVERITY_MAP.get(match.group("severity"), SEVERITY_INFO)
        column_raw = match.group("col")
        column = _coerce_int(column_raw, default=1) if column_raw else 1
        findings.append(
            LspFinding(
                file=match.group("file"),
                line=_coerce_int(match.group("line"), default=1),
                column=column,
                severity=severity,
                code=match.group("code") or "",
                message=match.group("message").strip(),
            )
        )
    return findings


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result >= 1 else default


__all__ = ("PythonMypyRunner",)
