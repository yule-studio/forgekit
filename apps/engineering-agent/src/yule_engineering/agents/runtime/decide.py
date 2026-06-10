"""Compat shim — moved to yule_agent_runtime.decide."""
import sys
from yule_agent_runtime import decide as _module
sys.modules[__name__] = _module
