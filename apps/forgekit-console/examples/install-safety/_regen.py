"""Regenerate `activation-lane.json` evidence from the live install-safety lane.

Run from the repo root: ``python3 apps/forgekit-console/examples/install-safety/_regen.py``.
The evidence is a real `activate()` trace (no hand-authoring) for four scenarios spanning
safe / blocked / approved / destructive — so the example can never drift from the code.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[4]
for _rel in (
    "packages/forgekit-runtime/src", "packages/forgekit-config/src",
    "packages/forgekit-provider/src", "packages/forgekit-contracts/src",
    "packages/forgekit-goal/src", "packages/hephaistos/src",
    "packages/armory/src", "packages/nexus/src", "apps/forgekit-console/src",
):
    sys.path.insert(0, str(_ROOT / _rel))

from forgekit_runtime import activation as ACT
from forgekit_runtime.decision_lane import OperatorApproval

_OP = OperatorApproval(approver="operator", decision_ref="", approved=True)
_TS = "2026-06-23T00:00:00Z"


def _run(label, cand, action, **kw):
    receipt = ACT.activate(cand, action, recorded_at=_TS, **kw)
    return {"label": label, "candidate": cand.to_dict(), "action": action,
            "readiness_state": ACT.derive_readiness_state(cand),
            "classification": ACT.classify_activation(cand, action).to_dict(),
            "receipt": receipt.to_dict()}


def main() -> None:
    scenarios = [
        _run("safe attach (present armory tool)",
             ACT.ActivationCandidate(id="ripgrep", kind="tool", source="armory",
                 present=True, armory_registered=True, curated=True, safety="safe",
                 why="코드 검색 속도"), ACT.ACT_ATTACH),
        _run("install-required external — no approval → blocked",
             ACT.ActivationCandidate(id="some-cli", kind="tool", source="external",
                 present=False, needs_install=True, armory_registered=True, curated=True,
                 why="포맷 편의"), ACT.ACT_INSTALL),
        _run("install-required external — operator approval → enabled",
             ACT.ActivationCandidate(id="some-cli", kind="tool", source="external",
                 present=False, needs_install=True, armory_registered=True, curated=True,
                 why="포맷 편의"), ACT.ACT_INSTALL, operator_approval=_OP),
        _run("destructive plugin — blocked even with approval",
             ACT.ActivationCandidate(id="deploy-secret-plugin", kind="plugin",
                 source="external", present=True, armory_registered=True, curated=True,
                 safety="safe", why="production deploy secret rotation"),
             ACT.ACT_ENABLE, operator_approval=_OP),
    ]
    out = {"lane": "install-safety", "doc": "docs/install-safety-lane.md",
           "states": list(ACT.ALL_STATES), "scenarios": scenarios}
    path = Path(__file__).resolve().parent / "activation-lane.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", path)


if __name__ == "__main__":
    main()
