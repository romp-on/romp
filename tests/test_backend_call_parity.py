#!/usr/bin/env python3
"""The kernel's call shape on a session backend equals every backend's signature.
CodexBackend.prune_live took three positional arguments while the kernel's _merge_live_atoms passed four,
so every live merge of a Codex session holding an input echo raised TypeError: the chat build and the
feed's merge of that session failed until the echo landed, and the timeline bars logged a live-merge
failure. Nothing pinned the shape: the backends duck-type the SessionBackend ABC (only the kernel's
_UnownedBackend, the refusing route for a sid no backend owns, subclasses it), so the ABC's own
signature, which also lagged, was never enforced, and the conformance test checked that each method
EXISTS, not what it accepts.
Two checks, both static (AST over the sources, no kernel import, so a bare run touches no state):
  1. every call kernel.py makes on a backend bound as `be = Sessions.backend_for(...)` binds against each
     backend class that defines the method (positional count and keyword names), so a caller that grows an
     argument fails here until every backend takes it (outside the scan: chained
     `Sessions.backend_for(x).method(...)` calls and functions that take `be` as a parameter);
  2. every method the SessionBackend ABC declares is accepted with at least the ABC's positional shape by
     each backend that defines it, so the ABC stays the contract it claims to be.
Synthetic fixtures only; the scan reads this repo's own sources.
"""
import ast
import os
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
KERNEL = os.path.join(os.path.dirname(HERE), "kernel")
SESSION_BACKEND = os.path.join(KERNEL, "session_backend.py")
BACKENDS = {   # class -> the source file defining it
    "SdkBackend": os.path.join(KERNEL, "sdk_backend.py"),
    "CodexBackend": os.path.join(KERNEL, "codex_backend.py"),
    "_UnownedBackend": os.path.join(KERNEL, "kernel.py"),
}


def _parse(path):
    with open(path, encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _is_static(fn):
    return any(isinstance(d, ast.Name) and d.id == "staticmethod" for d in fn.decorator_list)


def _class_signatures(tree, cls):
    """{method: (min_positional, max_positional, has_varargs, keyword_names, has_varkw)} for `cls`, the
    receiver (self, or cls) excluded; a staticmethod has none. max_positional is None with *args."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    a = item.args
                    positional = [x.arg for x in a.posonlyargs + a.args]
                    if not _is_static(item):
                        positional = positional[1:]                       # drop the receiver
                    required = len(positional) - len(a.defaults)
                    names = set(positional) | {k.arg for k in a.kwonlyargs}
                    out[item.name] = (required, None if a.vararg else len(positional),
                                      a.vararg is not None, names, a.kwarg is not None)
            return out
    raise AssertionError("class %s not found" % cls)


def _binds(sig, npos, kws):
    """Would a call with `npos` positional arguments and the keyword names `kws` bind to `sig`?"""
    required, maxpos, varargs, names, varkw = sig
    if maxpos is not None and npos > maxpos:
        return False
    if any(k not in names and not varkw for k in kws):
        return False
    return npos + len(kws) >= required


def _backend_for_calls(tree):
    """Every `be.<method>(...)` call in a kernel function whose every `be` binding is
    Sessions.backend_for(...): [(method, lineno, npos, keyword names)]. Calls with *args are skipped
    (their count is dynamic)."""
    found = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        binds = []
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "be" for t in n.targets):
                binds.append(n.value)
        if not binds or not all(isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute)
                                and v.func.attr == "backend_for" for v in binds):
            continue
        if "be" in [a.arg for a in fn.args.args]:
            continue                                     # a parameter: its binding is the caller's
        for n in ast.walk(fn):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "be"
                    and not any(isinstance(a, ast.Starred) for a in n.args)):
                found.append((n.func.attr, n.lineno, len(n.args), [k.arg for k in n.keywords if k.arg]))
    return found


class CallShapeParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        trees = {path: _parse(path) for path in set(BACKENDS.values()) | {SESSION_BACKEND}}   # each file once
        cls.calls = _backend_for_calls(trees[BACKENDS["_UnownedBackend"]])                 # kernel.py
        cls.sigs = {name: _class_signatures(trees[path], name) for name, path in BACKENDS.items()}
        cls.abc = _class_signatures(trees[SESSION_BACKEND], "SessionBackend")

    def test_the_live_merge_calls_prune_live_with_four_arguments_every_backend_takes(self):
        # the exact drift: pinned by name so a rename of the caller cannot pass this vacuously
        calls = [c for c in self.calls if c[0] == "prune_live"]
        self.assertTrue(calls, "kernel.py no longer calls be.prune_live on a backend_for backend; re-anchor")
        for _, lineno, npos, kws in calls:
            self.assertEqual((npos, kws), (4, []), "kernel.py:%d prune_live call shape" % lineno)
            for name, sigs in self.sigs.items():
                self.assertIn("prune_live", sigs, name)
                self.assertTrue(_binds(sigs["prune_live"], 4, []),
                                "%s.prune_live does not take the kernel's four arguments" % name)

    def test_the_restart_door_stays_in_the_scan_and_its_call_binds_on_the_codex_backend(self):
        # the restart door routes an ENDED Codex row to the Codex backend by its record (2026-09-24, the post-merge
        # note on #2125). Rebinding `be` there would take the whole door out of the call-shape scan, which reads only
        # a function whose every `be` binding is Sessions.backend_for; pinned by name, as prune_live is, so it cannot
        # pass vacuously. CodexBackend has a relaunch of its own since then, so the door's call must bind on it too.
        calls = [c for c in self.calls if c[0] == "relaunch"]
        self.assertTrue(calls, "kernel.py's restart door no longer calls be.relaunch on a backend_for backend: a "
                               "second binding of be takes the door out of the scan")
        self.assertIn("relaunch", self.sigs["CodexBackend"], "CodexBackend has no relaunch of its own")
        for _, lineno, npos, kws in calls:
            self.assertEqual((npos, kws), (1, []), "kernel.py:%d relaunch call shape" % lineno)
            for name, sigs in self.sigs.items():
                if "relaunch" in sigs:
                    self.assertTrue(_binds(sigs["relaunch"], 1, []),
                                    "%s.relaunch does not take the door's one argument" % name)

    def test_every_backend_for_call_binds_on_every_backend_that_defines_the_method(self):
        bad = []
        for method, lineno, npos, kws in self.calls:
            for name, sigs in self.sigs.items():
                if method in sigs and not _binds(sigs[method], npos, kws):
                    bad.append("kernel.py:%d be.%s(%d positional%s) does not bind on %s.%s"
                               % (lineno, method, npos, (", kw=" + ",".join(kws)) if kws else "", name, method))
        self.assertEqual(bad, [], "\n".join(bad))

    def test_every_backend_accepts_at_least_the_abcs_shape_for_each_method_it_defines(self):
        bad = []
        for method, (req, maxpos, _, names, _) in self.abc.items():
            if method.startswith("__"):
                continue
            fullest = maxpos if maxpos is not None else req      # a *args method's fullest FIXED call
            for name, sigs in self.sigs.items():
                sig = sigs.get(method)
                if sig is None:
                    continue
                # the ABC's fullest positional call must bind, and its required count must not be raised
                if not _binds(sig, fullest, []) or sig[0] > req:
                    bad.append("%s.%s accepts %d..%s but SessionBackend.%s declares %d..%s positional"
                               % (name, method, sig[0], sig[1], method, req, maxpos))
        self.assertEqual(bad, [], "\n".join(bad))

    def test_the_abc_declares_prune_lives_floor(self):
        self.assertEqual(self.abc["prune_live"][1], 4, "SessionBackend.prune_live: sid, tx_uuids, tx_user_texts, human_floor")


if __name__ == "__main__":
    unittest.main()
