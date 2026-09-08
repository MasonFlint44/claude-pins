"""Editor: a sectioned field picker over one pin, with the draft in the preview pane. Nothing is written
until save."""

from __future__ import annotations

import json
import os
import sys
import tempfile

from . import naming, prompt
from .model import EFFORT_LEVELS, PERMISSION_MODES, Pin, PinError, validate_alias
from .render import crumb, display_dir, grouped, palette
from .screen import Header, Hook, Item, Screen, hold_screen, show
from .text import pad
from .store import Store
from .theme import ERROR

MODEL_CHOICES = ["fable", "opus", "sonnet", "haiku"]

FIELDS = [
    ("identity", "title", "text", ""),
    ("identity", "alias", "text", ""),
    ("identity", "note", "text", ""),
    ("location", "cwd", "dir", ""),
    ("location", "worktree", "bool", "open in a fresh worktree each time"),
    ("launch", "model", "model", "claude's default model"),
    ("launch", "effort", "choice", " · ".join(EFFORT_LEVELS)),
    ("launch", "permission", "choice", " · ".join(PERMISSION_MODES)),
    ("retention", "keep", "bool", "touched every run, never expires"),
    ("retention", "fork", "bool", "open as a copy, original untouched"),
]
SAVE_CHOICES = ["save", "discard", "keep editing"]


def _get(pin: Pin, field: str):
    if field == "permission":
        return pin.launch.permission_mode
    if field in ("model", "effort"):
        return getattr(pin.launch, field)
    return getattr(pin, field)


def _set(pin: Pin, field: str, value) -> None:
    if field == "permission":
        pin.launch.permission_mode = value or None
    elif field in ("model", "effort"):
        setattr(pin.launch, field, value or None)
    else:
        setattr(pin, field, value)


def _shown(pin: Pin, field: str, kind: str) -> str:
    v = _get(pin, field)
    if kind == "bool":
        return "ON" if v else "off"
    if kind == "dir":
        return display_dir(v) if v else "(none)"
    if v in (None, ""):
        return "(default)" if kind in ("choice", "model") else ""
    return str(v)


def changed_fields(draft: Pin, original: Pin) -> set[str]:
    return {f for _, f, _, _ in FIELDS if _get(draft, f) != _get(original, f)}


def _validate(store: Store, draft: Pin, original: Pin) -> None:
    validate_alias(draft.alias)
    if not draft.title.strip():
        raise PinError("title is required")
    if draft.alias != original.alias and store.get(draft.alias) is not None:
        raise PinError(f"alias {draft.alias} is taken")


def _commit(store: Store, draft: Pin, original: Pin) -> tuple[str, str]:
    """Write the draft. Returns the saved alias and the session-name note (empty unless the alias changed
    and the session still carried the old one)."""
    target = store.require(original.alias)      # the store's own pin, which ``original`` may be
    old_alias = original.alias
    for _, f, _, _ in FIELDS:
        _set(target, f, _get(draft, f))
    store.save()
    note = naming.rename(target, old_alias) if draft.alias != old_alias else ""
    return draft.alias, note


# ---- the draft file, read back by ``pin _preview --draft`` -------------------------------------

def write_draft(path: str, draft: Pin, original: Pin) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"draft": draft.to_dict(), "original": original.to_dict()}, fh)


def read_draft(path: str) -> tuple[Pin, set[str]]:
    """(the draft pin, the fields that differ from the original)."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    draft, original = Pin.from_dict(data["draft"]), Pin.from_dict(data["original"])
    return draft, changed_fields(draft, original)


# ---- the editor ---------------------------------------------------------------------------------

def edit_pin(store: Store, alias: str) -> tuple[str, str] | None:
    """Interactive edit. Returns the (possibly new) alias and the session-name note if saved, else None."""
    color = palette(sys.stdout)
    original = store.require(alias)
    draft = original.copy()
    cursor = 1
    flash = ""
    fd, draft_path = tempfile.mkstemp(prefix="pin-draft-", suffix=".json")
    os.close(fd)

    def save() -> tuple[str, str] | None:
        nonlocal flash
        try:
            _validate(store, draft, original)
        except PinError as e:
            flash = color(f"✗ {e}", ERROR)
            return None
        return _commit(store, draft, original)

    try:
        with hold_screen():
            while True:
                dirty = changed_fields(draft, original)
                write_draft(draft_path, draft, original)
                table: list[tuple[str, str, str]] = []
                for sec, field, kind, hint in FIELDS:
                    star = "*" if field in dirty else " "
                    req = " *" if field == "title" else ""
                    value = _shown(draft, field, kind)
                    table.append((sec, f"{field}{req}", f"{star}{pad(value, 26)} {color(hint, 'dim')}"))
                table += [("", "Done", ""), ("", "Cancel", "")]
                ids = [f for _, f, _, _ in FIELDS] + ["done", "cancel"]
                items = [Item(i, line) for i, line in zip(ids, grouped(table, color))]
                prompt_text = crumb(original.alias, "edit" + (" (unsaved)" if dirty else ""))
                header = Header("enter change · alt-s save · esc back", status=flash or " ", color=color)
                flash = ""
                res = show(Screen(items, prompt=prompt_text, header=header, expect=["alt-s"], pos=cursor,
                                  preview=Hook("draft", (draft_path,)), preview_label=" draft "))
                if res is None:
                    if not dirty:
                        return None
                    try:
                        pick = prompt.choose(["save changes?"], SAVE_CHOICES, 1, crumb=crumb(original.alias, "edit", "unsaved"))
                    except prompt.Cancelled:
                        return None
                    if pick == 1:
                        saved = save()
                        if saved:
                            return saved
                        continue
                    if pick == 2:
                        return None
                    continue
                if res.key == "alt-s":
                    saved = save()
                    if saved:
                        return saved
                    continue
                target = res.ids[0] if res.ids else "-"
                if target == "-":
                    continue
                if target == "done":
                    if not dirty:
                        return None
                    saved = save()
                    if saved:
                        return saved
                    continue
                if target == "cancel":
                    return None
                idx = next(i for i, (_, f, _, _) in enumerate(FIELDS) if f == target)
                cursor = idx + 1
                kind = FIELDS[idx][2]
                field_crumb = crumb(original.alias, "edit", target)
                try:
                    if kind == "bool":
                        _set(draft, target, not _get(draft, target))
                    elif kind == "text":
                        _set(draft, target, prompt.text(target, _get(draft, target) or "", crumb=field_crumb))
                    elif kind == "dir":
                        value = prompt.directory(_get(draft, target) or "", crumb=field_crumb)
                        _set(draft, target, os.path.abspath(os.path.expanduser(value)) if value else "")
                    elif kind == "choice":
                        options = list(PERMISSION_MODES if target == "permission" else EFFORT_LEVELS)
                        pick = choose(field_crumb, options, _get(draft, target))
                        if pick is not None:
                            _set(draft, target, pick)
                    elif kind == "model":
                        pick = choose(field_crumb, MODEL_CHOICES + ["(type a model name…)"], _get(draft, target))
                        if pick == "(type a model name…)":
                            _set(draft, target, prompt.text("model", _get(draft, target) or "", crumb=field_crumb))
                        elif pick is not None:
                            _set(draft, target, pick)
                except prompt.Cancelled:
                    continue
    finally:
        try:
            os.unlink(draft_path)
        except OSError:
            pass


def choose(crumb: str, options: list[str], current: str | None) -> str | None:
    """A short list with ``(clear)``. Returns "" for clear, None when cancelled."""
    items = [Item(o, o) for o in options] + [Item("", "(clear)")]
    pos = (options.index(current) + 1) if current in options else None
    header = Header(prompt.LIST_HINTS, color=palette(sys.stdout))
    res = show(Screen(items, prompt=crumb, header=header, pos=pos))
    if res is None or not res.ids:
        return None
    return res.ids[0]
