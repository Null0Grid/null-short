#!/usr/bin/env bash
# Null//Short installer for Debian-based systems (Null OS, Debian, Ubuntu).
# Usage:  sudo ./install.sh
set -euo pipefail

PREFIX=/usr/lib/null-short
HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET_USER="${SUDO_USER:-}"

say() { printf '\033[36m::\033[0m %s\n' "$*"; }
die() { printf '\033[31mxx\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run with sudo:  sudo ./install.sh"
[[ -n "$TARGET_USER" && "$TARGET_USER" != root ]] || die "run via sudo from your normal user account"

python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
  || die "Python 3.11+ is required"

if ! python3 -c 'import evdev' 2>/dev/null; then
  say "installing python3-evdev"
  apt-get install -y python3-evdev
fi

say "installing files to $PREFIX"
rm -rf "$PREFIX/nullshort"
install -d "$PREFIX/config"
cp -r "$HERE/nullshort" "$PREFIX/"
install -m 644 "$HERE/config/config.toml" "$PREFIX/config/config.toml"
find "$PREFIX" -name '__pycache__' -prune -exec rm -rf {} +

cat > /usr/bin/null-short <<EOF
#!/bin/sh
PYTHONPATH=$PREFIX exec python3 -m nullshort "\$@"
EOF
chmod 755 /usr/bin/null-short

if [[ ! -f /etc/null-short/config.toml ]]; then
  install -D -m 644 "$HERE/config/config.toml" /etc/null-short/config.toml
  say "default config at /etc/null-short/config.toml"
else
  say "keeping existing /etc/null-short/config.toml"
fi

say "setting up the virtual keyboard (uinput)"
install -m 644 "$HERE/packaging/70-null-short.rules" /etc/udev/rules.d/70-null-short.rules
echo uinput > /etc/modules-load.d/null-short.conf
modprobe uinput || true
udevadm control --reload-rules
udevadm trigger --subsystem-match=misc --sysname-match=uinput || true

say "adding $TARGET_USER to the input group"
usermod -aG input "$TARGET_USER"

install -m 644 "$HERE/packaging/null-short.service" /usr/lib/systemd/user/null-short.service

cat <<EOF

  Null//Short installed.

  1. Log out and back in (so the input group applies).
  2. Start it:   systemctl --user enable --now null-short
  3. Hold Caps Lock, tap ↑ → ↑, release  →  Firefox.

  See your codes:   null-short list
  Emergency stop:   Ctrl+Alt+Shift+Esc
  Your own config:  cp /etc/null-short/config.toml ~/.config/null-short/config.toml
EOF
