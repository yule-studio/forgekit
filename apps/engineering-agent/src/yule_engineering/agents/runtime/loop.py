"""Compat shim — moved to yule_agent_runtime.loop."""
import sys
from yule_agent_runtime import loop as _module
sys.modules[__name__] = _module
