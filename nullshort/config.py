"""Loads and validates config.toml."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import codes

BUNDLED = Path(__file__).resolve().parent.parent / "config" / "config.toml"
SYSTEM = Path("/etc/null-short/config.toml")


def user_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "null-short" / "config.toml"


class ConfigError(ValueError):
    pass


@dataclass
class Entry:
    name: str
    code: tuple[str, ...]
    apps: list[str] = field(default_factory=list)
    run: str | None = None
    url: str | None = None
    cooldown: float = 1.0
    source: str = "config"     # config | registry | auto | auto (old)


@dataclass
class Config:
    trigger: str = "KEY_CAPSLOCK"
    tap_passthrough: bool = True
    sounds: bool = True
    notify: bool = False
    auto_apps: bool = True
    entries: list[Entry] = field(default_factory=list)
    source: Path | None = None

    def table(self) -> dict[tuple[str, ...], Entry]:
        return {e.code: e for e in self.entries}


def find(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit)
    for p in (user_path(), SYSTEM, BUNDLED):
        if p.is_file():
            return p
    raise ConfigError("no config.toml found")


def load(path: str | Path | None = None) -> Config:
    p = find(str(path) if path else None)
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"{p}: {e}") from e
    return from_dict(data, source=p)


def from_dict(data: dict, source: Path | None = None) -> Config:
    cfg = Config(source=source)
    trigger = data.get("trigger", cfg.trigger)
    if not isinstance(trigger, str) or not trigger.startswith("KEY_"):
        raise ConfigError(f"trigger must be a key name like KEY_CAPSLOCK, got {trigger!r}")
    cfg.trigger = trigger
    for key in ("tap_passthrough", "sounds", "notify", "auto_apps"):
        if key in data:
            if not isinstance(data[key], bool):
                raise ConfigError(f"{key} must be true or false")
            setattr(cfg, key, data[key])

    seen: dict[tuple[str, ...], str] = {}
    for i, raw in enumerate(data.get("code", []), start=1):
        where = f"code #{i}"
        if not isinstance(raw, dict):
            raise ConfigError(f"{where}: must be a [[code]] table")
        name = raw.get("name") or where
        try:
            seq = codes.parse(raw.get("seq", ""))
        except codes.CodeError as e:
            raise ConfigError(f"{name}: {e}") from e
        if seq in seen:
            raise ConfigError(
                f"{name}: code {codes.show(seq)} is already used by {seen[seq]}")
        apps = raw.get("apps", [])
        if isinstance(apps, str):
            apps = [apps]
        if not (apps or raw.get("run") or raw.get("url")):
            raise ConfigError(f"{name}: needs apps, run or url")
        cooldown = raw.get("cooldown", 1.0)
        if not isinstance(cooldown, (int, float)) or cooldown < 0:
            raise ConfigError(f"{name}: cooldown must be a number of seconds")
        seen[seq] = name
        cfg.entries.append(Entry(
            name=name, code=seq, apps=list(apps),
            run=raw.get("run"), url=raw.get("url"), cooldown=float(cooldown),
        ))
    return cfg
