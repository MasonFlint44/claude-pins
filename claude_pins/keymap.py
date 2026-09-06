"""Actions, their default keys, the ``keys.toml`` file, validation and conflicts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import config


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    key: str            # default key ("" = unbound by default)
    group: str          # open | pin | list | tool
    ellipsis: bool = False  # asks something first
    short: str = ""     # word used in the key-hint line

    @property
    def title(self) -> str:
        return self.label + ("…" if self.ellipsis else "")


ACTIONS: list[Action] = [
    Action("open", "Open", "enter", "open", short="open"),
    Action("open_fork", "Open as fork", "alt-o", "open"),
    Action("open_worktree", "Open in new worktree", "alt-w", "open"),
    Action("palette", "Actions palette", "ctrl-space", "pin", short="actions"),
    Action("edit", "Edit", "alt-e", "pin", ellipsis=True),
    Action("touch", "Touch transcript", "alt-t", "pin"),
    Action("keep", "Toggle keep", "alt-k", "pin"),
    Action("fork_mode", "Toggle fork mode", "", "pin"),
    Action("worktree_mode", "Toggle worktree mode", "", "pin"),
    Action("unpin", "Unpin", "alt-x", "pin"),
    Action("new", "New pin", "alt-n", "list", ellipsis=True, short="new"),
    Action("expired", "Show expired", "alt-a", "list"),
    Action("prune", "Prune expired", "alt-p", "list", ellipsis=True),
    Action("undo", "Undo", "alt-z", "list"),
    Action("sort", "Cycle sort", "alt-s", "list"),
    Action("preview", "Toggle preview", "alt-v", "list"),
    Action("help", "Help & shortcuts", "f1", "tool", short="help"),
    Action("select", "Multi-select", "tab", "tool"),
]
BY_ID = {a.id: a for a in ACTIONS}
GROUPS = ("open", "pin", "list", "tool")

# fzf's query-editing keys: bindable, but you lose the editing key (warning, not a block).
FZF_EDITING_KEYS = {f"ctrl-{c}" for c in "abdefhjknpuwy"} | {"alt-b", "alt-d", "alt-f"}
RESERVED = {"alt-enter": "Windows Terminal uses alt+enter for fullscreen"}

_SPECIAL = {
    "enter", "return", "space", "tab", "btab", "shift-tab", "esc", "bspace", "bs", "del", "up", "down",
    "left", "right", "home", "end", "insert", "pgup", "page-up", "pgdn", "page-down",
    "shift-up", "shift-down", "shift-left", "shift-right", "shift-delete", "alt-up", "alt-down",
    "alt-left", "alt-right", "alt-space", "alt-enter", "alt-bspace", "alt-bs", "ctrl-space",
    "ctrl-delete", "ctrl-\\", "ctrl-]", "ctrl-^", "ctrl-/", "double-click", "left-click", "right-click",
    "scroll-up", "scroll-down", "preview-scroll-up", "preview-scroll-down",
    "ctrl-alt-up", "ctrl-alt-down", "alt-shift-up", "alt-shift-down",
} | {f"f{i}" for i in range(1, 13)}
_KEY_RE = re.compile(r"^(ctrl-[a-z]|alt-[a-zA-Z0-9]|ctrl-alt-[a-z]|alt-[\\\]^/])$")


def validate_key(key: str) -> str | None:
    """Return an error message, or None when ``key`` is a valid fzf key name."""
    k = key.strip()
    if not k:
        return None  # unbound
    if k in _SPECIAL or _KEY_RE.match(k):
        return None
    if len(k) == 1 and k.isprintable():
        return f"{k!r} is a printable character: it would type into the query instead of triggering an action"
    return f"{k!r} is not an fzf key name (examples: alt-t, ctrl-space, f5, shift-tab)"


def key_warning(key: str) -> str | None:
    if key in FZF_EDITING_KEYS:
        return f"{key} is one of fzf's query-editing keys; binding it disables that editing key"
    if key in RESERVED:
        return RESERVED[key]
    return None


class Keymap:
    def __init__(self, keys: dict[str, str] | None = None):
        self.keys: dict[str, str] = {a.id: a.key for a in ACTIONS}
        if keys:
            for k, v in keys.items():
                if k in self.keys:
                    self.keys[k] = v.strip()

    def key(self, action_id: str) -> str:
        return self.keys.get(action_id, "")

    def label(self, action_id: str) -> str:
        return BY_ID[action_id].title

    def hint(self, action_id: str) -> str:
        k = self.key(action_id)
        a = BY_ID[action_id]
        return f"{k} {a.short or a.label.lower()}" if k else ""

    def bound(self) -> dict[str, str]:
        """key → action id for every bound action."""
        return {v: k for k, v in self.keys.items() if v}

    def conflicts(self, action_id: str, key: str) -> list[str]:
        """Other action ids already using ``key``."""
        return [a for a, k in self.keys.items() if k == key and a != action_id and key]

    def set(self, action_id: str, key: str) -> None:
        if action_id not in self.keys:
            raise KeyError(action_id)
        self.keys[action_id] = key.strip()

    def reset(self, action_id: str | None = None) -> None:
        if action_id is None:
            self.keys = {a.id: a.key for a in ACTIONS}
        else:
            self.keys[action_id] = BY_ID[action_id].key

    def is_default(self) -> bool:
        return all(self.keys[a.id] == a.key for a in ACTIONS)

    # ---- file -------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> "Keymap":
        path = path or config.keymap_file()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return cls()
        return cls(parse(text))

    def save(self, path: Path | None = None) -> Path:
        path = path or config.keymap_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# claude-pins keymap — action = \"fzf key name\"; an empty string unbinds.",
                 "# Key names: https://github.com/junegunn/fzf (man page, KEY/EVENT BINDINGS).", ""]
        for group in GROUPS:
            lines.append(f"# {group}")
            for a in ACTIONS:
                if a.group == group:
                    lines.append(f'{a.id} = "{self.keys[a.id]}"')
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
        return path


def parse(text: str) -> dict[str, str]:
    """Minimal ``name = "value"`` parser (a flat TOML table)."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip() if not raw.strip().startswith('"') else raw.strip()
        if not line or line.startswith("["):
            continue
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[name] = value
    return out
