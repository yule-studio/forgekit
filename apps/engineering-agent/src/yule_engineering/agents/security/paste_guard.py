"""Compat shim — moved to yule_security.paste_guard (packages/security)."""
import sys
from yule_security import paste_guard as _module
sys.modules[__name__] = _module
