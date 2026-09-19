#!/usr/bin/env bats

# .githooks/pre-push: the MESSAGE scan, driven by hand against real commits. The
# subject and body of each commit a push publishes, and an annotated tag's own
# message.
#
# A commit is published with its metadata, and the hook's two content scans (the
# tip's tree, each new commit's added lines) read none of it. The address scan
# (pre-push-identity.bats) covers the author and committer addresses; this file
# covers the message, which is where a commit describing a fix names the path
# that failed or the machine it failed on. A string a file carries can be
# redacted forward once it is out; a string a message carries cannot, since only
# a rewrite of the commit takes it back. So the hook greps every new commit's
# message like lines of content, against the whole denylist, and names the
# commit and the line.
#
# Three limits, all pinned here: only what THIS push publishes is read (a message
# a remote already holds is refused to no purpose, like an added line it holds);
# the author and committer NAMES on the commit are not read, being the author's
# own and on every commit they make; and the message is all that is read of a
# signed commit, not the verification report a log.showSignature config has git
# log print ahead of it, which names the signer. The message itself is read
# whole, the author's own name included: it is text they typed, and a FILE naming
# them is refused on the same ground. An annotated TAG's message is read the same
# way (every other read the hook makes peels a tag to its commit): the tag case
# at the end holds that.
#
# Every identifier below is SYNTHETIC: the denylist, the logins, the hosts and
# the paths are invented per test (the repo may go public, and a real one written
# here would be the very leak the hook exists to stop).

ROMP_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
HOOK="$ROMP_DIR/.githooks/pre-push"

load git-hermetic

setup() {
    # Hermetic git: the fixture commits with plain defaults under a synthetic identity
    # (git-hermetic exports GIT_AUTHOR_EMAIL / GIT_COMMITTER_EMAIL as tests@example.invalid,
    # which the address scan excuses as an address the environment chose), so the only
    # thing a fixture can trip here is the message scan.
    git_hermetic
    unset EMAIL        # a chosen address to the hook, and the developer's shell may set it
    TEST_DIR="$(mktemp -d)"
    REPO="$TEST_DIR/repo"
    mkdir -p "$REPO"
    git -C "$REPO" init -q
    git -C "$REPO" symbolic-ref HEAD refs/heads/main     # whatever init.defaultBranch says
    git -C "$REPO" config user.email tests@example.invalid
    git -C "$REPO" config user.name  Tester
    # The hook under test is run BY HAND below; the fixture's own git operations must
    # not run this machine's hooks.
    mkdir -p "$TEST_DIR/no-hooks"
    git -C "$REPO" config core.hooksPath "$TEST_DIR/no-hooks"

    # the denylist: an invented login and host, nothing that exists on any real machine
    STRINGS="$TEST_DIR/private-strings.txt"
    printf '# synthetic\nzzsynthuser\nTESTHOST\n' > "$STRINGS"

    export ROMP_PRIVATE_STRINGS="$STRINGS"
    export ROMP_NO_GITLEAKS=1          # the credential half has its own test file
}

teardown() { rm -rf "${TEST_DIR:-}"; }

ZERO=0000000000000000000000000000000000000000

# Run the hook from inside the repo, the way git does. bats' `run` executes the
# command in a subshell, so the cd here does not leak into the test.
_hook_in() { cd "$1" && shift && bash "$@"; }

# Feed the hook a ref line the way git does: <local_ref> <sha> <remote_ref> <remote_sha>.
# The default remote sha of zero means a new branch, i.e. every commit here is
# being published; pass the sha the remote holds to model updating a branch it has.
run_hook() {
    local sha remote_sha="${1:-$ZERO}"
    sha="$(git -C "$REPO" rev-parse HEAD)"
    run _hook_in "$REPO" "$HOOK" origin git@example.invalid:x/y.git <<< \
        "refs/heads/main $sha refs/heads/main $remote_sha"
}

# Stage one clean file, ready for a commit made with whatever options a test needs.
stage_file() {   # <path>
    printf '%s\n' "the web session's work on $1" > "$REPO/$1"
    git -C "$REPO" add "$1"
}

# ssh signing (gpg.format ssh) arrived in git 2.34; the signed-commit case skips below it.
git_at_least_2_34() {
    local v major rest minor
    v="$(git --version | awk '{print $3}')"
    major="${v%%.*}"; rest="${v#*.}"; minor="${rest%%.*}"
    [ "$major" -gt 2 ] 2>/dev/null || { [ "$major" -eq 2 ] && [ "$minor" -ge 34 ]; } 2>/dev/null
}

# A commit of one clean file with the given message paragraphs (each -m is one;
# git joins them with a blank line, so the second paragraph is message line 3).
commit_msg() {   # <path> <paragraph>...
    local path="$1"; shift
    local args=()
    local para
    for para in "$@"; do args+=(-m "$para"); done
    stage_file "$path"
    git -C "$REPO" commit -q "${args[@]}"
}

@test "a clean message passes (the control)" {
    commit_msg ok.txt "clean tree, clean message"
    run_hook
    [ "$status" -eq 0 ]
}

@test "a banned string in the SUBJECT is refused, naming the commit and line 1" {
    commit_msg web.txt "fix the path /home/zzsynthuser/code on that machine"
    sha="$(git -C "$REPO" rev-parse HEAD)"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"the MESSAGE of commit ${sha:0:10} carries a personal identifier on line 1 (line 1 is the subject)"* ]]
    [[ "$output" != *"zzsynthuser"* ]]          # the line is named; the string itself is not echoed
    [[ "$output" == *"BLOCKED"* ]]
    # the remedy is a rewrite of the commit, and it is the message's own
    [[ "$output" == *"A commit MESSAGE is never redacted forward"* ]]
    [[ "$output" == *"git commit --amend"* ]]
    [[ "$output" == *"reword rebase"* ]]
    # the tree and the added lines were clean: no content report, and not the address remedy
    [[ "$output" != *"ADDS a personal identifier"* ]]
    [[ "$output" != *"personal identifier in:"* ]]
    [[ "$output" != *"--reset-author"* ]]
}

@test "a banned string in the BODY alone is refused, naming the body's line" {
    commit_msg web.txt "fix the poll" "the failing run was on TESTHOST"
    sha="$(git -C "$REPO" rev-parse HEAD)"
    [ "$(git -C "$REPO" log -1 --format=%B | sed -n 3p)" = "the failing run was on TESTHOST" ]
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"the MESSAGE of commit ${sha:0:10} carries a personal identifier on line 3 "* ]]
    [[ "$output" != *"TESTHOST"* ]]
}

@test "every matching line is named, subject and body alike" {
    commit_msg web.txt "seen on TESTHOST" "reproduced under /home/zzsynthuser/x"
    sha="$(git -C "$REPO" rev-parse HEAD)"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"the MESSAGE of commit ${sha:0:10} carries a personal identifier on line 1,3 "* ]]
}

@test "the match is case-insensitive, like a line of content" {
    commit_msg web.txt "seen on testhost"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"the MESSAGE of commit"* ]]
}

@test "the author's and committer's NAMES are not read: they are on every commit they make" {
    # the name is on the denylist because a FILE must not carry it; the commit's own
    # author line carries it regardless, and refusing that would refuse every push
    stage_file web.txt
    GIT_AUTHOR_NAME=zzsynthuser GIT_COMMITTER_NAME=TESTHOST git -C "$REPO" commit -qm "clean message"
    [ "$(git -C "$REPO" log -1 --format='%an|%cn')" = "zzsynthuser|TESTHOST" ]
    run_hook
    [ "$status" -eq 0 ]
}

@test "the message is read whole: a trailer naming the author is refused like a file naming them" {
    commit_msg web.txt "clean subject" "Signed-off-by: zzsynthuser <dev@example.invalid>"
    sha="$(git -C "$REPO" rev-parse HEAD)"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"the MESSAGE of commit ${sha:0:10} carries a personal identifier on line 3 "* ]]
}

@test "the message remedy is not printed for a content hit" {
    printf '%s\n' "home is /home/zzsynthuser/code" > "$REPO/leak.txt"
    git -C "$REPO" add leak.txt
    git -C "$REPO" commit -qm "leak in a file, clean message"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"personal identifier in:"* ]]
    [[ "$output" != *"the MESSAGE of commit"* ]]
    [[ "$output" != *"never redacted forward"* ]]
}

@test "a leaking message on an INTERMEDIATE commit is caught and named when the tip's is clean" {
    commit_msg web.txt "broke on TESTHOST"
    leaky_sha="$(git -C "$REPO" rev-parse HEAD)"
    commit_msg api.txt "clean follow-up"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"the MESSAGE of commit ${leaky_sha:0:10} carries"* ]]
}

@test "a leaking message a remote already has is not rechecked: only what THIS push publishes counts" {
    # the string is already public through that remote, and a message has no forward
    # remedy; refusing every later push of the branch would fix nothing
    commit_msg web.txt "broke on TESTHOST, and pushed"
    git -C "$REPO" update-ref refs/remotes/origin/main HEAD
    commit_msg api.txt "the one commit new to every remote"
    run_hook "$(git -C "$REPO" rev-parse origin/main)"
    [ "$status" -eq 0 ]
}

@test "no denylist file means no message scan (a fresh clone is unaffected)" {
    export ROMP_PRIVATE_STRINGS="$TEST_DIR/does-not-exist.txt"
    commit_msg web.txt "broke on TESTHOST"
    run_hook
    [ "$status" -eq 0 ]
}

@test "a merge commit's message is read like any other commit's" {
    # a merge's message is typed by whoever resolves it, and a conflict note is where a machine's name lands
    commit_msg base.txt "base"
    git -C "$REPO" checkout -q -b feature
    commit_msg web.txt "branch work"
    git -C "$REPO" checkout -q main
    commit_msg api.txt "main work"
    git -C "$REPO" checkout -q feature
    git -C "$REPO" merge -q --no-ff -m "merge main, resolved on TESTHOST" main
    merge_sha="$(git -C "$REPO" rev-parse HEAD)"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"the MESSAGE of commit ${merge_sha:0:10} carries a personal identifier on line 1 "* ]]
}

@test "a signed commit is read as its message alone: the verification report log.showSignature prints ahead of it is not" {
    # With log.showSignature set, git log prints a signed commit's verification report before
    # whatever the format asks for, and the report names the signer: the address here, whose
    # domain goes on the denylist the way the author's own name is. Read as message text, that
    # refused a clean signed commit and numbered a real hit from the report's line, not the
    # subject. An ssh signature needs only ssh-keygen, which is on every machine that pushes.
    git_at_least_2_34 || skip "ssh signing needs git >= 2.34"
    command -v ssh-keygen >/dev/null || skip "ssh-keygen is not installed"
    ssh-keygen -q -t ed25519 -N '' -f "$TEST_DIR/key" -C 'romp tests' >/dev/null
    printf 'tests@example.invalid %s\n' "$(cut -d' ' -f1,2 "$TEST_DIR/key.pub")" > "$TEST_DIR/allowed"
    git -C "$REPO" config gpg.format ssh
    git -C "$REPO" config user.signingkey "$TEST_DIR/key.pub"
    git -C "$REPO" config gpg.ssh.allowedSignersFile "$TEST_DIR/allowed"
    git -C "$REPO" config log.showSignature true
    printf 'example.invalid\n' >> "$STRINGS"          # the signer's domain; the address scan excuses the address itself
    stage_file ok.txt
    git -C "$REPO" commit -q -S -m "clean subject" -m "clean body"
    [[ "$(git -C "$REPO" log -1 --format=%B)" == *"signature"* ]]      # the config is live: git log prepends the report
    run_hook
    [ "$status" -eq 0 ]
    stage_file web.txt
    git -C "$REPO" commit -q -S -m "seen on TESTHOST"
    sha="$(git -C "$REPO" rev-parse HEAD)"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"the MESSAGE of commit ${sha:0:10} carries a personal identifier on line 1 "* ]]
}

@test "a message hit and an address hit on one push report both remedies" {
    # the two metadata scans are independent; each names its own way out
    stage_file web.txt
    GIT_AUTHOR_EMAIL=dev@TESTHOST.example GIT_COMMITTER_EMAIL=dev@TESTHOST.example \
        git -C "$REPO" commit -qm "broke under /home/zzsynthuser"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"the MESSAGE of commit"* ]]
    [[ "$output" == *"whose domain carries a personal identifier"* ]]
    [[ "$output" == *"never redacted forward"* ]]
    [[ "$output" == *"--reset-author"* ]]
}

@test "an annotated tag's MESSAGE is read like a commit's, naming the tag and the line, with the tag's own remedy" {
    commit_msg ok.txt "clean"
    git -C "$REPO" tag -a v1 -m "release one" -m "cut on TESTHOST"
    sha="$(git -C "$REPO" rev-parse refs/tags/v1)"
    [ "$(git -C "$REPO" cat-file -t "$sha")" = tag ]
    run _hook_in "$REPO" "$HOOK" origin git@example.invalid:x/y.git <<< \
        "refs/tags/v1 $sha refs/tags/v1 $ZERO"
    [ "$status" -ne 0 ]
    [[ "$output" == *"the MESSAGE of tag refs/tags/v1 (${sha:0:10}) carries a personal identifier on line 3"* ]]
    [[ "$output" != *"TESTHOST"* ]]
    [[ "$output" == *"git tag -f -a <name> <commit>"* ]]
    # the commit it names has a clean message and is reported as nothing
    [[ "$output" != *"the MESSAGE of commit"* ]]
}
