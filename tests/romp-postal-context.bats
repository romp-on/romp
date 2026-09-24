#!/usr/bin/env bats

# romp-postal-context.sh is a SessionStart hook: in a romp session (one launched with
# ROMP_SID in its environment) whose mail is on, it emits a COMPACT pointer to the postal
# capability as additionalContext.
# The full norms live in the romp-postal skill (loaded on demand) + the postal MCP
# tools' own descriptions, so this pointer stays small and re-cheap every turn. It
# must be silent outside a romp session, and must never fail the turn.
# Two gates. ROMP_SID in the environment (the kernel sets it on every CLI it spawns), and the
# CLI's own id, from the payload's session_id, naming the session ROMP_SID names: the romp sid
# itself (a fresh spawn or a born fork, whose CLI the kernel pins to the sid) or the SDK
# registry's lastSid for it (the conversation a resume continued, or a compaction kept). Every
# process a session's Bash tool runs inherits ROMP_SID, so a `claude -p` a session spawned used to
# pass the first gate and take this pointer as its own; its id is in neither place, and the hook
# stays silent. The start's source moves the check in two places, both for a start the hook sees
# BEFORE the kernel's init-time lastSid write (SdkSession._on_message): an EMPTY lastSid (the reg
# as SdkBackend.spawn mints it) passes a `startup` and nothing else, so a child that auto-compacted
# mid-run and came back as a `compact` start with its own id gets nothing; and a `clear` passes on
# the reg's EXISTENCE with no id match, because a /clear's new id reaches the reg only after its
# SessionStart has run, so the match failed on every /clear (nothing a session's Bash tool runs
# fires a `clear`: a child's start is a `startup`, its compaction a `compact` under the same id).

setup() {
    TEST_DIR="$(mktemp -d)"
    export HOME="$TEST_DIR/home"; mkdir -p "$HOME"
    # The SDK registry the hook reads lives under the state root as sdk/<ROMP_SID>.json; ROMP_STATE_DIR
    # wins over XDG_STATE_HOME when set, so a developer's override is cleared.
    unset ROMP_STATE_DIR
    export XDG_STATE_HOME="$TEST_DIR/state"
    # The first gate is ROMP_SID in the hook's environment; a developer running the suite from inside
    # a romp session must not pass the silent case by accident, so it is cleared here and set per
    # test. CLAUDE_CODE_SESSION_ID stands in for a payload without a session_id and would leak a
    # developer's own id into the no-id cases.
    unset ROMP_SID CLAUDE_CODE_SESSION_ID
    SID="11111111-2222-3333-4444-555555555555"     # the romp sid: ROMP_SID, and a fresh spawn's CLI id
    FSID="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"    # the conversation the reg's lastSid names (a resume)
    OTHER="99999999-8888-7777-6666-555555555555"   # an id in neither place: a child the session ran (its first start, or its compaction mid-run)
    CLEARED="cccccccc-dddd-4eee-8fff-000000000000"  # the id a /clear rotated the CLI onto; the reg learns it only after the clear SessionStart
    HOOK="$(cd "$(dirname "$BATS_TEST_FILENAME")/../hooks" && pwd)/romp-postal-context.sh"
}

teardown() { rm -rf "$TEST_DIR"; }

# the SDK registry row for ROMP_SID with the given lastSid (json.dumps spacing, as write_reg writes it)
write_reg() {
    mkdir -p "$XDG_STATE_HOME/romp/sdk"
    printf '{"sid": "%s", "name": "web", "cwd": "/tmp/notes-api", "mode": "acceptEdits", "lastSid": "%s", "alive": true}' \
        "$SID" "$1" > "$XDG_STATE_HOME/romp/sdk/$SID.json"
}
# $1 the CLI's session_id, $2 the start's source (startup | resume | clear | compact)
payload() { printf '{"session_id":"%s","transcript_path":"/tmp/notes-api/t.jsonl","hook_event_name":"SessionStart","source":"%s"}' "$1" "$2"; }
# $1 the SessionStart payload on the hook's stdin
run_hook() { run bash -c 'printf "%s" "$1" | "$2"' _ "$1" "$HOOK"; }

@test "in a romp session it emits a compact postal pointer as additionalContext" {
    write_reg "$FSID"
    ROMP_SID="$SID" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
    [[ "$output" == *'"hookEventName": "SessionStart"'* ]]
    [[ "$output" == *'postal MCP tools'* ]]
    # the declare-your-intent norm is present up front — as send_message's REQUIRED `kind` parameter, never
    # the retired DELEGATE:/COORDINATE:/QUESTION: body prefix the hook used to teach beside it (two
    # instructions for one fact). The negative pin is deliberate: the prefix must not come back.
    [[ "$output" == *'set `kind` to delegate, coordinate, or question'* ]]
    [[ "$output" != *'DELEGATE'* ]]
    [[ "$output" == *'list_agents'* ]]     # the coordinate-before-editing norm is present up front
    [[ "$output" == *'romp-postal skill'* ]]   # points to the full guide, not inlined
}

@test "a fresh spawn passes before the registry has learned its id" {
    # the kernel pins a fresh CLI's id to the romp sid (--session-id) and mints the reg with lastSid "";
    # the id reaches the reg only when the CLI's init lands at the kernel, which can be after this hook
    write_reg ""
    ROMP_SID="$SID" run_hook "$(payload "$SID" startup)"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
}

@test "a born fork passes on the romp sid while the reg's lastSid is still the parent's conversation" {
    write_reg "$FSID"
    ROMP_SID="$SID" run_hook "$(payload "$SID" resume)"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
}

@test "a CLI the session itself ran (an id in neither place) gets nothing" {
    # a `claude -p` from the session's Bash tool inherits ROMP_SID; its own session_id is a fresh uuid
    write_reg "$FSID"
    ROMP_SID="$SID" run_hook "$(payload "$OTHER" startup)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "an empty lastSid lets an unrecorded id through at startup only: the init has not reached the kernel yet" {
    # SdkBackend.spawn mints the reg with lastSid "" and the init's flip fills it, so only a first start can
    # read an empty one; by any later source the field holds an id, and the payload's id must be it
    write_reg ""
    ROMP_SID="$SID" run_hook "$(payload "$FSID" startup)"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
    ROMP_SID="$SID" run_hook "$(payload "$FSID" compact)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a compaction with an id the reg does not hold gets nothing: the source alone is no pass" {
    # a `claude -p` the session's Bash tool ran auto-compacts mid-run and starts again as a `compact` with its
    # own id; an earlier cut let compact through without reading the reg, and the child took the pointer
    write_reg "$FSID"
    ROMP_SID="$SID" run_hook "$(payload "$OTHER" compact)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a /clear start with a new id and an existing reg passes: the reg learns the id only after this hook" {
    # a /clear rotates the CLI onto a fresh id; the kernel records it when the init lands (SdkSession._on_message),
    # AFTER the clear SessionStart has run, so the reg still holds the previous conversation here. Requiring the
    # match lost the pointer on every /clear; the source is enough, since nothing a session's Bash tool runs fires a clear
    write_reg "$FSID"
    ROMP_SID="$SID" run_hook "$(payload "$CLEARED" clear)"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
}

@test "a /clear start with no reg for the sid gets nothing, and the hook does not fail" {
    ROMP_SID="$SID" run_hook "$(payload "$CLEARED" clear)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a compaction keeps the CLI's id, so a compact start on the reg's lastSid passes" {
    write_reg "$FSID"
    ROMP_SID="$SID" run_hook "$(payload "$FSID" compact)"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
}

@test "a /clear start on the romp sid passes" {
    write_reg "$FSID"
    ROMP_SID="$SID" run_hook "$(payload "$SID" clear)"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
}

@test "no reg for the sid: an id that is not the romp sid gets nothing, and the hook does not fail" {
    ROMP_SID="$SID" run_hook "$(payload "$OTHER" startup)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "an unreadable reg is the same silence, never a failed turn" {
    mkdir -p "$XDG_STATE_HOME/romp/sdk"; printf 'not json' > "$XDG_STATE_HOME/romp/sdk/$SID.json"
    ROMP_SID="$SID" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    # a clear passes on the reg's existence, and an unreadable file is not one
    ROMP_SID="$SID" run_hook "$(payload "$CLEARED" clear)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a payload without session_id: CLAUDE_CODE_SESSION_ID stands in, and with neither the hook is silent" {
    write_reg "$FSID"
    ROMP_SID="$SID" CLAUDE_CODE_SESSION_ID="$FSID" run_hook '{}'
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
    ROMP_SID="$SID" run_hook '{}'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "outside a romp session (no ROMP_SID) it is silent" {
    write_reg "$FSID"
    run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "an empty ROMP_SID is not a romp session either" {
    write_reg "$FSID"
    ROMP_SID="" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "the pointer is self-contained (no dependence on the skill file) and never fails" {
    # the hook no longer reads SKILL.md; it emits the same pointer regardless, so a
    # missing skill file can't blank it or fail the turn.
    write_reg "$FSID"
    ROMP_SID="$SID" run_hook "$(payload "$FSID" startup)"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
}

# session-flags.json under the state root, written verbatim ($1 the JSON object)
write_flags() { mkdir -p "$XDG_STATE_HOME/romp"; printf '%s' "$1" > "$XDG_STATE_HOME/romp/session-flags.json"; }

# The mail-off gate: a session whose mail is off is told nothing about peers. Resolved in the bus's
# _mail_off_why order: a comment thread's default, the session's own isolation key (legacy included),
# then the "*" master.
@test "a session isolated by its own flag gets nothing" {
    write_reg "$FSID"; write_flags "{\"$SID\": {\"postalServiceOff\": true}}"
    ROMP_SID="$SID" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "the master default isolates a session with no key of its own" {
    write_reg "$FSID"; write_flags '{"*": {"postalServiceOff": true}}'
    ROMP_SID="$SID" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a session's explicit opt-in beats a master isolation" {
    write_reg "$FSID"; write_flags "{\"*\": {\"postalServiceOff\": true}, \"$SID\": {\"postalServiceOff\": false}}"
    ROMP_SID="$SID" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
}

@test "the legacy postalOff key still isolates" {
    write_reg "$FSID"; write_flags "{\"$SID\": {\"postalOff\": true}}"
    ROMP_SID="$SID" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a comment thread is told nothing until its mail is turned on" {
    mkdir -p "$XDG_STATE_HOME/romp/sdk"
    printf '{"sid": "%s", "lastSid": "%s", "threadOf": "%s"}' "$SID" "$FSID" "$OTHER" > "$XDG_STATE_HOME/romp/sdk/$SID.json"
    ROMP_SID="$SID" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    write_flags "{\"$SID\": {\"threadMail\": true}}"
    ROMP_SID="$SID" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
}

@test "a flags file that cannot be read is mail off, as the bus holds mail for it" {
    write_reg "$FSID"; write_flags 'not json'
    ROMP_SID="$SID" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a flags file that is valid JSON but not an object is mail off" {
    write_reg "$FSID"; write_flags '["not", "an", "object"]'
    ROMP_SID="$SID" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a missing flags file the kernel quarantined is mail off, as the bus reads it" {
    write_reg "$FSID"; mkdir -p "$XDG_STATE_HOME/romp"
    printf 'torn' > "$XDG_STATE_HOME/romp/session-flags.json.corrupt-20260101T000000Z"
    ROMP_SID="$SID" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a null isolation key is unset, so the master still isolates" {
    write_reg "$FSID"; write_flags "{\"*\": {\"postalServiceOff\": true}, \"$SID\": {\"postalServiceOff\": null}}"
    ROMP_SID="$SID" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a thread's threadMail must be the literal true: 1 keeps it silent" {
    mkdir -p "$XDG_STATE_HOME/romp/sdk"
    printf '{"sid": "%s", "lastSid": "%s", "threadOf": "%s"}' "$SID" "$FSID" "$OTHER" > "$XDG_STATE_HOME/romp/sdk/$SID.json"
    write_flags "{\"$SID\": {\"threadMail\": 1}}"
    ROMP_SID="$SID" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a fresh spawn with an unreadable reg is mail off, not an ordinary session" {
    mkdir -p "$XDG_STATE_HOME/romp/sdk"; printf 'not json' > "$XDG_STATE_HOME/romp/sdk/$SID.json"
    ROMP_SID="$SID" run_hook "$(payload "$SID" startup)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "ROMP_STATE_DIR outranks XDG_STATE_HOME for the flags" {
    mkdir -p "$TEST_DIR/override"
    printf '{"*": {"postalServiceOff": true}}' > "$TEST_DIR/override/session-flags.json"
    ROMP_STATE_DIR="$TEST_DIR/override" ROMP_SID="$SID" run_hook "$(payload "$SID" startup)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "with both own keys set, postalServiceOff decides" {
    write_reg "$FSID"; write_flags "{\"$SID\": {\"postalServiceOff\": false, \"postalOff\": true}}"
    ROMP_SID="$SID" run_hook "$(payload "$FSID" resume)"
    [[ "$output" == *'"additionalContext"'* ]]
    write_flags "{\"$SID\": {\"postalServiceOff\": true, \"postalOff\": false}}"
    ROMP_SID="$SID" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a state root that cannot be stat'd is mail off" {
    printf 'a file, not a directory' > "$TEST_DIR/rootfile"
    ROMP_STATE_DIR="$TEST_DIR/rootfile/romp" ROMP_SID="$SID" run_hook "$(payload "$SID" startup)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}
