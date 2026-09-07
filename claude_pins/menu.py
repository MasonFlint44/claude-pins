"""Fallback when fzf is missing or too old: numbered rows, letter-prefixed actions."""

from __future__ import annotations

import re
import sys

from . import config, prompt
from .cost import session_cost
from .editor import edit_pin
from .gitutil import current_branch, is_repo
from .listing import build_views, next_sort
from .model import Pin, PinError, kebab, next_free_alias
from .opener import launch, plan_open, touch_kept, touch_pin
from .render import legend, palette, preview, rows, session_rows
from .theme import ERROR, SUCCESS
from .sessions import iter_transcripts
from .store import Store
from .transcript import read_summary

ROW_ACTIONS = "N open · oN fork · wN worktree · tN touch · eN edit · xN unpin · pN preview"
LIST_ACTIONS = "n new · a show expired · p prune · z undo · s sort · ? help · q quit"
_CMD = re.compile(r"^([a-z?]?)(\d*)$")


def run_menu(store: Store, *, query: str = "", sort: str | None = None, reason: str = "") -> int:
    color = palette(sys.stdout)
    state = {"sort": sort or config.default_sort(), "expired": False, "flash": ""}
    while True:
        store = Store(store.path).load()
        touch_kept(store)
        views, expired = build_views(store, include_expired=state["expired"], sort=state["sort"])
        if query:
            q = query.lower()
            views = [v for v in views if q in f"{v.pin.alias} {v.title}".lower()]
        print()
        note = reason or f"install fzf ≥ 0.44 for the full picker: pin doctor"
        print(f" pins{' › ' + query if query else ''}".ljust(40) + color(f"({note})", "dim"))
        if state["flash"]:
            print(f" {color(state['flash'], ERROR if state['flash'].startswith('✗') else SUCCESS)}")
            state["flash"] = ""
        if views:
            for line in rows(views, color=color, numbered=True):
                print(f" {line}")
        else:
            print(color("  No pins yet. n pins a recent session, or run /pins:pin inside a Claude session.", "dim"))
        if expired and not state["expired"]:
            print(color(f"  {expired} expired · a show · p prune", "dim"))
        print()
        print(color(f"  {ROW_ACTIONS}", "dim"))
        print(color(f"  {LIST_ACTIONS}", "dim"))
        try:
            raw = input(" > ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            return 0
        m = _CMD.match(raw)
        if not m:
            state["flash"] = f"? unknown command {raw!r}"
            continue
        letter, num = m.group(1), m.group(2)
        if not letter and not num:
            continue
        if num:
            i = int(num)
            if not 1 <= i <= len(views):
                state["flash"] = f"no row {i}"
                continue
            v = views[i - 1]
            pin = v.pin
            if letter in ("", "o", "w"):
                if v.expiry.expired:
                    state["flash"] = f"✗ {pin.alias} has expired"
                    continue
                try:
                    plan = plan_open(store, pin, fork=True if letter == "o" else None, worktree="" if letter == "w" else None)
                except PinError as e:
                    state["flash"] = f"✗ {e}"
                    continue
                if plan is None:
                    state["flash"] = "cancelled"
                    continue
                launch(plan)
                return 0
            if letter == "t":
                state["flash"] = f"✓ touched {pin.alias}" if touch_pin(pin) else f"✗ transcript for {pin.alias} is gone"
            elif letter == "e":
                changed = edit_pin(store, pin.alias)
                state["flash"] = f"✓ saved {changed}" if changed else "edit cancelled"
            elif letter == "x":
                store.unpin(pin.alias); store.save()
                state["flash"] = f"✓ unpinned {pin.alias} · z undo"
            elif letter == "p":
                branch = current_branch(pin.cwd) if pin.cwd and is_repo(pin.cwd) else None
                cost = session_cost(pin.session_id, pin.transcript)
                print()
                print(preview(v, cost, branch, color=color))
            else:
                state["flash"] = f"? unknown row action {letter!r}"
            continue
        if letter == "q":
            return 0
        if letter == "?":
            print(f"\n  {legend()}\n")
        elif letter == "a":
            state["expired"] = not state["expired"]
        elif letter == "s":
            state["sort"] = next_sort(state["sort"]); state["flash"] = f"sort: {state['sort']}"
        elif letter == "z":
            try:
                kind, restored = store.restore_last(); store.save()
                state["flash"] = f"✓ restored {', '.join(p.alias for p in restored) or 'nothing'} ({kind})"
            except PinError as e:
                state["flash"] = str(e)
        elif letter == "p":
            all_views, _ = build_views(store, include_expired=True, with_summary=False)
            dead = [x.pin.alias for x in all_views if x.expiry.expired]
            if not dead:
                state["flash"] = "nothing to prune"; continue
            print(f" prune {len(dead)} expired pin(s): {', '.join(dead)}")
            try:
                if prompt.yesno("unpin them? (pin undo restores)", True):
                    store.unpin_many(dead, kind="prune"); store.save()
                    state["flash"] = f"✓ pruned {len(dead)} · z undo"
            except prompt.Cancelled:
                pass
        elif letter == "n":
            new_pin_menu(store, state, color)
        elif letter in ("o", "w", "t", "e", "x"):
            state["flash"] = f"{letter} needs a row number (e.g. {letter}1)"
        else:
            state["flash"] = f"? unknown command {raw!r}"


def new_pin_menu(store: Store, state: dict, color) -> None:
    pinned = {p.session_id: p.alias for p in store.pins}
    summaries = [read_summary(p) for p in iter_transcripts()[:30]]
    pairs = [(s, s.session_id in pinned) for s in summaries if s.exists]
    if not pairs:
        state["flash"] = "no sessions found"
        return
    print("\n pins › new")
    for i, line in enumerate(session_rows(pairs, color=color), 1):
        print(f"  {i:>2}  {line}")
    try:
        raw = input(" session number (enter cancels): ").strip()
    except (KeyboardInterrupt, EOFError):
        print(); return
    if not raw.isdigit() or not 1 <= int(raw) <= len(pairs):
        return
    s = pairs[int(raw) - 1][0]
    if s.session_id in pinned:
        state["flash"] = f"already pinned as {pinned[s.session_id]}"
        return
    suggestion = next_free_alias(kebab(s.title), store.aliases())
    try:
        alias = prompt.text("alias", suggestion) or suggestion
    except prompt.Cancelled:
        return
    try:
        store.add(Pin(alias=alias, session_id=s.session_id, title=s.title, cwd=s.cwd, transcript=s.path))
        store.save()
        state["flash"] = f"✓ pinned as {alias}"
    except PinError as e:
        state["flash"] = f"✗ {e}"
