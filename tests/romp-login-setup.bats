#!/usr/bin/env bats

# bin/romp-login-setup (T393, the user 2026-09-12): one command sets up a SECOND Claude login: a scratch sign-in
# mints a setup-token, the token lands in 1Password through a template file, and the script registers the login
# with romp itself. Under test with a stubbed `claude`, `op` and `romp` on PATH: the token must never appear on the
# script's output, in any command's arguments, or in a file left behind; it must reach the store whole; each step
# fails loudly. The script reads no terminal device (the CLI's stdin IS the terminal when run from one; a /dev/tty
# redirect broke the Bun-built CLI on macOS, the user 2026-09-12), so bats runs it directly.
#
# Negative checks are counts, never a bare `! cmd` before another command, which bats cannot see fail (tests/test_bats_bare_negation.py).
#
# Nothing here may contain a credential-shaped literal: gitleaks scans this repo, and a token written out longhand
# would flag the very test that proves the filter. The synthetic token is assembled at run time.

ROMP_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
SCRIPT="$ROMP_DIR/bin/romp-login-setup"

# sk-ant-oat01- followed by forty word characters, assembled so the literal never lives in a tracked file; the second shape
# is the CLI's own wider one (its detector: sk-ant-(oat|ort) and two digits; its redactor: letters, digits, _ . -): an oat02
# prefix and a dot in the body (round one, MEDIUM 4)
synthetic_token() { printf 'sk-ant-%s01-%s%s' oat "$(printf 'AbCdEfGhIj%.0s' 1 2 3)" 0123456789; }
synthetic_token_v2() { printf 'sk-ant-%s02-%s.%s' oat "$(printf 'AbCdEfGhIj%.0s' 1 2 3)" 0123456789; }

setup() {
    TEST_DIR="$(mktemp -d)"
    export TMPDIR="$TEST_DIR/tmp"; mkdir -p "$TMPDIR"     # the script's temporary directory lands here: the tests read what is left
    export TOK="$(synthetic_token)"
    export LOG="$TEST_DIR/log"; : > "$LOG"                 # every stub appends the command line it was called with
    export RECEIVED="$TEST_DIR/received"                    # the template 1Password received
    export CLAUDE_MODE="prints-token"                       # or: no-token
    export CLAUDE_REJECTS=0 OP_CREATE_FAILS=0 OP_ACCEPT_JUNK=0 ROMP_REFUSES=0 ROMP_NO_LOGIN_ADD=0
    MOCK="$TEST_DIR/mock"; mkdir -p "$MOCK"
    # claude: setup-token prints a sign-in narration and the token on its own line, like the CLI; -p (the check) runs
    # the helper the settings file names and answers ok only when it printed the stored token
    cat > "$MOCK/claude" <<'MOCK'
#!/usr/bin/env bash
echo "claude $*" >> "$LOG"
if [ "$1" = "setup-token" ]; then
    echo "Opening the browser to sign in..."
    echo "Signed in as user@example.com (Example Org)."
    if [ "$CLAUDE_MODE" = "prints-token" ]; then echo "Your token: $TOK"; echo "Keep it safe."; else echo "Sign-in was cancelled."; fi
    exit 0
fi
if [ "$1" = "-p" ]; then
    settings=""; while [ $# -gt 0 ]; do [ "$1" = "--settings" ] && settings="$2"; shift; done
    helper="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["apiKeyHelper"])' "$settings")"
    got="$("$helper")"
    [ -n "${ANTHROPIC_API_KEY:-}${ANTHROPIC_AUTH_TOKEN:-}${CLAUDE_CODE_OAUTH_TOKEN:-}${CCR_OAUTH_TOKEN_FILE:-}${CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR:-}${ANTHROPIC_CUSTOM_HEADERS:-}" ] && { echo "a credential rode the environment" >&2; exit 1; }
    if [ "$CLAUDE_REJECTS" = 0 ] && { [ "$got" = "$TOK" ] || [ "$OP_ACCEPT_JUNK" = 1 ]; }; then echo "ok"; exit 0; fi
    echo "Invalid API key: $got" >&2; exit 1
fi
exit 2
MOCK
    # op: item create records the template it was handed; read prints the token
    cat > "$MOCK/op" <<'MOCK'
#!/usr/bin/env bash
echo "op $*" >> "$LOG"
case "$1 $2" in
    "item create") [ "$OP_CREATE_FAILS" = 1 ] && { echo "[ERROR] vault not found" >&2; exit 1; }
                   tpl=""; while [ $# -gt 0 ]; do [ "$1" = "--template" ] && tpl="$2"; shift; done
                   cp "$tpl" "$RECEIVED"; echo '{"id":"x"}'; exit 0 ;;
    "read "*) printf '%s' "$TOK"; exit 0 ;;
esac
exit 2
MOCK
    # romp: a bare `login add` prints the stored-logins branch's usage line (exit 2, before any kernel check, as that branch
    # does), or main's unknown-command line under ROMP_NO_LOGIN_ADD; `login add <label>` records itself and answers as the
    # kernel would (0) unless ROMP_REFUSES
    cat > "$MOCK/romp" <<'MOCK'
#!/usr/bin/env bash
echo "romp $*" >> "$LOG"
if [ $# -eq 2 ]; then
    [ "${ROMP_NO_LOGIN_ADD:-0}" = 1 ] && { echo 'romp: unknown command "login". To start a session named "login": romp new login   (commands: romp help)' >&2; exit 2; }
    echo "usage: romp login add <label> (--cmd '<shell line that prints the token>' | --op <op://vault/item/field>) | romp login list | romp login remove <label|id>" >&2; exit 2
fi
[ "$ROMP_REFUSES" = 1 ] && { echo "romp login: the kernel is not running" >&2; exit 1; }
exit 0
MOCK
    chmod +x "$MOCK"/*
    export PATH="$MOCK:$PATH"
    export ANTHROPIC_API_KEY="not-a-real-key-either" ANTHROPIC_CUSTOM_HEADERS="x-test: 1" CCR_OAUTH_TOKEN_FILE="$TEST_DIR/none"   # the check must remove them from the CLI's environment
}

teardown() { rm -rf "$TEST_DIR"; }

# run merges stdout and stderr into $output, so the no-token checks read everything the script said
run_setup() { run "$SCRIPT" "$@" </dev/null; }

assert_no_token_in_output() { [[ "$output" != *"$TOK"* ]] || { echo "the token reached the output"; return 1; }; }
assert_no_token_in_args() {
    [ "$(grep -c -- "$TOK" "$LOG")" -eq 0 ] || { echo "the token rode a command's arguments: $(sed "s/$TOK/<token>/g" "$LOG")"; return 1; }
}
assert_nothing_left() { [ -z "$(ls -A "$TMPDIR")" ] || { echo "temporary files were left: $(ls -A "$TMPDIR")"; return 1; }; }
line_of() { grep -n -- "$1" "$LOG" | head -1 | cut -d: -f1; }

@test "the token is masked on screen, reaches 1Password in the template whole, rides no argument, and romp is told the reference" {
    run_setup Private "Claude second login"
    [ "$status" -eq 0 ] || { echo "$output"; false; }
    [[ "$output" == *"<token captured>"* ]]
    [[ "$output" == *"Signed in as user@example.com (Example Org)."* ]]   # every other line reaches the terminal
    assert_no_token_in_output
    assert_no_token_in_args
    grep -q "\"value\":\"$TOK\"" "$RECEIVED"                              # the template carried it whole
    grep -q '"category":"API_CREDENTIAL"' "$RECEIVED"
    grep -q '"id":"credential"' "$RECEIVED"
    grep -q '"title":"Claude second login"' "$RECEIVED"
    grep -q "^op item create --vault Private --template " "$LOG"          # through a template file, never an argument
    grep -q "^romp login add Claude second login --op op://Private/Claude second login/credential$" "$LOG"
    [ "$(line_of '^claude setup-token')" -lt "$(line_of '^op item create')" ]
    [ "$(line_of '^op item create')" -lt "$(line_of '^romp login add Claude')" ]
    [ "$(line_of '^romp login add$')" -lt "$(line_of '^claude setup-token')" ]   # the command's presence is checked before any sign-in
    assert_nothing_left
}

@test "a romp without login add stops before any sign-in (round one, MEDIUM 1: main has no such command yet)" {
    export ROMP_NO_LOGIN_ADD=1
    run_setup Private Second
    [ "$status" -eq 1 ]
    [[ "$output" == *"deploy the stored-logins change first"* ]]
    [ "$(grep -c '^claude' "$LOG")" -eq 0 ]
}

@test "a label with a quote, a backslash or a slash is refused before any sign-in (round one, MEDIUM 2 and 3)" {
    for bad in 'Work "quoted"' 'back\slash' 'a/b' ''; do
        run_setup Private "$bad"
        [ "$status" -ne 0 ]
        [[ "$output" == *"letters, digits, spaces, dashes and underscores"* ]] || [[ "$output" == *"usage:"* ]]
    done
    [ "$(grep -c '^claude' "$LOG")" -eq 0 ]
    run_setup Private "Work login_2 - personal"
    [ "$status" -eq 0 ]
}

@test "the CLI's token shapes: an oat02 prefix with a dot in the body is masked whole and captured whole (round one, MEDIUM 4)" {
    export TOK="$(synthetic_token_v2)"
    run_setup Private Second
    [ "$status" -eq 0 ]
    [[ "$TOK" == sk-ant-oat02-*.* ]]                                          # the synthetic token carries both
    assert_no_token_in_output
    [[ "$output" != *"sk-ant-"* ]]                                            # not even a prefix survives the mask
    grep -q "\"value\":\"$TOK\"" "$RECEIVED"                                # captured to the last character
}

@test "the sign-in runs under a scratch configuration directory that is removed afterwards" {
    run_setup Private Second
    [ "$status" -eq 0 ]
    assert_nothing_left
}

@test "no token printed: the script fails loudly, stores nothing and tells romp nothing" {
    export CLAUDE_MODE="no-token"
    run_setup Private Second
    [ "$status" -eq 1 ]
    [[ "$output" == *"no token was printed"* ]]
    [ "$(grep -c '^op item create' "$LOG")" -eq 0 ]
    [ "$(grep -Ec '^romp login add [^-]' "$LOG")" -eq 0 ]                   # no registration (the --help preflight is not one)
    assert_nothing_left
}

@test "1Password refusing the item stops the script before romp is told" {
    export OP_CREATE_FAILS=1
    run_setup Private Second
    [ "$status" -ne 0 ]
    [ "$(grep -Ec '^romp login add [^-]' "$LOG")" -eq 0 ]                   # no registration (the --help preflight is not one)
    assert_no_token_in_output
    assert_nothing_left
}

@test "romp refusing the registration is a loud failure, with the item already stored" {
    export ROMP_REFUSES=1
    run_setup Private Second
    [ "$status" -ne 0 ]
    [[ "$output" == *"the kernel is not running"* ]]
    [ -s "$RECEIVED" ]
    assert_no_token_in_output
}

@test "--check: a request through a helper reading the item is accepted with the environment's credential variables removed, and a junk helper is refused" {
    run_setup Private Second --check
    [ "$status" -eq 0 ] || { echo "$output"; false; }
    [[ "$output" == *"check passed"* ]]
    [ "$(grep -c '^claude -p ' "$LOG")" -eq 2 ]
    assert_no_token_in_output
    assert_no_token_in_args
    assert_nothing_left
}

@test "--check: the stored token not being accepted fails the check" {
    export CLAUDE_REJECTS=1
    run_setup Private Second --check
    [ "$status" -eq 1 ]
    [[ "$output" == *"the stored token was not accepted"* ]]
    assert_no_token_in_output
}

@test "--check: a junk helper that is ALSO accepted fails the check" {
    export OP_ACCEPT_JUNK=1
    run_setup Private Second --check
    [ "$status" -eq 1 ]
    [[ "$output" == *"a junk helper was also accepted"* ]]
}

@test "usage: the vault and the label are required, --check is accepted anywhere, an unknown flag is refused (round one, LOW 3)" {
    run_setup
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage:"* ]]
    run_setup Private
    [ "$status" -eq 2 ]
    run_setup Private Second --chekc
    [ "$status" -eq 2 ]
    run_setup Private Second extra
    [ "$status" -eq 2 ]
    [ "$(grep -c '^claude' "$LOG")" -eq 0 ]
    run_setup --check Private Second
    [ "$status" -eq 0 ]
    [[ "$output" == *"check passed"* ]]
}
