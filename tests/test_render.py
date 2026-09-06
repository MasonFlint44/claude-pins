"""Rendering goldens: rows at several widths, preview, menu, NO_COLOR."""
import re

from claude_pins.cost import Cost, format_tokens
from claude_pins.model import Launch, Pin
from claude_pins.render import Palette, View, display_dir, preview, rows, session_rows
from claude_pins.sessions import Expiry
from claude_pins.transcript import Summary
from tests.helpers import Sandbox


def views(home):
    def mk(alias, title, cwd, age_d, state="ok", remaining=28, **kw):
        pin = Pin(alias=alias, session_id=f"{alias}-x", title=title, cwd=cwd, **kw)
        return View(pin, Expiry(state, age_d * 86400, remaining), is_open=kw.pop("is_open", False) if False else False)
    v1 = View(Pin(alias="standup-prep", session_id="1", title="Standup prep", cwd=f"{home}/git/dotclaude", keep=True),
              Expiry("ok", 2 * 86400, 28), is_open=True)
    v2 = View(Pin(alias="cc-collector", session_id="2", title="Command center collector", cwd=f"{home}/git/command-center"),
              Expiry("ok", 9 * 86400, 21))
    v3 = View(Pin(alias="rc-mower", session_id="3", title="Navimow schedule debug", cwd=home), Expiry("expiring", 26 * 86400, 4))
    v4 = View(Pin(alias="insurance", session_id="4", title="USAA restructure", cwd=f"{home}/git/foo/.claude/worktrees/x",
                  fork=True, worktree=True), Expiry("ok", 1 * 86400, 29))
    v5 = View(Pin(alias="dead", session_id="5", title="Gone session", cwd=f"{home}/tmp"), Expiry("expired", 0, 0))
    return [v1, v2, v3, v4, v5]


class RowTests(Sandbox):
    def test_rows_100_cols(self):
        home = str(self.home)
        out = rows(views(home), width=100)
        self.assertEqual(out, [
            "standup-prep  Standup prep                                          ~/git/dotclaude        2d  ● ⚑",
            "cc-collector  Command center collector                              ~/git/command-center   9d",
            "rc-mower      Navimow schedule debug                                ~                     26d  ⏳",
            "insurance     USAA restructure                                      ~/git/foo ⌂x           1d  ⑂ ⌂",
            "dead          Gone session                                          ~/tmp                      ✗",
        ])
        for line in out:
            self.assertLessEqual(len(line), 100)

    def test_rows_narrow_truncates_title(self):
        out = rows(views(str(self.home)), width=60)
        self.assertEqual(out[1], "cc-collector  Command cen…  ~/git/command-center   9d")
        for line in out:
            self.assertLessEqual(len(line), 60)

    def test_rows_wide_and_numbered(self):
        out = rows(views(str(self.home)), width=140, numbered=True)
        self.assertTrue(out[0].startswith("1  standup-prep  Standup prep"))
        self.assertTrue(out[4].startswith("5  dead"))

    def test_colors(self):
        c = Palette(True)
        out = rows(views(str(self.home)), width=100, color=c)
        self.assertIn("\x1b[32m●\x1b[0m", out[0])            # green open
        self.assertIn("\x1b[33m26d\x1b[0m", out[2])         # yellow expiring age
        self.assertIn("\x1b[33m⏳\x1b[0m", out[2])
        self.assertTrue(out[4].startswith("\x1b[2;9m"))     # dim + strikethrough
        plain = [re.sub(r"\x1b\[[0-9;]*m", "", l) for l in out]
        self.assertEqual(plain, rows(views(str(self.home)), width=100))  # symbols carry meaning alone

    def test_display_dir(self):
        h = str(self.home)
        self.assertEqual(display_dir(h), "~")
        self.assertEqual(display_dir(f"{h}/git/foo"), "~/git/foo")
        self.assertEqual(display_dir(f"{h}/git/foo/.claude/worktrees/x"), "~/git/foo ⌂x")
        self.assertEqual(display_dir("/srv/app"), "/srv/app")
        self.assertEqual(display_dir(""), "")


class PreviewTests(Sandbox):
    def summary(self):
        return Summary(session_id="1", path="/p", cwd=f"{self.home}/git/dotclaude", git_branch="main", ai_title="Standup prep",
                       last_prompt="so the pin command should also touch the transcript when opening a fork, right?",
                       last_answer="Yes. The picker can bind keys to run a command and reload the list afterwards, "
                                   "which is how the flashes work.", model="claude-fable-5-1", effort="high",
                       permission_mode="auto", context_tokens=121_000, prompts=40, replies=45,
                       created="2026-09-06T01:00:00Z", mtime=1.0, size=10)

    def test_preview_golden(self):
        pin = Pin(alias="standup-prep", session_id="1", title="Standup prep", cwd=f"{self.home}/git/dotclaude",
                  note="Tuesday standup, uses jira-cards", pinned_at="2026-09-06T02:00:00-0500")
        view = View(pin, Expiry("ok", 2 * 86400, 28.3), summary=self.summary())
        text = preview(view, Cost("ok", 0.07, 4_800_000), current_branch="main", width=76)
        self.assertEqual(text.split("\n"), [
            "Standup prep",
            "Tuesday standup, uses jira-cards",
            "",
            "dir       ~/git/dotclaude",
            "branch    main  (session: main)",
            "model     fable-5-1 · effort high · mode auto",
            "context   ~121k (60%) · 85 msgs · 4.8M tokens",
            "cost      est $0.07 (ccusage)",
            "created   2026-09-06 · last 2d · pinned 2026-09-06 · expires 28d",
            "",
            "you       so the pin command should also touch the transcript when opening",
            "          a fork, right?",
            "claude    Yes. The picker can bind keys to run a command and reload the",
            "          list afterwards, which is how the flashes work.",
        ])

    def test_preview_variants(self):
        pin = Pin(alias="a", session_id="1", title="T", cwd=f"{self.home}/x", launch=Launch(model="opus", permission_mode="plan"), keep=True)
        s = self.summary()
        text = preview(View(pin, Expiry("expiring", 26 * 86400, 4), summary=s), Cost("zero"), current_branch="feature", width=80)
        self.assertIn("branch    feature  (session: main — differs)", text)
        self.assertIn("model     opus · effort high · mode plan", text)
        self.assertIn("cost      pricing unavailable for fable-5-1 · update ccusage", text)
        self.assertIn("expires 4d", text)
        self.assertIn("keep      on — touched every run", text)
        text = preview(View(pin, Expiry("expired", 0, 0), summary=Summary(exists=False)), Cost("missing"), None, width=80)
        self.assertIn("context   (transcript gone)", text)
        self.assertIn("cost      install ccusage for session cost · npm i -g ccusage", text)
        self.assertIn("expired", text)
        text = preview(View(pin, Expiry("ok", 100, 29.9), summary=s), None, None, width=80)
        self.assertNotIn("cost", text)
        self.assertIn("expires 29d", text)

    def test_session_rows(self):
        s = self.summary()
        s.mtime = __import__("time").time() - 7200
        out = session_rows([(s, False), (s, True)], width=90)
        self.assertRegex(out[0], r"^Standup prep\s+~/git/dotclaude\s+2h\s+85 msgs$")
        self.assertTrue(out[1].endswith("⚑ pinned"))

    def test_format_tokens(self):
        self.assertEqual(format_tokens(950), "950")
        self.assertEqual(format_tokens(121_002), "121k")
        self.assertEqual(format_tokens(4_800_000), "4.8M")
