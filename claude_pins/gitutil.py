"""Git helpers: branch state, tree cleanliness, worktree recreation. All best-effort."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

WORKTREE_DIR = Path(".claude") / "worktrees"


def _git(args: list[str], cwd: str | os.PathLike | None = None, timeout: float = 10.0) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return p.returncode, (p.stdout if p.returncode == 0 else p.stderr).strip()


def is_repo(path: str | os.PathLike) -> bool:
    rc, out = _git(["rev-parse", "--is-inside-work-tree"], cwd=path)
    return rc == 0 and out == "true"


def current_branch(path: str | os.PathLike) -> str | None:
    rc, out = _git(["branch", "--show-current"], cwd=path)
    if rc != 0:
        return None
    return out or "(detached)"


def is_clean(path: str | os.PathLike) -> bool:
    rc, out = _git(["status", "--porcelain", "--untracked-files=no"], cwd=path)
    return rc == 0 and out == ""


def branch_exists(repo: str | os.PathLike, branch: str) -> bool:
    rc, _ = _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo)
    return rc == 0


def checkout(path: str | os.PathLike, branch: str) -> tuple[bool, str]:
    rc, out = _git(["checkout", branch], cwd=path)
    return rc == 0, out


def split_worktree_path(cwd: str) -> tuple[str, str] | None:
    """``/repo/.claude/worktrees/<name>`` → (``/repo``, ``<name>``), else None."""
    m = re.match(r"^(.*?)/\.claude/worktrees/([^/]+)/?$", cwd)
    if not m:
        return None
    return m.group(1), m.group(2)


def worktree_branch_for(repo: str, name: str) -> str | None:
    """The surviving branch for a Claude worktree: ``worktree-<name>`` (spike finding), else ``<name>``."""
    for cand in (f"worktree-{name}", name):
        if branch_exists(repo, cand):
            return cand
    return None


def recreate_worktree(repo: str, name: str, branch: str | None) -> tuple[bool, str, str]:
    """Recreate ``<repo>/.claude/worktrees/<name>``.

    On ``branch`` when given (surviving branch), else on a fresh ``worktree-<name>`` off HEAD.
    Returns (ok, path, message). Unlocks and prunes stale registrations first — Claude locks
    its worktrees and can leave the lock behind.
    """
    path = str(Path(repo) / WORKTREE_DIR / name)
    _git(["worktree", "unlock", path], cwd=repo)
    _git(["worktree", "prune"], cwd=repo)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if branch:
        rc, out = _git(["worktree", "add", path, branch], cwd=repo, timeout=60)
        used = branch
    else:
        used = f"worktree-{name}"
        n = 2
        while branch_exists(repo, used):
            used = f"worktree-{name}-{n}"
            n += 1
        rc, out = _git(["worktree", "add", "-b", used, path, "HEAD"], cwd=repo, timeout=60)
    if rc != 0:
        return False, path, out
    return True, path, used
