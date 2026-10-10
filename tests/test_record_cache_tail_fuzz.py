"""Differential test of the assembly's roads over a tail-only leaf (2026-10-08, after two reviews in a row found wrong trees
in hand-picked cases): random appends mixing every record kind the fold's gates and the restore read, over a leaf with an
assembly document, and the hydrated tree after the road the code picks (fold, restore or whole parse), and again after a
restart (the boot's restore), must equal a cold whole parse (the rule off, no checkpoint directory).

The leaf's pre-cut part carries what the gates and the restore key on: prompt ids on every prompt, a typed slash command's raw
twin (its wrapper in the tail re-classifies it), a Skill payload record (a Skill tool_use in the tail links it), and a
compaction whose stitch target was never written (a record carrying it in the tail rebinds it). The appends draw from: prompts
(a fresh, a tail-reused or a pre-cut prompt id; a current, stale or missing stamp), replies, command wrappers wearing the
pre-cut twin's id or a fresh one, Skill tool_uses on the linked id or a fresh one, compactions (a boundary and its summary, the
summary a second earlier, the summary in the next append, a stale boundary), summaries off any boundary, verbatim duplicates,
the dangling target, and swaps of adjacent records.

WideDifferentialRoads (2026-10-09, after a third review found four defects the first test could not reach) widens both the
generator and the harness: stamps at, just past and inside the gap between the document's watermark and the cut's first tail
record; a stale compaction boundary under a current summary, and a lone one; records that open no turn on the settled pre-cut
tip; a typed slash command written where the next settle puts the cut, with a COST check (two whole parses in a row after the
settle's restart fail the case); documents without their preGates key (the old format); restarts between appends; and every
step's tree compared, with a floor on how many came from the restore road.

ROMP_FUZZ_CASES sets the case count (default 120, under a minute for both tests); ROMP_FUZZ_SEED the seed (default 1);
ROMP_FUZZ_ONLY=<case> replays one case of the wide test; ROMP_FUZZ_VERBOSE=1 prints each failing case's hazards and steps. A
longer sweep, run locally: ROMP_FUZZ_CASES=2000 ROMP_FUZZ_SEED=7. Synthetic only: invented text, placeholder ids."""
import collections
import json
import os
import random
import shutil
import sys
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import test_record_cache_tail_r2 as R                   # noqa: E402  one kernel copy: R2Base, knobs, fresh
from test_asm_checkpoint_served import transcript, iso  # noqa: E402

T, em, SID, NOW, WINDOW = R.T, R.em, R.SID, R.NOW, R.WINDOW
CASES = int(os.environ.get("ROMP_FUZZ_CASES", "120"))
SEED = int(os.environ.get("ROMP_FUZZ_SEED", "1"))
WRAP = "<command-name>/review</command-name>\n<command-message>review</command-message>\n<command-args></command-args>"


class Gen:
    """One case's appends, chained on the leaf's current tip."""
    KINDS = ("prompt", "reply", "compact", "wrapper", "skill", "orphan", "dup", "ghost")
    WEIGHTS = (30, 30, 18, 5, 5, 4, 5, 3)
    COMPACTS = ("pair", "pair", "early-summary", "split", "stale-boundary", "bare")
    FRESH_SUMMARY = ()

    def _near_t(self, kinds):
        raise NotImplementedError                       # WideGen's: this generator draws no near-cut stamp

    def _more(self, k, kinds, out):
        """A kind the subclass adds (and a dup or ghost whose condition failed: nothing)."""

    def __init__(self, rnd, parent, t, precut_t):
        self.rnd, self.parent, self.t, self.precut_t = rnd, parent, t, precut_t
        self.n, self.written, self.pids, self.pending = 0, [], [], []

    def _id(self, p):
        self.n += 1
        return "fz-%s%d" % (p, self.n)                  # never a base record's uuid (u1, a1, b40, s40, ...)

    def _stamp(self, kinds):
        x = self.rnd.random()
        if x < 0.04:
            kinds.append("stale")
            return iso(self.precut_t + self.rnd.randrange(0, 600))
        if x < 0.05:
            kinds.append("nostamp")
            return None
        self.t += self.rnd.randrange(5, 60)
        return iso(self.t)

    def _rec(self, r, ts):
        if ts is not None:
            r["timestamp"] = ts
        self.parent = r["uuid"]
        return r

    def append(self):
        kinds, out = [], list(self.pending)
        if self.pending:
            kinds.append("late-summary")
        self.pending = []
        for _ in range(self.rnd.randint(1, 5)):
            k = self.rnd.choices(self.KINDS, weights=self.WEIGHTS)[0]   # mostly benign: the roads a hazard picks need company
            if k == "prompt":
                pid = self.rnd.choices(("fresh", "tail", "pre"), weights=(6, 3, 1))[0]
                pv = {"fresh": self._id("pf"), "tail": self.rnd.choice(self.pids) if self.pids else self._id("pf"),
                      "pre": "p%d" % self.rnd.randrange(1, 40)}[pid]
                self.pids.append(pv)
                kinds.append("prompt:" + pid)
                ts = self._stamp(kinds)
                out.append(self._rec({"type": "user", "uuid": self._id("u"), "parentUuid": self.parent, "promptSource": "typed",
                                      "promptId": pv, "cwd": "/w/notes-api",
                                      "message": {"role": "user", "content": "step %d" % self.n}}, ts))
            elif k == "reply":
                kinds.append("reply")
                ts = self._stamp(kinds)
                out.append(self._rec({"type": "assistant", "uuid": self._id("a"), "parentUuid": self.parent, "cwd": "/w/notes-api",
                                      "message": {"role": "assistant", "content": [{"type": "text", "text": "ok %d " % self.n * 5}],
                                                  "stop_reason": "end_turn"}}, ts))
            elif k == "wrapper":
                pre = self.rnd.random() < 0.6
                kinds.append("wrapper:" + ("pre" if pre else "fresh"))
                self.t += 3
                out.append(self._rec({"type": "user", "uuid": self._id("w"), "parentUuid": self.parent, "cwd": "/w/notes-api",
                                      "promptId": "pid-pre" if pre else self._id("pw"),
                                      "message": {"role": "user", "content": WRAP}}, iso(self.t)))
            elif k == "skill":
                pre = self.rnd.random() < 0.6
                kinds.append("skill:" + ("pre" if pre else "fresh"))
                self.t += 3
                out.append(self._rec({"type": "assistant", "uuid": self._id("k"), "parentUuid": self.parent, "cwd": "/w/notes-api",
                                      "message": {"role": "assistant", "content": [
                                          {"type": "tool_use", "id": "toolu_pre_sk" if pre else self._id("toolu_"), "name": "Skill",
                                           "input": {"skill": "deploy"}}], "stop_reason": "tool_use"}}, iso(self.t)))
            elif k == "compact":
                v = self.rnd.choice(self.COMPACTS)
                kinds.append("compact:" + v)
                bu, su = self._id("b"), self._id("s")
                bt = (self.precut_t + 300) if v.startswith("stale-") else self._near_t(kinds) if v == "gap-boundary" \
                    else (self.t + 5)
                self.t = max(self.t, bt) + 2
                b = {"type": "system", "subtype": "compact_boundary", "uuid": bu, "parentUuid": None, "logicalParentUuid": self.parent,
                     "timestamp": iso(bt), "compactMetadata": {"trigger": "auto", "preTokens": 160000, "postTokens": 9000}}
                s = {"type": "user", "uuid": su, "parentUuid": bu, "isCompactSummary": True,
                     "timestamp": iso(bt - 1 if v == "early-summary" else self.t - 1 if v in self.FRESH_SUMMARY else bt + 1),
                     "message": {"role": "user", "content": "summary so far: %d" % self.n}}
                out.append(b)
                self.parent = bu
                if v == "split":
                    self.pending = [s]
                    self.parent = su
                elif v not in ("bare", "stale-bare"):
                    out.append(s)
                    self.parent = su
            elif k == "orphan":
                kinds.append("orphan-summary")
                self.t += 2
                out.append(self._rec({"type": "user", "uuid": self._id("o"), "parentUuid": self.parent, "isCompactSummary": True,
                                      "message": {"role": "user", "content": "summary so far: orphan %d" % self.n}}, iso(self.t)))
            elif k == "dup" and self.written:
                kinds.append("dup")
                out.append(dict(self.written[-1]))
            elif k == "ghost" and not any(r.get("uuid") == "ghost40" for r in self.written + out):
                kinds.append("ghost")
                self.t += 3
                out.append(self._rec({"type": "user", "uuid": "ghost40", "parentUuid": self.parent, "promptSource": "typed",
                                      "cwd": "/w/notes-api", "message": {"role": "user", "content": "the stitch target"}},
                                     iso(self.t)))
            else:
                self._more(k, kinds, out)
        if len(out) > 2 and self.rnd.random() < 0.15:
            i = self.rnd.randrange(0, len(out) - 1)
            out[i], out[i + 1] = out[i + 1], out[i]
            kinds.append("swap")
        self.written += [r for r in out if r.get("uuid")]
        return out, kinds


class DifferentialRoads(R.R2Base):
    TURNS = 80

    def setUp(self):
        super().setUp()
        for r in self.recs:                               # the pre-cut features the gates and the restore key on
            if r.get("type") == "user" and str(r.get("uuid", "")).startswith("u"):
                r["promptId"] = "p" + r["uuid"][1:]
            if r.get("uuid") == "u10":
                r["promptId"] = "pid-pre"
                r["message"]["content"] = "/review"       # a typed slash command's raw twin
            if r.get("uuid") == "u12":
                r["sourceToolUseID"] = "toolu_pre_sk"     # a Skill payload record
                r["message"]["content"] = "instructions for the deploy skill"
            if r.get("uuid") == "b40":
                real = r["logicalParentUuid"]
                r["logicalParentUuid"] = "ghost40"        # a stitch target never written, repaired through preservedSegment
                r["compactMetadata"]["preservedSegment"] = {"tailUuid": real, "anchorUuid": real, "headUuid": real}
        Path(self.path).write_text("".join(json.dumps(x) + "\n" for x in self.recs))

    def _ref(self):
        with T.knobs(0, 4, roots=[str(self.proj)]):
            self._reset()
            saved = em._CKPT_DIR_FN; em._CKPT_DIR_FN = None
            try:
                return T._strip_tree(self.parse())
            finally:
                em._CKPT_DIR_FN = saved

    def _parse_measured(self):
        s0 = dict(em._ASM_STATS)
        tree = self.parse()
        d = {k: v - s0.get(k, 0) for k, v in em._ASM_STATS.items() if v != s0.get(k, 0)}
        em.hydrate(tree, SID)
        road = "whole" if d.get("full") else "restore" if d.get("restore") else "fold" if d.get("fold") else "serve"
        why = sorted(k[2:] for k in d if k.startswith("g:")) + sorted(k for k in d if k.startswith("restore:") and k.endswith("Refused"))
        return road + ("(%s)" % ",".join(why) if why else ""), T._strip_tree(tree)

    def run_cases(self, n, seed):
        rnd = random.Random(seed)
        ck = self.td / "checkpoints"
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            tree = self.parse()
            self.assertTrue(em.asm_checkpoint_write(self.path, SID, tree=tree), em.asm_checkpoint_stats())
            del tree
        base_size, base_mtime = os.path.getsize(self.path), os.stat(self.path).st_mtime
        docs = {p.name: p.read_bytes() for p in ck.iterdir()}
        precut_t = NOW - 86400 + 5 * 60
        fails, roads = collections.Counter(), collections.Counter()
        examples = {}
        for c in range(n):
            with open(self.path, "r+b") as f:             # the leaf and the document as they were: every case starts restored
                f.truncate(base_size)
            os.utime(self.path, (base_mtime, base_mtime))
            for p in list(ck.iterdir()):
                if p.name not in docs:
                    p.unlink()
            for name, data in docs.items():
                (ck / name).write_bytes(data)
            self._reset()
            for m in ("_ASM_CHAIN_REFUSED_PATHS", "_ASM_CKPT_REFUSED"):
                getattr(em, m).clear()                    # a refusal of another case's tail is no fact about this one
            g = Gen(rnd, self.parent, self.t, precut_t)
            sig = []
            path_roads = []
            with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
                path_roads.append(self._parse_measured()[0])   # the boot's restore
                for step in range(2):
                    recs, kinds = g.append()
                    with open(self.path, "a") as f:
                        f.write("".join(json.dumps(r) + "\n" for r in recs))
                    sig.append(tuple(sorted(set(kinds))))
                    road, got = self._parse_measured()
                    path_roads.append(road)
                self._reset()
                broad, bgot = self._parse_measured()      # a restart over the same tail
            ref = self._ref()
            roads["/".join(path_roads)] += 1
            for variant, r_, t_ in (("append", road, got), ("boot", broad, bgot)):
                if t_ != ref:
                    key = (variant, r_, sig[0], sig[1])
                    fails[key] += 1
                    examples.setdefault(key, c)
        return fails, roads, examples

    HAZARDS = ("stale", "nostamp", "wrapper:pre", "skill:pre", "orphan-summary", "dup", "ghost", "swap", "prompt:pre",
               "compact:early-summary", "compact:split", "compact:stale-boundary", "late-summary")

    def classes(self, fails):
        """Failing cases grouped by variant and the hazards present in the case's appends (a distinct failing shape)."""
        out = collections.Counter()
        for (variant, _road, a, b), v in fails.items():
            hz = tuple(sorted({k for k in a + b if k in self.HAZARDS})) or ("none",)
            out[(variant,) + hz] += v
        return out

    def test_random_appends_give_the_cold_parse(self):
        fails, roads, examples = self.run_cases(CASES, SEED)
        cls = self.classes(fails)
        sys.stderr.write("fuzz classes: %d failing cases, %d shapes (variant + hazards): %s\n" % (
            sum(fails.values()), len(cls), "; ".join("%s x%d" % ("+".join(k), v) for k, v in cls.most_common())))
        lines = ["%s road=%s first=%s second=%s x%d (case %d)" % (k[0], k[1], k[2], k[3], v, examples[k])
                 for k, v in sorted(fails.items(), key=lambda kv: -kv[1])]
        sys.stderr.write("fuzz: %d cases, seed %d, roads %r, %d failing cases in %d shapes\n%s\n" % (
            CASES, SEED, dict(roads), sum(fails.values()), len(fails), "\n".join(lines[:40])))
        self.assertEqual(sum(fails.values()), 0, "\n".join(lines[:40]))



class WideGen(Gen):
    """The third review's shapes (2026-10-09) beside Gen's: stamps near and inside the gap between the document's watermark (the
    last pre-cut conversational stamp, `wm`) and the cut's first tail record (`cut_t`); a stale compaction boundary under a
    summary stamped current, and a lone stale boundary; a compaction boundary stamped in the gap; replies and tool results
    parented on the settled pre-cut tip (`tip`) after the tail moved on; and, through slash(), a typed slash command's raw twin
    and wrapper written fresh where the next settle puts the cut."""
    KINDS = Gen.KINDS + ("gap", "tip")
    WEIGHTS = Gen.WEIGHTS + (6, 4)
    COMPACTS = Gen.COMPACTS + ("stale-boundary-fresh-summary", "stale-bare", "gap-boundary")
    FRESH_SUMMARY = ("stale-boundary-fresh-summary", "gap-boundary")
    NEAR = ("w+1", "w", "gap", "cut")

    def __init__(self, rnd, parent, t, precut_t, wm, cut_t, tip):
        super().__init__(rnd, parent, t, precut_t)
        self.wm, self.cut_t, self.tip = wm, cut_t, tip

    def _near_t(self, kinds):
        v = self.rnd.choice(self.NEAR)
        kinds.append("nearcut:" + v)
        return {"w+1": self.wm + 1, "w": self.wm, "cut": self.cut_t,
                "gap": self.rnd.uniform(self.wm + 2, self.cut_t - 1) if self.cut_t - self.wm > 3 else self.wm + 1}[v]

    def _stamp(self, kinds):
        x = self.rnd.random()
        if x < 0.04:
            kinds.append("stale")
            return iso(self.precut_t + self.rnd.randrange(0, 600))
        if x < 0.05:
            kinds.append("nostamp")
            return None
        if x < 0.07:
            return iso(self._near_t(kinds))
        self.t += self.rnd.randrange(5, 60)
        return iso(self.t)

    def _reply(self, parent, ts):
        return {"type": "assistant", "uuid": self._id("a"), "parentUuid": parent, "timestamp": ts, "cwd": "/w/notes-api",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "ok %d " % self.n * 5}],
                            "stop_reason": "end_turn"}}

    def _tool_result(self, parent, ts):
        return {"type": "user", "uuid": self._id("r"), "parentUuid": parent, "timestamp": ts, "cwd": "/w/notes-api",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": self._id("toolu_r"),
                                                          "content": "exit 0"}]}}

    def _prompt(self, parent, ts, pid=None):
        return {"type": "user", "uuid": self._id("u"), "parentUuid": parent, "timestamp": ts, "promptSource": "typed",
                "promptId": pid or self._id("pf"), "cwd": "/w/notes-api", "message": {"role": "user", "content": "step %d" % self.n}}

    def _more(self, k, kinds, out):
        if k == "gap":                                    # a record stamped near the cut, after a fresh compaction in half the cases
            if self.rnd.random() < 0.5:                   #  (the boundary demotion's road holds the rest to the watermark)
                kinds.append("gap:after-compaction")
                bu, su = self._id("b"), self._id("s")
                self.t += 5
                out.append({"type": "system", "subtype": "compact_boundary", "uuid": bu, "parentUuid": None,
                            "logicalParentUuid": self.parent, "timestamp": iso(self.t),
                            "compactMetadata": {"trigger": "auto", "preTokens": 160000, "postTokens": 9000}})
                out.append({"type": "user", "uuid": su, "parentUuid": bu, "isCompactSummary": True, "timestamp": iso(self.t + 1),
                            "message": {"role": "user", "content": "summary so far: gap %d" % self.n}})
                self.parent, self.t = su, self.t + 2
            what = self.rnd.choices(("reply", "prompt", "tool_result"), weights=(6, 2, 2))[0]
            kinds.append("gap:" + what)
            ts = iso(self._near_t(kinds))
            out.append(self._rec({"reply": self._reply, "prompt": self._prompt, "tool_result": self._tool_result}[what](
                self.parent, ts), ts))
        elif k == "tip":                                  # a record that opens no turn, on the settled pre-cut tip, stamped current
            what = self.rnd.choice(("reply", "tool_result"))
            fork = self.rnd.random() < 0.5                # the chain goes on from it, or it stays a side branch
            kinds.append("tip:%s:%s" % (what, "fork" if fork else "side"))
            self.t += self.rnd.randrange(5, 60)
            r = {"reply": self._reply, "tool_result": self._tool_result}[what](self.tip, iso(self.t))
            out.append(r)
            if fork:
                self.parent = r["uuid"]

    def slash(self):
        """A typed slash command as the CLI writes it (a raw twin "/review" with a fresh prompt id, the command wrapper with the
        same id parented on it, a reply), then one more prompt and reply: a settle after it puts the cut on the wrapper."""
        pid = self._id("pc")
        out = []
        self.t += 30
        twin = self._rec({"type": "user", "uuid": self._id("t"), "parentUuid": self.parent, "promptSource": "typed",
                          "promptId": pid, "cwd": "/w/notes-api", "message": {"role": "user", "content": "/review"}}, iso(self.t))
        out.append(twin)
        out.append(self._rec({"type": "user", "uuid": self._id("w"), "parentUuid": twin["uuid"], "promptId": pid,
                              "cwd": "/w/notes-api", "message": {"role": "user", "content": WRAP}}, iso(self.t)))
        self.t += 20
        out.append(self._rec(self._reply(self.parent, iso(self.t)), iso(self.t)))
        self.t += 40
        out.append(self._rec(self._prompt(self.parent, iso(self.t)), iso(self.t)))
        self.t += 20
        out.append(self._rec(self._reply(self.parent, iso(self.t)), iso(self.t)))
        self.written += out
        return out, ["slash-at-cut"]


class WideDifferentialRoads(DifferentialRoads):
    """The third review's fuzz gaps (2026-10-09): every step's tree is compared with a cold parse of the file as it stood at that
    step (the boot's restore, each append, each restart, the final boot), each case draws from WideGen, and per case:
      - old document (share OLD_DOC): the document is rewritten without its preGates key, as every document written before
        that key existed (the first boot after a deploy reads only those);
      - restart (RESTART per gap between appends): a restart before the next append, whose parse is then a boot's restore;
      - slash (share SLASH): the last append is a typed slash command (WideGen.slash), then a settle (a whole parse and its
        document write), a restart and two parses: those two must not BOTH be whole parses (a COST failure: the tree is right
        but the leaf pays a whole parse at every boot and stays whole); a settle also follows SETTLE of the other cases.
    The test also asserts the restore road built at least RESTORE_FLOOR of the compared trees and the fold road some, so a
    disabled restore cannot pass. ROMP_FUZZ_ONLY=<case> replays one case (each case has its own seed)."""
    test_random_appends_give_the_cold_parse = None      # the parent's test runs once, in its own class
    OLD_DOC, RESTART, SLASH, SETTLE = 0.3, 0.3, 0.15, 0.15
    RESTORE_FLOOR = 0.15
    HAZARDS = DifferentialRoads.HAZARDS + (
        "nearcut:w+1", "nearcut:w", "nearcut:gap", "nearcut:cut", "gap:after-compaction", "gap:reply", "gap:prompt",
        "gap:tool_result", "compact:stale-boundary-fresh-summary", "compact:stale-bare", "compact:gap-boundary",
        "tip:reply:fork", "tip:reply:side", "tip:tool_result:fork", "tip:tool_result:side", "slash-at-cut", "old-doc",
        "restart", "settle")

    def _restart(self):
        self._reset()
        for m in ("_ASM_CHAIN_REFUSED_PATHS", "_ASM_CKPT_REFUSED"):
            getattr(em, m).clear()                        # in-memory facts a kernel restart forgets

    def _measure(self):
        s0 = dict(em._ASM_STATS)
        tree = self.parse()
        d = {k: v - s0.get(k, 0) for k, v in em._ASM_STATS.items() if v != s0.get(k, 0)}
        em.hydrate(tree, SID)
        road = "whole" if d.get("full") else "restore" if d.get("restore") else "fold" if d.get("fold") else "serve"
        why = sorted(k[2:] for k in d if k.startswith("g:")) + sorted(k for k in d if k.startswith("restore:") and k.endswith("Refused"))
        return road + ("(%s)" % ",".join(why) if why else ""), T._strip_tree(tree), d

    def _doc_facts(self, ck):
        """(document file name, its decoded body, watermark, the cut's first tail stamp, the last pre-cut uuid)."""
        import gzip
        name = next(p.name for p in ck.iterdir() if p.name.endswith(".asm.json.gz"))
        doc = json.loads(gzip.decompress((ck / name).read_bytes()).decode("utf-8"))
        (f,) = doc["files"].values()
        wm = em._carry_decode(doc["carry"])["max_ppt"]
        with open(self.path, "rb") as fh:
            fh.seek(f["cut"][0])
            first = json.loads(fh.readline())
        cut_t = em.parse_z(first["timestamp"])
        self.assertTrue(wm and cut_t and cut_t > wm + 3, (wm, cut_t))
        self.assertIn("preGates", doc)
        return name, doc, wm, cut_t, f["last"]

    def run_wide(self, n, seed, only=None):
        import gzip
        ck = self.td / "checkpoints"
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            tree = self.parse()
            self.assertTrue(em.asm_checkpoint_write(self.path, SID, tree=tree), em.asm_checkpoint_stats())
            del tree
        base_size, base_mtime = os.path.getsize(self.path), os.stat(self.path).st_mtime
        docs = {p.name: p.read_bytes() for p in ck.iterdir()}
        dname, doc, wm, cut_t, tip = self._doc_facts(ck)
        old = dict(doc)
        del old["preGates"]                               # the writer's own encoding, less the one key
        old_bytes = gzip.compress(json.dumps(old, separators=(",", ":")).encode("utf-8"), compresslevel=6)
        precut_t = NOW - 86400 + 5 * 60
        base_ref = self._ref()
        st = {"fails": collections.Counter(), "examples": {}, "roads": collections.Counter(), "compared": 0,
              "cover": collections.Counter(), "fail_cases": set(), "by_hazard": collections.Counter(), "cases": 0,
              "settles": collections.Counter(), "case_fails": {}, "hazards": {}}
        for c in (range(n) if only is None else [only]):
            rnd = random.Random("%d:%d" % (seed, c))
            with open(self.path, "r+b") as f:
                f.truncate(base_size)
            os.utime(self.path, (base_mtime, base_mtime))
            for p in list(ck.iterdir()):
                if p.name not in docs:
                    p.unlink()
            for name, data in docs.items():
                (ck / name).write_bytes(data)
            hz = set()
            if rnd.random() < self.OLD_DOC:
                (ck / dname).write_bytes(old_bytes)
                hz.add("old-doc")
            self._restart()
            g = WideGen(rnd, self.parent, self.t, precut_t, wm, cut_t, tip)
            steps = []                                    # (label, road, tree, file size, counters)
            with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
                steps.append(("boot0",) + self._measure()[:2] + (base_size,))
                slash = rnd.random() < self.SLASH
                n_app = rnd.choice((0, 1)) if slash else rnd.choice((2, 2, 3))
                for i in range(n_app + (1 if slash else 0)):
                    label = "slash" if (slash and i == n_app) else "append%d" % (i + 1)
                    if i and rnd.random() < self.RESTART:
                        self._restart()
                        hz.add("restart")
                        label += "@restart"
                    recs, kinds = g.slash() if label.startswith("slash") else g.append()
                    hz.update(kinds)
                    with open(self.path, "a") as f:
                        f.write("".join(json.dumps(r) + "\n" for r in recs))
                    road, got, _d = self._measure()
                    steps.append((label, road, got, os.path.getsize(self.path)))
                if slash or rnd.random() < self.SETTLE:
                    hz.add("settle")
                    held = {p.name: p.read_bytes() for p in ck.iterdir()}
                    for p in list(ck.iterdir()):
                        p.unlink()                        # the settle writes from a WHOLE entry: no document, a fresh process
                    self._restart()
                    tree = self.parse()
                    wrote = em.asm_checkpoint_write(self.path, SID, tree=tree)
                    del tree
                    st["settles"]["wrote" if wrote else "skipped"] += 1
                    if not wrote:
                        for name, data in held.items():
                            (ck / name).write_bytes(data)
                    self._restart()
                    r1, t1, d1 = self._measure()
                    r2, t2, d2 = self._measure()
                    size = os.path.getsize(self.path)
                    steps.append(("settle-boot1", r1, t1, size))
                    steps.append(("settle-boot2", r2, t2, size))
                    if wrote and d1.get("full") and d2.get("full"):
                        steps.append(("settle-cost", "%s/%s%s" % (r1, r2, "/afterRefusal" if d1.get("write:afterRefusal")
                                                                   else ""), None, size))
                self._restart()
                steps.append(("boot",) + self._measure()[:2] + (os.path.getsize(self.path),))
            full = Path(self.path).read_bytes()
            refs = {base_size: base_ref}
            for size in sorted({s[3] for s in steps} - {base_size}):
                Path(self.path).write_bytes(full[:size])  # the file as it stood at that step, parsed cold
                refs[size] = self._ref()
            Path(self.path).write_bytes(full)
            st["cases"] += 1
            for h in hz:
                st["cover"][h] += 1
            bad = []
            for label, road, got, size in steps:
                if label == "settle-cost":
                    bad.append(("cost", label, road))
                    continue
                st["compared"] += 1
                st["roads"][road.split("(")[0]] += 1
                if got != refs[size]:
                    bad.append(("tree", label.split("@")[0] + ("@restart" if "@" in label else ""), road))
            st["hazards"][c] = hz
            if bad:
                st["fail_cases"].add(c)
                st["case_fails"][c] = bad
                hzt = tuple(sorted(h for h in hz if h in self.HAZARDS)) or ("none",)
                for h in hzt:
                    st["by_hazard"][h] += 1
                for kind, label, road in bad:
                    key = (kind, label, road.split("(")[0] if kind == "tree" else road, hzt)
                    st["fails"][key] += 1
                    st["examples"].setdefault(key, c)
        return st

    def report(self, st, seed):
        fails = st["fails"]
        by_step = collections.Counter()
        for (kind, label, _road, _h), v in fails.items():
            by_step[(kind, label)] += v
        lines = ["%s step=%s road=%s hazards=%s x%d (case %d)" % (k[0], k[1], k[2], "+".join(k[3]), v, st["examples"][k])
                 for k, v in fails.most_common()]
        cost = collections.Counter()
        for (kind, _label, road, _h), v in fails.items():
            if kind == "cost":
                cost[road.split("/")[0]] += v
        cover = ", ".join("%s %d" % (h, st["cover"][h]) for h in sorted(st["cover"]))
        haz = ", ".join("%s %d/%d" % (h, v, st["cover"][h]) for h, v in st["by_hazard"].most_common())
        cmp_ = st["compared"]
        sys.stderr.write(
            "wide fuzz: %d cases, seed %d, %d failing cases, %d failing comparisons\n"
            "  compared trees %d by road %s (restore share %.3f)\n  settles %s\n"
            "  by kind and step: %s\n  cost failures by the first parse's road: %s\n  failing cases per hazard (failing/with hazard): %s\n  cases per hazard: %s\n%s\n" % (
                st["cases"], seed, len(st["fail_cases"]), sum(fails.values()), cmp_, dict(st["roads"]),
                st["roads"]["restore"] / max(1, cmp_), dict(st["settles"]),
                ", ".join("%s:%s x%d" % (k[0], k[1], v) for k, v in by_step.most_common()),
                ", ".join("%s x%d" % kv for kv in cost.most_common()), haz, cover, "\n".join(lines[:60])))
        if os.environ.get("ROMP_FUZZ_VERBOSE"):           # one line per failing case: its hazards and its failing steps
            for c in sorted(st["case_fails"]):
                sys.stderr.write("case %d hazards=%s fails=%s\n" % (c, "+".join(sorted(st["hazards"][c])), json.dumps(st["case_fails"][c])))
        return lines

    def test_every_step_of_wider_appends_gives_the_cold_parse(self):
        only = os.environ.get("ROMP_FUZZ_ONLY")
        st = self.run_wide(CASES, SEED, only=int(only) if only else None)
        lines = self.report(st, SEED)
        if only is None:                                  # the generator reaches what it claims to, and the roads ran
            for h in ("nearcut:w+1", "nearcut:w", "nearcut:gap", "nearcut:cut", "compact:stale-boundary-fresh-summary",
                      "compact:stale-bare", "gap:after-compaction", "slash-at-cut", "old-doc", "restart", "settle"):
                self.assertGreater(st["cover"][h], 0, "no case drew %s" % h)
            self.assertGreaterEqual(st["roads"]["restore"] / max(1, st["compared"]), self.RESTORE_FLOOR, dict(st["roads"]))
            self.assertGreater(st["roads"]["fold"], 0, dict(st["roads"]))
            self.assertGreater(st["settles"]["wrote"], 0, dict(st["settles"]))
        self.assertEqual(sum(st["fails"].values()), 0, "\n".join(lines[:60]))

if __name__ == "__main__":
    unittest.main()
