import os

from claude_pins.cost import Cost, session_cost
from claude_pins.match import loose_match
from claude_pins.model import Pin
from tests.helpers import Sandbox


class CostTests(Sandbox):
    def stub_ccusage(self, payload: str, code: int = 0):
        self.stub("ccusage", f"#!/bin/sh\nif [ \"$1\" = --version ]; then echo 'ccusage 20.0.18'; exit 0; fi\n"
                             f"echo \"$@\" >> {self.root}/ccusage.log\nprintf '%s' '{payload}'\nexit {code}\n")

    def test_not_installed(self):
        os.environ["PATH"] = str(self.bindir)
        c = session_cost("s", None)
        self.assertEqual(c.status, "missing")
        self.assertIn("npm i -g ccusage", c.line())

    def test_ok_and_cached(self):
        self.stub_ccusage('{"sessionId":"s","totalCost":0.0712,"totalTokens":4800000}')
        t = self.make_session("11111111-1111-1111-1111-111111111111")
        c = session_cost("11111111-1111-1111-1111-111111111111", t)
        self.assertEqual((c.status, c.total_tokens), ("ok", 4800000))
        self.assertEqual(c.line(), "est $0.07 (ccusage)")
        session_cost("11111111-1111-1111-1111-111111111111", t)
        self.assertEqual(len((self.root / "ccusage.log").read_text().splitlines()), 1)  # cached by mtime
        self.assertIn("--offline", (self.root / "ccusage.log").read_text())
        os.utime(t, None)
        session_cost("11111111-1111-1111-1111-111111111111", t)
        self.assertEqual(len((self.root / "ccusage.log").read_text().splitlines()), 2)

    def test_zero_and_unknown(self):
        self.stub_ccusage('{"sessionId":"s","totalCost":0,"totalTokens":100}')
        c = session_cost("s", None)
        self.assertEqual(c.status, "zero")
        self.assertEqual(c.line("fable-5-1", 10), "pricing unavailable for fable-5-1 · update ccusage")
        self.assertEqual(c.line("fable-5-1", 0), "est $0.00 (ccusage)")
        self.stub_ccusage("null")
        self.assertEqual(session_cost("t", None).status, "unknown")
        self.stub_ccusage("garbage", 1)
        self.assertEqual(session_cost("u", None).status, "error")


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
