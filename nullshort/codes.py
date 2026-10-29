"""Arrow codes: parsing, display and prefix matching.

A code is a tuple of directions, each one of "U", "D", "L", "R".
"""

from __future__ import annotations

# Linux input keycodes (from linux/input-event-codes.h). Kept here so the
# core logic and its tests never need python-evdev.
KEY_ESC = 1
KEY_LEFTCTRL = 29
KEY_LEFTSHIFT = 42
KEY_RIGHTSHIFT = 54
KEY_LEFTALT = 56
KEY_CAPSLOCK = 58
KEY_RIGHTCTRL = 97
KEY_RIGHTALT = 100
KEY_UP = 103
KEY_LEFT = 105
KEY_RIGHT = 106
KEY_DOWN = 108

ARROW_KEYS = {KEY_UP: "U", KEY_DOWN: "D", KEY_LEFT: "L", KEY_RIGHT: "R"}

GLYPHS = {"U": "↑", "D": "↓", "L": "←", "R": "→"}

_TOKENS = {
    "↑": "U", "u": "U", "up": "U",
    "↓": "D", "d": "D", "down": "D",
    "←": "L", "l": "L", "left": "L",
    "→": "R", "r": "R", "right": "R",
}


class CodeError(ValueError):
    """Raised when a code string can't be parsed."""


def parse(text: str) -> tuple[str, ...]:
    """Turn '↑ → ↑', 'up right up', 'U R U' or 'URU' into ('U', 'R', 'U')."""
    if not isinstance(text, str) or not text.strip():
        raise CodeError("code is empty")
    cleaned = text.replace(",", " ").strip()
    words = cleaned.split()
    out: list[str] = []
    for word in words:
        key = word.lower()
        if key in _TOKENS:
            out.append(_TOKENS[key])
            continue
        # Compact forms like "URU" or "↑→↑"
        for ch in word:
            k = ch.lower()
            if k not in _TOKENS:
                raise CodeError(f"unknown direction {ch!r} in {text!r}")
            out.append(_TOKENS[k])
    return tuple(out)


def show(code: tuple[str, ...]) -> str:
    """('U', 'R', 'U') -> '↑ → ↑'"""
    return " ".join(GLYPHS[d] for d in code)


class Matcher:
    """Looks up complete codes and answers "can this still become a code?"."""

    def __init__(self, codes: dict[tuple[str, ...], object]):
        self._codes = dict(codes)
        self._prefixes: set[tuple[str, ...]] = set()
        for code in self._codes:
            for i in range(1, len(code) + 1):
                self._prefixes.add(code[:i])

    def get(self, seq) -> object | None:
        return self._codes.get(tuple(seq))

    def alive(self, seq) -> bool:
        """True if seq is the start of (or equal to) at least one code."""
        return tuple(seq) in self._prefixes

    def __len__(self) -> int:
        return len(self._codes)
