"""Prompts: fzf screens while fzf is in use, plain text otherwise.

With fzf, a text field is fzf's query line (``--disabled``, prefilled, printed back on enter), a yes/no
and a choice are short lists, and a directory field is the query line over a list of completions that
reloads as you type. Without fzf (missing, too old, ``CLAUDE_PINS_NO_FZF``) the same calls are readline
prompts with the current value pre-filled and numbered menus. ctrl-c and esc cancel either way.
"""

from __future__ import annotations

import os
import sys

from . import fzf
from .render import crumb as default_crumb
from .render import palette

TEXT_HINTS = "enter save · esc cancel · ctrl-u clear"
LIST_HINTS = "enter choose · esc cancel"


class Cancelled(Exception):
    pass


def use_fzf() -> bool:
    return fzf.available()


def _header(hints: str, notes: list[str]) -> str:
    color = palette(sys.stdout)
    return "\n".join([color(hints, "dim"), *notes])


def _ask(text: str) -> str:
    try:
        return input(text)
    except (KeyboardInterrupt, EOFError):
        print()
        raise Cancelled()


# ---- lists ----------------------------------------------------------------------------------

def _list(crumb: str | None, notes: list[str], options: list[str], default: int) -> int:
    items = [fzf.Item(str(i), o) for i, o in enumerate(options, 1)]
    res = fzf.run(items, prompt=crumb or default_crumb(), header=_header(LIST_HINTS, notes), pos=default,
                  info="hidden")
    fzf.leave_screen()
    if res is None or not res.ids:
        raise Cancelled()
    return int(res.ids[0])


def choose(header: list[str], options: list[str], default: int = 1, *, crumb: str | None = None,
           stream=None) -> int:
    """A choice among ``options``; returns the 1-based index. ``header`` lines explain it. ``default``
    is where the cursor starts, and what bare enter picks in the plain menu."""
    if use_fzf():
        return _list(crumb, header, options, default)
    out = stream or sys.stdout
    for line in header:
        print(f" {line}", file=out)
    print(file=out)
    for i, opt in enumerate(options, 1):
        tag = "        (enter)" if i == default else ""
        print(f"  {i}) {opt}{tag}", file=out)
    print(file=out)
    while True:
        raw = _ask(f" choice [{default}]: ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw)
        print(f" pick 1–{len(options)}", file=out)


def yesno(question: str, default: bool = True, *, crumb: str | None = None, notes: list[str] | None = None) -> bool:
    """``notes`` are printed (plain) or shown in the header (fzf) above the question."""
    notes = notes or []
    if use_fzf():
        return _list(crumb, [*notes, question], ["yes", "no"], 1 if default else 2) == 1
    for line in notes:
        print(f" {line}")
    hint = "[Y/n]" if default else "[y/N]"
    raw = _ask(f" → {question} {hint} ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


# ---- text -----------------------------------------------------------------------------------

def _readline():
    try:
        import readline
        return readline
    except ImportError:
        return None


def _plain_text(label: str, default: str, completer=None) -> str:
    """readline with ``default`` pre-filled (and a completer when given); without readline the
    default is shown in the label and bare enter keeps it."""
    rl = _readline()
    if rl is not None:
        def hook():
            rl.insert_text(default)
            rl.redisplay()
        rl.set_pre_input_hook(hook)
        if completer is not None:
            rl.set_completer_delims(" \t\n")
            rl.set_completer(completer)
            rl.parse_and_bind("tab: complete")
    elif default:
        label = f"{label} [{default}]"
    try:
        value = _ask(f" {label}: ")
    finally:
        if rl is not None:
            rl.set_pre_input_hook(None)
            if completer is not None:
                rl.set_completer(None)
    if rl is None and not value.strip():
        return default
    return value.strip()


def text(label: str, default: str = "", *, crumb: str | None = None, note: str = "") -> str:
    """One-line text field. fzf: the query line under a breadcrumb ending in the field's name, with
    ``note`` under the hints; plain: ``label`` with the value pre-filled."""
    if use_fzf():
        res = fzf.run([], prompt=crumb or default_crumb(label), header=_header(TEXT_HINTS, [note] if note else []),
                      query=default, disabled=True, info="hidden")
        fzf.leave_screen()
        if res is None:
            raise Cancelled()
        return res.query.strip()
    if note:
        label = f"{label} ({note})"
    return _plain_text(label, default)


# ---- directories ----------------------------------------------------------------------------

def directory_completions(txt: str) -> list[str]:
    """Directories completing ``txt`` (``~`` expanded for the lookup, kept as typed in the result); dot
    directories only when the typed name starts the dot."""
    base = os.path.expanduser(txt)
    d, prefix = os.path.split(base)
    d = d or "."
    try:
        names = [n for n in os.listdir(d) if n.startswith(prefix) and (prefix.startswith(".") or not n.startswith("."))]
    except OSError:
        return []
    typed_dir = os.path.dirname(txt)
    return [os.path.join(typed_dir, n) + "/" if typed_dir else n + "/"
            for n in sorted(names) if os.path.isdir(os.path.join(d, n))]


def directory_rows(txt: str) -> list[str]:
    """What ``pin _dirs`` prints under the directory field: the typed directory itself first when it
    exists (so enter on the highlighted row keeps what was typed), then its completions."""
    rows = []
    if txt and os.path.isdir(os.path.expanduser(txt)):
        rows.append(txt.rstrip("/") + "/" if txt != "/" else "/")
    rows += [r for r in directory_completions(txt) if r not in rows]
    return rows


def _dir_completer(txt: str, state: int):
    paths = directory_completions(txt)
    return paths[state] if state < len(paths) else None


def directory(default: str = "", *, crumb: str | None = None, note: str = "") -> str:
    """A directory field: fzf's query line over the completions of what is typed (``pin _dirs``
    reloads them on every change, the one per-keystroke Python spawn); enter takes the highlighted
    directory, or the text when the list is empty. Plain: readline with tab completion. Returns the
    text as typed, "" for none; the caller expands and checks it."""
    if use_fzf():
        items = [fzf.Item(r, r) for r in directory_rows(default)]
        reload = f"reload({fzf.pin_exe()} _dirs {{q}})"
        res = fzf.run(items, prompt=crumb or default_crumb("directory"),
                      header=_header(TEXT_HINTS, [note] if note else []), query=default, disabled=True,
                      info="hidden", binds=[("change", reload)])
        fzf.leave_screen()
        if res is None:
            raise Cancelled()
        return (res.ids[0] if res.ids else res.query).strip()
    if note:
        label = f"directory ({note})"
    else:
        label = "directory (tab completes)"
    return _plain_text(label, default, _dir_completer)
