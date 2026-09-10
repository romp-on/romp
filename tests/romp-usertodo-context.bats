#!/usr/bin/env bats

# romp-usertodo-context.sh is a SessionStart hook (plans/user-todos.md slice 3): on the
# resume and compact sources it fetches the session's open user todos from the kernel
# (POST /usertodo/context) and emits the rendered block as additionalContext — the agent's
# own outstanding notes to the person it works for, so it can withdraw the moot ones after
# its working memory was wiped. It must be silent for every other source, silent outside a
# romp session (no ROMP_SID — deliberately NOT tmux-gated: SDK sessions need it too), and
# it must NEVER fail the turn, kernel down included.

setup() {
    TEST_DIR="$(mktemp -d)"
    export HOME="$TEST_DIR/home"; mkdir -p "$HOME"
    MOCK="$TEST_DIR/mock"; mkdir -p "$MOCK"
    export CURL_LOG="$TEST_DIR/curl.log"
    export CURL_STDIN="$TEST_DIR/curl.stdin"
    export TMUX_LOG="$TEST_DIR/tmux.log"
    # Mock curl: capture the stdin config (the token travels there, never argv), log argv,
    # answer with $CURL_RESPONSE. Unlike romp-wake's detached poke, this hook READS curl's
    # stdout, so the mock answers synchronously.
    cat > "$MOCK/curl" <<'MOCK'
#!/usr/bin/env bash
cat > "$CURL_STDIN" 2>/dev/null
echo "curl $*" >> "$CURL_LOG"
printf '%s' "${CURL_RESPONSE:-}"
exit "${CURL_EXIT:-0}"
MOCK
    chmod +x "$MOCK/curl"
    # Mock tmux: the hook must never consult it (an SDK session has no pane, and the sibling
    # postal-context hook's @romp gate is exactly what this one does NOT carry). Any call logs
    # and fails loudly, so a tmux-gated regression shows up as a non-empty log.
    cat > "$MOCK/tmux" <<'MOCK'
#!/usr/bin/env bash
echo "tmux $*" >> "$TMUX_LOG"
exit 1
MOCK
    chmod +x "$MOCK/tmux"
    export PATH="$MOCK:$PATH"
    # Clear the inherited romp env: running this suite from inside a romp session carries
    # ROMP_SID / ROMP_SERVE_PORT, which would silently flip the "outside a romp session"
    # and default-port cases (the romp-wake-hook.bats lesson, 2026-07-24).
    unset ROMP_SID ROMP_SERVE_PORT ROMP_KERNEL_PORT ROMP_SERVE_TOKEN ROMP_SUMMARIZING ROMP_STATE_DIR
    unset TMUX TMUX_PANE
    export CURL_RESPONSE='{"ok": true, "block": "Notes you still have open with the person you work for"}'
    SID="11111111-2222-3333-4444-555555555555"
    HOOK="$(cd "$(dirname "$BATS_TEST_FILENAME")/../hooks" && pwd)/romp-usertodo-context.sh"
}

teardown() { rm -rf "$TEST_DIR"; }

run_hook() {   # run_hook <source> — feed a SessionStart payload with that source
    run bash -c 'echo "{\"session_id\":\"'"$SID"'\",\"source\":\"'"$1"'\"}" | "'"$HOOK"'"'
}

@test "on resume it emits the kernel's block as additionalContext" {
    export ROMP_SID="$SID"
    run_hook resume
    [ "$status" -eq 0 ]
    [[ "$output" == *'"hookEventName": "SessionStart"'* ]]
    [[ "$output" == *'"additionalContext"'* ]]
    [[ "$output" == *'Notes you still have open with the person you work for'* ]]
}

@test "on compact it asks the kernel too — the post-compaction re-surface" {
    export ROMP_SID="$SID"
    run_hook compact
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
    grep -q '/usertodo/context' "$CURL_LOG"
}

@test "the query carries the STABLE romp sid, not the transcript session_id" {
    # ROMP_SID is the store's key on both backends; the hook payload's session_id is the
    # CURRENT transcript fsid, which a /clear fork moves off the stable id
    export ROMP_SID="99999999-8888-7777-6666-555555555555"
    run_hook resume
    [ "$status" -eq 0 ]
    grep -q '99999999-8888-7777-6666-555555555555' "$CURL_LOG"
    run grep -q "$SID" "$CURL_LOG"
    [ "$status" -ne 0 ]
}

@test "the request is a JSON POST of {id} with the content type set" {
    # the kernel's route reads a JSON body; a form-encoded or bare-id request would 400 there
    export ROMP_SID="$SID"
    run_hook resume
    [ "$status" -eq 0 ]
    grep -q -- '-X POST' "$CURL_LOG"
    grep -q 'Content-Type: application/json' "$CURL_LOG"
    grep -q -- "--data {\"id\":\"$SID\"}" "$CURL_LOG"
}

@test "startup, clear and fork are silent — no query at all" {
    export ROMP_SID="$SID"
    for src in startup clear fork; do
        run_hook "$src"
        [ "$status" -eq 0 ]
        [ -z "$output" ]
    done
    [ ! -s "$CURL_LOG" ]
}

@test "a payload with no source at all is silent" {
    export ROMP_SID="$SID"
    run bash -c 'echo "{\"session_id\":\"'"$SID"'\"}" | "'"$HOOK"'"'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -s "$CURL_LOG" ]
}

@test "an empty block means no output at all — a zero-todo session gets nothing" {
    export ROMP_SID="$SID"
    export CURL_RESPONSE='{"ok": true, "block": ""}'
    run_hook resume
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a whitespace-only or missing block is nothing to say too" {
    export ROMP_SID="$SID"
    export CURL_RESPONSE='{"ok": true, "block": " \n "}'
    run_hook resume
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    export CURL_RESPONSE='{"ok": true}'
    run_hook compact
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "the kernel saying the feature is off means no output at all, whatever block rides along" {
    # the per-install switch (the user 2026-09-03, OFF by default): the kernel is the authority and
    # its /usertodo/context answer carries `enabled`; false = inject nothing, even if a block came too
    export ROMP_SID="$SID"
    export CURL_RESPONSE='{"ok": true, "enabled": false, "block": "Notes you still have open with the person you work for"}'
    run_hook resume
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    grep -q '/usertodo/context' "$CURL_LOG"   # it asked; the kernel's answer decided
}

@test "enabled true emits the block; an older kernel with no enabled field is read as on" {
    export ROMP_SID="$SID"
    export CURL_RESPONSE='{"ok": true, "enabled": true, "block": "Notes you still have open with the person you work for"}'
    run_hook resume
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
    export CURL_RESPONSE='{"ok": true, "block": "Notes you still have open with the person you work for"}'
    run_hook resume
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
}

@test "a kernel answer that is not JSON emits nothing and never fails the turn" {
    export ROMP_SID="$SID"
    export CURL_RESPONSE='<html>502 Bad Gateway</html>'
    run_hook resume
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "outside a romp session (no ROMP_SID) it is silent and never queries" {
    run_hook resume
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -s "$CURL_LOG" ]
}

@test "it is not tmux-gated: no pane, no tmux server, and tmux is never asked" {
    # an SDK session has no pane; the block must reach it too. TMUX/TMUX_PANE are unset in setup
    # and the mock tmux fails every call — a gate on `tmux show -v @romp` would go silent here.
    export ROMP_SID="$SID"
    run_hook resume
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
    [ ! -s "$TMUX_LOG" ]
}

@test "a summarizer session is silent" {
    export ROMP_SID="$SID" ROMP_SUMMARIZING=1
    run_hook resume
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -s "$CURL_LOG" ]
}

@test "a mangled ROMP_SID never reaches the wire" {
    # the sid is interpolated into a JSON body — the same shape gate bin/romp applies to a
    # resume id keeps a crafted value out of the request entirely
    export ROMP_SID='11111111"</dev/null;touch pwned;'
    run_hook resume
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -s "$CURL_LOG" ]
    [ ! -e "$TEST_DIR/pwned" ] && [ ! -e "pwned" ]
}

@test "a failed or refused query never fails the turn and emits nothing" {
    export ROMP_SID="$SID" CURL_EXIT=22 CURL_RESPONSE=""
    run_hook resume
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "the kernel being unreachable (real curl, dead port) never fails the turn" {
    rm "$MOCK/curl"
    export ROMP_SID="$SID" ROMP_SERVE_PORT=1
    run_hook resume
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "it hits /usertodo/context on the configured kernel port" {
    export ROMP_SID="$SID" ROMP_SERVE_PORT=7777
    run_hook resume
    [ "$status" -eq 0 ]
    grep -q 'http://127.0.0.1:7777/usertodo/context' "$CURL_LOG"
}

@test "it defaults to port 29855 and honours ROMP_KERNEL_PORT, the other spelling" {
    export ROMP_SID="$SID"
    run_hook resume
    grep -q 'http://127.0.0.1:29855/usertodo/context' "$CURL_LOG"
    : > "$CURL_LOG"
    export ROMP_KERNEL_PORT=7778
    run_hook resume
    grep -q 'http://127.0.0.1:7778/usertodo/context' "$CURL_LOG"
}

@test "the serve token rides stdin as a curl config, never argv" {
    # /proc/<pid>/cmdline is world-readable — the romp-wake.sh rule
    export ROMP_SID="$SID" ROMP_SERVE_TOKEN="TESTTOKENDONOTUSE"
    run_hook resume
    [ "$status" -eq 0 ]
    run grep -q "TESTTOKENDONOTUSE" "$CURL_LOG"
    [ "$status" -ne 0 ]
    grep -q -- "--config" "$CURL_LOG"
    grep -q "X-Romp-Token: TESTTOKENDONOTUSE" "$CURL_STDIN"
}

@test "a token with curl-config metacharacters arrives escaped, the romp-wake.sh way" {
    export ROMP_SID="$SID" ROMP_SERVE_TOKEN='ab"c\d'
    run_hook resume
    [ "$status" -eq 0 ]
    run cat "$CURL_STDIN"
    [ "$output" = 'header = "X-Romp-Token: ab\"c\\d"' ]
}

@test "the serve token falls back to the state file" {
    export ROMP_SID="$SID" XDG_STATE_HOME="$TEST_DIR/state"
    mkdir -p "$XDG_STATE_HOME/romp"
    printf 'tok-from-file\n' > "$XDG_STATE_HOME/romp/serve-token"
    run_hook resume
    [ "$status" -eq 0 ]
    grep -q 'X-Romp-Token: tok-from-file' "$CURL_STDIN"
}

@test "a multi-line block round-trips into valid hook JSON" {
    export ROMP_SID="$SID"
    export CURL_RESPONSE='{"ok": true, "block": "Notes you still have open:\n- Need the auth-scheme decision (ut-11111111, opened 2026-08-20)\n\nIf one is met or moot now, withdraw it (withdraw_user_todo); otherwise leave it standing."}'
    run_hook resume
    [ "$status" -eq 0 ]
    # the emitted line is JSON Claude Code parses — prove it round-trips with the block intact
    python3 - "$output" <<'PY'
import json, sys
d = json.loads(sys.argv[1])
ctx = d["hookSpecificOutput"]["additionalContext"]
assert d["hookSpecificOutput"]["hookEventName"] == "SessionStart"
assert "withdraw_user_todo" in ctx and "\n\n" in ctx, ctx
PY
}
