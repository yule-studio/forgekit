"""Static analysis preflight seam — F9 / issue #100.

Role-aware wrapper around external LSP / linter / type-checker
binaries (ruff / pyright / mypy / tsc / golangci-lint / cargo-clippy).
Live LLM editor (F4) calls :func:`judge_lsp_preflight` between
retries so trivially-broken code never ships to the model again.

Hard rails (governance-tested):

  * The module **never** mutates target files — read-only analysis.
  * External binaries always run under a 30s timeout (env override).
  * Missing binary → advisory verdict, **never** a block.
  * ``YULE_LSP_PREFLIGHT_ENABLED`` defaults to ``false``; with the
    env OFF the verdict is always advisory and no subprocess runs.
  * Every subprocess ``stderr`` tail is passed through
    :func:`yule_security.paste_guard.guard_outbound`
    (``channel=VAULT``) so masked output is the only artefact that
    leaves the runner.
"""

from __future__ import annotations

from .lsp_preflight import (
    DEFAULT_TIMEOUT_SECONDS,
    LSP_ROLE_RUNNER_CHAINS,
    LspFinding,
    LspResult,
    LspRunnerProtocol,
    PreflightLspLevel,
    PreflightLspVerdict,
    is_lsp_preflight_enabled,
    judge_lsp_preflight,
    resolve_runner_chain,
)

__all__ = (
    "DEFAULT_TIMEOUT_SECONDS",
    "LSP_ROLE_RUNNER_CHAINS",
    "LspFinding",
    "LspResult",
    "LspRunnerProtocol",
    "PreflightLspLevel",
    "PreflightLspVerdict",
    "is_lsp_preflight_enabled",
    "judge_lsp_preflight",
    "resolve_runner_chain",
)
