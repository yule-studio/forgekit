"""``go vet`` runner — Go preflight (#100).

Invokes ``go vet ./...`` (or the supplied paths) and parses the
``path:line:col: message`` diagnostic shape. ``go vet`` is the
lightweight default — operators who want a richer chain can swap
in ``golangci-lint`` by binding a custom registry, the parser
already accepts the same line shape.

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
)
from . import mask_stderr, run_subprocess, which_binary


# go vet line shape (and golangci-lint default):
#   path/to/file.go:42:9: shadow: declaration of "err" shadows ...
# column is optional when the tool didn't pinpoint it.
_GO_LINE_RE = re.compile(
    r"^(?P<file>[^:\n]+\.go):(?P<line>\d+)(?::(?P<col>\d+))?:\s*(?P<message>.+)$"
)


@dataclass
class GoRunner:
    """``go vet`` wrapper.

    Go does not separate "error" / "warning" in vet output —
    every diagnostic from ``go vet`` is treated as ``error`` so
    the verdict aggregates to a block when the binary has anything
    to say. Operators who want softer levelling can override the
    runner instance attribute after construction.
    """

    runner_id: str = "go"
    language: str = "go"
    binary: str = "go"
    default_severity: str = SEVERITY_ERROR

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
                note="go binary missing — advisory only",
            )

        argv = [self.binary, "vet", *(paths or ["./..."])]
        exit_code, stdout, stderr = run_subprocess(
            argv,
            timeout=min(timeout, DEFAULT_TIMEOUT_SECONDS),
            subprocess_runner=subprocess_runner,
        )

        # ``go vet`` emits diagnostics on stderr.
        findings = _parse_go_output(stderr, default_severity=self.default_severity)
        if not findings:
            findings = _parse_go_output(stdout, default_severity=self.default_severity)

        return LspResult(
            language=self.language,
            runner_id=self.runner_id,
            findings=tuple(findings),
            exit_code=exit_code,
            stderr_tail=mask_stderr(stderr),
        )


def _parse_go_output(text: str, *, default_severity: str) -> List[LspFinding]:
    if not text:
        return []
    findings: List[LspFinding] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _GO_LINE_RE.match(line)
        if match is None:
            continue
        col_raw = match.group("col")
        findings.append(
            LspFinding(
                file=match.group("file"),
                line=_coerce_int(match.group("line"), default=1),
                column=_coerce_int(col_raw, default=1) if col_raw else 1,
                severity=default_severity,
                code="",
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


__all__ = ("GoRunner",)
