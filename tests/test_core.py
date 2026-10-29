import tempfile
import unittest
from pathlib import Path

from nullshort import codes, config, launcher
from nullshort.actions import Dispatcher
from nullshort.codes import (
    KEY_CAPSLOCK, KEY_DOWN, KEY_ESC, KEY_LEFT, KEY_LEFTALT, KEY_LEFTCTRL,
    KEY_LEFTSHIFT, KEY_RIGHT, KEY_UP, Matcher,
)
from nullshort.engine import PRESS, RELEASE, REPEAT, Router

KEY_A = 30


def tap(router, key):
    return [router.handle(key, PRESS), router.handle(key, RELEASE)]


class CodesTest(unittest.TestCase):
    def test_parse_forms(self):
        for text in ("↑ → ↑", "up right up", "U R U", "URU", "↑→↑", "u, r, u"):
            self.assertEqual(codes.parse(text), ("U", "R", "U"), text)

    def test_parse_errors(self):
        for bad in ("", "  ", "up sideways", "UXU"):
            with self.assertRaises(codes.CodeError):
                codes.parse(bad)

    def test_show(self):
        self.assertEqual(codes.show(("U", "D", "L", "R")), "↑ ↓ ← →")

    def test_matcher(self):
        m = Matcher({("U", "R", "U"): "ff", ("U", "D", "R"): "steam"})
        self.assertEqual(m.get(["U", "R", "U"]), "ff")
        self.assertIsNone(m.get(["U", "R"]))
        self.assertTrue(m.alive(["U"]))
        self.assertTrue(m.alive(["U", "D"]))
        self.assertFalse(m.alive(["D"]))


class RouterTest(unittest.TestCase):
    def setUp(self):
        self.r = Router(KEY_CAPSLOCK, Matcher({("U", "R", "U"): "firefox"}))

    def code(self, *keys):
        """Hold trigger, tap keys, release trigger. Returns all results."""
        out = [self.r.handle(KEY_CAPSLOCK, PRESS)]
        for k in keys:
            out += tap(self.r, k)
        out.append(self.r.handle(KEY_CAPSLOCK, RELEASE))
        return out

    def test_normal_keys_pass_through(self):
        res = tap(self.r, KEY_A)
        self.assertEqual([x.emit for x in res], [[(KEY_A, PRESS)], [(KEY_A, RELEASE)]])
        self.assertEqual(tap(self.r, KEY_UP)[0].emit, [(KEY_UP, PRESS)])

    def test_code_fires_and_swallows_arrows(self):
        res = self.code(KEY_UP, KEY_RIGHT, KEY_UP)
        self.assertTrue(all(not x.emit for x in res))
        self.assertEqual(res[-1].event, "fire")
        self.assertEqual(res[-1].action, "firefox")
        self.assertFalse(self.r.listening)

    def test_wrong_code_fails(self):
        res = self.code(KEY_DOWN, KEY_DOWN)
        self.assertEqual(res[1].event, "dead")
        self.assertEqual(res[-1].event, "fail")
        self.assertEqual(res[-1].action, ("D", "D"))

    def test_partial_code_fails(self):
        self.assertEqual(self.code(KEY_UP, KEY_RIGHT)[-1].event, "fail")

    def test_step_events(self):
        res = self.code(KEY_UP)
        self.assertEqual(res[1].event, "step")

    def test_plain_tap_passes_trigger(self):
        res = self.code()
        self.assertEqual(res[-1].emit, [(KEY_CAPSLOCK, PRESS), (KEY_CAPSLOCK, RELEASE)])

    def test_tap_passthrough_off(self):
        r = Router(KEY_CAPSLOCK, Matcher({}), tap_passthrough=False)
        r.handle(KEY_CAPSLOCK, PRESS)
        self.assertEqual(r.handle(KEY_CAPSLOCK, RELEASE).emit, [])

    def test_other_key_while_held_eaten_and_no_tap(self):
        res = self.code(KEY_A)
        self.assertTrue(all(not x.emit for x in res))

    def test_escape_cancels(self):
        res = self.code(KEY_UP, KEY_ESC, KEY_RIGHT, KEY_UP)
        self.assertEqual(res[-1].event, "cancel")
        self.assertTrue(all(not x.emit for x in res))

    def test_trigger_repeat_eaten(self):
        self.r.handle(KEY_CAPSLOCK, PRESS)
        self.assertEqual(self.r.handle(KEY_CAPSLOCK, REPEAT).emit, [])

    def test_arrow_held_past_trigger_release_does_not_leak(self):
        r = self.r
        r.handle(KEY_CAPSLOCK, PRESS)
        r.handle(KEY_UP, PRESS)
        r.handle(KEY_CAPSLOCK, RELEASE)
        self.assertEqual(r.handle(KEY_UP, REPEAT).emit, [])
        self.assertEqual(r.handle(KEY_UP, RELEASE).emit, [])
        # and arrows work normally afterwards
        self.assertEqual(r.handle(KEY_UP, PRESS).emit, [(KEY_UP, PRESS)])

    def test_key_down_before_trigger_releases_cleanly(self):
        r = self.r
        self.assertEqual(r.handle(KEY_A, PRESS).emit, [(KEY_A, PRESS)])
        r.handle(KEY_CAPSLOCK, PRESS)
        self.assertEqual(r.handle(KEY_A, REPEAT).emit, [(KEY_A, REPEAT)])
        self.assertEqual(r.handle(KEY_A, RELEASE).emit, [(KEY_A, RELEASE)])
        self.assertEqual(r.handle(KEY_CAPSLOCK, RELEASE).emit,
                         [(KEY_CAPSLOCK, PRESS), (KEY_CAPSLOCK, RELEASE)])

    def test_panic_combo(self):
        r = self.r
        for k in (KEY_LEFTCTRL, KEY_LEFTALT, KEY_LEFTSHIFT):
            r.handle(k, PRESS)
        res = r.handle(KEY_ESC, PRESS)
        self.assertEqual(res.event, "panic")
        self.assertEqual(res.emit, [])

    def test_plain_escape_is_not_panic(self):
        self.assertIsNone(self.r.handle(KEY_ESC, PRESS).event)

    def test_reset(self):
        self.r.handle(KEY_CAPSLOCK, PRESS)
        self.r.handle(KEY_UP, PRESS)
        self.r.reset()
        self.assertFalse(self.r.listening)
        self.assertEqual(self.r.handle(KEY_LEFT, PRESS).emit, [(KEY_LEFT, PRESS)])


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "a/applications").mkdir(parents=True)
        (self.root / "b/applications").mkdir(parents=True)
        self.dirs = [self.root / "a", self.root / "b"]
        (self.root / "b/applications/org.mozilla.firefox.desktop").write_text(
            "[Desktop Entry]\nName=Firefox\nExec=/usr/bin/flatpak run org.mozilla.firefox @@u %U @@\n"
            "[Desktop Action new-window]\nExec=firefox --new-window\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_exec_field_codes(self):
        self.assertEqual(launcher.exec_args("steam %U"), ["steam"])
        self.assertEqual(launcher.exec_args('"/opt/my app/run" --x %f'), ["/opt/my app/run", "--x"])
        self.assertEqual(launcher.exec_args("echo 100%%"), ["echo", "100%"])

    def test_resolve_order_and_group(self):
        found = launcher.resolve(["firefox-esr", "org.mozilla.firefox"], self.dirs)
        self.assertEqual(found[0], "org.mozilla.firefox")
        entry = launcher.read_entry(found[1])
        self.assertIn("flatpak run", entry["Exec"])   # not the Action's Exec

    def test_launch_falls_back_to_url(self):
        calls = []
        launcher_spawn = launcher._spawn
        launcher._spawn = lambda args, cwd=None: calls.append(args)
        try:
            e = config.Entry(name="Claude", code=("U",), apps=["nope"], url="https://claude.ai")
            self.assertIn("claude.ai", launcher.launch(e, self.dirs))
            e2 = config.Entry(name="FF", code=("U",), apps=["org.mozilla.firefox"])
            launcher.launch(e2, self.dirs)
            self.assertEqual(calls[-1], ["/usr/bin/flatpak", "run", "org.mozilla.firefox", "@@u", "@@"])
            with self.assertRaises(LookupError):
                launcher.launch(config.Entry(name="X", code=("U",), apps=["nope"]), self.dirs)
        finally:
            launcher._spawn = launcher_spawn


class ConfigTest(unittest.TestCase):
    def test_bundled_config(self):
        cfg = config.load(config.BUNDLED)
        names = {e.name for e in cfg.entries}
        self.assertEqual(names, {"Firefox", "Microsoft Edge", "Steam", "Minecraft",
                                 "qBittorrent", "Claude"})
        self.assertEqual(cfg.trigger, "KEY_CAPSLOCK")
        self.assertTrue(all(e.code[0] == "U" for e in cfg.entries))

    def test_duplicate_code_rejected(self):
        data = {"code": [{"name": "A", "seq": "U U", "run": "a"},
                         {"name": "B", "seq": "up up", "run": "b"}]}
        with self.assertRaisesRegex(config.ConfigError, "already used by A"):
            config.from_dict(data)

    def test_validation(self):
        for data in ({"code": [{"name": "A", "seq": "U"}]},
                     {"code": [{"name": "A", "seq": "X", "run": "a"}]},
                     {"trigger": "CAPS", "code": [{"seq": "U", "run": "a"}]},
                     {"code": []}):
            with self.assertRaises(config.ConfigError):
                config.from_dict(data)


class DispatcherTest(unittest.TestCase):
    def test_cooldown_and_errors(self):
        now = [0.0]
        launched = []
        cfg = config.Config(sounds=False)

        def fake_launch(entry):
            if entry.name == "Broken":
                raise LookupError("not installed")
            launched.append(entry.name)
            return "ok"

        d = Dispatcher(cfg, clock=lambda: now[0], launch=fake_launch)
        ff = config.Entry(name="Firefox", code=("U", "R", "U"))
        self.assertTrue(d.handle("fire", ff).startswith("fire"))
        self.assertTrue(d.handle("fire", ff).startswith("cooldown"))
        now[0] = 2.0
        d.handle("fire", ff)
        self.assertEqual(launched, ["Firefox", "Firefox"])
        broken = config.Entry(name="Broken", code=("D",))
        self.assertTrue(d.handle("fire", broken).startswith("error"))
        self.assertEqual(d.handle("fail", ("D", "D")), "fail: ↓ ↓")


if __name__ == "__main__":
    unittest.main()
