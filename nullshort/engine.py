"""The router: decides, key event by key event, what passes through to the
system and when a code fires.

It knows nothing about evdev or devices, which keeps it fully testable.
Key events are (keycode, value) with value 1 = press, 0 = release, 2 = repeat.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .codes import (
    ARROW_KEYS, KEY_ESC, KEY_LEFTALT, KEY_LEFTCTRL, KEY_LEFTSHIFT,
    KEY_RIGHTALT, KEY_RIGHTCTRL, KEY_RIGHTSHIFT, Matcher,
)

PRESS, RELEASE, REPEAT = 1, 0, 2

_CTRL = {KEY_LEFTCTRL, KEY_RIGHTCTRL}
_ALT = {KEY_LEFTALT, KEY_RIGHTALT}
_SHIFT = {KEY_LEFTSHIFT, KEY_RIGHTSHIFT}


@dataclass
class Result:
    # Key events to send on to the system, in order.
    emit: list[tuple[int, int]] = field(default_factory=list)
    # What happened, for feedback: None, "arm", "step", "dead", "cancel",
    # "fire", "fail", "panic".
    event: str | None = None
    # The matched entry when event == "fire".
    action: object | None = None


class Router:
    def __init__(self, trigger: int, matcher: Matcher, tap_passthrough: bool = True):
        self.trigger = trigger
        self.matcher = matcher
        self.tap_passthrough = tap_passthrough
        self.reset()

    def reset(self) -> None:
        self.listening = False
        self.seq: list[str] = []
        self.dirty = False        # something other than arrows happened while held
        self.cancelled = False
        self.passed_down: set[int] = set()   # presses we sent on, not yet released
        self.swallowed: set[int] = set()     # presses we ate, not yet released

    # ------------------------------------------------------------------ #
    def handle(self, code: int, value: int) -> Result:
        if code == self.trigger:
            return self._trigger(value)
        if self.listening:
            return self._while_listening(code, value)
        return self._normal(code, value)

    # ------------------------------------------------------------------ #
    def _trigger(self, value: int) -> Result:
        if value == PRESS and not self.listening:
            self.listening = True
            self.seq = []
            self.dirty = False
            self.cancelled = False
            return Result(event="arm")
        if value != RELEASE:
            return Result()  # repeats (or a stray second press) are eaten

        # Trigger released: decide.
        self.listening = False
        if self.cancelled:
            return Result(event="cancel")
        if self.seq:
            entry = self.matcher.get(self.seq)
            seq, self.seq = self.seq, []
            if entry is not None:
                return Result(event="fire", action=entry)
            return Result(event="fail", action=tuple(seq))
        if self.tap_passthrough and not self.dirty:
            # A plain tap: let the key do its normal job (e.g. toggle Caps Lock).
            return Result(emit=[(self.trigger, PRESS), (self.trigger, RELEASE)])
        return Result()

    def _while_listening(self, code: int, value: int) -> Result:
        if value == PRESS:
            self.swallowed.add(code)
            if code in ARROW_KEYS and not self.cancelled:
                self.seq.append(ARROW_KEYS[code])
                return Result(event="step" if self.matcher.alive(self.seq) else "dead")
            if code == KEY_ESC:
                self.cancelled = True
                self.seq = []
                return Result(event="cancel")
            self.dirty = True
            return Result()
        if value == REPEAT:
            if code in self.passed_down:
                return Result(emit=[(code, value)])
            return Result()
        # Release
        if code in self.passed_down:
            # Key was already down before the trigger; let its release through
            # so nothing sticks.
            self.passed_down.discard(code)
            return Result(emit=[(code, value)])
        self.swallowed.discard(code)
        return Result()

    def _normal(self, code: int, value: int) -> Result:
        if code in self.swallowed and value != PRESS:
            # Release/repeat of a key we ate while listening.
            if value == RELEASE:
                self.swallowed.discard(code)
            return Result()
        if value == PRESS:
            self.swallowed.discard(code)
            if code == KEY_ESC and self._panic_mods_down():
                return Result(event="panic")
            self.passed_down.add(code)
        elif value == RELEASE:
            self.passed_down.discard(code)
        return Result(emit=[(code, value)])

    def _panic_mods_down(self) -> bool:
        d = self.passed_down
        return bool(d & _CTRL) and bool(d & _ALT) and bool(d & _SHIFT)
