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
        self.assertIn("context", seen[0]); self.assertNotIn("cost", seen[0])   # flushed before the lookup
        self.assertIn("cost      est $1.50 (ccusage)\ncreated", buf.getvalue())
        self.assertTrue(buf.getvalue().rstrip().endswith("you       hi"))


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
