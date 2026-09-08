"""Every shell file bash parses on a user's machine parses on bash 3.2, macOS's system bash
(2026-09-08).

bash 3.2 finds the end of a `$( ... )` by scanning its raw text for the matching parenthesis: it
pairs quotes and parentheses as it goes and understands neither heredocs nor `#` comments (bash 4.0's
parse_comsub added both). A heredoc opened INSIDE a substitution is therefore read as shell text, and
whether the substitution parses is an accident of the body's quote balance. hooks/romp-summarize.sh's
Stop arm opened its transcript-reading Python that way, and three apostrophes in the Python's
comments left the scan inside a quote, so the closing paren was never found and the whole detached
compound the arm sits in failed to parse. The hook's synchronous prelude had already set the tmux
kind to "pending", so on a Mac running the system bash the status line's spinner never ended and no
summary landed (the hook is registered async, so no prompt was ever blocked). bash 5 parses the same
file, which is why no Linux run saw it.

The rule: a heredoc never opens while a `$(` is unclosed; put the heredoc in a function and
substitute the call. The rule is the SHAPE, not today's count: a body that happens to balance
(tools/romp-lab/lab.sh's port probe, tests/free-port.bash's) parses by accident, and the next
apostrophe in a comment breaks it with no test to say so, so the scanner flags the shape and reports
the body's state as the diagnosis. The same scan reads a `#` comment inside a `$( ... )` as shell,
so a comment there must leave the scan where it found it (quotes closed, parentheses balanced);
that is the rule's one other clause. What bash 3.2's scan counts: both quote kinds and backticks,
parentheses outside them, a backslash escaping the next character outside single quotes, `$'...'`
allowing a backslashed quote; a `'` inside "..." and a `"` inside '...' are literal. Whether the
heredoc's tag is quoted changes how the body EXPANDS at run time, never how the paren is found, so
<<TAG, <<'TAG' and <<-TAG are treated alike. Backticks are outside the rule: bash 3.2 scans `...`
straight to the next unescaped backtick, exactly as newer bash does.

Scope: every git-tracked file whose first line is a bash or sh shebang (macOS's /bin/sh is bash 3.2
in POSIX mode), every tracked *.sh, and the tests/*.bash helpers the bats suites source (bats runs
under whatever bash the machine has). The .bats files are bats syntax, not bash, and carry no
instance of the shape; they are outside this module.

Pinned three ways. The scanner on synthetic snippets: the defect shape with the odd apostrophes,
the balanced shape, the fixed shape, the comment clause, and the look-alikes it must not flag (a
here-string, a top-level heredoc, a heredoc inside a plain `( ... )` subshell, an arithmetic shift,
`${#x}`, a comment after the substitution closed). The scanner and `bash -n` over every file in
scope; on this change's parent the scanner named hooks/romp-summarize.sh:170 (the break),
tools/romp-lab/lab.sh:40 and tests/free-port.bash:40 (the shape, balanced). And a real bash 3.2,
when the machine has one (ROMP_BASH32, a bash-3.2 on PATH, or /bin/bash itself on a Mac), refuses
the defect snippet and parses every file in scope; that check skips otherwise rather than imitate
one. This module loads no romp code, so it needs no state-root preamble.
"""
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)

# `#!/bin/sh`, `#!/bin/bash`, `#!/usr/bin/env bash`; not `#!/usr/bin/env bats` or python.
SHEBANG = re.compile(r"^#!\s*(?:/usr/bin/env\s+)?(?:\S*/)?(?:ba)?sh\b")
# <<TAG, <<'TAG', <<"TAG", <<-TAG, <<\TAG. The caller has already stepped over `<<<` here-strings.
HEREDOC_OP = re.compile(r"<<(-?)[ \t]*(?:'([^']*)'|\"([^\"]*)\"|\\?([A-Za-z0-9_][A-Za-z0-9_.-]*))")


def shell_files(root=ROOT):
    """The files in scope, as paths relative to root (see the module docstring)."""
    out = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, check=True).stdout
    files = []
    for rel in out.decode("utf-8", "surrogateescape").split("\0"):
        if not rel:
            continue
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            continue
        if rel.endswith(".sh") or (rel.startswith("tests/") and rel.endswith(".bash")):
            files.append(rel)
            continue
        with open(path, "rb") as f:
            first = f.readline(256)
        if SHEBANG.match(first.decode("utf-8", "replace")):
            files.append(rel)
    return files


def bash32_scan(text):
    """Walk text the way bash 3.2 scans the inside of a $( ... ) for its closing paren.

    Returns (quote, depth, lowest): the quote the scan is inside when the text ends (None when it
    is not), the net parenthesis depth, and the lowest depth reached (below zero means a stray `)`
    would have closed the substitution early). Text bash 3.2 can see straight through returns
    (None, 0, 0).
    """
    quote = None
    depth = lowest = 0
    prev = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote == "'":
            if ch == "'":
                quote = None
        elif quote == "ansi":                       # $'...' takes backslash escapes
            if ch == "\\":
                i += 1
            elif ch == "'":
                quote = None
        elif quote in ('"', "`"):
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = None
        else:
            if ch == "\\":
                i += 1
            elif ch == "'":
                quote = "ansi" if prev == "$" else "'"
            elif ch in '"`':
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                lowest = min(lowest, depth)
        prev = ch
        i += 1
    return quote, depth, lowest


def diagnose(text, what):
    """Why bash 3.2 loses the closing paren over this text, or None when it reads straight through."""
    quote, depth, lowest = bash32_scan(text)
    if quote is not None:
        if quote in ("'", "ansi"):
            counted = text.count("'")
            noun = "apostrophe" if counted == 1 else "apostrophes"
        else:
            counted, noun = text.count(quote), "of them"
        shown = {"'": "'...'", "ansi": "$'...'", '"': '"..."', "`": "`...`"}[quote]
        return ("the %s reads as shell to bash 3.2 and leaves its scan inside a %s string (%d %s), so the "
                "substitution's closing paren is never found" % (what, shown, counted, noun))
    if lowest < 0:
        return ("the %s reads as shell to bash 3.2 and a stray `)` in it closes the substitution early"
                % what)
    if depth:
        return ("the %s reads as shell to bash 3.2 and leaves %d parenthesis unclosed, so the "
                "substitution's closing paren is taken for its own" % (what, depth))
    return None


class Finding:
    """One heredoc or comment that bash 3.2 reads as shell inside a $( ... )."""

    def __init__(self, path, line, kind, op, why):
        self.path, self.line, self.kind, self.op, self.why = path, line, kind, op, why

    @property
    def breaks(self):
        return self.why is not None

    def __str__(self):
        if self.kind == "heredoc":
            why = self.why or "its body balances today, so bash 3.2 parses it by accident"
            return ("%s:%d: heredoc %s opened inside $( ... ): %s; put the heredoc in a function and "
                    "substitute the call" % (self.path, self.line, self.op, why))
        return ("%s:%d: comment inside $( ... ): %s; balance its quotes and parentheses or move it "
                "out of the substitution" % (self.path, self.line, self.why))

    __repr__ = __str__


def findings_in(text, path="<text>"):
    """Every heredoc opened inside an unclosed $( ... ), and every comment there that bash 3.2's scan
    cannot see through, in file order.

    The walk knows what bash 4+ knows: quotes (including $'...'), backslashes, backticks as opaque
    spans, `$(`, `$((`, comments, heredoc bodies. Parentheses are tracked only while a substitution
    is open, since a top-level `)` may be a case pattern; `(( ... ))` is arithmetic, where `<<` shifts.
    """
    lines = text.split("\n")
    stack = []           # 'sq' 'ansi' 'dq' 'bt' 'subst' 'arith' 'paren'
    found = []
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        lineno = i + 1
        pending = []     # heredocs this line opens: (op text, tag, strip tabs, inside a $( ... ))
        prev = ""
        j = 0
        while j < len(line):
            ch = line[j]
            top = stack[-1] if stack else None
            if top == "sq":
                if ch == "'":
                    stack.pop()
                prev, j = ch, j + 1
                continue
            if top in ("ansi", "bt"):
                if ch == "\\":
                    prev, j = "", j + 2
                    continue
                if ch == ("'" if top == "ansi" else "`"):
                    stack.pop()
                prev, j = ch, j + 1
                continue
            if top == "dq":
                if ch == "\\":
                    prev, j = "", j + 2
                    continue
                if ch == '"':
                    stack.pop()
                elif ch == "$" and line.startswith("$((", j):
                    stack.append("arith")
                    prev, j = "", j + 3
                    continue
                elif ch == "$" and line.startswith("$(", j):
                    stack.append("subst")
                    prev, j = "", j + 2
                    continue
                elif ch == "`":
                    stack.append("bt")
                prev, j = ch, j + 1
                continue
            # unquoted shell text: top level, or inside a substitution, arithmetic or group
            if ch == "\\":
                prev, j = "", j + 2
                continue
            if ch == "'":
                stack.append("ansi" if prev == "$" else "sq")
                prev, j = ch, j + 1
                continue
            if ch == '"' or ch == "`":
                stack.append("dq" if ch == '"' else "bt")
                prev, j = ch, j + 1
                continue
            if ch == "$" and line.startswith("$((", j):
                stack.append("arith")
                prev, j = "", j + 3
                continue
            if ch == "$" and line.startswith("$(", j):
                stack.append("subst")
                prev, j = "", j + 2
                continue
            if ch == "(" and line.startswith("((", j):     # a bare (( ... )) arithmetic command
                stack.append("arith")
                prev, j = "", j + 2
                continue
            if ch == "(":
                if stack:
                    stack.append("paren")
                prev, j = ch, j + 1
                continue
            if ch == ")":
                if top == "arith":
                    if line.startswith("))", j):
                        stack.pop()
                        prev, j = ch, j + 2
                        continue
                elif top in ("paren", "subst"):
                    stack.pop()
                prev, j = ch, j + 1
                continue
            innermost = next((c for c in reversed(stack) if c in ("subst", "arith")), None)
            if ch == "#" and (j == 0 or line[j - 1] in " \t;|&("):
                if innermost == "subst":
                    why = diagnose(line[j:], "comment")
                    if why:
                        found.append(Finding(path, lineno, "comment", None, why))
                break                                # the rest of the line is a comment
            if ch == "<" and line.startswith("<<<", j):
                prev, j = "", j + 3                  # a here-string
                continue
            if ch == "<" and line.startswith("<<", j) and innermost != "arith":
                m = HEREDOC_OP.match(line, j)
                if m:
                    tag = next(g for g in m.groups()[1:] if g is not None)
                    pending.append((m.group(0), tag, bool(m.group(1)), innermost == "subst"))
                    prev, j = "", m.end()
                    continue
            prev, j = ch, j + 1
        i += 1
        for op, tag, strip_tabs, inside in pending:
            body = []
            while i < n:
                candidate = lines[i]
                i += 1
                if (candidate.lstrip("\t") if strip_tabs else candidate) == tag:
                    break
                body.append(candidate)
            if inside:
                found.append(Finding(path, lineno, "heredoc", op, diagnose("\n".join(body), "body")))
    return found


def scan_tree(files, root=ROOT):
    found = []
    for rel in files:
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as f:
            found.extend(findings_in(f.read(), rel))
    return found


def find_bash32():
    """A real bash 3.2 on this machine, or None. ROMP_BASH32 names one explicitly, and a name that
    cannot run, or runs some other version, is a loud failure rather than a skip: a mistyped path
    must never turn the real-3.2 check into a quiet pass (the review of this change caught the
    first cut skipping on a nonexistent ROMP_BASH32 while failing only on a wrong version)."""
    named = os.environ.get("ROMP_BASH32")
    if named:
        try:
            ver = subprocess.run([named, "--version"], capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError) as e:
            raise AssertionError("ROMP_BASH32=%s cannot be run: %s" % (named, e))
        if not re.match(r"GNU bash, version 3\.2\b", ver):
            raise AssertionError("ROMP_BASH32=%s is not a bash 3.2: %s" % (named, ver.splitlines()[:1]))
        return named
    for cand in [shutil.which(n) for n in ("bash-3.2", "bash3.2", "bash32", "bash3")] + ["/bin/bash"]:
        if not cand or not os.path.exists(cand):
            continue
        try:
            ver = subprocess.run([cand, "--version"], capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if re.match(r"GNU bash, version 3\.2\b", ver):
            return cand
    return None


# The hook's shape before the fix: a heredoc opened inside the substitution, three apostrophes in
# the body's comments. Synthetic, mirroring the real Stop arm.
DEFECT = '''\
excerpt=$(python3 - "$transcript" <<'PY' 2>/dev/null || true
import sys
# tool_result user messages carry no text and are ignored (don't reset).
# The assistant's prose this turn describes the outcome.
# Haiku's phrasing is consistent.
print("ok")
PY
)
echo "$excerpt"
'''
# The same Python in a function; only the call is substituted.
FIXED = '''\
_turn_excerpt() {
  python3 - "$transcript" <<'PY'
import sys
# tool_result user messages carry no text and are ignored (don't reset).
# The assistant's prose this turn describes the outcome.
# Haiku's phrasing is consistent.
print("ok")
PY
}
excerpt=$(_turn_excerpt 2>/dev/null || true)
echo "$excerpt"
'''
# The lab's port probe before the fix: the shape, with a body that happens to balance.
BALANCED = '''\
PORT=$(python3 - <<'PY'
import socket
s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()
PY
)
echo "$PORT"
'''


class Scanner(unittest.TestCase):
    def test_the_defect_shape_is_flagged_as_a_break(self):
        found = findings_in(DEFECT, "hook.sh")
        self.assertEqual([(f.line, f.kind, f.op) for f in found], [(1, "heredoc", "<<'PY'")])
        self.assertTrue(found[0].breaks)
        self.assertIn("hook.sh:1: heredoc <<'PY' opened inside $( ... )", str(found[0]))
        self.assertIn("inside a '...' string (3 apostrophes)", str(found[0]))

    def test_the_fixed_shape_is_clean(self):
        self.assertEqual(findings_in(FIXED, "hook.sh"), [])

    def test_a_balanced_body_is_still_the_shape(self):
        found = findings_in(BALANCED, "lab.sh")
        self.assertEqual([(f.line, f.kind, f.op) for f in found], [(1, "heredoc", "<<'PY'")])
        self.assertFalse(found[0].breaks)
        self.assertIn("balances today, so bash 3.2 parses it by accident", str(found[0]))

    def test_the_tag_quoting_does_not_matter_and_the_terminator_is_honored(self):
        for op in ("<<PY", '<<"PY"', "<<-PY", "<<\\PY"):
            text = DEFECT.replace("<<'PY'", op)
            if op == "<<-PY":
                text = text.replace("\nPY\n", "\n\t\tPY\n")     # <<- strips leading tabs
            # a top-level heredoc after it, odd apostrophes and all, is not flagged: proof the
            # scanner resumed at the right line
            text += "cat <<'EOF'\nit's fine here\nEOF\n"
            found = findings_in(text, "t.sh")
            self.assertEqual([(f.line, f.op) for f in found], [(1, op)], op)
            self.assertTrue(found[0].breaks, op)

    def test_quotes_inside_the_other_kind_of_quote_are_literal(self):
        body_ok = DEFECT.replace("# tool_result user messages carry no text and are ignored (don't reset).\n"
                                 "# The assistant's prose this turn describes the outcome.\n"
                                 "# Haiku's phrasing is consistent.\n",
                                 "print(\"don't\")\nprint('say \"hi')\necho it\\'s\n")
        found = findings_in(body_ok, "t.sh")
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0].breaks, str(found[0]))
        # and a stray paren in the body is its own break
        found = findings_in(BALANCED.replace("s.close()", "s.close())"), "t.sh")
        self.assertTrue(found[0].breaks)
        self.assertIn("stray `)`", str(found[0]))

    def test_look_alikes_are_not_flagged(self):
        clean = [
            'x=$(tr a-z A-Z <<<"it\'s")\n',                               # a here-string
            "cat <<EOF\nit's a top-level heredoc\nEOF\n",                 # no substitution open
            "( cat <<'EOF'\nit's inside a plain subshell\nEOF\n) &\n",   # ( ... ) is not $( ... )
            "n=$(( x << 2 ))\n",                                          # an arithmetic shift
            "(( n <<= 1 ))\n(( m << 2 )) && cat <<'EOF'\nit's\nEOF\n",     # bare arithmetic, then a real heredoc
            'x=$(echo "${#arr[@]}")\n',                                   # ${#x} is not a comment
            "x=$(echo a) # don't\n",                                      # a comment after the close
            "y=\"$(printf '%s' \"$v\" | tr -d ')')\"\necho \"$y\"\n",     # a paren inside quotes
        ]
        for text in clean:
            self.assertEqual(findings_in(text, "t.sh"), [], text)

    def test_a_comment_inside_a_substitution_must_stay_neutral(self):
        found = findings_in("x=$(\n  # don't\n  echo hi\n)\n", "t.sh")
        self.assertEqual([(f.line, f.kind) for f in found], [(2, "comment")])
        self.assertIn("t.sh:2: comment inside $( ... ): the comment reads as shell", str(found[0]))
        self.assertEqual(findings_in('x=$(\n  # fine (balanced) "yes"\n  echo hi\n)\n', "t.sh"), [])
        self.assertEqual(findings_in("# don't, at top level\nx=$(echo hi)\n", "t.sh"), [])

    def test_a_substitution_inside_double_quotes_counts(self):
        text = "_p=\"$(python3 - \"$#\" <<'PY'\nprint('x')  # don't\nPY\n)\"\n"
        found = findings_in(text, "helper.bash")
        self.assertEqual([(f.line, f.kind, f.breaks) for f in found], [(1, "heredoc", True)])


class Tree(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = shell_files()

    def test_scope_reaches_the_files_the_rule_is_for(self):
        for rel in ("hooks/romp-summarize.sh", "bin/romp", "bin/romp-node-launch", "install.sh",
                    "tools/romp-lab/lab.sh", "tests/free-port.bash"):
            self.assertIn(rel, self.files)
        self.assertEqual([f for f in self.files if f.endswith(".bats")], [])
        self.assertEqual([f for f in self.files if f.endswith(".py")], [])

    def test_no_heredoc_or_comment_reads_as_shell_inside_a_substitution(self):
        found = scan_tree(self.files)
        self.assertFalse(found, "\n" + "\n".join(str(f) for f in found))

    def test_bash_n_accepts_every_file(self):
        failed = []
        for rel in self.files:
            r = subprocess.run(["bash", "-n", os.path.join(ROOT, rel)], capture_output=True, text=True,
                               timeout=60)
            if r.returncode != 0:
                failed.append("%s: %s" % (rel, r.stderr.strip()))
        self.assertFalse(failed, "\n" + "\n".join(failed))


class Bash32Finder(unittest.TestCase):
    """ROMP_BASH32 names the real bash 3.2 explicitly. A name that is wrong in ANY way fails the class
    loudly: a mistyped path, a binary that will not run, or another version. Skipping there would
    turn the one real-3.2 gate into a quiet pass on the very machine set up to run it."""

    def _fake_bash(self, version_line):
        d = tempfile.mkdtemp(prefix="romp-bash32-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "bash")
        with open(path, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\necho '%s'\n" % version_line)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def test_a_nonexistent_named_bash_fails_loudly(self):
        with mock.patch.dict(os.environ, {"ROMP_BASH32": "/nonexistent/romp-bash-3.2"}):
            with self.assertRaisesRegex(AssertionError, "cannot be run"):
                find_bash32()

    def test_a_named_bash_of_another_version_fails_loudly(self):
        with mock.patch.dict(os.environ, {"ROMP_BASH32": self._fake_bash("GNU bash, version 5.9.0(1)-release")}):
            with self.assertRaisesRegex(AssertionError, "is not a bash 3.2"):
                find_bash32()

    def test_a_named_bash_3_2_is_taken_as_is(self):
        fake = self._fake_bash("GNU bash, version 3.2.57(1)-release (x86_64-apple-darwin)")
        with mock.patch.dict(os.environ, {"ROMP_BASH32": fake}):
            self.assertEqual(find_bash32(), fake)


class RealBash32(unittest.TestCase):
    """Executed only where a real bash 3.2 exists; never imitated."""

    def setUp(self):
        self.bash32 = find_bash32()
        if not self.bash32:
            self.skipTest("no bash 3.2 on this machine (ROMP_BASH32=/path/to/one runs these)")

    def test_the_defect_shape_really_fails_to_parse_and_the_fix_parses(self):
        broken = subprocess.run([self.bash32, "-n"], input=DEFECT, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(broken.returncode, 0, "bash 3.2 accepted the defect shape: " + broken.stderr)
        fixed = subprocess.run([self.bash32, "-n"], input=FIXED, capture_output=True, text=True, timeout=60)
        self.assertEqual(fixed.returncode, 0, fixed.stderr)

    def test_every_file_in_scope_parses(self):
        failed = []
        for rel in shell_files():
            r = subprocess.run([self.bash32, "-n", os.path.join(ROOT, rel)], capture_output=True, text=True,
                               timeout=60)
            if r.returncode != 0:
                failed.append("%s: %s" % (rel, r.stderr.strip()))
        self.assertFalse(failed, "\n" + "\n".join(failed))


if __name__ == "__main__":
    unittest.main()
