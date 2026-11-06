"""Command line for null-short."""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import urllib.error

from pathlib import Path

from . import __version__, apps as apps_mod, autocode, chain, codes, launcher, maintainer, registry
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
    print(f"null-short {__version__}  ·  trigger {cfg.trigger}  ·  {len(cat.entries)} codes\n")
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
    owners = ", ".join(m["name"] for m in reg.maintainers) or "unclaimed"
    print(f"registry  OK  block {reg.version}, {len(reg.entries)} apps, "
          f"maintainers: {owners}, head {reg.head[:12]}  ({reg.source})")
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
        sys.exit(f"downloaded registry rejected, kept the current one: {e}")
    if changed:
        print(f"Registry verified and updated to block {reg.version} ({len(reg.entries)} apps). "
              "The daemon picks it up automatically.")
    else:
        print(f"Already up to date (block {reg.version}).")


# ------------------------------------------------- registry chain (maintainers)
def _chain_path(args) -> Path:
    if args.chain:
        return Path(args.chain)
    local = Path("registry/chain.toml")
    return local if local.is_file() else registry.BUNDLED


def _code(seq: str) -> str:
    try:
        return codes.show(codes.parse(seq))
    except codes.CodeError as e:
        sys.exit(str(e))


def _maint(fn):
    def run(args):
        try:
            fn(args)
        except (chain.ChainError, registry.RegistryError) as e:
            sys.exit(f"error: {e}")
        except FileNotFoundError as e:
            sys.exit(f"error: {e}")
        except ValueError as e:      # bad keys etc.
            sys.exit(f"error: {e}")
    return run


def _signed(b, path):
    print(f"block {b.index} signed by {b.signer} and appended to {path}")
    print(f"hash {b.hash()}")
    print("Commit and push registry/chain.toml to publish it.")


@_maint
def cmd_reg_verify(args) -> None:
    path = _chain_path(args)
    reg = registry.load_file(path)
    owners = ", ".join(f"{m['name']} ({chain.sshsig.fingerprint(m['key'])})"
                       for m in reg.maintainers) or "none (unclaimed)"
    print(f"OK  {path}")
    print(f"    {len(reg.hashes)} blocks, all links and signatures valid")
    print(f"    {len(reg.entries)} active apps, {len(reg.reserved) - len(reg.entries)} retired")
    print(f"    maintainers: {owners}")
    print(f"    genesis {reg.hashes[0]}")
    print(f"    head    {reg.head}")


@_maint
def cmd_reg_log(args) -> None:
    path = _chain_path(args)
    blocks = chain.parse(path.read_text(encoding="utf-8"))
    chain.replay(blocks)
    for b in blocks:
        who = b.signer or "genesis"
        parts = []
        if b.add:
            parts.append(f"+{len(b.add)} " + ", ".join(a["name"] for a in b.add[:4])
                         + (" ..." if len(b.add) > 4 else ""))
        if b.extend:
            parts.append(f"extend {len(b.extend)}")
        if b.retire:
            parts.append("retire " + ", ".join(_code(r["seq"]) for r in b.retire))
        if b.maintainers is not None:
            parts.append("maintainers: " + (", ".join(m["name"] for m in b.maintainers) or "none"))
        print(f"  #{b.index:<3} {b.hash()[:12]}  {b.time}  {who:<12} {'; '.join(parts)}")


@_maint
def cmd_reg_claim(args) -> None:
    path = _chain_path(args)
    g = maintainer.claim(path, args.name, args.pubkey)
    key = g.maintainers[0]["key"]
    print(f"Registry claimed by {args.name} ({chain.sshsig.fingerprint(key)}).")
    print(f"New genesis hash: {g.hash()}")
    print("Commit registry/chain.toml. From now on only blocks signed with this key are accepted.")


@_maint
def cmd_reg_add(args) -> None:
    path = _chain_path(args)
    if args.from_file:
        entries = maintainer.entries_from_file(args.from_file)
    else:
        if not (args.name and args.seq and args.ids):
            sys.exit("give --from FILE, or --name, --seq and --ids")
        entries = [{"name": args.name, "seq": args.seq, "ids": args.ids,
                    **({"url": args.url} if args.url else {})}]
    b = maintainer.append(path, args.signer, args.key, add=entries)
    for a in entries:
        print(f"  + {_code(a['seq']):<10} {a['name']}")
    _signed(b, path)


@_maint
def cmd_reg_extend(args) -> None:
    path = _chain_path(args)
    b = maintainer.append(path, args.signer, args.key, extend=[{"seq": args.seq, "ids": args.ids}])
    _signed(b, path)


@_maint
def cmd_reg_retire(args) -> None:
    path = _chain_path(args)
    entry = {"seq": args.seq, **({"reason": args.reason} if args.reason else {})}
    b = maintainer.append(path, args.signer, args.key, retire=[entry])
    print(f"  retired {_code(args.seq)} (the code stays reserved forever)")
    _signed(b, path)


@_maint
def cmd_reg_maintainer(args) -> None:
    path = _chain_path(args)
    _, state = maintainer._load(path)
    adds = []
    for item in args.add or []:
        name, sep, key = item.partition("=")
        if not sep:
            sys.exit("--add takes NAME=PUBKEY_FILE")
        adds.append((name, key))
    new = maintainer.maintainer_list(state, adds, args.remove)
    b = maintainer.append(path, args.signer, args.key, maintainers=new)
    print("  maintainers now: " + ", ".join(m["name"] for m in new))
    _signed(b, path)


def cmd_devices(args) -> None:
    from . import daemon
    kbs = daemon.list_keyboards()
    if not kbs:
        print("No keyboards visible. Are you in the 'input' group? (log out/in after install)")
    for dev in kbs:
        print(f"  {dev.path:<20} {dev.name}")


# ---------------------------------------------------------------------- main
def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="null-short", description="null-short: arrow-code launcher")
    p.add_argument("--version", action="version", version=f"null-short {__version__}")
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

    r = sub.add_parser("registry", help="maintainer tools for the signed registry chain")
    r.add_argument("--chain", help="chain file (default: ./registry/chain.toml)")
    rs = r.add_subparsers(dest="rcmd", required=True, metavar="action")
    rs.add_parser("verify", help="check every link and signature").set_defaults(fn=cmd_reg_verify)
    rs.add_parser("log", help="list the blocks").set_defaults(fn=cmd_reg_log)
    c = rs.add_parser("claim", help="become the first maintainer (one time)")
    c.add_argument("--name", required=True, help="your maintainer name, e.g. null0grid")
    c.add_argument("--pubkey", required=True, help="ed25519 public key file, e.g. ~/.ssh/id_ed25519.pub")
    c.set_defaults(fn=cmd_reg_claim)

    def signing(sp):
        sp.add_argument("--signer", required=True, help="your maintainer name")
        sp.add_argument("--key", required=True,
                        help="private key file (or its .pub if the key is in ssh-agent)")
        return sp

    a = signing(rs.add_parser("add", help="add apps in a new signed block"))
    a.add_argument("--from", dest="from_file", help="file with [[app]] entries (e.g. from a submit issue)")
    a.add_argument("--name")
    a.add_argument("--seq")
    a.add_argument("--ids", nargs="+")
    a.add_argument("--url")
    a.set_defaults(fn=cmd_reg_add)
    e = signing(rs.add_parser("extend", help="add desktop IDs to an existing app"))
    e.add_argument("--seq", required=True)
    e.add_argument("--ids", nargs="+", required=True)
    e.set_defaults(fn=cmd_reg_extend)
    t = signing(rs.add_parser("retire", help="take an app out of use (code stays reserved)"))
    t.add_argument("--seq", required=True)
    t.add_argument("--reason")
    t.set_defaults(fn=cmd_reg_retire)
    m = signing(rs.add_parser("maintainer", help="add, rotate or remove maintainer keys"))
    m.add_argument("--add", action="append", metavar="NAME=PUBKEY_FILE")
    m.add_argument("--remove", action="append", metavar="NAME")
    m.set_defaults(fn=cmd_reg_maintainer)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    args.fn(args)
