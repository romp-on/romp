#!/usr/bin/env python3
"""The hive tray's per-spawn model/effort choice (the user 2026-08-13): an explicit choice
handed to spawn() outranks the remembered sdk-defaults seed for THAT session only — the seed
itself is untouched, so a one-off spawn never silently becomes everyone's default — and
set_spawn_defaults() is the deliberate 'make this my default' write to the same store the
statusline picks feed implicitly. Synthetic only: invented names, tmp state dirs."""
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the load — the module resolves its state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
# Hermetic HOME too: spawn() also reads the launch-defaults file (~/.config/romp/session-defaults,
# which OUTRANKS the remembered seed) — the developer machine's real file must not leak in here.
os.environ["HOME"] = tempfile.mkdtemp()
sb = SourceFileLoader("romp_sdk_backend_tray", os.path.join(BIN, "romp_sdk_backend.py")).load_module()


def _backend(d):
    return sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)


class SpawnOverrides(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.be = _backend(self.d)

    def test_explicit_choice_outranks_the_seed_for_this_session_only(self):
        sb.write_sdk_default(self.d, model="opus", effort="high")
        sid = self.be.spawn("web", self.d, model="fable", effort="max")
        reg = sb.read_reg(self.d, sid)
        self.assertEqual(reg["model"], "fable")
        self.assertEqual(reg["effort"], "max")
        # the SEED is untouched: the next plain spawn still starts from the remembered pick
        reg2 = sb.read_reg(self.d, self.be.spawn("api", self.d))
        self.assertEqual(reg2.get("model"), "opus")
        self.assertEqual(reg2["effort"], "high")

    def test_bogus_effort_override_falls_back_to_the_seed(self):
        sb.write_sdk_default(self.d, effort="low")
        sid = self.be.spawn("tests", self.d, effort="turbo")
        self.assertEqual(sb.read_reg(self.d, sid)["effort"], "low")

    def test_ultracode_is_a_valid_per_spawn_choice(self):
        # per-session by design — allowed as an explicit spawn choice, never written as a seed
        sid = self.be.spawn("web", self.d, effort="ultracode")
        self.assertEqual(sb.read_reg(self.d, sid)["effort"], "ultracode")
        self.assertNotEqual(sb.read_sdk_defaults(sb.Path(self.d)).get("effort"), "ultracode")


class SetSpawnDefaults(unittest.TestCase):
    def test_writes_the_shared_store_and_merges_per_field(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        be.set_spawn_defaults(model="fable", effort="max")
        got = sb.read_sdk_defaults(sb.Path(d))
        self.assertEqual((got.get("model"), got.get("effort")), ("fable", "max"))
        # None leaves a field alone: a model-only update keeps the remembered effort
        be.set_spawn_defaults(model="haiku")
        got = sb.read_sdk_defaults(sb.Path(d))
        self.assertEqual((got.get("model"), got.get("effort")), ("haiku", "max"))
        # …and the next spawn seeds from it
        reg = sb.read_reg(d, be.spawn("web", d))
        self.assertEqual((reg.get("model"), reg["effort"]), ("haiku", "max"))


if __name__ == "__main__":
    unittest.main()
