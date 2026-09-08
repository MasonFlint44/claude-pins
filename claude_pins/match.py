"""Loose matching: ``pins <words…>`` over pins, and ``pins add <session>`` over recent sessions."""

from __future__ import annotations

import re

from .model import Pin, is_session_id
from .sessions import iter_transcripts
from .transcript import Summary, read_summary

RECENT = 200  # sessions considered by title match and ``pins sessions``, newest first
_ID_PREFIX = re.compile(r"^[0-9a-f-]{8,}$")
ID_PREFIX_FLOOR = 8


def short_ids(ids: list[str]) -> list[str]:
    """The prefix each id is listed by: eight characters, or more when another listed id shares them
    (git's abbreviation rule), so what a listing prints always resolves. A UUID's ninth character is a
    hyphen, so a clash grows the prefix to ten."""
    out = []
    for sid in ids:
        n = ID_PREFIX_FLOOR
        while n < len(sid) and any(o != sid and o.startswith(sid[:n]) for o in ids):
            n += 1
        out.append(sid[:n])
    return out


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


def recent_sessions(limit: int = RECENT) -> list[Summary]:
    """Summaries of the newest transcripts, newest first."""
    out = []
    for path in iter_transcripts()[:limit]:
        s = read_summary(path)
        if s.exists and s.session_id:
            out.append(s)
    return out


def match_sessions(text: str, sessions: list[Summary] | None = None) -> list[Summary]:
    """Sessions named by ``text``: a full id, a unique id prefix (8+ characters), or words that
    must all appear in the title or directory. An exact id always wins alone."""
    sessions = recent_sessions() if sessions is None else sessions
    key = text.strip().lower()
    if is_session_id(key):
        return [s for s in sessions if s.session_id == key]
    if _ID_PREFIX.match(key):
        hits = [s for s in sessions if s.session_id.startswith(key)]
        if hits:
            return hits
    terms = key.split()
    if not terms:
        return []
    return [s for s in sessions if all(t in f"{s.title} {s.cwd}".lower() for t in terms)]
