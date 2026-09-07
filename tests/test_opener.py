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
    """The opener's tiers, driven through the plain prompts (piped stdin); OpenerScreenTests below run the
    same prompts as fzf screens."""

    def setUp(self):
        super().setUp()
        os.environ["CLAUDE_PINS_NO_FZF"] = "1"

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


class OpenerScreenTests(FzfSandbox):
    """With fzf in use, every question on the way to claude is an fzf screen: choices are lists, the
    directory is the query line over completions, and what the opener says arrives once the shell is back."""

    def setUp(self):
        super().setUp()
        self.make_session(SID, cwd=str(self.home / "Documents" / "old"))
        self.run_pin("add", SID, "sp")
        shutil.rmtree(self.home / "Documents" / "old")

    def arg(self, call, flag):
        a = call["argv"]
        return a[a.index(flag) + 1] if flag in a else None

    def stored(self):
        return {p["alias"]: p for p in json.loads(self.store_path().read_text())["pins"]}

    def test_missing_dir_menu_then_home(self):
        self.steps({"key": "", "select": ["open in ~"]}, {"key": "", "select": ["yes"]})
        r = self.run_pin("sp")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = self.fzf_calls()
        menu, ask = calls
        self.assertEqual(self.arg(menu, "--prompt"), "📌 pins › sp › open › ")
        self.assertEqual(plain(self.arg(menu, "--header")).split("\n"),
                         ["enter choose · esc cancel", "sp: directory ~/Documents/old is missing", " "])
        self.assertEqual([l.split("\t")[1] for l in menu["lines"]],
                         ["open in ~ (session context won't match this directory)", "choose another directory", "unpin"])
        self.assertEqual(plain(self.arg(ask, "--header")).split("\n")[-2:], ["update pin cwd?", " "])
        self.assertEqual(self.stored()["sp"]["cwd"], str(self.home))
        self.assertEqual(self.claude_calls()["cwd"], str(self.home))
        # the banner was held back until the screen was gone, so it is the last thing before claude
        self.assertEqual(r.stdout.strip(), "→ opening in ~ · session context won't match this directory")

    def test_directory_screen_and_unpin(self):
        other = self.home / "git" / "elsewhere"; other.mkdir(parents=True)
        self.steps({"key": "", "select": ["choose another"]}, {"query": "~/git/else", "raw": ["~/git/elsewhere/\t~/git/elsewhere/"]},
                   {"key": "", "select": ["no"]})
        r = self.run_pin("sp")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        dirs = self.fzf_calls()[1]
        self.assertEqual(self.arg(dirs, "--prompt"), "📌 pins › sp › open › directory › ")
        self.assertIn("--disabled", dirs["argv"])
        self.assertTrue(any("_dirs {q}" in a for a in dirs["argv"]))
        self.assertEqual(self.claude_calls()["cwd"], str(other))
        self.assertEqual(self.stored()["sp"]["cwd"], str(self.home / "Documents" / "old"))   # declined the update
        self.steps({"key": "", "select": ["choose another"]}, {"query": "/no/such/dir"})
        r = self.run_pin("sp")
        self.assertEqual(r.returncode, 1); self.assertIn("not a directory; cancelled", r.stdout)
        self.steps({"key": "", "select": ["choose another"]}, {"abort": True})
        r = self.run_pin("sp")
        self.assertEqual(r.returncode, 1); self.assertEqual(r.stdout, "")
        self.steps({"key": "", "select": ["unpin"]})
        r = self.run_pin("sp")
        self.assertEqual(r.returncode, 1); self.assertIn("✓ unpinned sp · pin undo restores it", r.stdout)
        self.assertEqual(self.stored(), {})

    def test_notes_reach_the_picker_flash(self):
        """From the picker the opener's screens run under the held screen: on a cancel what it said joins
        the flash instead of vanishing under the next screen."""
        self.steps({"key": "", "select": ["sp"]}, {"key": "", "select": ["unpin"]}, {"abort": True})
        r = self.run_pin()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout, "")
        calls = self.fzf_calls()
        self.assertIn("✓ unpinned sp · pin undo restores it · cancelled", plain(self.arg(calls[-1], "--header")))
        self.assertEqual(self.stored(), {})

    def test_already_open_and_branch_screens(self):
        repo = self.home / "git" / "foo"; repo.mkdir(parents=True)
        git("init", "-q", "-b", "main", cwd=repo); git("commit", "-q", "--allow-empty", "-m", "init", cwd=repo)
        git("branch", "feature", cwd=repo)
        shutil.rmtree(self.project_dir(str(self.home / "Documents" / "old")))    # the session moves to the repo
        self.make_session(SID, cwd=str(repo), branch="feature")
        self.run_pin("edit", "sp", "--cwd", str(repo))
        ps = self.root / "ps.txt"; ps.write_text(f"claude --resume {SID}\n")
        self.steps({"key": "", "select": ["resume anyway"]}, {"key": "", "select": ["checkout feature"]})
        r = self.run_pin("sp", env={"CLAUDE_PINS_PS": str(ps)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = self.fzf_calls()
        self.assertIn("sp is already open in another tab", plain(self.arg(calls[0], "--header")))
        self.assertIn("start:pos(2)", " ".join(calls[0]["argv"]))                 # default: cancel
        self.assertIn("is on main, the session was on feature", plain(self.arg(calls[1], "--header")))
        self.assertEqual(git("branch", "--show-current", cwd=repo), "feature")
        self.assertEqual(r.stdout.strip(), "→ checked out feature")
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID])


def plain(text):
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", text)
