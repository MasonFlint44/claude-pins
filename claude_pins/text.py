"""Terminal cell arithmetic: how wide text renders, and clipping and padding by that width.

Emoji and other East Asian wide characters take two cells; measuring by ``len`` misaligns every
column after one. This mirrors fzf's runewidth, so rows line up under fzf and in plain output alike.
"""

from __future__ import annotations

import unicodedata


def cell_width(ch: str) -> int:
    """Terminal cells one character takes, the way fzf's runewidth counts them: wide/fullwidth 2, combining 0."""
    if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def cells(text: str) -> int:
    """Display width of ``text`` in terminal cells (no colour codes expected)."""
    return sum(cell_width(ch) for ch in text)


def clip(text: str, width: int, *, marker: str = "…", left: bool = False) -> str:
    """``text`` cut to at most ``width`` cells; when cut, ``marker`` marks the cut end."""
    if cells(text) <= width:
        return text
    room = width - cells(marker)
    if room <= 0:
        return marker if width >= cells(marker) else ""
    if left:
        kept, w = [], 0
        for ch in reversed(text):
            cw = cell_width(ch)
            if w + cw > room:
                break
            kept.append(ch)
            w += cw
        return marker + "".join(reversed(kept))
    kept, w = [], 0
    for ch in text:
        cw = cell_width(ch)
        if w + cw > room:
            break
        kept.append(ch)
        w += cw
    return "".join(kept).rstrip() + marker


def pad(text: str, width: int, align: str = "<") -> str:
    """Pad or clip ``text`` to exactly ``width`` terminal cells."""
    text = clip(text, width)
    fill = " " * (width - cells(text))
    return text + fill if align == "<" else fill + text
