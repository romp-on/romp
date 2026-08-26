"""_patch_rows: Claude Code's structuredPatch (toolUseResult) → numbered diff rows with REAL file line numbers
+ context, used for the chat's Edit/MultiEdit diff gutter (the user 2026-06-29). Unit tests on the row
builder, plus END-TO-END through build_session — the whole parse path must deliver the record's
toolUseResult to the attach loop, which it silently failed to do from birth to 2026-08-20 (the event model
dropped the top-level field, so this consumer was dead while every unit test here stayed green).
SYNTHETIC fixtures only: placeholder UUIDs, invented file paths and diffs."""
import json
import os
import unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()
jd = km.jd


class PatchRows(unittest.TestCase):
    def test_real_line_numbers_with_context_and_a_hunk_header(self):
        sp = [{
            "oldStart": 10, "oldLines": 4, "newStart": 10, "newLines": 4,
            "lines": [" ctx_before", "-removed_a", "+added_a", " ctx_after"],
        }]
        rows = km._patch_rows(sp)
        # leading @@ header (no numbers), then the lines with real old/new numbers
        self.assertEqual(rows[0], {"sign": "@", "text": "@@ -10 +10 @@", "oldNo": None, "newNo": None})
        self.assertEqual(rows[1], {"sign": " ", "text": "ctx_before", "oldNo": 10, "newNo": 10})
        self.assertEqual(rows[2], {"sign": "-", "text": "removed_a", "oldNo": 11, "newNo": None})
        self.assertEqual(rows[3], {"sign": "+", "text": "added_a", "oldNo": None, "newNo": 11})
        self.assertEqual(rows[4], {"sign": " ", "text": "ctx_after", "oldNo": 12, "newNo": 12})

    def test_multiple_hunks_each_get_their_own_header_and_numbering(self):
        sp = [
            {"oldStart": 1, "newStart": 1, "lines": ["+first"]},
            {"oldStart": 50, "newStart": 51, "lines": ["-gone"]},
        ]
        rows = km._patch_rows(sp)
        heads = [r for r in rows if r["sign"] == "@"]
        self.assertEqual(len(heads), 2)
        self.assertEqual(heads[1]["text"], "@@ -50 +51 @@")
        # the second hunk's removed line numbers from its own oldStart
        gone = [r for r in rows if r["text"] == "gone"][0]
        self.assertEqual(gone["oldNo"], 50)
        self.assertIsNone(gone["newNo"])

    def test_empty_or_malformed_patch_is_safe(self):
        self.assertEqual(km._patch_rows([]), [])
        self.assertEqual(km._patch_rows(None), [])
        self.assertEqual(km._patch_rows([{"lines": ["+x"]}]), [])   # no oldStart/newStart → skipped

    def test_payload_is_capped(self):
        big = [{"oldStart": 1, "newStart": 1, "lines": ["+l%d" % i for i in range(2000)]}]
        rows = km._patch_rows(big)
        self.assertLessEqual(len(rows), 601)


NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600
FILE = "/tmp/notes-api/app.py"


class BuildSessionDiffRows(unittest.TestCase):
    """END-TO-END: an Edit tool_use + its tool_result record carrying toolUseResult.structuredPatch →
    the built event carries diffRows. This pins the whole chain — the event model must CARRY the
    record's top-level toolUseResult onto the atom, and the attach loop's filePath match must feed the
    right result to the right tool — not just the row shaping the unit tests above cover. Fixture
    mirrors test_kernel_askanswer's discover setup (names + transcript in the munged projects dir)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        self.tpath = pdir / (SID + ".jsonl")
        names = td / "names"; names.mkdir()
        (names / SID).write_text("testsess\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES,
                      km._tmux_sessions, km._read_task_store, km._GLOBAL_CLAUDE_MD)
        jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE = names, proj, td / "goals", td
        km.NAMES = names
        km._GLOBAL_CLAUDE_MD = td / "no-global.md"           # keep a real ~/.claude/CLAUDE.md out of the fixture
        km._read_task_store = lambda fsid, fold=None: []                # no to-do card in the way
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 100, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        jd.GOALDIR.mkdir(parents=True)
        km._parse_cache.clear()

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES,
         km._tmux_sessions, km._read_task_store, km._GLOBAL_CLAUDE_MD) = self.saved
        km._parse_cache.clear()
        self.td.cleanup()

    def _iso(self, t):
        return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def _write(self, tool_use_result):
        """A minimal Edit exchange: prompt → Edit tool_use → its tool_result user record (optionally
        carrying the top-level structured toolUseResult) → a closing reply."""
        result_rec = {"type": "user", "timestamp": self._iso(T0 + 20), "uuid": "u2", "parentUuid": "a1",
                      "message": {"role": "user", "content": [
                          {"type": "tool_result", "tool_use_id": "toolu_ed1", "content": "Applied 1 edit"}]}}
        if tool_use_result is not None:
            result_rec["toolUseResult"] = tool_use_result
        recs = [
            {"type": "user", "timestamp": self._iso(T0), "uuid": "u1", "parentUuid": None,
             "promptSource": "typed", "message": {"role": "user", "content": "kick things off"}},
            {"type": "assistant", "timestamp": self._iso(T0 + 10), "uuid": "a1", "parentUuid": "u1",
             "message": {"role": "assistant", "stop_reason": "tool_use",
                         "content": [{"type": "tool_use", "id": "toolu_ed1", "name": "Edit",
                                      "input": {"file_path": FILE, "old_string": "old_line",
                                                "new_string": "new_line"}}]}},
            result_rec,
            {"type": "assistant", "timestamp": self._iso(T0 + 30), "uuid": "a2", "parentUuid": "u2",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}],
                         "stop_reason": "end_turn"}},
        ]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")

    def _edit_event(self):
        events = km.build_session(SID, NOW)["events"]
        edits = [e for e in events if e.get("kind") == "tool" and e.get("name") == "Edit"]
        self.assertEqual(len(edits), 1)
        return edits[0]

    PATCH = [{"oldStart": 3, "oldLines": 1, "newStart": 3, "newLines": 1,
              "lines": ["-old_line", "+new_line"]}]

    def test_structured_patch_arrives_as_diffrows(self):
        self._write({"filePath": FILE, "structuredPatch": self.PATCH})
        ev = self._edit_event()
        self.assertEqual(ev.get("diffRows"), [
            {"sign": "@", "text": "@@ -3 +3 @@", "oldNo": None, "newNo": None},
            {"sign": "-", "text": "old_line", "oldNo": 3, "newNo": None},
            {"sign": "+", "text": "new_line", "oldNo": None, "newNo": 3},
        ], "the record's structuredPatch must reach the event as REAL-line-number rows")

    def test_mismatched_filepath_attaches_no_diffrows(self):
        # The attach guard: a result whose filePath names a DIFFERENT file must not feed this tool —
        # the client falls back to numberDiff's relative gutter instead of showing wrong line numbers.
        self._write({"filePath": "/tmp/notes-api/other.py", "structuredPatch": self.PATCH})
        self.assertNotIn("diffRows", self._edit_event())

    def test_no_structured_patch_attaches_no_diffrows(self):
        # A dict toolUseResult WITHOUT a structuredPatch (some tool results carry other shapes).
        self._write({"filePath": FILE})
        self.assertNotIn("diffRows", self._edit_event())

    def test_no_tooluseresult_attaches_no_diffrows(self):
        # An old record with no top-level toolUseResult at all — the pre-2026-08-20 world.
        self._write(None)
        self.assertNotIn("diffRows", self._edit_event())


if __name__ == "__main__":
    unittest.main()
