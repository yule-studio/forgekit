"""Screen CSS — Claude-Code-style chat-first layout. Kept out of app.py.

Top→bottom, TOP-ALIGNED: intro (small avatar + brand/meta, a fixed banner) · then
the session flow (:class:`tui.session_flow.SessionFlow`, ``1fr`` scroll) holding
one quiet issue line · the main area (transcript XOR help, ``height: auto``) · the
**session-following inline composer** (renders right after the content, NOT
docked) · a one-line hint. A short session leaves the composer near the top with
empty space below; as content grows the flow scrolls to keep it in view.
Per-widget CSS lives on each widget (IntroHeader / SessionFlow / Composer /
Transcript / CommandPalette); this holds only the Screen-level frame.
"""

SCREEN_CSS = """
Screen { layout: vertical; background: $background; }

#issue { height: 1; padding: 0 1; }
#hint { height: 1; padding: 0 1; color: $text-muted; }
"""

__all__ = ("SCREEN_CSS",)
