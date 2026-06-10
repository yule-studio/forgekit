"""Compat shim — moved to yule_agent_runtime.understand."""
import sys
from yule_agent_runtime import understand as _module
sys.modules[__name__] = _module
