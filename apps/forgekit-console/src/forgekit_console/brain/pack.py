"""Starter brain pack — build a read-only pack from a local source vault.

Philosophy: the *whole* local vault may be the source, but the forgekit runtime
never reads the raw vault — it reads this built **pack** (manifest + per-doc
compressed digests + a lightweight index). The build is read-only on the source
and deterministic, so the same vault always produces the same pack.

This is local-only today but the manifest shape is reusable for later
distribution. Writes never touch the source or the personal brain.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Tuple

from .models import PackEntry, PackManifest

_MD_SUFFIXES = (".md", ".markdown")
_SKIP_DIRS = {".git", ".obsidian", ".trash", "node_modules", ".cache"}
MANIFEST_NAME = "manifest.json"
INDEX_NAME = "index.json"
READONLY_MARKER = ".readonly"
DIGEST_DIR = "digests"


class PackBuildError(RuntimeError):
    """Raised when a starter pack cannot be built (e.g. missing source)."""


def _frontmatter_meta(text: str) -> Tuple[str, Tuple[str, ...]]:
    """Extract (title, tags) from a leading ``---`` frontmatter block, if any."""

    title, tags = "", ()
    if not text.startswith("---"):
        return title, tags
    end = text.find("\n---", 3)
    if end == -1:
        return title, tags
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "title" and value:
            title = value
        elif key == "tags" and value:
            tags = tuple(t.strip() for t in value.replace(",", " ").split() if t.strip())
    return title, tags


def _body_after_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            nl = text.find("\n", end + 1)
            return text[nl + 1:] if nl != -1 else ""
    return text


def _digest(text: str, limit: int) -> str:
    body = _body_after_frontmatter(text).strip()
    if len(body) <= limit:
        return body
    return body[:limit].rstrip() + " …"


def build_starter_pack(
    source_path: Path,
    dest_dir: Path,
    *,
    built_at: str = "1970-01-01T00:00:00Z",
    max_docs: int = 2000,
    digest_chars: int = 800,
) -> PackManifest:
    """Build a read-only starter pack from *source_path* into *dest_dir*."""

    source = Path(source_path)
    if not source.exists() or not source.is_dir():
        raise PackBuildError(f"source vault not found: {source}")
    dest = Path(dest_dir)
    digests = dest / DIGEST_DIR
    digests.mkdir(parents=True, exist_ok=True)

    files = []
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _MD_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        files.append(path)
        if len(files) >= max_docs:
            break

    entries: list[PackEntry] = []
    total_bytes = 0
    index: list[dict] = []
    for i, path in enumerate(files):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = str(path.relative_to(source))
        title, tags = _frontmatter_meta(text)
        if not title:
            title = path.stem
        digest = _digest(text, digest_chars)
        size = len(text.encode("utf-8"))
        total_bytes += size
        (digests / f"{i:05d}.txt").write_text(digest, encoding="utf-8")
        entries.append(PackEntry(rel_path=rel, title=title, bytes=size,
                                 digest_chars=len(digest), tags=tags))
        index.append({"i": i, "title": title, "rel_path": rel, "tags": list(tags)})

    manifest = PackManifest(
        source_path=str(source),
        built_at=built_at,
        doc_count=len(entries),
        total_bytes=total_bytes,
        entries=tuple(entries),
        meta={"digest_chars": str(digest_chars), "read_only": "true"},
    )
    (dest / MANIFEST_NAME).write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (dest / INDEX_NAME).write_text(
        json.dumps({"docs": index}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (dest / READONLY_MARKER).write_text(
        "이 디렉터리는 forgekit starter pack(read-only)입니다. 직접 수정하지 마세요.\n",
        encoding="utf-8",
    )
    return manifest


def pack_status(dest_dir: Path) -> Optional[dict]:
    """Read the built pack's manifest summary, or None if not built."""

    manifest = Path(dest_dir) / MANIFEST_NAME
    if not manifest.exists():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {
        "source_path": data.get("source_path"),
        "built_at": data.get("built_at"),
        "doc_count": data.get("doc_count", 0),
        "total_bytes": data.get("total_bytes", 0),
        "read_only": True,
    }


__all__ = (
    "PackBuildError",
    "build_starter_pack",
    "pack_status",
    "MANIFEST_NAME",
    "READONLY_MARKER",
)
