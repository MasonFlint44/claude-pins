"""Colour and glyphs: Claude Code's own tokens, the budget statusline's ramp, and the marker glyph set.

Two colour families: periwinkle and clay are the interface (prompt, pointer, matches), the ramp and the
mode tokens are data (effort, context, permission mode). Everything else is dim or plain, so the look
holds on light and dark terminals alike.
"""

from __future__ import annotations

import locale
import os
from dataclasses import dataclass

from . import config

# Claude Code's dark-theme tokens, read from the 2.1.263 bundle.
CLAY = "#d97757"          # claude
PERIWINKLE = "#b1b9f9"    # permission / suggestion
SUCCESS = "#4eba65"
ERROR = "#ff6b80"
WARNING = "#ffc107"
PLAN = "#48968c"          # planMode
AUTO_ACCEPT = "#af87ff"

# The permission-mode indicator's colours, as Claude Code maps them; ``default`` has none.
MODE_COLORS = {"plan": PLAN, "acceptEdits": AUTO_ACCEPT, "bypassPermissions": ERROR, "dontAsk": ERROR,
               "auto": WARNING}

# The budget statusline's ramp: green at 5%, gold at 50%, coral at 95%, linear in between.
RAMP = ((5, (0x6b, 0xb8, 0x5f)), (50, (0xfa, 0xb2, 0x19)), (95, (0xff, 0x58, 0x58)))
EFFORT_PCT = {"low": 0, "medium": 25, "high": 50, "xhigh": 75, "max": 100}


def ramp(pct: float) -> str:
    """``#rrggbb`` on the ramp for a percentage, clamped to its ends."""
    (lo_p, lo), *rest = RAMP
    if pct <= lo_p:
        return _hex(lo)
    for hi_p, hi in rest:
        if pct <= hi_p:
            t = (pct - lo_p) / (hi_p - lo_p)
            return _hex(tuple(round(a + (b - a) * t) for a, b in zip(lo, hi)))
        lo_p, lo = hi_p, hi
    return _hex(lo)


def _hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def effort_color(level: str) -> str | None:
    pct = EFFORT_PCT.get(level)
    return ramp(pct) if pct is not None else None


def mode_color(mode: str) -> str | None:
    return MODE_COLORS.get(mode)


# ---- the palette ----------------------------------------------------------------------------

_ATTRS = {"dim": "2", "bold": "1", "strike": "9"}


def sgr(style: str) -> str:
    """The SGR parameters for a style: an attribute name, or a ``#rrggbb`` foreground as 24-bit colour."""
    if style.startswith("#"):
        r, g, b = (int(style[i:i + 2], 16) for i in (1, 3, 5))
        return f"38;2;{r};{g};{b}"
    return _ATTRS[style]


class Palette:
    """ANSI colouring that turns into a no-op when colour is off."""

    def __init__(self, enabled: bool):
        self.enabled = enabled

    def __call__(self, text: str, *styles: str) -> str:
        if not self.enabled or not styles or not text:
            return text
        return f"\x1b[{';'.join(sgr(s) for s in styles)}m{text}\x1b[0m"


def palette(stream=None) -> Palette:
    return Palette(config.color_enabled(stream))


def fzf_colors() -> str:
    """The ``--color`` spec for fzf's chrome. The current row keeps fzf's default, since a 24-bit
    background would assume a dark terminal. The chrome is the terminal's own foreground (``-1``) with
    dim (SGR 2), which reads on light and dark alike; ``header:dim`` alone would keep fzf's teal."""
    dim = [f"{name}:-1:dim" for name in ("header", "info", "border", "label", "preview-label")]
    return ",".join([f"prompt:{CLAY}", f"spinner:{CLAY}", f"pointer:{PERIWINKLE}", f"hl:{PERIWINKLE}",
                     f"hl+:{PERIWINKLE}", f"marker:{SUCCESS}", *dim])


# ---- glyphs ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Glyphs:
    mode: str
    open: str
    keep: str
    fork: str
    worktree: str
    expiring: str
    expired: str
    logo: str       # empty in the text set
    pinned: str     # before the alias of an already pinned session; the text one shares ⚑'s Unicode block


EMOJI = Glyphs("emoji", "🟢", "🚩", "🔀", "🌳", "⏳", "🔴", "📌", "📌")
TEXT = Glyphs("text", "●", "⚑", "⑂", "⌂", "⧗", "✗", "", "⚲")
GLYPH_SETS = {"emoji": EMOJI, "text": TEXT}

# Emoji carry their own colour; the one-cell text glyphs get one so the states still read at a glance.
TEXT_GLYPH_COLORS = {"open": SUCCESS, "expiring": WARNING, "expired": ERROR,
                     "keep": PERIWINKLE, "fork": PERIWINKLE, "worktree": PERIWINKLE}


def glyph_mode() -> str:
    """``emoji`` on a UTF-8 locale outside the Linux console and dumb terminals; ``CLAUDE_PINS_GLYPHS``
    settles it either way."""
    forced = os.environ.get("CLAUDE_PINS_GLYPHS", "").strip().lower()
    if forced in GLYPH_SETS:
        return forced
    if os.environ.get("TERM", "") in ("linux", "dumb"):
        return "text"
    ctype = next((os.environ[k] for k in ("LC_ALL", "LC_CTYPE", "LANG") if os.environ.get(k)), "")
    encoding = ctype.partition(".")[2].partition("@")[0] if ctype else locale.getpreferredencoding(False)
    return "emoji" if encoding.lower().replace("-", "") == "utf8" else "text"


def glyphs() -> Glyphs:
    return GLYPH_SETS[glyph_mode()]


def paint_glyph(name: str, color: Palette, g: Glyphs | None = None) -> str:
    """A marker glyph, coloured when it is a text glyph."""
    g = g or glyphs()
    glyph = getattr(g, name)
    if g.mode == "emoji":
        return glyph
    return color(glyph, TEXT_GLYPH_COLORS[name])
