"""Finds installed apps by their .desktop ID and launches them."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path


def data_dirs() -> list[Path]:
    """Where .desktop files live, highest priority first (XDG spec + Flatpak)."""
    home = Path.home()
    xdg_home = os.environ.get("XDG_DATA_HOME") or str(home / ".local/share")
    xdg_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs = [Path(xdg_home)]
    dirs += [Path(p) for p in xdg_dirs.split(":") if p]
    dirs += [
        home / ".local/share/flatpak/exports/share",
        Path("/var/lib/flatpak/exports/share"),
        Path("/var/lib/snapd/desktop"),
    ]
    seen, out = set(), []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def find_desktop(app_id: str, dirs: list[Path] | None = None) -> Path | None:
    for d in dirs if dirs is not None else data_dirs():
        path = d / "applications" / f"{app_id}.desktop"
        if path.is_file():
            return path
    return None


def read_entry(path: Path) -> dict[str, str]:
    """Read the [Desktop Entry] group of a .desktop file."""
    entry: dict[str, str] = {}
    in_group = False
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            in_group = line == "[Desktop Entry]"
            continue
        if in_group and "=" in line:
            key, _, val = line.partition("=")
            entry.setdefault(key.strip(), val.strip())
    return entry


_FIELD_CODES = set("fFuUickdDnNvm")


def exec_args(exec_line: str) -> list[str]:
    """Split an Exec= line and drop field codes like %U, per the spec."""
    args: list[str] = []
    for token in shlex.split(exec_line):
        if len(token) == 2 and token[0] == "%" and token[1] in _FIELD_CODES:
            continue
        out, i = [], 0
        while i < len(token):
            ch = token[i]
            if ch == "%" and i + 1 < len(token):
                nxt = token[i + 1]
                if nxt == "%":
                    out.append("%")
                # embedded field codes are dropped
                i += 2
                continue
            out.append(ch)
            i += 1
        if out:
            args.append("".join(out))
    return args


def resolve(apps: list[str], dirs: list[Path] | None = None) -> tuple[str, Path] | None:
    """First installed app from the list, as (app_id, desktop_path)."""
    for app_id in apps:
        path = find_desktop(app_id, dirs)
        if path:
            return app_id, path
    return None


def _spawn(args: list[str], cwd: str | None = None) -> None:
    subprocess.Popen(
        args,
        cwd=cwd if cwd and os.path.isdir(cwd) else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def launch(entry, dirs: list[Path] | None = None) -> str:
    """Launch a code's target. Returns a short description of what ran.

    Order: first installed app from `apps`, then `run` command, then `url`.
    Raises LookupError if nothing could be launched.
    """
    found = resolve(entry.apps, dirs) if entry.apps else None
    if found:
        app_id, path = found
        info = read_entry(path)
        args = exec_args(info.get("Exec", ""))
        if not args:
            raise LookupError(f"{path} has no Exec line")
        _spawn(args, info.get("Path"))
        return f"app {app_id}"
    if entry.run:
        _spawn(shlex.split(entry.run))
        return f"command {entry.run!r}"
    if entry.url:
        opener = shutil.which("xdg-open") or "xdg-open"
        _spawn([opener, entry.url])
        return f"url {entry.url}"
    raise LookupError(f"{entry.name}: none of {entry.apps} is installed")
