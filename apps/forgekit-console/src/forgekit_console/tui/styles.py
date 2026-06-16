"""Screen CSS — Claude-like vertical flow. Kept out of app.py.

Top→bottom: compact intro (avatar + brand) · one-line setup/status issue line ·
thin input row · inline palette · the main content (log, or the full-width help
document when open) · a one-line hint. No side panels, no thick footer.
"""

SCREEN_CSS = """
Screen { layout: vertical; background: $background; }

#intro { height: auto; padding: 1 1 0 1; }
#issue { height: 1; padding: 0 1; }

#inputrow { height: 1; padding: 0 1; }
#modepill { width: auto; padding: 0 1 0 0; }
#prompt { border: none; background: $background; height: 1; padding: 0; }

#content { height: 1fr; }
#log { width: 1fr; padding: 0 1; }

#hint { height: 1; padding: 0 1; color: $text-muted; }
"""

__all__ = ("SCREEN_CSS",)
