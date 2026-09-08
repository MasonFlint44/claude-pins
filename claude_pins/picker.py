"""The fzf screens: main picker, actions palette, help/shortcuts, new pin, prune/undo flows."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from . import actions, altkeys, config, prompt
from .editor import edit_pin
from .keymap import ACTIONS, BY_ID, GROUPS, Keymap, key_warning, validate_key
from .listing import build_views
from .model import PinError, kebab, next_free_alias
from .opener import launch, touch_kept
from .render import (FZF_COLUMN_SEP, View, crumb, grouped, label_row, layout, legend, palette, preview, rows,
                     session_label_row, session_layout, session_rows,
                     terminal_height, terminal_width)
from .screen import Header, Hook, Item, Screen, hold_screen, preview_fits, show
from .theme import ERROR, SUCCESS
from .sessions import iter_transcripts
from .store import Store
from .transcript import read_summary

EMPTY_MESSAGE = "No pins yet. {new} pins a recent session, or run /pins:pin inside a Claude session."
TOO_SHORT_NOTE = "preview hidden: terminal too short"
LIST_WIDTH_SLACK = 4        # fzf's pointer and marker columns plus a little room at the right edge
# The query matches the columns a person thinks of a session by, not its idle time or marker glyphs:
# fzf's --nth over the tab-separated columns (see render.FZF_COLUMN_SEP).
PICKER_NTH = "1..3"         # alias, title, directory
SESSIONS_NTH = "1..2"       # title, directory (not idle, the message count or the pinned tag)


def list_items(views: list[View], km: Keymap, color, width: int | None = None) -> list[Item]:
    """The main list: the sticky label row (id ``-``) first, then one row per pin, at the terminal's width.
    The ``rows`` hook draws the same list, so a reload draws exactly what a restart would."""
    width = (width or terminal_width()) - LIST_WIDTH_SLACK
    if not views:
        return [Item("-", color(EMPTY_MESSAGE.format(new=km.key("new") or "pin add"), "dim"))]
    cols = layout(views, width)
    items = [Item("-", label_row(cols, color))]
    for v, line in zip(views, rows(views, width=width, color=color, cols=cols, sep=FZF_COLUMN_SEP)):
        items.append(Item(v.pin.alias, line))
    return items


def alt_keys_note() -> str:
    """What the help screen says about alt keys: in a terminal that is not sending them (a Mac's Option
    as Meta, xterm's metaSendsEscape), the switch to set (``altkeys.probe``); nothing elsewhere or once
    the switch is on."""
    probe = altkeys.probe()
    return probe.note() if probe and not probe.on else ""


def first_run_note() -> str:
    """The alt-keys note once, on the status line, the first time the picker runs; a marker file in the
    cache directory remembers that it has (the help screen keeps the line, so the picker never nags)."""
    note = alt_keys_note()
    if not note:
        return ""
    marker = config.noted_file()
    if marker.exists():
        return ""
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        return ""
    return note


@dataclass
class State:
    sort: str
    show_expired: bool = False
    query: str = ""
    flash: str = ""
    cursor: str = ""        # alias to highlight
    preview: bool = True


class Picker:
    def __init__(self, store: Store, keymap: Keymap | None = None, *, query: str = "", sort: str | None = None,
                 show_expired: bool = False):
        self.store = store
        self.km = keymap or Keymap.load()
        self.state = State(sort=sort or config.default_sort(), query=query, show_expired=show_expired)
        self.color = palette(sys.stdout)
        self.state.flash = first_run_note()

    # ---- helpers -------------------------------------------------------------------

    def hints(self, *ids: str) -> str:
        parts = [self.km.hint(i) for i in ids if self.km.hint(i)]
        return " · ".join(parts)

    def header(self, hints: str, *extra: str, legend: str = "", note: str = "") -> Header:
        """This screen's lines above the prompt. A flash takes the status line for one screen (over the
        too-short ``note``, which is about the same thing); otherwise the status line is blank. A flash
        is red when it starts with ✗, green with ✓, and dim otherwise (a note)."""
        flash = ""
        if self.state.flash:
            kind = ERROR if self.state.flash.startswith("✗") else SUCCESS if self.state.flash.startswith("✓") else "dim"
            flash = self.color(self.state.flash, kind)
        return Header(hints, legend=legend, extra=extra, status=flash or " ", note="" if flash else note,
                      color=self.color)

    def preview_fits(self, *, bottom_border: bool) -> bool:
        return preview_fits(terminal_height(), bottom_border=bottom_border)

    def expect_keys(self, *ids: str) -> tuple[list[str], dict[str, str]]:
        """The keys that end the screen for these actions; actions with a ``bind`` stay inside it."""
        keys, mapping = [], {}
        for i in ids:
            k = self.km.key(i)
            if k and k != "enter" and k != "tab" and not BY_ID[i].bind:
                keys.append(k)
                mapping[k] = i
        return keys, mapping

    def reload(self) -> None:
        self.store = Store(self.store.path).load()

    def rows_hook(self) -> Hook:
        return Hook("rows", (self.state.sort, "all" if self.state.show_expired else ""))

    def apply(self, outcome: actions.Outcome) -> None:
        self.state.flash = outcome.flash
        if outcome.cursor is not None:
            self.state.cursor = outcome.cursor

    def undo_hint(self) -> str:
        return self.km.key("undo") or "pin undo"

    # ---- main picker ----------------------------------------------------------------

    def run(self) -> int:
        """Loop until the user opens a pin (exec) or leaves (esc). Every screen along the way draws over
        the previous one; the normal screen comes back when the loop ends."""
        with hold_screen():
            return self.loop()

    def loop(self) -> int:
        while True:
            self.reload()
            touch_kept(self.store)
            views, expired = build_views(self.store, include_expired=self.state.show_expired, sort=self.state.sort)
            items = list_items(views, self.km, self.color)
            main_actions = ["open_fork", "open_worktree", "palette", "edit", "details", "touch", "keep", "fork_mode",
                            "worktree_mode", "unpin", "new", "expired", "prune", "undo", "sort", "preview", "refresh",
                            "help"]
            expect, mapping = self.expect_keys(*main_actions)
            pos = next((i + 1 for i, v in enumerate(views) if v.pin.alias == self.state.cursor), None)
            if self.state.query:
                pos = None
            footer = ""
            if expired and not self.state.show_expired:
                footer = f" {expired} expired · {self.km.key('expired') or 'pin list --all'} show · pin prune "
            elif expired and self.state.show_expired:
                footer = f" {expired} expired shown · pin prune "
            hints = self.hints("open", "palette", "edit", "new", "details", "help")
            note = self.color(TOO_SHORT_NOTE, "dim") if self.state.preview else ""
            header = self.header(hints, legend=legend(), note=note)
            # The list reloads in place on the refresh key and on resize (the rows are laid out for the width).
            res = show(Screen(items, prompt=crumb(), header=header, header_lines=1, expect=expect,
                              query=self.state.query, multi=bool(views), pos=pos, nth=PICKER_NTH, counter="pins",
                              preview=Hook("preview") if self.state.preview else None, label_from_row=True,
                              footer=footer, reload=self.rows_hook(), refresh_key=self.km.key("refresh")))
            self.state.flash = ""
            if res is None:
                return 0
            self.state.query = res.query
            ids = [i for i in res.ids if i != "-"]
            if ids:
                self.state.cursor = ids[0]
            action = mapping.get(res.key, "open") if res.key else "open"
            # The rows may have been reloaded since launch, so the views come from the store as it is now.
            self.reload()
            views, _ = build_views(self.store, include_expired=True, sort=self.state.sort)
            outcome = self.dispatch(action, ids, views, bottom_border=bool(footer))
            if outcome == "quit":
                return 0

    # ---- dispatch ---------------------------------------------------------------------

    def dispatch(self, action: str, ids: list[str], views: list[View], *, bottom_border: bool = False) -> str | None:
        by_alias = {v.pin.alias: v for v in views}
        selected = [by_alias[i] for i in ids if i in by_alias]
        first = selected[0] if selected else None
        if action in ("open", "open_fork", "open_worktree"):
            if first is None:
                return None
            return self.open(first, action)
        if action == "palette":
            if first is None:
                return None
            chosen = self.palette(selected)
            if chosen:
                return self.dispatch(chosen, ids, views, bottom_border=bottom_border)
            return None
        if action == "help":
            self.help_screen()
            return None
        if action == "refresh":        # from the palette; the restart is the refresh
            return None
        if action == "new":
            self.new_pin()
            return None
        if action == "expired":
            self.state.show_expired = not self.state.show_expired
            return None
        if action == "sort":
            self.state.sort, outcome = actions.cycle_sort(self.state.sort)
            self.apply(outcome)
            return None
        if action == "preview":
            if not self.state.preview and not self.preview_fits(bottom_border=bottom_border):
                key = self.km.key("details")
                self.state.flash = ("preview needs a taller terminal · " +
                                    (f"{key} for details" if key else "Details is in the palette"))
                return None
            self.state.preview = not self.state.preview
            return None
        if action == "details":
            if first is None:
                return None
            return self.details(first)
        if action == "prune":
            self.prune()
            return None
        if action == "undo":
            self.undo()
            return None
        if first is None:
            return None
        if action == "edit":
            changed = edit_pin(self.store, first.pin.alias)
            if changed:
                self.state.cursor = changed
                self.state.flash = f"✓ saved {changed}"
            return None
        if action == "touch":
            self.apply(actions.touch(selected))
            return None
        if action in actions.TOGGLES:
            self.apply(actions.toggle(self.store, selected, action))
            return None
        if action == "unpin":
            self.apply(actions.unpin(self.store, selected, self.undo_hint()))
            return None
        return None

    def open(self, view: View, action: str) -> str | None:
        plan, flash = actions.open_plan(self.store, view, action)
        if plan is None:
            self.state.flash = flash
            return None
        launch(plan)
        return "quit"

    # ---- palette -------------------------------------------------------------------------

    def palette(self, selected: list[View]) -> str | None:
        first = selected[0]
        multi = len(selected) > 1
        prompt = crumb(f"{len(selected)} selected" if multi else first.pin.alias, "actions")
        hidden: set[str] = {"palette", "select", "preview"}
        if first.expiry.expired:
            hidden |= {"open", "open_fork", "open_worktree", "touch"}
        if multi:
            hidden |= {"open", "open_fork", "open_worktree", "edit", "details"}
        counts = build_views(self.store, include_expired=True, sort=self.state.sort, with_summary=False)[1]
        table: list[tuple[str, str, str]] = []
        ids: list[str] = []
        for group in GROUPS:
            for a in ACTIONS:
                if a.group != group or a.id in hidden:
                    continue
                label = a.title
                if a.id == "open" and first.is_open:
                    label = "Resume anyway (open in another tab)"
                elif a.id == "keep":
                    label = f"Toggle keep ({'on' if first.pin.keep else 'off'})"
                elif a.id == "fork_mode":
                    label = f"Toggle fork mode ({'on' if first.pin.fork else 'off'})"
                elif a.id == "worktree_mode":
                    label = f"Toggle worktree mode ({'on' if first.pin.worktree else 'off'})"
                elif a.id == "expired":
                    label = f"{'Hide' if self.state.show_expired else 'Show'} expired ({counts})"
                elif a.id == "undo":
                    last = self.store.last_undo()
                    label = f"Undo last {last['kind']}" if last else "Undo (nothing to undo)"
                elif a.id == "sort":
                    label = f"Sort: {self.state.sort}"
                table.append((group, label, self.color(self.km.key(a.id), "dim")))
                ids.append(a.id)
        items = [Item(i, line) for i, line in zip(ids, grouped(table, self.color))]
        res = show(Screen(items, prompt=prompt, header=self.header("enter run · esc back")))
        if res is None or not res.ids or res.ids[0] == "-":
            return None
        return res.ids[0]

    # ---- details ---------------------------------------------------------------------------

    def details(self, view: View) -> str | None:
        """The whole preview on its own screen, with room for more of the last exchange; enter opens."""
        from .cost import session_cost
        from .gitutil import current_branch, is_repo
        pin = view.pin
        branch = current_branch(pin.cwd) if pin.cwd and os.path.isdir(pin.cwd) and is_repo(pin.cwd) else None
        cost = session_cost(pin.session_id, pin.transcript) if view.summary and view.summary.exists else None
        text = preview(view, cost, branch, width=terminal_width() - LIST_WIDTH_SLACK, color=self.color, exchange_lines=12)
        items = [Item("-", line) for line in text.split("\n")]
        res = show(Screen(items, prompt=crumb(pin.alias, "details"), header=self.header("enter open · esc back"),
                          disabled=True))
        if res is None:
            return None
        return self.open(view, "open")

    # ---- help / shortcuts ------------------------------------------------------------------

    def help_screen(self) -> None:
        while True:
            table = [(a.group, a.title, self.km.key(a.id) or self.color("(unbound)", "dim")) for g in GROUPS
                     for a in ACTIONS if a.group == g]
            ids = [a.id for g in GROUPS for a in ACTIONS if a.group == g]
            items = [Item(i, line) for i, line in zip(ids, grouped(table, self.color))]
            hints = "enter rebind · ctrl-r reset row · alt-r reset all · esc back"
            extra = [self.color("keymap: " + config.tilde(config.keymap_file()), "dim")]
            if alt_keys_note():
                extra.append(self.color(alt_keys_note(), "dim"))
            header = self.header(hints, *extra, legend=legend())
            res = show(Screen(items, prompt=crumb("help"), header=header, expect=["ctrl-r", "alt-r"]))
            self.state.flash = ""
            if res is None:
                return
            target = res.ids[0] if res.ids and res.ids[0] != "-" else None
            if res.key == "alt-r":
                self.km.reset(); self.km.save()
                self.state.flash = "✓ keymap reset to defaults"
                continue
            if target is None:
                continue
            if res.key == "ctrl-r":
                self.km.reset(target); self.km.save()
                self.state.flash = f"✓ {BY_ID[target].label}: reset to {self.km.key(target) or '(unbound)'}"
                continue
            self.rebind(target)

    def rebind(self, action_id: str) -> None:
        label = BY_ID[action_id].label
        where = crumb("help", label)
        note = "e.g. alt-t, f5; empty unbinds"
        while True:
            try:
                key = prompt.text(f'new key for "{label}"', self.km.key(action_id), crumb=where, note=note)
            except prompt.Cancelled:
                return
            err = validate_key(key)
            if err:
                note = self.color(f"✗ {err}", "bold")
                continue
            conflicts = self.km.conflicts(action_id, key)
            warn = key_warning(key)
            notes = []
            if conflicts:
                notes.append("conflicts: " + ", ".join(BY_ID[c].label for c in conflicts))
            if warn:
                notes.append(warn)
            if notes:
                try:
                    if not prompt.yesno("bind anyway?", False, crumb=where, notes=notes):
                        continue
                except prompt.Cancelled:
                    return
            for c in conflicts:
                self.km.set(c, "")
            self.km.set(action_id, key)
            self.km.save()
            self.state.flash = f"✓ {label}: {key or '(unbound)'}"
            return

    # ---- new pin -------------------------------------------------------------------------------

    def new_pin(self) -> None:
        pinned = {p.session_id: p.alias for p in self.store.pins}
        paths = iter_transcripts()[:200]
        summaries = [read_summary(p) for p in paths]
        pairs = [(s, pinned.get(s.session_id, "")) for s in summaries if s.exists]
        if not pairs:
            self.state.flash = "no sessions found under " + config.tilde(config.projects_dir())
            return
        width = terminal_width() - LIST_WIDTH_SLACK
        cols = session_layout(pairs, width)
        items = [Item("-", session_label_row(cols, self.color))]
        items += [Item(s.path, line)
                  for (s, _), line in zip(pairs, session_rows(pairs, width=width, color=self.color,
                                                               sep=FZF_COLUMN_SEP, cols=cols))]
        res = show(Screen(items, prompt=crumb("new"), header=self.header("enter pin · esc back"), header_lines=1,
                          preview=Hook("spreview"), preview_label=" session ", nth=SESSIONS_NTH,
                          counter="sessions"))
        if res is None or not res.ids:
            return
        summary = next(s for s, _ in pairs if s.path == res.ids[0])
        if summary.session_id in pinned:
            self.state.flash = f"already pinned as {pinned[summary.session_id]}"
            self.state.cursor = pinned[summary.session_id]
            return
        suggestion = next_free_alias(kebab(summary.title), self.store.aliases())
        note = "suggested from the title"
        while True:
            try:
                alias = prompt.text("alias", suggestion, crumb=crumb("new", "alias"), note=note) or suggestion
            except prompt.Cancelled:
                return
            try:
                self.apply(actions.pin_session(self.store, summary, alias))
            except PinError as e:
                note = self.color(f"✗ {e}", "bold")
                continue
            return

    # ---- prune / undo -----------------------------------------------------------------------------

    def prune(self) -> None:
        views, _ = build_views(self.store, include_expired=True, with_summary=False)

        def confirm(expired: list[str]) -> bool:
            try:
                return prompt.yesno("unpin them? (pin undo restores)", True, crumb=crumb("prune"),
                                    notes=[f"prune {len(expired)} expired pin(s): {', '.join(expired)}"])
            except prompt.Cancelled:
                return False

        self.apply(actions.prune(self.store, views, confirm, self.undo_hint()))

    def undo(self) -> None:
        self.apply(actions.undo(self.store))

