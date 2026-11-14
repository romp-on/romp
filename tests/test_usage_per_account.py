"""Fleet usage: one PAYLOAD ROW PER HOST, one AGGREGATED rendering (the user 2026-08-08).

The 5h / 7d windows are account-wide allowances, so the collapsed rail draws ONE set of bars — per
window, the worst known reading across every account (a shared login reports the same number from every
host and the max collapses it for free) — plus one API cell for everything key-billed. The per-host
story lives in the hover, so /usage/fleet now emits every reporting host, INCLUDING a key-only host with
no account digest at all (the old per-account dedup dropped those entirely, and a mixed host's key spend
never rendered anywhere).

The account is read from Claude Code's own ~/.claude.json (`oauthAccount.accountUuid`) — the identity the
CLI itself uses, with no API that reports it. It travels as an opaque digest, never the email: the only
question is "same login or not" (the client's aggregation and a reader of the hover both lean on it),
and an email is a personal identifier that would otherwise ride to every federated host.

Synthetic accounts and hosts only; no real uuid, email or hostname appears here.
"""
import inspect
import json
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_acctusage", os.path.join(BIN, "romp-kernel"))

ACCT_A = "aaaaaaaaaaaa"
ACCT_B = "bbbbbbbbbbbb"


def _usage(acct):
    return {"fiveHour": {"pct": 12, "resetsAt": None, "color": [1, 2, 3]},
            "sevenDay": None, "fable": None, "t": 1000, "acct": acct, "limited": None}


class AccountIdentity(unittest.TestCase):
    def test_it_is_a_digest_and_never_the_raw_identifier(self):
        # the read mechanics live in _acct_read now: ONE cached parse feeds the digest AND the
        # display label (the user 2026-08-09 added the label; the digest's own rules stand)
        src = inspect.getsource(km._acct_read)
        self.assertIn("val = jd._cred.login_digest(oa)", src, "the account identity the CLI itself uses")
        # the digest is short and opaque: enough to answer "same or not", carrying nothing back —
        # it hashes the account uuid (and, since 2026-11, its organization's uuid: one account in two
        # organizations is two logins), never the email, so cross-host equality carries no identity
        dsrc = inspect.getsource(km.jd._cred.login_digest)
        self.assertIn("accountUuid", dsrc)
        self.assertIn("organizationUuid", dsrc)
        self.assertIn("hexdigest()[:12]", dsrc)
        self.assertNotIn("emailAddress", dsrc)
        # the label is the deliberate, narrower exception: display-only (picker Billing row, tab
        # hover), read through _claude_account_label — the digest path itself never returns it
        self.assertIn('return _ACCT_CACHE["val"]', inspect.getsource(km._claude_account))
        self.assertNotIn('_ACCT_CACHE["label"]', inspect.getsource(km._claude_account))

    def test_a_missing_or_unreadable_file_is_no_account_not_a_crash(self):
        old = os.environ.get("HOME")
        try:
            os.environ["HOME"] = tempfile.mkdtemp()      # a home with no ~/.claude.json at all
            km._ACCT_CACHE["mtime"] = -1.0
            self.assertEqual(km._claude_account(), "")
        finally:
            if old is not None:
                os.environ["HOME"] = old
            km._ACCT_CACHE["mtime"] = -1.0

    def test_the_reading_is_cached_on_the_file_and_not_reparsed_per_poll(self):
        self.assertIn('_ACCT_CACHE["mtime"] == m', inspect.getsource(km._acct_read))

    def test_the_usage_payload_carries_it(self):
        self.assertIn('"acct": _claude_account(),', inspect.getsource(km._usage))
        # …and, since 2026-08-09, the login's NAME beside it — display for the hover only (the
        # rail's cross-host dedup stays on the digest, which carries nothing)
        self.assertIn('"acctLabel": _claude_account_label(),', inspect.getsource(km._usage))


class FleetRollup(unittest.TestCase):
    def setUp(self):
        self._usage_real = km._usage
        with km._remotes_lock:
            km._remotes.clear()

    def tearDown(self):
        km._usage = self._usage_real
        with km._remotes_lock:
            km._remotes.clear()

    def _remote(self, host, acct, status="up"):
        with km._remotes_lock:
            km._remotes[host] = {"host": host, "status": status, "usage": _usage(acct) if acct else None}

    def test_every_reporting_host_rides_along_shared_login_or_not(self):
        # the rail aggregates and the hover breaks down per host, so a shared login is NOT deduped
        # server-side any more — each host's row carries the same acct and the client collapses it
        km._usage = lambda: _usage(ACCT_A)
        self._remote("api", ACCT_A)
        self._remote("gpu", ACCT_A)
        rows = km._fleet_usage()
        self.assertEqual([r["host"] for r in rows], ["", "api", "gpu"])
        self.assertEqual({r["acct"] for r in rows}, {ACCT_A}, "the digest is what lets the client collapse them")

    def test_a_second_account_gets_its_own_row(self):
        km._usage = lambda: _usage(ACCT_A)
        self._remote("api", ACCT_B)
        rows = km._fleet_usage()
        self.assertEqual([r["host"] for r in rows], ["", "api"])
        self.assertEqual(rows[1]["acct"], ACCT_B)

    def test_a_disconnected_host_contributes_nothing(self):
        km._usage = lambda: _usage(ACCT_A)
        self._remote("api", ACCT_B, status="down")
        self.assertEqual(len(km._fleet_usage()), 1)

    def test_a_key_only_host_is_included_even_with_no_account_to_report(self):
        # the old per-account dedup dropped an acct-less row entirely, so a remote key-only host's
        # spend could never reach the rail (found 2026-08-08 while adding per-session auth): the host
        # itself is the identity for the spend half of a payload
        km._usage = lambda: _usage(ACCT_A)
        self._remote("api", "")
        with km._remotes_lock:
            km._remotes["api"]["usage"] = {"apiKey": True, "spend": {"fiveHour": {"usd": 1.0}}, "t": 1, "acct": ""}
        rows = km._fleet_usage()
        self.assertEqual([r["host"] for r in rows], ["", "api"])
        self.assertEqual(rows[1]["acct"], "")

    def test_the_local_row_is_always_first_so_the_notices_read_off_this_machine(self):
        km._usage = lambda: _usage(ACCT_A)
        self._remote("aaa-sorts-first", ACCT_B)
        self.assertEqual(km._fleet_usage()[0]["host"], "")


class Polling(unittest.TestCase):
    def test_a_remote_is_polled_at_most_once_a_minute(self):
        src = inspect.getsource(km._poll_remote_usage)
        self.assertIn("REMOTE_USAGE_EVERY", src)
        self.assertGreaterEqual(km.REMOTE_USAGE_EVERY, 60)
        self.assertIn('r.get("_usage_at")', src)

    def test_a_blip_keeps_the_last_good_reading_rather_than_blanking_the_bars(self):
        src = inspect.getsource(km._poll_remote_usage)
        self.assertEqual(src.count('return r.get("usage")'), 3)

    def test_the_supervisor_polls_it_beside_the_version(self):
        src = inspect.getsource(km._tunnel_supervisor)
        self.assertIn("ruse = _poll_remote_usage(r) if up else None", src)
        self.assertIn('r["usage"] = ruse', src)

    def test_the_snapshot_never_reaches_the_credential_file(self):
        # remotes.json is 0600 because every row holds a serve token; rewriting it every minute to store
        # a usage snapshot would be pure churn on a file that exists to survive a restart
        self.assertIn("usage", km._NOT_SAVED)
        self.assertIn("_usage_at", km._NOT_SAVED)
        self.assertIn("k not in _NOT_SAVED", inspect.getsource(km._remotes_rows_for_save))


class RailRendering(unittest.TestCase):
    js = km._LANDING_USAGE_JS

    def test_the_rail_reads_the_fleet_view(self):
        self.assertIn("fetch('/usage/fleet'", self.js)
        self.assertIn("function renderRows(rows,selfHost)", self.js)

    def test_the_rail_aggregates_and_never_repeats_the_windows_per_host(self):
        # collapsed = one set of bars (worst window across hosts) + one API cell; the per-host
        # .ru-set/.ru-host rail markup is gone (the user 2026-08-08) — hosts appear only in the hover
        self.assertIn("function aggBarsHTML(live)", self.js)
        self.assertIn("function apiCellHTML(live)", self.js)
        self.assertIn("aggBarsHTML(LAST)+apiCellHTML(LAST)", self.js)
        self.assertNotIn("class=ru-set", self.js)
        self.assertNotIn("class=ru-host>", self.js)
        self.assertIn("if(!best||d.pct>best.pct)best=d;", self.js, "worst known reading wins the bar")

    def test_the_api_cell_is_numbers_under_a_constant_label_and_no_bars(self):
        # a bare 'API' label — never any fragment of the key, not even a last-4 tail (the user
        # 2026-08-08, evening); each window then wears its ONE display name LEFT of its dollars+tokens
        # (the user 2026-08-09 — same words, font and position as the account bars); the spend bar
        # graphs are gone everywhere (2026-08-08, morning: they told you nothing)
        self.assertIn("'<div class=ru-name>API</div><span class=ah-slot></span>'", self.js)   # T301: the API-health dot's slot follows the label
        self.assertNotIn("_tail", self.js)
        # pay-per-token wears calendar-ish windows (the user 2026-08-13): 1 day + 1 month on the cell
        # (1 week rides the hover); day||fiveHour keeps an older remote's spend visible (version skew)
        # …the month segment carries the version-skew caveat (T235b): a legacy host's CALENDAR month is
        # left out of this rolling segment and the title says how many machines were not counted
        self.assertIn("seg('day','1 day')+seg('month','1 month',monthCav)", self.js)
        self.assertIn("var d=sp.day||sp.fiveHour,m=sp.month;   // m: the ROLLING month only (a legacy host has none here)", self.js)
        self.assertIn("'<div class=ru-pct>'+fmtUsd(sum[k].usd)+' · '+fmtTok(sum[k].tok)+' tok</div>'", self.js)
        self.assertNotIn("spendColor", self.js)
        self.assertNotIn("spendWinsHTML", self.js)

    def test_the_hover_is_one_block_per_account_with_the_machines_named_in_the_strips_host_dress(self):
        # the user 2026-09-19: three machines on one login drew three identical columns; the block is per account, the
        # machines one line beneath it in the tab strip's quiet italic host dress (the per-host .ru-tip-host heading is gone)
        css = km._landing()
        self.assertNotIn(".ru-tip-host{", css)
        self.assertIn(".ru-tip-machines .host-prefix{color:var(--dim,#9aa0a6);font-weight:400;font-style:italic;font-size:0.86em}", css)
        self.assertIn("function acctBlocksHTML(sets,manyMachines)", self.js)
        # a host section can carry BOTH its login's windows and its key's spend (per-session auth),
        # and the spend rows are numbers only — no track span
        self.assertIn("function winDet(u,det)", self.js)
        self.assertIn("winDet(r.usage,det);spendDet(r.usage,det);", self.js)
        # spend is ONE fleet-level section now (the user 2026-08-13): one shared key, one number,
        # "· N machines" when several — never a per-host repeat
        self.assertIn("function fleetSpendHTML(sets)", self.js)
        self.assertIn("API spend'+(hosts>1?' · '+hosts+' machines':'')", self.js)
        self.assertIn("' tok · '+(v.turns||0)+' turns</span>", self.js)

    def test_the_account_wide_notices_read_off_THIS_machine(self):
        # a limit pauses THIS kernel's retries and judges; a remote account's limit does not
        self.assertIn("function notices(u)", self.js)
        self.assertIn("var local=rows.length?rows[0].usage:null;\nnotices(local);", self.js)

    def test_the_tooltip_names_the_machines_only_when_there_is_more_than_one(self):
        self.assertIn("var manyMachines=(sets.length+NOREPORT.length)>1;", self.js)
        self.assertIn("if(manyMachines)h+='<div class=ru-tip-machines>'", self.js)
        self.assertIn("if(d._acct)h+='<div class=ru-tip-acct>'+esc(d._acct)+'</div>';", self.js, "the account line heads its block whenever the login is known")
        self.assertIn('"noReport": _usage_no_report()', open(os.path.join(BIN, "romp-kernel")).read(), "the route names the machines up with no report")
        with km._remotes_lock:
            saved = dict(km._remotes); km._remotes.clear()
            km._remotes.update({"TESTHOSTB": {"host": "TESTHOSTB", "status": "up", "usage": {"t": 1}},
                                "TESTHOSTC": {"host": "TESTHOSTC", "status": "up"}, "TESTHOSTD": {"host": "TESTHOSTD", "status": "down"}})
        try:
            self.assertEqual(km._usage_no_report(), ["TESTHOSTC"], "up with no report: named; a reporting or a down machine is not")
        finally:
            with km._remotes_lock:
                km._remotes.clear(); km._remotes.update(saved)

    def test_the_route_reads_the_cached_poll_rather_than_dialling_per_request(self):
        # a dashboard refresh must not cost an ssh round-trip per attached host: _fleet_usage reads the
        # rows the tunnel supervisor already filled, and does no network of its own
        src = inspect.getsource(km._fleet_usage)
        self.assertIn("_remotes.values()", src)
        for net in ("HTTPConnection", "_poll_remote_usage", "subprocess"):
            self.assertNotIn(net, src, "the route must answer from the cache")


if __name__ == "__main__":
    unittest.main()
