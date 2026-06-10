"""Compat shim — the Discord gateway moved to ``apps/discord-gateway``.

The canonical home is now the ``yule_discord`` package
(``apps/discord-gateway/src/yule_discord``). This shim eagerly walks the whole
relocated tree and registers **every** submodule under the old
``yule_engineering.discord.*`` import path via ``sys.modules`` so that existing
importers keep resolving to the **same** module objects::

    from yule_engineering.discord.ui.formatter import ...
    from yule_engineering.discord.engineering_channel_router import main
    import yule_engineering.discord.bot._legacy

all resolve to the identical ``yule_discord.*`` objects (object identity
preserved for ``is`` checks, ``patch(...)``, and ``reload``). New code should
import from ``yule_discord`` directly.

Why eager-walk (not per-file shim stubs): a directory of per-module
``sys.modules[__name__] = importlib.import_module(...)`` stubs re-executes the
real module a second time during ``import a.b.c`` resolution (the parent
``__path__`` still points at the stub dir), yielding two distinct module objects
for the same file. Registering every submodule up-front from this single
top-level shim avoids that race entirely.

Transitional note: ``yule_discord`` still imports shared infrastructure and agent
runtime (``yule_engineering.agents`` / ``.runtime`` — apps → monolith, acyclic)
and ``yule_engineering.planning`` (→ ``apps/planning-agent`` via shim, app → app
transitional). Those edges collapse once the gateway↔agent boundary is replaced
by ``packages/agent-contracts`` command/event/status.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys

import yule_discord as _pkg

# Alias the package itself.
sys.modules[__name__] = _pkg

# Eagerly import + alias every submodule under the old dotted namespace so deep
# paths resolve to the SAME object as ``yule_discord.*`` without re-execution.
_old_prefix = __name__ + "."
_new_prefix = _pkg.__name__ + "."
for _info in pkgutil.walk_packages(_pkg.__path__, _new_prefix):
    _module = importlib.import_module(_info.name)
    _old_name = _old_prefix + _info.name[len(_new_prefix):]
    sys.modules[_old_name] = _module
