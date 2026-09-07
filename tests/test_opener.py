import json
import os
import shutil
import subprocess
from pathlib import Path

from tests.helpers import FzfSandbox

SID = "11111111-1111-1111-1111-111111111111"


def git(*args, cwd):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd,
                          capture_output=True, text=True, check=True).stdout.strip()


class OpenerTests(FzfSandbox):
    def repo(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        git("init", "-q", "-b", "main", cwd=path)
        git("commit", "-q", "--allow-empty", "-m", "init", cwd=path)
        return path

    def pin_in(self, cwd: str, **kw):
        self.make_session(SID, cwd=cwd, **kw)
        r = self.run_pin("add", SID, "sp")
        self.assertEqual(r.returncode, 0, r.stderr)

    def stored(self):
        return {p["alias"]: p for p in json.loads(self.store_path().read_text())["pins"]}

    # tier 1: exists → silent
    def test_existing_dir_silent(self):
        self.pin_in(str(self.home / "git" / "proj"))
        r = self.run_pin("sp")
        self.assertEqual(r.stdout, "")
        self.assertEqual(self.claude_calls()["cwd"], str(self.home / "git" / "proj"))

    # tier 2a: worktree gone, branch survives
    def test_worktree_recreated_on_surviving_branch(self):
        repo = self.repo(self.home / "git" / "foo")
        wt = repo / ".claude" / "worktrees" / "x"
        git("worktree", "add", "-q", "-b", "worktree-x", str(wt), cwd=repo)
        git("worktree", "lock", str(wt), cwd=repo)
        self.pin_in(str(wt), branch="worktree-x")
        shutil.rmtree(wt)
        r = self.run_pin("sp", input="\ny\n")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("directory ~/git/foo/.claude/worktrees/x is missing", r.stdout)
        self.assertIn("branch worktree-x still exists in ~/git/foo", r.stdout)
        self.assertIn("1) recreate the worktree on branch worktree-x        (enter)", r.stdout)
        self.assertIn("→ recreated ~/git/foo/.claude/worktrees/x on worktree-x", r.stdout)
        self.assertNotIn("update pin cwd?", r.stdout)  # same path as the pin → nothing to update
        self.assertTrue(wt.is_dir())
        self.assertEqual(self.claude_calls()["cwd"], str(wt))
        self.assertEqual(git("branch", "--show-current", cwd=wt), "worktree-x")

    # tier 2b: worktree and branch gone → fresh worktree or repo root
    def test_worktree_branch_gone(self):
        repo = self.repo(self.home / "git" / "foo")
        wt = repo / ".claude" / "worktrees" / "x"
        self.pin_in(str(wt))
        shutil.rmtree(wt)
        r = self.run_pin("sp", input="2\nn\n")
        self.assertIn("branch worktree-x is gone", r.stdout)
        self.assertIn("1) create a fresh worktree x off HEAD", r.stdout)
        self.assertIn("2) open in the repo root ~/git/foo", r.stdout)
        self.assertEqual(self.claude_calls()["cwd"], str(repo))
        self.assertEqual(self.stored()["sp"]["cwd"], str(wt))  # answered n → pin unchanged
        r = self.run_pin("sp", input="1\ny\n")
        self.assertIn("→ recreated ~/git/foo/.claude/worktrees/x on worktree-x", r.stdout)
        self.assertEqual(self.stored()["sp"]["cwd"], str(wt))
        self.assertTrue(wt.is_dir())

    # tier 3: plain directory gone
    def test_plain_dir_gone(self):
        gone = self.home / "Documents" / "old"
        self.pin_in(str(gone))
        shutil.rmtree(gone)
        r = self.run_pin("sp", input="\ny\n")
        self.assertIn("1) open in ~ (session context won't match this directory)        (enter)", r.stdout)
        self.assertIn("2) choose another directory", r.stdout)
        self.assertIn("3) unpin", r.stdout)
        self.assertEqual(self.claude_calls()["cwd"], str(self.home))
        self.assertEqual(self.stored()["sp"]["cwd"], str(self.home))

    def test_choose_dir_and_unpin(self):
        gone = self.home / "Documents" / "old"
        self.pin_in(str(gone))
        shutil.rmtree(gone)
        other = self.home / "git" / "elsewhere"; other.mkdir(parents=True)
        r = self.run_pin("sp", input=f"2\n{other}\nn\n")
        self.assertEqual(self.claude_calls()["cwd"], str(other))
        r = self.run_pin("sp", input="3\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("✓ unpinned sp", r.stdout)
        self.assertEqual(self.stored(), {})

    def test_cancel_with_eof(self):
        gone = self.home / "Documents" / "old"
        self.pin_in(str(gone)); shutil.rmtree(gone)
        r = self.run_pin("sp", input="")
        self.assertEqual(r.returncode, 1)
        self.assertIsNone(self.claude_calls())

    # branch check
    def test_branch_mismatch_clean_offers_checkout(self):
        repo = self.repo(self.home / "git" / "foo")
        git("branch", "feature", cwd=repo)
        self.pin_in(str(repo), branch="feature")
        r = self.run_pin("sp", input="2\n")
        self.assertIn("is on main, the session was on feature", r.stdout)
        self.assertIn("2) checkout feature first (tree is clean)", r.stdout)
        self.assertIn("→ checked out feature", r.stdout)
        self.assertEqual(git("branch", "--show-current", cwd=repo), "feature")
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID])

    def test_branch_mismatch_dirty_no_checkout(self):
        repo = self.repo(self.home / "git" / "foo")
        git("branch", "feature", cwd=repo)
        (repo / "f").write_text("x"); git("add", "f", cwd=repo)
        self.pin_in(str(repo), branch="feature")
        r = self.run_pin("sp", input="\n")
        self.assertIn("working tree has changes, so checkout is not offered", r.stdout)
        self.assertNotIn("checkout feature", r.stdout)
        self.assertIn("1) continue on main        (enter)", r.stdout)
        self.assertEqual(git("branch", "--show-current", cwd=repo), "main")  # never auto-switch
        self.assertEqual(self.claude_calls()["cwd"], str(repo))

    def test_branch_check_skipped_for_worktree_open(self):
        repo = self.repo(self.home / "git" / "foo")
        git("branch", "feature", cwd=repo)
        self.pin_in(str(repo), branch="feature")
        r = self.run_pin("sp", "-w", input="")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("session was on", r.stdout)
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID, "--worktree"])

    # already open
    def test_already_open(self):
        self.pin_in(str(self.home / "git" / "proj"))
        ps = self.root / "ps.txt"; ps.write_text(f"claude --resume {SID}\n")
        r = self.run_pin("sp", input="\n", env={"CLAUDE_PINS_PS": str(ps)})
        self.assertEqual(r.returncode, 1)
        self.assertIn("sp is already open in another tab", r.stdout)
        self.assertIn("2) cancel        (enter)", r.stdout)
        self.assertIsNone(self.claude_calls())
        r = self.run_pin("sp", input="1\n", env={"CLAUDE_PINS_PS": str(ps)})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID])
        r = self.run_pin("list", env={"CLAUDE_PINS_PS": str(ps)})
        self.assertRegex(r.stdout, r"sp\s+A session\s+~/git/proj\s+\d+[md]\s+🟢")

    def test_open_touches_transcript(self):
        self.pin_in(str(self.home / "git" / "proj"), age_days=20)
        t = self.project_dir(str(self.home / "git" / "proj")) / f"{SID}.jsonl"
        before = t.stat().st_mtime
        self.run_pin("sp", "--fork")
        self.assertGreater(t.stat().st_mtime, before + 86400 * 19)

    def test_branch_check_skipped_inside_claude_worktree(self):
        # A session started with --worktree records gitBranch before the checkout, so it always
        # says the base branch; reopening in the worktree must not offer to check that out.
        repo = self.repo(self.home / "git" / "foo")
        wt = repo / ".claude" / "worktrees" / "x"
        git("worktree", "add", "-q", "-b", "worktree-x", str(wt), cwd=repo)
        self.pin_in(str(wt), branch="main")
        r = self.run_pin("sp", input="")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("session was on", r.stdout)
        self.assertEqual(git("branch", "--show-current", cwd=wt), "worktree-x")
        self.assertEqual(self.claude_calls()["cwd"], str(wt))

    def test_missing_dir_other_choices(self):
        repo = self.repo(self.home / "git" / "foo")
        wt = repo / ".claude" / "worktrees" / "x"
        git("worktree", "add", "-q", "-b", "worktree-x", str(wt), cwd=repo)
        self.pin_in(str(wt), branch="main")  # what Claude records for a worktree session: the base branch
        git("worktree", "remove", "--force", str(wt), cwd=repo)
        # repo root, and decline the cwd update
        r = self.run_pin("sp", input="2\nn\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("→ opening in the repo root ~/git/foo", r.stdout)
        self.assertEqual(self.claude_calls()["cwd"], str(repo))
        self.assertEqual(self.stored()["sp"]["cwd"], str(wt))
        # ~, accepting the update
        r = self.run_pin("sp", input="3\n\n")
        self.assertIn("session context won't match", r.stdout)
        self.assertEqual(self.stored()["sp"]["cwd"], str(self.home))

    def test_choose_dir_cancel_and_not_a_dir(self):
        gone = self.home / "Documents" / "old"
        self.pin_in(str(gone)); shutil.rmtree(gone)
        r = self.run_pin("sp", input="2\n")  # EOF at the directory prompt
        self.assertEqual(r.returncode, 1); self.assertIsNone(self.claude_calls())
        r = self.run_pin("sp", input="2\n/no/such/dir\n")
        self.assertEqual(r.returncode, 1); self.assertIn("not a directory; cancelled", r.stdout)
        r = self.run_pin("sp", input="2\n\n")  # empty answer
        self.assertEqual(r.returncode, 1); self.assertIn("not a directory; cancelled", r.stdout)
        r = self.run_pin("sp", input="9\n2\n")  # out-of-range choice is re-asked
        self.assertIn("pick 1–3", r.stdout)

    def test_recreate_worktree_failure(self):
        repo = self.repo(self.home / "git" / "foo")
        wt = repo / ".claude" / "worktrees" / "x"
        git("worktree", "add", "-q", "-b", "worktree-x", str(wt), cwd=repo)
        self.pin_in(str(wt), branch="worktree-x")
        git("worktree", "remove", "--force", str(wt), cwd=repo)
        git("checkout", "-q", "worktree-x", cwd=repo)  # the branch is now checked out in the main tree
        r = self.run_pin("sp", input="\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("could not recreate worktree:", r.stdout)
        self.assertIsNone(self.claude_calls())

    def test_branch_mismatch_cancel_and_checkout_failure(self):
        repo = self.repo(self.home / "git" / "foo")
        git("branch", "feature", cwd=repo)
        self.pin_in(str(repo), branch="feature")
        r = self.run_pin("sp", input="3\n")
        self.assertEqual(r.returncode, 1); self.assertIsNone(self.claude_calls())
        r = self.run_pin("sp", input="")
        self.assertEqual(r.returncode, 1)
        # the recorded branch no longer exists: checkout fails, nothing launches
        git("branch", "-D", "feature", cwd=repo)
        r = self.run_pin("sp", input="2\n")
        self.assertEqual(r.returncode, 1); self.assertIn("checkout failed:", r.stdout)
        self.assertIsNone(self.claude_calls())
        # detached HEAD in the session record: no check at all
        self.make_session(SID, cwd=str(repo), branch="HEAD")
        r = self.run_pin("sp")
        self.assertEqual(r.returncode, 0, r.stdout); self.assertEqual(self.claude_calls()["argv"], ["--resume", SID])

    def test_already_open_eof_cancels(self):
        self.pin_in(str(self.home / "git" / "proj"))
        ps = self.root / "ps.txt"; ps.write_text(f"/usr/bin/node /opt/claude --resume={SID}\n")
        r = self.run_pin("sp", input="", env={"CLAUDE_PINS_PS": str(ps)})
        self.assertEqual(r.returncode, 1); self.assertIsNone(self.claude_calls())

    def test_transcript_moved_updates_pin(self):
        self.pin_in(str(self.home / "git" / "proj"))
        old = Path(self.stored()["sp"]["transcript"])
        new_dir = self.projects / "-elsewhere"; new_dir.mkdir()
        old.rename(new_dir / old.name)
        r = self.run_pin("sp")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.stored()["sp"]["transcript"], str(new_dir / old.name))

    def test_claude_missing_from_path(self):
        self.pin_in(str(self.home / "git" / "proj"))
        (self.bindir / "claude").unlink()
        r = self.run_pin("sp")
        self.assertEqual(r.returncode, 1); self.assertIn("claude is not on PATH", r.stderr)
