# selection-contrast — transcript drag-selection contrast + mode-aware copy visibility

Two console-parity gaps closed with **real structure** (runtime property proof, not CSS
reading; honest per-mode guidance, not fake):

## Gap 1 — cross-widget drag-selection contrast
When the full-screen TUI captures the mouse, dragging selects text across widgets (the
transcript included). Textual highlights that selection with its `screen--selection`
component → `$screen-selection-background`, which defaulted to a ~50%-alpha blue
(`#0178D47F`) — a muddy, low-contrast block on the near-black brand background. The previous
selection-contrast work (`test_tui_selection_contrast`) only themed the composer input
(`text-area--selection`), not the transcript.

Fix: `theme.css_variables()` pins `screen-selection-background` to the brand desaturated-cyan
(`accent-dim`) and `styles.py` forces the light foreground on top, so every selected line is
uniformly high-contrast — matching the composer treatment. **Measured 4.75:1** (WCAG AA), proven
by resolving the component style on a mounted app (`help-select-copy.svg` + evidence txt).

## Gap 2 — mode-aware select/copy visibility
The help "select & copy" block always showed the full-screen mouse-capture caveat regardless of
the actual run mode. It is now **mode-aware** (`render.selection_copy_lines(inline)`):
- **inline** (mouse NOT captured): drag = terminal-native selection + terminal copy; `/copy` also works.
- **full** (mouse captured): drag = in-app selection + `Ctrl+C`; plain terminal drag blocked (modifier-drag to bypass); `/copy` works.

## Regenerate
```
python3 -c "from tests.forgekit import _SRC; import runpy; \
  runpy.run_path('apps/forgekit-console/examples/selection-contrast/_regen.py', run_name='__main__')"
```
Outputs `selection-contrast-evidence.txt` (runtime numbers) + `help-select-copy.svg` (real screenshot).

## Verify
`tests/forgekit/test_tui_transcript_selection.py` — runtime `screen--selection` contrast (≥4.5:1)
+ mode-aware guidance (inline vs full). Composer input contrast stays covered by
`tests/forgekit/test_tui_selection_contrast.py`.
