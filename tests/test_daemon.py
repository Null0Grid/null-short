"""Exercises the daemon's event glue with a fake evdev module, so it runs
anywhere (no keyboard, no /dev/uinput needed)."""

import os
import sys
import tempfile
import types
import unittest
from collections import namedtuple

from nullshort.codes import KEY_CAPSLOCK, KEY_LEFT, KEY_UP

EV_SYN, EV_KEY, EV_MSC = 0, 1, 4
KEY_A = 30

fake_ecodes = types.SimpleNamespace(
    EV_SYN=EV_SYN, EV_KEY=EV_KEY, EV_MSC=EV_MSC, KEY_A=KEY_A, KEY_UP=KEY_UP,
    ecodes={"KEY_CAPSLOCK": KEY_CAPSLOCK, "KEY_A": KEY_A},
)
fake_evdev = types.ModuleType("evdev")
fake_evdev.ecodes = fake_ecodes
sys.modules.setdefault("evdev", fake_evdev)
sys.modules.setdefault("evdev.ecodes", fake_ecodes)

from nullshort import config, daemon  # noqa: E402

Ev = namedtuple("Ev", "type code value")


class FakeUI:
    def __init__(self):
        self.out = []

    def write(self, t, c, v):
        self.out.append((t, c, v))

    def write_event(self, ev):
        self.out.append((ev.type, ev.code, ev.value))

    def syn(self):
        self.out.append((EV_SYN, 0, 0))


class FakeDev:
    def __init__(self, name, keys):
        self.name = name
        self._keys = keys

    def capabilities(self):
        return {EV_KEY: self._keys}


class DaemonGlueTest(unittest.TestCase):
    def setUp(self):
        # Keep the test away from the real home: no apps, no pins written.
        self.tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ)
        for var in ("HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME"):
            os.environ[var] = self.tmp.name
        os.environ["XDG_DATA_DIRS"] = self.tmp.name + "/none"
        self.addCleanup(self.restore)
        cfg = config.load(config.BUNDLED)
        cfg.sounds = False
        self.d = daemon.Daemon(cfg)
        self.fired = []
        self.d.dispatcher.handle = lambda ev, act: self.fired.append((ev, act))
        self.ui = FakeUI()

    def restore(self):
        os.environ.clear()
        os.environ.update(self.env)
        self.tmp.cleanup()

    def send(self, t, c, v):
        self.d.on_event(self.ui, Ev(t, c, v))

    def test_passthrough_and_syn(self):
        self.send(EV_KEY, KEY_A, 1)
        self.send(EV_SYN, 0, 0)
        self.assertEqual(self.ui.out, [(EV_KEY, KEY_A, 1), (EV_SYN, 0, 0)])

    def test_registry_code_fires(self):
        # No apps installed here, so only Claude (web fallback) is in the table.
        for k, v in [(KEY_CAPSLOCK, 1), (KEY_UP, 1), (KEY_UP, 0), (KEY_LEFT, 1),
                     (KEY_LEFT, 0), (KEY_UP, 1), (KEY_UP, 0), (KEY_CAPSLOCK, 0)]:
            self.send(EV_KEY, k, v)
        self.assertEqual(self.ui.out, [])
        self.assertEqual(self.fired[-1][0], "fire")
        self.assertEqual(self.fired[-1][1].name, "Claude")
        self.assertEqual(self.fired[-1][1].source, "registry")

    def test_reload_picks_up_new_app(self):
        appdir = os.path.join(self.tmp.name, "applications")
        os.makedirs(appdir)
        with open(os.path.join(appdir, "steam.desktop"), "w") as f:
            f.write("[Desktop Entry]\nType=Application\nName=Steam\nExec=steam\nCategories=Game;\n")
        before = self.d.code_count
        self.d.reload("test")
        self.assertEqual(self.d.code_count, before + 1)
        self.assertEqual(self.d.router.matcher.get(("U", "D", "R")).name, "Steam")

    def test_reload_waits_while_code_in_progress(self):
        self.send(EV_KEY, KEY_CAPSLOCK, 1)
        self.d.reload("test")
        self.assertTrue(self.d._pending_reload)
        self.send(EV_KEY, KEY_CAPSLOCK, 0)
        self.d.reload("test")
        self.assertFalse(self.d._pending_reload)

    def test_tap_emits_with_syn_between(self):
        self.send(EV_KEY, KEY_CAPSLOCK, 1)
        self.send(EV_KEY, KEY_CAPSLOCK, 0)
        self.assertEqual(self.ui.out, [(EV_KEY, KEY_CAPSLOCK, 1), (EV_SYN, 0, 0),
                                       (EV_KEY, KEY_CAPSLOCK, 0), (EV_SYN, 0, 0)])

    def test_router_crash_fails_open(self):
        def boom(*a):
            raise RuntimeError("bug")
        self.d.router.handle = boom
        with self.assertLogs("null-short", "ERROR"):
            self.send(EV_KEY, KEY_A, 1)
        self.assertEqual(self.ui.out, [(EV_KEY, KEY_A, 1)])

    def test_keyboard_detection(self):
        self.assertTrue(daemon.is_keyboard(FakeDev("AT keyboard", [KEY_A, KEY_UP])))
        self.assertFalse(daemon.is_keyboard(FakeDev("Power Button", [116])))
        self.assertFalse(daemon.is_keyboard(
            FakeDev(daemon.VIRTUAL_PREFIX + " (AT keyboard)", [KEY_A, KEY_UP])))

    def test_unknown_trigger(self):
        with self.assertRaises(SystemExit):
            daemon.resolve_key("KEY_NOPE")


if __name__ == "__main__":
    unittest.main()
