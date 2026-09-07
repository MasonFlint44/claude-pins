"""Editor: a sectioned field picker over one pin. Nothing is written until save."""

from __future__ import annotations

import os
import sys

from . import config, fzf, prompt
from .model import EFFORT_LEVELS, PERMISSION_MODES, Pin, PinError, validate_alias
from .render import crumb, display_dir, grouped, palette
from .text import pad
from .store import Store

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


def edit_pin(store: Store, alias: str, *, run=None) -> str | None:
    """Interactive edit. Returns the (possibly new) alias if saved, else None."""
    if run is None and not fzf.available():
        return edit_pin_plain(store, alias)
    run = run or fzf.run
    color = palette(sys.stdout)
    original = store.require(alias)
    draft = original.copy()
    cursor = 1

    def dirty_fields() -> set[str]:
        return {f for _, f, k, _ in FIELDS if _get(draft, f) != _get(original, f)}

    def save() -> str | None:
        try:
            validate_alias(draft.alias)
            if not draft.title.strip():
                raise PinError("title is required")
            if draft.alias != original.alias and store.get(draft.alias) is not None:
                raise PinError(f"alias {draft.alias} is taken")
        except PinError as e:
            print(f" ✗ {e}")
            return None
        target = store.require(original.alias)
        for _, f, _, _ in FIELDS:
            _set(target, f, _get(draft, f))
        store.save()
        return draft.alias

    while True:
        dirty = dirty_fields()
        table: list[tuple[str, str, str]] = []
        for sec, field, kind, hint in FIELDS:
            star = "*" if field in dirty else " "
            req = " *" if field == "title" else ""
            value = _shown(draft, field, kind)
            table.append((sec, f"{field}{req}", f"{star}{pad(value, 26)} {color(hint, 'dim')}"))
        table += [("", "Done", ""), ("", "Cancel", "")]
        ids = [f for _, f, _, _ in FIELDS] + ["done", "cancel"]
        items = [fzf.Item(i, line) for i, line in zip(ids, grouped(table, color))]
        prompt_text = crumb(original.alias, "edit" + (" (unsaved)" if dirty else ""))
        header = color("enter change · alt-s save · esc back", "dim")
        res = run(items, prompt=prompt_text, header=header, expect=["alt-s"], pos=cursor, info="hidden")
        if res is None:
            if not dirty:
                return None
            fzf.leave_screen()
            try:
                raw = input(" save changes? [Y/n/c] ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                print()
                return None
            if raw in ("", "y", "yes"):
                saved = save()
                if saved:
                    return saved
                continue
            if raw in ("n", "no"):
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
        try:
            if kind == "bool":
                _set(draft, target, not _get(draft, target))
            elif kind == "text":
                _set(draft, target, prompt.text(target, _get(draft, target) or ""))
            elif kind == "dir":
                value = prompt.text("cwd (tab completes)", _get(draft, target) or "")
                _set(draft, target, os.path.abspath(os.path.expanduser(value)) if value else "")
            elif kind == "choice":
                options = list(PERMISSION_MODES if target == "permission" else EFFORT_LEVELS)
                pick = choose(run, crumb(original.alias, "edit", target), options, _get(draft, target))
                if pick is not None:
                    _set(draft, target, pick)
            elif kind == "model":
                pick = choose(run, crumb(original.alias, "edit", "model"), MODEL_CHOICES + ["(type a model name…)"], _get(draft, target))
                if pick == "(type a model name…)":
                    _set(draft, target, prompt.text("model", _get(draft, target) or ""))
                elif pick is not None:
                    _set(draft, target, pick)
        except prompt.Cancelled:
            continue


def choose(run, crumb: str, options: list[str], current: str | None) -> str | None:
    """Short fzf list with ``(clear)``. Returns "" for clear, None when cancelled."""
    items = [fzf.Item(o, o) for o in options] + [fzf.Item("", "(clear)")]
    pos = (options.index(current) + 1) if current in options else None
    res = run(items, prompt=crumb, header="", expect=[], pos=pos, info="hidden")
    if res is None or not res.ids:
        return None
    return res.ids[0]


def edit_pin_plain(store: Store, alias: str) -> str | None:
    """The same form as a numbered text menu, for terminals without fzf."""
    color = palette(sys.stdout)
    original = store.require(alias)
    draft = original.copy()
    while True:
        dirty = {f for _, f, _, _ in FIELDS if _get(draft, f) != _get(original, f)}
        print(f"\n pins › {original.alias} › edit{' (unsaved)' if dirty else ''}")
        section = None
        for i, (sec, field, kind, hint) in enumerate(FIELDS, 1):
            if sec != section:
                section = sec
                print(color(f"  ── {sec} ──", "dim"))
            star = "*" if field in dirty else " "
            print(f"  {i:>2}  {field + (' *' if field == 'title' else ''):<11} {star}{_shown(draft, field, kind):<26} {color(hint, 'dim')}".rstrip())
        print(color("\n  N change field · s save · q cancel", "dim"))
        try:
            raw = input(" > ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            return None
        if raw == "q":
            return None
        if raw == "s":
            try:
                validate_alias(draft.alias)
                if not draft.title.strip():
                    raise PinError("title is required")
                if draft.alias != original.alias and store.get(draft.alias) is not None:
                    raise PinError(f"alias {draft.alias} is taken")
            except PinError as e:
                print(f" ✗ {e}")
                continue
            target = store.require(original.alias)
            for _, f, _, _ in FIELDS:
                _set(target, f, _get(draft, f))
            store.save()
            return draft.alias
        if not raw.isdigit() or not 1 <= int(raw) <= len(FIELDS):
            continue
        _, field, kind, _ = FIELDS[int(raw) - 1]
        try:
            if kind == "bool":
                _set(draft, field, not _get(draft, field))
            elif kind in ("text", "model"):
                _set(draft, field, prompt.text(field, _get(draft, field) or ""))
            elif kind == "dir":
                value = prompt.text("cwd", _get(draft, field) or "")
                _set(draft, field, os.path.abspath(os.path.expanduser(value)) if value else "")
            elif kind == "choice":
                options = list(PERMISSION_MODES if field == "permission" else EFFORT_LEVELS)
                for i, o in enumerate(options, 1):
                    print(f"  {i}) {o}")
                print("  0) (clear)")
                pick = prompt.text(f"{field} [0-{len(options)}]", "")
                if pick.isdigit() and 0 <= int(pick) <= len(options):
                    _set(draft, field, options[int(pick) - 1] if int(pick) else "")
        except prompt.Cancelled:
            continue
