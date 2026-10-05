#!/usr/bin/env python3
"""The judge prompt experiment's harness (plans/judge-prompt-experiments.md; scripts/judge_experiment.py) against a fake
`claude -p` that answers by the judge it sees in the system prompt and by a marker the candidate arm's prompt carries. The
corpus builder reads synthetic roots as files and writes only under its destination; a store the live judges wrote is cut to
the ending's turn start and re-keyed to the ending id, so the arm plans the turn once and inherits nothing from after the
cut; the measures score only the ending's own cards; a failed call marks the row not comparable; the closer sees the goal
history; the prompt swap reaches the calls and is restored; the budget stops a run past a fifth over; the labeller reads tier
one from the journals and gates on its own STABILITY (road (b); agreement with the user's actions is reported, not gated);
the report writes counts only. Every string invented."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
SCRIPT = os.path.join(ROOT, "scripts", "judge_experiment.py")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()          # hermetic before any romp load
os.environ.pop("ROMP_STATE_DIR", None)
os.makedirs(os.path.join(os.environ["XDG_STATE_HOME"], "romp"), exist_ok=True)
Path(os.environ["XDG_STATE_HOME"], "romp", "session-hosts").write_text("off")
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))   # the shared event model an in-process arm must leave alone
PV = int(re.search(r"^PLACEMENTS_V = (\d+)", Path(ROOT, "kernel", "judge.py").read_text(), re.M).group(1))   # the version a
#   store must carry for the judges to read it as current (a store under any other is sealed); read from the source, so a
#   PLACEMENTS_V bump does not turn every seeded store here into a sealed one

T0 = 1_700_000_000
SIDS = ["11111111-2222-3333-4444-eeeeeeeeee%02d" % i for i in (1, 2)]

FAKE_CLAUDE = r'''#!/usr/bin/env python3
"""A fake `claude -p` for the harness tests: answers by the judge named in the system prompt and by the candidate marker; with
JE_TEST_PROSE set, a candidate-marked prompt gets a sentence of prose no parser accepts. Invented text only; a fixed cost.
It does NOT exercise authentication (it never resolves a key): it answers as logged in so it stands in for the judges'
shapes, never for the credential path. With JE_TEST_NOAUTH set it answers a 'Not logged in' error envelope on EVERY call, so
preflight_auth's not-logged-in road is driven the way the judges' own auth road would fail."""
import json, os, re, sys
SENSITIVE = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "OP_SERVICE_ACCOUNT_TOKEN")  # names _judge_env strips; the fake records PRESENCE, never a value
args = sys.argv[1:]
if args and args[0] in ("-v", "--version"):
    print("2.1.0 (fake)"); sys.exit(0)
if os.environ.get("JE_TEST_NOAUTH"):
    print(json.dumps({"type": "result", "subtype": "error", "is_error": True, "result": "Not logged in",
                      "session_id": None, "total_cost_usd": 0})); sys.exit(0)
if os.environ.get("JE_TEST_PLAINTEXT"):
    print("the server is down for maintenance"); sys.exit(0)   # NOT JSON: preflight's decode-failure branch quotes the plain text, never empty braces
sysp = args[args.index("--system-prompt") + 1] if "--system-prompt" in args else ""
user = sys.stdin.read()
cand = "CANDIDATE-MARK" in sysp
judge = ("labeller" if "You classify the final assistant message" in sysp else "closer" if "turn-end auditor" in sysp
         else "grouper" if "You are a grouper" in sysp else "planner" if "planner" in sysp[:120]
         else "unblocker" if "marked blocked" in sysp else "other")
import hashlib as _hl                                        # the 2026-09-22 PR 2022 review: input identity across builds up to the per-call security mark
_blob = sysp + "\n" + user
_mk = re.search(r"<[a-zA-Z_-]+ ([0-9a-f]{8})>", _blob)       # the section mark (_mark: 8 CSPRNG hex, fresh per call)
_norm = _blob.replace(_mk.group(1), "MARK") if _mk else _blob
raw_hash = _hl.sha256(_blob.encode()).hexdigest()[:16]
mark_norm = _hl.sha256(_norm.encode()).hexdigest()[:16]      # equal across builds when the input is identical up to the mark
log = os.environ.get("JE_TEST_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(json.dumps({"judge": judge, "candidate": cand, "head": sysp[:40], "goalHistory": "<goal-history" in user,
                             "menu": (re.search(r"<open-goals[^>]*>\n(.*?)\n</open-goals", user, re.S) or [None, ""])[1] if judge == "planner" else None,
                             "argv": args, "cwd": os.getcwd(),                            # review MED 2: the flags and the cwd the caller ran, observed
                             "credPresent": [k for k in SENSITIVE if os.environ.get(k)],  # which sensitive names ride the child env (presence, never a value)
                             "ambientKeyRode": os.environ.get("ANTHROPIC_API_KEY") == "synthetic-ambient-key",   # a yes/no, never the value
                             "rawHash": raw_hash, "markNorm": mark_norm}) + "\n")         # raw differs per call (the mark); mark-normalized is equal across builds
if os.environ.get("JE_TEST_KILL_ALL") and judge in ("planner", "closer", "unblocker"):   # kill EVERY arm call (none ever serves): no usage row lands, so the budget stop must price kills from the floor
    import signal as _sig
    os.kill(os.getpid(), _sig.SIGALRM); sys.exit(1)
_kte = os.environ.get("JE_TEST_KILL_PLANNER_THEN_EMPTY")       # <dir>: the first PLANNER call's first attempt KILLS, its re-sample serves an EMPTY envelope (result "", last_call_fail None)
if _kte and judge == "planner":                                # so killedCalls reads 1 (one real kill) where the old fak+retry-rec reads 2 (PR 2122 review medium 2)
    import signal as _sig
    _mk = os.path.join(_kte, "planner")
    if not os.path.exists(_mk):
        open(_mk, "w").write("killed"); os.kill(os.getpid(), _sig.SIGALRM); sys.exit(1)
    elif open(_mk).read() == "killed":
        open(_mk, "w").write("emptied")
        print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "duration_ms": 7, "duration_api_ms": 5,
                          "num_turns": 1, "result": "", "stop_reason": "end_turn", "session_id": "11111111-2222-4333-8444-555555555555",
                          "total_cost_usd": 0.01, "usage": {"input_tokens": 10, "output_tokens": 5}}))
        sys.exit(0)                                            # a served EMPTY reply on the re-sample: not a kill, not a pause
_killdir = os.environ.get("JE_TEST_KILL_FIRST")                # kill-first knob: the FIRST planner/closer call dies for its first N attempts (per judge; the test clears the dir between arms)
if _killdir and judge in ("planner", "closer"):                # like a timer kill (empty stdout, returncode -SIGALRM), so the retry's re-sample road is exercised. N per judge via JE_TEST_KILL_FIRST_<JUDGE> (default 1)
    import signal as _sig
    _thr = int(os.environ.get("JE_TEST_KILL_FIRST_%s" % judge.upper(), "1"))
    _marker = os.path.join(_killdir, judge)
    _n = int(open(_marker).read()) if os.path.exists(_marker) else 0
    if _n < _thr:                                              # kill the first call's first N attempts; the (N+1)th attempt and every later call serve
        open(_marker, "w").write(str(_n + 1))
        _alog = os.environ.get("JE_TEST_ALARM_LOG")
        if _alog:
            open(_alog, "a").write("%s %d\n" % (judge, _sig.alarm(0)))   # the perl alarm the arm set: 240 at the harness alarm, 120 if the alarm-raise is removed
        os.kill(os.getpid(), _sig.SIGALRM)                     # die with empty stdout: a timeout kill the retry re-samples
        sys.exit(1)                                            # unreached
m = re.search(r"<(turn|segment|message)[^>]*>\n(.*?)\n</(turn|segment|message)", user, re.S)
text = m.group(2) if m else user
flag = bool(re.search(r"i can also|which option|not done", text, re.I))
menu = re.search(r"<open-goals[^>]*>\n(.*?)\n</open-goals", user, re.S)
menu_has = bool(menu and re.search(r"^\s*\d+\. ", menu.group(1), re.M))
if cand and os.environ.get("JE_TEST_PROSE"):
    reply_text = "I would rather not say in the shape you asked for."
elif judge == "labeller":
    if os.environ.get("JE_TEST_UNSTABLE"):
        mo = re.search(r"no particular order: ([a-z, ]+?)\.", sysp)     # order-dependent on EVERY ending: label sends the canonical order
        order = mo.group(1).strip() if mo else ""                        # first (call A) then a shuffle (call B, never canonical), so A and B disagree always
        cls = "offer" if order == "offer, question, undone, finished" else "finished"
    else:
        cls = ("offer" if re.search(r"i can also", text, re.I) else "question" if re.search(r"which option", text, re.I)
               else "undone" if re.search(r"not done", text, re.I) else "finished")
    reply_text = json.dumps({"class": cls, "why": "synthetic"})
elif judge == "closer":
    if os.environ.get("JE_TEST_CLOSER_NOOP"):
        reply_text = json.dumps({"done": [], "block": []})     # a closer that files no verdict, so a minted top keeps NO done/block (the scored-boundary pin)
    else:
        reply_text = json.dumps({"done": [], "block": [{"goal": 1, "why": "the go-ahead is owed"}]} if (cand and flag)
                                else {"done": [{"goal": 1, "why": "delivered"}], "block": []})
elif judge == "planner":
    if os.environ.get("JE_TEST_PLANNER_MINT"):
        reply_text = json.dumps({"ops": [{"why": "a fresh ask this turn", "do": "mint", "text": "A turn-minted goal"}]})   # ALWAYS mint a new top (even on a non-empty menu), so an opener-less ending has an own-turn top with no verdict
    elif not menu_has:
        reply_text = json.dumps({"ops": [{"why": "the ask", "do": "mint", "text": "The synthetic goal"}]})
    elif cand and flag:
        reply_text = json.dumps({"ops": [{"why": "the go-ahead is owed", "do": "block", "goal": 1}]})
    else:
        reply_text = json.dumps({"ops": [{"why": "delivered", "do": "done", "goal": 1}]})
elif judge == "unblocker":
    reply_text = json.dumps({"verdicts": []})
elif judge == "grouper":
    reply_text = json.dumps({"ops": []})       # a valid no-op grouper reply (the harness disables the grouper; this is only
    #                                            reached when a pin's mutation re-enables it, and must not add a parse failure)
else:
    reply_text = json.dumps({"result": "ok"})
env = {"type": "result", "subtype": "success", "is_error": False, "duration_ms": 7, "duration_api_ms": 5, "num_turns": 1,
       "result": reply_text, "stop_reason": "end_turn", "session_id": "11111111-2222-4333-8444-555555555555",
       "total_cost_usd": 0.01, "usage": {"input_tokens": 10, "output_tokens": 5}}
print(json.dumps(env))
'''

ENDINGS = [  # (the user's ask, the assistant's last text, the class the heuristic must give it)
    ("please fix the flicker on the notes page", "Fixed the flicker: the list re-rendered on every tick. I can also add a test for it.", "offer"),
    ("pick the storage layout", "Two layouts fit. Which option do you prefer?", "question"),
    ("wire the export button", "Wired the button. The docs update is not done yet.", "undone"),
    ("add a test for the render count", "Added a test that pins the render count.", "finished"),
]

# a judge-shaped seed the way the live judges write one: run the judge module itself over the synthetic roots with the fake
JUDGE_WRITER = r'''
import json, os, sys
sys.path.insert(0, os.path.join(os.environ["JE_ROOT"], "tests"))
from romp_load import load_source
load_source("romp_event_model", os.path.join(os.environ["JE_ROOT"], "bin", "romp-event-model"))
jd = load_source("romp_judge_seed_writer", os.path.join(os.environ["JE_ROOT"], "bin", "romp-judge"))
now = int(os.environ["JE_NOW"])
for sid in os.environ["JE_SIDS"].split(","):
    fsid, path, anchor, name = next(x for x in jd.discover(now, window=10**9) if x[0] == sid)
    jd._plan_session(fsid, str(path), now)
    store = jd.load_goals(fsid)
    session = jd.parsed_session(fsid, [str(path)], now)
    turns = session.get("turns") or []
    seg_by_id = {seg["id"]: seg for t in turns for seg in jd._segs(t, store)}
    for t in turns:
        if not jd._turn_open(t, turns):
            jd._close_turn(store, t, seg_by_id=seg_by_id)
    jd.rollup_status(store, True, now=now)
    jd.save_goals(fsid, store)
print("ok")
'''


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(sid, t, text, uid, parent=None, blocks=False):
    content = [{"type": "text", "text": text}] if blocks else text
    return {"type": "user", "timestamp": iso(t), "uuid": uid, "parentUuid": parent, "sessionId": sid, "cwd": "/TESTDIR",
            "promptSource": "sdk" if blocks else "typed", "message": {"role": "user", "content": content}}


def aline(sid, t, text, uid, parent):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uid, "parentUuid": parent, "sessionId": sid, "cwd": "/TESTDIR",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}}


class Harness(unittest.TestCase):
    def setUp(self):
        self.assertTrue(os.path.exists(SCRIPT), "the harness script exists (the base has none)")
        self.je = load_source("romp_judge_experiment", SCRIPT)
        self.td = tempfile.mkdtemp(prefix="romp-je-")
        self.addCleanup(shutil.rmtree, self.td, True)
        # the "live" roots the corpus builder reads as files: two sessions, two endings each
        self.live = Path(self.td, "live-state")
        self.state = self.live / "romp"; self.claude = Path(self.td, "live-claude")
        for sub in ("names", "goals", "goals-archive", "overrides", "sdk", "states", "episodes"):
            (self.state / sub).mkdir(parents=True)
        (self.state / "session-hosts").write_text("off")
        self.cwd = os.path.join(self.td, "proj"); os.makedirs(self.cwd)
        self.pdir = self.claude / "projects" / self.je.munge(self.cwd); self.pdir.mkdir(parents=True)
        (self.claude / "settings.json").write_text(json.dumps({"apiKeyHelper": "echo synthetic-key"}))   # so corpora carry the helper the arm preflight requires (the fake does not exercise auth)
        self.texts = []
        for k, sid in enumerate(SIDS):
            recs, t, parent = [], T0 + k * 10000, None
            for j, (ask, answer, _cls) in enumerate(ENDINGS[k * 2:k * 2 + 2]):
                u, a = "u%d%d" % (k, j), "a%d%d" % (k, j)
                recs.append(uline(sid, t, ask, u, parent)); recs.append(aline(sid, t + 30, answer, a, u))
                parent = a; t += 600; self.texts.append(answer)
            (self.pdir / (sid + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in recs))
            (self.state / "names" / sid).write_text("web\t%s\t#abcdef\n" % self.cwd)
            (self.state / "overrides" / (sid + ".jsonl")).write_text(json.dumps({"node": sid + ":g9", "op": "clear", "src": "user", "why": "cleared from the feed", "t": T0 + 10**6}) + "\n")
        self.fake = os.path.join(self.td, "fake_claude_p.py")
        Path(self.fake).write_text(FAKE_CLAUDE); os.chmod(self.fake, 0o755)
        self.log = os.path.join(self.td, "calls.log")
        os.environ["JE_TEST_LOG"] = self.log
        self.addCleanup(lambda: os.environ.pop("JE_TEST_LOG", None))
        self.addCleanup(lambda: os.environ.pop("JE_TEST_PROSE", None))
        self.addCleanup(lambda: os.environ.pop("JE_TEST_NOAUTH", None))
        self.addCleanup(lambda: os.environ.pop("JE_TEST_PLAINTEXT", None))
        # review low c: the credentials module reads the real MANAGED settings path first; a managed helper on the box would
        # build every corpus without a settings.json and red the helper pins under unittest. Stub it to a nonexistent file so
        # the resolution sees no managed helper, restored (with the settings cache) in cleanup.
        cred = self.je._credentials()
        saved_msp = cred.managed_settings_path
        cred.managed_settings_path = lambda: os.path.join(self.td, "no-such-managed-settings.json")
        cred._SETTINGS_CACHE.clear()
        def _restore_msp():
            cred.managed_settings_path = saved_msp
            cred._SETTINGS_CACHE.clear()
        self.addCleanup(_restore_msp)

    # ── helpers ──
    def _tree_hash(self, root):
        h = hashlib.sha256()
        for p in sorted(Path(root).rglob("*")):
            if p.is_file():
                h.update(str(p.relative_to(root)).encode()); h.update(p.read_bytes())
        return h.hexdigest()

    def _corpus(self, per_class=10, name="corpus"):
        dest = os.path.join(self.td, name)
        m = self.je.build_corpus(self.state, self.claude, dest, per_class=per_class, now=T0 + 10**6)
        return dest, m

    def _calls(self, judge=None):
        rows = [json.loads(l) for l in Path(self.log).read_text().splitlines() if l.strip()] if os.path.exists(self.log) else []
        return [r for r in rows if judge is None or r["judge"] == judge]

    def _clear_log(self):
        Path(self.log).write_text("")

    def _judge_written_stores(self):
        """The live judges' own stores over the synthetic sessions (the judge module, the fake as its model): what a real root holds."""
        env = dict(os.environ, XDG_STATE_HOME=str(self.live), CLAUDE_CONFIG_DIR=str(self.claude), ROMP_CLAUDE_BIN=self.fake,
                   JE_ROOT=ROOT, JE_NOW=str(T0 + 10**6), JE_SIDS=",".join(SIDS), ROMP_POSTAL_CLIENT_ONLY="1")
        env.pop("ROMP_STATE_DIR", None)
        out = subprocess.run([sys.executable, "-c", JUDGE_WRITER], env=env, capture_output=True, text=True, timeout=600)
        self.assertEqual(out.returncode, 0, out.stderr[-1500:])
        stores = {sid: json.loads((self.state / "goals" / (sid + ".json")).read_text()) for sid in SIDS}
        self._clear_log()
        return stores

    def _ending(self, m, sid, turn):
        return [e for e in m["endings"] if e["session"] == hashlib.sha256(sid.encode()).hexdigest()[:12] and e["turn"] == turn][0]

    # ── the corpus ──
    def test_the_corpus_builder_writes_only_under_its_destination_and_classifies_each_ending(self):
        before = self._tree_hash(self.live), self._tree_hash(self.claude)
        dest, m = self._corpus()
        self.assertEqual(len(m["endings"]), 4)
        for k, sid in enumerate(SIDS):
            for j in range(2):
                self.assertEqual(self._ending(m, sid, j)["class"], ENDINGS[k * 2 + j][2], "each ending in its own class")
        self.assertEqual((self._tree_hash(self.live), self._tree_hash(self.claude)), before, "the live roots are read, never written")
        manifest = Path(dest, "manifest.json").read_text()
        for text in self.texts:
            self.assertNotIn(text[:24], manifest, "no transcript text reaches the manifest")
        # the manifest carries the build-time identity the measures need (store key, cwd, leaves); it is cache-only, kept out
        # of every repository by refuse_inside_repo, and the shared REPORT carries counts only (pinned in the report test)
        written = [str(p.relative_to(dest)) for p in Path(dest).rglob("*") if p.is_file()]
        self.assertTrue(all(w.startswith(("claude/", "state/")) or w == "manifest.json" for w in written), written)
        # the corpus carries ONLY the apiKeyHelper into its claude root (so the arm can authenticate), never the rest of the
        # live settings.json (personal settings and paths). The live root's helper is a USER helper, so it is copied
        self.assertTrue(Path(dest, "claude", "settings.json").is_file(), "the builder copied the live user apiKeyHelper into the corpus root")
        self.assertEqual(json.loads(Path(dest, "claude", "settings.json").read_text()), {"apiKeyHelper": "echo synthetic-key"},
                         "only the apiKeyHelper entry is copied, nothing else")
        eid = m["endings"][0]["id"]
        self.assertTrue(list(Path(dest, "claude", "projects").glob("*/%s.jsonl" % eid)), "each ending is its own truncated transcript")
        self.assertTrue(Path(dest, "state", "romp", "names", eid).exists(), "each ending has its names entry")
        self.assertEqual(Path(dest, "state", "romp", "session-hosts").read_text(), "off")
        self.assertEqual(m.get("skipped"), {"no-transcript": 0, "few-turns": 0, "unreadable-names-entry": 0, "parse-failed": 0, "store-unreadable": 0, "registry-unreadable": 0}, "skips are counted, never named")
        # the journal is cut at the turn's start: a row before it stays, in the ending's own re-keyed journal; a later one goes
        e0 = self._ending(m, SIDS[0], 0)
        (self.state / "overrides" / (SIDS[0] + ".jsonl")).write_text(
            json.dumps({"node": SIDS[0] + ":g9", "op": "followup", "t": e0["startT"] - 100}) + "\n"
            + json.dumps({"node": SIDS[0] + ":g9", "op": "clear", "src": "user", "why": "cleared from the feed", "t": e0["cutT"] + 5}) + "\n")
        dest2, m2 = self._corpus(name="corpus2")
        e0 = self._ending(m2, SIDS[0], 0)
        rows = [json.loads(l) for l in Path(dest2, "state", "romp", "overrides", e0["id"] + ".jsonl").read_text().splitlines()]
        self.assertEqual([(r["op"], r["node"]) for r in rows], [("followup", e0["id"] + ":g9")], "the pre-cut row, re-keyed to the ending; the later clear gone")
        inside = os.path.join(self.td, "repo", "sub"); os.makedirs(os.path.join(self.td, "repo", ".git")); os.makedirs(inside)
        with self.assertRaises(SystemExit, msg="a destination inside a git checkout is refused"):
            self.je.build_corpus(self.state, self.claude, inside, per_class=10)
        self.assertFalse(list(Path(inside).rglob("*")), "and nothing was written there")

    def test_the_selection_caps_each_class_spreads_across_sessions_and_prefers_endings_the_judges_completed(self):
        # a third session with three offers, newest last; the first session's offer (turn 0) is the only one the judges completed
        sid3 = "11111111-2222-3333-4444-eeeeeeeeee03"
        recs, t, parent = [], T0 + 50000, None
        for j in range(3):
            recs.append(uline(sid3, t, "ask %d" % j, "u3%d" % j, parent)); recs.append(aline(sid3, t + 30, "Done %d. I can also tidy the names." % j, "a3%d" % j, "u3%d" % j))
            parent = "a3%d" % j; t += 600
        (self.pdir / (sid3 + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in recs))
        (self.state / "names" / sid3).write_text("api\t%s\t#abcdef\n" % self.cwd)
        m_all = self._corpus(per_class=10, name="all")[1]
        self.assertEqual(sum(1 for e in m_all["endings"] if e["class"] == "offer"), 4, "one offer in the first session, three in the third")
        e0 = self._ending(m_all, SIDS[0], 0)
        self._live_store_with_done(SIDS[0], e0["startT"], e0["cutT"], [])
        m1 = self._corpus(per_class=1, name="one")[1]
        offers = [e for e in m1["endings"] if e["class"] == "offer"]
        self.assertEqual([(e["session"], e["turn"], e["tierOneEligible"]) for e in offers],
                         [(hashlib.sha256(SIDS[0].encode()).hexdigest()[:12], 0, True)],
                         "the cap holds one per class, and the eligible offer wins the slot over the three newer ones the judges never ruled on")
        m2 = self._corpus(per_class=2, name="two")[1]
        offers = sorted((e["session"], e["turn"]) for e in m2["endings"] if e["class"] == "offer")
        self.assertEqual(len(offers), 2)
        self.assertEqual(len({s for s, _ in offers}), 2, "two slots go to two sessions before a second ending of one: %r" % offers)
        self.assertTrue(all("tierOneEligible" in e and "startT" in e for e in m2["endings"]))

    def test_sdk_composer_prompts_end_turns_too(self):
        sid = SIDS[0]
        recs = [uline(sid, T0, "first ask", "u1", None, blocks=True), aline(sid, T0 + 30, "First answer.", "a1", "u1"),
                {"type": "user", "timestamp": iso(T0 + 40), "uuid": "t1", "parentUuid": "a1", "sessionId": sid, "cwd": "/TESTDIR",
                 "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}},
                aline(sid, T0 + 50, "Continued after the tool.", "a2", "t1"),
                {"type": "user", "timestamp": iso(T0 + 60), "uuid": "m1", "parentUuid": "a2", "sessionId": sid, "isMeta": True,
                 "message": {"role": "user", "content": "a skill's markdown nobody typed"}},
                uline(sid, T0 + 600, "second ask", "u2", "a2", blocks=True), aline(sid, T0 + 630, "Second answer.", "a3", "u2")]
        ends = self.je.turn_ends(recs)
        self.assertEqual([recs[i]["uuid"] for i in ends], ["a2", "a3"], "the tool result and the meta record end nothing; the composer's text-block prompt does")
        self.assertEqual(self.je.turn_start(recs, ends[0]), T0)
        self.assertEqual(self.je.turn_start(recs, ends[1]), T0 + 600)

    def test_the_registry_names_the_leaf_transcripts_too(self):
        sid = SIDS[0]; leaf = "11111111-2222-3333-4444-ffffffffff01"
        (self.state / "sdk" / (sid + ".json")).write_text(json.dumps({"sid": sid, "lastSid": leaf}))
        (self.state / "episodes" / (sid + ".jsonl")).write_text(json.dumps({"fsid": "11111111-2222-3333-4444-ffffffffff02"}) + "\n")
        (self.state / "states" / (sid + ".jsonl")).write_text(json.dumps({"resumeFork": {"from": "11111111-2222-3333-4444-ffffffffff03", "to": leaf}}) + "\n")
        fsids = getattr(self.je, "known_fsids", None)
        self.assertIsNotNone(fsids, "the builder reads the registry's transcripts (the base read the sid's own only)")
        self.assertEqual(fsids(self.state, sid), {sid, leaf, "11111111-2222-3333-4444-ffffffffff02", "11111111-2222-3333-4444-ffffffffff03"})
        recs = [uline(leaf, T0 + 90000, "after the clear", "u1"), aline(leaf, T0 + 90030, "Cleared and continued. Which option do you prefer?", "a1", "u1"),
                uline(leaf, T0 + 90600, "one more", "u2", "a1"), aline(leaf, T0 + 90630, "Done.", "a2", "u2")]
        (self.pdir / (leaf + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in recs))
        m = self._corpus(name="leaves")[1]
        h = hashlib.sha256(sid.encode()).hexdigest()[:12]
        self.assertEqual(sum(1 for e in m["endings"] if e["session"] == h), 4, "the leaf's two endings join the anchor's two under the same session")

    # ── the store cut ──
    def _live_store_with_done(self, sid, start_t, cut_t, later_ops):
        """A live store in the judges' own shape: one top the planner minted from the ending's turn (its prompt-run placement at the
        turn's start) and one older top, the closer's done on the older one at the cut, later verdicts, and the user's later
        gestures in the journal."""
        seg0 = "%s:%d:aaaaaaaa" % (sid, start_t - 5000); segp = "%s:%d:bbbbbbbb" % (sid, start_t); segl = "%s:%d:cccccccc" % (sid, cut_t + 3000)
        old = {"id": sid + ":g1", "text": "The older goal", "parentId": None, "t": start_t - 5000, "mt": cut_t + 5000, "nodeComplete": True,
               "blocked": False, "cleared": False, "doneWhy": "synthetic", "settledDone": True, "settledAt": cut_t + 9, "rolledUp": False,
               "trail": [seg0, segp, segl], "blockCheckT": cut_t + 700, "closerLookT": start_t - 4000,
               "log": [{"ev_t": start_t - 4000, "at": start_t - 3999, "src": "planner", "kind": "block", "why": "synthetic"},
                       {"ev_t": start_t, "at": cut_t + 9, "src": "closer", "kind": "done", "why": "synthetic"},
                       {"ev_t": cut_t + 4000, "at": cut_t + 4001, "src": "closer", "kind": "block", "why": "synthetic later"}]}
        new = {"id": sid + ":g2", "text": "The turn's own goal", "parentId": None, "t": start_t, "mt": cut_t + 10, "nodeComplete": True,
               "blocked": False, "cleared": False, "doneWhy": "synthetic", "trail": [segp],
               "log": [{"ev_t": start_t, "at": cut_t + 8, "src": "closer", "kind": "done", "why": "synthetic"}]}   # ev_t is the turn it closed (its start), at the arrival (the cut)
        sub_later = {"id": sid + ":g3", "text": "A sub of the later turn", "parentId": sid + ":g2", "t": cut_t + 3000, "trail": [segl], "log": []}
        store = {"rompUuid": sid, "seq": 3, "nodes": {old["id"]: old, new["id"]: new, sub_later["id"]: sub_later},
                 "status": {old["id"]: "completed", new["id"]: "completed"}, "placementsV": PV, "rev": 4, "lastNode": sub_later["id"],
                 "placements": {seg0: old["id"], segp + "#p": new["id"], segp: new["id"], segl: sub_later["id"]},
                 "closedTurns": ["%s:%d:dddddddd" % (sid, start_t - 4990), "%s:%d:eeeeeeee" % (sid, start_t), "%s:%d:ffffffff" % (sid, cut_t + 3000)],
                 "closedSig": {"%s:%d:dddddddd" % (sid, start_t - 4990): "x", "%s:%d:eeeeeeee" % (sid, start_t): "y"}}
        (self.state / "goals" / (sid + ".json")).write_text(json.dumps(store))
        (self.state / "overrides" / (sid + ".jsonl")).write_text("".join(json.dumps(o) + "\n" for o in later_ops))
        return store

    def test_the_store_copy_is_cut_at_the_turns_start_and_keyed_to_the_ending(self):
        """The reviews of the harness (2026-09-21): a filter on `t` kept every verdict (the logs carry ev_t and at); the cut at the
        turn's END kept the live planner's node from the ending's own turn and the closer's verdict on it; the seed kept the
        session id, so no placement matched the arm's segment ids and the planner re-planned the history; the sealing fields
        sealed the seeded top out of the menu and a twin was minted; gate stamps from after the cut hid a node from the
        unblocker. The copy is now the store as the judges held it when the turn opened, keyed for the arm."""
        sid = SIDS[0]
        m = self._corpus()[1]
        e = self._ending(m, sid, 0)
        start, cut = float(e["startT"]), float(e["cutT"])
        store = self._live_store_with_done(sid, start, cut, [])
        eid = e["id"]
        try:
            before = self.je.store_before(store, cut, start, eid)
        except TypeError:
            self.fail("store_before takes the turn's start and the ending id (the base cut at the turn's end under the session id)")
        self.assertEqual(before["rompUuid"], eid)
        self.assertNotIn(sid, json.dumps(before), "every id prefix is the ending's now")
        g1, g2 = before["nodes"].get(eid + ":g1"), before["nodes"].get(eid + ":g2")
        self.assertEqual(sorted(before["nodes"]), [eid + ":g1", eid + ":g2"], "the older top and the turn's own prompt-run node; the later sub is gone")
        self.assertEqual([ev["kind"] for ev in g1["log"]], ["block"], "the older top keeps its pre-turn block; the done filed at the cut and the later block are gone: %r" % g1["log"])
        self.assertEqual(g2["log"], [], "the turn's own node is kept by birth with an empty log; nothing the turn's judging wrote survives")
        for field in ("nodeComplete", "doneWhy", "settledDone", "settledAt", "rolledUp"):
            self.assertNotIn(field, g1, field)
        self.assertNotIn("blockCheckT", g1, "a gate stamp from after the cut is dropped"); self.assertIn("closerLookT", g1, "one from before stays")
        self.assertEqual(g1["trail"], ["%s:%d:aaaaaaaa" % (eid, start - 5000)], "the older top's trail: the segment before the turn, re-keyed; the turn's own segment is not its")
        self.assertEqual(g2["trail"], ["%s:%d:bbbbbbbb" % (eid, start)], "the turn's own node keeps the segment that minted it")
        self.assertEqual(sorted(before["placements"]), sorted(["%s:%d:aaaaaaaa" % (eid, start - 5000), "%s:%d:bbbbbbbb#p" % (eid, start)]),
                         "the older placement and the turn's prompt-run; the turn's own work-run placement dropped so the arm plans it once")
        self.assertEqual(before["closedTurns"], ["%s:%d:dddddddd" % (eid, start - 4990)]); self.assertEqual(list(before["closedSig"]), ["%s:%d:dddddddd" % (eid, start - 4990)])
        self.assertEqual(before["status"], {}); self.assertNotIn("lastNode", before, "the last node pointed at a dropped node")
        self.assertLessEqual(g1["mt"], cut)
        self.assertEqual(self.je.event_time({"ev_t": 5, "at": 9}), 5); self.assertEqual(self.je.event_time({"at": 9}), 9)
        self.assertEqual(self.je.id_epoch("%s:1700000000:abcdef12#p" % sid), 1700000000.0); self.assertIsNone(self.je.id_epoch("g1"))
        # through the builder: the manifest names the tops by suffix; it also carries the build-time store key so a later move
        # of the live session cannot change which store the measures read (the guard keys on the node's own unblocker verdict).
        # The store key is legitimate for the cache (the manifest is written under dest, which refuse_inside_repo keeps out of
        # every repository); the privacy boundary that matters is the shared REPORT, pinned in the report test to carry counts only.
        m2 = self._corpus(name="corpus-seeded")[1]
        e2 = self._ending(m2, sid, 0)
        self.assertEqual(e2["topsBefore"], ["g1", "g2"])
        self.assertEqual(e2["storeKey"], sid, "the manifest carries the ending's own store key for the measures to read the live store")

    def test_a_judge_written_store_re_keyed_plans_the_turn_once_and_mints_no_twin(self):
        """Executed by the reviewer on a store the live planner wrote: 0 seed keys matched, the planner ran 1, 2, 3 times for the
        three endings, and a twin top was minted beside the sealed seeded one. Now one planner call and no twin per ending per
        build, over the judges' own stores."""
        stores = self._judge_written_stores()
        self.assertTrue(all(st["nodes"] and len(st["placements"]) == 2 for st in stores.values()),
                        "the live judges wrote real stores: a node and a placement per turn: %r" % {k: (len(v["nodes"]), len(v["placements"])) for k, v in stores.items()})
        dest, m = self._corpus(name="corpus-judged")
        run_root = os.path.join(self.td, "runs")
        res = self.je.run_arm(dest, "current", None, run_root, None, self.fake, now=T0 + 10**6)
        planner = self._calls("planner")
        self.assertEqual(len(planner), 4 * 3, "one planner call per ending per build over four endings and three builds: %d" % len(planner))
        for eid, r in res["endings"].items():
            e = next(x for x in m["endings"] if x["id"] == eid)
            for build in r["builds"]:
                scored = [n for n, v in build.items() if v["scored"]]
                self.assertEqual(len(scored), 1, "the turn's own card, one per ending, no twin: %r" % build)
                self.assertEqual(len(build), 1, "no twin top beside the seeded one (the fake's same-titled mint lands on it): %r" % build)
        self.assertEqual(res["failures"], 0)

    # ── the arms and the measures ──
    def test_an_arm_runs_the_judges_on_copies_and_its_columns_read_the_endings_own_cards(self):
        dest, m = self._corpus()
        corpus_before = self._tree_hash(dest)
        run_root = os.path.join(self.td, "runs")
        cand = os.path.join(self.td, "candidate.json")
        Path(cand).write_text(json.dumps({"CLOSER_SYS": "CANDIDATE-MARK You are a turn-end auditor in a logging pipeline.",
                                          "PLAN_SYS": "CANDIDATE-MARK You are a planner in a logging pipeline."}))
        base = self.je.run_arm(dest, "current", None, run_root, None, self.fake, now=T0 + 10**6)
        cand_res = self.je.run_arm(dest, "candidate", cand, run_root, None, self.fake, now=T0 + 10**6)
        self.assertEqual(self._tree_hash(dest), corpus_before, "the corpus is copied, never written")
        mb = self.je.measure(m, base, self.state)
        self.assertEqual((mb["endings"], mb["flaps"], mb.get("comparable")), (4, 0, True),
                         "four endings, the deterministic fake gives identical builds (no flap), comparable: %r" % mb)
        self.assertGreater(mb["costUsd"], 0); self.assertEqual(mb["calls"], round(mb["costUsd"] / 0.01), "one fixed-cost row per call")
        # the arm's own columns: the candidate blocks the offer, both complete the finished thread; the leaks/false interrupts
        # scored against the user's gestures are pinned in the dedicated tests below (road (b): not against the class)
        finished = [e["id"] for e in m["endings"] if e["class"] == "finished"][0]
        offer = [e["id"] for e in m["endings"] if e["class"] == "offer"][0]
        self.assertEqual({v["column"] for v in cand_res["endings"][finished]["builds"][-1].values() if v["scored"]}, {"completed"})
        self.assertEqual({v["column"] for v in cand_res["endings"][offer]["builds"][-1].values() if v["scored"]}, {"needs_input"})
        self.assertEqual({v["column"] for v in base["endings"][offer]["builds"][-1].values() if v["scored"]}, {"completed"},
                         "the current prompt completes the offer (the placement the candidate is meant to block)")
        self.assertIsNone(base["stopped"])
        closer = self._calls("closer")
        self.assertTrue(closer and all(r["goalHistory"] for r in closer), "every closer call carried the goal-history section production sends: %r" % closer[:2])
        # the 2026-09-22 PR 2022 review, item 10: the pre-flight probe's cost note in the results record equals the fake's envelope
        self.assertEqual(base["preflightProbe"], {"cost": 0.01, "ms": 7, "sessionId": "11111111-2222-4333-8444-555555555555"},
                         "the probe's cost note is the fake's envelope: %r" % base.get("preflightProbe"))
        # the 2026-09-22 PR 2022 review, item 3 (+ PR 2040 low 1): the builds' inputs are IDENTICAL up to the per-call
        # security mark, made a fact PER BUILD-TRIPLE, not merely as an aggregate collapse (which would pass a build-dependent
        # byte confined to one ending). Every RAW input is unique (a fresh mark per call); and every distinct mark-normalized
        # input recurs a MULTIPLE of the build count, not exactly it: a bucket can hold SEVERAL endings that send a
        # byte-identical planner input (this fixture has buckets of six beside buckets of three), and each of them recurs once
        # per build. A byte that differs across the builds of ONE ending splits that ending's hashes into counts of one, which
        # is not a multiple of the build count, and reds this. (The property would also false-red if a crash or a parse retry
        # fired in only some builds, changing the per-ending call count; that is unreachable under the deterministic fake
        # because the comparable assertion above would red first on the failure.)
        import collections as _c
        base_planner = [r for r in self._calls("planner") if not r["candidate"]]
        raw = [r["rawHash"] for r in base_planner]
        self.assertEqual(len(set(raw)), len(raw), "the security mark makes every raw planner input unique per call: %d unique of %d" % (len(set(raw)), len(raw)))
        counts = _c.Counter(r["markNorm"] for r in base_planner)
        self.assertGreater(len(counts), 1, "several distinct planner inputs were observed: %d" % len(counts))
        self.assertTrue(all(v % 3 == 0 for v in counts.values()),
                        "every mark-normalized planner input recurs once per build (a multiple of the build count; a bucket may hold several endings); a build-dependent byte in one ending would split it: %r" % dict(counts))

    def test_seal_pre_cut_adopt_seals_pre_cut_and_leaves_the_turn_at_every_older_version(self):
        """The 2026-09-23 method change (manager's condition 1): a seed at each older PLACEMENTS_V (9-14) plans exactly the
        ending's OWN turn, with every pre-cut unit sealed. seal_pre_cut_adopt seals the units born BEFORE the cut (keyed on
        time, derivation-independent) and adopts the current version, so _plan_session runs no whole-store seal and only the
        turn's units (time >= cut) remain to plan. Stubbed jd so the contract is deterministic."""
        import types as _t
        session = {"turns": [[{"id": "s0"}, {"id": "s1"}, {"id": "s2"}, {"id": "s3"}, {"id": "s4"}]]}
        units = [("s0", "work", 30), ("s1", "work", 50), ("s2", "work", 70), ("s3", "work", 100), ("s4", "work", 120)]   # cut at 100: s0,s1,s2 pre-cut; s3,s4 the turn
        for v in (9, 10, 11, 12, 13, 14):
            store = {"placements": {"s0#work": "nodeX"}, "placementsV": v}   # s0 is a pre-cut unit ALREADY PLACED (a node id): the guard must LEAVE it, never overwrite it with None (PR 2099 review low 4)
            jd = _t.SimpleNamespace(PLACEMENTS_V=15)
            jd.episode_floor = lambda fsid: None
            jd._segs = lambda turn, store: turn
            jd.plan_units = lambda session, store, floor=None, lazy_text=True: units
            jd._unit_key = lambda seg, phase: "%s#%s" % (seg, phase)
            jd._placed_key = lambda placements, key, live=None, floor=None: placements.get(key) is not None   # placed = a non-None node id
            sealed = self.je.seal_pre_cut_adopt(jd, "fsid", session, store, 100)
            self.assertEqual(store["placementsV"], 15, "adopts the current version from %d, so _plan_session skips its seal" % v)
            self.assertEqual(sealed, 2, "exactly the two UNPLACED pre-cut units sealed (from version %d), the placed one not counted: %r" % (v, store["placements"]))
            self.assertEqual(store["placements"].get("s0#work"), "nodeX", "a placed pre-cut key KEEPS its node id (the guard skips it), never overwritten to None")
            self.assertIsNone(store["placements"].get("s1#work"), "an unplaced pre-cut unit sealed")
            self.assertIsNone(store["placements"].get("s2#work"), "an unplaced pre-cut unit sealed")
            self.assertNotIn("s3#work", store["placements"], "the turn unit at the cut is left to plan")
            self.assertNotIn("s4#work", store["placements"], "the turn unit after the cut is left to plan")

    def test_own_turn_plannable_count_counts_only_work_and_live_units(self):
        """The excuse fix (PR 2143): the count is only the units the ARM's planner plans with an UNCONDITIONAL model call from a
        sealable own-turn position (work / live). A `prompt` (opener) is the opener judge's; a `nudge` is resolve-or-noop whose
        own-turn instance is never sealed (so an unplanned one is one of the kernel's no-call roads, not a fault); a `delegation`
        drives no planner call in the arm (no courier runs). So work + delegation counts 1 (only work), a NUDGE-ONLY own turn
        counts 0 (excused), a work unit counts. The pre-fix code (phase != 'prompt') counted nudge and delegation: this reds it."""
        import types as _t
        session = {"turns": [[{"id": "s1"}, {"id": "s2"}, {"id": "s3"}, {"id": "s4"}]]}
        jd = _t.SimpleNamespace()
        jd.episode_floor = lambda fsid: None
        jd._segs = lambda turn, store: turn
        jd._unit_key = lambda seg, phase: "%s#%s" % (seg, phase)
        jd._placed_key = lambda placements, key, live=None, floor=None: False   # none placed
        store = {"placements": {}}
        jd.plan_units = lambda session, store, floor=None, lazy_text=True: [("s1", "prompt", 100), ("s2", "nudge", 100), ("s3", "work", 100), ("s4", "delegation", 100)]
        self.assertEqual(self.je.own_turn_plannable_count(jd, "fsid", session, store), 1, "only the work unit counts; prompt (opener), nudge and delegation (no courier in the arm) do not")
        jd.plan_units = lambda session, store, floor=None, lazy_text=True: [("s2", "nudge", 100)]
        self.assertEqual(self.je.own_turn_plannable_count(jd, "fsid", session, store), 0, "a nudge-only own turn counts zero: an unplanned own-turn nudge is a no-call road, not a fault")
        jd.plan_units = lambda session, store, floor=None, lazy_text=True: [("s4", "delegation", 100)]
        self.assertEqual(self.je.own_turn_plannable_count(jd, "fsid", session, store), 0, "a delegation-only own turn counts zero: the arm runs no courier, so it drives no planner call in any arm")
        jd.plan_units = lambda session, store, floor=None, lazy_text=True: [("s3", "work", 100), ("s3b", "live", 100)]
        jd._segs = lambda turn, store: [{"id": "s3"}, {"id": "s3b"}]
        session = {"turns": [[{"id": "s3"}, {"id": "s3b"}]]}
        self.assertEqual(self.je.own_turn_plannable_count(jd, "fsid", session, store), 2, "work and live both count: they reach plan_llm unconditionally")

    def test_a_minted_top_scores_by_the_seedstart_boundary_not_startt(self):
        """PR 2099 review low 3: a top the arm mints in the ending's own turn WITHOUT filing a done or block on it records as
        SCORED, by the seedStart cut boundary. For an opener-less ending startT is None, so reverting the boundary to startT
        would read the minted-no-verdict top as unscored: the pin reds that revert. A top the arm filed a verdict on scores
        regardless of birth."""
        now = 1000
        lo = 500                                               # seedStart for an opener-less ending (its previous turn's end); startT would be None
        minted = {"t": 600, "log": []}                          # born in the turn, no done/block filed
        self.assertTrue(self.je.top_scored(minted, lo, now), "a top minted in the turn (no verdict) scores by the seedStart boundary")
        self.assertFalse(self.je.top_scored(minted, None, now), "the revert to startT (None) drops the minted-no-verdict top: the pin")
        seed = {"t": 100, "log": []}                            # born before the turn, never touched
        self.assertFalse(self.je.top_scored(seed, lo, now), "a pre-turn top with no verdict does not score")
        filed = {"t": 100, "log": [{"kind": "done", "at": now}]}   # pre-turn, but the arm filed a done THIS build (at >= now)
        self.assertTrue(self.je.top_scored(filed, lo, now), "a top the arm filed a done/block on this build scores regardless of birth")
        old_verdict = {"t": 100, "log": [{"kind": "done", "at": now - 100}]}   # a seed verdict, before this pass
        self.assertFalse(self.je.top_scored(old_verdict, lo, now), "a seed's own earlier verdict is not the arm's filing")

    def test_a_silent_measured_judge_reads_not_comparable_and_is_named_in_the_table(self):
        """The comparability precondition (manager's condition 2): an arm in which any measured judge made ZERO calls is not
        comparable, whatever the failure count, and the table names the silent judge, so a silent planner can never pass."""
        dest, m = self._corpus()
        run_root = os.path.join(self.td, "runs-silent")
        base = self.je.run_arm(dest, "current", None, run_root, None, self.fake, now=T0 + 10**6)
        self.assertTrue(self.je.measure(m, base, self.state)["comparable"], "the real run is comparable to start")
        base["callsByJudge"] = {"placer": 5, "closer": 5, "unblocker": 5}   # a run where the PLANNER was silent (every seed sealed)
        mb = self.je.measure(m, base, self.state)
        self.assertFalse(mb["comparable"], "a silent planner is not comparable")
        self.assertEqual(mb["silentJudges"], ["planner"], "the placer is reported but not required (a conditional judge)")
        (Path(run_root) / "current" / "results.json").write_text(json.dumps(base))
        self.je.report(dest, run_root, str(self.state))
        table = (Path(run_root) / "table.md").read_text()
        self.assertIn("planner 0 calls", table, "the table names the silent measured judge: %s" % table)
        self.assertIn("not comparable", table)
        # an old/withdrawn results record with NO per-judge record cannot read comparable on an empty cell
        base.pop("callsByJudge", None)
        m2 = self.je.measure(m, base, self.state)
        self.assertFalse(m2["comparable"]); self.assertTrue(m2["noPerJudgeRecord"], "no callsByJudge -> not comparable, not a silent pass")

    def test_an_old_version_seed_is_planned_through_the_real_road_and_an_opener_less_ending_is_not_skipped(self):
        """End-to-end (manager round-one medium 1 + 2, round-two medium): a corpus seed at an OLD placements version is driven
        through the real _plan_session road the harness uses (the fake as the model), and the seal_pre_cut_adopt WIRING makes
        its turn plan the SAME as at the current version, version-independently (a mutant removing the call would seal it whole
        and red this). NOTE the seal HALF (which units it seals) is not pinned here: these fixture placements are written at the
        current derivation, so _placed_key dedups the pre-cut units and seal_pre_cut_adopt seals zero of them; the seal's own
        logic is pinned by the stubbed unit test above. Opener-less endings (startT None) with seedStart set are sealed by
        seedStart; with neither seedStart nor startT they are refused loudly (no-seed-cut), never sealed on cutT (there is no
        store-derived third leg: seedStart, else startT, else refuse)."""
        self._judge_written_stores()                       # populate the live 2-turn stores so the corpus carries pre-cut seeds
        dest, m = self._corpus(name="seeded")
        self.assertTrue(all("seedStart" in e for e in m["endings"]), "build_corpus records seedStart for the run-time seal (opener-less endings included)")
        seeds = list(Path(dest, "state", "romp", "goals").glob("*.json"))
        self.assertTrue(seeds, "a turn-1 ending carries a pre-cut seed store from its turn 0")
        def set_all(version):
            for f in seeds:
                d = json.loads(f.read_text()); d["placementsV"] = version; f.write_text(json.dumps(d))
        def planner_of(rr):
            res = self.je.run_arm(dest, "current", None, os.path.join(self.td, rr), None, self.fake, now=T0 + 10**6)
            self.assertTrue(self.je.measure(m, res, self.state)["comparable"], "comparable: %r" % res.get("callsByJudge"))
            return (res.get("callsByJudge") or {}).get("planner", 0)
        set_all(PV)
        base = planner_of("r-cur")
        self.assertGreater(base, 0, "the turn is planned at the current version")
        for v in (PV - 1, 9):
            set_all(v)
            self.assertEqual(planner_of("r-v%d" % v), base,
                             "an old-version (%d) seed plans the SAME turn (seal_pre_cut_adopt normalizes; the whole-store seal did not fire)" % v)
        # opener-less: startT None but seedStart set -> sealed and adopted by seedStart, not skipped
        mp = Path(dest, "manifest.json")
        def set_manifest(mutate):
            mm = json.loads(mp.read_text())
            for me in mm["endings"]:
                mutate(me)
            mp.write_text(json.dumps(mm))
        set_manifest(lambda me: me.__setitem__("startT", None))   # a continuation; seedStart stays as the seed's cut
        set_all(9)
        self.assertEqual(planner_of("r-openerless"), base,
                         "an opener-less ending (startT None, seedStart set) is sealed, adopted and its turn planned via seedStart, not skipped")

    def test_a_degenerate_opener_less_ending_with_no_previous_turn_is_refused_and_a_zero_planner_ending_is_not_comparable(self):
        """Round-two/four medium's degenerate leg + the per-ending precondition. An ending with no seed cut at all (startT None
        and no seedStart) is REFUSED loudly (a no-seed-cut failure that counts in FAILURE_KINDS) rather than sealed on cutT, is
        named in endingsUnplanned, and marks the arm not comparable. No third derivation: seedStart, else startT, else refuse."""
        self._judge_written_stores()
        dest, m = self._corpus(name="degen")
        seeded = [x for x in m["endings"] if Path(dest, "state", "romp", "goals", x["id"] + ".json").is_file() and x["turn"] == 1]
        self.assertTrue(seeded, "a turn-1 ending with a seed store")
        e = seeded[0]
        mp = Path(dest, "manifest.json"); mm = json.loads(mp.read_text())
        for me in mm["endings"]:
            if me["id"] == e["id"]:
                me["startT"] = None; me.pop("seedStart", None)               # startT None and no seedStart: no seed cut -> refused, never sealed on cutT
        mp.write_text(json.dumps(mm))
        res = self.je.run_arm(dest, "current", None, os.path.join(self.td, "r-degen"), None, self.fake, now=T0 + 10**6)
        self.assertIn(e["id"], res.get("endingsUnplanned") or [], "the refused ending is named unplanned: %r" % res.get("endingsUnplanned"))
        self.assertGreater(res.get("failures", 0), 0, "the no-seed-cut refusal counts as a failure")
        errs = [json.loads(l) for l in (Path(self.td, "r-degen", "current", "state", "romp", "judge-errors.jsonl")).read_text().splitlines()]
        self.assertTrue(any(r.get("err") == "no-seed-cut" for r in errs), "a no-seed-cut failure row was filed (never a cutT seal)")
        self.assertGreaterEqual((res.get("failuresByKind") or {}).get("no-seed-cut", 0), 1,
                                "no-seed-cut is in FAILURE_KINDS so it counts against comparability: %r" % res.get("failuresByKind"))
        self.assertFalse(self.je.measure(m, res, self.state)["comparable"], "a refused / zero-planner ending marks the arm not comparable")
        # the per-ending precondition also fires from the measure record alone (a withdrawn re-read)
        m2 = self.je.measure(m, {"arm": "x", "failures": 0, "buildsPerCard": 3, "callsByJudge": {"planner": 5, "closer": 5},
                                 "endingsUnplanned": ["abc"], "endings": {}}, self.state)
        self.assertFalse(m2["comparable"]); self.assertEqual(m2["endingsUnplanned"], ["abc"])
        # a CRASHED ending marks the arm not comparable on its own, even at failures 0 (the comparable term, not just failures)
        m3 = self.je.measure(m, {"arm": "x", "failures": 0, "buildsPerCard": 3, "callsByJudge": {"planner": 5, "closer": 5},
                                 "endingsCrashed": ["def"], "endings": {}}, self.state)
        self.assertFalse(m3["comparable"], "a crashed ending alone marks the arm not comparable"); self.assertEqual(m3["endingsCrashed"], ["def"])

    def test_annotate_fills_seed_start_from_start_t_on_an_old_manifest(self):
        """The annotate step (round four): a manifest built before seedStart existed is filled by build_corpus's rule,
        seedStart = startT for an opener'd ending; an opener-less ending (startT None) is left null so the run refuses it."""
        self._judge_written_stores()
        dest, m = self._corpus(name="annot")
        mp = Path(dest, "manifest.json"); mm = json.loads(mp.read_text())
        for me in mm["endings"]:
            me.pop("seedStart", None)                          # an older manifest: no seedStart at all
        mm["endings"][0]["startT"] = None                      # one opener-less ending stays null (refused at run, not guessed)
        mp.write_text(json.dumps(mm))
        withstart = sum(1 for me in mm["endings"] if me.get("startT") is not None)
        self.assertEqual(self.je.annotate_seed_start(dest), withstart, "every opener'd ending gets seedStart = startT")
        after = json.loads(mp.read_text())["endings"]
        self.assertTrue(all(me.get("seedStart") == me.get("startT") for me in after), "seedStart equals startT after annotate (null stays null)")
        # a re-run writes NOTHING: the bytes are identical, not a reformat
        b1 = mp.read_bytes()
        self.assertEqual(self.je.annotate_seed_start(dest), 0, "a re-run is a no-op")
        self.assertEqual(mp.read_bytes(), b1, "a re-run leaves the manifest byte-for-byte")
        # a failed write leaves the ORIGINAL manifest intact (atomic temp + os.replace)
        json.loads(b1)                                          # sanity: b1 is valid
        d = json.loads(mp.read_text()); d["endings"][0].pop("seedStart", None); mp.write_text(json.dumps(d, indent=1))
        before = mp.read_bytes()
        saved_replace = self.je.os.replace
        self.je.os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
        try:
            with self.assertRaises(OSError):
                self.je.annotate_seed_start(dest)
        finally:
            self.je.os.replace = saved_replace
        self.assertEqual(mp.read_bytes(), before, "a failed os.replace leaves the original manifest intact")
        # a corpus inside a git checkout is refused (a mistyped --corpus cannot overwrite a repo manifest)
        with self.assertRaises(SystemExit):
            self.je.annotate_seed_start(ROOT)

    def test_the_measure_window_keys_on_seed_start_not_start_t(self):
        """Round-four low: placement_gestures and tier_one_label key their turn window on seedStart, not startT, so an
        opener-less ending (startT None) is not structurally empty. A needs_input top the live judges placed in [seedStart, cut)
        that the user crossed off after the cut is a false interrupt; a revert to startT (None) empties the window and it
        vanishes."""
        sid = SIDS[1]
        e = self._ending(self._corpus(name="win")[1], sid, 0)
        s, c = float(e["startT"]), float(e["cutT"])
        self._live_store_with_done(sid, s, c, [{"node": sid + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": c + 600}])
        e2 = dict(e); e2["startT"] = None; e2["seedStart"] = s        # opener-less: the window must come from seedStart
        results = {"arm": "x", "failures": 0, "callsByJudge": {"planner": 3, "closer": 3},
                   "endings": {e2["id"]: {"builds": [{e2["id"] + ":g1": {"column": "needs_input", "scored": True}}] * 2}}}
        mm = self.je.measure({"endings": [e2]}, results, self.state)
        self.assertEqual((mm["falseInterrupts"], mm["gesturedEndings"]), (1, 1),
                         "placement_gestures keys the window on seedStart: the cross-off is scored despite startT None: %r" % mm)
        # tier_one_label reads the same window from the start it is passed (the measure passes seedStart)
        self.assertIsNotNone(self.je.tier_one_label(self.state, sid, c, s), "tier_one_label finds the post-cut gesture via seedStart")
        self.assertIsNone(self.je.tier_one_label(self.state, sid, c, None), "with no start there is no window")

    def test_label_reads_tier_one_through_the_seed_start_window_for_an_opener_less_ending(self):
        """Round-four low: label()'s call site passes seedStart (not startT) to tier_one_label, so an opener-less ending's tier
        one is read from [seedStart, cut). The revert-to-startT mutant at the label() site empties the window and drops tierOne."""
        sid = SIDS[0]
        dest, m = self._corpus(name="lblwin")
        e = self._ending(m, sid, 0)
        s, c = float(e["startT"]), float(e["cutT"])
        self._live_store_with_done(sid, s, c, [{"node": sid + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": c + 600}])
        mp = Path(dest, "manifest.json"); mm = json.loads(mp.read_text())
        for me in mm["endings"]:
            if me["id"] == e["id"]:
                me["startT"] = None; me["seedStart"] = s        # opener-less: label() must key tier one on seedStart
        mp.write_text(json.dumps(mm))
        run_root = os.path.join(self.td, "lbl")
        self.je.label(dest, run_root, str(self.state), claude_bin=self.fake)
        labels = json.loads((Path(run_root) / "labels.json").read_text())
        row = [r for r in labels if r["id"] == e["id"]][0]
        self.assertIsNotNone(row.get("tierOne"), "label() reads tier one via the seedStart window for an opener-less ending: %r" % row)

    def test_evict_document_and_the_label_loop_keep_the_event_model_caches_flat(self):
        """The 2026-09-24 memory fix: the label/arm loops parse many one-shot documents, and neither event-model cache's own
        bound helps, so they grow to the cap. evict_document drops a document from both caches, and the label loop calls it per
        ending so the caches stay flat across the corpus instead of one entry per ending."""
        em = self.je._event_model()
        # the function: parsing then evicting a document leaves both caches as they were
        em._JSONL_CACHE.clear(); em._ASM_CACHE.clear()
        for i in range(6):
            sid = "evi%d" % i
            p = self.pdir / (sid + ".jsonl")
            p.write_text("".join(json.dumps(r) + "\n" for r in [uline(sid, T0, "ask%d" % i, "u1"), aline(sid, T0 + 30, "ok", "a1", "u1")]))
            em.parse_session(str(p), rompuuid=sid)
        self.assertGreaterEqual(len(em._ASM_CACHE), 6, "without eviction the assembly cache holds one per document")
        em._JSONL_CACHE.clear(); em._ASM_CACHE.clear()
        peak = 0
        for i in range(6):
            sid = "evi%d" % i; p = self.pdir / (sid + ".jsonl")
            em.parse_session(str(p), rompuuid=sid); em.evict_document(str(p))
            peak = max(peak, len(em._ASM_CACHE))
        self.assertLessEqual(peak, 1, "evict_document keeps the assembly cache flat across documents: peak %d" % peak)
        self.assertEqual((len(em._ASM_CACHE), len(em._JSONL_CACHE)), (0, 0), "both caches empty after evicting every document")
        # eviction goes through the counted pops, not a bare dict clear: the record BYTE weight returns (a bare dict pop would
        # leave _JSONL_CACHE_BYTES stale) and the assembly index is RELEASED (a dropped _asm_release never gives the atoms back).
        # The test's bare clears above leave the byte counter stale, so set it to a known floor first (review 2026-09-24).
        em._JSONL_CACHE.clear(); em._ASM_CACHE.clear(); em._JSONL_CACHE_BYTES[0] = 0
        sid = "byt0"; p = self.pdir / (sid + ".jsonl")
        p.write_text("".join(json.dumps(r) + "\n" for r in [uline(sid, T0, "a longer ask so the record has weight", "u1"), aline(sid, T0 + 30, "ok done", "a1", "u1")]))
        em.parse_session(str(p), rompuuid=sid)
        self.assertGreater(em.record_cache_stats()["bytes"], 0, "the parse held record bytes")
        em.evict_document(str(p))
        self.assertEqual(em.record_cache_stats()["bytes"], 0, "evict_document returns the record byte weight through _cache_pop_locked (a bare dict pop leaves it stale)")
        # a dropped release: an assembly entry carrying a lazy index must have it RELEASED when the entry is popped (a whole
        # parse's entry has no index, so drive it with a stub index keyed to the leaf; a bare dict pop would skip the release)
        released = []
        em._ASM_CACHE.clear()
        em._ASM_CACHE["k-release"] = {"path": str(p), "cands": (), "index": type("_Ix", (), {"release": lambda self: released.append(1)})()}
        em.evict_document(str(p))
        self.assertEqual(released, [1], "evict_document releases the popped assembly entry's index (a dropped _asm_release skips it)")
        self.assertNotIn("k-release", em._ASM_CACHE, "and pops the entry")
        # the label loop calls it per ending (the call site): both caches stay flat over a real corpus, not one-per-ending
        dest, m = self._corpus(name="evictloop")
        em._JSONL_CACHE.clear(); em._ASM_CACHE.clear()
        self.je.label(dest, os.path.join(self.td, "lbl-evict"), str(self.state), claude_bin=self.fake)
        self.assertLessEqual(len(em._ASM_CACHE), 1, "the label loop evicts each ending: assembly cache flat, not %d of %d" % (len(em._ASM_CACHE), len(m["endings"])))

    def test_an_excused_ending_is_still_scored_and_a_strict_subset_silence_is_not_comparable(self):
        """The cross-arm rule as REVERSED by the manager (2026-09-24): an ending unplanned in EVERY arm whose own turn had no
        planner unit (plannableUnits 0) is excused from the COMPARABILITY verdict only, never from the metrics. Its scored top
        still counts a leak and a flap: every excused ending sits in every arm's results, so the arms compare on one set without
        a skip, and the skip would drop the closer's verdicts the candidates exist to change. An ending unplanned in a STRICT
        SUBSET of arms still marks that arm not comparable."""
        dest, m = self._corpus(name="xarm")
        e_leak = self._ending(m, SIDS[0], 0)["id"]           # the excused ending; its top is completed then re-opened -> a leak
        e_one = self._ending(m, SIDS[1], 0)["id"]            # unplanned in A only -> a strict-subset silence
        el = self._ending(m, SIDS[0], 0)
        self._live_store_with_done(SIDS[0], float(el["startT"]), float(el["cutT"]),
                                   [{"node": SIDS[0] + ":g1", "op": "followup", "t": float(el["cutT"]) + 7200}])   # a re-open after the cut
        leak_builds = [{"g1": {"column": "completed", "scored": True}},   # majority completed (a leak) with a per-build disagreement (a flap)
                       {"g1": {"column": "completed", "scored": True}},
                       {"g1": {"column": "needs_input", "scored": True}}]
        run_root = os.path.join(self.td, "runs-xarm")
        os.makedirs(run_root)
        Path(run_root, "arms.json").write_text(json.dumps({"arms": ["A", "B", "C"]}))
        def write_arm(arm, unplanned, leak=False):
            os.makedirs(os.path.join(run_root, arm))
            endings = {}
            for x in m["endings"]:
                d = {"class": x["class"], "builds": [{}] * 3}
                if x["id"] == e_leak:
                    d["plannableUnits"] = 0                  # the own turn had nothing for the planner: excused (comparability only)
                    if leak:
                        d["builds"] = leak_builds
                        d["retry"] = {"firstAttemptKills": 1, "retryAttempts": 1, "recoveredCalls": 1, "killedCalls": 1}   # a per-ending retry record: the measure carries it into the attribution row (PR 2122 review point 4)
                endings[x["id"]] = d
            Path(run_root, arm, "results.json").write_text(json.dumps(
                {"arm": arm, "failures": 0, "buildsPerCard": 3, "finished": True, "callsByJudge": {"planner": 5, "closer": 5},
                 "endingsUnplanned": unplanned, "endings": endings}))
        write_arm("A", [e_leak, e_one], leak=True)           # A carries the excused ending's scored, leaking, flapping top
        write_arm("B", [e_leak])
        write_arm("C", [e_leak])
        rows = self.je.report(dest, run_root, str(self.state))
        by = {r["arm"]: r for r in rows}
        self.assertEqual(by["A"]["excusedEndings"], [e_leak], "the corpus-wide-unplanned ending with no planner unit is excused")
        self.assertEqual((by["A"]["leaks"], by["A"]["flaps"]), (1, 1), "the excused ending is STILL scored: its re-opened top is a leak and its per-build disagreement a flap")
        self.assertEqual(by["A"]["endings"], len(m["endings"]), "every ending is in the count; the excuse removes none")
        self.assertFalse(by["A"]["comparable"], "A has an ending the others planned (e_one): not comparable")
        self.assertTrue(by["B"]["comparable"] and by["C"]["comparable"], "B and C's only unplanned is the excused one: comparable")
        arow = [a for a in by["A"]["attribution"] if a["id"] == e_leak][0]   # the measure carries the ending's per-ending retry into its attribution row (PR 2122 review point 4)
        self.assertEqual((arow.get("retry") or {}).get("killedCalls"), 1, "the attribution row carries the ending's own retry record")
        table = (Path(run_root) / "table.md").read_text()
        self.assertIn("corpus-unplanned", table)
        self.assertIn("complete: all 3 expected arms finished", table)

    def test_the_excuse_needs_two_finished_arms_and_a_workless_turn(self):
        """The excuse gates (manager 2026-09-24). It fires only over a COMPLETE run (arms.json names the expected arms, at least
        two, every one present and finished; the intersection over the arms present excuses wrongly on a one-arm or partial run)
        AND when the ending's own turn was GROUNDED workless (plannableUnits 0); a candidate with plannable units was silenced by
        a seal-boundary fault or a dead planner and is NOT excused, so every arm reads not comparable."""
        dest, m = self._corpus(name="partial")
        e_all = m["endings"][0]["id"]
        def build(tag, arms, expected=("A", "B"), plannable=0):   # arms: list of (name, unplanned, finished)
            run_root = os.path.join(self.td, "runs-" + tag)
            os.makedirs(run_root)
            Path(run_root, "arms.json").write_text(json.dumps({"arms": list(expected)}))
            for name, unplanned, finished in arms:
                os.makedirs(os.path.join(run_root, name))
                endings = {x["id"]: {"class": x["class"], "builds": [{}] * 3} for x in m["endings"]}
                endings[e_all]["plannableUnits"] = plannable   # the recorded own-turn planner-unit count the report grounds on
                Path(run_root, name, "results.json").write_text(json.dumps(
                    {"arm": name, "failures": 0, "buildsPerCard": 3, "finished": finished, "callsByJudge": {"planner": 5, "closer": 5},
                     "endingsUnplanned": unplanned, "endings": endings}))
            return {r["arm"]: r for r in self.je.report(dest, run_root, str(self.state))}, run_root
        # one of two expected arms present: nothing excused, not comparable, partial
        by, run_root = build("one-present", [("A", [e_all], True)])
        self.assertEqual(by["A"]["excusedEndings"], [], "an expected arm is missing: nothing is excused")
        self.assertFalse(by["A"]["comparable"], "A's unplanned ending is not excused when the run is incomplete")
        self.assertNotIn("comparableDenominator", by["A"], "no separate denominator: the metrics run over every ending")
        self.assertIn("partial: 1 of 2 arms finished", (Path(run_root) / "table.md").read_text())
        # both present but one unfinished: still nothing excused
        by2, _ = build("one-unfinished", [("A", [e_all], True), ("B", [e_all], False)])
        self.assertEqual(by2["A"]["excusedEndings"], [], "an unfinished arm blocks the excuse")
        self.assertFalse(by2["A"]["comparable"], "the shared unplanned ending is not excused while B is unfinished")
        # both present, finished, and the ending's own turn was workless: excused, both comparable
        by3, _ = build("both-workless", [("A", [e_all], True), ("B", [e_all], True)], plannable=0)
        self.assertEqual(by3["A"]["excusedEndings"], [e_all], "two finished arms and a workless turn: the shared ending is excused")
        self.assertTrue(by3["A"]["comparable"] and by3["B"]["comparable"], "the shared workless-turn ending is excused: both comparable")
        # both present and finished, but the shared ending's own turn HAD plannable units: a fault, not a workless turn -> not excused
        by4, rr4 = build("both-with-units", [("A", [e_all], True), ("B", [e_all], True)], plannable=2)
        self.assertEqual(by4["A"]["excusedEndings"], [], "a candidate with plannable units is a seal-boundary fault / dead planner: NOT excused")
        self.assertFalse(by4["A"]["comparable"] or by4["B"]["comparable"], "an unplanned ending with plannable units marks EVERY arm not comparable")
        self.assertIn("not excused", (Path(rr4) / "table.md").read_text())
        # a ONE-arm expected set: no excuse at all (an excuse needs at least two arms)
        by5, rr5 = build("one-arm", [("A", [e_all], True)], expected=("A",), plannable=0)
        self.assertEqual(by5["A"]["excusedEndings"], [], "one expected arm: no excuse even for a workless turn")
        self.assertFalse(by5["A"]["comparable"], "a one-arm root reads not comparable over its unplanned endings")
        self.assertIn("one arm expected", (Path(rr5) / "table.md").read_text())

    def test_run_arm_inprocess_evicts_each_ending_from_the_event_model_caches(self):
        """Medium (manager 2026-09-24): run_arm_inprocess parses one document per ending and must evict each so the arm's
        event-model caches stay flat instead of growing one entry per ending (the OOM the label loop hit). Wrapping load_judge
        captures the arm's jd; after the whole corpus its em caches hold at most one document."""
        dest, m = self._corpus(name="evictarm")
        captured = {}
        orig = self.je.load_judge
        def patched(*a, **k):
            jd = orig(*a, **k)
            captured["jd"] = jd
            return jd
        self.je.load_judge = patched
        self.addCleanup(lambda: setattr(self.je, "load_judge", orig))
        env = {k: os.environ.get(k) for k in ("XDG_STATE_HOME", "CLAUDE_CONFIG_DIR", "ROMP_CLAUDE_BIN")}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in env.items()])
        self.je.run_arm_inprocess(dest, "current", None, os.path.join(self.td, "r-evictarm"), None, self.fake, now=T0 + 10**6)
        em = captured["jd"].em
        self.assertLessEqual(len(em._ASM_CACHE), 1, "run_arm_inprocess evicts each ending: assembly cache flat, not %d of %d" % (len(em._ASM_CACHE), len(m["endings"])))
        self.assertLessEqual(len(em._JSONL_CACHE), 1, "run_arm_inprocess evicts each ending: record cache flat, not %d of %d" % (len(em._JSONL_CACHE), len(m["endings"])))

    def test_a_nudge_only_unplanned_ending_reads_arms_comparable_via_the_parse(self):
        """The excuse fix (PR 2143), report level: a run whose only unplanned-in-every-arm ending is NUDGE-ONLY (its own turn a romp
        Nudge, which drives no planner model call on an absent/resolved goal) reads all arms COMPARABLE through the parse
        fallback. The pre-fix count treated the nudge as a plannable unit and read every arm not comparable."""
        nsid = "11111111-2222-4444-8888-00000000abcd"
        g = nsid + ":g1"
        recs = [uline(nsid, T0, "add a retry to the uploader", "nu0", None),
                aline(nsid, T0 + 30, "Added the retry.", "na0", "nu0"),
                uline(nsid, T0 + 600, "<!-- romp-injected --><!-- romp-goal-id: %s --> where does this stand?" % g, "nu1", "na0"),
                aline(nsid, T0 + 630, "Still going.", "na1", "nu1")]
        (self.pdir / (nsid + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in recs))
        (self.state / "names" / nsid).write_text("web\t%s\t#abcdef\n" % self.cwd)
        dest, m = self._corpus(name="nudgecorpus")
        ne = self._ending(m, nsid, 1)                        # the turn-1 ending: its own turn is the romp Nudge
        run_root = os.path.join(self.td, "runs-nudge")
        env = {k: os.environ.get(k) for k in ("XDG_STATE_HOME", "CLAUDE_CONFIG_DIR", "ROMP_CLAUDE_BIN")}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in env.items()])
        for arm in ("A", "B"):
            self.je.run_arm_inprocess(dest, arm, None, run_root, None, self.fake, now=T0 + 10**6)
        Path(run_root, "arms.json").write_text(json.dumps({"arms": ["A", "B"]}))
        for arm in ("A", "B"):                               # the nudge ending planned nothing (its goal-id resolves to no node in the fresh seed -> the kernel's no-resolvable-target road, one of its three no-call roads); strip the recorded count to force the PARSE fallback (as the launch-head arms had none)
            rp = Path(run_root, arm, "results.json"); r = json.loads(rp.read_text())
            self.assertIn(ne["id"], r["endingsUnplanned"], "the nudge ending is unplanned in arm %s: %r" % (arm, r["endingsUnplanned"]))
            for e in r["endings"].values():
                e.pop("plannableUnits", None)
            rp.write_text(json.dumps(r))
        by = {r["arm"]: r for r in self.je.report(dest, run_root, str(self.state))}
        self.assertIn(ne["id"], by["A"]["excusedEndings"], "the nudge-only ending is excused (own-turn nudge counts 0 plannable units, via the parse): %r" % by["A"]["excusedEndings"])
        self.assertTrue(by["A"]["comparable"] and by["B"]["comparable"], "both arms read comparable: the only unplanned ending is a no-call nudge (a no-resolvable-target road)")

    def test_a_seal_boundary_fault_is_not_excused_and_marks_every_arm_not_comparable(self):
        """The grounded excuse's key case (the contributor's fixture, manager 2026-09-24): with the seed boundary set PAST the
        cut on some endings, the seal seals the ending's OWN turn, so the planner is silent in every arm with no failure row.
        The intersection alone would excuse these and read every arm comparable (the prior head did); grounding the excuse in
        the ending's own turn (plannable units counted from the cut, not the seed boundary) keeps them NOT excused, so every arm
        reads not comparable. The report re-derives the count when the arm did not record it (the launch-head arms)."""
        dest, m = self._corpus(name="sealfault")
        mf = Path(dest, "manifest.json")
        man = json.loads(mf.read_text())
        faulted = [e["id"] for e in man["endings"][:2]]
        for e in man["endings"]:
            if e["id"] in faulted:
                e["seedStart"] = float(e["cutT"]) + 1        # the seed boundary past the cut: the seal seals the ending's own turn
        mf.write_text(json.dumps(man))
        run_root = os.path.join(self.td, "runs-sealfault")
        env = {k: os.environ.get(k) for k in ("XDG_STATE_HOME", "CLAUDE_CONFIG_DIR", "ROMP_CLAUDE_BIN")}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in env.items()])
        for arm in ("A", "B"):
            self.je.run_arm_inprocess(dest, arm, None, run_root, None, self.fake, now=T0 + 10**6)
        Path(run_root, "arms.json").write_text(json.dumps({"arms": ["A", "B"]}))
        # the faulted endings recorded plannable units > 0 (their own turn had work the seal wrongly sealed)
        resA = json.loads(Path(run_root, "A", "results.json").read_text())
        self.assertTrue(all(resA["endings"][eid].get("plannableUnits") for eid in faulted),
                        "the fixture's faulted endings must carry work in their own turn: %r" % {eid: resA["endings"][eid].get("plannableUnits") for eid in faulted})
        by = {r["arm"]: r for r in self.je.report(dest, run_root, str(self.state))}
        for arm in ("A", "B"):
            self.assertTrue(set(faulted) <= set(by[arm]["endingsUnplanned"]), "the seal silenced the planner on the faulted endings in %s: %r" % (arm, by[arm]["endingsUnplanned"]))
            self.assertEqual([e for e in faulted if e in by[arm]["excusedEndings"]], [], "a faulted ending is not excused in %s" % arm)
            self.assertFalse(by[arm]["comparable"], "%s reads not comparable over the faulted endings" % arm)
        # the report-time PARSE fallback: strip the recorded counts (as the launch-head arms lack them) and re-report -> same verdict
        for arm in ("A", "B"):
            rp = Path(run_root, arm, "results.json"); r = json.loads(rp.read_text())
            for eid in r["endings"]:
                r["endings"][eid].pop("plannableUnits", None)
            rp.write_text(json.dumps(r))
        by2 = {r["arm"]: r for r in self.je.report(dest, run_root, str(self.state))}
        for arm in ("A", "B"):
            self.assertEqual([e for e in faulted if e in by2[arm]["excusedEndings"]], [], "the parse fallback keeps a faulted ending not excused in %s" % arm)
            self.assertFalse(by2[arm]["comparable"], "the parse fallback keeps %s not comparable" % arm)

    def test_run_refuses_a_second_launch_with_a_different_arm_set(self):
        """The expected-arms record is written ONCE per run root (manager 2026-09-24): a later `run` with a different arm set
        would silently change what "every arm" means for the excuse, so it is refused before any arm runs; the same set is a
        no-op re-launch."""
        dest, _ = self._corpus(name="armset")
        run_root = os.path.join(self.td, "runs-armset")
        os.makedirs(run_root)
        Path(run_root, "arms.json").write_text(json.dumps({"arms": ["baseline", "candidate"]}))
        with self.assertRaises(SystemExit):
            self.je.main(["run", "--corpus", dest, "--run-root", run_root, "--claude-bin", self.fake,
                          "--budget-usd", "inf", "--arm", "baseline", "--arm", "other"])
        self.assertEqual(json.loads(Path(run_root, "arms.json").read_text())["arms"], ["baseline", "candidate"],
                         "the record is unchanged after the refusal, and no arm ran")
        self.assertFalse(list(Path(run_root).glob("*/results.json")), "no arm results were written")

    def test_a_crashed_ending_is_named_in_endings_crashed_from_a_real_run(self):
        """Round-four low: endingsCrashed is populated by a REAL crashed pass (not only measure's pass-through). A planner that
        raises for one ending files pass-crash, and that ending is named in endingsCrashed and the arm reads not comparable."""
        self._judge_written_stores()
        dest, m = self._corpus(name="crash")
        target = [x for x in m["endings"] if x.get("startT") is not None][0]["id"]
        orig = self.je.load_judge
        def patched(*a, **k):
            jd = orig(*a, **k)
            real = jd._plan_session
            def boom(fsid, *aa, **kk):
                if fsid == target:
                    raise RuntimeError("boom in planner")
                return real(fsid, *aa, **kk)
            jd._plan_session = boom
            return jd
        self.je.load_judge = patched
        self.addCleanup(lambda: setattr(self.je, "load_judge", orig))
        env = {k: os.environ.get(k) for k in ("XDG_STATE_HOME", "CLAUDE_CONFIG_DIR", "ROMP_CLAUDE_BIN")}
        def _restore():
            for k, v in env.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.addCleanup(_restore)
        res = self.je.run_arm_inprocess(dest, "current", None, os.path.join(self.td, "r-crash"), None, self.fake, now=T0 + 10**6)
        self.assertIn(target, res.get("endingsCrashed") or [], "a crashed ending is named in endingsCrashed: %r" % res.get("endingsCrashed"))
        self.assertFalse(self.je.measure(m, res, self.state)["comparable"], "a crashed ending marks the arm not comparable")

    def _add_node_log(self, sid, suffix, ev):
        """Append an event to a live top-level node's log (a helper for the unblocker-ruling pins)."""
        p = self.state / "goals" / (sid + ".json")
        store = json.loads(p.read_text())
        store["nodes"][sid + ":" + suffix]["log"].append(ev)
        p.write_text(json.dumps(store))

    def test_the_measures_score_leaks_and_false_interrupts_from_the_users_gestures(self):
        """Road (b): the measures score against the user's OWN later actions on the live cards, not the labeller's class. A
        completed top the user re-opened is a leak; a needs_input top the user plainly crossed off, with no re-open and no
        unblocker ruling that a reply answered the block, is a false interrupt."""
        m = self._corpus()[1]
        e_leak, e_fi = self._ending(m, SIDS[0], 0), self._ending(m, SIDS[1], 0)
        sl, cl = float(e_leak["startT"]), float(e_leak["cutT"])
        sf, cf = float(e_fi["startT"]), float(e_fi["cutT"])
        # SIDS[0] g1: placed in the turn, the user followed up after -> a re-open (a leak when the arm reads completed)
        self._live_store_with_done(SIDS[0], sl, cl, [{"node": SIDS[0] + ":g1", "op": "followup", "t": cl + 7200}])
        # SIDS[1] g1: placed in the turn, the user crossed it off with no re-open and no unblocker ruling -> a false interrupt
        self._live_store_with_done(SIDS[1], sf, cf, [{"node": SIDS[1] + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": cf + 600}])
        manifest = {"endings": [e_leak, e_fi]}
        results = {"arm": "x", "failures": 0, "endings": {
            e_leak["id"]: {"builds": [{e_leak["id"] + ":g1": {"column": "completed", "scored": True}}] * 2},
            e_fi["id"]: {"builds": [{e_fi["id"] + ":g1": {"column": "needs_input", "scored": True}}] * 2}}}
        mm = self.je.measure(manifest, results, self.state)
        self.assertEqual((mm["leaks"], mm["falseInterrupts"], mm["answeredThenCleared"], mm["gesturedEndings"]), (1, 1, 0, 2),
                         "the re-opened completed top is the one leak; the crossed-off needs_input top the one false interrupt: %r" % mm)

    def test_the_false_interrupt_keys_on_the_unblocker_ruling_not_a_bare_turn(self):
        """Road (b), the manager's ruling: the ONLY suppressor of a false interrupt is the kernel's own unblocker verdict on
        the node after the placement (a reply it ruled answered the block). A cleared needs_input top with no such ruling is a
        false interrupt; one the unblocker ruled answered before the clear is answeredThenCleared, never a false interrupt."""
        sid = SIDS[1]
        e = self._ending(self._corpus()[1], sid, 0)
        s, c = float(e["startT"]), float(e["cutT"])
        self._live_store_with_done(sid, s, c, [{"node": sid + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": c + 600}])
        manifest = {"endings": [e]}
        results = {"arm": "x", "failures": 0, "endings": {e["id"]: {"builds": [{e["id"] + ":g1": {"column": "needs_input", "scored": True}}] * 2}}}
        mm = self.je.measure(manifest, results, self.state)
        self.assertEqual((mm["falseInterrupts"], mm["answeredThenCleared"]), (1, 0),
                         "no unblocker ruling: a plain cross-off is a false interrupt: %r" % mm)
        # the kernel's unblocker ruled the block answered after the placement, before the clear
        self._add_node_log(sid, "g1", {"ev_t": c + 300, "at": c + 301, "src": "unblocker", "kind": "unblock", "why": "answered in passing"})
        mm = self.je.measure(manifest, results, self.state)
        self.assertEqual((mm["falseInterrupts"], mm["answeredThenCleared"]), (0, 1),
                         "the unblocker ruled the reply answered the block: answered-then-cleared, not a false interrupt: %r" % mm)

    def test_only_the_unblocker_judge_lift_suppresses_never_the_other_unblock_kinds_or_one_at_or_after_the_clear(self):
        """M1 + L1 + item 6: an `unblock` event lifts a block from several sources; ONLY the unblocker judge's lift (src
        unblocker), which it stamps having ruled the block ANSWERED or MOOT, suppresses a false interrupt. The topic-blind
        'you re-engaged' user unblock, a reopen-ancestor lift (optimistic), the planner's new-work unblock and a mechanical
        romp unblock do not; nor does a judge lift at or after the clear (a same-second or later lift is not the reply this
        clear crossed off)."""
        sid = SIDS[1]
        e = self._ending(self._corpus()[1], sid, 0)
        s, c = float(e["startT"]), float(e["cutT"])
        manifest = {"endings": [e]}
        results = {"arm": "x", "failures": 0, "endings": {e["id"]: {"builds": [{e["id"] + ":g1": {"column": "needs_input", "scored": True}}] * 2}}}

        def score(unblock_ev, clear_t=None):
            self._live_store_with_done(sid, s, c, [{"node": sid + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed",
                                                    "t": c + 600 if clear_t is None else clear_t}])
            if unblock_ev:
                self._add_node_log(sid, "g1", unblock_ev)
            mm = self.je.measure(manifest, results, self.state)
            return mm["falseInterrupts"], mm["answeredThenCleared"]

        # non-suppressors, each a false interrupt (all four tabled kinds)
        self.assertEqual(score({"ev_t": c + 300, "src": "user", "kind": "unblock", "why": "you re-engaged"}), (1, 0),
                         "a topic-blind 'you re-engaged' user unblock does not suppress")
        self.assertEqual(score({"ev_t": c + 300, "src": "user", "kind": "unblock", "why": "unblocked by reopen (optimistic)"}), (1, 0),
                         "a reopen-ancestor (optimistic) lift does not suppress")
        self.assertEqual(score({"ev_t": c + 300, "src": "planner", "kind": "unblock", "why": "new work filed on this branch"}), (1, 0),
                         "the planner's new-work unblock does not suppress")
        self.assertEqual(score({"ev_t": c + 300, "src": "romp", "kind": "unblock", "why": "moot"}), (1, 0),
                         "a mechanical romp unblock does not suppress")
        # the unblocker judge's lift suppresses, whether it ruled ANSWERED or MOOT (item 6: one src, both meanings)
        self.assertEqual(score({"ev_t": c + 300, "src": "unblocker", "kind": "unblock", "why": "answered in passing: the reply landed"}), (0, 1),
                         "the unblocker judge's answered lift is answered-then-cleared, not a false interrupt")
        self.assertEqual(score({"ev_t": c + 300, "src": "unblocker", "kind": "unblock", "why": "answered in passing: made moot by a later completion"}), (0, 1),
                         "item 6: a MOOT lift by the unblocker also suppresses (a top nobody answered, lifted moot then cleared): answered-then-cleared")
        # L1 + the same-second bound: a lift after the clear, and one AT THE CUT (the boundary), are not this clear's reply
        self.assertEqual(score({"ev_t": c + 900, "src": "unblocker", "kind": "unblock", "why": "answered in passing"}), (1, 0),
                         "L1: a judge lift AFTER the clear is not the reply this clear crossed off: a false interrupt")
        self.assertEqual(score({"ev_t": c, "src": "unblocker", "kind": "unblock", "why": "answered in passing"}), (1, 0),
                         "the 2026-09-22 PR 2035 review, low 6: a judge lift AT the cut is not strictly after it, so it does not suppress (a non-strict `<` would): a false interrupt")

    def test_later_gestures_key_on_the_cut_not_the_placement_time(self):
        """Review item 2: a placed top's 'later' user gestures key on the ending's CUT, not the placement's own ev_t (a
        closer done's ev_t is the turn's START). A cross-off made MID-TURN (after the turn's start, before its cut) is not a
        'later' gesture: the base keyed on the done's start-of-turn ev_t and counted it a false interrupt; keyed on the cut it
        does not, and the ending reads untouched."""
        sid = SIDS[1]
        e = self._ending(self._corpus(name="cutkey")[1], sid, 0)
        s, c = float(e["startT"]), float(e["cutT"])
        mid = s + (c - s) / 2.0                                # a gesture strictly between the turn's start and its cut
        self.assertLess(mid, c)
        self._live_store_with_done(sid, s, c, [{"node": sid + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": mid}])
        manifest = {"endings": [e]}
        results = {"arm": "x", "failures": 0, "endings": {e["id"]: {"builds": [{e["id"] + ":g1": {"column": "needs_input", "scored": True}}] * 2}}}
        mm = self.je.measure(manifest, results, self.state)
        self.assertEqual((mm["falseInterrupts"], mm["gesturedEndings"], mm["untouchedEndings"]), (0, 0, 1),
                         "a mid-turn cross-off is before the cut: not a later gesture, so no false interrupt: %r" % mm)

    def test_tier_one_ignores_a_mid_turn_gesture_keying_later_on_the_cut(self):
        """Review item 2, extended to the labeller's tier one for consistency: a done's own ev_t is the turn's START, so a
        user gesture made MID-TURN (before the cut) must not decide the tier-one verdict; the 'later' gestures key on the
        cut. The base keyed on the done's start-of-turn ev_t and read a lone mid-turn clear as 'finished'; keyed on the cut,
        only a post-cut gesture decides, so a lone mid-turn clear leaves no applicable verdict (None)."""
        sid = SIDS[0]
        e = self._ending(self._corpus(name="t1cut")[1], sid, 0)
        start, cut = float(e["startT"]), float(e["cutT"])
        mid = start + (cut - start) / 2.0
        self._live_store_with_done(sid, start, cut, [{"node": sid + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": mid}])
        self.assertIsNone(self.je.tier_one_label(self.state, sid, cut, start),
                          "a lone mid-turn clear is before the cut: not a later verdict, so no tier-one label")

    def test_a_gesture_exactly_at_the_cut_is_not_later_in_either_function(self):
        """the 2026-09-22 PR 2035 review, low 6: the later-gesture boundary is STRICT (> cut). A cross-off or a follow-up landing exactly AT the cut
        is not a later gesture, in both placement_gestures (the measure) and tier_one_label; a non-strict `>=` would count
        it. Nothing in the earlier pins sat exactly at the cut."""
        sid = SIDS[1]
        e = self._ending(self._corpus(name="atcut")[1], sid, 0)
        s, c = float(e["startT"]), float(e["cutT"])
        manifest = {"endings": [e]}
        # placement_gestures: a needs_input top with a cross-off AT the cut is not later -> no false interrupt, untouched
        self._live_store_with_done(sid, s, c, [{"node": sid + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": c}])
        results_ni = {"arm": "x", "failures": 0, "endings": {e["id"]: {"builds": [{e["id"] + ":g1": {"column": "needs_input", "scored": True}}] * 2}}}
        mm = self.je.measure(manifest, results_ni, self.state)
        self.assertEqual((mm["falseInterrupts"], mm["untouchedEndings"]), (0, 1), "a cross-off AT the cut is not a later gesture: %r" % mm)
        # a completed top with a follow-up AT the cut is not a re-open -> no leak
        self._live_store_with_done(sid, s, c, [{"node": sid + ":g1", "op": "followup", "t": c}])
        results_c = {"arm": "x", "failures": 0, "endings": {e["id"]: {"builds": [{e["id"] + ":g1": {"column": "completed", "scored": True}}] * 2}}}
        self.assertEqual(self.je.measure(manifest, results_c, self.state)["leaks"], 0, "a follow-up AT the cut is not a re-open")
        # tier_one_label: neither a cross-off nor a follow-up AT the cut is a later verdict
        self._live_store_with_done(sid, s, c, [{"node": sid + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": c}])
        self.assertIsNone(self.je.tier_one_label(self.state, sid, c, s), "a cross-off AT the cut is not a later verdict")
        self._live_store_with_done(sid, s, c, [{"node": sid + ":g1", "op": "followup", "t": c}])
        self.assertIsNone(self.je.tier_one_label(self.state, sid, c, s), "a follow-up AT the cut is not a later verdict")

    def test_the_harness_mute_why_matches_the_kernel(self):
        """The harness's mute-clear why must equal the kernel's own literal, or the mute exclusion drifts silent."""
        km = load_source("romp_kernel_whys", os.path.join(BIN, "romp-kernel"))
        self.assertEqual(self.je.MUTE_CLEAR_WHY, km._HIDDEN_FROM_FEED_WHY, "the harness excludes exactly the kernel's mute why")

    def test_install_call_retry_tags_recovered_rows_counts_the_kills_and_leaves_the_parse(self):
        """A transiently-failed arm-judge call is re-sampled; a recovered attempt's rows are KEPT but tagged (so first-attempt
        kills stay auditable) and count_failure_rows skips them, so only a call that failed every attempt counts. The counters
        tally first-attempt kills, re-samples and recoveries for the ARM judges only; a non-arm judge is retried but not
        counted; a pause is never retried."""
        ep = Path(self.td) / "judge-errors.jsonl"
        def fake_jd(script, judge="planner"):
            calls = {"n": 0}
            ctx = types.SimpleNamespace(paused=False, last_call_fail=None)
            jd = types.SimpleNamespace(_judge_ctx=ctx)
            def impl(*a, **k):
                ctx.paused = False
                verdict = script(calls["n"]); calls["n"] += 1
                if verdict == "serve":
                    ctx.last_call_fail = None; return "ok"
                if verdict == "pause":
                    ctx.paused = True; ctx.last_call_fail = None; return ""
                if verdict == "serve_empty":
                    ctx.last_call_fail = None; return ""              # a legitimate EMPTY reply, no failure: returned at once, never retried
                if verdict == "pause_stale":
                    ctx.paused = True; return ""                      # a pause that LEAVES last_call_fail set (stale), as the real pause paths do: the `paused` conjunct must return
                with ep.open("a", encoding="utf-8") as f:                 # a real transient call failure files one `call` row
                    f.write(json.dumps({"judge": judge, "err": "call", "note": "timeout"}) + "\n")
                ctx.last_call_fail = {"note": "timeout"}; return ""
            jd._judge_run_impl = impl
            return jd
        # fails once, then serves: with no retry the row stays a failure; with retries it recovers, the row kept but tagged out
        ep.write_text(""); c1 = {}
        jd = fake_jd(lambda n: "fail" if n == 0 else "serve")
        self.je.install_call_retry(jd, ep, c1, attempts=1)
        self.assertEqual((jd._judge_run_impl(k="v") or "", self.je.count_failure_rows(ep)), ("", 1), "no retry: the failed call stays a failure")
        ep.write_text(""); c2 = {}
        jd = fake_jd(lambda n: "fail" if n == 0 else "serve")
        self.je.install_call_retry(jd, ep, c2, attempts=3)
        self.assertEqual((jd._judge_run_impl(judge="planner"), self.je.count_failure_rows(ep)), ("ok", 0), "retry recovers: not comparable becomes comparable")
        self.assertEqual(len(ep.read_text().splitlines()), 1, "the kill row is KEPT (tagged), never deleted, so it stays auditable")
        self.assertEqual((c2["firstAttemptKills"], c2["recoveredCalls"]), (1, 1), "one first-attempt kill, one recovery")
        # fails every attempt: exactly one row counts, three rows on disk, the re-samples counted
        ep.write_text(""); c3 = {}
        jd = fake_jd(lambda n: "fail")
        self.je.install_call_retry(jd, ep, c3, attempts=3)
        self.assertEqual((jd._judge_run_impl(judge="closer"), self.je.count_failure_rows(ep)), ("", 1), "a call that fails every attempt counts once")
        self.assertEqual((len(ep.read_text().splitlines()), c3["firstAttemptKills"], c3["retryAttempts"], c3["recoveredCalls"]), (3, 1, 2, 0))
        # a downstream parse failure (not a call kill) is left untouched and named by kind
        ep.write_text(json.dumps({"judge": "planner", "err": "parse", "note": "bad reply"}) + "\n")
        self.assertEqual(self.je.failure_rows_by_kind(ep), {"parse": 1}, "a parse the re-sample does not touch stays its own row")
        # a non-arm judge is retried but not tallied into the arm counters; a pause is never retried
        ep.write_text(""); c4 = {}
        jd = fake_jd(lambda n: "fail" if n == 0 else "serve", judge="gister")
        self.je.install_call_retry(jd, ep, c4, attempts=3)
        self.assertEqual((jd._judge_run_impl(judge="gister"), c4["firstAttemptKills"], c4["killedCalls"]), ("ok", 0, 0), "a non-arm recovery is not an arm kill and not counted in killedCalls (the arm-only guard; `if True` would count it) (PR 2122 review medium 3)")
        ep.write_text(""); c5 = {}
        jd = fake_jd(lambda n: "pause")
        self.je.install_call_retry(jd, ep, c5, attempts=3)
        self.assertEqual((jd._judge_run_impl(judge="planner"), self.je.count_failure_rows(ep), c5["firstAttemptKills"]), ("", 0, 0), "a pause is a skip, not a failure to retry")
        # a served EMPTY reply (no failure): returned at once, never a first-attempt kill
        ep.write_text(""); c6 = {}
        jd = fake_jd(lambda n: "serve_empty")
        self.je.install_call_retry(jd, ep, c6, attempts=3)
        self.assertEqual((jd._judge_run_impl(judge="planner"), c6["firstAttemptKills"], c6["retryAttempts"]), ("", 0, 0), "a served empty reply is not a failure to retry")
        # a pause AFTER a failure with last_call_fail left STALE: the `paused` conjunct RETURNS (ending the retry), but a pause is
        # a stand-down, NOT a recovery (a recovery is a non-empty served reply, PR 2122 review medium 2)
        ep.write_text(""); c7 = {}
        jd = fake_jd(lambda n: "fail" if n == 0 else "pause_stale")
        self.je.install_call_retry(jd, ep, c7, attempts=3)
        self.assertEqual((jd._judge_run_impl(judge="planner"), c7["firstAttemptKills"], c7["recoveredCalls"]), ("", 1, 0), "a pause after a kill ends the retry via the paused conjunct but is NOT counted a recovery")
        self.assertEqual(c7["killedCalls"], 1, "ONE genuine kill: the wrapper counts kills directly, so a pause after a kill is not a second kill (fak+retry-rec would over-count it as 2) (PR 2122 review point 7)")
        self.assertEqual(self.je.count_failure_rows(ep), 0, "the earlier kill's row is still tagged out when the pause returns (not a final failure)")
        # a tagged (retried) row is skipped by BOTH counters, arm and non-arm
        ep.write_text(json.dumps({"judge": "planner", "err": "call", "note": "timeout", "retried": True}) + "\n"
                      + json.dumps({"judge": "gister", "err": "call", "note": "timeout", "retried": True}) + "\n")
        self.assertEqual((self.je.count_failure_rows(ep), self.je.count_non_arm_failure_rows(ep)), (0, 0), "a tagged row is skipped by both count_failure_rows and count_non_arm_failure_rows")

    def test_install_call_retry_refuses_a_concurrent_entry(self):
        """The by-line-range tag-and-count is correct only single-threaded (the arm's construction); the guard makes that an
        enforced invariant: a concurrent _judge_run_impl entry raises rather than silently racing the ledger rewrite."""
        ep = Path(self.td) / "e.jsonl"; ep.write_text("")
        ctx = types.SimpleNamespace(paused=False, last_call_fail=None)
        jd = types.SimpleNamespace(_judge_ctx=ctx)
        box = {}
        def impl(*a, **k):
            def reenter():
                try:
                    jd._judge_run_impl(judge="planner")               # a second thread enters while this call holds the guard
                except Exception as e:
                    box["e"] = e
            t = threading.Thread(target=reenter); t.start(); t.join()
            ctx.last_call_fail = None; return "ok"
        jd._judge_run_impl = impl
        self.je.install_call_retry(jd, ep, {}, attempts=1)
        self.assertEqual(jd._judge_run_impl(judge="planner"), "ok")
        self.assertIsInstance(box.get("e"), RuntimeError, "a concurrent entry is refused, not silently raced")

    def _restore_arm_env(self, extra=()):   # snapshot + addCleanup the process env load_judge / the kill knob mutate
        keys = ("XDG_STATE_HOME", "CLAUDE_CONFIG_DIR", "ROMP_CLAUDE_BIN", "ROMP_STATE_DIR") + tuple(extra)
        snap = {k: os.environ.get(k) for k in keys}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in snap.items()])

    def test_the_arm_retry_wiring_recovers_kills_and_restores(self):
        """Arm-level wiring of the retry (PR 2092 review point 1): the kill-first knob kills each arm's first planner and closer
        call, which the retry re-samples to a served reply, so a two-arm one-build run reads failures 0 with the counters filled
        and both arms comparable; the module's _judge_run_impl and its 120s alarm are RESTORED after the arm (a removed install
        or a dropped restore red the restore assertions), and the fake observed the arm's 240s alarm (the alarm-raise)."""
        dest, m = self._corpus(name="retrywire")
        run_root = os.path.join(self.td, "runs-retrywire")
        killdir = os.path.join(self.td, "killdir"); os.makedirs(killdir)
        alarmlog = os.path.join(self.td, "alarm.log")
        caps = []
        orig = self.je.load_judge
        def patched(*a, **k):
            jd = orig(*a, **k)
            caps.append({"jd": jd, "origImpl": jd._judge_run_impl, "alarm": jd.CALL_ALARM_S})   # captured BEFORE run_arm_inprocess installs the retry / raises the alarm
            return jd
        self.je.load_judge = patched
        self.addCleanup(lambda: setattr(self.je, "load_judge", orig))
        self._restore_arm_env(("JE_TEST_KILL_FIRST", "JE_TEST_KILL_FIRST_PLANNER", "JE_TEST_ALARM_LOG"))
        os.environ["JE_TEST_KILL_FIRST"] = killdir
        os.environ["JE_TEST_KILL_FIRST_PLANNER"] = "2"   # kill the first PLANNER call on its first TWO attempts (closer once), so the counters DIFFER: 2/3/2 (PR 2122 review point 6)
        os.environ["JE_TEST_ALARM_LOG"] = alarmlog
        res = []
        for arm in ("A", "B"):
            for f in os.listdir(killdir):
                os.unlink(os.path.join(killdir, f))   # clear the markers so each arm kills its OWN first planner and closer call
            res.append(self.je.run_arm_inprocess(dest, arm, None, run_root, None, self.fake, now=T0 + 10**6, builds=1))
        for r in res:
            self.assertEqual(r["failures"], 0, "the first-call kills recovered on re-sample: no failure (%r)" % r.get("failuresByKind"))
            self.assertEqual((r["retry"]["firstAttemptKills"], r["retry"]["retryAttempts"], r["retry"]["recoveredCalls"]), (2, 3, 2),
                             "planner killed twice + closer once, each recovered: fak/retry/rec = 2/3/2 (%r)" % r["retry"])
            self.assertEqual(r["retry"]["killedCalls"], 3, "three genuine kills (2 planner + 1 closer), the wrapper's direct tally: %r" % r["retry"])
            self.assertEqual(r["failuresByKind"], {}, "nothing failed every attempt")
            # the per-ending retry record: exactly one ending (the arm's first) carries the whole delta, and the endings sum to the arm total (PR 2122 review point 4)
            per = [e.get("retry") or {} for e in r["endings"].values()]
            self.assertEqual(sum(p.get("firstAttemptKills", 0) for p in per), 2, "the per-ending retry records sum to the arm's kills: %r" % per)
            self.assertEqual(sum(p.get("retryAttempts", 0) for p in per), 3, "the per-ending re-samples sum to the arm total")
        Path(run_root, "arms.json").write_text(json.dumps({"arms": ["A", "B"]}))
        rows = self.je.report(dest, run_root, str(self.state))
        self.assertTrue(all(x["comparable"] for x in rows), "both arms recovered and read comparable")
        # the three retry columns are asserted against counters that DIFFER (2/3/2), so a transposed or repeated print reds (PR 2122 review point 6)
        table = (Path(run_root) / "table.md").read_text()
        row = [l for l in table.splitlines() if l.startswith("| A |")][0]
        cells = [c.strip() for c in row.strip("|").split("|")]
        self.assertEqual((cells[-4], cells[-3], cells[-2]), ("2", "3", "2"), "the table prints first-attempt kills / re-samples / recovered = 2/3/2 for the arm: %r" % row)
        self.assertEqual(cells[-1], "0", "the failures cell reads 0 (comparable after recovery): %r" % row)
        for c in caps:
            self.assertEqual(c["jd"]._judge_run_impl.__name__, "_judge_run_impl", "the retry wrapper (_retrying) is restored to the module's original _judge_run_impl after the arm")
            self.assertEqual(c["jd"].CALL_ALARM_S, c["alarm"], "the module alarm is restored to its pre-arm value (120s)")
            self.assertEqual(c["alarm"], 120, "the module alarm before the arm is the production 120s")
        observed = [int(l.split()[1]) for l in Path(alarmlog).read_text().splitlines() if l.strip()]
        # Linux alarm() rounds the remaining seconds, so a fake reached >0.5s after perl's alarm reads 239: assert each value is
        # in (120, 240], which still reds without the alarm raise (that would read 120), without flaking on a loaded runner (PR 2122 review point 1)
        self.assertTrue(observed and all(120 < v <= 240 for v in observed), "each killed call ran under the arm's HARNESS_ALARM_S (>120, <=240); the alarm-raise mutant reads 120: %r" % observed)

    def test_the_budget_stop_counts_killed_attempts_from_a_floor_when_no_row_landed(self):
        """PR 2122 review medium 3: a killed attempt writes no usage row, so with the ledger EMPTY the mean-cost estimate is 0
        and the stop would never fire. The floor prices a killed attempt when no row landed, so a kill-heavy arm with an empty
        ledger still stops at the 1.2x bound. Killing every arm call leaves the ledger empty, yet the arm stops."""
        dest, m = self._corpus(name="killbudget")
        self._restore_arm_env(("JE_TEST_KILL_ALL",))
        os.environ["JE_TEST_KILL_ALL"] = "1"
        res = self.je.run_arm_inprocess(dest, "A", None, os.path.join(self.td, "r-killbudget"), 0.05, self.fake, now=T0 + 10**6, builds=1)
        self.assertEqual(res.get("calls"), 0, "every arm call was killed: no usage row landed, so the mean cost is undefined")
        self.assertIsNotNone(res.get("stopped"), "the arm STOPS on the killed-attempt floor estimate though the ledger is empty")
        self.assertGreater(res["stopped"].get("killedAttempts", 0), 0, "the stop names the killed attempts it counted at the floor")

    @unittest.skipIf(os.geteuid() == 0, "chmod-based unreadable/unwritable path does not stop root, so the assertRaises would fail")
    def test_tag_surfaces_a_read_failure_and_never_overwrites_the_ledger(self):
        """PR 2122 review low a: _tag's read path must SURFACE a read failure, never swallow it to an empty list and then write
        that over the ledger, destroying rows. With the errors file unreadable when the recovery tag reads it, the tag raises
        and the ledger's rows are preserved (pre-fix: the read swallowed, the ledger truncated to 0, the call returned ok)."""
        ep = Path(self.td) / "tagfail.jsonl"
        ep.write_text(json.dumps({"judge": "planner", "err": "x", "note": "one"}) + "\n"
                      + json.dumps({"judge": "planner", "err": "x", "note": "two"}) + "\n")
        ctx = types.SimpleNamespace(paused=False, last_call_fail=None)
        jd = types.SimpleNamespace(_judge_ctx=ctx)
        calls = {"n": 0}
        def impl(*a, **k):
            n = calls["n"]; calls["n"] += 1
            if n == 0:
                with ep.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"judge": "planner", "err": "call", "note": "kill"}) + "\n")   # a kill: a row written, the range captured
                ctx.last_call_fail = {"note": "kill"}; return ""
            os.chmod(ep, 0)                                       # the recovery tag will now READ this file and fail
            ctx.last_call_fail = None; return "ok"
        jd._judge_run_impl = impl
        self.je.install_call_retry(jd, ep, {}, attempts=3)
        try:
            with self.assertRaises(OSError):
                jd._judge_run_impl(judge="planner")              # the recovery tag reads the unreadable ledger and SURFACES the failure
        finally:
            os.chmod(ep, 0o644)
        self.assertEqual(len(ep.read_text().splitlines()), 3, "the ledger's rows are preserved, never overwritten with an empty list on a failed read")

    @unittest.skipIf(os.geteuid() == 0, "chmod-based unreadable/unwritable path does not stop root, so the assertRaises would fail")
    def test_tag_surfaces_a_write_failure_and_leaves_the_ledger_intact(self):
        """PR 2122 review point 5: _tag's WRITE half. An unwritable directory makes the temp write fail; _tag surfaces it
        (raises) rather than swallowing it, and never truncates the ledger with an in-place rewrite. A swallowed or in-place
        write reds this."""
        d = Path(self.td) / "tagwrite"; d.mkdir()
        ep = d / "e.jsonl"
        ep.write_text(json.dumps({"judge": "planner", "err": "x", "note": "one"}) + "\n")
        ctx = types.SimpleNamespace(paused=False, last_call_fail=None)
        jd = types.SimpleNamespace(_judge_ctx=ctx)
        calls = {"n": 0}
        def impl(*a, **k):
            n = calls["n"]; calls["n"] += 1
            if n == 0:
                with ep.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"judge": "planner", "err": "call", "note": "kill"}) + "\n")
                ctx.last_call_fail = {"note": "kill"}; return ""
            os.chmod(d, 0o555)                                   # the recovery tag's temp WRITE into this dir will now fail
            ctx.last_call_fail = None; return "ok"
        jd._judge_run_impl = impl
        self.je.install_call_retry(jd, ep, {}, attempts=3)
        try:
            with self.assertRaises(OSError):
                jd._judge_run_impl(judge="planner")
        finally:
            os.chmod(d, 0o755)
        self.assertEqual(len(ep.read_text().splitlines()), 2, "the ledger is intact (the kill row + the original), never truncated by a failed write")

    def test_an_opener_less_minted_top_is_scored_by_the_boundary_at_the_call_site(self):
        """PR 2122 review point 2: pin the SCORED rule at its CALL SITE, not only top_scored's unit test. For an opener-less
        ending (startT None) whose own turn mints a top the arm files no verdict on, that top reads scored by the seedStart
        boundary the call site passes; passing the turn start (None) there would drop it. Kill nothing; mint every planner call
        and no-op the closer, so the only scored tops are the own-turn mints scored purely by the boundary."""
        self._judge_written_stores()
        dest, m = self._corpus(name="openerscore")
        mp = Path(dest, "manifest.json"); mm = json.loads(mp.read_text())
        for e in mm["endings"]:
            e["startT"] = None                                   # read them as opener-less continuations; seedStart keeps the value build_corpus recorded (in this 2-turn fixture the turn's own start, e.g. T0+600, not the previous turn's end)
        mp.write_text(json.dumps(mm))
        self._restore_arm_env(("JE_TEST_PLANNER_MINT", "JE_TEST_CLOSER_NOOP"))
        os.environ["JE_TEST_PLANNER_MINT"] = "1"; os.environ["JE_TEST_CLOSER_NOOP"] = "1"
        res = self.je.run_arm_inprocess(dest, "current", None, os.path.join(self.td, "r-openerscore"), None, self.fake, now=T0 + 10**6, builds=1)
        scored = [(eid, sfx) for eid, e in res["endings"].items() for b in e["builds"] for sfx, v in b.items() if v.get("scored")]
        self.assertTrue(scored, "an opener-less ending's minted-no-verdict top scores by the seedStart boundary at the call site (the None-boundary revert reds this): %r"
                        % {eid: e["builds"] for eid, e in res["endings"].items()})

    def test_the_killed_count_ignores_a_served_empty_reply_after_a_kill(self):
        """PR 2122 review medium 2: the budget's killed count is the wrapper's DIRECT tally, not firstAttemptKills +
        retryAttempts - recoveredCalls. A kill followed by a served-EMPTY reply (not a kill, not a recovery) is ONE kill; the
        old formula reads two. An arm-level run with that scenario and a tiny budget stops with killedAttempts == 1."""
        dest, m = self._corpus(name="killempty")
        kdir = os.path.join(self.td, "kte"); os.makedirs(kdir)
        self._restore_arm_env(("JE_TEST_KILL_PLANNER_THEN_EMPTY",))
        os.environ["JE_TEST_KILL_PLANNER_THEN_EMPTY"] = kdir
        res = self.je.run_arm_inprocess(dest, "A", None, os.path.join(self.td, "r-killempty"), 0.001, self.fake, now=T0 + 10**6, builds=1)
        st = res.get("stopped")
        self.assertIsNotNone(st, "the tiny budget stops the arm after the first ending")
        self.assertEqual(st["killedAttempts"], 1, "one real kill: a served-empty reply after the kill is NOT a second kill (the old fak+retry-rec formula reads 2)")

    def test_the_budget_stop_prices_kills_at_the_mean_when_rows_landed(self):
        """PR 2122 review point 3: the budget stop's MEAN-COST branch (rows landed) is pinned, not only the floor. A kill-first
        run with landed rows adds the killed attempts at the mean call cost, so the stop's estimate EXCEEDS the ledger cost;
        pricing kills at zero when a row landed would leave estCost == cost, which this reds."""
        dest, m = self._corpus(name="killmean")
        killdir = os.path.join(self.td, "killdir-mean"); os.makedirs(killdir)
        self._restore_arm_env(("JE_TEST_KILL_FIRST", "JE_TEST_KILL_FIRST_PLANNER"))
        os.environ["JE_TEST_KILL_FIRST"] = killdir
        os.environ["JE_TEST_KILL_FIRST_PLANNER"] = "2"           # 3 kills over the first ending (planner x2 + closer x1), all recovered, with landed rows
        res = self.je.run_arm_inprocess(dest, "A", None, os.path.join(self.td, "r-killmean"), 0.001, self.fake, now=T0 + 10**6, builds=1)
        st = res.get("stopped")
        self.assertIsNotNone(st, "the tiny budget stops the arm after the first ending")
        self.assertGreater(res.get("calls") or 0, 0, "rows landed (the recovered calls), so the mean-cost branch is exercised, not the floor")
        self.assertGreater(st["killedAttempts"], 0, "killed attempts were counted at the stop")
        self.assertEqual(st["estCost"], round(st["cost"] + st["killedAttempts"] * res["cost"] / res["calls"], 4),
                         "the estimate prices each killed attempt at the MEAN landed call cost (cost/calls), not the floor and not zero: %r" % st)

    def test_a_partial_run_report_names_the_candidate_count_it_would_excuse(self):
        """PR 2122 review point 9: on a PARTIAL run the corpus-unplanned line prints the candidate set (what WOULD be excused
        once complete), not a false 0. Two expected arms, one present: the line names the 1 ending unplanned in every present
        arm and that 1 would be a candidate once complete."""
        dest, m = self._corpus(name="partialline")
        e0 = m["endings"][0]["id"]
        run_root = os.path.join(self.td, "runs-partialline"); os.makedirs(run_root)
        Path(run_root, "arms.json").write_text(json.dumps({"arms": ["A", "B"]}))   # two expected; only A present -> incomplete
        os.makedirs(os.path.join(run_root, "A"))
        Path(run_root, "A", "results.json").write_text(json.dumps(
            {"arm": "A", "failures": 0, "buildsPerCard": 3, "finished": True, "callsByJudge": {"planner": 5, "closer": 5},
             "endingsUnplanned": [e0], "endings": {x["id"]: {"class": x["class"], "builds": [{}] * 3} for x in m["endings"]}}))
        self.je.report(dest, run_root, str(self.state))
        line = [l for l in (Path(run_root) / "table.md").read_text().splitlines() if l.startswith("corpus-unplanned")][0]
        self.assertIn("1 ending(s) planned nothing in EVERY present arm", line, "the partial line names the candidate count, not a false 0: %r" % line)
        self.assertIn("1 would be candidates", line)

    def test_a_crashed_ending_is_evicted_from_the_event_model_caches(self):
        """Verifier low (a): the crash-path eviction (the finally around the build) drops a crashed ending's document from both
        caches, not only a successful one. A planner that raises for one ending still leaves the arm's caches flat; moving the
        evict out of the finally would leave the crashed ending's entries cached."""
        self._judge_written_stores()
        dest, m = self._corpus(name="crashevict")
        target = [x for x in m["endings"] if x.get("startT") is not None][0]["id"]
        caps = {}
        orig = self.je.load_judge
        def patched(*a, **k):
            jd = orig(*a, **k)
            real = jd._plan_session
            def boom(fsid, *aa, **kk):
                if fsid == target:
                    raise RuntimeError("boom in planner")
                return real(fsid, *aa, **kk)
            jd._plan_session = boom
            caps["jd"] = jd
            return jd
        self.je.load_judge = patched
        self.addCleanup(lambda: setattr(self.je, "load_judge", orig))
        self._restore_arm_env()
        res = self.je.run_arm_inprocess(dest, "current", None, os.path.join(self.td, "r-crashevict"), None, self.fake, now=T0 + 10**6)
        self.assertIn(target, res.get("endingsCrashed") or [], "the fixture crashed one ending")
        em = caps["jd"].em
        # every ending, the CRASHED one included, is evicted in the finally, so both caches are EMPTY after the run; an evict on
        # the success path only would leave the crashed ending's document cached (== 1), which this pins out
        self.assertEqual((len(em._ASM_CACHE), len(em._JSONL_CACHE)), (0, 0), "the crashed ending is evicted too (the finally): both caches empty, not %d/%d" % (len(em._ASM_CACHE), len(em._JSONL_CACHE)))

    def test_a_budget_stopped_arm_is_not_finished(self):
        """Verifier low (d): finished is set only when the loop ran EVERY manifest ending; a budget stop leaves the run partial,
        so the report does not read it complete and excuses nothing."""
        dest, m = self._corpus(name="budgetstop")
        self._restore_arm_env()
        res = self.je.run_arm_inprocess(dest, "A", None, os.path.join(self.td, "r-budget"), 0.001, self.fake, now=T0 + 10**6)   # a tiny budget: stops after the first ending
        self.assertIsNotNone(res.get("stopped"), "the tiny budget stopped the arm")
        self.assertFalse(res.get("finished"), "a budget-stopped arm is NOT finished (it did not run every ending)")

    def test_the_prompt_hashes_and_judge_commit_are_stamped(self):
        """Queued stamp (PR 2092): results record the sha256 of each prompt key's EFFECTIVE text after apply_prompts and the
        harness commit, so a results file identifies which text ran where the prompt KEYS alone could not; a candidate's swapped
        key hashes differently from the baseline's shipped text."""
        dest, m = self._corpus(name="phash")
        self._restore_arm_env()
        base = self.je.run_arm_inprocess(dest, "baseline", None, os.path.join(self.td, "r-pb"), None, self.fake, now=T0 + 10**6)
        self.assertEqual(set(base["promptHashes"]), {"PLAN_SYS", "CLOSER_SYS", "UNBLOCK_SYS"}, "every prompt key's effective text is hashed")
        self.assertIn("judgePyCommit", base, "the harness/judge.py commit is recorded (a sha or None off a checkout)")
        cand_f = os.path.join(self.td, "cand.json"); Path(cand_f).write_text(json.dumps({"CLOSER_SYS": "CANDIDATE-MARK a different closer prompt"}))
        cand = self.je.run_arm_inprocess(dest, "candidate", cand_f, os.path.join(self.td, "r-pc"), None, self.fake, now=T0 + 10**6)
        self.assertNotEqual(cand["promptHashes"]["CLOSER_SYS"], base["promptHashes"]["CLOSER_SYS"], "the swapped candidate text hashes differently from the shipped closer")
        self.assertEqual(cand["promptHashes"]["PLAN_SYS"], base["promptHashes"]["PLAN_SYS"], "a key the candidate did not swap keeps the shipped hash")

    def test_the_report_parse_restores_the_process_environment(self):
        """Verifier low (c) + PR 2122 review: the report-time parse loads a judge against a scratch state root (load_judge sets
        XDG_STATE_HOME, CLAUDE_CONFIG_DIR, ROMP_CLAUDE_BIN, pops ROMP_STATE_DIR, DEFAULTS ROMP_POSTAL_CLIENT_ONLY, grows sys.path
        and loads the judge module against the scratch root), then removes the scratch dir; it must restore ALL of that so an
        in-process caller (the tests) is left with nothing pointed at the deleted root."""
        dest, m = self._corpus(name="envrestore")
        import sys as _sys
        keys = ("XDG_STATE_HOME", "CLAUDE_CONFIG_DIR", "ROMP_CLAUDE_BIN", "ROMP_STATE_DIR", "ROMP_POSTAL_CLIENT_ONLY")
        self._restore_arm_env(("ROMP_POSTAL_CLIENT_ONLY",))
        for k in keys:
            os.environ.pop(k, None)                              # start with all UNSET so a leftover set-value is visible
        for name in ("romp_judge_experiment_arm", "romp_logins", "secrets", "concurrent.futures"):
            _sys.modules.pop(name, None)                         # load/pull these FRESH so they enter the difference set: the repo modules to pop, the stdlib pair (secrets, concurrent.futures) to LEAVE
        before_env = {k: os.environ.get(k) for k in keys}
        before_path = list(_sys.path); before_mods = set(_sys.modules)
        self.je.plannable_units_from_corpus(dest, m, [e["id"] for e in m["endings"][:2]])
        self.assertEqual({k: os.environ.get(k) for k in keys}, before_env, "the parse restores every env var it set or defaulted (ROMP_POSTAL_CLIENT_ONLY included), leaving nothing pointed at the deleted scratch root")
        self.assertEqual(_sys.path, before_path, "the parse restores sys.path (load_judge inserts ROOT/tests)")
        # only the REPOSITORY's own modules the parse loaded are popped; never a stdlib module (whose identity a pop breaks)
        root = os.path.abspath(str(self.je.ROOT)) + os.sep
        left_repo = {k for k in set(_sys.modules) - before_mods
                     if (getattr(_sys.modules.get(k), "__file__", None) or "") and os.path.abspath(_sys.modules[k].__file__).startswith(root)}
        self.assertEqual(left_repo, set(), "every repository module the parse loaded is popped: %r" % left_repo)
        self.assertNotIn("romp_judge_experiment_arm", _sys.modules, "the arm judge module loaded against the scratch root is popped")
        for std in ("secrets", "concurrent.futures"):           # freshly pulled by the parse (popped above), so they ARE in the difference set: a broad pop removes them and reds this
            self.assertIn(std, _sys.modules, "a stdlib module the parse pulled in is NOT popped: %s (a broad pop would remove it, breaking its identity for later code)" % std)
        # a caller that HELD set values must get them BACK, not popped: four keys to sentinels, ROMP_POSTAL_CLIENT_ONLY unset (PR 2134 review pin 2)
        sentinels = {"XDG_STATE_HOME": "/sentinel/state", "CLAUDE_CONFIG_DIR": "/sentinel/claude", "ROMP_CLAUDE_BIN": "/sentinel/bin", "ROMP_STATE_DIR": "/sentinel/rompstate"}
        for k, v in sentinels.items():
            os.environ[k] = v
        os.environ.pop("ROMP_POSTAL_CLIENT_ONLY", None)
        self.je.plannable_units_from_corpus(dest, m, [e["id"] for e in m["endings"][:2]])
        self.assertEqual({k: os.environ.get(k) for k in sentinels}, sentinels, "a caller's SET env values are restored, not popped (a pop-every-key restore reds this)")
        self.assertIsNone(os.environ.get("ROMP_POSTAL_CLIENT_ONLY"), "an unset key stays unset")

    def test_the_report_parse_does_not_disturb_a_caller_held_judge_module(self):
        """PR 2134 review pin 3: a caller (run_arm_inprocess) already holds the judge module in sys.modules, bound to its own
        state root. The parse must not RE-EXECUTE that module in place against the scratch root: after the parse the caller's
        judge module is the SAME object with its state-root bindings (ERRORS) unchanged, not pointing into the deleted dir."""
        import sys as _sys
        dest, m = self._corpus(name="holdjudge")
        self._restore_arm_env()
        self.je.run_arm_inprocess(dest, "A", None, os.path.join(self.td, "r-hold"), None, self.fake, now=T0 + 10**6, builds=1)
        held = _sys.modules.get("romp_judge_experiment_arm")
        self.assertIsNotNone(held, "run_arm_inprocess leaves its judge module in sys.modules (the caller holds it)")
        errors_before = held.ERRORS
        self.je.plannable_units_from_corpus(dest, m, [e["id"] for e in m["endings"][:2]])
        self.assertIs(_sys.modules.get("romp_judge_experiment_arm"), held, "the caller's judge module is the SAME object after the parse")
        self.assertEqual(held.ERRORS, errors_before, "its state root is unchanged: the parse did not re-execute it in place against the (now deleted) scratch root")

    def test_the_failures_cell_names_the_unnamed_remainder_and_the_columns_read_not_recorded(self):
        """PR 2092/2099 lows: the failures cell names the remainder when the kinds sum to less than the count ('N unnamed', the
        closer's cap road files a failure without a row), a no-per-judge-record run prints 'not recorded' for the measured-calls
        column, and an absent retry tally prints 'not recorded' (never a false 0), while a recorded zero stays 0."""
        dest, m = self._corpus(name="cells")
        run_root = os.path.join(self.td, "runs-cells"); os.makedirs(run_root)
        eids = {x["id"]: {"class": x["class"], "builds": [{}] * 3} for x in m["endings"]}
        # an old/withdrawn record: no callsByJudge and no retry; two failures, one named by kind, one rowless (the closer's cap road)
        Path(run_root, "old").mkdir()
        Path(run_root, "old", "results.json").write_text(json.dumps(
            {"arm": "old", "failures": 2, "buildsPerCard": 3, "failuresByKind": {"parse": 1}, "endings": eids}))
        self.je.report(dest, run_root, str(self.state))
        table = (Path(run_root) / "table.md").read_text()
        self.assertIn("parse 1", table); self.assertIn("1 unnamed", table)   # the rowless remainder is named, the count no longer dropped
        self.assertIn("not comparable", table)
        row = [l for l in table.splitlines() if l.startswith("| old |")][0]
        self.assertIn("| not recorded |", row, "no per-judge record prints 'not recorded' for the measured-calls column, not 0/0/0/0")
        self.assertEqual(row.count("not recorded"), 4, "measured-calls plus the three retry columns all read 'not recorded' for this record: %r" % row)
    def test_a_plainly_cleared_completed_top_is_no_leak_and_a_reopened_needs_input_is_no_false_interrupt(self):
        """The plan's negatives (round three): a completed top the user plainly cleared (no re-open) is NOT a leak; a
        needs_input top the user re-opened, even if later cleared, is NOT a false interrupt (the re-open short-circuits)."""
        sid = SIDS[0]
        e = self._ending(self._corpus()[1], sid, 0)
        s, c = float(e["startT"]), float(e["cutT"])
        # a completed top the user only cleared (no followup): not a leak
        self._live_store_with_done(sid, s, c, [{"node": sid + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": c + 600}])
        manifest = {"endings": [e]}
        res_completed = {"arm": "x", "failures": 0, "endings": {e["id"]: {"builds": [{e["id"] + ":g1": {"column": "completed", "scored": True}}] * 2}}}
        self.assertEqual(self.je.measure(manifest, res_completed, self.state)["leaks"], 0, "a completed top the user only cleared is not a leak")
        # a needs_input top the user re-opened THEN cleared: the re-open short-circuits, not a false interrupt
        self._live_store_with_done(sid, s, c, [{"node": sid + ":g1", "op": "followup", "t": c + 300},
                                               {"node": sid + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": c + 600}])
        res_ni = {"arm": "x", "failures": 0, "endings": {e["id"]: {"builds": [{e["id"] + ":g1": {"column": "needs_input", "scored": True}}] * 2}}}
        self.assertEqual(self.je.measure(manifest, res_ni, self.state)["falseInterrupts"], 0, "a re-opened needs_input top is not a false interrupt")

    def test_a_mute_clear_is_not_a_false_interrupt_but_an_ordinary_cross_off_is(self):
        """H1: a hideFromFeed mute journals a src-user clear with the DISTINCT why the kernel now stamps (its
        _HIDDEN_FROM_FEED_WHY, excluded here EXACTLY), so a mute does not score. The ordinary feed Clear / Clear-all stamps
        the generic 'cleared from the feed', which IS the user's cross-off and scores a false interrupt."""
        self.assertEqual(self.je.MUTE_CLEAR_WHY, "hidden from the feed", "the harness excludes the mute's own why, not the generic one")
        sid = SIDS[1]
        e = self._ending(self._corpus()[1], sid, 0)
        s, c = float(e["startT"]), float(e["cutT"])
        manifest = {"endings": [e]}
        results = {"arm": "x", "failures": 0, "endings": {e["id"]: {"builds": [{e["id"] + ":g1": {"column": "needs_input", "scored": True}}] * 2}}}
        self._live_store_with_done(sid, s, c, [{"node": sid + ":g1", "op": "clear", "src": "user", "why": self.je.MUTE_CLEAR_WHY, "t": c + 600}])
        self.assertEqual(self.je.measure(manifest, results, self.state)["falseInterrupts"], 0, "a mute's clear is not the user's cross-off: no false interrupt")
        self._live_store_with_done(sid, s, c, [{"node": sid + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": c + 600}])
        self.assertEqual(self.je.measure(manifest, results, self.state)["falseInterrupts"], 1, "the ordinary cross-off's generic why scores a false interrupt")

    def test_gestured_endings_counts_only_endings_the_user_acted_on(self):
        """gesturedEndings counts endings the user acted on (a re-open or a cross-off), not merely endings with a placement."""
        sid = SIDS[0]
        e = self._ending(self._corpus()[1], sid, 0)
        s, c = float(e["startT"]), float(e["cutT"])
        self._live_store_with_done(sid, s, c, [])          # a placement in the turn, but the user did nothing to it
        manifest = {"endings": [e]}
        results = {"arm": "x", "failures": 0, "endings": {e["id"]: {"builds": [{e["id"] + ":g1": {"column": "completed", "scored": True}}] * 2}}}
        mm = self.je.measure(manifest, results, self.state)
        self.assertEqual((mm["gesturedEndings"], mm["untouchedEndings"], mm["unplacedEndings"], mm["unresolvedEndings"], mm["leaks"]), (0, 1, 0, 0, 0),
                         "a placement with no user gesture is untouched, not gestured or unplaced, and scores nothing: %r" % mm)
        self.assertEqual(mm["gesturedEndings"] + mm["untouchedEndings"] + mm["unplacedEndings"] + mm["unresolvedEndings"], mm["endings"],
                         "L2: the four ending columns partition every ending: %r" % mm)

    def test_an_unresolved_ending_scores_no_leak_or_interrupt_only_flaps(self):
        """An ending whose live session no longer lists (the manifest hash resolves to nothing) cannot be scored against the
        user's actions; it still contributes flaps (and cost)."""
        manifest = {"endings": [{"id": "gone", "session": "deadbeef0000", "lane": "deadbeef0000", "startT": T0, "cutT": T0 + 30}]}
        results = {"arm": "x", "failures": 0, "endings": {"gone": {"builds": [
            {"gone:g1": {"column": "completed", "scored": True}}, {"gone:g1": {"column": "needs_input", "scored": True}}]}}}
        mm = self.je.measure(manifest, results, self.state)
        self.assertEqual((mm["leaks"], mm["falseInterrupts"], mm["unresolvedEndings"], mm["flaps"]), (0, 0, 1, 1),
                         "unresolved: no leak or interrupt, but the column flap between the two builds still counts: %r" % mm)

    def test_flaps_run_over_the_scored_cards_only(self):
        manifest = {"endings": [{"id": "gone", "session": "deadbeef0000", "lane": "deadbeef0000", "startT": T0, "cutT": T0 + 30}]}
        results = {"arm": "x", "failures": 0, "endings": {"gone": {"builds": [
            {"g1": {"column": "completed", "scored": False}, "g2": {"column": "needs_input", "scored": True}},
            {"g1": {"column": "cleared", "scored": False}, "g2": {"column": "working", "scored": True}}]}}}
        self.assertEqual(self.je.measure(manifest, results, self.state)["flaps"], 1,
                         "the unscored g1 differs across builds but must not count; only the scored g2 flap does")

    # ── the store resolution and the fault record: the manifest fixes the store identity at build, so a later move of the
    #    live session cannot change which store the measures read; a corrupt or unreadable store, archive or journal is a
    #    recorded fault, never a silent miss.
    def _false_interrupt_case(self, sid, name):
        """A corpus and a live store where the arm left one needs_input top the user plainly crossed off (a src-user clear on
        g1 after the placement, no re-open, no unblocker ruling): a false interrupt unless a corruption intervenes."""
        m = self._corpus(name=name)[1]
        e = self._ending(m, sid, 0)
        s, c = float(e["startT"]), float(e["cutT"])
        self._live_store_with_done(sid, s, c, [{"node": sid + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": c + 600}])
        results = {"arm": "x", "failures": 0, "endings": {e["id"]: {"builds": [{e["id"] + ":g1": {"column": "needs_input", "scored": True}}] * 2}}}
        return m, results

    def test_a_names_entry_gone_still_resolves_the_store_through_the_manifest(self):
        """The manifest carries the store key, so an ending scores from the surviving live store even with the anchor's names
        entry removed; it is not counted unresolved (the round-one head resolved through the live names and could not)."""
        m, results = self._false_interrupt_case(SIDS[0], "fi-nonames")
        (self.state / "names" / SIDS[0]).unlink()          # the names entry gone, goals/<sid>.json survives
        mm = self.je.measure(m, results, self.state)
        self.assertEqual((mm["falseInterrupts"], mm["unresolvedEndings"]), (1, 0),
                         "the store resolves through the manifest key, so the plain cross-off still scores a false interrupt: %r" % mm)

    def test_a_missing_live_store_counts_unresolved(self):
        m, results = self._false_interrupt_case(SIDS[0], "fi-gone")
        (self.state / "goals" / (SIDS[0] + ".json")).unlink()   # the live store is gone entirely (no archive either)
        mm = self.je.measure(m, results, self.state)
        self.assertEqual((mm["unresolvedEndings"], mm["falseInterrupts"]), (1, 0), "a gone store is unresolved, scores nothing: %r" % mm)

    def test_a_corrupt_live_store_is_recorded_as_a_fault(self):
        m, results = self._false_interrupt_case(SIDS[0], "fi-corrupt")
        (self.state / "goals" / (SIDS[0] + ".json")).write_text("{ not json")
        mm = self.je.measure(m, results, self.state)
        h = hashlib.sha256(SIDS[0].encode()).hexdigest()[:12]
        self.assertEqual(mm["falseInterrupts"], 0)
        self.assertIn({"session": h, "error": "JSONDecodeError"}, mm["liveReadErrors"], "a corrupt live store is a recorded fault: %r" % mm["liveReadErrors"])
        self.assertNotIn(SIDS[0], json.dumps(mm["liveReadErrors"]), "the fault carries the hashed id, never the raw sid")

    def test_a_corrupt_archive_is_recorded_as_a_fault(self):
        m, results = self._false_interrupt_case(SIDS[0], "fi-corrupt-arch")
        (self.state / "goals" / (SIDS[0] + ".json")).unlink()   # only the archive remains, and it is corrupt
        (self.state / "goals-archive" / (SIDS[0] + ".json")).write_text("{ not json")
        mm = self.je.measure(m, results, self.state)
        h = hashlib.sha256(SIDS[0].encode()).hexdigest()[:12]
        self.assertTrue(any(f["session"] == h for f in mm["liveReadErrors"]), "a corrupt archive is a recorded fault: %r" % mm["liveReadErrors"])

    def test_an_unreadable_goals_file_is_recorded_as_a_fault(self):
        m, results = self._false_interrupt_case(SIDS[0], "fi-perm")
        gf = self.state / "goals" / (SIDS[0] + ".json")
        os.chmod(gf, 0o000); self.addCleanup(os.chmod, gf, 0o644)
        if os.access(gf, os.R_OK):
            self.skipTest("cannot deny read (running as root?)")
        mm = self.je.measure(m, results, self.state)   # must not raise
        self.assertTrue(mm["liveReadErrors"], "an unreadable goals file is recorded, not raised: %r" % mm["liveReadErrors"])

    def test_an_unreadable_overrides_journal_is_a_fault_not_a_raise(self):
        m, results = self._false_interrupt_case(SIDS[0], "fi-jperm")
        ov = self.state / "overrides" / (SIDS[0] + ".jsonl")
        os.chmod(ov, 0o000); self.addCleanup(os.chmod, ov, 0o644)
        if os.access(ov, os.R_OK):
            self.skipTest("cannot deny read (running as root?)")
        mm = self.je.measure(m, results, self.state)   # must not raise (an unreadable journal escaped as PermissionError before)
        self.assertEqual(mm["falseInterrupts"], 0)
        self.assertTrue(mm["liveReadErrors"], "the unreadable journal is recorded, not raised: %r" % mm["liveReadErrors"])

    def test_an_invalid_utf8_byte_in_the_journal_is_a_fault_not_a_raise(self):
        """M (post-merge): an invalid UTF-8 byte in an override journal decoded as a UnicodeDecodeError (a ValueError), which
        the OSError-only guard let escape, taking down every arm's table. It is now a recorded fault; the ending is unresolved."""
        m, results = self._false_interrupt_case(SIDS[0], "fi-badbyte")
        ov = self.state / "overrides" / (SIDS[0] + ".jsonl")
        ov.write_bytes(b'{"node": "%s:g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": 1}\n\xff\n' % SIDS[0].encode())
        mm = self.je.measure(m, results, self.state)   # must not raise
        h = hashlib.sha256(SIDS[0].encode()).hexdigest()[:12]
        self.assertEqual((mm["falseInterrupts"], mm["unresolvedEndings"]), (0, 1), "an unreadable journal is unscorable: %r" % mm)
        self.assertIn({"session": h, "error": "UnicodeDecodeError"}, mm["liveReadErrors"], "the bad byte is a recorded fault: %r" % mm["liveReadErrors"])

    def test_a_torn_journal_row_is_a_fault_and_the_row_on_its_OWN_next_line_still_reads(self):
        """A torn (rejected JSON) journal row on its OWN line is recorded as a torn-journal-row fault and skipped, and the
        row on the NEXT line still reads (a torn line of its own must not swallow the row after it silently)."""
        sid = SIDS[1]
        e = self._ending(self._corpus()[1], sid, 0)
        s, c = float(e["startT"]), float(e["cutT"])
        self._live_store_with_done(sid, s, c, [])
        ov = self.state / "overrides" / (sid + ".jsonl")
        ov.write_text("{ torn not json\n" + json.dumps({"node": sid + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": c + 600}) + "\n")
        manifest = {"endings": [e]}
        results = {"arm": "x", "failures": 0, "endings": {e["id"]: {"builds": [{e["id"] + ":g1": {"column": "needs_input", "scored": True}}] * 2}}}
        mm = self.je.measure(manifest, results, self.state)
        h = hashlib.sha256(sid.encode()).hexdigest()[:12]
        self.assertEqual(mm["falseInterrupts"], 1, "the good clear row after the torn one still read: a false interrupt: %r" % mm)
        self.assertIn({"session": h, "error": "torn-journal-row"}, mm["liveReadErrors"], "the torn row is a recorded fault: %r" % mm["liveReadErrors"])

    def test_a_newlineless_tear_records_one_fault_and_does_not_silently_swallow_the_next_row(self):
        """Review low 8: a torn fragment with NO trailing newline before the next row's JSON puts both on one physical line;
        the reader splits on newlines, so that line is one rejected row. The decision (intended over reconstruction): it
        records exactly one torn-journal-row fault, LOUD, not a silent skip; the merged clear is part of the torn line, so it
        is not read as a cross-off and the ending scores untouched. The fault is what keeps the swallow from being silent."""
        sid = SIDS[1]
        e = self._ending(self._corpus()[1], sid, 0)
        s, c = float(e["startT"]), float(e["cutT"])
        self._live_store_with_done(sid, s, c, [])
        ov = self.state / "overrides" / (sid + ".jsonl")
        ov.write_text("{ torn not json" + json.dumps({"node": sid + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": c + 600}) + "\n")
        manifest = {"endings": [e]}
        results = {"arm": "x", "failures": 0, "endings": {e["id"]: {"builds": [{e["id"] + ":g1": {"column": "needs_input", "scored": True}}] * 2}}}
        mm = self.je.measure(manifest, results, self.state)   # must not raise
        h = hashlib.sha256(sid.encode()).hexdigest()[:12]
        self.assertEqual([f for f in mm["liveReadErrors"] if f["session"] == h], [{"session": h, "error": "torn-journal-row"}],
                         "exactly one torn-journal-row fault is recorded for the newline-less tear: %r" % mm["liveReadErrors"])
        self.assertEqual(mm["falseInterrupts"], 0, "the clear merged into the torn line is not reconstructed: the ending scores untouched, not a false interrupt: %r" % mm)

    def test_tier_one_label_does_not_read_a_mute_clear_as_the_users_finish(self):
        """Item 3: tier_one_label shares the cross-off predicate, so a mute-only journal (a src-user clear with the mute's
        why on a done-in-window top) does not label the ending 'finished'; it is None (nothing the user did applies)."""
        sid = SIDS[0]
        e = self._ending(self._corpus()[1], sid, 0)
        s, c = float(e["startT"]), float(e["cutT"])
        self._live_store_with_done(sid, s, c, [{"node": sid + ":g1", "op": "clear", "src": "user", "why": self.je.MUTE_CLEAR_WHY, "t": c + 600}])
        self.assertIsNone(self.je.tier_one_label(self.state, sid, c, s), "a mute's clear is not the user's finish: tier one is None")
        self._live_store_with_done(sid, s, c, [{"node": sid + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": c + 600}])
        self.assertEqual(self.je.tier_one_label(self.state, sid, c, s), "finished", "an ordinary cross-off is the user's finish")

    def test_the_auth_preflight_runs_the_judges_road_and_is_the_sole_gate(self):
        """Review M: preflight_auth runs the judges' OWN auth road, built from jd._judge_cmd and jd._judge_env with the
        resolved billing (jd._judge_auth), not a bare `claude -p`, and is the SOLE gate: there is no hard apiKeyHelper-file
        refusal (which false-refused a login-road operator). The fake answers logged-in, so the probe passes and returns a
        cost note; with JE_TEST_NOAUTH it answers 'Not logged in' and preflight refuses, quoting the CLI's words (never
        empty braces) and naming the hand-placed-helper remedy, never a rebuild (the 2026-09-22 PR 2022 review, lows 3 and 4)."""
        dest, m = self._corpus(name="preflight")
        saved_env = dict(os.environ)
        try:
            jd = self.je.load_judge(Path(self.td, "pf-state"), Path(dest, "claude"), self.fake)
            probe = self.je.preflight_auth(jd, jd.TRIAGE_MODEL)   # the fake answers logged-in: no raise (the 2026-09-22 PR 2022 review, low e: no claude_bin param)
            self.assertIsInstance(probe, dict); self.assertIn("cost", probe); self.assertIn("sessionId", probe)
            self.assertEqual(probe["ms"], 7, "the probe note carries the envelope's duration_ms, not None or a string (the 2026-09-22 PR 2022 review, item 10): %r" % probe)
            os.environ["JE_TEST_NOAUTH"] = "1"
            with self.assertRaises(SystemExit) as cm:
                self.je.preflight_auth(jd, jd.TRIAGE_MODEL)
        finally:
            os.environ.clear(); os.environ.update(saved_env)
        msg = str(cm.exception)
        self.assertIn("Not logged in", msg, "the refusal quotes the CLI's own words: %r" % msg)
        self.assertNotIn("{}", msg, "never empty braces (the decode-failure branch keeps and quotes the process): %r" % msg)
        self.assertIn("hand-place", msg, "the refusal names the hand-placed-helper remedy: %r" % msg)
        self.assertIn("do NOT rebuild", msg, "the refusal tells the operator NOT to rebuild (a rebuild re-picks endings): %r" % msg)
        # the 2026-09-22 PR 2022 review, item 4: the login remedy is the ENVIRONMENT-token road (the child's config dir is the corpus root,
        # so a file-based `claude login` never reaches it); the remedy names the token vars, not "sign a login in on this machine"
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", msg, "the refusal names the environment login-token road: %r" % msg)
        self.assertNotIn("on this machine", msg, "the refusal does not point at a file-based machine login the probe's child cannot read: %r" % msg)

    def test_the_preflight_probe_runs_the_judge_flags_in_the_scratch_with_creds_stripped(self):
        """Review MED 2: the pre-flight probe runs the JUDGES' own command and env, not a bare `claude -p`. The fake records
        its argv, cwd and the sensitive env names present; the probe carries the judge flags, runs in the romp judge scratch
        cwd, and the ambient credential and 1Password/OP names are stripped (never a value observed). This gives the headline
        claims teeth: a bare-probe mutant (argv or env unstripped, cwd the checkout) turns a pin here red."""
        dest, m = self._corpus(name="pf-teeth")
        jd = self.je.load_judge(Path(self.td, "pf-teeth-state"), Path(dest, "claude"), self.fake)
        self._clear_log()
        saved_env = dict(os.environ)
        try:
            os.environ["ANTHROPIC_API_KEY"] = "synthetic-ambient-key"       # an ambient credential the judges' env strips
            os.environ["OP_SERVICE_ACCOUNT_TOKEN"] = "synthetic-op-token"   # a 1Password name that must not ride the child
            self.je.preflight_auth(jd, jd.TRIAGE_MODEL)
        finally:
            os.environ.clear(); os.environ.update(saved_env)
        probe = [r for r in self._calls() if r["judge"] == "other"]         # the probe's system prompt matches no judge: "other"
        self.assertTrue(probe, "the probe call was logged")
        argv = probe[0]["argv"]
        for flag in ("--safe-mode", "--strict-mcp-config", "--system-prompt", "--output-format"):
            self.assertIn(flag, argv, "the probe carries the judges' flag %s (not a bare claude -p): %r" % (flag, argv))
        # the corpus carries an apiKeyHelper, so the probe is key-billed and carries the judges' held key (2026-10-05), with
        # its own helper turned off; the ambient key and the 1Password/OP names never ride
        self.assertEqual(probe[0]["credPresent"], ["ANTHROPIC_API_KEY"],
                         "only the held key rides the probe env; the 1Password/OP names are stripped: %r" % probe[0]["credPresent"])
        self.assertFalse(probe[0]["ambientKeyRode"], "the key that rides is the held one, never the ambient credential")
        self.assertEqual(argv[-2:], ["--settings", '{"apiKeyHelper": ""}'], "the probe turns its helper off as the judges do: %r" % argv)
        self.assertEqual(os.path.realpath(probe[0]["cwd"]), os.path.realpath(str(jd._ensure_judge_scratch())),
                         "the probe runs in the romp judge scratch, not the checkout (realpath both sides: the temp dir is a symlink on macOS): %r" % probe[0]["cwd"])

    def test_the_preflight_probe_timeout_tracks_the_alarm(self):
        """Review round two low 3: the probe's timeout tracks the judge module's own alarm (CALL_ALARM_S + 5), not a
        hardcoded 130, so raising the alarm does not kill a slow healthy probe. Pinned by the timeout the probe passes to
        subprocess.run; a regression to 130 reddens it."""
        dest, m = self._corpus(name="pf-timeout")
        jd = self.je.load_judge(Path(self.td, "pf-timeout-state"), Path(dest, "claude"), self.fake)
        seen = {}
        real_run = self.je.subprocess.run
        def spy(cmd, **kw):
            seen["timeout"] = kw.get("timeout")
            return real_run(cmd, **kw)
        self.je.subprocess.run = spy
        try:
            self.je.preflight_auth(jd, jd.TRIAGE_MODEL)
        finally:
            self.je.subprocess.run = real_run
        self.assertEqual(seen["timeout"], jd.CALL_ALARM_S + 5, "the probe timeout is CALL_ALARM_S + 5, not a hardcoded 130: %r" % seen["timeout"])

    def test_the_report_note_names_the_non_arm_judges_from_the_constant(self):
        """Review round two low 6: the report's scoring note renders the exclusion list from NON_ARM_JUDGES, so the table and
        the constant stay one source (a hardcoded list would drift when the constant changes)."""
        dest, m = self._corpus(name="notesrc")
        run_root = os.path.join(self.td, "runs-notesrc")
        self.je.run_arm(dest, "baseline", None, run_root, None, self.fake, now=T0 + 10**6)
        self.je.report(dest, run_root, self.state, figure=None)
        table = Path(run_root, "table.md").read_text()
        self.assertIn(", ".join(self.je.NON_ARM_JUDGES), table, "the note lists the constant's judges verbatim: %r" % table[-300:])

    def test_the_preflight_passes_over_a_helper_less_corpus_on_a_login_token(self):
        """Review MED 2: the pre-flight is the SOLE gate, so a corpus with NO apiKeyHelper (the operator on the login road)
        passes on a synthetic login token, never a hard helper-file refusal. A helper-refusal-re-added mutant turns this
        red. The synthetic token is never printed."""
        empty_claude = Path(self.td, "loginroad-claude"); empty_claude.mkdir()   # no settings.json: no helper
        dest = os.path.join(self.td, "loginroad-corpus")
        self.je.build_corpus(self.state, empty_claude, dest, per_class=10, now=T0 + 10**6)
        self.assertFalse(Path(dest, "claude", "settings.json").exists(), "the corpus carries no helper (the login road)")
        jd = self.je.load_judge(Path(self.td, "loginroad-state"), Path(dest, "claude"), self.fake)
        saved_env = dict(os.environ)
        try:
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "synthetic-login-token"   # the login road's token; the fake answers logged-in
            probe = self.je.preflight_auth(jd, jd.TRIAGE_MODEL)               # no SystemExit: the missing helper is not a refusal
        finally:
            os.environ.clear(); os.environ.update(saved_env)
        self.assertIsInstance(probe, dict); self.assertIn("sessionId", probe)

    def test_the_preflight_quotes_a_plain_text_reply_without_empty_braces(self):
        """Review MED 2: when the probe's stdout is not JSON, the decode-failure branch quotes the CLI's own plain text, never
        empty braces. A decode-branch-back-to-braces mutant turns this red."""
        dest, m = self._corpus(name="pf-plain")
        jd = self.je.load_judge(Path(self.td, "pf-plain-state"), Path(dest, "claude"), self.fake)
        saved_env = dict(os.environ)
        try:
            os.environ["JE_TEST_PLAINTEXT"] = "1"
            with self.assertRaises(SystemExit) as cm:
                self.je.preflight_auth(jd, jd.TRIAGE_MODEL)
        finally:
            os.environ.clear(); os.environ.update(saved_env)
        msg = str(cm.exception)
        self.assertIn("the server is down for maintenance", msg, "the plain-text stdout is quoted: %r" % msg)
        self.assertNotIn("{}", msg, "no empty braces on a non-JSON reply: %r" % msg)

    def test_a_paid_arm_refuses_before_walking_endings_when_the_probe_is_not_logged_in(self):
        """Review low 2: the arm runs the judges' auth road once before its first ending; when that probe reads 'Not logged
        in', the arm refuses (a short storm, not one per ending) and writes no results.json, EVEN over a corpus that carries
        the helper (the probe is the gate now, not the file's presence)."""
        dest, m = self._corpus(name="noauth")
        self.assertTrue((Path(dest) / "claude" / "settings.json").is_file(),
                        "the corpus carries the user helper, so the refusal is the probe's, not a missing file")
        run_root = os.path.join(self.td, "runs-noauth")
        os.environ["JE_TEST_NOAUTH"] = "1"
        try:
            # the 2026-09-22 PR 2022 review: drive the in-process arm DIRECTLY, so the refusal is the pre-flight's SystemExit (not any proxy
            # error raised ahead of it), and it names the failure in its own words
            jd = self.je.load_judge(Path(self.td, "noauth-state"), Path(dest, "claude"), self.fake)
            with self.assertRaises(SystemExit) as cm:
                self.je.run_arm_inprocess(dest, "baseline", None, run_root, None, self.fake, now=T0 + 10**6)
            msg = str(cm.exception)
            self.assertIn("did not authenticate", msg, "the refusal is the pre-flight probe's own words: %r" % msg)
            self.assertIn("Not logged in", msg, "the refusal quotes the CLI: %r" % msg)
            self.assertFalse(os.path.exists(os.path.join(run_root, "baseline", "results.json")), "the in-process arm wrote no results record before refusing")
            # and the subprocess road propagates it as a CalledProcessError (the propagation check)
            with self.assertRaises(subprocess.CalledProcessError):
                self.je.run_arm(dest, "baseline", None, run_root, None, self.fake)
        finally:
            os.environ.pop("JE_TEST_NOAUTH", None)
        self.assertFalse(os.path.exists(os.path.join(run_root, "baseline", "results.json")), "refused before writing any ending")

    def test_a_failed_call_or_a_rejected_reply_marks_the_row_not_comparable(self):
        """Executed by the reviewer: a prompt whose replies the parser rejected scored the perfect row. The failures count and mark it."""
        dest, m = self._corpus()
        run_root = os.path.join(self.td, "runs")
        cand = os.path.join(self.td, "prose.json")
        Path(cand).write_text(json.dumps({"CLOSER_SYS": "CANDIDATE-MARK You are a turn-end auditor in a logging pipeline."}))
        os.environ["JE_TEST_PROSE"] = "1"
        res = self.je.run_arm(dest, "prose", cand, run_root, None, self.fake, now=T0 + 10**6)
        os.environ.pop("JE_TEST_PROSE", None)
        self.assertGreaterEqual(res.get("closerNone", 0), 4, "every closer reply was prose the parser rejected (the base counted nothing): %r" % res.get("closerNone"))
        self.assertGreaterEqual(res.get("failures", 0), res.get("closerNone", 0))
        mm = self.je.measure(m, res, self.state)
        self.assertIs(mm.get("comparable"), False, "a row with failures is not comparable: %r" % mm); self.assertEqual(mm.get("failures"), res["failures"])
        rows = self.je.report(dest, run_root, self.state, figure=None)
        table = Path(run_root, "table.md").read_text()
        self.assertIn("not comparable", table); self.assertIn("| prose |", table)
        current = self.je.run_arm(dest, "current", None, run_root, None, self.fake, now=T0 + 10**6)
        self.assertTrue(self.je.measure(m, current, self.state)["comparable"])

    def test_the_prompt_swap_reaches_the_calls_and_the_process_is_left_as_it_was(self):
        dest, m = self._corpus()
        run_root = os.path.join(self.td, "runs")
        cand = os.path.join(self.td, "candidate.json")
        Path(cand).write_text(json.dumps({"CLOSER_SYS": "CANDIDATE-MARK You are a turn-end auditor in a logging pipeline."}))
        saved_env = dict(os.environ)
        shared_before, state_before = sys.modules.get("romp_event_model"), getattr(em, "STATE", None)
        try:
            self.je.run_arm_inprocess(dest, "inproc", cand, run_root, None, self.fake, now=T0 + 10**6, builds=1)
            jd = sys.modules[self.je.JUDGE_MODULE_NAME]
            self.assertNotIn("CANDIDATE-MARK", jd.CLOSER_SYS, "the module attribute is restored after the run")
            self.assertIn("turn-end auditor", jd.CLOSER_SYS)
        finally:
            os.environ.clear(); os.environ.update(saved_env)
        self.assertIs(sys.modules.get("romp_event_model"), shared_before, "the shared event model is the one the process had")
        self.assertIs(em, shared_before); self.assertEqual(getattr(em, "STATE", None), state_before, "and its roots are where they were")
        rows = self._calls("closer")
        self.assertTrue(rows and all(r["candidate"] for r in rows), "every closer call carried the candidate's prompt: %r" % rows[:2])
        with self.assertRaises(SystemExit):
            self.je.apply_prompts(jd, {"NOT_A_PROMPT": "x"})

    def test_the_budget_stops_the_run_a_fifth_over_from_its_own_ledger(self):
        dest, m = self._corpus()
        run_root = os.path.join(self.td, "runs")
        full = self.je.run_arm(dest, "full", None, run_root, None, self.fake, now=T0 + 10**6)
        per_ending = full["cost"] / len(full["endings"])
        budget = per_ending / self.je.BUDGET_OVERRUN + 0.001            # one ending's cost sits under a fifth over; two endings' does not
        res = self.je.run_arm(dest, "tight", None, run_root, budget, self.fake, now=T0 + 10**6)
        self.assertIsNotNone(res["stopped"], "past a fifth over the budget the run stops")
        self.assertEqual(len(res["endings"]), 2, "the first ending runs inside the fifth, the second trips it (a stop at the bare budget would stop after one)")
        self.assertGreater(res["stopped"]["cost"], budget * self.je.BUDGET_OVERRUN)
        with self.assertRaises(SystemExit) as cm:
            self.je.main(["run", "--corpus", dest, "--run-root", run_root, "--arm", "x"])
        self.assertNotEqual(cm.exception.code, 0, "run without a binary and a budget refuses")
        with self.assertRaises(SystemExit) as cm:
            self.je.main(["run-arm", "--corpus", dest, "--run-root", run_root, "--arm", "x"])
        self.assertNotEqual(cm.exception.code, 0)
        inside = os.path.join(self.td, "repo3", "runs"); os.makedirs(os.path.join(self.td, "repo3", ".git"))
        with self.assertRaises(subprocess.CalledProcessError):
            self.je.run_arm(dest, "bad", None, inside, None, self.fake, now=T0 + 10**6)
        self.assertFalse(list(Path(inside).rglob("*")) if os.path.exists(inside) else False, "the subprocess entry refuses a run root inside a checkout and writes nothing")

    # ── the labeller and the report ──
    def test_the_label_pass_reads_tier_one_from_the_journals_and_reports_the_agreement(self):
        fn = getattr(self.je, "label", None)
        self.assertIsNotNone(fn, "the labeller is a subcommand of the harness")
        dest, m = self._corpus()
        e_a = self._ending(m, SIDS[0], 0)                # the offer: the closer filed done, the user came back with a followup
        self._live_store_with_done(SIDS[0], e_a["startT"], e_a["cutT"], [{"node": SIDS[0] + ":g1", "op": "followup", "t": e_a["cutT"] + 7200}])
        e_b = self._ending(m, SIDS[1], 1)                # the finished thread: done, then the user cleared it and nothing more
        self._live_store_with_done(SIDS[1], e_b["startT"], e_b["cutT"], [{"node": SIDS[1] + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": e_b["cutT"] + 600}])
        run_root = os.path.join(self.td, "runs")
        summary = fn(dest, run_root, self.state, claude_bin=self.fake, model="fake")
        rows = {r["id"]: r for r in json.loads(Path(run_root, "labels.json").read_text())}
        self.assertEqual(rows[e_a["id"]]["tierOne"], "not finished", "a followup after the judges' done: not finished")
        self.assertEqual(rows[e_b["id"]]["tierOne"], "finished", "a clear with nothing after: finished")
        self.assertTrue(all(r["spanS"] > 0 for r in rows.values()), "the observation span is recorded per ending")
        self.assertEqual((summary["endings"], summary["tierOneLabelled"], summary["both"], summary["agree"]), (4, 2, 2, 2), summary)
        self.assertEqual((summary["labellerStable"], summary["stablePct"], summary["gatePassed"]), (4, 100.0, True),
                         "road (b): the gate is STABILITY (all four labelled the same in both orders): %r" % summary)
        self.assertEqual((summary["agreementPct"], summary["heuristicMatchesLabel"]), (100.0, 4),
                         "the agreement with the user's actions is REPORTED, not gated: %r" % summary)
        ledger = [json.loads(l) for l in Path(run_root, "labeller-ledger.jsonl").read_text().splitlines() if l.strip()]
        self.assertEqual((len(ledger), round(sum(r["cost"] for r in ledger), 2)), (8, 0.08), "two calls per ending, each on the ledger")
        # a followup nine days later still says not finished: the label keys on events, never on a window
        self._live_store_with_done(SIDS[1], e_b["startT"], e_b["cutT"], [{"node": SIDS[1] + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": e_b["cutT"] + 600},
                                                                          {"node": SIDS[1] + ":g1", "op": "followup", "t": e_b["cutT"] + 9 * 86400}])
        self.assertEqual(self.je.tier_one_label(self.state, SIDS[1], e_b["cutT"], e_b["startT"]), "not finished")
        with self.assertRaises(SystemExit):
            inside = os.path.join(self.td, "repo2", "runs"); os.makedirs(os.path.join(self.td, "repo2", ".git"))
            fn(dest, inside, self.state, claude_bin=self.fake, model="fake")

    def test_the_figure_marks_an_arm_that_is_not_comparable(self):
        rows = [{"arm": "current", "leaks": 3, "falseInterrupts": 0, "flaps": 0, "costUsd": 0.1, "failures": 0, "comparable": True},
                {"arm": "prose", "leaks": 0, "falseInterrupts": 0, "flaps": 0, "costUsd": 0.1, "failures": 4, "comparable": False}]
        seen = []
        class _Ax:
            def barh(self, *a, **k): pass
            def set_yticks(self, *a): pass
            def set_yticklabels(self, labels): seen.append(list(labels))
            def invert_yaxis(self): pass
            def annotate(self, *a, **k): pass
            def set_xlim(self, *a): pass
        class _Fig:
            def savefig(self, out, **k): Path(out).write_bytes(b"PNG")
        stub = types.ModuleType("cleanplots"); stub.fig = lambda **k: (_Fig(), [_Ax() for _ in range(k.get("cols", 5))])
        saved = sys.modules.get("cleanplots"); sys.modules["cleanplots"] = stub
        try:
            self.je.draw_figure(rows, os.path.join(self.td, "f.png"))
        finally:
            if saved is not None:
                sys.modules["cleanplots"] = saved
            else:
                sys.modules.pop("cleanplots", None)
        self.assertIn(["current", "prose (not comparable)"], seen, "the headline artifact says which arm cannot be read: %r" % seen)

    def test_the_report_writes_the_table_and_the_figure_road_both_ways(self):
        dest, m = self._corpus()
        run_root = os.path.join(self.td, "runs")
        e = self._ending(m, SIDS[0], 0)                  # the current prompt completes this top; the user re-opened it: the one leak
        self._live_store_with_done(SIDS[0], float(e["startT"]), float(e["cutT"]), [{"node": SIDS[0] + ":g1", "op": "followup", "t": float(e["cutT"]) + 7200}])
        self.je.run_arm(dest, "current", None, run_root, None, self.fake, now=T0 + 10**6)
        rows = self.je.report(dest, run_root, self.state, figure=None)
        table = Path(run_root, "table.md").read_text()
        self.assertEqual([r["arm"] for r in rows], ["current"])
        self.assertEqual((rows[0]["endings"], rows[0]["gesturedEndings"], rows[0]["untouchedEndings"], rows[0]["unplacedEndings"],
                          rows[0]["unresolvedEndings"], rows[0]["leaks"], rows[0]["falseInterrupts"], rows[0]["answeredThenCleared"], rows[0]["flaps"]),
                         (4, 1, 0, 1, 2, 1, 0, 0, 0),
                         "SIDS[0] turn0 scores the leak; turn1 is unplaced (its live tops sit outside the turn); SIDS[1] has no live store: %r" % rows[0])
        self.assertEqual(rows[0]["gesturedEndings"] + rows[0]["untouchedEndings"] + rows[0]["unplacedEndings"] + rows[0]["unresolvedEndings"],
                         rows[0]["endings"], "the four ending columns partition every ending: %r" % rows[0])
        self.assertIn("| current | 4 | 1 | 0 | 1 | 2 | 1 | 0 | 0 | 0 |", table)
        for text in self.texts:
            self.assertNotIn(text[:24], table)
        self.assertNotIn("The synthetic goal", table, "no goal title in the report")
        # the shared report carries counts only: no raw session id, cwd or goal title reaches the table or measures.json
        measures = Path(run_root, "measures.json").read_text()
        for leak in (SIDS[0], SIDS[1], self.cwd):
            self.assertNotIn(leak, table, "no raw identity in the shared table")
            self.assertNotIn(leak, measures, "no raw identity in measures.json")
        out = subprocess.run([sys.executable, SCRIPT, "report", "--corpus", dest, "--run-root", run_root,
                              "--live-state", str(self.state)], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr[-500:]); self.assertIn('"leaks": 1', out.stdout)
        # the figure road: a stub library writes the file; no library leaves the note
        png = os.path.join(run_root, "fig.png")
        drawn = []
        seen_labels = []
        class _Ax:
            def barh(self, *a, **k): pass
            def set_yticks(self, *a): pass
            def set_yticklabels(self, labels): seen_labels.append(list(labels))
            def invert_yaxis(self): pass
            def annotate(self, *a, **k): pass
            def set_xlim(self, *a): pass
            def clean(self, **k): pass
        class _Fig:
            def savefig(self, out, **k): drawn.append(out); Path(out).write_bytes(b"PNG")
        panels = []
        def fig(**k):
            panels.append(k.get("cols")); return _Fig(), [_Ax() for _ in range(k.get("cols", 4))]
        stub = types.ModuleType("cleanplots"); stub.fig = fig
        saved = sys.modules.get("cleanplots"); sys.modules["cleanplots"] = stub
        try:
            self.je.report(dest, run_root, self.state, figure=png)
        finally:
            if saved is not None:
                sys.modules["cleanplots"] = saved
            else:
                sys.modules.pop("cleanplots", None)
        self.assertEqual(drawn, [png]); self.assertTrue(Path(png).exists(), "the figure is written through the library")
        self.assertEqual(panels, [5], "five panels: the four measures and the failures")
        self.assertIn(["current"], seen_labels, "a comparable arm is named plainly")
        self.assertFalse(Path(run_root, "figure.note").exists())
        import builtins
        real_import = builtins.__import__
        def no_cleanplots(name, *a, **k):
            if name == "cleanplots":
                raise ImportError("no cleanplots here")
            return real_import(name, *a, **k)
        builtins.__import__ = no_cleanplots
        try:
            self.je.report(dest, run_root, self.state, figure=os.path.join(run_root, "fig2.png"))
        finally:
            builtins.__import__ = real_import
        self.assertIn("no figure", Path(run_root, "figure.note").read_text())
        self.assertFalse(Path(run_root, "fig2.png").exists())

    # ── round two of the fold ──
    def test_turn_boundaries_are_the_event_models_over_every_record_kind(self):
        """Round three: the copied opener rule reproduced three of the fold's refusals and none of the command-twin,
        local-command, skill-content or restore-replay handling, so an SDK corpus split turns the event model folds. The
        builder now takes its boundaries from `em.parse_session` itself; this pin drives a transcript carrying a wrapper of
        every kind between the assistant answers and asserts the builder's ends and starts equal the event model's own ended
        turns (mapped atoms to record indices the same way), and that a mid-turn wrapper leaves one turn with its start at the
        prompt."""
        sid = SIDS[0]
        # one turn's worth per opener kind, each followed by an assistant answer, all chained; wrappers between are non-openers
        recs = []
        t = [T0]
        prev = [None]
        def add(rec):
            rec = dict(rec); rec["parentUuid"] = prev[0]; recs.append(rec); prev[0] = rec["uuid"]; t[0] += 60
        def usr(text, uid, **f):
            add(dict({"type": "user", "timestamp": iso(t[0]), "uuid": uid, "sessionId": sid, "cwd": "/TESTDIR",
                      "promptSource": "typed", "message": {"role": "user", "content": text}}, **f))
        def asst(text, uid):
            add({"type": "assistant", "timestamp": iso(t[0]), "uuid": uid, "sessionId": sid, "cwd": "/TESTDIR",
                 "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}})
        # a real opener, then wrappers that must NOT open, interleaved with answers
        usr("first ask", "u1", promptSource="typed"); asst("first answer", "a1")
        usr("<system-reminder>\nnoise\n</system-reminder>", "w1", promptSource="typed"); asst("noted", "a2")
        usr("[SYSTEM NOTIFICATION - NOT USER INPUT]\na task finished", "w2", promptSource="sdk", origin={"kind": "task-notification"}); asst("saw it", "a3")
        usr("Another Claude session sent a message: ping", "w3", promptSource="sdk"); asst("pong", "a4")
        usr("<command-name>/usage</command-name>", "w4", isMeta=True, promptSource="typed"); asst("usage shown", "a5")
        usr("[Image: a screenshot]", "w5", isMeta=True); asst("looked", "a6")
        usr("second ask", "u2", promptSource="sdk"); asst("second answer", "a7")   # a composer (text-string) opener
        pth = os.path.join(self.td, "parity.jsonl"); open(pth, "w").write("".join(json.dumps(r) + "\n" for r in recs))
        _recs, endings = self.je.session_endings(pth, sid)
        # the event model's own ended turns the MODEL WORKED (an assistant atom not command-flagged), mapped the same way
        sess = em.parse_session(pth, rompuuid=sid)
        uuid_idx = {r.get("uuid"): i for i, r in enumerate(recs)}
        def worked(tn):
            return any(a.get("type") == "assistant" and not a.get("command") for a in tn["atoms"])
        want = sorted((max(uuid_idx[a["uuid"]] for a in tn["atoms"] if a.get("uuid") in uuid_idx),
                       float(tn["t"]) if tn.get("trigger") else None)
                      for tn in sess["turns"] if tn.get("ended") and worked(tn) and any(a.get("uuid") in uuid_idx for a in tn["atoms"]))
        self.assertEqual([(i, st) for i, st, _tx in endings], want, "the builder's boundaries are the event model's own worked turns")
        self.assertTrue(endings, "the transcript has ended turns")
        # a wrapper mid-turn: one prompt, a non-final assistant, a system-reminder, the final assistant → ONE ended turn at the prompt
        mid = [{"type": "user", "timestamp": iso(T0), "uuid": "p1", "parentUuid": None, "sessionId": sid, "cwd": "/TESTDIR",
                "promptSource": "typed", "message": {"role": "user", "content": "do the thing"}},
               {"type": "assistant", "timestamp": iso(T0 + 30), "uuid": "m1", "parentUuid": "p1", "sessionId": sid, "cwd": "/TESTDIR",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "working"}], "stop_reason": "tool_use"}},
               {"type": "user", "timestamp": iso(T0 + 40), "uuid": "sr", "parentUuid": "m1", "sessionId": sid, "cwd": "/TESTDIR",
                "promptSource": "typed", "message": {"role": "user", "content": "<system-reminder>\nnoise\n</system-reminder>"}},
               {"type": "assistant", "timestamp": iso(T0 + 50), "uuid": "m2", "parentUuid": "sr", "sessionId": sid, "cwd": "/TESTDIR",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"}}]
        pth2 = os.path.join(self.td, "midturn.jsonl"); open(pth2, "w").write("".join(json.dumps(r) + "\n" for r in mid))
        _r2, e2 = self.je.session_endings(pth2, sid)
        self.assertEqual(len(e2), 1, "the mid-turn system-reminder does not split the turn: %r" % e2)
        self.assertEqual((mid[e2[0][0]]["uuid"], e2[0][1]), ("m2", float(T0)), "one ending at the final assistant, its start at the prompt")
        self.assertEqual(e2[0][2], "done", "the last non-command assistant text is carried")

    def test_eligible_endings_are_picked_oldest_first(self):
        """Round two: the branches were swapped, so the eligible endings came newest first, the ones the user had had the least
        time to act on. With three eligible offers and one slot, the oldest wins."""
        sid3 = "11111111-2222-3333-4444-eeeeeeeeee03"
        recs, t, parent = [], T0 + 50000, None
        for j in range(3):
            recs.append(uline(sid3, t, "ask %d" % j, "u3%d" % j, parent)); recs.append(aline(sid3, t + 30, "Done %d. I can also tidy the names." % j, "a3%d" % j, "u3%d" % j))
            parent = "a3%d" % j; t += 600
        (self.pdir / (sid3 + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in recs))
        (self.state / "names" / sid3).write_text("api\t%s\t#abcdef\n" % self.cwd)
        # every offer of the third session completed by the judges in its own turn
        ends = self.je.turn_ends(recs)
        log = [{"ev_t": self.je.turn_start(recs, i), "at": self.je._ts(recs[i]) + 2, "src": "closer", "kind": "done", "why": "x"} for i in ends]   # ev_t at the turn's start, at the arrival (the cut)
        store = {"rompUuid": sid3, "seq": 1, "placementsV": PV, "placements": {}, "status": {},
                 "nodes": {sid3 + ":g1": {"id": sid3 + ":g1", "text": "The goal", "parentId": None, "t": T0 + 49000, "trail": [], "log": log}}}
        (self.state / "goals" / (sid3 + ".json")).write_text(json.dumps(store))
        m = self._corpus(per_class=1, name="oldest")[1]
        offers = [e for e in m["endings"] if e["class"] == "offer"]
        self.assertEqual([(e["session"], e["turn"], e["tierOneEligible"]) for e in offers],
                         [(hashlib.sha256(sid3.encode()).hexdigest()[:12], 0, True)], "the oldest eligible offer, not the newest: %r" % offers)

    def test_a_pre_turn_top_the_arm_leaves_alone_is_not_scored(self):
        """`scored` pinned: an older top the user CLEARED before the turn (off the arm's menu, so the arm files nothing on it)
        reads unscored, its inherited `cleared` column counting against no measure; the turn's own card, which the arm rules
        on this build, scores. The arm's own filing (`at` == the pass's now) parts the two from the seed's earlier `at`."""
        sid = SIDS[0]
        m = self._corpus()[1]
        e = self._ending(m, sid, 1)
        start = float(e["startT"])
        seg0 = "%s:%d:aaaaaaaa" % (sid, start - 5000)
        cleared = {"id": sid + ":g1", "text": "An older goal the user cleared", "parentId": None, "t": start - 5000, "trail": [seg0],
                   "log": [{"ev_t": start - 4000, "at": start - 3999, "src": "user", "kind": "clear", "why": "seen"}]}
        store = {"rompUuid": sid, "seq": 1, "placementsV": PV, "placements": {seg0: sid + ":g1"}, "status": {}, "nodes": {cleared["id"]: cleared},
                 "closedTurns": [], "closedSig": {}}
        (self.state / "goals" / (sid + ".json")).write_text(json.dumps(store))
        dest, m = self._corpus(name="corpus-scored")
        run_root = os.path.join(self.td, "runs")
        res = self.je.run_arm(dest, "current", None, run_root, None, self.fake, now=T0 + 10**6)
        build = res["endings"][self._ending(m, sid, 1)["id"]]["builds"][-1]
        self.assertTrue(all(isinstance(v, dict) and "scored" in v for v in build.values()),
                        "each card carries a scored stamp (the base recorded a bare column): %r" % build)
        cleared_card = build.get("g1")
        self.assertIsNotNone(cleared_card, "the inherited cleared top is in the build: %r" % build)
        self.assertEqual((cleared_card["column"], cleared_card["scored"]), ("cleared", False),
                         "the cleared top the arm leaves alone reads cleared and unscored: %r" % build)
        self.assertTrue(any(v["scored"] for n, v in build.items() if n != "g1"), "the turn's own card scores: %r" % build)
        self.assertEqual(self.je.measure(m, res, self.state)["falseInterrupts"], 0, "the untouched inherited blocked top counts against no finished ending")

    def test_the_seed_is_rolled_up_before_the_first_menu(self):
        """Round two: without the rollup before the first judge, the planner's first menu listed a pre-cut done sub and a
        user-cleared top (the flag cache the cut strips is what `open_menu` reads). The fake logs the menu it sees."""
        sid = SIDS[0]
        m = self._corpus()[1]
        e = self._ending(m, sid, 1)
        start = float(e["startT"])
        seg0 = "%s:%d:aaaaaaaa" % (sid, start - 5000)
        top = {"id": sid + ":g1", "text": "An open top with a finished part", "parentId": None, "t": start - 5000, "trail": [seg0],
               "log": []}
        sub = {"id": sid + ":g2", "text": "The finished part nobody should see", "parentId": sid + ":g1", "t": start - 4900, "trail": [seg0],
               "log": [{"ev_t": start - 4000, "at": start - 3999, "src": "closer", "kind": "done", "why": "x"}]}
        cleared = {"id": sid + ":g3", "text": "A top the user crossed off", "parentId": None, "t": start - 4800, "trail": [seg0],
                   "log": [{"ev_t": start - 3000, "at": start - 2999, "src": "user", "kind": "clear", "why": "seen"}]}
        store = {"rompUuid": sid, "seq": 3, "placementsV": PV, "placements": {seg0: sid + ":g1"}, "status": {},
                 "nodes": {n["id"]: n for n in (top, sub, cleared)}, "closedTurns": [], "closedSig": {}}
        (self.state / "goals" / (sid + ".json")).write_text(json.dumps(store))
        dest, m = self._corpus(name="corpus-menu")
        run_root = os.path.join(self.td, "runs")
        self.je.run_arm(dest, "current", None, run_root, None, self.fake, now=T0 + 10**6)
        menus = [r["menu"] for r in self._calls("planner") if r["menu"] and "An open top" in r["menu"]]
        self.assertTrue(menus, "the planner saw the seeded top: %r" % [r["menu"][:60] for r in self._calls("planner")])
        for menu in menus:
            self.assertNotIn("The finished part nobody should see", menu, "the pre-cut done sub is off the first menu")
            self.assertNotIn("A top the user crossed off", menu, "the cleared top is off the first menu")

    def test_failure_rows_of_every_kind_that_means_nothing_was_judged_count_once(self):
        counter = getattr(self.je, "count_failure_rows", None)
        self.assertIsNotNone(counter, "the arm counts only the judge-errors rows that mean nothing was judged (the base folded the count into the loop)")
        ledger = os.path.join(self.td, "judge-errors.jsonl")
        Path(ledger).write_text("".join(json.dumps({"err": k}) + "\n" for k in ("parse", "auth", "rate-limited", "fast-refused", "scratch", "stale-close", "workless-done", "timeout")))
        self.assertEqual(counter(ledger), 5, "the pause kinds and the call-level stand-downs count; anomaly notes do not; timeout is no kind the judges write")
        for k in ("auth", "rate-limited", "fast-refused", "scratch", "call", "parse", "give-up", "pass-crash", "unregistered-caller"):
            self.assertIn(k, self.je.FAILURE_KINDS)
        self.assertNotIn("timeout", self.je.FAILURE_KINDS)
        # comparability EXCLUDES only the named non-arm reshaping judges; every other failure row counts. An allowlist of the
        # three arm judges would wrongly drop the opener and placer (which the arm also runs) and the kinds that carry a tier
        # or "romp" instead of the judge name (rate-limited -> "triage", history-unreadable -> "romp").
        self.assertEqual(self.je.NON_ARM_JUDGES, ("grouper", "consolidator", "distiller", "gister"),
                         "review low a: the reshaping/summarizing judges plus the gister (its _followup_title call) are excluded")
        rows = [{"err": "call", "judge": "placer"}, {"err": "call", "judge": "opener"},
                {"err": "rate-limited", "judge": "triage"}, {"err": "history-unreadable", "judge": "romp"},
                {"err": "call", "judge": "planner"},
                {"err": "call", "judge": "grouper"}, {"err": "call", "judge": "consolidator"}, {"err": "give-up", "judge": "distiller"},
                {"err": "call", "judge": "gister"}]     # review low a: a failed gister call (no cached gist) does not mark the arm not comparable
        mixed = os.path.join(self.td, "judge-errors-mixed.jsonl")
        Path(mixed).write_text("".join(json.dumps(r) + "\n" for r in rows))
        self.assertEqual(counter(mixed), 5, "placer, opener, triage (rate-limited) and romp (history-unreadable) count with the "
                         "planner; the grouper, consolidator, distiller and gister rows do not")
        # the 2026-09-22 PR 2022 review (nonArmFailures): the excluded rows are counted too, so an excluded fault is never invisible
        self.assertEqual(self.je.count_non_arm_failure_rows(mixed), 4, "the four non-arm rows (grouper, consolidator, distiller, gister) are counted apart")
        # a rejected closer reply files its own row: it is counted once, not once as a row and once as a None
        dest, m = self._corpus()
        run_root = os.path.join(self.td, "runs")
        cand = os.path.join(self.td, "prose.json")
        Path(cand).write_text(json.dumps({"CLOSER_SYS": "CANDIDATE-MARK You are a turn-end auditor in a logging pipeline."}))
        os.environ["JE_TEST_PROSE"] = "1"
        res = self.je.run_arm(dest, "prose", cand, run_root, None, self.fake, now=T0 + 10**6)
        os.environ.pop("JE_TEST_PROSE", None)
        self.assertGreaterEqual(res["closerNone"], 1, "the closer was reached and its prose rejected")
        arm_ledger = os.path.join(run_root, "prose", "state", "romp", "judge-errors.jsonl")
        self.assertEqual(res["failures"], self.je.count_failure_rows(arm_ledger),
                         "failures is the ledger's own count, never the rows plus the Nones (a planner parse row would break an equality with closerNone): %r" % res["failures"])

    def test_the_turns_own_live_and_extra_target_placements_are_the_arms_to_make(self):
        sid = SIDS[0]
        m = self._corpus()[1]
        e = self._ending(m, sid, 0)
        start, cut = float(e["startT"]), float(e["cutT"])
        segp = "%s:%d:bbbbbbbb" % (sid, start)
        node = {"id": sid + ":g2", "text": "The turn's own goal", "parentId": None, "t": start, "trail": [segp], "log": []}
        store = {"rompUuid": sid, "seq": 2, "placementsV": PV, "status": {}, "nodes": {node["id"]: node},
                 "placements": {segp + "#p": node["id"], segp + "#d": node["id"], segp: node["id"], segp + "#live": node["id"], segp + "#n2": node["id"]}}
        try:
            before = self.je.store_before(store, cut, start, e["id"])
        except TypeError:
            self.fail("store_before cuts at the turn's start under the ending id (the base cut at the cut alone)")
        self.assertEqual(sorted(k.rsplit("#", 1)[-1] for k in before["placements"]), ["d", "p"], "only the prompt-run and the delegation keys survive from the turn")

    def test_the_same_titled_fork_lanes_join_the_sessions_transcripts(self):
        sid = SIDS[0]; fork = "11111111-2222-3333-4444-ffffffffff09"
        recs = [{"type": "custom-title", "customTitle": "web", "sessionId": fork, "timestamp": iso(T0 + 80000)},
                uline(fork, T0 + 80000, "in the fork", "u1"), aline(fork, T0 + 80030, "Forked and answered. Which option do you prefer?", "a1", "u1"),
                uline(fork, T0 + 80600, "and more", "u2", "a1"), aline(fork, T0 + 80630, "Done.", "a2", "u2")]
        (self.pdir / (fork + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in recs))
        other = "11111111-2222-3333-4444-ffffffffff08"
        (self.pdir / (other + ".jsonl")).write_text(json.dumps({"type": "custom-title", "customTitle": "somebody else", "sessionId": other}) + "\n")
        fl = getattr(self.je, "fork_lanes", None)
        self.assertIsNotNone(fl, "the builder reads the same-titled fork lanes beside the registry's fsids (the base read neither)")
        self.assertEqual(fl(self.pdir, "web", {sid}), [fork], "a same-titled transcript is a lane; another title is not")
        m = self._corpus(name="forks")[1]
        h = hashlib.sha256(sid.encode()).hexdigest()[:12]
        self.assertEqual(sum(1 for e in m["endings"] if e["session"] == h), 4, "the fork's two endings join the anchor's two")

    def test_the_skip_counters_count(self):
        (self.state / "names" / "11111111-2222-3333-4444-eeeeeeeeee07").write_text("gone\t%s\t#abcdef\n" % os.path.join(self.td, "nowhere"))
        (self.state / "names" / "11111111-2222-3333-4444-eeeeeeeeee08").write_text("one-field-only\n")
        short = "11111111-2222-3333-4444-eeeeeeeeee09"
        (self.pdir / (short + ".jsonl")).write_text(json.dumps(uline(short, T0, "only ask", "u1")) + "\n" + json.dumps(aline(short, T0 + 30, "Only answer.", "a1", "u1")) + "\n")
        (self.state / "names" / short).write_text("short\t%s\t#abcdef\n" % self.cwd)
        m = self._corpus(name="skips")[1]
        self.assertIn("skipped", m, "the manifest counts skips (the base wrote none)")
        self.assertEqual(m["skipped"], {"no-transcript": 1, "few-turns": 1, "unreadable-names-entry": 1, "parse-failed": 0, "store-unreadable": 0, "registry-unreadable": 0})
        self.assertNotIn("nowhere", json.dumps(m["skipped"]))

    def test_the_label_entry_takes_no_default_binary(self):
        import inspect
        sig = inspect.signature(self.je.label)
        self.assertIs(sig.parameters["claude_bin"].default, inspect.Parameter.empty, "the binary is the caller's to name, on every road")

    def test_the_shared_event_models_checkpoint_provider_survives_an_in_process_arm(self):
        dest, m = self._corpus()
        run_root = os.path.join(self.td, "runs")
        cand = os.path.join(self.td, "candidate.json")
        Path(cand).write_text(json.dumps({"CLOSER_SYS": "CANDIDATE-MARK You are a turn-end auditor in a logging pipeline."}))
        self.assertTrue(hasattr(em, "_CKPT_DIR_FN"), "the event model has a checkpoint-dir provider slot (the arm's judge could leave its own)")
        provider_before = em._CKPT_DIR_FN               # None at the floor: the test wires no kernel
        saved_env = dict(os.environ)
        try:
            self.je.run_arm_inprocess(dest, "inproc2", cand, run_root, None, self.fake, now=T0 + 10**6, builds=1)
        finally:
            os.environ.clear(); os.environ.update(saved_env)
        self.assertIs(em._CKPT_DIR_FN, provider_before, "the arm's judge left the shared provider as it was, not its own scratch-rooted one")

    def test_a_parse_failure_is_counted_and_logged_never_swallowed(self):
        """The manager's round-three fold: session_endings must not swallow a parse failure (the repo's fail-loud rule). A
        transcript that makes the event model raise is counted under skipped['parse-failed'] with no sid, and its type logged."""
        import io, contextlib
        sid = "11111111-2222-3333-4444-eeeeeeeeee05"
        (self.pdir / (sid + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in
            [uline(sid, T0, "ask", "u1"), aline(sid, T0 + 30, "answer", "a1", "u1"), uline(sid, T0 + 600, "again", "u2", "a1"), aline(sid, T0 + 630, "ok", "a2", "u2")]))
        (self.state / "names" / sid).write_text("boom\t%s\t#abcdef\n" % self.cwd)
        real = self.je._event_model()
        class _Boom:
            def __getattr__(self, n):
                return getattr(real, n)            # delegate everything else (evict_document, etc.) to the real event model
            def parse_session(self, path, **k):
                if sid in str(path):
                    raise ValueError("a transcript the fold cannot read")
                return real.parse_session(path, **k)
        self.je._EM[0] = _Boom()
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                m = self._corpus(name="parsefail")[1]
        finally:
            self.je._EM[0] = real
        self.assertEqual(m["skipped"]["parse-failed"], 1, "the unparseable session is counted, not dropped in silence")
        self.assertIn("could not parse", err.getvalue()); self.assertIn("ValueError", err.getvalue())
        self.assertNotIn(sid, json.dumps(m["skipped"]) + json.dumps([e["id"] for e in m["endings"]]))
        # session_endings itself does not catch: the builder is the one that counts and logs
        import inspect
        self.assertNotIn("except", inspect.getsource(self.je.session_endings).split("parse_session")[1].split("out = []")[0],
                         "session_endings lets a parse failure propagate; the builder counts and logs it")

    def test_the_event_model_load_leaves_no_scratch_root_behind(self):
        """The manager's round-three fold: the scratch state root the event-model load mints is removed once the module has
        bound its roots, so a build leaves no je-em-* directory."""
        import glob, tempfile as _tf
        self.je._EM[0] = None                                    # force a fresh load
        before = set(glob.glob(os.path.join(_tf.gettempdir(), "je-em-*")))
        dest, m = self._corpus(name="noscratch")
        self.assertTrue(m["endings"], "the build parsed through the freshly loaded event model")
        after = set(glob.glob(os.path.join(_tf.gettempdir(), "je-em-*")))
        self.assertEqual(after - before, set(), "no je-em-* scratch root is left behind: %r" % (after - before))

    # ── round on the second contributor's review of #1937 (folded on main after #1946) ──
    def _archive_cleared_top(self, sid, cut_t, cleared_at):
        """A cleared top in goals-archive (where the kernel's compaction moves it), with a closer done before the cut and the
        user's clear at `cleared_at`; the clear also rides the override journal, as append_clear writes it."""
        node = {"id": sid + ":gA", "text": "A goal the user cleared", "parentId": None, "t": cut_t - 5000, "cleared": True,
                "trail": ["%s:%d:aaaaaaaa" % (sid, cut_t - 5000)],
                "log": [{"ev_t": cut_t - 3, "at": cut_t - 2, "src": "closer", "kind": "done", "why": "delivered"},
                        {"ev_t": cleared_at, "at": cleared_at + 1, "src": "user", "kind": "clear", "why": "seen"}]}
        arch = {"rompUuid": sid, "nodes": {node["id"]: node}, "status": {node["id"]: "cleared"}}
        (self.state / "goals-archive" / (sid + ".json")).write_text(json.dumps(arch))
        with (self.state / "overrides" / (sid + ".jsonl")).open("a") as f:
            f.write(json.dumps({"node": sid + ":gA", "op": "clear", "src": "user", "why": "cleared from the feed", "t": cleared_at}) + "\n")
        return node

    def test_a_cleared_top_in_the_archive_is_visible_to_all_four_readers(self):
        """The second contributor's review of #1937: the kernel's compaction moves a cleared top into goals-archive/<sid>.json, and the harness read only
        the live store, so the top was missing from the done times, the eligibility mark, tier one and the seed (the plan counts
        640 clears against 22 resolves, so the finished signal lived mostly there). store_with_archive unions the archive in."""
        fn = getattr(self.je, "store_with_archive", None)
        self.assertIsNotNone(fn, "the harness unions the archive into the store its readers take (the base read only the live store)")
        sid = SIDS[0]
        m = self._corpus()[1]
        e = self._ending(m, sid, 1)
        start, cut = float(e["startT"]), float(e["cutT"])
        self._archive_cleared_top(sid, cut, cut + 3600)      # cleared an hour after this ending's cut
        merged = fn(self.state, sid)
        self.assertIn(sid + ":gA", merged["nodes"], "the archived cleared top is in the store the readers see")
        self.assertIn(cut - 3, self.je.top_done_times(merged), "its done is among the done times, so an ending in its window is eligible")
        # tier one: the closer's done in the window, then the user's clear with nothing after → finished
        self.assertEqual(self.je.tier_one_label(self.state, sid, cut, start), "finished",
                         "a cleared top the judges completed labels the ending finished, though it lives in the archive")
        # the seed: a top cleared AFTER the cut was open at the cut, so it returns to the seed
        dest, m2 = self._corpus(name="corpus-archive")
        seed = json.loads(Path(dest, "state", "romp", "goals", self._ending(m2, sid, 1)["id"] + ".json").read_text())
        self.assertIn(self._ending(m2, sid, 1)["id"] + ":gA", seed["nodes"], "the top cleared after the cut is in the ending's seed")

    def test_the_window_excludes_the_next_turns_done(self):
        """The second contributor's review of #1937: the window ran to cut + SETTLE_S, but no done carries an evidence time after its own cut, so a done
        in that tail is the next turn's; the window ends at the cut."""
        self.assertTrue(self.je.in_turn_window(1000.0, 900.0, 1000.0), "a done at the cut is in the window")
        self.assertFalse(self.je.in_turn_window(1000.5, 900.0, 1000.0), "a done after the cut belongs to the next turn")
        self.assertTrue(self.je.in_turn_window(950.0, 900.0, 1000.0))
        self.assertFalse(self.je.in_turn_window(899.0, 900.0, 1000.0), "before the turn start is not the ending's")
        self.assertFalse(self.je.in_turn_window(950.0, None, 1000.0), "an ending with no turn opener (None start) has no window: never eligible")

    def test_tier_one_matches_the_journals_full_node_key(self):
        """A cross-session tail collision: a followup on ANOTHER session's g1 and a clear on this one. The tail compare (the base)
        matches both by 'g1' and reads not finished; the full-key compare matches only this session's clear and reads finished."""
        sid = SIDS[0]; other = SIDS[1]
        m = self._corpus()[1]
        e = self._ending(m, sid, 0)
        start, cut = float(e["startT"]), float(e["cutT"])
        self._live_store_with_done(sid, start, cut, [{"node": other + ":g1", "op": "followup", "t": cut + 100},
                                                     {"node": sid + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": cut + 120}])
        self.assertEqual(self.je.tier_one_label(self.state, sid, cut, start), "finished",
                         "only this session's clear matches the full key; the other session's followup does not (the base's tail compare read not finished)")

    def test_the_restore_op_carries_a_nodes_dict(self):
        sid = SIDS[0]
        m = self._corpus()[1]
        e = self._ending(m, sid, 0)
        start, cut = float(e["startT"]), float(e["cutT"])
        self._live_store_with_done(sid, start, cut, [{"op": "clear", "src": "user", "node": sid + ":g1", "why": "cleared from the feed", "t": cut + 100},
                                                     {"op": "restore", "nodes": {sid + ":g1": {"text": "back"}}, "t": cut + 200}])
        self.assertEqual(self.je.tier_one_label(self.state, sid, cut, start), "not finished",
                         "a restore keyed by its `nodes` dict, not a `node` field, still reads as not finished")

    def test_the_gate_stamps_are_dropped_or_clamped_by_rule(self):
        sid = SIDS[0]
        m = self._corpus()[1]
        e = self._ending(m, sid, 0)
        start, cut = float(e["startT"]), float(e["cutT"])
        seg = "%s:%d:aaaaaaaa" % (sid, start - 5000)
        node = {"id": sid + ":g1", "text": "older", "parentId": None, "t": start - 5000, "trail": [seg],
                "blockCheckT": cut, "blockCheckDoneT": cut + 50, "delegLookT": cut + 60, "titledT": cut + 70, "servingT": start - 20,
                "log": []}
        store = {"rompUuid": sid, "seq": 1, "placementsV": PV, "placements": {seg: sid + ":g1"}, "status": {}, "nodes": {node["id"]: node},
                 "seams": [1, 2, 3], "confirming": [sid + ":g1"], "groupedSig": {"x": "y"}, "closeFails": 4}
        before = self.je.store_before(store, cut, start, e["id"])
        g1 = before["nodes"][e["id"] + ":g1"]
        self.assertNotIn("blockCheckT", g1, "a blockCheckT at or after the turn start is dropped (the strict gate hides the node otherwise)")
        self.assertNotIn("blockCheckDoneT", g1); self.assertNotIn("delegLookT", g1, "the arrival-domain stamps from after the cut are dropped")
        self.assertEqual(g1["titledT"], cut, "titledT is clamped to the cut, not dropped (dropping buys a title call)")
        self.assertEqual(g1["servingT"], start - 20, "a stamp from before the turn stays")
        for k in ("seams", "confirming", "groupedSig", "closeFails"):
            self.assertNotIn(k, before, "%s is store-level state from after the cut, dropped" % k)

    def test_the_labeller_never_asks_the_same_order_twice_and_reads_the_ask(self):
        # the identity shuffle falls back to the reversed class list
        seen = []
        real = self.je.ask_class
        def spy(claude_bin, model, text, order, ledger):
            seen.append(tuple(order)); return "finished", 0.0
        self.je.ask_class = spy
        # force rng to yield the identity order first: patch random inside label via a tiny corpus of one ending
        try:
            dest, m = self._corpus(name="shuffle")
            import random
            saved = random.Random
            class _Ident(random.Random):
                def shuffle(self, x, *a): pass          # leave the order as CLASSES
            random.Random = _Ident
            try:
                self.je.label(dest, os.path.join(self.td, "runs-shuffle"), self.state, claude_bin=self.fake, model="fake")
            finally:
                random.Random = saved
        finally:
            self.je.ask_class = real
        pairs = [(seen[i], seen[i + 1]) for i in range(0, len(seen) - 1, 2)]
        self.assertTrue(pairs, "the labeller ran")
        for a, b in pairs:
            self.assertNotEqual(a, b, "the two orders differ even when the shuffle is the identity: %r vs %r" % (a, b))
        # the labeller reads the ending turn's own opening ask (assembled by label from _ending_ask), and the final
        # assistant text stands on its own (_last_assistant_text no longer scans for a first ask, review item 1)
        sid = SIDS[0]; e = self._ending(m, sid, 0)
        path = next(iter((Path(dest) / "claude" / "projects").glob("*/%s.jsonl" % e["id"])))
        self.assertNotIn("The user's ask", self.je._last_assistant_text(str(path)), "the ask is assembled by label, not by _last_assistant_text")
        self.assertIn("please fix the flicker", self.je._ending_ask(str(path)), "the ending's own opening ask, read from the event model")

    def test_the_labeller_reads_the_ending_turns_own_ask_not_the_sessions_first(self):
        """Review item 1 (the ask bug): the per-ending transcript is the whole session up to the ending (records[:end+1]),
        so the ask appended for the labeller must be the ENDING turn's own opener, read from the event model's own
        segmentation, not the session's first user text. SIDS[0]'s second ending answers 'Which option', and its own ask is
        'pick the storage layout'; the base appended turn zero's 'please fix the flicker' instead."""
        dest, m = self._corpus(name="ask")
        texts = []
        real = self.je.ask_class
        def spy(claude_bin, model, text, order, ledger):
            texts.append(text); return "finished", 0.0
        self.je.ask_class = spy
        try:
            self.je.label(dest, os.path.join(self.td, "runs-ask"), self.state, claude_bin=self.fake, model="fake")
        finally:
            self.je.ask_class = real
        storage = [t for t in texts if "Which option" in t]        # SIDS[0]'s second ending's labeller input
        self.assertTrue(storage, "the second ending's labeller input was sent")
        self.assertIn("The user's ask that opened the last turn: pick the storage layout", storage[0],
                      "the ending turn's own ask is appended: %r" % storage[0][-200:])
        self.assertNotIn("flicker", storage[0], "not the session's first ask (the base appended turn zero's): %r" % storage[0])

    def test_a_failed_ask_parse_is_recorded_not_swallowed(self):
        """Review round two low 2: _ending_ask fails LOUD on a parse exception, like the corpus builder, recording the
        exception per ending (labels.json askError) and a total (labels-summary.json askParseErrors); the labeller still runs
        on the final text. The head swallowed every parse exception and returned '' like a legitimate opener-less ending."""
        dest, m = self._corpus(name="askfault")
        class _Boom:
            def parse_session(self, *a, **k):
                raise ValueError("synthetic parse boom")
            def evict_document(self, *a):
                pass
        saved = self.je._EM[0]
        self.je._EM[0] = _Boom()
        try:
            summary = self.je.label(dest, os.path.join(self.td, "runs-askfault"), self.state, claude_bin=self.fake, model="fake")
        finally:
            self.je._EM[0] = saved
        rows = json.loads(Path(self.td, "runs-askfault", "labels.json").read_text())
        faulted = [r for r in rows if r.get("askError")]
        self.assertTrue(faulted, "an ask parse fault is recorded on the row (the head swallowed it): %r" % rows[:1])
        self.assertEqual(faulted[0]["askError"], "ValueError", "the fault names the exception type: %r" % faulted[0])
        self.assertTrue(all(r.get("labelA") is not None for r in faulted), "the labeller still ran on the final text")
        self.assertGreaterEqual(summary.get("askParseErrors", 0), 1, "the summary totals the ask parse faults: %r" % summary.get("askParseErrors"))

    def test_a_faulted_store_is_recorded_not_swallowed(self):
        sid = SIDS[0]
        dest, m = self._corpus()                          # built clean (no store); the corruption comes after, so the endings are in the manifest
        (self.state / "goals" / (sid + ".json")).write_text("{ not json")
        faults = []
        out = self.je.tier_one_label(self.state, sid, float(self._ending(m, sid, 0)["cutT"]), float(self._ending(m, sid, 0)["startT"]), faults=faults)
        self.assertIsNone(out)
        self.assertEqual([f[0] for f in faults], [hashlib.sha256(sid.encode()).hexdigest()[:12]], "the fault is recorded by session hash, never the sid or path")
        self.assertNotIn(sid, json.dumps(faults))
        summary = self.je.label(dest, os.path.join(self.td, "runs-fault"), self.state, claude_bin=self.fake, model="fake")
        self.assertIn("tierOneErrors", summary, "the summary counts faulted sessions")
        # per-row: a faulted read carries its error string, told apart from a genuine null tier one (a session with no store)
        rows = json.loads(Path(self.td, "runs-fault", "labels.json").read_text())
        h0 = hashlib.sha256(SIDS[0].encode()).hexdigest()[:12]; h1 = hashlib.sha256(SIDS[1].encode()).hexdigest()[:12]
        faulted = [r for r in rows if r.get("tierOneError")]
        self.assertTrue(faulted and all(f["tierOne"] is None for f in faulted), "the faulted rows carry an error and a null tier one: %r" % faulted)
        clean = [r for r in rows if r["id"] in {e["id"] for e in m["endings"] if e["session"] == h1}]
        self.assertTrue(clean and all(r["tierOne"] is None and r["tierOneError"] is None for r in clean),
                        "a session with no store reads a null tier one with NO error: the two nulls are distinguished: %r" % clean)

    def test_a_clear_before_the_turn_reads_cleared_and_one_inside_the_turn_is_open(self):
        """M1: the seed is the store as at the turn's open. A top the user cleared BEFORE the turn start keeps its clear log and
        rolls up cleared; a clear INSIDE the turn is dropped with the turn's verdicts, so the card is open at the seed, the
        arm's to rule on."""
        sid = SIDS[0]
        m = self._corpus()[1]
        e = self._ending(m, sid, 1)
        start, cut = float(e["startT"]), float(e["cutT"])
        seg = "%s:%d:aaaaaaaa" % (sid, start - 5000)
        def store_with_clear(clear_t):
            node = {"id": sid + ":gA", "text": "A cleared top", "parentId": None, "t": start - 5000, "cleared": True, "trail": [seg],
                    "log": [{"ev_t": clear_t, "at": clear_t + 1, "src": "user", "kind": "clear", "why": "seen"}]}
            return {"rompUuid": sid, "seq": 1, "placementsV": PV, "placements": {seg: sid + ":gA"}, "status": {}, "nodes": {node["id"]: node}}
        before_start = self.je.store_before(store_with_clear(start - 100), cut, start, e["id"])
        ga = before_start["nodes"][e["id"] + ":gA"]
        self.assertEqual([ev["kind"] for ev in ga["log"]], ["clear"], "a clear before the turn start survives in the seed")
        jd = self.je.load_judge(Path(self.td) / "rollup-state", Path(self.td) / "noclaude", self.fake)   # roll it up as the arm would
        try:
            st = {"rompUuid": e["id"], "seq": 1, "placementsV": PV, "placements": {}, "status": {}, "nodes": before_start["nodes"]}
            jd.rollup_status(st, True)
            self.assertEqual(st["status"].get(e["id"] + ":gA"), "cleared", "it rolls up cleared: %r" % st.get("status"))
        finally:
            pass
        inside = self.je.store_before(store_with_clear(cut - 1), cut, start, e["id"])   # a clear one second before the cut, inside the turn
        gi = inside["nodes"][e["id"] + ":gA"]
        self.assertEqual(gi["log"], [], "a clear inside the turn is dropped: the card is open at the seed, the arm's to make")

    def test_a_corrupt_store_is_counted_never_swallowed(self):
        """M-low: store_with_archive swallowed a corrupt goals or goals-archive file, so a session lost its seed unseen. A
        corrupt store is counted under skipped['store-unreadable'] and its type logged; the reader raises."""
        sid = SIDS[0]
        (self.state / "goals" / (sid + ".json")).write_text("{ not json")
        with self.assertRaises(ValueError, msg="store_with_archive raises on a corrupt store (the caller counts it)"):
            self.je.store_with_archive(self.state, sid)
        import io, contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            m = self._corpus(name="corruptstore")[1]
        self.assertGreaterEqual(m["skipped"]["store-unreadable"], 1, "the session with the corrupt store is counted")
        self.assertIn("could not be read", err.getvalue())
        self.assertNotIn(sid, json.dumps(m["skipped"]))
        # a corrupt archive is caught too
        (self.state / "goals" / (sid + ".json")).write_text(json.dumps({"rompUuid": sid, "nodes": {}, "status": {}}))
        (self.state / "goals-archive" / (sid + ".json")).write_text("{ not json either")
        with self.assertRaises(ValueError):
            self.je.store_with_archive(self.state, sid)
        # a well-formed JSON top that is not an object (a list, a bare string) is quarantined too, never read as an empty store
        (self.state / "goals-archive" / (sid + ".json")).unlink()
        (self.state / "goals" / (sid + ".json")).write_text(json.dumps(["not", "a", "store"]))
        with self.assertRaises(ValueError, msg="a non-object store top raises, never reads as a store with no nodes"):
            self.je.store_with_archive(self.state, sid)

    def test_known_fsids_skips_absent_lineage_but_raises_on_a_corrupt_registry(self):
        """known_fsids reads the sid's lineage as files: an absent episodes/states file (ENOENT/ENOTDIR) contributes nothing,
        but a corrupt registry is a fault it raises (the builder counts registry-unreadable), never read as no lineage."""
        sid = SIDS[0]
        # no sdk/episodes/states files for this sid: known_fsids returns the sid alone, never raising on their absence
        self.assertEqual(self.je.known_fsids(self.state, sid), {sid}, "absent lineage files are skipped, not a fault")
        # a resume-fork leaf in states/ joins; an absent episodes file beside it still just skips
        (self.state / "states" / (sid + ".jsonl")).write_text(json.dumps({"resumeFork": {"from": sid, "to": sid + "-r"}}) + "\n")
        self.assertEqual(self.je.known_fsids(self.state, sid), {sid, sid + "-r"}, "the resume fork's ends join")
        # a corrupt registry RAISES; the builder counts it under registry-unreadable and names no sid
        (self.state / "sdk" / (sid + ".json")).write_text("{ not json")
        with self.assertRaises(ValueError, msg="a corrupt registry raises, never read as no lineage"):
            self.je.known_fsids(self.state, sid)
        import io, contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            m = self._corpus(name="badreg")[1]
        self.assertGreaterEqual(m["skipped"]["registry-unreadable"], 1, "the session with the corrupt registry is counted")
        self.assertNotIn(sid, json.dumps(m["skipped"]) + err.getvalue())

    def test_an_opener_less_ending_seeds_from_the_previous_ended_turns_cut(self):
        """M2: an ending whose turn has no opener (start None) stays ineligible (its manifest startT is null), but its seed and
        journal still cut SOMEWHERE: the previous ended turn's cut, never the ending's own cut. The base cut at the ending's own
        cut, so a node born in the ending's own turn (and a journal row inside it) seeded the ending: the arm inherited what its
        own turn produced. Here a node born and a journal row filed AFTER the previous cut are absent from the opener-less
        ending's seed and journal, present in every earlier ending's."""
        sid = SIDS[0]
        recs, t, parent = [], T0, None
        for j in range(3):
            recs.append(uline(sid, t, "ask %d" % j, "u%d" % j, parent)); recs.append(aline(sid, t + 30, "Answer %d." % j, "a%d" % j, "u%d" % j))
            parent = "a%d" % j; t += 600
        (self.pdir / (sid + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in recs))
        seg = "%s:%d:aaaaaaaa" % (sid, T0 + 900)                     # born between turn 1's cut (T0+630) and turn 2's cut (T0+1230)
        node = {"id": sid + ":gX", "text": "born after the previous cut", "parentId": None, "t": T0 + 900, "trail": [seg], "log": []}
        (self.state / "goals" / (sid + ".json")).write_text(json.dumps(
            {"rompUuid": sid, "seq": 1, "placementsV": PV, "placements": {seg: sid + ":gX"}, "status": {}, "nodes": {node["id"]: node}}))
        (self.state / "overrides" / (sid + ".jsonl")).write_text(
            json.dumps({"node": sid + ":gX", "op": "followup", "t": T0 + 100}) + "\n"        # before every turn's start: always kept
            + json.dumps({"node": sid + ":gX", "op": "followup", "t": T0 + 900}) + "\n")     # after turn 1's cut: in turn 2 only
        real = self.je.session_endings
        def patched(path, fsid):
            records, endings = real(path, fsid)
            if sid in str(path) and len(endings) >= 3:
                i, _s, tx = endings[-1]
                endings[-1] = (i, None, tx)                         # turn 2 loses its opener
            return records, endings
        self.je.session_endings = patched
        try:
            dest, m = self._corpus(name="openerless")
        finally:
            self.je.session_endings = real
        e2 = self._ending(m, sid, 2)
        self.assertIsNone(e2["startT"], "the opener-less ending keeps a null start: it is never eligible")
        self.assertIs(e2["tierOneEligible"], False)
        seed2 = json.loads(Path(dest, "state", "romp", "goals", e2["id"] + ".json").read_text())
        self.assertNotIn(e2["id"] + ":gX", seed2["nodes"], "the node born after the previous cut is NOT in the opener-less ending's seed")
        j2 = [json.loads(l) for l in Path(dest, "state", "romp", "overrides", e2["id"] + ".jsonl").read_text().splitlines()]
        self.assertEqual([r["t"] for r in j2], [T0 + 100], "its journal keeps only the row before the previous cut, not the one inside its own turn")

    def test_the_dropped_blockcheckt_lets_the_unblocker_examine_the_node(self):
        """M-low: the drop-list pin was key absence; drive it by the unblocker's own due gate. A blocked top's seed, rolled up
        the arm's way, is a re-examine candidate; the judge's due formula (`newest > max(bt, blockCheckT)`) fires with the
        stamp dropped (the head) and does not with a blockCheckT at the turn start surviving (the base). The real candidate
        finder and the real formula, over the store_before'd seed."""
        sid = SIDS[0]
        m = self._corpus()[1]
        e = self._ending(m, sid, 1)
        start, cut = float(e["startT"]), float(e["cutT"])
        seg = "%s:%d:aaaaaaaa" % (sid, start - 5000)
        node = {"id": sid + ":g1", "text": "A blocked top waiting on the user", "parentId": None, "t": start - 5000, "blocked": True,
                "blockCheckT": start, "trail": [seg],
                "log": [{"ev_t": start - 4000, "at": start - 3999, "src": "planner", "kind": "block", "why": "your call?"}]}
        store = {"rompUuid": sid, "seq": 1, "placementsV": PV, "placements": {seg: sid + ":g1"}, "status": {}, "nodes": {node["id"]: node},
                 "closedTurns": [], "closedSig": {}}
        before = self.je.store_before(store, cut, start, e["id"])
        gid = e["id"] + ":g1"
        self.assertNotIn("blockCheckT", before["nodes"][gid], "the stamp at the turn start is dropped from the seed")
        jd = self.je.load_judge(Path(self.td) / "ub-state", Path(self.td) / "ub-noclaude", self.fake)
        rolled = {"rompUuid": e["id"], "seq": 1, "placementsV": PV, "placements": {}, "status": {}, "nodes": dict(before["nodes"])}
        jd.rollup_status(rolled, True)                    # the arm's rollup sets the blocked flag from the diary
        cands = jd._blocked_sub_candidates(rolled)
        self.assertEqual([c[0] for c in cands], [gid], "the seeded blocked top is a re-examine candidate: %r" % cands)
        bt = cands[0][2]
        newest = start                                    # the ending turn's start, the newest ended turn the arm parses
        head_due = newest > max(bt, rolled["nodes"][gid].get("blockCheckT") or 0)
        base_due = newest > max(bt, start)                # the base kept blockCheckT == the turn start
        self.assertTrue(head_due, "with the stamp dropped the unblocker's gate fires (newest > the block time)")
        self.assertFalse(base_due, "with a surviving blockCheckT at the turn start the strict gate holds it: no examine")

    def test_the_labeller_gate_is_stability_not_agreement_with_the_user(self):
        """Road (b): the labeller's OWN gate is STABILITY (the same class in both shuffled orders), NOT agreement with the
        user's recorded actions (the pilot showed the class is a frame, not a truth). A corpus the labeller labels stably but
        that disagrees with the user's actions still PASSES the gate; an order-dependent labeller fails it."""
        dest, m = self._corpus()
        # a disagreement: SIDS[0] turn 0 is an offer to the labeller, but tier one reads finished (a clear, nothing after)
        e0 = self._ending(m, SIDS[0], 0)
        self._live_store_with_done(SIDS[0], float(e0["startT"]), float(e0["cutT"]), [{"node": SIDS[0] + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": float(e0["cutT"]) + 60}])
        e1 = self._ending(m, SIDS[1], 1)                 # the finished thread: labeller and tier one agree
        self._live_store_with_done(SIDS[1], float(e1["startT"]), float(e1["cutT"]), [{"node": SIDS[1] + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": float(e1["cutT"]) + 60}])
        stable = self.je.label(dest, os.path.join(self.td, "runs-stable"), self.state, claude_bin=self.fake, model="fake")
        self.assertEqual((stable["both"], stable["agree"], stable["agreementPct"]), (2, 1, 50.0),
                         "the offer disagrees with the user's clear (offer vs finished); agreement is only 50 percent: %r" % stable)
        self.assertEqual((stable["labellerStable"], stable["stablePct"], stable["gatePassed"]), (4, 100.0, True),
                         "road (b): the labeller is stable in both orders, so the gate PASSES though agreement is 50 percent: %r" % stable)
        # an order-dependent labeller: the two orders disagree, nothing is stable, the gate fails
        os.environ["JE_TEST_UNSTABLE"] = "1"
        try:
            unstable = self.je.label(dest, os.path.join(self.td, "runs-unstable"), self.state, claude_bin=self.fake, model="fake")
        finally:
            os.environ.pop("JE_TEST_UNSTABLE", None)
        self.assertEqual((unstable["labellerStable"], unstable["stablePct"], unstable["gatePassed"]), (0, 0.0, False),
                         "an order-dependent labeller agrees with itself on no ending: below the 90 percent stability gate: %r" % unstable)

    # ── the second contributor's review of #1946 ──
    def test_store_with_archive_skips_rewind_swept_nodes(self):
        sid = SIDS[0]
        (self.state / "goals" / (sid + ".json")).write_text(json.dumps({"rompUuid": sid, "seq": 2, "nodes": {}, "status": {},
                                                                        "rewindSwept": {sid + ":g9": 1}}))
        (self.state / "goals-archive" / (sid + ".json")).write_text(json.dumps({"rompUuid": sid, "nodes": {
            sid + ":gC": {"id": sid + ":gC", "text": "a cleared top", "parentId": None, "t": 1, "log": [{"ev_t": 1, "at": 2, "src": "user", "kind": "clear"}]},
            sid + ":g9": {"id": sid + ":g9", "text": "a rewound node", "parentId": None, "t": 1, "log": []}}}))
        merged = self.je.store_with_archive(self.state, sid)
        self.assertIn(sid + ":gC", merged["nodes"], "the cleared top is unioned")
        self.assertNotIn(sid + ":g9", merged["nodes"], "a rewind-swept node is not (its id is in the live store's rewindSwept)")

    def test_a_title_lane_is_seeded_and_labelled_from_its_own_store(self):
        """A same-titled fork lane's endings key their store, journal and dones under the LANE's stem, not the anchor's."""
        sid = SIDS[0]; lane = "11111111-2222-3333-4444-fffffffffa01"
        recs = [{"type": "custom-title", "customTitle": "web", "sessionId": lane, "timestamp": iso(T0 + 80000)},
                uline(lane, T0 + 80000, "in the lane", "u1"), aline(lane, T0 + 80030, "Lane answer.", "a1", "u1"),
                uline(lane, T0 + 80600, "more in the lane", "u2", "a1"), aline(lane, T0 + 80630, "Lane done. I can also tidy.", "a2", "u2")]
        (self.pdir / (lane + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in recs))
        # the lane's OWN store: a top the closer completed in the lane's first turn, at that turn's start
        laneseg = "%s:%d:aaaaaaaa" % (lane, T0 + 80000)
        (self.state / "goals" / (lane + ".json")).write_text(json.dumps({"rompUuid": lane, "seq": 1, "placementsV": PV,
            "placements": {laneseg: lane + ":gL"}, "status": {},
            "nodes": {lane + ":gL": {"id": lane + ":gL", "text": "The lane's own goal", "parentId": None, "t": T0 + 80000, "trail": [laneseg],
                                     "log": [{"ev_t": T0 + 80000, "at": T0 + 80005, "src": "closer", "kind": "done", "why": "delivered"}]}}}))
        (self.state / "overrides" / (lane + ".jsonl")).write_text(json.dumps({"node": lane + ":gL", "op": "followup", "t": T0 + 90000}) + "\n")
        m = self._corpus(name="lane")[1]
        h_anchor = hashlib.sha256(sid.encode()).hexdigest()[:12]; h_lane = hashlib.sha256(lane.encode()).hexdigest()[:12]
        lane_endings = [e for e in m["endings"] if e.get("lane") == h_lane]
        self.assertTrue(lane_endings, "the lane's endings carry its own lane hash: %r" % [(e["session"], e.get("lane")) for e in m["endings"]])
        self.assertTrue(all(e["session"] == h_anchor for e in lane_endings), "the anchor's hash stays in `session`")
        e0 = min(lane_endings, key=lambda e: e["turn"])
        self.je.label(os.path.join(self.td, "lane"), os.path.join(self.td, "runs-lane"), self.state, claude_bin=self.fake, model="fake")
        rows = {r["id"]: r for r in json.loads(Path(self.td, "runs-lane", "labels.json").read_text())}
        self.assertEqual(rows[e0["id"]]["tierOne"], "not finished",
                         "tier one reads the LANE's own store and journal (a done in the lane's first turn, a followup after): the anchor's store would give None")

    def test_label_never_falls_from_an_unresolved_lane_to_the_anchor(self):
        """M1: when an ending carries a lane hash, the label pass reads the LANE's store, never the anchor's. A lane the pass
        cannot resolve (no names entry, no goals file) yields a null tier one, not the anchor's. The base fell through
        `key_of.get(lane) or key_of.get(session)` to the anchor and read the wrong session's cards."""
        sid = SIDS[0]
        e = self._ending(self._corpus()[1], sid, 0)
        # the anchor's own store reads FINISHED (a user clear after the cut); a lane hash that resolves to nothing must not borrow it
        self._live_store_with_done(sid, float(e["startT"]), float(e["cutT"]), [{"node": sid + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": float(e["cutT"]) + 60}])
        self.assertEqual(self.je.tier_one_label(self.state, sid, float(e["cutT"]), float(e["startT"])), "finished", "the anchor's store reads finished")
        dest = os.path.join(self.td, "m1"); (Path(dest) / "claude" / "projects").mkdir(parents=True)
        lane_hash = hashlib.sha256(b"an-unregistered-lane-with-no-store-or-name").hexdigest()[:12]
        Path(dest, "manifest.json").write_text(json.dumps({"built": T0, "classes": list(self.je.CLASSES), "skipped": {}, "endings": [
            {"id": e["id"], "session": hashlib.sha256(sid.encode()).hexdigest()[:12], "lane": lane_hash,
             "class": "offer", "cutT": e["cutT"], "startT": e["startT"], "tierOneEligible": True}]}))
        self.je.label(dest, os.path.join(self.td, "runs-m1"), self.state, claude_bin=self.fake, model="fake")
        row = json.loads(Path(self.td, "runs-m1", "labels.json").read_text())[0]
        self.assertIsNone(row["tierOne"], "an unresolved lane never falls through to the anchor's store: tier one is null, not the anchor's finished")
        self.assertEqual(row["tierOneError"], "unresolved-key", "an unresolvable manifest hash is marked, not passed off as a store-less session's genuine null")

    def test_a_registered_same_titled_transcript_is_not_a_lane(self):
        """The `registered` half of the lane exclusion: a fork lane is an UNREGISTERED same-titled transcript. A second
        REGISTERED session whose transcript's custom title matches the first session's name ("web") must not be claimed as the
        first's lane; it keeps its own two endings under its own hash. Dropping the registered half folds its endings into the
        first session (processed first) and leaves it reporting no transcript of its own."""
        b = "11111111-2222-3333-4444-eeeeeeeeee03"    # registered, its transcript titled "web" (the first session's name), sorts AFTER it
        recs = [{"type": "custom-title", "customTitle": "web", "sessionId": b, "timestamp": iso(T0 + 40000)},
                uline(b, T0 + 40000, "b ask 0", "b0"), aline(b, T0 + 40030, "B answer 0.", "ba0", "b0"),
                uline(b, T0 + 40600, "b ask 1", "b1", "ba0"), aline(b, T0 + 40630, "B answer 1.", "ba1", "b1")]
        (self.pdir / (b + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in recs))
        (self.state / "names" / b).write_text("apiweb\t%s\t#abcdef\n" % self.cwd)
        m = self._corpus()[1]
        hb = hashlib.sha256(b.encode()).hexdigest()[:12]
        self.assertEqual(sum(1 for e in m["endings"] if e["session"] == hb), 2,
                         "the registered same-titled session keeps its own two endings under its own hash, never folded into the first as a lane: %r"
                         % [(e["session"], e.get("lane")) for e in m["endings"]])

    def test_the_per_session_queue_is_sorted_by_cut_time_before_the_pop(self):
        """The per-session sort by cut time: a session's candidates arrive in fsid (stem) order, not time order, once a fork
        lane joins. With the anchor's turns NEWER than the lane's and per_class=2, the two OLDEST eligible offers (both the
        lane's) win the slots; dropping the sort pops in stem order and takes the anchor's newer two. Its own state root."""
        root = Path(self.td, "sortstate", "romp")
        for sub in ("names", "goals", "goals-archive", "overrides", "sdk", "states", "episodes"):
            (root / sub).mkdir(parents=True)
        (root / "session-hosts").write_text("off")
        claude = Path(self.td, "sortclaude")
        cwd = os.path.join(self.td, "sortproj"); os.makedirs(cwd)
        pdir = claude / "projects" / self.je.munge(cwd); pdir.mkdir(parents=True)
        solo = "11111111-2222-3333-4444-aaaaaaaaaa01"; lane = "11111111-2222-3333-4444-ffffffffff90"   # lane's stem sorts AFTER, so it is appended after
        def offers(sid, base):
            recs, t, parent = [], base, None
            for j in range(2):
                u, a = "%s-u%d" % (sid[-2:], j), "%s-a%d" % (sid[-2:], j)
                recs.append(uline(sid, t, "ask %d" % j, u, parent)); recs.append(aline(sid, t + 30, "Done %d. I can also tidy the names." % j, a, u))
                parent = a; t += 600
            return recs
        (pdir / (solo + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in offers(solo, T0 + 10000)))       # the anchor's offers: newer
        lane_recs = [{"type": "custom-title", "customTitle": "solo", "sessionId": lane, "timestamp": iso(T0 - 5000)}] + offers(lane, T0 - 5000)   # the lane's: older
        (pdir / (lane + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in lane_recs))
        (root / "names" / solo).write_text("solo\t%s\t#abcdef\n" % cwd)
        def store_for(sid, base):
            log = [{"ev_t": base + j * 600 + 5, "at": base + j * 600 + 6, "src": "closer", "kind": "done", "why": "x"} for j in range(2)]   # a done in each turn's window: every offer eligible
            return {"rompUuid": sid, "seq": 1, "placementsV": PV, "placements": {}, "status": {},
                    "nodes": {sid + ":g1": {"id": sid + ":g1", "text": "g", "parentId": None, "t": base, "trail": [], "log": log}}}
        (root / "goals" / (solo + ".json")).write_text(json.dumps(store_for(solo, T0 + 10000)))
        (root / "goals" / (lane + ".json")).write_text(json.dumps(store_for(lane, T0 - 5000)))
        m = self.je.build_corpus(root, claude, os.path.join(self.td, "sortcorpus"), per_class=2, now=T0 + 10**6)
        offers_picked = [e for e in m["endings"] if e["class"] == "offer"]
        h_lane = hashlib.sha256(lane.encode()).hexdigest()[:12]
        self.assertEqual(len(offers_picked), 2, "the offer cap is two: %r" % offers_picked)
        self.assertTrue(all(e["tierOneEligible"] for e in offers_picked), "the picked offers are eligible (in the oldest-first branch): %r" % offers_picked)
        self.assertTrue(all(e["lane"] == h_lane for e in offers_picked),
                        "sorted by cut time, the two OLDEST eligible offers (the lane's) win; the dropped sort would take the anchor's newer two: %r"
                        % [(e["turn"], e["lane"], e["cutT"]) for e in offers_picked])

    def test_the_arm_writes_its_summary_even_when_the_closing_ledger_read_raises(self):
        """Item 3: the closing cost tally and the final results.json write live in the finally, so a raise from the ledger read
        after the loop has run every ending does not abort the arm with an unhandled error and no closing write. The base ran
        the tally and the final write after the loop, outside any finally, so a raise there propagated out of the arm."""
        dest, m = self._corpus()
        run_root = os.path.join(self.td, "runs-ledger")
        n_endings = len(m["endings"])
        real = self.je.ledger_cost
        calls = [0]
        def boom(usage):
            calls[0] += 1
            if calls[0] > n_endings:                 # the per-ending budget checks pass; only the CLOSING tally raises
                raise RuntimeError("ledger read failed")
            return real(usage)
        self.je.ledger_cost = boom
        raised = None
        try:
            self.je.run_arm_inprocess(dest, "ledgerboom", None, run_root, None, self.fake, now=T0 + 10**6, builds=1)
        except Exception as ex:
            raised = ex
        finally:
            self.je.ledger_cost = real
        self.assertIsNone(raised, "the closing ledger read raised, but the finally swallowed it after writing the summary: %r" % raised)
        res = json.loads(Path(run_root, "ledgerboom", "results.json").read_text())
        self.assertEqual(len(res["endings"]), n_endings, "the finally wrote the summary with every ending that ran: %r" % list(res.get("endings", {})))
        self.assertEqual(res.get("costError"), "RuntimeError", "the failed tally is RECORDED, not a bare pass: a reader tells a free arm from a broken tally: %r" % res.get("costError"))
        self.assertNotIn("cost", res, "and no cost is written when the tally never produced one")

    def test_a_bare_command_or_interrupt_turn_is_no_ending(self):
        sid = SIDS[0]
        # the interrupt tail is a real USER record reading "[Request interrupted by user]" (is_interrupt_record rejects an
        # assistant one): the worked turn u2 -> a2 (no end_turn, so the turn stays open through the tail) ends at the interrupt
        recs = [uline(sid, T0, "real ask", "u1"), aline(sid, T0 + 30, "Real answer.", "a1", "u1"),
                {"type": "user", "timestamp": iso(T0 + 100), "uuid": "c1", "parentUuid": "a1", "sessionId": sid, "isMeta": True,
                 "message": {"role": "user", "content": "<command-name>/usage</command-name>"}},
                {"type": "user", "timestamp": iso(T0 + 101), "uuid": "s1", "parentUuid": "c1", "sessionId": sid, "isMeta": True,
                 "message": {"role": "user", "content": "<local-command-stdout>usage</local-command-stdout>"}},
                uline(sid, T0 + 600, "another ask", "u2", "s1"),
                {"type": "assistant", "timestamp": iso(T0 + 630), "uuid": "a2", "parentUuid": "u2", "sessionId": sid,
                 "message": {"role": "assistant", "content": [{"type": "text", "text": "Working on it, calling a tool."}], "stop_reason": "tool_use"}},
                {"type": "user", "timestamp": iso(T0 + 640), "uuid": "i1", "parentUuid": "a2", "sessionId": sid,
                 "message": {"role": "user", "content": "[Request interrupted by user]"}}]
        pth = os.path.join(self.td, "cmds.jsonl"); open(pth, "w").write("".join(json.dumps(r) + "\n" for r in recs))
        _r, endings = self.je.session_endings(pth, sid)
        self.assertEqual([r[0] for r in endings], [1], "only the real worked turn (ending at a1) is an ending; the bare /usage and the interrupt-tailed turn are not: %r" % endings)

    def test_a_turn_ending_on_tool_use_still_ends(self):
        sid = SIDS[0]
        recs = [uline(sid, T0, "do it", "u1"),
                {"type": "assistant", "timestamp": iso(T0 + 30), "uuid": "a1", "parentUuid": "u1", "sessionId": sid,
                 "message": {"role": "assistant", "content": [{"type": "text", "text": "On it, running a tool."}], "stop_reason": "tool_use"}},
                uline(sid, T0 + 600, "next", "u2", "a1"), aline(sid, T0 + 630, "Done.", "a2", "u2")]
        pth = os.path.join(self.td, "tooluse.jsonl"); open(pth, "w").write("".join(json.dumps(r) + "\n" for r in recs))
        _r, endings = self.je.session_endings(pth, sid)
        self.assertEqual(len(endings), 1, "a tool_use assistant does not end the turn; it folds the next prompt until end_turn: one worked ending: %r" % endings)

    def test_one_endings_crash_files_a_row_and_the_arm_goes_on(self):
        """The per-ending guard: a raise inside one ending's build files a pass-crash row and records the ending, and the arm
        runs its other endings and writes results.json, rather than aborting with nothing. The crash is forced with a seed
        whose node log is not a list, which the rollup iterates and raises on."""
        dest, m = self._corpus()
        victim = m["endings"][1]["id"]
        # a valid-JSON seed of a shape the rollup chokes on (a node log that is a string, not a list of events)
        (Path(dest) / "state" / "romp" / "goals" / (victim + ".json")).write_text(json.dumps({
            "rompUuid": victim, "seq": 1, "placementsV": PV, "placements": {}, "status": {},
            "nodes": {victim + ":gx": {"id": victim + ":gx", "parentId": None, "t": 1, "log": "not-a-list"}}}))
        run_root = os.path.join(self.td, "runs-crash")
        self.je.run_arm_inprocess(dest, "current", None, run_root, None, self.fake, now=T0 + 10**6)
        res = json.loads(Path(run_root, "current", "results.json").read_text())
        self.assertIn(victim, res["endings"], "the crashed ending is recorded, not dropped")
        self.assertIn("crashed", res["endings"][victim], "with its crash noted: %r" % res["endings"][victim])
        self.assertGreaterEqual(res["failures"], 1, "the pass-crash is counted")
        self.assertEqual(len(res["endings"]), len(m["endings"]), "the arm ran every other ending")
        errs = [json.loads(l) for l in Path(run_root, "current", "state", "romp", "judge-errors.jsonl").read_text().splitlines() if l.strip()]
        self.assertTrue(any(e.get("err") == "pass-crash" for e in errs), "a pass-crash row is filed: %r" % errs[-3:])

    def test_the_restore_matcher_reads_the_ops_and_the_source(self):
        sid = SIDS[0]
        m = self._corpus()[1]
        e = self._ending(m, sid, 0)
        start, cut = float(e["startT"]), float(e["cutT"])
        def label_with(ops):
            self._live_store_with_done(sid, start, cut, ops)
            return self.je.tier_one_label(self.state, sid, cut, start)
        self.assertEqual(label_with([{"node": sid + ":g1", "op": "unclear", "t": cut + 10}]), "not finished", "an unclear alone re-opens")
        self.assertIsNone(label_with([{"node": sid + ":g1", "op": "clear", "src": "romp", "why": "episode", "t": cut + 10}]), "a romp clear is not the user's finish")
        self.assertIsNone(label_with([{"node": sid + ":g1", "op": "block", "src": "nudge", "t": cut + 10}]), "a nudge block is neither")
        self.assertEqual(label_with([{"node": sid + ":g1", "op": "resolve", "t": cut + 10}]), "finished", "a resolve is the user finishing it")
        self.assertEqual(label_with([{"op": "restore", "nodes": {sid + ":g1": {"text": "back"}}, "t": cut + 10}]), "not finished", "a restore by its nodes dict re-opens")

    def test_tier_one_eligible_is_false_for_a_turn_outside_the_window(self):
        """eligible must key on in_turn_window, not bool(dones): a session with a done far from a turn leaves that turn ineligible."""
        sid = SIDS[0]
        recs = [uline(sid, T0, "ask one", "u1"), aline(sid, T0 + 30, "Answer one.", "a1", "u1"),
                uline(sid, T0 + 6000, "ask two", "u2", "a1"), aline(sid, T0 + 6030, "Answer two.", "a2", "u2")]
        (self.pdir / (sid + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in recs))
        seg = "%s:%d:aaaaaaaa" % (sid, T0)
        (self.state / "goals" / (sid + ".json")).write_text(json.dumps({"rompUuid": sid, "seq": 1, "placementsV": PV, "placements": {seg: sid + ":g1"}, "status": {},
            "nodes": {sid + ":g1": {"id": sid + ":g1", "text": "g1", "parentId": None, "t": T0, "trail": [seg],
                                    "log": [{"ev_t": T0 + 10, "at": T0 + 12, "src": "closer", "kind": "done", "why": "x"}]}}}))
        m = self._corpus(name="win")[1]
        h = hashlib.sha256(sid.encode()).hexdigest()[:12]
        elig = {e["turn"]: e["tierOneEligible"] for e in m["endings"] if e["session"] == h}
        self.assertEqual(elig.get(0), True, "turn 0 has the done in its window")
        self.assertEqual(elig.get(1), False, "turn 1 (6000 s later) does not: eligible keys on the window, not bool(dones)")

    def test_run_requires_the_binary_and_the_budget_each(self):
        dest = self._corpus()[0]
        run_root = os.path.join(self.td, "runs-flags")
        for args in (["run", "--corpus", dest, "--run-root", run_root, "--arm", "x", "--budget-usd", "1"],       # no --claude-bin
                     ["run", "--corpus", dest, "--run-root", run_root, "--arm", "x", "--claude-bin", self.fake]): # no --budget-usd
            with self.assertRaises(SystemExit) as cm:
                self.je.main(args)
            self.assertNotEqual(cm.exception.code, 0)


    # ── this round: the three measure levers and the post-merge review items ──
    def test_the_arm_disables_the_grouper_executed_and_the_consolidator_a_safety_belt(self):
        """Lever A: arm runs compare WITHOUT regrouping or consolidation. The grouper and consolidator are not measured
        judges, their reshaping of the top set is the largest flap component, and the grouper's stuck calls are the
        120s-alarm timeouts. _plan_session calls _group_store after each placement, so the grouper no-op is EXECUTED by an
        arm pass (a seed with several open tops that would otherwise trip the grouper makes no grouper call); the consolidator
        no-op is a SAFETY BELT, reached by no arm pass (only run_consolidate drives it), kept so an in-process arm cannot leave
        it patched. The fake logs each call's judge. The arm's RESULT is captured and asserted so a signature-slip no-op
        (`lambda: 0`) that crashes every ending is caught here, not only elsewhere (review 2026-09-22 PR 2022)."""
        dest, m = self._corpus(name="nogroup")
        e0 = m["endings"][0]; eid = e0["id"]; st = float(e0["startT"])
        # a seed of THREE open tops born IN the turn (so all three score): the planner's one placement leaves two, which
        # without the grouper no-op trips the grouper
        nodes = {("%s:g%d" % (eid, i)): {"id": "%s:g%d" % (eid, i), "text": "Open top %d" % i, "parentId": None,
                                         "t": st + i, "trail": [], "log": []} for i in (1, 2, 3)}
        (Path(dest) / "state" / "romp" / "goals" / (eid + ".json")).write_text(json.dumps(
            {"rompUuid": eid, "seq": 3, "placementsV": PV, "placements": {}, "status": {}, "nodes": nodes,
             "closedTurns": [], "closedSig": {}}))
        run_root = os.path.join(self.td, "runs-nogroup")
        res = self.je.run_arm_inprocess(dest, "current", None, run_root, None, self.fake, now=T0 + 10**6, builds=1)
        self.assertEqual(self._calls("grouper"), [], "the arm made no grouper or consolidator model call: %r" % self._calls("grouper"))
        self.assertTrue(self._calls("planner"), "the planner still ran (the grouper is what is disabled)")
        # the arm SURVIVED the no-op: no failures, the seeded ending did not crash, its build carries the three tops all
        # scored with g1 completed (the closer's done) and g2, g3 working. A signature-slip no-op crashes every ending.
        self.assertEqual(res["failures"], 0, "the grouper no-op did not crash the arm: %r" % res)
        end = res["endings"][eid]
        self.assertNotIn("crashed", end, "the seeded ending did not crash: %r" % end)
        build = end["builds"][0]                                # keyed by the node SUFFIX (gN), the measure's join key
        self.assertEqual(sorted(build), ["g1", "g2", "g3"], "the three tops are in the build: %r" % build)
        self.assertEqual(build["g1"]["column"], "completed", "the closer's done put g1 completed: %r" % build)
        self.assertTrue(all(build[k]["scored"] for k in build), "the three in-turn tops are all scored: %r" % build)
        # review low 3: the no-ops are restored in the finally (an in-process arm must not leave the module patched)
        jd_loaded = sys.modules[self.je.JUDGE_MODULE_NAME]
        self.assertEqual((jd_loaded._group_store.__name__, jd_loaded._consolidate_store.__name__), ("_group_store", "_consolidate_store"),
                         "the grouper and consolidator are restored after the arm run, not left as the no-op lambdas")

    def test_the_measure_takes_the_majority_of_three_builds_and_counts_the_residual_flap(self):
        """Lever B: a card's column for scoring is the MAJORITY of the three builds of the same store; the flap figure is the
        residual disagreement (the card's columns not all equal). A re-opened card that reads completed/completed/needs_input
        is a leak (majority completed) and one flap; the same re-opened card reading needs_input/needs_input/completed is NOT
        a leak (majority needs_input) though one build read completed; completed/completed/completed is no flap. Scoring by
        the last build alone (not the majority) would flip both leak verdicts."""
        m = self._corpus()[1]
        e = self._ending(m, SIDS[0], 0)
        s, c = float(e["startT"]), float(e["cutT"])
        self._live_store_with_done(SIDS[0], s, c, [{"node": SIDS[0] + ":g1", "op": "followup", "t": c + 7200}])   # the user RE-OPENED g1
        manifest = {"endings": [e]}

        def scored(cols):
            return {"arm": "x", "failures": 0, "endings": {e["id"]: {"builds": [{e["id"] + ":g1": {"column": col, "scored": True}} for col in cols]}}}
        leak = self.je.measure(manifest, scored(["completed", "completed", "needs_input"]), self.state)
        self.assertEqual((leak["leaks"], leak["flaps"]), (1, 1),
                         "majority completed on a re-opened card is a leak, and the three builds disagree (one flap): %r" % leak)
        noleak = self.je.measure(manifest, scored(["needs_input", "needs_input", "completed"]), self.state)
        self.assertEqual((noleak["leaks"], noleak["flaps"]), (0, 1),
                         "majority needs_input is NOT a leak though one build read completed; still one flap: %r" % noleak)
        agree = self.je.measure(manifest, scored(["completed", "completed", "completed"]), self.state)
        self.assertEqual((agree["leaks"], agree["flaps"]), (1, 0), "three equal completed builds: a leak and no flap: %r" % agree)
        self.assertIsNone(self.je._majority([]), "review low 4: a card scored in no build has no column (empty list does not raise)")

    def test_leaks_and_false_interrupts_are_reported_by_class(self):
        """Review item 4 / item 3: the measure reports leaks and false interrupts split by the ending's heuristic class
        (leaksByClass / falseInterruptsByClass, absent at the base), so a reader reads the loose-ended strata
        (offer/question/undone) apart from the finished stratum. A leak on a 'question' ending and a false interrupt on a
        'finished' ending land in their own class buckets, never the other's."""
        m = self._corpus(name="byclass")[1]
        e_q = self._ending(m, SIDS[0], 1); e_f = self._ending(m, SIDS[1], 1)
        self.assertEqual((e_q["class"], e_f["class"]), ("question", "finished"), "the two endings' heuristic classes")
        sq, cq = float(e_q["startT"]), float(e_q["cutT"]); sf, cf = float(e_f["startT"]), float(e_f["cutT"])
        self._live_store_with_done(SIDS[0], sq, cq, [{"node": SIDS[0] + ":g1", "op": "followup", "t": cq + 7200}])   # re-open -> a leak
        self._live_store_with_done(SIDS[1], sf, cf, [{"node": SIDS[1] + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": cf + 600}])   # cross-off -> a false interrupt
        manifest = {"endings": [e_q, e_f]}
        results = {"arm": "x", "failures": 0, "endings": {
            e_q["id"]: {"builds": [{e_q["id"] + ":g1": {"column": "completed", "scored": True}}] * 3},
            e_f["id"]: {"builds": [{e_f["id"] + ":g1": {"column": "needs_input", "scored": True}}] * 3}}}
        mm = self.je.measure(manifest, results, self.state)
        self.assertEqual((mm["leaks"], mm["falseInterrupts"]), (1, 1), "one leak and one false interrupt overall: %r" % mm)
        self.assertEqual((mm.get("leaksByClass") or {}).get("question"), 1, "the leak is attributed to the question stratum: %r" % mm.get("leaksByClass"))
        self.assertEqual((mm.get("leaksByClass") or {}).get("finished"), 0, "no leak in the finished stratum")
        self.assertEqual((mm.get("falseInterruptsByClass") or {}).get("finished"), 1, "the false interrupt is in the finished stratum: %r" % mm.get("falseInterruptsByClass"))
        self.assertEqual((mm.get("falseInterruptsByClass") or {}).get("question"), 0, "no false interrupt in the question stratum")

    def test_the_measure_emits_per_ending_attribution_and_labeller_keyed_buckets(self):
        """Review round two low 4: the landing bar reads strata from the labeller's class, so the measure emits per-ending
        attribution (id, arm, leak, false interrupt, heuristic class, labeller class) and labeller-keyed buckets beside the
        heuristic ones, from labels passed in. The labeller class is set apart from the heuristic here to prove the keying is
        distinct."""
        m = self._corpus(name="attrib")[1]
        e_q = self._ending(m, SIDS[0], 1); e_f = self._ending(m, SIDS[1], 1)   # heuristic: question, finished
        sq, cq = float(e_q["startT"]), float(e_q["cutT"]); sf, cf = float(e_f["startT"]), float(e_f["cutT"])
        self._live_store_with_done(SIDS[0], sq, cq, [{"node": SIDS[0] + ":g1", "op": "followup", "t": cq + 7200}])   # leak
        self._live_store_with_done(SIDS[1], sf, cf, [{"node": SIDS[1] + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": cf + 600}])   # false interrupt
        manifest = {"endings": [e_q, e_f]}
        results = {"arm": "x", "failures": 0, "endings": {
            e_q["id"]: {"builds": [{e_q["id"] + ":g1": {"column": "completed", "scored": True}}] * 3},
            e_f["id"]: {"builds": [{e_f["id"] + ":g1": {"column": "needs_input", "scored": True}}] * 3}}}
        labels = {e_q["id"]: "finished", e_f["id"]: "question"}   # labeller classes, deliberately swapped from the heuristic
        mm = self.je.measure(manifest, results, self.state, labels=labels)
        attr = {a["id"]: a for a in mm["attribution"]}
        self.assertEqual((attr[e_q["id"]]["leak"], attr[e_q["id"]]["heuristicClass"], attr[e_q["id"]]["labellerClass"]),
                         (True, "question", "finished"), "the leak ending carries both class keyings: %r" % attr[e_q["id"]])
        self.assertEqual((attr[e_f["id"]]["falseInterrupt"], attr[e_f["id"]]["heuristicClass"], attr[e_f["id"]]["labellerClass"]),
                         (True, "finished", "question"), "the false-interrupt ending carries both class keyings: %r" % attr[e_f["id"]])
        self.assertEqual(mm["leaksByLabellerClass"].get("finished"), 1, "the leak is bucketed by the labeller class: %r" % mm["leaksByLabellerClass"])
        self.assertEqual(mm["falseInterruptsByLabellerClass"].get("question"), 1, "the false interrupt is bucketed by the labeller class: %r" % mm["falseInterruptsByLabellerClass"])

    def test_a_card_scored_in_one_build_of_three_flaps_and_does_not_leak(self):
        """Review MED 1: the majority is over ALL builds with a value per build, an UNSCORED build taking a sentinel, not a
        skipped slot. A completed re-opened card scored in ONE build of three (unscored in the other two) is a FLAP (the
        builds differ) and NO leak (the majority is the sentinel, not completed). The base read only the scored build,
        counted no flap and scored the lone build's completed as a leak."""
        m = self._corpus(name="oneof3")[1]
        e = self._ending(m, SIDS[0], 0)
        s, c = float(e["startT"]), float(e["cutT"])
        self._live_store_with_done(SIDS[0], s, c, [{"node": SIDS[0] + ":g1", "op": "followup", "t": c + 7200}])   # the user RE-OPENED g1
        manifest = {"endings": [e]}
        # g1 scored completed in build 0 only; unscored (absent) in builds 1 and 2
        results = {"arm": "x", "failures": 0, "endings": {e["id"]: {"builds": [
            {e["id"] + ":g1": {"column": "completed", "scored": True}}, {}, {}]}}}
        mm = self.je.measure(manifest, results, self.state)
        self.assertEqual((mm["leaks"], mm["flaps"]), (0, 1),
                         "one scored completed against two unscored: a flap, and no leak (the majority is the unscored sentinel): %r" % mm)

    def test_a_tie_including_the_unscored_sentinel_scores_no_column_either_order(self):
        """Review round two MEDIUM: a 1-1-1 tie that includes the UNSCORED sentinel must not let one build decide the column.
        The sentinel wins any tie it is part of (no column; the flap still counts), deterministically in either order. The
        head scored `completed` and a leak for [completed, needs_input, unscored] and flipped on a reorder."""
        U = self.je._UNSCORED
        self.assertIs(self.je._majority(["completed", "needs_input", U]), U, "a 1-1-1 tie with the sentinel scores no column")
        self.assertIs(self.je._majority([U, "needs_input", "completed"]), U, "same values reordered: deterministic")
        self.assertIs(self.je._majority(["needs_input", U, "completed"]), U)
        self.assertEqual(self.je._majority(["completed", "completed", U]), "completed", "a real majority still wins over the sentinel")
        # through the measure: a re-opened card scored completed / needs_input / unscored is no leak either order, one flap
        m = self._corpus(name="tie3")[1]
        e = self._ending(m, SIDS[0], 0)
        s, c = float(e["startT"]), float(e["cutT"])
        self._live_store_with_done(SIDS[0], s, c, [{"node": SIDS[0] + ":g1", "op": "followup", "t": c + 7200}])   # re-opened g1
        manifest = {"endings": [e]}
        def res(cols):
            builds = [({} if col is None else {e["id"] + ":g1": {"column": col, "scored": True}}) for col in cols]
            return {"arm": "x", "failures": 0, "endings": {e["id"]: {"builds": builds}}}
        for cols in (["completed", "needs_input", None], [None, "needs_input", "completed"]):
            mm = self.je.measure(manifest, res(cols), self.state)
            self.assertEqual((mm["leaks"], mm["flaps"]), (0, 1), "sentinel-tie card: no leak, one flap, order %r: %r" % (cols, mm))

    def test_a_column_needs_a_strict_majority_of_the_builds(self):
        """the 2026-09-22 PR 2035 review, MEDIUM: a card wins a column only on a STRICT majority of ALL builds, else no column (the
        sentinel), so no single build decides in any order. The pre-fix rule (first-seen among the modal real columns) let
        the first build decide an all-real tie: [completed, needs_input, working] scored `completed` and a leak, and a
        two-build [completed, needs_input] scored `completed` while its reverse scored `needs_input`. The odd-first and
        odd-middle cases (a real 2-of-3 majority) still score, catching a first-build or middle-build mutant."""
        U = self.je._UNSCORED
        self.assertIs(self.je._majority(["completed", "needs_input", "working"]), U, "three different real columns: no strict majority, no column")
        self.assertIs(self.je._majority(["completed", "needs_input"]), U, "a two-build real tie: no column, either order")
        self.assertIs(self.je._majority(["needs_input", "completed"]), U, "the reverse: also no column (order-independent)")
        self.assertEqual(self.je._majority(["needs_input", "completed", "completed"]), "completed", "odd-first: a real 2-of-3 majority scores")
        self.assertEqual(self.je._majority(["completed", "needs_input", "completed"]), "completed", "odd-middle: the same")
        self.assertEqual(self.je._majority(["completed", "completed", U]), "completed", "2 of 3 real is a strict majority over the sentinel")
        self.assertIs(self.je._majority(["completed", U]), U, "1 of 2 is not a strict majority")
        # through the measure on a re-opened card: an all-real tie and a two-build tie score NO leak, in every order
        m = self._corpus(name="strict")[1]
        e = self._ending(m, SIDS[0], 0)
        s, c = float(e["startT"]), float(e["cutT"])
        self._live_store_with_done(SIDS[0], s, c, [{"node": SIDS[0] + ":g1", "op": "followup", "t": c + 7200}])   # re-opened g1
        manifest = {"endings": [e]}
        def res(cols):
            builds = [({} if col is None else {e["id"] + ":g1": {"column": col, "scored": True}}) for col in cols]
            return {"arm": "x", "failures": 0, "endings": {e["id"]: {"builds": builds}}}
        for cols in (["completed", "needs_input", "working"], ["working", "needs_input", "completed"],
                     ["completed", "needs_input"], ["needs_input", "completed"]):
            mm = self.je.measure(manifest, res(cols), self.state)
            self.assertEqual(mm["leaks"], 0, "no strict majority: no column, no leak, order %r: %r" % (cols, mm))
        for cols in (["needs_input", "completed", "completed"], ["completed", "needs_input", "completed"]):
            mm = self.je.measure(manifest, res(cols), self.state)
            self.assertEqual(mm["leaks"], 1, "a real 2-of-3 majority completed scores the leak, order %r: %r" % (cols, mm))

    def test_report_records_a_wrong_shape_labels_file_as_unreadable_not_a_raise(self):
        """the 2026-09-22 PR 2040 review: a labels.json that is valid JSON of the WRONG SHAPE (a top-level object, a list of strings, a
        number) must be recorded as labellerKeying `unreadable`, never raise out of report. The head caught only OSError and
        ValueError, so a wrong shape raised (AttributeError / TypeError) out of the report."""
        dest, m = self._corpus(name="lblshape")
        e = self._ending(m, SIDS[0], 0)
        run_root = os.path.join(self.td, "runs-lblshape"); os.makedirs(os.path.join(run_root, "baseline"))
        results = {"arm": "baseline", "failures": 0, "buildsPerCard": 3, "endings": {e["id"]: {"builds": [{}] * 3}}}
        Path(run_root, "baseline", "results.json").write_text(json.dumps(results))
        for shape in (json.dumps({"an": "object"}), json.dumps(["a", "list", "of", "strings"]), json.dumps(7)):
            Path(run_root, "labels.json").write_text(shape)
            r = self.je.report(dest, run_root, self.state, figure=None)[0]   # must not raise
            self.assertEqual(r.get("labellerKeying"), "unreadable", "a wrong-shape labels.json (%s) is recorded unreadable, not raised: %r" % (shape[:20], r.get("labellerKeying")))

    def test_report_keys_labeller_buckets_from_labels_and_records_a_torn_file(self):
        """the 2026-09-22 PR 2035 review, MEDIUM: report keys the labeller buckets from labels.json's `label` (not the heuristic
        `class`), adds an `unlabeled` bucket so the buckets sum to the totals, and stamps the keying's source; a torn
        labels.json is recorded (labellerKeying `unreadable`), never a silent empty pass. The head fell back to an empty
        mapping and dropped unlabeled endings, so a torn or missing file read as zero leaks in every stratum."""
        dest, m = self._corpus(name="lblbucket")
        e = self._ending(m, SIDS[0], 1)                      # heuristic class 'question'
        s, c = float(e["startT"]), float(e["cutT"])
        self._live_store_with_done(SIDS[0], s, c, [{"node": SIDS[0] + ":g1", "op": "followup", "t": c + 7200}])   # re-opened -> a leak
        run_root = os.path.join(self.td, "runs-lblbucket"); os.makedirs(os.path.join(run_root, "baseline"))
        results = {"arm": "baseline", "failures": 0, "buildsPerCard": 3,
                   "endings": {e["id"]: {"builds": [{e["id"] + ":g1": {"column": "completed", "scored": True}}] * 3}}}
        Path(run_root, "baseline", "results.json").write_text(json.dumps(results))
        # a real labels.json: the leak ending labelled 'finished' (label), heuristic 'question' (class); reading `class` would misbucket
        Path(run_root, "labels.json").write_text(json.dumps([{"id": e["id"], "class": "question", "label": "finished"}]))
        r = self.je.report(dest, run_root, self.state, figure=None)[0]
        self.assertEqual(r["leaks"], 1, "the re-opened completed card is a leak")
        self.assertEqual(r.get("labellerKeying"), "present", "the keying source is stamped: %r" % r.get("labellerKeying"))
        self.assertEqual(r.get("leaksByLabellerClass"), {"finished": 1}, "keyed on the labeller's label, not the heuristic class: %r" % r.get("leaksByLabellerClass"))
        self.assertEqual(sum((r.get("leaksByLabellerClass") or {}).values()), r["leaks"], "the labeller buckets sum to the total")
        # a torn labels.json: recorded, not a silent empty pass; the leak buckets under 'unlabeled', still summing to the total
        Path(run_root, "labels.json").write_text("{ torn not json\n")
        r2 = self.je.report(dest, run_root, self.state, figure=None)[0]
        self.assertEqual(r2.get("labellerKeying"), "unreadable", "a torn labels.json is recorded as unreadable, not silently empty: %r" % r2.get("labellerKeying"))
        self.assertEqual(r2.get("leaksByLabellerClass"), {"unlabeled": 1}, "a torn file buckets the leak under unlabeled, summing to the total: %r" % r2.get("leaksByLabellerClass"))

    def test_non_arm_failures_are_carried_and_printed_beside_the_comparable_count(self):
        """The 2026-09-22 PR 2022 review: an excluded (non-arm) failure is counted nowhere else, so the measure carries nonArmFailures into
        the record and the report prints it in the failures cell (a comparable arm reads '0 (2 non-arm)')."""
        dest, m = self._corpus(name="nonarm")
        e = self._ending(m, SIDS[0], 0)
        manifest = {"endings": [e]}
        results = {"arm": "baseline", "failures": 0, "nonArmFailures": 2, "buildsPerCard": 3,
                   "callsByJudge": {"planner": 3, "closer": 3}, "endings": {e["id"]: {"builds": [{}] * 3}}}
        mm = self.je.measure(manifest, results, self.state)
        self.assertEqual((mm["failures"], mm.get("nonArmFailures"), mm["comparable"]), (0, 2, True),
                         "the excluded failures ride the record beside the comparable count: %r" % mm)
        run_root = os.path.join(self.td, "runs-nonarm"); os.makedirs(os.path.join(run_root, "baseline"))
        Path(run_root, "baseline", "results.json").write_text(json.dumps(results))
        self.je.report(dest, run_root, self.state, figure=None)
        self.assertIn("0 (2 non-arm)", Path(run_root, "table.md").read_text(), "the failures cell surfaces the excluded count")

    def test_a_crashed_ending_is_padded_to_the_build_count_before_scoring(self):
        """the 2026-09-22 PR 2035 review, low 4: a crash leaves fewer builds than buildsPerCard, so the finished builds must be padded to
        buildsPerCard with the sentinel before scoring, or one surviving build decides the column with no flap. A crashed
        one-of-three ending on a re-opened card scores no leak (the one completed build is a minority once padded) and one
        flap. The head scored it from the single build: a leak."""
        m = self._corpus(name="crashpad")[1]
        e = self._ending(m, SIDS[0], 0)
        s, c = float(e["startT"]), float(e["cutT"])
        self._live_store_with_done(SIDS[0], s, c, [{"node": SIDS[0] + ":g1", "op": "followup", "t": c + 7200}])   # re-opened g1
        manifest = {"endings": [e]}
        results = {"arm": "x", "failures": 4, "buildsPerCard": 3,
                   "endings": {e["id"]: {"class": e["class"], "crashed": "synthetic",
                                         "builds": [{e["id"] + ":g1": {"column": "completed", "scored": True}}]}}}   # one finished build, then a crash
        mm = self.je.measure(manifest, results, self.state)
        self.assertEqual((mm["leaks"], mm["flaps"]), (0, 1),
                         "the crashed ending is padded to 3 with the sentinel: one completed build is no strict majority, no leak, one flap: %r" % mm)

    def test_the_build_count_and_the_majority_rule_are_stamped(self):
        """Review MED 1: the per-card build count and the majority/flap definition are stamped in results.json and the report
        table, so a reader knows a card's column is the majority of N builds and the flap the residual disagreement."""
        dest, m = self._corpus(name="stamp")
        run_root = os.path.join(self.td, "runs-stamp")
        self.je.run_arm(dest, "baseline", None, run_root, None, self.fake, now=T0 + 10**6)
        res = json.loads(Path(run_root, "baseline", "results.json").read_text())
        self.assertEqual(res.get("buildsPerCard"), 3, "results.json stamps the per-card build count: %r" % res.get("buildsPerCard"))
        self.je.report(dest, run_root, self.state, figure=None)
        table = Path(run_root, "table.md").read_text()
        self.assertIn("majority", table.lower(), "the table states the majority rule: %r" % table[-300:])
        self.assertIn("3 builds", table, "the table states the build count: %r" % table[-300:])

    def test_the_corpus_builder_resolves_the_helper_through_the_credentials_module(self):
        """Review low 5: build_corpus resolves the apiKeyHelper through the credentials module, in Claude Code's own
        precedence, not by reading claude_root/settings.json directly. A MANAGED helper (read by the child from the system
        path) is NOT copied into the corpus; a USER helper is. A synthetic managed file stands in (pointed at through the
        credentials module's managed_settings_path), so no real system file is touched."""
        cred = self.je._credentials()
        managed = os.path.join(self.td, "managed-settings.json")
        Path(managed).write_text(json.dumps({"apiKeyHelper": "echo managed-key"}))
        saved_msp = cred.managed_settings_path
        empty_claude = Path(self.td, "empty-claude"); empty_claude.mkdir()   # no USER helper: the only helper is the managed one
        dest = os.path.join(self.td, "managed-corpus")
        try:
            cred.managed_settings_path = lambda: managed
            cred._SETTINGS_CACHE.clear()
            helper, source = self.je._corpus_helper(empty_claude)
            self.assertEqual((helper, source), ("echo managed-key", "managed"), "the managed helper resolves through the credentials module")
            self.je.build_corpus(self.state, empty_claude, dest, per_class=10, now=T0 + 10**6)
        finally:
            cred.managed_settings_path = saved_msp
            cred._SETTINGS_CACHE.clear()
        self.assertFalse(Path(dest, "claude", "settings.json").exists(),
                         "a managed helper is NOT copied into the corpus (the child reads it from the system path)")
        # the setUp's live claude root carries a USER helper, which IS copied
        dest2 = self._corpus(name="user-helper")[0]
        self.assertEqual(json.loads(Path(dest2, "claude", "settings.json").read_text()), {"apiKeyHelper": "echo synthetic-key"},
                         "a user helper is copied into the corpus")

    def test_a_labeled_row_from_a_torn_journal_keeps_its_label_and_carries_no_tier_one_error(self):
        """Review low 7: tierOneError marks a GENUINE read fault (tier one None). A torn journal row that still yields a
        label (the surviving rows read) leaves the label standing with NO tierOneError beside it; the torn-row fault is
        recorded at the run level (the summary's tierOneErrors), not on the labeled row."""
        dest, m = self._corpus(name="lowseven")
        sid = SIDS[0]
        e = self._ending(m, sid, 0)
        s, c = float(e["startT"]), float(e["cutT"])
        self._live_store_with_done(sid, s, c, [])            # a closer done on g1 in the window
        ov = self.state / "overrides" / (sid + ".jsonl")     # a torn row on its own line, then the user's cross-off
        ov.write_text("{ torn not json\n" + json.dumps({"node": sid + ":g1", "op": "clear", "src": "user", "why": "cleared from the feed", "t": c + 600}) + "\n")
        summary = self.je.label(dest, os.path.join(self.td, "runs-lowseven"), self.state, claude_bin=self.fake, model="fake")
        row = {r["id"]: r for r in json.loads(Path(self.td, "runs-lowseven", "labels.json").read_text())}[e["id"]]
        self.assertEqual(row["tierOne"], "finished", "the clear after the torn row still labels the ending finished: %r" % row)
        self.assertIsNone(row["tierOneError"], "a torn row that still yields a label carries NO tierOneError on the row: %r" % row)
        h = hashlib.sha256(sid.encode()).hexdigest()[:12]
        self.assertIn({"session": h, "error": "torn-journal-row"}, summary["tierOneErrors"],
                      "the torn-row fault rides the run-level summary, not the labeled row: %r" % summary["tierOneErrors"])


if __name__ == "__main__":
    unittest.main()
