#!/usr/bin/env python3
"""bench_record_cache_tail: the record cache holding a large transcript's TAIL only, against holding it whole, on a loop
shaped like the kernel's pusher, over SYNTHETIC transcripts (invented text, placeholder ids; never a real transcript).

The corpus: --files transcripts totalling --total-gb, each a chain of turns of the shape a coding session writes (a typed
prompt, one to six tool rounds whose results are long-tailed in size and carried twice as the CLI does, an occasional
background task and its completion notice, a closing reply, a compaction every couple of hundred turns). Generated once
(deterministic seed) and reused.

Three phases, each its own process so a measurement never inherits another's heap:

    python scripts/bench_record_cache_tail.py --phase gen  --corpus DIR
    python scripts/bench_record_cache_tail.py --phase prep --corpus DIR --repo CHECKOUT --ckpt CKPTDIR
    python scripts/bench_record_cache_tail.py --phase run  --corpus DIR --repo CHECKOUT --ckpt CKPTDIR --label after >> runs.jsonl
    python scripts/bench_record_cache_tail.py --phase summarize runs.jsonl --live-gib 20.2 --live-cache-gb 49.5 --live-rss-gb 74

`prep` is the kernel life before the measured one: a whole parse of every transcript, its assembly document and its fold
documents written (what a settle leaves on disk). `run` is a restart over those documents. Its cycle 0 is the boot: every
transcript parsed (restored from its document), its folds restored, and one whole read of each (the boot's whole readers:
an auto-nudge or interrupt parse that upgrades a restored tail entry). Then --cycles cycles of the pusher's shape:
  - appends to a rotating third of the transcripts (a tool round each cycle, a closing reply and a new prompt every third);
  - the folds over every transcript (the background-task views bgAll and bgRunning through the real fold, scan_bg_tasks_cached,
    and a small session-meta fold), as the jobs pass and every build run them;
  - a chat build near the end of every transcript (parse_session, the bodies of its last two turns hydrated);
  - every fifth cycle (cycles 2, 7, 12, ...) a scroll-back page at the top of one transcript (sixteen turns hydrated);
  - every tenth cycle a whole adapter over one transcript (FileAdapter with no document: the kernel's thread-message and
    anchor reads, and a parse demoted to whole), and, offset by five, a fold with no cursor over one transcript (a refold
    from record 0, as after a drop).
Every cycle also runs the three chain walks a review found reading older records off disk on every pass (2026-10-07), each
on the first transcript appended that cycle, and records per pass the records and bytes read before a window and the wall
time: `rewound` (the judges' memo road, em.rewound_uuids, retired by the append), `seeded` (the document road,
em.file_rewound with the session id, then em.chain_membership as the judges call it), and `rewindHold` (the rewind gesture's
kept chain, em.chain_membership with a pending cut and no session id). Each is warmed once per transcript at the boot, as the
judges' first pass after a restart would (counted apart, `warm`).
Every cycle also runs one ROUTINE-CALLER pass (2026-10-08, round two): one transcript, in rotation, gains a turn opened by a
compaction (a boundary and its summary: an assembly fold cannot carry it, g:boundary), and is parsed through a stand-in named
after one of the four kernel callers the deploy measured reading whole transcripts back (tasks_for, _auto_nudge_session,
_plan_session, _bg_placed_tops; each stand-in calls parse_session itself, so the reads before a window are attributed to its
name, as the kernel's are to the real caller's), then its settle writes the assembly document (asm_checkpoint_write from
the parse's tree). The stand-ins drive the event model only; the real callers are driven by
tests/test_record_cache_tail_r2.py. --doc-cap-mb sets the flat assembly document cap for every checkout alike (the devbox's
leaves are about seven times this corpus's, so 2.3 MiB here stands for its 16 MiB: the documents of about half the large
leaves exceed it, as there).
Per cycle it records the wall time, the bytes the reader pulled off disk, the record cache's weight (/perf recordCache.bytes,
the kernel's own estimate of resident bytes), the resident size and its high-water mark (VmRSS, VmHWM), the interpreter's
allocated blocks (sys.getallocatedblocks, after a collection: the small allocations the deploy's perf reads counted), the
assembly entries (whole and restored), and, where the checkout has the rule, the reads before a window, in total and by
caller. --repo picks the checkout whose event model runs: the base for
"before", this branch for "after" (ROMP_RECORD_CACHE_TAIL_MB=0 on this branch is the same as the base for the cache).

Guards: refuses to start with under 20 GiB available, and stops with exit 3 once its own resident size passes --max-rss-gb
(default 12). One process at a time."""
import argparse
import gc
import json
import os
import random
import statistics
import sys
import tempfile
import time

GIB = 1024 ** 3
MIB = 1024 ** 2
SID_FMT = "bbbbbbbb-4444-4222-8333-%012d"


def _status_kb(field):
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith(field + ":"):
                return int(line.split()[1])
    return 0


def _mem_available_gib():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / (1024 ** 2)
    return 0.0


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


class Writer:
    """One synthetic transcript, appended a turn or a tool round at a time, its uuid chain and clock carried."""

    def __init__(self, path, fi, rng, blob, t0):
        self.path, self.fi, self.rng, self.blob = path, fi, rng, blob
        self.sid = SID_FMT % fi
        self.n = 0
        self.parent = None
        self.t = t0
        self.pending_bg = []

    def _uid(self):
        self.n += 1
        return "11111111-2222-4333-%04d-%012d" % (self.fi % 10000, self.n)

    def _text(self, mean):
        n = max(8, int(self.rng.lognormvariate(0, 1.0) * mean))
        n = min(n, len(self.blob) - 1)
        i = self.rng.randrange(0, len(self.blob) - n)
        return self.blob[i:i + n]

    def _base(self, typ):
        u = self._uid()
        self.t += self.rng.uniform(0.5, 20)
        r = {"parentUuid": self.parent, "isSidechain": False, "userType": "external", "cwd": "/w/notes-api",
             "sessionId": self.sid, "version": "2.1.0", "gitBranch": "main", "type": typ, "uuid": u, "timestamp": _iso(self.t)}
        self.parent = u
        return r

    def prompt(self):
        r = self._base("user")
        r["promptSource"] = "typed"
        r["message"] = {"role": "user", "content": self._text(300)}
        return [r]

    def tool_round(self):
        out = []
        tid = "toolu_%d_%d" % (self.fi, self.n)
        bg = self.rng.random() < 0.03
        a = self._base("assistant")
        a["requestId"] = "req_%d_%d" % (self.fi, self.n)
        a["message"] = {"id": "msg_%d_%d" % (self.fi, self.n), "type": "message", "role": "assistant", "model": "model-x",
                        "content": [{"type": "tool_use", "id": tid, "name": self.rng.choice(("Bash", "Read", "Edit", "Grep")),
                                     "input": {"command": self._text(120), "description": self._text(30),
                                               **({"run_in_background": True} if bg else {})}}],
                        "stop_reason": "tool_use", "stop_sequence": None,
                        "usage": {"input_tokens": self.rng.randrange(1, 9), "cache_read_input_tokens": self.rng.randrange(0, 200000),
                                  "output_tokens": self.rng.randrange(1, 4000), "service_tier": "standard"}}
        out.append(a)
        body = self._text(2500)
        u = self._base("user")
        u["message"] = {"role": "user", "content": [{"tool_use_id": tid, "type": "tool_result", "content": body, "is_error": False}]}
        u["toolUseResult"] = {"stdout": body, "stderr": "", "interrupted": False, "isImage": False}
        out.append(u)
        if bg:
            self.pending_bg.append(tid)
        if self.pending_bg and self.rng.random() < 0.05:
            done = self.pending_bg.pop(0)
            nt = self._base("user")
            nt["message"] = {"role": "user", "content": "<task-notification>\n<task-id>b%s</task-id>\n<tool-use-id>%s</tool-use-id>\n"
                             "<status>completed</status>\n<summary>done</summary>\n</task-notification>" % (done, done)}
            out.append(nt)
        return out

    def reply(self):
        r = self._base("assistant")
        r["message"] = {"id": "msg_%d_%d" % (self.fi, self.n), "type": "message", "role": "assistant", "model": "model-x",
                        "content": [{"type": "text", "text": self._text(800)}], "stop_reason": "end_turn", "stop_sequence": None,
                        "usage": {"input_tokens": 3, "output_tokens": self.rng.randrange(1, 2000)}}
        return [r]

    def compact(self):
        b = self._base("system")
        b.update({"subtype": "compact_boundary", "parentUuid": None, "logicalParentUuid": b["parentUuid"],
                  "compactMetadata": {"trigger": "auto", "preTokens": 160000, "postTokens": 9000}})
        s = self._base("user")
        s["isCompactSummary"] = True
        s["message"] = {"role": "user", "content": "summary so far: " + self._text(2000)}
        return [b, s]

    def turn(self, k):
        recs = []
        if k and k % 220 == 0:
            recs += self.compact()
        recs += self.prompt()
        for _ in range(self.rng.randint(1, 6)):
            recs += self.tool_round()
        recs += self.reply()
        return recs

    def write(self, recs):
        with open(self.path, "a") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")


def gen(corpus, files, total_bytes, seed=11):
    os.makedirs(corpus, exist_ok=True)
    have = sorted(f for f in os.listdir(corpus) if f.endswith(".jsonl"))
    if len(have) >= files and sum(os.path.getsize(os.path.join(corpus, f)) for f in have[:files]) >= 0.95 * total_bytes:
        return [os.path.join(corpus, f) for f in have[:files]]
    rng = random.Random(seed)
    alphabet = "abcdefghijklmnopqrstuvwxyz      \n.,_/-0123456789"
    blob = "".join(rng.choice(alphabet) for _ in range(1 << 21))
    per = total_bytes // files
    paths = []
    for fi in range(files):
        sid = SID_FMT % fi
        p = os.path.join(corpus, sid + ".jsonl")
        if os.path.exists(p):
            os.unlink(p)
        w = Writer(p, fi, random.Random(seed * 1000 + fi), blob, 1_780_000_000 + fi * 7)
        k = 0
        size = 0
        # sizes vary like the live set's (0.7 to 1.9 GiB there): from 0.5 to 1.5 times the mean
        target = int(per * (0.5 + fi / max(1, files - 1)))
        with open(p, "w") as f:
            while size < target:
                for r in w.turn(k):
                    line = json.dumps(r) + "\n"
                    f.write(line)
                    size += len(line)
                k += 1
        paths.append(p)
        sys.stderr.write("gen %s %.1f MB %d turns\n" % (os.path.basename(p), size / 1e6, k))
    return paths


def load(repo, corpus):
    os.environ.setdefault("XDG_STATE_HOME", tempfile.mkdtemp(prefix="bench-tail-state-"))
    os.environ.pop("ROMP_STATE_DIR", None)
    os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
    os.environ.setdefault("ROMP_RECORD_CACHE_TAIL_ROOTS", os.path.realpath(corpus))   # a checkout without the rule ignores it
    sys.path.insert(0, os.path.join(repo, "tests"))
    from romp_load import load_source
    return load_source("romp_event_model", os.path.join(repo, "bin", "romp-event-model"))


def leaves(corpus, files):
    return sorted(os.path.join(os.path.realpath(corpus), f) for f in os.listdir(corpus) if f.endswith(".jsonl"))[:files]


def parse(em, path):
    sid = os.path.basename(path)[:-6]
    return em.parse_session(path, rompuuid=sid, name="bench", dir="/w/notes-api", candidate_files=[path], states=None,
                            postal_log=[], now=time.time())


def _args(path):
    return dict(rompuuid=os.path.basename(path)[:-6], name="bench", dir="/w/notes-api", candidate_files=[path], states=None,
                postal_log=[], now=time.time())


# The stand-ins for the four kernel callers (see the module docstring). Each calls parse_session from its OWN frame: the
# reader attributes a read before a window to the first frame outside the event model, so a shared helper would take the name.
def tasks_for(em, path):
    return em.parse_session(path, **_args(path))


def _auto_nudge_session(em, path):
    return em.parse_session(path, **_args(path))


def _plan_session(em, path):
    return em.parse_session(path, **_args(path))


def _bg_placed_tops(em, path):
    return em.parse_session(path, **_args(path))


CALLERS = (tasks_for, _auto_nudge_session, _plan_session, _bg_placed_tops)
META = {}


def meta_fold(em, path, cache):
    def step(st, r):
        st["n"] += 1
        if r.get("cwd"):
            st["cwd"] = r["cwd"]
        if r.get("type") == "assistant":
            m = (r.get("message") or {}).get("model")
            if m:
                st["model"] = m
        return st
    return em.fold_records(cache, path, lambda: {"n": 0, "cwd": None, "model": None}, step, ckpt="benchMeta")


def folds(em, path, caches):
    em.scan_bg_tasks_cached(path, caches["bgAll"], want_all=True, ckpt="bgAll")
    em.scan_bg_tasks_cached(path, caches["bgRunning"], want_all=False, ckpt="bgRunning")
    meta_fold(em, path, caches["meta"])


def prep(em, paths, ckpt):
    os.makedirs(ckpt, exist_ok=True)
    em.set_checkpoint_dir(lambda: ckpt)
    caches = {"bgAll": {}, "bgRunning": {}, "meta": {}}
    for p in paths:
        sid = os.path.basename(p)[:-6]
        tree = parse(em, p)
        folds(em, p, caches)
        ok = em.asm_checkpoint_write(p, sid, tree=tree)
        em.checkpoint_write(p)
        sys.stderr.write("prep %s doc=%s rss=%.2f GiB\n" % (os.path.basename(p), ok, _status_kb("VmRSS") / (1024 ** 2)))
        em.evict_document(p)


def run(em, paths, ckpt, cycles, label, max_rss_gb, seed=5):
    """The measured restart (see the module docstring). The corpus is put back as it was at the end (each file truncated to
    its starting size and given its starting mtime), so every run starts from the same bytes and the prep's documents
    still verify."""
    orig = {p: (os.path.getsize(p), os.stat(p).st_mtime) for p in paths}
    try:
        _run(em, paths, ckpt, cycles, label, max_rss_gb, seed)
    finally:
        for p, (size, mtime) in orig.items():
            with open(p, "r+b") as f:
                f.truncate(size)
            os.utime(p, (mtime, mtime))


def _run(em, paths, ckpt, cycles, label, max_rss_gb, seed):
    em.set_checkpoint_dir(lambda: ckpt)
    caches = {"bgAll": {}, "bgRunning": {}, "meta": {}}
    for name, c in caches.items():
        em.name_fold_cache(c, {"meta": "benchMeta"}.get(name, name))
    rng = random.Random(seed)
    alphabet = "abcdefghijklmnopqrstuvwxyz      \n.,_/-0123456789"
    blob = "".join(rng.choice(alphabet) for _ in range(1 << 20))
    writers = {}
    for fi, p in enumerate(paths):                      # carry each file's chain on from its last record (a bounded tail read)
        with open(p, "rb") as f:
            f.seek(max(0, os.path.getsize(p) - 4 * MIB))
            last = json.loads(f.read().splitlines()[-1])
        w = Writer(p, fi, random.Random(seed * 100 + fi), blob, (em.parse_z(last["timestamp"]) or time.time()) + 60)
        w.parent, w.n = last["uuid"], 10 ** 7
        writers[p] = w
    has_cold = hasattr(em, "cold_read_stats")
    rows = []

    def guard():
        rss = _status_kb("VmRSS") / (1024 ** 2)
        if rss > max_rss_gb:
            sys.stderr.write("stopping: resident %.2f GiB over the %.1f GiB guard\n" % (rss, max_rss_gb))
            print(json.dumps({"label": label, "aborted": "rss", "rssGiB": rss, "rows": rows}))
            raise SystemExit(3)

    def sample(c, t0, r0, c0, what):
        wall = round(time.monotonic() - t0, 3)
        gc.collect()                                    # after the wall: the blocks a collection would free are not held
        st = em.record_cache_stats()
        cold = em.cold_read_stats() if has_cold else {"passes": 0, "records": 0, "bytes": 0}
        by0, by1 = c0.get("byCaller") or {}, cold.get("byCaller") or {}
        with em._ASM_LOCK:
            asm = list(em._ASM_CACHE.values())
        row = {"cycle": c, "what": what, "wallS": wall, "readBytes": em.read_bytes_total() - r0,
               "cacheBytes": st["bytes"], "entries": st["entries"], "tailOnly": (st.get("tailOnly") or {}).get("entries", 0),
               "rssKiB": _status_kb("VmRSS"), "hwmKiB": _status_kb("VmHWM"), "blocks": sys.getallocatedblocks(),
               "asmWhole": sum(1 for e in asm if not e.get("prefix") and not e.get("preTurns")),
               "asmRestored": sum(1 for e in asm if e.get("prefix") or e.get("preTurns")),
               "coldPasses": cold["passes"] - c0["passes"], "coldBytes": cold["bytes"] - c0["bytes"],
               "coldRecords": cold["records"] - c0["records"],
               "coldByCaller": {k: v["records"] - (by0.get(k) or {}).get("records", 0) for k, v in by1.items()
                                if v["records"] != (by0.get(k) or {}).get("records", 0)}}
        rows.append(row)
        sys.stderr.write("%s c%-3d %-30s %6.2fs read %7.1f MB cache %6.2f GB rss %5.2f GiB blocks %5.1f M asm %d/%d cold %d recs %.1f MB\n" % (
            label, c, what, row["wallS"], row["readBytes"] / 1e6, row["cacheBytes"] / 1e9, row["rssKiB"] / (1024 ** 2),
            row["blocks"] / 1e6, row["asmWhole"], row["asmRestored"], row["coldRecords"], row["coldBytes"] / 1e6))
        return row

    def cold0():
        return em.cold_read_stats() if has_cold else {"passes": 0, "records": 0, "bytes": 0, "byCaller": {}}

    # cycle 0: the boot over the documents
    t0, r0, c0 = time.monotonic(), em.read_bytes_total(), cold0()
    for p in paths:
        tree = parse(em, p)
        em.hydrate([a for t in tree["turns"][-2:] for a in t["atoms"]])
        folds(em, p, caches)
        em._read_jsonl_incremental(p)                   # the boot's whole readers (an interrupt or auto-nudge parse's upgrade)
        guard()
    sample(0, t0, r0, c0, "boot")
    scen_rows = []

    def scenario(c, name, p, fn):
        s0, t = cold0(), time.monotonic()
        err = None
        try:
            fn()
        except Exception as e:                          # noqa: BLE001  a walk that fails is a row, never a stopped bench
            err = repr(e)[:160]
        s1 = cold0()
        ent = em._JSONL_CACHE.get(p)
        scen_rows.append({"cycle": c, "scenario": name, "file": os.path.basename(p), "wallS": round(time.monotonic() - t, 4),
                          "coldRecords": s1["records"] - s0["records"], "coldBytes": s1["bytes"] - s0["bytes"],
                          "ncold": getattr(ent[4], "ncold", 0) if ent is not None else None, "error": err})

    cuts = {}

    def walks(c, p):
        sid = os.path.basename(p)[:-6]
        scenario(c, "rewound", p, lambda: em.rewound_uuids(p, drop=False))
        scenario(c, "seeded", p, lambda: (em.file_rewound(p, rompuuid=sid, sdk_human=False),
                                          em.chain_membership(p, candidate_files=[p], rompuuid=sid, sdk_human=False)))
        scenario(c, "rewindHold", p, lambda: em.chain_membership(p, candidate_files=[p], leaf_override=cuts.get(p)))

    for p in paths:                                     # the judges' first pass after the restart: one walk of each per file
        cuts[p] = writers[p].parent
        walks(0, p)
    for r in scen_rows:
        r["scenario"] += ":warm"
    routine_rows = []
    n = len(paths)
    for c in range(1, cycles + 1):
        t0, r0, c0 = time.monotonic(), em.read_bytes_total(), cold0()
        what = []
        first = None
        for i, p in enumerate(paths):
            if (i + c) % 3 == 0:
                first = first or p
                w = writers[p]
                cuts[p] = w.parent                      # a pending bare rollback at the record before this cycle's appends
                recs = w.tool_round()
                if c % 3 == 0:
                    recs += w.reply() + w.prompt()
                w.write(recs)
        for p in paths:
            folds(em, p, caches)
            tree = parse(em, p)
            em.hydrate([a for t in tree["turns"][-2:] for a in t["atoms"]])
        guard()
        if first is not None:
            walks(c, first)                             # the judges' walks over a leaf that grew this cycle
        rp = paths[c % n]                               # the routine-caller pass (see the module docstring)
        w = writers[rp]
        w.write(w.compact() + w.prompt() + w.tool_round() + w.reply())
        caller = CALLERS[c % len(CALLERS)]
        s0, t1 = cold0(), time.monotonic()
        rerr, wrote = None, None
        try:
            tree = caller(em, rp)
            s1 = cold0()
            wrote = bool(em.asm_checkpoint_write(rp, os.path.basename(rp)[:-6], tree=tree))   # the settle after the turn
            del tree
        except Exception as e:                          # noqa: BLE001  a pass that fails is a row, never a stopped bench
            rerr, s1 = repr(e)[:160], cold0()
        routine_rows.append({"cycle": c, "caller": caller.__name__, "file": os.path.basename(rp),
                             "wallS": round(time.monotonic() - t1, 4), "coldRecords": s1["records"] - s0["records"],
                             "coldBytes": s1["bytes"] - s0["bytes"], "docWritten": wrote, "error": rerr})
        if c % 5 == 2:
            p = paths[(c // 5) % n]
            tree = parse(em, p)
            em.hydrate([a for t in tree["turns"][:16] for a in t["atoms"]])
            what.append("scroll-back")
        if c % 10 == 0:
            p = paths[(c // 10) % n]
            ad = em.FileAdapter([p], p)
            del ad
            what.append("whole adapter")
            guard()
        if c % 10 == 5:
            p = paths[(c // 10 + 3) % n]
            st = em.fold_records({}, p, int, lambda s, r: s + 1)
            what.append("refold from 0")
        sample(c, t0, r0, c0, "+".join(what) or "steady")
    disk = sum(os.path.getsize(p) for p in paths)
    out = {"label": label, "files": n, "diskBytes": disk, "cycles": cycles, "rows": rows, "walks": scen_rows, "routine": routine_rows,
           "docCap": getattr(em, "_ASM_CKPT_CAP", None), "asmStats": dict(em._ASM_STATS),
           "asmSkipped": dict((em.asm_checkpoint_stats() or {}).get("skipped") or {}),
           "recordCount": sum(len(em._JSONL_CACHE[p][4]) for p in paths if p in em._JSONL_CACHE),
           "skeletonBytes": (em.record_cache_stats().get("tailOnly") or {}).get("skeletonBytes"),
           "skeletons": (em.record_cache_stats().get("tailOnly") or {}).get("skeletons"),
           "tailBytes": getattr(em, "_TAIL_BYTES", None), "tailRecords": getattr(em, "_TAIL_RECORDS", None)}
    print(json.dumps(out))


def summarize(files, live_gib, live_cache_gb, live_rss_gb):
    runs = {}
    for fn in files:
        with open(fn) as f:
            for line in f:
                line = line.strip()
                if line.startswith("{"):
                    d = json.loads(line)
                    runs[d["label"]] = d
    def agg(d):
        rows = d["rows"]
        boot, steady = rows[0], rows[1:]
        plain = [r for r in steady if r["what"] == "steady"]
        return {
            "disk GiB": d["diskBytes"] / GIB,
            "boot s": boot["wallS"], "boot read GB": boot["readBytes"] / 1e9,
            "steady cycle s (median)": statistics.median(r["wallS"] for r in plain) if plain else float("nan"),
            "steady cycle s (max)": max(r["wallS"] for r in plain) if plain else float("nan"),
            "steady read MB/cycle (median)": statistics.median(r["readBytes"] for r in plain) / 1e6 if plain else float("nan"),
            "scroll-back cycle s": statistics.median([r["wallS"] for r in steady if "scroll-back" in r["what"]] or [float("nan")]),
            "whole-adapter cycle s": statistics.median([r["wallS"] for r in steady if "whole adapter" in r["what"]] or [float("nan")]),
            "refold-from-0 cycle s": statistics.median([r["wallS"] for r in steady if "refold" in r["what"]] or [float("nan")]),
            "refold-from-0 read MB": statistics.median([r["readBytes"] / 1e6 for r in steady if "refold" in r["what"]] or [float("nan")]),
            "cache GB (end)": rows[-1]["cacheBytes"] / 1e9,
            "RSS GiB (end)": rows[-1]["rssKiB"] / (1024 ** 2),
            "peak RSS GiB": max(r["hwmKiB"] for r in rows) / (1024 ** 2),
            "tail-only entries (end)": rows[-1]["tailOnly"],
            "allocated blocks (end, M)": (rows[-1]["blocks"] / 1e6) if "blocks" in rows[-1] else float("nan"),
            "allocated blocks (max over cycles, M)": max((r.get("blocks") or 0) for r in rows) / 1e6 if "blocks" in rows[-1] else float("nan"),
            "assembly entries whole / restored (end)": "%s / %s" % (rows[-1].get("asmWhole", "n/a"), rows[-1].get("asmRestored", "n/a")),
            "records read before a window, all cycles": sum(r.get("coldRecords", 0) for r in steady),
            "records": d.get("recordCount"),
        }
    def walk_agg(d, out):
        ws = d.get("walks") or []
        for name in ("rewound", "seeded", "rewindHold"):
            for kind, sel in (("per pass", name), ("warm, once per file", name + ":warm")):
                rs = [r for r in ws if r["scenario"] == sel]
                if not rs:
                    out["%s %s: records read before window (median/max)" % (name, kind)] = "n/a"
                    continue
                out["%s %s: records read before window (median/max)" % (name, kind)] = "%d / %d (n=%d)" % (
                    statistics.median(r["coldRecords"] for r in rs), max(r["coldRecords"] for r in rs), len(rs))
                out["%s %s: MB read before window (median/max)" % (name, kind)] = "%.1f / %.1f" % (
                    statistics.median(r["coldBytes"] for r in rs) / 1e6, max(r["coldBytes"] for r in rs) / 1e6)
                out["%s %s: wall s (median/max)" % (name, kind)] = "%.3f / %.3f" % (
                    statistics.median(r["wallS"] for r in rs), max(r["wallS"] for r in rs))
                errs = sum(1 for r in rs if r.get("error"))
                if errs:
                    out["%s %s: errors" % (name, kind)] = str(errs)
        return out
    def routine_agg(d, out):
        rs = d.get("routine") or []
        if not rs:
            return out
        out["routine pass: records before window per pass (median/max)"] = "%d / %d (n=%d)" % (
            statistics.median(r["coldRecords"] for r in rs), max(r["coldRecords"] for r in rs), len(rs))
        out["routine pass: MB before window, all passes"] = "%.1f" % (sum(r["coldBytes"] for r in rs) / 1e6)
        for name in ("tasks_for", "_auto_nudge_session", "_plan_session", "_bg_placed_tops"):
            sel = [r for r in rs if r["caller"] == name]
            if sel:
                out["routine pass %s: records before window (sum over %d passes)" % (name, len(sel))] = str(sum(r["coldRecords"] for r in sel))
        out["routine pass: wall s (median/max)"] = "%.3f / %.3f" % (statistics.median(r["wallS"] for r in rs), max(r["wallS"] for r in rs))
        out["routine pass: settles that wrote a document"] = "%d of %d" % (sum(1 for r in rs if r.get("docWritten")), len(rs))
        errs = sum(1 for r in rs if r.get("error"))
        if errs:
            out["routine pass: errors"] = str(errs)
        out["assembly documents skipped (run)"] = json.dumps(d.get("asmSkipped") or {}, sort_keys=True)
        return out
    order = ("before", "after", "base", "r1", "r2")
    labels = [l for l in order if l in runs] + [l for l in runs if l not in order]
    table = {l: routine_agg(runs[l], walk_agg(runs[l], agg(runs[l]))) for l in labels}
    keys = []
    for l in labels:
        keys += [k for k in table[l] if k not in keys]
    print("| measure | " + " | ".join(labels) + " |")
    print("|---|" + "---|" * len(labels))
    for k in keys:
        cells = []
        for l in labels:
            v = table[l].get(k, "n/a")
            cells.append(("%.3f" % v) if isinstance(v, float) else str(v))
        print("| %s | %s |" % (k, " | ".join(cells)))
    if "base" in runs and "r2" in runs:
        extrapolate_r2(runs, table, labels)
    if "after" in runs and "before" in runs:
        b, a = table["before"], table["after"]
        disk_gib = b["disk GiB"]
        recs = a["records"] or 0
        per_rec = (runs["after"]["diskBytes"] / recs) if recs else 5000
        tb = (runs["after"].get("tailBytes") or 32 * MIB)
        print()
        print("EXTRAPOLATION (not a measurement) to %.1f GiB of live transcripts in 23 files:" % live_gib)
        before_w = live_gib * GIB * 3.0
        live_records = live_gib * GIB / per_rec
        ra = runs["after"]                                 # the walk skeleton of each older record: the run's own measure (each
        skel_b = (ra["skeletonBytes"] / ra["skeletons"] + 8) if ra.get("skeletons") else 1000   #  skeleton by what it holds)
        after_w = 23 * tb * 3.0 + live_records * (20 + skel_b)   # offsets (16 bytes), a CRC (4) and a skeleton a record
        print("  cache weight whole (3 bytes a file byte): %.1f GB (tonight's measured cache: %.1f GB in 59 entries)" % (before_w / 1e9, live_cache_gb))
        print("  cache weight tail-only: 23 windows of %d MiB x 3 + %.1f M records x %d B of index and skeleton = %.2f GB" % (
            tb // MIB, live_records / 1e6, 20 + skel_b, after_w / 1e9))
        print("  bench ratio after/before (cache, end of run): %.4f -> %.2f GB of tonight's %.1f GB" % (
            a["cache GB (end)"] / b["cache GB (end)"], live_cache_gb * a["cache GB (end)"] / b["cache GB (end)"], live_cache_gb))
        print("  resident: tonight %.1f GB with a %.1f GB cache; the same heap with a tail-only cache: about %.1f GB "
              "(the other %.1f GB of heap unchanged, and growing separately)" % (
                  live_rss_gb, live_cache_gb, live_rss_gb - live_cache_gb + after_w / 1e9, live_rss_gb - live_cache_gb))
        print("  bench resident at the end: before %.2f GiB, after %.2f GiB over %.2f GiB on disk (peak %.2f vs %.2f GiB)" % (
            b["RSS GiB (end)"], a["RSS GiB (end)"], disk_gib, b["peak RSS GiB"], a["peak RSS GiB"]))


LIVE_GIB = 20.0              # the devbox's live transcripts (about 20 GiB, 2026-10-07)
LIVE_ENTRIES = 116           # cached record entries at the peak
LIVE_BASE_BLOCKS = 352e6     # allocated blocks of the whole-cache build at half an hour (perf_same_age, 2026-10-07)
LIVE_R1_BLOCKS = 355e6       # the round-one build at the same age (the deploy's read)
LIVE_BASE_CACHE_GB = 26.9    # its record cache count then
LIVE_BASE_RSS_GIB = 90.0     # resident before a restart, the whole-cache build


def extrapolate_r2(runs, table, labels):
    """Scale each run's end state per GiB on disk to the live set (an EXTRAPOLATION, not a measurement): the bench's heap is
    transcripts plus an interpreter, the live heap is transcripts plus everything else the kernel holds, so the transcript part is
    scaled by bytes on disk and the rest is the live base's remainder, the same for every build."""
    scale = LIVE_GIB * GIB / runs["base"]["diskBytes"]
    b = runs["base"]["rows"][-1]
    print()
    print("EXTRAPOLATION (not a measurement) to %.0f GiB of live transcripts (%d cached entries at the peak), by bytes on disk: x%.1f" % (
        LIVE_GIB, LIVE_ENTRIES, scale))
    base_blocks = b["blocks"] * scale
    other = max(0.0, LIVE_BASE_BLOCKS - base_blocks)
    print("  base's transcript blocks scaled: %.0f M; the live base build held %.0f M at half an hour, so %.0f M are the rest of the "
          "kernel (taken as fixed)" % (base_blocks / 1e6, LIVE_BASE_BLOCKS / 1e6, other / 1e6))
    for l in labels:
        r = runs[l]["rows"][-1]
        if "blocks" not in r:
            continue
        tb = r["blocks"] * scale
        cache = r["cacheBytes"] * scale / 1e9
        rss = (r["rssKiB"] / (1024 ** 2)) * scale
        print("  %-6s blocks %6.0f M (transcripts %5.0f M + rest %4.0f M); record cache count %5.1f GB; transcript-side resident "
              "%5.1f GiB" % (l, (tb + other) / 1e6, tb / 1e6, other / 1e6, cache, rss))
    print("  measured live for comparison: base %.0f M blocks, round one %.0f M (half an hour); base cache count %.1f GB; resident "
          "up to %.0f GiB before a restart" % (LIVE_BASE_BLOCKS / 1e6, LIVE_R1_BLOCKS / 1e6, LIVE_BASE_CACHE_GB, LIVE_BASE_RSS_GIB))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--phase", required=True, choices=("gen", "prep", "run", "summarize"))
    ap.add_argument("--corpus", default=os.path.join(tempfile.gettempdir(), "bench-record-cache-tail"))
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
    ap.add_argument("--ckpt")
    ap.add_argument("--files", type=int, default=12)
    ap.add_argument("--total-gb", type=float, default=2.4)
    ap.add_argument("--cycles", type=int, default=30)
    ap.add_argument("--label", default="run")
    ap.add_argument("--max-rss-gb", type=float, default=12.0)
    ap.add_argument("--live-gib", type=float, default=20.2)
    ap.add_argument("--live-cache-gb", type=float, default=49.5)
    ap.add_argument("--live-rss-gb", type=float, default=74.0)
    ap.add_argument("--doc-cap-mb", type=float, default=0, help="the flat assembly document cap, MiB (0: the checkout's own)")
    ap.add_argument("inputs", nargs="*")
    a = ap.parse_args()
    if a.phase == "summarize":
        return summarize(a.inputs, a.live_gib, a.live_cache_gb, a.live_rss_gb)
    if a.phase == "gen":
        gen(a.corpus, a.files, int(a.total_gb * 1e9))
        return
    if _mem_available_gib() < 20:
        sys.exit("refusing: under 20 GiB available (%.1f)" % _mem_available_gib())
    em = load(a.repo, a.corpus)
    if a.doc_cap_mb:
        em._ASM_CKPT_CAP = int(a.doc_cap_mb * MIB)       # every checkout alike (see the module docstring)
    paths = leaves(a.corpus, a.files)
    if a.phase == "prep":
        prep(em, paths, a.ckpt)
    else:
        run(em, paths, a.ckpt, a.cycles, a.label, a.max_rss_gb)


if __name__ == "__main__":
    main()
