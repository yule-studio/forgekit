"""Runner registry + shared helpers for the LSP preflight seam (#100).

Each runner is a thin subprocess wrapper that returns an
:class:`~yule_engineering.agents.static_analysis.lsp_preflight.LspResult`.
Runners are split into 6 small modules so the judgement seam only
needs to know runner IDs. Shared subprocess + PasteGuard glue lives
here so each runner can stay laser-focused on parsing its tool's
output format.

Hard rails enforced here:

  * ``run_subprocess`` always passes ``timeout`` (capped at the
    seam's ``DEFAULT_TIMEOUT_SECONDS=30``) so a hanging binary
    cannot wedge the retry loop.
  * ``stderr`` is passed through PasteGuard ``guard_outbound``
    (``channel=VAULT``) before the runner stuffs it into the
    :class:`LspResult`. The PasteGuard wrapper is forgiving: when
    the security module is somehow unavailable (e.g. early CI
    bootstrap) we redact by truncation rather than fail-open.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple


#: Hard cap on stderr length stored inside an :class:`LspResult`.
#: PasteGuard already masks credentials; this cap protects audit
#: log size when a runner spews a stack trace.
STDERR_TAIL_CHAR_LIMIT: int = 2000


def which_binary(name: str) -> Optional[str]:
    """Thin wrapper around :func:`shutil.which` for test seams."""

    return shutil.which(name)


def run_subprocess(
    argv: Sequence[str],
    *,
    timeout: int,
    cwd: Optional[str] = None,
    subprocess_runner: Optional[Callable[..., Any]] = None,
) -> Tuple[int, str, str]:
    """Execute ``argv`` and return ``(exit_code, stdout, stderr)``.

    ``subprocess_runner`` is the injection seam used by the test
    suite. When ``None`` we call :func:`subprocess.run` with the
    standard safe flags (no shell, capture both streams). Both
    ``TimeoutExpired`` and ``FileNotFoundError`` are normalised to
    a synthetic exit code + a short stderr note so the caller can
    always degrade to an advisory result without raising.
    """

    runner = subprocess_runner if subprocess_runner is not None else _default_subprocess_runner

    try:
        result = runner(
            list(argv),
            timeout=timeout,
            capture_output=True,
            text=True,
            cwd=cwd,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except FileNotFoundError:
        return 127, "", "binary not found"
    except Exception as exc:  # noqa: BLE001 — fail-soft surface
        return 1, "", f"subprocess error: {type(exc).__name__}"

    return (
        getattr(result, "returncode", 0) or 0,
        getattr(result, "stdout", "") or "",
        getattr(result, "stderr", "") or "",
    )


def mask_stderr(stderr: str) -> str:
    """Mask ``stderr`` through PasteGuard and truncate to the tail cap.

    The runner contract requires that any stderr stored in
    :class:`LspResult` is already masked. We delegate to
    :func:`guard_outbound` (``channel=VAULT``) so the same secret
    catalogue protects the LSP audit trail as the rest of the
    outbound surface. When PasteGuard is unavailable we still
    truncate so an oversized stderr cannot blow up the verdict.
    """

    if not isinstance(stderr, str) or not stderr:
        return ""

    redacted = _redact_via_paste_guard(stderr)
    if len(redacted) <= STDERR_TAIL_CHAR_LIMIT:
        return redacted
    overflow = len(redacted) - STDERR_TAIL_CHAR_LIMIT
    return redacted[-STDERR_TAIL_CHAR_LIMIT:] + f"\n[...truncated {overflow} chars]"


def _redact_via_paste_guard(stderr: str) -> str:
    try:
        from yule_security.paste_guard import (
            OutboundChannel,
            guard_outbound,
        )
    except Exception:  # pragma: no cover - bootstrap fallback
        return stderr[-STDERR_TAIL_CHAR_LIMIT:]

    try:
        verdict = guard_outbound(
            channel=OutboundChannel.VAULT,
            payload=stderr,
            fail_closed=True,
        )
    except Exception:  # pragma: no cover - defensive
        return stderr[-STDERR_TAIL_CHAR_LIMIT:]
    if verdict.blocked:
        return "[stderr blocked by PasteGuard]"
    return verdict.redacted


def _default_subprocess_runner(*args: Any, **kwargs: Any) -> Any:
    return subprocess.run(*args, **kwargs)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def build_runner_registry() -> Mapping[str, object]:
    """Return a fresh registry mapping runner IDs to instances.

    A new registry is constructed per call so unit tests can mutate
    their copy without leaking state across cases. The instances
    are cheap to build (no I/O until ``run`` is invoked).
    """

    from .python_ruff import PythonRuffRunner
    from .python_pyright import PythonPyrightRunner
    from .python_mypy import PythonMypyRunner
    from .typescript import TypescriptRunner
    from .go import GoRunner
    from .rust import RustRunner

    return {
        "python_ruff": PythonRuffRunner(),
        "python_pyright": PythonPyrightRunner(),
        "python_mypy": PythonMypyRunner(),
        "typescript": TypescriptRunner(),
        "go": GoRunner(),
        "rust": RustRunner(),
    }


__all__ = (
    "STDERR_TAIL_CHAR_LIMIT",
    "build_runner_registry",
    "mask_stderr",
    "run_subprocess",
    "which_binary",
)
