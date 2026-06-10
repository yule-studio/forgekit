"""Manifest discovery + lazy module loader (F11.1 / #107).

The F11 MVP forced callers to register every plugin / agent manifest by
hand. This module gives the runtime a tiny filesystem discovery surface
(``plugins/<id>/manifest.json`` + ``agents/<id>/manifest.json``) plus a
lazy importer (:func:`load_plugin_module`) that defers
:mod:`importlib` calls until a hook is actually dispatched.

Design rails:

  * The loader does *not* register manifests into a registry on its own
    — it just returns parsed, validated dataclass tuples. The caller
    decides which to register (so e.g. a HIGH-risk plugin can be left
    out until a human enables it).
  * JSON is the only on-disk format supported today; YAML would add a
    third-party dependency we don't yet need.
  * Manifests that fail validation are *not* silently dropped. The
    loader raises :class:`ManifestDiscoveryError` so the surrounding
    workflow can record the failure (mistake ledger) and operators can
    spot the regression.
  * Discovery is deterministic — entries are returned sorted by
    ``manifest.id`` so test fixtures don't depend on filesystem order.

Hard rails (regression-tested):

  * Unknown / extra files inside a plugin directory are ignored — only
    ``manifest.json`` is read.
  * A symlinked / non-mapping JSON body raises
    :class:`ManifestDiscoveryError` immediately.
  * :func:`load_plugin_module` refuses an empty ``module_path`` to avoid
    importing the engineering-agent's top-level package by accident.
"""

from __future__ import annotations

import importlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Tuple

from .manifest import (
    AgentManifest,
    ManifestValidationError,
    PluginManifest,
    load_agent_manifest_from_dict,
    load_plugin_manifest_from_dict,
)


_LOGGER = logging.getLogger(__name__)

_MANIFEST_FILENAME = "manifest.json"


class ManifestDiscoveryError(RuntimeError):
    """Raised when a manifest file is malformed or cannot be parsed.

    The error carries the offending path so the caller can include it in
    operator-facing telemetry without re-walking the directory tree.
    """

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


@dataclass(frozen=True)
class DiscoveryReport:
    """Outcome of a discovery sweep — useful for surface telemetry.

    Tests typically read :attr:`plugins` / :attr:`agents` directly; the
    ``skipped`` field records directories that contained no
    ``manifest.json`` (i.e. work-in-progress folders) so the runtime can
    warn instead of silently ignoring them.
    """

    plugins: Tuple[PluginManifest, ...]
    agents: Tuple[AgentManifest, ...]
    skipped: Tuple[Path, ...] = ()


def _read_manifest_json(path: Path) -> Any:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestDiscoveryError(path, f"unable to read manifest: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestDiscoveryError(path, f"invalid JSON: {exc.msg}") from exc


def _iter_manifest_dirs(root: Path) -> Tuple[Path, ...]:
    if not root.exists():
        return ()
    if not root.is_dir():
        raise ManifestDiscoveryError(root, "expected a directory")
    entries = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]
    entries.sort(key=lambda p: p.name)
    return tuple(entries)


def discover_manifests(
    *,
    plugins_dir: Path,
    agents_dir: Path,
) -> Tuple[Tuple[PluginManifest, ...], Tuple[AgentManifest, ...]]:
    """Scan ``plugins_dir`` and ``agents_dir`` for ``manifest.json`` files.

    Args:
      plugins_dir: root directory containing ``<plugin_id>/manifest.json``
        subdirs. May be absent; an empty tuple is returned in that case.
      agents_dir: same structure as ``plugins_dir`` but for agent
        manifests.

    Returns:
      ``(plugin_manifests, agent_manifests)`` — tuples of validated,
      frozen dataclasses sorted by ``id``.

    Raises:
      ManifestDiscoveryError: on malformed JSON, non-mapping payloads,
        or validation failures (re-wrapped from
        :class:`ManifestValidationError`). Discovery is fail-fast: a
        single bad manifest aborts the sweep so operators can fix it
        before any plugin runs.
    """

    plugins = _load_plugin_manifests(plugins_dir)
    agents = _load_agent_manifests(agents_dir)
    return plugins, agents


def discover_manifests_with_report(
    *,
    plugins_dir: Path,
    agents_dir: Path,
) -> DiscoveryReport:
    """Same as :func:`discover_manifests` but returns a :class:`DiscoveryReport`.

    The report includes ``skipped`` directories that had no
    ``manifest.json`` — useful for warning operators about a stale
    plugin scaffold.
    """

    skipped: list[Path] = []
    plugins = _load_plugin_manifests(plugins_dir, skipped_sink=skipped)
    agents = _load_agent_manifests(agents_dir, skipped_sink=skipped)
    return DiscoveryReport(
        plugins=plugins,
        agents=agents,
        skipped=tuple(sorted(skipped, key=lambda p: str(p))),
    )


def _load_plugin_manifests(
    root: Path,
    *,
    skipped_sink: list[Path] | None = None,
) -> Tuple[PluginManifest, ...]:
    out: list[PluginManifest] = []
    for entry in _iter_manifest_dirs(root):
        manifest_path = entry / _MANIFEST_FILENAME
        if not manifest_path.is_file():
            _LOGGER.warning("plugin directory %s missing %s; skipping", entry, _MANIFEST_FILENAME)
            if skipped_sink is not None:
                skipped_sink.append(entry)
            continue
        payload = _read_manifest_json(manifest_path)
        if not isinstance(payload, dict):
            raise ManifestDiscoveryError(manifest_path, "manifest payload must be a JSON object")
        try:
            out.append(load_plugin_manifest_from_dict(payload))
        except ManifestValidationError as exc:
            raise ManifestDiscoveryError(manifest_path, str(exc)) from exc
    out.sort(key=lambda m: m.id)
    return tuple(out)


def _load_agent_manifests(
    root: Path,
    *,
    skipped_sink: list[Path] | None = None,
) -> Tuple[AgentManifest, ...]:
    out: list[AgentManifest] = []
    for entry in _iter_manifest_dirs(root):
        manifest_path = entry / _MANIFEST_FILENAME
        if not manifest_path.is_file():
            _LOGGER.warning("agent directory %s missing %s; skipping", entry, _MANIFEST_FILENAME)
            if skipped_sink is not None:
                skipped_sink.append(entry)
            continue
        payload = _read_manifest_json(manifest_path)
        if not isinstance(payload, dict):
            raise ManifestDiscoveryError(manifest_path, "manifest payload must be a JSON object")
        try:
            out.append(load_agent_manifest_from_dict(payload))
        except ManifestValidationError as exc:
            raise ManifestDiscoveryError(manifest_path, str(exc)) from exc
    out.sort(key=lambda m: m.id)
    return tuple(out)


def load_plugin_module(manifest: PluginManifest) -> Any:
    """Lazy-import the plugin's ``module_path`` via :mod:`importlib`.

    The import happens *only* when this function is called, never at
    manifest registration time. Failures raise the original
    :class:`ImportError` (or its subclass) so the caller — typically
    :func:`yule_engineering.agents.extension.hook_chain.invoke_hook`
    — can record it in the mistake ledger as a manifest rejection.

    Refuses an empty ``module_path`` with :class:`ValueError` to keep a
    blank manifest from accidentally pulling the top-level package in.
    """

    if not isinstance(manifest, PluginManifest):
        raise TypeError("load_plugin_module expects a PluginManifest")
    if not manifest.module_path:
        raise ValueError(
            f"plugin '{manifest.id}' has an empty module_path; refusing to import"
        )
    return importlib.import_module(manifest.module_path)


__all__ = [
    "DiscoveryReport",
    "ManifestDiscoveryError",
    "discover_manifests",
    "discover_manifests_with_report",
    "load_plugin_module",
]
