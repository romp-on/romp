#!/usr/bin/env bats

# `romp billing <session> [login|key]` (the user 2026-09-11): which account an SDK session bills, read
# or switched from a terminal — the kernel's /auth route, the tab menu's Billing pick as a one-shot
# route. Bare it reads (GET /auth?id=) and prints one line; with a value it posts the switch and prints
# the kernel's answer the same way. The api-health contract: the token travels on stdin (never argv), a
# dead kernel fails LOUDLY, a kernel that ANSWERS with a refusal is reported as what it said (a refused
# token, a kernel without the route), never as "not reachable", and a bad value is a usage error. No
# key material is ever printed: the key is the label "API key". On origin/main `romp billing` is not
# a command at all, so every case here fails the wrapper's unknown-command path.

ROMP_SCRIPT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp"

setup() {
    unset ROMP_STATE_DIR ROMP_SERVE_TOKEN
    TEST_DIR="$(mktemp -d)"
    export XDG_STATE_HOME="$TEST_DIR/state"
    mkdir -p "$XDG_STATE_HOME/romp"
    printf 'TESTTOKEN123\n' > "$XDG_STATE_HOME/romp/serve-token"
    export ROMP_KERNEL_PORT=29855

    # Stub curl: records argv AND stdin (the auth header rides stdin as a curl config), replies with
    # the synthetic /auth answer in AUTH_JSON. It honours -w the way curl does (the format after the
    # body, with the status filled in), so the script's status split runs for real. CURL_FAIL: no
    # reply at all (curl's exit 7). CURL_CODE + CURL_BODY: a reply with that status and body.
    MOCK="$TEST_DIR/mock"; mkdir -p "$MOCK"
    export CURL_LOG="$TEST_DIR/curl.log"
    export CURL_STDIN="$TEST_DIR/curl.stdin"
    export AUTH_JSON="$TEST_DIR/auth.json"
    _reply '{"ok": true, "queued": false, "id": "11111111-2222-3333-4444-555555555555", "name": "web", "auth": "key", "authLive": "key", "authPending": false, "authPicked": true, "acct": "someone@example.com"}'
    cat > "$MOCK/curl" <<'MOCK'
#!/usr/bin/env bash
echo "$*" >> "$CURL_LOG"
cat >> "$CURL_STDIN" 2>/dev/null
[ -n "${CURL_FAIL:-}" ] && exit 7
_w=""
while [ $# -gt 0 ]; do [ "$1" = "-w" ] && _w="$2"; shift; done
if [ -n "${CURL_CODE:-}" ]; then printf '%s' "${CURL_BODY:-}"; else cat "$AUTH_JSON"; fi
if [ -n "$_w" ]; then printf '%b' "$(printf '%s' "$_w" | sed "s/%{http_code}/${CURL_CODE:-200}/")"; fi
exit 0
MOCK
    chmod +x "$MOCK/curl"
    export PATH="$MOCK:$PATH"
}

_reply() { printf '%s' "$1" > "$AUTH_JSON"; }

teardown() { rm -rf "$TEST_DIR"; }

@test "romp billing: bare reads GET /auth?id= and prints one line naming the side" {
    run "$ROMP_SCRIPT" billing web
    [ "$status" -eq 0 ]
    [ "$output" = "web: bills the API key" ]
    grep -q "127.0.0.1:29855/auth?id=web" "$CURL_LOG"
    run grep -q -- '-X POST' "$CURL_LOG"
    [ "$status" -ne 0 ]
    grep -q "X-Romp-Token: TESTTOKEN123" "$CURL_STDIN"
    # never in argv: /proc/<pid>/cmdline is world-readable
    run grep -q "TESTTOKEN123" "$CURL_LOG"
    [ "$status" -ne 0 ]
}

@test "romp billing: a login-billed session names its account; an applying switch says so" {
    _reply '{"ok": true, "queued": false, "id": "x", "name": "web", "auth": "login", "authLive": "", "authPending": true, "authPicked": true, "acct": "someone@example.com"}'
    run "$ROMP_SCRIPT" billing web
    [ "$status" -eq 0 ]
    [ "$output" = "web: bills the login (applying — not confirmed yet) · login account someone@example.com" ]
}

@test "romp billing: a session never picked is said to be unpicked, billing the machine's default" {
    _reply '{"ok": true, "queued": false, "id": "x", "name": "web", "auth": "key", "authLive": "key", "authPending": false, "authPicked": false, "acct": ""}'
    run "$ROMP_SCRIPT" billing web
    [ "$status" -eq 0 ]
    [ "$output" = "web: unpicked (bills the API key, the default for this machine)" ]
}

@test "romp billing: a value posts the switch with id and value, and prints the kernel's answer" {
    _reply '{"ok": true, "queued": false, "id": "x", "name": "web", "auth": "login", "authLive": "", "authPending": true, "authPicked": true, "acct": ""}'
    run "$ROMP_SCRIPT" billing web login
    [ "$status" -eq 0 ]
    [ "$output" = "web: bills the login (applying — not confirmed yet)" ]
    grep -q -- '-X POST' "$CURL_LOG"
    grep -q "127.0.0.1:29855/auth " "$CURL_LOG"
    grep -q '"id": *"web"' "$CURL_LOG"
    grep -q '"value": *"login"' "$CURL_LOG"
    grep -q "X-Romp-Token: TESTTOKEN123" "$CURL_STDIN"
}

@test "romp billing: a parked switch is reported as queued, neutrally about why, with the side billed until then" {
    # the kernel parks for more than an open turn (compaction, a hold, changes queued ahead): the line names
    # the possibilities rather than claiming one
    _reply '{"ok": true, "queued": true, "id": "x", "name": "web", "auth": "login", "authLive": "login", "authPending": false, "authPicked": true, "acct": ""}'
    run "$ROMP_SCRIPT" billing web key
    [ "$status" -eq 0 ]
    [ "$output" = "web: queued — the switch to the API key applies once the session can take it: it is mid-turn, compacting, being moved, held back by a usage limit, or has changes queued ahead (bills the login until then)" ]
}

@test "romp billing: a reply with no side is an error, never a quiet exit 0" {
    _reply '{"ok": true, "queued": false, "id": "x", "name": "web", "auth": "", "authLive": "", "authPending": false, "authPicked": false, "acct": ""}'
    run "$ROMP_SCRIPT" billing web
    [ "$status" -eq 1 ]
    [[ "$output" == *"billing unknown for web"* ]]
}

@test "romp billing: the kernel's refusal is printed as its own reason, exit 1" {
    _reply '{"ok": false, "error": "Couldn'"'"'t switch the account this session bills: no Claude login signed in on this machine."}'
    run "$ROMP_SCRIPT" billing web login
    [ "$status" -eq 1 ]
    [[ "$output" == *"refused — Couldn't switch the account this session bills: no Claude login signed in on this machine."* ]]
}

@test "romp billing: usage errors exit 2 — no session, a value outside login|key, too many arguments" {
    run "$ROMP_SCRIPT" billing
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp billing <session> [login|key]"* ]]
    run "$ROMP_SCRIPT" billing web credit-card
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp billing"* ]]
    run "$ROMP_SCRIPT" billing web key extra
    [ "$status" -eq 2 ]
    [ ! -e "$CURL_LOG" ]
}

@test "romp billing: a dead kernel fails LOUDLY" {
    CURL_FAIL=1 run "$ROMP_SCRIPT" billing web
    [ "$status" -ne 0 ]
    [[ "$output" == *"kernel not reachable"* ]]
}

@test "romp billing: a 404 says the kernel predates the command and wants a restart" {
    CURL_CODE=404 CURL_BODY='' run "$ROMP_SCRIPT" billing web key
    [ "$status" -eq 1 ]
    [[ "$output" == *"has no /auth route (HTTP 404)"* ]]
    [[ "$output" == *"restart romp"* ]]
    [[ "$output" != *"kernel not reachable"* ]]
}

@test "romp billing: a refused token is reported as that, not as a dead kernel" {
    CURL_CODE=403 CURL_BODY='forbidden' run "$ROMP_SCRIPT" billing web
    [ "$status" -eq 1 ]
    [[ "$output" == *"refused the serve token (HTTP 403)"* ]]
    [[ "$output" != *"kernel not reachable"* ]]
}

@test "romp billing: no serve token means no kernel, said plainly" {
    rm -f "$XDG_STATE_HOME/romp/serve-token"
    run "$ROMP_SCRIPT" billing web key
    [ "$status" -eq 1 ]
    [[ "$output" == *"the kernel isn't running (no serve token)"* ]]
    [ ! -e "$CURL_LOG" ]
}

@test "romp billing: listed in help, under the scripting group" {
    run "$ROMP_SCRIPT" help
    [ "$status" -eq 0 ]
    [[ "$output" == *"romp billing <session> [login|key]"* ]]
}
