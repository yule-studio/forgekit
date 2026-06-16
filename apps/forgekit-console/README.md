# forgekit-console

Forgekit operator console — a Textual TUI over the existing `yule` runtime /
harness / doctor surfaces.

- Entry: `forgekit` (bare) or `forgekit console` → opens the full-screen console.
- Install the TUI dep: `pip install -e '.[console]'` (textual).
- Pure core (`commands` / `data` / `models` / `tui.render`) is stdlib-only and
  testable without textual; the `tui.app` layer adds the terminal UI.

Scope (1차): console frame + slash palette + status pane + input + agent-entry
stubs. Out of scope: live LLM submit, Agent Town, Discord push.

See [`docs/forgekit-console.md`](../../docs/forgekit-console.md).
