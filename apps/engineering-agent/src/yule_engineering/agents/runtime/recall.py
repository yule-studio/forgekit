"""Compat shim — moved to yule_agent_runtime.recall."""
import sys
from yule_agent_runtime import recall as _module
sys.modules[__name__] = _module
