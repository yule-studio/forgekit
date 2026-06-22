# launchd unit for the ForgeKit control-plane daemon (GW7)

`forgekit runtime serve` is the bounded always-on daemon (heartbeat / kill-switch /
cooldown). On a **Mac mini host** (the 1차 ForgeKit control-plane host — see
`docs/control-plane-architecture.md` §3) it runs as a per-user **LaunchAgent**.
The Linux/systemd path (`deploy/systemd/`) is the 1급 production always-on host;
both invoke the same CLI and read the same `~/.forgekit/config.json` — only the
supervisor differs (no machine-specific hacks).

## Files

- `com.forgekit.runtime.plist` — LaunchAgent template. Substitute the
  `__PLACEHOLDER__` tokens before loading.

## Honest limit (read first)

A **closed lid suspends macOS** — a LaunchAgent does **not** survive clamshell
sleep, so "24h always-on" on a laptop/lid-closed Mac mini is not real without:

- running **lid-open** with `caffeinate -s` (keeps the system awake), or
- `sudo pmset -c sleep 0` (disable sleep on AC power), or
- using the **Linux/systemd** path, which is the genuine 1급 always-on host.

This template does not pretend the daemon runs through sleep.

## Install

```bash
# 1) Substitute placeholders into a real plist:
FORGEKIT_BIN="$(command -v forgekit)"          # installed entrypoint
REPO_ROOT="$HOME/local-dev/yule-studio-agent"  # your checkout
FORGEKIT_HOME="$HOME/.forgekit"
sed -e "s#__FORGEKIT_BIN__#$FORGEKIT_BIN#g" \
    -e "s#__REPO_ROOT__#$REPO_ROOT#g" \
    -e "s#__FORGEKIT_HOME__#$FORGEKIT_HOME#g" \
    -e "s#__USER_HOME__#$HOME#g" \
    deploy/launchd/com.forgekit.runtime.plist \
    > "$HOME/Library/LaunchAgents/com.forgekit.runtime.plist"

# 2) Ensure the log dir exists (logs live OUTSIDE the repo — no runtime state in git):
mkdir -p "$HOME/Library/Logs/forgekit"

# 3) Load it:
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.forgekit.runtime.plist"
```

## Operate

```bash
launchctl kickstart -k "gui/$(id -u)/com.forgekit.runtime"   # restart (e.g. after `git pull` + reinstall)
launchctl print "gui/$(id -u)/com.forgekit.runtime"          # status
launchctl bootout "gui/$(id -u)/com.forgekit.runtime"        # stop + unload
forgekit runtime status                                       # honest daemon heartbeat (no launchd needed)
forgekit runtime stop                                         # set kill-switch — daemon stops next tick
```

## Secrets

Do **not** commit secrets. Put them in macOS Keychain or `~/.forgekit/` (mode 600)
and reference via env; never in this plist (it is a checked-in template).

## Upgrade

```bash
cd "$REPO_ROOT" && git pull && python3 -m pip install -e .
launchctl kickstart -k "gui/$(id -u)/com.forgekit.runtime"
```

## See also

- `docs/control-plane-architecture.md` §3 — Mac mini vs Linux host matrix + sleep policy.
- `deploy/systemd/` — the Linux/systemd 1급 always-on path.
- `docs/operator-surfaces.md` — `/daemon` heartbeat surface + CLI `forgekit runtime serve|status|stop`.
