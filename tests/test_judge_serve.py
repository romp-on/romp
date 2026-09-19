"""The judges' own process: `romp-judge --serve` (stage three of the process split, plans/judges-process.md, the child's
side). A real child interpreter over a pipe, a synthetic transcript under a temp Claude root, a fake `claude -p` that
answers a fixed result envelope, and a temp state root: one `pass` through the child writes the stores an in-process pass
over the same fixture writes in a fresh interpreter, and the protocol's roads (ready, done, tracking off, malformed,
unknown op, busy, a raising tier, a pass that dies short of its done, quit, end of input) each answer as the protocol says;
the pass body is the one the kernel's producer runs (judge.py run_pass), pinned by the call on both sides, never a mirror.
The gate cases run the loop in this process over pipes, with a stand-in pass body whose thread outlives its done line, ends
without one, or is held short of it while more requests arrive. No real prompt or transcript text: every string here is
invented."""
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
# Hermetic state BEFORE the loads (the modules resolve their state root at import time; only pytest runs conftest's floor).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
em = load_source("romp_event_model_serve", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge_serve", os.path.join(BIN, "romp-judge"))

NOW = 1781100000
SID = "11111111-2222-4333-8444-000000000501"
T0 = NOW - 3600
ENVELOPE = os.path.join(HERE, "fixtures", "claude-p-result-envelope-standard.json")

FAKE_CLAUDE = r'''#!/usr/bin/env python3
"""A fake `claude -p` for the judge child's tests: one fixed result envelope, every call logged. Invented text only."""
import json, os, sys
log = os.environ.get("SERVE_TEST_CLAUDE_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(json.dumps(sys.argv[1:3]) + "\n")
if len(sys.argv) > 1 and sys.argv[1] in ("-v", "--version"):
    print("2.1.0 (fake)"); sys.exit(0)
sys.stdin.read()
import time
time.sleep(float(os.environ.get("SERVE_TEST_CLAUDE_SLEEP") or 0))
env = json.load(open(os.environ["SERVE_TEST_ENVELOPE"]))
env["result"] = "A short synthetic caption."
print(json.dumps(env))
'''


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "sessionId": SID, "cwd": "/TESTDIR",
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "sessionId": SID, "cwd": "/TESTDIR",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}}


RECORDS = [uline(T0, "please fix the flicker on the notes page", "u1"),
           aline(T0 + 30, "Fixed the flicker: the list re-rendered on every tick.", "a1", "u1"),
           uline(T0 + 300, "now add a test for it", "u2", "a1"),
           aline(T0 + 360, "Added a test that pins the render count.", "a2", "u2")]

STORE_DIRS = ("captions", "archive", "goals", "goals-archive", "episodes", "states", "names")


def _tree(state_root):
    """{relative path: content} of the judge stores under a state root, the volatile logs (errors, usage, scratch, the units
    cache keyed on this process) left out. An append log (.jsonl) compares as the SORTED tuple of its lines: its rows land in
    the order the tiers' pool workers finish, which differs between any two passes; the rows themselves must be the same."""
    out = {}
    for sub in STORE_DIRS:
        base = Path(state_root) / "romp" / sub
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file():
                data = p.read_bytes()
                out[str(p.relative_to(Path(state_root) / "romp"))] = tuple(sorted(data.splitlines())) if p.suffix == ".jsonl" else data
    return out


class _Child:
    """One `romp-judge --serve` child over a pipe, its stdout lines on a queue, its stderr kept."""
    def __init__(self, env, td):
        self.proc = subprocess.Popen([sys.executable, os.path.join(BIN, "romp-judge"), "--serve"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=td)
        self.lines = queue.Queue()
        self.err = []
        threading.Thread(target=self._pump, args=(self.proc.stdout, self.lines.put), daemon=True).start()
        threading.Thread(target=self._pump, args=(self.proc.stderr, self.err.append), daemon=True).start()

    @staticmethod
    def _pump(stream, sink):
        for line in iter(stream.readline, ""):
            sink(line)

    def send(self, obj):
        self.proc.stdin.write((json.dumps(obj) if not isinstance(obj, str) else obj) + "\n"); self.proc.stdin.flush()

    def line(self, timeout=120):
        """The next protocol line; fails at once when the child has exited with nothing queued (a base whose CLI knows no
        --serve prints its usage and exits 2), else after `timeout` seconds."""
        for _ in range(max(1, int(timeout / 0.25))):
            try:
                return json.loads(self.lines.get(timeout=0.25))
            except queue.Empty:
                if self.proc.poll() is not None and self.lines.empty():
                    break
        raise AssertionError("no protocol line (exit %r; stderr: %s)" % (self.proc.poll(), "".join(self.err)[-600:]))

    def close(self):
        if self.proc.poll() is None:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill(); self.proc.wait(timeout=10)


class Harness(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="romp-judge-serve-")
        self.addCleanup(shutil.rmtree, self.td, True)
        self.claude_root = os.path.join(self.td, "claude")
        cdir = os.path.join(self.td, "launchdir"); os.makedirs(cdir)
        munged = jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cdir))
        pdir = os.path.join(self.claude_root, "projects", munged); os.makedirs(pdir)
        with open(os.path.join(pdir, SID + ".jsonl"), "w") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in RECORDS))
        self.cdir = cdir
        self.fake = os.path.join(self.td, "fake_claude_p.py")
        with open(self.fake, "w") as fh:
            fh.write(FAKE_CLAUDE)
        os.chmod(self.fake, 0o755)
        self.children = []
        self.addCleanup(self._close_children)

    def _close_children(self):
        for c in self.children:
            c.close()
            if c.proc.poll() is None:
                c.proc.kill()

    def state_root(self, tag):
        root = os.path.join(self.td, "state-" + tag); os.makedirs(os.path.join(root, "romp", "names"))
        with open(os.path.join(root, "romp", "session-hosts"), "w") as fh:
            fh.write("off")
        with open(os.path.join(root, "romp", "names", SID), "w") as fh:
            fh.write("web\t%s\t#abcdef\n" % self.cdir)
        return root

    def env(self, root, **extra):
        env = dict(os.environ, XDG_STATE_HOME=root, CLAUDE_CONFIG_DIR=self.claude_root, ROMP_CLAUDE_BIN=self.fake,
                   SERVE_TEST_ENVELOPE=ENVELOPE, SERVE_TEST_CLAUDE_LOG=os.path.join(root, "claude-calls.log"),
                   ROMP_POSTAL_PORT=str(20000 + os.getpid() % 20000), ROMP_POSTAL_PEERS="0", ROMP_POSTAL_CLIENT_ONLY="1")
        for k in ("ROMP_STATE_DIR", "ROMP_MANAGER_PID", "ROMP_KERNEL_PORT", "ROMP_JUDGE_SERVE_FAULT"):
            env.pop(k, None)
        env.update(extra)
        return env

    def child(self, root, **extra):
        c = _Child(self.env(root, **extra), self.td)
        self.children.append(c)
        return c

    def calls(self, root):
        p = os.path.join(root, "claude-calls.log")
        return open(p).read().splitlines() if os.path.exists(p) else []


class ReadyLine(Harness):
    def test_the_child_announces_itself_with_the_protocol_and_judge_versions(self):
        root = self.state_root("ready"); c = self.child(root)
        ready = c.line()
        self.assertEqual(ready["op"], "ready")
        self.assertEqual(ready["pid"], c.proc.pid)
        self.assertEqual(ready["protocolVersion"], jd.PROTOCOL_VERSION); self.assertIsInstance(ready["protocolVersion"], int)
        self.assertEqual(ready["judgeVersion"], open(os.path.join(ROOT, "VERSION")).read().strip())
        c.send({"op": "quit"}); c.proc.wait(timeout=60)
        self.assertEqual(c.proc.returncode, 0)


class OnePass(Harness):
    def test_one_pass_through_the_child_writes_the_stores_an_in_process_pass_writes(self):
        root = self.state_root("child"); c = self.child(root)
        self.assertEqual(c.line()["op"], "ready")
        c.send({"op": "pass", "seq": 1, "now": NOW + 0.5, "mayStart": True})
        done = c.line()
        self.assertEqual((done["op"], done["seq"], done["tierStarts"]), ("done", 1, 2), done)
        self.assertGreaterEqual(done["wallMs"], 0.0); self.assertGreaterEqual(done["tierCpuMs"], 0.0); self.assertGreaterEqual(done["workerCpuMs"], 0.0)
        self.assertIsNone(done["failures"], done["failures"])
        self.assertIn("wholeReads", done["recordCache"]); self.assertIn("parse", done["asmCheckpoint"])
        self.assertIsInstance(done["recovered"], bool)
        self.assertGreaterEqual(done["parses"]["misses"], 1, "the pass parsed the transcript through the store: %r" % done["parses"])
        self.assertEqual(sorted(done["goalIo"]), sorted(jd.goal_io_stats()), "the goal-store I/O counters ride the line")
        c.send({"op": "quit"}); c.proc.wait(timeout=60); self.assertEqual(c.proc.returncode, 0)
        child_calls = self.calls(root)
        self.assertTrue(child_calls, "the pass reached the model road through the fake CLI")
        # the reference: the same fixture, the in-process pass (both tiers under one frame) in a FRESH interpreter over its own root
        ref = self.state_root("ref")
        prog = "\n".join([
            "import os, sys",
            "sys.path.insert(0, %r)" % HERE,
            "from romp_load import load_source",
            "jd = load_source('romp_judge_ref', %r)" % os.path.join(BIN, "romp-judge"),
            "frame = jd.begin_pass_frame()",
            "jd.run_index(now=%d)" % NOW,
            "jd.run_triage(now=%d)" % NOW,
            "jd.end_pass_frame(frame)",
        ])
        r = subprocess.run([sys.executable, "-c", prog], env=self.env(ref), cwd=self.td, capture_output=True, text=True, timeout=600)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertEqual(_tree(root), _tree(ref), "the child's stores are the in-process pass's, file for file")
        self.assertEqual(len(child_calls), len(self.calls(ref)), "the same number of model calls")
        self.assertTrue(all(l.startswith("romp-judge: ") for l in c.err if l.strip()), "every stderr line carries the prefix: %r" % c.err[:5])

    def test_may_start_false_or_absent_starts_no_tier_and_makes_no_call(self):
        """mayStart is the kernel's composite gate (the tracking switch, a live session, retries not paused), evaluated on the
        kernel side; the child gates on it alone, and an ABSENT field is false (round two: `tracking` absent read as true, so an
        omitted field judged, and the switch alone was one of the gate's three inputs)."""
        root = self.state_root("off"); c = self.child(root)
        self.assertEqual(c.line()["op"], "ready")
        c.send({"op": "pass", "seq": 7, "now": NOW, "mayStart": False})
        done = c.line()
        self.assertEqual((done["op"], done["seq"], done["tierStarts"], done["failures"]), ("done", 7, 0, None), done)
        c.send({"op": "pass", "seq": 8, "now": NOW})                                   # no field at all
        done = c.line()
        self.assertEqual((done["op"], done["seq"], done["tierStarts"]), ("done", 8, 0), "an absent mayStart is false (the base started both tiers)")
        c.send({"op": "pass", "seq": 9, "now": NOW, "mayStart": "yes"})                # not the boolean true
        self.assertEqual(c.line()["tierStarts"], 0, "only the boolean true starts a tier")
        self.assertEqual(self.calls(root), [], "no kernel-initiated model call")
        self.assertEqual(_tree(root).keys() - {"names/" + SID}, set(), "no store written")
        c.send({"op": "quit"}); c.proc.wait(timeout=60); self.assertEqual(c.proc.returncode, 0)

    def test_a_request_without_a_clock_runs_the_tiers_on_their_own_clock_the_kernels_default(self):
        """The request's `now` is optional (2026-09-18): absent or null, the tiers read their own clock during the pass, the
        in-process producer's behaviour and the kernel side's default; a number is the explicit variant, handed to both tiers
        truncated to the second. Green at the base too (the child never required the field), said as such: this pins the
        contract the kernel's default now relies on, and the source that passes the clock through only when it is a number."""
        root = self.state_root("noclock"); c = self.child(root)
        self.assertEqual(c.line()["op"], "ready")
        c.send({"op": "pass", "seq": 1, "mayStart": True})                                # no clock at all
        done = c.line()
        self.assertEqual((done["op"], done["seq"], done["tierStarts"], done["failures"]), ("done", 1, 2, None), done)
        self.assertGreaterEqual(sum(v["ran"] for k, v in done["tierGate"].items() if k != "stamps"), 1, "the tiers ran their stages on their own clock")
        c.send({"op": "pass", "seq": 2, "mayStart": True, "now": None})                   # an explicit null: the same
        done2 = c.line()
        self.assertEqual((done2["op"], done2["seq"], done2["tierStarts"]), ("done", 2, 2), done2)
        c.send({"op": "quit"}); c.proc.wait(timeout=60); self.assertEqual(c.proc.returncode, 0)
        import inspect
        src = inspect.getsource(jd._serve_pass)
        self.assertIn('now = int(now) if isinstance(now, (int, float)) and not isinstance(now, bool) else None', src, "a number is the explicit clock variant; anything else is the tiers' own clock")
        self.assertIn("run_pass(may_start, now=now, before_tier=_serve_fault)", src)

    def test_the_done_lines_counter_blocks_are_per_pass_deltas(self):
        """Round two (the kernel head's read): the blocks rode as cumulative process snapshots, which the kernel could not
        feed into its per-pass counters; the child keeps the previous snapshot and emits the difference, so a pass that
        does nothing answers all-zero blocks, and every numeric field of a block is the pass's own."""
        root = self.state_root("delta"); c = self.child(root)
        self.assertEqual(c.line()["op"], "ready")
        c.send({"op": "pass", "seq": 1, "now": NOW, "mayStart": True})
        first = c.line()
        self.assertEqual((first["op"], first["tierStarts"]), ("done", 2))
        c.send({"op": "pass", "seq": 2, "now": NOW, "mayStart": False})              # nothing runs: every delta is zero
        second = c.line()
        self.assertEqual((second["op"], second["seq"], second["tierStarts"]), ("done", 2, 0))
        c.send({"op": "pass", "seq": 3, "now": NOW, "mayStart": True})               # a WORKING third pass (round four): the
        third = c.line()                                                                 #  parse store is warm, so it restores nothing
        self.assertEqual((third["op"], third["seq"], third["tierStarts"]), ("done", 3, 2))

        def numbers(block, path=""):
            for k, v in block.items():
                if isinstance(v, dict):
                    yield from numbers(v, path + k + ".")
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    yield path + k, v
        gauges = {"recordCache": ("entries", "bytes", "budgetBytes", "countCap"), "asmCheckpoint": ("asmDocMemo",), "tierGate": ("stamps",)}
        # the tiers' gate counters ride the line too (2026-09-18: a flip's call count per pass had no gate figure to explain it): per tier,
        # ran/skipped/stamped/bypassed/incomplete/due_clock as deltas, the working first pass with runs, the idle second with none
        STAGES = ["close", "consolidate", "distill", "group", "plan", "unblock"]
        self.assertEqual(sorted(first.get("tierGate") or {}), sorted(STAGES + ["stamps"]), "the gate block, per stage, plus the stamps gauge (the base carried no tierGate)")
        for stage in STAGES:
            self.assertEqual(sorted(first["tierGate"][stage]), ["bypassed", "due_clock", "incomplete", "ran", "skipped", "stamped"], stage)
        self.assertGreaterEqual(sum(first["tierGate"][st]["ran"] for st in STAGES), 1, "the working pass ran stages under the gate: %r" % first["tierGate"])
        for name in ("recordCache", "asmCheckpoint", "parses", "goalIo", "tierGate"):     # the two blocks the base carried first,
            nonzero = [(k, v) for k, v in numbers(second.get(name) or {}) if v and k.split(".")[0] not in gauges.get(name, ())]
            self.assertEqual(nonzero, [], "%s: an idle pass reports a zero delta for every counter (the base reported the process totals)" % name)
        for name, keys in gauges.items():                                               # round three: the gauges ride as current
            for key in keys:
                self.assertEqual(second[name][key], first[name][key], "%s.%s is a gauge: the same current value on both passes, never a difference" % (name, key))
        self.assertGreater(second["recordCache"]["budgetBytes"], 0); self.assertGreater(second["recordCache"]["countCap"], 0)
        self.assertGreaterEqual(second["recordCache"]["entries"], 0)
        self.assertGreater(second["asmCheckpoint"]["asmDocMemo"]["capBytes"], 0, "a cap never reads zero on the second pass")
        for key in ("budgetBytes", "countCap"):                                          # the caps hold across a working pass too
            self.assertEqual(third["recordCache"][key], first["recordCache"][key])
        self.assertEqual(third["tierGate"]["stamps"], second["tierGate"]["stamps"], "the stamps held is a gauge: the same count on the idle and the warm pass")
        self.assertGreaterEqual(sum(third["tierGate"][st]["ran"] for st in STAGES), 1, "the warm third pass ran stages under the gate")
        self.assertEqual(third["asmCheckpoint"]["asmDocMemo"]["capBytes"], first["asmCheckpoint"]["asmDocMemo"]["capBytes"])
        first_restore = first["asmCheckpoint"]["restoreMs"].get("total", 0.0)
        self.assertGreater(first_restore, 0.0, "the first pass restored the fixture's document")
        self.assertLess(third["asmCheckpoint"]["restoreMs"].get("total", 0.0), first_restore,
                        "restoreMs is a cumulative counter, differenced: a warm third pass reads its own (near zero) restore time, not the "
                        "boot-to-now sum (round four; listed as a gauge it read the sum, %r against %r)" % (third["asmCheckpoint"]["restoreMs"], first["asmCheckpoint"]["restoreMs"]))
        self.assertEqual(second.get("parses"), {"misses": 0, "hits": 0}, "the parse store's misses and hits ride the line")
        self.assertGreaterEqual((first.get("parses") or {}).get("misses", 0), 1, "the working pass parsed through the store")
        self.assertEqual(sorted(second.get("goalIo") or {}), sorted(jd.goal_io_stats()))
        c.send({"op": "quit"}); c.proc.wait(timeout=60); self.assertEqual(c.proc.returncode, 0)

    def test_sigterm_mid_pass_exits_promptly_and_leaves_no_half_written_store(self):
        """A check the kernel seam relies on: the kernel ends the child by quit, then SIGTERM on its exit road (and a parent
        death signal at spawn); a child mid-pass must die at once on SIGTERM, its tier threads with it, and every store it
        was writing is either the old bytes or the new (the stores' atomic replace: a temp file written whole, then renamed).
        The kill lands in a pass that is WORKING (the fake CLI answers after half a second, so the tiers are between their
        model calls and their store writes), keyed on the first store file appearing and once at a late offset, and every
        store left behind parses. A leftover temp file beside the stores is EXPECTED when the kill lands between a temp write
        and its rename (2026-09-16: CI's Python 3.10 and 3.12 legs saw one); it is counted in the message, never a failure.
        The litter itself is a queued follow-up (a sweep at the child's start)."""
        def first_store(root, deadline=15.0):
            """Block until the first store file appears under the judge stores (the EVENT the kill keys on: the pass is writing),
            or the deadline passes; returns the path seen or None."""
            end = time.monotonic() + deadline
            while time.monotonic() < end:                                                # loop-ok: bounded by the deadline
                for sub in ("captions", "archive", "goals"):
                    base = Path(root) / "romp" / sub
                    if base.exists():
                        files = [p for p in base.rglob("*") if p.is_file()]
                        if files:
                            return str(files[0])
                time.sleep(0.005)
            return None

        for trigger in ("first-store", "late-0.6s"):
            with self.subTest(trigger=trigger):
                root = self.state_root("term-" + trigger); c = self.child(root, SERVE_TEST_CLAUDE_SLEEP="0.5")
                self.assertEqual(c.line()["op"], "ready")
                c.send({"op": "pass", "seq": 1, "now": NOW, "mayStart": True})
                if trigger == "first-store":
                    seen = first_store(root)                                               # the kill lands as the stores are being written
                    self.assertIsNotNone(seen, "a store file appeared while the pass ran")
                else:
                    time.sleep(0.6)                                                        # one late offset kept: inside the writing window
                t0 = time.monotonic(); c.proc.terminate()
                try:
                    c.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.fail("the child did not exit within 5 s of SIGTERM")
                self.assertLess(time.monotonic() - t0, 2.0, "prompt")
                self.assertEqual(c.proc.returncode, -15)
                temps, parsed, torn = [], 0, []
                for p in Path(root).rglob("*"):
                    if not p.is_file():
                        continue
                    if p.name.endswith(".tmp") or ".tmp." in p.name:
                        temps.append(p.name); continue                              # a temp write the kill cut short: expected
                    try:
                        if p.suffix == ".json":
                            json.loads(p.read_text() or "null"); parsed += 1
                        elif p.suffix == ".jsonl":
                            for line in p.read_text().splitlines():
                                if line.strip():
                                    json.loads(line)
                            parsed += 1
                    except ValueError as e:
                        torn.append((str(p.relative_to(root)), str(e)[:80]))
                self.assertEqual(torn, [], "no store is torn: each is the old bytes or the new (temp files beside them: %d)" % len(temps))
                self.assertGreaterEqual(parsed, 1 if trigger == "first-store" else 0, "every store left behind parses (the kill landed after the first write)")
                self.assertTrue(c.lines.empty(), "no done line for a killed pass")

    def test_stray_writes_to_file_descriptor_one_never_reach_the_channel(self):
        """Round two: the name swap (sys.stdout = stderr) left an os.write(1), a print to a captured stream and a child
        process inheriting fd 1 on the channel; fd 1 is dup2'd onto stderr for the process and the protocol goes to the
        saved descriptor. The test knob's stray shape writes all three from inside a tier."""
        root = self.state_root("stray"); c = self.child(root, ROMP_JUDGE_SERVE_FAULT="stray:index")
        self.assertEqual(c.line()["op"], "ready")
        c.send({"op": "pass", "seq": 1, "now": NOW, "mayStart": True})
        done = c.line()
        self.assertEqual((done["op"], done["seq"], done["tierStarts"], done["failures"]), ("done", 1, 2, None), done)
        c.send({"op": "quit"}); c.proc.wait(timeout=60); self.assertEqual(c.proc.returncode, 0)
        joined = "".join(c.err)
        for stray in ("stray line through file descriptor 1", "stray line through print", "stray line from a child process"):
            self.assertIn(stray, joined, "the stray landed on stderr")
        self.assertIn("romp-judge: stray line through print", joined, "a print carries the prefix")
        self.assertTrue(c.lines.empty(), "no stray line reached the protocol channel")

    def test_a_malformed_fault_knob_is_refused_loudly_and_ignored(self):
        """Round two: 'boom:index', 'sleep:index' and 'raise:nosuchtier' each matched positionally and were ignored in silence;
        the knob is parsed once at serve start, a value that is not one of its shapes is said on stderr and applies nothing."""
        for bad, why in (("boom:index", "not raise"), ("sleep:index", "not raise"), ("raise:nosuchtier", "no such tier"), ("sleep:index:soon", "wants seconds"),
                         ("garbage", "not raise")):                                           # round three: the shape before the tier
            with self.subTest(knob=bad):
                root = self.state_root("knob-" + bad.replace(":", "-")); c = self.child(root, ROMP_JUDGE_SERVE_FAULT=bad)
                self.assertEqual(c.line()["op"], "ready")
                c.send({"op": "pass", "seq": 1, "now": NOW, "mayStart": True})
                done = c.line()
                self.assertEqual((done["op"], done["tierStarts"], done["failures"]), ("done", 2, None), "no fault applied")
                c.send({"op": "quit"}); c.proc.wait(timeout=60)
                joined = "".join(c.err)
                self.assertIn("romp-judge: serve: ROMP_JUDGE_SERVE_FAULT ignored: ", joined, "said once on stderr (the base said nothing)")
                self.assertIn(why, joined)
                self.assertEqual(joined.count("ROMP_JUDGE_SERVE_FAULT ignored"), 1)


class Roads(Harness):
    def test_a_malformed_line_and_an_unknown_op_are_answered_and_the_loop_continues(self):
        root = self.state_root("bad"); c = self.child(root)
        self.assertEqual(c.line()["op"], "ready")
        c.send("this is not json")
        self.assertEqual(c.line(), {"op": "error", "seq": None, "reason": "malformed"})
        c.send("[1, 2, 3]")
        self.assertEqual(c.line(), {"op": "error", "seq": None, "reason": "malformed"})
        c.send({"op": "dance", "seq": 4})
        self.assertEqual(c.line(), {"op": "error", "seq": 4, "reason": "unknownOp"})
        c.send({"op": "pass", "seq": 5, "now": NOW, "mayStart": False})
        self.assertEqual(c.line()["seq"], 5, "the loop still answers a pass")
        c.send({"op": "quit"}); c.proc.wait(timeout=60); self.assertEqual(c.proc.returncode, 0)

    def test_a_pass_during_a_pass_is_refused_busy_and_never_queued(self):
        root = self.state_root("busy"); c = self.child(root, ROMP_JUDGE_SERVE_FAULT="sleep:index:2.0")
        self.assertEqual(c.line()["op"], "ready")
        c.send({"op": "pass", "seq": 1, "now": NOW, "mayStart": True})
        time.sleep(0.3)                                        # the first pass is inside its sleeping tier
        c.send({"op": "pass", "seq": 2, "now": NOW, "mayStart": True})
        first, second = c.line(), c.line()
        self.assertEqual(first, {"op": "error", "seq": 2, "reason": "busy"}, "the second request is refused at once")
        self.assertEqual((second["op"], second["seq"]), ("done", 1))
        c.send({"op": "pass", "seq": 3, "now": NOW, "mayStart": False})
        third = c.line()
        self.assertEqual((third["op"], third["seq"]), ("done", 3), "nothing was queued: no done for seq 2, the next pass answers")
        c.send({"op": "quit"}); c.proc.wait(timeout=60); self.assertEqual(c.proc.returncode, 0)

    def _serve_in_process(self, pass_body):
        """jd.serve on a thread of this process over two pipes, with `pass_body` in _serve_pass's place for the loop's life: the
        harness of the gate cases, which need a pass whose thread outlives its done line, one that ends without a done line,
        or one held short of it until the case releases it, and none of those shapes can be asked of the real pass body
        through a child process. A pump thread puts each answer line on a queue; the ready line is consumed here. Returns (send,
        answer, loop): send(obj) writes one request line, answer() parses the next answer line and fails the case when none
        arrives in 30 s, loop is the serve thread, which the case joins after its quit.
        The cleanups run in this order: the request pipe's write end closes and the loop is joined (the end of input ends a
        loop that a failed step left waiting); the loop's swaps of sys.stdout, sys.stderr and the event model's two stage
        providers are undone, the loop's pipe ends closed and the pump joined (the closed write end is its end of input);
        the patch is lifted; the fault knob, when the environment carried one, goes back. The knob is out of the environment
        for the run, so a runner's knob cannot reach the loop."""
        from unittest import mock
        r_in, w_in = os.pipe()
        r_out, w_out = os.pipe()
        inp, req_w = os.fdopen(r_in, "r"), os.fdopen(w_in, "w", buffering=1)
        out, ans_r = os.fdopen(w_out, "w", buffering=1), os.fdopen(r_out, "r")
        saved = (sys.stdout, sys.stderr, jd.em._SET_STAGE_FN[0], jd.em._READ_STAGE_FN[0])
        lines = queue.Queue()
        loop = threading.Thread(target=jd.serve, args=(inp, out), name="serve-under-test", daemon=True)
        pump = threading.Thread(target=_Child._pump, args=(ans_r, lines.put), daemon=True)

        def end_input():
            req_w.close()
            if loop.ident is not None:
                loop.join(30.0)

        def restore():
            sys.stdout, sys.stderr = saved[0], saved[1]
            jd.em.set_stage_provider(saved[2]); jd.em.set_read_stage_provider(saved[3])
            inp.close(); out.close()
            if pump.ident is not None:
                pump.join(30.0)
            ans_r.close()

        fault = os.environ.pop("ROMP_JUDGE_SERVE_FAULT", None)
        if fault is not None:
            self.addCleanup(os.environ.__setitem__, "ROMP_JUDGE_SERVE_FAULT", fault)   # registered first: it runs last
        patch = mock.patch.object(jd, "_serve_pass", pass_body); patch.start()
        self.addCleanup(patch.stop)                            # cleanups run last to first: end_input, restore, the patch, the knob
        self.addCleanup(restore)
        self.addCleanup(end_input)
        pump.start(); loop.start()

        def send(obj):
            req_w.write(json.dumps(obj) + "\n")

        def answer():
            try:
                return json.loads(lines.get(timeout=30))
            except queue.Empty:
                self.fail("the loop wrote no answer line within 30 s")
        self.assertEqual(answer()["op"], "ready")
        return send, answer, loop

    def test_a_pass_sent_on_its_predecessors_done_line_is_never_refused_busy_while_that_thread_exits(self):
        """The one-pass-at-a-time gate keys on the DONE LINE, the protocol's own end of a pass, never on the pass thread's
        exit. A thread is alive through its teardown after its last statement, and on a free-threaded interpreter, where no
        GIL holds the loop's thread behind the exiting one, that teardown outlives the done line by more than a request's
        round trip: the request that followed a done line read busy, and the kernel kills a child that answers anything but
        the pass's done. In process, with a pass body that lingers AFTER its done line, every interpreter shows the window,
        so a gate on the thread's liveness fails this case on a GIL build too."""
        def lingering_pass(req, emit):
            emit({"op": "done", "seq": req.get("seq"), "tierStarts": 0})
            time.sleep(0.5)                                    # the thread outlives its own done line, as a teardown does

        send, answer, loop = self._serve_in_process(lingering_pass)
        for seq in (1, 2, 3):                                  # each request follows its predecessor's done line at once
            send({"op": "pass", "seq": seq, "now": NOW, "mayStart": False})
            self.assertEqual(answer(), {"op": "done", "seq": seq, "tierStarts": 0},
                             "the pass that follows a done line answers with its own done (a gate on the thread's liveness "
                             "reads the exiting thread as busy)")
        send({"op": "quit"})
        loop.join(30.0)
        self.assertFalse(loop.is_alive(), "the loop ended on quit")

    def test_a_pass_that_dies_short_of_its_done_line_frees_the_child_for_the_next_pass(self):
        """The gate's second clause, the pass thread's liveness, covers the one case the done line cannot: a pass whose thread
        ended WITHOUT a done line (an exception out of _serve_pass) leaves the in-flight flag set, and its dead thread is what
        frees the child, so the next pass runs. With that clause dropped, the flag alone as the gate, the child reads busy for
        the rest of its life and the kernel kills it on the next answer. The wait is on the thread's exit itself, never a
        sleep: threading.excepthook, swapped for the run and restored after it, records the pass thread's exception and sets
        an Event, then that thread is joined; the exception is recorded, not printed, so the run stays quiet. The swap is
        process-wide, so an exception on any other thread in the window goes to the hook that was installed: it neither
        redirects the wait onto that thread nor fails this case."""
        from unittest import mock
        exited, seen, installed_hook = threading.Event(), [], threading.excepthook

        def record(args):                                      # runs on the dying thread, after its last statement
            if args.thread is None or args.thread.name != "serve-pass":
                installed_hook(args)                           # not the pass thread's: the runner's hook reports it
                return
            seen.append(args); exited.set()

        def dies_then_answers(req, emit):
            if req.get("seq") == 1:
                raise RuntimeError("the pass died short of its done line (invented)")
            emit({"op": "done", "seq": req.get("seq"), "tierStarts": 0})

        with mock.patch.object(threading, "excepthook", record):
            send, answer, loop = self._serve_in_process(dies_then_answers)
            send({"op": "pass", "seq": 1, "now": NOW, "mayStart": False})
            self.assertTrue(exited.wait(30.0), "the raising pass reached the unhandled-exception hook")
            seen[0].thread.join(30.0)
            self.assertFalse(seen[0].thread.is_alive(), "the pass thread exited: the liveness the clause reads")
            send({"op": "pass", "seq": 2, "now": NOW, "mayStart": False})
            self.assertEqual(answer(), {"op": "done", "seq": 2, "tierStarts": 0},
                             "the pass after one that died short of its done line answers with its own done (with the liveness "
                             "clause dropped it reads busy, and the child is latched busy for its life)")
            send({"op": "quit"})
            loop.join(30.0)
        self.assertFalse(loop.is_alive(), "the loop ended on quit")
        self.assertEqual([(a.exc_type, a.thread.name) for a in seen], [(RuntimeError, "serve-pass")],
                         "the one unhandled exception was the raising pass's, on the pass thread")

    def test_only_a_done_line_clears_the_gate_so_a_pass_refused_busy_leaves_the_running_one_alone(self):
        """The gate's other half: a pass is in flight from its request to its DONE line, and no other line ends it. The loop
        writes error lines while a pass runs (a second pass is answered busy, a bad request malformed or unknownOp), and a
        gate that any written line cleared would admit the request after the busy answer beside the running pass, two
        passes in one child. The pass body here is held short of its done line on an Event until the case releases it: the
        two passes sent meanwhile are both refused, the release answers the held pass, and the pass sent on that done line
        runs. Under a gate that every line clears, the third pass is admitted and answers nothing, so its assertion fails
        on the 30 s wait; the Event is set at cleanup, so no pass thread stays parked on it after a failed step."""
        gate, entered = threading.Event(), []

        def held_pass(req, emit):
            entered.append(req.get("seq"))
            gate.wait(60.0)                                    # released by the case, at the latest in its cleanup; the bound
                                                               #  ends a wait no cleanup reached
            emit({"op": "done", "seq": req.get("seq"), "tierStarts": 0})

        send, answer, loop = self._serve_in_process(held_pass)
        self.addCleanup(gate.set)
        send({"op": "pass", "seq": 1, "now": NOW, "mayStart": False})
        for seq in (2, 3):                                     # both arrive while pass 1 is short of its done line
            send({"op": "pass", "seq": seq, "now": NOW, "mayStart": False})
            self.assertEqual(answer(), {"op": "error", "seq": seq, "reason": "busy"},
                             "a pass sent while another is short of its done line is refused, a busy line already written or "
                             "not (a gate that any written line cleared admits this one beside the running pass)")
        gate.set()
        self.assertEqual(answer(), {"op": "done", "seq": 1, "tierStarts": 0}, "the release answers the held pass")
        send({"op": "pass", "seq": 4, "now": NOW, "mayStart": False})
        self.assertEqual(answer(), {"op": "done", "seq": 4, "tierStarts": 0}, "the pass sent on the done line runs")
        send({"op": "quit"})
        loop.join(30.0)
        self.assertFalse(loop.is_alive(), "the loop ended on quit")
        self.assertEqual(entered, [1, 4], "the refused passes never reached the pass body")

    def test_a_tier_that_raises_is_counted_and_the_pass_still_answers(self):
        root = self.state_root("raise"); c = self.child(root, ROMP_JUDGE_SERVE_FAULT="raise:triage")
        self.assertEqual(c.line()["op"], "ready")
        c.send({"op": "pass", "seq": 1, "now": NOW, "mayStart": True})
        done = c.line()
        self.assertEqual((done["op"], done["seq"], done["tierStarts"]), ("done", 1, 2))
        self.assertEqual(done["failures"]["count"], 1); self.assertIn("RuntimeError", done["failures"]["first"])
        c.send({"op": "pass", "seq": 2, "now": NOW, "mayStart": False})
        second = c.line()
        self.assertEqual((second["op"], second["seq"]), ("done", 2),
                         "the child is alive after a tier crash and takes the next pass (a busy line carries seq 2 too)")
        c.send({"op": "quit"}); c.proc.wait(timeout=60); self.assertEqual(c.proc.returncode, 0)
        joined = "".join(c.err)
        self.assertIn("romp-judge: judge tier triage:", joined)
        self.assertTrue(all(l.startswith("romp-judge: ") for l in c.err if l.strip()), "every stderr line carries the prefix")

    def test_the_end_of_input_ends_the_child_with_exit_zero(self):
        root = self.state_root("eof"); c = self.child(root)
        self.assertEqual(c.line()["op"], "ready")
        c.proc.stdin.close(); c.proc.wait(timeout=60)
        self.assertEqual(c.proc.returncode, 0)
        self.assertIn("romp-judge: serve: exiting", "".join(c.err))


class StaleTemps(Harness):
    """The follow-up of the SIGTERM tolerance (2026-09-16): a kill between a store's temp write and its rename left the temp file
    for the life of the state root. At its start the child removes every temp file whose name carries a writer pid that is no
    live process (the event, never an age); a live writer's temp, this process's, and a fixed-name temp (reused by the next
    write) stand. Red first: the planted temp files survive the base's start."""
    def test_the_child_sweeps_a_dead_writers_temp_files_at_its_start_and_leaves_live_and_fixed_ones(self):
        root = self.state_root("sweep"); romp = Path(root) / "romp"
        dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True).stdout.strip()
        self.assertTrue(dead.isdigit()); dead = int(dead)
        live = os.getpid()
        for sub in ("goals", "archive", "goals-archive", "judge-units-cache", "captions"):
            (romp / sub).mkdir(parents=True, exist_ok=True)
        planted_dead = [romp / "goals" / ("%s.json.tmp.%d.139812.7" % (SID, dead)),          # _publish_tmp
                        romp / "archive" / ("%s.json.tmp.%d.139812.8" % (SID, dead)),
                        romp / ("judge-limit.json.%d.7f3a.tmp" % dead),                       # _atomic_write_json
                        romp / ("planner-seen.jsonl.tmp.%d.7f3a" % dead),                     # the planner's rows
                        romp / "judge-units-cache" / ("abc.json.tmp.%d" % dead),              # the units cache
                        romp / "goals" / (".tmp-g1-%d-a1b2c3" % dead)]                        # a node's temp
        kept = [romp / "goals" / ("%s.json.tmp.%d.139812.9" % (SID, live)),                  # a live writer's temp
                romp / "judge-usage.jsonl.tmp",                                              # a fixed-name temp, reused
                romp / "judge-auth.tmp",
                romp / "goals" / (SID + ".json")]                                            # a store itself
        for p in planted_dead + kept:
            p.write_text("{}")
        c = self.child(root)
        self.assertEqual(c.line()["op"], "ready")
        c.send({"op": "quit"}); c.proc.wait(timeout=60); self.assertEqual(c.proc.returncode, 0)
        still = [str(p.relative_to(romp)) for p in planted_dead if p.exists()]
        self.assertEqual(still, [], "every dead writer's temp file is gone at the child's start (the base left them all)")
        for p in kept:
            self.assertTrue(p.exists(), "%s stands: a live writer's temp, a fixed-name temp or a store" % p.name)
        self.assertIn("romp-judge: serve: swept 6 stale temp files beside the stores", "".join(c.err))

    def test_the_sweep_is_by_the_writers_dead_pid_never_by_age(self):
        import inspect
        src = inspect.getsource(jd.sweep_stale_temps)
        self.assertIn("_pid_alive(pid)", src); self.assertNotIn("mtime", src); self.assertNotIn("time.time", src)
        self.assertEqual(jd.sweep_stale_temps([os.path.join(self.td, "no-such-dir")]), 0, "a missing directory is skipped")
        self.assertIn("swept = sweep_stale_temps()", inspect.getsource(jd.serve), "the sweep runs once at the child's start")

class Deltas(unittest.TestCase):
    def test_gauges_ride_as_current_values_and_counters_as_differences(self):
        """Round three: the delta differenced EVERY number, so a cache that shrank reported negative entries and bytes, the
        caps read zero from the second pass on and the last restore's timings went negative; the gauges of a block ride as
        their current values, the counters as differences, a new key whole."""
        prev = {"entries": 5, "bytes": 5000, "budgetBytes": 100, "countCap": 8, "inserts": 3, "wholeReads": {"a": {"count": 2, "bytes": 10}}}
        cur = {"entries": 2, "bytes": 1800, "budgetBytes": 100, "countCap": 8, "inserts": 4, "wholeReads": {"a": {"count": 3, "bytes": 15}, "b": {"count": 1, "bytes": 7}}}
        gauges = getattr(jd, "_SERVE_GAUGES", {})                                        # getattr: a copy at the round-two head reds on the numbers
        got = jd._serve_delta(prev, cur, *([gauges["recordCache"]] if gauges else []))
        self.assertEqual(got, {"entries": 2, "bytes": 1800, "budgetBytes": 100, "countCap": 8, "inserts": 1,
                               "wholeReads": {"a": {"count": 1, "bytes": 5}, "b": {"count": 1, "bytes": 7}}},
                         "a shrunk cache reads its size, the caps their value, the counters their difference (the base read entries -3, bytes -3200, budgetBytes 0)")
        asm_prev = {"restoreMs": {"total": 10.088}, "asmDocMemo": {"entries": 3, "bytes": 30, "capBytes": 99, "multiple": 10}, "parse": {"restore": 4}}
        asm_cur = {"restoreMs": {"total": 20.183}, "asmDocMemo": {"entries": 2, "bytes": 20, "capBytes": 99, "multiple": 10}, "parse": {"restore": 6}}
        got = jd._serve_delta(asm_prev, asm_cur, *([gauges["asmCheckpoint"]] if gauges else []))
        self.assertEqual(got, {"restoreMs": {"total": 10.095}, "asmDocMemo": {"entries": 2, "bytes": 20, "capBytes": 99, "multiple": 10}, "parse": {"restore": 2}},
                         "restoreMs accumulates since boot (the read saw 10.088, 20.183, 30.277 over three restores): a counter, differenced; "
                         "asmDocMemo a gauge, current (round four: listed as a gauge, restoreMs read the boot-to-now sum)")
        self.assertNotIn("restoreMs", (gauges or {}).get("asmCheckpoint", ()), "no cumulative counter in the gauge list")
        self.assertEqual(set(jd._SERVE_GAUGES), {"recordCache", "asmCheckpoint", "parses", "goalIo", "tierGate"}, "one gauge list per block")

    def test_the_fault_knob_names_the_shape_before_the_tier(self):
        self.assertEqual(jd._serve_fault_parse("garbage")[1][:44], "not raise:<tier>, sleep:<tier>:<seconds> or ")
        self.assertEqual(jd._serve_fault_parse("raise:nosuchtier")[1], "no such tier 'nosuchtier' (index or triage)")
        self.assertEqual(jd._serve_fault_parse("sleep:index:1.5"), (("sleep", "index", 1.5), None))
        self.assertEqual(jd._serve_fault_parse(""), (None, None))

    def test_the_prefixed_stream_never_doubles_a_prefix(self):
        import io
        raw = io.StringIO(); ps = jd._PrefixedStream(raw, "romp-judge: ")
        ps.write("romp-judge: already prefixed\n"); ps.write("plain\n"); ps.write("two "); ps.write("parts\nromp-judge: again\n")
        self.assertEqual(raw.getvalue(), "romp-judge: already prefixed\nromp-judge: plain\nromp-judge: two parts\nromp-judge: again\n",
                         "a line judge.py already prefixed is not prefixed twice (the base wrote romp-judge: romp-judge: ...)")


class Pins(unittest.TestCase):
    def test_main_dispatches_serve_and_the_usage_names_it(self):
        import inspect
        src = inspect.getsource(jd.main)
        self.assertIn('args[0] == "--serve"', src)
        self.assertIn("sys.exit(serve())", src)
        self.assertIn("--serve |", src, "the usage line names the arm")

    def test_one_pass_body_for_the_producer_and_the_child(self):
        """Round two: the child's pass was a COPY of the producer's body, pinned against itself, and had drifted three ways
        before it ever ran; both call the one function, and the pin is on the CALL on each side."""
        import inspect
        self.assertIn("res = run_pass(may_start, now=now, before_tier=_serve_fault)", inspect.getsource(jd._serve_pass))
        ksrc = open(os.path.join(ROOT, "kernel", "kernel.py")).read()
        self.assertIn("res = jd.run_pass(_tiers_may_start(tracking), before_tier=_tier_started)", ksrc, "the producer calls the same body")
        self.assertNotIn("def _run_tier(", ksrc, "no copy of the tier runner in the kernel")
        self.assertNotIn("jd.begin_pass_frame()", ksrc, "the frame is the body's")
        body = inspect.getsource(jd.run_pass)
        self.assertIn("frame = begin_pass_frame()", body); self.assertRegex(body, r"finally:\n\s+end_pass_frame\(frame\)")
        self.assertIn('with acc["lock"]:', inspect.getsource(jd._run_tier), "the CPU and the failures accumulate under the lock")
        self.assertIn('_set_stage("judge." + name)', inspect.getsource(jd._run_tier), "the tier threads carry their stage mark")
        self.assertIn("em.set_stage_provider(_serve_set_stage)", inspect.getsource(jd.serve), "the child installs its own provider")
        self.assertIn('may_start = req.get("mayStart") is True', inspect.getsource(jd._serve_pass), "the child gates on mayStart alone")
        self.assertIn("os.dup2(2, 1)", inspect.getsource(jd.serve), "fd 1 is stderr for the process")


if __name__ == "__main__":
    unittest.main()
