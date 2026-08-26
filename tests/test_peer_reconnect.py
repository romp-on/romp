#!/usr/bin/env python3
"""Kernel-to-kernel reconnect is AUTOMATIC after restarts (the user 2026-08-24): attachment is a
persisted intent — created on attach/check-in, ended only by an explicit detach — and while it
stands, both sides keep re-dialing with zero user effort.

The live bug this pins against (caught in the act on a deploy-heavy day): the check-in handshake
was keyed "once per ssh incarnation" alone, so a HUB kernel restart — the mobile's ssh survives,
sshd holds the reverse forward — left the freshly-booted hub without its peer row and the mobile
never re-announcing: the reverse listener stood while remotes.json sat empty and the panel said
disconnected until a manual re-dial minted a new ssh pid. _handshake_due now keys on the hub's
kernel INCARNATION too (its /version pid, polled every pass — the restart is the event) plus a slow
idempotent refresh floor that heals any row loss no event announces.

Synthetic only — hermetic temp STATE, placeholder hosts."""
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
km = SourceFileLoader("romp_kernel_reconnect", os.path.join(BIN, "romp-kernel")).load_module()


class HandshakeDue(unittest.TestCase):
    """The pure re-announce rule, executed."""

    def test_fires_on_a_new_ssh_incarnation(self):
        self.assertTrue(km._handshake_due({}, 111, 1000.0), "never handshaken → due")
        r = {"_handshook": {"ssh": 111, "hub": 9, "at": 1000.0}}
        self.assertTrue(km._handshake_due(r, 222, 1001.0), "the tunnel respawned → due")

    def test_fires_when_the_hub_kernel_restarts(self):
        # the mobile's ssh SURVIVES a hub kernel restart (sshd holds it) — the hub's /version pid is
        # the restart event this rule keys on, so the hub re-learns us within one pass
        r = {"_handshook": {"ssh": 111, "hub": 9, "at": 1000.0}, "hub_pid": 10}
        self.assertTrue(km._handshake_due(r, 111, 1001.0))

    def test_quiet_while_the_same_incarnations_stand(self):
        r = {"_handshook": {"ssh": 111, "hub": 9, "at": 1000.0}, "hub_pid": 9}
        self.assertFalse(km._handshake_due(r, 111, 1000.0 + km.CHECKIN_REFRESH_S - 1))

    def test_the_slow_refresh_floor_heals_unannounced_row_loss(self):
        # a hub that lost the row WITHOUT restarting emits no event this side can see — the bounded
        # idempotent refresh is the healer (never a hot loop: one handshake per CHECKIN_REFRESH_S)
        r = {"_handshook": {"ssh": 111, "hub": 9, "at": 1000.0}, "hub_pid": 9}
        self.assertTrue(km._handshake_due(r, 111, 1000.0 + km.CHECKIN_REFRESH_S))

    def test_a_legacy_pid_keyed_stamp_reads_as_due(self):
        # pre-2026-08-24 rows carried a bare int — the shape mismatch re-announces once, then re-keys
        self.assertTrue(km._handshake_due({"_handshook": 111}, 111, 1000.0))


class PersistedIntent(unittest.TestCase):
    """Attach intent survives a kernel restart and ends ONLY on explicit detach."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._saved = (km.REMOTES_FILE, dict(km._remotes))
        km.REMOTES_FILE = Path(self.td.name) / "remotes.json"
        km._remotes.clear()

    def tearDown(self):
        km.REMOTES_FILE = self._saved[0]
        km._remotes.clear(); km._remotes.update(self._saved[1])
        self.td.cleanup()

    def test_a_restart_restores_the_row_and_dials_immediately(self):
        km._remotes["alpha"] = {"host": "alpha", "proc": None, "status": "up", "fails": 7,
                                "next_try": 99999.0, "token": "tok", "kernel_port": 29855,
                                "local_port": 50001, "bus_port": 50002, "sids": ["s1"]}
        km._remotes_save()
        km._remotes.clear()
        km._remotes_load()                      # the boot path (the restart event's own re-arm)
        r = km._remotes["alpha"]
        self.assertEqual(r["status"], "down")
        self.assertEqual((r["fails"], r["next_try"]), (0, 0),
                         "a fresh boot dials immediately rather than resuming a long backoff")

    def test_a_checkin_peer_row_survives_the_hub_restart(self):
        # the hub side of the same bug: the mobile's row must come back on boot, so the standing
        # reverse forward reads up within one pass with no handshake needed at all
        ans, code = km.checkin_apply({"host": "mobile1", "kernelPort": 29855, "busPort": 25302,
                                      "token": "mtok"})
        self.assertEqual(code, 200)
        km._remotes.clear()
        km._remotes_load()
        self.assertTrue(km._remotes["mobile1"].get("checkin_peer"),
                        "the persisted intent includes checked-in peers")

    def test_detach_is_the_one_end_of_intent(self):
        km._remotes["alpha"] = {"host": "alpha", "proc": None, "status": "up", "token": "tok",
                                "kernel_port": 29855, "local_port": 50001, "bus_port": 50002,
                                "sids": [], "trust": "directed"}
        km._remotes_save()
        km.detach_remote("alpha")
        km._remotes.clear()
        km._remotes_load()
        self.assertNotIn("alpha", km._remotes, "after detach, no boot ever re-dials it")

    def test_the_backoff_never_answers_stop(self):
        # the ladder always schedules a next attempt — 15s doubling to the 5-min ceiling, relaxing
        # to 15 min once the outage is clearly not a blip; there is no give-up state
        waits = [km._tunnel_backoff(n) for n in range(0, 16)]
        self.assertEqual(waits[0], km.TUNNEL_BACKOFF_BASE)
        self.assertTrue(all(w > 0 for w in waits))
        self.assertEqual(max(waits), km.TUNNEL_BACKOFF_LONG)


if __name__ == "__main__":
    unittest.main()
