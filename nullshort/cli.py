"""Command line for Null//Short."""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import urllib.error

from . import __version__, apps as apps_mod, autocode, codes, launcher, registry
from .catalog import assemble
from .config import ConfigError, load

SOURCE_ORDER = {"config": 0, "registry": 1, "auto": 2, "auto (old)": 3}
ARROW_ORDER = {d: i for i, d in enumerate("URDL")}


def _sort_key(e):
    return (SOURCE_ORDER.get(e.source, 9), len(e.code), [ARROW_ORDER[d] for d in e.code])


def _cfg(args):
    try:
        return load(args.config)
    except ConfigError as e:
        sys.exit(f"config error: {e}")


def _catalog(args, save=True):
    cfg = _cfg(args)
    try:
        return cfg, assemble(cfg, save=save)
    except registry.RegistryError as e:
        sys.exit(f"registry error: {e}")


def _find_app(query: str) -> apps_mod.App:
    found = apps_mod.scan()
    if query in found:
        return found[query]
    q = query.lower()
    matches = [a for a in found.values() if a.name.lower() == q] or \
              [a for a in found.values() if q in a.name.lower() or q in a.id.lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        sys.exit(f"no installed app matches {query!r}  (see: null-short list)")
    names = "\n".join(f"  {a.id:<40} {a.name}" for a in matches[:15])
    sys.exit(f"{query!r} matches several apps, use the ID:\n{names}")


# ------------------------------------------------------------------ commands
def cmd_run(args) -> None:
    cfg = _cfg(args)
    from . import daemon  # needs python3-evdev
    daemon.run(cfg, args.config)


def cmd_list(args) -> None:
    cfg, cat = _catalog(args)
    print(f"Null//Short {__version__}  ·  trigger {cfg.trigger}  ·  {len(cat.entries)} codes\n")
    entries = sorted(cat.entries, key=_sort_key)
    if args.source:
        entries = [e for e in entries if e.source.startswith(args.source)]
    width = min(max((len(e.name) for e in entries), default=4), 32)
    for e in entries:
        found = launcher.resolve(e.apps) if e.apps else None
        target = found[0] if found else (e.run and f"runs {e.run}") or \
            (e.url and f"opens {e.url}") or "not installed"
        print(f"  {codes.show(e.code):<14} {e.name[:32]:<{width}}  {e.source:<10} {target}")
    for w in cat.warnings:
        print(f"\n  ! {w}", end="")
    if cat.warnings:
        print()


def cmd_check(args) -> None:
    cfg = _cfg(args)
    print(f"config    OK  {len(cfg.entries)} codes  ({cfg.source})")
    try:
        reg = registry.load()
    except registry.RegistryError as e:
        sys.exit(f"registry  ERROR  {e}")
    print(f"registry  OK  v{reg.version}, {len(reg.entries)} apps  ({reg.source})")
    cat = assemble(cfg, save=False)
    print(f"catalog   OK  {len(cat.entries)} codes, {len(cat.warnings)} warnings")
    for w in cat.warnings:
        print(f"  ! {w}")


def cmd_try(args) -> None:
    try:
        seq = codes.parse(args.code)
    except codes.CodeError as e:
        sys.exit(str(e))
    _, cat = _catalog(args)
    entry = cat.table().get(seq)
    if not entry:
        sys.exit(f"no code {codes.show(seq)}")
    print(f"{codes.show(seq)} → {entry.name}  ({entry.source})")
    if args.launch:
        try:
            print("launched", launcher.launch(entry))
        except LookupError as e:
            sys.exit(str(e))


def cmd_classify(args) -> None:
    app = _find_app(args.app)
    reg = registry.load()
    fam = app.family
    print(f"{app.name}  ({app.id})")
    print(f"  categories  {';'.join(app.categories) or 'none'}")
    print(f"  family      {codes.GLYPHS[fam]}  {apps_mod.FAMILY_NAMES[fam]}")
    r = reg.by_id().get(app.id)
    if r:
        print(f"  registry    {codes.show(r.code)}  (listed as {r.name})")
    pinned = autocode.load_pins().get(app.id)
    if pinned:
        print(f"  auto code   {codes.show(pinned)}  (pinned)")
    elif not r:
        print(f"  auto code   {codes.show(autocode.hash_code(app.id, fam))}  (if free)")


def cmd_submit(args) -> None:
    app = _find_app(args.app)
    reg = registry.load()
    listed = reg.by_id().get(app.id)
    if listed:
        sys.exit(f"{app.name} is already in the registry as {codes.show(listed.code)} ({listed.name})")
    if args.code:
        try:
            code = codes.parse(args.code)
        except codes.CodeError as e:
            sys.exit(str(e))
        if not registry.MIN_LEN <= len(code) <= registry.MAX_LEN or code[0] != "U":
            sys.exit("registry codes are ↑ + 2–3 arrows")
        if code in reg.codes():
            sys.exit(f"{codes.show(code)} is already taken in the registry")
        if code[1] != app.family:
            print(f"note: {app.name} classifies as {codes.GLYPHS[app.family]} "
                  f"({apps_mod.FAMILY_NAMES[app.family]}), not {codes.GLYPHS[code[1]]}")
    else:
        code = registry.suggest_code(app.id, app.family, reg)
        if code is None:
            sys.exit(f"the {apps_mod.FAMILY_NAMES[app.family]} family is full; pass --code")
    name = args.name or app.name
    snippet = registry.entry_toml(name, code, [app.id])
    url = registry.issue_url(name, app.id, app.categories, snippet)
    print(f"Proposed registry entry ({apps_mod.FAMILY_NAMES[app.family]}):\n")
    print(snippet)
    if args.print_only or not shutil.which("xdg-open"):
        print(f"Open this to send the request:\n{url}")
        return
    subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    print("Opened a pre-filled GitHub issue in your browser. Review it and hit Submit.")


def cmd_update_registry(args) -> None:
    try:
        changed, reg = registry.update(args.url or registry.RAW_URL)
    except urllib.error.HTTPError as e:
        hint = " (is the repo private?)" if e.code == 404 else ""
        sys.exit(f"download failed: HTTP {e.code}{hint}")
    except (urllib.error.URLError, OSError) as e:
        sys.exit(f"download failed: {e}")
    except registry.RegistryError as e:
        sys.exit(f"downloaded registry is invalid, kept the current one: {e}")
    if changed:
        print(f"Registry updated to v{reg.version} ({len(reg.entries)} apps). "
              "The daemon picks it up automatically.")
    else:
        print(f"Already up to date (v{reg.version}).")


def cmd_devices(args) -> None:
    from . import daemon
    kbs = daemon.list_keyboards()
    if not kbs:
        print("No keyboards visible. Are you in the 'input' group? (log out/in after install)")
    for dev in kbs:
        print(f"  {dev.path:<20} {dev.name}")


# ---------------------------------------------------------------------- main
def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="null-short", description="Null//Short: arrow-code launcher")
    p.add_argument("--version", action="version", version=f"Null//Short {__version__}")
    p.add_argument("-c", "--config", help="path to config.toml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")

    sub.add_parser("run", help="start the daemon").set_defaults(fn=cmd_run)
    s = sub.add_parser("list", help="show every code and where it came from")
    s.add_argument("--source", choices=["config", "registry", "auto"])
    s.set_defaults(fn=cmd_list)
    sub.add_parser("check", help="validate config, registry and codes").set_defaults(fn=cmd_check)
    s = sub.add_parser("try", help="look up a code, e.g. null-short try '↑ → ↑'")
    s.add_argument("code")
    s.add_argument("--launch", action="store_true", help="also launch it")
    s.set_defaults(fn=cmd_try)
    s = sub.add_parser("classify", help="show an app's family and code")
    s.add_argument("app", help="desktop ID or app name")
    s.set_defaults(fn=cmd_classify)
    s = sub.add_parser("submit", help="propose an app for the official registry")
    s.add_argument("app", help="desktop ID or app name")
    s.add_argument("--code", help="the code you want (default: a free one is suggested)")
    s.add_argument("--name", help="display name (default: from the .desktop file)")
    s.add_argument("--print", dest="print_only", action="store_true",
                   help="print the request link instead of opening the browser")
    s.set_defaults(fn=cmd_submit)
    s = sub.add_parser("update-registry", help="download the latest official registry")
    s.add_argument("--url", help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_update_registry)
    sub.add_parser("devices", help="list keyboards the daemon would take").set_defaults(fn=cmd_devices)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    args.fn(args)
