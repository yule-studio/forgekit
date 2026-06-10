"""``ruff`` runner — Python lint preflight (#100).

Invokes ``ruff check --output-format=json`` on the supplied paths
and translates each finding into :class:`LspFinding`. Severity
maps as follows:

  * fix-applicable errors (rule codes starting ``E`` / ``F``) →
    ``error`` so the verdict aggregates to a block.
  * Other lint rules → ``warning`` so the retry loop sees them
    but does not escalate to approval.

When ``ruff`` is not installed the runner returns an advisory
:class:`LspResult` (``advisory=True``). This is the hard rail
pinned by governance: missing binary must **never** block.
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
    SEVERITY_WARNING,
)
from . import mask_stderr, run_subprocess, which_binary


@dataclass
class PythonRuffRunner:
    """``ruff check --output-format=json`` wrapper."""

    runner_id: str = "python_ruff"
    language: str = "python"
    binary: str = "ruff"

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
                note="ruff binary missing — advisory only",
            )

        argv = [self.binary, "check", "--output-format=json", *paths]
        exit_code, stdout, stderr = run_subprocess(
            argv,
            timeout=min(timeout, DEFAULT_TIMEOUT_SECONDS),
            subprocess_runner=subprocess_runner,
        )

        findings = _parse_ruff_output(stdout)
        return LspResult(
            language=self.language,
            runner_id=self.runner_id,
            findings=tuple(findings),
            exit_code=exit_code,
            stderr_tail=mask_stderr(stderr),
        )


def _parse_ruff_output(stdout: str) -> List[LspFinding]:
    """Translate ruff JSON output into :class:`LspFinding` records.

    Ruff emits a JSON array of dicts with ``code`` / ``message`` /
    ``location`` (``row`` + ``column``) / ``filename``. We treat
    rule codes whose prefix is ``E`` or ``F`` as errors (syntax /
    pyflakes); everything else is a warning. Malformed output is
    silently ignored — the audit chain still records the runner
    invocation so the gap is visible.
    """

    if not stdout or not stdout.strip():
        return []
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(payload, list):
        return []

    findings: List[LspFinding] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("code") or "")
        message = str(entry.get("message") or "")
        filename = str(entry.get("filename") or "")
        location = entry.get("location") or {}
        if not isinstance(location, dict):
            location = {}
        line = _coerce_int(location.get("row"), default=1)
        column = _coerce_int(location.get("column"), default=1)
        severity = _ruff_severity(code)
        findings.append(
            LspFinding(
                file=filename,
                line=line,
                column=column,
                severity=severity,
                code=code,
                message=message,
            )
        )
    return findings


def _ruff_severity(code: str) -> str:
    head = (code[:1] or "").upper()
    if head in {"E", "F"}:
        return SEVERITY_ERROR
    return SEVERITY_WARNING


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result >= 1 else default


__all__ = ("PythonRuffRunner",)
