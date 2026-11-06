"""The official registry: hand-picked short codes for popular apps.

The registry is stored as a signed hash chain (registry/chain.toml, see
chain.py). This module turns the chain into a Registry, keeps the newest
trustworthy copy, and fetches updates.

Trust rules for updates:
  * the genesis block must match the one shipped with null-short
  * every later block must be signed by a maintainer listed in the chain
  * the new chain must extend the one we already have (no rewrites, no forks)
"""

from __future__ import annotations

import hashlib
import os
import tomllib
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import chain, codes

REPO = "Null0Grid/null-short"
RAW_URL = f"https://raw.githubusercontent.com/{REPO}/main/registry/chain.toml"
ISSUE_URL = f"https://github.com/{REPO}/issues/new"

BUNDLED = Path(__file__).resolve().parent.parent / "registry" / "chain.toml"
MIN_LEN, MAX_LEN = 3, 4


def user_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(base) / "null-short" / "chain.toml"


class RegistryError(ValueError):
    pass


@dataclass
class RegEntry:
    name: str
    code: tuple[str, ...]
    ids: list[str] = field(default_factory=list)
    url: str | None = None


@dataclass
class Registry:
    version: int = 0                      # index of the head block
    entries: list[RegEntry] = field(default_factory=list)   # active apps
    reserved: set = field(default_factory=set)              # every code ever used
    maintainers: list[dict] = field(default_factory=list)
    hashes: list[str] = field(default_factory=list)
    source: Path | None = None
    text: str = ""

    def by_id(self) -> dict[str, RegEntry]:
        return {i: e for e in self.entries for i in e.ids}

    def codes(self) -> set[tuple[str, ...]]:
        return set(self.reserved) | {e.code for e in self.entries}

    @property
    def head(self) -> str:
        return self.hashes[-1] if self.hashes else ""


def from_state(state: chain.State, source: Path | None = None, text: str = "") -> Registry:
    entries = [RegEntry(r.name, r.code, list(r.ids), r.url) for r in state.active.values()]
    return Registry(version=state.index, entries=entries, reserved=set(state.reserved),
                    maintainers=list(state.maintainers), hashes=list(state.hashes),
                    source=source, text=text)


def from_chain_text(text: str, source: Path | None = None,
                    genesis_hash: str | None = None) -> Registry:
    try:
        state = chain.replay(chain.parse(text), genesis_hash)
    except chain.ChainError as e:
        raise RegistryError(f"{source or 'chain'}: {e}") from e
    return from_state(state, source, text)


def load_file(path: Path, genesis_hash: str | None = None) -> Registry:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise RegistryError(f"{path}: {e}") from e
    return from_chain_text(text, path, genesis_hash)


def _extends(new: Registry, old: Registry) -> bool:
    """True if `new` contains all of `old`'s blocks unchanged."""
    return len(new.hashes) >= len(old.hashes) and new.hashes[:len(old.hashes)] == old.hashes


def load(bundled: Path = BUNDLED, user: Path | None = None) -> Registry:
    """The newest trustworthy registry: the bundled chain, or a longer chain
    fetched by update() that extends it."""
    best = load_file(bundled) if bundled.is_file() else Registry()
    up = user or user_path()
    if up.is_file() and best.hashes:
        try:
            fetched = load_file(up, genesis_hash=best.hashes[0])
            if len(fetched.hashes) > len(best.hashes) and _extends(fetched, best):
                best = fetched
        except RegistryError:
            pass        # a damaged or forged download is ignored
    return best


def update(url: str = RAW_URL, current: Registry | None = None,
           dest: Path | None = None, fetch=None) -> tuple[bool, Registry]:
    """Download the latest chain and keep it only if it verifies and extends
    ours. Returns (changed, registry)."""
    current = current or load()
    if fetch is None:
        def fetch(u):
            with urllib.request.urlopen(u, timeout=15) as r:
                return r.read().decode("utf-8")
    out = dest or user_path()
    text = fetch(url)
    new = from_chain_text(text, out, genesis_hash=current.hashes[0] if current.hashes else None)
    if not _extends(new, current):
        if _extends(current, new):
            return False, current           # remote is behind us
        raise RegistryError("the downloaded chain rewrites history (fork); rejected")
    if len(new.hashes) == len(current.hashes):
        return False, current
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(out)
    return True, new


# -------------------------------------------- flat entry lists (snippets)
def parse(text: str, source: Path | None = None) -> Registry:
    """Parse a flat list of [[app]] entries (the format `submit` produces).
    Applies the same rules as chain blocks. Used for snippets and tests."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise RegistryError(f"{source or 'registry'}: {e}") from e
    version = data.get("version", 0)
    if not isinstance(version, int):
        raise RegistryError("version must be an integer")
    state = chain.State()
    block = chain.Block(index=0, prev=chain.GENESIS_PREV, time="", maintainers=[],
                        add=data.get("app", []))
    try:
        chain.apply(state, block)
    except (chain.ChainError, codes.CodeError) as e:
        raise RegistryError(str(e).removeprefix("block 0: ")) from e
    reg = from_state(state, source)
    reg.version = version
    return reg


def suggest_code(app_id: str, family: str, reg: Registry,
                 extra_taken: set | None = None) -> tuple[str, ...] | None:
    """Pick a free registry code for an app: 3 arrows if any are free, else 4.
    Retired codes count as taken. Candidates are tried in an order seeded by
    the app ID, so suggestions are stable but spread out."""
    taken = reg.codes() | (extra_taken or set())
    seed = hashlib.sha256(app_id.encode()).digest()
    for length in range(MIN_LEN, MAX_LEN + 1):
        free_n = length - 2
        cands = []
        for n in range(4 ** free_n):
            tail = tuple("URDL"[(n >> (2 * i)) & 3] for i in range(free_n))
            cands.append(("U", family, *tail))
        cands.sort(key=lambda c: hashlib.sha256(seed + "".join(c).encode()).digest())
        for c in cands:
            if c not in taken:
                return c
    return None


def entry_toml(name: str, code: tuple[str, ...], ids: list[str]) -> str:
    id_list = ", ".join(f'"{i}"' for i in ids)
    return f'[[app]]\nname = "{name}"\nseq  = "{codes.show(code)}"\nids  = [{id_list}]\n'


def issue_url(name: str, app_id: str, categories: list[str], snippet: str) -> str:
    body = (
        f"### Registry request: {name}\n\n"
        f"- Desktop ID: `{app_id}`\n"
        f"- Categories: `{';'.join(categories) or 'none'}`\n\n"
        "Proposed entry (a maintainer adds it with "
        "`null-short registry add --from <file>`):\n\n"
        f"```toml\n{snippet}```\n\n"
        "_Sent with `null-short submit`._\n"
    )
    query = urllib.parse.urlencode({
        "title": f"Registry: add {name}",
        "labels": "registry",
        "body": body,
    })
    return f"{ISSUE_URL}?{query}"
