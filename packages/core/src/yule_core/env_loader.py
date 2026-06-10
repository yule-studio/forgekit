from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence


def load_env_files(repo_root: Path, filenames: Sequence[str] = (".env", ".env.local")) -> None:
    repo_root = repo_root.resolve()
    initial_env_keys = set(os.environ.keys())

    for filename in filenames:
        path = repo_root / filename
        if not path.exists():
            continue
        _load_env_file(path, initial_env_keys)


def _load_env_file(path: Path, initial_env_keys: set[str]) -> None:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[len("export ") :].strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = _normalize_value(value.strip())

        if not key:
            continue

        if key in initial_env_keys:
            continue

        os.environ[key] = value


def _normalize_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
