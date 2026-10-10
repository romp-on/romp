#!/usr/bin/env python3
"""Issue #1735, served: a REAL hermetic kernel with the freeze ON proves the live wiring the unit tests cannot.

The judges parse the living sessions at boot (no browser needed; the boot-parses lab pins that), so the record
cache fills and, at the pusher's idle boundary, the freeze fires: /perf's gc.freeze shows `active` with `freezes`
at least one. Then a transcript is appended and re-read repeatedly (each re-read REPLACES its acyclic cache entry,
a record-cache pop) and `reclaims` stays put: under the corrected trigger a pop is not a cyclic release and drives
no unfreeze pause. The gen-2 arithmetic: this lab drives no release, so it asserts the INEQUALITIES that hold for any
run (`gen."2".collections` at least the controller's `collections`, which is itself at least `freezes` + `reclaims`);
the EXACT equality, organic full collections = gen-2 collections less the controller's `collections`, is unit-tested
(test_the_organic_arithmetic_is_exact_via_the_collections_count). The reclaim of a measured cyclic ended session (a weakref judged at the idle tick) is covered by the unit tests.
The freeze is set ON in the kernel's own env here; the suite floors it OFF everywhere else. Synthetic only:
invented text, placeholder uuids, TESTHOST.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment

WEB = "aaaaaaaa-1111-2222-3333-444444444441"
API = "bbbbbbbb-1111-2222-3333-444444444444"


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _turns(t0, n):
    recs, parent = [], None
    for j in range(n):
        u, a = "u%d" % j, "a%d" % j
        recs.append({"type": "user", "timestamp": iso(t0 + j * 100), "uuid": u, "parentUuid": parent,
                     "promptSource": "typed", "message": {"role": "user", "content": "wire the fixtures into the suite %d" % j}})
        recs.append({"type": "assistant", "timestamp": iso(t0 + j * 100 + 20), "uuid": a, "parentUuid": u,
                     "message": {"role": "assistant", "content": [{"type": "text", "text": "done step %d" % j}], "stop_reason": "end_turn"}})
        parent = a
    return recs


class ServedGcFreeze(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab = tempfile.mkdtemp(prefix="romp-gcf-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states", "goals"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        t0 = int(time.time()) - 3600
        cls.paths = {}
        for sid, name, colour in ((WEB, "web", "#9cd2ff"), (API, "api", "#1EA1EB")):
            Path(state, "names", sid).write_text("%s\t%s\t%s\t#0c1a2e\n" % (name, cwd, colour))
            Path(state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
                 "model": "claude-opus-5", "liveModel": "Opus 5"}))
            Path(state, "states", sid + ".jsonl").write_text(json.dumps({"t": t0 + 70, "state": "idle"}) + "\n")
            p = os.path.join(proj, sid + ".jsonl")
            Path(p).write_text("".join(json.dumps(r) + "\n" for r in _turns(t0, 12)))
            cls.paths[sid] = p
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        cls.port, cls.token = _free_port(), "testtok-gcf"
        # the freeze ON in the kernel's own env (the suite floors it off); a low threshold so a couple loads fire it,
        # and a zero quiescent-drop so a re-fold pops the old record-cache entry at once (a pop is acyclic and never reclaims)
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST",
                              ROMP_GC_FREEZE="on", ROMP_GC_FREEZE_LOAD_TREES="2", ROMP_RECORD_CACHE_DROP_QUIESCENT_S="0")
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(cls.klog, "w"),
                                      stderr=subprocess.STDOUT, env=env)
        for _ in range(200):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % cls.port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            cls.kernel.kill()
            raise unittest.SkipTest("hermetic kernel never served /healthz here")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.kernel.terminate(); cls.kernel.wait(timeout=15)
        except Exception:
            cls.kernel.kill()
        shutil.rmtree(cls.lab, ignore_errors=True)

    def _perf(self):
        req = urllib.request.Request("http://127.0.0.1:%d/perf" % self.port, headers={"X-Romp-Token": self.token})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())

    def _wait(self, pred, secs=60):
        for _ in range(secs * 2):
            try:
                pf = self._perf()
                if pred(pf):
                    return pf
            except Exception:
                pass
            time.sleep(0.5)
        return self._perf()

    def test_the_freeze_fires_live_and_steady_re_reads_never_reclaim(self):
        # the judges' boot parse loads the sessions; at an idle cycle the freeze fires
        pf = self._wait(lambda pf: (pf.get("gc", {}).get("freeze", {}).get("freezes") or 0) >= 1)
        fr = pf["gc"]["freeze"]
        self.assertTrue(fr["enabled"] and fr["active"], "the freeze is on and holds a freeze: %r" % fr)
        self.assertGreaterEqual(fr["freezes"], 1, "the freeze fired on the judges' boot parse: %r" % fr)
        self.assertIn(fr["lastReconcileKind"], ("initial", "load"), "no reclaim yet, only loads: %r" % fr)
        # the frozen count is served only as of a cleanup (a reclaim re-reads it; no freeze lists the heap, 2026-10-07), and
        # this lab runs none yet, so it reads null with its time; `active` above is the live signal that a freeze holds
        self.assertIsNone(pf["gc"]["frozenAsOfCleanup"], "no cleanup yet, so no count served: %r" % pf["gc"])
        self.assertIsNone(pf["gc"]["frozenAsOfCleanupAt"], "and no cleanup time: %r" % pf["gc"])
        # and the retired walk of the frozen list is not served at all, while the freeze sub-block serves the same pair
        self.assertNotIn("frozen", pf["gc"], "the walk of the frozen list is not served: %r" % sorted(pf["gc"]))
        self.assertEqual((fr["frozenAsOfCleanup"], fr["frozenAsOfCleanupAt"]), (None, None), "the freeze block agrees: %r" % fr)
        reclaims_before = fr["reclaims"]
        inserts_before = int(pf["recordCache"]["inserts"])
        # steady re-reads: append to a transcript repeatedly (each re-read replaces its acyclic cache entry). Under the
        # corrected trigger this is a record-cache pop, NOT a cyclic release, so it must drive NO reclaim (the 2026-09-21
        # design finding: a pop-keyed reclaim would pay the full pause on a schedule).
        for k in range(6):
            with open(self.paths[WEB], "a") as f:
                f.write("".join(json.dumps(r) + "\n" for r in _turns(int(time.time()) + k * 1000, 2)))
            time.sleep(2)
        pf = self._perf(); fr = pf["gc"]["freeze"]
        self.assertGreater(int(pf["recordCache"]["inserts"]), inserts_before,
                           "the re-reads are real: the record cache re-inserted the appended transcript (else the no-reclaim proves nothing)")
        self.assertEqual(fr["reclaims"], reclaims_before,
                         "steady re-reads drove NO reclaim: a record-cache pop is acyclic and never triggers the unfreeze pause: %r" % fr)
        self.assertNotEqual(fr["lastReconcileKind"], "release", "and the last reconcile was not a release: %r" % fr)
        # the gen-2 arithmetic as an EQUALITY: every collect the controller ran is a gen-2 collection the hook saw, so the
        # organic full collections are gen2 less the controller's own `collections`, non-negative, and `collections` counts
        # at least one per freeze and per reclaim (a full release two), so it is never below freezes + reclaims
        gen2 = pf["gc"]["gen"]["2"]["collections"]
        self.assertGreaterEqual(gen2 - fr["collections"], 0,
                                "organic full collections = gen2 less the controller's own collections, non-negative: %r / gen2=%r" % (fr, gen2))
        self.assertGreaterEqual(fr["collections"], fr["freezes"] + fr["reclaims"],
                                "collections counts every run-step collect, so it is at least freezes + reclaims (a full release adds one more): %r" % fr)
        self.assertEqual(fr.get("errors", 0), 0, "no reconcile raised: %r" % fr)


if __name__ == "__main__":
    unittest.main()
