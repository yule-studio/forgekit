"""Brain layer policy — pure rules over the four layers.

The single source of truth for "who can write where, what the runtime reads, and
where a write goes by default". Disk operations (``personal``/``pack``) consult
these so the read-only / default-write-target invariants can't be bypassed.
"""

from __future__ import annotations

from typing import Tuple

from .models import (
    ALL_LAYERS,
    LAYER_PERSONAL,
    LAYER_SOURCE,
    LAYER_STARTER,
    LAYER_WORKING,
)

_WRITABLE = frozenset({LAYER_PERSONAL})
_RUNTIME_READABLE = frozenset({LAYER_PERSONAL, LAYER_STARTER, LAYER_WORKING})


class BrainPolicyError(RuntimeError):
    """Raised when a brain operation violates the layer policy."""


def is_writable(layer: str) -> bool:
    return layer in _WRITABLE


def is_runtime_readable(layer: str) -> bool:
    return layer in _RUNTIME_READABLE


def default_write_target() -> str:
    """Writes always default to the personal brain."""

    return LAYER_PERSONAL


def assert_writable(layer: str) -> None:
    """Raise unless *layer* accepts writes (only ``personal`` does)."""

    if layer not in ALL_LAYERS:
        raise BrainPolicyError(f"unknown brain layer: {layer!r}")
    if not is_writable(layer):
        reason = {
            LAYER_STARTER: "starter/shared brain is read-only (build from source instead)",
            LAYER_SOURCE: "source vault is a build input, not a write target",
            LAYER_WORKING: "working set is an ephemeral projection, not a store",
        }.get(layer, "not a writable layer")
        raise BrainPolicyError(f"refusing write to '{layer}': {reason}")


def runtime_read_order() -> Tuple[str, ...]:
    """Layer precedence the runtime reads (working > personal > starter).

    The ``source`` vault is intentionally absent — the runtime reads the built
    starter *pack*, never the raw vault.
    """

    return (LAYER_WORKING, LAYER_PERSONAL, LAYER_STARTER)


__all__ = (
    "BrainPolicyError",
    "is_writable",
    "is_runtime_readable",
    "default_write_target",
    "assert_writable",
    "runtime_read_order",
)
