import json
import os
import subprocess
import time
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
        self.assertRegex(r.stdout, r"standup-prep\s+Standup prep\s+~/git/proj\s+0m")   # naming the session is activity
        self.assertRegex(r.stdout, r"cc-collector\s+CC\s+~/git/cc\s+0m\s+🚩")  # keep → touched on every run
        r = self.run_pin("list", "--json")
        data = json.loads(r.stdout)
        self.assertEqual(sorted(d["alias"] for d in data), ["cc-collector", "standup-prep"])
        self.assertEqual({d["alias"]: d["markers"] for d in data}["cc-collector"], "🚩")
        r = self.run_pin("rm", "standup-prep")
        self.assertIn("✓ unpinned standup-prep · pins undo", r.stdout)
        r = self.run_pin("rm", "standup-prep")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no pin named standup-prep", r.stderr)
        r = self.run_pin("undo")
        self.assertIn("✓ restored standup-prep (unpin)", r.stdout)
        r = self.run_pin("undo")
        self.assertEqual(r.returncode, 1); self.assertIn("nothing to undo", r.stderr)

    def last_record(self, path: Path) -> dict:
        return json.loads(path.read_text().splitlines()[-1])

    def stored(self, alias: str) -> dict:
        return next(p for p in json.loads(self.store_path().read_text())["pins"] if p["alias"] == alias)

    def test_pinning_names_the_session(self):
        """add, rename, edit --rename, rm and undo each rename the session and say so; the pin's own
        title is untouched, and the session's prior name comes back on unpin."""
        t3 = self.make_session(SID3, age_days=3, title="Third", custom="My name")
        r = self.run_pin("add", SID1, "standup-prep")
        self.assertEqual(r.stdout.strip(), "✓ pinned as standup-prep · Standup prep · session named 📌 standup-prep")
        self.assertEqual(self.last_record(self.t1), {"type": "custom-title", "customTitle": "📌 standup-prep", "sessionId": SID1})
        self.assertEqual(self.stored("standup-prep")["prior_title"], "")
        r = self.run_pin("add", SID3, "third", "--title", "Mine")
        self.assertEqual(r.stdout.strip(), "✓ pinned as third · Mine · session named 📌 third")
        self.assertEqual((self.stored("third")["title"], self.stored("third")["prior_title"]), ("Mine", "My name"))
        r = self.run_pin("rename", "standup-prep", "sp")
        self.assertEqual(r.stdout.strip(), "✓ renamed standup-prep → sp · session named 📌 sp")
        r = self.run_pin("edit", "sp", "--rename", "sp2", "--note", "n")
        self.assertEqual(r.stdout.strip(), "✓ saved sp2 · session named 📌 sp2")
        r = self.run_pin("add", SID1, "sp3", "--rename")
        self.assertEqual(r.stdout.strip(), "already pinned as sp3 · renamed to sp3 · session named 📌 sp3")
        self.assertEqual(self.last_record(self.t1)["customTitle"], "📌 sp3")
        r = self.run_pin("list")
        self.assertRegex(r.stdout, r"sp3\s+Standup prep\s+~/git/proj\s+0m")          # the pin's title; naming touched it
        r = self.run_pin("rm", "sp3")
        self.assertEqual(r.stdout.strip(), "✓ unpinned sp3 · pins undo restores it · session name cleared")
        self.assertEqual(self.last_record(self.t1)["customTitle"], "")
        r = self.run_pin("undo")
        self.assertEqual(r.stdout.strip(), "✓ restored sp3 (unpin) · session named 📌 sp3")
        r = self.run_pin("rm", "third")
        self.assertEqual(r.stdout.strip(), '✓ unpinned third · pins undo restores it · session named "My name" again')
        self.assertEqual(self.last_record(t3)["customTitle"], "My name")
        # renamed inside Claude since: pins leave the name alone and say nothing about it
        with open(self.t1, "a") as fh:
            fh.write(json.dumps({"type": "custom-title", "customTitle": "Theirs", "sessionId": SID1}) + "\n")
        r = self.run_pin("rename", "sp3", "sp4")
        self.assertEqual(r.stdout.strip(), "✓ renamed sp3 → sp4")
        r = self.run_pin("rm", "sp4")
        self.assertEqual(r.stdout.strip(), "✓ unpinned sp4 · pins undo restores it")
        self.assertEqual(self.last_record(self.t1)["customTitle"], "Theirs")
        r = self.run_pin("undo")
        self.assertEqual(r.stdout.strip(), "✓ restored sp4 (unpin)")
        # no transcript yet: the warning as before, no name line
        r = self.run_pin("add", "44444444-4444-4444-4444-444444444444", "fresh")
        self.assertEqual(r.stdout.strip(), "✓ pinned as fresh · fresh")
        self.assertIn("no transcript found", r.stderr)

    def test_sessions_show_a_pinned_one_under_its_pin_title(self):
        self.pin_aged(SID1, "sp", "--title", "The pin's title")
        r = self.run_pin("sessions")
        self.assertRegex(r.stdout.splitlines()[0], rf"^{SID1[:8]}  The pin's title\s+~/git/proj\s+2d\s+2 msgs\s+📌 sp$")
        data = json.loads(self.run_pin("sessions", "--json", "pin's").stdout)
        self.assertEqual([(d["title"], d["alias"]) for d in data], [("The pin's title", "sp")])
        self.assertEqual(json.loads(self.run_pin("sessions", "--json", "📌").stdout), [])
        r = self.run_pin("add", "pin's title", "other")                        # words match the shown title
        self.assertIn("already pinned as sp", r.stdout)
        r = self.run_pin("_spreview", str(self.t1))
        self.assertTrue(r.stdout.startswith("The pin's title\n"), r.stdout)

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
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID2, "--fork-session", "--name", "cc-collector"])
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
                         ["--resume", SID1, "--fork-session", "--name", "sp", "--worktree", "--model", "opus", "--effort", "high", "--permission-mode", "plan"])
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
        self.assertIn("1 expired · pins list --all · pins prune", r.stdout)
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
        # the keep line says whether the session-start hook runs, which is whether Claude has the plugin enabled
        self.assertIn("· keep: no pins · touched on every pins run only: the pins plugin is not enabled", r.stdout)
        self.run_pin("add", SID1, "a", "--keep")
        self.write_settings({"enabledPlugins": {"pins@claude-toolbox": False}})
        self.assertIn("· keep: 1 pin · touched on every pins run only", self.run_pin("doctor").stdout)
        self.write_settings({"enabledPlugins": {"pins@claude-toolbox": True}})
        self.run_pin("add", SID2, "b", "--keep")
        r = self.run_pin("doctor")
        self.assertIn("✓ keep: 2 pins · touched on every pins run and every Claude session start (plugin hook)", r.stdout)
        (self.claude_dir / "settings.json").write_text("{")
        self.assertIn("· keep: 2 pins · touched on every pins run only: the pins plugin is not enabled", self.run_pin("doctor").stdout)

    def test_keep_hook_touches_kept_pins_and_says_nothing(self):
        """``pins _keep`` is the plugin's SessionStart hook: its stdout would land in Claude's context and a
        nonzero exit would show at every session start, so it is silent and exits 0 whatever the store holds."""
        self.run_pin("add", SID1, "a", "--keep")
        self.run_pin("add", SID2, "b")
        self.age(self.t1, 25); self.age(self.t2, 25)
        r = self.run_pin("_keep")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        self.assertLess(abs(self.t1.stat().st_mtime - time.time()), 5)
        self.assertLess(abs(self.t2.stat().st_mtime - (time.time() - 25 * 86400)), 5)
        from claude_pins import config
        for broken in ("{", "[]"):
            config.pins_file().write_text(broken)
            r = self.run_pin("_keep")
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        config.pins_file().unlink()
        r = self.run_pin("_keep")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

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
        self.assertEqual(r.returncode, 1); self.assertIn("no recent session matches 'nothing like this' · pins sessions lists them", r.stderr)
        r = self.run_pin("add", "1234567", "x")  # too short for a prefix, no title has it either
        self.assertEqual(r.returncode, 1); self.assertIn("no recent session matches", r.stderr)
        r = self.run_pin("add", SID2.upper(), "again")  # a full id is exact, even when already pinned
        self.assertIn("already pinned as cc", r.stdout)

    def test_sessions_listing(self):
        self.pin_aged(SID1, "sp")
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
        """On a tty ``pins list`` gets the picker's column labels and marker legend and ``pins sessions``
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
        self.assertEqual(r.returncode, 0); self.assertIn("pins sessions   recent sessions", r.stdout)
        r = self.run_pin("help")
        self.assertEqual(r.returncode, 0); self.assertIn("usage: pin", r.stdout)
        r = self.run_pin("--version")
        self.assertRegex(r.stdout, r"^pins \d+\.\d+\.\d+$")
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
        script = self.root / "tui.jsonl"
        script.write_text(json.dumps({"send": ["@down", "@enter"]}) + "\n")     # no, on the built-in picker's screen
        native = {"CLAUDE_PINS_NO_FZF": "1", "CLAUDE_PINS_TUI_SCRIPT": str(script)}
        r = self.run_pin("prune", env=native)
        self.assertEqual(r.returncode, 1); self.assertIn("1 expired: a", r.stdout)
        self.assertEqual(self.run_pin("_complete").stdout.split(), ["a"])
        r = self.run_pin("prune", input="", env={"CLAUDE_PINS_NO_FZF": "1"})     # no terminal to ask on
        self.assertEqual(r.returncode, 130); self.assertIn("no terminal to answer", r.stderr)
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
        # fzf is optional: a neutral line that says so and how to get it
        self.assertIn("· fzf: not found (optional; the built-in picker draws the same screens) · install fzf ≥ 0.44", r.stdout)
        self.assertNotIn("alt keys", r.stdout)                                  # not a Mac: no Option line
        self.assertIn("✗ store: pin store", r.stdout)
        self.assertIn(f"✗ projects dir {self.root}/missing/projects not found (set CLAUDE_CONFIG_DIR?)", r.stdout)
        self.assertIn("· keymap ~/.config/claude-pins/keys.toml (defaults)", r.stdout)
        self.stub("fzf", "#!/bin/sh\necho '0.38.0 (old)'\n")
        r = self.run_pin("doctor", env={"CLAUDE_PINS_FZF": str(self.bindir / "fzf")})
        self.assertIn("· fzf 0.38.0: need ≥ 0.44 (optional; the built-in picker draws the same screens)", r.stdout)

    def test_doctor_alt_keys_on_a_mac(self):
        """On a Mac, and in xterm anywhere, the doctor reports whether the terminal sends alt keys: ✓ when
        its switch is on, · with the switch to set when it is off, and · with the switch to check when the
        state is unknown."""
        env = {"CLAUDE_PINS_OS": "darwin", "TERM_PROGRAM": "iTerm.app", "ITERM_PROFILE": "Work"}
        r = self.run_pin("doctor", env=env)
        self.assertIn('· alt keys: not sure iTerm2 sends them · check "Left Option key: Esc+"', r.stdout)
        import plistlib
        prefs = self.home / "Library" / "Preferences" / "com.googlecode.iterm2.plist"
        prefs.parent.mkdir(parents=True)
        prefs.write_bytes(plistlib.dumps({"New Bookmarks": [{"Name": "Default", "Option Key Sends": 0},
                                                            {"Name": "Work", "Option Key Sends": 2}]}))
        self.assertIn("✓ alt keys: iTerm2 sends them", self.run_pin("doctor", env=env).stdout)
        r = self.run_pin("doctor", env={**env, "ITERM_PROFILE": "Default"})
        self.assertIn('· alt keys: iTerm2 does not send them · set "Left Option key: Esc+"', r.stdout)
        r = self.run_pin("doctor", env={"CLAUDE_PINS_OS": "darwin", "TERM_PROGRAM": "tmux"})
        self.assertIn("· alt keys: not sure this terminal sends them · set its Option as Meta switch", r.stdout)
        r = self.run_pin("doctor", env={"CLAUDE_PINS_OS": "linux", "LC_TERMINAL": "iTerm2"})      # ssh from iTerm2
        self.assertIn('· alt keys: iTerm2 does not send them · set "Left Option key: Esc+"', r.stdout)
        self.assertNotIn("alt keys", self.run_pin("doctor", env={"CLAUDE_PINS_OS": "linux"}).stdout)
        r = self.run_pin("doctor", env={"CLAUDE_PINS_OS": "linux", "XTERM_VERSION": "XTerm(379)"})    # stock xterm
        self.assertIn("· alt keys: xterm does not send them · set XTerm*metaSendsEscape: true", r.stdout)

    def test_doctor_without_fzf_exits_zero(self):
        r = self.run_pin("doctor", env={"CLAUDE_PINS_FZF": str(self.root / "no-such-fzf")})
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue(r.stdout.startswith("· fzf: not found"))
        self.assertNotIn("✗ fzf", r.stdout)       # ccusage's own line is a separate matter

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
        self.assertRegex(r.stderr, r"^pins: cannot write .*pins.json: \[Errno 13\]")  # one line, no traceback
        self.assertEqual(self.run_pin("_complete").stdout.split(), ["b"])
