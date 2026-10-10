#!/usr/bin/env python3
"""bench_gc_full_rate: how often FULL (generation-two) collections run, how long each pauses, and what share of wall time
they take, on a process shaped like the kernel, under today's collector settings and under two levers:

  (a) a higher full-collection threshold (gc.set_threshold's third value), so full collections run less often;
  (b) the gc-freeze controller's FULL-COLLECTION freeze (kernel/gc_freeze.py, ROMP_GC_FREEZE_FULL_MS): an organic full
      collection whose pause reached the threshold freezes its survivors from the collector's own "stop" callback, so
      later full collections walk only what was promoted since. This drives the REAL controller, loaded from this tree.

The workload (SYNTHETIC only: invented text, no real transcript):
  - a start-up heap, then the controller's initial freeze at the first idle tick (the live controller reads freezes 1);
  - a small UNTRACKED cache (the record cache's decoded records are untracked on the live kernel, so they never enter
    the walk; its size does not move a pause and it is kept small here to spend the memory on the tracked heap);
  - a TRACKED long-lived heap of decoded-JSON trees wrapped in small objects, sized by resident memory (1, 2, 4 GB,
    standing in for the live kernel's ~16 GB outside the cache), plus slow long-lived growth;
  - a steady churn paced to the live kernel's generation-0 rate (~70 collections a second): each request decodes a JSON
    message and builds a dict from it (short-lived), keeps both for a few seconds (medium-lived: promoted into the old
    generation, then freed, which is what drives CPython 3.12's "pending > a quarter of the long-lived total" rule), and
    one request in 25 (--cycle-every) leaves a reference cycle behind when it is dropped (cyclic garbage only the collector
    frees); each cycle counts its own free, so a row reports the peak of cycles that are DEAD but not yet collected, in
    cycles and in MiB (bytes per cycle measured once with tracemalloc): with the freeze, this includes the cycles a freeze
    pinned until a backstop (run --cycle-every 5 --cycle-life-s 5 for a row that stresses it);
  - an idle tick every second calls the controller's tick, so its backstop reclaims run and are timed like the kernel's.
Every collection is timed by a gc.callbacks hook; only the steady phase after the heap is built is measured.

One (size, setting) per process. The parent runs them one at a time (one CPU-heavy job), checks free memory first,
prints a table and fits each setting's pause and rate against heap size to extrapolate to --at-gb (LABELLED an
extrapolation, never a measurement):

    python3 scripts/bench_gc_full_rate.py --sizes 1,2,4 --seconds 150 --out /tmp/gcbench.jsonl
    python3 scripts/bench_gc_full_rate.py --summarize /tmp/gcbench.jsonl --at-gb 16
"""
import argparse
import collections
import importlib.util
import json
import os
import random
import string
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
MIB = 1024 * 1024

# name -> (third threshold or None for today's, full-freeze ms or None for off)
SETTINGS = collections.OrderedDict([
    ("today", (None, None)),
    ("a-t2=1000", (1000, None)),
    ("a-t2=5000", (5000, None)),
    ("b-full=250ms", (None, 250.0)),
    ("ab-full=250ms+t2=100", (100, 250.0)),
    ("ab-full=500ms+t2=100", (100, 500.0)),
    ("ab-full=500ms+t2=1000", (1000, 500.0)),
    ("ab-full=250ms+t2=1000", (1000, 250.0)),
])


def _status_kb(key):
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith(key + ":"):
                return int(line.split()[1])
    return 0


def _mem_available_mib():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    return 0


def _words(rng, n):
    return " ".join("".join(rng.choice(string.ascii_lowercase) for _ in range(rng.randint(2, 9))) for _ in range(n))


def _message(rng, i):
    """A synthetic transcript line's shape: an assistant turn with text and tool-use blocks, a usage block, ids."""
    blocks = []
    for b in range(rng.randint(2, 6)):
        if rng.random() < 0.5:
            blocks.append({"type": "text", "text": _words(rng, rng.randint(5, 40))})
        else:
            blocks.append({"type": "tool_use", "id": "toolu_%06d_%d" % (i, b), "name": rng.choice(["Read", "Bash", "Edit"]),
                           "input": {"path": "/tmp/x/%d.py" % rng.randint(0, 999),
                                     "args": [_words(rng, 2) for _ in range(rng.randint(1, 4))],
                                     "opts": {"limit": rng.randint(1, 400), "flags": [rng.randint(0, 9) for _ in range(3)]}}})
    return {"type": "assistant", "uuid": "11111111-2222-3333-4444-%012d" % i, "parentUuid": None,
            "message": {"role": "assistant", "content": blocks,
                        "usage": {"input_tokens": rng.randint(1, 9999), "output_tokens": rng.randint(1, 999),
                                  "cache": {"read": rng.randint(0, 9999), "write": rng.randint(0, 999)}}},
            "meta": {"cwd": "/tmp/x", "tags": [_words(rng, 1) for _ in range(3)]}}


class Rec:
    """A long-lived owner, as the kernel's Session/Turn objects own decoded trees (an instance with a __dict__)."""
    def __init__(self, i, tree):
        self.i = i
        self.tree = tree
        self.children = []


FREED = [0]


class CycNode:
    """One request's leftover reference cycle (it points at itself and holds the request's decoded message and result). Its
    finalizer counts frees, so created - freed - still-in-window is the cycles that are DEAD but not yet collected: the ones
    waiting for the next full collection, and, with the freeze, the ones a freeze pinned until a backstop."""
    __slots__ = ("me", "payload", "__weakref__")

    def __del__(self):
        FREED[0] += 1


def _untrack_all(obj, untrack):
    stack = [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            stack.extend(o.values())
            untrack(o)
        elif isinstance(o, list):
            stack.extend(o)
            untrack(o)


def child(size_gb, setting, seconds, seed, gen0_rate, life_s, cycle_every=25, cycle_life_s=None, cleanup_max_s="default",
          grow_pct_per_min=1.0):
    import ctypes
    import gc
    t2, full_ms = SETTINGS[setting]
    spec = importlib.util.spec_from_file_location("romp_gc_freeze_bench", os.path.join(ROOT, "kernel", "gc_freeze.py"))
    gcf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gcf)
    rng = random.Random(seed)
    texts = [json.dumps(_message(rng, i)) for i in range(256)]
    cycle_life_s = life_s if cycle_life_s is None else cycle_life_s
    # bytes per leftover cycle, measured once: the node, the request's decoded message and its result dict
    import tracemalloc
    tracemalloc.start()
    m0 = tracemalloc.get_traced_memory()[0]
    sample = []
    for k in range(2000):
        dd = json.loads(texts[k % 256])
        cn = CycNode(); cn.me = cn
        cn.payload = ({"id": k, "blocks": [{"k": b.get("type"), "n": len(b)} for b in dd["message"]["content"]],
                       "tmp": [x for x in dd["meta"]["tags"]]}, dd)
        sample.append(cn)
    bytes_per_cycle = (tracemalloc.get_traced_memory()[0] - m0) / 2000.0
    tracemalloc.stop()
    for cn in sample:
        cn.me = None; cn.payload = None
    del sample, cn, dd
    FREED[0] = 0

    # the timing hook: every collection, by generation
    tally = {0: [0, 0.0, 0.0], 1: [0, 0.0, 0.0], 2: [0, 0.0, 0.0]}
    pauses2 = []
    t0box = [None]
    measuring = [False]
    ctl_box = [None]
    ctl_tally = [0, 0.0, 0.0]                    # the controller's own full collections in the window (a backstop or a fold-in)
    last2 = [0]

    def hook(phase, info):
        if phase == "start":
            t0box[0] = time.perf_counter()
            return
        if info["generation"] == 2:
            last2[0] = int(info.get("collected", 0))
        if t0box[0] is None or not measuring[0]:
            return
        dt = (time.perf_counter() - t0box[0]) * 1000.0
        if info["generation"] == 2 and ctl_box[0] is not None and ctl_box[0]._in_run:
            ctl_tally[0] += 1; ctl_tally[1] += dt; ctl_tally[2] = max(ctl_tally[2], dt)
            return                               # reported apart: the organic columns are the collector's own
        t = tally[info["generation"]]
        t[0] += 1; t[1] += dt; t[2] = max(t[2], dt)
        if info["generation"] == 2:
            pauses2.append(dt)
    gc.callbacks.append(hook)                    # first, so its stop timing excludes the controller's freeze (a pointer move)

    cap_kw = {} if cleanup_max_s == "default" else {"full_cleanup_max_s": cleanup_max_s}
    ctl = gcf.GcFreeze(enabled=True, load_trees=8, gc=gc, full_freeze_ms=full_ms, full_t2=t2, **cap_kw)
    ctl_box[0] = ctl
    if not ctl.install_full_freeze() and t2 is not None:   # lever (a) alone: the threshold without the freeze
        a, b, _ = gc.get_threshold()
        gc.set_threshold(a, b, t2)

    # boot: a start-up heap, then the controller's initial freeze at the first idle tick (inserts > 0)
    boot = [json.loads(texts[i % 256]) for i in range(20000)]
    ctl.tick(1)

    # the untracked cache (fixed, small) and the tracked long-lived heap (sized by resident memory)
    untrack = ctypes.pythonapi.PyObject_GC_UnTrack
    untrack.argtypes = (ctypes.py_object,)
    cache = []
    rss0 = _status_kb("VmRSS")
    while _status_kb("VmRSS") - rss0 < 256 * 1024:
        for i in range(2000):
            tr = json.loads(texts[i % 256])
            _untrack_all(tr, untrack)
            cache.append(tr)
    rss1 = _status_kb("VmRSS")
    base = []
    target_kb = int(size_gb * 1024 * 1024)
    n = 0
    while _status_kb("VmRSS") - rss1 < target_kb:
        for _ in range(5000):
            r = Rec(n, json.loads(texts[n % 256]))
            if base and n % 7 == 0:
                base[-1].children.append(r.tree["meta"])    # cross-links, as session trees share structure
            base.append(r)
            n += 1
    heap_kb = _status_kb("VmRSS") - rss1
    tracked_objects = len(gc.get_objects())       # once, after the build: every tracked object outside the frozen set
    frozen_objects = gc.get_freeze_count()

    # the steady phase. The request rate follows a feedback loop on the measured generation-0 rate (the live kernel's
    # ~70 a second); medium-lived objects expire by TIME (life_s), so their live set does not scale with the rate.
    measuring[0] = True
    t_start = time.perf_counter()
    wall_t0 = t_start
    rss_start = _status_kb("VmRSS")
    rss_peak = rss_start
    next_tick = t_start + 1.0
    rps = 2000.0
    g0_mark = 0
    med = collections.deque()
    medc = collections.deque()                   # the leftover cycles, each kept for cycle_life_s (alive past a freeze, then dead)
    created = 0
    dead_peak = 0
    grow_per_s = max(1.0, len(base) * grow_pct_per_min / 100.0 / 60.0)   # long-lived growth, a share of the heap a minute
    grown = 0
    i = 0
    paced_from, paced_i = t_start, 0
    ticks = []
    rps_hist = []
    while True:
        now = time.perf_counter()
        if now - t_start >= seconds:
            break
        d = json.loads(texts[i % 256])
        r = {"id": i, "blocks": [{"k": b.get("type"), "n": len(b)} for b in d["message"]["content"]],
             "tmp": [x for x in d["meta"]["tags"]]}
        if i % cycle_every == 0:
            c = CycNode(); c.me = c; c.payload = (r, d)   # a cycle: freed only by the collector once its window drops it
            medc.append((now, c))
            created += 1
            del c
        else:
            med.append((now, (r, d)))
        while med and med[0][0] < now - life_s:
            med.popleft()
        while medc and medc[0][0] < now - cycle_life_s:
            medc.popleft()
        if grown < (now - t_start) * grow_per_s:
            base.append(Rec(n, d)); n += 1; grown += 1
        i += 1
        if now >= next_tick:                     # the pusher's idle tick (inserts constant: no load fold-ins)
            k = ctl.tick(1)
            if k:
                ticks.append(k)
            g0 = tally[0][0]
            obs = g0 - g0_mark
            g0_mark = g0
            rps = max(rps / 2.0, min(rps * 2.0, rps * gen0_rate / max(obs, 1)))
            rps_hist.append(rps)
            paced_from, paced_i = time.perf_counter(), i
            next_tick = paced_from + 1.0
            rss = _status_kb("VmRSS")
            rss_peak = max(rss_peak, rss)
            dead_peak = max(dead_peak, created - FREED[0] - len(medc))
        due = paced_from + (i - paced_i) / rps   # pace to the current rate
        if due > now:
            time.sleep(due - now)
    wall = time.perf_counter() - wall_t0
    measuring[0] = False
    rss_end = _status_kb("VmRSS")
    pauses2.sort()
    # end of run: one forced backstop (unfreeze, full collect, re-freeze), outside the measured window. Its pause is what a
    # backstop or a release reclaim costs at this heap; what it collects is the cyclic garbage nothing else could free
    # (with the freeze on: the cycles it pinned), and the resident drop after malloc_trim is that garbage's memory.
    med.clear()
    medc.clear()
    gc.collect()                                 # first the unfrozen garbage, so the backstop's count is the frozen part
    last2[0] = 0                                 # the tally is closed (measuring is False): the backstop is reported apart
    t_b = time.perf_counter()
    ctl._run("backstop", 1, [])
    backstop_ms = (time.perf_counter() - t_b) * 1000.0
    backstop_collected = last2[0]
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass
    rss_after_backstop = _status_kb("VmRSS")
    out = {"size_gb": size_gb, "setting": setting, "t2": t2, "full_ms": full_ms, "seconds": round(wall, 1),
           "heap_mib": heap_kb // 1024, "cache_mib": (rss1 - rss0) // 1024, "tracked_objects": tracked_objects,
           "frozen_at_start": frozen_objects, "rps_median": round(sorted(rps_hist)[len(rps_hist) // 2], 1) if rps_hist else 0.0, "requests": i, "life_s": life_s,
           "gen2": tally[2][0], "gen2_ms_sum": round(tally[2][1], 1), "gen2_ms_max": round(tally[2][2], 1),
           "gen2_ms_p50": round(pauses2[len(pauses2) // 2], 1) if pauses2 else 0.0,
           "gen1": tally[1][0], "gen1_ms_sum": round(tally[1][1], 1), "gen0": tally[0][0], "gen0_ms_sum": round(tally[0][1], 1),
           "full_per_hour": round(tally[2][0] * 3600.0 / wall, 1),
           "full_avg_ms": round(tally[2][1] / tally[2][0], 1) if tally[2][0] else 0.0,
           "full_share": round(tally[2][1] / 1000.0 / wall, 4),
           "all_gc_share": round((tally[0][1] + tally[1][1] + tally[2][1]) / 1000.0 / wall, 4),
           "rss_start_mib": rss_start // 1024, "rss_peak_mib": max(rss_peak, rss_end) // 1024, "rss_end_mib": rss_end // 1024,
           "hwm_mib": _status_kb("VmHWM") // 1024,
           "ctl": {"freezes": ctl.freezes, "reclaims": ctl.reclaims, "fullFreezes": ctl.full_freezes, "threshold": list(gc.get_threshold()),
                   "callbackErrors": ctl.callback_errors, "ticks": collections.Counter(ticks)},
           "cleanup_max_s": getattr(ctl, "full_cleanup_max_s", None), "grow_pct_per_min": grow_pct_per_min,
           "reclaims_in_window": ctl.reclaims, "cycle_every": cycle_every, "cycle_life_s": cycle_life_s, "cycles_created": created,
           "bytes_per_cycle": round(bytes_per_cycle), "dead_uncollected_peak_cycles": dead_peak,
           "dead_uncollected_peak_mib": round(dead_peak * bytes_per_cycle / MIB, 2),
           "ctl_in_window": ctl_tally[0], "ctl_in_window_ms_sum": round(ctl_tally[1], 1),
           "ctl_in_window_ms_max": round(ctl_tally[2], 1), "ctl_in_window_share": round(ctl_tally[1] / 1000.0 / wall, 4),
           "frozen_at_end": gc.get_freeze_count(), "backstop_ms": round(backstop_ms, 1),
           "backstop_collected": backstop_collected, "rss_after_backstop_mib": rss_after_backstop // 1024}
    print(json.dumps(out), flush=True)


def listing_child(size_gb, seed):
    """What listing or counting a large heap costs (the 2026-10-07 review): a tracked decoded-JSON heap of `size_gb`
    resident, then timed, each once: len(gc.get_objects()) (the listing a per-freeze count would pay), a full collection,
    gc.freeze() (a pointer move) and gc.get_freeze_count() (a walk of the frozen list, what a cleanup pays to re-read)."""
    import gc
    rng = random.Random(seed)
    texts = [json.dumps(_message(rng, i)) for i in range(256)]
    rss1 = _status_kb("VmRSS")
    base, n = [], 0
    while _status_kb("VmRSS") - rss1 < int(size_gb * 1024 * 1024):
        for _ in range(5000):
            base.append(Rec(n, json.loads(texts[n % 256]))); n += 1
    out = {"mode": "listing", "size_gb": size_gb, "heap_mib": (_status_kb("VmRSS") - rss1) // 1024}
    gc.collect()
    t = time.perf_counter(); k = len(gc.get_objects()); out["get_objects_ms"] = round((time.perf_counter() - t) * 1000, 1)
    out["tracked_objects"] = k
    t = time.perf_counter(); gc.collect(); out["full_collect_ms"] = round((time.perf_counter() - t) * 1000, 1)
    t = time.perf_counter(); gc.freeze(); out["freeze_ms"] = round((time.perf_counter() - t) * 1000, 3)
    t = time.perf_counter(); f = gc.get_freeze_count(); out["get_freeze_count_ms"] = round((time.perf_counter() - t) * 1000, 1)
    out["frozen_objects"] = f
    gc.unfreeze()
    out["hwm_mib"] = _status_kb("VmHWM") // 1024
    print(json.dumps(out), flush=True)


def summarize_listing(rows, at_gb):
    rows = sorted(rows, key=lambda r: r["size_gb"])
    print("LISTING COST (one read each, fresh process per size):")
    print("%5s %8s %12s %14s %12s %10s %16s" % ("GB", "heapMiB", "objects", "get_objects ms", "collect ms", "freeze ms",
                                              "freeze_count ms"))
    for r in rows:
        print("%5.1f %8d %12d %14.1f %12.1f %10.3f %16.1f" % (r["size_gb"], r["heap_mib"], r["tracked_objects"],
              r["get_objects_ms"], r["full_collect_ms"], r["freeze_ms"], r["get_freeze_count_ms"]))
    if len(rows) >= 2:
        xs = [r["heap_mib"] / 1024.0 for r in rows]
        print("EXTRAPOLATION to %.0f GB (linear fit; NOT a measurement):" % at_gb)
        for key in ("get_objects_ms", "full_collect_ms", "get_freeze_count_ms"):
            a, b = _fit(xs, [r[key] for r in rows])
            print("  %-20s %8.0f ms" % (key, a + b * at_gb))


def _fit(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else 0.0
    return my - b * mx, b


def summarize(path, at_gb, live_share=0.11, live_max_ms=17780.0, live_avg_ms=5900.0, live_rate=67.0):
    rows = [json.loads(l) for l in open(path) if l.strip().startswith("{")]
    listing = [r for r in rows if r.get("mode") == "listing"]
    rows = [r for r in rows if r.get("mode") != "listing"]
    if listing:
        summarize_listing(listing, at_gb)
        if not rows:
            return
        print()
    by = collections.OrderedDict()
    for r in rows:
        by.setdefault(r["setting"], []).append(r)
    print("MEASURED (steady phase, per heap size):")
    print("%-22s %5s %7s %9s %9s %9s %8s %8s %9s %6s %6s" % ("setting", "GB", "heapMiB", "full/h", "avg ms", "max ms",
                                                            "full%", "allgc%", "peakMiB", "growth", "frzs") + " %9s %9s %14s" % ("bstop ms", "pinned", "ctl in window"))
    for s, rs in by.items():
        for r in sorted(rs, key=lambda r: r["size_gb"]):
            print("%-22s %5.1f %7d %9.1f %9.1f %9.1f %7.2f%% %7.2f%% %9d %+6d %6d" % (
                s, r["size_gb"], r["heap_mib"], r["full_per_hour"], r["full_avg_ms"], r["gen2_ms_max"],
                100 * r["full_share"], 100 * r["all_gc_share"], r["rss_peak_mib"], r["rss_end_mib"] - r["rss_start_mib"],
                r["ctl"]["fullFreezes"]) + " %9.0f %9d %4d %6.0fms" % (r.get("backstop_ms", 0), r.get("backstop_collected", 0),
                r.get("ctl_in_window", 0), r.get("ctl_in_window_ms_max", 0)))
    print()
    print("EXTRAPOLATION to %.0f GB of tracked heap (linear fit over the measured sizes; NOT a measurement):" % at_gb)
    base = None
    ext = collections.OrderedDict()
    for s, rs in by.items():
        if len({r["size_gb"] for r in rs}) < 2:
            continue
        xs = [r["heap_mib"] / 1024.0 for r in rs]
        a1, b1 = _fit(xs, [r["full_avg_ms"] for r in rs])
        a2, b2 = _fit(xs, [r["gen2_ms_max"] for r in rs])
        a3, b3 = _fit(xs, [r["full_share"] for r in rs])
        ext[s] = (max(0.0, a1 + b1 * at_gb), max(0.0, a2 + b2 * at_gb), max(0.0, a3 + b3 * at_gb))
        if s == "today":
            base = ext[s]
    print("%-22s %10s %10s %9s %22s" % ("setting", "avg ms", "max ms", "full%", "live-scaled full% (x)"))
    for s, (avg, mx, sh) in ext.items():
        scaled = (live_share * sh / base[2]) if base and base[2] else float("nan")
        print("%-22s %10.0f %10.0f %8.2f%% %21.2f%%" % (s, avg, mx, 100 * sh, 100 * scaled))
    print("live kernel (03:25Z read, ~16 GB tracked outside the cache): %.0f full/h, avg %.0f ms, max %.0f ms, %.0f%% of wall"
          % (live_rate, live_avg_ms, live_max_ms, 100 * live_share))
    print("live-scaled = the live 11%% times the setting's extrapolated share over today's extrapolated share.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--child", action="store_true")
    ap.add_argument("--size-gb", type=float, default=1.0)
    ap.add_argument("--setting", default="today", choices=list(SETTINGS))
    ap.add_argument("--sizes", default="1,2,4")
    ap.add_argument("--settings", default=",".join(SETTINGS))
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--gen0-rate", type=float, default=70.0, help="target generation-0 collections a second (live: ~70)")
    ap.add_argument("--life-s", type=float, default=1.0, help="medium-lived request lifetime in seconds")
    ap.add_argument("--cycle-every", type=int, default=25, help="one request in N leaves a reference cycle")
    ap.add_argument("--cycle-life-s", type=float, default=None, help="how long a leftover cycle lives (default --life-s)")
    ap.add_argument("--listing-sizes", default=None, help="time gc.get_objects / get_freeze_count over heaps of these GB")
    ap.add_argument("--listing", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--cleanup-max-s", default="default", help="the cleanup cap in seconds, 'off' for none (d1e0d4654)")
    ap.add_argument("--grow-pct-per-min", type=float, default=1.0, help="long-lived growth, percent of the heap a minute")
    ap.add_argument("--out", default=None)
    ap.add_argument("--summarize", default=None)
    ap.add_argument("--at-gb", type=float, default=16.0)
    ap.add_argument("--max-peak-gb", type=float, default=12.0)
    a = ap.parse_args()
    if a.summarize:
        summarize(a.summarize, a.at_gb)
        return
    if a.listing:
        listing_child(a.size_gb, a.seed)
        return
    if a.listing_sizes:
        out = open(a.out, "a") if a.out else sys.stdout
        for size in [float(x) for x in a.listing_sizes.split(",")]:
            if size * 1.3 + 1.0 > a.max_peak_gb:
                sys.exit("refused: %.1f GB would peak over --max-peak-gb %.1f" % (size, a.max_peak_gb))
            if _mem_available_mib() < int((size * 1.3 + 1.0) * 1024) * 3:
                sys.exit("refused: MemAvailable is under three times the run's need; the machine is shared")
            res = subprocess.run([sys.executable, os.path.realpath(__file__), "--listing", "--size-gb", str(size),
                                  "--seed", str(a.seed)], capture_output=True, text=True, env=dict(os.environ, PYTHONHASHSEED="0"))
            line = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""
            if res.returncode != 0 or not line.startswith("{"):
                sys.stderr.write("listing @ %s GB failed (exit %d): %s\n" % (size, res.returncode, res.stderr[-600:]))
                continue
            out.write(line + "\n"); out.flush()
        return
    if a.child:
        cap = a.cleanup_max_s if a.cleanup_max_s == "default" else (None if a.cleanup_max_s.lower() in ("off", "0") else float(a.cleanup_max_s))
        child(a.size_gb, a.setting, a.seconds, a.seed, a.gen0_rate, a.life_s, a.cycle_every, a.cycle_life_s, cap, a.grow_pct_per_min)
        return
    sizes = [float(x) for x in a.sizes.split(",")]
    settings = a.settings.split(",")
    need_mib = int((max(sizes) * 1.6 + 1.0) * 1024)
    if max(sizes) * 1.6 + 1.0 > a.max_peak_gb:
        sys.exit("refused: the largest size would peak near %.1f GB, over --max-peak-gb %.1f" % (max(sizes) * 1.6 + 1, a.max_peak_gb))
    out = open(a.out, "a") if a.out else sys.stdout
    for size in sizes:
        for s in settings:
            avail = _mem_available_mib()
            if avail < need_mib * 3:
                sys.exit("refused: MemAvailable %d MiB is under three times the run's need (%d MiB); the machine is shared"
                         % (avail, need_mib))
            cmd = [sys.executable, os.path.realpath(__file__), "--child", "--size-gb", str(size), "--setting", s,
                   "--seconds", str(a.seconds), "--seed", str(a.seed), "--gen0-rate", str(a.gen0_rate), "--life-s", str(a.life_s),
                   "--cycle-every", str(a.cycle_every), "--cleanup-max-s", str(a.cleanup_max_s),
                   "--grow-pct-per-min", str(a.grow_pct_per_min)] + (["--cycle-life-s", str(a.cycle_life_s)] if a.cycle_life_s is not None else [])
            env = dict(os.environ, PYTHONHASHSEED="0")
            res = subprocess.run(cmd, capture_output=True, text=True, env=env)
            line = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""
            if res.returncode != 0 or not line.startswith("{"):
                sys.stderr.write("run %s @ %s GB failed (exit %d): %s\n" % (s, size, res.returncode, res.stderr[-800:]))
                continue
            out.write(line + "\n"); out.flush()
            r = json.loads(line)
            sys.stderr.write("%s @ %.0f GB: %.0f full/h, avg %.0f ms, max %.0f ms, %.2f%% paused, peak %d MiB, dead-uncollected "
                             "cycles peak %d (%.1f MiB)\n" % (
                s, size, r["full_per_hour"], r["full_avg_ms"], r["gen2_ms_max"], 100 * r["full_share"], r["rss_peak_mib"],
                r["dead_uncollected_peak_cycles"], r["dead_uncollected_peak_mib"]))


if __name__ == "__main__":
    main()
