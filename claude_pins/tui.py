"""The built-in picker: the same screens as fzf's, drawn by Python in a raw-mode terminal.

It runs when fzf is missing or too old (or ``--no-fzf``): the terminal goes into raw mode on the
alternate screen with mouse reporting on, each :class:`Screen` becomes a :class:`Session` that reads
events (``keys``), keeps the query, the cursor and the selection, matches the rows (``query``) and
redraws the whole screen from the top on every change. The chrome is fzf's under the options the fzf
backend passes: header first, the prompt line with the counter at its right, a gap row and the sticky
label rows, the ``>`` pointer, the ``▌`` marker and gutter, the current row on fzf's dark background,
matches in the theme's periwinkle, the preview pane below with a rounded border and its label, the
bottom border with the expired note, and the pane hidden under ten rows by the same arithmetic
(``screen.preview_fits``).

Keys are fzf's defaults: ctrl-a/e, home/end, left/right, backspace, del, ctrl-u/w, alt-b/f/d edit the
query; up/down, ctrl-p/n, ctrl-k/j, pgup/pgdn move, cycling at the ends; tab and shift-tab toggle a row;
shift-up/down scroll the pane; esc, ctrl-c, ctrl-g and ctrl-q leave; the keys in ``Screen.expect`` end
the screen with their name. Mouse: a click moves the cursor, a double-click accepts, a right click
toggles, the wheel moves over the list and scrolls over the pane (fzf's defaults), unless the keymap
binds those names to actions. The preview runs on a worker thread so the cursor never waits on ccusage;
a resize re-lays the rows out at the new width in process.

``ScriptedTerminal`` stands in for the terminal under ``CLAUDE_PINS_TUI_SCRIPT`` (the flow tests): it
feeds each screen the keys a step names and logs the frame the screen last drew.
"""

from __future__ import annotations

import fcntl
import json
import os
import queue
import select
import shutil
import signal
import struct
import subprocess
import sys
import termios
import threading
import tty

from . import ansi, config, hooks, keys, theme
from .keys import Key, Mouse, Reader, canonical
from .query import matches as query_matches
from .screen import Item, Result, Screen, alt_screen_up, entered_alt_screen, leave_screen, preview_fits, preview_rows
from .text import cell_width, cells

POINTER = ">"
MARKER = "▌"
GUTTER = "▌"
ELLIPSIS = "…"
INDENT = 2              # the pointer and marker columns; header and sticky rows are indented past them
PANE_PADDING = 2        # the pane's border and the space inside it, each side
ABORT_KEYS = {"esc", "ctrl-c", "ctrl-g", "ctrl-q"}

DIM = ("2",)
_hl = theme.sgr(theme.PERIWINKLE)
_prompt_style = ("1", theme.sgr(theme.CLAY))     # fzf draws the prompt and the query bold
_query_style = ("1",)
_pointer_style = ("1", theme.sgr(theme.PERIWINKLE), theme.CURRENT_BG)
_marker_style = (theme.sgr(theme.SUCCESS),)
_gutter_style = (theme.GUTTER_FG,)


# ---- terminals ---------------------------------------------------------------------------------------------

class Terminal:
    """The real terminal: raw mode on stdin, drawing on stdout, mouse reports, a resize signal."""

    def __init__(self):
        self.fd = sys.stdin.fileno()
        self.out = sys.stdout.fileno()
        self.saved = None
        self.wake_r, self.wake_w = os.pipe()
        os.set_blocking(self.wake_w, False)
        self.resized = False
        self._old_winch = None

    def size(self) -> tuple[int, int]:
        try:
            rows, cols = struct.unpack("HHHH", fcntl.ioctl(self.out, termios.TIOCGWINSZ, b"\0" * 8))[:2]
            if rows and cols:
                return rows, cols
        except OSError:
            pass
        size = shutil.get_terminal_size((100, 24))
        return size.lines, size.columns

    def enter(self) -> None:
        self.saved = termios.tcgetattr(self.fd)
        tty.setraw(self.fd)
        if not alt_screen_up():
            self.write("\x1b[?1049h")
        entered_alt_screen()
        self.write("\x1b[?1000h\x1b[?1006h")           # clicks and wheel, in the SGR form
        self._old_winch = signal.signal(signal.SIGWINCH, self._winch)

    def exit(self) -> None:
        self.write("\x1b[?1000l\x1b[?1006l\x1b[?25h")
        if self.saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
            self.saved = None
        if self._old_winch is not None:
            signal.signal(signal.SIGWINCH, self._old_winch)
            self._old_winch = None

    def close(self) -> None:
        for fd in (self.wake_r, self.wake_w):
            try:
                os.close(fd)
            except OSError:
                pass

    def _winch(self, *_):
        self.resized = True
        self.wake()

    def wake(self) -> None:
        try:
            os.write(self.wake_w, b"w")
        except OSError:
            pass

    def read(self, timeout: float | None) -> bytes | None:
        """Bytes from the keyboard; b"" on timeout; None when woken (a resize or a preview chunk)."""
        try:
            ready, _, _ = select.select([self.fd, self.wake_r], [], [], timeout)
        except InterruptedError:
            return None
        if self.wake_r in ready:
            try:
                os.read(self.wake_r, 4096)
            except OSError:
                pass
            return None
        if self.fd in ready:
            try:
                return os.read(self.fd, 4096)
            except OSError:
                return b""
        return b""

    def write(self, text: str) -> None:
        data = text.encode("utf-8", "replace")
        while data:
            try:
                n = os.write(self.out, data)
            except InterruptedError:
                continue
            data = data[n:]

    def suspend(self) -> None:
        """ctrl-z: hand the terminal back on the normal screen, stop, and take it again when continued."""
        self.exit()
        self.write("\x1b[?1049l")
        os.kill(os.getpid(), signal.SIGTSTP)
        self.write("\x1b[?1049h")
        self.enter()

    def async_preview(self) -> bool:
        return True


class ScriptedTerminal:
    """A terminal played from a file for the flow tests: ``CLAUDE_PINS_TUI_SCRIPT`` holds one JSON step per
    screen, ``{"send": ["navi", "@enter"]}``: text is typed, ``@name`` is a key (``@alt-t``, ``@down``),
    ``@click:ROW``, ``@dblclick:ROW``, ``@rclick:ROW`` and ``@wheel-up``/``@wheel-down`` (over the list;
    ``@pwheel-…`` over the pane) are mouse events, ``@resize:ROWSxCOLS`` changes the size, ``@pause`` is
    a moment with no input (so ``@esc`` before it is esc, not the start of alt-x). ``unlink`` and ``shell``
    run first, as in the fzf stub. When the steps run out the screen is abandoned, as esc would. Every
    screen appends what it was given and the last frame it drew to ``CLAUDE_PINS_TUI_LOG``."""

    def __init__(self, script: str, log: str | None):
        self.script, self.log = script, log
        self.rows = int(os.environ.get("LINES") or 24)
        self.cols = int(os.environ.get("COLUMNS") or 100)
        self.tokens: list[str] = []
        self.exhausted = False
        self.resized = False
        self.session: Session | None = None

    def size(self) -> tuple[int, int]:
        return self.rows, self.cols

    def next_step(self) -> dict:
        try:
            with open(self.script) as fh:
                steps = [ln for ln in fh.read().split("\n") if ln.strip()]
        except OSError:
            steps = []
        if not steps:
            self.exhausted = True
            return {}
        with open(self.script, "w") as fh:
            fh.write("\n".join(steps[1:]) + ("\n" if len(steps) > 1 else ""))
        step = json.loads(steps[0])
        if step.get("unlink"):
            os.unlink(step["unlink"])
        if step.get("shell"):
            subprocess.run(step["shell"], shell=True, check=True, capture_output=True)
        self.tokens = list(step.get("send", []))
        return step

    def _token(self, t: str) -> bytes | None:
        if not t.startswith("@"):
            return t.encode("utf-8")
        name, _, arg = t[1:].partition(":")
        if name == "pause":
            return None
        if name == "resize":
            r, c = arg.split("x")
            self.rows, self.cols = int(r), int(c)
            self.resized = True
            return None
        if name in ("click", "dblclick", "rclick"):
            s = self.session
            y = (s.list_top + s.sticky_count() + int(arg) - s.offset) if s else int(arg)   # the k-th match's row
            button = "right" if name == "rclick" else "left"
            report = keys.mouse_report(INDENT, y, button=button)
            return report + report if name == "dblclick" else report
        if name in ("wheel-up", "wheel-down", "pwheel-up", "pwheel-down"):
            s = self.session
            y = s.pane_top if name.startswith("p") and s else (s.list_top if s else 0)
            return keys.mouse_report(INDENT, y, scroll=1 if name.endswith("up") else -1)
        return keys.encode(name)

    def enter(self) -> None:
        entered_alt_screen()

    def exit(self) -> None:
        pass

    def read(self, timeout: float | None) -> bytes | None:
        if self.resized:
            return None
        if not self.tokens:
            self.exhausted = True
            return None
        data = self._token(self.tokens.pop(0))
        if self.resized:
            return None
        return data or b""

    def write(self, text: str) -> None:
        pass

    def wake(self) -> None:
        pass

    def suspend(self) -> None:
        pass

    def close(self) -> None:
        pass

    def async_preview(self) -> bool:
        return False

    def record(self, screen: Screen, frame: list[str], result: Result | None) -> None:
        if not self.log:
            return
        entry = {"prompt": screen.prompt, "header": ansi.plain(screen.header_text(self.cols, self.rows)),
                 "query": screen.query, "items": [f"{it.id}\t{ansi.plain(it.display)}" for it in screen.items],
                 "expect": list(screen.expect), "frame": [line.rstrip() for line in frame],
                 "result": None if result is None else {"key": result.key, "query": result.query, "ids": result.ids}}
        with open(self.log, "a") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def usable() -> bool:
    """Whether this process can run the built-in picker: a terminal on both ends that is not ``dumb``."""
    if os.environ.get("CLAUDE_PINS_TUI_SCRIPT"):
        return True
    try:
        return sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb"
    except (AttributeError, ValueError):
        return False


def _terminal():
    script = os.environ.get("CLAUDE_PINS_TUI_SCRIPT")
    if script:
        return ScriptedTerminal(script, os.environ.get("CLAUDE_PINS_TUI_LOG"))
    return Terminal()


def run_screen(screen: Screen) -> Result | None:
    """Draw ``screen`` in this terminal and wait for the user; None when the user left with esc, or
    when there is no terminal to ask on (a question from a script or a pipe cannot be answered)."""
    if not usable():
        print(f"pin: no terminal to answer {ansi.plain(screen.prompt).strip(' ›')}; cancelled", file=sys.stderr)
        return None
    term = _terminal()
    return Session(screen, term).run()


# ---- one screen ---------------------------------------------------------------------------------------------

class Session:
    def __init__(self, screen: Screen, term):
        self.screen = screen
        self.term = term
        self.items: list[Item] = list(screen.items)
        self.query = screen.query
        self.cx = len(self.query)                   # the cursor in the query
        self.matched: list[int] = []                # indices into self.items that match, in list order
        self.positions: dict[int, set[int]] = {}    # highlight positions per matched item
        self.cur = 0                                # index into self.matched
        self.offset = 0                             # first visible match
        self.selected: dict[int, int] = {}          # item index → selection order
        self.preview_text = ""
        self.preview_for: str | None = None         # the row id the pane shows
        self.preview_offset = 0
        self.preview_cache: dict[tuple[str, int], str] = {}
        self.preview_gen = 0
        self.preview_queue: queue.Queue = queue.Queue()
        self.killed = ""                            # ctrl-y's yank buffer
        self.frame: list[str] = []
        self.color = config.color_enabled(sys.stdout)        # off: attributes only, as fzf's --color=bw
        self.rows, self.cols = term.size()
        # layout, refreshed by draw()
        self.list_top = 0
        self.list_rows = 0
        self.pane_top = 0
        self.pane_rows = 0
        self._plain_cache: dict[int, str] = {}
        self.rematch()
        if screen.pos and 0 < screen.pos <= len(self.selectable()):
            want = self.selectable()[screen.pos - 1]
            if want in self.matched:
                self.cur = self.matched.index(want)

    # ---- rows -------------------------------------------------------------------------------------------

    def selectable(self) -> list[int]:
        return list(range(self.screen.header_lines, len(self.items)))

    def plain_of(self, i: int) -> str:
        if i not in self._plain_cache:
            self._plain_cache[i] = ansi.plain(self.items[i].display)
        return self._plain_cache[i]

    def rematch(self, *, keep: str | None = None) -> None:
        """Recompute the matches for the query; ``keep`` names the row the cursor stays on when it still
        matches (a reload), otherwise the cursor goes to the top, as fzf's does when the query changes."""
        before = keep
        self.matched, self.positions = [], {}
        for i in self.selectable():
            if self.screen.disabled or not self.query:
                self.matched.append(i)
                continue
            hit, pos = query_matches(self.query, self.plain_of(i), self.screen.nth)
            if hit:
                self.matched.append(i)
                self.positions[i] = pos
        self.cur = 0
        self.offset = 0
        if before is not None:
            for k, i in enumerate(self.matched):
                if self.items[i].id == before:
                    self.cur = k
                    break

    def current_index(self) -> int | None:
        return self.matched[self.cur] if self.matched else None

    def current_id(self) -> str | None:
        i = self.current_index()
        return self.items[i].id if i is not None else None

    def result_ids(self) -> list[str]:
        if self.selected:
            order = sorted(self.selected, key=self.selected.get)
            return [self.items[i].id for i in order]
        cid = self.current_id()
        return [cid] if cid is not None else []

    def replace_items(self, items: list[Item]) -> None:
        old_selected = {self.items[i].id for i in self.selected}
        cursor = self.current_id()
        self.items = list(items)
        self._plain_cache.clear()
        self.preview_cache.clear()
        self.rematch(keep=cursor)
        self.selected = {}
        for i in self.selectable():
            if self.items[i].id in old_selected:
                self.selected[i] = len(self.selected)

    # ---- the loop -----------------------------------------------------------------------------------------

    def run(self) -> Result | None:
        term = self.term
        if isinstance(term, ScriptedTerminal):
            term.session = self
            term.next_step()
        term.enter()
        reader = Reader(term.read)
        result: Result | None = None
        try:
            self.draw()
            while True:
                ev = reader.next(None)
                changed = False
                if self.term.resized:
                    self.term.resized = False
                    self.resize()
                    changed = True
                changed = self.take_preview() or changed
                if ev is None:
                    if isinstance(term, ScriptedTerminal) and term.exhausted:
                        break
                    if changed:         # a wake with nothing new (the worker's "done" after its last chunk) paints nothing
                        self.draw()
                    continue
                outcome = self.handle(ev)
                if outcome is not None:
                    if outcome == "abort":
                        break
                    result = outcome
                    break
                self.draw()
        finally:
            term.exit()
            term.close()
            if isinstance(term, ScriptedTerminal):
                term.record(self.screen, self.frame, result)
        return result

    def handle(self, ev) -> Result | str | None:
        if isinstance(ev, Mouse):
            return self.mouse(ev)
        name = canonical(ev.name)
        if name == "char":
            if ev.char == " " and "space" in self.screen.expect:
                return Result("space", self.query, self.result_ids())
            self.insert(ev.char)
            return None
        if name in self.screen.expect:
            return Result(name, self.query, self.result_ids())
        if name and name == self.screen.refresh_key:
            self.reload()
            return None
        if name in ABORT_KEYS:
            return "abort"
        if name == "enter":
            return Result("", self.query, self.result_ids())
        if name in ("up", "ctrl-p", "ctrl-k", "alt-up"):
            self.move(-1)
        elif name in ("down", "ctrl-n", "ctrl-j", "alt-down"):
            self.move(1)
        elif name == "pgup":
            self.move(-(self.max_items() - 1), cycle=False)
        elif name == "pgdn":
            self.move(self.max_items() - 1, cycle=False)
        elif name == "tab":                         # toggle+down, and nothing at all without --multi (fzf)
            if self.screen.multi and self.matched:
                self.toggle(); self.move(1, cycle=False)
        elif name == "shift-tab":
            if self.screen.multi and self.matched:
                self.toggle(); self.move(-1, cycle=False)
        elif name in ("shift-up", "preview-scroll-up"):
            self.scroll_preview(-1)
        elif name in ("shift-down", "preview-scroll-down"):
            self.scroll_preview(1)
        elif name in ("ctrl-a", "home"):
            self.cx = 0
        elif name in ("ctrl-e", "end"):
            self.cx = len(self.query)
        elif name in ("left", "ctrl-b"):
            self.cx = max(0, self.cx - 1)
        elif name in ("right", "ctrl-f"):
            self.cx = min(len(self.query), self.cx + 1)
        elif name in ("bspace", "ctrl-h"):
            if self.cx:
                self.edit(self.query[:self.cx - 1] + self.query[self.cx:], self.cx - 1)
        elif name in ("del", "ctrl-d"):
            if name == "ctrl-d" and not self.query:
                return "abort"
            if self.cx < len(self.query):
                self.edit(self.query[:self.cx] + self.query[self.cx + 1:], self.cx)
        elif name == "ctrl-u":
            self.killed = self.query[:self.cx]
            self.edit(self.query[self.cx:], 0)
        elif name in ("ctrl-w", "alt-bspace"):
            start = self.word_start()
            self.killed = self.query[start:self.cx]
            self.edit(self.query[:start] + self.query[self.cx:], start)
        elif name == "alt-d":
            end = self.word_end()
            self.killed = self.query[self.cx:end]
            self.edit(self.query[:self.cx] + self.query[end:], self.cx)
        elif name in ("alt-b", "shift-left"):
            self.cx = self.word_start()
        elif name in ("alt-f", "shift-right"):
            self.cx = self.word_end()
        elif name == "ctrl-y":
            self.insert(self.killed)
        elif name == "ctrl-z":
            self.term.suspend()
        return None

    # ---- editing ---------------------------------------------------------------------------------------------

    def insert(self, text: str) -> None:
        if text:
            self.edit(self.query[:self.cx] + text + self.query[self.cx:], self.cx + len(text))

    def edit(self, query: str, cx: int) -> None:
        if query == self.query:
            self.cx = cx
            return
        self.query, self.cx = query, cx
        if self.screen.on_change:
            self.replace_items(hooks.rows(self.screen.on_change, self.query, self.cols))
        else:
            self.rematch()

    def word_start(self) -> int:
        i = self.cx
        while i > 0 and self.query[i - 1] == " ":
            i -= 1
        while i > 0 and self.query[i - 1] != " ":
            i -= 1
        return i

    def word_end(self) -> int:
        i, n = self.cx, len(self.query)
        while i < n and self.query[i] == " ":
            i += 1
        while i < n and self.query[i] != " ":
            i += 1
        return i

    # ---- movement and selection -------------------------------------------------------------------------------

    def max_items(self) -> int:
        return max(1, self.list_rows - self.sticky_count())

    def sticky_count(self) -> int:
        return 1 + self.screen.header_lines          # the gap row, then the labels

    def move(self, delta: int, *, cycle: bool = True) -> None:
        """fzf's vmove: wrap around only from the last row onward (or the first backward)."""
        n = len(self.matched)
        if not n:
            return
        dest = self.cur + delta
        if cycle:
            if dest > n - 1 and self.cur == n - 1:
                dest = 0
            elif dest < 0 and self.cur == 0:
                dest = n - 1
        self.cur = max(0, min(n - 1, dest))

    def toggle(self) -> None:
        if not self.screen.multi:
            return
        i = self.current_index()
        if i is None:
            return
        if i in self.selected:
            del self.selected[i]
        else:
            self.selected[i] = (max(self.selected.values()) + 1) if self.selected else 0

    def scroll_preview(self, delta: int) -> None:
        if not self.pane_rows:
            return
        lines = self.pane_lines(self.cols - 2 * PANE_PADDING)
        inner = self.pane_rows - 2
        top = max(0, len(lines) - inner)
        self.preview_offset = max(0, min(top, self.preview_offset + delta))

    def mouse(self, ev: Mouse) -> Result | str | None:
        expect = self.screen.expect
        if ev.scroll:
            if self.pane_rows and self.pane_top <= ev.y < self.pane_top + self.pane_rows:
                name = "preview-scroll-up" if ev.scroll > 0 else "preview-scroll-down"
                if name in expect:
                    return Result(name, self.query, self.result_ids())
                self.scroll_preview(-1 if ev.scroll > 0 else 1)
            elif self.list_top <= ev.y < self.list_top + self.list_rows:
                name = "scroll-up" if ev.scroll > 0 else "scroll-down"
                if name in expect:
                    return Result(name, self.query, self.result_ids())
                self.move(-1 if ev.scroll > 0 else 1, cycle=False)
            return None
        if not ev.down:
            return None
        if ev.y == self.list_top - 1:               # the prompt line: the query cursor follows the click
            self.cx = max(0, min(len(self.query), ev.x - cells(self.screen.prompt)))
            return None
        k = self.row_at(ev.y)
        if k is None:
            return None
        self.cur = k
        if ev.double:
            if "double-click" in expect:
                return Result("double-click", self.query, self.result_ids())
            return Result("", self.query, self.result_ids())
        name = "right-click" if ev.button == "right" else "left-click"
        if name in expect:
            return Result(name, self.query, self.result_ids())
        if ev.button == "right" or ev.shift:
            self.toggle()
        return None

    def row_at(self, y: int) -> int | None:
        """The index into ``matched`` drawn on terminal row ``y``, if a row is."""
        first = self.list_top + self.sticky_count()
        k = self.offset + (y - first)
        if y < first or k < 0 or k >= len(self.matched) or y >= self.list_top + self.list_rows:
            return None
        return k

    # ---- reload, resize, preview ---------------------------------------------------------------------------------

    def resize(self) -> None:
        self.rows, self.cols = self.term.size()
        if self.screen.reload:
            self.replace_items(hooks.rows(self.screen.reload, self.query, self.cols))
        self.preview_cache.clear()
        self.preview_for = None

    def reload(self) -> None:
        if self.screen.reload:
            self.replace_items(hooks.rows(self.screen.reload, self.query, self.cols))

    def want_preview(self) -> None:
        """Ask for the pane's text for the row under the cursor, unless it is already there or on the way."""
        if not self.screen.preview or not self.pane_rows:
            return
        cid = self.current_id() if self.matched else ""
        cid = cid or ""
        if cid == self.preview_for:
            return
        self.preview_for = cid
        self.preview_offset = 0
        width = self.cols - 2 * PANE_PADDING
        cached = self.preview_cache.get((cid, width))
        if cached is not None:
            self.preview_text = cached
            return
        self.preview_text = ""
        self.preview_gen += 1
        gen, hook = self.preview_gen, self.screen.preview

        def work():
            text = ""
            try:
                for chunk in hooks.preview_text(hook, cid, width):
                    text += chunk
                    self.preview_queue.put((gen, cid, width, text, False))
                    self.term.wake()
            except Exception as e:  # noqa: BLE001 - the pane says what went wrong rather than the picker dying
                text += f"preview failed: {e}\n"
            self.preview_queue.put((gen, cid, width, text, True))
            self.term.wake()

        if self.term.async_preview():
            threading.Thread(target=work, daemon=True).start()
        else:
            work()
            self.take_preview()

    def take_preview(self) -> bool:
        """Take what the worker has posted; whether the pane's text changed. The worker posts the whole text
        after every chunk and once more when it is done, so the last post repeats the one before it."""
        changed = False
        while True:
            try:
                gen, cid, width, text, done = self.preview_queue.get_nowait()
            except queue.Empty:
                return changed
            if gen != self.preview_gen:
                continue
            if text != self.preview_text:
                self.preview_text = text
                changed = True
            if done:
                self.preview_cache[(cid, width)] = text

    # ---- drawing ----------------------------------------------------------------------------------------------------

    def layout(self) -> tuple[list[str], int]:
        """Where everything goes for the current size: the header lines, and the rows the list gets."""
        rows, cols = self.rows, self.cols
        header = self.screen.header_text(cols, rows).split("\n") if self.screen.header else []
        bottom = rows - (1 if self.screen.bottom_border else 0)
        self.pane_rows = 0
        if self.screen.preview and preview_fits(rows, bottom_border=self.screen.bottom_border):
            self.pane_rows = preview_rows(rows, bottom_border=self.screen.bottom_border)
        self.pane_top = bottom - self.pane_rows
        self.list_top = len(header) + 1
        self.list_rows = max(0, self.pane_top - self.list_top)
        return header, bottom

    def draw(self) -> None:
        header, bottom = self.layout()
        self.want_preview()
        rows, cols = self.rows, self.cols
        lines: list[str] = []
        for h in header:
            lines.append(self.fit(" " * INDENT + h, cols))
        lines.append(self.prompt_line(cols))
        # the list: the gap row, the labels, then the matches from the offset
        visible = self.max_items()
        if self.cur < self.offset:
            self.offset = self.cur
        elif self.cur >= self.offset + visible:
            self.offset = self.cur - visible + 1
        body: list[str] = [" " * cols]
        for i in range(self.screen.header_lines):
            body.append(self.fit(" " * INDENT + self.items[i].display.replace("\t", " "), cols))
        overflow = len(self.matched) > visible
        bar = self.scrollbar(len(self.matched), visible, self.offset) if overflow else None
        width = cols - INDENT - 1                   # the last column is the scrollbar's, drawn or not
        for n, k in enumerate(range(self.offset, min(len(self.matched), self.offset + visible))):
            i = self.matched[k]
            current = k == self.cur
            row = ansi.parse(self.items[i].display)
            row = ansi.styled(row, highlight=self.positions.get(i, frozenset()), hl=_hl, current=current)
            row = [(" " if ch == "\t" else ch, st) for ch, st in row]      # a tab draws as one space (--tabstop=1)
            # the current row's background covers the pointer, the marker and the text, not the padding
            bg = (theme.CURRENT_BG,) if current else ()
            gutter = [(POINTER, _pointer_style)] if current else [(GUTTER, _gutter_style)]
            mark = [(MARKER, _marker_style + bg)] if i in self.selected else [(" ", bg)]
            cells_ = gutter + mark + ansi.fit(row, width, ellipsis=ELLIPSIS)
            if overflow and bar[0] <= n < bar[0] + bar[1]:
                cells_.append(("│", DIM))
            body.append(self.emit(cells_))
        while len(body) < self.list_rows:
            body.append("")
        lines += body[:self.list_rows]
        if self.pane_rows:
            lines += self.pane(cols)
        if self.screen.bottom_border:
            lines.append(self.footer(cols))
        lines = lines[:rows]
        while len(lines) < rows:
            lines.append("")
        self.frame = [ansi.plain(line) for line in lines]
        out = ["\x1b[?25l\x1b[H"]
        for r, line in enumerate(lines):
            out.append(f"\x1b[{r + 1};1H{line}\x1b[K")
        cx = cells(self.screen.prompt) + cells(self.query[:self.cx])
        out.append(f"\x1b[{len(header) + 1};{cx + 1}H\x1b[?25h")
        self.term.write("".join(out))

    def emit(self, cells_: list) -> str:
        return ansi.emit(cells_, color=self.color)

    def fit(self, text: str, width: int, *, fill=()) -> str:
        return self.emit(ansi.fit(ansi.parse(text), width, ellipsis=ELLIPSIS, fill=fill))

    def prompt_line(self, cols: int) -> str:
        left = [(ch, _prompt_style) for ch in self.screen.prompt] + [(ch, _query_style) for ch in self.query]
        info = self.counter()
        if not info:
            return self.emit(ansi.fit(left, cols))
        # the counter ends one cell before the right edge, as fzf's inline-right does
        room = max(0, cols - cells(info) - 2)
        return self.emit(ansi.fit(left, room) + [(" ", ())] + [(ch, DIM) for ch in info] + [(" ", ())])

    def counter(self) -> str:
        if not self.screen.counter:
            return ""
        noun = self.screen.counter
        total = len(self.selectable())
        if total == 1:
            noun = noun.rstrip("s")
        text = f"{total} {noun}" if len(self.matched) == total else f"{len(self.matched)} of {total} {noun}"
        if self.selected:
            text += f" · {len(self.selected)} selected"
        return text

    @staticmethod
    def scrollbar(total: int, height: int, offset: int) -> tuple[int, int]:
        """fzf's getScrollbar: (first row, rows) of the bar for a list of ``total`` in ``height`` rows."""
        if total == 0 or total <= height:
            return 0, 0
        length = max(1, height * height // total)
        start = 0 if total == height else min(height - length, (height - length) * offset // (total - height))
        return start, length

    def pane_lines(self, width: int) -> list[str]:
        """The pane's text wrapped to its inner width, one entry per drawn line (colour kept)."""
        out: list[str] = []
        for line in self.preview_text.split("\n"):
            cells_ = ansi.parse(line)
            if not cells_:
                out.append("")
                continue
            while cells_:
                w, n = 0, 0
                while n < len(cells_) and w + cell_width(cells_[n][0]) <= width:
                    w += cell_width(cells_[n][0]); n += 1
                if n == 0:
                    n = 1
                out.append(self.emit(cells_[:n]))
                cells_ = cells_[n:]
        while out and out[-1] == "":
            out.pop()
        return out

    def pane(self, cols: int) -> list[str]:
        inner = cols - 2 * PANE_PADDING
        if self.screen.label_from_row:
            label = f" {self.current_id() or ''} "
        else:
            label = self.screen.preview_label
        top = ["─"] * (cols - 2)
        if label:
            lw = cells(label)
            start = max(0, (cols - 2 - lw) // 2)
            top = top[:start] + list(label) + top[start + lw:]
            top = top[:cols - 2]
        dim = self.emit([(ch, DIM) for ch in "╭" + "".join(top) + "╮"])
        lines = [dim]
        body = self.pane_lines(inner)
        height = self.pane_rows - 2
        top_line = max(0, min(self.preview_offset, max(0, len(body) - height)))
        self.preview_offset = top_line
        bar = self.scrollbar(len(body), height, top_line) if len(body) > height else None
        side = self.emit([("│", DIM)])
        for n in range(height):
            k = top_line + n
            text = body[k] if k < len(body) else ""
            # the scrollbar takes the padding column inside the right border, as fzf draws it
            track = side if bar and bar[0] <= n < bar[0] + bar[1] else " "
            row = ansi.fit(ansi.parse(text), inner, ellipsis=ELLIPSIS)
            if n == 0 and bar:                          # fzf's position over the first row: ``3/17``
                where = f"{top_line + 1}/{len(body)}"
                row = row[:inner - cells(where)] + [(ch, DIM) for ch in where]
            lines.append(side + " " + self.emit(row) + track + side)
        lines.append(self.emit([(ch, DIM) for ch in "╰" + "─" * (cols - 2) + "╯"]))
        return lines[:self.pane_rows]

    def footer(self, cols: int) -> str:
        label = self.screen.footer
        line = ["─"] * cols
        lw = cells(label)
        line = line[:2] + list(label) + line[2 + lw:]
        return self.emit([(ch, DIM) for ch in "".join(line)[:cols]])


# ---- pin _keys -------------------------------------------------------------------------------------------------

def key_check() -> int:
    """Print the name of every key and mouse event until esc twice or ctrl-c."""
    if not usable():
        print("pin _keys needs a terminal", file=sys.stderr)
        return 1
    term = Terminal()
    term.enter()
    reader = Reader(term.read)
    escapes = 0
    try:
        term.write("\x1b[H\x1b[2J")
        term.write("press keys and click; every event is named as the picker reads it · esc twice or ctrl-c ends\r\n\r\n")
        while True:
            ev = reader.next(None)
            if ev is None:
                continue
            term.write(f"{ev}\r\n")
            if isinstance(ev, Key) and ev.name == "ctrl-c":
                return 0
            escapes = escapes + 1 if isinstance(ev, Key) and ev.name == "esc" else 0
            if escapes == 2:
                return 0
    finally:
        term.exit()
        term.close()
        leave_screen(force=True)
