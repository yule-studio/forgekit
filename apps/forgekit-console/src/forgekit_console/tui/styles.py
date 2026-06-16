"""Screen CSS for the console — kept out of app.py so the app stays lean.

Content-first single column: a 1-line context header, a 1-line operator status
pill, a thin 1-line input row, then the inline palette / inline help surfaces,
then the main reading-flow log (with an optional dashboard rail), then a 1-line
contextual hint. No thick chrome, no side panels by default.
"""

SCREEN_CSS = """
Screen { layout: vertical; background: $background; }

#header { height: 1; padding: 0 1; color: $text-muted; }
#statuspill { height: 1; padding: 0 1; }

#inputrow { height: 1; padding: 0 1; }
#modepill { width: auto; padding: 0 1 0 0; }
#prompt { border: none; background: $background; height: 1; padding: 0; }

#palette { }
#help { }

#body { height: 1fr; }
#log { width: 1fr; padding: 0 1; }
#rail {
    display: none;
    width: 46;
    border-left: solid $panel-darken-2;
    padding: 0 1;
    color: $text-muted;
}
#rail.-show { display: block; }

#hint { height: 1; padding: 0 1; color: $text-muted; }
"""

__all__ = ("SCREEN_CSS",)
