"""Command line: null-short run | list | check | try | devices"""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__, codes, launcher
from .config import ConfigError, load


def _load(args):
    try:
        return load(args.config)
    except ConfigError as e:
        sys.exit(f"config error: {e}")


def cmd_run(args) -> None:
    cfg = _load(args)
    from . import daemon  # needs python3-evdev
    daemon.run(cfg)


def cmd_list(args) -> None:
    cfg = _load(args)
    print(f"Null//Short {__version__}  ·  config: {cfg.source}  ·  trigger: {cfg.trigger}\n")
    width = max(len(e.name) for e in cfg.entries)
    for e in cfg.entries:
        found = launcher.resolve(e.apps) if e.apps else None
        if found:
            status = f"✓ {found[0]}"
        elif e.run:
            status = f"→ runs {e.run}"
        elif e.url:
            status = f"→ opens {e.url} (no app installed)"
        else:
            status = "✗ not installed"
        print(f"  {codes.show(e.code):<12} {e.name:<{width}}  {status}")


def cmd_check(args) -> None:
    cfg = _load(args)
    print(f"OK: {len(cfg.entries)} codes in {cfg.source}, no duplicates")


def cmd_try(args) -> None:
    cfg = _load(args)
    try:
        seq = codes.parse(args.code)
    except codes.CodeError as e:
        sys.exit(str(e))
    entry = cfg.table().get(seq)
    if not entry:
        sys.exit(f"no code {codes.show(seq)}")
    print(f"{codes.show(seq)} → {entry.name}")
    if args.launch:
        try:
            print("launched", launcher.launch(entry))
        except LookupError as e:
            sys.exit(str(e))


def cmd_devices(args) -> None:
    from . import daemon
    kbs = daemon.list_keyboards()
    if not kbs:
        print("No keyboards visible. Are you in the 'input' group? (log out/in after install)")
    for dev in kbs:
        print(f"  {dev.path:<20} {dev.name}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="null-short", description="Null//Short: arrow-code launcher")
    p.add_argument("--version", action="version", version=f"Null//Short {__version__}")
    p.add_argument("-c", "--config", help="path to config.toml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="start the daemon").set_defaults(fn=cmd_run)
    sub.add_parser("list", help="show codes and which apps are installed").set_defaults(fn=cmd_list)
    sub.add_parser("check", help="validate the config").set_defaults(fn=cmd_check)
    t = sub.add_parser("try", help="look up a code, e.g. null-short try '↑ → ↑'")
    t.add_argument("code")
    t.add_argument("--launch", action="store_true", help="also launch it")
    t.set_defaults(fn=cmd_try)
    sub.add_parser("devices", help="list keyboards the daemon would take").set_defaults(fn=cmd_devices)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    args.fn(args)
