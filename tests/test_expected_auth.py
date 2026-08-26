#!/usr/bin/env python3
"""ROMP_EXPECTED_AUTH — the box-wide intended-auth declaration (the user 2026-08-15).

On a machine whose sessions authenticate through Claude Code's apiKeyHelper the key never rides
service.env, so _launched_keyed reads "login" for every session while key auth IS the design — and
the per-init apiKeySource mismatch line was a permanent false alarm. The mechanics under test:

  * ROMP_EXPECTED_AUTH=key|login in the manager env declares the intended side; unset (or junk)
    preserves the launch-intent comparison exactly as it was. Under a declaration the landing that
    MATCHES is quiet and the landing that CONTRADICTS is the problem-ring entry, naming the
    declaration — the never-silent property inverts, it never disappears.
  * api_key_auth persists (reg apiKeyAuth) and restores on construction, with auth_live ("what the
    CLI actually reported") beside it: as a runtime-only default-False flag, a keyed session's
    rate-limit events landed in the login's usage.json between a kernel restart and its next init.
  * An all-keyed box fails loudly, not silently: refresh_usage says "no session can poll" once per
    episode (the rail timer calls it every 60s), as a problem only when no declaration explains it;
    the keyed no-window payload carries spend and nothing about rate limits (the notice was deleted 2026-08-24)
    the bars are absent.

Synthetic sids/paths only; no real key material or session data.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = SourceFileLoader("romp_sdk_backend_expected", os.path.join(BIN, "romp_sdk_backend.py")).load_module()
km = SourceFileLoader("romp_kernel_expected", os.path.join(BIN, "romp-kernel")).load_module()


class _Declared(unittest.TestCase):
    """Base: a backend with captured logs and a clean ROMP_EXPECTED_AUTH slate (each test sets its
    own declaration; the world is restored after)."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self._exp_before = os.environ.pop("ROMP_EXPECTED_AUTH", None)
        self.logs = []
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logs.append)

    def tearDown(self):
        os.environ.pop("ROMP_EXPECTED_AUTH", None)
        if self._exp_before is not None:
            os.environ["ROMP_EXPECTED_AUTH"] = self._exp_before

    def _sess(self, n=1, **reg):
        return sb.SdkSession(self.be, {"sid": "11111111-2222-3333-4444-%012d" % n,
                                       "name": "s%d" % n, "cwd": "/tmp", **reg})

    def _problem_texts(self):
        return [p["text"] for p in self.be.problems(20)]


class DeclarationParsing(_Declared):
    def test_key_login_and_nothing_else(self):
        for raw, want in (("key", "key"), ("login", "login"), (" Key ", "key"),
                          ("LOGIN", "login"), ("both", ""), ("1", ""), ("", "")):
            os.environ["ROMP_EXPECTED_AUTH"] = raw
            self.assertEqual(sb._expected_auth(), want, "raw=%r" % raw)
        os.environ.pop("ROMP_EXPECTED_AUTH", None)
        self.assertEqual(sb._expected_auth(), "", "unset declares nothing")


class DeclarationInvertsTheMismatch(_Declared):
    """The apiKeyHelper box: launched WITHOUT a key in the env (_launched_keyed False), the CLI
    finds one through the helper. Undeclared that rings every init; declared =key it is the
    intended, quiet state — and the LOGIN landing becomes the flagged anomaly."""

    def test_declared_key_and_a_keyed_landing_is_quiet(self):
        os.environ["ROMP_EXPECTED_AUTH"] = "key"
        s = self._sess(1)
        s._launched_keyed = False                       # no key rode service.env — the helper box
        self.be._note_auth_source(s, "apiKeyHelper")
        self.assertFalse([t for t in self._problem_texts() if "billing" in t],
                         "the intended landing must not ring")
        self.assertTrue(any("apiKeySource=" in m for m in self.logs),
                        "…but the state-change info line still self-documents the box")

    def test_declared_key_and_a_login_landing_rings_naming_the_declaration(self):
        os.environ["ROMP_EXPECTED_AUTH"] = "key"
        s = self._sess(2)
        s._launched_keyed = False
        self.be._note_auth_source(s, "none")            # the helper failed — billing the login
        texts = self._problem_texts()
        self.assertTrue(any("ROMP_EXPECTED_AUTH=key" in t and "billing the login" in t
                            for t in texts),
                        "the inverted check flags the contradicting landing, naming the "
                        "declaration: %r" % texts)

    def test_declared_login_and_a_keyed_landing_rings(self):
        os.environ["ROMP_EXPECTED_AUTH"] = "login"
        s = self._sess(3)
        s._launched_keyed = False
        self.be._note_auth_source(s, "apiKeyHelper")
        texts = self._problem_texts()
        self.assertTrue(any("ROMP_EXPECTED_AUTH=login" in t and "billing the API key" in t
                            for t in texts), texts)

    def test_the_declaration_beats_the_launch_intent(self):
        # launched keyed (the key WAS injected) but the box declares login: the login landing that
        # the old comparison would flag is now the intended one — quiet.
        os.environ["ROMP_EXPECTED_AUTH"] = "login"
        s = self._sess(4)
        s._launched_keyed = True
        self.be._note_auth_source(s, "none")
        self.assertFalse([t for t in self._problem_texts() if "billing" in t],
                         "the declared side is the expected side, whatever _options injected")

    def test_undeclared_keeps_todays_wording_exactly(self):
        s = self._sess(5)
        s._launched_keyed = False
        self.be._note_auth_source(s, "apiKeyHelper")
        texts = self._problem_texts()
        self.assertTrue(any("launched for the login but the CLI reports" in t
                            and "Check the login (claude /login) and service.env." in t
                            for t in texts),
                        "no declaration → the launch-intent comparison, verbatim: %r" % texts)


class AllKeyedUsageLineHonorsTheDeclaration(_Declared):
    """The refresh_usage all-keyed line rings as a PROBLEM exactly when the state contradicts (or
    lacks) a declaration: =key → the box working as designed, an info line; =login → all-keyed
    CONTRADICTS the declaration and must ring; undeclared → the surprising case, rings as before.
    `not _expected_auth()` muted the contradiction — the one state the declaration exists to flag."""

    def _all_keyed_box(self, n=7):
        s = self._sess(n)
        s.client, s.loop, s.ended, s.api_key_auth = object(), object(), False, True
        self.be.sessions[s.sid] = s
        return s

    def _usage_problems(self):
        return [p for p in self.be.problems(20) if "telemetry is unavailable" in p["text"]]

    def test_declared_key_is_an_info_line(self):
        os.environ["ROMP_EXPECTED_AUTH"] = "key"
        self._all_keyed_box()
        self.be.refresh_usage()
        self.assertFalse(self._usage_problems(), "all-keyed under =key is the design, not a problem")

    def test_declared_login_rings_the_contradiction(self):
        os.environ["ROMP_EXPECTED_AUTH"] = "login"
        self._all_keyed_box()
        self.be.refresh_usage()
        self.assertTrue(self._usage_problems(), "all-keyed CONTRADICTS =login — it must ring")

    def test_undeclared_still_rings(self):
        self._all_keyed_box()
        self.be.refresh_usage()
        self.assertTrue(self._usage_problems(), "undeclared all-keyed stays the surprising case")


class GearPickMakesTheDeclarationInert(_Declared):
    """Q3 (2026-08-26): ONE explicit gear Billing pick supersedes ROMP_EXPECTED_AUTH from then on —
    the env var described the box's UNPICKED design, and once billing is hand-managed its per-init
    alarms fought the user's own choice on every spawn re-seeded from the remembered default. The
    pick's durable trace is the remembered auth default (set_auth is its only writer), so inertness
    keys on the PICK EVENT — a spawn's seeded reg.auth never counts as explicit."""

    def test_the_pick_supersedes_the_env_declaration(self):
        os.environ["ROMP_EXPECTED_AUTH"] = "key"
        sb.write_sdk_default(Path(self.d), auth="login")   # set_auth's durable trace — the gear pick
        s = self._sess(11)
        s._launched_keyed = True
        self.be._note_auth_source(s, "none")               # a login landing: contradicts the ENV, honors the PICK
        self.assertFalse([t for t in self._problem_texts() if "billing" in t],
                         "the env declaration is INERT after the pick — no false alarm")
        s2 = self._sess(12)
        s2._launched_keyed = False
        self.be._note_auth_source(s2, "apiKeyHelper")      # a keyed landing: contradicts the PICK
        texts = self._problem_texts()
        self.assertTrue(any("the remembered Billing pick is login" in t and "billing the API key" in t
                            for t in texts),
                        "a landing contradicting the pick rings, NAMING THE PICK not the env var: %r" % texts)
        self.assertFalse(any("ROMP_EXPECTED_AUTH" in t for t in texts),
                         "the env var no longer speaks anywhere once picked")

    def test_a_seeded_spawn_never_makes_the_declaration_inert(self):
        # the remembered default SEEDS reg.auth on a spawn — that seed must not count as explicit:
        # with no pick trace in the defaults, the env declaration still governs
        os.environ["ROMP_EXPECTED_AUTH"] = "key"
        s = self._sess(13, auth="key")                     # a spawn re-seeded from some remembered default
        s._launched_keyed = True
        self.be._note_auth_source(s, "none")               # lands on login — contradicts its own seed
        texts = self._problem_texts()
        self.assertTrue(any("billing the login" in t for t in texts),
                        "the seeded session is still judged (against its own pick side): %r" % texts)
        self.assertFalse(any("remembered Billing pick" in t for t in texts),
                         "…but nothing pretends a remembered default was an explicit box-wide pick")

    def test_all_keyed_gate_follows_the_pick(self):
        os.environ["ROMP_EXPECTED_AUTH"] = "login"
        sb.write_sdk_default(Path(self.d), auth="key")     # the user picked key billing by hand
        be2 = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logs.append)
        s = sb.SdkSession(be2, {"sid": "11111111-2222-3333-4444-%012d" % 14, "name": "s14", "cwd": "/tmp"})
        s.connected = True
        s.api_key_auth = True
        be2.sessions[s.sid] = s
        be2.refresh_usage()
        probs = [p for p in be2.problems(20) if "telemetry is unavailable" in p["text"]]
        self.assertFalse(probs, "all-keyed under a KEY pick is the design — an info line, not a problem "
                                "(the env =login is inert)")

    def test_the_real_set_auth_leaves_the_trace(self):
        # end to end: the gear pick itself writes the durable trace _declared_auth keys on
        sb.write_reg(Path(self.d), "11111111-2222-3333-4444-%012d" % 15,
                     {"sid": "11111111-2222-3333-4444-%012d" % 15, "name": "s15", "cwd": "/tmp"})
        self.assertTrue(self.be.set_auth("11111111-2222-3333-4444-%012d" % 15, "login"))
        self.assertEqual(sb._declared_auth(Path(self.d)), ("login", "pick"))


class PickOutranksTheDeclaration(_Declared):
    """An explicit per-session Billing pick (sess.auth) beats the box-wide declaration: the
    declaration describes the box's UNPICKED design, and set_auth's contract is that the next
    init confirms the PICK — judged, and worded, against what the pick launched."""

    def test_a_declared_box_with_an_honored_opposite_pick_is_quiet(self):
        # declared =key, but THIS session was explicitly picked to the login and landed there
        os.environ["ROMP_EXPECTED_AUTH"] = "key"
        s = self._sess(6, auth="login")
        s._launched_keyed = False
        self.be._note_auth_source(s, "none")
        self.assertFalse([t for t in self._problem_texts() if "billing" in t],
                         "a landing honoring the pick is intended, whatever the box declares")

    def test_a_landing_contradicting_the_pick_rings_even_when_it_matches_the_declaration(self):
        # declared =key AND the CLI landed keyed — but the user picked the login for THIS session:
        # billing against the pick must never pass silently, and the ring speaks the launch-intent
        # wording (the pick's side), never the declaration's
        os.environ["ROMP_EXPECTED_AUTH"] = "key"
        s = self._sess(7, auth="login")
        s._launched_keyed = False
        self.be._note_auth_source(s, "apiKeyHelper")
        texts = self._problem_texts()
        self.assertTrue(any("launched for the login but the CLI reports" in t
                            and "billing the API key" in t for t in texts), texts)
        self.assertFalse(any("ROMP_EXPECTED_AUTH" in t for t in texts),
                         "the pick's wording, not the declaration's: %r" % texts)


class SetAuthInvalidatesTheLiveReport(_Declared):
    """auth_live is DISPLAY truth from a live CLI report, and set_auth reconnects to apply — the
    process that made the report no longer exists once the switch lands. The report and its
    persisted reg twin clear with the pick, so the Billing row falls back to the plain intent
    until the next init re-confirms, and a kernel restart cannot resurrect the old side as a
    false "CLI reports" disagreement."""

    def test_the_pick_clears_the_report_live_persisted_and_dormant(self):
        sid = self.be.spawn("n", "/tmp")
        s = sb.SdkSession(self.be, sb.read_reg(self.be.state_dir, sid))
        self.be.sessions[sid] = s
        self.be._note_auth_source(s, "apiKeyHelper")     # an init landed: the CLI reported the key
        self.assertEqual(s.auth_live, "key")
        self.assertTrue(self.be.set_auth(sid, "login"))
        self.assertEqual(s.auth_live, "", "the report described the replaced process")
        self.assertEqual(s.snapshot()["authLive"], "", "…so no live-row disagreement")
        s2 = sb.SdkSession(self.be, sb.read_reg(self.be.state_dir, sid))
        self.assertEqual((s2.auth_live, s2.api_key_auth), ("", False),
                         "a restart restores 'no init yet', never the old side")
        self.assertEqual(self.be.live_sessions()[sid]["authLive"], "",
                         "the dormant row claims nothing either")


class ApiKeyAuthPersists(_Declared):
    """The flag is written to the reg on every flip and restored on construction — the post-restart
    window where a keyed session read False (and its rate-limit events contaminated the login's
    usage.json via the max-merge) is closed."""

    def _reg(self, sid):
        return sb.read_reg(self.be.state_dir, sid)

    def test_a_flip_writes_the_reg_and_construction_restores_it(self):
        sid = self.be.spawn("n", "/tmp")
        s = sb.SdkSession(self.be, self._reg(sid))
        self.assertEqual((s.api_key_auth, s.auth_live), (False, ""),
                         "a fresh session: no init has ever landed")
        self.be._note_auth_source(s, "apiKeyHelper")
        self.assertIs(self._reg(sid)["apiKeyAuth"], True)
        s2 = sb.SdkSession(self.be, self._reg(sid))
        self.assertTrue(s2.api_key_auth, "restored — not reset to False until the next init")
        self.assertEqual(s2.auth_live, "key", "the Billing row keeps the CLI's truth too")

    def test_the_flip_back_to_login_persists_the_same_way(self):
        sid = self.be.spawn("n", "/tmp")
        s = sb.SdkSession(self.be, self._reg(sid))
        self.be._note_auth_source(s, "ANTHROPIC_API_KEY")
        self.be._note_auth_source(s, "none")
        self.assertIs(self._reg(sid)["apiKeyAuth"], False)
        s2 = sb.SdkSession(self.be, self._reg(sid))
        self.assertFalse(s2.api_key_auth)
        self.assertEqual(s2.auth_live, "login")

    def test_no_flip_no_write(self):
        # a login session that has never been keyed: the early return means no reg field, so a
        # restart restores auth_live "" (no CONFIRMED side) rather than a manufactured "login"
        sid = self.be.spawn("n", "/tmp")
        s = sb.SdkSession(self.be, self._reg(sid))
        self.be._note_auth_source(s, "none")
        self.assertEqual(s.auth_live, "login", "the runtime truth is set on every init")
        self.assertNotIn("apiKeyAuth", self._reg(sid))


class AuthLiveOnTheWire(_Declared):
    def test_snapshot_reports_the_cli_truth_beside_the_intent(self):
        s = self._sess(1, auth="login")
        self.assertEqual(s.snapshot()["authLive"], "", "unknown until an init lands")
        self.be._note_auth_source(s, "apiKeyHelper")
        snap = s.snapshot()
        self.assertEqual(snap["auth"], "login", "the launch intent stays what it was")
        self.assertEqual(snap["authLive"], "key", "…and the live truth rides beside it")

    def test_dormant_rows_carry_the_persisted_truth(self):
        sid = self.be.spawn("n", "/tmp")
        self.be._update_reg(sid, apiKeyAuth=True)
        self.assertEqual(self.be.live_sessions()[sid]["authLive"], "key")
        sid2 = self.be.spawn("m", "/tmp")
        self.assertEqual(self.be.live_sessions()[sid2]["authLive"], "",
                         "no persisted report — a dormant row claims nothing")

    def test_the_kernel_payload_passes_it_through(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('"authLive": tm.get("authLive", "")', src,
                      "the tab-hover Billing row reads the live truth off the session payload")


class RefreshUsageAllKeyed(_Declared):
    """Every live session keyed = nobody can poll the subscription windows. Said once per episode
    (the rail timer calls refresh_usage every 60s), as a problem only when undeclared."""

    LINE = "all billing API keys"

    def _live_keyed(self, n):
        s = self._sess(n)
        s.client, s.loop, s.ended = object(), object(), False
        s.api_key_auth = True
        s.refresh_usage = lambda: True
        return s

    def _lines(self):
        return [m for m in self.logs if self.LINE in m]

    def test_logged_once_per_episode_not_per_call(self):
        a, b = self._live_keyed(1), self._live_keyed(2)
        self.be.sessions = {a.sid: a, b.sid: b}
        for _ in range(3):
            self.be.refresh_usage()
        self.assertEqual(len(self._lines()), 1, "the 60s timer must not spam the log")
        self.assertIn("2 live session(s)", self._lines()[0])

    def test_undeclared_rings_and_a_key_declaration_does_not(self):
        a = self._live_keyed(1)
        self.be.sessions = {a.sid: a}
        self.be.refresh_usage()
        self.assertTrue(any(self.LINE in t for t in self._problem_texts()),
                        "undeclared all-keyed silence is the surprising case — it rings")
        os.environ["ROMP_EXPECTED_AUTH"] = "key"
        be2 = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None,
                            log=self.logs.append)
        b = sb.SdkSession(be2, {"sid": "11111111-2222-3333-4444-%012d" % 9,
                                "name": "s9", "cwd": "/tmp"})
        b.client, b.loop, b.ended = object(), object(), False
        b.api_key_auth = True
        be2.sessions = {b.sid: b}
        be2.refresh_usage()
        self.assertEqual(len(self._lines()), 2, "declared, the condition still logs (info)…")
        self.assertFalse(any(self.LINE in p["text"] for p in be2.problems(10)),
                         "…but as the box working as designed, never a problem")

    def test_a_pollable_session_rearms_the_one_shot(self):
        keyed = self._live_keyed(1)
        self.be.sessions = {keyed.sid: keyed}
        self.be.refresh_usage()                          # episode 1: logged
        sub = self._sess(2)
        sub.client, sub.loop, sub.ended = object(), object(), False
        polled = []
        sub.refresh_usage = lambda: polled.append(True) or True
        self.be.sessions[sub.sid] = sub
        self.be._note_auth_source(sub, "none")           # its init CONFIRMED the login — only a
        #   confirmed login re-arms (a pre-init spawn is "unknown": the companion test below)
        self.be.refresh_usage()                          # a candidate exists: polls, re-arms
        self.assertTrue(polled)
        sub.api_key_auth = True                          # …and the box goes all-keyed again
        self.be.refresh_usage()
        self.assertEqual(len(self._lines()), 2, "a NEW episode logs again")

    def test_a_pre_init_spawn_does_not_rearm_or_re_ring(self):
        # a fresh spawn is connected before its first turn, and its init — the only event that can
        # set api_key_auth — arrives only WITH that turn: until then its default-False flag means
        # "unknown", not "login", and it must not open a new episode on the motivating all-keyed box
        keyed = self._live_keyed(1)
        self.be.sessions = {keyed.sid: keyed}
        self.be.refresh_usage()                          # episode 1: logged
        fresh = self._sess(2)                            # connected, no init yet: auth unknown
        fresh.client, fresh.loop, fresh.ended = object(), object(), False
        fresh.refresh_usage = lambda: True
        self.be.sessions[fresh.sid] = fresh
        self.be.refresh_usage()                          # a compose-window tick
        self.be._note_auth_source(fresh, "apiKeyHelper")  # its first init lands keyed
        self.be.refresh_usage()
        self.assertEqual(len(self._lines()), 1,
                         "an unknown was never a login — same episode, no repeat line")

    def test_no_connected_sessions_is_not_the_all_keyed_case(self):
        self.be.sessions = {}
        self.be.refresh_usage()
        dormant = self._sess(3)                          # keyed but not connected (client None)
        dormant.api_key_auth = True
        self.be.sessions = {dormant.sid: dormant}
        self.be.refresh_usage()
        self.assertEqual(self._lines(), [], "nothing connected — nothing to say")


class UsageTelemetryUnavailable(unittest.TestCase):
    """kernel _usage(): the keyed no-window payload says WHY the bars are absent — key auth means
    the windows are structurally absent (both usage.json writers skip keyed sessions), and the rail
    hover renders the reason. Key auth is the reason when the manager env carries a key OR the box
    declares ROMP_EXPECTED_AUTH=key (the apiKeyHelper box, where no key ever rides service.env)."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.real_state = km.jd.STATE
        km.jd.STATE = Path(self.d)
        self.real_key = km._auth_key_present
        self.real_acct = km._claude_account
        self._exp_before = os.environ.pop("ROMP_EXPECTED_AUTH", None)

    def tearDown(self):
        km.jd.STATE = self.real_state
        km._auth_key_present = self.real_key
        km._claude_account = self.real_acct
        os.environ.pop("ROMP_EXPECTED_AUTH", None)
        if self._exp_before is not None:
            os.environ["ROMP_EXPECTED_AUTH"] = self._exp_before

    def test_no_payload_carries_the_retired_telemetry_flag(self):
        # the flag and its "rate-limit telemetry unavailable under API-key auth" hover line were
        # DELETED (the user 2026-08-24: they know which machines are key-only and want the spend
        # without a notice about rate limits that don't apply). A keyed host's payload carries
        # spend and NOTHING about rate limits; a login host's windows are untouched.
        (Path(self.d) / "usage.json").write_text(json.dumps({"t": 1000, "apiKey": True}))
        km._auth_key_present = lambda: True
        km._claude_account = lambda: ""
        u = km._usage()
        self.assertTrue(u.get("apiKey"))
        self.assertNotIn("telemetryUnavailable", u)
        os.environ["ROMP_EXPECTED_AUTH"] = "key"    # the apiKeyHelper declaration marks nothing either
        (Path(self.d) / "spend.json").write_text(json.dumps({"days": {}, "hours": {}}))
        self.assertNotIn("telemetryUnavailable", km._usage())

    def test_login_windows_stay_untouched_by_the_deletion(self):
        (Path(self.d) / "usage.json").write_text(json.dumps(
            {"t": 1000, "five_hour": {"pct": 40, "resets_at": None}}))   # unstamped legacy keeps bars
        km._auth_key_present = lambda: True
        km._claude_account = lambda: ""
        u = km._usage()
        self.assertTrue(u.get("fiveHour"), "rate-limit windows render exactly as before")
        self.assertNotIn("telemetryUnavailable", u)


if __name__ == "__main__":
    unittest.main()
