"""Naming a session after its pin: the record, the sidecar, and the guard on every rename."""

import json
import stat

from claude_pins import naming
from claude_pins.model import Pin
from claude_pins.transcript import read_summary
from tests.helpers import Sandbox

SID = "11111111-1111-1111-1111-111111111111"


class NamingTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.t = self.make_session(SID, title="Standup prep", age_days=2)
        self.pin = Pin(alias="standup", session_id=SID, title="Standup prep", transcript=str(self.t))

    def last_line(self) -> str:
        return self.t.read_bytes().decode("utf-8").splitlines()[-1]

    def test_claim_appends_the_record_claude_writes(self):
        before = self.t.stat()
        self.assertEqual(naming.claim(self.pin), "session named 📌 standup")
        # byte for byte the compact form /rename writes: Claude's line matcher wants no space after the colons
        self.assertEqual(self.last_line(), '{"type":"custom-title","customTitle":"📌 standup","sessionId":"%s"}' % SID)
        self.assertTrue(self.t.read_bytes().endswith(b"\n"))
        self.assertEqual(self.pin.prior_title, "")
        self.assertEqual(read_summary(self.t).custom_title, "📌 standup")
        self.assertEqual(read_summary(self.t).title, "📌 standup")
        # a rename is not activity: the idle time, the sort and the retention sweep keep the old mtime
        self.assertEqual(self.t.stat().st_mtime_ns, before.st_mtime_ns)

    def test_claim_keeps_the_prior_name(self):
        t = self.make_session("22222222-2222-2222-2222-222222222222", custom="Mine", age_days=1)
        pin = Pin(alias="mine", session_id="22222222-2222-2222-2222-222222222222", transcript=str(t))
        naming.claim(pin)
        self.assertEqual(pin.prior_title, "Mine")
        self.assertEqual(read_summary(t).custom_title, "📌 mine")

    def test_rename_restore_and_reclaim_follow_the_guard(self):
        naming.claim(self.pin)
        self.pin.alias = "sp"
        self.assertEqual(naming.rename(self.pin, "standup"), "session named 📌 sp")
        self.assertEqual(read_summary(self.t).custom_title, "📌 sp")
        # the user renamed the session inside Claude: pins leave it alone from then on
        naming.set_title(self.t, SID, "Their own name")
        self.assertEqual(naming.rename(self.pin, "sp"), "")
        self.assertEqual(naming.restore(self.pin), "")
        self.assertEqual(read_summary(self.t).custom_title, "Their own name")
        # back under the pin's name, an unpin clears (no prior name) and an undo names it again
        naming.set_title(self.t, SID, "📌 sp")
        self.assertEqual(naming.restore(self.pin), "session name cleared")
        self.assertEqual(self.last_line(), '{"type":"custom-title","customTitle":"","sessionId":"%s"}' % SID)
        self.assertEqual(read_summary(self.t).custom_title, "")
        self.assertEqual(read_summary(self.t).title, "Standup prep")
        self.assertEqual(naming.reclaim(self.pin), "session named 📌 sp")
        # an undo when the session was named meanwhile does nothing
        naming.set_title(self.t, SID, "")
        naming.set_title(self.t, SID, "Named again")
        self.assertEqual(naming.reclaim(self.pin), "")
        self.assertEqual(read_summary(self.t).custom_title, "Named again")

    def test_restore_puts_the_prior_name_back(self):
        t = self.make_session("22222222-2222-2222-2222-222222222222", custom="Mine", age_days=1)
        pin = Pin(alias="mine", session_id="22222222-2222-2222-2222-222222222222", transcript=str(t))
        naming.claim(pin)
        self.assertEqual(naming.restore(pin), 'session named "Mine" again')
        self.assertEqual(read_summary(t).custom_title, "Mine")
        self.assertEqual(naming.reclaim(pin), "session named 📌 mine")      # still the prior name: undo renames
        naming.set_title(t, "22222222-2222-2222-2222-222222222222", "Mine")
        naming.set_title(t, "22222222-2222-2222-2222-222222222222", "Other")
        self.assertEqual(naming.reclaim(pin), "")

    def test_sidecar_is_rewritten_deleted_and_never_created(self):
        sidecar = self.t.parent / SID / "custom-title.json"
        naming.claim(self.pin)
        self.assertFalse(sidecar.exists())
        sidecar.parent.mkdir()
        sidecar.write_text('{"customTitle":"old"}')
        self.pin.alias = "sp"
        naming.rename(self.pin, "standup")
        self.assertEqual(json.loads(sidecar.read_text()), {"customTitle": "📌 sp"})
        naming.restore(self.pin)
        self.assertFalse(sidecar.exists())
        self.assertTrue(sidecar.parent.is_dir())

    def test_missing_or_unwritable_transcript_is_skipped(self):
        gone = Pin(alias="gone", session_id="33333333-3333-3333-3333-333333333333", transcript=str(self.root / "no.jsonl"))
        for f in (naming.claim, naming.restore, naming.reclaim):
            self.assertEqual(f(gone), "")
        self.assertEqual(naming.rename(gone, "x"), "")
        self.assertEqual(gone.prior_title, "")
        self.t.chmod(stat.S_IRUSR)
        try:
            self.assertEqual(naming.claim(self.pin), "")
            self.assertEqual(read_summary(self.t).custom_title, "")
        finally:
            self.t.chmod(stat.S_IRUSR | stat.S_IWUSR)
        self.assertFalse(naming.set_title(self.root / "no.jsonl", SID, "x"))
        self.assertFalse((self.root / "no.jsonl").exists())

    def test_empty_transcript_and_unwritable_sidecar(self):
        empty = self.projects / "e" / f"{SID}.jsonl"
        empty.parent.mkdir(); empty.write_text("")
        self.assertTrue(naming.set_title(empty, SID, "📌 x"))
        self.assertEqual(empty.read_text().count("\n"), 1)
        self.assertEqual(read_summary(empty).custom_title, "📌 x")
        sidecar = self.t.parent / SID / "custom-title.json"
        sidecar.parent.mkdir(); sidecar.write_text('{"customTitle":"old"}')
        sidecar.parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            self.assertTrue(naming.set_title(self.t, SID, ""))            # the record still lands
        finally:
            sidecar.parent.chmod(stat.S_IRWXU)
        self.assertEqual(sidecar.read_text(), '{"customTitle":"old"}')
        naming.claim(self.pin)
        self.t.chmod(stat.S_IRUSR)
        try:
            self.assertEqual(naming.restore(self.pin), "")
        finally:
            self.t.chmod(stat.S_IRUSR | stat.S_IWUSR)
        self.assertEqual(read_summary(self.t).custom_title, "📌 standup")

    def test_a_cut_last_line_is_not_glued_onto(self):
        with open(self.t, "ab") as fh:
            fh.write(b'{"type":"user","message":{"role":"user","content":"half')
        self.assertTrue(naming.set_title(self.t, SID, "📌 standup"))
        lines = self.t.read_bytes().split(b"\n")
        self.assertTrue(lines[-3].endswith(b'"half'))
        self.assertEqual(json.loads(lines[-2])["customTitle"], "📌 standup")
        self.assertEqual(read_summary(self.t).custom_title, "📌 standup")

    def test_pin_name_and_transcript_found_without_the_hint(self):
        self.assertEqual(naming.pin_name("rc-mower"), "📌 rc-mower")
        pin = Pin(alias="standup", session_id=SID, transcript=str(self.root / "moved.jsonl"))
        self.assertEqual(naming.claim(pin), "session named 📌 standup")
        self.assertEqual(naming.current_title(self.t), "📌 standup")
        self.assertIsNone(naming.current_title(self.root / "no.jsonl"))
