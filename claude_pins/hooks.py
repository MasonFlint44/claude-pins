"""The commands a screen runs while it is up, as Python.

Each is one function: the fzf backend reaches it through a ``pins _<hook>`` subprocess (``cli`` prints
what the function returns), the built-in picker calls it in process. Keeping one function per hook is
what makes a reload draw exactly what a launch would on either backend.

The previews are generators of chunks: the pin preview yields everything above the cost line first, then
the cost line and the rest once ``cost.session_cost`` answers (ccusage can take seconds on a cache miss),
so a pane fills at once and the cost drops in.
"""

from __future__ import annotations

import os
from typing import Iterator

from . import config
from .gitutil import current_branch, is_repo
from . import render
from .listing import session_pairs
from .render import Palette, palette, preview, session_preview
from .screen import Hook, Item
from .store import load_store
from .transcript import read_summary

def preview_text(hook: Hook, row: str, width: int | None = None, color: Palette | None = None) -> Iterator[str]:
    """The pane's text for ``row`` (its id) under ``hook``, in chunks that concatenate to the whole."""
    color = color or palette()
    if hook.name == "preview":
        yield from pin_preview(row, width, color)
    elif hook.name == "spreview":
        if row:
            summary = session_pairs(load_store(), [read_summary(row)])[0][0]     # a pinned one under its pin's title
            yield session_preview(summary, width, color) + "\n"
    elif hook.name == "draft":
        yield draft_preview(hook.args[0], width, color)
    else:
        raise ValueError(f"not a preview hook: {hook.name}")


def pin_preview(alias: str, width: int | None = None, color: Palette | None = None) -> Iterator[str]:
    from .cost import session_cost
    from .listing import view_for
    color = color or palette()
    if not alias or alias == "-":
        return
    pin = load_store().get(alias)
    if pin is None:
        return
    view = view_for(pin, set())
    branch = current_branch(pin.cwd) if pin.cwd and os.path.isdir(pin.cwd) and is_repo(pin.cwd) else None
    if not (view.summary and view.summary.exists):
        yield preview(view, None, branch, width, color) + "\n"
        return
    yield from render.preview_chunks(view, lambda: session_cost(pin.session_id, pin.transcript), branch, width, color)


def draft_preview(path: str, width: int | None = None, color: Palette | None = None) -> str:
    """The editor's pane: the unsaved draft, read from the file the editor keeps current, with the
    changed rows marked. No cost lookup: the draft cannot change it and the pane redraws on every move."""
    from .editor import read_draft
    from .listing import view_for
    color = color or palette()
    try:
        pin, changed = read_draft(path)
    except (OSError, ValueError, KeyError):
        return ""
    view = view_for(pin, set())
    branch = current_branch(pin.cwd) if pin.cwd and os.path.isdir(pin.cwd) and is_repo(pin.cwd) else None
    return preview(view, None, branch, width, color, changed=changed) + "\n"


def rows(hook: Hook, query: str = "", width: int | None = None, color: Palette | None = None) -> list[Item]:
    """The rows a list screen reloads: the picker's pins under ``Hook("rows", sort, all)``, or the
    directory field's completions of ``query`` under ``Hook("dirs")``."""
    if hook.name == "rows":
        return pin_rows(hook.args[0], hook.args[1] == "all", width, color)
    if hook.name == "dirs":
        return dir_rows(query)
    raise ValueError(f"not a row hook: {hook.name}")


def pin_rows(sort: str, show_all: bool, width: int | None = None, color: Palette | None = None) -> list[Item]:
    from .keymap import Keymap
    from .listing import build_views
    from .picker import list_items
    views, _ = build_views(load_store(), include_expired=show_all, sort=sort or config.default_sort())
    return list_items(views, Keymap.load(), color or palette(), width=width)


def dir_rows(text: str) -> list[Item]:
    from .prompt import directory_rows
    return [Item(r, r) for r in directory_rows(text or "")]
