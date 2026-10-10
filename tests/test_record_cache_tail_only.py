#!/usr/bin/env python3
"""The record cache holds a large transcript's TAIL only (2026-10-07).

The reader kept every live transcript whole, about 3 bytes of memory per file byte, so a machine whose live transcripts
were 20 GiB on disk held about 61 GiB of decoded records. A transcript (a file under the Claude projects root) whose held
span reaches _TAIL_MIN_FILE_BYTES now keeps only its newest records in memory (the last _TAIL_BYTES or the last
_TAIL_RECORDS, whichever is more); every older record stays indexed by byte offset and is read off disk when asked for,
through a read-only sequence with the whole list's length and order. Pinned here, on synthetic transcripts only:

  - a 200 MB transcript read through the whole reader holds only its tail: the cache's own weight and tracemalloc both
    say so, for what stays held and for the read's peak, and its first and last records are the file's;
  - an append extends the window and, past one and a half windows, slides it, under the same generation;
  - every index, slice, walk and reversed walk equals a plain read's; a fold from record 0, a fold whose cursor fell
    behind the window, a checkpoint write and a fresh process's restore all get every record they need;
  - nothing can see a truncated view: the C json encoder refuses the sequence, and a held view whose file was rewritten,
    replaced, or edited in place at equal length with its last bytes untouched raises rather than serving a wrong record
    (every record's CRC is checked before it is decoded, review find); a whole list growing past the threshold, by a
    small append or one large one, takes its records' CRCs only after proving them against the records it holds;
  - the record floor (the last _TAIL_RECORDS) never stretches the window past two windows' bytes;
  - other large files (the postal log's case) stay whole, and ROMP_RECORD_CACHE_TAIL_MB=0 turns the rule off;
  - the assembly's whole parse, its document and a fresh process's restore over a tail-only leaf equal the rule-off
    parse, and the chat build's scroll-back pages over it equal the whole build.

Every test asserts the entry it reads is tail-only, so each fails on a base that holds every entry whole."""
import array
import json
import os
import random
import shutil
import sys
import tempfile
import time
import tracemalloc
import unittest
from contextlib import contextmanager
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
for _k in ("ROMP_RECORD_CACHE_TAIL_MB", "ROMP_RECORD_CACHE_TAIL_RECORDS", "ROMP_RECORD_CACHE_TAIL_ROOTS"):
    os.environ.pop(_k, None)                     # the module's defaults, whatever the shell running the suite exports
km = load_source("romp_kernel_tail_only", os.path.join(BIN, "romp-kernel"))
jd, em = km.jd, km.em
sys.path.insert(0, HERE)
from test_asm_checkpoint_served import transcript    # noqa: E402  the stage 4a served fixture's builder (synthetic)

SID = "aaaaaaaa-4444-4222-8333-555555555555"
NOW = 1781200000
MIB = 1024 * 1024
WORDS = ("alpha beta gamma delta retry budget cache window tail fold cursor index offset append slide page scroll "
         "notes api web tests make build lint route handler schema migration").split()


def _tail(recs):
    return type(recs).__name__ == "_TailRecords"


@contextmanager
def knobs(tail_bytes, records=4, roots=None):
    """The rule's knobs for one test (module globals the reader reads at call time); restored after. On a base without the
    rule the attributes are set and ignored, so a test fails on its behaviour, not on a missing name."""
    names = ("_TAIL_BYTES", "_TAIL_MIN_FILE_BYTES", "_TAIL_RECORDS", "_COLD_CHUNK_BYTES")
    saved = {n: getattr(em, n, None) for n in names}
    em._TAIL_BYTES, em._TAIL_MIN_FILE_BYTES, em._TAIL_RECORDS = int(tail_bytes), 2 * int(tail_bytes), int(records)
    em._COLD_CHUNK_BYTES = max(4096, int(tail_bytes) // 2)       # several runs per streaming pass, so the run seams are crossed
    set_roots = getattr(em, "set_tail_roots", None)
    if set_roots is not None:
        set_roots(roots)
    try:
        yield
    finally:
        for n, v in saved.items():
            if v is None:
                try:
                    delattr(em, n)
                except AttributeError:
                    pass
            else:
                setattr(em, n, v)
        if set_roots is not None:
            set_roots(None)


def fresh():
    """A kernel restart's in-memory side (the record cache, the assembly cache, the checkpoints' pending restores)."""
    with em._JSONL_CACHE_LOCK:
        em._JSONL_CACHE.clear()
        em._JSONL_CACHE_BYTES[0] = 0
    with em._ASM_LOCK:
        em._ASM_CACHE.clear()
    em._TRAILING_CACHE.clear()
    with em._ASM_CKPT_LOCK:
        em._HYDRATED.clear(); em._HYDRATED_BYTES[0] = 0
    em._LAZY_FILES.clear()
    memo = getattr(em, "_ASM_DOC_MEMO", None)
    if memo is not None:
        with em._ASM_CKPT_LOCK:
            memo.clear()
            getattr(em, "_ASM_DOC_MEMO_BYTES", [0])[0] = 0
    with em._CKPT_LOCK:
        em._CKPT_PENDING.clear(); em._CKPT_SEQ.clear(); em._FOLD_DIRTY.clear(); em._CKPT_DOC_FOLDS.clear()
        em._COLD_FOLDS.clear(); em._COLD_REASONS.clear(); em._COLD_OVER_KB.clear()
        em._DOC_MEMO.clear(); em._DOC_MEMO_BYTES[0] = 0
    with em._READ_BYTES_LOCK:
        em._READ_BYTES.clear()
    em._LAST_ENTRY.ent = None


def _rec(i, rnd, big=False):
    """One synthetic transcript record of a coding session's shape (invented text, placeholder ids)."""
    k = i % 4
    u = "11111111-2222-4333-8444-%012d" % i
    p = "11111111-2222-4333-8444-%012d" % (i - 1) if i else None
    t = "2026-10-07T00:%02d:%02d.000Z" % ((i // 60) % 60, i % 60)
    if k == 0:
        return {"type": "user", "uuid": u, "parentUuid": p, "timestamp": t, "cwd": "/w/notes-api",
                "message": {"role": "user", "content": " ".join(rnd.choice(WORDS) for _ in range(12)) + " %d" % i}}
    if k == 1:
        return {"type": "assistant", "uuid": u, "parentUuid": p, "timestamp": t, "cwd": "/w/notes-api",
                "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_%d" % i, "name": "Bash",
                                                              "input": {"command": "make test # %d" % i}}],
                            "usage": {"input_tokens": 3 + i % 7, "output_tokens": 40}}}
    if k == 2:
        n = rnd.choice((40, 80, 160, 400, 1600, 6000)) * (8 if big else 1)
        return {"type": "user", "uuid": u, "parentUuid": p, "timestamp": t, "cwd": "/w/notes-api",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_%d" % (i - 1),
                                                         "content": " ".join(rnd.choice(WORDS) for _ in range(n))}]}}
    return {"type": "assistant", "uuid": u, "parentUuid": p, "timestamp": t, "cwd": "/w/notes-api",
            "message": {"role": "assistant", "content": [{"type": "text", "text": " ".join(rnd.choice(WORDS) for _ in range(60))}],
                        "stop_reason": "end_turn"}}


def write_transcript(path, target_bytes, start=0, seed=3, big=False, mode="w"):
    """Records appended until the file holds `target_bytes` more; returns (records written, the next index). A blank line
    and a line that is not JSON sit among them, as the reader skips such lines."""
    rnd = random.Random(seed + start)
    out, i, n = [], start, 0
    with open(path, mode) as f:
        while n < target_bytes:
            r = _rec(i, rnd, big)
            line = json.dumps(r) + "\n"
            f.write(line); n += len(line)
            out.append(r)
            if i % 997 == 13:
                f.write("\n{not json\n"); n += 11
            i += 1
    return out, i


def plain(path):
    """The reference: every record of the file as the reader's own reference scanner decodes it."""
    return list(em._read_jsonl(path))


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp()).resolve()
        self.proj = self.td / "projects" / "-w-notes-api"
        self.proj.mkdir(parents=True)
        self.ck = self.td / "checkpoints"
        em.set_checkpoint_dir(lambda: self.ck)
        fresh()

    def tearDown(self):
        em.set_checkpoint_dir(None)
        fresh()
        shutil.rmtree(self.td, ignore_errors=True)

    def leaf(self, name=SID):
        return str(self.proj / (name + ".jsonl"))


class LargeTranscriptHoldsItsTail(Base):
    """The headline: a 200 MB transcript at the default window (32 MiB, 2,000 records)."""

    def test_a_200_mb_transcript_holds_only_its_tail_in_memory(self):
        with knobs(32 * MIB, 2000, roots=[str(self.proj)]):
            path = self.leaf()
            recs, _ = write_transcript(path, 200 * 1000 * 1000, big=True)
            size = os.path.getsize(path)
            first, last, count = recs[0], recs[-1], len(recs)
            del recs
            tracemalloc.start()
            try:
                base_now, _ = tracemalloc.get_traced_memory()
                tracemalloc.reset_peak()
                ent = em._read_jsonl_entry(path)              # the WHOLE reader (tail_ok False): what a parse asks for
                held, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            got = ent[4]
            self.assertTrue(_tail(got), "the entry of a %d-byte transcript is held tail-only" % size)
            self.assertEqual((ent[5], len(got)), (0, count), "base 0 and every record counted: the whole list's shape")
            self.assertEqual(got[0], first, "the first record comes off disk")
            self.assertEqual(got[-1], last, "the last record comes out of the window")
            window = size - got.hot_offset()
            self.assertLessEqual(window, 32 * MIB + 400 * 1024, "the window is the last 32 MiB (plus at most one record)")
            stats = em.record_cache_stats()
            skel = 8 * count + int(getattr(got, "skel_bytes", 0) or 0)   # the walk skeleton of each older record, by what it holds
            self.assertLess(stats["bytes"], int(3 * (32 * MIB + 400 * 1024)) + 20 * count + skel + 1,
                            "the cache weighs the window, the index and the skeletons, not the file: %d" % stats["bytes"])
            self.assertLess(held - base_now, 160 * MIB,
                            "tracemalloc: %.1f MB held after reading a %.1f MB file (whole: about 3 bytes a file byte)"
                            % ((held - base_now) / 1e6, size / 1e6))
            self.assertLess(peak - base_now, 200 * MIB, "tracemalloc: the read peaked at %.1f MB: the scan drops what it "
                                                        "decodes before the window" % ((peak - base_now) / 1e6))
            again = em._read_jsonl_entry(path)
            self.assertIs(again, ent, "an unchanged file is a hit: the same entry, nothing read")


class TheWindow(Base):
    def _read(self, path):
        return em._read_jsonl_entry(path)

    def test_appends_extend_the_window_and_slide_it_under_the_same_generation(self):
        with knobs(64 * 1024, 10, roots=[str(self.proj)]):
            path = self.leaf()
            _, nxt = write_transcript(path, 1 * MIB)
            e1 = self._read(path)
            self.assertTrue(_tail(e1[4]))
            c1, h1 = e1[4].ncold, len(e1[4].hot)
            _, nxt = write_transcript(path, 8 * 1024, start=nxt, mode="a")       # a small append: inside the hysteresis
            e2 = self._read(path)
            self.assertEqual(e2[6], e1[6], "an append keeps the generation (every fold cursor stands)")
            self.assertEqual(e2[4].ncold, c1, "a small append extends the window without sliding it")
            self.assertGreater(len(e2[4].hot), h1)
            _, nxt = write_transcript(path, 200 * 1024, start=nxt, mode="a")     # past one and a half windows: it slides
            e3 = self._read(path)
            self.assertEqual(e3[6], e1[6])
            self.assertGreater(e3[4].ncold, c1, "the window slid forward")
            self.assertLessEqual(e3[1] - e3[4].hot_offset(), 64 * 1024 + 64 * 1024, "back to about one window")
            self.assertEqual(list(e3[4]), plain(path), "the records are the file's, every one, in order")
            self.assertIsNot(e3[4].hot, e2[4].hot, "a NEW list per append: a served one never changes under its holder")

    def test_every_index_slice_and_walk_equals_a_plain_read(self):
        with knobs(32 * 1024, 4, roots=[str(self.proj)]):
            path = self.leaf()
            write_transcript(path, 600 * 1024)
            got, ref = self._read(path)[4], plain(path)
            self.assertTrue(_tail(got))
            n = len(ref)
            self.assertEqual(len(got), n)
            self.assertEqual(list(got), ref)
            self.assertEqual(list(reversed(got)), ref[::-1])
            rnd = random.Random(5)
            for _ in range(300):
                i = rnd.randrange(-n, n)
                self.assertEqual(got[i], ref[i])
                a, b = sorted(rnd.randrange(-n - 3, n + 3) for _ in range(2))
                sl = got[a:b]
                self.assertEqual(len(sl), len(ref[a:b]))
                self.assertEqual(list(sl), ref[a:b])
                if len(sl):
                    j = rnd.randrange(len(sl))
                    self.assertEqual(sl[j], ref[a:b][j])
                    self.assertEqual(list(sl[j:]), ref[a:b][j:], "a slice of a slice")
                    self.assertEqual(list(reversed(sl)), ref[a:b][::-1])
            self.assertEqual(got[::7], ref[::7])
            with self.assertRaises(IndexError):
                got[n]
            self.assertTrue(bool(got))

    def test_a_fold_from_record_0_streams_the_older_records_and_leaves_the_entry_tail_only(self):
        with knobs(32 * 1024, 4, roots=[str(self.proj)]):
            path = self.leaf()
            write_transcript(path, 600 * 1024)
            ent = self._read(path)
            self.assertTrue(_tail(ent[4]))
            c0 = em.cold_read_stats()
            cache = {}
            step = lambda st, r: st + [r.get("uuid")]
            state = em.fold_records(cache, path, list, step)          # no cursor: a refold over every record
            self.assertEqual(state, [r.get("uuid") for r in plain(path)])
            c1 = em.cold_read_stats()
            self.assertEqual(c1["records"] - c0["records"], ent[4].ncold, "every record before the window read once")
            self.assertLessEqual(c1["passes"] - c0["passes"], 1, "in one streaming pass, never a read per record")
            after = em._JSONL_CACHE.get(path)
            self.assertTrue(after is not None and _tail(after[4]) and after[6] == ent[6], "the entry stays tail-only, same gen")
            write_transcript(path, 4 * 1024, start=100000, mode="a")
            state2 = em.fold_records(cache, path, list, step)          # an append: the new records alone, from the window
            self.assertEqual(state2, [r.get("uuid") for r in plain(path)])
            self.assertEqual(em.cold_read_stats()["records"], c1["records"], "an append reads nothing before the window: %r" % em.cold_read_stats())

    def test_a_fold_whose_cursor_fell_behind_the_window_steps_the_gap_from_disk(self):
        with knobs(32 * 1024, 4, roots=[str(self.proj)]):
            path = self.leaf()
            write_transcript(path, 600 * 1024)
            ent = self._read(path)
            ref = plain(path)
            behind = ent[4].ncold // 2
            cache = {path: (behind, ent[6], [r.get("uuid") for r in ref[:behind]])}
            state = em.fold_records(cache, path, list, lambda st, r: st + [r.get("uuid")])
            self.assertEqual(state, [r.get("uuid") for r in ref])

    def test_checkpoint_write_and_a_fresh_processs_restore_over_a_tail_only_entry(self):
        with knobs(32 * 1024, 4, roots=[str(self.proj)]):
            path = self.leaf()
            write_transcript(path, 600 * 1024)
            cache = {}
            count = lambda st, r: st + 1
            em.fold_records(cache, path, int, count, ckpt="tailCount")
            self.assertTrue(_tail(em._JSONL_CACHE[path][4]))
            self.assertTrue(em.checkpoint_write(path))
            n = len(plain(path))
            fresh()
            write_transcript(path, 4 * 1024, start=100000, mode="a")
            cache2 = {}
            em.name_fold_cache(cache2, "tailCount")
            got = em.fold_records(cache2, path, int, count, ckpt="tailCount")
            self.assertEqual(got, len(plain(path)))
            self.assertGreater(got, n)
            self.assertLess(em.read_bytes_report().get(path, 0), 64 * 1024, "the fresh process read the tail past the cut only")

    def test_a_rewritten_file_refuses_rather_than_serving_a_wrong_record(self):
        with knobs(32 * 1024, 4, roots=[str(self.proj)]):
            path = self.leaf()
            write_transcript(path, 300 * 1024)
            ent = self._read(path)
            self.assertTrue(_tail(ent[4]))
            held = ent[4]
            write_transcript(path, 300 * 1024, seed=99)                          # a rewrite: other records, about the same size
            with self.assertRaises(em.TailRecordsRead):
                held[0]
            with self.assertRaises(em.TailRecordsRead):
                list(held)
            self.assertGreater(em.cold_read_stats()["rewrites"], 0)
            self.assertEqual(list(self._read(path)[4]), plain(path), "the next read through the reader sees the rewrite")

    def _edit_in_place(self, path, j, old=b"make", new=b"MAKE"):
        """Record j's bytes edited in place at equal length (the file's last bytes untouched): the case the 64-byte witness
        alone cannot see (review find, 2026-10-07)."""
        ent = em._JSONL_CACHE[path]
        at, ln = int(ent[7][2 * j]), int(ent[7][2 * j + 1])
        with open(path, "r+b") as f:
            f.seek(at)
            b = f.read(ln)
            self.assertIn(old, b)
            f.seek(at)
            f.write(b.replace(old, new, 1))

    def test_a_same_length_edit_of_an_older_record_is_refused_by_a_held_view(self):
        with knobs(32 * 1024, 4, roots=[str(self.proj)]):
            path = self.leaf()
            write_transcript(path, 300 * 1024)
            held = self._read(path)[4]
            self.assertTrue(_tail(held))
            j = next(i for i in range(held.ncold) if i % 4 == 1)          # a tool call before the window ("make test # i")
            before = held[j]
            self._edit_in_place(path, j)
            fresh_view = self._read(path)[4]                               # another reader sees the new mtime and re-reads
            self.assertNotEqual(fresh_view[j], before, "the re-read serves the file as it now is")
            with self.assertRaises(em.TailRecordsRead):
                held[j]                                                    # the held view never serves the edited bytes
            with self.assertRaises(em.TailRecordsRead):
                list(held)
            self.assertEqual(held[-1], self._read(path)[4][-1], "records inside its window are still its own")

    def test_a_replaced_file_is_refused_by_a_held_view(self):
        with knobs(32 * 1024, 4, roots=[str(self.proj)]):
            path = self.leaf()
            write_transcript(path, 300 * 1024)
            held = self._read(path)[4]
            self.assertTrue(_tail(held))
            tmp = path + ".tmp"
            shutil.copy(path, tmp)
            os.replace(tmp, path)                                          # the same bytes, another file
            with self.assertRaises(em.TailRecordsRead):
                held[0]

    def test_a_whole_list_that_grows_past_the_threshold_converts_with_checksums(self):
        with knobs(32 * 1024, 4, roots=[str(self.proj)]):
            path = self.leaf()
            _, nxt = write_transcript(path, 40 * 1024)
            first = self._read(path)
            self.assertIs(type(first[4]), list, "under twice the window: whole")
            write_transcript(path, 120 * 1024, start=nxt, mode="a")
            grown = self._read(path)
            self.assertTrue(_tail(grown[4]), "past the threshold by appends: the window, under the same generation")
            self.assertEqual(grown[6], first[6])
            self.assertEqual(list(grown[4]), plain(path))
            self.assertEqual(len(grown[4].crcs), len(grown[4]), "a CRC for every record, the ones held whole before included")
            self._edit_in_place(path, 1)
            with self.assertRaises(em.TailRecordsRead):
                grown[4][1]

    def test_a_large_append_to_a_whole_list_takes_its_checksums_before_dropping_its_records(self):
        with knobs(32 * 1024, 4, roots=[str(self.proj)]):
            path = self.leaf()
            _, nxt = write_transcript(path, 40 * 1024)
            first = self._read(path)
            self.assertIs(type(first[4]), list)
            n0 = len(first[4])
            write_transcript(path, 300 * 1024, start=nxt, mode="a")          # one append over two windows: the scan drops
            grown = self._read(path)[4]
            self.assertTrue(_tail(grown))
            self.assertGreater(grown.ncold, n0, "the list held whole went to disk with the append's older records")
            self.assertEqual(len(grown.crcs), len(grown))
            self.assertEqual(list(grown), plain(path))
            self._edit_in_place(path, 1)
            with self.assertRaises(em.TailRecordsRead):
                grown[1]

    def test_the_record_floor_is_bounded_in_bytes(self):
        with knobs(32 * 1024, 1000, roots=[str(self.proj)]):
            path = self.leaf()
            with open(path, "w") as f:
                for i in range(60):                                        # 60 records of about 10 KB: 1,000 of them would be 10 MB
                    f.write(json.dumps({"type": "user", "uuid": "u%d" % i, "message": {"content": "x" * 10000}}) + "\n")
            got = self._read(path)[4]
            self.assertTrue(_tail(got))
            self.assertLessEqual(os.path.getsize(path) - got.hot_offset(), 64 * 1024 + 10100,
                                 "the record floor reaches back at most two windows")
            self.assertEqual(list(got), plain(path))

    def test_no_c_fast_path_sees_a_truncated_view(self):
        with knobs(32 * 1024, 4, roots=[str(self.proj)]):
            path = self.leaf()
            write_transcript(path, 300 * 1024)
            got = self._read(path)[4]
            self.assertTrue(_tail(got))
            self.assertNotIsInstance(got, list, "a list subclass would hand C readers its storage: the window alone")
            with self.assertRaises(TypeError):
                json.dumps(got)                                   # loud, never the window serialised as the whole
            self.assertEqual(len(list(got)), len(plain(path)))
            self.assertEqual(sum(1 for _ in got), len(plain(path)))

    def test_files_outside_the_transcripts_root_and_the_off_switch_stay_whole(self):
        other = self.td / "state" / "timeline"
        other.mkdir(parents=True)
        log = str(other / "messages.jsonl")                      # the postal log's place: walked whole by every parse
        with knobs(32 * 1024, 4, roots=[str(self.proj)]):
            path = self.leaf()
            write_transcript(path, 300 * 1024)
            shutil.copy(path, log)
            self.assertTrue(_tail(self._read(path)[4]), "the transcript is held tail-only")
            self.assertIs(type(self._read(log)[4]), list, "a large file outside the transcripts root stays whole")
            self.assertFalse(em.entry_whole_resident(path), "a fold from record 0 over a tail-only entry reads: not 'resident'")
            self.assertTrue(em.entry_indexed_whole(path), "its offsets start at record 0: the assembly writer's need")
            self.assertTrue(em.entry_whole_resident(log))
        with knobs(32 * 1024, 4, roots=None):
            fresh()
            self.assertIs(type(self._read(path)[4]), list, "the default root is PROJECTS, not this temp directory")
        with knobs(0, 4, roots=[str(self.proj)]):
            fresh()
            self.assertIs(type(self._read(path)[4]), list, "ROMP_RECORD_CACHE_TAIL_MB=0: every entry whole, as before")
        with knobs(32 * 1024, 4, roots=[str(self.proj)]):
            fresh()
            self.assertTrue(_tail(self._read(path)[4]), "on again (the guard: this test reads the rule, not a no-op)")


def _strip_tree(tree):
    t = json.loads(json.dumps(em.plain_tree(tree), default=lambda o: "<unserializable>"))
    t.pop("cutTurn", None)
    for turn in t["turns"]:
        for a in turn["atoms"]:
            a.pop("lazy", None)
    return t


class Assembly(Base):
    """The assembly's whole parse, its document, a fresh process's restore and a fold, over a tail-only leaf."""

    def parse(self, path):
        return em.parse_session(path, rompuuid=SID, name="impl", dir="/TESTDIR", candidate_files=[path],
                                states=None, postal_log=[], now=NOW)

    def test_the_whole_parse_the_document_and_the_restore_over_a_tail_only_leaf_equal_the_rule_off_parse(self):
        path = self.leaf()
        recs = transcript(NOW - 86400, turns=160, compact_every=40)
        Path(path).write_text("".join(json.dumps(r) + "\n" for r in recs))
        with knobs(0, 4, roots=[str(self.proj)]):
            saved = em._CKPT_DIR_FN; em._CKPT_DIR_FN = None
            try:
                ref = _strip_tree(self.parse(path))
            finally:
                em._CKPT_DIR_FN = saved
        fresh()
        with knobs(16 * 1024, 4, roots=[str(self.proj)]):
            tree = self.parse(path)
            ent = em._JSONL_CACHE.get(path)
            self.assertTrue(ent is not None and _tail(ent[4]), "the parse read the leaf through a tail-only entry")
            self.assertEqual(_strip_tree(tree), ref, "the whole parse over the tail-only entry equals the rule-off parse")
            key = (os.path.realpath(path), SID, False)
            ad = em._ASM_CACHE[key]["ad"]
            self.assertIs(type(ad._src[path]), list, "the whole adapter holds its own list: the writer never walks the disk")
            c0 = em.cold_read_stats()["records"]
            self.assertTrue(em.asm_checkpoint_write(path, SID, tree=tree), em.asm_checkpoint_stats())
            self.assertEqual(em.cold_read_stats()["records"], c0, "the document was written without reading before the window")
            more = [{"type": "user", "uuid": "u-more", "parentUuid": recs[-1]["uuid"], "timestamp": "2026-06-11T12:00:00.000Z",
                     "promptSource": "typed", "cwd": "/w/notes-api", "message": {"role": "user", "content": "one more"}}]
            fresh()
            restored = self.parse(path)
            em.hydrate(restored, SID)
            self.assertEqual(_strip_tree(restored), ref, "a fresh process's restore, hydrated, equals the rule-off parse")
            with open(path, "a") as f:
                f.write(json.dumps(more[0]) + "\n")
            fresh()
            folded = self.parse(path)
            em.hydrate(folded, SID)
        with knobs(0, 4, roots=[str(self.proj)]):
            fresh()
            saved = em._CKPT_DIR_FN; em._CKPT_DIR_FN = None
            try:
                ref2 = _strip_tree(self.parse(path))
            finally:
                em._CKPT_DIR_FN = saved
        self.assertEqual(_strip_tree(folded), ref2, "after an append, still equal")


class ChatScrollBack(unittest.TestCase):
    """The chat build's scroll-back pages (the kernel's _chat_history_page) over a tail-only leaf equal the whole build:
    the test_chat_pages harness's check, with the leaf's record entry tail-only throughout."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.saved_state = jd.STATE
        jd._rebind_state(self.td / "state")
        for d in ("states", "goals", "sdk", "checkpoints"):
            (jd.STATE / d).mkdir(parents=True, exist_ok=True)
        em.set_checkpoint_dir(lambda: jd.STATE / "checkpoints")
        self.proj = self.td / "proj"; self.proj.mkdir()
        self.leaf = str(self.proj / (SID + ".jsonl"))
        self.rows = [{"sid": SID, "name": "web", "path": self.leaf, "mtime": NOW, "anchor": SID}]
        self.saved = (km._sessions, km._live_map)
        km._sessions = lambda now, **kw: list(self.rows)
        km._live_map = lambda: {}
        self.fresh()

    def tearDown(self):
        km._sessions, km._live_map = self.saved
        km._live_scope.chat_floor0 = None
        em.set_checkpoint_dir(None)
        jd._rebind_state(self.saved_state)
        self.fresh()
        shutil.rmtree(self.td, ignore_errors=True)

    def fresh(self):
        fresh()
        with km._chat_fold_lock:
            km._chat_fold.clear()
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()   # the two share one identity key (test_judge_parse_cache)
        km._parse_mode.clear()
        km._built_chat.clear() if hasattr(km, "_built_chat") else None
        km._prev_chat_events.clear()
        km._RENDER_FLOOR.clear()
        with km._page_lock:
            km._PAGE_CACHE.clear()
        km._live_scope.chat_floor0 = None
        with em._MAT_LOCK:
            em._MAT_LRU.clear()

    def test_scroll_back_pages_over_a_tail_only_leaf_equal_the_whole_build(self):
        recs = transcript(NOW - 86400, turns=120, compact_every=25)
        Path(self.leaf).write_text("".join(json.dumps(r) + "\n" for r in recs))
        strip = lambda ev: json.loads(json.dumps(ev, default=lambda o: "<unserializable>"))
        with knobs(0, 4, roots=[str(self.proj)]):                 # the reference: the rule off, a fully hydrated build
            self.fresh(); saved = em._CKPT_DIR_FN; em._CKPT_DIR_FN = None
            try:
                m = km.build_session(SID, NOW, {}, floor=0)
            finally:
                em._CKPT_DIR_FN = saved
            heads = strip(m.get("headCards") or [])
            whole = strip(m["events"])[len(heads):]
        with knobs(16 * 1024, 4, roots=[str(self.proj)]):
            self.fresh()
            km.build_session(SID, NOW, {}, floor=0)              # a whole parse over the tail-only entry, then its document
            ent = em._JSONL_CACHE.get(self.leaf)
            self.assertTrue(ent is not None and _tail(ent[4]), "the chat build read the leaf through a tail-only entry")
            self.assertTrue(em.asm_checkpoint_write(self.leaf, SID, tree=km._parse(self.leaf, SID, NOW)), em.asm_checkpoint_stats())
            self.fresh()
            km._live_scope.chat_floor0 = False
            try:
                m2 = km.build_session(SID, NOW, {})
            finally:
                km._live_scope.chat_floor0 = None
            floor = m2["floor"]
            self.assertGreater(floor, 0, "a restored parse renders from the cut")
            tail = strip(m2["events"])
            self.assertEqual(tail, whole[len(whole) - len(tail):], "the floor'd list is the whole build's tail")
            for size in (1, 7, 16):
                with self.subTest(page_turns=size):
                    pages = []
                    for lo in range(0, floor, size):
                        pages += km._chat_history_page(SID, lo, min(lo + size, floor), NOW)
                    self.assertEqual(strip(pages) + tail, whole, "scroll-back pages of %d turns plus the tail equal the whole" % size)
            ent = em._JSONL_CACHE.get(self.leaf)
            if ent is not None:
                self.assertTrue(_tail(ent[4]) or ent[5] > 0, "the leaf's record entry never became whole")



def write_forked(path, n, fork_at=40, branch=3, seed=5):
    """A synthetic linear transcript of `n` records with a REWOUND branch: `branch` records hanging off record `fork_at`,
    written right after it in file order (so they sit before the window of a large file), while the spine carries on from
    record fork_at + 1. Returns (the branch's uuids, the next record index)."""
    rnd = random.Random(seed)
    out, bu = [], []
    for i in range(n):
        out.append(json.dumps(_rec(i, rnd)))
        if i == fork_at:
            parent = _rec(i, rnd)["uuid"]
            for b in range(branch):
                u = "22222222-2222-4333-8444-%012d" % b
                kind = "user" if b % 2 == 0 else "assistant"
                msg = ({"role": "user", "content": "a prompt the user rewound away %d" % b} if kind == "user" else
                       {"role": "assistant", "content": [{"type": "text", "text": "a reply on the rewound line %d" % b}]})
                out.append(json.dumps({"type": kind, "uuid": u, "parentUuid": parent, "timestamp": "2026-10-07T00:00:41.000Z",
                                       "cwd": "/w/notes-api", "message": msg}))
                bu.append(u); parent = u
    Path(path).write_text("\n".join(out) + "\n")
    return set(bu), n


def append_chain(path, start, count, seed=9):
    """`count` more spine records continuing the linear chain at record index `start`."""
    rnd = random.Random(seed + start)
    with open(path, "a") as f:
        for i in range(start, start + count):
            f.write(json.dumps(_rec(i, rnd)) + "\n")
    return start + count


def cold_records():
    return em.cold_read_stats()["records"]


class RoutineWalksReadNothingBeforeTheWindow(Base):
    """The chain walks a judge pass runs whenever a session's leaf grew (review finds, 2026-10-07): each read every record
    before a tail-only entry's window off disk, every pass. They now walk the entry's skeletons (the graph fields of each
    older record, kept from the scan and the window's slides) and read nothing before the window per pass."""

    def setUp(self):
        super().setUp()
        em._REWOUND_CACHE.clear()

    def tearDown(self):
        em._REWOUND_CACHE.clear()
        super().tearDown()

    def _ref_rewound(self, path):
        with knobs(0, 10, roots=[str(self.proj)]):
            fresh(); em._REWOUND_CACHE.clear()
            out = em._rewound_walk(path)[0]
        fresh(); em._REWOUND_CACHE.clear()
        return out

    def test_the_rewound_memo_road_reads_nothing_before_the_window_per_pass(self):
        path = self.leaf()
        branch, nxt = write_forked(path, 6000)
        ref = self._ref_rewound(path)
        self.assertEqual(ref, branch, "the reference walk files the branch as rewound")
        with knobs(64 * 1024, 10, roots=[str(self.proj)]):
            ent = em._read_jsonl_entry(path)
            self.assertTrue(_tail(ent[4]) and ent[4].ncold > 100, "the leaf is held tail-only, its branch before the window")
            c0 = cold_records()
            self.assertEqual(em.rewound_uuids(path, drop=False), ref, "the first walk equals the whole walk")
            self.assertEqual(cold_records() - c0, 0, "the first call after a restart (no memo to restore) read nothing "
                                                     "before the window: its retiring fold steps no record")
            per_pass = []
            for k in range(3):
                nxt = append_chain(path, nxt, 3)          # the session wrote: the memo is retired and the walk runs again
                c0 = cold_records()
                got = em.rewound_uuids(path, drop=False)
                per_pass.append(cold_records() - c0)
                self.assertEqual(got, ref, "pass %d: the walk over skeletons files the same rewound set" % k)
            self.assertEqual(per_pass, [0, 0, 0], "records read before the window per pass: %r" % per_pass)
            self.assertTrue(_tail(em._JSONL_CACHE[path][4]), "the entry stayed tail-only")
        self.assertEqual(self._ref_rewound(path), ref)


def _batch_shape():
    """A parallel tool batch (three calls of one model message, results parented at their own calls) and a rewound prompt,
    in the shape tests/test_parallel_tool_batch.py pins (synthetic ids)."""
    t = "2026-10-07T01:00:%02d.000Z"
    msg = "msg_skel_batch"

    def call(i, uid, parent):
        return {"type": "assistant", "uuid": uid, "parentUuid": parent, "timestamp": t % i,
                "message": {"id": msg, "role": "assistant", "content": [
                    {"type": "tool_use", "id": "toolu_b%d" % i, "name": "Bash", "input": {"command": "make lint %d" % i}}]}}

    def result(i, uid, call_uid):
        return {"type": "user", "uuid": uid, "parentUuid": call_uid, "sourceToolAssistantUUID": call_uid, "promptId": "p-b",
                "timestamp": t % (10 + i), "toolUseResult": {"stdout": "ok %d" % i},
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_b%d" % i,
                                                         "content": "ok %d" % i}]}}
    return [
        {"type": "user", "uuid": "b-p", "parentUuid": None, "timestamp": t % 0, "promptId": "p-b",
         "message": {"role": "user", "content": "run the three checks"}},
        call(1, "b-c1", "b-p"), call(2, "b-c2", "b-c1"), call(3, "b-c3", "b-c2"),
        result(1, "b-r1", "b-c1"), result(3, "b-r3", "b-c3"), result(2, "b-r2", "b-c2"),
        {"type": "assistant", "uuid": "b-a", "parentUuid": "b-r2", "timestamp": t % 30,
         "message": {"id": "msg_skel_reply", "role": "assistant", "content": [{"type": "thinking", "thinking": "hm"},
                                                                              {"type": "text", "text": "All three pass."}]}},
        {"type": "user", "uuid": "b-x", "parentUuid": "b-a", "timestamp": t % 31,
         "message": {"role": "user", "content": [{"type": "text", "text": "a prompt rewound away"},
                                                 {"type": "image", "source": {"type": "base64", "data": "AAAA"}}]}},
        {"type": "user", "uuid": "b-y", "parentUuid": "b-a", "timestamp": t % 32,
         "message": {"role": "user", "content": "the prompt that replaced it"}},
    ]


def golden_scenarios():
    """The golden scenarios' builders (tests/test_event_model_golden.py SINGLE_FILE), loaded WITHOUT re-executing the event
    model: that module loads it by path under the shared name, which load_source re-executes into this test's own module
    object (resetting its knobs and caches), so it is run here with a loader that hands back the module already loaded.
    The environment it sets for its own state root is restored."""
    import types
    saved_env, saved_mod = dict(os.environ), sys.modules.get("romp_load")
    stub = types.ModuleType("romp_load")
    stub.load_source = lambda name, path: em
    sys.modules["romp_load"] = stub
    try:
        ns = {"__name__": "golden_scenarios_for_tail_only", "__file__": os.path.join(HERE, "test_event_model_golden.py")}
        src = Path(HERE, "test_event_model_golden.py").read_text()
        exec(compile(src, ns["__file__"], "exec"), ns)
        return ns["SINGLE_FILE"]
    finally:
        if saved_mod is not None:
            sys.modules["romp_load"] = saved_mod
        else:
            sys.modules.pop("romp_load", None)
        os.environ.clear(); os.environ.update(saved_env)


def _verdict_field_shapes():
    """Shapes whose verdicts turn on the skeleton fields no golden scenario exercises (review find, 2026-10-07): a retry-storm
    fork whose second assistant branch is an isApiErrorMessage echo, one whose second branch is a blank-text stub (the
    assistant-text mark), and a parallel batch carrying an isMeta hook note. Synthetic ids and text."""
    U = lambda n: "66666666-2222-3333-4444-%012d" % n
    ts = lambda k: "2026-10-07T03:00:%02d.000Z" % k

    def storm(second):
        return [
            {"type": "user", "uuid": U(1), "parentUuid": None, "timestamp": ts(0), "message": {"role": "user", "content": "do the thing"}},
            {"type": "assistant", "uuid": U(2), "parentUuid": U(1), "timestamp": ts(1),
             "message": {"id": "m1", "role": "assistant", "content": [{"type": "thinking", "thinking": "hm"}]}},
            {"type": "assistant", "uuid": U(5), "parentUuid": U(2), "timestamp": ts(2),
             "message": {"id": "m2", "role": "assistant", "content": [{"type": "text", "text": "the real reply"}]}},
            dict({"type": "assistant", "uuid": U(6), "parentUuid": U(2), "timestamp": ts(3)}, **second),
            {"type": "system", "subtype": "api_error", "uuid": U(3), "parentUuid": U(2), "timestamp": ts(4)},
            {"type": "user", "uuid": U(4), "parentUuid": U(3), "timestamp": ts(5), "message": {"role": "user", "content": "next prompt"}},
        ]
    meta = _batch_shape()
    meta.insert(5, {"type": "user", "uuid": "b-m", "parentUuid": "b-r1", "isMeta": True, "timestamp": "2026-10-07T01:00:12.500Z",
                    "message": {"role": "user", "content": "a hook note the harness wrote"}})
    return {
        "storm_api_error_echo": storm({"isApiErrorMessage": True, "message": {"id": "m3", "role": "assistant",
                                                                              "content": [{"type": "text", "text": "API Error: overloaded"}]}}),
        "storm_blank_stub": storm({"message": {"id": "m3", "role": "assistant", "content": [{"type": "text", "text": "  \n "}]}}),
        "batch_with_meta_note": meta,
    }


class SkeletonWalksEqualFullWalks(Base):
    """A walk over a tail-only entry's skeletons files every record as the walk over the full records does, for every graph
    shape the golden scenarios pin (compactions with intact and broken stitches, a detached manual compact, an eclipsed
    retry storm, rewinds, broken chains, a /clear, queued prompts, slash commands) and a parallel tool batch, with the
    shape BEFORE the window; and a skeleton asked for a field it does not keep refuses, so the walk is re-run whole."""

    def _scenarios(self):
        out = {name: fn() for name, (fn, _states) in golden_scenarios().items()}
        out["parallel_batch"] = _batch_shape()
        out.update(_verdict_field_shapes())
        return out

    def _mismatches(self):
        """Every (scenario, layout) whose walk over skeletons differs from the walk over the full records, or refuses a field
        (_SkelMiss), with the shape before the window; [] when the skeletons stand in for the records exactly."""
        path = self.leaf()
        out = []
        with knobs(16 * 1024, 4, roots=[str(self.proj)]):
            for name, recs in sorted(self._scenarios().items()):
                for layout in ("continue", "back"):
                    fresh()
                    self._write(path, recs, layout)
                    ent = em._read_jsonl_entry(path)
                    self.assertTrue(_tail(ent[4]) and ent[4].ncold > len(recs), "the scenario is before the window")
                    full = em.FileAdapter([path], path)
                    c0 = cold_records()
                    try:
                        walk = em.FileAdapter([path], path, walk_only=True)
                        got = (walk.chain_verdicts(), em._membership_of(walk), walk.leaf_uuid, walk.parent_of, walk._adopted)
                    except em._SkelMiss as e:
                        out.append((name, layout, "refused %s" % e)); continue
                    self.assertEqual(cold_records(), c0, "the walk read nothing before the window")
                    want = (full.chain_verdicts(), em._membership_of(full), full.leaf_uuid, full.parent_of, full._adopted)
                    if got != want:
                        diff = sorted(u for u in set(got[0]) | set(want[0]) if got[0].get(u) != want[0].get(u))
                        out.append((name, layout, "verdicts differ on %s" % diff[:4]))
        return out

    def _write(self, path, recs, layout):
        rnd = random.Random(11)
        lines = [json.dumps(r) for r in recs]
        last = next((r["uuid"] for r in reversed(recs) if r.get("uuid")), None)
        prev = last if layout == "continue" else None
        for i in range(1200):                             # about 600 KB of filler: the scenario sits before a 16 KiB window
            r = _rec(100000 + i, rnd)
            r["uuid"] = "33333333-2222-4333-8444-%012d" % i
            r["parentUuid"] = prev
            prev = r["uuid"]
            lines.append(json.dumps(r))
        if layout == "back":                              # the leaf returns to the scenario's line: the filler is a /clear branch
            lines.append(json.dumps({"type": "user", "uuid": "back-1", "parentUuid": last, "timestamp": "2026-10-07T02:00:00.000Z",
                                     "message": {"role": "user", "content": "back on the first line"}}))
        Path(path).write_text("\n".join(lines) + "\n")

    def test_skeleton_walks_file_every_shape_as_the_full_walk(self):
        self.assertEqual(self._mismatches(), [])

    def test_a_broken_verdict_field_in_the_skeleton_is_caught(self):
        """Review find (2026-10-07): the check above stayed green with isApiErrorMessage or isMeta dropped from the skeleton,
        or the assistant-text mark broken, because no shape it walked depended on them; the verdict-field shapes now do. Each
        mutation of the skeleton builder must make the check report a mismatch (a wrong verdict, or a refused field)."""
        S0, A0, M0, K0 = em._SKEL_SCALARS, em._SkelRec._ALLOW, em._skel_text_mark, em._skel

        def blank(field):                                 # the key kept in the allowed set, its value gone: answers "absent"
            def mut(r):
                sk = K0(r)
                if type(sk) is em._SkelRec and dict.__contains__(sk, field) and em._SKEL_BARE.get(dict.get(sk, "type")) is not sk:
                    dict.__delitem__(sk, field)
                return sk
            return {"_skel": mut}

        def unkept(field):                                # the natural edit: the field leaves the kept list (and the allowed set)
            sc = tuple(k for k in S0 if k != field)
            return {"_SKEL_SCALARS": sc, "_ALLOW": frozenset(sc + ("message", "attachment", "compactMetadata"))}
        mutations = {
            "isApiErrorMessage blanked": blank("isApiErrorMessage"), "isApiErrorMessage unkept": unkept("isApiErrorMessage"),
            "isMeta blanked": blank("isMeta"), "isMeta unkept": unkept("isMeta"),
            "text mark without strip": {"_skel_text_mark": lambda t: ("x" if t else "") if type(t) is str else t},
            "text mark always non-empty": {"_skel_text_mark": lambda t: "x" if type(t) is str else t},
        }
        for name, patch in mutations.items():
            with self.subTest(mutation=name):
                try:
                    em._skel = patch.get("_skel", K0)
                    em._skel_text_mark = patch.get("_skel_text_mark", M0)
                    em._SKEL_SCALARS = patch.get("_SKEL_SCALARS", S0)
                    em._SkelRec._ALLOW = patch.get("_ALLOW", A0)
                    found = self._mismatches()
                finally:
                    em._SKEL_SCALARS, em._SkelRec._ALLOW, em._skel_text_mark, em._skel = S0, A0, M0, K0
                    fresh()
                self.assertTrue(found, "the skeleton-vs-full check caught the mutation")

    def test_a_skeleton_refuses_a_field_it_does_not_keep_and_the_walk_reruns_whole(self):
        r = {"type": "user", "uuid": "u1", "parentUuid": None, "toolUseResult": {"stdout": "x"},
             "message": {"role": "user", "model": "m", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "big"}]}}
        s = em._skel(r)
        self.assertEqual((s.get("uuid"), s.get("promptId"), s["message"]["content"][0].get("tool_use_id")), ("u1", None, "t"))
        for probe in (lambda: s.get("toolUseResult"), lambda: s["cwd"], lambda: "toolUseResult" in s,
                      lambda: s["message"].get("model"), lambda: s["message"]["content"][0].get("content")):
            with self.assertRaises(em._SkelMiss):
                probe()
        before = em.cold_read_stats()["skeleton"]["misses"]
        seen = []

        def build(walk_only):
            seen.append(walk_only)
            if walk_only:
                return s.get("toolUseResult")
            return "whole"
        self.assertEqual(em._walk_with_skeletons(build), "whole")
        self.assertEqual(seen, [True, False])
        self.assertEqual(em.cold_read_stats()["skeleton"]["misses"], before + 1)


class DocumentSeededWalksReadNothingBeforeTheWindow(Base):
    """file_rewound and chain_membership over a leaf whose assembly document stands (review find, 2026-10-07): the document's
    cut moves only on a whole parse, so on a large leaf it trails the end of the file by far more than the window, and each
    call read the records between the cut and the window off disk TWICE (the document chain check, then the seeded
    adapter), on every judge pass the leaf grew. Both now walk the skeletons of that span."""

    def parse(self, path):
        return em.parse_session(path, rompuuid=SID, name="impl", dir="/TESTDIR", candidate_files=[path],
                                states=None, postal_log=[], now=NOW)

    def _grow(self, path, last, start, n, fork_at=None):
        """`n` spine records chained onto uuid `last`, and, when `fork_at` is given, a two-record branch off the spine record
        with that index that the spine then leaves (a rewound line). Returns (the last spine uuid, the branch's uuids)."""
        rnd = random.Random(start)
        prev, branch, lines = last, set(), []
        for i in range(start, start + n):
            r = _rec(i, rnd)
            r["uuid"] = "44444444-2222-4333-8444-%012d" % i
            r["parentUuid"] = prev
            r["timestamp"] = "2026-06-11T13:%02d:%02d.000Z" % ((i // 60) % 60, i % 60)
            lines.append(json.dumps(r)); prev = r["uuid"]
            if i == fork_at:
                for b, kind in enumerate(("user", "assistant")):
                    u = "55555555-2222-4333-8444-%012d" % b
                    msg = ({"role": "user", "content": "a line the user rewound away"} if kind == "user" else
                           {"role": "assistant", "content": [{"type": "text", "text": "a reply on the rewound line"}]})
                    lines.append(json.dumps({"type": kind, "uuid": u, "parentUuid": r["uuid"] if b == 0 else branch_last,
                                             "timestamp": r["timestamp"], "message": msg}))
                    branch.add(u); branch_last = u
        with open(path, "a") as f:
            f.write("\n".join(lines) + "\n")
        return prev, branch

    def _ref(self, path):
        with knobs(0, 4, roots=[str(self.proj)]):
            fresh()
            saved = em._CKPT_DIR_FN; em._CKPT_DIR_FN = None
            try:
                out = (em.file_rewound(path), em.chain_membership(path))
            finally:
                em._CKPT_DIR_FN = saved
        fresh()
        return out

    def test_the_seeded_walks_read_nothing_before_the_window_per_pass(self):
        path = self.leaf()
        recs = transcript(NOW - 86400, turns=160, compact_every=40)
        Path(path).write_text("".join(json.dumps(r) + "\n" for r in recs))
        with knobs(16 * 1024, 4, roots=[str(self.proj)]):
            tree = self.parse(path)
            self.assertTrue(em.asm_checkpoint_write(path, SID, tree=tree), em.asm_checkpoint_stats())
        last, branch = self._grow(path, recs[-1]["uuid"], 0, 1500, fork_at=30)   # far past the cut: the span is before the window
        ref_rw, ref_cm = self._ref(path)
        self.assertEqual(ref_rw, branch, "the reference walk files the branch as rewound")
        with knobs(16 * 1024, 4, roots=[str(self.proj)]):
            fresh()
            self.assertEqual(em.file_rewound(path, rompuuid=SID, sdk_human=False), ref_rw)
            ent = em._JSONL_CACHE[path]
            self.assertTrue(_tail(ent[4]) and ent[5] > 0 and ent[4].ncold > 1000,
                            "the entry holds the records past the document's cut, tail-only, the branch before its window")
            per_pass, nxt = [], 1500
            for k in range(3):
                last, _ = self._grow(path, last, nxt, 3); nxt += 3
                c0 = cold_records()
                rw = em.file_rewound(path, rompuuid=SID, sdk_human=False)
                cm = em.chain_membership(path, rompuuid=SID, sdk_human=False)
                per_pass.append(cold_records() - c0)
                self.assertEqual(rw, ref_rw, "pass %d: the seeded walk over skeletons files the same rewound set" % k)
                self.assertTrue(branch <= cm["rewind"], "pass %d: the membership's rewind set holds the branch" % k)
            self.assertEqual(per_pass, [0, 0, 0], "records read before the window per pass: %r" % per_pass)
        ref_rw2, ref_cm2 = self._ref(path)
        self.assertEqual(rw, ref_rw2)
        self.assertEqual(cm, ref_cm2, "the seeded membership equals the whole walk's after the appends")


class RewindHoldReadsNothingBeforeTheWindow(Base):
    """The rewind gesture's kept-chain walk (the kernel's _rewind_kept_uuids, review find, 2026-10-07): while a bare rollback's
    hold is armed, every feed or chat build asks em.chain_membership with the pending cut and no session id, on the pusher
    thread and, mid-pass, inside _goals_snap_lock. That always built a whole adapter, which over a tail-only leaf read every
    record before the window off disk, once per gesture and again at take and dissolve. It now walks the skeletons."""

    def setUp(self):
        super().setUp()
        self.saved = (km._sessions, km._sdk)
        self.cut = ""
        test = self

        class Backend:
            def pending_cut(self, sid):
                return test.cut
        km._sessions = lambda now, **kw: [{"sid": SID, "name": "web", "path": self.leaf(), "mtime": NOW, "anchor": SID}]
        km._sdk = lambda: Backend()
        km._rewind_kept_memo.clear()

    def tearDown(self):
        km._sessions, km._sdk = self.saved
        km._rewind_kept_memo.clear()
        super().tearDown()

    def test_the_rewind_holds_kept_chain_reads_nothing_before_the_window(self):
        path = self.leaf()
        branch, nxt = write_forked(path, 6000)
        rnd = random.Random(5)
        self.cut = [_rec(i, rnd) for i in range(5990)][-1]["uuid"]   # a pending bare rollback near the end of the leaf
        with knobs(0, 10, roots=[str(self.proj)]):
            fresh()
            ref = em.chain_membership(path, candidate_files=[path], leaf_override=self.cut)
        self.assertTrue(branch <= ref["rewind"])
        fresh()
        with knobs(64 * 1024, 10, roots=[str(self.proj)]):
            ent = em._read_jsonl_entry(path)
            self.assertTrue(_tail(ent[4]) and ent[4].ncold > 100)
            per_build = []
            for k in range(3):
                km._rewind_kept_memo.clear()                 # each build's memo key moves when a record lands (the take)
                c0 = cold_records()
                kept = km._rewind_kept_uuids(SID)
                per_build.append(cold_records() - c0)
                self.assertEqual(kept, ref["kept"], "build %d: the kept chain under the pending cut" % k)
            self.assertEqual(per_build, [0, 0, 0], "records read before the window per build: %r" % per_build)
            nxt = append_chain(path, nxt, 3)                 # the branch take's records land: the dissolve-time walk
            c0 = cold_records()
            km._rewind_kept_memo.clear()
            self.assertIsNotNone(km._rewind_kept_uuids(SID))
            self.assertEqual(cold_records() - c0, 0, "the walk after the take read nothing before the window")


class ARefusedReadDropsItsEntry(Base):
    """A refusal drops the reader's entry of its generation (review find, 2026-10-07): before, nothing removed it, so a file
    replaced by a byte-identical copy that kept its mtime, or edited in place at equal length and then appended to, raised
    TailRecordsRead on every read (folds, parses, walks) until the entry was evicted or the kernel restarted."""
    _read = TheWindow._read
    _edit_in_place = TheWindow._edit_in_place

    def test_a_copy_restored_with_its_mtime_raises_once_then_reads_afresh(self):
        with knobs(32 * 1024, 4, roots=[str(self.proj)]):
            path = self.leaf()
            write_transcript(path, 300 * 1024)
            ent = self._read(path)
            self.assertTrue(_tail(ent[4]))
            st = os.stat(path)
            tmp = path + ".tmp"
            shutil.copy2(path, tmp)                                        # the same bytes and mtime (cp -p, rsync -a)
            os.replace(tmp, path)
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
            self.assertIs(self._read(path), ent, "same mtime and size: the reader serves the held entry")
            with self.assertRaises(em.TailRecordsRead):
                list(em.FileAdapter([path], path)._src[path])
            again = self._read(path)
            self.assertIsNot(again, ent, "the refusal dropped the entry: the next read re-read the file")
            self.assertNotEqual(again[6], ent[6], "under a fresh generation")
            self.assertEqual(list(again[4]), plain(path))
            self.assertEqual(len(em.FileAdapter([path], path).by_uuid), len({r["uuid"] for r in plain(path)}))

    def test_an_edit_then_an_append_raises_once_then_reads_afresh(self):
        with knobs(32 * 1024, 4, roots=[str(self.proj)]):
            path = self.leaf()
            _, nxt = write_transcript(path, 300 * 1024)
            ent = self._read(path)
            j = next(i for i in range(ent[4].ncold) if i % 4 == 1)
            st = os.stat(path)
            self._edit_in_place(path, j)
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))            # the edit itself goes unseen by the stat
            write_transcript(path, 4 * 1024, start=nxt, mode="a")          # an append before the next stat
            grown = self._read(path)
            self.assertEqual(grown[6], ent[6], "the reader took it as an append, under the same generation")
            with self.assertRaises(em.TailRecordsRead):
                grown[4][j]
            again = self._read(path)
            self.assertNotEqual(again[6], ent[6], "the refusal dropped the entry: a fresh generation")
            self.assertEqual(list(again[4]), plain(path), "the re-read serves the file as it now is, every record")

    def test_a_refusal_leaves_an_entry_another_reader_already_re_read(self):
        with knobs(32 * 1024, 4, roots=[str(self.proj)]):
            path = self.leaf()
            write_transcript(path, 300 * 1024)
            held = self._read(path)[4]
            j = next(i for i in range(held.ncold) if i % 4 == 1)
            self._edit_in_place(path, j)
            fresh_ent = self._read(path)                                   # a new mtime: re-read, a fresh generation
            with self.assertRaises(em.TailRecordsRead):
                held[j]
            self.assertIs(self._read(path), fresh_ent, "the fresh entry stands")


class WholeAdaptersShareOneList(Base):
    """A whole adapter over a tail-only entry (review find, 2026-10-07) decoded a private full copy off disk, so two whole
    adapters over one transcript held two copies (and each paid a streaming pass), where the cache's single whole list had
    been shared before. A second whole adapter over the same entry generation now shares the first one's list, and after an
    append extends it with the window's newer records from memory."""

    def test_a_second_whole_adapter_shares_the_first_ones_records_and_reads_nothing(self):
        with knobs(32 * 1024, 4, roots=[str(self.proj)]):
            path = self.leaf()
            _, nxt = write_transcript(path, 300 * 1024)
            self.assertTrue(_tail(em._read_jsonl_entry(path)[4]))
            a1 = em.FileAdapter([path], path)
            c0 = cold_records()
            a2 = em.FileAdapter([path], path)
            self.assertEqual(cold_records() - c0, 0, "the second whole adapter read nothing before the window")
            self.assertIs(a2._src[path][0], a1._src[path][0], "the two share the records before the window: one copy")
            write_transcript(path, 4 * 1024, start=nxt, mode="a")
            c0 = cold_records()
            a3 = em.FileAdapter([path], path)
            self.assertEqual(cold_records() - c0, 0, "after an append, the shared list is extended from the window")
            self.assertIs(a3._src[path][0], a1._src[path][0])
            self.assertEqual(a3._src[path], plain(path), "every record, in order")
            self.assertEqual(a3._src_keys[path], (em._JSONL_CACHE[path][6], 0, len(plain(path))))
            del a1, a2, a3
            fresh()
            write_transcript(path, 4 * 1024, start=nxt + 1000, mode="a")
            em._read_jsonl_entry(path)
            c0 = cold_records()
            a4 = em.FileAdapter([path], path)
            self.assertGreater(cold_records() - c0, 0, "a new generation with no live holder streams its own list")
            self.assertEqual(a4._src[path], plain(path))


class SkeletonsStayOutOfTheCollectorsWalk(Base):
    """The record cache keeps its decoded records out of the cyclic collector's walk (the kernel's gc pause work); skeletons
    are dict subclasses, which that untracking skips by type, so they are untracked where they are built: a large leaf
    holds hundreds of thousands of them, and tracked they would lengthen every full collection."""

    def test_skeletons_and_their_list_are_untracked(self):
        import gc
        if not em._GC_UNTRACK_ON:
            self.skipTest("the record cache's gc untracking is off in this interpreter")
        with knobs(32 * 1024, 4, roots=[str(self.proj)]):
            path = self.leaf()
            _, nxt = write_transcript(path, 300 * 1024)
            write_forked(self.leaf("bbbbbbbb-4444-4222-8333-555555555555"), 600)
            for p in (path, self.leaf("bbbbbbbb-4444-4222-8333-555555555555")):
                recs = em._read_jsonl_entry(p)[4]
                self.assertTrue(_tail(recs) and recs.skel is not None and len(recs.skel) >= recs.ncold)
                tracked = [i for i, s in enumerate(recs.skel[:recs.ncold]) if gc.is_tracked(s)
                           or (isinstance(s.get("message"), dict) and gc.is_tracked(s.get("message")))
                           or any(gc.is_tracked(b) for b in ((s.get("message") or {}).get("content") or [])
                                  if isinstance(b, dict))]
                self.assertEqual(tracked, [], "skeletons tracked by the collector")
                self.assertFalse(gc.is_tracked(recs.skel), "the skeleton list itself")
            write_transcript(path, 200 * 1024, start=nxt, mode="a")          # an append slides the window: new skeletons
            recs = em._read_jsonl_entry(path)[4]
            self.assertEqual([i for i, s in enumerate(recs.skel[:recs.ncold]) if gc.is_tracked(s)], [])
            self.assertFalse(gc.is_tracked(recs.skel))


def _counted_skeleton_bytes(skels):
    """What the cache counts for these skeletons (and their list's pointers): each skeleton by what it holds, or, on a tree
    with the first flat charge, that charge."""
    fn = getattr(em, "_skel_bytes", None)
    if fn is not None:
        return sum(fn(s) for s in skels) + 8 * len(skels)
    return len(skels) * (getattr(em, "_SKEL_BYTES_PER_RECORD", 0) + 8)


def _weight_mix(n, rnd, prompt_words=300):
    """Synthetic records of every skeleton shape: the coding-session mix, long typed prompts, queued prompts and a compaction
    boundary with its preserved segment (invented text, placeholder ids)."""
    out = []
    for i in range(n):
        r = _rec(i, rnd)
        if i % 10 == 4:
            r["message"]["content"] = " ".join(rnd.choice(WORDS) for _ in range(prompt_words))
        if i % 50 == 7:
            r = {"type": "attachment", "uuid": r["uuid"], "parentUuid": r["parentUuid"], "timestamp": r["timestamp"],
                 "attachment": {"type": "queued_command", "prompt": " ".join(rnd.choice(WORDS) for _ in range(40))}}
        if i % 200 == 99:
            r = {"type": "system", "subtype": "compact_boundary", "uuid": r["uuid"], "parentUuid": None,
                 "logicalParentUuid": r["parentUuid"], "timestamp": r["timestamp"],
                 "compactMetadata": {"trigger": "auto", "preTokens": 9000,
                                     "preservedSegment": {"headUuid": r["parentUuid"], "anchorUuid": r["parentUuid"],
                                                          "tailUuid": r["parentUuid"]}}}
        out.append(r)
    return out


class SkeletonWeightIsWhatSkeletonsHold(Base):
    """The cache counts each skeleton by what it holds once its decoded record is freed (review find, 2026-10-07): the first
    flat charge, 600 bytes a record, was measured while the records were still alive, and the skeletons alone held 25 to 40
    percent more (ids, parent links, timestamps, message and tool ids, a user record's text), without bound for a long user
    text. The counted figure must stay within about 10 percent of tracemalloc's, measured after the records are freed."""

    def test_the_counted_weight_is_within_ten_percent_of_tracemalloc_after_the_records_are_freed(self):
        import gc
        for prompt_words in (12, 300, 2000):
            with self.subTest(prompt_words=prompt_words):
                lines = [json.dumps(r) for r in _weight_mix(20000, random.Random(prompt_words), prompt_words)]
                gc.collect()
                tracemalloc.start()
                try:
                    b0, _ = tracemalloc.get_traced_memory()
                    recs = [json.loads(l) for l in lines]        # decoded the way the scan decodes them
                    skels = [em._skel(r) for r in recs]
                    del recs                                       # the records leave memory: what stays is the skeletons
                    gc.collect()
                    held = tracemalloc.get_traced_memory()[0] - b0
                finally:
                    tracemalloc.stop()
                counted = _counted_skeleton_bytes(skels)
                self.assertLess(abs(counted - held) / held, 0.10,
                                "counted %d bytes for %d skeletons, tracemalloc holds %d (%.0f against %.0f a record)"
                                % (counted, len(skels), held, counted / len(skels), held / len(skels)))

    def test_a_tail_only_entrys_weight_carries_its_skeletons_and_a_long_prompt_by_its_length(self):
        with knobs(32 * 1024, 4, roots=[str(self.proj)]):
            path = self.leaf()
            rnd = random.Random(3)
            recs = _weight_mix(3000, rnd)
            recs[100] = dict(recs[100], type="user", message={"role": "user", "content": "q" * 1000000})   # a pasted megabyte
            Path(path).write_text("".join(json.dumps(r) + "\n" for r in recs))
            ent = em._read_jsonl_entry(path)
            t = ent[4]
            self.assertTrue(_tail(t) and t.ncold > 200)
            held = max(0, ent[1] - t.hot_offset())
            base = int(held * em.RECORD_CACHE_RESIDENT_PER_FILE_BYTE) + len(t.offs) * t.offs.itemsize + len(t.crcs) * t.crcs.itemsize
            skel_term = em._entry_weight(ent) - base
            self.assertEqual(skel_term, _counted_skeleton_bytes(t.skel[:t.ncold]),
                             "the entry's weight counts its skeletons as each holds")
            self.assertGreater(skel_term, 1000000, "the pasted megabyte before the window is counted by its length")
            stats = em.record_cache_stats()["tailOnly"]
            self.assertEqual((stats.get("skeletonBytes"), stats.get("skeletons")), (t.skel_bytes, t.ncold))


class TheFileRunsWhole(unittest.TestCase):
    """A direct run (python tests/test_record_cache_tail_only.py) runs every class: the main guard sat mid-file, so the seven
    classes after it never ran that way and the run still said OK (review find, 2026-10-07)."""

    def test_the_main_guard_is_the_last_statement(self):
        import ast
        tree = ast.parse(Path(__file__).read_text())
        guards = [i for i, n in enumerate(tree.body) if isinstance(n, ast.If) and "__main__" in ast.dump(n.test)]
        self.assertEqual(guards, [len(tree.body) - 1], "the main guard is the file's last top-level statement")



class TheAssemblyConvergePassOverATailOnlyLeaf(Base):
    """The kernel's assembly converge pass (kernel._converge_assembly, review find, 2026-10-07: only its predicate,
    entry_indexed_whole, was tested over a tail-only leaf). An idle leaf the boot parsed whole, held tail-only, gets its
    assembly document written by the pass from the entry in memory: no record read before the window, the entry stays
    tail-only, and the document restores. A planted control with the pass's old predicate (a whole RESIDENT entry) skips the
    leaf as having nothing to write from, so this test fails if the pass stops serving tail-only leaves."""

    def parse(self, path):
        return em.parse_session(path, rompuuid=SID, name="impl", dir="/TESTDIR", candidate_files=[path],
                                states=None, postal_log=[], now=NOW)

    def _pass(self, path):
        for name, val in (("CKPT_CONVERGE_MS", 5000.0), ("CKPT_CONVERGE_BYTES", em._CKPT_CYCLE_CAP_DEFAULT), ("ASM_CONVERGE", True)):
            saved = getattr(km, name); setattr(km, name, val); self.addCleanup(setattr, km, name, saved)
        km._ASM_CONVERGE_DONE.clear(); km._ASM_CONVERGE_BLIP.clear(); km._ASM_CONVERGE_NOENTRY.clear()
        em._ASM_CKPT_STATS["converge"] = {"writes": 0, "bytes": 0, "deferred": 0, "candidates": 0, "skipped": {}}
        km._begin_checkpoint_cycle()
        c0 = cold_records()
        n = km._converge_assembly(time.time(), time.monotonic())
        return n, cold_records() - c0, dict(em.asm_checkpoint_stats()["converge"])

    def _setup(self):
        path = self.leaf()
        recs = transcript(NOW - 86400, turns=160, compact_every=40)
        Path(path).write_text("".join(json.dumps(r) + "\n" for r in recs))
        old = time.time() - 600
        os.utime(path, (old, old))                     # idle past the quiescence window: the converge pass's leaf, not the settle's
        return path

    def test_the_converge_pass_writes_the_document_from_a_tail_only_entry_and_reads_nothing(self):
        path = self._setup()
        with knobs(16 * 1024, 4, roots=[str(self.proj)]):
            tree = self.parse(path)
            ent = em._JSONL_CACHE.get(path)
            self.assertTrue(ent is not None and _tail(ent[4]) and ent[5] == 0, "the boot's whole parse left a tail-only entry")
            self.assertFalse(em._asm_ckpt_file(path).exists())
            n, cold, conv = self._pass(path)
            self.assertEqual((n, conv["writes"]), (1, 1), "the pass wrote the leaf's document: %r" % conv)
            self.assertTrue(em._asm_ckpt_file(path).exists())
            self.assertEqual(cold, 0, "the pass read nothing before the window")
            self.assertTrue(_tail(em._JSONL_CACHE[path][4]), "the entry stayed tail-only")
            ref = _strip_tree(tree)
            fresh()
            restored = self.parse(path)
            em.hydrate(restored, SID)
            self.assertEqual(_strip_tree(restored), ref, "a fresh process restores from the pass's document")

    def test_planted_the_old_predicate_skips_the_leaf(self):
        path = self._setup()
        with knobs(16 * 1024, 4, roots=[str(self.proj)]):
            self.parse(path)
            saved = em.entry_indexed_whole
            em.entry_indexed_whole = em.entry_whole_resident          # the pass's predicate before tail-only entries
            self.addCleanup(setattr, em, "entry_indexed_whole", saved)
            n, cold, conv = self._pass(path)
            self.assertEqual(n, 0)
            self.assertEqual(conv["skipped"].get("noEntry"), 1, "the planted predicate finds nothing to write from: %r" % conv)
            self.assertFalse(em._asm_ckpt_file(path).exists())


if __name__ == "__main__":
    unittest.main()
