import json
import os
import subprocess
from pathlib import Path

from tests.helpers import FzfSandbox

SID1 = "11111111-1111-1111-1111-111111111111"
SID2 = "22222222-2222-2222-2222-222222222222"
SID3 = "33333333-3333-3333-3333-333333333333"


class CliTests(FzfSandbox):
    def setUp(self):
        super().setUp()
        self.t1 = self.make_session(SID1, age_days=2, title="Standup prep")
        self.t2 = self.make_session(SID2, cwd=str(self.home / "git" / "cc"), age_days=9, title="Command center collector")

    def test_add_list_rm_undo(self):
        r = self.run_pin("add", SID1, "standup-prep")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("✓ pinned as standup-prep · Standup prep", r.stdout)
        r = self.run_pin("add", SID2, "cc-collector", "--title", "CC", "--keep")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_pin("list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"standup-prep\s+Standup prep\s+~/git/proj\s+2d")
        self.assertRegex(r.stdout, r"cc-collector\s+CC\s+~/git/cc\s+0m\s+🚩")  # keep → touched on every run
        r = self.run_pin("list", "--json")
        data = json.loads(r.stdout)
        self.assertEqual(sorted(d["alias"] for d in data), ["cc-collector", "standup-prep"])
        self.assertEqual({d["alias"]: d["markers"] for d in data}["cc-collector"], "🚩")
        r = self.run_pin("rm", "standup-prep")
        self.assertIn("✓ unpinned standup-prep · pin undo", r.stdout)
        r = self.run_pin("rm", "standup-prep")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no pin named standup-prep", r.stderr)
        r = self.run_pin("undo")
        self.assertIn("✓ restored standup-prep (unpin)", r.stdout)
        r = self.run_pin("undo")
        self.assertEqual(r.returncode, 1); self.assertIn("nothing to undo", r.stderr)

    def test_add_idempotency(self):
        self.run_pin("add", SID1, "standup-prep")
        r = self.run_pin("add", SID1, "standup-prep")
        self.assertIn("already pinned as standup-prep", r.stdout)
        r = self.run_pin("add", SID1, "standup-prep", "--title", "New title")
        self.assertIn("title updated", r.stdout)
        r = self.run_pin("add", SID1, "other", "--rename")
        self.assertIn("renamed to other", r.stdout)
        r = self.run_pin("add", SID2, "other")
        self.assertEqual(r.returncode, 1)
        self.assertIn("alias other is taken", r.stderr); self.assertIn("try other-2", r.stderr)
        r = self.run_pin("add", "not-a-uuid", "x")
        self.assertEqual(r.returncode, 1)
        r = self.run_pin("add", SID3, "brand-new")  # no transcript yet
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no transcript found", r.stderr)

    def test_open_by_alias_and_words(self):
        self.run_pin("add", SID1, "standup-prep", "--title", "Standup prep")
        self.run_pin("add", SID2, "cc-collector", "--title", "Command center collector")
        r = self.run_pin("standup-prep")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        call = self.claude_calls()
        self.assertEqual(call["argv"], ["--resume", SID1])
        self.assertEqual(call["cwd"], str(self.home / "git" / "proj"))
        # loose match on title words, unique hit
        self.argv_log.unlink()
        r = self.run_pin("center", "collector", "--fork")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID2, "--fork-session"])
        # worktree one-off with a name
        r = self.run_pin("cc-collector", "-w", "feat")
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID2, "--worktree", "feat"])
        # ambiguous, non-tty → exit 1 listing hits
        r = self.run_pin("e", env={"CLAUDE_PINS_FZF": ""})
        self.assertEqual(r.returncode, 1)
        self.assertIn("several pins match", r.stderr)

    def test_pin_modes_and_overrides(self):
        self.run_pin("add", SID1, "sp", "--fork", "--worktree")
        self.run_pin("edit", "sp", "--model", "opus", "--effort", "high", "--permission-mode", "plan")
        self.run_pin("sp")
        self.assertEqual(self.claude_calls()["argv"],
                         ["--resume", SID1, "--fork-session", "--worktree", "--model", "opus", "--effort", "high", "--permission-mode", "plan"])
        self.run_pin("sp", "--resume")
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID1, "--model", "opus", "--effort", "high", "--permission-mode", "plan"])
        self.run_pin("edit", "sp", "--no-fork", "--no-worktree", "--effort", "", "--rename", "sp2")
        self.run_pin("sp2")
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID1, "--model", "opus", "--permission-mode", "plan"])

    def test_touch_keep_expiry(self):
        self.run_pin("add", SID1, "a", "--keep")
        self.run_pin("add", SID2, "b")
        self.age(self.t1, 25); self.age(self.t2, 25)
        r = self.run_pin("list")  # keep pins are touched on every run
        self.assertRegex(r.stdout, r"a\s+Standup prep\s+~/git/proj\s+0m\s+🚩")
        self.assertRegex(r.stdout, r"b\s+Command center collector\s+~/git/cc\s+25d\s+⏳")
        r = self.run_pin("touch", "b")
        self.assertIn("✓ touched b", r.stdout)
        self.assertLess(abs(self.t2.stat().st_mtime - os.path.getmtime(self.t1)), 5)

    def test_expired_prune_undo(self):
        self.run_pin("add", SID1, "a"); self.run_pin("add", SID2, "b")
        self.t2.unlink()  # retention sweep took it
        r = self.run_pin("list")
        self.assertNotIn("b ", r.stdout)
        self.assertIn("1 expired · pin list --all · pin prune", r.stdout)
        r = self.run_pin("list", "--all")
        self.assertRegex(r.stdout, r"b\s+Command center collector\s+~/git/cc\s+🔴")
        r = self.run_pin("b")
        self.assertEqual(r.returncode, 1); self.assertIn("gone (expired)", r.stderr)
        r = self.run_pin("prune", "-y")
        self.assertIn("✓ pruned 1", r.stdout)
        r = self.run_pin("prune", "-y")
        self.assertIn("nothing to prune", r.stdout)
        r = self.run_pin("undo")
        self.assertIn("✓ restored b (prune)", r.stdout)

    def test_status_and_complete(self):
        self.run_pin("add", SID1, "a", "--title", "T")
        r = self.run_pin("_status", SID1)
        self.assertEqual(r.stdout.strip(), "pinned\ta\tT")
        r = self.run_pin("_status", SID2)
        self.assertEqual(r.stdout.strip(), "unpinned")
        r = self.run_pin("_status", env={"CLAUDE_CODE_SESSION_ID": SID1})
        self.assertEqual(r.stdout.strip(), "pinned\ta\tT")
        self.run_pin("add", SID2, "b")
        r = self.run_pin("_complete")
        self.assertEqual(r.stdout.split(), ["a", "b"])

    def test_doctor(self):
        r = self.run_pin("doctor")
        self.assertIn("✓ fzf 0.44.1", r.stdout)
        self.assertIn("cleanupPeriodDays 30", r.stdout)
        self.assertIn("✓ store", r.stdout)

    def test_no_color_and_color(self):
        self.run_pin("add", SID1, "a")
        r = self.run_pin("_preview", "a")
        self.assertNotIn("\x1b[", r.stdout)
        r = self.run_pin("_preview", "a", env={"NO_COLOR": "", "CLAUDE_PINS_COLOR": "1"})
        self.assertIn("\x1b[", r.stdout)

    def test_corrupt_store_message(self):
        self.store_path().parent.mkdir(parents=True)
        self.store_path().write_text("nope")
        r = self.run_pin("list")
        self.assertEqual(r.returncode, 1)
        self.assertIn("corrupt", r.stderr); self.assertIn(".bak", r.stderr)

    def test_rename(self):
        self.run_pin("add", SID1, "a"); self.run_pin("add", SID2, "b")
        r = self.run_pin("rename", "a", "standup")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("✓ renamed a → standup", r.stdout)
        self.assertEqual(self.run_pin("_complete").stdout.split(), ["standup", "b"])
        r = self.run_pin("rename", "standup", "b")
        self.assertEqual(r.returncode, 1); self.assertIn("alias b is taken; try b-2", r.stderr)
        r = self.run_pin("rename", "standup", "Bad Alias")
        self.assertEqual(r.returncode, 1); self.assertIn("invalid alias", r.stderr)
        r = self.run_pin("rename", "nope", "x")
        self.assertEqual(r.returncode, 1); self.assertIn("no pin named nope", r.stderr)

    def test_add_by_title_and_id_prefix(self):
        self.make_session(SID3, cwd=str(self.home / "git" / "cc2"), age_days=1, title="Command center v2")
        r = self.run_pin("add", "standup", "sp")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("✓ pinned as sp · Standup prep", r.stdout)
        r = self.run_pin("add", "command center", "cc")
        self.assertEqual(r.returncode, 1)
        self.assertIn("2 sessions match 'command center'; give the id or more words:", r.stderr)
        self.assertRegex(r.stderr, rf"  {SID3[:8]}  Command center v2\s+~/git/cc2")
        self.assertRegex(r.stderr, rf"  {SID2[:8]}  Command center collector\s+~/git/cc")
        r = self.run_pin("add", "center CC2", "cc2")  # words match the directory too, case-insensitively
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("✓ pinned as cc2 · Command center v2", r.stdout)
        r = self.run_pin("add", SID2[:8], "cc")
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("✓ pinned as cc · Command center collector", r.stdout)
        r = self.run_pin("add", "nothing like this", "x")
        self.assertEqual(r.returncode, 1); self.assertIn("no recent session matches 'nothing like this' · pin sessions lists them", r.stderr)
        r = self.run_pin("add", "1234567", "x")  # too short for a prefix, no title has it either
        self.assertEqual(r.returncode, 1); self.assertIn("no recent session matches", r.stderr)
        r = self.run_pin("add", SID2.upper(), "again")  # a full id is exact, even when already pinned
        self.assertIn("already pinned as cc", r.stdout)

    def test_sessions_listing(self):
        self.run_pin("add", SID1, "sp")
        r = self.run_pin("sessions")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertRegex(lines[0], rf"^{SID1[:8]}  Standup prep\s+~/git/proj\s+2d\s+2 msgs\s+📌 sp$")
        self.assertRegex(lines[1], rf"^{SID2[:8]}  Command center collector\s+~/git/cc\s+9d\s+2 msgs$")
        self.assertEqual(len(lines), 2)                                             # piped: no label row
        r = self.run_pin("sessions", "collector")
        self.assertEqual(len(r.stdout.splitlines()), 1); self.assertIn(SID2[:8], r.stdout)
        r = self.run_pin("sessions", "--json", "standup")
        data = json.loads(r.stdout)
        self.assertEqual([(d["session_id"], d["alias"], d["messages"]) for d in data], [(SID1, "sp", 2)])
        r = self.run_pin("sessions", "zzz")
        self.assertEqual(r.returncode, 1); self.assertIn("no matching sessions", r.stderr)
        self.assertEqual(json.loads(self.run_pin("sessions", "zzz", "--json").stdout), [])
        self.t1.unlink(); self.t2.unlink()
        r = self.run_pin("sessions")
        self.assertEqual(r.returncode, 1); self.assertIn("no sessions found", r.stderr)

    def test_tables_on_a_terminal(self):
        """On a tty ``pin list`` gets the picker's column labels and marker legend and ``pin sessions``
        its labels; piped output (the other tests) stays bare rows."""
        self.run_pin("add", SID1, "standup-prep", "--keep")
        out = self.run_pin_tty("list")
        lines = [l for l in out.splitlines() if l.strip()]
        self.assertRegex(lines[0], r"^alias\s+title\s+directory\s+idle$")
        self.assertRegex(lines[1], r"^standup-prep\s+Standup prep\s+~/git/proj\s+0m\s+🚩$")
        self.assertEqual(lines[2], "🟢 open  🚩 keep  🔀 fork  🌳 worktree  ⏳ expiring  🔴 expired")
        self.assertEqual(len(lines), 3)
        out = self.run_pin_tty("sessions")
        lines = out.splitlines()
        self.assertRegex(lines[0], r"^id\s+title\s+directory\s+idle\s+msgs\s+pin$")
        self.assertRegex(lines[1], rf"^{SID1[:8]}  Standup prep\s+~/git/proj\s+0m\s+2 msgs\s+📌 standup-prep$")
        self.assertEqual(len(lines), 3)
        self.run_pin("rm", "standup-prep")
        self.assertNotIn("pin", self.run_pin_tty("sessions").splitlines()[0])       # no column when nothing is pinned
        self.assertNotIn("alias", self.run_pin_tty("list", "--json"))             # never in the JSON

    def test_listed_id_prefix_grows_on_a_clash(self):
        """The listed id is the shortest prefix (8+) unique among the recent sessions, git-style, so
        it always resolves; a UUID's ninth character is the hyphen, so a clash grows it to ten."""
        twin = SID1[:9] + "5" + SID1[10:]
        self.make_session(twin, age_days=3, title="Standup prep twin")
        r = self.run_pin("sessions")
        ids = [l.split("  ")[0] for l in r.stdout.splitlines()]
        self.assertEqual(ids, [SID1[:10], twin[:10], SID2[:8]])                  # newest first
        r = self.run_pin("add", SID1[:8], "x")
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"2 sessions match '{SID1[:8]}'; give the id or more words:", r.stderr)
        self.assertRegex(r.stderr, rf"  {SID1[:10]}  Standup prep\s")
        self.assertRegex(r.stderr, rf"  {twin[:10]}  Standup prep twin\s")
        r = self.run_pin("add", twin[:10], "twin")
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("✓ pinned as twin · Standup prep twin", r.stdout)

    def test_help_and_bad_usage(self):
        r = self.run_pin("--help")
        self.assertEqual(r.returncode, 0); self.assertIn("pin sessions   recent sessions", r.stdout)
        r = self.run_pin("help")
        self.assertEqual(r.returncode, 0); self.assertIn("usage: pin", r.stdout)
        r = self.run_pin("--version")
        self.assertRegex(r.stdout, r"^pin \d+\.\d+\.\d+$")
        r = self.run_pin("words", "--bogus")  # query parser errors exit 2 like argparse
        self.assertEqual(r.returncode, 2); self.assertIn("unrecognized arguments", r.stderr)
        r = self.run_pin("add")
        self.assertEqual(r.returncode, 2)
        r = self.run_pin("list", env={"CLAUDE_PINS_FZF": ""})  # non-tty and no words → plain list
        self.assertEqual(r.returncode, 0); self.assertIn("No pins yet. Run /pins:pin inside a Claude session", r.stdout)
        r = self.run_pin(env={"CLAUDE_PINS_FZF": ""})
        self.assertEqual(r.returncode, 0); self.assertIn("No pins yet", r.stdout)
        r = self.run_pin("nomatch", env={"CLAUDE_PINS_FZF": ""})
        self.assertEqual(r.returncode, 1); self.assertIn("no pin matches 'nomatch'", r.stderr)

    def test_edit_flags(self):
        self.run_pin("add", SID1, "a", "--note", "n1")
        r = self.run_pin("edit", "a", "--title", "T2", "--note", "", "--cwd", "~/git/cc", "--model", "sonnet",
                         "--permission-mode", "plan", "--keep")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(self.run_pin("list", "--json").stdout)[0]
        self.assertEqual((d["title"], d["note"], d["cwd"], d["launch"], d["keep"]),
                         ("T2", "", str(self.home / "git" / "cc"), {"model": "sonnet", "permission_mode": "plan"}, True))
        self.run_pin("edit", "a", "--model", "", "--permission-mode", "", "--no-keep")
        d = json.loads(self.run_pin("list", "--json").stdout)[0]
        self.assertEqual((d["launch"], d["keep"]), ({}, False))
        r = self.run_pin("add", SID1, "a", "--note", "n2")
        self.assertIn("note updated", r.stdout)

    def test_prune_asks(self):
        self.run_pin("add", SID1, "a"); self.t1.unlink()
        plain = {"CLAUDE_PINS_NO_FZF": "1"}
        r = self.run_pin("prune", input="n\n", env=plain)
        self.assertEqual(r.returncode, 1); self.assertIn("1 expired: a", r.stdout)
        self.assertEqual(self.run_pin("_complete").stdout.split(), ["a"])
        r = self.run_pin("prune", input="", env=plain)
        self.assertEqual(r.returncode, 130)  # ctrl-c / EOF at the question
        self.steps({"abort": True})                                  # with fzf the question is a screen: esc
        r = self.run_pin("prune")
        self.assertEqual(r.returncode, 130)
        argv = self.fzf_calls()[0]["argv"]
        self.assertEqual(argv[argv.index("--prompt") + 1], "📌 pins › prune › ")
        self.steps({"key": "", "select": ["yes"]})
        r = self.run_pin("prune")
        self.assertEqual(r.returncode, 0); self.assertIn("✓ pruned 1", r.stdout)   # printed after the screen is gone
        r = self.run_pin("touch", "a")
        self.assertEqual(r.returncode, 1); self.assertIn("no pin named a", r.stderr)
        self.run_pin("undo")
        r = self.run_pin("touch", "a")
        self.assertEqual(r.returncode, 1); self.assertIn("a: transcript is gone (expired)", r.stderr)

    def test_doctor_failures(self):
        self.store_path().parent.mkdir(parents=True); self.store_path().write_text("nope")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.root / "missing")
        r = self.run_pin("doctor", env={"CLAUDE_PINS_FZF": str(self.root / "no-such-fzf")})
        self.assertEqual(r.returncode, 1)
        self.assertIn("✗ fzf: not found · install fzf ≥ 0.44", r.stdout)
        self.assertIn("✗ store: pin store", r.stdout)
        self.assertIn(f"✗ projects dir {self.root}/missing/projects not found (set CLAUDE_CONFIG_DIR?)", r.stdout)
        self.assertIn("· keymap ~/.config/claude-pins/keys.toml (defaults)", r.stdout)
        self.stub("fzf", "#!/bin/sh\necho '0.38.0 (old)'\n")
        r = self.run_pin("doctor", env={"CLAUDE_PINS_FZF": str(self.bindir / "fzf")})
        self.assertIn("✗ fzf 0.38.0: need ≥ 0.44", r.stdout)

    def test_hidden_helpers_edge_cases(self):
        self.run_pin("add", SID1, "a")
        for args in (("_preview",), ("_preview", "-"), ("_preview", "nope"), ("_spreview",)):
            r = self.run_pin(*args)
            self.assertEqual((r.returncode, r.stdout), (0, ""), args)
        r = self.run_pin("_spreview", str(self.t1))
        self.assertIn("Standup prep", r.stdout); self.assertIn("dir        ~/git/proj", r.stdout)
        r = self.run_pin("_spreview", str(self.root / "gone.jsonl"))
        self.assertIn("(transcript gone)", r.stdout)
        self.t1.unlink()
        r = self.run_pin("_preview", "a")  # transcript gone: static preview, no cost line
        self.assertIn("(transcript gone)", r.stdout); self.assertIn("expired", r.stdout)
        self.assertNotIn("cost", r.stdout)
        r = self.run_pin("_rows")           # the only pin is expired: the sticky row carries the empty message
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("\n"), 1)
        self.assertTrue(r.stdout.startswith("-\tNo pins yet."))
        r = self.run_pin("_rows", "--all", env={"COLUMNS": "50"})
        self.assertRegex(r.stdout, r"^-\talias\s+title\s+directory\s+idle\n")
        self.assertRegex(r.stdout, r"\na\t.*🔴\n")

    def test_undo_when_repinned_and_store_unwritable(self):
        self.run_pin("add", SID1, "a"); self.run_pin("rm", "a"); self.run_pin("add", SID1, "b")
        r = self.run_pin("undo")
        self.assertEqual(r.returncode, 0); self.assertIn("✓ restored nothing (already re-pinned) (unpin)", r.stdout)
        d = self.store_path().parent; d.chmod(0o500)
        try:
            r = self.run_pin("rm", "b")
        finally:
            d.chmod(0o700)
        self.assertEqual(r.returncode, 1)
        self.assertRegex(r.stderr, r"^pin: cannot write .*pins.json: \[Errno 13\]")  # one line, no traceback
        self.assertEqual(self.run_pin("_complete").stdout.split(), ["b"])
