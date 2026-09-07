"""The fzf screens: main picker, actions palette, help/shortcuts, new pin, prune/undo flows."""

from __future__ import annotations

import os
import shlex
import sys
from dataclasses import dataclass

from . import config, fzf, prompt
from .editor import edit_pin
from .keymap import ACTIONS, BY_ID, GROUPS, Keymap, key_warning, validate_key
from .listing import build_views, next_sort
from .model import Pin, PinError, kebab, next_free_alias
from .opener import launch, plan_open, touch_kept, touch_pin
from .render import (LEGEND, View, crumb, grouped, label_row, layout, palette, preview, rows, session_rows,
                     terminal_height, terminal_width)
from .sessions import iter_transcripts
from .store import Store
from .transcript import read_summary

EMPTY_MESSAGE = "No pins yet. {new} pins a recent session, or run /pins:pin inside a Claude session."
TOO_SHORT_NOTE = "preview hidden: terminal too short"
LIST_WIDTH_SLACK = 4        # fzf's pointer and marker columns plus a little room at the right edge


def pin_exe() -> str:
    """How the preview command re-enters this tool."""
    exe = os.environ.get("CLAUDE_PINS_EXE") or os.path.abspath(sys.argv[0])
    return shlex.quote(exe)


def list_items(views: list[View], km: Keymap, color) -> list[fzf.Item]:
    """The main list: the sticky label row (id ``-``) first, then one row per pin, at the terminal's width.
    Shared with ``pin _rows`` so a reload draws exactly what a restart would."""
    width = terminal_width() - LIST_WIDTH_SLACK
    if not views:
        return [fzf.Item("-", color(EMPTY_MESSAGE.format(new=km.key("new") or "pin add"), "dim"))]
    cols = layout(views, width)
    items = [fzf.Item("-", label_row(cols, color))]
    for v, line in zip(views, rows(views, width=width, color=color, cols=cols)):
        items.append(fzf.Item(v.pin.alias, line))
    return items


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
        self.fzf_version = fzf.fzf_version()

    # ---- helpers -------------------------------------------------------------------

    def hints(self, *ids: str) -> str:
        parts = [self.km.hint(i) for i in ids if self.km.hint(i)]
        return self.color(" · ".join(parts), "dim")

    def header(self, hints: str, *notes: str) -> str:
        """The lines above the prompt (fzf's --header-first): hints first, then the screen's notes. A flash
        takes the second line, displacing the note that was there, so the list never moves."""
        lines = [hints, *notes]
        if self.state.flash:
            flash = self.color(self.state.flash, "red" if self.state.flash.startswith("✗") else "green")
            lines[1:2] = [flash]
        return "\n".join(lines)

    def preview_fits(self, *, bottom_border: bool) -> bool:
        return fzf.preview_fits(terminal_height(), bottom_border=bottom_border)

    def expect_keys(self, *ids: str) -> tuple[list[str], dict[str, str]]:
        """The --expect keys (each restarts fzf) for these actions; actions with a ``bind`` stay inside fzf."""
        keys, mapping = [], {}
        for i in ids:
            k = self.km.key(i)
            if k and k != "enter" and k != "tab" and not BY_ID[i].bind:
                keys.append(k)
                mapping[k] = i
        return keys, mapping

    def rows_command(self) -> str:
        """``pin _rows`` for fzf's reload, with the launch width as the fallback for builds without
        $FZF_COLUMNS (the width fzf reports wins where it exists)."""
        cmd = f"COLUMNS={terminal_width()} {pin_exe()} _rows --sort {self.state.sort}"
        return cmd + (" --all" if self.state.show_expired else "")

    def reload(self) -> None:
        self.store = Store(self.store.path).load()

    # ---- main picker ----------------------------------------------------------------

    def run(self) -> int:
        """Loop until the user opens a pin (exec) or leaves (esc)."""
        while True:
            self.reload()
            touched = touch_kept(self.store)
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
            preview_cmd = f"{pin_exe()} _preview {{1}}" if self.state.preview else None
            hints = self.hints("open", "palette", "edit", "new", "details", "help")
            note = self.color(TOO_SHORT_NOTE, "dim")
            header = self.header(hints, self.color(LEGEND, "dim"))
            env = {fzf.HEADER_VAR: header, fzf.NOTE_VAR: note}
            if self.state.preview and not self.preview_fits(bottom_border=bool(footer)):
                header += "\n" + note
            # The list reloads in place on the refresh key and, where fzf has the event, on resize; the
            # too-short note follows the height on resize too, or on every cursor move and keystroke
            # where the event is missing (0.44).
            binds: list[tuple[str, str]] = []
            reload = f"reload({self.rows_command()})"
            if self.km.key("refresh"):
                binds.append((self.km.key("refresh"), reload))
            transform = f"transform-header({fzf.header_transform(bottom_border=bool(footer))})"
            if fzf.supports("resize", self.fzf_version):
                binds.append(("resize", reload + ("+" + transform if self.state.preview else "")))
            elif self.state.preview:
                binds += [("focus", transform), ("change", transform)]
            res = fzf.run(items, prompt=crumb(), header=header, header_lines=1, expect=expect, query=self.state.query,
                          multi=bool(views), pos=pos, preview=preview_cmd, preview_label_cmd="echo ' '{1}' '",
                          border_label=footer, binds=binds, env=env,
                          info_command=fzf.INFO_COMMAND if fzf.supports("info-command", self.fzf_version) else None)
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
            if first.expiry.expired:
                alias = first.pin.alias
                self.state.flash = (f"✗ {alias} has expired · unpin it or pin prune" if first.summary else
                                    f"✗ {alias}: transcript for session {first.pin.session_id[:8]}… is gone (expired)"
                                    f" · pin unpin {alias}")
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
            self.state.sort = next_sort(self.state.sort)
            self.state.flash = f"sort: {self.state.sort}"
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
        names = ", ".join(v.pin.alias for v in selected)
        if action == "touch":
            ok = [v.pin.alias for v in selected if touch_pin(v.pin)]
            self.state.flash = f"✓ touched {', '.join(ok)}" if ok else "✗ nothing to touch (transcripts gone)"
            return None
        if action in ("keep", "fork_mode", "worktree_mode"):
            attr = {"keep": "keep", "fork_mode": "fork", "worktree_mode": "worktree"}[action]
            value = not getattr(first.pin, attr)
            for v in selected:
                setattr(self.store.require(v.pin.alias), attr, value)
            self.store.save()
            self.state.flash = f"✓ {attr} {'on' if value else 'off'} for {names}"
            return None
        if action == "unpin":
            self.store.unpin_many([v.pin.alias for v in selected])
            self.store.save()
            undo_key = self.km.key("undo") or "pin undo"
            self.state.flash = f"✓ unpinned {names} · {undo_key} undo"
            self.state.cursor = ""
            return None
        return None

    def open(self, view: View, action: str) -> str | None:
        fork = True if action == "open_fork" else None
        worktree = "" if action == "open_worktree" else None
        try:
            plan = plan_open(self.store, view.pin, fork=fork, worktree=worktree)
        except PinError as e:
            self.state.flash = f"✗ {e}"
            return None
        if plan is None:
            self.state.flash = "cancelled"
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
        items = [fzf.Item(i, line) for i, line in zip(ids, grouped(table, self.color))]
        hints = self.color("enter run · esc back", "dim")
        res = fzf.run(items, prompt=prompt, header=self.header(hints), expect=[], info="hidden")
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
        items = [fzf.Item("-", line) for line in text.split("\n")]
        hints = self.color("enter open · esc back", "dim")
        res = fzf.run(items, prompt=crumb(pin.alias, "details"), header=self.header(hints), expect=[], disabled=True,
                      info="hidden")
        if res is None:
            return None
        return self.open(view, "open")

    # ---- help / shortcuts ------------------------------------------------------------------

    def help_screen(self) -> None:
        while True:
            table = [(a.group, a.title, self.km.key(a.id) or self.color("(unbound)", "dim")) for g in GROUPS
                     for a in ACTIONS if a.group == g]
            ids = [a.id for g in GROUPS for a in ACTIONS if a.group == g]
            items = [fzf.Item(i, line) for i, line in zip(ids, grouped(table, self.color))]
            hints = self.color("enter rebind · ctrl-r reset row · ctrl-alt-r reset all · esc back", "dim")
            notes = (self.color(LEGEND, "dim"), self.color("keymap: " + config.tilde(config.keymap_file()), "dim"))
            res = fzf.run(items, prompt=crumb("help"), header=self.header(hints, *notes), expect=["ctrl-r", "ctrl-alt-r"],
                          info="hidden")
            self.state.flash = ""
            if res is None:
                return
            target = res.ids[0] if res.ids and res.ids[0] != "-" else None
            if res.key == "ctrl-alt-r":
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
        while True:
            try:
                key = prompt.text(f'new key for "{label}" (e.g. alt-t, f5; empty unbinds)', self.km.key(action_id))
            except prompt.Cancelled:
                return
            err = validate_key(key)
            if err:
                print(f" ✗ {err}")
                continue
            conflicts = self.km.conflicts(action_id, key)
            warn = key_warning(key)
            notes = []
            if conflicts:
                notes.append("conflicts: " + ", ".join(BY_ID[c].label for c in conflicts))
            if warn:
                notes.append(warn)
            if notes:
                print(" " + " · ".join(notes))
                try:
                    if not prompt.yesno("bind anyway?", False):
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
        pairs = [(s, s.session_id in pinned) for s in summaries if s.exists]
        if not pairs:
            self.state.flash = "no sessions found under " + config.tilde(config.projects_dir())
            return
        items = [fzf.Item(s.path, line)
                 for (s, _), line in zip(pairs, session_rows(pairs, width=terminal_width() - LIST_WIDTH_SLACK, color=self.color))]
        hints = self.color("enter pin · esc back", "dim")
        res = fzf.run(items, prompt=crumb("new"), header=self.header(hints), expect=[],
                      preview=f"{pin_exe()} _spreview {{1}}", preview_label_cmd="echo ' session '")
        if res is None or not res.ids:
            return
        summary = next(s for s, _ in pairs if s.path == res.ids[0])
        if summary.session_id in pinned:
            self.state.flash = f"already pinned as {pinned[summary.session_id]}"
            self.state.cursor = pinned[summary.session_id]
            return
        suggestion = next_free_alias(kebab(summary.title), self.store.aliases())
        while True:
            try:
                alias = prompt.text("alias (suggested from title · enter · ctrl-c cancel)", suggestion) or suggestion
            except prompt.Cancelled:
                return
            pin = Pin(alias=alias, session_id=summary.session_id, title=summary.title, cwd=summary.cwd,
                      transcript=summary.path)
            try:
                self.store.add(pin)
            except PinError as e:
                print(f" ✗ {e}")
                continue
            self.store.save()
            self.state.cursor = alias
            self.state.flash = f"✓ pinned as {alias}"
            return

    # ---- prune / undo -----------------------------------------------------------------------------

    def prune(self) -> None:
        views, _ = build_views(self.store, include_expired=True, with_summary=False)
        expired = [v.pin.alias for v in views if v.expiry.expired]
        if not expired:
            self.state.flash = "nothing to prune"
            return
        fzf.leave_screen()      # a text prompt follows (until 0.5.0's batch 5 makes it an fzf screen)
        print(f" prune {len(expired)} expired pin(s): {', '.join(expired)}")
        try:
            ok = prompt.yesno("unpin them? (pin undo restores)", True)
        except prompt.Cancelled:
            ok = False
        if not ok:
            self.state.flash = "prune cancelled"
            return
        self.store.unpin_many(expired, kind="prune")
        self.store.save()
        self.state.flash = f"✓ pruned {len(expired)} · {self.km.key('undo') or 'pin undo'} undo"

    def undo(self) -> None:
        try:
            kind, restored = self.store.restore_last()
        except PinError as e:
            self.state.flash = str(e)
            return
        self.store.save()
        names = ", ".join(p.alias for p in restored) or "nothing (already re-pinned)"
        self.state.flash = f"✓ restored {names} ({kind})"
        if restored:
            self.state.cursor = restored[0].alias

