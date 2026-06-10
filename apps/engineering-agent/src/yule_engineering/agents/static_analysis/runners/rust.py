"""``cargo clippy`` runner — Rust preflight (#100).

Invokes ``cargo clippy --message-format=json --quiet`` and parses
the streaming JSONL output. Each line is one cargo message; only
``compiler-message`` records with a non-empty diagnostic body are
forwarded as :class:`LspFinding`.

Missing binary → advisory :class:`LspResult` (``advisory=True``).
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


_RUST_SEVERITY_MAP = {
    "error": SEVERITY_ERROR,
    "warning": SEVERITY_WARNING,
    "note": SEVERITY_INFO,
    "help": SEVERITY_HINT,
}


@dataclass
class RustRunner:
    """``cargo clippy`` wrapper."""

    runner_id: str = "rust"
    language: str = "rust"
    binary: str = "cargo"

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
                note="cargo binary missing — advisory only",
            )

        # ``paths`` is treated as a list of crate roots; cargo
        # operates on the workspace by default so we just forward
        # any explicit ``--manifest-path`` markers the caller
        # included. Empty paths → workspace-level scan.
        argv = [self.binary, "clippy", "--message-format=json", "--quiet"]
        for path in paths or ():
            if str(path).endswith("Cargo.toml"):
                argv.extend(["--manifest-path", str(path)])

        exit_code, stdout, stderr = run_subprocess(
            argv,
            timeout=min(timeout, DEFAULT_TIMEOUT_SECONDS),
            subprocess_runner=subprocess_runner,
        )

        findings = _parse_clippy_output(stdout)
        return LspResult(
            language=self.language,
            runner_id=self.runner_id,
            findings=tuple(findings),
            exit_code=exit_code,
            stderr_tail=mask_stderr(stderr),
        )


def _parse_clippy_output(stdout: str) -> List[LspFinding]:
    if not stdout:
        return []
    findings: List[LspFinding] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("reason") != "compiler-message":
            continue
        message_block = payload.get("message")
        if not isinstance(message_block, dict):
            continue
        text = str(message_block.get("message") or "")
        if not text:
            continue
        severity_raw = str(message_block.get("level") or "").lower()
        severity = _RUST_SEVERITY_MAP.get(severity_raw, SEVERITY_INFO)
        code_block = message_block.get("code") or {}
        code = ""
        if isinstance(code_block, dict):
            code = str(code_block.get("code") or "")
        spans = message_block.get("spans") or []
        file_path = ""
        line_no = 1
        column_no = 1
        if isinstance(spans, list):
            for span in spans:
                if isinstance(span, dict) and span.get("is_primary"):
                    file_path = str(span.get("file_name") or "")
                    line_no = _coerce_int(span.get("line_start"), default=1)
                    column_no = _coerce_int(span.get("column_start"), default=1)
                    break
        findings.append(
            LspFinding(
                file=file_path,
                line=line_no,
                column=column_no,
                severity=severity,
                code=code,
                message=text,
            )
        )
    return findings


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result >= 1 else default


__all__ = ("RustRunner",)
