#!/usr/bin/env python3
"""bench_record_cache_gc: how long one FULL (generation-two) collection pauses the process, against the size of the
record cache, and whether evicting an entry still frees its memory.

The kernel's record cache holds the decoded JSON of transcript files (dicts, lists and strings), tens of gigabytes on a
busy machine. A full collection of the cyclic garbage collector visits every container it tracks, so with the cache
in its view each full pause grows with the cache. This drives the REAL reader (the event model's _read_jsonl_entry)
over SYNTHETIC transcript files (invented text and placeholder uuids; never a real transcript) in a
fresh process per size, then:

  1. loads files until the cache weighs the target (recordCache.bytes, the unit the kernel's /perf reports), timing the
     load (the reader's own cost, which an untracking pass adds to);
  2. times gc.collect(2) three times (median), with nothing frozen: what an organic full collection, or a freeze
     reclaim's unfreeze-and-collect, costs; and once after gc.freeze(), for reference;
  3. evicts every entry with the collector DISABLED and reads the live pymalloc blocks and the resident size (after
     malloc_trim) before and after, so what comes back came back by reference counting alone; then one gc.collect()
     says how many unreachable objects were left for the cyclic collector (0 means none).

Run one size per process (each prints one JSON line), then --summarize fits pause against cache weight and
extrapolates to a given weight (labelled as an extrapolation):

    python scripts/bench_record_cache_gc.py --corpus DIR --size-gb 4 >> runs.jsonl
    ROMP_RECORD_CACHE_GC_UNTRACK=off python scripts/bench_record_cache_gc.py --corpus DIR --size-gb 4 >> runs.jsonl
    python scripts/bench_record_cache_gc.py --summarize runs.jsonl --at-gb 61.8

The corpus is generated on first use (deterministic seed) and reused. Peak memory is roughly the target weight plus
the interpreter."""
import argparse
import ctypes
import gc
import json
import os
import random
import statistics
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
GIB = 1024 ** 3


def _rss_kb():
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return 0


def _trim():
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def gen_corpus(d, total_bytes, file_bytes=110 * 1000 * 1000, seed=7):
    """Synthetic transcripts of the shape a coding session writes: assistant turns (text or a tool call, a usage block)
    and user turns carrying a tool result, sizes drawn from a long-tailed distribution. Invented content only."""
    os.makedirs(d, exist_ok=True)
    have = sorted(f for f in os.listdir(d) if f.endswith(".jsonl"))
    if sum(os.path.getsize(os.path.join(d, f)) for f in have) >= total_bytes:
        return [os.path.join(d, f) for f in have]
    rng = random.Random(seed)
    alphabet = "abcdefghijklmnopqrstuvwxyz      \n.,_/-0123456789"
    blob = "".join(rng.choice(alphabet) for _ in range(1 << 20))

    def text(mean):
        n = max(8, int(rng.lognormvariate(0, 1.1) * mean))
        i = rng.randrange(0, len(blob) - n) if n < len(blob) else 0
        return blob[i:i + n]

    def uid(i, k):
        return "11111111-2222-3333-%04d-%012d" % (k % 10000, i)

    paths, written, fi = [], 0, 0
    while written < total_bytes:
        p = os.path.join(d, "s%03d.jsonl" % fi)
        n = 0
        with open(p, "w") as f:
            i = 0
            while n < file_bytes:
                i += 1
                base = {"parentUuid": uid(i - 1, fi), "isSidechain": False, "userType": "external",
                        "cwd": "/w/notes-api", "sessionId": uid(0, fi), "version": "2.1.0",
                        "gitBranch": "main", "uuid": uid(i, fi), "timestamp": "2026-01-01T00:%02d:%02d.000Z" % (i % 60, i % 60)}
                r = rng.random()
                if r < 0.45:
                    tid = "toolu_%016d" % i
                    if rng.random() < 0.5:
                        content = [{"type": "text", "text": text(300)}]
                    else:
                        content = [{"type": "tool_use", "id": tid, "name": rng.choice(("Bash", "Read", "Edit", "Grep")),
                                    "input": {"command": text(120), "description": text(30)}}]
                    rec = dict(base, type="assistant", requestId="req_%016d" % i, message={
                        "id": "msg_%016d" % i, "type": "message", "role": "assistant", "model": "model-x",
                        "content": content, "stop_reason": None, "stop_sequence": None,
                        "usage": {"input_tokens": rng.randrange(1, 9), "cache_creation_input_tokens": rng.randrange(0, 5000),
                                  "cache_read_input_tokens": rng.randrange(0, 200000),
                                  "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": rng.randrange(0, 5000)},
                                  "output_tokens": rng.randrange(1, 4000), "service_tier": "standard"}})
                elif r < 0.9:
                    out = text(900)
                    rec = dict(base, type="user", message={"role": "user", "content": [
                        {"tool_use_id": "toolu_%016d" % (i - 1), "type": "tool_result", "content": out, "is_error": False}]},
                        toolUseResult={"stdout": out, "stderr": "", "interrupted": False, "isImage": False})
                else:
                    rec = dict(base, type="user", message={"role": "user", "content": text(200)})
                line = json.dumps(rec) + "\n"
                f.write(line)
                n += len(line)
        paths.append(p)
        written += n
        fi += 1
    return paths


def load_em():
    os.environ.setdefault("ROMP_RECORD_CACHE_BUDGET_MB", str(10 ** 6))   # never evict by budget during the load
    os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="kgc-bench-state-")
    os.environ.pop("ROMP_STATE_DIR", None)
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    from romp_load import load_source
    return load_source("romp_event_model_bench_gc", os.path.join(ROOT, "bin", "romp-event-model"))


def run(corpus, size_gb):
    em = load_em()
    paths = gen_corpus(corpus, int(size_gb * GIB / em.RECORD_CACHE_RESIDENT_PER_FILE_BYTE) + 200 * 1000 * 1000)
    mode = ("untrack" if getattr(em, "_record_cache_gc_untrack_enabled", lambda: False)() else "tracked")
    gc.collect()
    rss0, blocks0 = _rss_kb(), sys.getallocatedblocks()
    t0 = time.perf_counter()
    file_bytes = 0
    for p in paths:
        if em._JSONL_CACHE_BYTES[0] >= size_gb * GIB:
            break
        em._read_jsonl_entry(p)
        file_bytes += os.path.getsize(p)
    load_s = time.perf_counter() - t0
    em._LAST_ENTRY.ent = None
    weight = em._JSONL_CACHE_BYTES[0]
    records = sum(len(e[4]) for e in em._JSONL_CACHE.values())
    gc.collect()                                          # settle: whatever the load left in the young generations
    pauses = []
    for _ in range(3):
        t = time.perf_counter(); gc.collect(2); pauses.append((time.perf_counter() - t) * 1000.0)
    tracked = len(gc.get_objects())                       # every container the collector tracks, cache and interpreter
    gc.freeze()
    t = time.perf_counter(); gc.collect(2); frozen_ms = (time.perf_counter() - t) * 1000.0
    gc.unfreeze()
    gc.collect()
    rss1, blocks1 = _rss_kb(), sys.getallocatedblocks()
    # eviction, with the cyclic collector OFF: only reference counting can free anything here
    gc.disable()
    with em._JSONL_CACHE_LOCK:
        for p in list(em._JSONL_CACHE):
            em._cache_pop_locked(p)
    _trim()
    rss2, blocks2 = _rss_kb(), sys.getallocatedblocks()
    left_for_gc = gc.collect()
    gc.enable()
    return {"mode": mode, "sizeGb": size_gb, "weightGb": round(weight / GIB, 3), "fileGb": round(file_bytes / GIB, 3),
            "records": records, "trackedObjects": tracked, "loadS": round(load_s, 2),
            "fullPauseMs": [round(x, 1) for x in pauses], "fullPauseMedianMs": round(statistics.median(pauses), 1),
            "frozenPauseMs": round(frozen_ms, 2), "rssBaseGb": round(rss0 / 1024 ** 2, 3),
            "rssLoadedGb": round(rss1 / 1024 ** 2, 3), "rssEvictedGb": round(rss2 / 1024 ** 2, 3),
            "blocksBase": blocks0, "blocksLoaded": blocks1, "blocksEvicted": blocks2,
            "unreachableAfterEviction": left_for_gc, "python": sys.version.split()[0]}


def summarize(path, at_gb):
    rows = [json.loads(l) for l in open(path) if l.strip()]
    for mode in sorted({r["mode"] for r in rows}):
        rs = sorted((r for r in rows if r["mode"] == mode), key=lambda r: r["weightGb"])
        xs, ys = [r["weightGb"] for r in rs], [r["fullPauseMedianMs"] for r in rs]
        print("%s:" % mode)
        for r in rs:
            print("  weight %.2f GiB  records %d  tracked %d  load %.1f s  full pause %s ms (median %.1f)  frozen %.2f ms"
                  "  rss loaded %.2f -> evicted %.2f GiB  blocks %d -> %d (base %d)  unreachable %d"
                  % (r["weightGb"], r["records"], r["trackedObjects"], r["loadS"], r["fullPauseMs"], r["fullPauseMedianMs"],
                     r["frozenPauseMs"], r["rssLoadedGb"], r["rssEvictedGb"], r["blocksLoaded"], r["blocksEvicted"],
                     r["blocksBase"], r["unreachableAfterEviction"]))
        if len(xs) >= 2:
            mx, my = statistics.fmean(xs), statistics.fmean(ys)
            slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
            icpt = my - slope * mx
            print("  fit: %.1f ms per GiB of cache weight, intercept %.1f ms; EXTRAPOLATED full pause at %.1f GiB: %.0f ms"
                  % (slope, icpt, at_gb, icpt + slope * at_gb))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=os.path.expanduser("~/.local/state/kgc-bench/corpus"))
    ap.add_argument("--size-gb", type=float)
    ap.add_argument("--summarize")
    ap.add_argument("--at-gb", type=float, default=61.8)
    a = ap.parse_args()
    if a.summarize:
        summarize(a.summarize, a.at_gb)
    else:
        print(json.dumps(run(a.corpus, a.size_gb)), flush=True)
