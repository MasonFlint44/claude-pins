"""``pin`` command line: picker, matching, subcommands, hidden helpers for fzf previews and the plugin."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__, config, fzf
from .cost import price_check_online, session_cost
from .gitutil import current_branch, is_repo
from .listing import build_views
from .match import loose_match
from .model import Launch, Pin, PinError, is_session_id, validate_alias, PERMISSION_MODES, EFFORT_LEVELS
from .opener import launch, plan_open, touch_kept, touch_pin
from .render import palette, preview, rows, session_preview
from .sessions import find_transcript, iter_transcripts, session_id_from_env
from .store import Store, load_store
from .transcript import read_summary

SUBCOMMANDS = ("add", "list", "ls", "edit", "rm", "unpin", "undo", "prune", "touch", "doctor", "open",
               "_preview", "_spreview", "_status", "_complete", "help")


def _global_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--version", action="version", version=f"pin {__version__}")
    p.add_argument("--sort", choices=config.SORT_ORDERS, help="initial sort for the picker")
    p.add_argument("--fork", action="store_true", help="open as a fork (one-off)")
    p.add_argument("--resume", action="store_true", help="plain resume, ignoring the pin's fork/worktree modes")
    p.add_argument("-w", "--worktree", nargs="?", const="", metavar="NAME", help="open in a new worktree (one-off)")
    p.add_argument("--no-fzf", action="store_true", help="use the numbered menu instead of fzf")
    p.add_argument("--all", action="store_true", help="start with expired pins shown")


EPILOG = ("pin            open the picker\npin <words…>   open the one pin matching, else the picker pre-filtered\n"
          "Env: CLAUDE_PINS_FILE, CLAUDE_PINS_SORT, CLAUDE_PINS_NO_FZF, CLAUDE_PINS_EXPIRE_WARN, NO_COLOR, CLAUDE_CONFIG_DIR")


def build_query_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pin", add_help=False)
    _global_options(p)
    p.add_argument("words", nargs="*")
    return p


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pin", description="Pin Claude Code sessions and resume them by name.", epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _global_options(p)
    sub = p.add_subparsers(dest="cmd", metavar="command")

    a = sub.add_parser("add", help="pin a session: pin add <session-id> <alias> [--title …]")
    a.add_argument("session_id"); a.add_argument("alias")
    a.add_argument("--title", default=""); a.add_argument("--note", default="")
    a.add_argument("--cwd", default=None, help="directory to resume in (default: the session's)")
    a.add_argument("--keep", action="store_true"); a.add_argument("--fork", action="store_true")
    a.add_argument("--worktree", action="store_true")
    a.add_argument("--rename", action="store_true", help="if the session is already pinned, rename it to <alias>")

    ls = sub.add_parser("list", aliases=["ls"], help="list pins")
    ls.add_argument("--all", action="store_true", help="include expired pins")
    ls.add_argument("--sort", choices=config.SORT_ORDERS)
    ls.add_argument("--json", action="store_true")

    e = sub.add_parser("edit", help="edit a pin (no flags → interactive editor)")
    e.add_argument("alias")
    e.add_argument("--title"); e.add_argument("--note"); e.add_argument("--cwd"); e.add_argument("--rename", metavar="ALIAS")
    e.add_argument("--model"); e.add_argument("--effort", choices=EFFORT_LEVELS + ("",))
    e.add_argument("--permission-mode", choices=PERMISSION_MODES + ("",))
    for flag in ("keep", "fork", "worktree"):
        e.add_argument(f"--{flag}", dest=flag, action="store_true", default=None)
        e.add_argument(f"--no-{flag}", dest=flag, action="store_false")

    for name in ("rm", "unpin"):
        r = sub.add_parser(name, help="unpin (pin undo restores)")
        r.add_argument("alias")
    sub.add_parser("undo", help="restore the last unpin or prune")
    pr = sub.add_parser("prune", help="unpin every expired pin")
    pr.add_argument("-y", "--yes", action="store_true")
    t = sub.add_parser("touch", help="touch a pin's transcript (resets its 30-day clock)")
    t.add_argument("alias")
    sub.add_parser("doctor", help="check fzf, ccusage, the store and Claude's projects dir")
    o = sub.add_parser("open", help="open a pin by alias")
    o.add_argument("alias")
    o.add_argument("--fork", action="store_true"); o.add_argument("--resume", action="store_true")
    o.add_argument("-w", "--worktree", nargs="?", const="", metavar="NAME")

    for hidden in ("_preview", "_spreview", "_status", "_complete"):
        h = sub.add_parser(hidden)
        h.add_argument("arg", nargs="?")
    sub.add_parser("help")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    # `pin <words…>` — anything that is not a subcommand is a match query.
    first = next((a for a in argv if not a.startswith("-")), None)
    if first is not None and first not in SUBCOMMANDS and "-h" not in argv and "--help" not in argv:
        try:
            opts = build_query_parser().parse_args(argv)
        except SystemExit as e:
            return int(e.code or 0)
        try:
            return run_query(opts)
        except PinError as e:
            print(f"pin: {e}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print()
            return 130
    try:
        opts = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)
    try:
        return dispatch(opts, parser)
    except PinError as e:
        print(f"pin: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130


def dispatch(opts, parser) -> int:
    cmd = opts.cmd
    if cmd is None:
        return run_query(opts)
    if cmd == "help":
        parser.print_help(); return 0
    return {
        "add": cmd_add, "list": cmd_list, "ls": cmd_list, "edit": cmd_edit, "rm": cmd_unpin, "unpin": cmd_unpin,
        "undo": cmd_undo, "prune": cmd_prune, "touch": cmd_touch, "doctor": cmd_doctor, "open": cmd_open,
        "_preview": cmd_preview, "_spreview": cmd_spreview, "_status": cmd_status, "_complete": cmd_complete,
    }[cmd](opts)


# ---- picker / matching -------------------------------------------------------------------------

def _use_fzf(opts) -> bool:
    return not getattr(opts, "no_fzf", False) and fzf.available()


def run_query(opts) -> int:
    store = load_store()
    words = list(getattr(opts, "words", []) or [])
    query = " ".join(words)
    if words:
        hits = loose_match(store.pins, words)
        if len(hits) == 1:
            return _open(store, hits[0], fork=opts.fork or None, worktree=opts.worktree, plain=opts.resume)
        if not hits:
            print(f"pin: no pin matches {query!r}", file=sys.stderr)
    if not (sys.stdin.isatty() and sys.stdout.isatty()) and not os.environ.get("CLAUDE_PINS_FZF"):
        if words:
            if hits:
                print("pin: several pins match; be more specific:", file=sys.stderr)
                for h in hits:
                    print(f"  {h.alias:<18} {h.title}", file=sys.stderr)
            return 1
        opts.json = False
        return cmd_list(opts)
    if _use_fzf(opts):
        from .picker import Picker
        return Picker(store, query=query if words else "", sort=opts.sort, show_expired=opts.all).run()
    from .menu import run_menu
    reason = "" if fzf.fzf_version() else "fzf not found"
    if fzf.fzf_version() and fzf.fzf_version() < config.MIN_FZF:
        reason = f"fzf {'.'.join(map(str, fzf.fzf_version()))} is too old"
    return run_menu(store, query=query if words else "", sort=opts.sort, reason=reason)


def _open(store: Store, pin: Pin, *, fork=None, worktree=None, plain=False) -> int:
    touch_kept(store)
    plan = plan_open(store, pin, fork=fork, worktree=worktree, plain=plain)
    if plan is None:
        return 1
    launch(plan)
    return 0


def cmd_open(opts) -> int:
    store = load_store()
    return _open(store, store.require(opts.alias), fork=opts.fork or None, worktree=opts.worktree, plain=opts.resume)


# ---- subcommands ---------------------------------------------------------------------------------

def cmd_add(opts) -> int:
    store = load_store()
    sid = opts.session_id.lower()
    if not is_session_id(sid):
        raise PinError(f"{opts.session_id!r} is not a session id (expected a UUID)")
    alias = validate_alias(opts.alias)
    existing = store.by_session(sid)
    if existing is not None:
        if opts.rename and alias != existing.alias:
            store.rename(existing.alias, alias)
            changed = [f"renamed to {alias}"]
        else:
            changed = []
        if opts.title and opts.title != existing.title:
            existing.title = opts.title; changed.append("title updated")
        if opts.note and opts.note != existing.note:
            existing.note = opts.note; changed.append("note updated")
        if changed:
            store.save()
            print(f"already pinned as {existing.alias} · {', '.join(changed)}")
        else:
            print(f"already pinned as {existing.alias} · pass --title/--note to update, or --rename with a new alias")
        return 0
    transcript = find_transcript(sid)
    summary = read_summary(transcript) if transcript else None
    if transcript is None:
        print(f"warning: no transcript found for {sid[:8]}… yet (a brand-new session writes it after the first turn)",
              file=sys.stderr)
    cwd = opts.cwd or (summary.cwd if summary else "") or os.getcwd()
    title = opts.title or (summary.title if summary else "") or alias
    pin = Pin(alias=alias, session_id=sid, title=title, cwd=cwd, transcript=str(transcript or ""),
              note=opts.note, keep=opts.keep, fork=opts.fork, worktree=opts.worktree)
    store.add(pin)
    store.save()
    print(f"✓ pinned as {alias} · {title}")
    return 0


def cmd_list(opts) -> int:
    store = load_store()
    touch_kept(store)
    views, expired = build_views(store, include_expired=opts.all, sort=opts.sort or config.default_sort())
    if opts.json:
        out = []
        for v in views:
            d = v.pin.to_dict()
            d.update(state=v.expiry.state, age=v.age, open=v.is_open, markers=v.markers,
                     remaining_days=round(v.expiry.remaining_days, 1))
            out.append(d)
        print(json.dumps(out, indent=2))
        return 0
    color = palette(sys.stdout)
    if not views:
        print("No pins yet. Run /pin inside a Claude session, or: pin add <session-id> <alias>")
    for line in rows(views, color=color):
        print(line)
    if expired and not opts.all:
        print(color(f"{expired} expired · pin list --all · pin prune", "dim"))
    return 0


def cmd_edit(opts) -> int:
    store = load_store()
    pin = store.require(opts.alias)
    flags = {k: getattr(opts, k) for k in ("title", "note", "cwd", "rename", "model", "effort", "permission_mode",
                                            "keep", "fork", "worktree")}
    if all(v is None for v in flags.values()):
        from .editor import edit_pin
        saved = edit_pin(store, opts.alias)
        print(f"✓ saved {saved}" if saved else "no changes")
        return 0
    if flags["rename"]:
        store.rename(pin.alias, flags["rename"])
    for k in ("title", "note"):
        if flags[k] is not None:
            setattr(pin, k, flags[k])
    if flags["cwd"] is not None:
        pin.cwd = os.path.abspath(os.path.expanduser(flags["cwd"]))
    if flags["model"] is not None:
        pin.launch.model = flags["model"] or None
    if flags["effort"] is not None:
        pin.launch.effort = flags["effort"] or None
    if flags["permission_mode"] is not None:
        pin.launch.permission_mode = flags["permission_mode"] or None
    for k in ("keep", "fork", "worktree"):
        if flags[k] is not None:
            setattr(pin, k, flags[k])
    store.save()
    print(f"✓ saved {pin.alias}")
    return 0


def cmd_unpin(opts) -> int:
    store = load_store()
    pin = store.unpin(opts.alias)
    store.save()
    print(f"✓ unpinned {pin.alias} · pin undo restores it")
    return 0


def cmd_undo(opts) -> int:
    store = load_store()
    kind, restored = store.restore_last()
    store.save()
    names = ", ".join(p.alias for p in restored) or "nothing (already re-pinned)"
    print(f"✓ restored {names} ({kind})")
    return 0


def cmd_prune(opts) -> int:
    store = load_store()
    views, _ = build_views(store, include_expired=True, with_summary=False)
    dead = [v.pin.alias for v in views if v.expiry.expired]
    if not dead:
        print("nothing to prune")
        return 0
    print(f"{len(dead)} expired: {', '.join(dead)}")
    if not opts.yes:
        from . import prompt
        try:
            if not prompt.yesno("unpin them? (pin undo restores)", True):
                return 1
        except prompt.Cancelled:
            return 130
    store.unpin_many(dead, kind="prune")
    store.save()
    print(f"✓ pruned {len(dead)} · pin undo restores them")
    return 0


def cmd_touch(opts) -> int:
    store = load_store()
    pin = store.require(opts.alias)
    if not touch_pin(pin):
        raise PinError(f"{pin.alias}: transcript is gone (expired)")
    print(f"✓ touched {pin.alias}")
    return 0


def cmd_doctor(opts) -> int:
    ok = True
    v = fzf.fzf_version()
    if v is None:
        print(f"✗ fzf: not found · {fzf.install_hint()}"); ok = False
    elif v < config.MIN_FZF:
        print(f"✗ fzf {'.'.join(map(str, v))}: need ≥ 0.44 · {fzf.install_hint()}"); ok = False
    else:
        print(f"✓ fzf {'.'.join(map(str, v))}")
    cc = price_check_online()
    print(("✓ " if "reachable" in cc else "· ") + cc)
    path = config.pins_file()
    try:
        store = load_store()
        print(f"✓ store {config.tilde(path)}: {len(store.pins)} pins, {len(store.undo)} undo entries")
    except PinError as e:
        print(f"✗ store: {e}"); ok = False
    pd = config.projects_dir()
    if pd.is_dir():
        print(f"✓ projects {config.tilde(pd)}: {len(iter_transcripts())} session transcripts")
    else:
        print(f"✗ projects dir {config.tilde(pd)} not found (set CLAUDE_CONFIG_DIR?)"); ok = False
    print(f"✓ cleanupPeriodDays {config.cleanup_period_days()} · warn at {config.expire_warn_days()}d before expiry")
    print(f"· keymap {config.tilde(config.keymap_file())}{'' if config.keymap_file().exists() else ' (defaults)'}")
    return 0 if ok else 1


# ---- hidden helpers -----------------------------------------------------------------------------

def cmd_preview(opts) -> int:
    if not opts.arg or opts.arg == "-":
        return 0
    store = load_store()
    pin = store.get(opts.arg)
    if pin is None:
        return 0
    from .listing import view_for
    view = view_for(pin, set())
    branch = current_branch(pin.cwd) if pin.cwd and os.path.isdir(pin.cwd) and is_repo(pin.cwd) else None
    cost = session_cost(pin.session_id, pin.transcript) if view.summary and view.summary.exists else None
    print(preview(view, cost, branch, color=palette()))
    return 0


def cmd_spreview(opts) -> int:
    if not opts.arg:
        return 0
    print(session_preview(read_summary(opts.arg), color=palette()))
    return 0


def cmd_status(opts) -> int:
    """For the plugin: print ``alias\\ttitle`` if the session is pinned, else ``unpinned``."""
    sid = (opts.arg or session_id_from_env() or "").lower()
    store = load_store()
    pin = store.by_session(sid) if sid else None
    if pin:
        print(f"pinned\t{pin.alias}\t{pin.title}")
    else:
        print("unpinned")
    return 0


def cmd_complete(opts) -> int:
    for a in load_store().aliases():
        print(a)
    return 0
