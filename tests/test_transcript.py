import json
import os

from claude_pins import transcript
from claude_pins.transcript import read_summary, touch
from tests.helpers import Sandbox, session_records, write_jsonl


class TranscriptTests(Sandbox):
    def test_basic_fields(self):
        p = self.make_session("11111111-1111-1111-1111-111111111111", title="Standup prep",
                              prompt="so the pin command should also…", answer="Yes. The picker can bind keys",
                              n_turns=3)
        s = read_summary(p, use_cache=False)
        self.assertEqual(s.title, "Standup prep")
        self.assertEqual(s.session_id, "11111111-1111-1111-1111-111111111111")
        self.assertEqual(s.model, "claude-fable-5-1")
        self.assertEqual(s.effort, "high")
        self.assertEqual(s.permission_mode, "auto")
        self.assertEqual(s.context_tokens, 121002)
        self.assertEqual(s.context_pct, 61)
        self.assertEqual(s.last_prompt, "so the pin command should also…")
        self.assertEqual(s.last_answer, "Yes. The picker can bind keys")
        self.assertEqual(s.first_prompt, "prompt 0")
        self.assertEqual(s.prompts, 3)
        self.assertEqual(s.replies, 3)
        self.assertEqual(s.messages, 6)
        self.assertEqual(s.git_branch, "main")
        self.assertTrue(s.cwd.endswith("git/proj"))
        self.assertEqual(s.created, "2026-09-01T10:00:00.000Z")

    def test_custom_title_wins_and_first_prompt_fallback(self):
        p = self.make_session("11111111-1111-1111-1111-111111111111", title="AI", custom="Mine")
        self.assertEqual(read_summary(p, use_cache=False).title, "Mine")
        sid = "22222222-2222-2222-2222-222222222222"
        recs = [r for r in session_records(sid, "/x", prompt="first words of a prompt") if r["type"] not in ("ai-title",)]
        p = write_jsonl(self.projects / "x" / f"{sid}.jsonl", recs)
        self.assertEqual(read_summary(p, use_cache=False).title, "first words of a prompt")

    def test_missing_and_empty_and_malformed(self):
        s = read_summary(self.projects / "nope.jsonl", use_cache=False)
        self.assertFalse(s.exists)
        self.assertEqual(s.title, "untitled")
        empty = self.projects / "e" / "e.jsonl"
        empty.parent.mkdir(); empty.write_text("")
        s = read_summary(empty, use_cache=False)
        self.assertTrue(s.exists); self.assertEqual(s.messages, 0)
        bad = self.projects / "e" / "bad.jsonl"
        bad.write_text('{"type":"ai-title","aiTitle":"ok"}\nnot json at all\n{"broken\n{"type":"last-prompt","lastPrompt":"lp"}\n')
        s = read_summary(bad, use_cache=False)
        self.assertEqual(s.ai_title, "ok"); self.assertEqual(s.last_prompt, "lp")

    def test_truncated_tail(self):
        p = self.make_session("11111111-1111-1111-1111-111111111111")
        with open(p, "a") as fh:
            fh.write('{"type":"assistant","message":{"content":[{"type":"text","text":"half wri')
        s = read_summary(p, use_cache=False)
        self.assertTrue(s.truncated_tail)
        self.assertEqual(s.last_answer, "hi back")  # partial line ignored

    def test_huge_file_reads_head_and_tail_only(self):
        sid = "33333333-3333-3333-3333-333333333333"
        recs = session_records(sid, "/big", n_turns=1, title="Big")
        # Pad the middle with 3 MB of records; the custom title sits in the middle.
        filler = {"type": "user", "cwd": "/big", "sessionId": sid, "message": {"role": "user", "content": "x" * 2000}}
        middle = [filler] * 1500
        middle.insert(700, {"type": "custom-title", "customTitle": "Renamed midway", "sessionId": sid})
        p = write_jsonl(self.projects / "big" / f"{sid}.jsonl", recs[:-3] + middle + recs[-3:])
        self.assertGreater(p.stat().st_size, 2 * transcript.WINDOW)
        s = read_summary(p, use_cache=False)
        self.assertEqual(s.title, "Renamed midway")
        self.assertEqual(s.prompts, 1501)
        self.assertEqual(s.last_answer, "hi back")
        self.assertEqual(s.model, "claude-fable-5-1")

    def test_cache_by_mtime(self):
        p = self.make_session("11111111-1111-1111-1111-111111111111", title="v1")
        self.assertEqual(read_summary(p).title, "v1")
        cache = self.home / ".cache" / "claude-pins" / "transcripts" / "11111111-1111-1111-1111-111111111111.json"
        self.assertTrue(cache.exists())
        # Same size+mtime → cached answer even if content changed
        data = json.loads(cache.read_text()); data["summary"]["ai_title"] = "cached"; cache.write_text(json.dumps(data))
        self.assertEqual(read_summary(p).title, "cached")
        touch(p)
        self.assertEqual(read_summary(p).title, "v1")

    def test_touch(self):
        p = self.make_session("11111111-1111-1111-1111-111111111111", age_days=20)
        before = p.stat().st_mtime
        self.assertTrue(touch(p))
        self.assertGreater(p.stat().st_mtime, before + 86400 * 19)
        self.assertFalse(touch(self.projects / "gone.jsonl"))
