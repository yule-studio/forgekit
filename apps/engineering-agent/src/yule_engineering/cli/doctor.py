from __future__ import annotations

from pathlib import Path

from ..diagnostics.doctor import doctor_exit_code, render_doctor_report, run_doctor


def run_doctor_command(repo_root: Path, agent_id: str) -> int:
    checks = run_doctor(repo_root=repo_root, agent_id=agent_id)
    print(render_doctor_report(checks), end="")
    return doctor_exit_code(checks)
