"""Scans installed apps (.desktop files) and sorts each one into a family.

Family = the second arrow of every app code:
    ↑  dev & system   (terminals, editors, IDEs, settings, file managers, tools)
    →  internet       (browsers, chat, mail, torrents, remote desktop)
    ↓  games          (games, launchers, emulators)
    ←  everything else (media, graphics, office, AI, education...)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import launcher

FAMILY_NAMES = {"U": "dev & system", "R": "internet", "D": "games", "L": "media & other"}

# Checked in this order; the first family with a matching category wins.
_FAMILY_RULES = [
    ("D", {"Game", "Emulator"}),
    ("R", {"WebBrowser", "Network", "Email", "Chat", "InstantMessaging", "IRCClient",
           "FileTransfer", "P2P", "RemoteAccess", "News", "Feed", "VideoConference"}),
    ("U", {"Development", "IDE", "TerminalEmulator", "System", "Settings", "Utility",
           "TextEditor", "FileManager", "FileTools", "PackageManager", "Security",
           "Monitor", "Debugger", "RevisionControl"}),
]
DEFAULT_FAMILY = "L"


@dataclass
class App:
    id: str
    name: str
    path: Path
    categories: list[str] = field(default_factory=list)

    @property
    def family(self) -> str:
        return classify(self.categories)


def classify(categories) -> str:
    cats = set(categories)
    for fam, names in _FAMILY_RULES:
        if cats & names:
            return fam
    return DEFAULT_FAMILY


def _visible(entry: dict[str, str]) -> bool:
    if entry.get("Type", "Application") != "Application":
        return False
    if entry.get("NoDisplay", "").lower() == "true" or entry.get("Hidden", "").lower() == "true":
        return False
    if not entry.get("Exec"):
        return False
    desktops = {d for d in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":") if d}
    if desktops:
        only = {d for d in entry.get("OnlyShowIn", "").split(";") if d}
        never = {d for d in entry.get("NotShowIn", "").split(";") if d}
        if only and not (only & desktops):
            return False
        if never & desktops:
            return False
    return True


def scan(dirs: list[Path] | None = None) -> dict[str, App]:
    """All visible installed apps, keyed by desktop ID. Earlier dirs win."""
    found: dict[str, App] = {}
    shadowed: set[str] = set()
    for base in dirs if dirs is not None else launcher.data_dirs():
        appdir = base / "applications"
        if not appdir.is_dir():
            continue
        for path in sorted(appdir.rglob("*.desktop")):
            app_id = str(path.relative_to(appdir))[: -len(".desktop")].replace("/", "-")
            if app_id in found or app_id in shadowed:
                continue
            try:
                entry = launcher.read_entry(path)
            except OSError:
                continue
            if not _visible(entry):
                # A hidden copy in a higher-priority dir hides lower ones too.
                shadowed.add(app_id)
                continue
            cats = [c for c in entry.get("Categories", "").split(";") if c]
            found[app_id] = App(app_id, entry.get("Name", app_id), path, cats)
    return found


def fingerprint(dirs: list[Path] | None = None) -> tuple:
    """Cheap change detector: modification times of the application dirs."""
    out = []
    for base in dirs if dirs is not None else launcher.data_dirs():
        appdir = base / "applications"
        try:
            out.append((str(appdir), appdir.stat().st_mtime_ns))
        except OSError:
            out.append((str(appdir), None))
    return tuple(out)
