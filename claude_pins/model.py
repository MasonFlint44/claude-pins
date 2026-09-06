"""The pin record and its validation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any

ALIAS_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

PERMISSION_MODES = ("default", "acceptEdits", "plan", "auto", "bypassPermissions")
EFFORT_LEVELS = ("low", "medium", "high", "max")


class PinError(Exception):
    """A user-facing error; the message is printed as-is."""


def is_alias(text: str) -> bool:
    return bool(ALIAS_RE.match(text))


def is_session_id(text: str) -> bool:
    return bool(UUID_RE.match(text.lower()))


def kebab(text: str, limit: int = 40) -> str:
    """Turn free text into a kebab-case alias (``Pin Claude sessions`` → ``pin-claude-sessions``)."""
    words = re.findall(r"[A-Za-z0-9]+", text.lower())
    out = ""
    for w in words:
        candidate = (out + "-" + w) if out else w
        if len(candidate) > limit and out:
            break
        out = candidate
    return out[:limit].strip("-") or "pin"


def validate_alias(alias: str) -> str:
    alias = alias.strip()
    if not alias:
        raise PinError("alias cannot be empty")
    if not is_alias(alias):
        raise PinError(
            f"invalid alias {alias!r}: use lowercase letters, digits and single dashes (e.g. standup-prep)"
        )
    return alias


def next_free_alias(alias: str, taken) -> str:
    """``alias`` if free, else ``alias-2``, ``alias-3``… (the design's suggestion rule)."""
    if alias not in taken:
        return alias
    base = re.sub(r"-\d+$", "", alias) or alias
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


@dataclass
class Launch:
    model: str | None = None
    effort: str | None = None
    permission_mode: str | None = None

    def flags(self) -> list[str]:
        out: list[str] = []
        if self.model:
            out += ["--model", self.model]
        if self.effort:
            out += ["--effort", self.effort]
        if self.permission_mode:
            out += ["--permission-mode", self.permission_mode]
        return out

    def summary(self) -> str:
        parts = [self.model or "default model"]
        if self.effort:
            parts.append(f"effort {self.effort}")
        if self.permission_mode:
            parts.append(f"mode {self.permission_mode}")
        return " · ".join(parts)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Launch":
        data = data or {}
        return cls(
            model=data.get("model") or None,
            effort=data.get("effort") or None,
            permission_mode=data.get("permission_mode") or None,
        )

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v}


@dataclass
class Pin:
    alias: str
    session_id: str
    title: str = ""
    cwd: str = ""
    transcript: str = ""
    note: str = ""
    pinned_at: str = ""
    fork: bool = False
    worktree: bool = False
    keep: bool = False
    launch: Launch = field(default_factory=Launch)

    def to_dict(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "title": self.title,
            "session_id": self.session_id,
            "cwd": self.cwd,
            "transcript": self.transcript,
            "note": self.note,
            "pinned_at": self.pinned_at,
            "fork": bool(self.fork),
            "worktree": bool(self.worktree),
            "keep": bool(self.keep),
            "launch": self.launch.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Pin":
        return cls(
            alias=str(data.get("alias", "")),
            session_id=str(data.get("session_id", "")),
            title=str(data.get("title", "") or ""),
            cwd=str(data.get("cwd", "") or ""),
            transcript=str(data.get("transcript", "") or ""),
            note=str(data.get("note", "") or ""),
            pinned_at=str(data.get("pinned_at", "") or ""),
            fork=bool(data.get("fork", False)),
            worktree=bool(data.get("worktree", False)),
            keep=bool(data.get("keep", False)),
            launch=Launch.from_dict(data.get("launch")),
        )

    def copy(self) -> "Pin":
        return Pin.from_dict(self.to_dict())
