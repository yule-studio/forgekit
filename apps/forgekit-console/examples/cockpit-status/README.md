# cockpit-status — operator cockpit status line (GW5)

The console issue line already showed the runtime **mode posture** (routing/usage/approval/loop).
The remaining operator-cockpit gap was that the two control-plane facts an operator most needs —
how many goals are **parked awaiting their approval**, and how much of today's **token budget** is
spent — were only reachable by polling (`/goal awaiting`, `/usage`). They are now surfaced as
badges on the persistent issue line, refreshed at every turn boundary.

Honesty rails (no fakes):
- `awaiting` is a REAL count from the goal store (reuses `goal_continuity_status` — the same
  snapshot `forgekit runtime status` shows), never a fabricated number;
- `budget` is REAL: today's ledger spend ÷ configured `daily_token_budget`;
- a store / ledger read failure degrades to NO badge (0 / None) — never a guess;
- badges default OFF, so `runtime_mode_line`'s existing callers/tests are unchanged;
- the awaiting badge is warn-coloured + carries the `/goal awaiting` action pointer so it
  can't be missed; the budget badge turns warn at ≥90%.

## Regenerate
```
python3 -c "from tests.forgekit import _SRC; exec(open('apps/forgekit-console/examples/cockpit-status/_regen.py').read())" \
  > apps/forgekit-console/examples/cockpit-status/cockpit-status-evidence.txt
```

## Verify
`tests/forgekit/test_tui_cockpit_status.py` — pure render badges (backward compatible),
`ForgekitConsoleApp._cockpit_badges()` reading the live goal store + usage ledger, and a
mounted pilot proving the `#issue` widget shows the awaiting badge.
