"""The registry chain: a signed, append-only hash chain of registry changes.

Every block holds the hash of the block before it and a signature from a
maintainer, so history can't be rewritten and nobody without a maintainer key
can publish. Replaying the chain from the genesis block gives the registry.

Blocks may only:
  add      new apps (a code or desktop ID can never be reused, even if retired)
  extend   add desktop IDs to an existing app
  retire   take an app out of use (its code stays reserved forever)
  set maintainers   replace the maintainer key list
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import codes, sshsig

NAMESPACE = "null-short-registry"
GENESIS_PREV = "0" * 64
_DOMAIN = b"null-short-block-v1\n"


class ChainError(ValueError):
    pass


@dataclass
class Block:
    index: int
    prev: str
    time: str
    signer: str = ""
    sig: str = ""
    maintainers: list[dict] | None = None    # None = no change
    add: list[dict] = field(default_factory=list)
    extend: list[dict] = field(default_factory=list)
    retire: list[dict] = field(default_factory=list)

    def canonical(self) -> bytes:
        """The exact bytes that are hashed and signed. Formatting-independent."""
        body = {
            "index": self.index,
            "prev": self.prev,
            "time": self.time,
            "signer": self.signer,
            "add": [{"name": a["name"], "seq": _letters(a["seq"]), "ids": list(a["ids"]),
                     **({"url": a["url"]} if a.get("url") else {})} for a in self.add],
            "extend": [{"seq": _letters(e["seq"]), "ids": list(e["ids"])} for e in self.extend],
            "retire": [{"seq": _letters(r["seq"]), **({"reason": r["reason"]} if r.get("reason") else {})}
                       for r in self.retire],
        }
        if self.maintainers is not None:
            body["maintainers"] = [{"name": m["name"], "key": _bare_key(m["key"])}
                                   for m in self.maintainers]
        return _DOMAIN + json.dumps(body, sort_keys=True, separators=(",", ":")).encode()

    def hash(self) -> str:
        return hashlib.sha256(self.canonical()).hexdigest()


def _letters(seq) -> str:
    return "".join(codes.parse(seq) if isinstance(seq, str) else seq)


def _bare_key(line: str) -> str:
    parts = line.split()
    return " ".join(parts[:2])


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------- TOML in / out
def parse(text: str) -> list[Block]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ChainError(f"chain is not valid TOML: {e}") from e
    blocks = []
    for raw in data.get("block", []):
        try:
            blocks.append(Block(
                index=raw["index"], prev=raw["prev"], time=raw["time"],
                signer=raw.get("signer", ""), sig=raw.get("sig", "").strip(),
                maintainers=raw.get("maintainers"),
                add=raw.get("add", []), extend=raw.get("extend", []),
                retire=raw.get("retire", []),
            ))
        except (KeyError, TypeError) as e:
            raise ChainError(f"malformed block: missing {e}") from e
    if not blocks:
        raise ChainError("chain has no blocks")
    return blocks


def _q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


HEADER = """\
# Null//Short registry chain. DO NOT EDIT BY HAND.
#
# Each block is hashed, linked to the block before it and signed by a
# maintainer. Change it only with `null-short registry ...` commands;
# hand edits break the chain and every client will reject it.
"""


def dump(blocks: list[Block]) -> str:
    out = [HEADER]
    for b in blocks:
        out.append("[[block]]")
        out.append(f"index  = {b.index}")
        out.append(f"prev   = {_q(b.prev)}")
        out.append(f"hash   = {_q(b.hash())}   # informational; recomputed on load")
        out.append(f"time   = {_q(b.time)}")
        out.append(f"signer = {_q(b.signer)}")
        if b.maintainers is not None:
            items = ", ".join(f"{{ name = {_q(m['name'])}, key = {_q(_bare_key(m['key']))} }}"
                              for m in b.maintainers)
            out.append(f"maintainers = [{items}]")
        if b.sig:
            out.append(f"sig = '''\n{b.sig.strip()}\n'''")
        for a in b.add:
            out.append("\n[[block.add]]")
            out.append(f"name = {_q(a['name'])}")
            out.append(f"seq  = {_q(codes.show(codes.parse(a['seq']) if isinstance(a['seq'], str) else a['seq']))}")
            out.append(f"ids  = [{', '.join(_q(i) for i in a['ids'])}]")
            if a.get("url"):
                out.append(f"url  = {_q(a['url'])}")
        for e in b.extend:
            out.append("\n[[block.extend]]")
            out.append(f"seq = {_q(codes.show(codes.parse(e['seq'])))}")
            out.append(f"ids = [{', '.join(_q(i) for i in e['ids'])}]")
        for r in b.retire:
            out.append("\n[[block.retire]]")
            out.append(f"seq = {_q(codes.show(codes.parse(r['seq'])))}")
            if r.get("reason"):
                out.append(f"reason = {_q(r['reason'])}")
        out.append("")
    return "\n".join(out)


# ------------------------------------------------------------------ replay
@dataclass
class AppRecord:
    name: str
    code: tuple[str, ...]
    ids: list[str]
    url: str | None = None


@dataclass
class State:
    active: dict[tuple[str, ...], AppRecord] = field(default_factory=dict)
    reserved: set[tuple[str, ...]] = field(default_factory=set)
    ids: dict[str, tuple[str, ...]] = field(default_factory=dict)
    maintainers: list[dict] = field(default_factory=list)
    hashes: list[str] = field(default_factory=list)

    @property
    def index(self) -> int:
        return len(self.hashes) - 1

    @property
    def head(self) -> str:
        return self.hashes[-1]


def check_code(seq) -> tuple[str, ...]:
    """Registry codes: ↑ + 2–3 arrows (auto codes are 5+, so they never clash)."""
    code = codes.parse(seq) if isinstance(seq, str) else tuple(seq)
    if not 3 <= len(code) <= 4 or code[0] != "U":
        raise ChainError(f"{codes.show(code)}: registry codes are ↑ + 2–3 arrows")
    return code


def apply(state: State, b: Block, verify_sig: bool = True) -> None:
    """Validate one block against the state and apply it. Raises ChainError."""
    where = f"block {b.index}"
    if b.index != len(state.hashes):
        raise ChainError(f"{where}: expected index {len(state.hashes)}")
    expected_prev = state.head if state.hashes else GENESIS_PREV
    if b.prev != expected_prev:
        raise ChainError(f"{where}: broken link (prev hash doesn't match block {b.index - 1})")

    if b.index == 0:
        if b.maintainers is None:
            raise ChainError("genesis block must list maintainers (may be empty)")
        if b.sig or b.extend or b.retire:
            raise ChainError("genesis block only adds apps and sets maintainers")
    else:
        if not state.maintainers:
            raise ChainError(f"{where}: registry has no maintainers yet (run `null-short registry claim`)")
        signer = next((m for m in state.maintainers if m["name"] == b.signer), None)
        if signer is None:
            raise ChainError(f"{where}: signer {b.signer!r} is not a maintainer")
        if verify_sig and not sshsig.verify(b.sig, b.canonical(), NAMESPACE, signer["key"]):
            raise ChainError(f"{where}: bad signature from {b.signer!r}")
        if not (b.add or b.extend or b.retire or b.maintainers is not None):
            raise ChainError(f"{where}: empty block")

    if b.maintainers is not None:
        names = set()
        for m in b.maintainers:
            if not isinstance(m, dict) or not m.get("name") or not m.get("key"):
                raise ChainError(f"{where}: maintainers need a name and key")
            try:
                sshsig.parse_public_key(m["key"])
            except sshsig.SigError as e:
                raise ChainError(f"{where}: maintainer {m['name']}: {e}") from e
            if m["name"] in names:
                raise ChainError(f"{where}: duplicate maintainer {m['name']}")
            names.add(m["name"])
        if b.index > 0 and not b.maintainers:
            raise ChainError(f"{where}: can't remove every maintainer")

    for a in b.add:
        name = a.get("name") or "?"
        try:
            code = check_code(a.get("seq", ""))
        except codes.CodeError as e:
            raise ChainError(f"{where}: {name}: {e}") from e
        if code in state.reserved:
            raise ChainError(f"{where}: {name}: {codes.show(code)} was already used")
        ids = a.get("ids") or []
        if not ids or not all(isinstance(i, str) and i for i in ids):
            raise ChainError(f"{where}: {name}: needs at least one desktop ID")
        for i in ids:
            if i in state.ids or ids.count(i) > 1:
                raise ChainError(f"{where}: {name}: desktop ID {i} was already used")
        state.reserved.add(code)
        state.active[code] = AppRecord(name, code, list(ids), a.get("url"))
        for i in ids:
            state.ids[i] = code

    for e in b.extend:
        code = codes.parse(e.get("seq", ""))
        rec = state.active.get(code)
        if rec is None:
            raise ChainError(f"{where}: extend {codes.show(code)}: no active app with that code")
        for i in e.get("ids") or []:
            if i in state.ids:
                raise ChainError(f"{where}: desktop ID {i} was already used")
            state.ids[i] = code
            rec.ids.append(i)

    for r in b.retire:
        code = codes.parse(r.get("seq", ""))
        if code not in state.active:
            raise ChainError(f"{where}: retire {codes.show(code)}: no active app with that code")
        del state.active[code]       # code and IDs stay reserved

    if b.maintainers is not None:
        state.maintainers = [dict(m) for m in b.maintainers]
    state.hashes.append(b.hash())


def replay(blocks: list[Block], genesis_hash: str | None = None,
           verify_sig: bool = True) -> State:
    state = State()
    for b in blocks:
        apply(state, b, verify_sig)
        if b.index == 0 and genesis_hash and state.head != genesis_hash:
            raise ChainError("this chain has a different genesis block (not the official registry)")
    return state


# ----------------------------------------------------------------- signing
def sign(block: Block, key: str, ssh_keygen: str | None = None) -> str:
    """Sign a block with ssh-keygen. `key` is a private key file, or a .pub
    file whose private half is loaded in ssh-agent."""
    exe = ssh_keygen or shutil.which("ssh-keygen")
    if not exe:
        raise ChainError("ssh-keygen not found (sudo apt install openssh-client)")
    with tempfile.TemporaryDirectory() as tmp:
        msg = Path(tmp, "block")
        msg.write_bytes(block.canonical())
        res = subprocess.run([exe, "-Y", "sign", "-f", os.path.expanduser(key),
                              "-n", NAMESPACE, str(msg)], capture_output=True, text=True)
        sig_file = Path(tmp, "block.sig")
        if res.returncode != 0 or not sig_file.is_file():
            raise ChainError(f"ssh-keygen failed: {res.stderr.strip() or res.returncode}")
        return sig_file.read_text().strip()
