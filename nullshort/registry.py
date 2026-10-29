"""The official registry: hand-picked short codes for popular apps.

Rules (checked by tests and CI):
  * every code starts with ↑ and is 3 or 4 arrows long
    (auto codes are 5, so the two can never collide)
  * the second arrow is the app's family
  * no two entries share a code or a desktop ID
"""

from __future__ import annotations

import hashlib
import os
import tomllib
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import codes

REPO = "Null0Grid/null-short"
RAW_URL = f"https://raw.githubusercontent.com/{REPO}/main/registry/registry.toml"
ISSUE_URL = f"https://github.com/{REPO}/issues/new"

BUNDLED = Path(__file__).resolve().parent.parent / "registry" / "registry.toml"
MIN_LEN, MAX_LEN = 3, 4


def user_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(base) / "null-short" / "registry.toml"


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
    version: int = 0
    entries: list[RegEntry] = field(default_factory=list)
    source: Path | None = None

    def by_id(self) -> dict[str, RegEntry]:
        return {i: e for e in self.entries for i in e.ids}

    def codes(self) -> set[tuple[str, ...]]:
        return {e.code for e in self.entries}


def parse(text: str, source: Path | None = None) -> Registry:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise RegistryError(f"{source or 'registry'}: {e}") from e
    version = data.get("version", 0)
    if not isinstance(version, int):
        raise RegistryError("version must be an integer")
    reg = Registry(version=version, source=source)
    seen_codes: dict[tuple[str, ...], str] = {}
    seen_ids: dict[str, str] = {}
    for raw in data.get("app", []):
        name = raw.get("name") or "?"
        try:
            code = codes.parse(raw.get("seq", ""))
        except codes.CodeError as e:
            raise RegistryError(f"{name}: {e}") from e
        if not MIN_LEN <= len(code) <= MAX_LEN or code[0] != "U":
            raise RegistryError(f"{name}: registry codes are ↑ + {MIN_LEN - 1}–{MAX_LEN - 1} arrows")
        if code in seen_codes:
            raise RegistryError(f"{name}: {codes.show(code)} already used by {seen_codes[code]}")
        ids = raw.get("ids", [])
        if isinstance(ids, str):
            ids = [ids]
        if not ids:
            raise RegistryError(f"{name}: needs at least one desktop ID")
        for i in ids:
            if i in seen_ids:
                raise RegistryError(f"{name}: ID {i} already belongs to {seen_ids[i]}")
            seen_ids[i] = name
        seen_codes[code] = name
        reg.entries.append(RegEntry(name, code, list(ids), raw.get("url")))
    return reg


def load_file(path: Path) -> Registry:
    try:
        return parse(path.read_text(encoding="utf-8"), source=path)
    except OSError as e:
        raise RegistryError(f"{path}: {e}") from e


def load(bundled: Path = BUNDLED, user: Path | None = None) -> Registry:
    """The newest valid registry: bundled copy or one fetched by update()."""
    best = load_file(bundled) if bundled.is_file() else Registry()
    up = user or user_path()
    if up.is_file():
        try:
            fetched = load_file(up)
            if fetched.version > best.version:
                best = fetched
        except RegistryError:
            pass
    return best


def update(url: str = RAW_URL, current: Registry | None = None,
           dest: Path | None = None, fetch=None) -> tuple[bool, Registry]:
    """Download the latest registry. Returns (changed, registry)."""
    current = current or load()
    if fetch is None:
        def fetch(u):
            with urllib.request.urlopen(u, timeout=15) as r:
                return r.read().decode("utf-8")
    out = dest or user_path()
    text = fetch(url)
    new = parse(text, source=out)          # validate before saving anything
    if new.version <= current.version:
        return False, current
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(out)
    return True, new


def suggest_code(app_id: str, family: str, reg: Registry,
                 extra_taken: set | None = None) -> tuple[str, ...] | None:
    """Pick a free registry code for an app: 3 arrows if any are free, else 4.
    Candidates are tried in an order seeded by the app ID, so suggestions are
    stable but spread out."""
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
        "Proposed entry for `registry/registry.toml`:\n\n"
        f"```toml\n{snippet}```\n\n"
        "_Sent with `null-short submit`._\n"
    )
    query = urllib.parse.urlencode({
        "title": f"Registry: add {name}",
        "labels": "registry",
        "body": body,
    })
    return f"{ISSUE_URL}?{query}"
