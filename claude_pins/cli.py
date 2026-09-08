"""``pins`` command line: picker, matching, subcommands, hidden helpers for fzf previews and the plugin."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__, altkeys, config, fzf, hooks, naming
from .cost import doctor_line
from .listing import build_views, session_pairs
from .match import loose_match, match_sessions, recent_sessions, short_ids
from .model import Launch, Pin, PinError, is_session_id, validate_alias, PERMISSION_MODES, EFFORT_LEVELS
from .opener import launch, plan_open, touch_kept, touch_pin
from .render import (Palette, label_row, layout, legend, palette, rows, session_label_row,
                     session_layout, session_rows, terminal_width)
from .text import cells, pad
from .sessions import find_transcript, iter_transcripts, session_id_from_env
from .screen import Hook, leave_screen
from .store import Store, load_store
from .transcript import Summary, read_summary

SUBCOMMANDS = ("add", "list", "ls", "sessions", "edit", "rename", "rm", "unpin", "undo", "prune", "touch", "doctor", "open",
               "_preview", "_spreview", "_rows", "_dirs", "_status", "_complete", "_keep", "_keys", "help")


def _global_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--version", action="version", version=f"pins {__version__}")
    p.add_argument("--sort", choices=config.SORT_ORDERS, help="initial sort for the picker")
    p.add_argument("--fork", action="store_true", help="open as a fork (one-off)")
    p.add_argument("--resume", action="store_true", help="plain resume, ignoring the pin's fork/worktree modes")
    p.add_argument("-w", "--worktree", nargs="?", const="", metavar="NAME", help="open in a new worktree (one-off)")
    p.add_argument("--no-fzf", action="store_true", help="use the built-in picker instead of fzf")
    p.add_argument("--all", action="store_true", help="start with expired pins shown")


EPILOG = ("pins           open the picker\npins <words…>  open the one pin matching, else the picker pre-filtered\n"
          "pins sessions   recent sessions with ids, for pins add <id-or-title-words> <alias>\n"
          "Env: CLAUDE_PINS_FILE, CLAUDE_PINS_SORT, CLAUDE_PINS_NO_FZF, CLAUDE_PINS_EXPIRE_WARN, NO_COLOR, CLAUDE_CONFIG_DIR")


def build_query_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pins", add_help=False)
    _global_options(p)
    p.add_argument("words", nargs="*")
    return p


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pins", description="Pin Claude Code sessions and resume them by name.", epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _global_options(p)
    sub = p.add_subparsers(dest="cmd", metavar="command")

    a = sub.add_parser("add", help="pin a session: pins add <session> <alias> [--title …]")
    a.add_argument("session", metavar="session", help="session id, unique id prefix, or words from its title")
    a.add_argument("alias")
    a.add_argument("--title", default=""); a.add_argument("--note", default="")
    a.add_argument("--cwd", default=None, help="directory to resume in (default: the session's)")
    a.add_argument("--keep", action="store_true"); a.add_argument("--fork", action="store_true")
    a.add_argument("--worktree", action="store_true")
    a.add_argument("--rename", action="store_true", help="if the session is already pinned, rename it to <alias>")

    ls = sub.add_parser("list", aliases=["ls"], help="list pins")
    ls.add_argument("--all", action="store_true", help="include expired pins")
    ls.add_argument("--sort", choices=config.SORT_ORDERS)
    ls.add_argument("--json", action="store_true")

    ss = sub.add_parser("sessions", help="list recent sessions (what pins add and the picker's new-pin screen match)")
    ss.add_argument("words", nargs="*", help="every word must appear in the title or directory")
    ss.add_argument("--json", action="store_true")

    e = sub.add_parser("edit", help="edit a pin (no flags → interactive editor)")
    e.add_argument("alias")
    e.add_argument("--title"); e.add_argument("--note"); e.add_argument("--cwd"); e.add_argument("--rename", metavar="ALIAS")
    e.add_argument("--model"); e.add_argument("--effort", choices=EFFORT_LEVELS + ("",))
    e.add_argument("--permission-mode", choices=PERMISSION_MODES + ("",))
    for flag in ("keep", "fork", "worktree"):
        e.add_argument(f"--{flag}", dest=flag, action="store_true", default=None)
        e.add_argument(f"--no-{flag}", dest=flag, action="store_false")

    rn = sub.add_parser("rename", help="rename a pin: pins rename <alias> <new-alias>")
    rn.add_argument("alias"); rn.add_argument("new_alias")

    for name in ("rm", "unpin"):
        r = sub.add_parser(name, help="unpin (pins undo restores)")
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

    for hidden in ("_preview", "_spreview", "_dirs", "_status", "_complete", "_keep", "_keys"):
        h = sub.add_parser(hidden)
        h.add_argument("arg", nargs="?")
        if hidden == "_preview":
            h.add_argument("--draft", metavar="FILE")
        if hidden == "_dirs":
            h.add_argument("--gap", action="store_true")
    rw = sub.add_parser("_rows")
    rw.add_argument("--sort", choices=config.SORT_ORDERS)
    rw.add_argument("--all", action="store_true")
    rw.add_argument("--gap", action="store_true", help="print the blank sticky row first (fzf 0.63+)")
    sub.add_parser("help")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    # `pins <words…>` — anything that is not a subcommand is a match query.
    first = next((a for a in argv if not a.startswith("-")), None)
    if first is not None and first not in SUBCOMMANDS and "-h" not in argv and "--help" not in argv:
        try:
            opts = build_query_parser().parse_args(argv)
        except SystemExit as e:
            return int(e.code or 0)
        try:
            return run_query(opts)
        except PinError as e:
            print(f"pins: {e}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print()
            return 130
        finally:
            leave_screen(force=True)
    try:
        opts = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)
    try:
        return dispatch(opts, parser)
    except PinError as e:
        print(f"pins: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130
    finally:
        leave_screen(force=True)      # every way out of a screen, error paths included


def dispatch(opts, parser) -> int:
    cmd = opts.cmd
    if cmd is None:
        return run_query(opts)
    if cmd == "help":
        parser.print_help(); return 0
    return {
        "add": cmd_add, "list": cmd_list, "ls": cmd_list, "sessions": cmd_sessions, "edit": cmd_edit,
        "rename": cmd_rename, "rm": cmd_unpin, "unpin": cmd_unpin,
        "undo": cmd_undo, "prune": cmd_prune, "touch": cmd_touch, "doctor": cmd_doctor, "open": cmd_open,
        "_preview": cmd_preview, "_spreview": cmd_spreview, "_rows": cmd_rows, "_dirs": cmd_dirs, "_status": cmd_status,
        "_keys": cmd_keys,
        "_complete": cmd_complete, "_keep": cmd_keep,
    }[cmd](opts)


# ---- picker / matching -------------------------------------------------------------------------

def run_query(opts) -> int:
    store = load_store()
    words = list(getattr(opts, "words", []) or [])
    query = " ".join(words)
    if words:
        hits = loose_match(store.pins, words)
        if len(hits) == 1:
            return _open(store, hits[0], fork=opts.fork or None, worktree=opts.worktree, plain=opts.resume)
        if not hits:
            print(f"pins: no pin matches {query!r}", file=sys.stderr)
    from . import tui
    scripted = os.environ.get("CLAUDE_PINS_FZF") or os.environ.get("CLAUDE_PINS_TUI_SCRIPT")   # the tests' stand-ins
    if not (sys.stdin.isatty() and sys.stdout.isatty()) and not scripted:
        if words:
            if hits:
                print("pins: several pins match; be more specific:", file=sys.stderr)
                for h in hits:
                    print(f"  {h.alias:<18} {h.title}", file=sys.stderr)
            return 1
        opts.json = False
        return cmd_list(opts)
    if getattr(opts, "no_fzf", False):
        os.environ["CLAUDE_PINS_NO_FZF"] = "1"      # every screen the picker reaches uses the built-in one too
    if not fzf.available() and not tui.usable():    # a dumb terminal: the list, and where the commands are
        opts.json = False
        code = cmd_list(opts)
        print("(no picker on this terminal: pins <alias> opens a pin, pins help lists the commands)")
        return code
    from .picker import Picker
    return Picker(store, query=query if words else "", sort=opts.sort, show_expired=opts.all).run()


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

def session_table(sessions: list[Summary], store: Store | None, *, all_sessions: list[Summary] | None = None,
                  color: Palette | None = None, labels: bool = False) -> list[str]:
    """``pins sessions``' lines: the listed id prefix (unique among ``all_sessions``, the ones a prefix is
    resolved against), then the session row (a pinned one tagged, under the pin's title, when ``store`` is
    given); with ``labels`` a dim label row first."""
    color = color or Palette(False)
    ids = [s.session_id for s in (all_sessions or sessions)]
    prefixes = dict(zip(ids, short_ids(ids)))
    id_w = max(cells(prefixes[s.session_id]) for s in sessions)
    width = terminal_width() - id_w - 2
    pairs = session_pairs(store, sessions) if store else [(s, "") for s in sessions]
    cols = session_layout(pairs, width)
    lines = []
    if labels:
        lines.append(f"{color(pad('id', id_w), 'dim')}  {session_label_row(cols, color)}")
    for s, line in zip(sessions, session_rows(pairs, width=width, color=color, cols=cols)):
        lines.append(f"{color(pad(prefixes[s.session_id], id_w), 'dim')}  {line}")
    return lines


def _recent(store: Store) -> list[Summary]:
    """The recent sessions as the tables show them: a pinned one under its pin's title, so words match
    what is listed rather than the session's name ``📌 alias``."""
    return [s for s, _ in session_pairs(store, recent_sessions())]


def resolve_session(text: str, store: Store) -> str:
    """The session id named by ``text`` (see ``match_sessions``). Ambiguity and no match are errors."""
    if is_session_id(text.strip().lower()):
        return text.strip().lower()
    sessions = _recent(store)
    hits = match_sessions(text, sessions)
    if len(hits) == 1:
        return hits[0].session_id
    if not hits:
        raise PinError(f"no recent session matches {text!r} · pins sessions lists them")
    lines = [f"{len(hits)} sessions match {text!r}; give the id or more words:"]
    lines += [f"  {line}" for line in session_table(hits, store, all_sessions=sessions)]
    raise PinError("\n".join(lines))


def _line(*parts: str) -> str:
    """One report line: the parts that are not empty, joined by `` · ``."""
    return " · ".join(p for p in parts if p)


def cmd_add(opts) -> int:
    store = load_store()
    sid = resolve_session(opts.session, store)
    alias = validate_alias(opts.alias)
    existing = store.by_session(sid)
    if existing is not None:
        if opts.rename and alias != existing.alias:
            old = existing.alias
            store.rename(old, alias)
            changed = [f"renamed to {alias}", naming.rename(existing, old)]
        else:
            changed = []
        if opts.title and opts.title != existing.title:
            existing.title = opts.title; changed.append("title updated")
        if opts.note and opts.note != existing.note:
            existing.note = opts.note; changed.append("note updated")
        if changed:
            store.save()
            print(_line(f"already pinned as {existing.alias}", *changed))
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
    note = naming.claim(pin)
    store.save()
    print(_line(f"✓ pinned as {alias}", title, note))
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
        print("No pins yet. Run /pins:pin inside a Claude session, or: pins add <session-id> <alias>")
    # On a terminal the table gets the picker's column labels and marker legend; piped output stays
    # bare rows so grep and friends see nothing else.
    interactive = sys.stdout.isatty() and bool(views)
    width = terminal_width()
    cols = layout(views, width) if views else None
    if interactive:
        print(label_row(cols, color))
    for line in rows(views, width=width, color=color, cols=cols):
        print(line)
    if interactive:
        print(color(legend(), "dim"))
    if expired and not opts.all:
        print(color(f"{expired} expired · pins list --all · pins prune", "dim"))
    return 0


def cmd_sessions(opts) -> int:
    store = load_store()
    sessions = _recent(store)
    if opts.words:
        sessions = match_sessions(" ".join(opts.words), sessions)
    if opts.json:
        print(json.dumps([{"session_id": s.session_id, "title": s.title, "cwd": s.cwd, "mtime": s.mtime,
                           "messages": s.messages, "alias": alias or None}
                          for s, alias in session_pairs(store, sessions)], indent=2))
        return 0
    if not sessions:
        print("no matching sessions" if opts.words else "no sessions found", file=sys.stderr)
        return 1
    for line in session_table(sessions, store, all_sessions=_recent(store), color=palette(sys.stdout),
                              labels=sys.stdout.isatty()):
        print(line)
    return 0


def cmd_rename(opts) -> int:
    store = load_store()
    old = store.require(opts.alias).alias
    pin = store.rename(old, validate_alias(opts.new_alias))
    store.save()
    print(_line(f"✓ renamed {old} → {pin.alias}", naming.rename(pin, old)))
    return 0


def cmd_edit(opts) -> int:
    store = load_store()
    pin = store.require(opts.alias)
    flags = {k: getattr(opts, k) for k in ("title", "note", "cwd", "rename", "model", "effort", "permission_mode",
                                            "keep", "fork", "worktree")}
    if all(v is None for v in flags.values()):
        from .editor import edit_pin
        saved = edit_pin(store, opts.alias)
        print(_line(f"✓ saved {saved[0]}", saved[1]) if saved else "no changes")
        return 0
    note = ""
    if flags["rename"]:
        old = pin.alias
        store.rename(old, flags["rename"])
        note = naming.rename(pin, old)
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
    print(_line(f"✓ saved {pin.alias}", note))
    return 0


def cmd_unpin(opts) -> int:
    store = load_store()
    pin = store.unpin(opts.alias)
    store.save()
    print(_line(f"✓ unpinned {pin.alias} · pins undo restores it", naming.restore(pin)))
    return 0


def cmd_undo(opts) -> int:
    store = load_store()
    kind, restored = store.restore_last()
    store.save()
    names = ", ".join(p.alias for p in restored) or "nothing (already re-pinned)"
    print(_line(f"✓ restored {names} ({kind})", *[naming.reclaim(p) for p in restored]))
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
            from .render import crumb
            if not prompt.yesno("unpin them? (pins undo restores)", True, crumb=crumb("prune")):
                return 1
        except prompt.Cancelled:
            return 130
    store.unpin_many(dead, kind="prune")
    store.save()
    print(f"✓ pruned {len(dead)} · pins undo restores them")
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
    # fzf is optional: the built-in picker draws the same screens without it
    if v is None:
        print(f"· fzf: not found ({fzf.OPTIONAL_NOTE}) · {fzf.install_hint()}")
    elif v < config.MIN_FZF:
        print(f"· fzf {'.'.join(map(str, v))}: need ≥ 0.44 ({fzf.OPTIONAL_NOTE}) · {fzf.install_hint()}")
    else:
        print(f"✓ fzf {'.'.join(map(str, v))}")
    probe = altkeys.probe()
    if probe:
        print(probe.doctor_line())
    print(doctor_line())
    path = config.pins_file()
    try:
        store = load_store()
        print(f"✓ store {config.tilde(path)}: {len(store.pins)} pins, {len(store.undo)} undo entries")
        kept = sum(1 for p in store.pins if p.keep)
        count = f"{kept} {'pin' if kept == 1 else 'pins'}" if kept else "no pins"
        if config.plugin_enabled():
            print(f"✓ keep: {count} · touched on every pins run and every Claude session start (plugin hook)")
        else:
            print(f"· keep: {count} · touched on every pins run only: "
                  "the pins plugin is not enabled, so no session-start hook (/plugin install pins@claude-toolbox, then restart Claude)")
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
    """The pane for fzf: the pin under the cursor, printed as it comes (fzf renders preview output as it
    arrives, so the pane fills at once and the cost line drops in when ccusage answers)."""
    if opts.draft:
        sys.stdout.write(hooks.draft_preview(opts.draft))
        return 0
    for chunk in hooks.pin_preview(opts.arg or ""):
        sys.stdout.write(chunk)
        sys.stdout.flush()
    return 0


def cmd_dirs(opts) -> int:
    """The directory field's list for fzf's ``reload``: the gap row with ``--gap``, the typed directory,
    then its completions."""
    items = hooks.dir_rows(opts.arg or "")
    for line in fzf.lines_for([fzf.GAP_ROW, *items] if opts.gap else items):
        print(line)
    return 0


def cmd_spreview(opts) -> int:
    sys.stdout.write("".join(hooks.preview_text(Hook("spreview"), opts.arg or "")))
    return 0


def cmd_rows(opts) -> int:
    """The picker's rows for fzf's ``reload``: the sticky rows first (the gap row with ``--gap``, then the
    labels), then ``alias<tab>display`` with the display's columns tab-separated as the picker sends
    them, at the width fzf reports. Colour is on unless NO_COLOR says otherwise, like ``_preview``
    (stdout is a pipe)."""
    items = hooks.pin_rows(opts.sort, opts.all)
    for line in fzf.lines_for([fzf.GAP_ROW, *items] if opts.gap else items, columns=True):
        print(line)
    return 0


def cmd_keys(opts) -> int:
    """Print the name of every key and mouse event this terminal sends, the way the built-in picker reads
    them; esc twice or ctrl-c ends it. For checking a terminal, and for bug reports."""
    from .tui import key_check
    return key_check()


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


def cmd_keep(opts) -> int:
    """For the plugin's SessionStart hook: touch every ``keep`` pin's transcript and say nothing. A
    SessionStart hook's stdout lands in Claude's context and a nonzero exit is shown to the user at
    every start, so a store that is missing, corrupt or unreadable is left for ``pins doctor``."""
    try:
        touch_kept(load_store())
    except Exception:
        pass
    return 0
