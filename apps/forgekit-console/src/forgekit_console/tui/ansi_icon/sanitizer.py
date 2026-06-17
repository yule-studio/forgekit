"""The ANSI allowlist sanitizer — the security boundary for the icon path.

Raw ANSI is NEVER trusted or replayed. This module reads raw ANSI text and emits a
:class:`~.model.AnsiDoc` containing ONLY what is provably safe to redraw:

* printable text and newlines,
* SGR (``ESC [ … m``) colour / bold / reset — and *within* SGR only the codes we
  understand (reset, bold, the basic-16 / 256 / truecolor fg+bg, default fg/bg).

Everything else is DROPPED and recorded as a reject reason:

* OSC (``ESC ] …``) — incl. OSC 8 hyperlinks and OSC 52 clipboard writes,
* DCS / PM / APC / SOS string controls (``ESC P`` / ``ESC ^`` / ``ESC _`` / ``ESC X``),
* any CSI whose final byte is not ``m`` — cursor moves (``A B C D H f s u``),
  erase/clear (``J K``), private-mode set/reset (``? … h/l``, e.g. ``?1049h`` alt
  screen), scroll regions, …,
* charset designators (``ESC ( ) * +``),
* any other / unknown / truncated escape,
* other C0 control bytes (anything < 0x20 except ``\n``) are dropped silently.

There is no "pass through unknown but harmless" path: unknown ⇒ dropped. The
output model cannot encode an escape, so a renderer built from it is safe by
construction. Pure stdlib → runs in a bare CI install.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from . import model as m
from .model import AnsiDoc, RESET_STYLE, SanitizeResult, Span, Style

_ESC = "\x1b"

# Hard caps — a sanitizer must not be a DoS vector on a hostile/corrupt asset.
_MAX_CHARS = 200_000
_MAX_LINES = 4_000
_MAX_LINE_LEN = 4_000

# --- 256-colour palette → rgb ----------------------------------------------
_BASE16 = (
    (0, 0, 0), (205, 49, 49), (13, 188, 121), (229, 229, 16),
    (36, 114, 200), (188, 63, 188), (17, 168, 205), (229, 229, 229),
    (102, 102, 102), (241, 76, 76), (35, 209, 139), (245, 245, 67),
    (59, 142, 234), (214, 112, 214), (41, 184, 219), (255, 255, 255),
)
_CUBE = (0, 95, 135, 175, 215, 255)


def _xterm256_to_rgb(n: int) -> Optional[m.RGB]:
    if n < 0 or n > 255:
        return None
    if n < 16:
        return _BASE16[n]
    if n < 232:  # 6x6x6 colour cube
        n -= 16
        return (_CUBE[(n // 36) % 6], _CUBE[(n // 6) % 6], _CUBE[n % 6])
    g = 8 + (n - 232) * 10  # 24-step grayscale ramp
    return (g, g, g)


def _basic_to_rgb(code: int) -> Optional[m.RGB]:
    if 30 <= code <= 37:
        return _BASE16[code - 30]
    if 90 <= code <= 97:
        return _BASE16[code - 90 + 8]
    if 40 <= code <= 47:
        return _BASE16[code - 40]
    if 100 <= code <= 107:
        return _BASE16[code - 100 + 8]
    return None


def _apply_sgr(style: Style, param_str: str) -> Style:
    """Fold an SGR parameter string onto *style*. Unknown SGR codes are ignored.

    Only the colour/bold/reset subset is honoured; everything else inside an ``m``
    sequence is safe to drop (it cannot move the cursor or touch the screen state).
    """

    # Empty params (``ESC[m``) == reset.
    raw = param_str if param_str != "" else "0"
    try:
        codes = [int(p) if p != "" else 0 for p in raw.split(";")]
    except ValueError:
        return style  # malformed numeric params → no style change

    fg, bg, bold = style.fg, style.bg, style.bold
    i = 0
    while i < len(codes):
        c = codes[i]
        if c == 0:
            fg, bg, bold = None, None, False
        elif c == 1:
            bold = True
        elif c == 22:
            bold = False
        elif c == 39:
            fg = None
        elif c == 49:
            bg = None
        elif c in (38, 48):  # extended fg/bg: 38;5;n | 38;2;r;g;b
            target_fg = c == 38
            if i + 1 < len(codes) and codes[i + 1] == 5 and i + 2 < len(codes):
                rgb = _xterm256_to_rgb(codes[i + 2])
                i += 2
                if target_fg:
                    fg = rgb
                else:
                    bg = rgb
            elif i + 1 < len(codes) and codes[i + 1] == 2 and i + 4 < len(codes):
                rgb = tuple(max(0, min(255, codes[i + 2 + k])) for k in range(3))
                i += 4
                if target_fg:
                    fg = rgb  # type: ignore[assignment]
                else:
                    bg = rgb  # type: ignore[assignment]
            # malformed extended colour → ignore the introducer
        else:
            rgb = _basic_to_rgb(c)
            if rgb is not None:
                if 40 <= c <= 47 or 100 <= c <= 107:
                    bg = rgb
                else:
                    fg = rgb
            # any other SGR code (italic/underline/blink/…) → ignored (safe)
        i += 1
    return Style(fg=fg, bg=bg, bold=bold)


def _consume_string_control(text: str, j: int, n: int) -> int:
    """Skip an OSC/DCS/PM/APC/SOS body to its ST (``ESC \\``) or BEL terminator."""

    while j < n:
        ch = text[j]
        if ch == "\x07":  # BEL terminates OSC
            return j + 1
        if ch == _ESC and j + 1 < n and text[j + 1] == "\\":  # ST
            return j + 2
        j += 1
    return n


def _handle_escape(
    text: str, i: int, n: int, style: Style, rejected: List[str]
) -> Tuple[int, Style]:
    """Process an ESC at index *i*. Returns ``(next_index, new_style)``.

    SGR is the only sequence that survives (folded into *style*); every other
    escape is consumed and its reason recorded — nothing unsafe is ever emitted.
    """

    if i + 1 >= n:
        rejected.append(m.REJECT_LONE_ESC)
        return n, style
    nxt = text[i + 1]

    if nxt == "[":  # CSI — parameter/intermediate bytes then a final 0x40–0x7e
        j = i + 2
        while j < n and 0x20 <= ord(text[j]) <= 0x3F:
            j += 1
        if j >= n or not (0x40 <= ord(text[j]) <= 0x7E):
            rejected.append(m.REJECT_MALFORMED_CSI)
            return (j if j < n else n), style
        final = text[j]
        params = text[i + 2 : j]
        end = j + 1
        # SGR only, and only when it carries no private-mode marker (``?`` / ``<``…).
        if final == "m" and not (params and params[0] in "?<=>"):
            return end, _apply_sgr(style, params)
        rejected.append(m.REJECT_CSI)  # cursor / erase / private-mode / scroll / …
        return end, style

    if nxt == "]":  # OSC (hyperlinks, clipboard, title, …)
        rejected.append(m.REJECT_OSC)
        return _consume_string_control(text, i + 2, n), style

    if nxt in "P^_X":  # DCS / PM / APC / SOS string controls
        rejected.append(m.REJECT_DCS)
        return _consume_string_control(text, i + 2, n), style

    if nxt in "()*+":  # charset designators → drop the designator byte too
        rejected.append(m.REJECT_CHARSET)
        return min(i + 3, n), style

    rejected.append(m.REJECT_UNKNOWN_ESC)  # ESC c (RIS), ESC 7/8, ESC =, …
    return i + 2, style


def sanitize(text: str) -> SanitizeResult:
    """Parse raw ANSI *text* into a safe :class:`AnsiDoc`, dropping all unsafe bytes.

    Pure and total: never raises, never writes anything. The returned
    :class:`SanitizeResult` carries the safe doc plus the reason label for every
    sequence that was stripped (empty when the input was already clean).
    """

    if not isinstance(text, str):  # defensive — accept only decoded text
        return SanitizeResult(AnsiDoc(()), ok=False, reason="not-text")
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS]

    rejected: List[str] = []
    lines: List[Tuple[Span, ...]] = []
    cur_spans: List[Span] = []
    buf: List[str] = []
    style = RESET_STYLE

    def flush() -> None:
        if buf:
            cur_spans.append(Span("".join(buf), style))
            buf.clear()

    def end_line() -> None:
        flush()
        lines.append(tuple(cur_spans))
        cur_spans.clear()

    i, n = 0, len(text)
    while i < n and len(lines) < _MAX_LINES:
        ch = text[i]
        if ch == _ESC:
            flush()
            i, style = _handle_escape(text, i, n, style, rejected)
            continue
        if ch == "\n":
            end_line()
            i += 1
            continue
        o = ord(ch)
        if o < 0x20:  # other C0 control (CR, TAB, BEL, …) → drop silently
            i += 1
            continue
        if len(buf) + sum(len(s.text) for s in cur_spans) < _MAX_LINE_LEN:
            buf.append(ch)
        i += 1
    end_line()

    # trim trailing blank lines (a final newline yields one; pixel art often pads)
    while lines and not any(s.text for s in lines[-1]):
        lines.pop()

    doc = AnsiDoc(tuple(lines))
    ok = not doc.is_empty
    reason = "" if ok else "empty-after-sanitize"
    return SanitizeResult(doc=doc, ok=ok, rejected=tuple(rejected), reason=reason)


def _sgr_for(style: Style) -> str:
    """Build the minimal safe SGR introducer for *style* (truecolor). '' for reset."""

    parts: List[str] = []
    if style.bold:
        parts.append("1")
    if style.fg is not None:
        parts.append("38;2;{};{};{}".format(*style.fg))
    if style.bg is not None:
        parts.append("48;2;{};{};{}".format(*style.bg))
    return _ESC + "[" + ";".join(parts) + "m" if parts else ""


def serialize_clean(doc: AnsiDoc) -> str:
    """Re-emit *doc* as a CANONICAL, minimal ANSI string (truecolor SGR only).

    Used by the bake step to write the runtime asset: a re-serialization of the
    sanitized model, so the shipped file contains only the safe SGR subset (no OSC,
    no cursor, no private modes) regardless of what the raw source held. Each line
    resets at its end so styles never bleed across rows.
    """

    out: List[str] = []
    for line in doc.lines:
        row = []
        for span in line:
            intro = _sgr_for(span.style)
            row.append(intro + span.text)
        out.append("".join(row) + (_ESC + "[0m" if any(s.style != RESET_STYLE for s in line) else ""))
    return "\n".join(out) + "\n"


__all__ = ("sanitize", "serialize_clean")
