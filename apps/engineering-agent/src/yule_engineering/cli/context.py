from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..core.context_loader import load_agent_context, render_context


def run_context_command(repo_root: Path, agent_id: str, output: Optional[str]) -> int:
    loaded_context = load_agent_context(repo_root=repo_root, agent_id=agent_id)
    rendered = render_context(loaded_context)

    if output:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
        print(str(output_path))
        return 0

    print(rendered)
    return 0
