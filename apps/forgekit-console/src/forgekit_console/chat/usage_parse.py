"""Vendor-native usage parsing seam (WT1 #239) — pure, stdlib-only.

A provider's chat response may carry a ``usage`` block (prompt/completion/total
tokens). This module turns that block into a :class:`ProviderUsage` so the submit
path can record ``usage_basis=live`` for the providers that actually report it —
and degrade to an honest estimate for the ones that do not.

Honesty rails:
- Parsing is keyed by ``submit_compat`` so only wired transports are ever "live".
- A missing / malformed / all-zero usage block returns ``None`` → estimate fallback
  (NEVER a faked live number).
- Partial blocks (only prompt or only completion) are tolerated by deriving the
  total; if nothing usable survives, it still degrades to ``None``.
"""

from __future__ import annotations

import json
from typing import Mapping, Optional

from ..providers.contract import SUBMIT_OPENAI
from . import models as m


def _coerce_int(value) -> Optional[int]:
    """Best-effort non-negative int from a usage field. None if not a clean number."""

    if isinstance(value, bool):  # bools are ints in python — reject them explicitly
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer():
        return int(value) if value >= 0 else None
    return None


def parse_openai_usage(payload: Mapping) -> Optional[m.ProviderUsage]:
    """Parse an openai-style ``usage`` block. ollama's ``/v1/chat/completions`` and
    real OpenAI/gemini-compat endpoints emit this. Returns None when unusable."""

    if not isinstance(payload, Mapping):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return None
    pt = _coerce_int(usage.get("prompt_tokens"))
    ct = _coerce_int(usage.get("completion_tokens"))
    tt = _coerce_int(usage.get("total_tokens"))
    # tolerate partial blocks: derive a total from the parts when it is missing.
    if tt is None and (pt is not None or ct is not None):
        tt = (pt or 0) + (ct or 0)
    if not tt or tt <= 0:  # nothing measurable → honest estimate fallback
        return None
    try:
        raw_json = json.dumps(usage, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        raw_json = ""
    return m.ProviderUsage(
        input_tokens=pt or 0, output_tokens=ct or 0, total_tokens=tt, raw_json=raw_json
    )


# submit_compat → parser. Only listed compats can ever produce live usage.
_PARSERS = {SUBMIT_OPENAI: parse_openai_usage}


def parse_usage(compat: str, payload: Mapping) -> Optional[m.ProviderUsage]:
    """Dispatch native-usage parsing by the provider's ``submit_compat``."""

    parser = _PARSERS.get((compat or "").strip())
    return parser(payload) if parser else None


def live_usage_supported(compat: str) -> bool:
    """True if this compat has a wired native-usage parser (so live is achievable)."""

    return (compat or "").strip() in _PARSERS


__all__ = ("parse_openai_usage", "parse_usage", "live_usage_supported")
