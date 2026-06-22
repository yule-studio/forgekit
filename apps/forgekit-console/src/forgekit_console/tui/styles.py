"""Screen CSS — Claude-Code-style chat-first layout. Kept out of app.py.

Top→bottom, TOP-ALIGNED: intro (small avatar + brand/meta, a fixed banner) · then
the session flow (:class:`tui.session_flow.SessionFlow`, ``1fr`` scroll) holding
one quiet issue line · the main area (transcript XOR help, ``height: auto``) · the
**session-following inline composer BAR** (renders right after the content, NOT
docked — input row + inline palette + sub-hint row, all in one bar). A short
session leaves the bar near the top with empty space below; as content grows the
flow scrolls to keep it in view.
Per-widget CSS lives on each widget (IntroHeader / SessionFlow / Composer /
Transcript / CommandPalette); this holds only the Screen-level frame.

The brand colour tokens are registered globally by the app via
:func:`tui.theme.css_variables` (``App.get_css_variables``), so every widget can
reference ``$accent`` / ``$accent-secondary`` / ``$brand-border`` / ``$text`` etc.
against the forgekit cyan/magenta-on-black palette instead of textual's defaults.
"""

SCREEN_CSS = """
/* terminal-native: no painted screen background — the terminal's own bg shows through
   (inline mode especially), so ForgeKit blends into the existing terminal flow instead
   of looking like a separate boxed app window. `--full` still renders the same layout. */
Screen { layout: vertical; background: transparent; }

/* cross-widget drag-selection (full mode): brand desaturated-cyan block (bg via the
   $screen-selection-background brand var) with the LIGHT brand foreground forced on top,
   so EVERY selected line is uniformly high-contrast regardless of its own colour (dim /
   accent / warn). Without forcing the fg, Textual leaves the original text colour, so a
   selected `[dim]` line would be muted-on-dim — low contrast. Mirrors the composer's
   `text-area--selection` treatment for a consistent, on-brand selection everywhere. */
Screen > .screen--selection { color: $text; }

#issue { height: 1; padding: 0 1; }

/* transient stage marker (thinking → generating); collapses to 0 rows when empty. */
#livestatus { height: auto; padding: 0 1; color: $text-muted; }

/* Full mode: the composer is the last child after the 1fr flow, so it naturally sits at
   the bottom (the 1fr flow fills the space above it) — no dock needed, no overlap.
   Inline mode (below): the flow is content-driven (auto), so the composer is DOCKED at the
   bottom to stay pinned as the flow grows past the viewport. Opening the slash palette
   grows the composer UPWARD (it is `height: auto`), so the command area never reads as a box. */
Screen.-inline #composer { dock: bottom; }

/* INLINE mode (`.-inline`, set on the Screen by the app when run inline): the reading
   flow is CONTENT-DRIVEN (`height: auto`), NOT a fixed bounded box. So a short session
   renders only a few rows and a long /doctor|/provider|/usage output makes the inline
   block GROW — the terminal draws that many rows and output accumulates downward instead
   of the old hard 14-row cap pushing earlier content out of a tiny window.
   `max-height: 100%` keeps the flow content-driven up to the viewport, then (and only
   then) it scrolls — so a short session is COMPACT (a few rows, no empty box) while a long
   session fills the viewport and stays scrollable (older content is never lost, and
   SessionFlow remains the single scroll owner).
   Honest Textual limit: the inline region can only grow up to the terminal viewport; once
   the conversation exceeds the viewport the flow still scrolls internally (it is not yet
   true native-scrollback accumulation — that is the print-flow seam, see
   tui/transcript_sink.py and docs/forgekit-console-ui.md). In `--full` the flow stays
   `1fr` (fills the alt-screen) with the composer as its last (bottom) child. */
Screen.-inline #flow { height: auto; max-height: 100%; }
"""

__all__ = ("SCREEN_CSS",)
