"""Bytes from the terminal to key and mouse events, the way fzf reads them.

The table is fzf's own (``src/tui/light.go``, 0.67.0): arrows as CSI and SS3, F1–F4 as SS3 and every
F key in the tilde form, Home and End in their three encodings, the modified arrows ``ESC [ 1 ; N X``
with N naming shift (2), alt (3), ctrl (5) and their sums, alt as ESC before the key, ctrl-alt as ESC
before the control byte, backspace as 127 and 8, ctrl-space as NUL, and SGR mouse reports. Names are
fzf's key names, which the keymap file already uses (``alt-t``, ``ctrl-space``, ``f5``, ``shift-tab``).

Esc on its own is the hard case: the terminal sends alt-x as ESC x, so ESC alone is only esc once nothing
follows it. fzf waits ``ESCDELAY`` milliseconds (default 100) and so does :class:`Reader`; a longer
partial sequence left over after the wait is dropped, as fzf drops it.

:func:`encode` is the other direction, for tests and the terminal check: the bytes a terminal sends for a
name.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, Union

DEFAULT_ESC_DELAY = 0.1
DOUBLE_CLICK = 0.5          # seconds between two left clicks on one cell that make a double-click


@dataclass(frozen=True)
class Key:
    name: str           # "enter", "alt-t", "ctrl-a", "up", "f1", … or "char" for a typed character
    char: str = ""      # the character when name is "char"

    def __str__(self) -> str:
        return self.char if self.name == "char" else self.name


@dataclass(frozen=True)
class Mouse:
    x: int                  # 0-based cell
    y: int
    button: str = "left"    # "left", "middle", "right" or "" for a wheel event
    down: bool = True       # press (M) rather than release (m)
    scroll: int = 0         # +1 wheel up, -1 wheel down
    double: bool = False
    shift: bool = False
    alt: bool = False
    ctrl: bool = False

    def __str__(self) -> str:
        if self.scroll:
            return f"scroll-{'up' if self.scroll > 0 else 'down'} at {self.x},{self.y}"
        what = f"{'double-' if self.double else ''}{self.button}-{'click' if self.down else 'release'}"
        return f"{what} at {self.x},{self.y}"


Event = Union[Key, Mouse]      # typing.Union: a shebang may find an older python3 before pin can say so

ESC = 0x1B
_CTRL_NAMES = {0: "ctrl-space", 8: "ctrl-h", 9: "tab", 13: "enter", 28: "ctrl-\\", 29: "ctrl-]", 30: "ctrl-^",
               31: "ctrl-/", 127: "bspace"}
_ARROWS = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end"}
_TILDE = {"1": "home", "2": "insert", "3": "del", "4": "end", "5": "pgup", "6": "pgdn", "7": "home", "8": "end",
          "11": "f1", "12": "f2", "13": "f3", "14": "f4", "15": "f5", "17": "f6", "18": "f7", "19": "f8",
          "20": "f9", "21": "f10", "23": "f11", "24": "f12"}
_SS3_F = {"P": "f1", "Q": "f2", "R": "f3", "S": "f4"}


def _modifiers(n: int) -> str:
    """The ``ctrl-alt-shift-`` prefix for an xterm modifier parameter (1 + shift 1 + alt 2 + ctrl 4)."""
    n -= 1
    return "".join(p for bit, p in ((4, "ctrl-"), (2, "alt-"), (1, "shift-")) if n & bit)


def decode(buf: bytes, *, alt: bool = False) -> tuple[Key | Mouse | None, int]:
    """One event from the front of ``buf``: (event, bytes consumed). ``(None, 0)`` means the bytes so far
    are the start of a longer sequence; ``(None, n)`` a sequence fzf would also drop."""
    if not buf:
        return None, 0
    b = buf[0]
    if b != ESC:
        if b in _CTRL_NAMES:
            return Key(_CTRL_NAMES[b]), 1
        if 1 <= b <= 26:
            return Key(f"ctrl-{chr(b + 96)}"), 1
        if b < 32:
            return None, 1
        if 0x80 <= b < 0xC2 or b >= 0xF5:
            return Key("esc"), 1        # not a UTF-8 lead byte: fzf reads a bad rune as esc
        n = 1 if b < 0x80 else 2 if b < 0xE0 else 3 if b < 0xF0 else 4
        if len(buf) < n:
            return None, 0
        try:
            return Key("char", buf[:n].decode("utf-8")), n
        except UnicodeDecodeError:
            return Key("esc"), 1        # fzf reads a bad rune as esc
    if len(buf) == 1:
        return None, 0                  # esc alone, or the start of a sequence: the reader decides by time
    b1 = buf[1]
    if b1 == 8:
        return Key("ctrl-alt-bspace"), 2
    if 1 <= b1 <= 26 and b1 not in (9, 13, 10):
        return Key(f"ctrl-alt-{chr(b1 + 96)}"), 2
    if b1 == ESC:
        if len(buf) == 2:
            return Key("esc"), 2
        ev, n = decode(buf[1:], alt=True)   # ESC ESC [ A: alt-up
        return ev, (n + 1 if n else 0)
    if b1 == 127:
        return Key("alt-bspace"), 2
    if b1 in (0x5B, 0x4F):              # '[' or 'O'
        if len(buf) < 3:
            return None, 0
        c = chr(buf[2])
        if c in _ARROWS:
            return Key(("alt-" if alt else "") + _ARROWS[c]), 3
        if c == "Z":
            return Key("shift-tab"), 3
        if c in _SS3_F:
            return Key(_SS3_F[c]), 3
        if c == "<" and b1 == 0x5B:
            return _mouse(buf)
        if c.isdigit():
            end = 3
            while end < len(buf) and (buf[end:end + 1].isdigit() or buf[end] == 0x3B):
                end += 1
            if end >= len(buf):
                return None, 0
            final = chr(buf[end])
            num, _, mod = buf[2:end].decode("ascii").partition(";")
            if final == "~":
                if num == "200":
                    return Key("paste-begin"), end + 1
                if num == "201":
                    return Key("paste-end"), end + 1
                name = _TILDE.get(num)
                if name is None:
                    return None, end + 1
                prefix = _modifiers(int(mod)) if mod.isdigit() else ""
                return Key(prefix + name), end + 1
            if final in _ARROWS and num == "1" and mod.isdigit():
                return Key(_modifiers(int(mod)) + _ARROWS[final]), end + 1
            return None, end + 1
        return None, 3
    # alt + a printable character
    n = 1 if b1 < 0x80 else 2 if b1 < 0xE0 else 3 if b1 < 0xF0 else 4
    if len(buf) < 1 + n:
        return None, 0
    try:
        ch = buf[1:1 + n].decode("utf-8")
    except UnicodeDecodeError:
        return None, 1 + n
    if ch == " ":
        return Key("alt-space"), 1 + n
    if ch == "\r":
        return Key("alt-enter"), 1 + n
    return Key(f"alt-{ch}"), 1 + n


def _mouse(buf: bytes) -> tuple[Mouse | None, int]:
    """An SGR report ``ESC [ < b ; x ; y M|m`` (xterm mode 1006): button bits, 1-based cell."""
    end = 3
    while end < len(buf) and buf[end] not in (0x4D, 0x6D):      # 'M' or 'm'
        if not (buf[end:end + 1].isdigit() or buf[end] == 0x3B):
            return None, end + 1
        end += 1
    if end >= len(buf):
        return None, 0
    try:
        t, x, y = (int(p) for p in buf[3:end].decode("ascii").split(";"))
    except ValueError:
        return None, end + 1
    down = buf[end] == 0x4D
    scroll = 0
    if t >= 64:
        t -= 64
        scroll = -1 if t & 1 else 1
    button = {0: "left", 1: "middle", 2: "right"}.get(t & 3, "")
    if scroll:
        button = ""
    return Mouse(x - 1, y - 1, button, down, scroll, shift=bool(t & 4), alt=bool(t & 8), ctrl=bool(t & 16)), end + 1


class Reader:
    """Turns what a terminal sends into events, holding a lone ESC or a partial sequence for the esc
    delay before deciding. ``read(timeout)`` returns bytes (empty on timeout); ``clock`` is for tests."""

    def __init__(self, read: Callable[[float], bytes], *, esc_delay: float | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.read = read
        self.clock = clock
        self.buf = b""
        env = os.environ.get("ESCDELAY", "")
        self.esc_delay = esc_delay if esc_delay is not None else (int(env) / 1000 if env.isdigit() else DEFAULT_ESC_DELAY)
        self._last_down: tuple[float, int, int] | None = None
        self.woken = False              # a wake arrived while a partial sequence was being waited out

    def next(self, timeout: float | None = None) -> Event | None:
        """The next event, waiting at most ``timeout`` (None: for ever) for bytes; None on timeout."""
        deadline = None if timeout is None else self.clock() + timeout
        while True:
            ev, n = decode(self.buf)
            if ev is not None:
                self.buf = self.buf[n:]
                return self._clicks(ev)
            if n:                       # a sequence fzf drops too
                self.buf = self.buf[n:]
                continue
            if self.buf:                # a lone ESC or a partial sequence: give the rest the esc delay
                more = self.read(self.esc_delay)
                if more:
                    self.buf += more
                    continue
                if more is None:
                    self.woken = True
                if self.buf == b"\x1b":
                    self.buf = b""
                    return Key("esc")
                self.buf = b""          # a partial sequence with nothing behind it
                continue
            wait = None if deadline is None else max(0.0, deadline - self.clock())
            if wait == 0.0:
                return None
            data = self.read(wait)
            if data is None:            # woken for something other than input
                return None
            if not data:
                if deadline is not None and self.clock() >= deadline:
                    return None
                continue
            self.buf += data

    def _clicks(self, ev: Event) -> Event:
        """fzf's double-click: two left presses on one cell within half a second."""
        if not isinstance(ev, Mouse) or ev.scroll or not ev.down or ev.button != "left":
            return ev
        now = self.clock()
        last = self._last_down
        self._last_down = (now, ev.x, ev.y)
        if last and now - last[0] < DOUBLE_CLICK and (last[1], last[2]) == (ev.x, ev.y):
            self._last_down = None
            return Mouse(ev.x, ev.y, ev.button, ev.down, 0, True, ev.shift, ev.alt, ev.ctrl)
        return ev


# ---- the other direction ---------------------------------------------------------------------------------

ALIASES = {"btab": "shift-tab", "bs": "bspace", "return": "enter", "page-up": "pgup", "page-down": "pgdn",
           "alt-bs": "alt-bspace", "shift-delete": "shift-del", "ctrl-delete": "ctrl-del", "alt-delete": "alt-del",
           "delete": "del"}


def canonical(name: str) -> str:
    """One name per key: the keymap file accepts fzf's aliases, the decoder emits one of them."""
    return ALIASES.get(name, name)


def encode(name: str) -> bytes:
    """The bytes a terminal sends for ``name`` (the common encoding where there are several)."""
    name = canonical(name)
    if name == "char" or len(name) == 1:
        return name.encode("utf-8")
    fixed = {"enter": b"\r", "tab": b"\t", "shift-tab": b"\x1b[Z", "esc": b"\x1b", "bspace": b"\x7f",
             "ctrl-h": b"\x08", "ctrl-space": b"\x00", "space": b" ", "up": b"\x1b[A", "down": b"\x1b[B",
             "right": b"\x1b[C", "left": b"\x1b[D", "home": b"\x1b[H", "end": b"\x1b[F", "insert": b"\x1b[2~",
             "del": b"\x1b[3~", "pgup": b"\x1b[5~", "pgdn": b"\x1b[6~", "f1": b"\x1bOP", "f2": b"\x1bOQ",
             "f3": b"\x1bOR", "f4": b"\x1bOS", "alt-bspace": b"\x1b\x7f", "alt-space": b"\x1b ",
             "alt-enter": b"\x1b\r", "ctrl-alt-bspace": b"\x1b\x08", "ctrl-\\": b"\x1c", "ctrl-]": b"\x1d",
             "ctrl-^": b"\x1e", "ctrl-/": b"\x1f"}
    if name in fixed:
        return fixed[name]
    for num, key in _TILDE.items():
        if key == name and len(num) == 2:
            return f"\x1b[{num}~".encode()
    if name.startswith("ctrl-alt-") and len(name) == 10:
        return b"\x1b" + bytes([ord(name[-1]) - 96])
    if name.startswith("ctrl-") and len(name) == 6:
        return bytes([ord(name[-1]) - 96])
    if name.startswith("alt-") and len(name) == 5:
        return b"\x1b" + name[-1].encode("utf-8")
    mods = {"shift-": 1, "alt-": 2, "ctrl-": 4}
    n, rest = 0, name
    changed = True
    while changed:
        changed = False
        for p, bit in mods.items():
            if rest.startswith(p):
                n |= bit; rest = rest[len(p):]; changed = True
    if n:
        for c, key in _ARROWS.items():
            if key == rest:
                return f"\x1b[1;{n + 1}{c}".encode()
        for num, key in _TILDE.items():
            if key == rest and num in ("3", "5", "6"):
                return f"\x1b[{num};{n + 1}~".encode()
    raise ValueError(f"no encoding for key {name!r}")


def mouse_report(x: int, y: int, *, button: str = "left", down: bool = True, scroll: int = 0,
                 shift: bool = False) -> bytes:
    """An SGR mouse report for cell (x, y), 0-based."""
    t = {"left": 0, "middle": 1, "right": 2, "": 0}[button]
    if scroll:
        t = 64 + (1 if scroll < 0 else 0)
    if shift:
        t |= 4
    return f"\x1b[<{t};{x + 1};{y + 1}{'M' if down or scroll else 'm'}".encode()
