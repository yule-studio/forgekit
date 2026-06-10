"""Compat shim — moved to yule_agent_runtime.policies."""
import sys
from yule_agent_runtime import policies as _module
sys.modules[__name__] = _module
