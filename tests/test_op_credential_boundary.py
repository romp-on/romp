#!/usr/bin/env python3
"""Where op's own credential may and may not go (2026-09-05), pinned at the source across the four
programs that spawn children: the kernel (claims first, scrubs the tmux server, strips revives), the
manager (a scrubbed tmux server), the launcher (scrubs the server's globals before a pane exists), and the
door (the request's auth decides which credential names are reserved). Synthetic throughout."""
import os
import re
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)


def _read(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


class KernelBoundary(unittest.TestCase):
    def test_the_claim_is_the_kernels_first_act_and_scrubs_the_tmux_server(self):
        src = _read("kernel/kernel.py")
        main = src[src.rindex("\ndef main():"):]
        claim = main.index("_claimed = jd._keysrc.claim_op_env()")
        self.assertLess(claim, main.index("_ensure_bundles()"), "before the first child")
        self.assertLess(claim, main.index("threading.Thread("), "before the first thread")
        self.assertIn('jd._keysrc.tmux_unset_global(_claimed, os.environ.get("ROMP_TMUX_SOCKET", ""))', main)

    def test_both_tmux_launch_paths_strip_the_credential(self):
        src = _read("kernel/kernel.py")
        self.assertIn("jd._keysrc.strip_op_env(env)     # op's credential stays with the kernel", src)
        self.assertIn('env=jd._keysrc.strip_op_env(dict(os.environ)))', src, "the revive launch too")

    def test_the_door_passes_the_requests_auth_to_the_one_rule(self):
        src = _read("kernel/kernel.py")
        self.assertIn('eerr = _env_error(env_req, str((b or {}).get("auth") or ""))', src)
        self.assertIn("jd._keysrc.runtime_reserved_names(auth or \"\", jd._keysrc.select_source())", src)
        sb = _read("kernel/sdk_backend.py")
        self.assertIn('env_request_error(env, (reg or {}).get("auth") or "")', sb, "set_env knows the session's auth")
        self.assertIn('err = env_request_error(env, auth or "")', sb, "spawn knows the pick")
        self.assertIn("_keysrc.runtime_reserved_names(sess.auth, key_source)", sb, "the launch")
        self.assertIn('_keysrc.runtime_reserved_names(reg.get("auth") or "", self._work_key_source())', sb, "the fork copy")


class ManagerAndLauncher(unittest.TestCase):
    def test_the_manager_starts_the_tmux_server_without_op_credentials_when_romp_runs_op(self):
        src = _read("bin/romp-manager")
        self.assertIn("function withoutOpCredentials(env)", src)
        self.assertIn("if (!out.ROMP_API_KEY_REF) return out;", src, "a helper box keeps its environment")
        self.assertRegex(src, r"k === 'OP_SERVICE_ACCOUNT_TOKEN' \|\| k === 'OP_CONNECT_HOST' \|\| k === 'OP_CONNECT_TOKEN' \|\| k === 'OP_ACCOUNT' \|\| k\.startsWith\('OP_SESSION_'\)")
        self.assertIn("env: withoutOpCredentials(process.env) });", src)

    def test_the_launcher_scrubs_the_servers_globals_before_the_pane_exists(self):
        src = _read("bin/romp")
        block = src[src.index('if [[ -n "${ROMP_API_KEY_REF:-}" ]]; then'):src.index('tmux new-session -d -s "$name" -c "$work_dir"')]
        self.assertIn('tmux set-environment -gu "$op_var"', block)
        self.assertIn("tmux show-environment -g", block)
        self.assertIn("OP_SESSION_[A-Za-z0-9_]*", block)


if __name__ == "__main__":
    unittest.main()
