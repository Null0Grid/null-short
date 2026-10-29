# Null//Short

Arrow-code launcher for Linux, part of the Null Grid family. Hold a trigger key, tap a
short arrow code, let go, and the app opens. Think stratagem inputs, but for your desktop.

```
Hold Caps Lock  →  tap ↑ → ↑  →  release  →  Firefox
```

## Codes

Every app code starts with **↑** ("launch").

| Code    | App            |
|---------|----------------|
| ↑ → ↑   | Firefox        |
| ↑ → →   | Microsoft Edge |
| ↑ → ↓   | qBittorrent    |
| ↑ ↓ →   | Steam          |
| ↑ ↓ ←   | Minecraft      |
| ↑ ← ↑   | Claude (opens claude.ai if no desktop app is installed) |

Each code tries the distro package first, then the Flatpak, so it works either way.
Minecraft also accepts Prism Launcher.

## Install

Debian-based systems (Null OS, Debian 12+, Ubuntu 23.04+). Needs Python 3.11+.

```bash
git clone https://github.com/Null0Grid/null-short
cd null-short
sudo ./install.sh
# log out and back in, then:
systemctl --user enable --now null-short
```

The installer pulls in `python3-evdev`, adds you to the `input` group, sets up the
virtual keyboard (uinput) and installs a systemd user service.

## Using it

- **Fire a code:** hold the trigger, tap the arrows, release.
- **Wrong code:** an error sound plays and nothing happens.
- **Changed your mind:** press **Esc** while holding the trigger.
- **Tap Caps Lock on its own:** it still toggles Caps Lock as normal.
- **Emergency stop:** **Ctrl+Alt+Shift+Esc** shuts the daemon down and hands the
  keyboard straight back.

```bash
null-short list               # codes, and which apps were found
null-short check              # validate your config
null-short try "↑ → ↑"        # look up a code (add --launch to open it)
null-short devices            # keyboards it will take over
journalctl --user -u null-short -f   # live log
```

## Config

Copy the default and edit your own:

```bash
mkdir -p ~/.config/null-short
cp /etc/null-short/config.toml ~/.config/null-short/config.toml
null-short check && systemctl --user restart null-short
```

```toml
trigger = "KEY_CAPSLOCK"      # or KEY_RIGHTALT, KEY_F13, ...

[[code]]
name = "Firefox"
seq  = "↑ → ↑"               # also: "up right up" or "URU"
apps = ["firefox-esr", "firefox", "org.mozilla.firefox"]

[[code]]
name = "NULL // PLAY"
seq  = "↑ ↓ ↓ ↑"
run  = "gamemode"            # any command
cooldown = 2                 # seconds before it can fire again (default 1)
```

Each entry needs at least one of `apps` (.desktop IDs, tried in order), `run` or `url`.
Duplicate codes are rejected.

## How it works

1. The daemon grabs each keyboard through evdev so it sees every key first.
2. A small router passes everything straight through until the trigger goes down.
3. While the trigger is held, arrows are recorded and swallowed (they don't scroll
   your page), and the sequence is checked against the codes as you type.
4. On release, an exact match launches the app; anything else is a fail.
5. Passed-through keys are re-sent via a virtual keyboard (uinput).

It **fails open**: if the router hits an error, the key is passed through untouched.
It waits for all keys to be released before grabbing a keyboard, so nothing sticks,
and it picks up keyboards you plug in later.

## Security note

Being in the `input` group lets programs running as you read keyboard input
directly, which is what Null//Short needs to work. Only install it on machines you trust.
`sudo ./uninstall.sh` removes everything except your config and group membership.

## Known limits (v0.1)

- No on-screen HUD yet; feedback is sounds only (and optional notifications).
- The Caps Lock LED on the physical keyboard may not light, even though Caps Lock works.
- Codes come only from the config; the community registry and automatic codes for
  every installed app are on the roadmap.

## Development

```bash
python3 -m unittest discover -s tests     # no evdev or keyboard needed
python3 -m nullshort -c config/config.toml list
```
