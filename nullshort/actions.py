"""Turns router results into launches, sounds and notifications."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path

from . import codes, launcher
from .config import Config, Entry

log = logging.getLogger("null-short")

SOUND_DIR = Path("/usr/share/sounds/freedesktop/stereo")
SOUNDS = {
    "fire": "complete.oga",
    "fail": "dialog-error.oga",
    "cancel": "dialog-information.oga",
}


def _quiet(args: list[str]) -> None:
    try:
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


class Dispatcher:
    def __init__(self, cfg: Config, clock=time.monotonic, launch=launcher.launch):
        self.cfg = cfg
        self.clock = clock
        self._launch = launch
        self._last: dict[tuple[str, ...], float] = {}
        self._player = shutil.which("pw-play") or shutil.which("paplay")

    def handle(self, event: str | None, action) -> str | None:
        """React to one router result. Returns a log line (handy for tests)."""
        if event == "fire":
            return self._fire(action)
        if event == "fail":
            seq = codes.show(action) if action else ""
            self.sound("fail")
            self.notify(f"No code {seq}")
            msg = f"fail: {seq}"
            log.info(msg)
            return msg
        if event == "cancel":
            self.sound("cancel")
            return "cancel"
        return None

    def _fire(self, entry: Entry) -> str:
        now = self.clock()
        last = self._last.get(entry.code)
        if last is not None and now - last < entry.cooldown:
            msg = f"cooldown: {entry.name}"
            log.info(msg)
            return msg
        self._last[entry.code] = now
        try:
            what = self._launch(entry)
        except (LookupError, OSError, ValueError) as e:
            self.sound("fail")
            self.notify(f"{entry.name}: {e}")
            msg = f"error: {entry.name}: {e}"
            log.warning(msg)
            return msg
        self.sound("fire")
        msg = f"fire: {codes.show(entry.code)} {entry.name} ({what})"
        log.info(msg)
        return msg

    def sound(self, kind: str) -> None:
        if not self.cfg.sounds or not self._player:
            return
        path = SOUND_DIR / SOUNDS.get(kind, "")
        if path.is_file():
            _quiet([self._player, str(path)])

    def notify(self, text: str) -> None:
        if self.cfg.notify and shutil.which("notify-send"):
            _quiet(["notify-send", "-a", "Null//Short", "-t", "2000", "Null//Short", text])
