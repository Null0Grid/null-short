"""Verify OpenSSH signatures (`ssh-keygen -Y sign`) in pure Python.

Format: OpenSSH PROTOCOL.sshsig. Only ssh-ed25519 keys are supported.
"""

from __future__ import annotations

import base64
import hashlib
import struct

from . import ed25519

MAGIC = b"SSHSIG"
_HASHES = {"sha256": hashlib.sha256, "sha512": hashlib.sha512}


class SigError(ValueError):
    pass


def _read_string(buf: bytes, pos: int) -> tuple[bytes, int]:
    if pos + 4 > len(buf):
        raise SigError("truncated signature")
    (n,) = struct.unpack(">I", buf[pos:pos + 4])
    pos += 4
    if pos + n > len(buf):
        raise SigError("truncated signature")
    return buf[pos:pos + n], pos + n


def _string(b: bytes) -> bytes:
    return struct.pack(">I", len(b)) + b


def parse_public_key(line: str) -> bytes:
    """'ssh-ed25519 AAAA... comment' -> the wire-format key blob."""
    parts = line.split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise SigError("only ssh-ed25519 keys are supported")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except ValueError as e:
        raise SigError("bad public key") from e
    kind, pos = _read_string(blob, 0)
    key, pos = _read_string(blob, pos)
    if kind != b"ssh-ed25519" or len(key) != 32 or pos != len(blob):
        raise SigError("bad public key")
    return blob


def fingerprint(line: str) -> str:
    digest = hashlib.sha256(parse_public_key(line)).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def verify(armored: str, message: bytes, namespace: str, public_key_line: str) -> bool:
    """True if `armored` is a valid signature of `message` by that key."""
    try:
        want_blob = parse_public_key(public_key_line)
        lines = [ln.strip() for ln in armored.strip().splitlines()]
        if not lines or lines[0] != "-----BEGIN SSH SIGNATURE-----" \
                or lines[-1] != "-----END SSH SIGNATURE-----":
            return False
        blob = base64.b64decode("".join(lines[1:-1]), validate=True)
        if blob[:6] != MAGIC:
            return False
        (version,) = struct.unpack(">I", blob[6:10])
        if version != 1:
            return False
        pos = 10
        pub, pos = _read_string(blob, pos)
        ns, pos = _read_string(blob, pos)
        reserved, pos = _read_string(blob, pos)
        hash_alg, pos = _read_string(blob, pos)
        sig, pos = _read_string(blob, pos)
        if pub != want_blob or ns != namespace.encode():
            return False
        hfn = _HASHES.get(hash_alg.decode(errors="replace"))
        if hfn is None:
            return False
        sig_kind, spos = _read_string(sig, 0)
        raw_sig, spos = _read_string(sig, spos)
        if sig_kind != b"ssh-ed25519":
            return False
        raw_pub = _read_string(pub, _read_string(pub, 0)[1])[0]
        signed = (MAGIC + _string(ns) + _string(reserved) + _string(hash_alg)
                  + _string(hfn(message).digest()))
        return ed25519.verify(raw_pub, signed, raw_sig)
    except (SigError, ValueError, struct.error):
        return False
