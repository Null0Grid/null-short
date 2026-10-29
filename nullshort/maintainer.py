"""Maintainer side of the registry chain: claim it, then add signed blocks."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from . import chain, sshsig


def _load(path: Path) -> tuple[list[chain.Block], chain.State]:
    try:
        blocks = chain.parse(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise chain.ChainError(f"{path}: {e}") from e
    return blocks, chain.replay(blocks)


def _write(path: Path, blocks: list[chain.Block]) -> None:
    chain.replay(blocks)                  # never write a chain that doesn't verify
    tmp = path.with_suffix(".tmp")
    tmp.write_text(chain.dump(blocks), encoding="utf-8")
    tmp.replace(path)


def read_pubkey(arg: str) -> str:
    """A public key given as a file path or as the 'ssh-ed25519 AAAA...' text."""
    p = Path(os.path.expanduser(arg))
    line = p.read_text().strip() if p.is_file() else arg.strip()
    sshsig.parse_public_key(line)         # raises SigError if unusable
    return " ".join(line.split()[:2])


def claim(path: Path, name: str, pubkey: str) -> chain.Block:
    """Set the first maintainer in the genesis block. Only possible while the
    chain is still just the unclaimed genesis block."""
    blocks, state = _load(path)
    if len(blocks) != 1 or state.maintainers:
        raise chain.ChainError("the registry is already claimed; add maintainers with "
                               "`null-short registry maintainer --add`")
    g = blocks[0]
    g.maintainers = [{"name": name, "key": read_pubkey(pubkey)}]
    _write(path, [g])
    return g


def append(path: Path, signer: str, key: str, *, add=None, extend=None, retire=None,
           maintainers=None, sign=chain.sign, time: str | None = None) -> chain.Block:
    """Build, sign, verify and append one block."""
    blocks, state = _load(path)
    if not state.maintainers:
        raise chain.ChainError("the registry isn't claimed yet; run `null-short registry claim`")
    b = chain.Block(index=len(blocks), prev=state.head, time=time or chain.now(),
                    signer=signer, maintainers=maintainers,
                    add=list(add or []), extend=list(extend or []), retire=list(retire or []))
    chain.apply(state, b, verify_sig=False)      # check the rules before signing
    b.sig = sign(b, key)
    blocks.append(b)
    _write(path, blocks)                          # full re-verify incl. signature
    return b


def entries_from_file(path: Path) -> list[dict]:
    """[[app]] entries from a file, e.g. the snippet in a submit issue."""
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise chain.ChainError(f"{path}: {e}") from e
    apps = data.get("app", [])
    if not apps:
        raise chain.ChainError(f"{path}: no [[app]] entries")
    return [{k: v for k, v in a.items() if k in ("name", "seq", "ids", "url")} for a in apps]


def maintainer_list(state: chain.State, add: list[tuple[str, str]] | None,
                    remove: list[str] | None) -> list[dict]:
    current = [dict(m) for m in state.maintainers]
    for name in remove or []:
        if not any(m["name"] == name for m in current):
            raise chain.ChainError(f"{name} is not a maintainer")
        current = [m for m in current if m["name"] != name]
    for name, key in add or []:
        current = [m for m in current if m["name"] != name]   # re-adding = key rotation
        current.append({"name": name, "key": read_pubkey(key)})
    return current
