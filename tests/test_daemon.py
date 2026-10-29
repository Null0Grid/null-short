"""Exercises the daemon's event glue with a fake evdev module, so it runs
anywhere (no keyboard, no /dev/uinput needed)."""

import sys
import types
import unittest
from collections import namedtuple

from nullshort.codes import KEY_CAPSLOCK, KEY_RIGHT, KEY_UP

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
        cfg = config.load(config.BUNDLED)
        cfg.sounds = False
        self.d = daemon.Daemon(cfg)
        self.fired = []
        self.d.dispatcher.handle = lambda ev, act: self.fired.append((ev, act))
        self.ui = FakeUI()

    def send(self, t, c, v):
        self.d.on_event(self.ui, Ev(t, c, v))

    def test_passthrough_and_syn(self):
        self.send(EV_KEY, KEY_A, 1)
        self.send(EV_SYN, 0, 0)
        self.assertEqual(self.ui.out, [(EV_KEY, KEY_A, 1), (EV_SYN, 0, 0)])

    def test_code_fires_firefox(self):
        for k, v in [(KEY_CAPSLOCK, 1), (KEY_UP, 1), (KEY_UP, 0), (KEY_RIGHT, 1),
                     (KEY_RIGHT, 0), (KEY_UP, 1), (KEY_UP, 0), (KEY_CAPSLOCK, 0)]:
            self.send(EV_KEY, k, v)
        self.assertEqual(self.ui.out, [])
        self.assertEqual(self.fired[-1][0], "fire")
        self.assertEqual(self.fired[-1][1].name, "Firefox")

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
