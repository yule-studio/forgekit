"""Repo-local toolchain detection — read version pins from a repo's own manifests.

Pure given an injected file reader; no version manager involved. We read the files a
developer already commits (``.tool-versions``, ``.mise.toml``, ``.nvmrc``,
``.python-version``, ``go.mod`` …) and surface the *declared* requirements. We never
guess a version we cannot read — an unreadable/odd file is skipped, not invented.

Line-based parsing (no ``tomllib``) so it behaves identically on 3.9 and 3.13.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .models import (
    ToolRequirement,
    SRC_TOOL_VERSIONS, SRC_MISE, SRC_NVMRC, SRC_PYTHON_VERSION, SRC_RUBY_VERSION,
    SRC_GO, SRC_JAVA_VERSION, SRC_PACKAGE_JSON, SRC_PYPROJECT,
)

Reader = Callable[[str], Optional[str]]

# manifest file → mise-style tool name, for the single-line "<version>" files.
_SINGLE: Dict[str, tuple] = {
    ".nvmrc": ("node", SRC_NVMRC),
    ".node-version": ("node", SRC_NVMRC),
    ".python-version": ("python", SRC_PYTHON_VERSION),
    ".ruby-version": ("ruby", SRC_RUBY_VERSION),
    ".java-version": ("java", SRC_JAVA_VERSION),
    ".go-version": ("go", SRC_GO),
    ".terraform-version": ("terraform", SRC_GO),
}

# Order = precedence for de-dup (explicit asdf/mise manifests win over single-file pins,
# which win over derived package.json/go.mod/pyproject hints).
_SCAN_ORDER = (
    ".tool-versions", ".mise.toml", "mise.toml", ".config/mise/config.toml",
    ".nvmrc", ".node-version", ".python-version", ".ruby-version",
    ".java-version", ".go-version", ".terraform-version",
    "go.mod", "package.json", "pyproject.toml",
)


def _default_reader(root: Path) -> Reader:
    def read(rel: str) -> Optional[str]:
        p = root / rel
        try:
            return p.read_text(encoding="utf-8") if p.is_file() else None
        except OSError:
            return None
    return read


def _clean_version(tok: str) -> str:
    # keep the declared token mostly as-is but drop wrapping quotes/`v` prefix noise.
    tok = (tok or "").strip().strip('"').strip("'").strip()
    return tok[1:].strip() if tok[:1] == "v" and tok[1:2].isdigit() else tok


def _parse_tool_versions(text: str, fname: str) -> List[ToolRequirement]:
    out: List[ToolRequirement] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        tool = parts[0].strip()
        ver = _clean_version(parts[1]) if len(parts) > 1 else ""
        if tool:
            out.append(ToolRequirement(tool, ver, SRC_TOOL_VERSIONS, fname, raw=line))
    return out


_MISE_KV = re.compile(r'^\s*([A-Za-z0-9_.\-]+)\s*=\s*(.+?)\s*$')


def _parse_mise_toml(text: str, fname: str) -> List[ToolRequirement]:
    # Minimal [tools] reader. Supports `node = "20"`, `python = ["3.13"]`,
    # `node = { version = "20" }`. Ignores other sections.
    out: List[ToolRequirement] = []
    in_tools = False
    for line in text.splitlines():
        s = line.split("#", 1)[0].rstrip()
        h = s.strip()
        if h.startswith("[") and h.endswith("]"):
            in_tools = h.strip("[]").strip() == "tools"
            continue
        if not in_tools:
            continue
        m = _MISE_KV.match(s)
        if not m:
            continue
        tool, val = m.group(1).strip(), m.group(2).strip()
        ver = ""
        inline = re.search(r'version\s*=\s*["\']([^"\']+)["\']', val)
        if inline:
            ver = inline.group(1)
        elif val.startswith("["):
            first = re.search(r'["\']([^"\']+)["\']', val)
            ver = first.group(1) if first else ""
        else:
            ver = _clean_version(val)
        out.append(ToolRequirement(tool, ver, SRC_MISE, fname, raw=h))
    return out


def _parse_single(text: str, tool: str, source: str, fname: str) -> List[ToolRequirement]:
    ver = _clean_version(text.strip().splitlines()[0]) if text.strip() else ""
    return [ToolRequirement(tool, ver, source, fname, raw=ver)] if ver else []


def _parse_go_mod(text: str, fname: str) -> List[ToolRequirement]:
    m = re.search(r'^\s*go\s+([0-9][0-9.]*)\s*$', text, re.MULTILINE)
    return [ToolRequirement("go", m.group(1), SRC_GO, fname, raw=m.group(0).strip())] if m else []


def _parse_package_json(text: str, fname: str) -> List[ToolRequirement]:
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    out: List[ToolRequirement] = []
    eng = (data or {}).get("engines") or {}
    if isinstance(eng, dict):
        for tool in ("node", "npm", "pnpm", "yarn"):
            v = eng.get(tool)
            if isinstance(v, str) and v.strip():
                out.append(ToolRequirement(tool, v.strip(), SRC_PACKAGE_JSON, fname,
                                           raw=f"engines.{tool}={v.strip()}"))
    return out


def _parse_pyproject(text: str, fname: str) -> List[ToolRequirement]:
    m = re.search(r'requires-python\s*=\s*["\']([^"\']+)["\']', text)
    return [ToolRequirement("python", m.group(1).strip(), SRC_PYPROJECT, fname,
                            raw=f"requires-python={m.group(1).strip()}")] if m else []


def detect_requirements(root, *, reader: Optional[Reader] = None) -> List[ToolRequirement]:
    """Scan ``root`` for repo-local version manifests → de-duplicated requirements.

    First (highest-precedence) source for a given tool wins; later sources are dropped
    so a derived ``package.json`` hint never overrides an explicit ``.tool-versions``.
    """

    read = reader or _default_reader(Path(root))
    collected: List[ToolRequirement] = []
    for fname in _SCAN_ORDER:
        text = read(fname)
        if not text:
            continue
        if fname == ".tool-versions":
            collected += _parse_tool_versions(text, fname)
        elif fname in (".mise.toml", "mise.toml", ".config/mise/config.toml"):
            collected += _parse_mise_toml(text, fname)
        elif fname in _SINGLE:
            tool, source = _SINGLE[fname]
            collected += _parse_single(text, tool, source, fname)
        elif fname == "go.mod":
            collected += _parse_go_mod(text, fname)
        elif fname == "package.json":
            collected += _parse_package_json(text, fname)
        elif fname == "pyproject.toml":
            collected += _parse_pyproject(text, fname)
    seen: Dict[str, ToolRequirement] = {}
    for req in collected:
        seen.setdefault(req.tool, req)     # first wins (scan order = precedence)
    return list(seen.values())


__all__ = ("Reader", "detect_requirements")
