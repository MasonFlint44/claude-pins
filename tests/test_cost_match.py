import os

from claude_pins.cost import Cost, session_cost
from claude_pins.match import loose_match
from claude_pins.model import Pin
from tests.helpers import Sandbox


def row(sid, total, tokens, breakdowns):
    return {"period": sid, "totalCost": total, "totalTokens": tokens,
            "modelsUsed": [b[0] for b in breakdowns],
            "modelBreakdowns": [{"modelName": m, "cost": c, "inputTokens": t, "outputTokens": 0,
                                 "cacheCreationTokens": 0, "cacheReadTokens": 0} for m, c, t in breakdowns]}


SID = "11111111-1111-1111-1111-111111111111"


class CostTests(Sandbox):
    def stub_ccusage(self, offline: list, online: list | None = None, online_exit: int = 0, online_sleep: float = 0):
        """A ccusage stand-in: prints the offline listing, or the online one when --offline is absent."""
        import json as _json
        (self.root / "offline.json").write_text(_json.dumps({"session": offline}))
        (self.root / "online.json").write_text(_json.dumps({"session": online if online is not None else offline}))
        self.stub("ccusage", "#!/bin/sh\n"
                             "if [ \"$1\" = --version ]; then echo 'ccusage 20.0.20'; exit 0; fi\n"
                             f"echo \"$@\" >> {self.root}/ccusage.log\n"
                             "case \" $* \" in *' --offline '*) cat " + str(self.root / "offline.json") + ";;\n"
                             f"*) sleep {online_sleep}; cat " + str(self.root / "online.json") + f"; exit {online_exit};; esac\n")

    def calls(self):
        p = self.root / "ccusage.log"
        return p.read_text().splitlines() if p.exists() else []

    def test_not_installed(self):
        os.environ["PATH"] = str(self.bindir)
        c = session_cost("s", None)
        self.assertEqual(c.status, "missing")
        self.assertIn("npm i -g ccusage@latest", c.line())

    def test_ok_offline_and_cached_by_mtime(self):
        self.stub_ccusage([row(SID, 0.0712, 4800000, [("claude-opus-4-1", 0.0712, 100)])])
        t = self.make_session(SID)
        c = session_cost(SID, t)
        self.assertEqual((c.status, c.total_tokens, c.source), ("ok", 4800000, "offline"))
        self.assertEqual(c.line(), "est $0.07 (ccusage)")
        session_cost(SID, t)
        self.assertEqual(len(self.calls()), 1)              # cached
        self.assertIn("--offline", self.calls()[0])
        os.utime(t, None)
        session_cost(SID, t)
        self.assertEqual(len(self.calls()), 2)

    def test_partial_offline_falls_back_to_online(self):
        offline = [row(SID, 0.05, 22_000_000, [("claude-haiku-4-5-20251001", 0.05, 100), ("claude-fable-5-1", 0.0, 21_000_000)])]
        online = [row(SID, 23.81, 22_000_000, [("claude-haiku-4-5-20251001", 0.05, 100), ("claude-fable-5-1", 23.76, 21_000_000)])]
        self.stub_ccusage(offline, online)
        t = self.make_session(SID)
        c = session_cost(SID, t)
        self.assertEqual((c.status, c.source), ("ok", "online"))
        self.assertEqual(c.line(), "est $23.81 (ccusage online)")
        self.assertEqual(len(self.calls()), 2)
        session_cost(SID, t)
        self.assertEqual(len(self.calls()), 2)              # online answer cached

    def test_partial_everywhere_shows_update_hint_and_backs_off(self):
        offline = [row(SID, 0.05, 1000, [("claude-haiku-4-5-20251001", 0.05, 100), ("claude-fable-5-1", 0.0, 900)])]
        self.stub_ccusage(offline, offline)
        t = self.make_session(SID)
        c = session_cost(SID, t)
        self.assertEqual(c.status, "partial")
        self.assertEqual(c.unpriced, ["claude-fable-5-1"])
        self.assertEqual(c.line(), "est ≥ $0.05 · no price for fable-5-1 · npm i -g ccusage@latest")
        self.assertEqual(len(self.calls()), 2)
        session_cost(SID, t)
        self.assertEqual(len(self.calls()), 2)              # no online retry for a while
        self.assertEqual(session_cost(SID, t, allow_online=False).status, "partial")

    def test_online_failure_keeps_offline_estimate(self):
        offline = [row(SID, 0.0, 1000, [("claude-fable-5-1", 0.0, 1000)])]
        self.stub_ccusage(offline, [], online_exit=1)
        t = self.make_session(SID)
        c = session_cost(SID, t)
        self.assertEqual((c.status, c.source), ("partial", "offline"))
        self.assertEqual(c.line(), "no price for fable-5-1 · npm i -g ccusage@latest")

    def test_online_timeout(self):
        offline = [row(SID, 0.0, 1000, [("claude-fable-5-1", 0.0, 1000)])]
        self.stub_ccusage(offline, offline, online_sleep=3)
        c = session_cost(SID, self.make_session(SID), online_timeout=0.3)
        self.assertEqual(c.status, "partial")

    def test_unknown_and_error_and_no_breakdown(self):
        self.stub_ccusage([row("other", 1, 1, [("m", 1, 1)])])
        self.assertEqual(session_cost(SID, None).status, "unknown")
        self.stub("ccusage", "#!/bin/sh\necho garbage\n")
        self.assertEqual(session_cost(SID, None).status, "error")
        r = {"period": SID, "totalCost": 0, "totalTokens": 500, "modelsUsed": ["claude-x"]}
        self.stub_ccusage([r], [r])
        c = session_cost(SID, None)
        self.assertEqual((c.status, c.unpriced), ("partial", ["claude-x"]))


class DoctorTests(CostTests):
    def test_doctor_covers_all_sessions(self):
        from claude_pins.cost import doctor_line
        self.stub_ccusage([row("a", 1, 10, [("claude-opus-5", 1, 10)]), row("b", 0, 10, [("claude-fable-5-1", 0, 10)]),
                           row("c", 0, 10, [("claude-fable-5-1", 0, 10)])],
                          [row("a", 1, 10, [("claude-opus-5", 1, 10)]), row("b", 2, 10, [("claude-fable-5-1", 2, 10)]),
                           row("c", 2, 10, [("claude-fable-5-1", 2, 10)])])
        line = doctor_line()
        self.assertIn("offline table has no price for fable-5-1 (2 sessions); the online fallback prices them", line)
        self.stub_ccusage([row("a", 1, 10, [("claude-opus-5", 1, 10)])])
        self.assertIn("covers all 1 models", doctor_line())
        self.stub_ccusage([row("b", 0, 10, [("claude-fable-5-1", 0, 10)])], [row("b", 0, 10, [("claude-fable-5-1", 0, 10)])])
        self.assertIn("no price for fable-5-1 even online", doctor_line())

    def test_doctor_edge_outcomes(self):
        from claude_pins.cost import doctor_line
        self.stub_ccusage([])
        self.assertIn("no priced sessions yet", doctor_line())
        self.stub_ccusage([row("b", 0, 10, [("claude-fable-5-1", 0, 10)])], [], online_exit=1)
        self.assertIn("online fallback unreachable", doctor_line())
        self.stub("ccusage", "#!/bin/sh\n[ \"$1\" = --version ] && { echo 'ccusage 1'; exit 0; }\necho nope\n")
        self.assertIn("offline listing failed", doctor_line())
        os.environ["PATH"] = str(self.bindir); (self.bindir / "ccusage").unlink()
        self.assertIn("not installed", doctor_line())
        self.assertEqual(Cost("unknown").line(), "not indexed by ccusage yet")
        self.assertEqual(Cost("error").line(), "unavailable (ccusage error)")

    def test_listing_shape_variants(self):
        import json as _json
        (self.root / "offline.json").write_text(_json.dumps({"sessions": [row(SID, 1, 10, [("m", 1, 10)])]}))
        self.stub("ccusage", f"#!/bin/sh\ncat {self.root}/offline.json\n")
        self.assertEqual(session_cost(SID, None).status, "ok")
        os.environ["CLAUDE_PINS_CCUSAGE"] = str(self.root / "missing")
        self.assertEqual(session_cost(SID, None).status, "missing")

    def test_preview_streams_before_cost(self):
        import io
        from claude_pins.render import stream_preview, View
        from claude_pins.model import Pin
        from claude_pins.sessions import Expiry
        from claude_pins.transcript import Summary
        seen = []
        buf = io.StringIO()
        orig = buf.flush
        buf.flush = lambda: seen.append(buf.getvalue())
        view = View(Pin(alias="a", session_id=SID, title="T", cwd="/x"), Expiry("ok", 100, 29), summary=Summary(exists=True, ai_title="T", model="claude-fable-5-1", prompts=1, last_prompt="hi"))
        stream_preview(view, lambda: Cost("ok", 1.5, 10), None, width=80, out=buf)
        self.assertIn("model", seen[0]); self.assertNotIn("cost", seen[0])     # flushed before the lookup
        self.assertIn("cost       est $1.50 (ccusage)\ntranscript", buf.getvalue())
        self.assertTrue(buf.getvalue().rstrip().endswith("you        hi"))


class MatchTests(Sandbox):
    def test_loose_match(self):
        pins = [Pin(alias="standup-prep", session_id="1", title="Standup prep"),
                Pin(alias="cc-collector", session_id="2", title="Command center collector"),
                Pin(alias="prep-2", session_id="3", title="Tax prep")]
        self.assertEqual([p.alias for p in loose_match(pins, ["standup-prep"])], ["standup-prep"])
        self.assertEqual([p.alias for p in loose_match(pins, ["PREP"])], ["standup-prep", "prep-2"])
        self.assertEqual([p.alias for p in loose_match(pins, ["center", "coll"])], ["cc-collector"])
        self.assertEqual(loose_match(pins, ["zzz"]), [])
        self.assertEqual(len(loose_match(pins, [])), 3)

    def test_match_sessions(self):
        from claude_pins.match import match_sessions, recent_sessions
        from claude_pins.transcript import Summary
        a = Summary(session_id="11111111-1111-1111-1111-111111111111", ai_title="Standup prep", cwd="/home/u/git/proj")
        b = Summary(session_id="11111111-2222-2222-2222-222222222222", ai_title="Tax prep", cwd="/home/u/Documents")
        c = Summary(session_id="33333333-3333-3333-3333-333333333333", custom_title="Mower", cwd="/home/u/git/rc")
        ss = [a, b, c]
        self.assertEqual(match_sessions(a.session_id.upper(), ss), [a])          # exact id, any case
        self.assertEqual(match_sessions("33333333", ss), [c])                    # unique prefix
        self.assertEqual(match_sessions("11111111", ss), [a, b])                 # ambiguous prefix: both
        self.assertEqual(match_sessions("11111111-2222", ss), [b])
        self.assertEqual(match_sessions("prep", ss), [a, b])                     # title words
        self.assertEqual(match_sessions("PREP documents", ss), [b])              # …and directory, any case
        self.assertEqual(match_sessions("git", ss), [a, c])
        self.assertEqual(match_sessions("   ", ss), [])
        self.assertEqual(match_sessions("99999999", ss), [])                     # prefix with no hit, no title either
        self.assertEqual(match_sessions("deadbeef-cafe", ss), [])
        # recent_sessions skips transcripts without a session id (nothing to pin)
        self.make_session("44444444-4444-4444-4444-444444444444", title="Real")
        (self.projects / "x").mkdir(); (self.projects / "x" / "55555555-5555-5555-5555-555555555555.jsonl").write_text("{}\n")
        self.assertEqual([s.title for s in recent_sessions()], ["Real"])


class PromptTests(Sandbox):
    def test_plain_text_without_a_prefilling_readline(self):
        """libedit (macOS) takes the hook but inserts nothing, so the default goes in the label and bare
        enter keeps it; GNU readline pre-fills and an empty answer means empty."""
        from unittest import mock
        from claude_pins import prompt

        class Editline:
            __doc__ = "Importing this module enables command line editing using libedit readline."

            def set_completer_delims(self, d): pass
            def set_completer(self, c): self.completer = c
            def parse_and_bind(self, s): pass

        self.assertFalse(prompt.prefills(Editline()))
        self.assertTrue(prompt.prefills(type("Gnu", (), {"__doc__": "GNU readline", "backend": "readline"})()))
        self.assertFalse(prompt.prefills(type("Ed", (), {"backend": "editline"})()))
        os.environ["CLAUDE_PINS_NO_FZF"] = "1"
        answers, asked, shown = iter(["", "c", "  new  ", "", "c"]), [], []
        with mock.patch.object(prompt, "_readline", lambda: Editline()), \
                mock.patch("builtins.input", lambda text: asked.append(text) or next(answers)), \
                mock.patch("builtins.print", lambda *a, **k: shown.append(" ".join(map(str, a)))):
            self.assertEqual(prompt.text("title", "Standup prep"), "Standup prep")   # enter keeps
            self.assertEqual(prompt.text("note", "a note"), "")                      # c clears
            self.assertEqual(prompt.text("note", "a note"), "new")                   # anything else replaces
            self.assertEqual(prompt.text("note", ""), "")                            # nothing to keep: no menu line
            self.assertEqual(prompt.directory("~/git"), "")
        self.assertEqual(asked, [" title: ", " note: ", " note: ", " note: ", " directory (tab completes): "])
        self.assertEqual(shown, [' title: enter keeps "Standup prep", c clears, or type a new value',
                                 ' note: enter keeps "a note", c clears, or type a new value',
                                 ' note: enter keeps "a note", c clears, or type a new value',
                                 ' directory (tab completes): enter keeps "~/git", c clears, or type a new value'])

    def test_directory_completions(self):
        from claude_pins.prompt import directory_completions
        (self.home / "git" / "alpha").mkdir(parents=True); (self.home / "git" / "alps").mkdir()
        (self.home / "git" / "alpha.txt").write_text("")  # files never complete
        self.assertEqual(directory_completions("~/git/al"), ["~/git/alpha/", "~/git/alps/"])
        self.assertEqual(directory_completions(str(self.home / "git" / "alph")), [str(self.home / "git" / "alpha") + "/"])
        self.assertEqual(directory_completions("/no/such/dir/x"), [])
        cwd = os.getcwd()
        os.chdir(self.home / "git")
        try:
            self.assertEqual(directory_completions("alp"), ["alpha/", "alps/"])
        finally:
            os.chdir(cwd)


class CostEdgeTests(CostTests):
    def test_cache_and_binary_edge_cases(self):
        from claude_pins.cost import _cache_file, ccusage_bin, session_cost
        self.stub_ccusage([row(SID, 0, 10, [("claude-x", 0, 10)])], [row(SID, 0, 10, [("claude-x", 0, 10)])])
        p = self.make_session(SID)
        c = session_cost(SID, p)
        self.assertEqual(c.status, "partial")
        # a partial answer with allow_online=False is served from the cache without a retry
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(session_cost(SID, p, allow_online=False).status, "partial")
        self.assertEqual(len(self.calls()), 2)
        # garbage in the cache file is ignored
        _cache_file(SID).write_text("[")
        self.assertEqual(session_cost(SID, p).status, "partial")
        # a cache directory that cannot be created is not fatal
        os.environ["XDG_CACHE_HOME"] = str(self.root / "file"); (self.root / "file").write_text("")
        self.assertEqual(session_cost(SID, p).status, "partial")
        os.environ.pop("XDG_CACHE_HOME")
        # a missing transcript still prices (mtime 0); CLAUDE_PINS_CCUSAGE can name a PATH command
        self.assertEqual(session_cost(SID, self.root / "gone").status, "partial")
        os.environ["CLAUDE_PINS_CCUSAGE"] = "ccusage"
        self.assertEqual(ccusage_bin(), "ccusage")
        # exit codes and timeouts on the offline run
        self.stub("ccusage", "#!/bin/sh\nexit 3\n")
        os.environ.pop("CLAUDE_PINS_CCUSAGE")
        self.assertEqual(session_cost(SID, p).status, "error")
        self.stub("ccusage", "#!/bin/sh\nsleep 2\n")
        self.assertEqual(session_cost(SID, p, timeout=0.2).status, "error")
        self.stub("ccusage", "#!/bin/sh\necho '{\"session\": 5}'\n")
        self.assertEqual(session_cost(SID, p).status, "error")
        self.stub("ccusage", "#!/bin/sh\necho '{\"session\": [1, {\"period\": \"x\"}]}'\n")
        self.assertEqual(session_cost(SID, p).status, "unknown")
        self.stub("ccusage", "#!/bin/sh\n[ \"$1\" = --version ] && exit 0\necho '{\"session\": []}'\n")
        from claude_pins.cost import doctor_line
        self.assertIn("ccusage ?:", doctor_line())
        self.stub("ccusage", "#!/bin/sh\nsleep 2\n")
        self.assertIn("not runnable", doctor_line(timeout=0.2))

    def test_cost_line_wording(self):
        self.assertEqual(Cost("partial", 0, 10, "offline", ["claude-x"]).line(), "no price for x · npm i -g ccusage@latest")
        self.assertEqual(Cost("partial", 1.25, 10, "online", []).line("fable-5-1"), "est ≥ $1.25 · no price for fable-5-1 · npm i -g ccusage@latest")
        self.assertEqual(Cost("partial", 0, 10).line(), "no price for this model · npm i -g ccusage@latest")
        self.assertEqual(Cost("ok", 2, 10, "online").line(), "est $2.00 (ccusage online)")
        self.assertEqual(Cost("missing").line(), "install ccusage for session cost · npm i -g ccusage@latest")

    def test_coverage_skips_junk_rows(self):
        from claude_pins.cost import _coverage, unpriced_models
        used, unpriced = _coverage([1, {"modelBreakdowns": [2, {"modelName": ""}, {"modelName": "m", "inputTokens": 0}]}])
        self.assertEqual((used, unpriced), ({}, {}))
        self.assertEqual(unpriced_models({"modelBreakdowns": [3, {"modelName": "m", "inputTokens": 5, "cost": 0}]}), ["m"])

    def test_cost_cache_replace_failure_cleans_up(self):
        from claude_pins.cost import _cache_file, session_cost
        self.stub_ccusage([row(SID, 1, 10, [("m", 1, 10)])])
        _cache_file(SID).mkdir(parents=True)
        self.assertEqual(session_cost(SID, None).status, "ok")
        self.assertEqual([f.name for f in _cache_file(SID).parent.iterdir()], [_cache_file(SID).name])
