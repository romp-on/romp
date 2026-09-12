#!/usr/bin/env python3
"""The feed memo's input census (T368).

build_feed serves each living session's cards from _feed_memo while _feed_session_key(s, ...) is unchanged, so the
key must contain every input _feed_session_entry reads that can change the entry. This module is the census that
argument rests on, in the shape of tests/test_chat_build_sig_inputs.py: HELPERS classifies every callee the body
reads from module scope (the derivation rule below), MODULE_READS every module-scope name the body reads WITHOUT
calling it (the read rule below), CTX every field the body reads from the build's context dict, and LOCAL the
closures defined inside the body. Each HELPERS or CTX entry is one of:

  sig    an input the key folds, under the named labels (_FEED_MEMO_LABELS; several when the helper reads
         under more than one component); the first label is the one its main read rides;
  pure   a function of inputs already classified (a store the key identifies, identities another helper
         resolved) and nothing else; the note says of what;
  clock  the build's clock, handed to helpers whose clock-derived outputs LEAVE the entry (a placeholder's
         `t` and every card's age tint are stamped per build by the fold): tests/test_feed_session_memo.py's
         clock case pins that two builds apart in time differ in those fields alone.

Each MODULE_READS entry is one of:

  const  a module constant that never changes at runtime (an uppercase name bound once: a tuple or frozenset of
         strings, an int, a compiled regex, a class such as Path, a module alias); the note says what it is, and
         the test checks the value is of an immutable type;
  sig    a runtime table or module state whose content can change the entry, labelled under the key component
         that covers it, exactly as HELPERS does;
  pure   a read that cannot change the card, with a note saying why.

THE CALL CENSUS. The test derives the call set from the function's AST, every ast.Call in the body (nested defs,
lambdas, comprehensions and f-strings included), and requires it to EQUAL the tables' keys, so a helper added to
the body without a classification fails the suite by name (a future read must be labelled), and so does a stale
entry for one removed. The rule: a callee is reduced to its ROOT by walking down through attribute, subscript and
call nodes (`a.b`, `a[i]`, `a(...)`) until a node that is none of those, and is read under the dotted name of the
root joined with the attributes that follow it up to the first subscript or call: `_seg_key`,
`jd.load_goals_shared_or_fault`, `os.path.basename` (every attribute of a module chain), and `_auto_nudge_data`
for `_auto_nudge_data().get(...)` (the `.get` is a method on the value the classified call returned). A root that
is a Name is one of three things. A name the body binds (a parameter of the def, of a nested def or of a lambda;
an assignment, for, with, walrus or comprehension target; an except handler's name; a nested def's name) is a
LOCAL: a callee rooted in it (`tm.get`, `out.append`) is skipped, except a nested def's own call, which must be in
LOCAL. A name the kernel does not bind and the builtins module does is a builtin (`len`, `sorted`, `str`,
`dict.fromkeys`) and is skipped, `open` excepted: the one builtin that reads a file is classified like any helper.
Every other Name is a module-level name of the kernel (a helper, a module alias such as jd, em, cm, sb, os or
json, a class such as Sessions) and its dotted name MUST be in HELPERS, so nothing rooted in module scope passes
unlabelled, whatever the shape of the call. A root that is not a Name (a literal's method, `", ".join`; a
bool-op's, `(nd.get("x") or {}).get`; a path expression's, `(jd.STATE / "x.json").read_text`) is a method on a
value an expression produced: the calls inside that expression are walked on their own, and the method call is
subject to the EXPRESSION-METHOD RULE: every module-scope Name inside the callee expression that is not itself a
counted callee's root is a read (the read rule below counts it), and when there is at least one such read the
method call is reported as the first such read's dotted name (source order) joined with the method,
`jd.STATE.read_text` for `(jd.STATE / "x.json").read_text()`, and MUST be in HELPERS, since the method is what
reads (a Path constant reads nothing; its read_text reads a file). A method on an expression with no such read
inside (a local's, a literal's, a bool-op over locals, a classified call's value, `(_helper() or {}).get`) names
nothing of module scope that the census has not already counted, and is skipped.

THE READ CENSUS. Every ast.Name in Load context under the body is classified by the same three-way rule (a body
local skipped, a builtin the kernel does not bind skipped, `open` excepted), and every module-scope Name that is
NOT the root of a counted callee chain is a READ, under a dotted name: the Name alone when it is read bare or
subscripted (`_NEEDS_INPUT_STATES` for `perm_state in _NEEDS_INPUT_STATES`, `_node_anchor_rev` for
`_node_anchor_rev[fsid]`), or the Name joined with its first attribute for an attribute load (`jd.CITE_MIN_CHARS`,
`jd.STATE` for `jd.STATE / "x.json"`). The read set must EQUAL MODULE_READS' keys, as the call set must EQUAL
HELPERS plus LOCAL, so a module table read by subscript, a constant compared against, or a path built from a
module directory is labelled by name or fails. The only skips in either census are builtins and body locals:
there is no list of names or modules exempted.

Further pins: every `sig` label exists in the key's label tuple; every label in the tuple is claimed by some read
(no dead component); every LOCAL name is a def nested in the body; every HELPERS name resolves in the kernel to a
callable and every MODULE_READS name to a value; a `const` read's value is of an immutable type; the label tuple
matches the list the key builder's docstring documents, in order; the miss attribution map covers the labels plus
`cold`. Names, not lines: the tables say what each read is, the key builder's docstring says how each component
is taken. The census enforces ONE level, the helpers the body calls directly and the module names it reads
directly; what each reads in turn is the classification's claim, verified by the differential cases in
tests/test_feed_session_memo.py.
"""
import ast
import builtins
import inspect
import io
import os
import pathlib
import re
import tempfile
import textwrap
import tokenize
import types
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
# Hermetic state BEFORE the load: the kernel resolves its state root at import time (a bare unittest run
# otherwise reads REAL state); this module only reads source, but the load is the same load.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_feed_memo_inputs", os.path.join(BIN, "romp-kernel"))

# ── the helpers the body calls, helper -> (kind, labels or note) ──────────────────────────────────────────
HELPERS = {
    # the transcript (the cache-only parse, the anchors, the api-error tail, the background scans)
    "_api_error": ("sig", ("transcript",)),
    "_atom_prose_chars": ("sig", ("transcript",)),
    "_last_plain_user_turn_t": ("sig", ("transcript",)),
    "_open_turn_progress": ("sig", ("transcript",)),
    "_pure_delegation_top": ("sig", ("transcript",)),
    "_seg_anchors": ("sig", ("transcript",)),
    "_seg_jump": ("sig", ("transcript",)),
    "_seg_key": ("sig", ("transcript",)),
    "_seg_last_text": ("sig", ("transcript",)),
    "_segs_seam": ("sig", ("transcript",)),
    "em.turn_scalar": ("sig", ("transcript",)),
    "jd._prompt_anchor_uuid": ("sig", ("transcript",)),
    "_heal_session_tops": ("sig", ("transcript", "store")),            # the background scan over the store's tops
    "_warm_wanted": ("sig", ("transcript", "parse")),                  # moved since boot, or working: worth a warm
    # the placeholders: their own _parse, the caption gist, the clear set, the current ask
    "_provisional_card": ("sig", ("transcript", "captions", "cleared")),
    "_blocked_placeholder": ("sig", ("transcript", "captions", "ask")),
    "_awaiting_card": ("sig", ("transcript",)),
    # the states log (machine cuts, the retrying-since fold, the awaiting overlay) and the live row
    "_interrupt_suppresses_nudge": ("sig", ("states",)),
    "_session_retrying": ("sig", ("states", "row")),
    "_session_awaiting": ("sig", ("states", "reg", "row", "bg", "watch", "subagents", "peers")),
    # the names registry (own sid, and the peers a card names)
    "_name_color": ("sig", ("names", "peers")),
    "_name_of": ("sig", ("names", "peers")),
    # the goal store (every node and status read of ctx["store"], and the shared loads inside the bg memos)
    "_agent_open_set": ("sig", ("store",)),
    "_all_outstanding_delegated": ("sig", ("store",)),
    "_goal_awaiting_stamp_full": ("sig", ("store",)),
    "_node_log_rows": ("sig", ("store",)),
    "_open_leaves": ("sig", ("store",)),
    "_parked_rows": ("sig", ("store",)),
    "_session_started_face": ("sig", ("store",)),
    "jd._done_since": ("sig", ("store",)),
    "jd.review_boundary": ("sig", ("store",)),
    "_handoff_card_fields": ("sig", ("store", "peers")),
    "_bg_owner_tops": ("sig", ("transcript", "store", "reg", "bg")),
    "_bg_service_descs": ("sig", ("transcript", "store", "bg", "postal")),
    "_awaiting_task_descs": ("sig", ("transcript", "store", "bg")),
    "_bg_live_norm": ("sig", ("bg", "reg", "row", "transcript")),      # the launch ledger, the row's bgTasks
    # the warm-anchor table, the postal log, the nudge records, the flags, the debug rows, the cap offer
    "_node_anchor_uuids": ("sig", ("anchors",)),
    "_peer_answered": ("sig", ("postal",)),
    "_peer_identity": ("sig", ("postal", "peers")),
    "_handoff_peer_identities": ("sig", ("peers",)),
    "jd.load_goals_shared_or_fault": ("sig", ("peers",)),               # an origin sender's store, by the peers deps
    "_auto_nudge_data": ("sig", ("nudge",)),
    "_nudge_times": ("sig", ("nudge",)),
    "_session_flag": ("sig", ("hide",)),
    "_card_warn_rows": ("sig", ("debug",)),
    "_cap_switch_offer": ("sig", ("row", "usage", "offer", "auth")),   # authLive, usage.json (recorded), its cap window's crossing, the key on hand
    # pure over classified inputs
    "_awaiting_peer_items": ("pure", "over the peer identities _session_awaiting resolved (peers), nothing else"),
    "_login_refusal_label": ("pure", "over the live row's authLogin, authLoginLive and authLabel (row) and the api error (transcript): a stored login the session's API error refused, by label (T346)"),
    "lg.mark_refused": ("pure", "a WRITE, not a read: the login registry's refused mark for the login the api error named (idempotent); its return enters nothing, and the label the card shows is the row's (T346)"),
}

# ── the module-scope names the body reads without calling, name -> (kind, labels or note) ─────────────────
MODULE_READS = {
    "_NEEDS_INPUT_STATES": ("const", "the live-prompt perm states (permission, picker), a tuple of strings bound once"),
    "jd.CITE_MIN_CHARS": ("const", "the judge module's citation floor, an int bound once"),
    "jd.WHY_IN_FLIGHT": ("const", "the in-flight-class stall reasons, a tuple of the judge module's constant strings"),
    "jd.STATE": ("const", "the state root, a Path bound once at import (handed to the registry write above; the tests rebind it whole)"),
}

# ── the context fields the body reads, field -> (kind, labels or note) ────────────────────────────────────
CTX = {
    "now": ("clock", "handed to the placeholders and the stamp reads; every clock-derived output leaves the entry"),
    "live_map": ("sig", ("row",)),                     # tm = live_map.get(fsid): live, perm_state, since
    "cleared": ("sig", ("cleared",)),                  # `nid in cleared` per top (the session's slice)
    "dbg_rows": ("sig", ("debug",)),
    "wmap": ("sig", ("wait",)),
    "stalls": ("sig", ("stalls",)),
    "jauth_map": ("sig", ("jauth",)),
    "jactive": ("sig", ("jactive",)),
    "ps": ("sig", ("parse", "transcript", "live", "cut", "states")),   # the cache-only, live-merged parse
    "who_working": ("sig", ("downtime", "parse")),     # _session_working over the open turn, suspension-aware
    "interrupting": ("sig", ("interrupting",)),
    "store": ("sig", ("store",)),                      # _feed_goals(fsid), read once in the key
    "closer": ("sig", ("closer", "jactive")),          # the settle gap under the body's gate
}

# closures defined inside the body: pure over its locals, no component
LOCAL = ("_subtree", "_closure_done", "_fsubmax", "_closure_blocked", "_block_check_floor", "_note_peers", "flatten")

KINDS = {"sig", "pure", "clock"}          # HELPERS and CTX
READ_KINDS = {"const", "sig", "pure"}     # MODULE_READS
# the value types a `const` read may hold: bound once, never mutated in place
CONST_TYPES = (int, float, str, bytes, bool, tuple, frozenset, re.Pattern, type, types.ModuleType, pathlib.PurePath,
               type(None))


def _stripped(src, strings=False):
    """The source with comments removed and, unless `strings`, every string literal replaced by an empty one,
    so a mention of a helper in a comment or a message is not counted as a call (the ctx census keeps the
    literals: the field names are the strings)."""
    toks = []
    for t in tokenize.generate_tokens(io.StringIO(src).readline):
        if t.type == tokenize.COMMENT:
            continue
        if t.type == tokenize.STRING and not strings:
            t = t._replace(string='""')
        toks.append(t)
    return tokenize.untokenize(toks)


def _callee_root(func):
    """A callee reduced to its root (the module docstring's rule): walks down through Attribute, Subscript and Call
    nodes until a node that is none of those, and returns (root node, dotted name), the name the root's id joined
    with the attributes that follow it up to the first Subscript or Call (`os.path.basename`; `_auto_nudge_data` for
    `_auto_nudge_data().get`), or None when the root is not a Name (a literal, a bool-op, a path expression: a
    method on a value)."""
    attrs, f = [], func
    while True:
        if isinstance(f, ast.Attribute):
            attrs.append(f.attr)
            f = f.value
        elif isinstance(f, ast.Subscript):
            attrs, f = [], f.value
        elif isinstance(f, ast.Call):
            attrs, f = [], f.func
        else:
            break
    return f, (".".join([f.id] + attrs[::-1]) if isinstance(f, ast.Name) else None)


def _bound_in(fn):
    """Every name the body binds, at any depth: the parameters of the def, of a nested def and of a lambda (every
    ast.arg), every Store-context Name (assignment, augmented assignment, for, with, walrus and comprehension
    targets, unpacking included), an except handler's name, an import alias, a nested def's or class's name. A name
    the body declares `global` is not bound here."""
    names = set()
    for x in ast.walk(fn):
        if isinstance(x, ast.arg):
            names.add(x.arg)
        elif isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store):
            names.add(x.id)
        elif isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and x is not fn:
            names.add(x.name)
        elif isinstance(x, ast.ExceptHandler) and x.name:
            names.add(x.name)
        elif isinstance(x, ast.alias):
            names.add((x.asname or x.name).split(".")[0])
    for x in ast.walk(fn):
        if isinstance(x, ast.Global):
            names.difference_update(x.names)
    return names


def _read_name(node, parent):
    """A read's dotted name: the Name joined with its first attribute when it is the value of an Attribute load
    (`jd.CITE_MIN_CHARS`, `jd.STATE`), else the Name alone (a bare compare, a subscript's value, an operand)."""
    if isinstance(parent, ast.Attribute) and parent.value is node:
        return node.id + "." + parent.attr
    return node.id


def _census(fn, module):
    """(calls, reads) for the body: the callees it reads from module scope plus the nested defs it calls, and the
    module-scope names it reads without calling, both as dotted names by the module docstring's rules. `module` is
    the kernel: a name it binds is never a builtin, however it is spelled. One three-way rule classifies every
    Name: a body local (skipped; a nested def's own call is kept for LOCAL), a builtin the module does not bind
    (skipped, `open` excepted), else module scope (kept). The call census walks every ast.Call; a callee whose
    root is a Name is counted under _callee_root's dotted name and that Name node is a callee root; a callee that
    is an Attribute on an expression (no Name root) is an expression-method call. The read census then counts
    every Load-context Name of module scope that is not a callee root, under _read_name. Each expression-method
    call whose callee expression holds at least one such read is counted as the first read's dotted name (source
    order) joined with the method (`jd.STATE.read_text`); one holding none is a method on a value the census has
    already accounted for and is skipped."""
    local, nested = _bound_in(fn), {x.name for x in ast.walk(fn) if isinstance(x, ast.FunctionDef) and x is not fn}
    bound = vars(module)

    def scoped(name):                             # module scope: neither a body local nor an unshadowed builtin
        return name not in local and not (name not in bound and hasattr(builtins, name) and name != "open")

    parent = {}
    for p in ast.walk(fn):
        for c in ast.iter_child_nodes(p):
            parent[c] = p
    calls, roots, on_exprs = set(), set(), []
    for x in ast.walk(fn):
        if not isinstance(x, ast.Call):
            continue
        root, name = _callee_root(x.func)
        if name is None:
            if isinstance(x.func, ast.Attribute):
                on_exprs.append(x)                # a method on a value an expression produced: ruled on below
            continue
        roots.add(root)
        if root.id in nested:
            calls.add(name)                       # a closure's call: LOCAL must name it
        elif scoped(root.id):
            calls.add(name)                       # rooted in module scope: HELPERS must name it

    def read_nodes(under):
        return [n for n in ast.walk(under)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n not in roots and scoped(n.id)]

    reads = {_read_name(n, parent[n]) for n in read_nodes(fn)}
    for x in on_exprs:
        inner = read_nodes(x.func)
        if inner:                                 # the method reads on a module-scope value: HELPERS must name it
            first = min(inner, key=lambda n: (n.lineno, n.col_offset))
            calls.add(_read_name(first, parent[first]) + "." + x.func.attr)
    return calls, reads


def _calls_of(fn, module):
    return _census(fn, module)[0]


def _reads_of(fn, module):
    return _census(fn, module)[1]


def _ctx_reads_of(src):
    return set(re.findall(r'ctx\["([a-z_]+)"\]', _stripped(src, strings=True)))


def _documented_labels():
    """The component list the key builder's docstring documents: the `label:` lines of its Components block, at
    the block's own indent, in order."""
    doc = inspect.cleandoc(km._feed_session_key.__doc__)
    block = doc.split("Components, label:", 1)[1].split("NOT components", 1)[0]
    rows = [(len(m.group(1)), m.group(2)) for m in re.finditer(r"^( +)([a-z]+): ", block, re.M)]
    indent = min(i for i, _ in rows)
    return tuple(lab for i, lab in rows if i == indent)


def _resolve(name):
    """(object, found): what a dotted name reaches from the kernel's namespace (or, for a classified builtin such
    as `open`, from builtins), attribute by attribute; found is False when a step is missing."""
    root, *attrs = name.split(".")
    missing = object()
    obj = getattr(km, root) if root in vars(km) else getattr(builtins, root, missing)
    for a in attrs:
        obj = getattr(obj, a, missing) if obj is not missing else missing
    return (None if obj is missing else obj), obj is not missing


class Census(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = inspect.getsource(km._feed_session_entry)
        cls.fn = ast.parse(textwrap.dedent(cls.src)).body[0]
        cls.calls, cls.reads = _census(cls.fn, km)
        cls.ctx = _ctx_reads_of(cls.src)

    def test_every_helper_the_body_calls_is_classified_and_nothing_stale_remains(self):
        table = set(HELPERS) | set(LOCAL)
        self.assertEqual(self.calls, table,
                         "a helper _feed_session_entry calls is missing from HELPERS (an unclassified read: label it "
                         "under the key component that covers it, or add the component), or the table names one the "
                         "body no longer calls: %r" % sorted(self.calls ^ table))

    def test_every_module_name_the_body_reads_is_classified_and_nothing_stale_remains(self):
        self.assertEqual(self.reads, set(MODULE_READS),
                         "a module-scope name _feed_session_entry reads without calling is missing from MODULE_READS "
                         "(a constant: const, with what it is; a runtime table or module state: sig, under the key "
                         "component that covers it), or the table names one the body no longer reads: %r"
                         % sorted(self.reads ^ set(MODULE_READS)))

    def test_every_context_field_the_body_reads_is_classified(self):
        self.assertEqual(self.ctx, set(CTX), sorted(self.ctx ^ set(CTX)))

    def test_every_entry_has_a_known_kind_and_a_sig_entry_names_labels_of_the_key(self):
        labels = set(km._FEED_MEMO_LABELS)
        for table, kinds in ((HELPERS, KINDS), (CTX, KINDS), (MODULE_READS, READ_KINDS)):
            for name, (kind, what) in table.items():
                self.assertIn(kind, kinds, name)
                if kind == "sig":
                    self.assertIsInstance(what, tuple, name)
                    self.assertTrue(what, "%s: a sig entry names at least one label" % name)
                    for lab in what:
                        self.assertIn(lab, labels, "%s: %r is not a component of _feed_session_key" % (name, lab))
                else:
                    self.assertIsInstance(what, str, "%s: a %s entry carries its note" % (name, kind))

    def test_every_component_of_the_key_is_claimed_by_a_read(self):
        claimed = set()
        for table in (HELPERS, CTX, MODULE_READS):
            for kind, what in table.values():
                if kind == "sig":
                    claimed.update(what)
        self.assertEqual(claimed, set(km._FEED_MEMO_LABELS),
                         "a key component no read claims is a dead component (or a read lost its label): %r"
                         % sorted(claimed ^ set(km._FEED_MEMO_LABELS)))

    def test_the_local_closures_are_defs_nested_in_the_body(self):
        fn = self.fn
        nested = {x.name for x in ast.walk(fn) if isinstance(x, ast.FunctionDef) and x is not fn}
        for name in LOCAL:
            self.assertIn(name, nested, "%s is listed as a local closure but the body defines no such def" % name)
        self.assertFalse(set(LOCAL) & set(HELPERS), "a name is a closure or a helper, never both")

    def test_the_helper_names_resolve_in_the_kernel(self):
        """Every dotted name walks from the kernel's namespace (or, for a classified builtin such as `open`, from
        builtins) through each attribute to a callable: `os.path.basename` is `km.os`, then `.path`, then
        `.basename`; an expression-method entry reaches the method on the value, `jd.STATE.read_text`."""
        for name in HELPERS:
            obj, found = _resolve(name)
            self.assertTrue(found and callable(obj), name)

    def test_the_module_read_names_resolve_in_the_kernel_and_a_const_is_immutable(self):
        """Every MODULE_READS name reaches a value the same way (`jd.CITE_MIN_CHARS` is `km.jd`, then
        `.CITE_MIN_CHARS`), and a `const` entry is an uppercase name whose value is of a type that cannot change
        under it: a tuple or frozenset of such values, an int, a string, a regex, a class, a module, a path. A
        dict, list or set read under `const` is a table whose content can move, and belongs under `sig` with a
        label."""
        def immutable(v):
            if isinstance(v, (tuple, frozenset)):
                return all(immutable(i) for i in v)
            return isinstance(v, CONST_TYPES)
        for name, (kind, _what) in MODULE_READS.items():
            obj, found = _resolve(name)
            self.assertTrue(found, "%s does not resolve in the kernel" % name)
            if kind == "const":
                self.assertTrue(immutable(obj), "%s is classified const but holds a %s" % (name, type(obj).__name__))
                self.assertTrue(name.split(".")[-1].isupper(),
                                "%s: a const is an uppercase name (a runtime table reads under sig)" % name)

    def test_the_derivation_sees_every_callee_shape(self):
        """The rule on a synthetic body, so a regression to a narrower reader fails here rather than passing a read
        unlabelled: a module alias chain, a class method, a bare module helper, a builtin that reads (`open`), a
        method on a call's result, a local's method, a nested def, a lambda's parameter, an except name; and the two
        shapes the call census alone missed (T368 round two): a method on a path expression, which yields the read
        `jd.STATE` and the call `jd.STATE.read_text`, and a module table read by subscript, `_TABLE[fsid]`, which
        yields the read `_TABLE` (its `.get` call is counted as today, `_TABLE.get`, and is no read). A bare
        constant compared against is the read `_STATES`; a method on a classified call's value inside a bool-op,
        `(_helper() or {}).get`, holds no read and stays skipped, as a method on a local's value does."""
        src = textwrap.dedent('''
            def body(s, ctx):
                def inner(x):
                    return x
                fsid = s["sid"]
                p = os.path.basename(s["path"])
                be = Sessions.backend_for(p)
                data = json.dumps(_helper().get("k"), sort_keys=True)
                more = (_helper() or {}).get("k2")
                with open(p) as fh:
                    rows = [str(r).strip() for r in fh]
                try:
                    tm = ctx.get("tm")
                except Exception as exc:
                    tm = exc.args
                f = lambda q: q.lower()
                out = ", ".join(sorted(rows, key=f)) + (tm or {}).get("x", "")
                raw = (jd.STATE / "x.json").read_text()
                row = _TABLE[fsid]
                got = _TABLE.get(fsid)
                if tm.get("state") in _STATES:
                    out += raw
                return inner(out), be, data, more, row, got, dict.fromkeys(rows), sb.thing(len(rows))
        ''')
        fn = ast.parse(src).body[0]
        module = type(km)("synthetic")            # binds nothing: every non-local, non-builtin root is module scope
        calls, reads = _census(fn, module)
        self.assertEqual(calls,
                         {"os.path.basename", "Sessions.backend_for", "json.dumps", "_helper", "open", "sb.thing",
                          "inner", "jd.STATE.read_text", "_TABLE.get"})
        self.assertEqual(reads, {"jd.STATE", "_TABLE", "_STATES"})
        self.assertEqual(_calls_of(fn, module), calls)
        self.assertEqual(_reads_of(fn, module), reads)


class LabelsAndDocstring(unittest.TestCase):
    def test_the_label_tuple_matches_the_key_builders_documented_component_list_in_order(self):
        self.assertEqual(km._FEED_MEMO_LABELS, _documented_labels(),
                         "the docstring of _feed_session_key lists every component; the tuple must be that list")

    def test_the_labels_are_distinct_and_the_deps_are_labels(self):
        self.assertEqual(len(set(km._FEED_MEMO_LABELS)), len(km._FEED_MEMO_LABELS))
        for dep in km._FEED_MEMO_DEPS:
            self.assertIn(dep, km._FEED_MEMO_LABELS, dep)
        self.assertEqual(km._FEED_MEMO_LABELS[-1], "peers", "the peers dependency closes the tuple")

    def test_the_miss_attribution_map_covers_every_label_and_cold(self):
        self.assertEqual(set(km._FEED_MEMO_STATS["miss_by"]), set(km._FEED_MEMO_LABELS) | {"cold"})
        self.assertEqual(set(km._feed_memo_report()["miss_by"]), set(km._FEED_MEMO_LABELS) | {"cold"})

    def test_the_key_builder_returns_one_value_per_label(self):
        """A key is compared to the labels by position (_feed_memo_miss zips them), so the return statement must
        hand back exactly as many values as there are labels: pinned on the source, since a build needs a world."""
        src = _stripped(inspect.getsource(km._feed_session_key))
        ret = src.rsplit("return (", 1)[1].rsplit(")", 1)[0]
        names = [n.strip() for n in ret.replace("\n", " ").split(",") if n.strip()]
        self.assertEqual(len(names), len(km._FEED_MEMO_LABELS),
                         "the key's return tuple has %d values for %d labels" % (len(names), len(km._FEED_MEMO_LABELS)))

    def test_the_entry_docstring_names_this_pin(self):
        self.assertIn("test_feed_memo_inputs", km._feed_session_entry.__doc__)


if __name__ == "__main__":
    unittest.main()
