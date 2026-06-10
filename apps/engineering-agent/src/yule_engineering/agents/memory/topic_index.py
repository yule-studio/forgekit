"""Compat shim — moved to yule_agent_memory.topic_index."""
import sys
from yule_agent_memory import topic_index as _module
sys.modules[__name__] = _module
