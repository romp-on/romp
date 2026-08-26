"""Effort-switch UX (the user 2026-07-06): /effort has no SDK runtime control, so romp applies it by
RECONNECTING the session (resume) — which otherwise leaves nothing in the chat. Now the effort badge shows
switching-dots and the chat shows a transient "Reloading session…" element while the reconnect is pending,
both driven by an `effortPending` flag that mirrors `modelPending` end-to-end and clears when the new client
connects. Source pins on build_session + the SDK backend."""
import inspect
import os
import unittest
from importlib.machinery import SourceFileLoader
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_efr", os.path.join(BIN, "romp-kernel")).load_module()
BACKEND_SRC = open(os.path.join(BIN, "romp_sdk_backend.py")).read()


class EffortReconnect(unittest.TestCase):
    def test_build_session_emits_a_reconnecting_event_while_effort_pending(self):
        src = inspect.getsource(km.build_session)
        self.assertIn('if (tm0 or {}).get("effortPending"):', src)
        self.assertIn('events.append({"kind": "reconnecting", "effort": (tm0 or {}).get("effort") or ""})', src)

    def test_the_reconnecting_notice_precedes_the_queued_bubble(self):
        # like the compacting element, it must sit ABOVE any queued/provisional message
        src = inspect.getsource(km.build_session)
        i_recon = src.index('events.append({"kind": "reconnecting"')
        i_queued = src.index('events.append({"kind": "queued"')
        self.assertGreater(i_recon, 0)
        self.assertGreater(i_queued, i_recon)

    def test_status_dict_carries_effortPending_for_the_badge_dots(self):
        src = inspect.getsource(km.build_session)
        self.assertIn('"effortPending": bool(tm.get("effortPending")),', src)

    def test_sessions_live_passes_effortPending_through_from_the_sdk_backend(self):
        src = inspect.getsource(km.Sessions.live)
        self.assertIn('"effortPending": bool(st.get("effortPending")),', src)

    def test_backend_set_effort_arms_the_pending_flag_and_reconnects(self):
        self.assertIn('s._effort_pending = value', BACKEND_SRC)
        self.assertIn('self._update_reg(sid, effort=value, effortPending=True)', BACKEND_SRC)
        self.assertIn('s.request_reconnect()', BACKEND_SRC)

    def test_every_effort_pick_is_remembered_ultracode_included(self):
        # the user 2026-08-14: they pick ultracode and expect NEW sessions to follow. The old guard
        # (`if value != "ultracode"`) deliberately never remembered it, so the seed sat on their one
        # historical max pick and every new session opened at max — reading as a downgrade. spawn
        # still hands each new session its own per-session launch shape (--effort xhigh + the
        # ultracode settings key), so the CLI's session-scoping is preserved.
        self.assertNotIn('if value != "ultracode"', BACKEND_SRC)
        self.assertIn("write_sdk_default(self.state_dir, effort=value)", BACKEND_SRC)
        # …and the seed round-trips through the defaults store (behavioral, hermetic state dir)
        import tempfile as _tf
        from importlib.machinery import SourceFileLoader as _SFL
        sb = _SFL("romp_sdk_backend_efr", os.path.join(BIN, "romp_sdk_backend.py")).load_module()
        td = _tf.mkdtemp()
        sb.write_sdk_default(td, effort="ultracode")
        d = sb.read_sdk_defaults(td)
        self.assertEqual(d.get("effort"), "ultracode")
        self.assertIn("ultracode", sb.EFFORT_LEVELS,
                      "spawn's seed filter (in EFFORT_LEVELS) must accept the remembered ultracode")

    def test_setters_write_the_reg_through_the_locked_rmw(self):
        # the bare read→mutate→write raced the loop threads' own locked RMWs (queue/echo mirrors,
        # liveCtx) and could silently drop the just-picked field: the label looked right, then the
        # value reverted at the next respawn when __init__ re-read the reg (the user 2026-08-14,
        # whose ultracode sessions seemed to downgrade at random). The whole setter family goes
        # through _update_reg now.
        for pin in ('self._update_reg(sid, effort=value, effortPending=True)',
                    'self._update_reg(sid, auth=value, authPending=True, apiKeyAuth=None)',
                    'self._update_reg(sid, mode=mode)',
                    'self._update_reg(sid, fast=(value == "on"), liveFast=value)',
                    'self._update_reg(sid, name=new_name,',   # + the rename ping rides the same locked RMW when owed (2026-08-24/25)
                    'self._update_reg(sid, model=value, modelPending=bool(s._model_pending))',
                    'self._update_reg(sid, model=value, liveModel=_alias_label(value), modelPending=False)'):
            self.assertIn(pin, BACKEND_SRC)

    def test_backend_clears_the_pending_flag_when_the_reconnect_lands(self):
        # cleared the instant the new client connects (reconnect loop) — event-based, mirrors _model_pending
        self.assertIn('if self._effort_pending:', BACKEND_SRC)
        self.assertIn('self.backend._update_reg(self.sid, effortPending=False)', BACKEND_SRC)
        # exposed on both the live snapshot and the reg-backed live_sessions (for dormant/all sessions)
        self.assertIn('"effortPending": bool(self._effort_pending),', BACKEND_SRC)
        self.assertIn('"effortPending": bool(reg.get("effortPending")),', BACKEND_SRC)

    def test_backend_clears_pending_on_thread_death_so_the_dots_never_trap(self):
        self.assertIn('if sess._effort_pending:', BACKEND_SRC)


if __name__ == "__main__":
    unittest.main()
