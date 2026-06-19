"""Forward-compat shim — runtime paths now live in ``packages/forgekit-config``.

The canonical module moved to :mod:`forgekit_config.paths` (ForgeKit core, WT2).
This shim re-exports every public name and aliases itself to the real module via
``sys.modules`` so existing importers keep working with object identity preserved:

    from .. import runtime_paths            # module ref → forgekit_config.paths
    from ..runtime_paths import config_path # name import → same function object

New code should import :mod:`forgekit_config.paths` directly. Owner matrix:
``docs/forgekit-architecture-ownership.md``.
"""

from __future__ import annotations

import sys

from forgekit_config import paths as _paths
from forgekit_config.paths import (  # noqa: F401
    ENV_HOME,
    brain_root,
    config_path,
    escalation_ledger_path,
    forgekit_home,
    operator_inbox_path,
    personal_brain_dir,
    starter_pack_dir,
    state_dir,
)

# Identity alias: ``forgekit_console.runtime_paths is forgekit_config.paths``.
sys.modules[__name__] = _paths
