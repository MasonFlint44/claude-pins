"""Prompts as screens: a text field is the query line (disabled as a filter, prefilled, handed back on
enter), a yes/no and a choice are short lists, and a directory field is the query line over a list of
completions that reloads as you type. ctrl-c and esc cancel. Either backend draws them; without a
terminal to ask on, a prompt is cancelled.
"""

from __future__ import annotations

import os
import sys

from .render import crumb as default_crumb
from .render import palette
from .screen import Header, Hook, Item, Screen, leave_screen, show

TEXT_HINTS = "enter save · esc cancel · ctrl-u clear"
LIST_HINTS = "enter choose · esc cancel"


class Cancelled(Exception):
    pass


def _header(hints: str, notes: list[str]) -> Header:
    return Header(hints, extra=tuple(notes), color=palette(sys.stdout))


# ---- lists ----------------------------------------------------------------------------------

def _list(crumb: str | None, notes: list[str], options: list[str], default: int) -> int:
    items = [Item(str(i), o) for i, o in enumerate(options, 1)]
    res = show(Screen(items, prompt=crumb or default_crumb(), header=_header(LIST_HINTS, notes), pos=default))
    leave_screen()
    if res is None or not res.ids:
        raise Cancelled()
    return int(res.ids[0])


def choose(header: list[str], options: list[str], default: int = 1, *, crumb: str | None = None) -> int:
    """A choice among ``options``; returns the 1-based index. ``header`` lines explain it; the cursor
    starts on ``default``."""
    return _list(crumb, header, options, default)


def yesno(question: str, default: bool = True, *, crumb: str | None = None, notes: list[str] | None = None) -> bool:
    """``notes`` are shown in the header above the question."""
    return _list(crumb, [*(notes or []), question], ["yes", "no"], 1 if default else 2) == 1


# ---- text -----------------------------------------------------------------------------------

def text(label: str, default: str = "", *, crumb: str | None = None, note: str = "") -> str:
    """One-line text field: the query line under a breadcrumb ending in the field's name, with ``note``
    under the hints and ``default`` already typed."""
    res = show(Screen([], prompt=crumb or default_crumb(label), header=_header(TEXT_HINTS, [note] if note else []),
                      query=default, disabled=True))
    leave_screen()
    if res is None:
        raise Cancelled()
    return res.query.strip()


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


def directory(default: str = "", *, crumb: str | None = None, note: str = "") -> str:
    """A directory field: the query line over the completions of what is typed (the ``dirs`` hook
    reloads them on every change); enter takes the highlighted directory, or the text when the list is
    empty. Returns the text as typed, "" for none; the caller expands and checks it."""
    items = [Item(r, r) for r in directory_rows(default)]
    res = show(Screen(items, prompt=crumb or default_crumb("directory"),
                      header=_header(TEXT_HINTS, [note] if note else []), query=default, disabled=True,
                      on_change=Hook("dirs")))
    leave_screen()
    if res is None:
        raise Cancelled()
    return (res.ids[0] if res.ids else res.query).strip()
