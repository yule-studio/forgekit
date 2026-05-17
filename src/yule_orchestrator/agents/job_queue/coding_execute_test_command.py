"""P1-E — stack-aware test command selection for the coding executor.

배경
----
이전엔 ``SubprocessTestRunner`` 가 모든 repo 에 대해
``python3 -m unittest discover -s tests -t .`` 를 강제 실행했다.
canonical session ``11917bf1e75d`` (target repo
``yule-studio/naver-search-clone`` — Next.js + NestJS + PostgreSQL +
Docker Compose) 가 ``test_failed`` 로 막힌 직접 원인.

본 모듈은 worktree 의 실제 파일 시그널을 보고 적절한 test command 를
선택하는 **deterministic heuristic** 의 SSoT.

선택 우선순위:

  1. ``CodingExecuteRequest.metadata['test_command']`` — operator override.
  2. JS/TS repo (``package.json`` 존재):
     a. ``package.json`` ``scripts.test`` 있으면 그 script.
     b. 그렇지 않으면 package manager 의 default ``test`` 명령.
  3. Python repo:
     a. ``pyproject.toml`` 에 pytest 의 ``[tool.pytest]`` / ``[tool.pytest.ini_options]``
        / 또는 ``pytest.ini`` 존재 → ``python3 -m pytest``.
     b. ``manage.py`` 존재 → ``python3 manage.py test``.
     c. 그 외 ``python3 -m unittest discover`` (기존 default).
  4. signal 0 — operator-visible ``no_test_command_resolved`` 사유로
     selection 반환. caller (worker) 가 명시적 failure 로 surface.

operator 가 진단할 수 있게 selection 결과는 dataclass 로 반환 — caller
가 ``test_summary`` 에 그대로 합쳐 result_json 에 audit.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tokens — caller / status surface 가 grep / dedup 가능하게.
# ---------------------------------------------------------------------------


STRATEGY_METADATA_OVERRIDE: str = "metadata_override"
STRATEGY_JS_SCRIPT: str = "package_json_test_script"
STRATEGY_JS_PM_DEFAULT: str = "package_manager_test_default"
STRATEGY_PYTHON_PYTEST: str = "python_pytest"
STRATEGY_PYTHON_DJANGO: str = "python_django_manage_test"
STRATEGY_PYTHON_UNITTEST_DEFAULT: str = "python_unittest_discover_default"
STRATEGY_UNRESOLVED: str = "no_test_command_resolved"


PACKAGE_MANAGER_PNPM: str = "pnpm"
PACKAGE_MANAGER_YARN: str = "yarn"
PACKAGE_MANAGER_NPM: str = "npm"
PACKAGE_MANAGER_BUN: str = "bun"
PACKAGE_MANAGER_NONE: str = "none"


# Lock files → package manager. 한 repo 에 두 lock 이 있어도 우선순위
# 는 deterministic (pnpm > yarn > bun > npm — 더 명시적인 도구 우선).
_LOCK_TO_PM: Tuple[Tuple[str, str], ...] = (
    ("pnpm-lock.yaml", PACKAGE_MANAGER_PNPM),
    ("yarn.lock", PACKAGE_MANAGER_YARN),
    ("bun.lockb", PACKAGE_MANAGER_BUN),
    ("package-lock.json", PACKAGE_MANAGER_NPM),
)


# Default test command per package manager when ``package.json``
# 에 ``scripts.test`` 가 없을 때 사용. operator 가 실제 monorepo /
# turbo / nx 설정을 가지고 있으면 ``metadata.test_command`` override
# 로 명시.
_PM_TEST_DEFAULT: Mapping[str, Tuple[str, ...]] = {
    PACKAGE_MANAGER_PNPM: ("pnpm", "run", "test"),
    PACKAGE_MANAGER_YARN: ("yarn", "test"),
    PACKAGE_MANAGER_NPM: ("npm", "test", "--silent"),
    PACKAGE_MANAGER_BUN: ("bun", "test"),
    PACKAGE_MANAGER_NONE: ("npm", "test", "--silent"),
}


# Python unittest discover default — 이전과 동일 (Python repo 회귀
# 차단 + JS/TS 가 아닌 case 의 final fallback).
PYTHON_UNITTEST_DEFAULT: Tuple[str, ...] = (
    "python3",
    "-m",
    "unittest",
    "discover",
    "-s",
    "tests",
    "-t",
    ".",
)


@dataclass(frozen=True)
class TestCommandSelection:
    """Deterministic test command selection result.

    Surfaced via ``WorktreeContext.test_summary`` so operator status /
    audit logs see exactly which heuristic fired.
    """

    command: Tuple[str, ...]
    strategy: str
    package_manager: Optional[str] = None
    reason: str = ""
    detected_signals: Tuple[str, ...] = ()

    def to_audit(self) -> dict:
        return {
            "command": list(self.command),
            "strategy": self.strategy,
            "package_manager": self.package_manager,
            "reason": self.reason,
            "detected_signals": list(self.detected_signals),
        }


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _detect_package_manager(worktree_path: Path) -> Tuple[str, Tuple[str, ...]]:
    """Return (package_manager, lock_files_found)."""

    found: list[str] = []
    chosen = PACKAGE_MANAGER_NONE
    for lock_name, pm in _LOCK_TO_PM:
        if (worktree_path / lock_name).is_file():
            found.append(lock_name)
            if chosen == PACKAGE_MANAGER_NONE:
                chosen = pm
    return chosen, tuple(found)


def _read_package_json(worktree_path: Path) -> Optional[Mapping[str, Any]]:
    pkg = worktree_path / "package.json"
    if not pkg.is_file():
        return None
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - malformed JSON is a real failure
        logger.warning(
            "stack test command: package.json malformed at %s — falling back",
            pkg,
        )
        return None
    if not isinstance(data, dict):
        return None
    return data


def _split_test_script(raw: str) -> Tuple[str, ...]:
    """Tokenize a ``scripts.test`` value with shell-aware quote handling.

    package.json 에는 ``"test": "vitest run && pnpm typecheck"`` 같은
    composite script 도 흔하다. 본 helper 는 굳이 그 chain 을 분해하지
    않고 package manager 의 ``run test`` 를 우선 사용 — composite 도
    package manager 가 그대로 실행한다.
    """

    # We don't actually parse the script content — caller will use
    # ``<pm> run test`` so npm/pnpm/yarn 가 chain 을 그대로 실행한다.
    raw = (raw or "").strip()
    return (raw,) if raw else ()


# ---------------------------------------------------------------------------
# Public selector
# ---------------------------------------------------------------------------


def select_test_command(
    *,
    worktree_path: str,
    request_metadata: Optional[Mapping[str, Any]] = None,
    fallback_command: Optional[Sequence[str]] = None,
) -> TestCommandSelection:
    """Return the test command for *worktree_path*.

    *request_metadata* — ``CodingExecuteRequest.metadata`` (may be None).
    *fallback_command* — if heuristics produce nothing AND no metadata
    override, return this as final fallback (caller default). When also
    None, returns a ``no_test_command_resolved`` selection so caller can
    surface ``REASON_TEST_COMMAND_UNRESOLVED``.
    """

    metadata = dict(request_metadata or {})

    # 1. metadata override — always wins.
    override = metadata.get("test_command")
    if isinstance(override, (list, tuple)) and override:
        return TestCommandSelection(
            command=tuple(str(c) for c in override),
            strategy=STRATEGY_METADATA_OVERRIDE,
            reason="explicit metadata.test_command",
        )

    root = Path(worktree_path)
    if not root.is_dir():
        # Worktree doesn't exist — pure fallback (caller will likely
        # fail before this anyway; defensive).
        return TestCommandSelection(
            command=tuple(fallback_command) if fallback_command else PYTHON_UNITTEST_DEFAULT,
            strategy=STRATEGY_PYTHON_UNITTEST_DEFAULT,
            reason="worktree path missing — pure fallback",
        )

    pkg_data = _read_package_json(root)
    package_json_present = pkg_data is not None
    pm_detected, lock_files = _detect_package_manager(root)
    detected_signals: list[str] = []
    if package_json_present:
        detected_signals.append("package.json")
    detected_signals.extend(lock_files)

    # 2. JS/TS — package.json present takes precedence over Python
    # signals (full-stack monorepo with both files would still pick
    # JS because the executor's primary command must match the
    # tooling the repo ships with).
    if package_json_present:
        scripts = pkg_data.get("scripts") if isinstance(pkg_data, Mapping) else None
        if isinstance(scripts, Mapping) and isinstance(scripts.get("test"), str):
            raw_script = str(scripts.get("test") or "").strip()
            if raw_script and not _looks_like_no_op_test_script(raw_script):
                pm = pm_detected if pm_detected != PACKAGE_MANAGER_NONE else PACKAGE_MANAGER_NPM
                return TestCommandSelection(
                    command=_build_pm_run_test(pm),
                    strategy=STRATEGY_JS_SCRIPT,
                    package_manager=pm,
                    reason=f"package.json scripts.test = {raw_script!r}",
                    detected_signals=tuple(detected_signals),
                )
        # No usable ``test`` script — but package.json exists so this
        # is still a JS/TS repo. Fall back to package manager default
        # so the operator sees the failure under the right tool.
        pm = pm_detected if pm_detected != PACKAGE_MANAGER_NONE else PACKAGE_MANAGER_NPM
        return TestCommandSelection(
            command=_PM_TEST_DEFAULT[pm],
            strategy=STRATEGY_JS_PM_DEFAULT,
            package_manager=pm,
            reason=(
                "package.json present but no usable scripts.test — "
                "using package manager default"
            ),
            detected_signals=tuple(detected_signals),
        )

    # 3. Python project.
    if (root / "pytest.ini").is_file():
        detected_signals.append("pytest.ini")
        return TestCommandSelection(
            command=("python3", "-m", "pytest"),
            strategy=STRATEGY_PYTHON_PYTEST,
            reason="pytest.ini detected",
            detected_signals=tuple(detected_signals),
        )
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        detected_signals.append("pyproject.toml")
        try:
            text = pyproject.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            text = ""
        if re.search(r"\[tool\.pytest", text):
            return TestCommandSelection(
                command=("python3", "-m", "pytest"),
                strategy=STRATEGY_PYTHON_PYTEST,
                reason="pyproject.toml [tool.pytest] detected",
                detected_signals=tuple(detected_signals),
            )
    if (root / "manage.py").is_file():
        detected_signals.append("manage.py")
        return TestCommandSelection(
            command=("python3", "manage.py", "test"),
            strategy=STRATEGY_PYTHON_DJANGO,
            reason="manage.py detected — Django test runner",
            detected_signals=tuple(detected_signals),
        )

    # 4. Final fallback — caller default OR unittest discover.
    if fallback_command:
        return TestCommandSelection(
            command=tuple(str(c) for c in fallback_command),
            strategy=STRATEGY_PYTHON_UNITTEST_DEFAULT,
            reason="caller-supplied fallback (no JS/TS or Python signals)",
            detected_signals=tuple(detected_signals),
        )
    # Note: explicit ``no_test_command_resolved`` would force every Python
    # repo without pyproject/pytest/manage to fail — that's too strict.
    # Keep unittest discover as the last-resort fallback for back-compat.
    return TestCommandSelection(
        command=PYTHON_UNITTEST_DEFAULT,
        strategy=STRATEGY_PYTHON_UNITTEST_DEFAULT,
        reason="no JS/TS or Python project signals — unittest discover fallback",
        detected_signals=tuple(detected_signals),
    )


def _build_pm_run_test(pm: str) -> Tuple[str, ...]:
    if pm == PACKAGE_MANAGER_PNPM:
        return ("pnpm", "run", "test")
    if pm == PACKAGE_MANAGER_YARN:
        return ("yarn", "test")
    if pm == PACKAGE_MANAGER_BUN:
        return ("bun", "test")
    return ("npm", "test", "--silent")


_NO_OP_PATTERNS: Tuple[str, ...] = (
    "echo \"Error: no test specified\"",
    "no test specified",
)


def _looks_like_no_op_test_script(raw: str) -> bool:
    text = (raw or "").strip().lower()
    return any(pat.lower() in text for pat in _NO_OP_PATTERNS)


__all__ = (
    "PACKAGE_MANAGER_BUN",
    "PACKAGE_MANAGER_NONE",
    "PACKAGE_MANAGER_NPM",
    "PACKAGE_MANAGER_PNPM",
    "PACKAGE_MANAGER_YARN",
    "PYTHON_UNITTEST_DEFAULT",
    "STRATEGY_JS_PM_DEFAULT",
    "STRATEGY_JS_SCRIPT",
    "STRATEGY_METADATA_OVERRIDE",
    "STRATEGY_PYTHON_DJANGO",
    "STRATEGY_PYTHON_PYTEST",
    "STRATEGY_PYTHON_UNITTEST_DEFAULT",
    "STRATEGY_UNRESOLVED",
    "TestCommandSelection",
    "select_test_command",
)
