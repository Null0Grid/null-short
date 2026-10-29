import tempfile
import unittest
import urllib.parse
from pathlib import Path

from nullshort import apps, autocode, catalog, codes, config, registry
from nullshort.apps import App


def desktop(dirpath: Path, app_id: str, name: str, categories: str, extra: str = ""):
    d = dirpath / "applications"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{app_id}.desktop").write_text(
        f"[Desktop Entry]\nType=Application\nName={name}\nExec={app_id}\n"
        f"Categories={categories}\n{extra}")


def app(app_id, cats="", name=None):
    return App(app_id, name or app_id, Path(f"/x/{app_id}.desktop"),
               [c for c in cats.split(";") if c])


class ClassifyTest(unittest.TestCase):
    def test_families(self):
        cases = {
            "Network;WebBrowser;": "R",               # Firefox
            "Network;FileTransfer;P2P;Qt;": "R",      # qBittorrent
            "Network;Game;": "D",                     # Steam: games beat internet
            "Game;": "D",                             # Minecraft
            "Qt;KDE;System;TerminalEmulator;": "U",   # Konsole
            "TextEditor;Development;IDE;": "U",       # VS Code
            "AudioVideo;Player;": "L",                # VLC
            "Office;": "L",
            "": "L",                                  # no categories
        }
        for cats, fam in cases.items():
            self.assertEqual(apps.classify(cats.split(";")), fam, cats)

    def test_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            hi, lo = Path(tmp, "hi"), Path(tmp, "lo")
            desktop(hi, "steam", "Steam", "Game;")
            desktop(lo, "steam", "Old Steam", "Game;")
            desktop(lo, "hidden", "Hidden", "Utility;", "NoDisplay=true\n")
            desktop(hi, "masked", "Masked", "Utility;", "Hidden=true\n")
            desktop(lo, "masked", "Masked", "Utility;")
            desktop(lo, "org.kde.konsole", "Konsole", "System;TerminalEmulator;")
            (lo / "applications/kde4").mkdir()
            (lo / "applications/kde4/foo.desktop").write_text(
                "[Desktop Entry]\nName=Foo\nExec=foo\n")
            found = apps.scan([hi, lo])
            self.assertEqual(found["steam"].name, "Steam")
            self.assertNotIn("hidden", found)
            self.assertNotIn("masked", found)      # hidden copy shadows the lower one
            self.assertEqual(found["org.kde.konsole"].family, "U")
            self.assertIn("kde4-foo", found)


class AutoCodeTest(unittest.TestCase):
    def test_hash_is_stable_and_shaped(self):
        a = autocode.hash_code("org.example.App", "R")
        self.assertEqual(a, autocode.hash_code("org.example.App", "R"))
        self.assertEqual(len(a), 5)
        self.assertEqual(a[:2], ("U", "R"))
        self.assertNotEqual(a, autocode.hash_code("org.example.App", "R", salt=1))
        # spread: 200 IDs should land on many different codes
        spread = {autocode.hash_code(f"app{i}", "L") for i in range(200)}
        self.assertGreater(len(spread), 50)

    def test_pins_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, "state", "pins.toml")
            pins = {"org.a.App": ("U", "L", "U", "D", "R"), 'weird"id': ("U", "U", "L", "L", "L")}
            autocode.save_pins(pins, p)
            self.assertEqual(autocode.load_pins(p), pins)
            self.assertEqual(autocode.load_pins(Path(tmp, "missing.toml")), {})


class RegistryTest(unittest.TestCase):
    def test_bundled_registry_is_valid(self):
        reg = registry.load_file(registry.BUNDLED)
        self.assertGreaterEqual(len(reg.entries), 40)
        names = {e.name for e in reg.entries}
        for must in ("Firefox", "Microsoft Edge", "Steam", "Minecraft", "qBittorrent", "Claude"):
            self.assertIn(must, names)
        for e in reg.entries:
            self.assertIn(e.code[1], "URDL")
            self.assertLessEqual(len(e.code), registry.MAX_LEN)

    def test_registry_families_match_comments(self):
        # Spot-check that well-known apps sit in the family the classifier gives them.
        reg = registry.load_file(registry.BUNDLED).by_id()
        expect = {"firefox-esr": "R", "org.qbittorrent.qBittorrent": "R", "steam": "D",
                  "minecraft-launcher": "D", "org.kde.konsole": "U", "vlc": "L"}
        for app_id, fam in expect.items():
            self.assertEqual(reg[app_id].code[1], fam, app_id)

    def test_rules_enforced(self):
        bad = [
            '[[app]]\nname="A"\nseq="↑ →"\nids=["a"]',                    # too short
            '[[app]]\nname="A"\nseq="↑ → ↑ ↑ ↑"\nids=["a"]',              # too long
            '[[app]]\nname="A"\nseq="→ → ↑"\nids=["a"]',                  # must start ↑
            '[[app]]\nname="A"\nseq="↑ → ↑"\nids=[]',                     # no ids
            '[[app]]\nname="A"\nseq="↑ → ↑"\nids=["a"]\n'
            '[[app]]\nname="B"\nseq="↑ → ↑"\nids=["b"]',                  # dup code
            '[[app]]\nname="A"\nseq="↑ → ↑"\nids=["a"]\n'
            '[[app]]\nname="B"\nseq="↑ → →"\nids=["a"]',                  # dup id
            'version = "two"',
        ]
        for text in bad:
            with self.assertRaises(registry.RegistryError, msg=text):
                registry.parse(text)

    def test_suggest_code(self):
        reg = registry.load_file(registry.BUNDLED)
        code = registry.suggest_code("org.example.Newapp", "R", reg)
        self.assertEqual(code[:2], ("U", "R"))
        self.assertNotIn(code, reg.codes())
        self.assertEqual(code, registry.suggest_code("org.example.Newapp", "R", reg))
        self.assertEqual(len(code), 4)      # all 3-arrow internet codes are taken
        # family full
        full = registry.Registry(entries=[
            registry.RegEntry("x", c, [str(c)]) for c in
            [("U", "D", a) for a in "URDL"] +
            [("U", "D", a, b) for a in "URDL" for b in "URDL"]])
        self.assertIsNone(registry.suggest_code("z", "D", full))

    def test_issue_url(self):
        snippet = registry.entry_toml("Null File", ("U", "U", "L", "D"), ["dev.nullgrid.NullFile"])
        url = registry.issue_url("Null File", "dev.nullgrid.NullFile", ["System"], snippet)
        self.assertTrue(url.startswith(registry.ISSUE_URL + "?"))
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(q["title"], ["Registry: add Null File"])
        self.assertIn('seq  = "↑ ↑ ← ↓"', q["body"][0])
        self.assertIn("dev.nullgrid.NullFile", q["body"][0])
        registry.parse("version=1\n" + snippet)    # the snippet itself is valid


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.reg = registry.parse(
            'version=1\n'
            '[[app]]\nname="Firefox"\nseq="↑ → ↑"\nids=["firefox-esr","org.mozilla.firefox"]\n'
            '[[app]]\nname="Steam"\nseq="↑ ↓ →"\nids=["steam"]\n'
            '[[app]]\nname="Claude"\nseq="↑ ← ↑"\nids=["claude"]\nurl="https://claude.ai"\n')
        self.apps = {a.id: a for a in [
            app("org.mozilla.firefox", "Network;WebBrowser;", "Firefox"),
            app("org.example.Chat", "Network;Chat;", "Chat"),
            app("org.example.Editor", "Development;", "Editor"),
        ]}

    def build(self, cfg=None, pins=None, apps_=None):
        return catalog.build(cfg or config.Config(), apps_ or self.apps, self.reg, pins or {})

    def test_sources(self):
        cat = self.build()
        by_name = {e.name: e for e in cat.entries}
        self.assertEqual(by_name["Firefox"].source, "registry")
        self.assertEqual(by_name["Firefox"].apps, ["org.mozilla.firefox"])   # installed one
        self.assertEqual(by_name["Claude"].source, "registry")              # url fallback
        self.assertNotIn("Steam", by_name)                                   # not installed
        self.assertEqual(by_name["Chat"].source, "auto")
        self.assertEqual(by_name["Chat"].code[:2], ("U", "R"))
        self.assertEqual(by_name["Editor"].code[:2], ("U", "U"))
        self.assertTrue(cat.pins_changed)
        self.assertEqual(set(cat.pins), {"org.example.Chat", "org.example.Editor"})

    def test_config_wins(self):
        cfg = config.from_dict({"code": [
            {"name": "My Firefox", "seq": "→ →", "apps": ["org.mozilla.firefox"]},
            {"name": "Steal", "seq": "↑ ← ↑", "run": "x"},
        ]})
        cat = self.build(cfg)
        names = {e.name for e in cat.entries}
        self.assertIn("My Firefox", names)
        self.assertNotIn("Firefox", names)      # registry entry for a covered app is skipped
        self.assertNotIn("Claude", names)       # its code was taken by config
        self.assertTrue(any("Claude" in w for w in cat.warnings))

    def test_auto_off(self):
        cat = self.build(config.Config(auto_apps=False))
        self.assertFalse(any(e.source == "auto" for e in cat.entries))

    def test_pins_keep_codes_stable(self):
        first = self.build()
        chat_code = {e.name: e.code for e in first.entries}["Chat"]
        # A new app arrives whose first-choice hash equals Chat's code:
        # Chat must keep its code, the newcomer gets the next salt.
        newcomer = app("aaa.first.in.order", "Network;", "Newcomer")
        orig = autocode.hash_code
        autocode.hash_code = lambda i, f, salt=0, length=3: (
            chat_code if (i == newcomer.id and salt == 0) else orig(i, f, salt, length))
        try:
            apps_ = dict(self.apps, **{newcomer.id: newcomer})
            second = self.build(pins=first.pins, apps_=apps_)
        finally:
            autocode.hash_code = orig
        codes_ = {e.name: e.code for e in second.entries}
        self.assertEqual(codes_["Chat"], chat_code)
        self.assertNotEqual(codes_["Newcomer"], chat_code)

    def test_app_moving_into_registry_keeps_old_code(self):
        pins = {"steam": ("U", "D", "L", "L", "L")}
        apps_ = dict(self.apps, steam=app("steam", "Game;", "Steam"))
        cat = self.build(pins=pins, apps_=apps_)
        steam = [e for e in cat.entries if e.name == "Steam"]
        self.assertEqual({e.source for e in steam}, {"registry", "auto (old)"})
        self.assertEqual({e.code for e in steam}, {("U", "D", "R"), ("U", "D", "L", "L", "L")})

    def test_no_duplicate_codes_ever(self):
        many = {f"app{i}": app(f"app{i}", "Game;") for i in range(150)}
        cat = self.build(apps_=many)
        all_codes = [e.code for e in cat.entries]
        self.assertEqual(len(all_codes), len(set(all_codes)))
        self.assertEqual(sum(1 for e in cat.entries if e.source == "auto"), 150)
        self.assertEqual(cat.warnings, [])
        self.assertTrue(any(len(e.code) > 5 for e in cat.entries))   # spilled to longer codes
        self.assertTrue(all(codes.show(e.code) for e in cat.entries))


if __name__ == "__main__":
    unittest.main()
