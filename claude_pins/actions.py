"""What a key does to the store, shared by every picker.

The fzf screens and the built-in fallback draw differently but change the same things: touch a pin, flip
keep/fork/worktree, unpin, undo, prune, pin a session, cycle the sort. Each lives here once, takes the
store and the selected views, and answers with an :class:`Outcome`: the flash to show on the next screen
and, when the change moved a pin, the alias the cursor should land on. Nothing here draws or prompts;
the caller supplies confirmation as a callable and shows the flash its own way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .listing import next_sort
from .model import Pin, PinError
from .opener import Plan, plan_open, take_notes, touch_pin
from .render import View
from .store import Store
from .transcript import Summary

TOGGLES = {"keep": "keep", "fork_mode": "fork", "worktree_mode": "worktree"}   # action id → Pin attribute


@dataclass
class Outcome:
    flash: str = ""
    cursor: str | None = None   # alias to put the cursor on; "" clears it; None leaves it alone


def _names(views: list[View]) -> str:
    return ", ".join(v.pin.alias for v in views)


def expired_flash(view: View) -> str:
    """Why an expired pin cannot open: its transcript is on the way out or already gone."""
    alias = view.pin.alias
    if view.summary:
        return f"✗ {alias} has expired · unpin it or pin prune"
    return (f"✗ {alias}: transcript for session {view.pin.session_id[:8]}… is gone (expired)"
            f" · pin unpin {alias}")


def open_plan(store: Store, view: View, action: str) -> tuple[Plan | None, str]:
    """The launch plan for ``open``/``open_fork``/``open_worktree``, or None with the flash explaining why
    not. What the opener said on the way (a recreated worktree, an unpin) would be lost under the next
    screen, so a cancel carries those notes in its flash."""
    if view.expiry.expired:
        return None, expired_flash(view)
    fork = True if action == "open_fork" else None
    worktree = "" if action == "open_worktree" else None
    try:
        plan = plan_open(store, view.pin, fork=fork, worktree=worktree)
    except PinError as e:
        return None, f"✗ {e}"
    if plan is None:
        return None, " · ".join([*take_notes(), "cancelled"])
    return plan, ""


def touch(views: list[View]) -> Outcome:
    ok = [v.pin.alias for v in views if touch_pin(v.pin)]
    return Outcome(f"✓ touched {', '.join(ok)}" if ok else "✗ nothing to touch (transcripts gone)")


def toggle(store: Store, views: list[View], action: str) -> Outcome:
    """Flip keep, fork or worktree mode on every selected pin to the opposite of the first one's."""
    attr = TOGGLES[action]
    value = not getattr(views[0].pin, attr)
    for v in views:
        setattr(store.require(v.pin.alias), attr, value)
    store.save()
    return Outcome(f"✓ {attr} {'on' if value else 'off'} for {_names(views)}")


def unpin(store: Store, views: list[View], undo_hint: str) -> Outcome:
    store.unpin_many([v.pin.alias for v in views])
    store.save()
    return Outcome(f"✓ unpinned {_names(views)} · {undo_hint} undo", cursor="")


def undo(store: Store) -> Outcome:
    try:
        kind, restored = store.restore_last()
    except PinError as e:
        return Outcome(str(e))
    store.save()
    names = ", ".join(p.alias for p in restored) or "nothing (already re-pinned)"
    return Outcome(f"✓ restored {names} ({kind})", cursor=restored[0].alias if restored else None)


def prune(store: Store, views: list[View], confirm: Callable[[list[str]], bool], undo_hint: str) -> Outcome:
    """Unpin every expired pin among ``views`` once ``confirm(aliases)`` agrees."""
    expired = [v.pin.alias for v in views if v.expiry.expired]
    if not expired:
        return Outcome("nothing to prune")
    if not confirm(expired):
        return Outcome("prune cancelled")
    store.unpin_many(expired, kind="prune")
    store.save()
    return Outcome(f"✓ pruned {len(expired)} · {undo_hint} undo")


def pin_session(store: Store, summary: Summary, alias: str) -> Outcome:
    """Pin a session under ``alias``; raises PinError (alias taken, session pinned) for the caller to retry."""
    store.add(Pin(alias=alias, session_id=summary.session_id, title=summary.title, cwd=summary.cwd,
                  transcript=summary.path))
    store.save()
    return Outcome(f"✓ pinned as {alias}", cursor=alias)


def cycle_sort(current: str) -> tuple[str, Outcome]:
    new = next_sort(current)
    return new, Outcome(f"sort: {new}")
