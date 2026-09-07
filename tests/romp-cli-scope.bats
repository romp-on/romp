#!/usr/bin/env bats

# bin/romp-cli-scope — the exec-in-place wrapper the kernel spawns a session's `claude` CLI through
# on Linux under systemd, so the CLI (and everything it later starts: tool shells, setsid children,
# tmux servers) runs in a transient scope of its own instead of the manager service's cgroup, which
# systemd's default KillMode=control-group empties on every service restart.
#
# A FAKE systemd-run first on PATH records its argv (FAKE_LOG holds the LAST call's argv, one element
# per line; FAKE_CALLS appends one line per call) and then execs the command after `--`, so the tests
# see exactly what the wrapper asked for and the fake "real CLI" still runs. The wrapper calls it
# twice per launch: a pre-flight `-- true`, then the scoped CLI. Nothing here touches the real
# systemd-run.

setup() {
    TEST_DIR="$(mktemp -d)"
    WRAPPER="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-cli-scope"
    BIN="$TEST_DIR/bin"
    mkdir -p "$BIN"
    export FAKE_LOG="$TEST_DIR/systemd-run.argv"
    export FAKE_CALLS="$TEST_DIR/systemd-run.calls"
    cat > "$BIN/systemd-run" <<'SH'
#!/bin/sh
# one argv element per line, then exec the command after `--` (what --scope mode does for real)
printf '%s\n' "$@" > "$FAKE_LOG"
echo "$*" >> "$FAKE_CALLS"
while [ "$#" -gt 0 ]; do a="$1"; shift; [ "$a" = "--" ] && break; done
exec "$@"
SH
    chmod +x "$BIN/systemd-run"
    # the fake "real CLI": its pid, its parent's pid, and its argv one element per line on stdout;
    # its own pid to REAL_PIDFILE too, for the exec-in-place checks on a background launch
    REAL="$TEST_DIR/claude"
    export REAL_PIDFILE="$TEST_DIR/real.pid"
    cat > "$REAL" <<'SH'
#!/bin/sh
echo "REAL pid=$$ ppid=$PPID"
printf 'ARG:%s\n' "$@"
echo "$$" > "$REAL_PIDFILE"
SH
    chmod +x "$REAL"
    export PATH="$BIN:$PATH"
    export ROMP_CLI_REAL="$REAL"
    export ROMP_SID="11111111-2222-3333-4444-555555555555"
    unset ROMP_CLI_SCOPE
    # the per-session limits: a self-hosted suite inherits a real service.env setting through its tool
    # shell, and every exact argv pin below assumes none is set
    unset ROMP_CLI_SCOPE_MEMORY_MAX ROMP_CLI_SCOPE_MEMORY_HIGH ROMP_CLI_SCOPE_MEMORY_SWAP_MAX ROMP_CLI_SCOPE_OOM_SCORE_ADJ
}

teardown() { rm -rf "$TEST_DIR"; }

@test "runs the real CLI through systemd-run with the arguments passed verbatim" {
    run "$WRAPPER" --input-format stream-json "two words" --resume "$ROMP_SID" ""
    [ "$status" -eq 0 ]
    [[ "$output" == *"REAL pid="* ]]
    # every argument, in order, including the one with a space, the ones starting with --,
    # and the empty one
    expected="$(printf 'ARG:%s\n' --input-format stream-json "two words" --resume "$ROMP_SID" "")"
    [ "$(printf '%s\n' "$output" | grep '^ARG:')" = "$expected" ]
    [ -s "$FAKE_LOG" ]
}

@test "asks systemd-run for a --user --scope --quiet --collect unit and hands it the real CLI after --" {
    run "$WRAPPER" a b
    [ "$status" -eq 0 ]
    grep -qx -- '--user' "$FAKE_LOG"
    grep -qx -- '--scope' "$FAKE_LOG"
    grep -qx -- '--quiet' "$FAKE_LOG"
    grep -qx -- '--collect' "$FAKE_LOG"
    grep -qx -- "--description=romp session $ROMP_SID" "$FAKE_LOG"
    # after `--`: the real CLI, then the args verbatim
    tail_after_dash="$(sed -n '/^--$/,$p' "$FAKE_LOG" | sed 1d)"
    [ "$tail_after_dash" = "$(printf '%s\n' "$REAL" a b)" ]
}

@test "the unit is romp-session-<first 8 of the sid>-<pid>-<start time>, and differs between two runs" {
    run "$WRAPPER"
    [ "$status" -eq 0 ]
    unit1="$(grep '^--unit=' "$FAKE_LOG")"
    # <pid> is the pid the CLI ran as (exec-in-place: wrapper pid == CLI pid); the time component is
    # what keeps the name unique once pids wrap while an earlier CLI's scope is still loaded (a tmux
    # server it started keeps the scope alive) — systemd refuses a duplicate name as "already loaded"
    pid1="$(printf '%s\n' "$output" | sed -n 's/^REAL pid=\([0-9]*\) .*/\1/p')"
    [ -n "$pid1" ]
    [[ "$unit1" =~ ^--unit=romp-session-11111111-${pid1}-[0-9]+$ ]]
    run "$WRAPPER"
    [ "$status" -eq 0 ]
    unit2="$(grep '^--unit=' "$FAKE_LOG")"
    [[ "$unit2" =~ ^--unit=romp-session-11111111-[0-9]+-[0-9]+$ ]]
    [ "$unit1" != "$unit2" ]
}

# No session identity, no scope (2026-09-05): the SDK's per-connect version check runs `<cli_path> -v`
# with the KERNEL's environment — ROMP_CLI_REAL (the kernel exports it) but no ROMP_SID — and a probe
# must run the CLI directly rather than mint a romp-session-unknown-* scope per connect.
@test "an empty ROMP_SID runs the real CLI directly — a probe, not a session; systemd-run is not invoked" {
    ROMP_SID= run "$WRAPPER" -v
    [ "$status" -eq 0 ]
    [ ! -e "$FAKE_LOG" ]
    [ ! -e "$FAKE_CALLS" ]
    [ "$(printf '%s\n' "$output" | grep '^ARG:')" = "ARG:-v" ]
}

@test "an unset ROMP_SID runs the real CLI directly the same way" {
    unset ROMP_SID
    run "$WRAPPER" -v
    [ "$status" -eq 0 ]
    [ ! -e "$FAKE_CALLS" ]
    [[ "$output" == *"REAL pid="* ]]
}

@test "a pre-flight scope (-- true) runs before the CLI's own scope" {
    run "$WRAPPER" a
    [ "$status" -eq 0 ]
    [ "$(wc -l < "$FAKE_CALLS")" -eq 2 ]
    first="$(sed -n 1p "$FAKE_CALLS")"
    [[ "$first" == *"--user --scope --quiet --collect -- true" ]]
    [[ "$first" != *"--unit="* ]]
    second="$(sed -n 2p "$FAKE_CALLS")"
    [[ "$second" == *"--unit=romp-session-11111111-"* ]]
    [[ "$second" == *"-- $REAL a" ]]
}

@test "a failing pre-flight falls back to the real CLI directly, with one stderr line naming the reason" {
    # systemd-run that cannot start a scope (the user bus gone): the CLI must still launch, in
    # the caller's cgroup, and the reason must reach stderr as ONE line starting
    # `romp-cli-scope: fallback:` — the kernel logs a line of that form as a problem the moment it
    # arrives (tests/test_cli_scope.py FallbackNotice); every other stderr line, the wrapper's
    # `refused:` line included, it only buffers
    cat > "$BIN/systemd-run" <<'SH'
#!/bin/sh
echo "$*" >> "$FAKE_CALLS"
echo "Failed to connect to bus: No such file or directory" >&2
echo "a second stderr line that must not be quoted" >&2
exit 1
SH
    chmod +x "$BIN/systemd-run"
    ERR="$TEST_DIR/stderr"
    run sh -c '"$0" --input-format stream-json "two words" 2>"$1"' "$WRAPPER" "$ERR"
    [ "$status" -eq 0 ]
    # the real CLI ran, directly, with its arguments intact
    [[ "$output" == *"REAL pid="* ]]
    [ "$(printf '%s\n' "$output" | grep '^ARG:')" = "$(printf 'ARG:%s\n' --input-format stream-json "two words")" ]
    # systemd-run was tried exactly once (the pre-flight), never for the CLI itself
    [ "$(wc -l < "$FAKE_CALLS")" -eq 1 ]
    [[ "$(cat "$FAKE_CALLS")" == *"-- true" ]]
    # one stderr line: the fallback form, the reason's first line, and the direct run
    [ "$(wc -l < "$ERR")" -eq 1 ]
    grep -q '^romp-cli-scope: fallback: ' "$ERR"
    grep -q 'Failed to connect to bus' "$ERR"
    grep -q 'directly' "$ERR"
    # armed absence check (a bare mid-test `! grep` is exempt from bats' errexit and asserts nothing)
    run grep -q 'second stderr line' "$ERR"
    [ "$status" -ne 0 ]
}

# The pre-flight is bounded (2026-09-05): `timeout 10 systemd-run … -- true` when coreutils' timeout is
# on PATH. A user bus that accepts the connection and never answers would otherwise hold systemd-run
# for its own 90 s default, past the SDK's 60 s initialize timeout, so the fallback never engaged in the
# case it was added for. The fake systemd-run below sleeps on the pre-flight only, so the no-timeout test
# can show an unbounded pre-flight that completes; the bound itself is proven with a fake timeout that
# exits 124.
_preflight_sleeps() {   # $1: seconds the fake systemd-run sleeps on a `-- true` pre-flight, then exits 0
    cat > "$BIN/systemd-run" <<SH
#!/bin/sh
printf '%s\n' "\$@" > "\$FAKE_LOG"
echo "\$*" >> "\$FAKE_CALLS"
case "\$*" in *"-- true") sleep $1; exit 0 ;; esac
while [ "\$#" -gt 0 ]; do a="\$1"; shift; [ "\$a" = "--" ] && break; done
exec "\$@"
SH
    chmod +x "$BIN/systemd-run"
}

# A fake `timeout` first on PATH that records its argv (TIMEOUT_LOG, one call per line) and then hands
# over to the real one, so the bound is real and its argv is visible. Skips the test without a real one.
_fake_timeout() {
    REAL_TIMEOUT="$(PATH="${PATH#"$BIN:"}" command -v timeout || true)"
    [ -n "$REAL_TIMEOUT" ] || skip "no timeout(1) on this box"
    export TIMEOUT_LOG="$TEST_DIR/timeout.calls"
    cat > "$BIN/timeout" <<SH
#!/bin/sh
echo "\$*" >> "\$TIMEOUT_LOG"
exec "$REAL_TIMEOUT" "\$@"
SH
    chmod +x "$BIN/timeout"
}

@test "a pre-flight that hits the 10 s bound (timeout exits 124) falls back to a direct run" {
    # coreutils' timeout exits 124 when the bound is hit; a fake that says so at once covers the wrapper's
    # branch in milliseconds, where a real 20 s sleeper and a 9-16 s wall-clock window could miss on a
    # loaded runner. The fake IS the timeout(1) the wrapper finds, so this runs on a box without one too.
    export TIMEOUT_LOG="$TEST_DIR/timeout.calls"
    cat > "$BIN/timeout" <<'SH'
#!/bin/sh
echo "$*" >> "$TIMEOUT_LOG"
exit 124
SH
    chmod +x "$BIN/timeout"
    ERR="$TEST_DIR/stderr"
    run sh -c '"$0" --input-format stream-json 2>"$1"' "$WRAPPER" "$ERR"
    [ "$status" -eq 0 ]
    # the bound was asked for once, with the pre-flight behind it; systemd-run itself never ran — not for
    # the pre-flight (the fake cut it) and not for the CLI (it ran directly)
    [ "$(wc -l < "$TIMEOUT_LOG")" -eq 1 ]
    [ "$(cat "$TIMEOUT_LOG")" = "10 systemd-run --user --scope --quiet --collect -- true" ]
    [ ! -e "$FAKE_CALLS" ]
    [[ "$output" == *"REAL pid="* ]]
    [ "$(printf '%s\n' "$output" | grep '^ARG:')" = "$(printf 'ARG:%s\n' --input-format stream-json)" ]
    # one stderr line, the fallback form, naming the bound as the reason
    [ "$(wc -l < "$ERR")" -eq 1 ]
    grep -q '^romp-cli-scope: fallback: ' "$ERR"
    grep -q 'did not finish within 10 s' "$ERR"
    grep -q 'directly' "$ERR"
}

@test "the pre-flight runs as \`timeout 10 systemd-run …\`; the CLI's own scope does not go through timeout" {
    _fake_timeout
    run "$WRAPPER" a
    [ "$status" -eq 0 ]
    [[ "$output" == *"REAL pid="* ]]
    [ "$(wc -l < "$TIMEOUT_LOG")" -eq 1 ]
    [ "$(cat "$TIMEOUT_LOG")" = "10 systemd-run --user --scope --quiet --collect -- true" ]
    # both systemd-run calls still happened: the pre-flight (through timeout) and the scoped CLI
    [ "$(wc -l < "$FAKE_CALLS")" -eq 2 ]
    [[ "$(sed -n 2p "$FAKE_CALLS")" == *"--unit=romp-session-11111111-"* ]]
}

@test "without timeout on PATH the pre-flight runs unbounded and a slow one still leads to a scoped CLI" {
    # a PATH with the fake systemd-run, `date` (the unit name needs it) and `sleep` (the fake's
    # pre-flight delay needs it), but no timeout at all
    mkdir -p "$TEST_DIR/tools"
    ln -s "$(command -v date)" "$TEST_DIR/tools/date"
    ln -s "$(command -v sleep)" "$TEST_DIR/tools/sleep"
    _preflight_sleeps 2
    t0="$(date +%s)"
    PATH="$BIN:$TEST_DIR/tools" run "$WRAPPER" a
    t1="$(date +%s)"
    [ "$status" -eq 0 ]
    [ $((t1 - t0)) -ge 1 ]
    # not cut off: the pre-flight completed and the CLI ran in its scope
    [ "$(wc -l < "$FAKE_CALLS")" -eq 2 ]
    [[ "$(sed -n 2p "$FAKE_CALLS")" == *"--unit=romp-session-11111111-"* ]]
    [[ "$output" == *"REAL pid="* ]]
}

@test "exec in place: the CLI keeps the wrapper's pid and its parent is the launching shell" {
    # Launch from a shell whose pid we know; the wrapper execs systemd-run, which execs the CLI —
    # so the CLI's pid must be that shell's pid and its ppid the shell's parent (this bats
    # process), with no extra parent between them.
    run sh -c 'echo "SH pid=$$ ppid=$PPID"; exec "$0"' "$WRAPPER"
    [ "$status" -eq 0 ]
    sh_pid="$(printf '%s\n' "$output" | sed -n 's/^SH pid=\([0-9]*\) .*/\1/p')"
    sh_ppid="$(printf '%s\n' "$output" | sed -n 's/^SH pid=[0-9]* ppid=\([0-9]*\)$/\1/p')"
    real_pid="$(printf '%s\n' "$output" | sed -n 's/^REAL pid=\([0-9]*\) .*/\1/p')"
    real_ppid="$(printf '%s\n' "$output" | sed -n 's/^REAL pid=[0-9]* ppid=\([0-9]*\)$/\1/p')"
    [ -n "$sh_pid" ] && [ -n "$real_pid" ]
    [ "$real_pid" = "$sh_pid" ]
    [ "$real_ppid" = "$sh_ppid" ]
}

# Exec-in-place and a clean stdout on the DIRECT paths (2026-09-05). The scoped path's pid test above
# says nothing about the four direct paths, and a wrapper that forked the CLI (or printed anything of
# its own) passed every test here. Both matter: the kernel finds the CLI by the pid the SDK spawned,
# and one stray byte on stdout corrupts the SDK's stream-json protocol. So: launch the wrapper as a
# background job (its pid is known before it runs), wait, and check that the fake CLI's own $$ IS that
# pid, its parent is this shell, and stdout is EXACTLY the fake CLI's output.
# $1..: `VAR=value` settings for the launch (none is fine), then `--`, then the wrapper's arguments.
assert_direct_exec_in_place() {
    local envs=()
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do envs+=("$1"); shift; done
    shift
    OUT="$TEST_DIR/stdout"; ERR="$TEST_DIR/stderr"; EXP="$TEST_DIR/expected"
    env "${envs[@]}" "$WRAPPER" "$@" >"$OUT" 2>"$ERR" 3>&- &
    local wpid=$!
    wait "$wpid"
    [ -s "$REAL_PIDFILE" ]
    [ "$(cat "$REAL_PIDFILE")" = "$wpid" ]
    printf 'REAL pid=%s ppid=%s\n' "$wpid" "$BASHPID" > "$EXP"
    printf 'ARG:%s\n' "$@" >> "$EXP"
    cmp -s "$OUT" "$EXP"
    [ ! -e "$FAKE_LOG" ]   # never a scoped launch on a direct path
}

@test "direct path, ROMP_CLI_SCOPE=0: exec in place, stdout exactly the CLI's, stderr empty" {
    assert_direct_exec_in_place ROMP_CLI_SCOPE=0 -- --input-format stream-json "two words" ""
    [ ! -s "$ERR" ]
    [ ! -e "$FAKE_CALLS" ]
}

@test "direct path, no systemd-run on PATH: exec in place, stdout exactly the CLI's, stderr empty" {
    mkdir -p "$TEST_DIR/tools"   # a PATH with nothing on it the wrapper could mistake for systemd-run
    assert_direct_exec_in_place "PATH=$TEST_DIR/tools" -- --input-format stream-json
    [ ! -s "$ERR" ]
    [ ! -e "$FAKE_CALLS" ]
}

@test "direct path, empty ROMP_SID (a probe): exec in place, stdout exactly the CLI's, stderr empty" {
    assert_direct_exec_in_place ROMP_SID= -- -v
    [ ! -s "$ERR" ]
    [ ! -e "$FAKE_CALLS" ]
}

@test "direct path, a failed pre-flight: exec in place, stdout exactly the CLI's, ONE stderr line" {
    cat > "$BIN/systemd-run" <<'SH'
#!/bin/sh
echo "$*" >> "$FAKE_CALLS"
echo "Failed to connect to bus: No such file or directory" >&2
exit 1
SH
    chmod +x "$BIN/systemd-run"
    assert_direct_exec_in_place -- --input-format stream-json "two words"
    # the notice went to stderr, never stdout, in the fallback form
    [ "$(wc -l < "$ERR")" -eq 1 ]
    grep -q '^romp-cli-scope: fallback: ' "$ERR"
    [ "$(wc -l < "$FAKE_CALLS")" -eq 1 ]
}

@test "ROMP_CLI_SCOPE=0 runs the real CLI directly — systemd-run is not invoked" {
    ROMP_CLI_SCOPE=0 run "$WRAPPER" x "y z"
    [ "$status" -eq 0 ]
    [ ! -e "$FAKE_LOG" ]
    [ "$(printf '%s\n' "$output" | grep '^ARG:')" = "$(printf 'ARG:%s\n' x "y z")" ]
}

@test "no systemd-run on PATH: the real CLI runs directly" {
    rm "$BIN/systemd-run"
    PATH="$BIN" run "$WRAPPER" --input-format stream-json
    [ "$status" -eq 0 ]
    [ ! -e "$FAKE_LOG" ]
    [[ "$output" == *"ARG:--input-format"* ]]
}

# The refusal tests run the wrapper under an inner sh that reports the exit code on stdout: bats's
# `run` warns on a bare 127 (it reads it as command-not-found), and 127 is exactly the code wanted.
# The line is the `refused:` form, not `fallback:`: no CLI ran, so the kernel logs no fallback for
# it and leaves it to the launch-error card (tests/test_cli_scope.py FallbackNotice).
@test "an empty ROMP_CLI_REAL is refused: exit 127 and one \`refused:\` stderr line naming the variable" {
    ERR="$TEST_DIR/stderr"
    ROMP_CLI_REAL= run sh -c '"$0" a 2>"$1"; echo "exit=$?"' "$WRAPPER" "$ERR"
    [ "$status" -eq 0 ]
    [ "$output" = "exit=127" ]
    [ "$(wc -l < "$ERR")" -eq 1 ]
    grep -q '^romp-cli-scope: refused: ' "$ERR"
    grep -q 'ROMP_CLI_REAL' "$ERR"
    [ ! -e "$FAKE_LOG" ]
}

@test "an unset ROMP_CLI_REAL is refused the same way" {
    unset ROMP_CLI_REAL
    ERR="$TEST_DIR/stderr"
    run sh -c '"$0" 2>"$1"; echo "exit=$?"' "$WRAPPER" "$ERR"
    [ "$status" -eq 0 ]
    [ "$output" = "exit=127" ]
    [ "$(wc -l < "$ERR")" -eq 1 ]
    grep -q '^romp-cli-scope: refused: ' "$ERR"
    grep -q 'ROMP_CLI_REAL' "$ERR"
}

# ── the per-session limits (2026-09-06) ────────────────────────────────────────────────────────────
# ROMP_CLI_SCOPE_MEMORY_MAX / _MEMORY_HIGH / _MEMORY_SWAP_MAX become `-p MemoryMax= / MemoryHigh= /
# MemorySwapMax=` on the scope, with `-p OOMPolicy=continue` whenever one is set (a scope's default
# `stop` ends the whole scope on one OOM kill); ROMP_CLI_SCOPE_OOM_SCORE_ADJ is written to the
# wrapper's own /proc/self/oom_score_adj before the exec. Every one opt-in; the kernel validates the
# same rules first (tests/test_cli_scope.py LimitRules runs size_ok/adj_ok over the same corpus).

@test "the memory limits become -p properties on the pre-flight and on the CLI's scope, with OOMPolicy=continue" {
    ERR="$TEST_DIR/stderr"
    ROMP_CLI_SCOPE_MEMORY_MAX=16G ROMP_CLI_SCOPE_MEMORY_HIGH=12G ROMP_CLI_SCOPE_MEMORY_SWAP_MAX=0 \
        run sh -c '"$0" --input-format stream-json 2>"$1"' "$WRAPPER" "$ERR"
    [ "$status" -eq 0 ]
    [[ "$output" == *"REAL pid="* ]]
    [ ! -s "$ERR" ]
    # both calls carry the same properties, in variable order, ahead of `--`
    [ "$(wc -l < "$FAKE_CALLS")" -eq 2 ]
    [[ "$(sed -n 1p "$FAKE_CALLS")" == *"--user --scope --quiet --collect -p MemoryMax=16G -p MemoryHigh=12G -p MemorySwapMax=0 -p OOMPolicy=continue -- true" ]]
    [[ "$(sed -n 2p "$FAKE_CALLS")" == *"--description=romp session $ROMP_SID -p MemoryMax=16G -p MemoryHigh=12G -p MemorySwapMax=0 -p OOMPolicy=continue -- $REAL --input-format stream-json" ]]
    # one argv element each: no property leaked into the CLI's arguments
    tail_after_dash="$(sed -n '/^--$/,$p' "$FAKE_LOG" | sed 1d)"
    [ "$tail_after_dash" = "$(printf '%s\n' "$REAL" --input-format stream-json)" ]
    grep -qx -- '-p' "$FAKE_LOG"
    grep -qx -- 'MemoryMax=16G' "$FAKE_LOG"
    grep -qx -- 'OOMPolicy=continue' "$FAKE_LOG"
}

@test "one limit alone still brings OOMPolicy=continue; none, or empty ones, bring no -p at all" {
    ROMP_CLI_SCOPE_MEMORY_MAX=16G run "$WRAPPER" a
    [ "$status" -eq 0 ]
    [[ "$(sed -n 2p "$FAKE_CALLS")" == *" -p MemoryMax=16G -p OOMPolicy=continue -- $REAL a" ]]
    rm -f "$FAKE_CALLS" "$FAKE_LOG"
    run "$WRAPPER" a
    [ "$status" -eq 0 ]
    run grep -q -- '-p' "$FAKE_CALLS"
    [ "$status" -ne 0 ]
    rm -f "$FAKE_CALLS" "$FAKE_LOG"
    # empty is what the kernel sends down for an unset or refused variable
    ERR="$TEST_DIR/stderr"
    ROMP_CLI_SCOPE_MEMORY_MAX= ROMP_CLI_SCOPE_MEMORY_HIGH= ROMP_CLI_SCOPE_MEMORY_SWAP_MAX= ROMP_CLI_SCOPE_OOM_SCORE_ADJ= \
        run sh -c '"$0" a 2>"$1"' "$WRAPPER" "$ERR"
    [ "$status" -eq 0 ]
    [ ! -s "$ERR" ]
    run grep -q -- '-p' "$FAKE_CALLS"
    [ "$status" -ne 0 ]
}

@test "a value that is not a size is skipped with one ignored: line naming the variable; the others and the scope stand" {
    ERR="$TEST_DIR/stderr"
    ROMP_CLI_SCOPE_MEMORY_MAX=abc ROMP_CLI_SCOPE_MEMORY_HIGH=12M \
        run sh -c '"$0" a 2>"$1"' "$WRAPPER" "$ERR"
    [ "$status" -eq 0 ]
    [[ "$output" == *"REAL pid="* ]]
    [ "$(wc -l < "$ERR")" -eq 1 ]
    grep -q '^romp-cli-scope: ignored: ROMP_CLI_SCOPE_MEMORY_MAX is not a size' "$ERR"
    grep -q 'runs in its scope without it' "$ERR"
    run grep -q 'abc' "$ERR"
    [ "$status" -ne 0 ]          # the variable is named, its value is not echoed
    # the scope was started, with the valid limit only
    [ "$(wc -l < "$FAKE_CALLS")" -eq 2 ]
    [[ "$(sed -n 2p "$FAKE_CALLS")" == *"--unit=romp-session-11111111-"* ]]
    [[ "$(sed -n 2p "$FAKE_CALLS")" == *" -p MemoryHigh=12M -p OOMPolicy=continue -- $REAL a" ]]
    run grep -q 'MemoryMax' "$FAKE_CALLS"
    [ "$status" -ne 0 ]
}

@test "the size rule: digits with one optional K/M/G/T suffix, or infinity; nothing else" {
    local good=(16G 8192M 1024 0 1T 5K infinity 016M)
    local bad=(16g abc "16 G" 16GB 1.5G -1 G 50% Infinity 16GG " 16G" 1_000 K16 0x10 16E 16P "1G 512M")
    local v
    for v in "${good[@]}"; do
        rm -f "$FAKE_CALLS"
        ERR="$TEST_DIR/stderr"
        ROMP_CLI_SCOPE_MEMORY_MAX="$v" run sh -c '"$0" 2>"$1"' "$WRAPPER" "$ERR"
        [ "$status" -eq 0 ]
        [ ! -s "$ERR" ] || { echo "refused a good size: $v"; cat "$ERR"; false; }
        grep -q -- "-p MemoryMax=$v -p OOMPolicy=continue" "$FAKE_CALLS"
    done
    for v in "${bad[@]}"; do
        rm -f "$FAKE_CALLS"
        ERR="$TEST_DIR/stderr"
        ROMP_CLI_SCOPE_MEMORY_MAX="$v" run sh -c '"$0" 2>"$1"' "$WRAPPER" "$ERR"
        [ "$status" -eq 0 ]
        grep -q '^romp-cli-scope: ignored: ROMP_CLI_SCOPE_MEMORY_MAX is not a size' "$ERR" || { echo "accepted a bad size: $v"; false; }
        run grep -q 'MemoryMax' "$FAKE_CALLS"
        [ "$status" -ne 0 ]
    done
}

@test "a systemd-run that rejects the properties: the pre-flight re-runs bare and the CLI gets its scope without them, after one ignored: line" {
    # an older systemd (OOMPolicy= on scopes needs 253) refuses a scope carrying the properties; the
    # pre-flight carries them so it is the pre-flight that fails, not the CLI's own launch, and the
    # bare retry gives the CLI a scope without them (the memory limits, all of them, for this launch)
    cat > "$BIN/systemd-run" <<'SH'
#!/bin/sh
printf '%s\n' "$@" > "$FAKE_LOG"
echo "$*" >> "$FAKE_CALLS"
case " $* " in *" -p "*) echo "Failed to start transient scope unit: Unknown assignment: OOMPolicy=continue" >&2; exit 1 ;; esac
while [ "$#" -gt 0 ]; do a="$1"; shift; [ "$a" = "--" ] && break; done
exec "$@"
SH
    chmod +x "$BIN/systemd-run"
    ERR="$TEST_DIR/stderr"
    ROMP_CLI_SCOPE_MEMORY_MAX=16G run sh -c '"$0" a 2>"$1"' "$WRAPPER" "$ERR"
    [ "$status" -eq 0 ]
    [[ "$output" == *"REAL pid="* ]]
    # with the properties (refused), bare (passes), with the properties once more (refused again: it is
    # them, not a passing fault), then the CLI's scope without them
    [ "$(wc -l < "$FAKE_CALLS")" -eq 4 ]
    [[ "$(sed -n 1p "$FAKE_CALLS")" == *"-p MemoryMax=16G -p OOMPolicy=continue -- true" ]]
    [ "$(sed -n 2p "$FAKE_CALLS")" = "--user --scope --quiet --collect -- true" ]
    [[ "$(sed -n 3p "$FAKE_CALLS")" == *"-p MemoryMax=16G -p OOMPolicy=continue -- true" ]]
    [[ "$(sed -n 4p "$FAKE_CALLS")" == *"--unit=romp-session-11111111-"* ]]
    [[ "$(sed -n 4p "$FAKE_CALLS")" == *"--description=romp session $ROMP_SID -- $REAL a" ]]
    # one stderr line: the ignored form, the properties it dropped, systemd-run's own first line
    [ "$(wc -l < "$ERR")" -eq 1 ]
    grep -q '^romp-cli-scope: ignored: systemd-run rejected the per-session limits' "$ERR"
    grep -q -- '-p MemoryMax=16G -p OOMPolicy=continue' "$ERR"
    grep -q 'Unknown assignment' "$ERR"
    grep -q 'runs in its scope without it' "$ERR"
}

@test "the ignored: line quotes the failure that decided — the second refusal of the properties, not the first" {
    # call 1 (with the properties) fails with a bus fault, call 2 (bare) passes, call 3 (with them
    # again) fails with the real rejection: that third call is what drops the limits, and the line
    # must quote it — quoting the first named a transient fault as the reason a limit was dropped
    cat > "$BIN/systemd-run" <<'SH'
#!/bin/sh
printf '%s\n' "$@" > "$FAKE_LOG"
echo "$*" >> "$FAKE_CALLS"
n="$(wc -l < "$FAKE_CALLS")"
case "$n" in
    1) echo "Failed to connect to bus: Connection timed out" >&2; exit 1 ;;
    3) echo "Failed to start transient scope unit: Unknown assignment: OOMPolicy=continue" >&2; exit 1 ;;
esac
while [ "$#" -gt 0 ]; do a="$1"; shift; [ "$a" = "--" ] && break; done
exec "$@"
SH
    chmod +x "$BIN/systemd-run"
    ERR="$TEST_DIR/stderr"
    ROMP_CLI_SCOPE_MEMORY_MAX=16G run sh -c '"$0" a 2>"$1"' "$WRAPPER" "$ERR"
    [ "$status" -eq 0 ]
    [[ "$output" == *"REAL pid="* ]]
    [ "$(wc -l < "$FAKE_CALLS")" -eq 4 ]
    [[ "$(sed -n 4p "$FAKE_CALLS")" == *"--description=romp session $ROMP_SID -- $REAL a" ]]
    [ "$(wc -l < "$ERR")" -eq 1 ]
    grep -q '^romp-cli-scope: ignored: systemd-run rejected the per-session limits' "$ERR"
    grep -q 'Unknown assignment' "$ERR"
    run grep -q 'Connection timed out' "$ERR"
    [ "$status" -ne 0 ]
}

@test "a pre-flight that fails once and then passes keeps the limits: a passing fault is not a refused property" {
    # the fake fails its FIRST call only (a bus hiccup); the bare retry passes, the retry with the
    # properties passes, and the CLI gets its scope with them, with nothing on stderr
    cat > "$BIN/systemd-run" <<'SH'
#!/bin/sh
printf '%s\n' "$@" > "$FAKE_LOG"
echo "$*" >> "$FAKE_CALLS"
[ "$(wc -l < "$FAKE_CALLS")" -gt 1 ] || { echo "Failed to connect to bus: Connection timed out" >&2; exit 1; }
while [ "$#" -gt 0 ]; do a="$1"; shift; [ "$a" = "--" ] && break; done
exec "$@"
SH
    chmod +x "$BIN/systemd-run"
    ERR="$TEST_DIR/stderr"
    ROMP_CLI_SCOPE_MEMORY_MAX=16G run sh -c '"$0" a 2>"$1"' "$WRAPPER" "$ERR"
    [ "$status" -eq 0 ]
    [[ "$output" == *"REAL pid="* ]]
    [ ! -s "$ERR" ]
    [ "$(wc -l < "$FAKE_CALLS")" -eq 4 ]
    [[ "$(sed -n 1p "$FAKE_CALLS")" == *"-p MemoryMax=16G -p OOMPolicy=continue -- true" ]]
    [ "$(sed -n 2p "$FAKE_CALLS")" = "--user --scope --quiet --collect -- true" ]
    [[ "$(sed -n 3p "$FAKE_CALLS")" == *"-p MemoryMax=16G -p OOMPolicy=continue -- true" ]]
    [[ "$(sed -n 4p "$FAKE_CALLS")" == *"--unit=romp-session-11111111-"*" -p MemoryMax=16G -p OOMPolicy=continue -- $REAL a" ]]
}

@test "with limits set, a pre-flight that fails bare too falls back to a direct run with the fallback line, not ignored:" {
    cat > "$BIN/systemd-run" <<'SH'
#!/bin/sh
echo "$*" >> "$FAKE_CALLS"
echo "Failed to connect to bus: No such file or directory" >&2
exit 1
SH
    chmod +x "$BIN/systemd-run"
    ERR="$TEST_DIR/stderr"
    ROMP_CLI_SCOPE_MEMORY_MAX=16G run sh -c '"$0" a 2>"$1"' "$WRAPPER" "$ERR"
    [ "$status" -eq 0 ]
    [[ "$output" == *"REAL pid="* ]]
    [ "$(wc -l < "$FAKE_CALLS")" -eq 2 ]     # the pre-flight with the properties, then bare; never the CLI
    [[ "$(sed -n 1p "$FAKE_CALLS")" == *"-p MemoryMax=16G -p OOMPolicy=continue -- true" ]]
    [ "$(sed -n 2p "$FAKE_CALLS")" = "--user --scope --quiet --collect -- true" ]
    [ "$(wc -l < "$ERR")" -eq 1 ]
    grep -q '^romp-cli-scope: fallback: ' "$ERR"
    grep -q 'Failed to connect to bus' "$ERR"
    run grep -q 'ignored:' "$ERR"
    [ "$status" -ne 0 ]
}

@test "with limits set, the bounded pre-flight carries them: timeout 10 systemd-run … -p … -- true" {
    _fake_timeout
    ROMP_CLI_SCOPE_MEMORY_MAX=16G ROMP_CLI_SCOPE_MEMORY_SWAP_MAX=0 run "$WRAPPER" a
    [ "$status" -eq 0 ]
    [ "$(wc -l < "$TIMEOUT_LOG")" -eq 1 ]
    [ "$(cat "$TIMEOUT_LOG")" = "10 systemd-run --user --scope --quiet --collect -p MemoryMax=16G -p MemorySwapMax=0 -p OOMPolicy=continue -- true" ]
}

# The adjustment is written to the wrapper's OWN oom_score_adj, ahead of the pre-flight; exec-in-place
# carries it to the CLI on every path that reaches one, the fallback included. A second fake CLI
# reports its adjustment (the first one's stdout is pinned byte-for-byte elsewhere).
_adj_cli() {
    REAL_ADJ="$TEST_DIR/claude-adj"
    cat > "$REAL_ADJ" <<'SH'
#!/bin/sh
echo "ADJ:$(cat /proc/self/oom_score_adj)"
SH
    chmod +x "$REAL_ADJ"
    export ROMP_CLI_REAL="$REAL_ADJ"
}

@test "ROMP_CLI_SCOPE_OOM_SCORE_ADJ raises the CLI's own oom_score_adj; no -p for it" {
    [ -r /proc/self/oom_score_adj ] || skip "no /proc/self/oom_score_adj on this box"
    local cur; cur="$(cat /proc/self/oom_score_adj)"
    [ "$cur" -le 900 ] || skip "this process already sits near the top of the range"
    _adj_cli
    ERR="$TEST_DIR/stderr"
    ROMP_CLI_SCOPE_OOM_SCORE_ADJ=$((cur + 100)) run sh -c '"$0" 2>"$1"' "$WRAPPER" "$ERR"
    [ "$status" -eq 0 ]
    [ "$output" = "ADJ:$((cur + 100))" ]
    [ ! -s "$ERR" ]
    run grep -q -- '-p' "$FAKE_CALLS"
    [ "$status" -ne 0 ]
    # and the test's own process is untouched: the write was on the wrapper, which exec'd away
    [ "$(cat /proc/self/oom_score_adj)" = "$cur" ]
}

@test "an adjustment that is not an integer in -1000..1000 is skipped with one ignored: line; the CLI keeps its inherited value" {
    [ -r /proc/self/oom_score_adj ] || skip "no /proc/self/oom_score_adj on this box"
    local cur; cur="$(cat /proc/self/oom_score_adj)"
    _adj_cli
    local v
    for v in 1001 -1001 +5 5x -- - 1e3 abc 0100 -0100 01000 00; do   # leading zeros: octal to Linux, refused
        ERR="$TEST_DIR/stderr"
        ROMP_CLI_SCOPE_OOM_SCORE_ADJ="$v" run sh -c '"$0" 2>"$1"' "$WRAPPER" "$ERR"
        [ "$status" -eq 0 ]
        [ "$output" = "ADJ:$cur" ] || { echo "value $v: $output"; false; }
        [ "$(wc -l < "$ERR")" -eq 1 ]
        grep -q '^romp-cli-scope: ignored: ROMP_CLI_SCOPE_OOM_SCORE_ADJ is not an integer in -1000..1000' "$ERR"
    done
}

@test "an adjustment below what this user may set is reported as not applied, and the CLI still runs in its scope" {
    [ -r /proc/self/oom_score_adj ] || skip "no /proc/self/oom_score_adj on this box"
    [ "$(id -u)" -ne 0 ] || skip "root may lower it"
    local cur; cur="$(cat /proc/self/oom_score_adj)"
    [ "$cur" -gt -1000 ] || skip "already at the floor"
    _adj_cli
    ERR="$TEST_DIR/stderr"
    ROMP_CLI_SCOPE_OOM_SCORE_ADJ=-1000 run sh -c '"$0" 2>"$1"' "$WRAPPER" "$ERR"
    [ "$status" -eq 0 ]
    [ "$output" = "ADJ:$cur" ]
    [ "$(wc -l < "$ERR")" -eq 1 ]
    grep -q '^romp-cli-scope: ignored: ROMP_CLI_SCOPE_OOM_SCORE_ADJ could not be applied (the write was refused: ' "$ERR"
    grep -q 'privilege' "$ERR"
    run grep -q 'cannot be opened' "$ERR"     # the file opened; only the write was refused
    [ "$status" -ne 0 ]
    [[ "$(sed -n 2p "$FAKE_CALLS")" == *"--unit=romp-session-11111111-"* ]]
}

_wrapper_fns() {   # $@: function names — their bodies, lifted verbatim out of the wrapper (which execs, so cannot be
    local name       # sourced), each followed by a newline, so a command can be appended to the result of $(…)
    for name in "$@"; do sed -n "/^$name() {/,/^}/p" "$WRAPPER"; echo; done
}

@test "apply_adj names what failed: a file it cannot open for writing is reported as that, never as the floor" {
    # the wrapper writes /proc/self/oom_score_adj and nothing else, and that file cannot be made unopenable
    # here without a user namespace, so its own function runs, lifted, against a file this user cannot open
    # for writing — the same two checks it makes on the real one, in the same shell. Before this, the one
    # line asserted the floor for any failure, so a read-only /proc in a hardened container sent the operator
    # hunting a floor (a review finding, 2026-09-06)
    [ "$(id -u)" -ne 0 ] || skip "root can open anything"
    : > "$TEST_DIR/oom_score_adj" && chmod 444 "$TEST_DIR/oom_score_adj"
    run sh -c "$(_wrapper_fns ignored apply_adj)"$'\n''apply_adj "$1" "$2"; cat "$2"' sh 500 "$TEST_DIR/oom_score_adj"
    [ "$status" -eq 0 ]
    [ "$output" = "romp-cli-scope: ignored: ROMP_CLI_SCOPE_OOM_SCORE_ADJ could not be applied ($TEST_DIR/oom_score_adj cannot be opened for writing by this process) — the CLI runs in its scope without it" ]
    run grep -c 'privilege' <<< "$output"
    [ "$output" = "0" ]
}

@test "apply_adj on this process's own file: the floor line for a value below it, no line for the inherited value" {
    [ -r /proc/self/oom_score_adj ] || skip "no /proc/self/oom_score_adj on this box"
    [ "$(id -u)" -ne 0 ] || skip "root may lower it"
    local cur; cur="$(cat /proc/self/oom_score_adj)"
    [ "$cur" -gt -1000 ] || skip "already at the floor"
    local fns; fns="$(_wrapper_fns ignored apply_adj)"$'\n'
    # `run` forks, so the sh below writes its own oom_score_adj, and prints it after the attempt
    run sh -c "$fns"'apply_adj "$1" /proc/self/oom_score_adj; cat /proc/self/oom_score_adj' sh -1000
    [ "$status" -eq 0 ]
    [ "${lines[0]}" = "romp-cli-scope: ignored: ROMP_CLI_SCOPE_OOM_SCORE_ADJ could not be applied (the write was refused: a value below the user manager's own oom_score_adj needs a privilege it does not have) — the CLI runs in its scope without it" ]
    [ "${lines[1]}" = "$cur" ]
    run sh -c "$fns"'apply_adj "$1" /proc/self/oom_score_adj; cat /proc/self/oom_score_adj' sh "$cur"
    [ "$status" -eq 0 ]
    [ "$output" = "$cur" ]
    [ "$(cat /proc/self/oom_score_adj)" = "$cur" ]
}

@test "the adjustment applies on the fallback path too: a session run directly still carries it, with the one fallback line" {
    # the write is to /proc/self and needs no scope; before it moved ahead of the pre-flight, a fallback
    # launch skipped it silently while the kernel's boot line kept calling the adjustment in force
    [ -r /proc/self/oom_score_adj ] || skip "no /proc/self/oom_score_adj on this box"
    local cur; cur="$(cat /proc/self/oom_score_adj)"
    [ "$cur" -le 900 ] || skip "this process already sits near the top of the range"
    _adj_cli
    cat > "$BIN/systemd-run" <<'SH'
#!/bin/sh
echo "$*" >> "$FAKE_CALLS"
echo "Failed to connect to bus: No such file or directory" >&2
exit 1
SH
    chmod +x "$BIN/systemd-run"
    ERR="$TEST_DIR/stderr"
    ROMP_CLI_SCOPE_MEMORY_MAX=16G ROMP_CLI_SCOPE_OOM_SCORE_ADJ=$((cur + 100)) run sh -c '"$0" 2>"$1"' "$WRAPPER" "$ERR"
    [ "$status" -eq 0 ]
    [ "$output" = "ADJ:$((cur + 100))" ]
    [ "$(wc -l < "$FAKE_CALLS")" -eq 2 ]     # the pre-flight with the properties, then bare; the CLI ran directly
    [ "$(wc -l < "$ERR")" -eq 1 ]
    grep -q '^romp-cli-scope: fallback: ' "$ERR"
    run grep -q 'ignored:' "$ERR"
    [ "$status" -ne 0 ]
    [ "$(cat /proc/self/oom_score_adj)" = "$cur" ]
}

@test "properties systemd rejects do not cost the adjustment: the CLI carries it in its bare scope, after the one ignored: line" {
    [ -r /proc/self/oom_score_adj ] || skip "no /proc/self/oom_score_adj on this box"
    local cur; cur="$(cat /proc/self/oom_score_adj)"
    [ "$cur" -le 900 ] || skip "this process already sits near the top of the range"
    _adj_cli
    cat > "$BIN/systemd-run" <<'SH'
#!/bin/sh
echo "$*" >> "$FAKE_CALLS"
case " $* " in *" -p "*) echo "Failed to start transient scope unit: Unknown assignment: OOMPolicy=continue" >&2; exit 1 ;; esac
while [ "$#" -gt 0 ]; do a="$1"; shift; [ "$a" = "--" ] && break; done
exec "$@"
SH
    chmod +x "$BIN/systemd-run"
    ERR="$TEST_DIR/stderr"
    ROMP_CLI_SCOPE_MEMORY_MAX=16G ROMP_CLI_SCOPE_OOM_SCORE_ADJ=$((cur + 100)) run sh -c '"$0" 2>"$1"' "$WRAPPER" "$ERR"
    [ "$status" -eq 0 ]
    [ "$output" = "ADJ:$((cur + 100))" ]
    [ "$(wc -l < "$FAKE_CALLS")" -eq 4 ]
    [[ "$(sed -n 4p "$FAKE_CALLS")" == *"--unit=romp-session-11111111-"* ]]
    run grep -q -- '-p' <<< "$(sed -n 4p "$FAKE_CALLS")"
    [ "$status" -ne 0 ]
    [ "$(wc -l < "$ERR")" -eq 1 ]
    grep -q '^romp-cli-scope: ignored: systemd-run rejected the per-session limits' "$ERR"
}
