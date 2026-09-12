#!/usr/bin/env python3
"""The API cell's hover history (the user 2026-09-08, who wanted the cell's hover to carry some history the
way the spend hover does). The rail's API cell hover and its click detail gain a History section that the
shell reads from GET /api-health when they open: the signal's state with its since-time and reason, one row
per window, the newest transitions with how long each state held, and a kernel restart shown as its own row
or divider. The shell half is _LANDING_APIH_JS: tests/test_api_health_hover_browser.py drives it in Chromium,
and the Script, Skin and Strip classes at the end of this module pin its source (kernel-inlined JavaScript is
tested from the Python side); the rest of this module holds the KERNEL half: the payload the section reads,
served through the real dispatcher on the credential the shell's fetch carries, and the kernel's own construction
of the backend, run with the backend module swapped for a recorder.

Rules pinned here: every field the section reads is in the payload; the transitions tail is bounded; a
restart files the row whose reason the section matches, and a bucket already unknown files nothing (the
section's bootAt divider covers that case, so the boundary is never hidden); the shell's cookie alone reads
the route with no Origin header, which is what a same-origin GET fetch sends; the apiHealth frame carries no
history and _api_health_push still sends nothing on an unchanged world, a route read in between included; the
backend's aggregator is seeded with the kernel's own boot clock, so a bucket the boot seeded has one since-time
in the head, the divider and the boot's row; that clock is the float, to the millisecond, and the seed is
clamped past the restored tail, so the restart row is the newest row however close the previous kernel's last
transition came to this start; the aggregator owns that stamp (truncated with floor, so its whole seconds are
/version's started) and serves it as bootAt, so bootAt, the seeded stateSince and the restart row are one number
clamp or not; and a restored row with a null t is skipped, never the whole history.

Synthetic only: a private synthetic sid, invented key material assembled at run time, a fixed epoch."""
import hashlib
import inspect
import io
from contextlib import redirect_stderr
import json
import math
import os
import re
import tempfile
import time
import types
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
DOCS = os.path.join(os.path.dirname(HERE), "docs")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads: they resolve their state root at import time, and only pytest runs
# conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_apih_hover", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_apih_hover", os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py"))

SID = "88888888-aaaa-4bbb-8ccc-000000000001"     # this module's private synthetic sid
KEY_MATERIAL = "test-key-material-" + "h" * 28    # invented; not shaped like any provider's key
KEY_FP = hashlib.sha256(KEY_MATERIAL.encode()).hexdigest()[:12]   # a synthetic 12-hex identity for the label
T0 = 1_756_800_000.0                              # a fixed synthetic epoch: the derivation is pure in `now`
TOK = km.TOKEN
JS = km._LANDING_APIH_JS


def _hist():
    """The History block of the cell's script, or '' before the section exists."""
    i, j = JS.find("// -- History"), JS.find("// full=false is the HOVER")
    return JS[i:j] if 0 <= i < j else ""


HIST = _hist()


def _storm(t_from, t_to, label, step=14.0):
    """Attempts every `step` seconds, two of every five a 429 retry: 40%, past every enter threshold."""
    out, t, i = [], t_from, 0
    while t < t_to:
        is429 = (i % 5) in (3, 4)
        out.append(sb.AhEvent(t, label, "fable", "retry" if is429 else "ok", "429" if is429 else "ok",
                              429 if is429 else None, SID, i // 20))
        t += step
        i += 1
    return out


def _label(ah):
    return sb.api_health_auth_label("ANTHROPIC_API_KEY", salt=ah.salt(), key_fp=KEY_FP, launched_keyed=True)


def _serve_get(path, headers=None):
    """Drive the REAL do_GET dispatcher over a fake socket and return (status, body): the route's gate and
    its body are what the shell's fetch meets, so the test asks the handler rather than the source."""
    h = km.Handler.__new__(km.Handler)
    h.client_address = ("127.0.0.1", 0)
    h.headers = dict(headers or {})
    h.path = path
    h.command = "GET"
    h.request_version = "HTTP/1.1"
    h.wfile = io.BytesIO()
    h.rfile = io.BytesIO()
    h.close_connection = True
    captured = {}
    h.send_response = lambda code, *a: captured.__setitem__("status", code)
    h.send_header = lambda k, v: None
    h.end_headers = lambda: None
    h.log_message = lambda *a: None
    h.do_GET()
    return captured.get("status"), h.wfile.getvalue().decode("utf-8", "replace")


class _Backend:
    """A stand-in SDK backend over a REAL aggregator on a scratch state dir, one storm in its ring."""

    def __init__(self, storm=True):
        self.ah = sb.ApiHealth(tempfile.mkdtemp(), boot_at=km._STARTED)     # seeded the way _sdk_locked seeds the live one
        self.label = _label(self.ah)
        if storm:
            now = time.time()
            for e in _storm(now - 600, now, self.label):
                self.ah._push(e)

    def api_health_snapshot(self, now=None, uptime_s=None):
        out = self.ah.snapshot(now, uptime_s=uptime_s)
        out["coverage"].update({"sdkSessionsLive": 1, "inTurn": 1, "retrying": 1})
        return out


class Payload(unittest.TestCase):
    """What the section reads, from the aggregator's own snapshot."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.ah = sb.ApiHealth(self.d)
        self.label = _label(self.ah)
        self.key = self.label + "|fable"

    def storm(self, lo, hi):
        for e in _storm(lo, hi, self.label):
            self.ah._push(e)

    def test_the_worst_bucket_names_a_bucket_that_carries_since_why_and_the_windows(self):
        self.storm(T0 - 600, T0)
        snap = self.ah.snapshot(T0, uptime_s=100)
        key = snap["overall"]["worstBucket"]
        self.assertEqual(key, self.key)
        self.assertIn(key, snap["buckets"], "the head row reads the bucket the roll-up names")
        b = snap["buckets"][key]
        self.assertEqual((snap["overall"]["state"], b["state"]), ("thrashing", "thrashing"))
        self.assertEqual(b["stateSince"], T0, "since: the read that entered the state")
        self.assertTrue(b["why"].startswith("rate429 over "), b["why"])
        self.assertEqual(snap["config"]["windows"], [60, 300, 900], "the rows come from the config in force")
        self.assertEqual(set(b["windows"]), {str(w) for w in snap["config"]["windows"]},
                         "windows are keyed by the config's seconds, as strings")
        for w in b["windows"].values():
            for k in ("requests", "noStatus", "rate429", "rate5xx", "gaveUp", "sessionsRetrying", "complete"):
                self.assertIn(k, w, k)
        self.assertIs(b["windows"]["60"]["complete"], True)
        self.assertIs(b["windows"]["900"]["complete"], False, "a window longer than the uptime says so")
        self.assertEqual((snap["asOf"], snap["uptimeS"]), (T0, 100.0), "the as-of stamp and the uptime the caveat names")
        self.assertIn("bootAt", snap, "the boot's stamp rides the snapshot itself")

    def test_transition_rows_carry_the_fields_the_tail_renders_newest_last(self):
        self.storm(T0 - 600, T0)
        self.ah.snapshot(T0)
        snap = self.ah.snapshot(T0 + 2000)          # past retention: the episode closes
        rows = snap["transitions"]
        self.assertEqual([(r["from"], r["to"]) for r in rows], [("unknown", "thrashing"), ("thrashing", "unknown")])
        for r in rows:
            self.assertLessEqual({"t", "bucket", "auth", "family", "from", "to", "why"}, set(r))
            self.assertEqual(r["bucket"], self.key, "durations are computed per bucket, so every row names its own")
        self.assertEqual([r["t"] for r in rows], sorted(r["t"] for r in rows), "newest last; the section sorts newest first")

    def test_the_tail_is_bounded_however_many_transitions_pass(self):
        for i in range(40):                          # two transitions per cycle: unknown -> thrashing -> unknown
            base = T0 + i * 5000
            self.storm(base - 600, base)
            self.ah.snapshot(base)
            snap = self.ah.snapshot(base + 2000)
        self.assertEqual(len(snap["transitions"]), sb.API_HEALTH_TRANSITIONS_KEEP)
        self.assertEqual(snap["transitions"][-1]["t"], T0 + 39 * 5000 + 2000, "the newest is kept")
        m = re.search(r"var HIST_ROWS=(\d+);", JS)
        self.assertIsNotNone(m, "the section caps its rows")
        self.assertLessEqual(int(m.group(1)), sb.API_HEALTH_TRANSITIONS_KEEP, "the section shows a slice of the tail")
        self.assertEqual(int(m.group(1)), 4, "four rows: a glance, not a log (T301)")

    def test_a_restart_files_the_row_the_section_matches(self):
        self.storm(T0 - 600, T0)
        self.ah.snapshot(T0)
        ah2 = sb.ApiHealth(self.d, boot_at=T0 + 30)
        snap = ah2.snapshot(T0 + 31)
        row = snap["transitions"][-1]
        self.assertEqual((row["from"], row["to"], row["t"]), ("thrashing", "unknown", T0 + 30))
        self.assertEqual(row["why"], sb.API_HEALTH_RESTART_WHY)
        self.assertIn("var RESTART_WHY='%s';" % sb.API_HEALTH_RESTART_WHY, JS,
                      "the section matches the boot's reason byte for byte")
        self.assertIn("restart=r.why===RESTART_WHY", HIST)
        self.assertIn("(restart?' · kernel restarted':'')", HIST, "and names the restart on the row")
        self.assertEqual(snap["buckets"][self.key]["state"], "unknown", "no held pre-restart state")

    def test_a_bucket_already_unknown_files_nothing_so_the_divider_is_keyed_on_boot_at(self):
        self.storm(T0 - 600, T0)
        self.ah.snapshot(T0)
        self.ah.snapshot(T0 + 2000)                  # thrashing -> unknown before the stop
        n = len(self.ah.snapshot(T0 + 2001)["transitions"])
        ah2 = sb.ApiHealth(self.d, boot_at=T0 + 3000)
        snap = ah2.snapshot(T0 + 3001)
        self.assertEqual(len(snap["transitions"]), n, "already unknown: the boot files no row")
        self.assertEqual(snap["bootAt"], T0 + 3000, "so the boundary rides the payload as bootAt")
        # and the section keys the divider on it, since no row marks the boundary
        self.assertIn("if(!crossed&&pre){crossed=true;", HIST)
        self.assertIn("if(!sawRestart){out+='<div class=\"ru-tip-row ah-hrow ah-boot\">", HIST)
        self.assertIn("<span class=ah-hword>kernel restarted</span>", HIST)
        self.assertIn('"bootAt": self.boot_stamp', inspect.getsource(sb.ApiHealth.snapshot),
                      "bootAt is the aggregator's own seed stamp, served in its snapshot")
        self.assertNotIn('out["bootAt"]', inspect.getsource(km.Handler.do_GET), "the route stamps no second number")


class OneClock(unittest.TestCase):
    """The head's since, the boot's restart row and the payload's bootAt read ONE clock, the kernel's own start
    (_STARTED), passed to the backend at construction. An aggregator seeding itself from its own clock ran seconds
    after _STARTED (the backend is built after the boot's imports and warm-up), and a hover head that named that
    clock for a bucket the boot seeded disagreed with a divider drawn at the kernel's start whenever the gap
    straddled a minute. The clock is the FLOAT, to the millisecond the payload's stamps carry: int() sits on the
    second boundary before the process started, under a row the previous kernel filed in that same second, and
    the tail would read that row as current above the restart row. The seed is clamped one millisecond past a
    restored row that is not before it, with one log line. The aggregator owns the stamp (ApiHealth.boot_stamp:
    the start truncated to the millisecond with floor, or the clamp) and serves it as bootAt, so the route stamps
    nothing of its own: bootAt, the seeded stateSince and the restart row are one number in the clamp case too."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        ah = sb.ApiHealth(self.d)
        self.label = _label(ah)
        self.key = self.label + "|fable"
        for e in _storm(T0 - 600, T0, self.label):
            ah._push(e)
        ah.snapshot(T0)                              # thrashing, persisted: the next boot files thrashing -> unknown

    def _check(self, snap, boot):
        b = snap["buckets"][self.key]
        row = snap["transitions"][-1]
        self.assertEqual(b["stateSince"], boot, "the seeded since IS the boot clock")
        self.assertEqual((row["t"], row["from"], row["to"], row["why"]), (boot, "thrashing", "unknown", sb.API_HEALTH_RESTART_WHY),
                         "and so is the restart row's stamp")
        self.assertEqual(snap["bootAt"], boot, "and so is the payload's bootAt")
        self.assertEqual(b["state"], "unknown")
        self.assertNotEqual(b["why"], sb.API_HEALTH_RESTART_WHY,
                            "the read that served this payload wrote its own reason over the seed's, so the section can key nothing on it")

    def test_the_aggregator_seeds_state_since_and_the_restart_row_from_boot_at(self):
        snap = sb.ApiHealth(self.d, boot_at=T0 + 30).snapshot(T0 + 31)
        self._check(snap, T0 + 30)

    def test_the_backend_hands_its_boot_at_to_the_aggregator_and_the_route_stamps_no_second_number(self):
        be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, boot_at=T0 + 30)
        self._check(be.api_health.snapshot(T0 + 31), T0 + 30)
        self.assertIn('"bootAt": self.boot_stamp', inspect.getsource(sb.ApiHealth.snapshot))
        self.assertNotIn('out["bootAt"]', inspect.getsource(km.Handler.do_GET))
        # and the section keys the divider on bootAt alone: no why-keyed branch, nothing for it to be dead on
        self.assertIn("boot=d.bootAt,", HIST)
        self.assertNotIn("b.why===RESTART_WHY", HIST)

    def test_the_kernel_builds_the_backend_with_its_own_start_as_boot_at(self):
        """_sdk_locked run for real, with the backend module it loads swapped for a recorder: the construction passes
        the kernel's _STARTED itself, the float, as boot_at. The names the construction reaches for (the module
        loader, the path check, the catalog cache and refresh, the binary lookup, the boot mark, the problem report)
        are stubbed for the call and put back; the kernel's singleton slot and the judge module's two wires too."""
        built = []

        class _Recorder:
            def __init__(self, *a, **kw):
                built.append(kw)

        fake = types.SimpleNamespace(SdkBackend=_Recorder, startup_auth_env=lambda *a, **k: {})

        names = ("_sdk_backend", "load_source", "_ensure_sdk_on_path", "_model_catalog_boot",
                 "_claude_bin", "_mark_boot", "_sdk_problem")
        saved = {n: getattr(km, n) for n in names}
        saved_jd = (km.jd._LOGIN_AUTH_ENV_FN, km.jd._USAGE_REFRESH_FN)
        problems = []
        try:
            km._sdk_backend = None
            km.load_source = lambda name, path: fake
            km._ensure_sdk_on_path = lambda: True
            km._model_catalog_boot = lambda _async=True: False   # the boot's one catalog call (T296): stubbed whole
            km._claude_bin = lambda: "/bin/true"
            km._mark_boot = lambda *a, **k: None
            km._sdk_problem = problems.append
            err = io.StringIO()
            with redirect_stderr(err):
                be = km._sdk_locked()
        finally:
            for n in names:
                setattr(km, n, saved[n])
            km.jd._LOGIN_AUTH_ENV_FN, km.jd._USAGE_REFRESH_FN = saved_jd
        self.assertEqual(problems, [], "the construction ran clean")
        self.assertNotIn("model catalog boot:", err.getvalue(),
                         "...and so did the catalog leg: no swallowed traceback under the boot's own except (T296b)")
        self.assertEqual(len(built), 1, "one backend built")
        self.assertIsInstance(be, _Recorder)
        kw = built[0]
        self.assertIn("boot_at", kw, "the kernel hands its start to the backend")
        self.assertIs(kw["boot_at"], km._STARTED,
                      "the kernel's own _STARTED, the float itself: never int(), never a clock read at construction")
        self.assertIs(kw["reconcile"], True)

    def test_a_backend_built_without_boot_at_seeds_from_its_own_clock_as_before(self):
        before = time.time()
        be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        after = time.time()
        stamp = be.api_health.boot_stamp
        self.assertGreaterEqual(stamp, math.floor(before * 1000) / 1000.0)
        self.assertLessEqual(stamp, after)
        self.assertEqual(be.api_health.snapshot(after + 1)["bootAt"], stamp)

    def test_the_route_serves_boot_at_state_since_and_the_restart_row_as_one_number(self):
        be = self._seeded(self.d, km._STARTED, [])              # seeded the way _sdk_locked seeds the live one
        out = self._route(be)
        b = out["buckets"][self.key]
        row = [r for r in out["transitions"] if r["why"] == sb.API_HEALTH_RESTART_WHY][-1]
        self.assertEqual(out["bootAt"], be.ah.boot_stamp, "the aggregator's own stamp, served as bootAt")
        self.assertLessEqual(out["bootAt"], km._STARTED)
        self.assertLess(km._STARTED - out["bootAt"], 0.001, "the kernel's start truncated to the millisecond")
        self.assertEqual(int(out["bootAt"]), int(km._STARTED),
                         "/version's started: the same start in whole seconds (the restored tail is a year old: no clamp)")
        self.assertEqual(b["stateSince"], out["bootAt"], "head since and bootAt: one number")
        self.assertEqual(row["t"], out["bootAt"], "the restart row's stamp too")
        self.assertEqual(out["bootId"], km._BOOT_ID, "bootId stays the route's, from /version's globals")

    @staticmethod
    def _seeded(d, boot, lines):
        class _Seeded(_Backend):
            def __init__(inner):
                inner.ah = sb.ApiHealth(d, boot_at=boot, log=lines.append)
        return _Seeded()

    def _route(self, be):
        saved = km._sdk
        try:
            km._sdk = lambda: be
            status, body = _serve_get("/api-health", {"Cookie": "romp_token=" + TOK})
        finally:
            km._sdk = saved
        self.assertEqual(status, 200, body[:200])
        return json.loads(body)

    def test_a_same_second_race_reads_one_stamp_in_the_head_the_divider_and_the_restart_row(self):
        # the previous kernel's last read filed AFTER this kernel's start (it drains under SIGTERM while still serving
        # and the manager respawns at once, or the clock stepped): the seed is clamped past that row, and bootAt IS the
        # clamped stamp. A route stamping its own round(_STARTED, 3) put the head (stateSince) and the restart row at
        # the clamped stamp while the divider read bootAt, and with a minute boundary inside the gap the head named one
        # minute and the divider the one before
        d, key = self._previous_kernel(km._STARTED + 0.2)
        lines = []
        be = self._seeded(d, km._STARTED, lines)
        out = self._route(be)
        b = out["buckets"][key]
        row = [r for r in out["transitions"] if r["why"] == sb.API_HEALTH_RESTART_WHY][-1]
        old = round(km._STARTED + 0.2, 3)
        self.assertEqual(out["bootAt"], be.ah.boot_stamp, "bootAt is the seed's own stamp")
        self.assertAlmostEqual(out["bootAt"], old + 0.001, delta=1e-6, msg="one millisecond past the previous kernel's row")
        self.assertEqual(b["stateSince"], out["bootAt"], "the head's since")
        self.assertEqual(row["t"], out["bootAt"], "the restart row")
        self.assertEqual(row["t"], max(r["t"] for r in out["transitions"]), "the newest row")
        self.assertIn(int(out["bootAt"]), (int(km._STARTED), int(km._STARTED) + 1),
                      "the clamp may cross the second; /version's started stays int(_STARTED)")
        self.assertEqual(len([ln for ln in lines if "not before this boot" in ln]), 1, lines)
        # the section reads the divider and the restart row from these fields and nothing else, so they show one time
        self.assertIn("boot=d.bootAt,", HIST)
        self.assertIn("<span class=ru-tip-k>'+hmd(boot)+'</span><span class=ah-hword>kernel restarted</span>", HIST)

    def test_the_stamp_is_the_start_truncated_to_the_millisecond_so_its_whole_seconds_are_versions_started(self):
        # floor, never round: a start at X.9996 rounds to (X+1).000 while /version's started is int(X.9996) = X, so
        # int(bootAt) == started would fail for the boots whose fraction is .9995 or more. The floor never lands on
        # the next second either: a float one step below a whole second multiplies to more than half an ulp below
        # it, so the largest float under X+1 truncates to X.999
        for x in (T0, T0 + 0.4, T0 + 0.9994, T0 + 0.9995, T0 + 0.9996, T0 + 0.9999,
                  math.nextafter(T0 + 1, 0), math.nextafter(T0 + 2, 0), time.time()):
            ah = sb.ApiHealth(tempfile.mkdtemp(), boot_at=x)
            self.assertEqual(int(ah.boot_stamp), int(x), repr(x))
            self.assertLessEqual(ah.boot_stamp, x, repr(x))
            self.assertLess(x - ah.boot_stamp, 0.001, repr(x))
            self.assertEqual(ah.boot_stamp, round(ah.boot_stamp, 3), "at the millisecond")
            self.assertEqual(ah.snapshot(x + 1)["bootAt"], ah.boot_stamp, "served as bootAt")
        self.assertEqual(sb.ApiHealth(tempfile.mkdtemp(), boot_at=T0 + 0.9996).boot_stamp, T0 + 0.999)
        self.assertEqual(sb.ApiHealth(tempfile.mkdtemp(), boot_at=math.nextafter(T0 + 1, 0)).boot_stamp, T0 + 0.999)

    def _previous_kernel(self, last_t):
        """A state file as the previous kernel left it: its last read filed unknown -> thrashing at `last_t`."""
        d = tempfile.mkdtemp()
        ah = sb.ApiHealth(d)
        label = _label(ah)
        for e in _storm(last_t - 600, last_t, label):
            ah._push(e)
        ah.snapshot(last_t)
        return d, label + "|fable"

    def test_the_restart_row_is_the_newest_row_when_the_previous_kernel_filed_in_this_boot_s_second(self):
        # the previous kernel drains under SIGTERM while still serving, and an open card's re-read filed a transition
        # at T0 + 0.7; the manager respawned at once and this kernel's _STARTED is T0 + 0.9. int() of that is T0,
        # BEFORE the row, and a seed there would sort under it: the tail would read the old kernel's thrashing as the
        # current state above the restart row while the head said unknown since the boot
        d, key = self._previous_kernel(T0 + 0.7)
        lines = []
        ah = sb.ApiHealth(d, boot_at=T0 + 0.9, log=lines.append)
        snap = ah.snapshot(T0 + 1.5)
        rows = snap["transitions"]
        self.assertEqual(rows[-1]["why"], sb.API_HEALTH_RESTART_WHY)
        self.assertEqual(ah.boot_stamp, T0 + 0.9, "the float boot, already at the millisecond")
        self.assertEqual(rows[-1]["t"], ah.boot_stamp, "the restart row at the float boot")
        self.assertEqual(snap["bootAt"], ah.boot_stamp, "served as bootAt")
        self.assertEqual(rows[-1]["t"], max(r["t"] for r in rows), "the newest row in the tail")
        self.assertAlmostEqual(rows[-2]["t"], T0 + 0.7, delta=1e-6)
        self.assertGreater(rows[-2]["t"], int(T0 + 0.9), "an int seed would sit under this row")
        b = snap["buckets"][key]
        self.assertEqual(b["stateSince"], rows[-1]["t"], "the head's since and the row: one number")
        self.assertEqual([ln for ln in lines if "not before this boot" in ln], [], "nothing to clamp: the boot is past the row")

    def test_a_boot_not_past_the_restored_tail_seeds_one_millisecond_past_it_and_says_so_once(self):
        # the previous kernel filed AFTER this one's start (the two overlapped, or the clock stepped back): the seed
        # moves one millisecond past that row and the log says so once. Judged at the millisecond the tail is kept
        # in: a boot anywhere inside the row's own millisecond truncates to it and is clamped; one in the next
        # millisecond truncates to that and stamps there on its own
        for boot, clamped in ((T0 + 0.65, True), (T0 + 0.7, True), (T0 + 0.7004, True), (T0 + 0.7009, True),
                              (T0 + 0.701, False), (T0 + 0.7015, False)):
            d, key = self._previous_kernel(T0 + 0.7)
            lines = []
            ah = sb.ApiHealth(d, boot_at=boot, log=lines.append)
            snap = ah.snapshot(T0 + 2)
            row, b = snap["transitions"][-1], snap["buckets"][key]
            self.assertEqual(row["why"], sb.API_HEALTH_RESTART_WHY, boot)
            self.assertAlmostEqual(row["t"], T0 + 0.701, delta=1e-6, msg=repr(boot))
            self.assertEqual(row["t"], max(r["t"] for r in snap["transitions"]), "the restart row stays the newest")
            self.assertEqual(b["stateSince"], row["t"], "one number: the head's since and the row")
            self.assertEqual(snap["bootAt"], row["t"], "and bootAt, clamp or not: the aggregator's own stamp")
            self.assertEqual(ah.boot_stamp, row["t"])
            said = [ln for ln in lines if "not before this boot" in ln]
            self.assertEqual(len(said), 1 if clamped else 0, (boot, lines))
            if clamped:
                self.assertIn("%.3f" % (T0 + 0.7), said[0])

    def test_the_clamp_judges_the_per_bucket_tails_too(self):
        # the global tail and each bucket's own tail are separate deques; a row in a bucket's tail alone (the global
        # one bounded it away) still moves the seed
        d, key = self._previous_kernel(T0 + 0.7)
        p = Path(d, sb.API_HEALTH_STATE_FILE)
        doc = json.loads(p.read_text())
        doc["transitions"] = []                                # the global tail empty, the bucket's tail intact
        p.write_text(json.dumps(doc))
        lines = []
        ah = sb.ApiHealth(d, boot_at=T0 + 0.7, log=lines.append)
        self.assertAlmostEqual(ah.boot_stamp, T0 + 0.701, delta=1e-6)
        self.assertEqual(len([ln for ln in lines if "not before this boot" in ln]), 1, lines)


class StateFileRows(unittest.TestCase):
    """A restored row the seed cannot stamp is skipped and counted, and never costs the other rows: the seed's max
    over the restored stamps must never meet a t that is not a finite number (a null t passed the old check, the
    max raised on it, and the outer guard would drop every bucket and transition the file held, with the file's
    other rows sound)."""

    def test_a_row_with_a_null_or_missing_t_is_skipped_and_the_rest_of_the_history_kept(self):
        d = tempfile.mkdtemp()
        ah = sb.ApiHealth(d)
        label = _label(ah)
        key = label + "|fable"
        for e in _storm(T0 - 600, T0, label):
            ah._push(e)
        ah.snapshot(T0)                                        # unknown -> thrashing at T0, persisted
        p = Path(d, sb.API_HEALTH_STATE_FILE)
        doc = json.loads(p.read_text())
        good = list(doc["transitions"])
        self.assertEqual([(r["from"], r["to"], r["t"]) for r in good], [("unknown", "thrashing", T0)])
        # a hand-edited file, or a row from a build that wrote t differently: null, missing, a string, a bool, NaN
        bad = [dict(good[0], t=None), {k: v for k, v in good[0].items() if k != "t"}, dict(good[0], t="%.3f" % (T0 + 1)),
               dict(good[0], t=True), dict(good[0], t=float("nan"))]
        doc["transitions"] = [bad[0], good[0], bad[1], bad[2]]
        doc["buckets"][key]["transitions"] = [bad[3], good[0], bad[4]]
        p.write_text(json.dumps(doc))
        lines = []
        ah2 = sb.ApiHealth(d, boot_at=T0 + 10, log=lines.append)
        snap = ah2.snapshot(T0 + 11)
        self.assertEqual([ln for ln in lines if "unreadable" in ln], [], "the file is readable; five ROWS are not")
        skipped = [ln for ln in lines if "malformed row(s) skipped" in ln]
        self.assertEqual(len(skipped), 1, lines)
        self.assertIn("5 malformed", skipped[0])
        self.assertEqual(ah2.boot_stamp, T0 + 10, "nothing to clamp: every row the seed could stamp is before the boot")
        self.assertEqual(snap["bootAt"], T0 + 10)
        self.assertEqual([(r["from"], r["to"], r["t"]) for r in snap["transitions"]],
                         [("unknown", "thrashing", T0), ("thrashing", "unknown", T0 + 10)],
                         "the sound row kept, the restart row filed after it")
        b = snap["buckets"][key]
        self.assertEqual((b["state"], b["stateSince"]), ("unknown", T0 + 10), "the bucket came back")
        self.assertEqual([r["t"] for r in b["transitions"]], [T0, T0 + 10], "its own tail too")
        for r in bad:
            self.assertFalse(sb.ApiHealth._row_ok(r), r)
        self.assertTrue(sb.ApiHealth._row_ok(good[0]))
        self.assertTrue(sb.ApiHealth._row_ok(dict(good[0], t=int(T0))), "an int stamp is a number")

    def test_an_int_t_past_float_range_is_skipped_as_one_row_and_the_rest_of_the_history_kept(self):
        """math.isfinite converts an int to float first and raises OverflowError past about 309 digits, and
        json.loads reads a 400-digit integer literal as such an int (1e400 reads as inf, which the finite check
        rejects on its own). The row is rejected, never raised on."""
        d = tempfile.mkdtemp()
        ah = sb.ApiHealth(d)
        label = _label(ah)
        key = label + "|fable"
        for e in _storm(T0 - 600, T0, label):
            ah._push(e)
        ah.snapshot(T0)                                        # unknown -> thrashing at T0, persisted
        p = Path(d, sb.API_HEALTH_STATE_FILE)
        doc = json.loads(p.read_text())
        good = list(doc["transitions"])
        self.assertEqual([(r["from"], r["to"], r["t"]) for r in good], [("unknown", "thrashing", T0)])
        huge = dict(good[0], t=10 ** 400)
        doc["transitions"] = [good[0], huge]
        p.write_text(json.dumps(doc))
        back = json.loads(p.read_text())["transitions"][1]["t"]
        self.assertIsInstance(back, int, "json reads the literal back as an int, not as inf")
        with self.assertRaises(OverflowError):
            float(back)
        lines = []
        ah2 = sb.ApiHealth(d, boot_at=T0 + 10, log=lines.append)
        snap = ah2.snapshot(T0 + 11)
        self.assertEqual([ln for ln in lines if "unreadable" in ln], [], "the file is readable; one ROW is not")
        skipped = [ln for ln in lines if "malformed row(s) skipped" in ln]
        self.assertEqual(len(skipped), 1, lines)
        self.assertIn("1 malformed", skipped[0])
        self.assertEqual(ah2.boot_stamp, T0 + 10, "nothing to clamp: the one row the seed could stamp is before the boot")
        self.assertEqual(snap["bootAt"], T0 + 10)
        self.assertEqual([(r["from"], r["to"], r["t"]) for r in snap["transitions"]],
                         [("unknown", "thrashing", T0), ("thrashing", "unknown", T0 + 10)],
                         "the sound row kept, the restart row filed after it")
        b = snap["buckets"][key]
        self.assertEqual((b["state"], b["stateSince"]), ("unknown", T0 + 10), "the bucket came back")
        self.assertEqual([r["t"] for r in b["transitions"]], [T0, T0 + 10], "its own tail too")
        self.assertFalse(sb.ApiHealth._row_ok(huge), "rejected as a row, no raise")
        self.assertFalse(sb.ApiHealth._row_ok(dict(good[0], t=-(10 ** 400))), "the negative side too")
        self.assertTrue(sb.ApiHealth._row_ok(dict(good[0], t=1e300)), "a large float the range holds is a number")


class Route(unittest.TestCase):
    """The route as the shell's fetch meets it: the real dispatcher, the credential the browser carries."""

    def setUp(self):
        self._saved = km._sdk
        self.be = _Backend()
        km._sdk = lambda: self.be

    def tearDown(self):
        km._sdk = self._saved

    def test_the_route_serves_every_field_the_section_reads(self):
        status, body = _serve_get("/api-health", {"X-Romp-Token": TOK})
        self.assertEqual(status, 200)
        out = json.loads(body)
        for k in ("asOf", "bootAt", "uptimeS", "config", "overall", "buckets", "transitions"):
            self.assertIn(k, out, k)
        self.assertIsInstance(out["config"]["windows"], list)
        self.assertIsInstance(out["bootAt"], float)
        self.assertEqual(out["bootAt"], round(out["bootAt"], 3), "to the millisecond, as every stamp in the payload")
        key = out["overall"]["worstBucket"]
        self.assertIn(key, out["buckets"])
        b = out["buckets"][key]
        for k in ("state", "stateSince", "why", "windows", "family", "auth"):
            self.assertIn(k, b, k)
        self.assertEqual(set(b["windows"]), {str(w) for w in out["config"]["windows"]})

    def test_the_cookie_alone_reads_it_with_no_origin_header_the_shell_s_fetch_shape(self):
        # a same-origin GET fetch sends the romp_token cookie and NO Origin header; _origin_ok accepts an absent
        # Origin, so this is exactly the credential the shell's fetch('/api-health') carries (kernel.py's own
        # /usage/fleet read goes the same way). The header form the CLI uses is not what the shell sends.
        status, body = _serve_get("/api-health", {"Cookie": "romp_token=" + TOK})
        self.assertEqual(status, 200, body[:200])
        self.assertIn("buckets", body)
        status, body = _serve_get("/api-health", {"Cookie": "romp_token=" + TOK,
                                                  "Origin": "http://evil.example", "Host": "127.0.0.1:%d" % km.PORT})
        self.assertEqual(status, 403, "a cross-site page's cookie is refused")
        self.assertIn("fetchDoc(h?'/remote/'+encodeURIComponent(h)+'/api-health':'/api-health')", JS)
        self.assertIn("fetch(u,{cache:'no-store'})", JS)
        self.assertIn("fetch('/usage/fleet',{cache:'no-store'})", km._LANDING_USAGE_JS, "the same shape as the shell's other read")

    def test_a_missing_backend_is_a_loud_503_that_the_section_shows_as_its_failure_line(self):
        km._sdk = lambda: None
        status, body = _serve_get("/api-health", {"Cookie": "romp_token=" + TOK})
        self.assertEqual(status, 503)
        self.assertIn("error", json.loads(body))
        self.assertIn("if(!r.ok){var tp=(typeof r.text==='function')?r.text():Promise.resolve('');", HIST, "a non-2xx is the failure, with its status")
        self.assertIn("return {error:'HTTP '+r.status+(t?", HIST)
        self.assertIn(".catch(function(e){return {error:String((e&&e.message)||e)};});}", HIST)
        self.assertIn("Could not read the API history'+(many?' of '+esc(name):'')+': '+esc((d&&d.error)||'no answer')", HIST)


class FrameUnchanged(unittest.TestCase):
    """The pushed frame carries no history, and the dedupe still sends nothing on an unchanged world."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._state, self._alive, self._send, self._sdk = km.jd.STATE, km._alive_sessions, km._send_to_app, km._sdk
        km.jd.STATE = Path(self.td.name)
        km._alive_sessions = lambda now, live_map: []
        self.sent = []
        km._send_to_app = lambda app, m: self.sent.append((app, m))
        self.be = _Backend()
        km._sdk = lambda: self.be
        km._APIH_LAST[0] = None

    def tearDown(self):
        km.jd.STATE, km._alive_sessions, km._send_to_app, km._sdk = self._state, self._alive, self._send, self._sdk
        km._APIH_LAST[0] = None
        self.td.cleanup()

    def test_the_frame_carries_no_history_and_an_unchanged_world_sends_once_across_a_read(self):
        f1 = km._api_health_frame(10, {})
        km._api_health_push(f1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(set(f1), {"type", "state", "cls", "reason", "text", "waiting", "retrying", "blocked",
                                   "since", "sessions", "seq", "hosts", "quiet", "errs", "host"}, "the documented keys, nothing added")
        for k in ("windows", "transitions", "buckets", "history", "overall", "bootAt"):
            self.assertNotIn(k, f1)
        # a hover reads the route in between (the read files a transition: the storm classifies)
        status, body = _serve_get("/api-health", {"Cookie": "romp_token=" + TOK})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["overall"]["state"], "thrashing", "the read observed the storm")
        f2 = km._api_health_frame(11, {})
        self.assertEqual(f1, f2, "the world the frame reads did not change")
        km._api_health_push(f2)
        self.assertEqual(len(self.sent), 1, "byte-identical: nothing sent")


class Docs(unittest.TestCase):
    def _section(self):
        doc = Path(DOCS, "reference.md").read_text()
        i = doc.index("### The bottom bar's indicator")
        return doc[i:doc.index("\n## ", i)]

    def test_the_reference_describes_the_section_from_the_payload_s_fields(self):
        sec = self._section()
        self.assertIn("**History**", sec)
        self.assertIn("`GET /api-health`", sec)
        # T301: the reading counts the failures over the longest window and draws `series`; the state machine's word
        # is read only through the plain phrasing, so `overall.state` is no longer what the section describes
        for k in ("`config.windows`", "`requests`", "`noStatus`", "`rateLimited`", "`serverErrors`", "`gaveUp`", "`series`",
                  "`transitions`", "`asOf`", "`bootAt`", "`hosts`", "`quiet`"):
            self.assertIn(k, sec, k)
        self.assertNotIn("`overall.state` with", sec, "the machine's word is not what the user reads")
        self.assertIn("`kernel restarted`", sec)
        self.assertIn("never the previous numbers", sec)
        self.assertIn("Nothing polls", sec)

    def test_the_reference_says_what_the_boot_stamp_is(self):
        doc = re.sub(r"\s+", " ", Path(DOCS, "reference.md").read_text())
        sec = re.sub(r"\s+", " ", self._section())
        self.assertIn("a bucket the boot seeded is `unknown` since `bootAt`: the boot time or, when an older kernel's "
                      "last row overlaps it, one millisecond past that row", sec)
        self.assertIn("`bootAt` is the boot's stamp in this signal: the kernel's start truncated to the millisecond", doc)
        self.assertIn("`/version`'s `started` is the whole-second boot time", doc)
        self.assertIn("the payload serves that stamp as `bootAt`, and the kernel log says when it was moved", doc)

    def test_the_guide_tells_the_user_what_the_hover_shows(self):
        guide = Path(DOCS, "guide.md").read_text()
        self.assertIn("the history under it", guide)
        self.assertIn("the last 15 minutes", guide)
        self.assertIn("A kernel restart shows as its own line there", guide)
        self.assertIn("every connected kernel", guide)

    def test_the_reference_names_the_three_failed_reads(self):
        self.assertIn("A read that fails (a non-2xx, no answer, or an answer without the signal's shape) shows one line "
                      "saying so in place of the rows, never the previous numbers.", re.sub(r"\s+", " ", self._section()),
                      "the three cases the script reports as its failure line")


class Script(unittest.TestCase):
    """Source pins over the cell's script for what a reader wants named and the browser module cannot see as such:
    the read's call sites, the no-timer rule, the paint gate, the tail's rules, the roles and the description. The
    behaviours themselves execute in tests/test_api_health_hover_browser.py."""

    def test_the_history_is_the_one_read_fired_by_the_show_the_open_from_hidden_and_a_frame_on_an_open_card(self):
        self.assertTrue(HIST, "the cell's script carries a History block")
        self.assertEqual(JS.count("fetch("), 1, "one read in the cell's script: the history's")
        self.assertIn("function load(fresh){var n=++histSeq,names=[''].concat(hostsOf(LAST)),by={};", HIST)
        self.assertIn("tip.style.display='block';el.setAttribute('aria-describedby','ah-summary');load(true);render();armAge();}", JS,
                      "show drops the last answer and reads")
        self.assertIn("var was=tip.style.display==='block';", JS)
        self.assertIn("window.__rompApiClose=close;back.onclick=close;try{tip.focus();}catch(e){}if(!was)load();}", JS,
                      "open reads when nothing was showing, behind the answer it has")
        self.assertIn("if(tip.style.display==='block'){if(typeof ev.clientX==='number')lastX=ev.clientX;anchor();return;}show(ev);});", JS,
                      "a pointer on a tip that focus shows re-anchors it and does not re-read")
        self.assertIn("if(tip.style.display!=='block')return;   // an open detail re-renders from the new frame, nothing else does\nload();", JS,
                      "a frame on an open card re-reads")

    def test_no_timers_the_newest_read_wins_and_the_answer_paints_through_the_held_gate(self):
        self.assertNotIn("setTimeout", JS)
        # the one timer is the read-age label's minute tick (T316 review: a pinned detail must not say 'now' for ten
        # minutes): it re-words one span and never reads anything; nothing polls the history
        self.assertEqual(JS.count("setInterval("), 1)
        self.assertIn("function ageTick(){var n=tip.querySelector('.ah-ago');if(n){var w=ageWords();if(w)n.textContent=w;}}", JS)
        self.assertNotIn("fetch", JS[JS.index("function ageTick"):JS.index("function disarmAge")], "the tick reads nothing")
        self.assertIn("window.addEventListener('focus',function(){winFocusEl=document.activeElement;requestAnimationFrame(function(){winFocusEl=null;});});", JS,
                      "the window-focus mark is cleared on the next animation frame, an event")
        # T301: one read per machine (this machine's document and every attached host's, each landing on its own), so one guard of each
        self.assertEqual(HIST.count("if(n!==histSeq)return;"), 1, "the read drops an answer a newer read superseded")
        self.assertEqual(HIST.count("if(tip.style.display!=='block')return;if(held){dirty=true;return;}render();"), 1,
                         "the answer repaints an open card only, and never under a held pointer")
        self.assertIn("names.forEach(function(h){fetchDoc(h?'/remote/'+encodeURIComponent(h)+'/api-health':'/api-health')", HIST, "one read per machine, each landing on its own")
        self.assertIn(".catch(function(e){return {error:String((e&&e.message)||e)};});}", HIST, "a rejected fetch is that machine's failure line, never an unhandled rejection")

    def test_a_failed_read_is_one_line_in_place_of_the_rows(self):
        self.assertIn("return (d&&d.buckets)?d:{error:'malformed answer'};", HIST, "an answer without the signal's shape is a failure too")
        # T301: per machine: the line stands where that machine's reading would, named when there are several
        self.assertIn("if(!d||d.error){h+='<div class=\"ah-line ah-err\">Could not read the API history'+(many?' of '+esc(name):'')+': '+esc((d&&d.error)||'no answer')+'</div>';return;}", HIST)
        self.assertIn("if(!HIST)return h+'<div class=\"rl-dots ah-wait\"><i></i><i></i><i></i></div></div>';", HIST,
                      "before the first answer: the loader's dots")

    def test_the_section_draws_stacked_bars_a_vertical_legend_and_an_age_in_words(self):
        # T316 (the user's design): one stacked histogram per machine (successes in the accent, 429 in the blocked red, 5xx
        # in the 5xx magenta, the other band for no-connection and other-status failures only when present), no peak text,
        # one ceiling label; the legend vertical, its class tokens in their inks (T340), the other line only when it applies;
        # the as-of stamp an age
        self.assertIn("var h='<div class=\"ru-tip-win ah-hist\"><div class=ru-tip-name><span>History</span>'+ago+'</div>';", HIST)
        self.assertIn("'<span class=\"ru-tip-reset ah-ago\">'+esc(ageWords())+'</span>'", HIST)
        self.assertIn("function ageWords(){return LANDED&&MERGE?'read '+MERGE.agoWords((Date.now()-LANDED)/1000):'';}", JS,
                      "the read's age: the time since this machine's document landed, one clock (review find: asOf is another host's clock)")
        self.assertIn("if(h==='')LANDED=Date.now();", HIST)
        self.assertNotIn("Date.now()/1000-loc.asOf", HIST, "never the browser's clock against the kernel's")
        self.assertIn("if(fresh){READINGS={};LANDED=null;}", HIST, "a fresh show drops the last hover's counts with its rows")
        self.assertIn("var rd=READINGS[host];if(rd&&rd.counts&&(rd.counts.none+rd.counts.other)>0)other=true;", HIST, "the other legend row follows the counted lines too")
        self.assertIn("other:[],older:true}", HIST, "an older kernel's series is marked and named")
        self.assertNotIn("peak '+mx", HIST, "no peak text")
        self.assertNotIn("hms(loc.asOf)", HIST)
        self.assertIn("var BAR_CLASSES=['ok','rateLimited','serverErrors','noStatus','other'];", HIST, "the stack order: successes, 429, 5xx, then the other classes")
        self.assertIn("bars+='<rect class=\"ah-seg ah-seg-'+c+'\" x=\"'+x.toFixed(1)+'\" y=\"'+(yb-hgt).toFixed(1)+'\" width=\"'+bw.toFixed(1)+'\" height=\"'+hgt.toFixed(1)+'\"></rect>';", HIST,
                      "one rect per class per bar, stacked bottom-up, every attribute quoted, the fill a class per theme")
        self.assertNotIn("style=\"fill:", HIST, "no inline fill: the light theme must be able to re-ink a segment")
        self.assertNotIn("<polyline", HIST, "no overlaid lines")
        self.assertIn("'<span class=ru-tip-gy style=\"top:'+(big?ty:ty/H*56).toFixed(0)+'px\">'+top+'</span><div class=ru-tip-gx>'+xlab+'</div></div>';}", HIST, "one ceiling label; the ticks under")
        # T340 (the user 2026-09-11): no swatches; the class token wears its ink and the explanation sits beside it as plain text
        self.assertIn("var LEGEND_ROWS=[['r429','429','rate limit: the API told us to slow down'],['r5xx','5xx','server error: the API itself failed'],['none','other','no connection, or another error']];", JS)
        self.assertIn("function legendHTML(other){var h='<div class=ah-legend>';LEGEND_ROWS.forEach(function(r){if(r[0]==='none'&&!other)return;", HIST, "the other line only when the range holds any")
        self.assertIn("h+='<div class=ah-lrow><span class=\"ah-lt ah-c-'+r[0]+'\">'+r[1]+'</span> <span>'+r[2]+'</span></div>';});return h+'</div>';}", HIST, "the class token in its ink, the explanation beside it, no swatch")
        self.assertNotIn("ah-lsw", JS, "the legend swatches are gone"); self.assertNotIn("ah-sw", JS, "…and the waiting rows' coloured squares with them")
        self.assertIn("if(!pinned)h+=legendHTML(other);", HIST, "the hover: the legend under the histograms, once")
        self.assertIn("if(pinned)h+=rangeHTML()+legendHTML(other);", HIST, "the detail: the legend under the range chips, where the eye starts (a short window folded the bottom one away)")
        self.assertEqual(HIST.count("legendHTML(other);"), 2, "drawn in exactly one of the two places")
        self.assertIn(".ah-legend{display:flex;flex-direction:column;align-items:flex-start;gap:3px;margin-top:7px}", km._landing(), "vertical, left-justified; no group opacity (T340 review: the tokens must stand at full strength)")
        self.assertIn("if(many)h+='<div class=\"ru-tip-row ah-gname\"><span class=ah-nm>'+esc(name)+'</span></div>';", HIST, "several machines: each histogram under its machine's name")
        self.assertIn("var RANGES={hour:{tier:'minute',per:1,label:'1 hour',s:3600},day:{tier:'fiveMin',per:3,label:'24 hours',s:86400},week:{tier:'hour',per:1,label:'7 days',s:604800}};", JS,
                      "the hover draws the day as quarter-hours; the detail offers the three ranges")
        self.assertIn("R=RANGES[pinned?range:'day'];", HIST, "the hover draws the day; the detail its chosen range")
        self.assertIn("if(pinned)h+=rangeHTML()+legendHTML(other);", HIST, "the range chips only in the detail")
        self.assertIn("else if(act.indexOf('range:')===0){range=act.slice(6);render();}", JS)
        self.assertNotIn("winRow", JS, "the per-window rows are gone")
        self.assertNotIn("worst of", JS, "and the bucket-count caveat with them")
        self.assertNotIn("kernel up", JS, "and the uptime caveat")
        self.assertNotIn("headWords", JS, "no summary head line: the per-machine lines do the work")
        self.assertEqual(sb._AH_COUNTED, ("ok", "429", "529", "5xx", "other"),
                         "requests excludes 'none', so requests plus noStatus counts every attempt once")
        self.assertIn("rows.forEach(function(r){h+=rowHTML(r,full);});h+='</div>';}\nh+=histHTML();\nif(full)h+=", JS,
                      "the section sits after the sessions waiting and before the footer, in hover and detail alike")
        self.assertNotIn("tmux session", JS, "T331: the terminal-coverage line is gone (the frame carries no count for it either)")

    def test_the_card_names_every_machine_with_its_counts_in_their_colours_and_the_window_once(self):
        # T316: no head sentence; one line per machine (the dot in its state, the kernel's own name, the counts as coloured
        # pieces); the window named once beside the title; this machine by the name its peers know
        self.assertIn("mg.machines.forEach(function(x){h+='<div class=\"ru-tip-row ah-mline\" data-host=\"'+esc(x.host)+'\"><i class=ah-dot data-dot='+esc(x.dot)+'></i><span class=ah-nm>'+esc(x.name)+'</span>'+partsHTML(x.parts||[])", JS)
        self.assertIn("function partsHTML(parts){return '<span class=ah-desc>'+parts.map(function(p){return '<span class=\"ah-c-'+esc(p.kind)+'\">'+esc(p.text)+'</span>';}).join(' \u00b7 ')+'</span>';}", HIST)
        self.assertIn("var win=MERGE?MERGE.windowWords(rd?rd.windowS:86400):'';", JS)
        self.assertIn("(win?'<span class=\"ru-tip-reset ah-win\">'+esc(win)+'</span>':'')", JS, "the window once, at the top")
        self.assertIn("function SELF(){return (LAST&&LAST.host)||'this machine';}", JS, "this kernel's own name, from its frame")
        self.assertNotIn("ah-head", JS.split("function html(m,full)")[1].split("function anchor()")[0], "no head row in the card")
        html = km._landing()
        # the inks (review find: chip colours as text sit under 4.5:1; the failure line's #ef6b6f / #B02A1C is the precedent),
        # each with its light twin; the bars' fills as classes with their light twins; the swatches keep the chip colours
        for dark, light in ((".ah-c-ok{color:var(--accent,#9cd2ff)}", "body.theme-light .ah-c-ok{color:#C2410C}"),
                            (".ah-c-r429{color:#ef6b6f}", "body.theme-light .ah-c-r429{color:#B02A1C}"),
                            (".ah-c-r5xx{color:#e879f9}", "body.theme-light .ah-c-r5xx{color:#86198F}"),
                            (".ah-c-none{color:#d9f99d}", "body.theme-light .ah-c-none{color:#4f46e5}"),   # T340: the other band's hue as ink
                            (".ah-seg-ok{fill:var(--accent,#9cd2ff)}", "body.theme-light .ah-seg-ok{fill:#C2410C}"),
                            (".ah-seg-serverErrors{fill:var(--st-5xx-bg,#c026d3)}", "body.theme-light .ah-seg-serverErrors{fill:#A21CAF}"),
                            (".ah-seg-noStatus,.ah-seg-other{fill:#d9f99d}", "body.theme-light .ah-seg-noStatus,body.theme-light .ah-seg-other{fill:#4f46e5}")):
            self.assertIn(dark, html, dark)
            self.assertIn(light, html, "the light twin: " + light)
        for rule in ("#ah-tip.ru-modal{width:min(720px,92vw)}", ".ah-bars.ah-big svg{height:110px}"):
            self.assertIn(rule, html)
        # the read's age re-ticks once a minute while the tip or the detail is open, the label alone; cleared on close
        self.assertIn("function armAge(){if(!ageTimer)ageTimer=setInterval(ageTick,60000);}", JS)
        self.assertIn("function disarmAge(){if(ageTimer){clearInterval(ageTimer);ageTimer=null;}}", JS)
        self.assertIn("load(true);render();armAge();}", JS, "armed on show")
        self.assertIn("render();back.classList.add('on');armAge();", JS, "and on open")
        self.assertIn("function hide(){tip.style.display='none';el.removeAttribute('aria-describedby');disarmAge();}", JS, "cleared on hide (close hides)")

    def test_the_tail_holds_four_rows_newest_first_in_plain_words_each_state_s_hold_and_a_restart_never_hidden(self):
        self.assertIn("var HIST_ROWS=4;", JS, "T301: a glance, not a log")
        self.assertIn(".sort(function(a,b){return b.t-a.t;})", HIST, "newest first")
        self.assertIn("var end=now,cur=true;for(var j=i-1;j>=0;j--)if(rows[j].bucket===r.bucket){end=rows[j].t;cur=false;break;}", HIST,
                      "a state holds until the SAME bucket's next change")
        self.assertIn("+dur(end-r.t)+(cur?' so far':'')+", HIST)
        self.assertNotIn("end===now", HIST, "'so far' is a flag, never a stamp comparison")
        self.assertIn("if(pre&&end>boot){end=boot;cur=false;}", HIST, "a hold from before the boot ends at the boot")
        self.assertIn("pre=hasBoot&&r.t<boot;", HIST)
        self.assertIn("if(restart)sawRestart=true;shown++;}", HIST, "any restart row above suppresses the divider")
        self.assertEqual(HIST.count("shown++"), 1, "shown counts transition rows only: the divider takes no slot")
        self.assertIn("var word=(multi?bname(d,r.bucket)+' ':'')+(STATE_WORD[r.to]||r.to)", HIST, "a bucket is named only when there are several; the state in plain words")
        self.assertIn("var STATE_WORD={thrashing:'rate-limit storm',degraded:'API failing',recovering:'recovering',healthy:'fine',unknown:'quiet'};", JS)
        self.assertIn("return dup?fam+' · '+(b.label||b.auth||key.split('|')[0]):fam;}", HIST, "two of one family are told apart by the login's label, else the auth label")

    def test_the_roles_follow_the_mode_and_the_cell_is_described_by_the_short_summary(self):
        self.assertIn("el.addEventListener('focus',function(){if(moving||skipFocus||winFocusEl===el||pinned||tip.style.display==='block')return;show(null);});", JS)
        self.assertNotIn("winFocus=true", JS, "the mark is an element, not a flag")
        self.assertIn("var desc=document.createElement('span');desc.id='ah-summary';desc.className='ah-vh';document.body.appendChild(desc);", JS)
        self.assertIn("tip.innerHTML=html(LAST,pinned);if(!pinned)anchor();fitAxisLabels(tip);desc.textContent=descText();", JS, "refreshed on every render")
        # T301: the description is the head's plain words, a failed read said as such, and the read in flight named
        self.assertIn("function descText(){var tail=' Press Enter to open it.';var mg=merged();var m0=mg.machines[0];", HIST, "this machine's own line")
        self.assertIn("var w=m0?(m0.parts||[]).map(function(p){return p.text;}).join(' \u00b7 '):'';", HIST, "its words alone: the counts, or the kernel's own words")
        self.assertIn("if(!w){w=DOTWORD[mg.dot]||'';if(w)w=w.charAt(0).toUpperCase()+w.slice(1);}", HIST, "a fine frame before its read: the dot's state word, so the description never lacks one")
        self.assertIn("var loc=HIST&&HIST[''];if(loc&&loc.error)return 'Could not read the API history: '+loc.error+'.'+tail;", HIST)
        self.assertIn("if(!HIST||(HIST['']&&HIST[''].pending))return 'API health: '+w+' Reading the details.'+tail;\nreturn 'API health: '+w+tail;}", HIST)
        self.assertNotIn("'unknown'", HIST.replace("unknown:'quiet'", ""), "the machine's word never reaches the description")
        self.assertNotIn("'aria-describedby','ah-tip'", JS, "the tip's whole text is never the description")
        self.assertIn("tip.setAttribute('role','tooltip');tip.setAttribute('aria-label','API health');tip.tabIndex=-1;", JS, "created as a tooltip")
        self.assertIn("tip.classList.remove('ru-modal');tip.setAttribute('role','tooltip');tip.removeAttribute('aria-modal');", JS, "show: tooltip")
        self.assertIn("tip.setAttribute('role','dialog');tip.setAttribute('aria-modal','true');el.removeAttribute('aria-describedby');", JS, "open: dialog")
        self.assertEqual(JS.count("'aria-modal'"), 2, "set by open, removed by show, nowhere else")
        self.assertIn("function hide(){tip.style.display='none';el.removeAttribute('aria-describedby');disarmAge();}", JS)
        self.assertIn("el.addEventListener('blur',function(){if(moving)return;if(!pinned)hide();});", JS)
        self.assertIn("skipFocus=true;try{(fb&&fb.focus?fb:el).focus();}catch(e){}skipFocus=false;}", JS,
                      "the close's refocus fires the cell's focus event; the flag covers that one call")


class Skin(unittest.TestCase):
    """The section's CSS in the served page: status hexes and never the accent, the quiet row styles, the clip, and
    no new font size. The two light-theme colours also execute in the browser module (the computed style)."""

    @classmethod
    def setUpClass(cls):
        cls.html = km._landing()

    def test_the_dot_wears_the_accent_when_fine_the_status_tokens_otherwise_and_the_rows_are_quiet(self):
        # T301 (the user 2026-09-10): the fine dot IS the romp accent; errors wear the blocked red, quiet the label gray;
        # every colour through a token with a fallback for a var-less harness
        self.assertIn("#rail-api[data-dot=fine] .ah-dot,.ah-dot[data-dot=fine]{background:var(--accent,#9cd2ff);opacity:1}", self.html)
        self.assertIn("#rail-api[data-dot=errors] .ah-dot,.ah-dot[data-dot=errors]{background:var(--st-blocked-bg,#e5484d);opacity:1}", self.html)
        self.assertIn("#rail-api[data-dot=quiet] .ah-dot,.ah-dot[data-dot=quiet]{background:var(--dim,#9aa4ad);opacity:.55}", self.html)
        self.assertNotIn("data-state=thrashing", self.html, "the machine's words are not colours any more")
        rules = re.findall(r"[^{}]*\.ah-err[^{}]*\{[^}]*\}", self.html)
        for rule in rules:
            self.assertNotIn("var(--accent", rule, "the failure line is never the accent: " + rule)
        self.assertIn(".ah-hword{opacity:.8}.ah-hsub{opacity:.55}.ah-boot .ah-hword{font-style:italic;opacity:.6}", self.html)
        self.assertIn(".ah-hname{margin-top:6px}.ah-err{color:#ef6b6f}.ah-wait{margin:5px 0 2px}", self.html)
        self.assertIn("body.theme-light .ah-err{color:#B02A1C}", self.html, "the light theme's error-text red")
        self.assertIn(".ah-vh{position:absolute;width:1px;height:1px;margin:-1px;padding:0;border:0;overflow:hidden;clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap}", self.html)
        self.assertIn(".rl-dots i{width:7px;height:7px;border-radius:50%;background:#9cd2ff;animation:rl-bnc", self.html, "the boot splash's dots stand in")

    def test_the_hover_measures_after_a_reset_is_capped_to_the_room_above_the_rail_and_adds_no_font_size(self):
        self.assertIn("tip.style.left='0px';tip.style.top='0px';tip.style.maxHeight=Math.max(0,r.top-14)+'px';\nvar w=tip.offsetWidth,h=tip.offsetHeight;", JS)
        self.assertIn("tip.style.left='';tip.style.top='';tip.style.maxHeight='';", JS, "the pinned card clears the cap")
        self.assertIn("#ah-tip{overflow:hidden;box-sizing:border-box}", self.html, "the hover clips at the cap")
        self.assertNotIn("font-size", HIST, "no new font size: the reason and as-of reuse .ru-tip-reset")


class Strip(unittest.TestCase):
    def test_the_strip_has_no_api_cell_to_mirror_and_the_follow_up_is_named(self):
        strip = Path(os.path.dirname(HERE), "ui", "webview", "strip.ts").read_text()
        self.assertNotIn("rail-api", strip, "nothing in strip.ts renders the API health cell yet")
        self.assertNotIn("apiHealth", strip)
        self.assertIn("the VS Code strip twin (strip.ts", Path(os.path.dirname(HERE), "kernel", "kernel.py").read_text(),
                      "the follow-up is named in the cell's header")


if __name__ == "__main__":
    unittest.main()
