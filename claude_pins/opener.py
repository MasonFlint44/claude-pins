"""Opening a pin: already-open check, missing-directory tiers, branch check, touch, exec."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass

from . import config, gitutil, naming, prompt
from .model import Pin, PinError
from .render import Palette, crumb, palette
from .screen import leave_screen, screen_held
from .theme import WARNING
from .sessions import expiry_for, find_transcript, open_session_ids
from .store import Store
from .transcript import read_summary, touch


@dataclass
class Plan:
    argv: list[str]
    cwd: str
    transcript: str | None
    fork: bool
    worktree: str | None  # None = off; "" = unnamed worktree; else the name


def build_argv(pin: Pin, *, fork: bool, worktree: str | None) -> list[str]:
    argv = ["claude", "--resume", pin.session_id]
    if fork:
        # a fork copies the session's name, so without its own it would show as 📌 alias too
        argv += ["--fork-session", "--name", pin.alias]
    if worktree is not None:
        argv.append("--worktree")
        if worktree:
            argv.append(worktree)
    argv += pin.launch.flags()
    return argv


_notes: list[str] = []      # what was said while a screen was held, printed once the shell is back


def _banner(text: str, color: Palette):
    """A line for the user. While the picker holds the alternate screen a print would vanish under the
    next screen, so it waits for ``take_notes()`` (launch prints them, a cancel flashes them)."""
    if screen_held():
        _notes.append(text)
    else:
        print(color(f" {text}", WARNING), file=sys.stdout)


def take_notes() -> list[str]:
    notes = list(_notes)
    _notes.clear()
    return notes


def _maybe_update_cwd(store: Store, pin: Pin, new_cwd: str) -> None:
    if new_cwd == pin.cwd:
        return
    if prompt.yesno("update pin cwd?", True, crumb=crumb(pin.alias, "open")):
        pin.cwd = new_cwd
        store.save()


def resolve_directory(store: Store, pin: Pin, color: Palette) -> str | None:
    """Missing-directory menu. Returns the directory to launch in, or None if cancelled/unpinned."""
    cwd = pin.cwd or str(config.home())
    if os.path.isdir(cwd):
        return cwd
    home = str(config.home())
    wt = gitutil.split_worktree_path(cwd)
    header = [f"{pin.alias}: directory {config.tilde(cwd)} is missing"]
    options: list[tuple[str, str]] = []
    if wt and os.path.isdir(wt[0]) and gitutil.is_repo(wt[0]):
        repo, name = wt
        branch = gitutil.worktree_branch_for(repo, name)
        if branch:
            header.append(f"branch {branch} still exists in {config.tilde(repo)}")
            options.append((f"recreate the worktree on branch {branch}", "recreate"))
        else:
            header.append(f"branch worktree-{name} is gone from {config.tilde(repo)}")
            options.append((f"create a fresh worktree {name} off HEAD", "fresh"))
        options.append((f"open in the repo root {config.tilde(repo)}", "root"))
    options.append(("open in ~ (session context won't match this directory)", "home"))
    options.append(("choose another directory", "choose"))
    options.append(("unpin", "unpin"))
    try:
        idx = prompt.choose(header, [o[0] for o in options], 1, crumb=crumb(pin.alias, "open"))
    except prompt.Cancelled:
        return None
    action = options[idx - 1][1]
    if action == "recreate" or action == "fresh":
        repo, name = wt  # type: ignore[misc]
        branch = gitutil.worktree_branch_for(repo, name) if action == "recreate" else None
        ok, path, info = gitutil.recreate_worktree(repo, name, branch)
        if not ok:
            _banner(f"could not recreate worktree: {info}", color)
            return None
        _banner(f"→ recreated {config.tilde(path)} on {info}", color)
        _maybe_update_cwd(store, pin, path)
        return path
    if action == "root":
        repo = wt[0]  # type: ignore[index]
        _banner(f"→ opening in the repo root {config.tilde(repo)}", color)
        _maybe_update_cwd(store, pin, repo)
        return repo
    if action == "home":
        _banner("→ opening in ~ · session context won't match this directory", color)
        _maybe_update_cwd(store, pin, home)
        return home
    if action == "choose":
        try:
            typed = prompt.directory("", crumb=crumb(pin.alias, "open", "directory"))
        except prompt.Cancelled:
            return None
        chosen = os.path.abspath(os.path.expanduser(typed)) if typed else ""
        if not chosen or not os.path.isdir(chosen):
            _banner("not a directory; cancelled", color)
            return None
        _banner(f"→ opening in {config.tilde(chosen)}", color)
        _maybe_update_cwd(store, pin, chosen)
        return chosen
    if action == "unpin":
        store.unpin(pin.alias)
        store.save()
        _banner(" · ".join(filter(None, [f"✓ unpinned {pin.alias} · pins undo restores it", naming.restore(pin)])), color)
        return None
    return None


def check_branch(pin: Pin, cwd: str, recorded: str, color: Palette) -> bool:
    """Branch mismatch banner + optional checkout (clean tree only). False = cancel."""
    if not recorded or recorded == "HEAD" or not gitutil.is_repo(cwd):
        return True
    if gitutil.split_worktree_path(cwd):
        return True  # a worktree session records the branch before the checkout, so it is always stale
    current = gitutil.current_branch(cwd)
    if current is None or current == recorded:
        return True
    header = [f"{pin.alias}: {config.tilde(cwd)} is on {current}, the session was on {recorded}"]
    options = [(f"continue on {current}", "go")]
    if gitutil.is_clean(cwd):
        options.append((f"checkout {recorded} first (tree is clean)", "checkout"))
    else:
        header.append("(working tree has changes, so checkout is not offered)")
    options.append(("cancel", "cancel"))
    try:
        idx = prompt.choose(header, [o[0] for o in options], 1, crumb=crumb(pin.alias, "open"))
    except prompt.Cancelled:
        return False
    action = options[idx - 1][1]
    if action == "checkout":
        ok, out = gitutil.checkout(cwd, recorded)
        if not ok:
            _banner(f"checkout failed: {out}", color)
            return False
        _banner(f"→ checked out {recorded}", color)
        return True
    return action == "go"


def plan_open(store: Store, pin: Pin, *, fork: bool | None = None, worktree: str | None = None,
              plain: bool = False, interactive: bool = True) -> Plan | None:
    """Everything before exec. Returns None when the user cancelled (message already printed)."""
    color = palette(sys.stdout)
    transcript = find_transcript(pin.session_id, hint=pin.transcript)
    if transcript is None:
        raise PinError(f"{pin.alias}: transcript for session {pin.session_id[:8]}… is gone (expired) · pins unpin {pin.alias}")
    if str(transcript) != pin.transcript:
        pin.transcript = str(transcript)
        store.save()
    # Effective modes: one-off overrides beat the pin's defaults.
    if plain:
        use_fork, use_wt = False, None
    else:
        use_fork = pin.fork if fork is None else fork
        if worktree is not None:
            use_wt = worktree
        else:
            use_wt = "" if pin.worktree else None
    if interactive and pin.session_id in open_session_ids():
        try:
            idx = prompt.choose([f"{pin.alias} is already open in another tab"],
                                ["resume anyway (a second claude on the same session)", "cancel"], 2,
                                crumb=crumb(pin.alias, "open"))
        except prompt.Cancelled:
            return None
        if idx != 1:
            return None
    cwd = resolve_directory(store, pin, color) if interactive else (pin.cwd if os.path.isdir(pin.cwd) else str(config.home()))
    if cwd is None:
        return None
    if interactive and use_wt is None:
        summary = read_summary(transcript)
        if not check_branch(pin, cwd, summary.git_branch, color):
            return None
    return Plan(build_argv(pin, fork=use_fork, worktree=use_wt), cwd, str(transcript), use_fork, use_wt)


def launch(plan: Plan) -> None:
    """Touch the transcript, cd, exec claude. Never returns on success."""
    if plan.transcript:
        touch(plan.transcript)
    exe = shutil.which(plan.argv[0])
    if not exe:
        raise PinError("claude is not on PATH")
    os.chdir(plan.cwd)
    leave_screen(force=True)
    for note in take_notes():
        print(note)
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(exe, plan.argv)


def touch_pin(pin: Pin) -> bool:
    transcript = find_transcript(pin.session_id, hint=pin.transcript)
    return bool(transcript) and touch(transcript)


def touch_kept(store: Store) -> list[str]:
    """Touch every ``keep`` pin's transcript (runs on every ``pins`` invocation)."""
    touched = []
    for p in store.pins:
        if p.keep and touch_pin(p):
            touched.append(p.alias)
    return touched
