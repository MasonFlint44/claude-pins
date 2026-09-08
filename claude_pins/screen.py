"""What a screen is, apart from what draws it.

Two backends draw the picker's screens: fzf when it is on PATH and new enough (``claude_pins.fzf``), and
the built-in picker otherwise (``claude_pins.tui``). Both take a :class:`Screen`: the rows, the prompt, the
header parts, the keys that end it, the query, where the cursor starts, whether rows can be multi-selected,
which columns the query matches, and the hooks that run while it is up (the preview, a reload, the rows
for the query as typed). Both answer with a :class:`Result`, or None for esc. The picker, the editor and
the prompts build screens and call :func:`show`; nothing there knows which backend drew it.

Hooks are named, not closures: fzf runs commands, so its backend lowers each to a ``pins _<hook>``
subprocess, while the built-in picker runs the same function in process (``claude_pins.hooks`` holds
them; the ``pins`` subcommands call the same functions, so a reload draws what a launch would).
"""

from __future__ import annotations

import contextlib
import sys
from dataclasses import dataclass, field

from . import theme
from .text import cells


@dataclass
class Result:
    key: str            # "" for enter
    query: str
    ids: list[str]      # first field of every selected line


@dataclass
class Item:
    id: str
    display: str    # what the screen shows and, minus colour codes, what the query matches


@dataclass(frozen=True)
class Hook:
    """A command a screen runs while it is up. ``preview``/``spreview`` take the row under the cursor,
    ``draft`` the draft file, ``rows`` the sort and whether expired pins show, ``dirs`` the query."""
    name: str
    args: tuple[str, ...] = ()


# ---- the preview pane's size rule -----------------------------------------------------------------

# The pane sits below the list and hides itself when it would get fewer than ten rows (fzf measures the
# pane's own rows, bottom border included, and re-checks on every resize; the built-in picker does the
# same arithmetic).
PREVIEW_MIN_ROWS = 10
PREVIEW_SHARE = 55


def preview_fits(lines: int, *, bottom_border: bool = False) -> bool:
    """fzf's threshold arithmetic for the pane, so both backends hide it on the same terminal: the pane
    gets 55% of the rows left after the bottom border, and hides under ten."""
    return preview_rows(lines, bottom_border=bottom_border) >= PREVIEW_MIN_ROWS


def preview_rows(lines: int, *, bottom_border: bool = False) -> int:
    """The rows the pane takes on a terminal of ``lines``, borders included."""
    return (lines - (1 if bottom_border else 0)) * PREVIEW_SHARE // 100


def min_lines(*, bottom_border: bool = False) -> int:
    """The shortest terminal on which the pane still shows (the same arithmetic, solved for the height)."""
    lines = PREVIEW_MIN_ROWS
    while not preview_fits(lines, bottom_border=bottom_border):
        lines += 1
    return lines


# ---- the header ---------------------------------------------------------------------------------------

HEADER_GAP = 4          # the least space between the legend and the hints on one line
HEADER_MARGIN = 4       # fzf's two-cell header indent plus the two cells it cuts at the right edge (measured)


@dataclass
class Header:
    """The lines above the prompt: the legend on the left and the hints right-justified on one line when
    the width allows ``HEADER_GAP`` between them, else the legend over the hints (hints alone stay left);
    then ``extra``; then the status line, which is the flash for one screen, the too-short note, or a
    space that keeps the prompt off the header. ``text()`` is the layout for a size; fzf re-fits it
    itself on resize through ``fzf.header_transform()`` (the same arithmetic in shell, fed by ``env()``),
    the built-in picker by calling ``text()`` again. A line holding one space is kept where fzf would
    drop a trailing newline."""
    hints: str
    legend: str = ""
    extra: tuple[str, ...] = ()
    status: str = " "
    note: str = ""
    color: theme.Palette | None = None

    def _dim(self, text: str) -> str:
        return self.color(text, "dim") if self.color else text

    def env(self) -> dict[str, str]:
        from .fzf import EXTRA_VAR, HINTS_CELLS_VAR, HINTS_VAR, LEGEND_CELLS_VAR, LEGEND_VAR, NOTE_VAR, STATUS_VAR
        return {HINTS_VAR: self._dim(self.hints), HINTS_CELLS_VAR: str(cells(self.hints)),
                LEGEND_VAR: self._dim(self.legend), LEGEND_CELLS_VAR: str(cells(self.legend)),
                EXTRA_VAR: "\n".join(self.extra), STATUS_VAR: self.status, NOTE_VAR: self.note}

    def text(self, columns: int | None = None, lines: int | None = None, *, bottom_border: bool = False) -> str:
        if columns is None or lines is None:
            from .render import terminal_height, terminal_width
            columns = terminal_width() if columns is None else columns
            lines = terminal_height() if lines is None else lines
        out = []
        pad = columns - cells(self.legend) - cells(self.hints) - HEADER_MARGIN
        if self.legend and pad >= HEADER_GAP:
            out.append(self._dim(self.legend) + " " * pad + self._dim(self.hints))
        elif self.legend:
            out += [self._dim(self.legend), self._dim(self.hints)]
        else:
            out.append(self._dim(self.hints))
        out += self.extra
        short = self.note and lines < min_lines(bottom_border=bottom_border)
        out.append(self.note if short else self.status)
        return "\n".join(out)


# ---- a screen ---------------------------------------------------------------------------------------------

@dataclass
class Screen:
    items: list[Item]
    prompt: str
    header: Header | None = None
    expect: list[str] = field(default_factory=list)   # keys that end the screen besides enter and esc
    query: str = ""
    multi: bool = False
    pos: int | None = None          # 1-based row the cursor starts on, among the selectable rows
    header_lines: int = 0           # leading items that are sticky label rows: shown, never matched or chosen
    disabled: bool = False          # the query is a text field, not a filter: every row stays
    nth: str | None = None          # the tab-separated columns the query matches, as fzf's --nth ("1..3")
    counter: str = ""               # the noun of ``3 of 4 pins · 2 selected`` at the prompt's right; "" hides it
    preview: Hook | None = None     # the pane's text for the row under the cursor
    preview_label: str = ""         # the pane's title; with ``label_from_row`` it is the row's id instead
    label_from_row: bool = False
    footer: str = ""                # the bottom border's label (the expired note); empty means no border
    reload: Hook | None = None      # the rows again, on ``refresh_key`` and on resize
    refresh_key: str = ""
    on_change: Hook | None = None   # the rows for the query as typed (the directory field)

    @property
    def bottom_border(self) -> bool:
        return bool(self.footer)

    def header_text(self, columns: int | None = None, lines: int | None = None) -> str:
        return self.header.text(columns, lines, bottom_border=self.bottom_border) if self.header else ""


def show(screen: Screen) -> Result | None:
    """Draw ``screen`` with whichever backend this terminal gets and wait for the user."""
    from . import fzf
    if fzf.available():
        return fzf.run_screen(screen)
    from . import tui
    return tui.run_screen(screen)


# ---- the alternate screen ---------------------------------------------------------------------------------

_alt_screen = False     # a backend left the terminal on the alternate screen
_held = 0               # depth of hold_screen(): screens that want the next one to draw over this one


@contextlib.contextmanager
def hold_screen():
    """Keep the alternate screen up between the screens inside this block (the picker's loop, the
    editor's), so they draw over each other; leaving the outermost block returns to the normal screen.
    A prompt run outside any hold (``pins prune``, ``pins edit``) drops the screen as soon as it ends,
    so what the command prints afterwards is seen."""
    global _held
    _held += 1
    try:
        yield
    finally:
        _held -= 1
        if _held == 0:
            leave_screen()


def screen_held() -> bool:
    return _held > 0


def alt_screen_up() -> bool:
    return _alt_screen


def entered_alt_screen() -> None:
    """A backend has drawn on the alternate screen and left it up for the next one."""
    global _alt_screen
    _alt_screen = True


def leave_screen(*, force: bool = False) -> None:
    """Return to the normal screen if a backend left the alternate one up and no screen holds it
    (``force`` ignores holds: exec and every way out of ``main``). Both backends restore everything else
    themselves on exit (cooked mode, cursor, mouse tracking), so this one sequence is the whole restore.
    Idempotent."""
    global _alt_screen
    if not _alt_screen or (_held and not force):
        return
    _alt_screen = False
    seq = "\x1b[?1049l"
    try:
        if sys.stderr.isatty():
            sys.stderr.write(seq); sys.stderr.flush()
        elif sys.stdin.isatty():
            with open("/dev/tty", "w") as tty:
                tty.write(seq)
    except (OSError, ValueError):
        pass
