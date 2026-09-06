import os
import subprocess
from pathlib import Path

from claude_pins import gitutil
from claude_pins.sessions import (expiry_for, find_transcript, format_age, iter_transcripts,
                                  open_session_ids)
from tests.helpers import Sandbox


class DiscoveryTests(Sandbox):
    def test_iter_and_find(self):
        a = self.make_session("11111111-1111-1111-1111-111111111111", age_days=5)
        b = self.make_session("22222222-2222-2222-2222-222222222222", cwd=str(self.home), age_days=1)
        (self.projects / "x").mkdir(); (self.projects / "x" / "notes.jsonl").write_text("{}\n")
        (self.projects / "x" / "memory").mkdir()
        self.assertEqual(iter_transcripts(), [b, a])
        self.assertEqual(find_transcript("11111111-1111-1111-1111-111111111111"), a)
        self.assertEqual(find_transcript("11111111-1111-1111-1111-111111111111", hint=str(a)), a)
        self.assertIsNone(find_transcript("99999999-9999-9999-9999-999999999999"))


class ExpiryTests(Sandbox):
    def test_states_default_period(self):
        p = self.make_session("11111111-1111-1111-1111-111111111111", age_days=2)
        e = expiry_for(p)
        self.assertEqual(e.state, "ok"); self.assertAlmostEqual(e.remaining_days, 28, delta=0.01)
        self.age(p, 26)
        e = expiry_for(p)
        self.assertEqual(e.state, "expiring"); self.assertAlmostEqual(e.remaining_days, 4, delta=0.01)
        self.age(p, 31)
        e = expiry_for(p)
        self.assertEqual(e.state, "expiring"); self.assertEqual(e.remaining_days, 0)
        e = expiry_for(self.projects / "gone.jsonl")
        self.assertTrue(e.expired)

    def test_custom_period_and_warn(self):
        self.write_settings({"cleanupPeriodDays": 10})
        os.environ["CLAUDE_PINS_EXPIRE_WARN"] = "3"
        p = self.make_session("11111111-1111-1111-1111-111111111111", age_days=6)
        self.assertEqual(expiry_for(p).state, "ok")
        self.age(p, 7.5)
        self.assertEqual(expiry_for(p).state, "expiring")
        self.write_settings({"cleanupPeriodDays": 0})  # rejected by schema → default
        self.assertEqual(expiry_for(p).state, "ok")

    def test_fake_clock(self):
        p = self.make_session("11111111-1111-1111-1111-111111111111", age_days=0)
        os.environ["CLAUDE_PINS_NOW"] = str(p.stat().st_mtime + 29 * 86400)
        self.assertEqual(expiry_for(p).state, "expiring")

    def test_format_age(self):
        self.assertEqual(format_age(120), "2m")
        self.assertEqual(format_age(2 * 3600 + 5), "2h")
        self.assertEqual(format_age(26 * 86400 + 100), "26d")


class OpenDetectionTests(Sandbox):
    def test_process_table(self):
        table = [
            "claude --resume 11111111-1111-1111-1111-111111111111",
            "/home/x/.local/bin/claude -r 22222222-2222-2222-2222-222222222222 --model opus",
            "node /x/claude --session-id=33333333-3333-3333-3333-333333333333",
            "claude",
            "vim --resume 44444444-4444-4444-4444-444444444444",
            "claude remote-control --no-create-session-in-dir",
        ]
        self.assertEqual(open_session_ids(table), {
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
            "33333333-3333-3333-3333-333333333333"})

    def test_env_file(self):
        f = self.root / "ps.txt"
        f.write_text("claude --resume 11111111-1111-1111-1111-111111111111\n")
        os.environ["CLAUDE_PINS_PS"] = str(f)
        self.assertEqual(open_session_ids(), {"11111111-1111-1111-1111-111111111111"})


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


class GitTests(Sandbox):
    def repo(self, name="repo"):
        r = self.root / name
        r.mkdir()
        git("init", "-q", "-b", "main", cwd=r)
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init", cwd=r)
        return r

    def test_branch_and_clean(self):
        r = self.repo()
        self.assertTrue(gitutil.is_repo(r))
        self.assertFalse(gitutil.is_repo(self.root))
        self.assertEqual(gitutil.current_branch(r), "main")
        self.assertTrue(gitutil.is_clean(r))
        (r / "f").write_text("x"); git("add", "f", cwd=r)
        self.assertFalse(gitutil.is_clean(r))

    def test_worktree_recreate_surviving_branch(self):
        r = self.repo()
        wt = r / ".claude" / "worktrees" / "x"
        git("worktree", "add", "-b", "worktree-x", str(wt), cwd=r)
        git("worktree", "lock", "--reason", "claude session x (pid 1 start 2)", str(wt), cwd=r)
        import shutil; shutil.rmtree(wt)
        self.assertEqual(gitutil.split_worktree_path(str(wt)), (str(r), "x"))
        self.assertEqual(gitutil.worktree_branch_for(str(r), "x"), "worktree-x")
        ok, path, used = gitutil.recreate_worktree(str(r), "x", "worktree-x")
        self.assertTrue(ok, used)
        self.assertEqual(Path(path), wt)
        self.assertEqual(gitutil.current_branch(wt), "worktree-x")

    def test_worktree_recreate_branch_gone(self):
        r = self.repo()
        self.assertIsNone(gitutil.worktree_branch_for(str(r), "y"))
        ok, path, used = gitutil.recreate_worktree(str(r), "y", None)
        self.assertTrue(ok, used)
        self.assertEqual(used, "worktree-y")
        self.assertEqual(gitutil.current_branch(path), "worktree-y")
        ok, _, used2 = gitutil.recreate_worktree(str(r), "y2", None)
        self.assertEqual(used2, "worktree-y2")

    def test_checkout_offered_only_when_clean(self):
        r = self.repo()
        git("branch", "feature", cwd=r)
        ok, _ = gitutil.checkout(r, "feature")
        self.assertTrue(ok); self.assertEqual(gitutil.current_branch(r), "feature")
