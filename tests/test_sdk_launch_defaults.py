#!/usr/bin/env python3
"""The launch-defaults file (~/.config/romp/session-defaults) governs EVERY spawn path, not just
the tmux launcher (the user 2026-08-13: one answer for what a new session launches as — and then
set PERM_MODE=auto and watched a board-spawned SDK session launch asking for permissions anyway,
because SdkBackend.spawn read only its remembered last-pick seed). Precedence: explicit per-spawn
choice > the file > the remembered seed > the hardcoded floor. Validation mirrors bin/romp's exact
rules so the two readers can never accept different values. Synthetic only: tmp HOME/state dirs."""
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
sb = SourceFileLoader("romp_sdk_backend_launchdef", os.path.join(BIN, "romp_sdk_backend.py")).load_module()


def _backend(d):
    return sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)


class LaunchDefaultsFile(unittest.TestCase):
    def setUp(self):
        self._home = os.environ.get("HOME")
        self.home = tempfile.mkdtemp()
        os.environ["HOME"] = self.home
        self.d = tempfile.mkdtemp()
        self.be = _backend(self.d)

    def tearDown(self):
        if self._home is not None:
            os.environ["HOME"] = self._home

    def _write(self, text):
        cfg = os.path.join(self.home, ".config", "romp")
        os.makedirs(cfg, exist_ok=True)
        with open(os.path.join(cfg, "session-defaults"), "w") as f:
            f.write(text)

    def test_absent_file_reads_empty_and_spawn_behaves_as_before(self):
        self.assertEqual(sb.read_session_defaults(), {})
        reg = sb.read_reg(self.d, self.be.spawn("web", self.d))
        self.assertEqual(reg["mode"], "acceptEdits")
        self.assertEqual(reg["effort"], sb.DEFAULT_EFFORT)

    def test_the_file_seeds_mode_model_and_effort(self):
        self._write("MODEL=fable\nEFFORT=max\nPERM_MODE=auto\n")
        reg = sb.read_reg(self.d, self.be.spawn("web", self.d))
        self.assertEqual(reg["mode"], "auto")
        self.assertEqual(reg["model"], "fable")
        self.assertEqual(reg["effort"], "max")

    def test_the_file_outranks_the_remembered_seed(self):
        sb.write_sdk_default(self.d, model="opus", effort="low", mode="acceptEdits")
        self._write("MODEL=fable\nEFFORT=max\nPERM_MODE=auto\n")
        reg = sb.read_reg(self.d, self.be.spawn("web", self.d))
        self.assertEqual((reg["model"], reg["effort"], reg["mode"]), ("fable", "max", "auto"))

    def test_an_explicit_spawn_choice_outranks_the_file(self):
        self._write("MODEL=fable\nEFFORT=max\n")
        reg = sb.read_reg(self.d, self.be.spawn("web", self.d, model="haiku", effort="low"))
        self.assertEqual((reg["model"], reg["effort"]), ("haiku", "low"))

    def test_validation_mirrors_the_launcher_never_sources(self):
        # bash-parity: values are NOT stripped, so a trailing space fails exactly as it does in
        # bin/romp's regex check; junk lines and unknown keys are skipped, never executed
        self._write("PERM_MODE=auto \nMODEL=$(rm -rf /)\nEFFORT=turbo\nGARBAGE\n# comment\n")
        self.assertEqual(sb.read_session_defaults(), {})
        self._write("PERM_MODE=dontAsk\nEFFORT=ultracode\n")
        self.assertEqual(sb.read_session_defaults(), {"mode": "dontAsk"},
                         "ultracode is per-session only — the file cannot set it, matching bin/romp")


if __name__ == "__main__":
    unittest.main()
