"""Forgekit brand theme — the single source for the console's colour tokens.

The palette is extracted from the ``forgekit`` wordmark banner
(``assets/brand/forgekit-banner.png``): a pixel-art "FORGEKIT" with a
cyan→magenta gradient on near-black. The values below are tuned for *readability
on black* — the neon cyan/magenta are used as ACCENTS and markers (prompt marker,
active tab, brand wordmark, status dots), while body text stays foreground/muted.
That keeps the console restrained (Claude-Code style) rather than gaudy cyberpunk.

Two ways to consume these:

* **CSS** — :func:`css_variables` returns the brand variable map; the app merges
  it via ``App.get_css_variables`` so EVERY stylesheet (the screen CSS *and* each
  widget's ``DEFAULT_CSS``) can reference ``$accent`` / ``$accent-secondary`` /
  ``$brand-border`` etc. (Textual resolves variables per-stylesheet, so app-level
  ``CSS`` declarations alone would not reach widget ``DEFAULT_CSS`` — the override
  is what makes them global.) Textual's own ``$background`` / ``$text`` are also
  re-pinned to the brand values.
* **Rich markup** — render helpers embed the hex constants directly (e.g.
  ``f"[{ACCENT_PRIMARY}]●[/{ACCENT_PRIMARY}]"``) so the pure string builders stay
  unit-testable without a terminal. Do NOT scatter raw hexes in call sites —
  reference these constants (or :func:`wordmark`).
"""

from __future__ import annotations

# --- core brand tokens (hex; tuned for readability on black) ----------------
BG = "#0b0d12"  # near-black background
FG = "#e8eaf0"  # primary foreground / body text
MUTED = "#8b90a0"  # secondary / dim text

ACCENT_PRIMARY = "#00d8f0"  # cyan / aqua — the "forge" half of the wordmark
ACCENT_SECONDARY = "#f23ccf"  # magenta / pink — the "kit" half
ACCENT_DIM = "#2f6f7a"  # desaturated cyan — quiet accents
BORDER = "#262a36"  # subtle borders / separators / rules
INPUT_RULE = "#aeb4c0"  # light/near-white grey — the composer input bar rules (Claude)

WARNING = "#e0b020"  # amber
SUCCESS = "#3ddc97"  # green
ERROR = "#ff5c7a"  # red/pink

# --- CSS variable map (merged via App.get_css_variables) --------------------
# Re-pins textual's own variables to brand values and exposes brand-specific
# ones. Registered globally so widget DEFAULT_CSS can reference them too.
def css_variables() -> dict:
    """Brand CSS variables to merge into the app's stylesheet variable scope."""

    return {
        "background": BG,
        "surface": BORDER,
        "text": FG,
        "text-muted": MUTED,
        "accent": ACCENT_PRIMARY,
        "accent-secondary": ACCENT_SECONDARY,
        "accent-dim": ACCENT_DIM,
        "brand-border": BORDER,
        "input-rule": INPUT_RULE,
        "warning": WARNING,
        "success": SUCCESS,
        "error": ERROR,
    }


def wordmark(text: str = "forgekit") -> str:
    """Return the cyan→magenta gradient markup for the brand wordmark.

    "forge" renders in :data:`ACCENT_PRIMARY` (cyan) and "kit" in
    :data:`ACCENT_SECONDARY` (magenta) — the two halves of the banner gradient.
    For any other text the split falls on the midpoint so callers always get a
    two-tone mark. Pure markup → unit-testable without a terminal.
    """

    lower = text.lower()
    if "forge" in lower and "kit" in lower:
        idx = lower.index("kit")
        head, tail = text[:idx], text[idx:]
    else:
        mid = max(1, len(text) // 2)
        head, tail = text[:mid], text[mid:]
    return f"[b {ACCENT_PRIMARY}]{head}[/b {ACCENT_PRIMARY}][b {ACCENT_SECONDARY}]{tail}[/b {ACCENT_SECONDARY}]"


__all__ = (
    "BG", "FG", "MUTED",
    "ACCENT_PRIMARY", "ACCENT_SECONDARY", "ACCENT_DIM", "BORDER",
    "WARNING", "SUCCESS", "ERROR",
    "css_variables", "wordmark",
)
