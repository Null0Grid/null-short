#!/usr/bin/env bash
# Removes null-short. Leaves your config, pins and group membership alone.
# Usage:  sudo ./uninstall.sh
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }

if [[ -n "${SUDO_USER:-}" ]]; then
  sudo -u "$SUDO_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$SUDO_USER")" \
    systemctl --user disable --now null-short 2>/dev/null || true
fi

rm -f /usr/lib/systemd/user/null-short.service /usr/bin/null-short \
      /etc/udev/rules.d/70-null-short.rules /etc/modules-load.d/null-short.conf
rm -rf /usr/lib/null-short
udevadm control --reload-rules || true

echo "null-short removed. /etc/null-short was kept; delete it if you like."
echo "To leave the input group:  sudo gpasswd -d ${SUDO_USER:-\$USER} input"
