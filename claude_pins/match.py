"""``pin <words…>``: loose matching over alias and title."""

from __future__ import annotations

from .model import Pin


def loose_match(pins: list[Pin], words: list[str]) -> list[Pin]:
    """Every word must appear (case-insensitive) in ``alias + title``. Exact alias wins outright."""
    if not words:
        return list(pins)
    joined = " ".join(words).strip().lower()
    exact = [p for p in pins if p.alias == joined]
    if exact:
        return exact
    terms = [w.lower() for w in joined.split() if w]
    out = []
    for p in pins:
        hay = f"{p.alias} {p.title}".lower()
        if all(t in hay for t in terms):
            out.append(p)
    return out
