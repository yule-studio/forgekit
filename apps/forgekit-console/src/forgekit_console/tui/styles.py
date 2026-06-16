"""Screen CSS — Claude-Code-style chat-first layout. Kept out of app.py.

Top→bottom: intro (small avatar + brand/meta) · one quiet issue line · the
transcript (chat-first main area, ``1fr``) · a one-line hint · a **fixed bottom
composer** docked to the bottom (always visible — even when ``/help`` fills the
transcript). Per-widget CSS lives on each widget (IntroHeader / Composer /
Transcript / CommandPalette); this holds only the Screen-level frame.
"""

SCREEN_CSS = """
Screen { layout: vertical; background: $background; }

#issue { height: 1; padding: 0 1; }
#hint { height: 1; padding: 0 1; color: $text-muted; }
"""

__all__ = ("SCREEN_CSS",)
