#!/usr/bin/env bats

# Resolve path to the romp script under test
ROMP_SCRIPT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp"

setup() {
    TEST_DIR="$(mktemp -d)"
    WORK_DIR="$TEST_DIR/myproject"
    MOCK_DIR="$TEST_DIR/mock"
    export MOCK_LOG="$TEST_DIR/mock.log"

    mkdir -p "$WORK_DIR" "$MOCK_DIR"

    # Fixtures the tmux mock reads:
    #   sessions file: one per line, "name" or "name|rompflag" (flag defaults to 1)
    #   identity file: "name=colour" lines (for @identity-bg lookups)
    export MOCK_TMUX_SESSIONS_FILE="$TEST_DIR/mock_sessions.txt"
    export MOCK_TMUX_IDENTITY_FILE="$TEST_DIR/mock_identity.txt"
    touch "$MOCK_TMUX_SESSIONS_FILE" "$MOCK_TMUX_IDENTITY_FILE"

    cat > "$MOCK_DIR/tmux" << 'MOCK'
#!/usr/bin/env bash
echo "tmux $*" >> "$MOCK_LOG"
# Opt-in: simulate an older tmux that rejects a given option (e.g. tmux 3.0 has no
# copy-mode-position-style, added in 3.2). Off unless a test sets MOCK_TMUX_FAIL_OPT.
if [[ -n "${MOCK_TMUX_FAIL_OPT:-}" && "$*" == *"$MOCK_TMUX_FAIL_OPT"* ]]; then
  echo "invalid option: $MOCK_TMUX_FAIL_OPT" >&2
  exit 1
fi
case "$1" in
  has-session)
    # $3 is "=<name>"; a session exists iff its name is in the file
    target="${3#=}"
    cut -d'|' -f1 "$MOCK_TMUX_SESSIONS_FILE" 2>/dev/null | grep -qx "$target" && exit 0
    exit 1
    ;;
  display-message)
    echo "${MOCK_TMUX_CURRENT:-mysession}"
    exit 0
    ;;
  list-sessions)
    # Reformat each session line per the requested -F format ($3).
    # @romp defaults to 1; a "name|0" line is a non-romp session.
    fmt="$3"
    while IFS='|' read -r s c; do
      [[ -z "$s" ]] && continue
      c="${c:-1}"
      out="$fmt"
      out="${out//'#{@romp}'/$c}"
      out="${out//'#{session_name}'/$s}"
      out="${out//'#S'/$s}"
      echo "$out"
    done < "$MOCK_TMUX_SESSIONS_FILE" 2>/dev/null
    exit 0
    ;;
  show)
    if [[ "$2" == "-t" && "$4" == "-v" && "$5" == "@identity-bg" ]]; then
      result=$(grep "^${3}=" "$MOCK_TMUX_IDENTITY_FILE" 2>/dev/null | head -1 | cut -d= -f2)
      [[ -n "$result" ]] && { echo "$result"; exit 0; }
      exit 1
    fi
    # global status-format[0] — the default main-row composition the
    # provisioning pins onto each session (sentinel for assertions)
    if [[ "$2" == "-gv" && "$3" == "status-format[0]" ]]; then
      echo "GLOBAL_ROW0"; exit 0
    fi
    exit 0
    ;;
esac
exit 0
MOCK
    chmod +x "$MOCK_DIR/tmux"

    # Hermetic claude: the launch path probes `claude --version` for the 2.1.224
    # floor (the inbound-accept setting + @romp-inbound-accept tag) — a dev
    # machine's real claude would nondeterministically flip those on. Pin a
    # modern version; per-test override via _stub_claude.
    _stub_claude "2.1.226"

    export PATH="$MOCK_DIR:$PATH"
    unset TMUX            # default: outside tmux → attach-session branch
    # Hermetic HOME: bin/romp probes $HOME/.claude/romp-postal.mcp.json (would
    # nondeterministically append --mcp-config on a dev machine) and writes the
    # names map under XDG_STATE_HOME (was polluting the REAL state dir).
    export HOME="$TEST_DIR/home"
    export XDG_STATE_HOME="$HOME/.local/state"
    mkdir -p "$HOME"
    cd "$WORK_DIR"
}

teardown() {
    # Tests that launch a background romp-manager record its pid in MGR_PID so we
    # always reap it (and its child kernels), even if an assertion aborted the test.
    [[ -n "${MGR_PID:-}" ]] && kill "$MGR_PID" 2>/dev/null
    rm -rf "$TEST_DIR"
}

# Helper — runs romp with merged stdout+stderr so BATS captures errors
run_romp() {
    "$ROMP_SCRIPT" "$@" 2>&1
}

# Helper — a fake `claude` reporting the given version (the launch path only ever
# runs `claude --version`; the exec line itself lands in the tmux mock's log)
_stub_claude() {
    cat > "$MOCK_DIR/claude" <<STUB
#!/usr/bin/env bash
echo "$1 (Claude Code)"
STUB
    chmod +x "$MOCK_DIR/claude"
}

# ─── Launch tests ────────────────────────────────────────────────────

@test "bare romp with nothing running STARTS the stack — one command from any state" {
    # 2026-08-13: the front door heals instead of pointing at a second command. The
    # manager stub stands in for the real supervisor: it "boots a kernel" by minting
    # the serve token; the probe stub answers alive once that has happened.
    touch "$MOCK_LOG"
    cat > "$MOCK_DIR/mgr" << STUB
#!/usr/bin/env bash
echo "manager \$1" >> "\$MOCK_LOG"
mkdir -p "$XDG_STATE_HOME/romp"
echo tok123 > "$XDG_STATE_HOME/romp/serve-token"
STUB
    chmod +x "$MOCK_DIR/mgr"
    cat > "$MOCK_DIR/probe" << STUB
#!/usr/bin/env bash
[[ -f "$XDG_STATE_HOME/romp/serve-token" ]]
STUB
    chmod +x "$MOCK_DIR/probe"
    export ROMP_MANAGER_BIN="$MOCK_DIR/mgr"
    export ROMP_ALIVE_PROBE="$MOCK_DIR/probe"
    export ROMP_OPENER=""      # set-but-empty: print the URL, open nothing (the standing seam)
    run run_romp
    [ "$status" -eq 0 ]
    [[ "$output" == *"starting it"* ]]
    grep -q 'manager up' "$MOCK_LOG"
    [[ "$output" == *"?token=tok123"* ]]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]   # the front door never launches a session
}

@test "bare romp: a manager that yields no kernel fails loudly, never a session" {
    touch "$MOCK_LOG"
    cat > "$MOCK_DIR/mgr" << STUB
#!/usr/bin/env bash
echo "manager \$1" >> "\$MOCK_LOG"
STUB
    chmod +x "$MOCK_DIR/mgr"
    export ROMP_MANAGER_BIN="$MOCK_DIR/mgr"
    export ROMP_ALIVE_PROBE=false     # never alive
    export ROMP_START_TRIES=2         # keep the bounded wait short in tests
    run run_romp
    [ "$status" -eq 1 ]
    [[ "$output" == *"no kernel answered"* ]]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "new -t: terminal session named by the argument, claude exec'd with --name + --session-id" {
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -q 'tmux new-session -d -s myproject' "$MOCK_LOG"
    grep -q 'tmux set -t myproject @romp 1' "$MOCK_LOG"
    # The pill carries the session name, and a self-assigned --session-id lets
    # romp record name<->id up front (names map → resume picker). The command is
    # handed to the pane with respawn-pane (atomic), not typed with send-keys.
    grep -qE 'tmux respawn-pane -k -t myproject exec claude --name "myproject" --session-id [0-9a-f-]{36}' "$MOCK_LOG"
    grep -q 'tmux attach-session -t myproject' "$MOCK_LOG"
}

@test "new -t on a 2.1.224+ claude: inbound-accept setting + @romp-inbound-accept tag" {
    # The kernel's inbox-socket delivery leg fires only for launches that passed the
    # CLI's inbound-accept setting (an unverifiable sender's mail can otherwise be
    # held and silently expire); the tag records exactly those launches — one code
    # path writes both, so they can never disagree. Setup pins claude at 2.1.226.
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -qF -- "--settings '{\"crossSessionInbound\":\"accept\"}'" "$MOCK_LOG"
    grep -q 'tmux set -t myproject @romp-inbound-accept 1' "$MOCK_LOG"
}

@test "new -t on an old claude: no setting, no tag, one upgrade nudge" {
    _stub_claude "2.1.220"
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    ! grep -q -- '--settings' "$MOCK_LOG"
    ! grep -q -- '@romp-inbound-accept' "$MOCK_LOG"
    [[ "$output" == *"claude update"* ]]     # the informative floor line, not a failure
}

@test "launch hands the exec line to respawn-pane, never typed via send-keys (dropped-char bug)" {
    # Regression: a fresh shell flushes its tty input on startup, so send-keys'd
    # keys are dropped — the launch once started `ec claude …` (the "ex" eaten).
    # The exec command must reach the pane atomically (respawn-pane), so the exec
    # line must NEVER appear on a send-keys call.
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -q 'tmux respawn-pane -k -t myproject exec claude' "$MOCK_LOG"
    ! grep -q 'send-keys.*exec claude' "$MOCK_LOG"
}

@test "old tmux without copy-mode-position-style still launches claude (no set -e abort)" {
    # Regression: bin/romp sets the cosmetic copy-mode-position-style, added in tmux
    # 3.2. On an older tmux (e.g. a remote host on 3.0) that errors "invalid option",
    # which under `set -e` aborted session creation before the claude launch — the
    # pane was left at a bare shell. The cosmetic set must be guarded so the session
    # still starts. Simulate the old tmux by failing exactly that option.
    export MOCK_TMUX_FAIL_OPT="copy-mode-position-style"
    run run_romp new -t --detach myproject
    [ "$status" -eq 0 ]
    grep -qE 'tmux respawn-pane -k -t myproject exec claude' "$MOCK_LOG"
}

@test "append-system-prompt: omitted when no working-style prompt is installed" {
    # Default hermetic HOME has no romp-session-prompt.md, so the -f guard skips it.
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    ! grep -q -- '--append-system-prompt' "$MOCK_LOG"
}

@test "append-system-prompt: appended (deferred \$(cat ...)) when the prompt is installed" {
    mkdir -p "$HOME/.claude"
    printf 'Working style: be explicit.\n' > "$HOME/.claude/romp-session-prompt.md"
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    # The flag carries a deferred cat of the fixed path — the multi-line content
    # stays OUT of the exec line, so the launch shell expands it at exec time.
    grep -F -- "--append-system-prompt \"\$(cat $HOME/.claude/romp-session-prompt.md)\"" "$MOCK_LOG"
    # Still the same single exec line, handed to the pane via respawn-pane.
    grep -qE 'tmux respawn-pane -k -t myproject exec claude --name "myproject" --session-id [0-9a-f-]{36} --append-system-prompt .*' "$MOCK_LOG"
}

@test "append-system-prompt: also appended on the resume path" {
    mkdir -p "$HOME/.claude"
    printf 'Working style: be explicit.\n' > "$HOME/.claude/romp-session-prompt.md"
    run run_romp resume abc123-uuid
    [ "$status" -eq 0 ]
    grep -F -- "--append-system-prompt \"\$(cat $HOME/.claude/romp-session-prompt.md)\"" "$MOCK_LOG"
}

@test "provisioning pins status-format[0] alongside the session-scoped peers row" {
    # tmux gotcha (2026-06-12): a session-scoped status-format[1] shadows the
    # whole inherited array — without [0] pinned to the global composition the
    # main status row (status-left + windows + status-right) renders EMPTY.
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t myproject status-format\[0\] GLOBAL_ROW0' "$MOCK_LOG"
    grep -q 'tmux set -t myproject status-format\[1\]' "$MOCK_LOG"
}

@test "named session: romp new -t my-task → my-task" {
    run run_romp new -t my-task
    [ "$status" -eq 0 ]
    grep -q 'tmux new-session -d -s my-task' "$MOCK_LOG"
    grep -q 'tmux attach-session -t my-task' "$MOCK_LOG"
}

@test "session name sanitization: dots and colons replaced with dashes" {
    run run_romp new -t "my.task:v2"
    [ "$status" -eq 0 ]
    grep -q 'tmux new-session -d -s my-task-v2' "$MOCK_LOG"
    grep -q 'exec claude --name "my-task-v2"' "$MOCK_LOG"
}

@test "session name sanitization: shell metacharacters folded to dashes (no command injection)" {
    # A name/dir carrying $(), ;, or quotes must NOT survive into the launch
    # command the pane shell runs — every unsafe char becomes '-'. Regression for
    # the command-injection-via-session-name hole.
    run run_romp new -t 'pwn$(touch INJECTED);x"y'
    [ "$status" -eq 0 ]
    local line
    line="$(grep -F 'respawn-pane' "$MOCK_LOG" | grep -F 'exec claude')"
    [ -n "$line" ]
    # no shell metacharacters survive in the exec line
    ! grep -qE '[$();]' <<<"$line"
    # exactly the two quotes that wrap --name "<name>", no injected extras. The
    # fixed --settings tail romp itself appends carries its own JSON quotes — a
    # trusted constant, not name-derived — so strip it before counting.
    line="${line%%--settings*}"
    [ "$(grep -o '"' <<<"$line" | wc -l | tr -d ' ')" -eq 2 ]
}

@test "interrupt/escape key bindings route the session name through tmux #{q:} quoting" {
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -F 'bind -n C-c' "$MOCK_LOG"    | grep -qF 'romp-interrupt-reset #{q:session_name}'
    grep -F 'bind -n Escape' "$MOCK_LOG" | grep -qF 'romp-interrupt-reset #{q:session_name}'
    # the unquoted (injectable) form must be gone
    ! grep -qF 'romp-interrupt-reset #{session_name}' "$MOCK_LOG"
}

@test "resume: a session id with shell metacharacters is refused before any launch" {
    # resume_id is typed into `claude --resume <id>`; a non-alphanumeric id must
    # be rejected before a session is created.
    run run_romp resume 'abc;touch INJECTED' --name myproject --detach
    [ "$status" -ne 0 ]
    [[ "$output" == *"invalid session id"* ]]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "state dir is created private (0700)" {
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    local perms
    # GNU stat (-c) first, BSD/macOS stat (-f) as fallback. The reverse order
    # breaks on Linux, where `stat -f` means --file-system and mangles output.
    perms="$(stat -c '%a' "$XDG_STATE_HOME/romp" 2>/dev/null || stat -f '%Lp' "$XDG_STATE_HOME/romp")"
    [ "$perms" = "700" ]
}

# ─── Resume tests ────────────────────────────────────────────────────

@test "resume: bare -r with no resumable sessions is a no-op" {
    # bare -r opens the by-name picker; with an empty names map there is
    # nothing to offer — no session may be created as a side effect. The names
    # dir exists-but-empty (steady state on any machine that ran romp before);
    # a MISSING dir is the silent first-run path, exercised below.
    # NOTE bats/macOS gotcha: a false [[ ]] mid-test is SWALLOWED (only the
    # last command's status fails a test) — assert with simple commands
    # (grep, [ ]) so failures actually fire.
    mkdir -p "$XDG_STATE_HOME/romp/names"
    run run_romp resume
    [ "$status" -eq 0 ]
    grep -q "no resumable sessions" <<<"$output"
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "resume: --resume is a silent alias of resume (agent-facing text names it)" {
    mkdir -p "$XDG_STATE_HOME/romp/names"
    run run_romp resume
    [ "$status" -eq 0 ]
    grep -q "no resumable sessions" <<<"$output"

    run run_romp --resume
    [ "$status" -eq 0 ]
    grep -q "no resumable sessions" <<<"$output"
    [[ "$output" != *"retired"* ]]
}

@test "resume: first run ever (no names dir) exits silently, creating nothing" {
    touch "$MOCK_LOG"    # this path may make no tmux calls at all
    run run_romp resume
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "an unknown bare word is a loud error naming both readings, never a session" {
    # Round 3: commands are bare words, so a word that is not one gets exit 2
    # with the `romp new` fix spelled out — nothing silently becomes a session.
    touch "$MOCK_LOG"    # this path makes no tmux calls at all
    run run_romp foo
    [ "$status" -eq 2 ]
    [[ "$output" == *'unknown command "foo"'* ]]
    [[ "$output" == *"romp new foo"* ]]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "resume: the old-kernel revive shape (name --resume id --detach) still works, silently" {
    # A kernel on pre-round-3 code revives tmux sessions as `romp <name> --resume
    # <sid> --detach`; that exact shape must keep working (SILENTLY) until every
    # kernel restarts onto new code — its spawn path swallows stderr.
    run run_romp web --resume abc123-uuid --detach
    [ "$status" -eq 0 ]
    [[ "$output" != *"retired"* ]]
    grep -q 'tmux new-session -d -s web' "$MOCK_LOG"
    grep -q 'tmux respawn-pane -k -t web exec claude --resume abc123-uuid --name "web"' "$MOCK_LOG"
    ! grep -q 'tmux attach-session' "$MOCK_LOG"
}

@test "resume: explicit session id resumes that conversation" {
    run run_romp resume abc123-uuid
    [ "$status" -eq 0 ]
    grep -q 'tmux respawn-pane -k -t myproject exec claude --resume abc123-uuid --name "myproject"' "$MOCK_LOG"
}

@test "resume: name collision uniquifies instead of hijacking the session" {
    echo "myproject" > "$MOCK_TMUX_SESSIONS_FILE"

    run run_romp resume abc123-uuid
    [ "$status" -eq 0 ]
    ! grep -qE 'tmux attach-session -t myproject$' "$MOCK_LOG"
    grep -q 'tmux new-session -d -s myproject-2' "$MOCK_LOG"
    grep -q 'tmux respawn-pane -k -t myproject-2 exec claude --resume abc123-uuid --name "myproject-2"' "$MOCK_LOG"
}

# ─── Detach tests ────────────────────────────────────────────────────

@test "detach: new -t --detach creates the session but does not attach" {
    run run_romp new -t --detach myproject
    [ "$status" -eq 0 ]
    grep -q 'tmux new-session -d -s myproject' "$MOCK_LOG"
    grep -qE 'tmux respawn-pane -k -t myproject exec claude --name "myproject" --session-id [0-9a-f-]{36}' "$MOCK_LOG"
    ! grep -q 'tmux attach-session' "$MOCK_LOG"
    [[ "$output" == *"attach with: tmux attach -t myproject"* ]]
}

@test "detach: --resume + id + detach (the skill conversion path) still works as an alias" {
    run run_romp --resume sess-xyz --detach
    [ "$status" -eq 0 ]
    grep -q 'tmux new-session -d -s myproject' "$MOCK_LOG"
    grep -q 'tmux respawn-pane -k -t myproject exec claude --resume sess-xyz --name "myproject"' "$MOCK_LOG"
    ! grep -q 'tmux attach-session' "$MOCK_LOG"
    [[ "$output" == *"(detached)"* ]]
}

# ─── Misc ────────────────────────────────────────────────────────────

@test "unknown option shows error" {
    run run_romp -x
    [ "$status" -eq 2 ]
    [[ "$output" == *"unknown option: -x"* ]]
}

@test "old-kernel spawn shape (--detach <name>) still works, silently" {
    # A kernel on pre-round-3 code spawns dashboard tmux sessions as `romp
    # --detach <name>` with stderr swallowed — the shape must keep working.
    run run_romp --detach oldk
    [ "$status" -eq 0 ]
    [[ "$output" != *"retired"* ]]
    grep -q 'tmux new-session -d -s oldk' "$MOCK_LOG"
    ! grep -q 'tmux attach-session' "$MOCK_LOG"
}

@test "new: usage errors are loud — missing name, two names, dangling -d" {
    touch "$MOCK_LOG"    # these paths make no tmux calls at all
    run run_romp new
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp new"* ]]
    run run_romp new -t alpha beta
    [ "$status" -eq 2 ]
    run run_romp new -t -d
    [ "$status" -eq 2 ]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "existing session reattaches instead of creating new" {
    echo "myproject" > "$MOCK_TMUX_SESSIONS_FILE"

    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    ! grep -q 'tmux new-session' "$MOCK_LOG"
    grep -q 'tmux attach-session -t myproject' "$MOCK_LOG"
}

# ─── Identity-color tests ────────────────────────────────────────────

@test "color: first session gets the first palette color + a status dot" {
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t myproject @identity-bg #1EA1EB' "$MOCK_LOG"
    # The tab dot is seeded blue (ready) at launch; the status hook drives
    # it thereafter.
    grep -q 'tmux set -t myproject @romp-emoji 🔵' "$MOCK_LOG"
}

@test "color: second session gets a different color from the first" {
    echo "other" > "$MOCK_TMUX_SESSIONS_FILE"
    echo "other=#1EA1EB" > "$MOCK_TMUX_IDENTITY_FILE"

    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t myproject @identity-bg #54B204' "$MOCK_LOG"
}

@test "color: third session gets teal (colorblind-tuned order: blue, green, teal)" {
    # The 3rd slot is teal #4EA8A9, the more colorblind-friendly of teal/purple against
    # the blue+green pair (the user 2026-06-12) — pin both earlier colors as taken.
    printf '%s\n' "s1" "s2" > "$MOCK_TMUX_SESSIONS_FILE"
    printf '%s\n' "s1=#1EA1EB" "s2=#54B204" > "$MOCK_TMUX_IDENTITY_FILE"

    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t myproject @identity-bg #4EA8A9' "$MOCK_LOG"
}

@test "color: a kernel-written palette-colors mirror overrides the built-in set" {
    # The identity palette is selectable (2026-07-12): the kernel mirrors the ACTIVE set to
    # STATE/palette-colors (bg<TAB>fg per line) and the launcher assigns from it; the hardcoded
    # arrays are only the fallback for a machine whose kernel never booted.
    mkdir -p "$XDG_STATE_HOME/romp"
    printf '#AA0000\twhite\n#00BB00\tblack\n' > "$XDG_STATE_HOME/romp/palette-colors"

    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t myproject @identity-bg #AA0000' "$MOCK_LOG"
    grep -q 'tmux set -t myproject @identity-fg white' "$MOCK_LOG"
}

@test "color: all colors taken falls back to a hash pick" {
    local palette=("#1EA1EB" "#54B204" "#4EA8A9" "#DD42FF" "#E87221" "#98998A" "#F85B5A" "#F9D849" "#9088F0")
    > "$MOCK_TMUX_SESSIONS_FILE"
    > "$MOCK_TMUX_IDENTITY_FILE"
    for i in "${!palette[@]}"; do
        echo "sess${i}" >> "$MOCK_TMUX_SESSIONS_FILE"
        echo "sess${i}=${palette[$i]}" >> "$MOCK_TMUX_IDENTITY_FILE"
    done

    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t myproject @identity-bg #' "$MOCK_LOG"
}

# ─── No attach/rename subcommands (use tmux a / tmux rename) ─────────

@test "'a', 'attach', 'rename' are unknown commands, never sessions" {
    # There is no attach/rename command (plain tmux does both), and round 3 made
    # every non-command bare word a loud error pointing at `romp new`.
    for word in a attach rename; do
        : > "$MOCK_LOG"
        run run_romp "$word"
        [ "$status" -eq 2 ]
        [[ "$output" == *"romp new ${word}"* ]]
        [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
    done
}

@test "retired human spellings fail loudly naming today's word, and start nothing" {
    # Rounds 1-2 spellings (short view flags, dashed manager commands). The
    # agent-facing aliases (--mail/--url/--send/--interrupt/--end/--resume,
    # --version, first-arg --detach) are exercised elsewhere and stay SILENT.
    for flag in -l --launch -d -f -j -r --on --refresh --status --update --checkin --checkout --default-dir --debug; do
        : > "$MOCK_LOG"
        run run_romp "$flag"
        [ "$status" -eq 2 ]
        [[ "$output" == *"retired"* ]]
        # every hint names today's spelling, or says the command is gone (the terminal TUIs)
        [[ "$output" == *"is now"* || "$output" == *"just: romp"* || "$output" == *"is gone"* ]]
        [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
    done
    # spot-check: a RENAMED command names its new spelling, a DELETED one says so
    run run_romp -d
    [[ "$output" == *"is gone"* ]]
    [[ "$output" == *"romp"* ]]
    run run_romp --refresh
    [[ "$output" == *"romp refresh"* ]]
}

# ─── kernel-manager commands (up / refresh / status) ─────────────────

@test "manager commands (up/refresh/status) dispatch to romp-manager with the right sub-command" {
    cat > "$MOCK_DIR/romp-manager" << 'MOCK'
#!/usr/bin/env bash
echo "romp-manager called: $*" >> "$MOCK_LOG"
MOCK
    chmod +x "$MOCK_DIR/romp-manager"
    export ROMP_MANAGER_BIN="$MOCK_DIR/romp-manager"
    # --refresh also bounces the postal bus now; mock it so the test never touches the real bus
    cat > "$MOCK_DIR/romp-postal-service" << 'MOCK'
#!/usr/bin/env bash
echo "romp-postal-service called: $*" >> "$MOCK_LOG"
MOCK
    chmod +x "$MOCK_DIR/romp-postal-service"
    export ROMP_POSTAL_BIN="$MOCK_DIR/romp-postal-service"

    run run_romp up              # `romp up` is PURELY start-the-manager
    [ "$status" -eq 0 ]
    grep -q 'romp-manager called: up' "$MOCK_LOG"
    ! grep -q 'romp-postal-service called' "$MOCK_LOG"   # up does not touch the bus

    : > "$MOCK_LOG"
    run run_romp refresh         # restart EVERYTHING: the bus AND all kernels
    [ "$status" -eq 0 ]
    grep -q 'romp-postal-service called: restart' "$MOCK_LOG"   # bus bounced first
    grep -q 'romp-manager called: restart-all' "$MOCK_LOG"      # then the kernels

    : > "$MOCK_LOG"
    run run_romp status
    [ "$status" -eq 0 ]
    grep -q 'romp-manager called: status' "$MOCK_LOG"
    ! grep -q 'romp-postal-service called' "$MOCK_LOG"   # status does not touch the bus
}

@test "refresh appends a caller-attribution line to restart-audit.jsonl before restarting" {
    # 2026-07-16: three staged-demo teardowns traced back to untraceable fleet-wide refreshes —
    # kernel-downtime.jsonl records only {start,end}, and agents (Bash tool) leave no shell history.
    # The audit line answers WHO (sid -> session name, parent argv, tty) before the restart runs.
    cat > "$MOCK_DIR/romp-manager" << 'MOCK'
#!/usr/bin/env bash
echo "romp-manager called: $*" >> "$MOCK_LOG"
MOCK
    chmod +x "$MOCK_DIR/romp-manager"
    export ROMP_MANAGER_BIN="$MOCK_DIR/romp-manager"
    cat > "$MOCK_DIR/romp-postal-service" << 'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
    chmod +x "$MOCK_DIR/romp-postal-service"
    export ROMP_POSTAL_BIN="$MOCK_DIR/romp-postal-service"

    # an agent-shaped caller: CLAUDE_CODE_SESSION_ID set, resolvable through the names map
    mkdir -p "$XDG_STATE_HOME/romp/names"
    printf 'demo_agent\t/tmp\t#000000\twhite\n' \
        > "$XDG_STATE_HOME/romp/names/11111111-2222-3333-4444-555555555555"
    export CLAUDE_CODE_SESSION_ID="11111111-2222-3333-4444-555555555555"

    run run_romp refresh
    [ "$status" -eq 0 ]
    audit="$XDG_STATE_HOME/romp/restart-audit.jsonl"
    [ -f "$audit" ]
    grep -q '"sid": "11111111-2222-3333-4444-555555555555"' "$audit"
    grep -q '"name": "demo_agent"' "$audit"          # sid resolved to the session's NAME
    grep -q '"parent":' "$audit"                     # the caller's parent argv rides along
    grep -q 'romp-manager called: restart-all' "$MOCK_LOG"   # ...and the restart still ran
}

@test "refresh survives an unwritable audit dir (attribution is best-effort, never blocks)" {
    cat > "$MOCK_DIR/romp-manager" << 'MOCK'
#!/usr/bin/env bash
echo "romp-manager called: $*" >> "$MOCK_LOG"
MOCK
    chmod +x "$MOCK_DIR/romp-manager"
    export ROMP_MANAGER_BIN="$MOCK_DIR/romp-manager"
    cat > "$MOCK_DIR/romp-postal-service" << 'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
    chmod +x "$MOCK_DIR/romp-postal-service"
    export ROMP_POSTAL_BIN="$MOCK_DIR/romp-postal-service"

    mkdir -p "$XDG_STATE_HOME/romp"
    chmod 500 "$XDG_STATE_HOME/romp"                 # audit append will fail
    run run_romp refresh
    chmod 700 "$XDG_STATE_HOME/romp"                 # restore for teardown
    [ "$status" -eq 0 ]
    grep -q 'romp-manager called: restart-all' "$MOCK_LOG"   # the restart went through regardless
}

@test "romp up does not forward trailing words to the manager (romp refresh is its own command)" {
    cat > "$MOCK_DIR/romp-manager" << 'MOCK'
#!/usr/bin/env bash
echo "romp-manager called: $*" >> "$MOCK_LOG"
MOCK
    chmod +x "$MOCK_DIR/romp-manager"
    export ROMP_MANAGER_BIN="$MOCK_DIR/romp-manager"
    run run_romp up restart main
    [ "$status" -eq 0 ]
    grep -q 'romp-manager called: up' "$MOCK_LOG"   # starts the manager; trailing words are NOT forwarded
    ! grep -q 'restart' "$MOCK_LOG"
}

@test "'on', 'serve', 'down', 'launch', 'open' are unknown commands: loud exit 2, no session" {
    # These words never became round-3 commands (up replaced on; serve was removed;
    # there is no down; the dashboard is bare romp). Each must fail naming the fix.
    for word in on serve down launch open; do
        : > "$MOCK_LOG"
        run run_romp "$word"
        [ "$status" -eq 2 ]
        [[ "$output" == *"romp new ${word}"* ]]
        [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
    done
}

@test "romp-manager: control verbs error cleanly when no manager is running" {
    command -v node >/dev/null 2>&1 || skip "node not available"
    local mgr; mgr="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-manager"
    # port nothing is listening on → the control client must fail fast with a clear message
    run env ROMP_MANAGER_PORT=7531 node "$mgr" status
    [ "$status" -eq 1 ]
    [[ "$output" == *"not running"* ]]
}

@test "romp-manager: /ensure spawns an additional kernel on demand, idempotently" {
    command -v node >/dev/null 2>&1 || skip "node not available"
    command -v curl >/dev/null 2>&1 || skip "curl not available"
    local mgr; mgr="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-manager"

    # Fake kernel launcher: ignore --port and just stay alive, so the manager keeps it "running"
    # without any real port binding (the test asserts on the manager's bookkeeping, not a live kernel).
    local fake="$TEST_DIR/fake-serve"
    printf '#!/usr/bin/env bash\nexec sleep 30\n' > "$fake"
    chmod +x "$fake"

    local cport=7541 mport=7542 kport=7543
    # Launch the manager in the background; it auto-spawns 'main' on mport via the fake launcher.
    ROMP_MANAGER_PORT=$cport ROMP_SERVE_PORT=$mport ROMP_SERVE_BIN="$fake" \
        node "$mgr" up >/dev/null 2>&1 &
    MGR_PID=$!   # teardown reaps this

    # Wait for the control endpoint to come up (≤ ~3s)
    local i
    for i in $(seq 1 30); do
        curl -fsS "http://127.0.0.1:$cport/status" >/dev/null 2>&1 && break
        sleep 0.1
    done

    # Ensure a second kernel on kport → freshly spawned
    run curl -fsS -X POST "http://127.0.0.1:$cport/ensure?port=$kport"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"spawned":true'* ]]
    [[ "$output" == *"\"port\":$kport"* ]]
    [[ "$output" == *"\"id\":\"k$kport\""* ]]

    # Ensuring the same port again is idempotent — no second spawn
    run curl -fsS -X POST "http://127.0.0.1:$cport/ensure?port=$kport"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"spawned":false'* ]]

    # /status now lists both the default 'main' kernel and the on-demand one
    run curl -fsS "http://127.0.0.1:$cport/status"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"id":"main"'* ]]
    [[ "$output" == *"\"id\":\"k$kport\""* ]]

    # Graceful shutdown (teardown also reaps via MGR_PID as a backstop)
    curl -fsS -X POST "http://127.0.0.1:$cport/stop" >/dev/null 2>&1 || true
}

@test "romp-manager: /restart-all kicks every kernel in the registry (romp refresh)" {
    command -v node >/dev/null 2>&1 || skip "node not available"
    command -v curl >/dev/null 2>&1 || skip "curl not available"
    local mgr; mgr="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-manager"
    local fake="$TEST_DIR/fake-serve"
    printf '#!/usr/bin/env bash\nexec sleep 30\n' > "$fake"
    chmod +x "$fake"

    local cport=7551 mport=7552 kport=7553
    ROMP_MANAGER_PORT=$cport ROMP_SERVE_PORT=$mport ROMP_SERVE_BIN="$fake" \
        node "$mgr" up >/dev/null 2>&1 &
    MGR_PID=$!
    local i
    for i in $(seq 1 30); do curl -fsS "http://127.0.0.1:$cport/status" >/dev/null 2>&1 && break; sleep 0.1; done
    curl -fsS -X POST "http://127.0.0.1:$cport/ensure?port=$kport" >/dev/null   # a 2nd kernel in the registry

    run curl -fsS -X POST "http://127.0.0.1:$cport/restart-all"
    [ "$status" -eq 0 ]
    # the response lists EVERY kernel it kicked — the default 'main' AND the on-demand one (not just main)
    [[ "$output" == *'"restarted"'* ]]
    [[ "$output" == *'main'* ]]
    [[ "$output" == *"k$kport"* ]]

    curl -fsS -X POST "http://127.0.0.1:$cport/stop" >/dev/null 2>&1 || true
}

# ─── Help (-h / --help) ──────────────────────────────────────────────

@test "-h prints usage and starts no session" {
    run run_romp -h
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage:"* ]]
    [[ "$output" == *"romp new"* ]]
    ! grep -q 'tmux new-session' "$MOCK_LOG"
}

@test "help, -h and --help all print usage" {
    touch "$MOCK_LOG"    # help makes no tmux calls at all
    run run_romp --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage:"* ]]
    run run_romp help
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage:"* ]]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "mail dispatches to romp-postal with its args (--mail is its silent alias)" {
    cat > "$MOCK_DIR/romp-postal" << 'MOCK'
#!/usr/bin/env bash
echo "romp-postal called: $*" >> "$MOCK_LOG"
MOCK
    chmod +x "$MOCK_DIR/romp-postal"
    # same PATH-prepend shadowing as the dashboard test — without the seam this
    # exec'd the REAL romp-postal (a live mail send) instead of the mock
    export ROMP_POSTAL_BIN="$MOCK_DIR/romp-postal"

    run run_romp mail send beta "hello"
    [ "$status" -eq 0 ]
    grep -q 'romp-postal called: send beta hello' "$MOCK_LOG"

    : > "$MOCK_LOG"
    run run_romp --mail send beta "hello"    # delivered postal footers name --mail forever
    [ "$status" -eq 0 ]
    [[ "$output" != *"retired"* ]]
    grep -q 'romp-postal called: send beta hello' "$MOCK_LOG"
}

# _romp_resume_rows builds the resume-picker rows in ONE python pass: it walks the
# projects tree once into a sid->transcript index, reads each session's name file
# + cached gloss (archive headline, else latest caption), and emits FS-delimited
# rows newest-first. These extract JUST the function (never source the whole
# script — that would re-run its top-level dispatch + reset ROMP_*_DIR) and point
# the dirs at fixtures. FS is \x1f; fields are mtime|sid|name|dir|rgb|kind|text.
_resume_rows_fn() {   # writes the extracted function to $1
    sed -n '/^_romp_resume_rows()/,/^}/p' "$ROMP_SCRIPT" > "$1"
}

@test "resume rows: archive headline, caption fallback, ordering, live-exclusion" {
    local ndir="$TEST_DIR/names" adir="$TEST_DIR/archive" cdir="$TEST_DIR/captions"
    local pdir="$TEST_DIR/projects" fn="$TEST_DIR/_rows.sh"
    mkdir -p "$ndir" "$adir" "$cdir" "$pdir/proj-a"
    _resume_rows_fn "$fn"

    # three resumable sessions + one LIVE (must be excluded)
    printf 'arch-sess\t/tmp/b\t#aabbcc\t#000000\n' > "$ndir/sid-arch"
    printf 'cap-sess\t/tmp/c\t#ddeeff\t#000000\n'  > "$ndir/sid-cap"
    printf 'live-sess\t/tmp/a\t#112233\t#ffffff\n' > "$ndir/sid-live"
    : > "$pdir/proj-a/sid-arch.jsonl"
    : > "$pdir/proj-a/sid-cap.jsonl"
    : > "$pdir/proj-a/sid-live.jsonl"
    # cap-sess transcript OLDER than arch-sess -> arch-sess sorts first
    touch -t 202606160000 "$pdir/proj-a/sid-cap.jsonl"
    touch -t 202606161200 "$pdir/proj-a/sid-arch.jsonl"
    printf '{"headline":"Synthetic archive headline"}\n' > "$adir/sid-arch.json"
    printf '{"caption":"older step"}\n{"caption":"newest caption step"}\n' > "$cdir/sid-cap.jsonl"

    run env ROMP_NAMES_DIR="$ndir" ROMP_ARCHIVE_DIR="$adir" ROMP_CAPTIONS_DIR="$cdir" \
        ROMP_PROJECTS_DIR="$pdir" \
        bash -c 'source "$1"; _romp_resume_rows "$2" "$3"' _ "$fn" $'sid-live' $'\x1f'
    [ "$status" -eq 0 ]
    # live session excluded
    [[ "$output" != *"live-sess"* ]]
    # newest first: arch row before cap row
    local first; first="$(printf '%s\n' "$output" | head -1)"
    [[ "$first" == *"arch-sess"* ]]
    # archive headline wins for arch-sess; caption fallback for cap-sess (last non-empty)
    [[ "$output" == *"Synthetic archive headline"* ]]
    [[ "$output" == *"newest caption step"* ]]
    [[ "$output" != *"older step"* ]]
    # rgb derived from the bg hex (#aabbcc -> 170;187;204)
    [[ "$output" == *$'\x1f'"170;187;204"$'\x1f'* ]]
}

@test "resume rows: stale name file (transcript gone) is pruned" {
    local ndir="$TEST_DIR/names" pdir="$TEST_DIR/projects" fn="$TEST_DIR/_rows.sh"
    mkdir -p "$ndir" "$pdir/proj-a" "$TEST_DIR/archive" "$TEST_DIR/captions"
    _resume_rows_fn "$fn"
    printf 'has-tx\t/tmp/x\t\t\n'   > "$ndir/sid-has"
    printf 'stale\t/tmp/y\t\t\n'    > "$ndir/sid-stale"
    : > "$pdir/proj-a/sid-has.jsonl"          # only sid-has has a transcript
    run env ROMP_NAMES_DIR="$ndir" ROMP_ARCHIVE_DIR="$TEST_DIR/archive" \
        ROMP_CAPTIONS_DIR="$TEST_DIR/captions" ROMP_PROJECTS_DIR="$pdir" \
        bash -c 'source "$1"; _romp_resume_rows "$2" "$3"' _ "$fn" '' $'\x1f'
    [ "$status" -eq 0 ]
    [ -f "$ndir/sid-has" ]            # kept
    [ ! -f "$ndir/sid-stale" ]        # pruned
}

@test "resume rows: an EMPTY/unreadable projects index never prunes the cache" {
    # Regression guard: if the projects tree is missing, "transcript gone" is
    # unverifiable, so we must NOT delete any name files (an env mismatch once
    # wiped the whole cache this way).
    local ndir="$TEST_DIR/names" fn="$TEST_DIR/_rows.sh"
    mkdir -p "$ndir" "$TEST_DIR/archive" "$TEST_DIR/captions"
    _resume_rows_fn "$fn"
    printf 'a\t/tmp/a\t\t\n' > "$ndir/sid-a"
    printf 'b\t/tmp/b\t\t\n' > "$ndir/sid-b"
    run env ROMP_NAMES_DIR="$ndir" ROMP_ARCHIVE_DIR="$TEST_DIR/archive" \
        ROMP_CAPTIONS_DIR="$TEST_DIR/captions" ROMP_PROJECTS_DIR="$TEST_DIR/nonexistent" \
        bash -c 'source "$1"; _romp_resume_rows "$2" "$3"' _ "$fn" '' $'\x1f'
    [ "$status" -eq 0 ]
    [ -f "$ndir/sid-a" ]             # both survive — nothing pruned without an index
    [ -f "$ndir/sid-b" ]
    [ -z "$output" ]                 # and no rows (no transcripts to show)
}

@test "help -h reflects which commands are PRESENT (presence-checked, no drift)" {
    # Run a copy of romp with only SOME backing romp-* binaries reachable: present commands show, absent
    # ones are hidden, built-ins always show — so the help can't drift from what's installed (the user 2026-06-16).
    local td; td="$TEST_DIR/help"; mkdir -p "$td"
    cp "$ROMP_SCRIPT" "$td/romp"
    local b; for b in romp-manager romp-version; do printf '#!/bin/sh\nexit 0\n' > "$td/$b"; chmod +x "$td/$b"; done
    run env PATH="$td:/usr/bin:/bin:/opt/homebrew/bin" bash "$td/romp" -h
    [ "$status" -eq 0 ]
    # built-ins (no backing binary) always shown
    [[ "$output" == *"romp new"* ]]
    [[ "$output" == *"romp resume"* ]]
    # `romp serve` was removed (tailnet reach = tailscale serve to loopback) — must not resurface
    [[ "$output" != *"romp serve"* ]]
    # present backing → shown
    [[ "$output" == *"romp up"* ]]
    [[ "$output" == *"romp status"* ]]
    [[ "$output" == *"romp version"* ]]
    # absent backing → hidden
    [[ "$output" != *"romp mail"* ]]
    # the retired terminal TUIs must not come back as help rows
    [[ "$output" != *"romp monitor"* ]]
    [[ "$output" != *"romp feed"* ]]
    [[ "$output" != *"romp judges"* ]]
}

# ─── ROMPHOME — never launch a session in $HOME ──────────────────────
# $HOME is the one cwd whose direct children include the macOS TCC-protected
# Downloads/Desktop/Documents; indexing them trips spurious OS file-access
# prompts. A $HOME launch is redirected to ROMPHOME instead.

@test "ROMPHOME: a launch from \$HOME is redirected there, not created in \$HOME" {
    export ROMPHOME="$TEST_DIR/romphome"
    mkdir -p "$ROMPHOME"
    local expect; expect="$(cd "$ROMPHOME" && pwd -P)"
    local home_real; home_real="$(cd "$HOME" && pwd -P)"
    cd "$HOME"
    run run_romp new -t box
    [ "$status" -eq 0 ]
    grep -qF "tmux new-session -d -s box -c $expect" "$MOCK_LOG"
    # the session must NOT be rooted at $HOME
    ! grep -qF "tmux new-session -d -s box -c $home_real" "$MOCK_LOG"
    # and the redirect is announced to the user
    [[ "$output" == *"not launching in \$HOME"* ]]
}

@test "ROMPHOME: a name-less resume from \$HOME is named after ROMPHOME, not \$HOME" {
    # Regression: basename(\$HOME) is the username — a privacy leak as a session
    # name. `romp new` requires a name now, so the folder-name default only fires
    # on an explicit-id resume without --name; the name must come from the
    # resolved (redirected) dir.
    export ROMPHOME="$TEST_DIR/scratchpad"
    mkdir -p "$ROMPHOME"
    cd "$HOME"
    run run_romp resume abc123-uuid
    [ "$status" -eq 0 ]
    grep -q 'tmux new-session -d -s scratchpad' "$MOCK_LOG"
    ! grep -qE 'tmux new-session -d -s home( |$| -)' "$MOCK_LOG"
}

@test "ROMPHOME: a launch from a normal project dir is unaffected" {
    export ROMPHOME="$TEST_DIR/romphome"
    mkdir -p "$ROMPHOME"
    # setup() already cd'd into $WORK_DIR, a normal project dir
    local expect; expect="$(cd "$WORK_DIR" && pwd -P)"
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -qF "tmux new-session -d -s myproject -c $expect" "$MOCK_LOG"
    [[ "$output" != *"not launching in \$HOME"* ]]
}

@test "new: -d launches in the given directory, not the cwd" {
    local other="$TEST_DIR/elsewhere"
    mkdir -p "$other"
    local expect; expect="$(cd "$other" && pwd -P)"
    run run_romp new -t -d "$other" side
    [ "$status" -eq 0 ]
    grep -qF "tmux new-session -d -s side -c $expect" "$MOCK_LOG"
}

@test "romp checkin/checkout: usage without a host, loud failure with no kernel" {
    run "$ROMP_SCRIPT" checkin
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp checkin <host>"* ]]
    run "$ROMP_SCRIPT" checkout
    [ "$status" -eq 2 ]
    # port 1 refuses instantly → the CLI must fail LOUDLY, never pretend the checkout happened
    ROMP_KERNEL_PORT=1 run "$ROMP_SCRIPT" checkout somehost
    [ "$status" -eq 1 ]
    [[ "$output" == *"kernel not reachable"* ]]
}

# ─── romp new (SDK default) ──────────────────────────────────────────

@test "new (no -t): no kernel token → loud error naming both fixes, nothing launched" {
    touch "$MOCK_LOG"    # this path makes no tmux calls at all
    run run_romp new api
    [ "$status" -eq 1 ]
    [[ "$output" == *"kernel isn't running"* ]]
    [[ "$output" == *"romp new -t api"* ]]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "new (no -t): POSTs the kernel /new with backend sdk, and starts no tmux session" {
    command -v python3 >/dev/null 2>&1 || skip "python3 not available"
    touch "$MOCK_LOG"    # this path makes no tmux calls at all
    mkdir -p "$XDG_STATE_HOME/romp"
    printf 'tok-test' > "$XDG_STATE_HOME/romp/serve-token"
    # One-shot fake kernel: accept a single POST, log it, answer ok:true. Ephemeral
    # port, announced via a file WRITTEN AFTER BIND — the same pattern as
    # romp-headless.bats. The `until` below waits on the listening EVENT; the
    # `sleep 0.3` this replaces was a guessed duration that macOS CI runners
    # reliably lost (two release-gate failures), while every faster machine won it.
    python3 - "$TEST_DIR/port" "$TEST_DIR/req.log" <<'PY' &
import sys, json
from http.server import BaseHTTPRequestHandler, HTTPServer
portfile, log = sys.argv[1], sys.argv[2]
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        with open(log, "w") as f:
            json.dump({"path": self.path, "token": self.headers.get("X-Romp-Token"),
                       "body": json.loads(body or b"{}")}, f)
        out = json.dumps({"ok": True, "id": "11111111-2222-3333-4444-555555555555"}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out))); self.end_headers()
        self.wfile.write(out)
    def log_message(self, *a): pass
srv = HTTPServer(("127.0.0.1", 0), H)
with open(portfile, "w") as f:
    f.write(str(srv.server_address[1]))
srv.handle_request()
PY
    local srv=$!
    until [ -s "$TEST_DIR/port" ]; do sleep 0.05; done
    ROMP_KERNEL_PORT="$(cat "$TEST_DIR/port")" run run_romp new api
    kill "$srv" 2>/dev/null || true
    [ "$status" -eq 0 ]
    [[ "$output" == *"started \"api\""* ]]
    [[ "$output" == *"dashboard"* ]]
    [ -f "$TEST_DIR/req.log" ]
    grep -q '"path": "/new"' "$TEST_DIR/req.log"
    grep -q '"token": "tok-test"' "$TEST_DIR/req.log"
    grep -q '"name": "api"' "$TEST_DIR/req.log"
    grep -q '"backend": "sdk"' "$TEST_DIR/req.log"
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

# ── per-session launch defaults: model / effort / permission mode ────────────────────────────
# Every romp session takes one answer for these (the user 2026-08-13), from
# ~/.config/romp/session-defaults — a FILE because the launchd-rooted kernel that spawns
# sessions never sees a shell env var. A per-spawn --model/--effort (the hive tray's bean drop)
# outranks the file; a resume keeps the conversation's own model/effort.

_write_session_defaults() {
    mkdir -p "$HOME/.config/romp"
    printf '%s\n' "$@" > "$HOME/.config/romp/session-defaults"
}

@test "session-defaults: model, effort and permission mode reach the launch line" {
    _write_session_defaults "MODEL=fable" "EFFORT=max" "PERM_MODE=auto"
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    local line; line="$(grep -F 'respawn-pane' "$MOCK_LOG" | grep -F 'exec claude')"
    [[ "$line" == *"--model fable"* ]]
    [[ "$line" == *"--effort max"* ]]
    [[ "$line" == *"--permission-mode auto"* ]]
}

@test "session-defaults: absent file leaves every flag off (the CLI's own settings stand)" {
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    local line; line="$(grep -F 'respawn-pane' "$MOCK_LOG" | grep -F 'exec claude')"
    [[ "$line" != *"--model"* ]]
    [[ "$line" != *"--effort"* ]]
    [[ "$line" != *"--permission-mode"* ]]
}

@test "session-defaults: a junk value is dropped rather than passed to the CLI" {
    _write_session_defaults "MODEL=fable; touch INJECTED" "EFFORT=turbo" "PERM_MODE=yolo"
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    local line; line="$(grep -F 'respawn-pane' "$MOCK_LOG" | grep -F 'exec claude')"
    [[ "$line" != *"--model"* ]]
    [[ "$line" != *"--effort"* ]]
    [[ "$line" != *"--permission-mode"* ]]
    [ ! -e INJECTED ]
}

@test "new --model/--effort (a tray bean drop) outrank the file for that session" {
    _write_session_defaults "MODEL=fable" "EFFORT=max"
    run run_romp new -t --model opus --effort high myproject
    [ "$status" -eq 0 ]
    local line; line="$(grep -F 'respawn-pane' "$MOCK_LOG" | grep -F 'exec claude')"
    [[ "$line" == *"--model opus"* ]]
    [[ "$line" == *"--effort high"* ]]
    [[ "$line" != *"--model fable"* ]]
}

@test "a resume keeps the conversation's own model/effort, but still takes the mode" {
    _write_session_defaults "MODEL=fable" "EFFORT=max" "PERM_MODE=auto"
    run run_romp resume abc123-uuid --name web --detach
    [ "$status" -eq 0 ]
    local line; line="$(grep -F 'respawn-pane' "$MOCK_LOG" | grep -F 'exec claude')"
    [[ "$line" == *"--resume abc123-uuid"* ]]
    [[ "$line" != *"--model"* ]]
    [[ "$line" != *"--effort"* ]]
    [[ "$line" == *"--permission-mode auto"* ]]
}

# ── romp default-host: which machine new sessions land on ───────────────────────────────────
@test "default-host: round-trips, prints, and clears" {
    run run_romp default-host
    [ "$status" -eq 0 ]
    [[ "$output" == *"unset"* ]]
    run run_romp default-host TESTHOST
    [ "$status" -eq 0 ]
    [ "$(cat "$HOME/.config/romp/default-host")" = "TESTHOST" ]
    run run_romp default-host
    [[ "$output" == *"TESTHOST"* ]]
    run run_romp default-host ""
    [ "$status" -eq 0 ]
    [ ! -f "$HOME/.config/romp/default-host" ]
}

@test "default-host: refuses an ssh-option-shaped name" {
    run run_romp default-host "-oProxyCommand=touch /tmp/pwned"
    [ "$status" -ne 0 ]
    [[ "$output" == *"not a host name"* ]]
    [ ! -f "$HOME/.config/romp/default-host" ]
}
