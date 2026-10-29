"""Builds the full code table: your config → the registry → auto codes."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import autocode, codes
from .apps import App
from .config import Config, Entry
from .registry import Registry

# Try 3 hashed arrows first (64 codes per family), then longer codes when a
# family is crowded (Steam adds a .desktop file for every game, for example).
HASH_LENGTHS = (3, 4, 5)
SALTS_PER_LENGTH = 64


@dataclass
class Catalog:
    entries: list[Entry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pins: dict[str, tuple[str, ...]] = field(default_factory=dict)
    pins_changed: bool = False

    def table(self) -> dict[tuple[str, ...], Entry]:
        return {e.code: e for e in self.entries}


def build(cfg: Config, apps: dict[str, App], reg: Registry,
          pins: dict[str, tuple[str, ...]]) -> Catalog:
    cat = Catalog(pins=dict(pins))
    taken: dict[tuple[str, ...], Entry] = {}
    covered: set[str] = set()   # app IDs that already have a code

    def add(entry: Entry) -> bool:
        other = taken.get(entry.code)
        if other is not None:
            cat.warnings.append(
                f"{entry.name} ({entry.source}) skipped: {codes.show(entry.code)} "
                f"belongs to {other.name} ({other.source})")
            return False
        taken[entry.code] = entry
        cat.entries.append(entry)
        return True

    # 1. Your config always wins.
    for e in cfg.entries:
        if add(e):
            covered.update(e.apps)

    # 2. Registry entries for installed apps (or ones with a web fallback).
    for r in reg.entries:
        installed = [i for i in r.ids if i in apps]
        if any(i in covered for i in r.ids):
            continue
        if not installed and not r.url:
            continue
        entry = Entry(name=r.name, code=r.code, apps=installed or list(r.ids),
                      url=r.url, source="registry")
        if add(entry):
            covered.update(r.ids)

    if not cfg.auto_apps:
        return cat

    # 3. Auto codes for everything else. Pinned apps first so they keep their
    #    codes; then new apps in ID order so results are deterministic.
    pending = [a for a in apps.values() if a.id not in covered]
    pending.sort(key=lambda a: (a.id not in cat.pins, a.id))
    for app in pending:
        pinned = cat.pins.get(app.id)
        if pinned and pinned not in taken:
            add(Entry(name=app.name, code=pinned, apps=[app.id], source="auto"))
            continue
        code = _free_code(app, taken)
        if code is None:
            cat.warnings.append(f"{app.name}: no free auto code in its family")
            continue
        if pinned:
            cat.warnings.append(f"{app.name}: pinned code {codes.show(pinned)} is taken, "
                                f"moved to {codes.show(code)}")
        add(Entry(name=app.name, code=code, apps=[app.id], source="auto"))
        cat.pins[app.id] = code
        cat.pins_changed = True

    # 4. Apps that moved into the registry keep their old auto code as an alias,
    #    so muscle memory still works.
    for app_id, code in cat.pins.items():
        if app_id in covered and app_id in apps and code not in taken:
            add(Entry(name=apps[app_id].name, code=code, apps=[app_id], source="auto (old)"))

    return cat


def _free_code(app: App, taken) -> tuple[str, ...] | None:
    for length in HASH_LENGTHS:
        for salt in range(SALTS_PER_LENGTH):
            code = autocode.hash_code(app.id, app.family, salt, length)
            if code not in taken:
                return code
    return None


def assemble(cfg: Config, save: bool = True) -> Catalog:
    """Scan installed apps, load registry and pins, build, and save new pins."""
    from . import apps as apps_mod, registry as registry_mod
    cat = build(cfg, apps_mod.scan(), registry_mod.load(), autocode.load_pins())
    if save and cat.pins_changed:
        try:
            autocode.save_pins(cat.pins)
            cat.pins_changed = False
        except OSError as e:
            cat.warnings.append(f"could not save pins: {e}")
    return cat
