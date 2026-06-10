"""Compatibility shim — git/repo utilities moved to ``packages/vcs`` (yule_vcs).

The canonical home is now the ``yule_vcs`` package. This shim re-exports it
under the old ``yule_engineering.agents.git`` path so existing imports keep
resolving to the same objects. New code should import from ``yule_vcs``.
"""
