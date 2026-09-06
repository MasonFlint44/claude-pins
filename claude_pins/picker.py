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
from .render import LEGEND, View, palette, rows, session_rows, terminal_width
from .sessions import iter_transcripts
from .store import Store
from .transcript import read_summary

EMPTY_MESSAGE = "No pins yet. {new} pins a recent session, or run /pin inside a Claude session."


def pin_exe() -> str:
    """How the preview command re-enters this tool."""
    exe = os.environ.get("CLAUDE_PINS_EXE") or os.path.abspath(sys.argv[0])
    return shlex.quote(exe)


@dataclass
class State:
    sort: str
    show_expired: bool = False
    query: str = ""
    flash: str = ""
    cursor: str = ""        # alias to highlight
    preview: bool = True


class Picker:
    def __init__(self, store: Store, keymap: Keymap | None = None, *, query: str = "", sort: str | None = None):
        self.store = store
        self.km = keymap or Keymap.load()
        self.state = State(sort=sort or config.default_sort(), query=query)
        self.color = palette(sys.stdout)

    # ---- helpers -------------------------------------------------------------------

    def hints(self, *ids: str) -> str:
        parts = [self.km.hint(i) for i in ids if self.km.hint(i)]
        return self.color(" · ".join(parts), "dim")

    def header(self, hints: str) -> str:
        width = terminal_width()
        plain_len = len(_strip(hints))
        line1 = " " * max(0, width - plain_len - 3) + hints
        if self.state.flash:
            return f"{line1}\n{self.color(self.state.flash, 'green')}"
        return line1

    def expect_keys(self, *ids: str) -> tuple[list[str], dict[str, str]]:
        keys, mapping = [], {}
        for i in ids:
            k = self.km.key(i)
            if k and k != "enter" and k != "tab":
                keys.append(k)
                mapping[k] = i
        return keys, mapping

    def reload(self) -> None:
        self.store = Store(self.store.path).load()

    # ---- main picker ----------------------------------------------------------------

    def run(self) -> int:
        """Loop until the user opens a pin (exec) or leaves (esc)."""
        while True:
            self.reload()
            touched = touch_kept(self.store)
            views, expired = build_views(self.store, include_expired=self.state.show_expired, sort=self.state.sort)
            items = []
            if views:
                for v, line in zip(views, rows(views, color=self.color)):
                    items.append(fzf.Item(v.pin.alias, line, f"{v.pin.alias} {v.title}"))
            else:
                items.append(fzf.Item("-", self.color(EMPTY_MESSAGE.format(new=self.km.key("new") or "pin add"), "dim"), " "))
            main_actions = ["open_fork", "open_worktree", "palette", "edit", "touch", "keep", "fork_mode",
                            "worktree_mode", "unpin", "new", "expired", "prune", "undo", "sort", "preview", "help"]
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
            res = fzf.run(items, prompt=f"pins › ", header=self.header(self.hints("open", "palette", "new", "help")),
                          expect=expect, query=self.state.query, multi=bool(views), pos=pos,
                          preview=preview_cmd, preview_label_cmd="echo ' '{1}' '", border_label=footer)
            self.state.flash = ""
            if res is None:
                return 0
            self.state.query = res.query
            ids = [i for i in res.ids if i != "-"]
            if ids:
                self.state.cursor = ids[0]
            action = mapping.get(res.key, "open") if res.key else "open"
            outcome = self.dispatch(action, ids, views)
            if outcome == "quit":
                return 0

    # ---- dispatch ---------------------------------------------------------------------

    def dispatch(self, action: str, ids: list[str], views: list[View]) -> str | None:
        by_alias = {v.pin.alias: v for v in views}
        selected = [by_alias[i] for i in ids if i in by_alias]
        first = selected[0] if selected else None
        if action in ("open", "open_fork", "open_worktree"):
            if first is None:
                return None
            if first.expiry.expired:
                self.state.flash = f"✗ {first.pin.alias} has expired · unpin it or pin prune"
                return None
            return self.open(first, action)
        if action == "palette":
            if first is None:
                return None
            chosen = self.palette(selected)
            if chosen:
                return self.dispatch(chosen, ids, views)
            return None
        if action == "help":
            self.help_screen()
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
            self.state.preview = not self.state.preview
            return None
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
        crumb = f"pins › {len(selected)} selected › actions › " if multi else f"pins › {first.pin.alias} › actions › "
        items: list[fzf.Item] = []
        hidden: set[str] = set()
        if first.expiry.expired:
            hidden |= {"open", "open_fork", "open_worktree", "touch"}
        if multi:
            hidden |= {"open", "open_fork", "open_worktree", "edit"}
        counts = build_views(self.store, include_expired=True, sort=self.state.sort, with_summary=False)[1]
        for group in GROUPS:
            group_items = []
            for a in ACTIONS:
                if a.group != group or a.id in hidden or a.id in ("palette", "select", "preview"):
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
                key = self.km.key(a.id)
                group_items.append(fzf.Item(a.id, f"{label:<30}{self.color(key, 'dim')}", f"{label} {key}"))
            if group_items:
                items.append(fzf.Item("-", self.color(f"── {group} ──", "dim"), " "))
                items.extend(group_items)
        hints = self.color("enter run · esc back", "dim")
        res = fzf.run(items, prompt=crumb, header=self.header(hints), expect=[], pos=2)
        if res is None or not res.ids or res.ids[0] == "-":
            return None
        return res.ids[0]

    # ---- help / shortcuts ------------------------------------------------------------------

    def help_screen(self) -> None:
        while True:
            items = [fzf.Item("-", f"{LEGEND}      {self.color('keymap: ' + config.tilde(config.keymap_file()), 'dim')}", " ")]
            for group in GROUPS:
                items.append(fzf.Item("-", self.color(f"── {group} ──", "dim"), " "))
                for a in ACTIONS:
                    if a.group == group:
                        key = self.km.key(a.id) or self.color("(unbound)", "dim")
                        items.append(fzf.Item(a.id, f"{a.title:<30}{key}", f"{a.title} {self.km.key(a.id)}"))
            hints = self.color("enter rebind · ctrl-r reset row · ctrl-alt-r reset all · esc back", "dim")
            res = fzf.run(items, prompt="pins › help › ", header=self.header(hints), expect=["ctrl-r", "ctrl-alt-r"], pos=3)
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
        items = [fzf.Item(s.path, line, f"{s.title} {s.cwd}") for (s, _), line in zip(pairs, session_rows(pairs, color=self.color))]
        hints = self.color("enter pin · esc back", "dim")
        res = fzf.run(items, prompt="pins › new › ", header=self.header(hints), expect=[],
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


def _strip(text: str) -> str:
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", text)
