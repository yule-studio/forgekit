"""Safety agent layer — pre-tool-call risk gate (F12 / #103).

This package houses the *tool-call* axis of the safety stack. It is
intentionally a different layer from
:mod:`yule_engineering.agents.security.paste_guard`:

  * :mod:`paste_guard` runs on *outbound payloads* (LLM prompts,
    Discord posts, GitHub comments, Vault writes). It scrubs the
    bytes before they leave the agent process.
  * :mod:`risk_classifier` + :mod:`tool_call_gate` run on *tool
    calls* (subprocess / external HTTP / file write / git
    operation) before the tool actually fires. The gate decides
    ``ALLOW`` / ``REQUIRE_APPROVAL`` / ``BLOCK`` based on the
    autonomy level of the caller.

Both gates can be active at the same time on the same operation
(e.g. a git push touches the tool-call gate first, then any
PR-body / discord-summary it produces still has to pass through
PasteGuard). They do not share state — each enforces its own
hard rails.

Public API:

  * :class:`RiskClass`, :class:`ToolCallContext`,
    :class:`RiskSignal`, :func:`classify_tool_call`
  * :class:`GateAction`, :class:`ToolGateVerdict`,
    :func:`gate_tool_call`
  * :data:`ENV_TOOL_GATE_ENABLED`,
    :data:`ENV_TOOL_GATE_DEFAULT_AUTONOMY` — env knobs the gate
    reads at call time.
"""

from .risk_classifier import (
    RiskClass,
    RiskSignal,
    ToolCallContext,
    classify_tool_call,
)
from .tool_call_gate import (
    ENV_TOOL_GATE_DEFAULT_AUTONOMY,
    ENV_TOOL_GATE_ENABLED,
    GateAction,
    ToolGateVerdict,
    gate_tool_call,
)


__all__ = (
    "ENV_TOOL_GATE_DEFAULT_AUTONOMY",
    "ENV_TOOL_GATE_ENABLED",
    "GateAction",
    "RiskClass",
    "RiskSignal",
    "ToolCallContext",
    "ToolGateVerdict",
    "classify_tool_call",
    "gate_tool_call",
)
