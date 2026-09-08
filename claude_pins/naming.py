"""Naming a Claude session after its pin.

A pin is invisible from inside Claude: its ``/resume`` picker, prompt box and terminal title show the
session's own name. So pinning renames the session to ``📌 <alias>``, the way ``/rename`` does it: one
custom-title record appended to the transcript, which the picker reads at once and a running session
adopts within a few turns (it re-reads the last such record before each of its own metadata writes).
Unpinning puts the name the session had back, or clears it. Every rename goes through here, and each
one first checks that the session still carries the name pins gave it: a name the user set inside the
session since is theirs and is left alone.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .model import Pin
from .sessions import find_transcript
from .transcript import read_summary

GLYPH = "📌"          # the session name's glyph is data shared across terminals, not the theme's


def pin_name(alias: str) -> str:
    """The session name a pin gives its session: ``📌 alias``."""
    return f"{GLYPH} {alias}"


def current_title(transcript: str | os.PathLike) -> str | None:
    """The session's custom title ("" when it has none), or None when the transcript is gone."""
    summary = read_summary(transcript)
    return summary.custom_title if summary.exists else None


def set_title(transcript: str | os.PathLike, session_id: str, title: str) -> bool:
    """Append the custom-title record (an empty title clears the name) and keep the sidecar in step.
    False when the transcript could not be written.

    The line is byte for byte what ``/rename`` writes, compact JSON with the glyph unescaped: Claude
    recognises a title record by the substrings ``"type":"custom-title"`` and ``"customTitle":"``, and
    a line with spaces after the colons was ignored and overridden by the session's own re-append.
    The sidecar ``<dir>/<session id>/custom-title.json`` is Claude's fallback when neither the head nor
    the tail of the transcript holds a title record: it is rewritten when it exists and deleted on a
    clear, as Claude does, and never created.
    """
    path = Path(transcript)
    record = {"type": "custom-title", "customTitle": title, "sessionId": session_id}
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"
    try:
        with open(path, "r+b") as fh:
            fh.seek(0, os.SEEK_END)
            end = fh.tell()
            if end:
                fh.seek(end - 1)
                if fh.read(1) != b"\n":       # a transcript cut mid-record: do not glue onto its last line
                    fh.write(b"\n")
            fh.write(line.encode("utf-8"))
    except OSError:
        return False
    sidecar = path.parent / path.stem / "custom-title.json"
    if sidecar.is_file():
        try:
            if title:
                sidecar.write_text(json.dumps({"customTitle": title}, ensure_ascii=False), encoding="utf-8")
            else:
                sidecar.unlink()
        except OSError:
            pass
    return True


def _transcript(pin: Pin) -> Path | None:
    return find_transcript(pin.session_id, hint=pin.transcript)


def claim(pin: Pin) -> str:
    """Name a newly pinned session after its pin, keeping the name it had in ``pin.prior_title``.
    Returns the line to report, "" when there is no transcript to write to."""
    path = _transcript(pin)
    if path is None:
        return ""
    pin.prior_title = current_title(path) or ""
    name = pin_name(pin.alias)
    return f"session named {name}" if set_title(path, pin.session_id, name) else ""


def rename(pin: Pin, old_alias: str) -> str:
    """After the store renamed ``pin`` from ``old_alias``: rename the session too, if it still carries
    ``📌 old_alias``. Returns the line to report, "" when nothing was written."""
    path = _transcript(pin)
    if path is None or current_title(path) != pin_name(old_alias):
        return ""
    name = pin_name(pin.alias)
    return f"session named {name}" if set_title(path, pin.session_id, name) else ""


def restore(pin: Pin) -> str:
    """On unpin: put the prior name back, or clear the name, if the session still carries the pin's.
    Returns the line to report, "" when nothing was written."""
    path = _transcript(pin)
    if path is None or current_title(path) != pin_name(pin.alias):
        return ""
    if not set_title(path, pin.session_id, pin.prior_title):
        return ""
    return f'session named "{pin.prior_title}" again' if pin.prior_title else "session name cleared"


def reclaim(pin: Pin) -> str:
    """On undo of an unpin: name the session after the restored pin again, if it still carries what the
    unpin left (the prior name, or none). Returns the line to report, "" when nothing was written."""
    path = _transcript(pin)
    if path is None or current_title(path) != pin.prior_title:
        return ""
    name = pin_name(pin.alias)
    return f"session named {name}" if set_title(path, pin.session_id, name) else ""


__all__ = ["GLYPH", "pin_name", "current_title", "set_title", "claim", "rename", "restore", "reclaim"]
