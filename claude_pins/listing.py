"""Build the sorted, filtered list of pin views shared by the picker, the menu and ``pin list``."""

from __future__ import annotations

from . import config
from .model import Pin
from .render import View
from .sessions import expiry_for, find_transcript, open_session_ids
from .store import Store
from .transcript import read_summary

SORTS = config.SORT_ORDERS


def view_for(pin: Pin, open_ids: set[str] | None = None, *, with_summary: bool = True) -> View:
    transcript = find_transcript(pin.session_id, hint=pin.transcript)
    e = expiry_for(transcript)
    summary = read_summary(transcript) if (with_summary and transcript) else None
    return View(pin, e, is_open=(open_ids is not None and pin.session_id in open_ids), summary=summary)


def build_views(store: Store, *, include_expired: bool = False, sort: str = "recency",
                with_summary: bool = True, open_ids: set[str] | None = None) -> tuple[list[View], int]:
    """(views, number of expired pins hidden or shown)."""
    open_ids = open_session_ids() if open_ids is None else open_ids
    views = [view_for(p, open_ids, with_summary=with_summary) for p in store.pins]
    expired = sum(1 for v in views if v.expiry.expired)
    if not include_expired:
        views = [v for v in views if not v.expiry.expired]
    if sort == "alias":
        views.sort(key=lambda v: v.pin.alias)
    elif sort == "pinned":
        views.sort(key=lambda v: v.pin.pinned_at)
    else:  # recency: most recently written transcript first; expired last
        views.sort(key=lambda v: (v.expiry.expired, v.expiry.age_seconds))
    return views, expired


def next_sort(current: str) -> str:
    i = SORTS.index(current) if current in SORTS else 0
    return SORTS[(i + 1) % len(SORTS)]
