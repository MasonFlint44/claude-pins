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
from .transcript import Summary, shorten

SYM_OPEN, SYM_KEEP, SYM_FORK, SYM_WORKTREE, SYM_EXPIRING, SYM_EXPIRED = "●", "⚑", "⑂", "⌂", "⏳", "✗"
LEGEND = f"{SYM_OPEN} open  {SYM_KEEP} keep  {SYM_FORK} fork  {SYM_WORKTREE} worktree  {SYM_EXPIRING} expiring  {SYM_EXPIRED} expired"

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
    env = os.environ.get("FZF_PREVIEW_COLUMNS") or os.environ.get("COLUMNS")
    if env and env.isdigit():
        return int(env)
    return shutil.get_terminal_size((default, 24)).columns


def short_model(model: str) -> str:
    return model[len("claude-"):] if model.startswith("claude-") else (model or "")


def display_dir(cwd: str) -> str:
    """``~``-relative; Claude worktrees abbreviate to ``~/git/foo ⌂x``."""
    if not cwd:
        return ""
    wt = split_worktree_path(cwd)
    if wt:
        return f"{config.tilde(wt[0])} {SYM_WORKTREE}{wt[1]}"
    return config.tilde(cwd)


def pad(text: str, width: int, align: str = "<") -> str:
    """Pad/truncate by display cells (wide symbols count as one cell here; good enough)."""
    n = len(text)
    if n > width:
        text = text[: max(0, width - 1)] + "…" if width > 0 else ""
        n = len(text)
    fill = " " * (width - n)
    return text + fill if align == "<" else fill + text


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


def rows(views: list[View], width: int | None = None, color: Palette | None = None,
         numbered: bool = False) -> list[str]:
    """One rendered line per view, columns sized to the terminal width."""
    width = width or terminal_width()
    color = color or Palette(False)
    if not views:
        return []
    alias_w = min(16, max(len(v.pin.alias) for v in views))
    dir_w = min(26, max((len(display_dir(v.pin.cwd)) for v in views), default=1) or 1)
    age_w = max(3, max(len(v.age) for v in views))
    mark_w = max((len(v.markers) for v in views), default=0)
    num_w = (len(str(len(views))) + 2) if numbered else 0
    fixed = num_w + alias_w + 2 + 2 + dir_w + 2 + age_w + (2 + mark_w if mark_w else 0)
    title_w = max(8, width - fixed - 2)
    out = []
    for i, v in enumerate(views, 1):
        num = pad(f"{i}", num_w - 2, ">") + "  " if numbered else ""
        alias = pad(v.pin.alias, alias_w)
        title = pad(v.title, title_w)
        d = pad(display_dir(v.pin.cwd), dir_w)
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


def _date(iso: str) -> str:
    return iso[:10] if iso else ""


def preview(view: View, cost: Cost | None = None, current_branch: str | None = None,
            width: int | None = None, color: Palette | None = None, *, pinned_line: bool = True) -> str:
    """The §6.2 preview pane. ``current_branch`` is the branch checked out now (None = not a repo)."""
    width = width or terminal_width(80)
    color = color or Palette(False)
    p, s, e = view.pin, view.summary or Summary(exists=False), view.expiry
    lines: list[str] = []

    def row(label: str, value: str):
        lines.append(f"{color(pad(label, 9), 'dim')} {value}")

    lines.append(color(view.title, "bold"))
    if p.note:
        lines.append(p.note)
    lines.append("")
    row("dir", display_dir(p.cwd) or "(unknown)")
    recorded = "" if s.git_branch in ("", "HEAD") else s.git_branch
    if not recorded:
        row("branch", current_branch or "(not a git repo)")
    elif current_branch is None:
        row("branch", f"{recorded}  (not a git repo)" if p.cwd and os.path.isdir(p.cwd) else recorded)
    elif current_branch == recorded:
        row("branch", f"{current_branch}  (session: {recorded})")
    else:
        row("branch", f"{current_branch}  {color('(session: ' + recorded + ' — differs)', 'yellow')}")
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
    if s.exists:
        ctx.append(f"{s.messages} msgs")
    if cost and cost.total_tokens:
        ctx.append(f"{format_tokens(cost.total_tokens)} tokens")
    row("context", " · ".join(ctx) or "(transcript gone)")
    if cost is not None:
        row("cost", cost.line(short_model(s.model), s.messages))
    when = []
    if s.created:
        when.append(_date(s.created))
    if not e.expired:
        when.append(f"last {format_age(e.age_seconds)}")
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
    if s.last_prompt or s.last_answer:
        lines.append("")
        for label, text in (("you", s.last_prompt), ("claude", s.last_answer)):
            if not text:
                continue
            wrapped = textwrap.wrap(" ".join(text.split()), max(20, width - 12))[:3]
            if len(wrapped) == 3 and len(" ".join(text.split())) > sum(len(w) for w in wrapped):
                wrapped[-1] = shorten(wrapped[-1], max(1, len(wrapped[-1]) - 1))
            for i, w in enumerate(wrapped):
                lines.append(f"{color(pad(label if i == 0 else '', 9), 'dim')} {w}")
    return "\n".join(lines)


def session_rows(items: list[tuple[Summary, bool]], width: int | None = None, color: Palette | None = None) -> list[str]:
    """§6.6 rows: title · dir · age · msgs · pinned marker."""
    width = width or terminal_width()
    color = color or Palette(False)
    if not items:
        return []
    now = time.time()
    dir_w = min(24, max(len(display_dir(s.cwd)) for s, _ in items))
    msgs_w = max(len(f"{s.messages} msgs") for s, _ in items)
    tag_w = 10 if any(p for _, p in items) else 0
    title_w = max(10, width - dir_w - 4 - msgs_w - 3 - 4 - tag_w)
    out = []
    for s, pinned in items:
        age = pad(format_age(max(0.0, now - s.mtime)), 3, ">")
        line = f"{pad(shorten(s.title, title_w), title_w)}  {color(pad(display_dir(s.cwd), dir_w), 'dim')}  {color(age, 'dim')}  {pad(f'{s.messages} msgs', msgs_w, '>')}"
        if pinned:
            line += "  " + color(f"{SYM_KEEP} pinned", "green")
        out.append(line)
    return out


def session_preview(s: Summary, width: int | None = None, color: Palette | None = None) -> str:
    from .sessions import expiry_for
    view = View(Pin(alias="", session_id=s.session_id, cwd=s.cwd), expiry_for(s.path if s.exists else None), summary=s)
    return preview(view, None, None, width, color)
