# Null//Short

Arrow-code launcher for Linux, part of the Null Grid family. Hold a trigger key, tap a
short arrow code, let go, and the app opens. Think stratagem inputs, but for your desktop.

```
Hold Caps Lock  →  tap ↑ → ↑  →  release  →  Firefox
```

Every installed app gets a code automatically. Popular apps get short, hand-picked
codes from the official registry; everything else gets an automatic one.

## The code grammar

Every app code starts with **↑** ("launch"). The second arrow is the app's **family**,
worked out from the categories in its `.desktop` file:

| Starts with | Family          | Examples                                   |
|-------------|-----------------|--------------------------------------------|
| ↑ ↑         | dev & system    | terminals, editors, IDEs, settings, files  |
| ↑ →         | internet        | browsers, chat, mail, torrents, remote     |
| ↑ ↓         | games           | games, launchers, emulators                |
| ↑ ←         | media & other   | video, music, graphics, office, AI         |

So you can often guess roughly where an app lives before you learn it.

## Where codes come from

The first source with a code wins:

1. **Your config:** `[[code]]` entries you write yourself.
2. **The official registry** (`registry/registry.toml`): short 3–4 arrow codes for
   popular apps. It ships with 40 apps, including:

   | Code  | App            | Code  | App         |
   |-------|----------------|-------|-------------|
   | ↑ → ↑ | Firefox        | ↑ ↓ → | Steam       |
   | ↑ → → | Microsoft Edge | ↑ ↓ ← | Minecraft   |
   | ↑ → ↓ | qBittorrent    | ↑ ← ↑ | Claude      |

3. **Automatic codes** for every other app: **↑ + family + 3 arrows** taken from a hash
   of the app's ID.
   - The same app gets the same code on every machine, with no list to maintain.
   - If that code is taken, the next hash is tried. A family with more than 64 auto
     apps (Steam adds one per game) spills over into longer codes.
   - Once given out, a code is **pinned** in `~/.local/state/null-short/pins.toml`, so
     installing more apps never moves it.
   - If an app later joins the registry, it gets the short code and its old auto code
     keeps working too.

Registry codes are 3–4 arrows and auto codes are 5+, so they can never clash.

Each entry tries the distro package first, then the Flatpak. Claude opens claude.ai
in your browser when no desktop app is installed.

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
- **New apps** get their code within about 10 seconds of installing; no restart needed.

```bash
null-short list                   # every code, and where it came from
null-short list --source auto     # just the automatic ones
null-short classify steam         # an app's categories, family and code
null-short try "↑ → ↑"            # look up a code (add --launch to open it)
null-short check                  # validate config + registry
null-short devices                # keyboards it will take over
journalctl --user -u null-short -f
```

## The official registry

**Suggest an app:**

```bash
null-short submit "null file"
```

This classifies the app, picks a free short code in its family (or use
`--code "↑ ↑ ← ↓"`), and opens a pre-filled GitHub issue in your browser. Nothing is
sent until you press Submit, and no GitHub token is needed. Use `--print` to get the
link instead.

**Get the latest registry** without reinstalling:

```bash
null-short update-registry
```

It's only saved if it's valid and newer than what you have.

**Maintaining it** (repo owner): paste the entry from the issue into
`registry/registry.toml`, bump `version`, and push. CI rejects duplicate codes or IDs,
codes of the wrong length, and codes that don't start with ↑. Published codes should
never change.

## Config

```bash
mkdir -p ~/.config/null-short
cp /etc/null-short/config.toml ~/.config/null-short/config.toml
```

```toml
trigger   = "KEY_CAPSLOCK"   # or KEY_RIGHTALT, KEY_F13, ...
auto_apps = true             # automatic codes for every installed app

[[code]]
name = "NULL // PLAY"
seq  = "↓ ↓ ↑"               # also: "down down up" or "DDU"
run  = "gamemode"            # or apps = [".desktop IDs"], or url = "..."
cooldown = 2                 # seconds before it can fire again (default 1)
```

Your codes override registry and auto codes. The daemon reloads the config by itself.

## How it works

1. The daemon grabs each keyboard through evdev so it sees every key first.
2. A small router passes everything straight through until the trigger goes down.
3. While the trigger is held, arrows are recorded and swallowed (they don't scroll
   your page).
4. On release, an exact match launches the app; anything else is a fail.
5. Passed-through keys are re-sent via a virtual keyboard (uinput).

It **fails open**: if the router hits an error, the key is passed through untouched.
It waits for all keys to be released before grabbing a keyboard, so nothing sticks,
and it picks up keyboards you plug in later.

## Security note

Being in the `input` group lets programs running as you read keyboard input
directly, which is what Null//Short needs to work. Only install it on machines you trust.
`sudo ./uninstall.sh` removes everything except your config, pins and group membership.

## Known limits

- No on-screen HUD yet; feedback is sounds only (and optional notifications).
- The Caps Lock LED on the physical keyboard may not light, even though Caps Lock works.
- The same app installed two ways (distro package and Flatpak) has two IDs. The registry
  merges them; auto codes treat them as separate apps.
- `update-registry` needs the GitHub repo to be public.

## Development

```bash
python3 -m unittest discover -s tests     # no evdev or keyboard needed
python3 -m nullshort -c config/config.toml list
```
