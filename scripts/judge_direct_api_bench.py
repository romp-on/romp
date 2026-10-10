#!/usr/bin/env python3
"""Measure what one captioner call costs the machine on each judge road: a `claude -p` process per call (the CLI
road) versus the judge process calling the Messages API itself (the direct road, 2026-10-06).

Everything here is synthetic and local. A stand-in Messages API (stdlib http.server, run as a SEPARATE process so
its own CPU is not counted) answers every request with a canned caption after a fixed delay and realistic usage
fields; it speaks both the streaming form the CLI asks for and the plain JSON form the direct road asks for. The
real claude CLI is pointed at it with ANTHROPIC_BASE_URL and runs under a throwaway HOME and CLAUDE_CONFIG_DIR, so
it reads no settings and writes no transcript anywhere real; the key is a dummy string handed to the judges' key
holder by an in-memory runner. No real key is read and the real API is never called: the harness refuses to start
if ANTHROPIC_BASE_URL would point anywhere but the stand-in.

For each road and each concurrency it replays N synthetic caption tasks through kernel/judge.py's own caption_llm
(the same code the kernel runs, prompt building and parsing included) on a thread pool of that size, and reports:
  core-seconds per call: user+sys CPU of this process plus every child it reaped (the CLI processes), over N;
  wall per call: mean and p95 of each call's own duration;
  calls per hour: N over the batch's wall time, at that concurrency;
  cores busy: core-seconds over the batch's wall time while it ran;
  cores at the stated hourly volume (--volume, default 8,000: about 7,000 captions and 1,000 gists at peak).

The machine is shared: before each batch the harness waits until the one-minute load average is under --load-max
(default 0.75 x cores), and gives up on that batch after --load-wait seconds, saying so.

    python3 scripts/judge_direct_api_bench.py --n 200 --conc 6,3,2 --delay 0.9
"""
import argparse
import http.server
import importlib.util
import json
import os
import random
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DUMMY_KEY = "bench-dummy-value-not-a-credential"

# Invented work for the captioner, on the neutral demo project the docs use (notes-api, with web / api / tests).
_ASKS = ["add a dark-mode toggle to the settings page", "fix the flaky pagination test in the notes list",
         "rename the tag endpoint to labels", "make the search box debounce its requests",
         "add a created-at sort to the notes index", "log slow queries over 200 ms in the api",
         "move the markdown renderer into its own module", "add a regression test for empty note titles",
         "cache the user profile lookup for a minute", "show a toast when a note fails to save"]
_FILES = ["web/settings.ts", "web/notes/list.tsx", "api/routes/tags.py", "web/search.tsx", "api/notes/index.py",
          "api/db/log.py", "web/render/markdown.ts", "tests/test_notes.py", "api/users/profile.py", "web/toast.tsx"]
_CAPTIONS = ["Added a dark-mode toggle to settings", "Fixed the flaky pagination test", "Renamed the tag endpoint",
             "Debounced the search box requests", "Added a created-at sort to the index", "Logged slow api queries",
             "Moved the markdown renderer out", "Added an empty-title regression test", "Cached the profile lookup",
             "Added a save-failure toast"]


def synthetic_units(n, seed=7):
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        k = rnd.randrange(len(_ASKS))
        steps = " ".join("Step %d: edited %s and re-ran the tests." % (j + 1, _FILES[(k + j) % len(_FILES)])
                         for j in range(rnd.randint(2, 8)))
        out.append("USER: %s (task %d)\nASSISTANT: %s\nTOOLS USED: Edit %s, Bash run the tests"
                   % (_ASKS[k], i, steps, _FILES[k]))
    return out


# ── the stand-in Messages API (run as its own process: --serve) ──────────────────────────────────────────────
def serve(port, delay):
    rnd = random.Random(11)
    lock = threading.Lock()

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(404)
            self.send_header("content-length", "0")
            self.end_headers()

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("content-length") or 0))
            try:
                req = json.loads(body or b"{}")
            except ValueError:
                req = {}
            chars = len(json.dumps(req.get("system") or "")) + len(json.dumps(req.get("messages") or ""))
            with lock:
                cap = rnd.choice(_CAPTIONS)
            usage = {"input_tokens": max(1, chars // 4), "output_tokens": 12, "cache_creation_input_tokens": 0,
                     "cache_read_input_tokens": 0}
            time.sleep(delay)
            model = req.get("model") or "claude-haiku-4-5-20251001"
            if req.get("stream"):
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("connection", "close")
                self.end_headers()
                events = [("message_start", {"type": "message_start", "message": {
                              "id": "msg_bench", "type": "message", "role": "assistant", "model": model, "content": [],
                              "stop_reason": None, "usage": dict(usage, output_tokens=1)}}),
                          ("content_block_start", {"type": "content_block_start", "index": 0,
                                                   "content_block": {"type": "text", "text": ""}}),
                          ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                                   "delta": {"type": "text_delta", "text": cap}}),
                          ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                          ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                                             "usage": {"output_tokens": usage["output_tokens"]}}),
                          ("message_stop", {"type": "message_stop"})]
                for ev, data in events:
                    self.wfile.write(("event: %s\ndata: %s\n\n" % (ev, json.dumps(data))).encode())
                self.wfile.flush()
                self.close_connection = True
                return
            out = json.dumps({"id": "msg_bench", "type": "message", "role": "assistant", "model": model,
                              "content": [{"type": "text", "text": cap}], "stop_reason": "end_turn",
                              "usage": usage}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    print(srv.server_address[1], flush=True)
    srv.serve_forever()


# ── the replay ───────────────────────────────────────────────────────────────────────────────────────────────
def _cpu(who):
    r = resource.getrusage(who)
    return r.ru_utime + r.ru_stime


def _wait_for_load(load_max, load_wait):
    t0 = time.monotonic()
    while os.getloadavg()[0] > load_max:
        if time.monotonic() - t0 > load_wait:
            return False
        time.sleep(10)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--serve", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--n", type=int, default=200, help="caption tasks per batch")
    ap.add_argument("--conc", default="6,3,2", help="concurrencies, comma-separated")
    ap.add_argument("--paths", default="cli,direct", help="roads to measure: cli, direct")
    ap.add_argument("--delay", type=float, default=0.9, help="the stand-in API's answer delay, seconds")
    ap.add_argument("--volume", type=int, default=8000, help="calls an hour to price in cores")
    ap.add_argument("--load-max", type=float, default=0.75 * (os.cpu_count() or 1))
    ap.add_argument("--load-wait", type=float, default=600.0)
    ap.add_argument("--out", help="write the result rows as JSON here")
    a = ap.parse_args()
    if a.serve is not None:
        return serve(a.serve, a.delay)

    claude = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    root = Path(tempfile.mkdtemp(prefix="romp-judge-bench-"))
    srv = subprocess.Popen([sys.executable, __file__, "--serve", "0", "--delay", str(a.delay)],
                           stdout=subprocess.PIPE, text=True)
    port = int(srv.stdout.readline())
    base = "http://127.0.0.1:%d" % port
    # The judge module resolves its state root and the CLI child inherits this environment: set both BEFORE the load.
    for k in list(os.environ):
        if k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_CUSTOM_HEADERS",
                 "ROMP_STATE_DIR", "ROMP_JUDGE_DIRECT_API") or k.lower().endswith("_proxy"):
            os.environ.pop(k)
    for d in ("state", "home", "claude-config"):
        (root / d).mkdir()
    os.environ.update({"XDG_STATE_HOME": str(root / "state"), "HOME": str(root / "home"),
                       "CLAUDE_CONFIG_DIR": str(root / "claude-config"), "ROMP_CLAUDE_BIN": claude,
                       "ANTHROPIC_BASE_URL": base, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                       "ROMP_DISTILLER_NOTES": str(root / "no-notes.md"), "ROMP_KERNEL_NO_OPEN": "1",
                       "ROMP_SERVICE_ENV_FILE": str(root / "no-service.env"), "ROMP_SERVICE_ENV": str(root / "no-service.env")})
    if os.environ["ANTHROPIC_BASE_URL"] != base:
        sys.exit("refusing: the API base is not the local stand-in")
    spec = importlib.util.spec_from_file_location("romp_judge_bench", str(REPO / "kernel" / "judge.py"))
    jd = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(REPO / "kernel"))
    spec.loader.exec_module(jd)
    jd._KEY_SOURCE = jd._cred.HeldKey(lambda: "bench-holder", env_name="ANTHROPIC_API_KEY", label="apiKeyHelper",
                                      runner=lambda cmd: DUMMY_KEY)   # in memory: no helper runs, no file holds it
    jd._DEFAULT_AUTH_FN = lambda reg: "key"
    jd._judge_engine = lambda: "claude"

    units = synthetic_units(a.n)
    rows = []
    try:
        for road in [p.strip() for p in a.paths.split(",") if p.strip()]:
            os.environ["ROMP_JUDGE_DIRECT_API"] = "on" if road == "direct" else "off"
            jd.caption_llm(units[0])                   # warm: the CLI's first run under a fresh config dir
            for conc in [int(c) for c in a.conc.split(",") if c.strip()]:
                if not _wait_for_load(a.load_max, a.load_wait):
                    print("skipped %s at concurrency %d: load stayed over %.0f" % (road, conc, a.load_max), flush=True)
                    continue
                load0 = os.getloadavg()[0]
                lat = []

                def one(u):
                    t = time.monotonic()
                    cap = jd.caption_llm(u)
                    lat.append(time.monotonic() - t)
                    return cap
                s0, c0, t0 = _cpu(resource.RUSAGE_SELF), _cpu(resource.RUSAGE_CHILDREN), time.monotonic()
                with ThreadPoolExecutor(max_workers=conc) as ex:
                    caps = list(ex.map(one, units))
                wall = time.monotonic() - t0
                core = (_cpu(resource.RUSAGE_SELF) - s0) + (_cpu(resource.RUSAGE_CHILDREN) - c0)
                ok = sum(1 for c in caps if c)
                row = {"road": road, "concurrency": conc, "n": a.n, "served": ok, "delay_s": a.delay,
                       "core_s_per_call": core / a.n, "wall_s_per_call_mean": statistics.mean(lat),
                       "wall_s_per_call_p95": sorted(lat)[int(0.95 * (len(lat) - 1))], "batch_wall_s": wall,
                       "calls_per_hour": 3600.0 * a.n / wall, "cores_busy": core / wall,
                       "cores_at_volume": a.volume * (core / a.n) / 3600.0, "volume_per_hour": a.volume,
                       "load1_before": load0}
                rows.append(row)
                print(json.dumps(row), flush=True)
    finally:
        srv.terminate()
        srv.wait()
        shutil.rmtree(root, ignore_errors=True)
    print("\nroad    conc  served  core-s/call  wall/call (mean, p95)  calls/hour  cores busy  cores at %d/h"
          % a.volume)
    for r in rows:
        print("%-7s %4d  %3d/%-3d  %10.3f  %8.2f s %8.2f s  %10.0f  %10.2f  %12.2f"
              % (r["road"], r["concurrency"], r["served"], r["n"], r["core_s_per_call"], r["wall_s_per_call_mean"],
                 r["wall_s_per_call_p95"], r["calls_per_hour"], r["cores_busy"], r["cores_at_volume"]))
    if a.out:
        Path(a.out).write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
