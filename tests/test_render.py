import os
"""Rendering goldens: rows at several widths, preview, menu, NO_COLOR."""
import re

from claude_pins.cost import Cost, format_tokens
from claude_pins.model import Launch, Pin
from claude_pins import theme
from claude_pins.render import (FZF_COLUMN_SEP, Palette, View, branch_line, crumb, display_dir, fit_dir, format_size,
                                grouped, label_row, layout, legend, preview, rows, session_label_row, session_layout,
                                session_rows)
from claude_pins.text import cells, clip, pad
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
            "standup-prep  Standup prep                                         ~/git/dotclaude         2d  🟢 🚩",
            "cc-collector  Command center collector                             ~/git/command-center    9d",
            "rc-mower      Navimow schedule debug                               ~                      26d  ⏳",
            "insurance     USAA restructure                                     ~/git/foo › x           1d  🔀 🌳",
            "dead          Gone session                                         ~/tmp                       🔴",
        ])
        for line in out:
            self.assertLessEqual(cells(line), 100)
        self.assertEqual(label_row(layout(views(home), 100)),
                         "alias         title                                                directory             idle")

    def test_rows_fzf_columns(self):
        """On the fzf screens the columns are tab-separated so --nth can pick them; the tab stands in for
        one of the two spaces (--tabstop=1 draws it as one), so the plain and fzf rows agree cell for cell.
        A tab inside a value is flattened first: it would pass for a column boundary."""
        home = str(self.home)
        vs = views(home)
        plain_rows = rows(vs, width=100)
        fzf_rows = rows(vs, width=100, sep=FZF_COLUMN_SEP)
        self.assertEqual([r.replace("\t ", "  ") for r in fzf_rows], plain_rows)
        self.assertEqual([r.count("\t") for r in fzf_rows], [4, 3, 4, 4, 4])     # alias·title·dir·idle(·markers)
        self.assertEqual(fzf_rows[1].split("\t")[2].strip(), "~/git/command-center")
        vs[1].pin.title = "tab\there"
        vs[1].pin.cwd = f"{home}/git/odd\tname"
        out = rows(vs, width=100, sep=FZF_COLUMN_SEP)[1]
        self.assertEqual(out.count("\t"), 3)
        self.assertIn("tab here", out)
        self.assertIn("~/git/odd name", out)
        vs[1].pin.title = "tab\there"
        self.assertIn("tab here", rows(vs, width=100)[1])

    def test_rows_text_glyphs(self):
        """The one-cell fallback set: the marker column narrows and ⧗ stands in for ⏳."""
        os.environ["CLAUDE_PINS_GLYPHS"] = "text"
        out = rows(views(str(self.home)), width=100)
        self.assertEqual(out, [
            "standup-prep  Standup prep                                           ~/git/dotclaude         2d  ● ⚑",
            "cc-collector  Command center collector                               ~/git/command-center    9d",
            "rc-mower      Navimow schedule debug                                 ~                      26d  ⧗",
            "insurance     USAA restructure                                       ~/git/foo › x           1d  ⑂ ⌂",
            "dead          Gone session                                           ~/tmp                       ✗",
        ])
        self.assertEqual(legend(), "● open  ⚑ keep  ⑂ fork  ⌂ worktree  ⧗ expiring  ✗ expired")
        self.assertEqual(crumb("a", "edit"), "pins › a › edit › ")            # no logo in the text set
        os.environ["CLAUDE_PINS_GLYPHS"] = "emoji"
        self.assertEqual(legend(), "🟢 open  🚩 keep  🔀 fork  🌳 worktree  ⏳ expiring  🔴 expired")
        self.assertEqual(crumb(), "📌 pins › ")

    def test_glyph_detection(self):
        os.environ.pop("CLAUDE_PINS_GLYPHS")
        os.environ["LANG"] = "en_US.UTF-8"
        os.environ["TERM"] = "xterm-256color"
        self.assertEqual(theme.glyph_mode(), "emoji")
        os.environ["LC_ALL"] = "C"                      # LC_ALL outranks LANG
        self.assertEqual(theme.glyph_mode(), "text")
        os.environ["LC_ALL"] = "de_DE.utf8@euro"
        self.assertEqual(theme.glyph_mode(), "emoji")
        os.environ["TERM"] = "linux"                    # the console has no colour emoji
        self.assertEqual(theme.glyph_mode(), "text")
        os.environ["CLAUDE_PINS_GLYPHS"] = "EMOJI"      # the override wins, whatever the case
        self.assertEqual(theme.glyph_mode(), "emoji")
        os.environ["CLAUDE_PINS_GLYPHS"] = "bogus"      # an unknown value falls back to detection
        self.assertEqual(theme.glyph_mode(), "text")

    def test_rows_narrow_shortens_directory_first(self):
        """Under the title floor the directory column gives way (fish-style, then cut from the left)."""
        out = rows(views(str(self.home)), width=60)
        self.assertEqual(out[1], "cc-collector  Command center…   …command-center    9d")
        self.assertEqual(out[0], "standup-prep  Standup prep      ~/git/dotclaude    2d  🟢 🚩")
        for line in out:
            self.assertLessEqual(cells(line), 60)
        cols = layout(views(str(self.home)), 60)
        self.assertEqual((cols.title, cols.dir), (16, 15))
        self.assertEqual(label_row(cols), "alias         title             directory        idle")

    def test_widths_are_terminal_cells(self):
        self.assertEqual(cells("⏳ 📌 ●"), 7)
        self.assertEqual(pad("⏳", 4), "⏳  ")
        self.assertEqual(pad("⏳xy", 3), "⏳…")
        self.assertEqual(clip("abcdef", 4), "abc…")
        self.assertEqual(clip("abcdef", 4, left=True), "…def")
        self.assertEqual(clip("ab", 1), "…")
        self.assertEqual(clip("ab", 0), "")
        home = str(self.home)
        v = views(home)[0]
        v.pin.alias = "⏳⏳⏳⏳⏳⏳⏳⏳"   # 16 cells in 8 characters: still a 20-cell column, not 16
        line = rows([v], width=100)[0]
        self.assertTrue(line.startswith("⏳⏳⏳⏳⏳⏳⏳⏳  Standup prep"))

    def test_fit_dir(self):
        self.assertEqual(fit_dir("~/git/claude-pins", 30), "~/git/claude-pins")
        self.assertEqual(fit_dir("~/git/claude-pins/deep/dir", 20), "~/g/c/deep/dir")     # only as far as needed
        self.assertEqual(fit_dir("~/git/claude-pins/deep/dir", 12), "~/g/c/d/dir")
        self.assertEqual(fit_dir("~/.config/claude-pins", 16), "~/.c/claude-pins")       # dot-directories keep two
        self.assertEqual(fit_dir("~/git/claude-pins/deep/dir", 7), "…/d/dir")            # the end always survives
        self.assertEqual(fit_dir("~/git/foo › wt-name", 17), "~/g/foo › wt-name")
        self.assertEqual(fit_dir("/srv/very-long-app-name", 10), "…-app-name")

    def test_rows_wide_and_numbered(self):
        out = rows(views(str(self.home)), width=140, numbered=True)
        self.assertTrue(out[0].startswith("1  standup-prep  Standup prep"))
        self.assertTrue(out[4].startswith("5  dead"))

    def test_colors(self):
        c = Palette(True)
        gold, coral, green, peri = (theme.sgr(x) for x in (theme.WARNING, theme.ERROR, theme.SUCCESS, theme.PERIWINKLE))
        out = rows(views(str(self.home)), width=100, color=c)
        self.assertIn(f"\x1b[{gold}m 26d\x1b[0m", out[2])     # expiring age in warning gold
        self.assertNotIn("\x1b[", out[0][out[0].index("🟢"):])  # emoji carry their own colour
        self.assertTrue(out[4].startswith("\x1b[2;9m"))        # dim + strikethrough
        plain = [re.sub(r"\x1b\[[0-9;]*m", "", l) for l in out]
        self.assertEqual(plain, rows(views(str(self.home)), width=100))  # symbols carry meaning alone
        os.environ["CLAUDE_PINS_GLYPHS"] = "text"                # the text glyphs get the colour emoji carry
        out = rows(views(str(self.home)), width=100, color=c)
        self.assertIn(f"\x1b[{green}m●\x1b[0m \x1b[{peri}m⚑\x1b[0m", out[0])
        self.assertIn(f"\x1b[{gold}m⧗\x1b[0m", out[2])
        self.assertIn(f"\x1b[{peri}m⑂\x1b[0m \x1b[{peri}m⌂\x1b[0m", out[3])
        self.assertTrue(out[4].endswith(f"\x1b[{coral}m✗\x1b[0m"))
        plain = [re.sub(r"\x1b\[[0-9;]*m", "", l) for l in out]
        self.assertEqual(plain, rows(views(str(self.home)), width=100))

    def test_palette_and_ramp(self):
        c = Palette(True)
        self.assertEqual(c("x", "bold", "#d97757"), "\x1b[1;38;2;217;119;87mx\x1b[0m")
        self.assertEqual(Palette(False)("x", "bold"), "x")
        self.assertEqual(c("", "bold"), "")
        self.assertEqual(theme.ramp(0), "#6bb85f"); self.assertEqual(theme.ramp(5), "#6bb85f")
        self.assertEqual(theme.ramp(50), "#fab219"); self.assertEqual(theme.ramp(95), "#ff5858")
        self.assertEqual(theme.ramp(100), "#ff5858")
        self.assertEqual(theme.ramp(27.5), "#b2b53c")            # halfway between green and gold
        self.assertEqual([theme.effort_color(e) for e in ("low", "medium", "high", "xhigh", "max")],
                         ["#6bb85f", "#abb540", "#fab219", "#fd803c", "#ff5858"])
        self.assertIsNone(theme.effort_color("weird"))
        self.assertEqual(theme.mode_color("plan"), theme.PLAN)
        self.assertEqual(theme.mode_color("bypassPermissions"), theme.ERROR)
        self.assertIsNone(theme.mode_color("default"))
        spec = theme.fzf_colors()
        self.assertIn("prompt:#d97757", spec); self.assertIn("hl+:#b1b9f9", spec); self.assertIn("header:-1:dim", spec)
        self.assertNotIn("bg+", spec)                             # the current row keeps fzf's default

    def test_display_dir(self):
        h = str(self.home)
        self.assertEqual(display_dir(h), "~")
        self.assertEqual(display_dir(f"{h}/git/foo"), "~/git/foo")
        self.assertEqual(display_dir(f"{h}/git/foo/.claude/worktrees/x"), "~/git/foo › x")
        self.assertEqual(display_dir("/srv/app"), "/srv/app")
        self.assertEqual(display_dir(""), "")

    def test_grouped_gutter(self):
        lines = grouped([("open", "Open", "enter"), ("open", "Open as fork", "alt-o"), ("pin", "Edit…", "alt-e"),
                         ("pin", "Toggle keep (off)", "")])
        self.assertEqual(lines, ["open  Open               enter", "      Open as fork       alt-o",
                                 "pin   Edit…              alt-e", "      Toggle keep (off)"])
        self.assertEqual(grouped([("", "Done", ""), ("", "Cancel", "")]), ["Done", "Cancel"])


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
            "dir        ~/git/dotclaude",
            "branch     main",
            "model      fable-5-1 · effort high · mode auto",
            "context    ~121k (60%) · 4.8M tokens",
            "cost       est $0.07 (ccusage)",
            "transcript 85 msgs · 10 B",
            "created    2026-09-06 · last activity 2d ago · pinned 2026-09-06 · expires 28d",
            "session    1",
            "",
            "you        so the pin command should also touch the transcript when",
            "           opening a fork, right?",
            "claude     Yes. The picker can bind keys to run a command and reload the",
            "           list afterwards, which is how the flashes work.",
        ])
        long = preview(view, None, "main", width=76, exchange_lines=12)
        self.assertNotIn("…", long)
        self.assertIn("\x1b[2m1\x1b[0m", preview(view, None, "main", width=76, color=Palette(True)))  # dim session id

    def test_branch_line(self):
        c = Palette(False)
        self.assertEqual(branch_line("", None, True, c), "(not a git repo)")
        self.assertEqual(branch_line("main", None, True, c), "(not a git repo) · session ran on main")
        self.assertEqual(branch_line("main", None, False, c), "session ran on main")
        self.assertEqual(branch_line("", "main", True, c), "main")
        self.assertEqual(branch_line("main", "main", True, c), "main")
        self.assertEqual(branch_line("HEAD", "main", True, c), "main")            # detached when recorded: nothing
        self.assertEqual(branch_line("feat/x", "main", True, c), "main checked out · session ran on feat/x")
        self.assertEqual(branch_line("feat/x", "main", True, Palette(True)),
                         f"\x1b[{theme.sgr(theme.WARNING)}mmain checked out · session ran on feat/x\x1b[0m")
        self.assertEqual(format_size(10), "10 B"); self.assertEqual(format_size(2048), "2 KB")
        self.assertEqual(format_size(1_300_000), "1.2 MB")

    def test_preview_variants(self):
        pin = Pin(alias="a", session_id="1", title="T", cwd=f"{self.home}/x", launch=Launch(model="opus", permission_mode="plan"), keep=True)
        s = self.summary()
        text = preview(View(pin, Expiry("expiring", 26 * 86400, 4), summary=s), Cost("partial", 0.0, 100, "offline", ["claude-fable-5-1"]), current_branch="feature", width=80)
        self.assertIn("branch     feature checked out · session ran on main", text)
        self.assertIn("model      opus · effort high · mode plan", text)
        coloured = preview(View(pin, Expiry("expiring", 26 * 86400, 4), summary=s), None, "main", width=80, color=Palette(True))
        self.assertIn(f"effort \x1b[{theme.sgr(theme.ramp(50))}mhigh\x1b[0m · mode \x1b[{theme.sgr(theme.PLAN)}mplan\x1b[0m", coloured)
        self.assertIn(f"~121k (\x1b[{theme.sgr(theme.ramp(60))}m60%\x1b[0m)", coloured)   # context on the ramp
        self.assertIn(f"\x1b[{theme.sgr(theme.WARNING)}mexpires 4d\x1b[0m", coloured)
        self.assertIn("cost       no price for fable-5-1 · npm i -g ccusage@latest", text)
        self.assertIn("expires 4d", text)
        self.assertIn("keep       on — touched every run", text)
        text = preview(View(pin, Expiry("expired", 0, 0), summary=Summary(exists=False)), Cost("missing"), None, width=80)
        self.assertIn("context    (transcript gone)", text)
        self.assertNotIn("\ntranscript ", text)
        self.assertIn("cost       install ccusage for session cost · npm i -g ccusage@latest", text)
        self.assertIn("expired", text)
        s.context_tokens = 0
        text = preview(View(pin, Expiry("ok", 100, 29.9), summary=s), None, None, width=80)
        self.assertNotIn("context", text)                     # nothing to say: no row rather than an empty one
        text = preview(View(pin, Expiry("ok", 100, 29.9), summary=s), None, None, width=80)
        self.assertNotIn("cost", text)
        self.assertIn("expires 29d", text)

    def test_session_rows(self):
        s = self.summary()
        s.mtime = __import__("time").time() - 7200
        pairs = [(s, ""), (s, "standup-prep")]
        out = session_rows(pairs, width=90)
        self.assertRegex(out[0], r"^Standup prep\s+~/git/dotclaude\s+2h\s+85 msgs$")
        self.assertEqual(cells(out[0]), cells(out[1]) - cells("  📌 standup-prep"))   # the tag is its own column
        self.assertLessEqual(max(cells(o) for o in out), 90)
        self.assertTrue(out[1].endswith("📌 standup-prep"))                          # the pin's alias, not "pinned"
        self.assertEqual(session_label_row(session_layout(pairs, 90)),
                         "title                                      directory        idle     msgs  pin")
        self.assertEqual(session_label_row(session_layout([(s, "")], 90)),
                         "title                                                       directory        idle     msgs")
        fzf_out = session_rows(pairs, width=90, sep=FZF_COLUMN_SEP)
        self.assertEqual([r.replace("\t ", "  ") for r in fzf_out], out)
        self.assertEqual([r.count("\t") for r in fzf_out], [3, 4])                 # title·dir·idle·msgs(·pin)
        os.environ["CLAUDE_PINS_GLYPHS"] = "text"
        self.assertTrue(session_rows(pairs, width=90)[1].endswith("⚲ standup-prep"))
        long = session_rows([(s, "a-very-long-alias-that-goes-on-and-on")], width=70)[0]
        self.assertLessEqual(cells(long), 70)
        self.assertIn("⚲ a-very-long…", long)                                     # capped like the picker's alias column

    def test_format_tokens(self):
        self.assertEqual(format_tokens(950), "950")
        self.assertEqual(format_tokens(121_002), "121k")
        self.assertEqual(format_tokens(4_800_000), "4.8M")


class FzfWrapperTests(Sandbox):
    def test_version_and_availability(self):
        from claude_pins import fzf
        os.environ["PATH"] = str(self.bindir)  # the real fzf, if installed, is out of reach
        self.assertIsNone(fzf.fzf_version())
        self.stub("fzf", "#!/bin/sh\necho 'no digits here'\n")
        self.assertIsNone(fzf.fzf_version())
        self.assertFalse(fzf.available())
        self.stub("fzf", "#!/bin/sh\necho '0.44.1 (brew)'\n")
        self.assertEqual(fzf.fzf_version(), (0, 44, 1))
        self.assertTrue(fzf.available())
        os.environ["CLAUDE_PINS_NO_FZF"] = "1"
        self.assertFalse(fzf.available())
        os.environ.pop("CLAUDE_PINS_NO_FZF")
        os.environ["CLAUDE_PINS_FZF"] = str(self.root / "missing")
        self.assertIsNone(fzf.fzf_bin())
        self.assertIsNone(fzf.run([fzf.Item("a", "A")], prompt="> "))
        os.environ["CLAUDE_PINS_FZF"] = "fzf"  # a PATH lookup works too
        self.assertEqual(fzf.fzf_version(), (0, 44, 1))
        self.assertIn("install fzf ≥ 0.44", fzf.install_hint())

    def test_run_output_parsing(self):
        from claude_pins import fzf
        # a stand-in that echoes what fzf would print: query, key, selected lines
        self.stub("fzf", "#!/bin/sh\ncat > /dev/null\nprintf 'que\\nalt-t\\nb\\tB\\tB\\n'\n")
        r = fzf.run([fzf.Item("a", "A"), fzf.Item("b", "B")], prompt="> ", expect=["alt-t"],
                    query="que", multi=True, disabled=True, header="h", extra=["--extra"])
        self.assertEqual((r.key, r.query, r.ids), ("alt-t", "que", ["b"]))
        self.stub("fzf", "#!/bin/sh\ncat > /dev/null\nprintf 'q\\n'\n")  # no --expect: line 2 is a selection
        r = fzf.run([fzf.Item("a", "A")], prompt="> ")
        self.assertEqual((r.key, r.query, r.ids), ("", "q", []))
        self.stub("fzf", "#!/bin/sh\nexit 2\n")  # bad option
        self.assertIsNone(fzf.run([fzf.Item("a", "A")], prompt="> "))
        self.stub("fzf", "#!/bin/sh\ncat > /dev/null\nexit 1\n")  # no match: still a result
        self.assertEqual(fzf.run([], prompt="> ").ids, [])
        (self.bindir / "fzf").write_text("not executable")
        self.assertIsNone(fzf.run([fzf.Item("a", "A")], prompt="> "))  # OSError


class KeymapFileTests(Sandbox):
    def test_parse_and_bad_entries(self):
        from claude_pins.keymap import Keymap, parse
        text = '# comment\n[section]\nopen = "enter" # trailing\n"quoted" = x\nnoequals\nnew = \'alt-n\'\nunknown = "f9"\n'
        self.assertEqual(parse(text), {"open": "enter", '"quoted"': "x", "new": "alt-n", "unknown": "f9"})
        km = Keymap(parse(text))
        self.assertEqual(km.key("new"), "alt-n"); self.assertEqual(km.key("unknown"), "")
        self.assertEqual(km.label("new"), "New pin…")
        with self.assertRaises(KeyError):
            km.set("unknown", "f9")
        self.assertTrue(km.is_default())
        km.set("touch", "f5")
        self.assertFalse(km.is_default())
        self.assertEqual(km.conflicts("edit", "f5"), ["touch"])
        self.assertEqual(km.conflicts("edit", ""), [])
        self.assertEqual(Keymap.load(self.root / "nope.toml").key("touch"), "alt-t")
