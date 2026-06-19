"""Transcript sink — the seam between in-widget rendering and print-flow scrollback.

WHY THIS EXISTS (honest current limitation)
-------------------------------------------
The reading flow is rendered by a Textual ``RichLog`` (:class:`tui.transcript.Transcript`)
inside the ``SessionFlow`` scroll region. In inline mode the flow is now content-driven
(``height: auto; max-height: 100%`` — see ``tui.styles``), so a short session is compact
and a long one fills the viewport and stays scrollable. That removes the old fixed 14-row
box that pushed earlier output out of a tiny window.

But it is **not yet true terminal-native scrollback accumulation**: Textual inline mode
re-renders its region every frame, so content that scrolls above the region is owned by
the app (scrollable within ``SessionFlow``), NOT written into the terminal's own
scrollback the way a print-based CLI (Claude Code) does. Once a conversation exceeds the
viewport, older turns live in the app's scroll buffer, not in the terminal history.

THE SEAM
--------
``TranscriptSink`` is the single write surface for the reading flow. Today the only
implementation is :class:`WidgetSink` (writes to the RichLog — the current behaviour).
The NEXT step toward Claude-Code-style accumulation is :class:`PrintFlowSink`: when a turn
is *finalized* it would be printed to stdout ABOVE the inline region, handing that turn to
the terminal's native scrollback, while the live region keeps only the in-progress turn +
composer. That migration is intentionally NOT wired yet (it needs an inline driver that
can emit above-region lines without the next frame overwriting them); ``PrintFlowSink``
documents the contract and fails honestly rather than pretending.

Migration path (what still has to change to go print-flow):
  1. route all reading-flow writes in ``tui.app`` through a ``TranscriptSink`` (today they
     call ``Transcript.write`` directly);
  2. mark turn boundaries (``begin_turn`` / ``finalize_turn``) so a finalized turn is a unit
     that can be emitted to scrollback;
  3. implement ``PrintFlowSink.finalize_turn`` against the inline driver's above-region
     write primitive (Textual does not expose this yet — the real blocker);
  4. keep ``WidgetSink`` as the ``--full`` (alt-screen) implementation, where scrollback
     emission does not apply.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable


@runtime_checkable
class TranscriptSink(Protocol):
    """Where finalized reading-flow lines go. One write surface; many backends."""

    def begin_turn(self) -> None:
        """Mark the start of a new user→response turn (vertical breathing room)."""

    def write(self, line: str) -> None:
        """Append one rendered line to the current turn."""

    def write_lines(self, lines: Sequence[str]) -> None:
        """Append several lines (convenience)."""

    def finalize_turn(self) -> None:
        """Mark the current turn complete. A scrollback sink would emit it to the
        terminal's native history here; the widget sink is a no-op (the RichLog already
        holds it)."""


class WidgetSink:
    """Current implementation — writes to the in-app ``Transcript`` RichLog.

    This is the behaviour today: the reading flow is owned by the app's scroll region.
    ``finalize_turn`` is a no-op because the RichLog already retains every line.
    """

    def __init__(self, transcript) -> None:
        self._t = transcript

    def begin_turn(self) -> None:
        self._t.begin_turn()

    def write(self, line: str) -> None:
        self._t.write(line)

    def write_lines(self, lines: Sequence[str]) -> None:
        for line in lines:
            self._t.write(line)

    def finalize_turn(self) -> None:  # noqa: D401 - intentional no-op (RichLog retains lines)
        return None


class PrintFlowSink:
    """SEAM (not wired): emit finalized turns to the terminal's native scrollback.

    This is the next step toward true Claude-Code accumulation. It is deliberately not
    implemented against a real inline driver yet — Textual's inline mode does not expose a
    supported "write above the region" primitive, so wiring it now would be faking it. The
    contract is captured so the migration is a known, named piece of work, not a vague
    aspiration. See the module docstring's migration path.
    """

    def __init__(self, *, emit_line=None) -> None:
        # emit_line: callable that writes one line into terminal scrollback above the
        # inline region. None until a supported primitive exists.
        self._emit_line = emit_line

    def begin_turn(self) -> None:
        return None

    def write(self, line: str) -> None:  # pragma: no cover - seam, not wired
        raise NotImplementedError(
            "PrintFlowSink is the print-flow seam — not wired yet (Textual inline has no "
            "supported above-region write). Use WidgetSink today; see transcript_sink.py."
        )

    def write_lines(self, lines: Sequence[str]) -> None:  # pragma: no cover - seam
        for line in lines:
            self.write(line)

    def finalize_turn(self) -> None:  # pragma: no cover - seam
        raise NotImplementedError("print-flow scrollback emission is not wired yet")


__all__ = ("TranscriptSink", "WidgetSink", "PrintFlowSink")
