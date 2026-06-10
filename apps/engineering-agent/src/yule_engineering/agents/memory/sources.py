"""Compat shim — moved to yule_agent_memory.sources."""
import sys
from yule_agent_memory import sources as _module
sys.modules[__name__] = _module
