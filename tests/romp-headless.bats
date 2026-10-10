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
    def do_GET(self):   # `romp compact --wait` reads /sessions once BEFORE its POST (the baseline notice, 2026-09-21): no rows here
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"[]")
    def log_message(self, *a):
        pass
class _Bound(http.server.HTTPServer):   # no reverse lookup of the bind address: HTTPServer.server_bind runs socket.getfqdn(host), about 36 s on GitHub's macOS images
    def server_bind(self):
        import socketserver
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]
s = _Bound(("127.0.0.1", 0), H)
with open(tdir + "/port", "w") as f:
    f.write(str(s.server_address[1]))
for _ in range(4):   # the one POST, and the /sessions read a --wait makes before it; teardown ends the server either way
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

@test "romp end names how many waiting messages did not go through, and the file that keeps them" {
    # the kernel's answer counts the messages the user typed that were still waiting when the session ended and
    # came back as not delivered (`undelivered`, present only when nonzero, 2026-09-21): a caller with no chat pane
    # open, often a peer session, read a plain ok while the message went nowhere. The line names the count and
    # the file, never the text (the decoy key stands for any text a kernel's answer might carry), which would land
    # in that session's transcript, and no path: a remote session's kernel answers verbatim and its file is on that
    # machine, so a local path would name a file holding no such row. Exit 0, since the session did end.
    start_fake_kernel '{"ok": true, "undelivered": 1, "text": "decoy typed words"}'
    run "$ROMP_SCRIPT" end web
    [ "$status" -eq 0 ]
    [[ "$output" == *"romp end: ok (web); 1 message still waiting for it did not go through"* ]]
    [[ "$output" == *"kept in undelivered.jsonl under the state directory of the kernel that ran the session"* ]]
    [[ "$output" != *"decoy typed words"* ]]
    [[ "$output" != *" /"* && "$output" != *"(/"* ]]   # no absolute path anywhere in the line
    kill "$SERVER_PID"; rm -f "$TEST_DIR/port"
    start_fake_kernel '{"ok": true, "undelivered": 2}'
    run "$ROMP_SCRIPT" end web
    [ "$status" -eq 0 ]
    [[ "$output" == *"romp end: ok (web); 2 messages still waiting for it did not go through"* ]]
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

@test "romp send --from-user marks the person's own line in the body; without the flag the body names nothing" {
    # the chat page relays the person's words with --from-user, so the session queues them ahead of peer and machine
    # sends (2026-10-06); the flag works ahead of the session name and right after it, as --tag does. The test above
    # pins the unflagged body: no field at all.
    start_fake_kernel '{"ok": true}'
    run "$ROMP_SCRIPT" send --from-user helper 'hold the deploy until the web tests pass'
    [ "$status" -eq 0 ]
    python3 - "$TEST_DIR/req" <<'PY'
import json, sys
body = open(sys.argv[1]).read().split("\n", 1)[1]
assert json.loads(body) == {"name": "helper", "text": "hold the deploy until the web tests pass", "fromUser": True}, body
PY
    run "$ROMP_SCRIPT" send helper --from-user 'and tell me when the api pod is back'
    [ "$status" -eq 0 ]
    python3 - "$TEST_DIR/req" <<'PY'
import json, sys
body = open(sys.argv[1]).read().split("\n", 1)[1]
assert json.loads(body) == {"name": "helper", "text": "and tell me when the api pod is back", "fromUser": True}, body
PY
}

@test "romp send refuses --from-user together with --tag: a tagged text is machine-sent" {
    start_fake_kernel '{"ok": true}'
    run "$ROMP_SCRIPT" send --from-user --tag kickoff helper 'boot brief'
    [ "$status" -eq 2 ]
    [[ "$output" == *"--from-user"* ]]
    run "$ROMP_SCRIPT" send helper --tag kickoff --from-user 'boot brief'
    [ "$status" -eq 2 ]
    [[ "$output" == *"--from-user"* ]]
    [ ! -e "$TEST_DIR/req" ]
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
    # the failure exit is the Codex compaction's: its loud ends leave a notice on the row and a failed Claude /compact leaves
    # none, so that one reads as a clean end, never as a failure (done when a poll caught the compacting sample, the timeout
    # otherwise); and a queued compaction's baseline is the first sample after the request that reads not compacting, which
    # lands mid-turn. The line said "the compaction" with no backend and "the first sample after the turn" (the post-merge
    # review of the native compaction, 2026-09-21)
    [[ "$output" == *"when a Codex compaction ended loudly"* ]]
    [[ "$output" == *"a failed Claude /compact leaves no notice on the row, so it reads as a clean end, never as a failure"* ]]
    [[ "$output" == *"new since the first sample after the request that reads not compacting"* ]]
    [[ "$output" != *"first sample after the turn"* ]]
}

# The queued+--wait path died before its first poll (set -e killed the arming assignment — review
# find, 2026-08-30) and the --wait fake below is MULTI-request: POST answers queued, then GET
# /sessions walks quiet → compacting → quiet, the armed-only-after-quiet sequence. A leading-zero
# --timeout was octal to (( )) and the timeout never fired; a remote response refuses --wait
# honestly (the local /sessions never lists remote rows).

start_wait_kernel() {   # $1 = POST response body; $2 = the poll script; $3 = the baseline read's suffix; $4 = baseline reads to fail
    # The CLI reads /sessions once BEFORE its POST (the baseline, 2026-09-21) and polls it after. $2 scripts the polls
    # after the POST: comma-separated samples, each "q" (quiet) or "c" (compacting), with "A", "B", "R" or "T" appended
    # for the row's launch error (four notices: A the systemError end and B the notLoaded end, both marked a
    # compaction's end by noRetry as the kernel marks them; R the systemError end once more, A's words at a later
    # stamp, as a second compaction failing the same way leaves them (2026-09-21); T a turn's rejection, which carries
    # no mark), then optionally "/<n>" for the row's compactEnd, the backend's bracket-end record (the post-merge
    # review, 2026-09-21): a count of n whose last end is clean, "/<n>L" whose last end is loud with notice A's
    # words, or "/<n>X" whose last end is the restarted kernel's, loud with notice X's words (2026-09-22; a sample
    # without "/" is a row with no record: a Claude session, or a kernel from before the record); the
    # last sample repeating; the default "q,c,c,q" is the armed-only-after-quiet walk. $3 is the baseline read's
    # suffix in the same syntax ("A" = notice A from before this wait, "/0" = a record with no end yet). $4 makes that
    # many baseline reads answer 500 first (a kernel blip the CLI must retry).
    python3 - "$1" "$TEST_DIR" "${2:-q,c,c,q}" "${3:-}" "${4:-0}" <<'PY' &
import http.server, json, sys
body, tdir, script, base, basefail = sys.argv[1].encode(), sys.argv[2], sys.argv[3].split(","), sys.argv[4], int(sys.argv[5])
state = {"posted": False, "polls": 0, "failed": 0}
NOTICES = {"A": {"text": "Codex could not compact this conversation (it reported systemError); the conversation continues as it was",
                 "at": 1781100004.5, "limit": False, "noRetry": True},
           "B": {"text": "This conversation stopped being available while it was compacting (Codex reported notLoaded); nothing was compacted as far as romp can tell",
                 "at": 1781100009.5, "limit": False, "noRetry": True},
           "T": {"text": "codex turn/start rejected: the synthetic rejection a turn leaves as it ends",
                 "at": 1781100006.5, "limit": False},
           "X": {"text": "romp restarted while this conversation was compacting; whether Codex compacted it is unknown",
                 "at": 1781100019.5, "limit": False, "noRetry": True}}
NOTICES["R"] = dict(NOTICES["A"], at=1781100014.5)   # the same words as A at a new stamp: told from A by the stamp alone
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); self.rfile.read(n)
        state["posted"] = True
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if not state["posted"] and state["failed"] < basefail:
            state["failed"] += 1
            self.send_response(500); self.send_header("Content-Length", "0"); self.end_headers(); return
        if state["posted"]:
            state["polls"] += 1
            sample = script[min(state["polls"], len(script)) - 1]
        else:
            sample = "q" + base
        st, _, rec = sample.partition("/")
        row = {"id": "11111111-2222-3333-4444-555555555555", "name": "busy1",
               "compacting": st[0] == "c", "launchError": NOTICES.get(st[1:2])}
        if rec:   # the end record: "<n>" a count whose last end is clean, "<n>L" one whose last end is loud with A's words,
                  # "<n>X" one whose last end is the restarted kernel's, loud with X's words (2026-09-22). The stamp is
                  # one fixed value for "<n>" and the notice's for "<n>L" and "<n>X", so at one n the three are three
                  # different records under the CLI's identity judgment (the count with the stamp): a walk that flips
                  # between them at one count means a new end there, which the kernel never produces (it writes count
                  # and stamp together, once per end), so flip only to mean one (found in review, 2026-09-22)
            n, loud = int(rec.rstrip("LX")), rec[-1:] in ("L", "X")
            words = NOTICES["A" if rec[-1] == "L" else "X"] if loud else None
            row["compactEnd"] = {"ends": n, "kind": ("loud" if loud else "clean") if n else "",
                                 "text": words["text"] if loud else "",
                                 "at": (words["at"] if loud else 1781100005.5) if n else None}
        b = json.dumps([row]).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers()
        self.wfile.write(b)
    def log_message(self, *a):
        pass
class _Bound(http.server.HTTPServer):   # no reverse lookup of the bind address: HTTPServer.server_bind runs socket.getfqdn(host), about 36 s on GitHub's macOS images
    def server_bind(self):
        import socketserver
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]
s = _Bound(("127.0.0.1", 0), H)
with open(tdir + "/port", "w") as f:
    f.write(str(s.server_address[1]))
for _ in range(16):
    s.handle_request()
PY
    SERVER_PID=$!
    until [ -s "$TEST_DIR/port" ]; do sleep 0.05; done
    export ROMP_KERNEL_PORT="$(cat "$TEST_DIR/port")"
}

NOTICE_A="Codex could not compact this conversation"
NOTICE_X="romp restarted while this conversation was compacting"
NOTICE_B="This conversation stopped being available while it was compacting"
NOTICE_T="codex turn/start rejected"

@test "romp compact --wait on a QUEUED compaction survives set -e, arms after quiet, and completes" {
    start_wait_kernel '{"ok": true, "queued": true}'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 30
    [ "$status" -eq 0 ]
    [[ "$output" == *"queued for busy1"* ]]
    [[ "$output" == *"done — busy1 compacted"* ]]
}

# A compaction that ends loudly (Codex reported systemError) drops the row's compacting bit exactly as a clean end does,
# so the poll alone read "done" over an uncompacted thread and exited 0 (the second review of the native compaction,
# 2026-09-21). The row carries the backend's launch error; the wait exits 1 with the notice's words when that notice
# is NEW against the wait's baseline, whether or not a compacting sample was caught. The baseline is per path: a
# compaction that runs at once is judged against the row as read BEFORE the request (a loud end can land before the
# first poll); a queued one is judged against the first sample that reads not compacting, the earliest ours could have
# fired, so the prior compaction's loud end is not attributed to ours. A notice standing at the baseline is not this
# wait's. A baseline read that fails is retried, and a wait whose baseline never came is refused, not judged blind.
# Only a notice the kernel marks a compaction's end (noRetry) is judged: a queued wait arms mid-turn (the compacting
# bit is off for the whole turn it waits behind), so the turn's own end notice is new against the baseline while the
# compaction proceeds, and read as a loud end it failed a wait over a thread that was compacted (review, 2026-09-21).
@test "romp compact --wait exits 1 with the notice's words when the compaction it saw ended loudly" {
    start_wait_kernel '{"ok": true, "queued": false}' 'q,c,c,qA'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 30
    [ "$status" -eq 1 ]
    [[ "$output" == *"compacting busy1 now"* ]]
    [[ "$output" == *"busy1 did not compact: $NOTICE_A"* ]]
    [[ "$output" != *"busy1 compacted"* ]]
}

@test "romp compact --wait exits 1 on a loud end it never saw compacting, instead of waiting out the timeout" {
    start_wait_kernel '{"ok": true, "queued": false}' 'q,qA'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 30
    [ "$status" -eq 1 ]
    [[ "$output" == *"busy1 did not compact: $NOTICE_A"* ]]
    [[ "$output" != *"didn't see the compaction start"* ]]
}

@test "romp compact --wait exits 1 on a loud end that landed before its first poll, judged against the read before the POST" {
    # the baseline is the notice that stood BEFORE the request: taken from the first poll after it, a compaction that
    # ran and failed in between was read as a standing notice and the wait ran to its timeout (2026-09-21)
    start_wait_kernel '{"ok": true, "queued": false}' 'qA'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 8
    [ "$status" -eq 1 ]
    [[ "$output" == *"busy1 did not compact: $NOTICE_A"* ]]
    [[ "$output" != *"didn't see the compaction start"* ]]
}

@test "romp compact --wait reads a notice that stood before it armed as not this wait's and still reports done" {
    start_wait_kernel '{"ok": true, "queued": false}' 'qA,cA,cA,qA' A
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 30
    [ "$status" -eq 0 ]
    [[ "$output" == *"done — busy1 compacted"* ]]
    [[ "$output" != *"did not compact"* ]]
}

@test "romp compact --wait exits 1 on a loud end wearing the standing notice's words at a new stamp: the identity is the stamp too" {
    # the notice's identity is its stamp and its text together: a compaction that fails the way the standing notice's
    # did leaves the same words at a later stamp, and that is this wait's loud end. A wait judging the words alone read
    # it as the notice that stood at the baseline and printed done over an uncompacted thread (the post-merge review of
    # the exit clause, 2026-09-21); no case drove the two halves apart before this one.
    start_wait_kernel '{"ok": true, "queued": false}' 'q,c,c,qR' A
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 30
    [ "$status" -eq 1 ]
    [[ "$output" == *"compacting busy1 now"* ]]
    [[ "$output" == *"busy1 did not compact: $NOTICE_A"* ]]
    [[ "$output" != *"busy1 compacted"* ]]
}

@test "romp compact --wait on a QUEUED compaction takes its baseline from the arming sample: the prior compaction's loud end is not ours" {
    # the prior compaction (compacting on the first poll) ends loudly; ours runs after it and completes: judged against
    # the read before the request, that notice was new and a clean compaction exited 1 (2026-09-21)
    start_wait_kernel '{"ok": true, "queued": true}' 'c,qA,c,qA'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 30
    [ "$status" -eq 0 ]
    [[ "$output" == *"queued for busy1"* ]]
    [[ "$output" == *"done — busy1 compacted"* ]]
    [[ "$output" != *"did not compact"* ]]
}

@test "romp compact --wait on a QUEUED compaction still exits 1 with the words of a notice new since the arming sample" {
    start_wait_kernel '{"ok": true, "queued": true}' 'qA,c,qB'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 30
    [ "$status" -eq 1 ]
    [[ "$output" == *"busy1 did not compact: $NOTICE_B"* ]]
    [[ "$output" != *"$NOTICE_A"* ]]
}

@test "romp compact --wait on a QUEUED compaction is not failed by the turn's own end notice landing after the arming sample" {
    # armed on the mid-turn sample with an empty baseline, the turn ends with a rejection (no mark), then ours runs
    # and completes with that notice standing: read as a loud end, the wait exited 1 with the turn's words
    start_wait_kernel '{"ok": true, "queued": true}' 'q,qT,cT,qT'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 30
    [ "$status" -eq 0 ]
    [[ "$output" == *"queued for busy1"* ]]
    [[ "$output" == *"done — busy1 compacted"* ]]
    [[ "$output" != *"did not compact"* ]]
    [[ "$output" != *"$NOTICE_T"* ]]
}

@test "romp compact --wait on a QUEUED compaction still exits 1 with the compaction's own loud end after the turn's notice" {
    start_wait_kernel '{"ok": true, "queued": true}' 'q,qT,cT,qB'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 30
    [ "$status" -eq 1 ]
    [[ "$output" == *"busy1 did not compact: $NOTICE_B"* ]]
    [[ "$output" != *"$NOTICE_T"* ]]
}

@test "romp compact --wait on a QUEUED compaction reports a loud end between the request and the arming sample by the timeout, never attributed" {
    # ours fired and failed before the first sample read quiet: the notice is baselined with the arming sample (it cannot
    # be told from a prior compaction's or a failed turn's), so the wait sees no compaction start and says so
    start_wait_kernel '{"ok": true, "queued": true}' 'qA'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 6
    [ "$status" -eq 1 ]
    [[ "$output" == *"didn't see the compaction start"* ]]
    [[ "$output" != *"did not compact"* ]]
}

@test "romp compact --wait retries a baseline read that failed, so a standing notice is not read as this compaction's failure" {
    # the read before the request answers 500 once while the row carries a standing notice: taken as an empty baseline,
    # the standing notice read as new and a clean compaction exited 1 with the old words (2026-09-21)
    start_wait_kernel '{"ok": true, "queued": false}' 'qA,cA,cA,qA' A 1
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 30
    [ "$status" -eq 0 ]
    [[ "$output" == *"done — busy1 compacted"* ]]
    [[ "$output" != *"did not compact"* ]]
}

@test "romp compact --wait refuses the wait loudly when the baseline never came, instead of judging blind" {
    start_wait_kernel '{"ok": true, "queued": false}' 'qA,cA,cA,qA' A 3
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 30
    [ "$status" -eq 1 ]
    [[ "$output" == *"compacting busy1 now"* ]]
    [[ "$output" == *"can't judge this compaction from here"* ]]
    [[ "$output" == *"still requested"* ]]
    [[ "$output" != *"did not compact"* ]]
}

@test "romp compact --wait on a QUEUED compaction is not refused when the baseline never came: its baseline is the arming sample" {
    # the refusal is scoped to a compaction that runs at once, the one judged against the read before the request. A
    # queued one takes its baseline from the first sample that reads not compacting and has no use for that read, so
    # the same three failed reads leave it waiting and judging; a refusal of both paths passed every case before this
    # one (the post-merge review of the exit clause, 2026-09-21).
    start_wait_kernel '{"ok": true, "queued": true}' 'q,c,c,q' '' 3
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 30
    [ "$status" -eq 0 ]
    [[ "$output" == *"queued for busy1"* ]]
    [[ "$output" == *"done — busy1 compacted"* ]]
    [[ "$output" != *"can't judge this compaction from here"* ]]
    [[ "$output" != *"did not compact"* ]]
}

# The row's end record (the post-merge review of the wait, 2026-09-21). Every accepted turn on the Codex backend clears the
# row's launchError, and a message parked behind the compaction is delivered at the loud end's poke, so the notice the cases
# above judge was erased within milliseconds of a failed compaction, before the CLI's next poll, which read quiet with no
# notice and printed done over an uncompacted thread. The backend now records every end of its compaction bracket (a
# counter, the last end's kind, words and stamp: compactEnd on the row), which no turn erases, and the wait judges that
# record when the row carries it: done when the count advanced past its baseline with a clean last end, exit 1 with the
# end's words when the last end is loud. The bit and the notice still judge a row without the record (the cases above).
@test "romp compact --wait exits 1 with the end's words when the loud end's notice was erased before the poll: the record is judged" {
    # compacting, then quiet with NO notice (the parked message's accepted turn cleared it) and the record advanced, loud:
    # the notice judgment reads nothing here, and the bit judgment read done
    start_wait_kernel '{"ok": true, "queued": false}' 'q/0,c/0,q/1L' '/0'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 30
    [ "$status" -eq 1 ]
    [[ "$output" == *"compacting busy1 now"* ]]
    [[ "$output" == *"busy1 did not compact: $NOTICE_A"* ]]
    [[ "$output" != *"busy1 compacted"* ]]
}

@test "romp compact --wait reports done on the record's clean end, whether or not it caught the compacting sample" {
    start_wait_kernel '{"ok": true, "queued": false}' 'q/0,q/1' '/0'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 10
    [ "$status" -eq 0 ]
    [[ "$output" == *"done — busy1 compacted"* ]]
    [[ "$output" != *"did not compact"* ]]
    [[ "$output" != *"didn't see the compaction start"* ]]
}

@test "romp compact --wait does not read the bit falling as done when the row carries a record whose count did not move" {
    # every end advances the count: a bit that fell with the count unmoved is an end the kernel has not recorded, never
    # a compaction. A kernel restarted mid-compaction from a baseline of zero had exactly this shape until its load
    # recorded that end (2026-09-22, the restart cases below); on a kernel that records it, the shape is a record lost
    # with the kernel (a clean end saved its bit, the kernel died before the next poll, and the restarted one serves
    # zero again) or a kernel from before the record, and the timeout's line names both (2026-09-22)
    start_wait_kernel '{"ok": true, "queued": false}' 'q/0,c/0,q/0' '/0'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 6
    [ "$status" -eq 1 ]
    [[ "$output" == *"the compaction's end was never recorded, or its record was lost to a kernel restart right after it"* ]]
    [[ "$output" != *"still compacting"* ]]
    [[ "$output" != *"busy1 compacted"* ]]
}

@test "romp compact --wait on a QUEUED compaction baselines the record's count from the arming sample: the prior compaction's loud end is not ours" {
    # the prior compaction ends loudly (count 4, loud, notice A) before the arming sample; ours then runs and completes
    # (count 5, clean, the notice cleared by a turn). Judged against the read before the request (count 3), the prior
    # compaction's loud end would be new and exit 1. The record stays 4L while ours runs, as the kernel keeps it (one
    # stamp per count): a walk that served the clean stamp at 4 under the compacting sample read as a new clean end
    # there, and the case passed on that sample, done at 4 s with the count-5 sample never served (found in review,
    # 2026-09-22). Retained behavior from the record's introduction, pinned: a wait that keeps the pre-request record
    # as a queued wait's baseline exits 1 at the arming sample
    start_wait_kernel '{"ok": true, "queued": true}' 'c/3,qA/4L,cA/4L,q/5' '/3'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 30
    [ "$status" -eq 0 ]
    [[ "$output" == *"queued for busy1"* ]]
    [[ "$output" == *"done — busy1 compacted"* ]]
    [[ "$output" != *"did not compact"* ]]
}

@test "romp compact --wait on a QUEUED compaction exits 1 with the end's words when the record's last end since the arming sample is loud" {
    start_wait_kernel '{"ok": true, "queued": true}' 'q/3,c/3,q/4L' '/3'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 30
    [ "$status" -eq 1 ]
    [[ "$output" == *"busy1 did not compact: $NOTICE_A"* ]]
    [[ "$output" != *"busy1 compacted"* ]]
}

@test "romp compact --wait reads a count below its baseline as a kernel restart and judges the record it started over: a clean end is done" {
    # the kernel restarted between two polls and the record it started over holds one clean end (count 1, below the
    # baseline of 3), the compaction never caught mid-flight: done on the record, rather than holding out for a count
    # the restarted kernel can no longer reach. No compacting sample was seen, so the bit alone cannot print done here
    # (before the record was judged past a restart, this walk ran to the timeout's did-not-see-it-start line), which
    # is what pins the judgment to the record (2026-09-22)
    start_wait_kernel '{"ok": true, "queued": false}' 'q/3,q/1' '/3'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 6
    [ "$status" -eq 0 ]
    [[ "$output" == *"done — busy1 compacted"* ]]
    [[ "$output" != *"did not compact"* ]]
}

@test "romp compact --wait reads a count below its baseline as a kernel restart and exits 1 with the restarted kernel's recorded end" {
    # the kernel restarted after the compacting sample with the compaction still running: its load ended it as a loud
    # end it records (count 1, below the baseline of 2), and the message parked behind the compaction was delivered
    # by the same boot and cleared the notice before this poll, so the row reads quiet with no notice. Judged as a row
    # without the record, the bit alone printed done here over an outcome nobody knows (2026-09-22)
    start_wait_kernel '{"ok": true, "queued": false}' 'q/2,c/2,q/1X' '/2'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 30
    [ "$status" -eq 1 ]
    [[ "$output" == *"busy1 did not compact: $NOTICE_X"* ]]
    [[ "$output" != *"busy1 compacted"* ]]
}

@test "romp compact --wait reads the restarted kernel's recorded end from a baseline of one, where the count alone reads unmoved" {
    # the restart's record always starts at one (the load's one loud end), so against a baseline of one (a session
    # compacted once before) the count reads neither below nor above it, and the message parked at the restart has
    # cleared the notice: judged by the count alone, no branch fired and the wait ran to its timeout's never-recorded
    # line over an end the load had recorded. The record's identity is its count with its last end's stamp, and the
    # restart's stamp is new (found in review, 2026-09-22)
    start_wait_kernel '{"ok": true, "queued": false}' 'q/1,c/1,q/1X' '/1'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 8
    [ "$status" -eq 1 ]
    [[ "$output" == *"busy1 did not compact: $NOTICE_X"* ]]
    [[ "$output" != *"never recorded"* ]]
    [[ "$output" != *"busy1 compacted"* ]]
}

@test "romp compact --wait tells the restarted kernel's recorded end from a loud end of the same count by its stamp alone" {
    # the identity is the record's count WITH its last end's stamp: here the baseline is one loud end (a compaction
    # Codex refused, or a previous restart's own end) and the restart's record is one loud end too, so the count and
    # the kind both read unmoved and only the stamp says this is a new end. A judgment reading the kind beside the count
    # runs this walk to its timeout's never-recorded line; the sibling case above cannot tell, since its baseline is
    # clean (the third review, 2026-09-22)
    start_wait_kernel '{"ok": true, "queued": false}' 'q/1L,c/1L,q/1X' '/1L'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 8
    [ "$status" -eq 1 ]
    [[ "$output" == *"busy1 did not compact: $NOTICE_X"* ]]
    [[ "$output" != *"$NOTICE_A"* ]]
    [[ "$output" != *"never recorded"* ]]
    [[ "$output" != *"busy1 compacted"* ]]
}

@test "romp compact --wait from a baseline of zero reads the restarted kernel's recorded end: exit 1 with its words, not the timeout" {
    # the same restart from a record with no end yet: the load's recorded end moves the count to one, which the
    # count-advanced judgment reads like any end. Before the load recorded it, the restarted kernel served zero again
    # and the wait ran to its timeout's never-recorded line (2026-09-22)
    start_wait_kernel '{"ok": true, "queued": false}' 'q/0,c/0,q/1X' '/0'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 30
    [ "$status" -eq 1 ]
    [[ "$output" == *"busy1 did not compact: $NOTICE_X"* ]]
    [[ "$output" != *"never recorded"* ]]
    [[ "$output" != *"busy1 compacted"* ]]
}

@test "romp compact --wait reads a count back at zero below its baseline as a restart that found no compaction running, and judges the rest on the bit" {
    # the compaction ended and saved its bit down before the kernel died (a loud end saves its notice in the same write,
    # which the notice judgment reads while it stands; a death between that save and the parked message's ACK loses the
    # record with the kernel and the boot's delivery clears the notice, a residual for persisting the record beside the
    # bit in the registry row), so the restarted kernel had no end to record: from this sample on the row is judged as
    # one without the record, and the bit seen and then fallen is done (2026-09-22)
    start_wait_kernel '{"ok": true, "queued": false}' 'q/3,c/3,q/0' '/3'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 30
    [ "$status" -eq 0 ]
    [[ "$output" == *"busy1 compacted"* ]]
    [[ "$output" != *"did not compact"* ]]
}

@test "romp compact --wait judges a record that appears on a row baselined without one: the restart's loud end exits 1, not done on the bit" {
    # the baseline row carried no record (a live kernel from before it, on a checkout pulled ahead of its refresh); the
    # kernel was replaced mid-wait by one with the record, whose load ended the compaction it found still running as a
    # loud end it records (count 1, X's words), and the message parked at the restart cleared the notice before this
    # poll. Judged only when the baseline carried the record, the sample fell through to the bit, seen and then fallen,
    # which printed done over an outcome nobody knows (found in review, 2026-09-22). From a baseline of no end, a count
    # above zero is an end since, by its kind
    start_wait_kernel '{"ok": true, "queued": false}' 'q,c,q/1X' ''
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 30
    [ "$status" -eq 1 ]
    [[ "$output" == *"busy1 did not compact: $NOTICE_X"* ]]
    [[ "$output" != *"busy1 compacted"* ]]
}

@test "romp compact --wait keeps the bit's judgment for a record at zero that appears on a row baselined without one" {
    # the same replacement mid-wait, but the newer kernel found no compaction running (ours ended and saved its bit
    # before the old kernel went), so it serves a record with no end: the kernel that ran the compaction kept no record
    # for the fallen bit to be held against, so the bit seen and then fallen is done, as the baseline row would have
    # been judged (retained behavior, pinned beside the case above, 2026-09-22)
    start_wait_kernel '{"ok": true, "queued": false}' 'q,c,q/0' ''
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 30
    [ "$status" -eq 0 ]
    [[ "$output" == *"busy1 compacted"* ]]
    [[ "$output" != *"did not compact"* ]]
}

@test "romp compact --wait leaves the rest of a wait unjudged after a count back at zero, even when a record appears again" {
    # the two roads never mix (2026-09-22): a count back at zero marks the baseline unjudged rather than empty, so a
    # later record on the same wait (a coincidental compaction on the restarted kernel; ours ended before the death) is
    # not read as an end since a baseline of no end. No compacting sample was seen, so the bit cannot print done
    # either, and the wait runs to its did-not-see-it-start line; with the mark the empty string, the loud record
    # exited 1 with a coincidental end's words
    start_wait_kernel '{"ok": true, "queued": false}' 'q/3,q/0,q/1L' '/3'
    run "$ROMP_SCRIPT" compact busy1 --wait --timeout 6
    [ "$status" -eq 1 ]
    [[ "$output" == *"didn't see the compaction start"* ]]
    [[ "$output" != *"did not compact"* ]]
    [[ "$output" != *"busy1 compacted"* ]]
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

@test "romp send --tag ahead of the session name tags the send instead of addressing a session called --tag" {
    # 2026-09-12: a timer's `romp send --tag <label> <session> <text>` read `--tag` AS the session name four
    # times in one night and the kernel refused a paste to a target that does not exist, while the timer read
    # "sent". A verb's own flag is never a session name; the leading form tags and addresses like the trailing one.
    start_fake_kernel '{"ok": true}'
    run "$ROMP_SCRIPT" send --tag wake helper 'the ten-minute pulse'
    [ "$status" -eq 0 ]
    python3 - "$TEST_DIR/req" <<'PY'
import json, sys
body = open(sys.argv[1]).read().split("\n", 1)[1]
d = json.loads(body)
assert d["name"] == "helper", d
assert d["text"] == "the ten-minute pulse\n\n<!-- romp-tag: wake -->", d
PY
    run "$ROMP_SCRIPT" send --tag
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp send"* ]]
    run "$ROMP_SCRIPT" send --tag wake
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp send"* ]]
}
