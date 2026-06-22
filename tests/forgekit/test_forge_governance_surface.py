"""G2 — forge governance operator surface (/resolve apply | log).

Proves the forge governance receipt is operator-visible AND persisted, not just a preview:
- ``/resolve <req>`` is a PREVIEW (does NOT persist) + tells the operator how to record;
- ``/resolve apply <req>`` runs the gate and PERSISTS the receipt to the append-only
  ledger; the surface confirms the ledger path;
- ``/resolve log`` reads the recorded receipts back (audit) — a blocked/destructive run is
  recorded honestly (✗), an authorized safe run as ✓;
- the surface never fabricates: a destructive request is recorded ``blocked`` (not executed).

Hermetic: a tmp FORGEKIT_HOME isolates the ledger; pure router call (no TUI).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _rel in (
    "packages/forgekit-runtime/src",
    "packages/forgekit-config/src",
    "packages/forgekit-provider/src",
    "packages/forgekit-contracts/src",
    "packages/forgekit-goal/src",
    "packages/hephaistos/src",
    "packages/armory/src",
    "packages/nexus/src",
    "apps/forgekit-console/src",
):
    _p = str(_ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from forgekit_console.commands.parser import parse_input
from forgekit_console.commands.registry import H_RESOLVE
from forgekit_console.commands.router import _hephaistos_result
from forgekit_runtime.forge import read_forge_receipts

_REQ = "java spring boot REST API"


class _Ctx:
    def __init__(self, home):
        self.env = {"FORGEKIT_HOME": home}
        self.config = None
        self.nexus_role = ""


def _run(line, home):
    return _hephaistos_result(H_RESOLVE, parse_input(line), _Ctx(home))


class ForgeSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.mkdtemp()

    def test_preview_does_not_persist(self) -> None:
        r = _run(f"/resolve {_REQ}", self.home)
        joined = "\n".join(r.lines)
        self.assertIn("governance", joined)
        self.assertIn("apply", joined)                       # tells operator how to record
        self.assertEqual(read_forge_receipts(env={"FORGEKIT_HOME": self.home}), [])

    def test_apply_persists_receipt(self) -> None:
        r = _run(f"/resolve apply {_REQ}", self.home)
        joined = "\n".join(r.lines)
        self.assertIn("forge execution receipt", joined)
        self.assertIn("ledger 기록됨", joined)
        entries = read_forge_receipts(env={"FORGEKIT_HOME": self.home})
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["receipt"]["outcome"], "executed")

    def test_log_reads_back_audit(self) -> None:
        _run(f"/resolve apply {_REQ}", self.home)
        _run("/resolve apply 프로덕션 배포 deploy secret", self.home)  # destructive → blocked
        r = _run("/resolve log", self.home)
        joined = "\n".join(r.lines)
        self.assertIn("최근 2건", joined)
        self.assertIn("✓ executed", joined)                  # safe authorized
        self.assertIn("✗ blocked", joined)                   # destructive honestly recorded

    def test_log_empty_is_honest(self) -> None:
        r = _run("/resolve log", self.home)
        self.assertIn("기록 없음", "\n".join(r.lines))

    def test_apply_without_request_prompts(self) -> None:
        r = _run("/resolve apply", self.home)
        self.assertIn("요청을 입력", "\n".join(r.lines))


if __name__ == "__main__":
    unittest.main()
