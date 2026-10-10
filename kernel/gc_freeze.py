#!/usr/bin/env python3
"""Road B for issue #1735: keep the loaded decoded heap out of the cycle collector's walk.

A generation-two (full) collection visits every tracked container in the interpreter; the
request-serving kernel holds millions of them in decoded transcript trees, so a warm full
collection pauses every thread (the pusher included) for seconds. `gc.freeze()` moves the
currently tracked objects into a permanent generation the collector never walks again, so a
later full collection walks only what was allocated since. Measured (plans/gc-full-collection-pause.md):
a warm full collection over 5.6 million loaded objects fell from 2.83 s to 0.1 ms once they were
frozen, and the freeze itself is a pointer move.

Two operations, both at the pusher's IDLE boundary (never a timer), where a pause is paid with no
browser waiting:

  - a LOAD fold-in (cheap): `gc.collect(); gc.freeze()`. The collect walks only the unfrozen objects
    loaded since the last freeze, so it costs milliseconds; the freeze folds their survivors in.
    Keyed on the record cache's insert counter growing by a material number of trees.
  - a RELEASE reclaim (up to the full pause), STAGED: `gc.collect()` with the freeze IN PLACE first (cheap,
    it takes a released cycle allocated since the last freeze), then the owed refs are re-read and only a
    survivor drives `gc.unfreeze(); gc.collect()` (the frozen-heap walk); a `gc.freeze()` re-holds either
    way. A release the cheap walk took whole counts as a LOAD pass, so the backstop's bound does not stretch.

WHEN to reclaim is MEASURED, not guessed. Three review rounds each guessed cyclicity from a state flag
(a session's `client`, its thread) and each found a new path the guess missed (a teardown exception
after the client was cleared leaves a cyclic session with client None). So instead of deciding at the
pop, every session-end pop REGISTERS a `weakref.ref(session)` (with its worker thread) via `note_ended`,
and the idle tick judges each by observation: a dead ref died by reference counting (acyclic, no
reclaim); a live ref whose worker thread still runs is not garbage yet (kept for the next tick, a kill
mid-turn); a live ref whose thread has finished is a cycle the collector must take (reclaim once for all
such, then drop them). Exact on every path, no per-path reasoning. A BOUNDED BACKSTOP (after
`backstop_foldins` load fold-ins) covers any ended owner nobody registered. The record cache's
`released` counter is a /perf statistic, never a trigger: its entries are acyclic.

The thread test rests on CPython's own threading: a running session and its worker Thread reference each
other (the Thread holds the session as its `_target`), and `Thread.run` deletes `_target` in a `finally`
when it returns, so once the thread has finished it no longer holds the session. A live ref whose thread
has finished is therefore held by a DIFFERENT surviving cycle (a traceback frame, a stray reference), the
reclaim's target. An UNSTARTED thread reads as finished (`is_alive()` False) and still holds the session
(the Thread keeps `_target`, since `run`'s finally never fires for it), so the ref is alive on a cycle;
judging it a reclaim is correct precisely because ONLY the collector can take that cycle (refcounting never
will), which is what a reclaim does. A ref a live ROOT keeps (a helper thread still running when the tick
judges it, its frame and its target, not a cycle) reads the same, alive with a finished worker, and is TREATED
as a surviving cycle: the reclaim frees nothing, so it costs ONE reclaim, is counted a `survivor` on /perf,
and is dropped (never re-registered), never a pass per tick.

A request that arrives during a reconcile waits that one collection; the idle boundary is the best
moment for it, not a guarantee none arrives. Default on; `ROMP_GC_FREEZE=off` (or `0`/`false`) off.

A third operation, the FULL-COLLECTION freeze (2026-10-07): the load fold-in keys on the record cache's inserts, and
once the cache's decoded records were untracked (they never enter the walk at all) the heap that grows OUTSIDE the cache
(about 16 GB tracked on the live kernel) was frozen only by the start-up freeze, so every organic full collection walked
it: about 67 an hour averaging 5.9 s, longest 17.8 s, 11% of wall time. So the controller also hooks gc.callbacks and, on
the "stop" of an ORGANIC generation-2 collection whose pause reached `full_freeze_ms`, calls gc.freeze() right there. At
that instant every tracked object has just survived a full collection (the collector merged the young generations into
the old and freed the unreachable), so the freeze pins no garbage; it is a pointer move, so it costs no pause, and the
next full collection walks only what was promoted since. The pause threshold keys the freeze on the event that costs
(an expensive walk) and leaves cheap full collections alone (the bench froze about once every 10 to 40 s while the cheap
walks averaged about 250 ms; scripts/bench_gc_full_rate.py). What a freeze CAN pin is a cycle that
forms LATER among objects it froze (alive at the freeze, cyclic garbage afterwards): those are not walked until an
unfreeze, so once a full-collection freeze has run, a backstop reclaim (the same unfreeze/collect/re-freeze the fold-in
backstop runs) is owed at the next idle tick when the organic full-collection pause summed since the last reclaim reaches
`full_backstop_ratio` times the largest pause seen (the last reclaim's walk or an organic one, about the whole heap): its
cost is bounded by what the collector already spends, keyed on measured pauses, never a clock. While a backstop is owed
and has not run, the callback freezes NOTHING more (each skip counted), so what is pinned cannot grow; and because the
idle tick may never come under sustained load (the live kernel once read no idle pusher cycle in 5.5 hours), an owed
backstop that has waited `full_force_s` (ROMP_GC_FREEZE_FULL_FORCE_S, default 600 s) runs at the next BUSY pusher cycle
boundary instead (pusher_tick, never inside the gc callback or a locked region), counted as forced. The idle tick stays
the preferred path. The rule is ONE for both backstops: the fold-in backstop (after `backstop_foldins` load fold-ins) and
the full backstop share the owed test (`_backstop_owed`), one owed-since clock and one forced run (the live kernel once
read 4 idle pusher cycles in 91, so the fold-in backstop could wait as long as the full one). The forced path is governed by
ROMP_GC_FREEZE_FULL_FORCE_S alone, so it also covers the fold-in backstop with ROMP_GC_FREEZE_FULL_MS off.

The interval between cleanups is CAPPED (2026-10-07 live finding): the cost rule owes a cleanup at `full_backstop_ratio`
times the largest walk, and each cleanup's own walk raises that walk, so as the pinned heap grew cleanups got rarer (live:
about every 2 h and stretching, the tracked heap back on the no-fix curve by 9.7 h) while each freeze pinned more. Once
anything is frozen, a cleanup is also owed `full_cleanup_max_s` (ROMP_GC_FREEZE_CLEANUP_MAX_S, default 3000 s) after the
last cleanup (or the first freeze), its owed clock starting at that moment, while the full-collection freeze is on (with
ROMP_GC_FREEZE_FULL_MS off there is no cap, as before it); with the forced path that is at most an hour
under sustained load. A lower ratio would still scale with the walk; a reference taken from the cheap collections would owe
a whole-heap walk every few minutes. The cap costs one whole-heap walk per interval and nothing else. Bench (1 GB, 25
min, one request in 5 leaving a cycle that lives 5 s, survivors growing 3% of the heap a minute; scripts/bench_gc_full_rate.py
--cleanup-max-s): no cap, peak dead-uncollected cycles 2,092 MiB, peak RSS 4,268 MiB, 2 cleanups (longest 19.1 s), paused
4.0% + 1.5% in cleanups; cap 300 s, 827 MiB, 2,929 MiB, 5 cleanups (longest 9.7 s), paused 4.0% + 2.6%. The third collection
threshold is raised to `full_t2` with the freeze: once the long-lived heap is frozen, a full collection is cheap but
CPython's quarter rule no longer holds it back (the long-lived total it divides is the small unfrozen part), so the
generation-1 count gate is what bounds the rate. `ROMP_GC_FREEZE_FULL_MS=off` (or 0) restores the
behaviour before this operation; `ROMP_GC_FREEZE=off` turns every operation off.
"""
import gc as _gc_mod
import os
import threading
import time
import weakref

DEFAULT_LOAD_TREES = 8            # record-cache inserts since the last freeze that count as a material load
MIN_LOAD_TREES = 1               # floored here: a threshold of 0 or below would fold in every idle cycle
DEFAULT_BACKSTOP_FOLDINS = 1000  # a reclaim after this many load fold-ins since the last reclaim, bounding a cyclic release
#                                  no ended ref caught; at the measured ~97 fold-ins an hour it fires about once in ten hours
DEFAULT_FULL_FREEZE_MS = 250.0   # an organic full collection at least this long freezes its survivors (bench: scripts/bench_gc_full_rate.py)
DEFAULT_FULL_T2 = 100          # the third collection threshold while the full freeze is on (CPython's default is 10)
DEFAULT_FULL_FORCE_S = 600.0     # an owed full backstop that waited this long runs on a busy pusher cycle (the idle tick may never come)
DEFAULT_FULL_CLEANUP_MAX_S = 3000.0   # once anything is frozen, a cleanup is owed at most this long after the last (plus the
#                                       600 s forced bound: an hour at most under load)
DEFAULT_FULL_BACKSTOP_RATIO = 10.0   # a backstop is owed once the organic full-collection pause since the last reclaim reaches this many
#                                      times the largest whole-heap walk seen: the backstop then costs at most ~1/10 of what full
#                                      collections spend (a count of freezes instead fired one every ~4 min at a 4 GB bench heap)
_OFF = ("off", "0", "false")


def enabled_from_env(env=None):
    """The freeze is on unless ROMP_GC_FREEZE names an off value (the measurement switch)."""
    v = (env if env is not None else os.environ).get("ROMP_GC_FREEZE")
    return not (v is not None and v.strip().lower() in _OFF)


def load_trees_from_env(env=None):
    """(load_trees, bad_raw): the ROMP_GC_FREEZE_LOAD_TREES knob, parsed with a fallback to the default on a
    value that is not a positive integer, floored at MIN_LOAD_TREES. `bad_raw` is the offending string when the
    default was substituted (the caller says it once and counts it), else None. Parsed here, never at the kernel's
    import with a bare int(): a bad value must not kill the kernel before it serves (#1735 verifier, the high)."""
    raw = (env if env is not None else os.environ).get("ROMP_GC_FREEZE_LOAD_TREES")
    if raw is None or raw.strip() == "":
        return DEFAULT_LOAD_TREES, None
    try:
        n = int(raw.strip())
    except (TypeError, ValueError):
        return DEFAULT_LOAD_TREES, raw
    return max(MIN_LOAD_TREES, n), None


def full_freeze_ms_from_env(env=None):
    """(full_freeze_ms, bad_raw): the ROMP_GC_FREEZE_FULL_MS knob. Unset or empty is the default; an off value (off, 0,
    false) is None, the full-collection freeze disabled (today's behaviour before 2026-10-07); a positive number is the
    pause threshold in ms; anything else falls back to the default with `bad_raw` set, said once by the caller, never
    fatal at import (the #1735 rule for this module's knobs)."""
    raw = (env if env is not None else os.environ).get("ROMP_GC_FREEZE_FULL_MS")
    if raw is None or raw.strip() == "":
        return DEFAULT_FULL_FREEZE_MS, None
    v = raw.strip().lower()
    if v in _OFF:
        return None, None
    try:
        ms = float(v)
    except (TypeError, ValueError):
        return DEFAULT_FULL_FREEZE_MS, raw
    if not ms > 0 or ms != ms or ms == float("inf"):
        return (None, None) if ms == 0 else (DEFAULT_FULL_FREEZE_MS, raw)
    return ms, None


def full_t2_from_env(env=None):
    """(full_t2, bad_raw): the ROMP_GC_FREEZE_FULL_T2 knob, the third collection threshold installed with the full freeze.
    Unset or empty is the default; an off value is None (the threshold left as it is); a positive integer is the value;
    anything else falls back to the default with `bad_raw` set (said once by the caller, never fatal)."""
    raw = (env if env is not None else os.environ).get("ROMP_GC_FREEZE_FULL_T2")
    if raw is None or raw.strip() == "":
        return DEFAULT_FULL_T2, None
    v = raw.strip().lower()
    if v in _OFF:
        return None, None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return DEFAULT_FULL_T2, raw
    return (n, None) if n > 0 else (DEFAULT_FULL_T2, raw)


def full_force_s_from_env(env=None):
    """(full_force_s, bad_raw): the ROMP_GC_FREEZE_FULL_FORCE_S knob, how long an owed full backstop may wait for an idle
    tick before a busy pusher cycle runs it. Unset or empty is the default; an off value is None (never forced: the idle
    tick only, as at 203fcc519); a positive number is seconds; anything else falls back to the default with `bad_raw` set."""
    raw = (env if env is not None else os.environ).get("ROMP_GC_FREEZE_FULL_FORCE_S")
    if raw is None or raw.strip() == "":
        return DEFAULT_FULL_FORCE_S, None
    v = raw.strip().lower()
    if v in _OFF:
        return None, None
    try:
        sec = float(v)
    except (TypeError, ValueError):
        return DEFAULT_FULL_FORCE_S, raw
    if not sec > 0 or sec != sec or sec == float("inf"):
        return DEFAULT_FULL_FORCE_S, raw
    return sec, None


def full_cleanup_max_s_from_env(env=None):
    """(full_cleanup_max_s, bad_raw): the ROMP_GC_FREEZE_CLEANUP_MAX_S knob, the longest a frozen heap goes between cleanups.
    Unset or empty is the default; an off value is None (no cap: the cost rule alone, as at d1e0d4654); a positive number is
    seconds; anything else falls back to the default with `bad_raw` set (said once by the caller, never fatal)."""
    raw = (env if env is not None else os.environ).get("ROMP_GC_FREEZE_CLEANUP_MAX_S")
    if raw is None or raw.strip() == "":
        return DEFAULT_FULL_CLEANUP_MAX_S, None
    v = raw.strip().lower()
    if v in _OFF:
        return None, None
    try:
        sec = float(v)
    except (TypeError, ValueError):
        return DEFAULT_FULL_CLEANUP_MAX_S, raw
    if not sec > 0 or sec != sec or sec == float("inf"):
        return DEFAULT_FULL_CLEANUP_MAX_S, raw
    return sec, None


class GcFreeze:
    """The freeze controller. `gc` and `clock` are injected so a test drives a fake collector or a real one; the
    kernel passes the real `gc`. The `tick`/`_run` path runs on the pusher thread; `note_ended` runs on every
    thread that ends a session (the HTTP handler, housekeeping, a worker), so a small lock guards the ended list."""

    def __init__(self, enabled=True, load_trees=DEFAULT_LOAD_TREES, backstop_foldins=DEFAULT_BACKSTOP_FOLDINS,
                 gc=_gc_mod, clock=time.perf_counter, full_freeze_ms=None, full_backstop_ratio=DEFAULT_FULL_BACKSTOP_RATIO, full_t2=None,
                 full_force_s=DEFAULT_FULL_FORCE_S, full_cleanup_max_s=DEFAULT_FULL_CLEANUP_MAX_S):
        self.enabled = bool(enabled)
        self.full_freeze_ms = float(full_freeze_ms) if full_freeze_ms else None   # None: the full-collection freeze is off
        self.full_backstop_ratio = max(1.0, float(full_backstop_ratio))
        self.full_force_s = float(full_force_s) if full_force_s else None   # None: an owed backstop waits for an idle tick only
        self._owed_since = None      # the clock when the full backstop was first seen owed; None while not owed
        self.full_cleanup_max_s = float(full_cleanup_max_s) if full_cleanup_max_s else None   # None: no cap on the interval
        self._pin_since = None       # the clock at the first freeze, then at each cleanup: frozen (so pinnable) since then
        self.full_freeze_skips = 0   # freezes the callback skipped because a backstop was owed (pinning frozen until it runs)
        self.forced = 0              # owed full backstops run on a busy pusher cycle because no idle tick came within full_force_s
        self.reclaim_skips = 0       # reclaims whose collection did not run (another thread was collecting): left owed, retried
        self.full_t2 = int(full_t2) if full_t2 else None   # the third threshold installed with the full freeze; None leaves it
        self._saved_threshold = None # the thresholds install_full_freeze replaced, restored by remove_full_freeze
        self._full_t0 = None         # the open generation-2 collection's start (the collector serialises collections: one slot)
        self._in_run = False         # True while _run issues its own collections: the run re-freezes itself, the callback stands down
        self.full_freezes = 0        # freezes the gc callback ran after an expensive organic full collection
        self._full_since_reclaim = 0 # of those, since the last reclaim (no backstop is owed while it is 0: nothing pinned)
        self._full_ms_since_reclaim = 0.0   # organic full-collection pause summed since the last reclaim
        self._full_ref_ms = 0.0      # the largest walk seen since (and including) the last reclaim: about the whole heap
        self.last_full_ms = 0.0      # the pause of the organic full collection that last froze
        self.callback_errors = 0     # failures inside gc_callback: counted, never raised into the collector
        self.frozen_as_of_cleanup = None     # the frozen count re-read at the LAST RECLAIM (gc.get_freeze_count walks the frozen
        #                                      list, so it is read only inside a reclaim's pause, never on a /perf read and never
        #                                      at a freeze: listing what a freeze adds was the same whole-heap walk); None until one
        self.frozen_as_of_cleanup_at = None  # that reclaim's wall-clock time (epoch seconds)
        self.load_trees = max(MIN_LOAD_TREES, int(load_trees))
        self.backstop_foldins = max(1, int(backstop_foldins))
        self._gc = gc
        self._clock = clock
        self.frozen = False
        self._ins_mark = 0           # cache inserts at the last freeze
        self._foldins = 0            # load fold-ins since the last reclaim (the backstop counts these)
        self._ended = []             # (weakref(session), weakref(thread)|None) registered at each session-end pop
        self._ended_lock = threading.Lock()   # note_ended runs on other threads than the tick; guard the list
        self.freezes = 0             # initial freeze plus load fold-ins
        self.reclaims = 0            # unfreeze/collect/re-freeze passes (a cyclic ended ref, or the backstop)
        self.survivors = 0           # owed refs still alive after a reclaim's collect: kept by a LIVE ROOT, not a cycle (a wasted pause), counted once and dropped
        self.last_release_sids = []  # first 8 chars of each sid a reclaim was owed for, set under the ended lock, cleared per judgement
        self.last_release_survivors = 0   # of the LAST RUN's owed refs, how many a live root kept through it (0 when the reclaim freed
        #                                   them, and 0 on any run that owed none, e.g. a load after a live-root release: read right after a release)
        self.last_kept_sids = []     # the sids of those survivors, likewise rebound each run (cleared to [] by a run that owed none); named kept-by-a-live-root in the release line
        self.collections = 0         # gc.collect() calls the RUN STEP issued (a full release issues two): /perf, so organic = gen2 collections less this, exactly
        self.last_ms = 0.0           # the last reconcile's collection pause
        self.total_ms = 0.0          # every reconcile's collection pause, summed
        self.last_kind = None        # "initial" | "load" | "release" | "backstop", for /perf

    def note_ended(self, session, thread=None):
        """Register an ended session for the idle tick to judge (a weakref, never a strong ref, so it can die by
        reference counting). `thread` is the session's worker thread; the tick reads whether it still runs. Called
        from whatever thread ended the session, so it takes the ended lock."""
        if not self.enabled:
            return
        try:
            pair = (weakref.ref(session), weakref.ref(thread) if thread is not None else None)
        except TypeError:
            return                   # an object that cannot be weak-referenced: the backstop still covers it
        with self._ended_lock:
            self._ended.append(pair)

    def resolve_ended(self):
        """Judge the registered ended sessions by OBSERVATION and return the list of refs a reclaim is OWED for (empty
        when none, so it reads as a boolean too). A dead ref died by reference counting (acyclic, dropped, no reclaim);
        a live ref whose thread still runs is not garbage yet (kept); a live ref whose thread has finished is a cycle the
        collector must take (owed, and dropped: one reclaim then gone, never re-registered, so a live ROOT that keeps it
        costs exactly one wasted pause). Never reclaims for a dead ref: that is the whole point of measuring instead of
        guessing. Judges IN PLACE under the ended lock, so a registration landing on another thread mid-judgement is never
        dropped. The owed sids' first 8 characters are set on `last_release_sids` under the lock, cleared each judgement, so
        a release names the sessions it was owed for (the 2026-09-22 PR 1999 review, attribution)."""
        keep, owed = [], []
        with self._ended_lock:
            ended = self._ended
            self._ended = keep                       # new registrations land in `keep` while we judge the old list
            for sref, tref in ended:
                if sref() is None:                   # died by refcount: acyclic, gone, nothing to reclaim
                    continue
                t = tref() if tref is not None else None
                if t is not None and t.is_alive():
                    keep.append((sref, tref))        # the worker thread still runs: not garbage yet, judge again next tick
                else:
                    owed.append((sref, tref))        # alive with its thread finished: a surviving cycle the reclaim must take
            self.last_release_sids = [(getattr(sref(), "sid", "") or "")[:8] for sref, _ in owed if sref() is not None]
        return owed                                  # `keep` is already self._ended (set under the lock); no racy rebind here

    def tick(self, inserts):
        """One idle-boundary pass: judge the ended sessions, then run the owed operation. Returns the kind run, or
        None. The reclaim is owed by an observed cyclic ended session or the backstop; the fold-in by a material
        load; the first freeze once anything is loaded."""
        if not self.enabled:
            return None
        owed = self.resolve_ended()
        if not self.frozen:
            return self._run("initial", inserts, []) if inserts > 0 else None
        if owed:
            return self._run("release", inserts, owed)
        if self._backstop_owed():
            return self._run("backstop", inserts, [])
        if (inserts - self._ins_mark) >= self.load_trees:
            return self._run("load", inserts, [])
        return None

    def _run(self, kind, inserts, owed):
        """Run the owed collector operation and return the kind actually run. A RELEASE is STAGED (the 2026-09-22 PR 1999
        review): a plain collect with the freeze IN PLACE first (cheap, it takes a cycle allocated since the last freeze),
        then the owed refs are re-read; only when one survived does it unfreeze and walk the frozen heap (the up-to-full
        pause). A release the cheap walk took whole counts as a LOAD pass, so the backstop's bound does not stretch. A
        BACKSTOP is blind (no owed refs) and unfreezes straight away. After the collect and the re-freeze, an owed ref still
        alive is kept by a LIVE ROOT, not a cycle: a wasted pause, counted as a survivor and dropped (never re-registered).
        The re-read runs after the re-freeze, which is harmless: a freeze collects nothing, so a ref alive after the collect
        is alive after the freeze too."""
        self._in_run = True
        try:
            return self._run_steps(kind, inserts, owed)
        finally:
            self._in_run = False

    def _run_steps(self, kind, inserts, owed):
        t0 = self._clock()
        reclaimed = False
        if kind == "release":
            self._gc.collect(); self.collections += 1    # cheap: the freeze stays in place, so this walks only the unfrozen
            if any(sref() is not None for sref, _ in owed):
                self._gc.unfreeze()                  # a survivor: the released cycle is in the frozen set, lift it and walk
                if not self._collect_ran():
                    return self._reclaim_did_not_run(owed)
                reclaimed = True
            # else the cheap collect took the released cycle whole: no full pause, counted as a load pass below
        elif kind == "backstop":
            self._gc.unfreeze()                      # blind periodic reclaim: nothing owed to re-read, walk everything
            if not self._collect_ran():
                return self._reclaim_did_not_run([])
            reclaimed = True
        else:
            self._gc.collect(); self.collections += 1    # initial / load fold-in: walk only the unfrozen
        self.last_ms = (self._clock() - t0) * 1000.0
        self.total_ms += self.last_ms
        self._gc.freeze()                            # (re)freeze: the survivors leave the collector's walk again
        if reclaimed:
            try:
                self.frozen_as_of_cleanup = int(self._gc.get_freeze_count())   # one walk of the frozen list, inside the pause a
                self.frozen_as_of_cleanup_at = round(time.time(), 1)           #  reclaim already pays; served as of this cleanup
            except Exception:
                pass
        was_frozen = self.frozen
        self.frozen = True
        if reclaimed or self._pin_since is None:
            self._pin_since = self._clock()          # a cleanup re-freezes everything: the cap's interval starts over here
        self._ins_mark = inserts
        # the owed refs still alive after the re-freeze are kept by a LIVE ROOT, not a cycle: the reclaim freed nothing. Record
        # this release's survivor count and their sids (the release line names them kept, not "reclaimed"), and total them.
        kept = [(getattr(sref(), "sid", "") or "")[:8] for sref, _ in owed if sref() is not None]
        self.last_release_survivors = len(kept)
        self.last_kept_sids = kept
        self.survivors += len(kept)
        if reclaimed:
            self.last_kind = kind
            self.reclaims += 1
            self._foldins = 0
            self._full_since_reclaim = 0
            self._full_ms_since_reclaim = 0.0
            self._full_ref_ms = self.last_ms         # the reclaim walked the whole heap: the new reference
            self._owed_since = None
        else:
            self.last_kind = "initial" if not was_frozen else "load"   # a cheap-collect release folds in like a load
            self.freezes += 1
            if was_frozen:
                self._foldins += 1                   # a load fold-in; the initial freeze is not one
                self._backstop_owed()                # this fold-in may create the fold-in backstop's debt: start its clock now
        return self.last_kind

    def _collect_ran(self):
        """gc.collect() for a reclaim, and whether a generation-2 collection actually RAN: the call returns 0 without
        collecting while another thread is inside a collection (the collecting flag stays set through the callbacks, with
        the interpreter lock released), so the collector's own count is read around it. A collector without get_stats (a
        test double) is taken to have run."""
        try:
            before = self._gc.get_stats()[2]["collections"]
        except Exception:
            before = None
        self._gc.collect()
        ran = True
        if before is not None:
            try:
                ran = self._gc.get_stats()[2]["collections"] > before
            except Exception:
                ran = True
        if ran:
            self.collections += 1                    # counted only when it ran: organic = gen-2 collections less `collections`
        return ran

    def _reclaim_did_not_run(self, owed):
        """A reclaim whose collection did not run: re-freeze (the unfreeze moved everything back into the walk), count it,
        put the owed sessions back on the ended list, and change nothing else, so the backstop or release stays owed and the
        next boundary retries. Returns None (no reconcile ran)."""
        self._gc.freeze()
        self.reclaim_skips += 1
        if owed:
            with self._ended_lock:
                self._ended.extend(owed)
        return None

    def gc_callback(self, phase, info):
        """The gc.callbacks hook for the full-collection freeze. Times each generation-2 collection from "start" to "stop"
        and, when an ORGANIC one (not a collection _run issued) took at least `full_freeze_ms`, freezes right there, at the
        one instant every tracked object is a survivor of a full walk. Like the kernel's perf hook it NEVER takes a lock
        (an automatic collection runs on whichever thread crossed the threshold, possibly inside one of that thread's own
        locked regions) and never raises into the collector: a failure is counted under `callback_errors`."""
        try:
            if info.get("generation") != 2:
                return
            if phase == "start":
                self._full_t0 = self._clock()
                return
            t0, self._full_t0 = self._full_t0, None
            if t0 is None or self._in_run or not self.enabled or not self.full_freeze_ms:
                return
            dt = (self._clock() - t0) * 1000.0
            self._full_ms_since_reclaim += dt
            if dt > self._full_ref_ms:
                self._full_ref_ms = dt
            owed = self._backstop_owed()             # stamps the owed clock HERE if this collection's pause created the debt,
            #                                          the cheap early return below included (2026-10-07 review)
            if dt < self.full_freeze_ms:
                return
            if owed:                                 # a backstop owed and not yet run: freeze nothing more, so the pinned set cannot grow
                self.full_freeze_skips += 1
                return
            self._gc.freeze()
            self.frozen = True
            if self._pin_since is None:
                self._pin_since = self._clock()
            self.full_freezes += 1
            self._full_since_reclaim += 1
            self.last_full_ms = dt
            self._backstop_owed()                    # a freeze can complete the debt (the cost rule needs one): stamp it now
        except Exception:
            self.callback_errors += 1

    def full_backstop_owed(self):
        """Whether the full-collection freeze owes a backstop reclaim: a freeze ran since the last reclaim (so something
        may be pinned) and the organic full-collection pause since then reached `full_backstop_ratio` times the largest walk."""
        return (self._full_since_reclaim > 0 and self._full_ref_ms > 0
                and self._full_ms_since_reclaim >= self.full_backstop_ratio * self._full_ref_ms)

    def _backstop_owed(self):
        """Whether EITHER backstop is owed (the fold-in count, or the full-collection cost rule), stamping the one owed-since
        clock at the first reading that finds it owed (the callback or a tick). One test, one clock, both backstops."""
        cap_at = (self._pin_since + self.full_cleanup_max_s       # the cap belongs to the full-collection freeze: off with it,
                  if (self.full_cleanup_max_s and self.full_freeze_ms   # so ROMP_GC_FREEZE_FULL_MS=off restores the old collector
                      and self._pin_since is not None) else None)
        cap_due = cap_at is not None and self._clock() >= cap_at   # the clock is read only when a cap is set
        owed = self._foldins >= self.backstop_foldins or self.full_backstop_owed() or cap_due
        if owed and self._owed_since is None:
            self._owed_since = cap_at if cap_due else self._clock()   # the cap's debt arose when the cap was reached
        return owed

    def force_due(self):
        """Whether an owed backstop (either kind) has waited `full_force_s` for an idle tick: then a busy pusher cycle runs it."""
        if not (self.enabled and self.full_force_s) or not self._backstop_owed():
            return False
        return (self._clock() - self._owed_since) >= self.full_force_s

    def force_backstop(self, inserts):
        """Run an owed backstop (either kind) on a busy cycle once force_due; returns "forced" when it ran, else None. Called
        only from pusher_tick at a cycle boundary, never from the gc callback."""
        if not self.force_due():
            return None
        self.resolve_ended()                         # judge the ended list as the idle tick would (the reclaim covers them all)
        if self._run("backstop", inserts, []) is None:
            return None                              # the collection did not run: still owed, the next boundary retries
        self.forced += 1
        return "forced"

    def install_full_freeze(self):
        """When the controller and the full-collection freeze are both on: gc_callback into gc.callbacks (once) and the
        third collection threshold raised to `full_t2` (the first two kept). Returns whether it is installed. Off (either
        switch) installs nothing and leaves the thresholds as they are: the collector as it was before 2026-10-07."""
        if not (self.enabled and self.full_freeze_ms):
            return False
        if self.gc_callback not in self._gc.callbacks:
            self._full_t0 = None
            self._gc.callbacks.append(self.gc_callback)
        if self.full_t2 and self._saved_threshold is None:
            self._saved_threshold = tuple(self._gc.get_threshold())
            t0, t1 = self._saved_threshold[0], self._saved_threshold[1]
            self._gc.set_threshold(t0, t1, self.full_t2)
        return True

    def remove_full_freeze(self):
        """gc_callback out of gc.callbacks and the thresholds install_full_freeze replaced put back; a no-op when absent."""
        try:
            self._gc.callbacks.remove(self.gc_callback)
        except ValueError:
            pass
        self._full_t0 = None
        if self._saved_threshold is not None:
            self._gc.set_threshold(*self._saved_threshold)
            self._saved_threshold = None

    def perf(self):
        """The /perf gc block's freeze sub-block: the state and the reconcile trade, so a reconcile collection is
        told apart from an organic one. `active` is whether a freeze is held now (named to not collide with the
        integer `gc.frozen`); `frozenCount` (gc.get_freeze_count) is added by the caller."""
        return {"enabled": self.enabled, "active": self.frozen, "loadTrees": self.load_trees,
                "backstopFoldins": self.backstop_foldins, "freezes": self.freezes, "reclaims": self.reclaims,
                "collections": self.collections, "survivors": self.survivors, "lastReleaseSurvivors": self.last_release_survivors,
                "lastReleaseSids": list(self.last_release_sids),
                "endedPending": len(self._ended), "lastReconcileMs": round(self.last_ms, 1),
                "lastReconcileKind": self.last_kind, "totalReconcileMs": round(self.total_ms, 1),
                "fullFreezeMs": self.full_freeze_ms, "fullFreezes": self.full_freezes, "fullT2": self.full_t2,
                "fullSinceReclaim": self._full_since_reclaim, "fullBackstopRatio": self.full_backstop_ratio,
                "fullMsSinceReclaim": round(self._full_ms_since_reclaim, 1), "fullRefMs": round(self._full_ref_ms, 1),
                "fullFreezeSkips": self.full_freeze_skips, "fullForceS": self.full_force_s, "forced": self.forced,
                "reclaimSkips": self.reclaim_skips, "cleanupMaxS": self.full_cleanup_max_s, "frozenAsOfCleanup": self.frozen_as_of_cleanup,
                "frozenAsOfCleanupAt": self.frozen_as_of_cleanup_at,
                "owedForS": round(self._clock() - self._owed_since, 1) if self._owed_since is not None else None,
                "lastFullMs": round(self.last_full_ms, 1), "callbackErrors": self.callback_errors}


def pusher_tick(controller, idle, first, stats_fn, on_error):
    """The pusher's cycle-boundary call, extracted so it is pinned in-process. Reconcile on an IDLE, non-FIRST cycle; on a
    BUSY non-first cycle, run only an owed full backstop that has waited past its bound (controller.force_due), so pinned
    cycles are reclaimed under sustained load too. `stats_fn` returns the record cache stats (its `inserts` keys the LOAD fold-in). A raising `stats_fn` or
    tick is handed to `on_error` and never propagates: the pusher must not die on the freeze. Returns the reconcile
    kind run, or None."""
    if first or not controller.enabled:
        return None
    try:
        if not idle:
            force_due = getattr(controller, "force_due", None)
            if force_due is None or not force_due():
                return None
            return controller.force_backstop(int((stats_fn() or {}).get("inserts") or 0))
        inserts = int((stats_fn() or {}).get("inserts") or 0)
        return controller.tick(inserts)
    except Exception as e:
        on_error(e)
    return None
