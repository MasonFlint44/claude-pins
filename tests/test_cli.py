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
        self.assertRegex(r.stdout, r"cc-collector\s+CC\s+~/git/cc\s+0m\s+⚑")  # keep → touched on every run
        r = self.run_pin("list", "--json")
        data = json.loads(r.stdout)
        self.assertEqual(sorted(d["alias"] for d in data), ["cc-collector", "standup-prep"])
        self.assertEqual({d["alias"]: d["markers"] for d in data}["cc-collector"], "⚑")
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
        self.assertRegex(r.stdout, r"a\s+Standup prep\s+~/git/proj\s+0m\s+⚑")
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
        self.assertRegex(r.stdout, r"b\s+Command center collector\s+~/git/cc\s+✗")
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
