#!/usr/bin/env python3
"""T401 (5a), the stage marks: every thread that can build a pre-cut atom or hydrate a body carries a stage mark, so the
per-stage rows on /perf (asmIndex.materializedByStage, asmCheckpoint.hydratedByStage) never read `none`. The read boot of
2026-09-13 22:01 UTC counted 206 MB hydrated and 100,000 atoms built under `none`: the judge tiers and the request handler
carried no mark, and the tiers' per-session POOL workers carried none even after the tier thread was marked, since a
thread-local does not cross into a pool worker (round two). The census here is the design's proof: it reads every Thread,
Timer and pool construction in kernel.py and judge.py by the ast, resolves each target by SCOPE (the def inside the
enclosing function, else the module-level def: a local helper named like a marked function is never vouched for by its
namesake, round three), decides marked-or-not from the ast (a _stage_marked decorator on that def, a _stage_marked wrapper
at the site, or a _set_stage call in that def's own body), and lists the pure I/O helpers by SITE (file, enclosing function,
target). Hermetic: synthetic fixtures, the kernel and judge loaded against a temp state directory, threads joined explicitly.
The served pin (a real boot over two documented sessions and one real socket request) is tests/test_stage_marks_served.py."""
import ast
import inspect
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
_ROOT = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _ROOT
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.makedirs(os.path.join(_ROOT, "romp"), exist_ok=True)
Path(_ROOT, "romp", "session-hosts").write_text("off\n")      # a fresh state root pins the hosts off (the 2026-09-11 rule)
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_stage_marks", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend", os.path.join(BIN, "romp_sdk_backend.py"))   # the real backend: its push thread is a row here
_DEFAULT_MARK = getattr(km, "_stage_default", None) or km._stage_marked   # the hand-off wrapper; a tree with only the plain wrapper
#                                                                            (round one) reads the pre-marked Codex pin's red through it

em = km.em
KERNEL_DIR = os.path.join(os.path.dirname(HERE), "kernel")
SOURCES = {"kernel.py": open(os.path.join(KERNEL_DIR, "kernel.py"), encoding="utf-8").read(),
           "judge.py": open(os.path.join(KERNEL_DIR, "judge.py"), encoding="utf-8").read()}
BACKEND_SOURCES = {"sdk_backend.py": open(os.path.join(KERNEL_DIR, "sdk_backend.py"), encoding="utf-8").read(),
                   "codex_backend.py": open(os.path.join(KERNEL_DIR, "codex_backend.py"), encoding="utf-8").read()}
BACKEND_CTORS = ("SdkBackend", "CodexBackend")               # the kernel hands these its callbacks (kwargs and positionals)
BUILD_MODULES = ("romp_event_model", "romp_judge", "event_model.py", "judge.py", "romp-event-model", "romp-judge")
# The event model's names a session backend may use without building: per-record readers of ONE raw transcript line (the
# author rule for the boot scan, the command and skill wrappers' regexes, a record's text), none of which parses a session,
# builds an atom or hydrates a body. A backend that uses any other name on its event-model binding (a parse, the lazy
# index, a hydrate) builds directly, and every thread it starts turns unmarked until marked or listed.
EM_READ_ONLY = {"author_of", "is_interrupt_record", "is_skill_load_wrapper", "_record_origin", "_text_of",
                "CMD_WRAP_RE", "COMMAND_NAME_RE", "COMMAND_NAME_ANY_RE", "SKILL_CONTENT_RE"}
_TREES = {}                                                  # module text -> its parsed tree: kernel.py is parsed once per module (item 5)


_SRC_OF = {}                                                 # id(tree) -> its text, for source segments of inline reads


def _parse(text):
    tree = _TREES.get(text)
    if tree is None:
        tree = _TREES[text] = ast.parse(text)
        _SRC_OF[id(tree)] = text
    return tree
SID = "11111111-2222-4333-8444-0000000000d5"
CTORS = ("Thread", "Timer", "ThreadPoolExecutor", "_TimedPool")
POOL_SHAPED = ("ThreadPoolExecutor", "ProcessPoolExecutor", "Executor", "_TimedPool", "Pool")   # any pool-shaped name is a row

# Threads that can build no atom and hydrate no body: pure I/O helpers, keyed by SITE (file, the enclosing function of the
# Thread line, the target's name), each with the reason it is left unmarked. A name that recurs (go, run, work, one, _ask,
# _pf, _gl, _bd) is keyed to its real enclosing function, so a new closure of the same name elsewhere is flagged.
ALLOW = {
    ("kernel.py", "main", "_sdk"): "constructs the SDK backend, whose own work (the boot reconcile, the CLI streams) reads raw records "
                                   "through the backend and never the index or a hydrate; the KERNEL callbacks it hands the backend run "
                                   "on threads of the backend's own, so each is marked at the hand-off or listed in CALLBACK_ALLOW "
                                   "(the callback census below; the 2026-09-14 read boot counted 3,312 builds under `none:` from the "
                                   "backend's push-session thread while this reason said the backend built nothing); two wiring pins "
                                   "hold its Thread line literal",
    ("kernel.py", "main", "_pusher"): "marks inside: push and connect through _push's decorator",
    ("kernel.py", "main", "_jobs_loop"): "marks inside: jobs.<name> through _job_stage",
    ("kernel.py", "main", "_heartbeat"): "WS keepalive frames",
    ("kernel.py", "main", "_parent_watch"): "a pid probe on the spawning manager",
    ("kernel.py", "main", "_update_check_loop"): "the main-drift git probe",
    ("kernel.py", "main", "_ensure_postal_bus"): "starts the postal bus process",
    ("kernel.py", "main", "_tunnel_supervisor"): "ssh tunnel supervision and status",
    ("kernel.py", "_notify_bus_peer", "_revive_postal_bus"): "re-spawns the postal bus process",
    ("kernel.py", "_notify_bus_origin_trust", "_revive_postal_bus"): "re-spawns the postal bus process",
    ("kernel.py", "_spawn_tunnel", "_wake_when_port_up"): "a socket probe on a dialed tunnel",
    ("kernel.py", "do_POST", "_run_main_update"): "a git fast-forward of the checkout",
    ("kernel.py", "do_POST", "_fleet_restart_run"): "the remote half of a fleet restart over the tunnels, then this kernel's own",
    ("kernel.py", "do_POST", "_propagate_judge_settings"): "fans an applied settings body to linked kernels over HTTP",
    ("kernel.py", "_dispatch_ws", "_propagate_judge_settings"): "fans an applied settings body to linked kernels over HTTP",
    ("kernel.py", "_converge_peer_settings", "_push_settings_to_peer"): "peer HTTP with a settings body",
    ("kernel.py", "_login_start", "_login_reader"): "drives the login CLI's output for states",
    ("kernel.py", "_new_ws_client", "_ws_sender"): "the WS frame writer",
    ("kernel.py", "_commands_for_cwd", "_do_warm_commands"): "lists the CLI's slash commands into the commands cache",
    ("kernel.py", "_first_cycle_sampler_start", "_first_cycle_sampler_run"): "the first-pass stack sampler: reads frames, builds nothing",
    ("kernel.py", "_dispatch_ws", "_pf"): "a native file dialog for the paperclip",
    ("kernel.py", "_dispatch_ws", "_gl"): "a git link subprocess for a file",
    ("kernel.py", "_dispatch_ws", "_bd"): "a native folder dialog for a field",
    ("kernel.py", "_spend_detail", "_ask"): "one peer's spend call over its tunnel",
    ("kernel.py", "pairs_snapshot", "one"): "one peer's status call over its tunnel",
    ("kernel.py", "_refresh_remote_prices", "work"): "the model price refresh over HTTP",
    ("kernel.py", "_push_notify", "run"): "web push delivery to subscriptions",
    ("kernel.py", "_push_forward", "run"): "the peer relay POST",
    ("kernel.py", "_refresh_model_catalog", "go"): "the models frame notice",
}

# Kernel callables handed to a session backend's constructor that can build no atom and hydrate no body, keyed (constructor,
# parameter, the callable's source), each with its reason; a handed callable that builds (the one-session push) is marked at
# the hand-off instead. A backend runs these on threads of its own (sdk_backend.py's "sdk-push-session" Thread), which the
# kernel-file census cannot see, so a pure-I/O claim about the backend thread has to cover them one by one.
CALLBACK_ALLOW = {
    ("SdkBackend", "notify", "_send_to_app"): "a frame to the app over its socket",
    ("SdkBackend", "poke", "_wake_kernel"): "sets the kernel's wake event",
    ("SdkBackend", "push", "_pusher_wake.set"): "sets the pusher's wake event",
    ("SdkBackend", "log", "_backend_log"): "a stderr line through the exit log",
    ("SdkBackend", "boot_phase", "_mark_boot"): "a boot-row stamp (censusDone, attachDone)",
    ("SdkBackend", "on_session_return", "_repromote_returned_session"): "a skeleton-set discard and a pusher wake; builds nothing",
    ("CodexBackend", "notify", "_send_to_app"): "a frame to the app over its socket",
    ("CodexBackend", "poke", "_wake_kernel"): "sets the kernel's wake event",
    ("CodexBackend", "push", "_pusher_wake.set"): "sets the pusher's wake event",
    ("CodexBackend", "log", "<lambda>"): "a stderr line",
    # handed by ATTRIBUTE assignment on the constructed backend (kernel.py, after the SDK constructor): the same rows
    ("SdkBackend", "login_ok", "<lambda>"): "reads the credential store's account state",
    ("SdkBackend", "postal_restore", "_bus_restore_mail"): "a POST to the local postal bus",
    ("SdkBackend", "rewind_resolved_cb", "_on_rewind_resolved"): "archives or restores held goals in the goal store at a rewind's "
                                                                 "outcome and logs through the judge; no session parse, build or hydrate",
}
# The backend constructors' positional parameters, so a callable passed by position is keyed by its parameter's name.
CTOR_POSITIONALS = {"SdkBackend": ("state_dir", "claude_bin", "notify"), "CodexBackend": ("state_dir",)}


def _ctor_of(call):
    f = call.func
    n = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
    return n if (n in CTORS or n in POOL_SHAPED or (n or "").endswith("Pool")) else None


def _pool_bindings(tree):
    """What the module's own names mean for pools: `timed` holds the bare names that ARE the judge's timed pool (its class and any
    bare name assigned to it), `imported` the names bound by an import of a raw executor (aliases included). Only a bare name may
    inherit the rebinding; an attribute spelling (concurrent.futures.ThreadPoolExecutor) or an alias never does (item 1)."""
    timed, imported = set(), {}
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name == "_TimedPool":
            timed.add("_TimedPool")
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Name) and n.value.id in timed:
            timed.update(t.id for t in n.targets if isinstance(t, ast.Name))
        if isinstance(n, ast.ImportFrom) and n.module == "concurrent.futures":
            for a in n.names:
                imported[a.asname or a.name] = a.name
    return timed, imported


def _names_in(node):
    """The identifiers an expression names (a conditional target names several functions)."""
    return [n.id for n in ast.walk(node) if isinstance(n, ast.Name)]


WRAPPERS = ("_stage_marked", "_stage_default")   # _stage_default: the mark only when the thread carries none (a hand-off a backend may
#                                                  run synchronously under a request's route, round two of the 5a follow-up)


def _wrapped_at_site(node):
    """True for a target written `_stage_marked(<name>)(<fn>)` or `_stage_default(<name>)(<fn>)`: the wrapper call at the site."""
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Call) and isinstance(node.func.func, ast.Name) \
        and node.func.func.id in WRAPPERS


def _def_marked(fn):
    """A def is marked when a `_stage_marked(...)` decorator sits on it or its own body calls `_set_stage(...)`: read from the
    ast of THAT def, never from a text window (round three, medium 1 and low 3)."""
    for d in fn.decorator_list:
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "_stage_marked":
            return True
    def own_body(node):                                   # the def's own statements: a nested def's body is that def's, not this one's
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            yield child
            yield from own_body(child)
    for n in own_body(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_set_stage":
            return True
    return False


def census(fname, text):
    """Every Thread, Timer and pool construction in `text`: a list of rows (line, ctor, enclosing function name, target
    name, verdict) where the verdict is "marked", "allowed", "pool", or a reason it is UNMARKED. Targets are resolved by
    scope: the def of that name inside the enclosing function (the last one defined before the site), else the module-level
    def; a bare pool (any constructor that is not the judge's timed one, whose submit carries the mark) is unmarked."""
    tree = _parse(text)
    module_defs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for n in tree.body:                                          # decorated module-level defs and class bodies (the handler)
        if isinstance(n, ast.ClassDef):
            for m in n.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    module_defs.setdefault(m.name, m)
    timed, imported = _pool_bindings(tree)
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    def enclosing(line):
        best = None
        for fn in funcs:
            if fn.lineno <= line <= (fn.end_lineno or fn.lineno) and (best is None or (fn.end_lineno - fn.lineno) < (best.end_lineno - best.lineno)):
                best = fn
        return best
    def resolve(name, enc, line):
        local = [n for n in (ast.walk(enc) if enc is not None else ()) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == name and n is not enc and n.lineno < line]
        if local:
            return max(local, key=lambda n: n.lineno)     # the last def before the site by LINE, not by ast.walk's breadth order (item 2)
        return module_defs.get(name)
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _ctor_of(node) is None:
            continue
        ctor = _ctor_of(node)
        enc = enclosing(node.lineno)
        enc_name = enc.name if enc is not None else "<module>"
        if ctor not in ("Thread", "Timer"):
            bare_name = isinstance(node.func, ast.Name)
            if bare_name and ctor in timed:
                rows.append((node.lineno, ctor, enc_name, None, "pool"))                     # the judge's timed pool: its submit rides
            elif ctor in CTORS or ctor in imported or ctor in POOL_SHAPED:
                rows.append((node.lineno, ctor, enc_name, None, "a bare pool: its submit carries no mark"))
            else:
                rows.append((node.lineno, ctor, enc_name, None, "an unrecognised pool-shaped call the census cannot vouch for"))
            continue
        kw = next((k for k in node.keywords if k.arg == ("target" if ctor == "Thread" else "function")), None)
        target = kw.value if kw is not None else (node.args[1] if len(node.args) > 1 else None)
        if target is None:
            rows.append((node.lineno, ctor, enc_name, None, "no target the census can read")); continue
        if _wrapped_at_site(target):
            rows.append((node.lineno, ctor, enc_name, ast.get_source_segment(text, target), "marked")); continue
        names = [n for n in _names_in(target) if resolve(n, enc, node.lineno) is not None]
        if not names:
            rows.append((node.lineno, ctor, enc_name, ast.get_source_segment(text, target), "no function the census can resolve")); continue
        for name in names:
            fn = resolve(name, enc, node.lineno)
            if _def_marked(fn):
                rows.append((node.lineno, ctor, enc_name, name, "marked"))
            elif (fname, enc_name, name) in ALLOW:
                rows.append((node.lineno, ctor, enc_name, name, "allowed"))
            else:
                rows.append((node.lineno, ctor, enc_name, name, "unmarked: no _stage_marked decorator on the def the scope binds, "
                             "no _set_stage in its body, no wrapper at the site, not a listed helper"))
    return rows


def _reaches_builds(tree):
    """The names a module uses on the event model or the judge that can build or hydrate, sorted: empty when the module can
    build nothing on its own. A binding of the judge counts whole (every surface of it parses); a binding of the event
    model counts by the attributes read on it, less EM_READ_ONLY (the SDK backend binds it for the author rule of its
    boot scan). Reach: a module-level `import`, a from-import, a name assigned from `load_source(...)` (an `or` chain
    included), and an INLINE `sys.modules["romp_event_model"].x` or `sys.modules.get("romp_event_model").x` read (round
    two, low 2); a binding smuggled through another name (`m = sys.modules; m["romp_judge"].parse`) or a getattr by string
    is outside it. The two session backends read no building name today, so every thread they start can build only
    through a kernel callback (the rows of callback_census); a backend that starts to turns every one of its thread rows
    unmarked."""
    def inline_module(value):                                   # `sys.modules["romp_event_model"]` / `sys.modules.get("...")`
        if isinstance(value, ast.Subscript) and isinstance(value.value, ast.Attribute) and value.value.attr == "modules":
            d = ast.dump(value.slice)
        elif isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == "get" \
                and isinstance(value.func.value, ast.Attribute) and value.func.value.attr == "modules":
            d = ast.dump(value)
        else:
            return None
        return ("judge" if "judge" in d else "em") if any(m in d for m in BUILD_MODULES) else None
    bound = {}                                                   # name -> "judge" | "em"
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if any(m in a.name for m in BUILD_MODULES):
                    bound[a.asname or a.name] = "judge" if "judge" in a.name else "em"
        elif isinstance(n, ast.ImportFrom) and n.module and any(m in n.module for m in BUILD_MODULES):
            return sorted(a.asname or a.name for a in n.names)   # a from-import of either module names the surface itself
        elif isinstance(n, ast.Assign) and isinstance(n.value, (ast.Call, ast.BoolOp)):
            d = ast.dump(n.value)
            if "load_source" in d and any(m in d for m in BUILD_MODULES):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        bound[t.id] = "judge" if "judge" in d else "em"
    used = set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.Attribute):
            continue
        kind = bound.get(n.value.id) if isinstance(n.value, ast.Name) else inline_module(n.value)   # a bound name, or the
        if kind is None:                                                                            #  inline sys.modules read
            continue
        if kind == "judge" or n.attr not in EM_READ_ONLY:
            used.add("%s.%s" % (ast.get_source_segment(_SRC_OF.get(id(tree), ""), n.value) if not isinstance(n.value, ast.Name)
                                 else n.value.id, n.attr))
    return sorted(used)


def backend_census(fname, text):
    """Every Thread, Timer and pool construction in a session backend: (line, ctor, enclosing function, verdict), the verdict
    "callback-only" while the module reaches no event model or judge (its threads run kernel callbacks, covered by
    callback_census, or the backend's own I/O), else unmarked: a backend that builds directly has to mark or list its threads."""
    tree = _parse(text)
    reaches = _reaches_builds(tree)
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _ctor_of(node) is None:
            continue
        enc = [fn for fn in funcs if fn.lineno <= node.lineno <= (fn.end_lineno or fn.lineno)]
        enc_name = min(enc, key=lambda fn: fn.end_lineno - fn.lineno).name if enc else "<module>"
        rows.append((node.lineno, _ctor_of(node), enc_name,
                     "callback-only" if not reaches else "unmarked: %s reads %s on the event model or the judge, so its threads can "
                     "build directly; mark them or list them" % (fname, ", ".join(reaches))))
    return rows


def _backend_ctor_of(node):
    """The backend constructor a call names (`sbmod.SdkBackend(...)`, `CodexBackend(...)`), else None."""
    if not isinstance(node, ast.Call):
        return None
    ctor = node.func.attr if isinstance(node.func, ast.Attribute) else (node.func.id if isinstance(node.func, ast.Name) else None)
    return ctor if ctor in BACKEND_CTORS else None


def callback_census(text):
    """Every kernel callable handed to a session backend: (line, ctor, parameter, source, verdict) for each positional or
    keyword argument of the two constructors AND for each attribute assigned on a name the constructor's result was bound to
    (`_sdk_backend.login_ok = ...`, keyed by the attribute), when the value IS a callable the census can read: a
    `_stage_marked(<name>)(<fn>)` or `_stage_default(<name>)(<fn>)` wrapper ("marked"), a name bound to a module-level def,
    a bound method (an attribute spelled in lower case; an upper-case attribute is a constant, `jd.STATE`), or a lambda; a
    value (a constant, a call's result, a module attribute in upper case) is no row. An unwrapped callable is "allowed" only
    on CALLBACK_ALLOW with its reason, else unmarked."""
    tree = _parse(text)
    module_defs = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    bound = {}                                                   # name -> ctor: `_sdk_backend = sbmod.SdkBackend(...)`
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and _backend_ctor_of(n.value):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    bound[t.id] = _backend_ctor_of(n.value)
    handed_all = []                                              # (ctor, parameter or attribute, value node)
    for node in ast.walk(tree):
        ctor = _backend_ctor_of(node)
        if ctor is not None:
            params = list(CTOR_POSITIONALS.get(ctor, ()))
            handed_all += [(ctor, params[i] if i < len(params) else "arg%d" % i, a) for i, a in enumerate(node.args)]
            handed_all += [(ctor, k.arg, k.value) for k in node.keywords]
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Attribute) \
                and isinstance(node.targets[0].value, ast.Name) and node.targets[0].value.id in bound:
            handed_all.append((bound[node.targets[0].value.id], node.targets[0].attr, node.value))
    rows = []
    for ctor, param, value in handed_all:
        if _wrapped_at_site(value):
            rows.append((value.lineno, ctor, param, ast.get_source_segment(text, value), "marked")); continue
        if isinstance(value, ast.Name) and value.id in module_defs:
            src = value.id
        elif isinstance(value, ast.Attribute) and value.attr == value.attr.lower():
            src = ast.get_source_segment(text, value)
        elif isinstance(value, ast.Lambda):
            src = "<lambda>"
        else:
            continue                                                          # a value, not a callable
        if (ctor, param, src) in CALLBACK_ALLOW:
            rows.append((value.lineno, ctor, param, src, "allowed"))
        else:
            rows.append((value.lineno, ctor, param, src, "unmarked: a kernel callable the backend may run on a thread of its own, "
                         "neither wrapped `_stage_default(<name>)(<fn>)` at the hand-off nor listed in CALLBACK_ALLOW"))
    return rows


class StageMarksCensus(unittest.TestCase):
    def test_every_thread_and_pool_site_in_the_kernel_and_the_judge_is_marked_allowed_or_a_riding_pool(self):
        """T401 (5a): every construction site in kernel.py and judge.py is marked (by the ast: a _stage_marked decorator on
        the def the scope binds, a _set_stage in that def's body, or a _stage_marked wrapper at the site), a pure I/O helper
        listed by site with its reason, or a pool whose submit carries the submitter's stage (the judge's timed pool, pinned
        below); a bare pool is flagged."""
        bad, seen, pools = [], 0, 0
        for fname, text in SOURCES.items():
            for line, ctor, enc, target, verdict in census(fname, text):
                seen += 1
                if verdict == "pool":
                    pools += 1
                elif verdict not in ("marked", "allowed"):
                    bad.append((fname, line, ctor, enc, target, verdict))
        self.assertEqual(bad, [], "sites that can reach a build or a hydration without a stage mark")
        self.assertGreaterEqual(seen, 40, "the census walked the construction sites: %d" % seen)
        self.assertGreaterEqual(pools, 7, "the judge's pools are sites the census walked: %d" % pools)

    def test_every_listed_helper_site_still_exists(self):
        """A stale ALLOW entry would silently vouch for nothing: every key names a site the census saw."""
        sites = {(f, enc, t) for f, text in SOURCES.items() for _, _, enc, t, v in census(f, text)
                 if t and v in ("marked", "allowed") or (t and v.startswith("unmarked"))}   # rows whose target resolved to a def (item 3)
        stale = sorted(k for k in ALLOW if k not in sites)
        self.assertEqual(stale, [], "ALLOW entries with no site behind them")

    def test_every_callable_the_kernel_hands_a_backend_is_marked_at_the_hand_off_or_listed_with_a_reason(self):
        """The 2026-09-14 read boot (the first after 5b's merge) counted 3,312 builds and 9.4 MB of hydration under `none:`,
        all from `_push_session_now` run on sdk_backend.py's own "sdk-push-session" Thread, a thread no kernel-file census can
        see: the census covers the callables the kernel HANDS a backend instead, by the constructors and by attribute
        assignment on the constructed backend (round two, low 1: login_ok, postal_restore, rewind_resolved_cb). The one that
        builds (the one-session push) is wrapped `_stage_default("push.session")` at both hand-off sites, never decorated on
        the def (its WS-handler and spawn callers keep their own marks) and never a plain mark (the Codex backend calls it
        synchronously under a request's route, round two's medium); every other handed callable is listed with the reason
        it can build nothing."""
        rows = callback_census(SOURCES["kernel.py"])
        bad = [r for r in rows if r[4] not in ("marked", "allowed")]
        self.assertEqual(bad, [], "callables handed to a backend that can build or hydrate under no mark")
        marked = sorted((ctor, param, src) for _, ctor, param, src, v in rows if v == "marked")
        self.assertEqual(marked, [("CodexBackend", "push_session", '_stage_default("push.session")(_push_session_now)'),
                                  ("SdkBackend", "push_session", '_stage_default("push.session")(_push_session_now)')],
                         "the one-session push is the callable that builds: the thread's default mark at both hand-offs")
        by_attr = sorted(param for _, ctor, param, src, v in rows if ctor == "SdkBackend" and v == "allowed"
                         and param in ("login_ok", "postal_restore", "rewind_resolved_cb"))
        self.assertEqual(by_attr, ["login_ok", "postal_restore", "rewind_resolved_cb"], "the attribute hand-offs are rows: %s" % rows)
        self.assertGreaterEqual(len(rows), 13, "the census read the handed callables: %s" % rows)

    def test_every_listed_callback_still_has_a_hand_off_behind_it(self):
        seen = {(ctor, param, src) for _, ctor, param, src, _ in callback_census(SOURCES["kernel.py"])}
        stale = sorted(k for k in CALLBACK_ALLOW if k not in seen)
        self.assertEqual(stale, [], "CALLBACK_ALLOW entries with no hand-off behind them")

    def test_the_backends_threads_run_callbacks_only_while_neither_backend_reaches_the_event_model_or_the_judge(self):
        """A backend thread that builds DIRECTLY (not through a kernel callback) in a later change is seen here: the walk
        over sdk_backend.py and codex_backend.py reads every Thread, Timer and pool site, and every row is "callback-only"
        exactly while the module reads no building name on the event model or the judge (the SDK backend binds the event
        model for the per-record author rule of its boot scan, listed in EM_READ_ONLY); the day one does, every row turns
        unmarked."""
        seen = 0
        for fname, text in BACKEND_SOURCES.items():
            self.assertEqual(_reaches_builds(_parse(text)), [], "%s reads a building name on the event model or the judge" % fname)
            rows = backend_census(fname, text)
            seen += len(rows)
            self.assertEqual([r for r in rows if r[3] != "callback-only"], [], fname)
        self.assertGreaterEqual(seen, 8, "the walk read the backends' thread sites: %d" % seen)

    # The census's own rules, proven on synthetic snippets: what round one and round two got wrong.
    SNIPPET_SHADOW = '''
@_stage_marked("push")
def _push():
    pass

def _dispatch():
    def _push():
        pass
    threading.Thread(target=_push).start()
'''
    SNIPPET_WINDOW = '''
def _helper():
    pass

def _other():
    _set_stage("x")

def _start():
    threading.Thread(target=_helper).start()
'''
    SNIPPET_POOL = '''
def _fan():
    with ThreadPoolExecutor(max_workers=2) as ex:
        ex.submit(print)
'''
    SNIPPET_CLOSURE = '''
def _elsewhere():
    def go():
        pass
    threading.Thread(target=go).start()
'''
    SNIPPET_MARKED = '''
def _tier():
    _set_stage("judge.x")

def _start():
    threading.Thread(target=_tier).start()
    threading.Thread(target=_stage_marked("warm")(_tier)).start()
'''

    def _verdicts(self, snippet):
        return [(enc, t, v.split(":")[0]) for _, _, enc, t, v in census("kernel.py", snippet)]

    def test_a_local_helper_named_like_a_marked_function_is_not_vouched_for_by_its_namesake(self):
        """Round three, medium 1: round two resolved a target by NAME and read the first def in the file, so a local _push handed
        to a Thread passed on the module-level _push's decorator. The scope binds the local def, which is unmarked."""
        self.assertEqual(self._verdicts(self.SNIPPET_SHADOW), [("_dispatch", "_push", "unmarked")])

    def test_an_unmarked_def_is_not_read_as_marked_by_a_set_stage_call_that_follows_it(self):
        """Round three, low 3: an 80-line forward text window read any later _set_stage( as the def's own."""
        self.assertEqual(self._verdicts(self.SNIPPET_WINDOW), [("_start", "_helper", "unmarked")])

    def test_a_bare_pool_is_flagged_where_the_judges_timed_pool_is_covered_by_its_submit(self):
        """Round three, low 4: only _TimedPool.submit carries the mark; a bare ThreadPoolExecutor is not covered."""
        self.assertEqual(self._verdicts(self.SNIPPET_POOL), [("_fan", None, "a bare pool")])
        self.assertEqual(self._verdicts("class _TimedPool:\n    pass\nThreadPoolExecutor = _TimedPool\n" + self.SNIPPET_POOL), [("_fan", None, "pool")])

    def test_a_generic_closure_name_outside_its_listed_site_is_flagged(self):
        """Round three, medium 2: ALLOW is keyed by site, so a new `go` closure in an unlisted function is not vouched for."""
        self.assertEqual(self._verdicts(self.SNIPPET_CLOSURE), [("_elsewhere", "go", "unmarked")])

    SNIPPET_NESTED = '''
def _outer():
    def _inner():
        _set_stage("x")
    pass

def _start():
    threading.Thread(target=_outer).start()
'''
    SNIPPET_ORDER = '''
def _dispatch():
    def _deep_holder():
        def _go():
            pass
    def _go():
        _set_stage("y")
    threading.Thread(target=_go).start()
'''
    SNIPPET_ALIAS = '''
from concurrent.futures import ThreadPoolExecutor as _RawPool
class _TimedPool:
    pass
ThreadPoolExecutor = _TimedPool
def _fan():
    with _RawPool(max_workers=2) as ex:
        ex.submit(print)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex2:
        ex2.submit(print)
'''

    def test_an_inner_closures_set_stage_does_not_mark_its_outer_def(self):
        """Item 2: _def_marked walked into nested defs, so an unmarked def whose inner closure sets a stage read as marked."""
        self.assertEqual(self._verdicts(self.SNIPPET_NESTED), [("_start", "_outer", "unmarked")])

    def test_the_scope_binds_the_last_def_by_line_not_by_walk_order(self):
        """Item 2: a deeper earlier def must not win over the shallower later one the scope binds."""
        self.assertEqual(self._verdicts(self.SNIPPET_ORDER), [("_dispatch", "_go", "marked")])

    def test_an_aliased_or_attribute_spelled_raw_pool_is_flagged_where_only_a_bare_rebound_name_inherits(self):
        """Item 1: judge.py rebinds ThreadPoolExecutor to its timed pool; an alias of the raw executor and an attribute spelling
        are pools whose submit carries no mark, and neither inherits the rebinding."""
        self.assertEqual(self._verdicts(self.SNIPPET_ALIAS), [("_fan", None, "a bare pool"), ("_fan", None, "a bare pool")])

    SNIPPET_HANDOFF = '''
def _push_now(sid): pass
def _wake(): pass
def _send(msg): pass
def _sdk():
    b = sbmod.SdkBackend(jd.STATE, _bin(), _send, poke=_wake, push=_ev.set, push_session=_push_now, log=lambda m: None,
                         boot_at=_STARTED, code_version=_sha())
    c = cxmod.CodexBackend(jd.STATE, notify=_send, push_session=_stage_marked("push.session")(_push_now))
'''

    SNIPPET_ATTR_HANDOFF = SNIPPET_HANDOFF + '''
def _restore(sid, mids): pass
def _wire():
    _sdk_backend = sbmod.SdkBackend(jd.STATE, _bin(), _send)
    _sdk_backend.login_ok = lambda: True
    _sdk_backend.postal_restore = _restore
    _sdk_backend.state_dir = jd.STATE
    other.postal_restore = _restore
'''

    def test_a_callable_assigned_on_the_constructed_backend_is_a_row_keyed_by_its_attribute(self):
        rows = {(ctor, param, src): v for _, ctor, param, src, v in callback_census(self.SNIPPET_ATTR_HANDOFF)}
        self.assertEqual(rows[("SdkBackend", "login_ok", "<lambda>")], "allowed", "keyed (constructor, attribute, source): the kernel's listed row")
        self.assertEqual(rows[("SdkBackend", "postal_restore", "_restore")][:8], "unmarked", "another callable on the same attribute is not")
        self.assertNotIn(("SdkBackend", "state_dir", "jd.STATE"), rows, "a constant assigned on the backend is no row")
        self.assertEqual([k for k in rows if k[1] == "postal_restore"], [("SdkBackend", "postal_restore", "_restore")],
                         "an attribute on a name the constructor never bound is not a hand-off")

    def test_the_default_mark_wrapper_marks_at_a_site_like_the_plain_one(self):
        rows = {(ctor, param, src): v for _, ctor, param, src, v in callback_census(
            'b = sbmod.SdkBackend(jd.STATE, _bin(), _send, push_session=_stage_default("push.session")(_push_now))')}
        self.assertEqual(rows, {("SdkBackend", "push_session", '_stage_default("push.session")(_push_now)'): "marked"})

    def test_the_callback_census_reads_every_handed_callable_and_no_value(self):
        rows = {(ctor, param, src): v for _, ctor, param, src, v in callback_census(self.SNIPPET_HANDOFF)}
        self.assertEqual(rows[("SdkBackend", "notify", "_send")][:8], "unmarked", "a positional callable is keyed by its parameter")
        self.assertEqual(rows[("SdkBackend", "push", "_ev.set")][:8], "unmarked", "a bound method is a callable")
        self.assertEqual(rows[("SdkBackend", "log", "<lambda>")][:8], "unmarked")
        self.assertEqual(rows[("SdkBackend", "push_session", "_push_now")][:8], "unmarked", "the unwrapped hand-off: the read boot's row")
        self.assertEqual(rows[("CodexBackend", "push_session", '_stage_marked("push.session")(_push_now)')], "marked")
        self.assertEqual(rows[("CodexBackend", "notify", "_send")][:8], "unmarked")
        self.assertNotIn(("SdkBackend", "state_dir", "jd.STATE"), rows, "an upper-case attribute is a constant, not a callable")
        self.assertEqual({k[1] for k in rows} & {"claude_bin", "boot_at", "code_version"}, set(), "calls and plain names are values")

    SNIPPET_BACKEND_IO = '''
import threading
def _push_session(self, sid):
    threading.Thread(target=run, name="sdk-push-session", daemon=True).start()
'''
    SNIPPET_BACKEND_READS = SNIPPET_BACKEND_IO + '''
_em = sys.modules.get("romp_event_model") or load_source("romp_event_model_echo", _HERE / "event_model.py")
def _scan(rec): return _em.author_of(rec), _em.is_interrupt_record(rec)
'''
    SNIPPET_BACKEND_BUILDS = SNIPPET_BACKEND_READS + '''
def _tail(path): return _em.parse_events(path)
'''
    SNIPPET_BACKEND_INLINE = SNIPPET_BACKEND_IO + '''
def _tail(path): return sys.modules["romp_event_model"].parse_events(path)
def _who(rec): return sys.modules.get("romp_event_model").author_of(rec)
'''

    def test_an_inline_sys_modules_read_counts_like_a_bound_name(self):
        self.assertEqual(_reaches_builds(_parse(self.SNIPPET_BACKEND_INLINE)), ['sys.modules["romp_event_model"].parse_events'],
                         "the subscript read of a building name counts; the .get read of a record-level name does not")
        self.assertTrue(backend_census("x.py", self.SNIPPET_BACKEND_INLINE)[0][3].startswith("unmarked"))

    def test_a_backend_thread_is_callback_only_until_the_backend_reads_a_building_name_on_the_event_model(self):
        self.assertEqual([r[3] for r in backend_census("x.py", self.SNIPPET_BACKEND_IO)], ["callback-only"])
        self.assertEqual([r[3] for r in backend_census("x.py", self.SNIPPET_BACKEND_READS)], ["callback-only"],
                         "the author rule and the interrupt bit read one record each: no build")
        self.assertEqual(_reaches_builds(_parse(self.SNIPPET_BACKEND_BUILDS)), ["_em.parse_events"])
        self.assertEqual(backend_census("x.py", self.SNIPPET_BACKEND_BUILDS)[0][3],
                         "unmarked: x.py reads _em.parse_events on the event model or the judge, so its threads can build directly; "
                         "mark them or list them")

    def test_a_set_stage_in_the_defs_own_body_and_a_wrapper_at_the_site_both_mark(self):
        self.assertEqual(self._verdicts(self.SNIPPET_MARKED), [("_start", "_tier", "marked"), ("_start", '_stage_marked("warm")(_tier)', "marked")])

    def test_the_pools_submit_carries_the_submitters_stage_into_the_worker(self):
        src = inspect.getsource(jd._TimedPool.submit)
        self.assertIn("stage = em._read_stage()", src, "the submitter's mark is read at submit")
        self.assertIn("em._set_stage_mark(stage)", src, "and set on the worker")
        self.assertIn("em._set_stage_mark(prev)", src, "and the worker's previous mark restored in the finally")
        self.assertIn("em.set_stage_provider(_set_stage)", SOURCES["kernel.py"], "the kernel installs its setter")

    def test_every_request_method_is_marked_with_its_route_and_the_tier_runner_with_its_tier(self):
        src = SOURCES["kernel.py"]
        for m in ("do_OPTIONS", "do_HEAD", "do_GET", "do_POST"):
            i = src.index("    def %s(self):" % m)
            head = src[max(0, i - 200):i]
            self.assertIn('@_stage_marked(lambda self: "http.%s." + _route_seg(self.path))' % m.split("_")[1], head, m)
        self.assertIn('_set_stage("judge." + name)', inspect.getsource(km.jd._run_tier), "the tier threads (the shared runner)")

    def test_the_route_names_the_stage_with_two_segments_where_the_roads_differ_by_the_second(self):
        self.assertEqual([km._route_seg(p) for p in ("/chat/x/y", "/ws", "/remote/h/ws", "/", "", "/perf?stacks=1", "/state?x=1",
                                                      "/push/relay", "/push", "/tunnels/dial?x=1", "/usage/fleet", "/usage")],
                         ["chat", "ws", "remote", "root", "root", "perf", "state", "push.relay", "push", "tunnels.dial", "usage.fleet", "usage"])

    def test_the_rows_noted_flag_is_set_by_compare_and_set_under_the_notes_lock(self):
        """1610 low 4, pinned by source: the race (two threads finding the corrupt row at once) is not observable in a test (60
        barrier trials noted once at head and base), so the shape is pinned: the check and the set sit under _ASM_CKPT_LOCK."""
        src = inspect.getsource(em.LazyIndex._refuse_rows)
        i_lock, i_check, i_set = src.index("with _ASM_CKPT_LOCK:"), src.index("if self._rows_noted:"), src.index("self._rows_noted = True")
        self.assertLess(i_lock, i_check); self.assertLess(i_check, i_set)


class BuildsCountUnderTheThreadsStage(unittest.TestCase):
    """A build from a marked thread lands under its name in asmIndex.materializedByStage; a build inside a judge pool worker lands
    under the tier that submitted it; an unmarked thread's reads `none`."""

    def setUp(self):
        self._prev = (em._READ_STAGE_FN[0], getattr(em, "_SET_STAGE_FN", [None])[0])
        em.set_read_stage_provider(km._current_read_stage)   # several kernel loads share one event model in a pytest process: the
        em.set_stage_provider(km._set_stage)                 #  providers must be THIS load's, whose thread-local the marks set

    def tearDown(self):
        em._READ_STAGE_FN[0], em._SET_STAGE_FN[0] = self._prev

    def _index(self):
        recs = [["r%d" % i, None, "u", None, i, 1000 + i, 0, None, None, None] for i in range(3)]
        rows = [json.dumps({"r": i, "s": {"type": "user", "author": "human", "t": 1000 + i}, "seq": i}, separators=(",", ":")) for i in range(3)]
        idx = em.LazyIndex({"atoms": rows, "records": recs, "fsids": []}, SID, "/TESTDIR/x.jsonl")
        return em.LazyAtoms(idx, range(3))

    def _build_on(self, runner, name=None):
        la = self._index()
        before = dict(em.asm_index_stats()["materializedByStage"])
        th = threading.Thread(target=runner, args=(lambda: la[0],), name=name); th.start(); th.join(10)
        after = em.asm_index_stats()["materializedByStage"]
        return {k: v - before.get(k, 0) for k, v in after.items() if v - before.get(k, 0)}

    def test_a_tier_threads_build_lands_under_judge_and_its_tier_name(self):
        delta = self._build_on(lambda build: jd._run_tier(build, "triage", jd._pass_acc()), name="triage")
        self.assertEqual(list(delta), ["judge.triage:<lambda>"], delta)

    def test_a_pool_workers_build_lands_under_the_tier_that_submitted_it(self):
        """Round two's medium: the tier thread was marked but its per-session pool workers built under `none` (1930 of 1930 builds
        on a served boot). The pool's submit carries the mark; a build inside the worker lands under judge.<tier>."""
        def tier(build):
            with jd._TimedPool(max_workers=1) as ex:
                ex.submit(build).result(10)
        delta = self._build_on(lambda build: jd._run_tier(lambda: tier(build), "triage", jd._pass_acc()), name="triage")
        self.assertEqual(list(delta), ["judge.triage:<lambda>"], delta)

    def test_a_pool_worker_restores_its_previous_mark_after_the_run(self):
        seen = []
        def tier():
            with jd._TimedPool(max_workers=1) as ex:
                ex.submit(lambda: seen.append(km._current_read_stage())).result(10)
                ex.submit(lambda: seen.append(km._current_read_stage())).result(10)
                ex.submit(lambda: None).result(10)
                seen.append(ex.submit(lambda: km._current_read_stage()).result(10))
        th = threading.Thread(target=lambda: jd._run_tier(tier, "index", jd._pass_acc()), name="index"); th.start(); th.join(10)
        self.assertEqual(seen, ["judge.index", "judge.index", "judge.index"], seen)

    def _push_through_the_backend(self, handed):
        """The REAL backend's one-session push: `handed` is what the kernel passes as push_session; the backend runs it on its
        own "sdk-push-session" Thread (sdk_backend.py _push_session), where it builds one lazy row; the stage that build lands
        under and the stage the thread read while it ran come back."""
        la = self._index()
        seen, done = [], threading.Event()
        def _push_session_now(sid):
            seen.append(em._read_stage()); la[0]; done.set()
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None, push_session=handed(_push_session_now))
        before = dict(em.asm_index_stats()["materializedByStage"])
        be._push_session(SID)
        self.assertTrue(done.wait(5), "the backend ran the callable on its own thread")
        after = em.asm_index_stats()["materializedByStage"]
        return seen, {k: v - before.get(k, 0) for k, v in after.items() if v - before.get(k, 0)}

    def test_the_backends_push_thread_builds_under_push_session_through_the_wrapper_the_kernel_hands_it(self):
        """The 2026-09-14 read boot's row, fixed: the backend's "sdk-push-session" Thread carries no mark of its own, and the
        `_stage_default("push.session")` wrapper the kernel puts on `_push_session_now` at the hand-off (the census pins the two
        sites) marks the call for its length, so the row lands under push.session; the thread reads none again after."""
        seen, delta = self._push_through_the_backend(lambda fn: _DEFAULT_MARK("push.session")(fn))
        self.assertEqual(seen, ["push.session"], seen)
        self.assertEqual(list(delta), ["push.session:_push_session_now"], delta)

    def _codex_push_from(self, handed, premark):
        """The REAL Codex backend's stored push_session, called the way set_mode and kill call it: synchronously on the
        caller's thread, here a thread pre-marked `premark` (None: unmarked, the backend's own event loop)."""
        cx = load_source("romp_codex_backend_stage_marks", os.path.join(os.path.dirname(HERE), "kernel", "codex_backend.py"))
        la = self._index()
        seen = []
        def _push_session_now(sid):
            seen.append(em._read_stage()); la[0]
        be = cx.CodexBackend(tempfile.mkdtemp(), push_session=handed(_push_session_now), client_factory=lambda *a, **k: None)
        before = dict(em.asm_index_stats()["materializedByStage"])
        after_mark = []
        def caller():
            km._set_stage(premark)
            try:
                be.push_session(SID)
                after_mark.append(getattr(km._STAGE_TL, "name", None))
            finally:
                km._set_stage(None)
        th = threading.Thread(target=caller, name="codex-caller"); th.start(); th.join(10)
        after = em.asm_index_stats()["materializedByStage"]
        return seen, {k: v - before.get(k, 0) for k, v in after.items() if v - before.get(k, 0)}, after_mark

    def test_the_codex_backends_synchronous_push_under_a_request_keeps_the_requests_route(self):
        """Round two's medium: the Codex backend calls push_session synchronously at set_mode (the WS recv loop, http.GET.ws)
        and kill (do_POST): a plain _stage_marked at the hand-off overwrote the route for the call's length; the default
        mark leaves a standing mark alone, and the route's mark stands after the call too."""
        seen, delta, after = self._codex_push_from(lambda fn: _DEFAULT_MARK("push.session")(fn), "http.GET.ws")
        self.assertEqual(seen, ["http.GET.ws"], seen)
        self.assertEqual(list(delta), ["http.GET.ws:_push_session_now"], delta)
        self.assertEqual(after, ["http.GET.ws"], "the caller's mark stands after the call")

    def test_the_codex_backends_push_from_an_unmarked_thread_takes_push_session_and_leaves_none_after(self):
        seen, delta, after = self._codex_push_from(lambda fn: _DEFAULT_MARK("push.session")(fn), None)
        self.assertEqual(seen, ["push.session"], seen)
        self.assertEqual(list(delta), ["push.session:_push_session_now"], delta)
        self.assertEqual(after, [None], "the default mark is restored to none after the call")

    def test_the_backends_push_thread_reads_none_when_the_callable_is_handed_bare(self):
        """The read boot's face at the base: handed bare, the same callable on the same backend thread builds under `none:`,
        which is why the mark rides the hand-off and no census of the kernel's own Thread lines could see it."""
        seen, delta = self._push_through_the_backend(lambda fn: fn)
        self.assertEqual(seen, [None], seen)
        self.assertEqual(list(delta), ["none:_push_session_now"], delta)

    def test_an_unmarked_threads_build_reads_none_the_read_boots_face(self):
        delta = self._build_on(lambda build: build())
        self.assertEqual(len(delta), 1, delta); self.assertTrue(next(iter(delta)).startswith("none:"), delta)

    def test_the_index_block_carries_the_per_stage_map(self):
        st = em.asm_index_stats()
        self.assertIn("materializedByStage", st); self.assertIsInstance(st["materializedByStage"], dict)


if __name__ == "__main__":
    unittest.main()
