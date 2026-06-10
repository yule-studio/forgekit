"""Compat shim — moved to yule_agent_memory.relevance_selector."""
import sys
from yule_agent_memory import relevance_selector as _module
sys.modules[__name__] = _module
