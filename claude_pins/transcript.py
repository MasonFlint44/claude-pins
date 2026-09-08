"""Read what the picker needs from a session transcript without parsing the whole file.

Head and tail (64 KB each) are parsed as JSON records; the middle is only byte-scanned for
message counts and title records. Results are cached per (size, mtime).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, asdict, field
from pathlib import Path

from . import config
from .text import clip

WINDOW = 64 * 1024
CACHE_FORMAT = 2

# Context windows per model family; anything larger than the default is assumed 1M.
DEFAULT_WINDOW = 200_000
LARGE_WINDOW = 1_000_000


@dataclass
class Summary:
    session_id: str = ""
    path: str = ""
    cwd: str = ""
    git_branch: str = ""
    version: str = ""
    ai_title: str = ""
    custom_title: str = ""
    first_prompt: str = ""
    last_prompt: str = ""
    last_answer: str = ""
    model: str = ""
    effort: str = ""
    permission_mode: str = ""
    mode: str = ""
    context_tokens: int = 0
    prompts: int = 0
    replies: int = 0
    created: str = ""
    last: str = ""
    mtime: float = 0.0
    size: int = 0
    exists: bool = True
    truncated_tail: bool = False

    @property
    def title(self) -> str:
        return self.custom_title or self.ai_title or shorten(self.first_prompt, 60) or "untitled"

    @property
    def messages(self) -> int:
        return self.prompts + self.replies

    @property
    def context_window(self) -> int:
        if self.context_tokens > DEFAULT_WINDOW:
            return LARGE_WINDOW
        return config.context_window_for(self.model) or DEFAULT_WINDOW

    @property
    def context_pct(self) -> int:
        if not self.context_tokens:
            return 0
        return min(999, round(100 * self.context_tokens / self.context_window))

    def to_dict(self) -> dict:
        return asdict(self)


def shorten(text: str, limit: int) -> str:
    """``text`` on one line, clipped to ``limit`` terminal cells with an ellipsis."""
    return clip(" ".join((text or "").split()), max(1, limit))


def _iter_records(blob: bytes, *, drop_first: bool = False, drop_last: bool = False):
    lines = blob.split(b"\n")
    if drop_first and lines:
        lines = lines[1:]
    if drop_last and lines:
        lines = lines[:-1]
    for line in lines:
        line = line.strip()
        if not line.startswith(b"{"):
            continue
        try:
            yield json.loads(line)
        except ValueError:
            continue


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(parts)
    return ""


def _is_human_prompt(rec: dict) -> bool:
    msg = rec.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return not content.startswith("<")  # skip system-injected xml-ish attachments
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "text" for b in content) and not any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        )
    return False


def _apply(summary: Summary, rec: dict, *, head: bool) -> None:
    t = rec.get("type")
    if t == "ai-title":
        summary.ai_title = str(rec.get("aiTitle") or "")
    elif t == "custom-title":
        summary.custom_title = str(rec.get("customTitle") or "")
    elif t == "last-prompt":
        summary.last_prompt = str(rec.get("lastPrompt") or "")
    elif t == "permission-mode":
        summary.permission_mode = str(rec.get("permissionMode") or "")
    elif t == "mode":
        summary.mode = str(rec.get("mode") or "")
    if rec.get("sessionId") and not summary.session_id:
        summary.session_id = str(rec["sessionId"])
    if rec.get("cwd"):
        summary.cwd = str(rec["cwd"])
    if rec.get("gitBranch"):
        summary.git_branch = str(rec["gitBranch"])
    if rec.get("version"):
        summary.version = str(rec["version"])
    ts = rec.get("timestamp")
    if ts:
        if head and not summary.created:
            summary.created = str(ts)
        summary.last = str(ts)
    if t == "user" and _is_human_prompt(rec):
        text = _text_of((rec.get("message") or {}).get("content"))
        if head and not summary.first_prompt:
            summary.first_prompt = text
        if text:
            summary.last_prompt = text
    if t == "assistant":
        msg = rec.get("message") or {}
        if msg.get("model"):
            summary.model = str(msg["model"])
        if rec.get("effort"):
            summary.effort = str(rec["effort"])
        usage = msg.get("usage")
        if isinstance(usage, dict):
            total = 0
            for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
                v = usage.get(key)
                if isinstance(v, (int, float)):
                    total += int(v)
            if total:
                summary.context_tokens = total
        text = _text_of(msg.get("content")).strip()
        if text:
            summary.last_answer = text


def _pat(name: str) -> tuple[bytes, bytes]:
    return (f'"type":"{name}"'.encode(), f'"type": "{name}"'.encode())


_USER, _TOOL_RESULT, _ASSISTANT, _TEXT = _pat("user"), _pat("tool_result"), _pat("assistant"), _pat("text")
_TITLE = (b'-title"',)


def _has(line: bytes, pats: tuple[bytes, ...]) -> bool:
    return any(p in line for p in pats)


def _scan_counts(path: Path, summary: Summary) -> None:
    """Whole-file byte scan: prompt/reply counts plus every title record, in file order."""
    prompts = replies = 0
    with open(path, "rb") as fh:
        for line in fh:
            if _has(line, _ASSISTANT):
                if _has(line, _TEXT):
                    replies += 1
            elif _has(line, _USER):
                if not _has(line, _TOOL_RESULT):
                    prompts += 1
            elif _has(line[:48], _TITLE):
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("type") == "custom-title":
                    # last wins, an empty one included: /rename with nothing clears the name, and the
                    # head/tail pass above must not leave an older name standing over the clear
                    summary.custom_title = str(rec.get("customTitle") or "")
                elif rec.get("type") == "ai-title" and rec.get("aiTitle"):
                    summary.ai_title = str(rec["aiTitle"])
    summary.prompts = prompts
    summary.replies = replies


def read_summary(path: str | os.PathLike, *, use_cache: bool = True) -> Summary:
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return Summary(path=str(path), exists=False)
    cache_file = _cache_path(path)
    if use_cache:
        cached = _load_cache(cache_file, st)
        if cached is not None:
            return cached
    summary = Summary(path=str(path), mtime=st.st_mtime, size=st.st_size)
    with open(path, "rb") as fh:
        head = fh.read(WINDOW)
        if st.st_size > WINDOW:
            fh.seek(max(WINDOW, st.st_size - WINDOW))
            tail = fh.read()
        else:
            tail = b""
    if tail:
        overlap = st.st_size <= 2 * WINDOW
        for rec in _iter_records(head, drop_last=True):
            _apply(summary, rec, head=True)
        summary.truncated_tail = not tail.endswith(b"\n")
        for rec in _iter_records(tail, drop_first=not overlap):
            _apply(summary, rec, head=False)
    else:
        summary.truncated_tail = bool(head) and not head.endswith(b"\n")
        for rec in _iter_records(head):
            _apply(summary, rec, head=True)
    _scan_counts(path, summary)
    if use_cache:
        _save_cache(cache_file, summary)
    return summary


# ---- cache ---------------------------------------------------------------------

def _cache_path(path: Path) -> Path:
    key = path.stem
    return config.cache_dir() / "transcripts" / f"{key}.json"


def _load_cache(cache_file: Path, st) -> Summary | None:
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("format") != CACHE_FORMAT:
        return None
    s = data.get("summary") or {}
    if s.get("size") != st.st_size or abs(float(s.get("mtime", -1)) - st.st_mtime) > 1e-6:
        return None
    try:
        return Summary(**{k: v for k, v in s.items() if k in Summary.__dataclass_fields__})
    except TypeError:
        return None


def _save_cache(cache_file: Path, summary: Summary) -> None:
    """Atomic write through a per-process temp file: fzf runs several previews at once, and two of
    them summarizing the same transcript must not truncate each other's half-written file."""
    text = json.dumps({"format": CACHE_FORMAT, "summary": summary.to_dict()})
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{cache_file.stem}-", suffix=".tmp", dir=str(cache_file.parent))
    except OSError:
        return
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, cache_file)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def touch(path: str | os.PathLike) -> bool:
    """Bump the transcript's mtime (the whole retention protection). False if it is gone."""
    try:
        os.utime(path, None)
        return True
    except OSError:
        return False
