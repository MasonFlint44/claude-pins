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

    def test_last_title_record_wins_even_when_empty(self):
        """/rename with nothing clears the name; a clear in the middle of a big file must not lose to an
        older name the head/tail pass saw, and a later name in the tail beats a clear in the middle."""
        sid = "44444444-4444-4444-4444-444444444444"
        recs = session_records(sid, "/x", title="AI", custom="Head name")
        recs.append({"type": "custom-title", "customTitle": "", "sessionId": sid})
        p = write_jsonl(self.projects / "x" / f"{sid}.jsonl", recs)
        s = read_summary(p, use_cache=False)
        self.assertEqual((s.custom_title, s.title), ("", "AI"))
        filler = {"type": "user", "cwd": "/x", "sessionId": sid, "message": {"role": "user", "content": "x" * 2000}}
        middle = [filler] * 100
        middle.insert(50, {"type": "custom-title", "customTitle": "", "sessionId": sid})
        big = write_jsonl(self.projects / "x" / "big.jsonl", recs[:-1] + middle + recs[-1:])
        self.assertGreater(big.stat().st_size, 2 * transcript.WINDOW)
        s = read_summary(big, use_cache=False)
        self.assertEqual((s.custom_title, s.title), ("", "AI"))
        with open(big, "a") as fh:
            fh.write(json.dumps({"type": "custom-title", "customTitle": "Tail name", "sessionId": sid}) + "\n")
        self.assertEqual(read_summary(big, use_cache=False).title, "Tail name")

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

    def test_cache_write_is_atomic_and_leaves_no_temp_files(self):
        p = self.make_session("11111111-1111-1111-1111-111111111111")
        s = read_summary(p)
        cache_dir = transcript._cache_path(p).parent
        self.assertEqual(sorted(f.name for f in cache_dir.iterdir()), [f"{p.stem}.json"])
        self.assertEqual(transcript._load_cache(transcript._cache_path(p), p.stat()).title, s.title)
        # two writers: neither sees the other's half-written file (distinct temp names, os.replace)
        names = []
        real = transcript.tempfile.mkstemp

        def spy(*a, **kw):
            fd, name = real(*a, **kw)
            names.append(name)
            return fd, name
        transcript.tempfile.mkstemp = spy
        try:
            transcript._save_cache(transcript._cache_path(p), s)
            transcript._save_cache(transcript._cache_path(p), s)
        finally:
            transcript.tempfile.mkstemp = real
        self.assertEqual(len(set(names)), 2)
        self.assertTrue(all(os.path.basename(n).startswith(f".{p.stem}-") for n in names))
        self.assertEqual(sorted(f.name for f in cache_dir.iterdir()), [f"{p.stem}.json"])

    def test_cache_unwritable_is_silent(self):
        p = self.make_session("11111111-1111-1111-1111-111111111111")
        os.environ["XDG_CACHE_HOME"] = str(self.root / "not-a-dir-file")
        (self.root / "not-a-dir-file").write_text("x")  # mkdir under a file fails
        self.assertEqual(read_summary(p).title, "A session")
        self.assertEqual(read_summary(p).title, "A session")  # and again, uncached

    def test_cache_ignores_other_formats_and_stale_sizes(self):
        p = self.make_session("11111111-1111-1111-1111-111111111111")
        read_summary(p)
        cf = transcript._cache_path(p)
        data = json.loads(cf.read_text())
        data["format"] = 1
        cf.write_text(json.dumps(data))
        self.assertIsNone(transcript._load_cache(cf, p.stat()))
        data["format"] = transcript.CACHE_FORMAT; data["summary"]["size"] = 1
        cf.write_text(json.dumps(data))
        self.assertIsNone(transcript._load_cache(cf, p.stat()))
        data["summary"]["size"] = p.stat().st_size; data["summary"]["bogus_field"] = 1
        cf.write_text(json.dumps(data))
        self.assertEqual(transcript._load_cache(cf, p.stat()).title, "A session")  # unknown keys dropped

    def test_context_window_from_settings_and_size(self):
        p = self.make_session("11111111-1111-1111-1111-111111111111")
        s = read_summary(p, use_cache=False)
        self.assertEqual(s.context_window, 200_000)
        self.write_settings({"model": "claude-fable-5-1[1m]"})
        self.assertEqual(s.context_window, 1_000_000)
        self.assertEqual(s.context_pct, 12)
        s.context_tokens = 400_000
        self.write_settings({})
        self.assertEqual(s.context_window, 1_000_000)  # more than 200k in context: must be a 1M session
        s.context_tokens = 0
        self.assertEqual(s.context_pct, 0)

    def test_prompt_heuristics(self):
        sid = "33333333-3333-3333-3333-333333333333"
        recs = session_records(sid, "/x", prompt="real question")
        common = {k: v for k, v in recs[2].items() if k not in ("message", "uuid", "parentUuid")}
        recs.insert(-1, {**common, "type": "user", "message": {"role": "user", "content": "<system-reminder>injected</system-reminder>"}})
        recs.insert(-1, {**common, "type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]}})
        recs.insert(-1, {**common, "type": "user", "message": {"role": "user", "content": [{"type": "image"}]}})
        recs.insert(-1, {**common, "type": "user", "message": {"role": "user", "content": 42}})
        p = write_jsonl(self.projects / "x" / f"{sid}.jsonl", recs)
        s = read_summary(p, use_cache=False)
        self.assertEqual(s.last_prompt, "real question")
        self.assertEqual(s.prompts, 4)  # the byte scan only excludes tool_result lines: an approximation

    def test_cache_replace_failure_cleans_up(self):
        p = self.make_session("11111111-1111-1111-1111-111111111111")
        cf = transcript._cache_path(p)
        cf.mkdir(parents=True)  # a directory where the cache file goes: os.replace fails after the temp write
        self.assertEqual(read_summary(p).title, "A session")
        self.assertEqual([f.name for f in cf.parent.iterdir()], [cf.name])  # no .tmp left behind
