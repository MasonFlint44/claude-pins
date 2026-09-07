"""Terminal rendering: colors, symbols, picker rows, preview text."""

from __future__ import annotations

import os
import shutil
import textwrap
import time
from dataclasses import dataclass, field

from . import config
from .cost import Cost, format_tokens
from .gitutil import split_worktree_path
from .model import Pin
from .sessions import Expiry, format_age
from .text import cell_width, cells, clip, pad
from .transcript import Summary, shorten

SYM_OPEN, SYM_KEEP, SYM_FORK, SYM_WORKTREE, SYM_EXPIRING, SYM_EXPIRED = "●", "⚑", "⑂", "⌂", "⏳", "✗"
LEGEND = f"{SYM_OPEN} open  {SYM_KEEP} keep  {SYM_FORK} fork  {SYM_WORKTREE} worktree  {SYM_EXPIRING} expiring  {SYM_EXPIRED} expired"
LOGO = "📌"


def crumb(*parts: str) -> str:
    """The prompt breadcrumb every screen shares: ``📌 pins › alias › edit › ``."""
    return f"{LOGO} " + "".join(f"{p} › " for p in ("pins", *parts))

_CODES = {
    "green": "32", "yellow": "33", "red": "31", "cyan": "36", "magenta": "35", "blue": "34",
    "dim": "2", "bold": "1", "strike": "9", "reset": "0",
}


class Palette:
    """ANSI coloring that turns into a no-op under ``NO_COLOR``."""

    def __init__(self, enabled: bool):
        self.enabled = enabled

    def __call__(self, text: str, *styles: str) -> str:
        if not self.enabled or not styles or not text:
            return text
        codes = ";".join(_CODES[s] for s in styles)
        return f"\x1b[{codes}m{text}\x1b[0m"


def palette(stream=None) -> Palette:
    return Palette(config.color_enabled(stream))


def terminal_width(default: int = 100) -> int:
    """Columns: the preview pane's, else fzf's own (0.46+, so a reload fits the resized terminal), else the
    shell's, else the terminal's."""
    env = os.environ.get("FZF_PREVIEW_COLUMNS") or os.environ.get("FZF_COLUMNS") or os.environ.get("COLUMNS")
    if env and env.isdigit():
        return int(env)
    return shutil.get_terminal_size((default, 24)).columns


def terminal_height(default: int = 24) -> int:
    env = os.environ.get("LINES")
    if env and env.isdigit():
        return int(env)
    return shutil.get_terminal_size((100, default)).lines


def grouped(rows: list[tuple[str, str, str]], color: Palette | None = None, *, gap: int = 2) -> list[str]:
    """Lines for a (group, label, key) table: the group name sits in a dim gutter on the first row of its
    group, so every row is a real entry and nothing separates them; the key column starts after the
    longest label. Consecutive rows with the same group share the gutter."""
    color = color or Palette(False)
    gutter_w = max((cells(g) for g, _, _ in rows), default=0)
    label_w = max((cells(l) for _, l, _ in rows), default=0)
    out, seen = [], None
    for group, label, key in rows:
        name = "" if group == seen else group
        seen = group
        gutter = (color(pad(name, gutter_w), "dim") + " " * gap) if gutter_w else ""
        out.append((gutter + pad(label, label_w) + " " * gap + key).rstrip())
    return out


def short_model(model: str) -> str:
    return model[len("claude-"):] if model.startswith("claude-") else (model or "")


WORKTREE_SEP = " › "


def display_dir(cwd: str) -> str:
    """``~``-relative; a Claude worktree renders as ``~/git/foo › name`` (the statusline's breadcrumb)."""
    if not cwd:
        return ""
    wt = split_worktree_path(cwd)
    if wt:
        return f"{config.tilde(wt[0])}{WORKTREE_SEP}{wt[1]}"
    return config.tilde(cwd)


def _abbreviate(component: str) -> str:
    """fish-style: the first character, two for dot-directories (``.claude`` → ``.c``)."""
    if component.startswith(".") and len(component) > 1:
        return component[:2]
    return component[:1]


def fit_dir(text: str, width: int) -> str:
    """Shorten a displayed directory to ``width`` cells: leading components abbreviate fish-style
    (``~/g/c/claude-pins``) one at a time from the left, the last component stays whole, and only when
    that is still too wide is the start cut off. The end of a path is what tells directories apart."""
    if cells(text) <= width:
        return text
    head, sep, tail = text.partition(WORKTREE_SEP)
    parts = head.split("/")
    for i in range(len(parts) - 1):
        if parts[i] in ("", "~"):
            continue
        parts[i] = _abbreviate(parts[i])
        candidate = "/".join(parts) + sep + tail
        if cells(candidate) <= width:
            return candidate
    return clip("/".join(parts) + sep + tail, width, left=True)


@dataclass
class View:
    """A pin plus the state the picker renders: expiry, open flag, summary."""

    pin: Pin
    expiry: Expiry
    is_open: bool = False
    summary: Summary | None = None
    markers: str = field(init=False, default="")

    def __post_init__(self):
        m = []
        if self.is_open:
            m.append(SYM_OPEN)
        if self.pin.keep:
            m.append(SYM_KEEP)
        if self.pin.fork:
            m.append(SYM_FORK)
        if self.pin.worktree:
            m.append(SYM_WORKTREE)
        if self.expiry.expired:
            m.append(SYM_EXPIRED)
        elif self.expiry.expiring:
            m.append(SYM_EXPIRING)
        self.markers = " ".join(m)

    @property
    def age(self) -> str:
        return "" if self.expiry.expired else format_age(self.expiry.age_seconds)

    @property
    def title(self) -> str:
        return self.pin.title or (self.summary.title if self.summary else "") or "untitled"


@dataclass
class Columns:
    """Column widths for one rendering of the pin table, in cells."""

    alias: int
    title: int
    dir: int
    age: int
    markers: int
    number: int = 0     # digits of the row number in the plain menu, 0 = not numbered


TITLE_FLOOR = 16
MIN_COLUMN = 8
AGE_WIDTH = 4           # "999d"


def layout(views: list[View], width: int, *, numbered: bool = False) -> Columns:
    """Alias and directory are as wide as their longest value (at least their label), capped near a fifth
    and a third of the width; age and markers are fixed; the title takes what is left, and when that falls
    under its floor the directory gives way first (its end still tells directories apart), then the alias."""
    alias_w = min(max(max(cells(v.pin.alias) for v in views), len("alias")), max(MIN_COLUMN, width // 5))
    dir_w = min(max(max(cells(display_dir(v.pin.cwd)) for v in views), len("dir")), max(MIN_COLUMN, width // 3))
    mark_w = max(cells(v.markers) for v in views)
    num_w = len(str(len(views))) if numbered else 0

    def title_room() -> int:
        gaps = 2 + 2 + 2 + (2 if mark_w else 0) + (2 if num_w else 0)
        return width - (num_w + alias_w + dir_w + AGE_WIDTH + mark_w + gaps)

    while title_room() < TITLE_FLOOR and dir_w > MIN_COLUMN:
        dir_w -= 1
    while title_room() < TITLE_FLOOR and alias_w > MIN_COLUMN:
        alias_w -= 1
    return Columns(alias_w, max(TITLE_FLOOR, title_room()), dir_w, AGE_WIDTH, mark_w, num_w)


def label_row(cols: Columns, color: Palette | None = None) -> str:
    """The column labels, laid out exactly like a row; the picker shows it as fzf's sticky header line."""
    color = color or Palette(False)
    num = " " * (cols.number + 2) if cols.number else ""
    directory = "directory" if cols.dir >= len("directory") else "dir"
    line = f"{pad('alias', cols.alias)}  {pad('title', cols.title)}  {pad(directory, cols.dir)}  {pad('idle', cols.age, '>')}"
    return num + color(line.rstrip(), "dim")


def rows(views: list[View], width: int | None = None, color: Palette | None = None,
         numbered: bool = False, cols: Columns | None = None) -> list[str]:
    """One rendered line per view, columns sized to the terminal width."""
    width = width or terminal_width()
    color = color or Palette(False)
    if not views:
        return []
    cols = cols or layout(views, width, numbered=numbered)
    alias_w, title_w, dir_w, age_w, mark_w, num_w = (cols.alias, cols.title, cols.dir, cols.age, cols.markers,
                                                      cols.number)
    out = []
    for i, v in enumerate(views, 1):
        num = pad(f"{i}", num_w, ">") + "  " if numbered else ""
        alias = pad(v.pin.alias, alias_w)
        title = pad(v.title, title_w)
        d = pad(fit_dir(display_dir(v.pin.cwd), dir_w), dir_w)
        age = pad(v.age, age_w, ">")
        marks = ("  " + pad(v.markers, mark_w)) if mark_w else ""
        if v.expiry.expired:
            line = color(f"{alias}  {title}  {d}  {age}", "dim", "strike") + color(marks.rstrip(), "dim")
        else:
            age_c = color(age, "yellow") if v.expiry.expiring else color(age, "dim")
            marks_c = marks
            if color.enabled and marks:
                marks_c = marks.replace(SYM_OPEN, color(SYM_OPEN, "green")).replace(SYM_EXPIRING, color(SYM_EXPIRING, "yellow"))
            line = f"{color(alias, 'bold')}  {title}  {color(d, 'dim')}  {age_c}{marks_c}"
        out.append(num + line.rstrip())
    return out


PENDING = Cost("pending")            # sentinel: render a placeholder line, filled in by stream_preview
COST_PLACEHOLDER = "\x00cost\x00"


def stream_preview(view: View, compute_cost, current_branch: str | None = None, width: int | None = None,
                   color: Palette | None = None, out=None) -> None:
    """Print the preview with everything but the cost line first, then the cost once computed.

    fzf renders preview output as it arrives, so the pane fills instantly even when the cost
    lookup has to go online.
    """
    import sys as _sys
    out = out or _sys.stdout
    color = color or Palette(False)
    text = preview(view, PENDING, current_branch, width, color)
    before, _, after = text.partition(COST_PLACEHOLDER)
    out.write(before)
    out.flush()
    cost = compute_cost()
    s = view.summary or Summary(exists=False)
    out.write(f"{color(pad('cost', LABEL_WIDTH), 'dim')} {cost.line(short_model(s.model), s.messages)}")
    out.write(after + "\n")
    out.flush()


def _date(iso: str) -> str:
    return iso[:10] if iso else ""


LABEL_WIDTH = 10


def format_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def branch_line(recorded: str, current: str | None, cwd_exists: bool, color: Palette) -> str:
    """The branch row. ``recorded`` is what the transcript says (empty when the session ran outside git or
    on a detached HEAD); ``current`` is what is checked out now (None when the directory is not a repo)."""
    recorded = "" if recorded in ("", "HEAD", "(detached)") else recorded
    if current is None:
        if not recorded:
            return "(not a git repo)"
        return f"(not a git repo) · session ran on {recorded}" if cwd_exists else f"session ran on {recorded}"
    if not recorded or current == recorded:
        return current
    return color(f"{current} checked out · session ran on {recorded}", "yellow")


def preview(view: View, cost: Cost | None = None, current_branch: str | None = None,
            width: int | None = None, color: Palette | None = None, *, exchange_lines: int = 3) -> str:
    """The preview pane. ``current_branch`` is the branch checked out now (None = not a repo).
    ``exchange_lines`` caps each side of the last exchange; the details screen asks for more."""
    width = width or terminal_width(80)
    color = color or Palette(False)
    p, s, e = view.pin, view.summary or Summary(exists=False), view.expiry
    lines: list[str] = []

    def row(label: str, value: str):
        lines.append(f"{color(pad(label, LABEL_WIDTH), 'dim')} {value}")

    lines.append(color(view.title, "bold"))
    if p.note:
        lines.append(p.note)
    lines.append("")
    row("dir", display_dir(p.cwd) or "(unknown)")
    row("branch", branch_line(s.git_branch, current_branch, bool(p.cwd and os.path.isdir(p.cwd)), color))
    launch = []
    if p.launch.model or s.model:
        launch.append(short_model(p.launch.model or s.model))
    if p.launch.effort or s.effort:
        launch.append(f"effort {p.launch.effort or s.effort}")
    if p.launch.permission_mode or s.permission_mode:
        launch.append(f"mode {p.launch.permission_mode or s.permission_mode}")
    row("model", " · ".join(launch) or "(default)")
    ctx = []
    if s.context_tokens:
        ctx.append(f"~{format_tokens(s.context_tokens)} ({s.context_pct}%)")
    if cost and cost.total_tokens:
        ctx.append(f"{format_tokens(cost.total_tokens)} tokens")
    if ctx or not s.exists:
        row("context", " · ".join(ctx) or "(transcript gone)")
    if cost is not None:
        lines.append(COST_PLACEHOLDER if cost is PENDING else f"{color(pad('cost', LABEL_WIDTH), 'dim')} {cost.line(short_model(s.model), s.messages)}")
    if s.exists:
        row("transcript", f"{s.messages} msgs · {format_size(s.size)}")
    when = []
    if s.created:
        when.append(_date(s.created))
    if not e.expired:
        when.append(f"last activity {format_age(e.age_seconds)} ago")
    if p.pinned_at:
        when.append(f"pinned {_date(p.pinned_at)}")
    if e.expired:
        when.append(color("expired", "red"))
    else:
        exp = f"expires {int(e.remaining_days)}d" if e.remaining_days >= 1 else "expires today"
        when.append(color(exp, "yellow") if e.expiring else exp)
    row("created", " · ".join(when))
    if p.keep:
        row("keep", "on — touched every run")
    if p.session_id:
        row("session", color(p.session_id, "dim"))
    if s.last_prompt or s.last_answer:
        lines.append("")
        for label, text in (("you", s.last_prompt), ("claude", s.last_answer)):
            if not text:
                continue
            flat = " ".join(text.split())
            wrapped = textwrap.wrap(flat, max(20, width - LABEL_WIDTH - 3))[:exchange_lines]
            if len(wrapped) == exchange_lines and len(flat) > sum(len(w) for w in wrapped):
                wrapped[-1] = shorten(wrapped[-1], max(1, len(wrapped[-1]) - 1))
            for i, w in enumerate(wrapped):
                lines.append(f"{color(pad(label if i == 0 else '', LABEL_WIDTH), 'dim')} {w}")
    return "\n".join(lines)


def session_rows(items: list[tuple[Summary, bool]], width: int | None = None, color: Palette | None = None) -> list[str]:
    """Session rows for the new-pin screen: title · dir · age · msgs · pinned marker."""
    width = width or terminal_width()
    color = color or Palette(False)
    if not items:
        return []
    now = time.time()
    dir_w = min(max(cells(display_dir(s.cwd)) for s, _ in items), max(MIN_COLUMN, width // 3))
    msgs_w = max(cells(f"{s.messages} msgs") for s, _ in items)
    tag_w = cells(f"  {SYM_KEEP} pinned") if any(p for _, p in items) else 0
    title_w = max(TITLE_FLOOR, width - dir_w - 2 - AGE_WIDTH - 2 - msgs_w - 2 - tag_w)
    out = []
    for s, pinned in items:
        age = pad(format_age(max(0.0, now - s.mtime)), AGE_WIDTH, ">")
        line = f"{pad(s.title, title_w)}  {color(pad(fit_dir(display_dir(s.cwd), dir_w), dir_w), 'dim')}  {color(age, 'dim')}  {pad(f'{s.messages} msgs', msgs_w, '>')}"
        if pinned:
            line += "  " + color(f"{SYM_KEEP} pinned", "green")
        out.append(line)
    return out


def session_preview(s: Summary, width: int | None = None, color: Palette | None = None) -> str:
    from .sessions import expiry_for
    view = View(Pin(alias="", session_id=s.session_id, cwd=s.cwd), expiry_for(s.path if s.exists else None), summary=s)
    return preview(view, None, None, width, color)
