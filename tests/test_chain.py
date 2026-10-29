"""The signed registry chain: signatures, links, rules, updates and attacks.

Signing tests use real ssh-keygen (as maintainers do) and are skipped if it
isn't installed. Verification is pure Python.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from nullshort import chain, ed25519, maintainer, registry, sshsig

HAVE_SSH = shutil.which("ssh-keygen") is not None


def keygen(directory: Path, name: str) -> Path:
    key = directory / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key), "-C", name],
                   check=True)
    return key


def genesis_text(maintainers=None):
    g = chain.Block(index=0, prev=chain.GENESIS_PREV, time="2026-01-01T00:00:00Z",
                    maintainers=maintainers or [],
                    add=[{"name": "Firefox", "seq": "↑ → ↑", "ids": ["firefox-esr"]},
                         {"name": "Steam", "seq": "↑ ↓ →", "ids": ["steam"]}])
    return chain.dump([g])


class Ed25519Test(unittest.TestCase):
    # RFC 8032 section 7.1, test 2
    PUB = bytes.fromhex("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c")
    MSG = bytes.fromhex("72")
    SIG = bytes.fromhex(
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
        "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00")

    def test_rfc_vector(self):
        self.assertTrue(ed25519.verify(self.PUB, self.MSG, self.SIG))
        self.assertFalse(ed25519.verify(self.PUB, b"\x73", self.SIG))
        bad = bytearray(self.SIG)
        bad[5] ^= 1
        self.assertFalse(ed25519.verify(self.PUB, self.MSG, bytes(bad)))
        self.assertFalse(ed25519.verify(self.PUB[:31], self.MSG, self.SIG))


class UnsignedChainTest(unittest.TestCase):
    def test_bundled_chain(self):
        reg = registry.load_file(registry.BUNDLED)
        self.assertEqual(len(reg.entries), 40)
        self.assertEqual(reg.version, 0)

    def test_canonical_ignores_formatting(self):
        a = chain.Block(0, chain.GENESIS_PREV, "t", maintainers=[],
                        add=[{"name": "A", "seq": "↑ → ↑", "ids": ["a"]}])
        b = chain.Block(0, chain.GENESIS_PREV, "t", maintainers=[],
                        add=[{"name": "A", "seq": "up right up", "ids": ["a"]}])
        self.assertEqual(a.hash(), b.hash())
        b.add[0]["name"] = "B"
        self.assertNotEqual(a.hash(), b.hash())

    def test_dump_parse_roundtrip(self):
        blocks = chain.parse(genesis_text())
        again = chain.parse(chain.dump(blocks))
        self.assertEqual(blocks[0].hash(), again[0].hash())

    def test_unclaimed_rejects_new_blocks(self):
        blocks = chain.parse(genesis_text())
        state = chain.replay(blocks)
        b = chain.Block(1, state.head, "t", signer="x", sig="",
                        add=[{"name": "B", "seq": "↑ ← ↑", "ids": ["b"]}])
        with self.assertRaisesRegex(chain.ChainError, "no maintainers"):
            chain.replay(blocks + [b])

    def test_genesis_rules(self):
        g = chain.parse(genesis_text())[0]
        g.prev = "1" * 64
        with self.assertRaisesRegex(chain.ChainError, "broken link"):
            chain.replay([g])
        g = chain.parse(genesis_text())[0]
        g.maintainers = None
        with self.assertRaisesRegex(chain.ChainError, "maintainers"):
            chain.replay([g])

    def test_genesis_anchor(self):
        text = genesis_text()
        real = chain.replay(chain.parse(text)).head
        fake = text.replace('"Steam"', '"Steem"')
        with self.assertRaisesRegex(registry.RegistryError, "different genesis"):
            registry.from_chain_text(fake, genesis_hash=real)

    def test_garbage(self):
        for text in ("", "not toml [", "[[block]]\nindex = 0"):
            with self.assertRaises((chain.ChainError, registry.RegistryError)):
                registry.from_chain_text(text)


@unittest.skipUnless(HAVE_SSH, "ssh-keygen not installed")
class SignedChainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.me = keygen(self.dir, "me")
        self.friend = keygen(self.dir, "friend")
        self.evil = keygen(self.dir, "evil")
        self.path = self.dir / "chain.toml"
        self.path.write_text(genesis_text())
        maintainer.claim(self.path, "me", str(self.me) + ".pub")

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, name, seq, ids, signer="me", key=None, **kw):
        return maintainer.append(self.path, signer, str(key or self.me),
                                 add=[{"name": name, "seq": seq, "ids": ids}], **kw)

    def reg(self, path=None):
        return registry.load_file(path or self.path)

    def test_sshsig_matches_ssh_keygen(self):
        msg = self.dir / "m"
        msg.write_bytes(b"hello")
        subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(self.me), "-n", chain.NAMESPACE,
                        str(msg)], check=True, capture_output=True)
        sig = (self.dir / "m.sig").read_text()
        pub = (self.dir / "me.pub").read_text()
        self.assertTrue(sshsig.verify(sig, b"hello", chain.NAMESPACE, pub))
        self.assertFalse(sshsig.verify(sig, b"hellO", chain.NAMESPACE, pub))
        self.assertFalse(sshsig.verify(sig, b"hello", "other-namespace", pub))
        other = (self.dir / "friend.pub").read_text()
        self.assertFalse(sshsig.verify(sig, b"hello", chain.NAMESPACE, other))

    def test_claim_only_once(self):
        with self.assertRaisesRegex(chain.ChainError, "already claimed"):
            maintainer.claim(self.path, "evil", str(self.evil) + ".pub")

    def test_add_extend_retire(self):
        self.add("Null File", "↑ ↑ ↓ ←", ["null-file"])
        maintainer.append(self.path, "me", str(self.me),
                          extend=[{"seq": "↑ ↑ ↓ ←", "ids": ["dev.nullgrid.NullFile"]}])
        maintainer.append(self.path, "me", str(self.me), retire=[{"seq": "↑ ↓ →"}])
        reg = self.reg()
        self.assertEqual(reg.version, 3)
        by_id = reg.by_id()
        self.assertEqual(by_id["dev.nullgrid.NullFile"].name, "Null File")
        self.assertNotIn("steam", by_id)                       # retired
        self.assertIn(("U", "D", "R"), reg.codes())            # but still reserved
        self.assertNotEqual(registry.suggest_code("x", "D", reg), ("U", "D", "R"))

    def test_rules_still_apply_to_signed_blocks(self):
        with self.assertRaisesRegex(chain.ChainError, "already used"):
            self.add("Copycat", "↑ → ↑", ["copycat"])            # code taken
        with self.assertRaisesRegex(chain.ChainError, "already used"):
            self.add("Dupe", "↑ ← ↓", ["steam"])                 # ID taken
        maintainer.append(self.path, "me", str(self.me), retire=[{"seq": "↑ ↓ →"}])
        with self.assertRaisesRegex(chain.ChainError, "already used"):
            self.add("Reuse", "↑ ↓ →", ["reuse"])                # retired code
        self.assertEqual(self.reg().version, 1)                  # nothing bad was written

    def test_wrong_key_or_signer_rejected(self):
        with self.assertRaisesRegex(chain.ChainError, "bad signature"):
            self.add("Evil", "↑ ← ↓", ["evil"], key=self.evil)
        with self.assertRaisesRegex(chain.ChainError, "not a maintainer"):
            self.add("Evil", "↑ ← ↓", ["evil"], signer="evil", key=self.evil)
        self.assertEqual(self.reg().version, 0)

    def test_maintainers_and_rotation(self):
        new = maintainer.maintainer_list(chain.replay(chain.parse(self.path.read_text())),
                                         [("friend", str(self.friend) + ".pub")], None)
        maintainer.append(self.path, "me", str(self.me), maintainers=new)
        self.add("Null Scribe", "↑ ← ↓ ↓", ["net.nullgrid.NullScribe"],
                 signer="friend", key=self.friend)
        state = chain.replay(chain.parse(self.path.read_text()))
        gone = maintainer.maintainer_list(state, None, ["me"])
        maintainer.append(self.path, "friend", str(self.friend), maintainers=gone)
        with self.assertRaisesRegex(chain.ChainError, "not a maintainer"):
            self.add("Late", "↑ ← ↓ →", ["late"])
        with self.assertRaisesRegex(chain.ChainError, "every maintainer"):
            maintainer.append(self.path, "friend", str(self.friend), maintainers=[])

    def test_tampering_is_detected(self):
        self.add("Null File", "↑ ↑ ↓ ←", ["null-file"])
        self.add("Null Scribe", "↑ ← ↓ ↓", ["net.nullgrid.NullScribe"])
        good = self.path.read_text()
        attacks = {
            "change an app in a signed block": good.replace('"null-file"', '"evil-file"'),
            "change genesis": good.replace('"firefox-esr"', '"evil-browser"', 1),
            "drop a middle block": self._drop_block(good, 1),
            "swap signatures": self._swap_sigs(good),
        }
        for what, text in attacks.items():
            with self.subTest(what), self.assertRaises(registry.RegistryError):
                registry.from_chain_text(text)

    @staticmethod
    def _drop_block(text, n):
        parts = text.split("[[block]]")
        del parts[n + 1]
        return "[[block]]".join(parts)

    @staticmethod
    def _swap_sigs(text):
        blocks = chain.parse(text)
        blocks[1].sig, blocks[2].sig = blocks[2].sig, blocks[1].sig
        return chain.dump(blocks)

    def test_client_update_rules(self):
        bundled = self.dir / "bundled.toml"
        bundled.write_text(self.path.read_text())          # what ships with the app
        user = self.dir / "user" / "chain.toml"
        current = registry.load(bundled, user)
        self.assertEqual(current.version, 0)

        self.add("Null File", "↑ ↑ ↓ ←", ["null-file"])
        remote = self.path.read_text()
        changed, reg = registry.update("x", current, user, fetch=lambda u: remote)
        self.assertTrue(changed)
        self.assertEqual(reg.version, 1)
        self.assertEqual(registry.load(bundled, user).version, 1)     # persisted

        # Same chain again: nothing to do.
        changed, _ = registry.update("x", reg, user, fetch=lambda u: remote)
        self.assertFalse(changed)

        # A stale mirror that's behind us is ignored, not an error.
        changed, _ = registry.update("x", reg, user, fetch=lambda u: bundled.read_text())
        self.assertFalse(changed)

        # A fork (history rewritten with a valid signature) is rejected.
        forked = self.dir / "fork.toml"
        forked.write_text(bundled.read_text())
        maintainer.append(forked, "me", str(self.me),
                          add=[{"name": "Other", "seq": "↑ ← ↓", "ids": ["other"]}])
        with self.assertRaisesRegex(registry.RegistryError, "fork"):
            registry.update("x", reg, user, fetch=lambda u: forked.read_text())

        # Forged or broken downloads never replace the saved chain.
        with self.assertRaises(registry.RegistryError):
            registry.update("x", reg, user, fetch=lambda u: remote.replace("null-file", "x"))
        self.assertEqual(registry.load(bundled, user).version, 1)

        # A damaged saved file falls back to the bundled chain.
        user.write_text("garbage [")
        self.assertEqual(registry.load(bundled, user).version, 0)


if __name__ == "__main__":
    unittest.main()
