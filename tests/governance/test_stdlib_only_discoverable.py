"""Governance regression — `tests/governance/` 는 stdlib-only 로 유지.

CI baseline 이 ``python3 -m unittest discover -s tests -t .`` (third-party
runner 없음) 이라서 governance 테스트 모듈에 top-level ``import pytest``
가 들어가면 unittest loader 가 모듈 자체를 import 못 해서 전체 discover
가 빨갛게 된다 (이전 회귀: 2026-05-17, `test_code_audit.py`).

본 테스트는:

  * ``tests/governance/*.py`` 의 *top-level* import 라인을 AST 로 훑어
    ``pytest`` 를 모듈 단위로 import 하는 모듈이 없음을 보장한다.
  * 함수 / 메서드 안에서 lazy import 하는 패턴은 허용 — 어차피 unittest
    discover 의 module load 단계에서는 실행되지 않으므로 CI blocker 가
    아니다.

같은 원칙을 다른 디렉터리에 확장하고 싶다면 ``DIRS`` 를 늘리면 된다.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from typing import Iterable, List, Tuple

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


DIRS: Tuple[str, ...] = ("tests/governance",)


def _top_level_imports(source: str) -> Iterable[str]:
    """Yield module names imported at module top level (not inside func)."""

    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                yield node.module.split(".")[0]


class GovernanceTestsAreStdlibOnlyTests(unittest.TestCase):
    def test_no_top_level_pytest_import_in_governance_dir(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        offenders: List[str] = []
        for rel in DIRS:
            base = repo_root / rel
            for path in base.rglob("test_*.py"):
                try:
                    source = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                if "pytest" in set(_top_level_imports(source)):
                    offenders.append(str(path.relative_to(repo_root)))

        self.assertEqual(
            offenders,
            [],
            "CI 는 stdlib `python3 -m unittest discover` 만 가지고 있어 "
            "top-level `import pytest` 가 들어가면 discover 단계에서 fail 한다. "
            "fixture 가 필요하면 unittest.TestCase + unittest.mock.patch 로 "
            "재작성하거나 lazy import (def test 안에서 import pytest) 로 옮겨라. "
            f"위반 파일: {offenders}",
        )


if __name__ == "__main__":
    unittest.main()
