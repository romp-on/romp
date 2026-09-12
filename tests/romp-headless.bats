#!/usr/bin/env bats

# `romp send|interrupt|end <session> [text]` — headless session control through the kernel
# HTTP API (2026-07-05: interrupt/end existed only as browser WS ops, so a runaway session had no
# headless stop). Bare words since round 3 (2026-07-25); the dashed spellings stay as SILENT
# aliases because agent-facing text delivered before then names them. A tiny one-shot python
# server stands in for the kernel; failures must be LOUD (non-zero exit + a message), never a
# silent curl swallow.

ROMP_SCRIPT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp"

setup() {
    TEST_DIR="$(mktemp -d)"
}

teardown() {
    [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null
    rm -rf "$TEST_DIR"
}

# Start a one-shot fake kernel; writes its port to $TEST_DIR/port and its request to $TEST_DIR/req.
# $2 (optional): seconds to hold the answer AFTER reading the request — a kernel that took the message
# but answers late (the boot-storm shape the exit-code test below drives).
start_fake_kernel() {   # $1 = response body, $2 = answer delay in seconds (default 0)
    python3 - "$1" "$TEST_DIR" "${2:-0}" <<'PY' &
import http.server, json, sys, time
body, tdir, delay = sys.argv[1].encode(), sys.argv[2], float(sys.argv[3])
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        with open(tdir + "/req", "w") as f:
            f.write(self.path + "\n" + self.rfile.read(n).decode())
        if delay:
            time.sleep(delay)
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
s = http.server.HTTPServer(("127.0.0.1", 0), H)
with open(tdir + "/port", "w") as f:
    f.write(str(s.server_address[1]))
s.handle_request()
PY
    SERVER_PID=$!
    until [ -s "$TEST_DIR/port" ]; do sleep 0.05; done
    export ROMP_KERNEL_PORT="$(cat "$TEST_DIR/port")"
}

@test "romp interrupt <name> POSTs /interrupt and exits 0 on ok" {
    start_fake_kernel '{"ok": true}'
    run "$ROMP_SCRIPT" interrupt runaway
    [ "$status" -eq 0 ]
    [[ "$output" == *"ok (runaway)"* ]]
    grep -q "^/interrupt$" <(head -1 "$TEST_DIR/req")
    grep -q '"name": "runaway"' "$TEST_DIR/req"
}

@test "romp end stops a session through /end" {
    start_fake_kernel '{"ok": true}'
    run "$ROMP_SCRIPT" end done-with-it
    [ "$status" -eq 0 ]
    grep -q "^/end$" <(head -1 "$TEST_DIR/req")
}

@test "romp end self resolves through ROMP_SID and defers to idle by default" {
    # a session closing ITSELF after its work (the user 2026-08-15): self = the spawn-frozen sid,
    # and the kernel kills at the turn's settle so the goodbye lands first
    start_fake_kernel '{"ok": true}'
    ROMP_SID="11111111-2222-3333-4444-555555555555" run "$ROMP_SCRIPT" end self
    [ "$status" -eq 0 ]
    grep -q "^/end$" <(head -1 "$TEST_DIR/req")
    grep -q '"id": "11111111-2222-3333-4444-555555555555"' "$TEST_DIR/req"
    grep -q '"when": "idle"' "$TEST_DIR/req"
}

@test "romp end self --now skips the deferral; self outside a session fails loudly" {
    start_fake_kernel '{"ok": true}'
    ROMP_SID="11111111-2222-3333-4444-555555555555" run "$ROMP_SCRIPT" end self --now
    [ "$status" -eq 0 ]
    run grep -q '"when"' "$TEST_DIR/req"
    [ "$status" -ne 0 ]
    ROMP_SID="" run "$ROMP_SCRIPT" end self
    [ "$status" -eq 2 ]
    [[ "$output" == *"only works from inside a romp SDK session"* ]]
}

@test "the dashed spellings are silent aliases: --send works and says nothing about it" {
    # Agent-facing text delivered before 2026-07-25 (postal reply footers, skill
    # docs in old transcripts) names the dashed forms; they must keep working
    # with no retirement noise.
    start_fake_kernel '{"ok": true}'
    run "$ROMP_SCRIPT" --send helper "hello"
    [ "$status" -eq 0 ]
    [[ "$output" != *"retired"* ]]
    grep -q "^/send$" <(head -1 "$TEST_DIR/req")
    grep -q '"name": "helper"' "$TEST_DIR/req"
}

@test "romp send ships JSON-safe text" {
    start_fake_kernel '{"ok": true}'
    run "$ROMP_SCRIPT" send helper 'fix the "thing" \ and this'
    [ "$status" -eq 0 ]
    grep -q "^/send$" <(head -1 "$TEST_DIR/req")
    python3 - "$TEST_DIR/req" <<'PY'
import json, sys
body = open(sys.argv[1]).read().split("\n", 1)[1]
assert json.loads(body) == {"name": "helper", "text": 'fix the "thing" \\ and this'}, body
PY
}

@test "romp send --tag appends the render-hint marker; bad labels and missing text exit 2" {
    start_fake_kernel '{"ok": true}'
    run "$ROMP_SCRIPT" send helper --tag kickoff 'boot brief for the run'
    [ "$status" -eq 0 ]
    python3 - "$TEST_DIR/req" <<'PY'
import json, sys
body = open(sys.argv[1]).read().split("\n", 1)[1]
d = json.loads(body)
assert d["name"] == "helper", d
assert d["text"] == "boot brief for the run\n\n<!-- romp-tag: kickoff -->", d
PY
    run "$ROMP_SCRIPT" send helper --tag 'two words' 'text'
    [ "$status" -eq 2 ]
    [[ "$output" == *"--tag must be one word"* ]]
    run "$ROMP_SCRIPT" send helper --tag kickoff
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp send"* ]]
}

@test "romp send reports queued when the kernel parked it" {
    # a sender inside the target's own open turn (an agent sending itself a slash command) must learn
    # the command has not run yet (2026-09-03: a parked /clear read 'ok' and never fired)
    start_fake_kernel '{"ok": true, "queued": true}'
    run "$ROMP_SCRIPT" send busy1 "/frobnicate now"
    [ "$status" -eq 0 ]
    [[ "$output" == *"romp send: queued (busy1)"* ]]
    [[ "$output" == *"delivers when the session is quiet"* ]]
}

@test "romp send still says ok on queued:false and on a bare ok reply" {
    start_fake_kernel '{"ok": true, "queued": false}'
    run "$ROMP_SCRIPT" send web "hello"
    [ "$status" -eq 0 ]
    [[ "$output" == *"romp send: ok (web)"* ]]
}

@test "a kernel refusal is loud: non-zero exit + the kernel's answer" {
    start_fake_kernel '{"ok": false, "error": "id or name required"}'
    run "$ROMP_SCRIPT" interrupt ghost
    [ "$status" -eq 1 ]
    [[ "$output" == *"refused"* ]]
    [[ "$output" == *"id or name required"* ]]   # the kernel's own words, not the raw body
}

@test "an unreachable kernel is loud, not a silent curl swallow" {
    ROMP_KERNEL_PORT=1 run "$ROMP_SCRIPT" interrupt anyone
    [ "$status" -eq 1 ]
    [[ "$output" == *"kernel not reachable"* ]]
}

@test "usage errors exit 2: missing session name, send without text" {
    run "$ROMP_SCRIPT" interrupt
    [ "$status" -eq 2 ]
    run "$ROMP_SCRIPT" send lonely
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp send"* ]]
}

# ── romp compact (2026-08-30, the user via the dashboard team) ──
# First-class in-place compaction: POSTs /compact, tells the caller which arm ran (now vs queued),
# refuses honestly. The one-shot fake kernel captures the request like the send/interrupt tests.

@test "romp compact <name> POSTs /compact and says compacting now" {
    start_fake_kernel '{"ok": true, "queued": false}'
    run "$ROMP_SCRIPT" compact bigctx
    [ "$status" -eq 0 ]
    [[ "$output" == *"compacting bigctx now"* ]]
    grep -q "^/compact$" <(head -1 "$TEST_DIR/req")
    grep -q '"name": "bigctx"' "$TEST_DIR/req"
}

@test "romp compact reports queued when a turn is open" {
    start_fake_kernel '{"ok": true, "queued": true}'
    run "$ROMP_SCRIPT" compact busy1
    [ "$status" -eq 0 ]
    [[ "$output" == *"queued for busy1"* ]]
    [[ "$output" == *"fires the moment the current turn ends"* ]]
}

@test "romp compact refusals are loud: dead session, unreachable kernel, usage" {
    start_fake_kernel '{"ok": false, "error": "no live session named '"'"'ghost'"'"' — a dead session has no context to compact; revive it first"}'
    run "$ROMP_SCRIPT" compact ghost
    [ "$status" -eq 1 ]
    [[ "$output" == *"revive it first"* ]]
    ROMP_KERNEL_PORT=1 run "$ROMP_SCRIPT" compact anyone
    [ "$status" -eq 1 ]
    [[ "$output" == *"kernel not reachable"* ]]
    run "$ROMP_SCRIPT" compact
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp compact"* ]]
    run "$ROMP_SCRIPT" compact who --timeout notanumber
    [ "$status" -eq 2 ]
}

@test "romp help lists compact beside the other session verbs" {
    run "$ROMP_SCRIPT" help
    [[ "$output" == *"romp compact <session>"* ]]
}

# The queued+--wait path died before its first poll (set -e killed the arming assignment — review
# find, 2026-08-30) and the --wait fake below is MULTI-request: POST answers queued, then GET
# /sessions walks quiet → compacting → quiet, the armed-only-after-quiet sequence. A leading-zero
# --timeout was octal to (( )) and the timeout never fired; a remote response refuses --wait
# honestly (the local /sessions never lists remote rows).

start_wait_kernel() {   # $1 = POST response body; GETs serve quiet,compacting,compacting,quiet…
    python3 - "$1" "$TEST_DIR" <<'PY' &
import http.server, json, sys
body, tdir = sys.argv[1].encode(), sys.argv[2]
hits = {"n": 0}
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); self.rfile.read(n)
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        hits["n"] += 1
        rows = [{"id": "11111111-2222-3333-4444-555555555555", "name": "busy1",
                 "compacting": hits["n"] in (2, 3)}]
        b = json.dumps(rows).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers()
        self.wfile.write(b)
    def log_message(self, *a):
        pass
s = http.server.HTTPServer(("127.0.0.1", 0), H)
with open(tdir + "/port", "w") as f:
    f.write(str(s.server_address[1]))
for _ in range(12):
    s.handle_request()
PY
    SERVER_PID=$!
    until [ -s "$TEST_DIR/port" ]; do sleep 0.05; done
    export ROMP_KERNEL_PORT="$(cat "$TEST_DIR/port")"
}

@test "romp compact --wait on a QUEUED compaction survives set -e, arms after quiet, and completes" {
    start_wait_kernel '{"ok": true, "queued": true}'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 30
    [ "$status" -eq 0 ]
    [[ "$output" == *"queued for busy1"* ]]
    [[ "$output" == *"done — busy1 compacted"* ]]
}

@test "romp compact --wait refuses a leading-zero timeout (octal to the poll arithmetic)" {
    run "$ROMP_SCRIPT" compact who --timeout 08
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp compact"* ]]
}

@test "romp compact --wait on a remote session refuses honestly instead of reporting it dead" {
    start_fake_kernel '{"ok": true, "queued": false, "remote": "TESTHOST-B"}'
    run "$ROMP_SCRIPT" compact farswitch --wait --timeout 10
    [ "$status" -eq 1 ]
    [[ "$output" == *"compacting farswitch now"* ]]
    [[ "$output" == *"can't follow a remote session from here (it lives on TESTHOST-B)"* ]]
    [[ "$output" == *"still requested"* ]]
}

@test "a dash-leading session name reaches the kernel like send does; the verb's own flags still get usage" {
    start_fake_kernel '{"ok": true, "queued": false}'
    run "$ROMP_SCRIPT" compact -oddname
    [ "$status" -eq 0 ]
    grep -q '"name": "-oddname"' "$TEST_DIR/req"
    run "$ROMP_SCRIPT" compact --wait
    [ "$status" -eq 2 ]
}

@test "romp send: a kernel that took the request but answers late exits 3 and says the message may be delivered" {
    # 2026-09-12: this shape wore "kernel not reachable" and exit 1, so a retry-on-exit caller re-sent a delivered
    # message on every try (nine copies of one wake, twenty seconds apart, after a restart). The request must be
    # on the fake kernel's disk (it was taken), the exit distinct from a refusal, and the words honest.
    start_fake_kernel '{"ok": true}' 3
    ROMP_KERNEL_HTTP_TIMEOUT_S=1 run "$ROMP_SCRIPT" send helper 'a wake the kernel took slowly'
    [ "$status" -eq 3 ]
    [[ "$output" == *"took the request but did not answer within 1s"* ]]
    [[ "$output" == *"may already have delivered the message"* ]]
    [[ "$output" == *"do not retry blindly"* ]]
    [[ "$output" != *"not reachable"* ]]
    grep -q "^/send$" <(head -1 "$TEST_DIR/req")
}

@test "romp send: a kernel nobody is listening on is 'not reachable', exit 1, and nothing was sent" {
    # a port with no listener: the request never left, so the old message and code stand, and the curl code is named
    export ROMP_KERNEL_PORT=1
    run "$ROMP_SCRIPT" send helper 'a message nobody took'
    [ "$status" -eq 1 ]
    [[ "$output" == *"kernel not reachable"* ]]
    [[ "$output" == *"[curl exit"* ]]
}
