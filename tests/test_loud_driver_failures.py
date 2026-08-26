#!/usr/bin/env python3
"""No judge driver may fail silently (the user 2026-08-26, T111 — the fail-loudly house rule
applied to the pattern the T110 report exposed: one poisoned goal killed a whole store's distills
with zero calls and zero errors, undiagnosable from the log). Every per-session swallow leg in the
judge drivers now files a `pass-crash` judge-error row: the five fleet loops verbatim
(planner/grouper/consolidator/closer/unblocker, matching the distiller's T110 shape), the index
tier's captioner/archiver legs (their designed fallbacks preserved — a fallback that hides its
trigger is the same black hole one tier down), and the courier's two sequential swallows (the
per-session parse skip and the courier-link attach). Behavior is unchanged beyond logging.
SYNTHETIC fixtures only; private synthetic sids."""
import os
import re
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from inspect import getsource
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = SourceFileLoader("romp_judge_loudfail", os.path.join(BIN, "romp-judge")).load_module()

NOW = 1_787_800_000
SID = "a55f0001-1111-4222-8333-000000000001"    # private synthetic sid — never the shared placeholder


class RepresentativeCrashRow(unittest.TestCase):
    """One driver pinned end to end: a _plan_session that dies files ('planner', sid, 'pass-crash')
    with the reason in the note — and the driver itself neither raises nor changes its return."""

    def test_a_poisoned_plan_session_logs_and_the_pass_survives(self):
        saved = (jd._plan_session, jd.discover, jd._log_judge_error)
        rows = []
        try:
            jd.discover = lambda now, window=None, forks=True: [(SID, Path("/dev/null"), SID, "web")]
            jd._plan_session = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("poisoned goal"))
            jd._log_judge_error = lambda judge, fsid, err, note=None, goal=None, seg=None: \
                rows.append((judge, fsid, err, note))
            placed = jd.run_plan(now=NOW)
        finally:
            jd._plan_session, jd.discover, jd._log_judge_error = saved
        self.assertEqual(placed, 0, "the pass survives the crash — behavior unchanged beyond logging")
        self.assertEqual(len(rows), 1)
        judge, fsid, err, note = rows[0]
        self.assertEqual((judge, fsid, err), ("planner", SID, "pass-crash"))
        self.assertIn("poisoned goal", note or "")


class SourceSweep(unittest.TestCase):
    """The pattern itself is pinned at the source level, so a future driver written with the old
    idiom fails here, not in production: no `for fut in as_completed(futs)` loop may carry a bare
    `except Exception: pass` (or a bare-continue), and the courier's two sequential swallows carry
    their named rows."""

    def test_no_fleet_loop_swallows_bare(self):
        lines = getsource(jd).splitlines()
        loops = [i for i, ln in enumerate(lines) if "as_completed(futs)" in ln]
        self.assertGreaterEqual(len(loops), 7, "the driver loops are all visible to the sweep")
        for i in loops:
            for j in range(i, min(i + 12, len(lines))):
                if re.match(r"^\s+except Exception\b.*:\s*$", lines[j]):
                    body = lines[j + 1].strip()
                    self.assertNotIn(body, ("pass", "continue"),
                                     "a driver loop at source line ~%d swallows bare — every "
                                     "swallow leg files a judge-error row (T111)" % (j + 1))

    def test_the_courier_swallows_carry_their_rows(self):
        src = getsource(jd)
        self.assertIn('"pass-crash", note="parse: %r"', src,
                      "the per-session parse skip names its reason")
        self.assertIn('"pass-crash", note="link-attach: %r"', src,
                      "the courier-link attach names its reason")

    def test_the_index_legs_keep_their_fallbacks_and_add_the_reason(self):
        src = getsource(jd)
        self.assertIn('cap, cap_paused = "", True', src, "the captioner fallback is preserved")
        i = src.index('cap, cap_paused = "", True')
        self.assertIn('_log_judge_error("captioner"', src[i:i + 300],
                      "…and the crash reason no longer vanishes")
        self.assertIn('_log_judge_error("archiver"', src)


if __name__ == "__main__":
    unittest.main()
