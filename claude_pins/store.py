"""The pin store: ``pins.json`` with atomic writes, corrupt-file recovery and undo tombstones."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from . import config
from .model import Pin, PinError, next_free_alias, validate_alias

FORMAT = 1
UNDO_CAP = 10


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


class Store:
    """In-memory view of the store file. ``load`` then mutate then ``save``."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else config.pins_file()
        self.pins: list[Pin] = []
        self.undo: list[dict] = []
        self.loaded = False

    # ---- persistence -----------------------------------------------------

    def load(self) -> "Store":
        self.loaded = True
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self.pins, self.undo = [], []
            return self
        except OSError as e:
            raise PinError(f"cannot read {self.path}: {e}")
        try:
            data = json.loads(raw) if raw.strip() else {}
            if not isinstance(data, dict):
                raise ValueError("top level is not an object")
            pins = [Pin.from_dict(p) for p in data.get("pins", [])]
            undo = data.get("undo", [])
            if not isinstance(undo, list):
                undo = []
        except (ValueError, AttributeError, TypeError) as e:
            bak = self._quarantine()
            raise PinError(
                f"pin store {self.path} is corrupt ({e}).\n"
                f"A copy was kept at {bak}; the original is untouched. "
                f"Fix or move it aside and run again."
            )
        self.pins = pins
        self.undo = undo
        return self

    def _quarantine(self) -> Path:
        bak = self.path.with_suffix(self.path.suffix + ".bak")
        if not bak.exists():
            try:
                shutil.copy2(self.path, bak)
            except OSError:
                pass
        return bak

    def save(self) -> None:
        data = {
            "format": FORMAT,
            "pins": [p.to_dict() for p in self.pins],
            "undo": self.undo[-UNDO_CAP:],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        fd, tmp = tempfile.mkstemp(prefix=".pins-", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ---- lookup ------------------------------------------------------------

    def get(self, alias: str) -> Pin | None:
        for p in self.pins:
            if p.alias == alias:
                return p
        return None

    def by_session(self, session_id: str) -> Pin | None:
        for p in self.pins:
            if p.session_id == session_id:
                return p
        return None

    def aliases(self) -> list[str]:
        return [p.alias for p in self.pins]

    def require(self, alias: str) -> Pin:
        pin = self.get(alias)
        if pin is None:
            raise PinError(f"no pin named {alias}")
        return pin

    # ---- mutation ------------------------------------------------------------

    def add(self, pin: Pin) -> Pin:
        validate_alias(pin.alias)
        existing = self.by_session(pin.session_id)
        if existing is not None:
            raise PinError(f"session already pinned as {existing.alias}")
        taken = self.get(pin.alias)
        if taken is not None:
            suggestion = next_free_alias(pin.alias, self.aliases())
            raise PinError(
                f"alias {pin.alias} is taken by session {taken.session_id[:8]}… "
                f"({taken.title or 'untitled'}); try {suggestion}"
            )
        if not pin.pinned_at:
            pin.pinned_at = now_iso()
        self.pins.append(pin)
        return pin

    def rename(self, alias: str, new_alias: str) -> Pin:
        pin = self.require(alias)
        validate_alias(new_alias)
        if new_alias != alias and self.get(new_alias) is not None:
            raise PinError(f"alias {new_alias} is taken; try {next_free_alias(new_alias, self.aliases())}")
        pin.alias = new_alias
        return pin

    def unpin(self, alias: str) -> Pin:
        pin = self.require(alias)
        self.pins.remove(pin)
        self._tombstone("unpin", [pin])
        return pin

    def unpin_many(self, aliases: list[str], kind: str = "unpin") -> list[Pin]:
        pins = [self.require(a) for a in aliases]
        for p in pins:
            self.pins.remove(p)
        if pins:
            self._tombstone(kind, pins)
        return pins

    def _tombstone(self, kind: str, pins: list[Pin]) -> None:
        self.undo.append({"kind": kind, "at": now_iso(), "pins": [p.to_dict() for p in pins]})
        del self.undo[:-UNDO_CAP]

    def last_undo(self) -> dict | None:
        return self.undo[-1] if self.undo else None

    def restore_last(self) -> tuple[str, list[Pin]]:
        """Undo the last unpin/prune. Returns (kind, restored pins)."""
        if not self.undo:
            raise PinError("nothing to undo")
        entry = self.undo.pop()
        restored: list[Pin] = []
        for data in entry.get("pins", []):
            pin = Pin.from_dict(data)
            if self.by_session(pin.session_id) is not None:
                continue  # re-pinned meanwhile under another alias; skip silently
            if self.get(pin.alias) is not None:
                pin.alias = next_free_alias(pin.alias, self.aliases())
            self.pins.append(pin)
            restored.append(pin)
        return entry.get("kind", "unpin"), restored


def load_store(path: Path | None = None) -> Store:
    return Store(path).load()
