"""Coloured text as cells, so a row can be highlighted, painted and clipped after it was rendered.

The rows a screen gets already carry their colour (``theme.Palette`` writes SGR codes); fzf reads those
with ``--ansi`` and draws its own pointer, current-line background and match highlights over them. The
built-in picker does the same here: :func:`parse` turns a string into (character, style) cells, and
:func:`render` writes them back out fitted to a width with the highlights and the current-row style
applied on top of what the row brought.
"""

from __future__ import annotations

import re

from .text import cell_width

_SGR = re.compile(r"\x1b\[([0-9;]*)m")
_OTHER = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[^[]")

Style = tuple[str, ...]     # SGR codes in force: "1", "2", "9", "38;2;r;g;b", "38;5;n", "48;5;n", …


def _codes(params: str) -> list[str]:
    """Split SGR parameters into codes, keeping a colour's arguments with it."""
    parts = params.split(";") if params else ["0"]
    out, i = [], 0
    while i < len(parts):
        p = parts[i] or "0"
        if p in ("38", "48") and i + 1 < len(parts):
            if parts[i + 1] == "2" and i + 4 < len(parts):
                out.append(";".join(parts[i:i + 5])); i += 5; continue
            if parts[i + 1] == "5" and i + 2 < len(parts):
                out.append(";".join(parts[i:i + 3])); i += 3; continue
        out.append(p); i += 1
    return out


def parse(text: str) -> list[tuple[str, Style]]:
    """The characters of ``text`` with the style each one is drawn in; colour codes consumed, other
    escapes dropped."""
    cells: list[tuple[str, Style]] = []
    style: tuple[str, ...] = ()
    pos = 0
    for m in _SGR.finditer(text):
        for ch in _OTHER.sub("", text[pos:m.start()]):
            cells.append((ch, style))
        pos = m.end()
        for code in _codes(m.group(1)):
            if code == "0":
                style = ()
            elif code == "22":
                style = tuple(c for c in style if c not in ("1", "2"))
            elif code == "39":
                style = tuple(c for c in style if not c.startswith("38;") and not (c.isdigit() and 30 <= int(c) <= 37))
            elif code == "49":
                style = tuple(c for c in style if not c.startswith("48;") and not (c.isdigit() and 40 <= int(c) <= 47))
            elif code.startswith("38;") or (code.isdigit() and 30 <= int(code) <= 37):
                style = tuple(c for c in style if not c.startswith("38;") and not (c.isdigit() and 30 <= int(c) <= 37)) + (code,)
            elif code.startswith("48;") or (code.isdigit() and 40 <= int(code) <= 47):
                style = tuple(c for c in style if not c.startswith("48;") and not (c.isdigit() and 40 <= int(c) <= 47)) + (code,)
            elif code not in style:
                style = style + (code,)
    for ch in _OTHER.sub("", text[pos:]):
        cells.append((ch, style))
    return cells


def plain(text: str) -> str:
    return "".join(ch for ch, _ in parse(text))


def has_fg(style: Style) -> bool:
    return any(c.startswith("38;") or (c.isdigit() and 30 <= int(c) <= 37) for c in style)


def emit(cells: list[tuple[str, Style]]) -> str:
    """The cells as text with the fewest SGR changes, ending in a reset when anything was styled."""
    out, cur = [], ()
    for ch, style in cells:
        if style != cur:
            out.append("\x1b[0m" if not style else "\x1b[0;" + ";".join(style) + "m")
            cur = style
        out.append(ch)
    if cur:
        out.append("\x1b[0m")
    return "".join(out)


def fit(cells: list[tuple[str, Style]], width: int, *, ellipsis: str = "…",
        fill: Style = ()) -> list[tuple[str, Style]]:
    """The cells clipped to ``width`` terminal cells (``ellipsis`` marking a cut) and padded to it with
    spaces in the ``fill`` style; a wide character that would straddle the edge is dropped."""
    total = sum(cell_width(ch) for ch, _ in cells)
    if total > width:
        room = width - cell_width(ellipsis)
        kept, w = [], 0
        for ch, style in cells:
            cw = cell_width(ch)
            if w + cw > room:
                break
            kept.append((ch, style)); w += cw
        kept.append((ellipsis, kept[-1][1] if kept else ()))
        cells, total = kept, w + cell_width(ellipsis)
    return cells + [(" ", fill)] * (width - total)


def styled(cells: list[tuple[str, Style]], *, highlight: set[int] | frozenset[int] = frozenset(),
           hl: str = "", current: bool = False) -> list[tuple[str, Style]]:
    """``highlight`` positions get the ``hl`` foreground code in place of their own; with ``current``
    the row is painted as the current one (``theme.CURRENT_FG`` for cells without a colour of their own,
    ``theme.CURRENT_BG`` under every cell)."""
    from .theme import CURRENT_BG, CURRENT_FG
    out = []
    for i, (ch, style) in enumerate(cells):
        if current:
            if "1" not in style:
                style = style + ("1",)                  # fzf bolds the whole current row
            if not has_fg(style):
                style = style + (CURRENT_FG,)
            style = tuple(c for c in style if not c.startswith("48;")) + (CURRENT_BG,)
        if i in highlight and hl:
            style = tuple(c for c in style if not c.startswith("38;") and not (c.isdigit() and 30 <= int(c) <= 37)) + (hl,)
        out.append((ch, style))
    return out
