#!/usr/bin/env bats

# Exercises the romp-postal-service program end to end: a real bus (own port per test),
# CLI client ops, the loop guard, the stdio MCP server, and autostop. tmux is
# mocked so no real sessions are needed.

POSTAL="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-postal-service"

setup() {
    TEST_DIR="$(mktemp -d)"
    export XDG_STATE_HOME="$TEST_DIR/state"
    export ROMP_POSTAL_PORT=$((47200 + ${BATS_TEST_NUMBER:-0}))
    export ROMP_POSTAL_POLL=1
    export ROMP_POSTAL_IDLE_GRACE=2
    export ROMP_POSTAL_HEARTBEAT_TTL=2
    export HOME="$TEST_DIR/home"; mkdir -p "$HOME"   # sandbox the client-only marker
    unset SSH_CONNECTION SSH_TTY                      # default: not a remote machine

    # The bus no longer shells tmux for session ops. Identity is the CLAUDE_CODE_SESSION_ID env (the harness
    # sets it for every session) resolved to a name via the names registry; the session list comes from the
    # kernel's GET /sessions (ROMP_SESSIONS_FILE seam, from a "name|uuid" fixture via mksessions). Delivery +
    # status-bar chrome go through the kernel too — absent here, they no-op (the maildir-drain backstop covers
    # delivery). A hermetic stub tmux (always-empty) keeps any residual tmux() call from touching real tmux.
    MOCK="$TEST_DIR/mock"; mkdir -p "$MOCK"
    export SESS="$TEST_DIR/sessions.txt"
    printf 'alpha|uuid-a\nbeta|uuid-b\n' > "$SESS"
    export ROMP_SESSIONS_FILE="$TEST_DIR/sessions.json"
    mksessions                                       # SESS → the kernel's unified-rows JSON the seam reads
    mkdir -p "$XDG_STATE_HOME/romp/names"            # names registry: my_id (env) → my_name + identity colours
    printf 'alpha\t%s\t#111111\t#ffffff\n' "$HOME" > "$XDG_STATE_HOME/romp/names/uuid-a"
    printf 'beta\t%s\t#222222\t#ffffff\n' "$HOME" > "$XDG_STATE_HOME/romp/names/uuid-b"
    export CLAUDE_CODE_SESSION_ID=uuid-a             # "this session" = alpha by default (tests acting as beta override it)
    printf '#!/usr/bin/env bash\nexit 0\n' > "$MOCK/tmux"   # hermetic stub: every tmux call → "" (no real tmux)
    chmod +x "$MOCK/tmux"
    export PATH="$MOCK:$PATH"

    "$POSTAL" serve >/dev/null 2>&1 &
    BUS_PID=$!
    # Readiness is load-bearing: every test assumes the bus is up, and proceeding without it surfaces as a
    # confusing DOWNSTREAM failure (a 2026-08-14 CI runner lost this race: "remote --force" probed a port
    # the bus hadn't bound yet and failed three asserts later, reading like a tunnel bug). Fail HERE,
    # naming the real problem. A dead bus process is the early exit; the doubled bound is only a backstop.
    local up=0 _
    for _ in $(seq 1 100); do
        curl -s "127.0.0.1:$ROMP_POSTAL_PORT/ping" >/dev/null 2>&1 && { up=1; break; }
        kill -0 "$BUS_PID" 2>/dev/null || break
        sleep 0.1
    done
    if [ "$up" != 1 ]; then
        local alive=no; kill -0 "$BUS_PID" 2>/dev/null && alive=yes
        echo "# setup: postal bus never came up on 127.0.0.1:$ROMP_POSTAL_PORT (pid $BUS_PID, alive=$alive)" >&2
        return 1
    fi
}

teardown() {
    kill "$BUS_PID" 2>/dev/null
    rm -rf "$TEST_DIR"
}

mb() { echo "$XDG_STATE_HOME/romp/postal/mail/$1"; }
cnt() { ls -1 "$1" 2>/dev/null | wc -l | tr -d ' '; }
# convert the "name|uuid" SESS fixture into the kernel's unified GET /sessions JSON rows (what the
# ROMP_SESSIONS_FILE seam serves to local_agents). Call after any change to $SESS.
mksessions() {
    { printf '['
      local first=1 n u
      while IFS='|' read -r n u; do
          [ -n "$n" ] || continue
          [ "$first" = 1 ] || printf ','
          first=0
          printf '{"id":"%s","name":"%s","state":"working","dir":"","bg":"","fg":"","working":"","backend":"tmux"}' "$u" "$n"
      done < "$SESS"
      printf ']'
    } > "$ROMP_SESSIONS_FILE"
}
# toggle POSTAL ISOLATION on for a session uuid (writes the kernel's session-flags.json that the bus reads)
iso() { mkdir -p "$XDG_STATE_HOME/romp"; printf '{"%s":{"postalServiceOff":true}}' "$1" > "$XDG_STATE_HOME/romp/session-flags.json"; }

@test "agents lists live sessions and marks you" {
    run "$POSTAL" agents
    [ "$status" -eq 0 ]
    [[ "$output" == *"alpha (you)"* ]]
    [[ "$output" == *"beta"* ]]
    # every row carries its short stable id (2026-08-24): the rename-proof address, and what a
    # duplicate-name refusal's candidates can be matched against
    [[ "$output" == *"alpha (you) · uuid-a"* ]]
    [[ "$output" == *"beta · uuid-b"* ]]
}

# ── comment threads (the user 2026-08-22): a thread is a real forked session hidden until promotion;
# it mails its PARENT under its own name, is addressable for replies, and stays a marked minor player ──
addthread() {
    python3 - "$ROMP_SESSIONS_FILE" <<'PY'
import json, sys
rows = json.load(open(sys.argv[1]))
rows.append({"id": "uuid-t", "name": "alpha-comment-1", "state": "working", "dir": "", "working": "",
             "backend": "sdk", "thread": True, "parent": "uuid-a", "lastSid": "uuid-t"})
json.dump(rows, open(sys.argv[1], "w"))
PY
}

@test "a comment thread mails its parent under its own name; its own name still self-refuses" {
    addthread
    CLAUDE_CODE_SESSION_ID=uuid-t run "$POSTAL" send --kind coordinate alpha "the anchor section needs a second pass"
    [ "$status" -eq 0 ]
    [ "$(cnt "$(mb uuid-a)/new")" = "1" ]
    grep -q "From: alpha-comment-1" "$(mb uuid-a)/new/"*
    # the self-send refusal keys on the THREAD's own identity now, not the parent's
    CLAUDE_CODE_SESSION_ID=uuid-t run "$POSTAL" send --kind coordinate alpha-comment-1 "note to self"
    [ "$status" -ne 0 ]
    [[ "$output" == *"own name"* ]]
}

@test "the parent replies to a thread by name, and agents marks the thread row" {
    addthread
    run "$POSTAL" send --kind coordinate alpha-comment-1 "good catch, apply it"
    [ "$status" -eq 0 ]
    [ "$(cnt "$(mb uuid-t)/new")" = "1" ]
    grep -q "From: alpha" "$(mb uuid-t)/new/"*
    run "$POSTAL" agents
    [ "$status" -eq 0 ]
    [[ "$output" == *"(thread of alpha)"* ]]
}

@test "send delivers into the recipient's maildir" {
    run "$POSTAL" send beta "hello there"
    [ "$status" -eq 0 ]
    [[ "$output" == *"delivered to 'beta'"* ]]
    [ "$(cnt "$(mb uuid-b)/new")" = "1" ]
    grep -q "hello there" "$(mb uuid-b)/new/"*
    grep -q "From: alpha" "$(mb uuid-b)/new/"*
}

@test "an anonymous send is refused at the door, and by the CLI before it" {
    # the bus: a raw POST without from_id names the sender's identity resolution as the breakage
    # (authorized with the machine's serve token, like every direct caller — the CLI carries it)
    _tok="${ROMP_SERVE_TOKEN:-$(cat "$XDG_STATE_HOME/romp/serve-token")}"
    run curl -s -X POST -H "X-Romp-Token: $_tok" \
        "127.0.0.1:$ROMP_POSTAL_PORT/send" \
        -d '{"to": "beta", "body": "How is it going?", "kind": "question"}'
    [[ "$output" == *"sender identity required"* ]]
    [ "$(cnt "$(mb uuid-b)/new")" = "0" ]        # no ghost mail minted
    # the CLI: with no resolvable self, it refuses with the actionable half and posts nothing
    CLAUDE_CODE_SESSION_ID= ROMP_SID= run "$POSTAL" send beta "How is it going?"
    [ "$status" -ne 0 ]
    [[ "$output" == *"no session identity resolved"* ]]
    [ "$(cnt "$(mb uuid-b)/new")" = "0" ]
}

@test "a non-session caller sends with --from, and the refusal names the flag" {
    # a launchd/cron script has no session identity; --from gives it an explicit, placeable one
    CLAUDE_CODE_SESSION_ID= ROMP_SID= run "$POSTAL" send --from morning-brief beta "the morning summary"
    [ "$status" -eq 0 ]
    [ "$(cnt "$(mb uuid-b)/new")" = "1" ]
    grep -q "From: morning-brief" "$(mb uuid-b)/new/"*
    grep -q "ext:morning-brief" "$(mb uuid-b)/new/"*
    # without --from the refusal is loud AND names the door
    CLAUDE_CODE_SESSION_ID= ROMP_SID= run "$POSTAL" send beta "anonymous attempt"
    [ "$status" -ne 0 ]
    [[ "$output" == *"pass --from"* ]]
    # a garbage label exits 2
    CLAUDE_CODE_SESSION_ID= ROMP_SID= run "$POSTAL" send --from "two words" beta "x"
    [ "$status" -eq 2 ]
    [[ "$output" == *"--from must be one word"* ]]
}

@test "send to an unknown session errors" {
    run "$POSTAL" send ghost "x"
    [ "$status" -ne 0 ]
    [[ "$output" == *"no live romp session named 'ghost'"* ]]
}

@test "an isolated session (mailbox off) is invisible in agents" {
    iso uuid-b                                   # beta toggles postal isolation on
    run "$POSTAL" agents
    [ "$status" -eq 0 ]
    [[ "$output" == *"alpha"* ]]
    [[ "$output" != *"beta"* ]]                  # isolated → hidden from peers
}

@test "sending TO an isolated session is refused, not parked" {
    iso uuid-b
    run "$POSTAL" send beta "secret"
    [ "$status" -ne 0 ]
    [[ "$output" == *"isolation"* ]]
    [[ "$output" == *"RECIPIENT"* ]]             # the error names whose mailbox is off: the RECIPIENT's
    [[ "$output" == *"YOUR mailbox is fine"* ]]  # ...and reassures the sender it's not them
    [ "$(cnt "$(mb uuid-b)/new")" = "0" ]        # nothing delivered or parked
}

@test "an isolated session cannot send" {
    iso uuid-a                                   # alpha (the current session) is isolated
    run "$POSTAL" send beta "hi"
    [ "$status" -ne 0 ]
    [[ "$output" == *"isolation"* ]]
    [[ "$output" == *"YOUR OWN mailbox is OFF"* ]]   # makes clear it's the SENDER's own mailbox,
    [[ "$output" == *"relay"* ]]                     # ...so a relaying agent tells the user the right thing
    [ "$(cnt "$(mb uuid-b)/new")" = "0" ]
}

@test "an isolated session holds its inbox until it reconnects" {
    run "$POSTAL" send beta "for beta"           # delivered while beta is on the Romp Postal Service
    [ "$(cnt "$(mb uuid-b)/new")" = "1" ]
    iso uuid-b                                    # beta now isolates
    export CLAUDE_CODE_SESSION_ID=uuid-b
    run "$POSTAL" inbox
    [ "$status" -eq 0 ]
    [[ "$output" != *"for beta"* ]]              # held — not delivered while isolated
    [ "$(cnt "$(mb uuid-b)/new")" = "1" ]        # still waiting in new/
}

@test "send to a non-live session errors (addressing is live-only)" {
    # gamma has a persistent names/ record but is NOT a live session: live-only
    # addressing means the send fails and nothing is parked.
    mkdir -p "$XDG_STATE_HOME/romp/names"
    printf 'gamma\t/tmp\t#aa3344\twhite\n' > "$XDG_STATE_HOME/romp/names/uuid-g"
    run "$POSTAL" send gamma "DELEGATE: I'm taking over; you're relieved."
    [ "$status" -ne 0 ]
    [[ "$output" == *"no live romp session named 'gamma'"* ]]
    [ "$(cnt "$(mb uuid-g)/new")" = "0" ]
}

@test "tool descriptions use only the DELEGATE/COORDINATE/QUESTION vocabulary (no stale ASK:/FYI:/HANDOFF:)" {
    # f537fd1 unified the lead-word vocabulary, but missed the revive_session examples; this guards
    # against any old caps-colon lead word creeping back into the MCP tool descriptions / norms.
    run grep -nE "\b(ASK|FYI|HANDOFF):" "$POSTAL"
    [ "$status" -ne 0 ]   # grep finds nothing → non-zero → pass
}

@test "orphan sweep bounces a normal orphan but spares a parked handoff" {
    # Build the orphan mailbox by hand: live-only addressing means a send can't create
    # one (gamma/uuid-g is not a live session). The X-Park plumbing is retained, so a
    # parked message still survives the sweep while a normal orphan is bounced.
    local box; box="$(mb uuid-g)/new"; mkdir -p "$box"
    printf 'From: alpha\nFrom-Id: uuid-a\nDate: t\n\nnormal stale\n' > "$box/normal.msg"
    printf 'From: alpha\nFrom-Id: uuid-a\nX-Park: 1\nDate: t\n\nparked: take over\n' > "$box/parked.msg"
    [ "$(cnt "$box")" = "2" ]
    ROMP_POSTAL_ORPHAN_GRACE=0 run "$POSTAL" sweep
    [ "$status" -eq 0 ]
    [ ! -e "$box/normal.msg" ]                               # non-parked orphan bounced + removed
    [ "$(cnt "$box")" = "1" ]                                # parked handoff still waiting
    grep -q "X-Park: 1" "$box"/*                             # ...and it's the parked one
}

@test "recall unsends an unread message you sent; sent shows it recalled" {
    "$POSTAL" send beta "stale ask please ignore"           # as alpha (uuid-a)
    [ "$(cnt "$(mb uuid-b)/new")" = "1" ]
    run "$POSTAL" recall beta
    [ "$status" -eq 0 ]
    [[ "$output" == *"recalled 1 message"* ]]
    [ "$(cnt "$(mb uuid-b)/new")" = "0" ]                    # gone from the recipient's box
    run "$POSTAL" sent
    [[ "$output" == *"recalled"* ]]                          # receipt reflects it
}

@test "recall removes only your OWN queued messages" {
    "$POSTAL" send beta "from alpha — recall me"            # From-Id: uuid-a
    printf 'From: zeta\nFrom-Id: uuid-zeta\nDate: t\n\nfrom someone else\n' > "$(mb uuid-b)/new/other.msg"
    [ "$(cnt "$(mb uuid-b)/new")" = "2" ]
    run "$POSTAL" recall beta                                # as alpha
    [ "$status" -eq 0 ]
    [ "$(cnt "$(mb uuid-b)/new")" = "1" ]                    # only alpha's removed
    grep -q "from someone else" "$(mb uuid-b)/new/"*        # the other sender's survives
}

@test "pending-mail marker tracks unread mail (present when queued, gone when drained)" {
    pend="$XDG_STATE_HOME/romp/postal/mail-pending/uuid-b"
    [ ! -e "$pend" ]
    "$POSTAL" send beta "ping"
    [ -e "$pend" ]                                  # delivery raises the marker
    CLAUDE_CODE_SESSION_ID=uuid-b "$POSTAL" inbox >/dev/null    # consuming read empties new/
    [ ! -e "$pend" ]                                # ...and clears the marker
}

@test "retry reconciles a stale pending marker (new/ already empty)" {
    mkdir -p "$XDG_STATE_HOME/romp/postal/mail-pending"
    touch "$XDG_STATE_HOME/romp/postal/mail-pending/uuid-ghost"   # marker but no new/ mail
    run "$POSTAL" retry
    [ "$status" -eq 0 ]
    [ ! -e "$XDG_STATE_HOME/romp/postal/mail-pending/uuid-ghost" ]   # reconciled away
}

@test "inbox consumes; peek does not" {
    "$POSTAL" send beta "keep then read"
    CLAUDE_CODE_SESSION_ID=uuid-b run "$POSTAL" peek
    [[ "$output" == *"keep then read"* ]]
    [ "$(cnt "$(mb uuid-b)/new")" = "1" ]
    CLAUDE_CODE_SESSION_ID=uuid-b run "$POSTAL" inbox
    [[ "$output" == *"keep then read"* ]]
    [ "$(cnt "$(mb uuid-b)/new")" = "0" ]
}

@test "loop guard: 8 rapid drains cap at 6, rest pause and are retained" {
    local box; box="$(mb uuid-loop)/new"; mkdir -p "$box"
    local deliv=0 paused=0 n
    for n in $(seq 1 8); do
        printf 'From: g\nDate: t\n\nrapid #%s\n' "$n" > "$box/$(date +%s).$$_${n}.h"
        if [ -n "$("$POSTAL" drain --id uuid-loop)" ]; then deliv=$((deliv+1)); else paused=$((paused+1)); fi
    done
    [ "$deliv" = "6" ]
    [ "$paused" = "2" ]
    [ "$(cnt "$box")" = "2" ]
}

@test "MCP: initialize, tools/list, and tool calls work" {
    req='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_agents","arguments":{}}}
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"send_message","arguments":{"to":"beta","body":"via tool","kind":"coordinate"}}}
{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"send_message","arguments":{"to":"beta","body":"no kind"}}}'
    out="$(printf '%s\n' "$req" | CLAUDE_CODE_SESSION_ID=uuid-a "$POSTAL" mcp 2>/dev/null)"
    [[ "$out" == *'"protocolVersion": "2025-06-18"'* ]]
    [[ "$out" == *"send_message"* ]]
    [[ "$out" == *"check_inbox"* ]]
    [[ "$out" == *"list_agents"* ]]
    [[ "$out" == *"inputSchema"* ]]
    [[ "$out" == *"alpha (you)"* ]]
    [[ "$out" == *"Delivered to 'beta'"* ]]
    [[ "$out" == *"Need 'kind'"* ]]
    [ "$(cnt "$(mb uuid-b)/new")" -ge 1 ]
    grep -q "X-Kind: coordinate" "$(mb uuid-b)/new/"*
}

@test "bus self-stops when no romp clients remain" {
    : > "$SESS"; mksessions   # drop all sessions (also from the seam the bus reads); heartbeats age out (TTL=2)
    local stopped=0 _
    for _ in $(seq 1 30); do
        curl -s "127.0.0.1:$ROMP_POSTAL_PORT/ping" >/dev/null 2>&1 || { stopped=1; break; }
        sleep 0.5
    done
    [ "$stopped" = "1" ]
}

@test "remote: on the host (no SSH) refuses and creates no marker" {
    run "$POSTAL" remote
    [ "$status" -eq 0 ]
    [[ "$output" == *"looks like your Romp Postal Service host"* ]]
    [[ "$output" == *"RemoteForward"* ]]
    [ ! -e "$HOME/.config/romp-postal/client-only" ]
}

@test "remote --force with the bus reachable configures the client and connects" {
    rm -f "$XDG_STATE_HOME/romp/postal/server.pid"   # model a tunnel: reachable port, no local bus
    export SSH_CONNECTION="1 2 3 4"
    run "$POSTAL" remote --force
    [ "$status" -eq 0 ]
    [ -e "$HOME/.config/romp-postal/client-only" ]
    [[ "$output" == *"Already connected"* ]]
    [[ "$output" == *"alpha"* ]]
}

@test "remote: nudge fires for an unconfigured remote, gone once configured" {
    export ROMP_POSTAL_PEERS=0    # the nudge belongs to the LEGACY singleton scheme (peer mode silences it)
    export SSH_CONNECTION="1 2 3 4"
    run "$POSTAL" agents
    [[ "$output" == *"romp mail remote"* ]]
    mkdir -p "$HOME/.config/romp-postal"; touch "$HOME/.config/romp-postal/client-only"
    run "$POSTAL" agents
    [[ "$output" != *"romp mail remote"* ]]
}
