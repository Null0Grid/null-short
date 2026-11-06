"""Automatic codes for apps that aren't in the registry, plus local pinning.

Auto code = ↑ + family arrow + 3 arrows from a hash of the app ID.
The hash means the same app gets the same code on every machine. If that code
is already taken, the next salt is tried. Once given out, a code is pinned in
~/.local/state/null-short/pins.toml so installing other apps never moves it.
"""

from __future__ import annotations

import hashlib
import os
import tomllib
from pathlib import Path

from . import codes

HASH_ARROWS = 3
_DIRS = "URDL"


def hash_code(app_id: str, family: str, salt: int = 0, length: int = HASH_ARROWS) -> tuple[str, ...]:
    digest = hashlib.sha256(f"{app_id}|{salt}".encode()).digest()
    arrows = []
    for i in range(length):
        byte = digest[i // 4]
        arrows.append(_DIRS[(byte >> (2 * (i % 4))) & 3])
    return ("U", family, *arrows)


def pins_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(base) / "null-short" / "pins.toml"


def load_pins(path: Path | None = None) -> dict[str, tuple[str, ...]]:
    p = path or pins_path()
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    pins = {}
    for app_id, text in data.get("pins", {}).items():
        try:
            pins[app_id] = codes.parse(text)
        except (codes.CodeError, TypeError):
            continue
    return pins


def save_pins(pins: dict[str, tuple[str, ...]], path: Path | None = None) -> None:
    p = path or pins_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# null-short: auto codes already given out. They never change.",
             "# Delete a line to let that app get a fresh code.", "", "[pins]"]
    for app_id in sorted(pins):
        key = app_id.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'"{key}" = "{"".join(pins[app_id])}"')
    tmp = p.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(p)
