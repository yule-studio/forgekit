"""``pyright`` runner — Python type-check preflight (#100).

Invokes ``pyright --outputjson`` on the supplied paths and
translates each diagnostic. Pyright already classifies severity
(``error`` / ``warning`` / ``information`` / ``hint``) so the
mapping is direct.

When ``pyright`` is not installed the runner returns an advisory
:class:`LspResult` (``advisory=True``). Missing binary must
**never** block.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence

from ..lsp_preflight import (
    DEFAULT_TIMEOUT_SECONDS,
    LspFinding,
    LspResult,
    SEVERITY_ERROR,
    SEVERITY_HINT,
    SEVERITY_INFO,
    SEVERITY_WARNING,
)
from . import mask_stderr, run_subprocess, which_binary


_PYRIGHT_SEVERITY_MAP = {
    "error": SEVERITY_ERROR,
    "warning": SEVERITY_WARNING,
    "information": SEVERITY_INFO,
    "info": SEVERITY_INFO,
    "hint": SEVERITY_HINT,
}


@dataclass
class PythonPyrightRunner:
    """``pyright --outputjson`` wrapper."""

    runner_id: str = "python_pyright"
    language: str = "python"
    binary: str = "pyright"

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
                note="pyright binary missing — advisory only",
            )

        argv = [self.binary, "--outputjson", *paths]
        exit_code, stdout, stderr = run_subprocess(
            argv,
            timeout=min(timeout, DEFAULT_TIMEOUT_SECONDS),
            subprocess_runner=subprocess_runner,
        )

        findings = _parse_pyright_output(stdout)
        return LspResult(
            language=self.language,
            runner_id=self.runner_id,
            findings=tuple(findings),
            exit_code=exit_code,
            stderr_tail=mask_stderr(stderr),
        )


def _parse_pyright_output(stdout: str) -> List[LspFinding]:
    """Parse pyright's ``--outputjson`` envelope into findings."""

    if not stdout or not stdout.strip():
        return []
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []

    diagnostics = payload.get("generalDiagnostics")
    if not isinstance(diagnostics, list):
        return []

    findings: List[LspFinding] = []
    for entry in diagnostics:
        if not isinstance(entry, dict):
            continue
        file_path = str(entry.get("file") or "")
        severity_raw = str(entry.get("severity") or "").lower()
        severity = _PYRIGHT_SEVERITY_MAP.get(severity_raw, SEVERITY_INFO)
        message = str(entry.get("message") or "")
        rule = str(entry.get("rule") or "")
        line = 1
        column = 1
        range_value = entry.get("range")
        if isinstance(range_value, dict):
            start = range_value.get("start")
            if isinstance(start, dict):
                # pyright uses 0-based offsets; normalise to 1-based.
                line = _coerce_int(start.get("line"), default=0) + 1
                column = _coerce_int(start.get("character"), default=0) + 1
        findings.append(
            LspFinding(
                file=file_path,
                line=line,
                column=column,
                severity=severity,
                code=rule,
                message=message,
            )
        )
    return findings


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


__all__ = ("PythonPyrightRunner",)
