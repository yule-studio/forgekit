"""Compat shim — moved to yule_agent_memory.long_term_memory."""
import sys
from yule_agent_memory import long_term_memory as _module
sys.modules[__name__] = _module
