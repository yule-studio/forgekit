"""``tsc`` runner — TypeScript type-check preflight (#100).

Invokes ``tsc --noEmit --pretty false`` and parses the standard
``file(line,col): error TSxxxx: message`` diagnostic shape. The
``tsc`` family does not have a JSON output mode that ships with
every install, so we parse the canonical text format.

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
    SEVERITY_WARNING,
)
from . import mask_stderr, run_subprocess, which_binary


# tsc diagnostic shape:
#   src/foo.ts(12,5): error TS2322: Type 'string' is not assignable to 'number'.
_TSC_LINE_RE = re.compile(
    r"^(?P<file>[^()]+)\((?P<line>\d+),(?P<col>\d+)\):\s*"
    r"(?P<severity>error|warning)\s+(?P<code>TS\d+):\s*(?P<message>.+)$"
)


@dataclass
class TypescriptRunner:
    """``tsc --noEmit`` wrapper."""

    runner_id: str = "typescript"
    language: str = "typescript"
    binary: str = "tsc"

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
                note="tsc binary missing — advisory only",
            )

        argv = [self.binary, "--noEmit", "--pretty", "false", *paths]
        exit_code, stdout, stderr = run_subprocess(
            argv,
            timeout=min(timeout, DEFAULT_TIMEOUT_SECONDS),
            subprocess_runner=subprocess_runner,
        )

        # tsc emits diagnostics on stdout by default; some tool
        # wrappers route them to stderr. Parse both for robustness.
        findings = _parse_tsc_output(stdout)
        if not findings:
            findings = _parse_tsc_output(stderr)

        return LspResult(
            language=self.language,
            runner_id=self.runner_id,
            findings=tuple(findings),
            exit_code=exit_code,
            stderr_tail=mask_stderr(stderr),
        )


def _parse_tsc_output(text: str) -> List[LspFinding]:
    if not text:
        return []
    findings: List[LspFinding] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _TSC_LINE_RE.match(line)
        if match is None:
            continue
        severity_raw = match.group("severity")
        severity = SEVERITY_ERROR if severity_raw == "error" else SEVERITY_WARNING
        findings.append(
            LspFinding(
                file=match.group("file"),
                line=_coerce_int(match.group("line"), default=1),
                column=_coerce_int(match.group("col"), default=1),
                severity=severity,
                code=match.group("code"),
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


__all__ = ("TypescriptRunner",)
