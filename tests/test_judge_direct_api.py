#!/usr/bin/env python3
"""The captioner and the gister call the Messages API directly from the judge process (2026-10-06).

Until then every judge call started one `claude -p` process, and the captioner (about 7,000 calls an hour at
peak) and the gister (about 1,000) paid a CLI start of roughly a core-second each on a shared machine near its
load line. A key-billed call on a Haiku model now goes straight to the Messages API over one keep-alive
connection per worker thread, and answers in the CLI's envelope shape so the rest of _judge_run is unchanged.

What this module pins, against a real local stand-in HTTP server (never the real API, never a real key):
  * the request carries the CLI's model id, max_tokens, thinking setting and temperature, the judge's own system
    prompt and user text, the held key in x-api-key, and no cache_control;
  * the reply parses into the same caption the CLI road gives for the same model text;
  * the usage row keeps its fields, with the API's own token counts and the cost priced from them;
  * ROMP_JUDGE_DIRECT_API=off, a login-billed call, a non-Haiku model, another judge, no held key and a proxy
    setting all keep the CLI road;
  * a helper that fails is loud (the auth latch, an "auth" row) and sends nothing anywhere;
  * a 401 brings the held key's one refresh and one retry; overloads retry, bounded; a refusal is a content
    refusal; a call past the alarm comes back in the alarm-kill shape;
  * the key never reaches a log line or any written file, even when the server quotes it back;
  * idle connections are pooled and reused across calls and passes; the road adds no concurrency of its own.

Synthetic values throughout, and this module's own synthetic session id.
"""
import contextlib
import http.server
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_SERVICE_ENV_FILE"] = os.path.join(os.environ["XDG_STATE_HOME"], "no-such-service.env")
os.environ["ROMP_SERVICE_ENV"] = os.environ["ROMP_SERVICE_ENV_FILE"]
os.environ.pop("CLAUDE_CODE_API_KEY_HELPER_TTL_MS", None)
jd = load_source("romp_judge_direct_api", os.path.join(BIN, "romp-judge"))
cred = jd._cred

SID = "3c9b5e21-8d4f-4a6b-b1c2-6f7e8d9a0b1c"     # this module's own synthetic session id
HELD_1 = "synthetic-direct-value-one"
HELD_2 = "synthetic-direct-value-two"
UNIT = "USER: add a dark-mode toggle to the notes-api settings page\nTOOLS USED: Edit web/settings.ts"
CAPTION = "Added a dark-mode toggle to the settings page"
_REAL_RUN = subprocess.run
_PROXY_ENV = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy",
              "ANTHROPIC_CUSTOM_HEADERS", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
              "ROMP_JUDGE_DIRECT_API", "ANTHROPIC_BASE_URL")


def _message(text=CAPTION, usage=None, stop="end_turn", model="claude-haiku-4-5-20251001"):
    return {"id": "msg_synthetic", "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": text}], "stop_reason": stop,
            "usage": usage or {"input_tokens": 412, "output_tokens": 11, "cache_creation_input_tokens": 0,
                               "cache_read_input_tokens": 0}}


class StandInAPI:
    """A local Messages API stand-in: records every request (path, headers, body, the client's port) and answers
    from a script of (status, headers, body) entries, the last repeating; `delay` holds each answer."""

    def __init__(self):
        self.requests, self.script, self.delay = [], [(200, {}, _message())], 0.0
        self.inflight = self.max_inflight = 0
        self.drop_after = False                       # close the connection after answering, without saying so
        self._lock = threading.Lock()
        api = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                with api._lock:
                    api.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                         "body": json.loads(body or b"{}"), "port": self.client_address[1]})
                    n = len(api.requests)
                    drop = api.drop_after                 # read with the request: a test that resets the flag after
                    #                                       its call returns cannot race the close below
                    status, hdrs, payload = api.script[min(n, len(api.script)) - 1]
                    api.inflight += 1
                    api.max_inflight = max(api.max_inflight, api.inflight)
                try:
                    if api.delay:
                        time.sleep(api.delay)
                    if callable(payload):
                        payload = payload(api.requests[n - 1])
                    out = json.dumps(payload).encode()
                    self.send_response(status)
                    for k, v in hdrs.items():
                        self.send_header(k, v)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                    if drop:
                        self.close_connection = True  # the server's idle close: the client still thinks it is open
                finally:
                    with api._lock:
                        api.inflight -= 1

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _vault(outputs):
    """A fixture secret store: a helper that counts its runs and prints outputs[n-1] on its n-th run (the last
    repeats); None makes that run exit 1. Returns (dir, script)."""
    d = tempfile.mkdtemp()
    Path(d, "outputs").write_text("".join(("FAIL" if o is None else o) + "\n" for o in outputs))
    script = Path(d, "helper.sh")
    script.write_text(
        "#!/bin/sh\n"
        "d='%s'\n"
        "n=$(( $(cat \"$d/runs\" 2>/dev/null || echo 0) + 1 ))\n"
        "echo \"$n\" > \"$d/runs\"\n"
        "line=$(sed -n \"${n}p\" \"$d/outputs\")\n"
        "[ -n \"$line\" ] || line=$(tail -n 1 \"$d/outputs\")\n"
        "[ \"$line\" != FAIL ] || exit 1\n"
        "echo \"$line\"\n" % d)
    script.chmod(0o700)
    return d, str(script)


def _runs(d):
    try:
        return int(Path(d, "runs").read_text())
    except (OSError, ValueError):
        return 0


class DirectRoadBase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.state = os.path.join(self.root, "state")
        self.cfg = os.path.join(self.root, "claude-config")
        os.makedirs(self.state)
        os.makedirs(self.cfg)
        self._state_before = jd.STATE
        jd._rebind_state(Path(self.state))
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        self._reg("key")
        self._env_before = {k: os.environ.get(k) for k in _PROXY_ENV + ("CLAUDE_CONFIG_DIR", "ROMP_DISTILLER_NOTES")}
        for k in _PROXY_ENV:
            os.environ.pop(k, None)
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg
        os.environ["ROMP_DISTILLER_NOTES"] = os.path.join(self.root, "no-notes.md")   # no operator notes in prompts
        self._managed_before = cred.managed_settings_path
        cred.managed_settings_path = lambda: os.path.join(self.cfg, "managed-settings.json")
        self.now = [5000.0]
        self._source_before = jd._KEY_SOURCE
        jd._KEY_SOURCE = cred.HeldKey(cred.child_key_helper, env_name="ANTHROPIC_API_KEY", label="apiKeyHelper",
                                      max_age_fn=cred.helper_ttl_if_set, clock=lambda: self.now[0])
        self._wires = (jd._DEFAULT_AUTH_FN, jd._DEFAULT_LOGIN_FN, jd._LOGIN_AUTH_ENV_FN)
        jd._DEFAULT_AUTH_FN = jd._DEFAULT_LOGIN_FN = jd._LOGIN_AUTH_ENV_FN = None
        jd._auth_cache[:] = [None, {}]
        jd._HELD_FAIL_SAID.clear()
        jd._DIRECT_API_SAID.clear()
        jd._DIRECT_POOL.clear()
        jd._judge_ctx.fsid = SID
        jd._judge_ctx.paused = False
        jd._judge_ctx.last_call_fail = None
        self.api = StandInAPI()
        os.environ["ANTHROPIC_BASE_URL"] = self.api.url
        self.spawns = []                              # CLI children: (argv, the key it was given)
        self.cli_result = CAPTION
        for p in (patch.object(jd, "_judge_engine", return_value="claude"),
                  patch.object(jd.subprocess, "run", side_effect=self._fake_cli)):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.api.close()
        jd._KEY_SOURCE = self._source_before
        jd._DEFAULT_AUTH_FN, jd._DEFAULT_LOGIN_FN, jd._LOGIN_AUTH_ENV_FN = self._wires
        cred.managed_settings_path = self._managed_before
        for k, v in self._env_before.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        jd._rebind_state(self._state_before)
        jd._auth_cache[:] = [None, {}]
        jd._DIRECT_POOL.clear()
        jd._judge_ctx.fsid = None
        jd._judge_ctx.paused = False
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(getattr(self, "vault", ""), ignore_errors=True)

    def _reg(self, auth):
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"sid": SID, "auth": auth}))
        jd._auth_cache[:] = [None, {}]

    def _helper(self, outputs):
        self.vault, script = _vault(outputs)
        Path(self.cfg, "settings.json").write_text(json.dumps({"apiKeyHelper": script}))

    def _fake_cli(self, cmd, input=None, env=None, **kw):
        if isinstance(cmd, str):                      # the fixture helper, run by the holder: for real
            return _REAL_RUN(cmd, input=input, env=env, **kw)
        self.spawns.append((list(cmd), (env or {}).get("ANTHROPIC_API_KEY")))
        return SimpleNamespace(stdout=json.dumps({"result": self.cli_result, "usage": {"input_tokens": 1},
                                                  "duration_ms": 3, "total_cost_usd": 0.0}),
                               stderr="", returncode=0)

    def _usage(self):
        try:
            return [json.loads(ln) for ln in jd.USAGE.read_text().splitlines() if ln.strip()]
        except OSError:
            return []

    def _errors(self):
        try:
            return [json.loads(ln) for ln in jd.ERRORS.read_text().splitlines() if ln.strip()]
        except OSError:
            return []

    def _caption(self):
        with contextlib.redirect_stderr(io.StringIO()):
            return jd.caption_llm(UNIT)


class TheRequest(DirectRoadBase):
    def test_a_caption_goes_to_the_api_with_the_clis_model_settings_and_the_judges_own_prompt(self):
        self._helper([HELD_1])
        self.assertEqual(self._caption(), jd._clean_caption(CAPTION))
        self.assertEqual(self.spawns, [], "no CLI process for a key-billed Haiku caption")
        self.assertEqual(len(self.api.requests), 1)
        r = self.api.requests[0]
        self.assertEqual(r["path"], "/v1/messages")
        self.assertEqual(r["headers"]["x-api-key"], HELD_1, "the held key, in the header")
        self.assertEqual(r["headers"]["anthropic-version"], "2023-06-01")
        b = r["body"]
        self.assertEqual(b["model"], "claude-haiku-4-5-20251001", "the id CLI 2.1.284 sends for the bare alias")
        self.assertEqual(b["max_tokens"], 32000)
        self.assertEqual(b["thinking"], {"type": "disabled"})
        self.assertEqual(b["temperature"], 1)
        self.assertTrue(b["system"].startswith(jd.CAPTION_SYS), "the captioner's own system prompt")
        self.assertEqual(len(b["messages"]), 1)
        self.assertEqual(b["messages"][0]["role"], "user")
        self.assertIn(UNIT, b["messages"][0]["content"])
        mark = re.search(r"<unit ([0-9a-f]{8})>", b["messages"][0]["content"]).group(1)
        self.assertEqual(b["system"], jd.CAPTION_SYS + jd.UNTRUSTED_SYS % (mark, mark),
                         "the per-call mark rides the system prompt, last, as on the CLI road")
        self.assertNotIn("cache_control", json.dumps(b), "no prompt caching, as DISABLE_PROMPT_CACHING asks the CLI")
        self.assertNotIn("stream", b)

    def test_the_gister_takes_the_same_road(self):
        self._helper([HELD_1])
        self.api.script = [(200, {}, _message("the feed card recency tint"))]
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(jd.gist_llm("why does the feed card tint stay grey"), "the feed card recency tint")
        self.assertEqual(self.spawns, [])
        self.assertTrue(self.api.requests[0]["body"]["system"].startswith(jd.GIST_SYS))
        self.assertEqual(self._usage()[-1]["judge"], "gister")

    def test_the_caption_is_the_one_the_cli_road_gives_for_the_same_model_text(self):
        self._helper([HELD_1])
        raw = "  'Wired the dark-mode toggle into settings.'  "
        self.api.script = [(200, {}, _message(raw))]
        direct = self._caption()
        os.environ["ROMP_JUDGE_DIRECT_API"] = "off"
        self.cli_result = raw
        cli = self._caption()
        self.assertEqual(len(self.spawns), 1)
        self.assertEqual(direct, cli)
        self.assertTrue(direct)

    def test_the_usage_row_keeps_its_fields_with_the_apis_token_counts(self):
        self._helper([HELD_1])
        usage = {"input_tokens": 1234, "output_tokens": 17, "cache_creation_input_tokens": 5,
                 "cache_read_input_tokens": 7}
        self.api.script = [(200, {}, _message(usage=usage))]
        self._caption()
        rows = self._usage()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        for f in ("in", "out", "cache_w", "cache_r", "cost", "ms", "sent", "recv", "model", "judge", "tier"):
            self.assertIn(f, row, f)
        self.assertEqual((row["in"], row["out"], row["cache_w"], row["cache_r"]), (1234, 17, 5, 7))
        self.assertAlmostEqual(row["cost"], 1234 * 1e-6 + 17 * 5e-6 + 5 * 1.25e-6 + 7 * 0.1e-6, places=12)
        self.assertEqual((row["model"], row["judge"], row["tier"], row["auth"], row["route"]),
                         ("haiku", "captioner", "index", "key", "api"))
        self.assertIsInstance(row["ms"], int)
        self.assertLessEqual(row["sent"], row["recv"])

    def test_one_connection_serves_sequential_calls(self):
        self._helper([HELD_1])
        for _ in range(5):
            self._caption()
        self.assertEqual(len(self.api.requests), 5)
        self.assertEqual(len({r["port"] for r in self.api.requests}), 1, "one keep-alive connection, reused")

    def test_connections_outlive_the_pool_that_opened_them(self):
        self._helper([HELD_1])

        def one(_):
            jd._judge_ctx.fsid = SID
            with contextlib.redirect_stderr(io.StringIO()):
                return jd.caption_llm(UNIT)
        for _ in range(3):                            # the index pass makes a fresh thread pool every pass
            with ThreadPoolExecutor(max_workers=2) as ex:
                list(ex.map(one, range(2)))
        self.assertEqual(len(self.api.requests), 6)
        self.assertLessEqual(len({r["port"] for r in self.api.requests}), 2, "later passes reuse the idle connections")

    def test_a_connection_the_server_closed_is_replaced_without_a_retry(self):
        self._helper([HELD_1])
        self.api.drop_after = True
        self._caption()
        self.api.drop_after = False
        time.sleep(0.05)                              # let the server's close land
        backoff, jd._DIRECT_API_BACKOFF_S = jd._DIRECT_API_BACKOFF_S, (5.0, 8.0)   # a retry would wait 3.75 s or more
        try:
            t0 = time.monotonic()
            self.assertEqual(self._caption(), jd._clean_caption(CAPTION))
            self.assertLess(time.monotonic() - t0, 2.0, "a fresh connection at once, not a backoff retry")
        finally:
            jd._DIRECT_API_BACKOFF_S = backoff
        self.assertEqual(len(self.api.requests), 2, "one request reached the API for the second call")
        self.assertEqual(len({r["port"] for r in self.api.requests}), 2)

    def test_the_road_adds_no_concurrency_of_its_own(self):
        self._helper([HELD_1])
        self.api.delay = 0.05

        def one(_):
            jd._judge_ctx.fsid = SID
            with contextlib.redirect_stderr(io.StringIO()):
                return jd.caption_llm(UNIT)
        with ThreadPoolExecutor(max_workers=3) as ex:
            outs = list(ex.map(one, range(12)))
        self.assertEqual(outs, [jd._clean_caption(CAPTION)] * 12)
        self.assertLessEqual(self.api.max_inflight, 3, "the pool's cap is the cap")


class WhoStaysOnTheCLI(DirectRoadBase):
    def test_the_off_switch_restores_the_cli(self):
        self._helper([HELD_1])
        os.environ["ROMP_JUDGE_DIRECT_API"] = "off"
        self.assertEqual(self._caption(), jd._clean_caption(CAPTION))
        self.assertEqual(len(self.spawns), 1)
        self.assertEqual(self.spawns[0][1], HELD_1, "the CLI child carries the held key as before")
        self.assertEqual(self.api.requests, [])
        self.assertNotIn("route", self._usage()[-1])

    def test_a_login_billed_call_keeps_the_cli(self):
        self._helper([HELD_1])
        self._reg("login")
        self.assertEqual(self._caption(), jd._clean_caption(CAPTION))
        self.assertEqual(len(self.spawns), 1)
        self.assertIsNone(self.spawns[0][1])
        self.assertEqual(self.api.requests, [])
        self.assertEqual(_runs(self.vault), 0, "a login-billed call never touches the held key")

    def test_other_judges_and_models_keep_the_cli(self):
        self._helper([HELD_1])
        with contextlib.redirect_stderr(io.StringIO()):
            jd._judge_run("haiku", "SYS", "u", judge="archiver", tier="index")
            jd._judge_run("haiku", "SYS", "u", judge="titler", tier="index")
            jd._judge_run("sonnet", "SYS", "u", judge="planner", tier="triage")
            jd._judge_run("sonnet", "SYS", "u", judge="captioner", tier="index")
            jd._judge_run("claude-fable-5-1", "SYS", "u", judge="gister", tier="index")
        self.assertEqual(len(self.spawns), 5)
        self.assertEqual(self.api.requests, [])

    def test_a_pinned_haiku_id_goes_direct_as_pinned(self):
        self._helper([HELD_1])
        with contextlib.redirect_stderr(io.StringIO()):
            jd._judge_run("claude-haiku-4-5", "SYS", "u", judge="captioner", tier="index")
        self.assertEqual(self.api.requests[0]["body"]["model"], "claude-haiku-4-5")

    def test_no_held_key_keeps_the_cli_and_says_so_once(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            jd.caption_llm(UNIT)
            jd.caption_llm(UNIT)
        self.assertEqual(len(self.spawns), 2)
        self.assertEqual(self.api.requests, [])
        self.assertEqual(err.getvalue().count("no held API key"), 1)

    def test_a_proxy_setting_keeps_the_cli_and_says_so_once(self):
        self._helper([HELD_1])
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:9"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            jd.caption_llm(UNIT)
            jd.caption_llm(UNIT)
        self.assertEqual(len(self.spawns), 2)
        self.assertEqual(self.api.requests, [])
        self.assertEqual(err.getvalue().count("HTTPS_PROXY is set"), 1)


class FailuresAreLoud(DirectRoadBase):
    def test_a_missing_helper_fails_loudly_and_sends_nothing(self):
        Path(self.cfg, "settings.json").write_text(json.dumps({"apiKeyHelper": os.path.join(self.cfg, "no-such")}))
        self.assertEqual(self._caption(), "")
        note = "apiKeyHelper is not on the manager's PATH (exit 127)"
        self.assertEqual(self.api.requests, [], "no request without a key")
        self.assertEqual(self.spawns, [], "and no fall onto the CLI's own helper")
        self.assertEqual(jd._auth_down_map()[SID]["note"], note)
        self.assertIn(("auth", note), [(r["err"], r["note"]) for r in self._errors()])

    def test_an_empty_key_never_reaches_the_wire(self):
        route = {"model": "claude-haiku-4-5-20251001", "price": jd._DIRECT_API_PRICES[(4, 5)], "scheme": "http",
                 "host": "127.0.0.1", "port": self.api.server.server_address[1], "path": "/v1/messages"}
        p = jd._direct_api_run(route, "SYS", "u", "")
        w = json.loads(p.stdout)
        self.assertTrue(w["is_error"])
        self.assertTrue(jd._is_auth_error(w["result"]), "a credential-class error, which latches")
        self.assertEqual(self.api.requests, [])

    def test_a_401_brings_one_refresh_and_one_retry(self):
        self._helper([HELD_1, HELD_2])
        self.assertTrue(self._caption())
        refused = {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
        self.api.script = [(200, {}, _message()),
                           (401, {"x-should-retry": "false"}, refused),
                           (200, {}, _message())]
        self.now[0] += cred.HELD_KEY_REFRESH_GAP_S + 1
        self.assertEqual(self._caption(), jd._clean_caption(CAPTION), "refused, refreshed, retried once, served")
        self.assertEqual([r["headers"]["x-api-key"] for r in self.api.requests], [HELD_1, HELD_1, HELD_2])
        self.assertEqual(_runs(self.vault), 2, "exactly one refresh")
        self.assertEqual(jd._auth_down_map(), {})

    def test_overloads_retry_bounded_then_serve(self):
        self._helper([HELD_1])
        over = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
        self.api.script = [(529, {"retry-after": "0"}, over), (429, {"retry-after": "0"}, over), (200, {}, _message())]
        self.assertEqual(self._caption(), jd._clean_caption(CAPTION))
        self.assertEqual(len(self.api.requests), 3)
        self.assertEqual(len(self._usage()), 1)

    def test_overloads_past_the_bound_are_an_error_envelope(self):
        self._helper([HELD_1])
        over = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
        self.api.script = [(529, {"retry-after": "0"}, over)]
        self.assertEqual(self._caption(), "")
        self.assertEqual(len(self.api.requests), 1 + jd._DIRECT_API_RETRIES)
        self.assertEqual(self._usage(), [], "no usage row for an error envelope, as on the CLI road")
        row = self._errors()[-1]
        self.assertEqual(row["err"], "call")
        self.assertIn("529 overloaded_error", row["note"])
        self.assertFalse(jd._judge_ctx.last_call_fail["refusal"])
        self.assertEqual(jd._auth_down_map(), {})

    def test_a_400_is_not_retried(self):
        self._helper([HELD_1])
        bad = {"type": "error", "error": {"type": "invalid_request_error", "message": "synthetic bad request"}}
        self.api.script = [(400, {}, bad)]
        self.assertEqual(self._caption(), "")
        self.assertEqual(len(self.api.requests), 1)

    def test_a_refusal_is_a_content_refusal(self):
        self._helper([HELD_1])
        self.api.script = [(200, {}, _message("", stop="refusal"))]
        self.assertEqual(self._caption(), "")
        self.assertTrue(jd._judge_ctx.last_call_fail["refusal"], "the content-refusal class: struck, never latched")
        self.assertEqual(self._usage(), [])

    def test_a_call_past_the_alarm_comes_back_in_the_kill_shape(self):
        self.api.delay = 1.0
        route = {"model": "claude-haiku-4-5-20251001", "price": jd._DIRECT_API_PRICES[(4, 5)], "scheme": "http",
                 "host": "127.0.0.1", "port": self.api.server.server_address[1], "path": "/v1/messages"}
        t0 = time.monotonic()
        p = jd._direct_api_run(route, "SYS", "u", HELD_1, deadline_s=0.3)
        self.assertLess(time.monotonic() - t0, 0.9)
        self.assertEqual((p.stdout, p.returncode), ("", jd._KILL_RC))


class TheKeyStaysSecret(DirectRoadBase):
    def test_the_key_never_reaches_a_log_line_or_any_written_file(self):
        self._helper([HELD_1, HELD_2])
        (jd.STATE / "debug-mode.json").write_text(json.dumps({"on": True}))   # error rows carry input and reply

        def quoting(r):                               # a server that quotes the key it was sent, everywhere it can
            k = r["headers"]["x-api-key"]
            return {"type": "error", "error": {"type": "invalid_request_error", "message": "bad key %s" % k}}

        def quoting_401(r):
            k = r["headers"]["x-api-key"]
            return {"type": "error", "error": {"type": "authentication_error", "message": "refused %s" % k}}
        self.api.script = [(200, {}, lambda r: _message("Captioned %s" % r["headers"]["x-api-key"])),
                           (400, {}, quoting), (401, {}, quoting_401), (401, {}, quoting_401)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            jd.caption_llm(UNIT)
            jd.caption_llm(UNIT)
            self.now[0] += cred.HELD_KEY_REFRESH_GAP_S + 1
            jd.caption_llm(UNIT)
        self.assertGreaterEqual(len(self.api.requests), 4)
        self.assertEqual({r["headers"]["x-api-key"] for r in self.api.requests}, {HELD_1, HELD_2})
        self.assertIn("[key withheld]", jd.ERRORS.read_text(), "the quotes were written, blanked (the walk below "
                                                               "is not vacuous)")
        for value in (HELD_1, HELD_2):
            self.assertNotIn(value, err.getvalue(), "the service log")
            self.assertNotIn(value, json.dumps(dict(os.environ)), "the judge process's own environment")
        leaked = []
        for dirpath, _dirs, files in os.walk(self.root):
            for name in files:
                data = Path(dirpath, name).read_bytes()
                leaked += [os.path.join(dirpath, name) for v in (HELD_1, HELD_2) if v.encode() in data]
        self.assertEqual(leaked, [], "no file under the state root or the config dir holds the key")


if __name__ == "__main__":
    unittest.main()
