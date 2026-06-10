"""Compat shim — moved to yule_agent_runtime.models."""
import sys
from yule_agent_runtime import models as _module
sys.modules[__name__] = _module
