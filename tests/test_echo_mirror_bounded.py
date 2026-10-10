"""The echo mirror (reg['echoes']) is bounded to what a restart needs (2026-10-09).

Every registry write re-reads and rewrites the whole file under the backend's one registry lock, so a large file makes
every hold long. The echo list was most of the largest files. The mirror now keeps pending echoes up to a bounded age,
dropped (never-delivered) ones up to a longer one, a few landed ones, and fits a byte budget by dropping the oldest,
never a recent pending echo. These tests use synthetic sessions only.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_CLI_SCOPE"] = "0"
if importlib.util.find_spec("claude_agent_sdk") is None:
    _tag = "python%d.%d" % sys.version_info[:2]
    for _sp in sorted(Path(os.path.expanduser("~/.local/state/romp/sdkvenv/lib")).glob(_tag + "/site-packages")):
        sys.path.insert(0, str(_sp))
sb = load_source("romp_sdk_backend_echo_mirror", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "6a2c9e10-2222-4333-8444-0000000000d1"     # private synthetic sid


def _echo(key, t, text, **flags):
    atom = {"type": "user", "uuid": key, "session_id": SID, "t": t, "parentUuid": None, "author": "human",
            "_echo_text": text, "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
    atom.update(flags)
    return atom


class EchoMirrorBounded(unittest.TestCase):

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        Path(self.d, "session-hosts").write_text("off")
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        sb.write_reg(Path(self.d), SID, {"sid": SID, "name": "web", "cwd": self.d, "alive": True, "lastSid": SID})
        self.now = time.time()

    def _stash(self, atoms):
        with self.be._live_lock:
            d = self.be._live.setdefault(SID, {})
            for a in atoms:
                d[a["uuid"]] = a

    def _reg_bytes(self):
        return os.path.getsize(sb._reg_path(Path(self.d), SID))

    def test_thousands_of_landed_echoes_write_a_bounded_registry_and_a_restart_reseeds_every_pending_echo(self):
        filler = "a synthetic message body " * 80                       # about 2 KB per echo
        landed = [_echo("echo:landed-%04d" % i, int(self.now) - 3000 + i, filler, _landed=True) for i in range(3000)]
        pending = [_echo("echo:pending-%d" % i, int(self.now) - 60 * i, "a synthetic send in flight %d" % i)
                   for i in range(4)]
        dropped = [_echo("echo:dropped-%d" % i, int(self.now) - 600, "a synthetic lost send %d" % i, dropped=True)
                   for i in range(2)]
        self._stash(landed + pending + dropped)
        unbounded = len(json.dumps([{"text": a["_echo_text"], "landed": True} for a in landed]))
        self.assertGreater(unbounded, 5_000_000, "the synthetic session would write megabytes unbounded")
        self.be._persist_echoes(SID)
        self.assertLess(self._reg_bytes(), sb.ECHO_MIRROR_MAX_BYTES + 16 * 1024,
                        "the registry stays under the mirror's budget plus the rest of the record")
        reg = sb.read_reg(Path(self.d), SID)
        kinds = [("landed" if e.get("landed") else "dropped" if e.get("dropped") else "pending") for e in reg["echoes"]]
        self.assertEqual(kinds.count("landed"), sb.ECHO_MIRROR_LANDED_KEEP)
        self.assertEqual(kinds.count("pending"), 4)
        self.assertEqual(kinds.count("dropped"), 2)

        # the next kernel: a fresh backend over the same state reseeds from the registry
        be2 = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        be2._reseed_echoes([reg])
        live = be2._live.get(SID) or {}
        for a in pending:
            self.assertIn(a["uuid"], live, "every pending echo is reseeded after the restart")
        for a in dropped:
            self.assertTrue(live.get(a["uuid"], {}).get("dropped"), "a never-delivered record survives the restart")

    def test_recent_pending_echoes_are_kept_even_over_the_budget(self):
        big = "x" * 4096
        pending = [_echo("echo:p-%03d" % i, int(self.now) - i, big) for i in range(100)]    # about 410 KB, all recent
        self._stash(pending)
        self.be._persist_echoes(SID)
        self.assertEqual(len(sb.read_reg(Path(self.d), SID)["echoes"]), 100,
                         "a send that may still be in flight is never dropped for size")

    def test_a_small_mirror_keeps_old_echoes_and_a_large_one_sheds_its_oldest(self):
        """No age bound: an old stale or never-delivered echo still rides a small mirror (the restart contract). Over the
        budget, the oldest go first."""
        old = [_echo("echo:old-stale", 5, "three day old words", stale=True, dropped=True),
               _echo("echo:old-pending", 2000, "an old send")]
        self._stash(old)
        self.be._persist_echoes(SID)
        self.assertEqual(sorted(e.get("uuid") for e in sb.read_reg(Path(self.d), SID)["echoes"]),
                         ["echo:old-pending", "echo:old-stale"])
        big = "y" * 8192
        old_big = [_echo("echo:big-%03d" % i, int(self.now) - 7200 - i, big) for i in range(60)]    # about 490 KB, 2 h old
        self._stash(old_big)
        self.be._persist_echoes(SID)
        kept = [e.get("uuid") for e in sb.read_reg(Path(self.d), SID)["echoes"]]
        self.assertLess(self._reg_bytes(), sb.ECHO_MIRROR_MAX_BYTES + 16 * 1024)
        self.assertIn("echo:big-000", kept, "the newest of the old sends stay")
        self.assertNotIn("echo:big-059", kept, "the oldest go first")

    def test_never_delivered_echoes_are_kept_over_the_budget_and_every_cut_is_logged(self):
        """Review 2 of the mirror bound: over the budget the cap cut echoes already flagged never delivered (any age),
        so a restart could no longer offer them back, and said nothing. They are always kept now, and a cut is logged
        with its count."""
        logs = []
        self.be._log_cb = logs.append
        self.be._log = lambda msg, *a, **k: logs.append(msg)
        big = "y" * 8192
        lost = [_echo("echo:lost-%03d" % i, 100 + i, big, dropped=True) for i in range(20)]           # ancient, flagged
        old = [_echo("echo:old-%03d" % i, int(self.now) - 7200 - i, big) for i in range(40)]          # 2 h, pending
        self._stash(lost + old)
        self.be._persist_echoes(SID)
        kept = {e.get("uuid") for e in sb.read_reg(Path(self.d), SID)["echoes"]}
        for a in lost:
            self.assertIn(a["uuid"], kept, "a never-delivered echo was cut for size")
        cut = [l for l in logs if "over the" in str(l) and "byte budget not written" in str(l)]
        self.assertEqual(len(cut), 1, logs)
        n_cut = 40 - len([u for u in kept if u.startswith("echo:old-")])
        self.assertGreater(n_cut, 0)
        self.assertIn(" %d echo(es)" % n_cut, cut[0])

    def test_the_selector_keeps_order_and_never_drops_a_fresh_send(self):
        now = 1_800_000_000.0
        entries = [{"t": now - 10, "text": "y" * 300_000}, {"t": now - 7200, "text": "z" * 300_000}]
        out = sb.echo_mirror_select(entries, now)
        self.assertEqual([e["t"] for e in out], [now - 10], "over budget, the older non-fresh entry goes first")


if __name__ == "__main__":
    unittest.main()
