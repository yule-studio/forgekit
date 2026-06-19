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

#issue { height: 1; padding: 0 1; }

/* transient stage marker (thinking → generating); collapses to 0 rows when empty. */
#livestatus { height: auto; padding: 0 1; color: $text-muted; }

/* INLINE mode (`.-inline`, set on the Screen by the app when run inline): the console
   is a BOUNDED terminal-flow region, not a full-screen takeover. The reading flow is
   capped so the inline block stays compact (recent conversation + docked composer);
   the terminal keeps its native scrollback above/below the region. */
Screen.-inline #flow { height: 14; }
"""

__all__ = ("SCREEN_CSS",)
