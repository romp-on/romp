#!/usr/bin/env python3
"""The judge prompt experiment (plans/judge-prompt-experiments.md): a corpus of transcript endings, arms of judge prompts
run over copies of it with the judge module rebound onto scratch roots, the four measures and a report.

    judge_experiment.py build-corpus --state-root ~/.local/state/romp --claude-root ~/.claude --dest DIR [--per-class N]
    judge_experiment.py run --corpus DIR --run-root DIR --arm NAME[=PROMPTS.json] ... --budget-usd X --claude-bin PATH
    judge_experiment.py label --corpus DIR --run-root DIR --live-state ROOT --claude-bin PATH [--model M]
    judge_experiment.py report --corpus DIR --run-root DIR --live-state ROOT [--figure PNG]

Every path the experiment writes is under the destination the caller names, and a destination inside a git checkout is
refused: the corpus is the user's own history and stays out of the repository. The corpus builder reads the live state
root and the Claude root as FILES (names, the registry, transcripts, goal stores, override journals) and loads no romp
module against them; each arm runs in a subprocess of its own with the judge module loaded against the arm's scratch
root. Every model call is paid by whoever owns the binary named in `--claude-bin`, which is required, as is a budget on
`run` (`inf` for none): nothing here can tell a real binary from a fake, so nothing runs without both being said.
"""
import argparse
import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BIN = ROOT / "bin"
CLASSES = ("offer", "question", "undone", "finished")
PROMPT_KEYS = ("PLAN_SYS", "CLOSER_SYS", "UNBLOCK_SYS")
JUDGE_MODULE_NAME = "romp_judge_experiment_arm"
OFFER_RE = re.compile(r"\b(i can also|i could also|if you (want|like|prefer)|shall i|want me to|would you like( me)?|"
                      r"let me know if you|happy to|say the word)\b", re.I)
UNDONE_RE = re.compile(r"\b(not (yet )?done|left (undone|for later|open)|to ?do|did not run|didn't run|not run|"
                       r"remains? (to be done|undone|open)|unfinished|still (need|needs) to|haven't)\b|- \[ \]", re.I)
QUESTION_RE = re.compile(r"\?\s*$")
BUDGET_OVERRUN = 1.2          # a run stops once its ledger passes this multiple of its budget
COLUMN_OF = {"blocked": "needs_input", "completed": "completed", "cleared": "cleared"}   # the store-derivable part of the feed's rule
STABILITY_GATE_PCT = 90.0     # the labeller's OWN gate: the fraction that get the same class in both shuffled orders (road (b): the
#                               class is a stratification frame, not a truth, so its agreement with the user's actions is reported, not gated)
NON_ARM_JUDGES = ("grouper", "consolidator", "distiller", "gister")   # the reshaping/summarizing/titling judges an arm's
#   _plan_session drives but the measure does not read: a failure of one (a stuck grouper call, the pilot's 120s-alarm
#   timeouts; a gister call `_followup_title` makes to `gist_llm` when no gist is cached, whose failure leaves label and
#   column unchanged) must NOT mark the arm not comparable. Every OTHER failure row IS counted, an EXCLUSION list not an
#   allowlist: the arm also runs the opener and the placer (neither would be in an allowlist), and three failure kinds never
#   carry judge None (a rate-limited row carries the TIER "triage", a store-quarantined or history-unreadable row carries
#   "romp"), so an allowlist let those through. A row naming a non-arm judge above is the only kind excluded.
FAILURE_KINDS = ("parse", "give-up", "pass-crash", "call", "auth", "rate-limited", "fast-refused", "scratch",
                 "unregistered-caller", "history-unreadable", "store-quarantined", "no-seed-cut")   # judge-errors rows that mean the ending was not judged:
#   a rejected reply, a crashed or refused call, the two pause kinds (`auth`, `rate-limited`), the call-level stand-downs (`fast-refused`,
#   `scratch`, `unregistered-caller`). A `timeout` files under `call`, so it is not named. The `*-unreadable` family and the store-fault pair
#   (`history-unreadable` aside) cannot fire on the arm's road (it hands the judges a store it just wrote and read), and are named only so a
#   future road that can reach them counts them.
# A transient arm-judge model call (a 120s-alarm kill with empty stdout, an empty reply, an error envelope) leaves a `call`
# failure row that marks the arm not comparable. A served call's duration tracks its OUTPUT size, so the kill falls more often
# on the candidate arm's longer closer/planner replies (the 2026-09-22 clean pilot: 2 timeouts baseline, 5 candidate) -- not a
# uniform coin flip: it is a SELECTION effect, symmetric in mechanism but not in realization, and re-sampling a killed long
# reply can shift which done/block verdicts land (plans/judge-prompt-experiments.md line 49). The harness re-samples a
# transiently-failed call up to CALL_ATTEMPTS times (identically for every arm) and gives each attempt HARNESS_ALARM_S rather
# than the module's 120s, so a slow-but-real closer menu finishes; a call that fails EVERY attempt still counts. See
# install_call_retry.
CALL_ATTEMPTS = 3
HARNESS_ALARM_S = 240
KILL_COST_FLOOR_USD = 0.02    # a per killed-attempt cost FLOOR for the budget stop when the usage ledger has no landed row to price a kill (a kill writes no usage row): about the pilot's per-ending mean 0.198 over its ~10 calls per ending, so a kill-heavy arm with zero landed rows still stops (PR 2122 review medium 3)
REPORTED_JUDGES = ("planner", "placer", "closer", "unblocker")   # the arm judges the measures read; the per-judge call counts
#   join the table for all four, so a reader sees exactly what ran.
MEASURED_JUDGES = ("planner", "closer")                          # the subset whose ZERO-call count over the WHOLE arm marks it
#   NOT comparable, however clean its failure count: the planner and the closer run for NEARLY every judged ending (the closer
#   returns before any model call on an empty menu, and the harness calls it only when a turn closed), so over 60+ endings a
#   ZERO aggregate is a silent judge, not "nothing to do" (the 2026-09-23 PLACEMENTS_V bump sealed every old-version seed and a
#   re-pilot read comparable with the planner silent; the precondition rests on the aggregate and makes that impossible). The
#   `placer` and `unblocker` are reported but EXCLUDED from the hard precondition because both are CONDITIONAL: place_llm is the
#   card-first second call (a sub-step's parent inside an already-chosen card) and the unblocker runs only over goals a pass
#   blocked, so a legitimate arm with no sub-step placement or no blocks makes zero of those; requiring them would false-refuse
#   such a run. See seal_pre_cut_adopt.
ID_EPOCH_RE = re.compile(r"^[0-9a-f-]{36}:(\d{9,11})(?::|$)")   # a turn id or segment id carries its epoch second after the fsid


def event_time(ev):
    """A verdict log event's time: `ev_t` (the evidence time) first, `at` (the arrival) second. The events never carry `t`
    (round two of the harness: a filter on `t` kept every event, so a copy carried verdicts from after the cut)."""
    for key in ("ev_t", "at", "t"):
        v = ev.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def id_epoch(ident):
    """The epoch second inside a turn id or a segment id (`<fsid>:<epoch>:<hash>`, a placement key may carry a phase suffix),
    or None when the id has another shape."""
    m = ID_EPOCH_RE.match(str(ident or ""))
    return float(m.group(1)) if m else None


# ── the corpus ──────────────────────────────────────────────────────────────────────────────────
def classify_ending(text):
    """The heuristic pre-pass over a turn's last NON-COMMAND assistant text (the caller passes it): one of CLASSES. The
    selection, not the truth; an offer outranks a question outranks an undone item, and the rest is finished."""
    t = (text or "").strip()
    if not t:
        return "finished"
    if OFFER_RE.search(t):
        return "offer"
    last = [p for p in re.split(r"\n\s*\n", t) if p.strip()]
    if last and QUESTION_RE.search(last[-1].strip()):
        return "question"
    if UNDONE_RE.search(t):
        return "undone"
    return "finished"


def refuse_inside_repo(dest):
    """A destination inside a git checkout is refused: the corpus never enters a repository."""
    p = Path(dest).resolve()
    for anc in (p, *p.parents):
        if (anc / ".git").exists():
            raise SystemExit("refused: %s lies inside a git checkout (%s); the corpus stays out of every repository" % (dest, anc))


def munge(cwd):
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd))


def _records(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _text_of(rec):
    m = rec.get("message") or {}
    c = m.get("content")
    if isinstance(c, str):
        return c
    return "\n".join(b.get("text", "") for b in (c or []) if isinstance(b, dict) and b.get("type") == "text")


def _ts(rec):
    s = rec.get("timestamp")
    if not s:
        return None
    try:
        from datetime import datetime, timezone
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


# Turn boundaries come from the event model ITSELF (kernel/event_model.py), loaded hermetically against a scratch state root
# (never the live one): `parse_session` folds a transcript into the same turns the judges segment, so the builder's endings are
# theirs by construction. A copy of the opener rule drifted (round three of the review: it reproduced three of the fold's
# refusals and none of the command-twin, local-command, skill-content or restore-replay handling), so the copy is gone.
_EM = [None]
_CRED = [None]


def _credentials():
    """kernel/credentials.py, loaded once (the judge module loads the same file under this name). The corpus builder
    resolves Claude Code's apiKeyHelper through it, in the CLI's own precedence (managed settings outrank the user
    file), rather than reading a settings file directly: a managed helper is read by the child from the system path and
    must not be mistaken for a missing one (the 2026-09-22 pre-flight review)."""
    if _CRED[0] is None:
        sys.path.insert(0, str(ROOT / "tests"))
        from romp_load import load_source
        _CRED[0] = sys.modules.get("romp_credentials") or load_source("romp_credentials", str(ROOT / "kernel" / "credentials.py"))
    return _CRED[0]


def _event_model():
    """kernel/event_model.py, loaded once against a throwaway state root so `parse_session` reads no live state (it parses the
    explicit transcript path either way; its bound roots only reach postal-log and states annotations the boundaries do not use)."""
    if _EM[0] is None:
        saved = {k: os.environ.get(k) for k in ("XDG_STATE_HOME", "ROMP_STATE_DIR", "ROMP_POSTAL_CLIENT_ONLY")}
        scratch = tempfile.mkdtemp(prefix="je-em-")
        os.makedirs(os.path.join(scratch, "romp"), exist_ok=True)
        with open(os.path.join(scratch, "romp", "session-hosts"), "w") as f:
            f.write("off")
        os.environ["XDG_STATE_HOME"] = scratch
        os.environ.pop("ROMP_STATE_DIR", None)
        os.environ["ROMP_POSTAL_CLIENT_ONLY"] = "1"
        sys.path.insert(0, str(ROOT / "tests"))
        from romp_load import load_source
        try:
            _EM[0] = load_source("romp_event_model_ends", str(BIN / "romp-event-model"))
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            shutil.rmtree(scratch, ignore_errors=True)   # the module has bound its roots; parse_session reads the explicit path,
            #                                              and the roots reach only absent postal-log/states files (handled as absence)
    return _EM[0]


def _worked_assistant_atom(turn):
    """The judge's own line (kernel/judge.py `_seg_command_worked`): a turn put the MODEL to work iff it has an assistant
    atom that is not the `command`-flagged local-command stdout echo."""
    return any(a.get("type") == "assistant" and not a.get("command") for a in (turn.get("atoms") or []))


def _atom_text(a):
    body = a.get("text") if isinstance(a.get("text"), str) else (a.get("message") or {}).get("content")
    return _text_of({"message": {"content": body}})


def session_endings(path, fsid):
    """(records, [(end_index, start_t, last_text)]) per ENDED turn the model WORKED, from the event model's segmentation. A turn
    with no non-command assistant atom (a bare /model or /usage, an idle compaction with no continuation) or an interrupt tail
    is not an ending: it inflates the finished class and the denominator without a card to judge. `end_index` is the record of
    the turn's last atom (so an interrupted continuation still reads open to the arm's judges); `start_t` is the turn's own
    start, None when the turn has no opener (the ending is then ineligible); `last_text` is the turn's last NON-COMMAND
    assistant text, what classify_ending reads."""
    records = _records(path)
    uuid_idx = {r.get("uuid"): i for i, r in enumerate(records) if r.get("uuid")}
    em = _event_model()
    sess = em.parse_session(str(path), rompuuid=fsid)   # a parse that raises is surfaced (fail loud): the builder counts parse-failed
    out = []
    for turn in sess.get("turns") or []:
        atoms = turn.get("atoms") or []
        if not turn.get("ended") or not _worked_assistant_atom(turn):
            continue
        if atoms and getattr(em, "is_interrupt_record", None) and em.is_interrupt_record(atoms[-1]):
            continue                                  # an interrupted turn is open to the judges, not an ending
        idxs = [uuid_idx[a.get("uuid")] for a in atoms if a.get("uuid") in uuid_idx]
        if not idxs:
            continue
        last_text = next((_atom_text(a) for a in reversed(atoms) if a.get("type") == "assistant" and not a.get("command")), "")
        start = float(turn["t"]) if turn.get("trigger") else None
        out.append((max(idxs), start, last_text))
    out.sort(key=lambda x: x[0])
    return records, out


def turn_ends(records):
    """The record indices that end a turn, via the event model (a temp file, since `parse_session` reads a path). For the
    tests and any caller holding records rather than a path; the builder calls `session_endings` on the transcript directly."""
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    try:
        with os.fdopen(fd, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        fsid = next((r.get("sessionId") for r in records if r.get("sessionId")), "s")
        return [t[0] for t in session_endings(p, fsid)[1]]
    finally:
        os.unlink(p)


def turn_start(records, end_index):
    """The start time of the turn ending at `end_index`, or None when that index is not a turn end."""
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    try:
        with os.fdopen(fd, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        fsid = next((r.get("sessionId") for r in records if r.get("sessionId")), "s")
        return next((st for i, st, _tx in session_endings(p, fsid)[1] if i == end_index), None)
    finally:
        os.unlink(p)


def custom_title(path):
    """The transcript's custom-title record in its head (the judge's `_custom_title`), or None."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(65536).decode("utf-8", "replace")
    except OSError:
        return None
    for line in head.split("\n"):
        if "custom-title" not in line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("type") == "custom-title" and o.get("customTitle"):
            return o["customTitle"]
    return None


_TITLE_MEMO = {}


def fork_lanes(project_dir, name, exclude):
    """The same-customTitle fork transcripts in the session's project directory (the judge's discovery lists each as its own
    lane): every other transcript there whose head carries the session's name as its custom title. The head read is memoized
    across sessions (the judge's `title_memo`), so a project directory shared by many sessions is read once per transcript."""
    out = []
    try:
        entries = sorted(os.listdir(project_dir))
    except OSError:
        return out
    for fn in entries:
        stem = fn[:-6] if fn.endswith(".jsonl") else None
        if not stem or stem in exclude:
            continue
        path = os.path.join(project_dir, fn)
        if path not in _TITLE_MEMO:
            _TITLE_MEMO[path] = custom_title(path)
        if name and _TITLE_MEMO[path] == name:
            out.append(stem)
    return out


def known_fsids(state_root, sid):
    """Every transcript fsid romp has on record for `sid`, read as files the way the kernel's registry reader does: the sid
    itself, the registry's lastSid, each /clear episode head and both ends of every resume fork."""
    out = {str(sid)}
    state_root = Path(state_root)
    reg_path = state_root / "sdk" / (sid + ".json")
    if reg_path.is_file():
        reg = json.loads(reg_path.read_text(encoding="utf-8"))   # a corrupt registry RAISES (the builder counts registry-unreadable)
        if isinstance(reg, dict) and reg.get("lastSid"):
            out.add(str(reg["lastSid"]))
    for sub, pick in (("episodes", lambda r: [r.get("fsid")]),
                      ("states", lambda r: [(r.get("resumeFork") or {}).get(k) for k in ("from", "to")])):
        fp = state_root / sub / (sid + ".jsonl")
        try:
            lines = fp.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            if e.errno in (errno.ENOENT, errno.ENOTDIR):
                continue                              # absent: contributes nothing, as before
            raise                                     # a real read fault (EACCES, EIO): surfaced, the builder counts it
        for line in lines:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict):
                out.update(str(v) for v in pick(r) if v)
    return out


def in_turn_window(t, start_t, cut_t):
    """Whether a verdict time belongs to the ending's turn: from the turn's start to the cut. No closer or planner done carries
    an evidence time past its own cut, so a done after the cut is the next turn's; an ending with no turn start (None) has no
    window and is ineligible."""
    if start_t is None:
        return False
    return start_t <= t <= cut_t


DROP_STORE_FIELDS = ("seams", "closeFails", "confirming", "groupedSig", "consolidatedSig", "rewindSwept", "summaryQuote",
                     "summaryAnchors", "summaryQuoteOff", "warns", "unblockFails", "parseFails", "courierDeferred")   # store-level
#   state from after the cut (a re-key makes the id-keyed ones live under the ending id); the arm's rollup remakes what it needs
DROP_NODE_FIELDS = ("nodeComplete", "blocked", "cleared", "doneWhy", "settledAt", "settledDone", "rolledUp", "summary",
                    "summaryParts", "summaryAnchor", "distilledMt")          # the flag cache and the sealing fields: the arm's rollup remakes them
DROP_STAMPS = ("blockCheckT", "blockCheckDoneT", "delegLookT", "closerLookT", "awaitingAt")   # gate stamps: kept only when before the
#   turn start, else dropped (a blockCheckT at or before the cut hides a node from the unblocker's strict gate, so clamping is not enough)
CLAMP_STAMPS = ("titledT", "servingT")   # capped at the cut, never dropped: dropping titledT buys a title call per node per build


def deep_rekey(obj, old, new):
    """Every string in `obj` (keys and values, nested) with `old` replaced by `new`: the store's ids all carry the session
    id as their prefix (node ids, segment ids, turn ids, placement keys), and the arm parses under the ending id."""
    if isinstance(obj, str):
        return obj.replace(old, new) if old else obj
    if isinstance(obj, list):
        return [deep_rekey(x, old, new) for x in obj]
    if isinstance(obj, dict):
        return {deep_rekey(k, old, new): deep_rekey(v, old, new) for k, v in obj.items()}
    return obj


def store_before(store, cut_t, start_t=None, eid=None):
    """The goal store as the judges held it when the ending's turn OPENED, keyed for the arm. Every id prefix (the session
    id) becomes the ending id, so the arm's segment and turn ids match the seed's (`_placed_key` and the closer's one-shot
    read them; without the re-key the planner re-planned the whole history on every build). The cut is the turn's start
    (`start_t`; the caller passes the previous ended turn's cut for an opener-less ending, or the cut itself when there is no
    previous): nodes born before it, each node's verdict
    log cut to events before it (`ev_t`, then `at`), trails to segments before it, `closedTurns`, `closedSig` and
    placements to turns and segments before it; the ending turn's own prompt-run placement (`#p`, and a delegation's `#d`)
    and the node it points to are kept, so the planner runs the turn's work-run once, as the live pass would; the flag
    cache and the sealing fields are dropped for the arm's rollup (kept, they sealed the seeded top out of the menu and a
    twin was minted); gate stamps at or after the turn start are dropped (`titledT`/`servingT` clamped, never dropped, to spare a re-title);
    store-level state from after the cut (the fields in DROP_STORE_FIELDS) is dropped; nodes whose first trail entry is a
    dropped segment go, and the orphan pass runs to a fixed point. Nothing keyed to the turn or later survives except that
    prompt-run node; what stays is re-keyed to the ending id. A node the user CLEARED before the turn start keeps its clear
    log row (its rollup reads `cleared`); a clear INSIDE the turn is dropped with the rest of the turn's verdicts, so that
    card is the arm's to rule on, open at the seed."""
    sid = str(store.get("rompUuid") or "")
    src = deep_rekey(store, sid, eid) if (eid and sid) else store
    lo = start_t if start_t is not None else cut_t
    placements, targets, dropped_segs = {}, set(), set()
    for k, v in (src.get("placements") or {}).items():
        ep = id_epoch(k)
        if ep is not None and ep < lo:
            placements[k] = v
        elif ep is not None and ep <= cut_t and k.rsplit("#", 1)[-1] in ("p", "d"):
            placements[k] = v                        # the turn's own prompt-run (or delegation) placement: its mint stands; the
            #                                          work-run, live re-plan (#live) and extra-target (#n<i>) keys are the arm's to make
            if isinstance(v, str):
                targets.add(v)
        else:
            dropped_segs.add(k.split("#")[0])
    nodes = {}
    for nid, nd in (src.get("nodes") or {}).items():
        times = [t for t in (event_time(e) for e in (nd.get("log") or [])) if t is not None]
        born = float(nd.get("t") or 0) or (min(times) if times else 0)
        target = nid in targets
        if not (born < lo or (target and born <= cut_t)):
            continue
        trail = list(nd.get("trail") or [])
        if trail and trail[0] in dropped_segs and not target:
            continue                                 # minted from a segment the copy does not hold
        log = [e for e in (nd.get("log") or []) if (event_time(e) or 0) < lo]   # a node is kept by BIRTH; its verdict rows before the turn stay,
        #                                                                          the turn's own drop (no writer emits a `mint` row to keep)
        nd2 = {k: v for k, v in nd.items() if k not in DROP_NODE_FIELDS}
        nd2["log"] = log
        nd2["trail"] = [t for t in trail if (id_epoch(t) or 0) < lo or (target and (id_epoch(t) or 0) <= cut_t)]
        for stamp in DROP_STAMPS:
            try:
                if nd2.get(stamp) is not None and float(nd2[stamp]) >= lo:
                    nd2.pop(stamp)
            except (TypeError, ValueError):
                nd2.pop(stamp, None)
        for stamp in CLAMP_STAMPS:
            try:
                if nd2.get(stamp) is not None and float(nd2[stamp]) > cut_t:
                    nd2[stamp] = cut_t
            except (TypeError, ValueError):
                nd2.pop(stamp, None)
        if nd2.get("mt") and float(nd2["mt"]) > cut_t:
            nd2["mt"] = cut_t
        nodes[nid] = nd2
    changed = True
    while changed:                                   # loop-ok: the orphan pass to a fixed point over a finite dict
        changed = False
        for nid in list(nodes):
            parent = nodes[nid].get("parentId")
            if parent is not None and parent not in nodes:
                nodes.pop(nid); changed = True
    out = {k: v for k, v in src.items()
           if k not in ("nodes", "status", "closedTurns", "closedSig", "placements", "lastNode") + DROP_STORE_FIELDS}
    out["rompUuid"] = eid or sid
    out["nodes"] = nodes
    out["status"] = {}
    out["closedTurns"] = [t for t in (src.get("closedTurns") or []) if (id_epoch(t) or 0) < lo]
    cs = src.get("closedSig")
    out["closedSig"] = {k: v for k, v in cs.items() if (id_epoch(k) or 0) < lo} if isinstance(cs, dict) else cs
    out["placements"] = placements
    if src.get("lastNode") in nodes:
        out["lastNode"] = src["lastNode"]
    return out


def store_with_archive(state_root, sid):
    """The session's live goal store with the CLEARED tops the kernel's compaction moved into goals-archive/<sid>.json unioned
    back into its nodes (they carry their full verdict log there). The four readers of a session's cards (the done times, the
    eligibility mark, tier one and the seed) all take this, so a top the user crossed off is not invisible for being archived."""
    state_root = Path(state_root)
    store = {}
    live = state_root / "goals" / (sid + ".json")
    if live.is_file():
        store = json.loads(live.read_text(encoding="utf-8"))   # a corrupt live store RAISES (fail loud): the caller counts store-unreadable
    if not isinstance(store, dict):
        raise ValueError("goal store top level is not an object")   # a non-object top is a fault, not an empty store
    nodes = dict(store.get("nodes") or {})
    swept = set(store.get("rewindSwept") or {})      # ids the rewind path tombstoned: their archive copies are not cleared subtrees
    arch_path = state_root / "goals-archive" / (sid + ".json")
    if arch_path.is_file():
        arch = json.loads(arch_path.read_text(encoding="utf-8"))   # a corrupt archive RAISES too
        if not isinstance(arch, dict):
            raise ValueError("goal archive top level is not an object")
        for nid, nd in (arch.get("nodes") or {}).items():
            if nid in swept:
                continue                             # a rewound node, not a user-cleared card: never unioned in
            nodes.setdefault(nid, nd)                # the live store wins a shared key; a cleared top lives only in the archive
    store = dict(store)
    store["nodes"] = nodes
    store.setdefault("rompUuid", sid)
    return store


def top_done_times(store):
    """The evidence times of every closer or planner `done` on a top-level node (of a store already unioned with its archive):
    the endings these fall within are the ones the user's later card actions can label (tier one)."""
    out = []
    for nid, nd in (store.get("nodes") or {}).items():
        if nd.get("parentId") is not None:
            continue
        for ev in nd.get("log") or []:
            if ev.get("kind") == "done" and ev.get("src") in ("closer", "planner"):
                t = event_time(ev)
                if t is not None:
                    out.append(t)
    return out


def _corpus_helper(claude_root):
    """(the apiKeyHelper command the corpus should carry, its source) for `claude_root`, resolved through the credentials
    module in Claude Code's own precedence. The source is "user", "managed" or None; only a "user" helper is copied into the
    corpus (see build_corpus). CLAUDE_CONFIG_DIR is pointed at the live claude root for the resolution and restored after."""
    cred = _credentials()
    saved = os.environ.get("CLAUDE_CONFIG_DIR")
    os.environ["CLAUDE_CONFIG_DIR"] = str(claude_root)
    try:
        return cred.api_key_helper(None, operator_only=True), cred.helper_source()
    finally:
        if saved is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = saved


def build_corpus(state_root, claude_root, dest, per_class=75, now=None, min_turns=2):
    """Read the live roots as files, write the corpus under `dest`: per ending a truncated transcript under
    dest/claude/projects/<munged cwd>/<ending id>.jsonl, a names entry, the store before the cut and the override journal
    before the cut under dest/state/romp/, and a manifest of ids, classes and cut times (no text)."""
    refuse_inside_repo(dest)
    state_root, claude_root, dest = Path(state_root), Path(claude_root), Path(dest)
    now = time.time() if now is None else now
    names_dir = state_root / "names"
    picked = {c: [] for c in CLASSES}
    candidates = []
    skipped = {"no-transcript": 0, "few-turns": 0, "unreadable-names-entry": 0, "parse-failed": 0,
               "store-unreadable": 0, "registry-unreadable": 0}
    dones_by_key = {}                                  # store key -> the top-done times of that lane's own store (cached)
    def dones_for(key):
        if key not in dones_by_key:
            dones_by_key[key] = top_done_times(store_with_archive(state_root, key))
        return dones_by_key[key]
    entries = [e for e in (sorted(names_dir.iterdir()) if names_dir.is_dir() else [])]
    registered = {e.name for e in entries}             # every session's sid: a fork lane is never one of these
    claimed = set()                                    # transcript paths an earlier session already took as its own
    for entry in entries:
        try:
            fields = entry.read_text(encoding="utf-8").strip().split("\t")
        except OSError:
            skipped["unreadable-names-entry"] += 1
            continue
        if len(fields) < 2:
            skipped["unreadable-names-entry"] += 1
            continue
        name, cwd = fields[0], fields[1]
        color = fields[2] if len(fields) > 2 else "#888888"
        sid = entry.name
        try:
            own = known_fsids(state_root, sid)         # the sid, its /clear and resume leaves: all keyed under the sid
        except (OSError, ValueError):
            skipped["registry-unreadable"] += 1
            sys.stderr.write("judge-experiment: a registry or lineage record could not be read; the session is skipped\n")
            continue
        pdir = claude_root / "projects" / munge(cwd)
        lanes = set(fork_lanes(pdir, name, registered | own))   # same-titled forks, excluding every registered sid and the own leaves
        found = False
        for fsid in sorted(own | lanes):
            transcript = pdir / (fsid + ".jsonl")
            if not transcript.is_file() or str(transcript) in claimed:
                continue
            claimed.add(str(transcript))
            found = True
            is_lane = fsid in lanes and fsid not in own
            store_key = fsid if is_lane else sid       # a title lane's store/journal is keyed under its own stem; the leaves' under the sid
            try:
                dones = dones_for(store_key)
            except (OSError, ValueError):
                skipped["store-unreadable"] += 1
                sys.stderr.write("judge-experiment: a goal store could not be read; the lane is skipped\n")
                continue
            try:
                records, endings = session_endings(transcript, fsid)
            except Exception:
                skipped["parse-failed"] += 1
                sys.stderr.write("judge-experiment: the event model could not parse a transcript (%s); skipped\n" % type(sys.exc_info()[1]).__name__)
                continue
            if len(endings) < min_turns:
                skipped["few-turns"] += 1
                continue
            prev_cut = None
            for k, (i, start_t, last_text) in enumerate(endings):
                cut_t = _ts(records[i]) or 0
                cls = classify_ending(last_text)
                eligible = any(in_turn_window(t, start_t, cut_t) for t in dones)   # keys on the real start: a None start is never in a window
                seed_start = start_t if start_t is not None else prev_cut   # an opener-less ending's seed cuts at the previous ended turn's end
                candidates.append((sid, name, cwd, color, k, i, cut_t, cls, transcript, eligible, start_t, fsid, store_key, seed_start))
                prev_cut = cut_t
        if not found:
            skipped["no-transcript"] += 1
    # spread across sessions: round-robin over sessions within each class, the tier-one-eligible endings first
    by_class = {c: {} for c in CLASSES}
    for cand in candidates:
        by_class[cand[7]].setdefault(cand[0], []).append(cand)
    for c in CLASSES:
        for want_eligible in (True, False):
            # eligible endings OLDEST first (the user had the most time to act on their cards), the rest newest first;
            # each session's list sorted by the cut TIME first (candidates arrive in stem order, not time), then pop() takes
            # the last: for eligible, oldest; for the rest, newest
            queues = [sorted((x for x in v if x[9] == want_eligible), key=lambda x: x[6]) for v in by_class[c].values()]
            queues = [(list(reversed(q)) if want_eligible else q) for q in queues if q]
            while queues and len(picked[c]) < per_class:
                for q in list(queues):
                    if len(picked[c]) >= per_class:
                        break
                    picked[c].append(q.pop())
                    if not q:
                        queues.remove(q)
    (dest / "state" / "romp" / "names").mkdir(parents=True, exist_ok=True)
    for sub in ("goals", "overrides"):
        (dest / "state" / "romp" / sub).mkdir(parents=True, exist_ok=True)
    (dest / "state" / "romp" / "session-hosts").write_text("off")
    # an arm runs the real judges with CLAUDE_CONFIG_DIR pointed at this claude root (load_judge), so the CLI resolves the key
    # from HERE. The helper is resolved through the credentials module, in Claude Code's own precedence, not by reading a
    # settings file directly (the 2026-09-22 pre-flight review). A USER helper (the operator's ~/.claude settings) is copied so the arm's
    # CLI on the KEY road resolves the key from the corpus root; only the helper key is copied, no other setting or secret. A
    # MANAGED helper is read by the child from the system path and needs no copy, and the LOGIN road needs no helper at all,
    # so both write nothing: reading claude_root/settings.json directly used to copy a user helper a managed one outranks, and
    # to false-refuse a login-road operator whose managed helper it never saw.
    (dest / "claude").mkdir(parents=True, exist_ok=True)
    helper, helper_source = _corpus_helper(claude_root)
    if helper_source == "user" and helper:
        (dest / "claude" / "settings.json").write_text(json.dumps({"apiKeyHelper": helper}))
    manifest = {"built": now, "classes": list(CLASSES), "endings": [], "skipped": skipped}
    for c in CLASSES:
        for sid, name, cwd, color, k, i, cut_t, cls, transcript, eligible, start_t, fsid, store_key, seed_start in picked[c]:
            eid = str(uuid.uuid5(uuid.NAMESPACE_URL, "romp-judge-experiment:%s:%s:%d" % (sid, fsid, k)))
            pdir = dest / "claude" / "projects" / munge(cwd)
            pdir.mkdir(parents=True, exist_ok=True)
            records = _records(transcript)[:i + 1]
            with open(pdir / (eid + ".jsonl"), "w", encoding="utf-8") as fh:
                for r in records:
                    fh.write(json.dumps(r) + "\n")
            (dest / "state" / "romp" / "names" / eid).write_text("%s\t%s\t%s\n" % (name, cwd, color))
            try:
                store = store_with_archive(state_root, store_key)   # the lane's own store (its stem for a title lane, the sid otherwise)
            except (OSError, ValueError):
                continue                              # already counted above for this lane
            before = store_before(store, cut_t, seed_start, eid) if store.get("nodes") else None
            if before is not None:                        # a session with no store yet starts the arm fresh (load_goals mints the shape)
                (dest / "state" / "romp" / "goals" / (eid + ".json")).write_text(json.dumps(before))
            lo = seed_start if seed_start is not None else cut_t   # the journal cut matches the seed's cut (the previous turn's end for an opener-less ending)
            ov = state_root / "overrides" / (store_key + ".jsonl")   # the lane's own journal
            if ov.is_file():
                kept = []
                for l in ov.read_text(encoding="utf-8").splitlines():
                    try:
                        row = json.loads(l)
                    except ValueError:
                        continue
                    if lo is not None and float(row.get("t") or 0) < lo:
                        kept.append(json.dumps(deep_rekey(row, store_key, eid)))
                (dest / "state" / "romp" / "overrides" / (eid + ".jsonl")).write_text("".join(x + "\n" for x in kept))
            manifest["endings"].append({"id": eid, "session": hashlib.sha256(sid.encode()).hexdigest()[:12],
                                        "lane": hashlib.sha256(store_key.encode()).hexdigest()[:12], "turn": k,
                                        "class": cls, "cutT": cut_t, "startT": start_t, "seedStart": seed_start, "tierOneEligible": bool(eligible),
                                        # the store's own identity, fixed AT BUILD so a later move of the live session cannot change which
                                        # store the measures read (this cache is never the repo, a mail or a body); the guard keys on the
                                        # node's own unblocker verdict, so no cwd or transcript leaves are needed
                                        "storeKey": store_key,
                                        "topsBefore": sorted(n.split(":")[-1] for n, nd in (before or {"nodes": {}})["nodes"].items()
                                                             if nd.get("parentId") is None)})
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def annotate_seed_start(corpus):
    """Fill `seedStart` on a manifest built BEFORE the field existed, by build_corpus's own rule: seedStart = the ending's
    startT (a turn with an opener). An OPENER-LESS ending (startT None) is LEFT null, so the run refuses it loudly rather than
    guessing a boundary from a manifest that no longer holds the previous turn's cut. For today's corpus every ending has
    startT, so all are filled and seedStart equals startT. Only fills an ending that has no seedStart yet (a re-run is a
    no-op). Returns the count filled with a non-null value."""
    corpus = Path(corpus)
    refuse_inside_repo(corpus)                       # a mistyped --corpus must never overwrite a manifest in the repo
    mf = corpus / "manifest.json"
    m = json.loads(mf.read_text())
    filled = nonnull = 0
    for e in m["endings"]:
        if "seedStart" not in e:
            filled += 1
            e["seedStart"] = e.get("startT")
            if e["seedStart"] is not None:
                nonnull += 1
    if filled == 0:
        return 0                                     # nothing to fill: leave the manifest byte-for-byte (a re-run is a true no-op)
    tmp = mf.with_name(mf.name + ".tmp")             # atomic: write a temp beside it, then os.replace, so a kill mid-write leaves the index intact
    tmp.write_text(json.dumps(m, indent=1))          # keep build_corpus's indent so the format does not drift
    os.replace(str(tmp), str(mf))
    return nonnull


# ── the arms ────────────────────────────────────────────────────────────────────────────────────
def load_judge(state_root, claude_root, claude_bin):
    """Load the event model and the judge module against the arm's roots: the module binds its roots from the environment
    at import, so the environment is set first and the modules are loaded fresh under names of the run's own."""
    os.environ["XDG_STATE_HOME"] = str(state_root)
    os.environ.pop("ROMP_STATE_DIR", None)
    os.environ["CLAUDE_CONFIG_DIR"] = str(claude_root)
    os.environ["ROMP_CLAUDE_BIN"] = str(claude_bin)
    os.environ.setdefault("ROMP_POSTAL_CLIENT_ONLY", "1")
    sys.path.insert(0, str(ROOT / "tests"))
    from romp_load import load_source
    shared = sys.modules.pop("romp_event_model", None)              # the judge module re-executes the event model under its shared
    try:                                                             # name at import; a caller's own event model (the tests') must not
        load_source("romp_event_model", str(BIN / "romp-event-model"))   # be left bound to the arm's scratch roots
        jd = load_source(JUDGE_MODULE_NAME, str(BIN / "romp-judge"))   # a name of the run's own: the tests' shared-state guard
    finally:                                                         # watches the module named romp_judge
        if shared is not None:
            sys.modules["romp_event_model"] = shared
        else:
            sys.modules.pop("romp_event_model", None)
    return jd


def apply_prompts(jd, prompts):
    """Swap the arm's prompts into the module attributes the calls read; returns what to restore."""
    saved = {}
    for key, text in (prompts or {}).items():
        if key not in PROMPT_KEYS:
            raise SystemExit("unknown prompt key %r (one of %s)" % (key, ", ".join(PROMPT_KEYS)))
        saved[key] = getattr(jd, key)
        setattr(jd, key, text)
    return saved


HELP_REMEDY = ("hand-place an apiKeyHelper into the existing corpus's claude root (do NOT rebuild it: a rebuild re-picks "
               "the endings and orphans the labeller's id-keyed labels), or export a login token into the environment the "
               "run inherits (ANTHROPIC_AUTH_TOKEN or CLAUDE_CODE_OAUTH_TOKEN, e.g. from `claude setup-token`; a file-based "
               "`claude login` does not reach the probe's child, whose config dir is the corpus root), then re-run the arm")


def preflight_auth(jd, model):
    """Before a paid arm walks a single ending: run the judges' OWN auth road once and be the SOLE gate, so a login-road
    operator with no helper is not false-refused (the 2026-09-22 pre-flight review). The probe is built from `jd._judge_cmd`
    and `jd._judge_env` with the resolved billing (`jd._judge_auth(None)`), so `_judge_env` strips the ambient credential and
    the 1Password names and injects only the resolved login tokens, exactly as every judge call does: an environment-only
    credential that would pass a bare `claude -p` fails here as the judges do. It runs the judges' own binary
    (`jd._judge_claude_bin`, from ROMP_CLAUDE_BIN as load_judge exported it), so it takes no `claude_bin`: a caller's would be
    ignored (the 2026-09-22 PR 2022 review, low e). Refuse (SystemExit) when the envelope is an error, reads 'Not logged in', or carries no
    session_id. The storm the gate replaces is loud and free, but it should be SHORT (one refusal, not one per ending).
    Returns a {"cost", "ms", "sessionId"} note the arm ledgers in results.json.

    The refusal QUOTES the CLI's own words (the envelope's `result`, else truncated stdout and stderr, else the exception
    name), never empty braces: a decode failure keeps the process object and quotes it (the 2026-09-22 PR 2022 review, low 3). It names the
    hand-placed-helper remedy, never a rebuild (the 2026-09-22 PR 2022 review, low 4). The probe's timeout tracks the judge module's own alarm at
    `jd.CALL_ALARM_S`, backstopped by five seconds, so raising the alarm does not kill a slow, healthy probe (the 2026-09-22 PR 2022 review, low f)."""
    auth = jd._judge_auth(None)
    env = jd._judge_env("triage", auth=auth)
    # a key-billed probe carries the judges' held key (2026-10-05) and, like every judge call, turns its own helper off
    held = env.get(jd._KEY_SOURCE.env_name, "") if auth == "key" else ""
    cmd = jd._judge_cmd(model, "Reply with the word ok.", auth=auth, key_held=bool(held))
    scratch = jd._ensure_judge_scratch()                        # the same romp-owned scratch cwd every judge call runs in, so the
    #                                                             CLI's per-invocation files do not land in the checkout (the 2026-09-22 PR 2022 review, low 1)
    try:
        p = subprocess.run(cmd, input="ok", env=env, cwd=scratch, capture_output=True, text=True, timeout=jd.CALL_ALARM_S + 5)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SystemExit("refused: the judges' auth probe could not run (%s); %s" % (type(e).__name__, HELP_REMEDY))
    try:
        envelope = json.loads(p.stdout or "")
        envelope = envelope if isinstance(envelope, dict) else None
    except ValueError:
        envelope = None                                          # a decode failure: p is kept, its stdout/stderr quoted below
    blob = (json.dumps(envelope) if envelope is not None else "") + (p.stderr or "")
    if envelope is None or envelope.get("is_error") or not envelope.get("session_id") or "Not logged in" in blob:
        detail = (envelope.get("result") if isinstance(envelope, dict) else None) or \
                 ("stdout %r stderr %r" % ((p.stdout or "")[:200], (p.stderr or "")[:200]))
        raise SystemExit("refused: the judges' auth probe did not authenticate (%s); %s" % (detail, HELP_REMEDY))
    return {"cost": float(envelope.get("total_cost_usd") or 0), "ms": int(envelope.get("duration_ms") or 0),
            "sessionId": envelope.get("session_id")}


def restore_prompts(jd, saved):
    for key, text in saved.items():
        setattr(jd, key, text)


def column_of(status):
    return COLUMN_OF.get(status, "working")


def count_failure_rows(errors_path):
    """Rows on an arm's judge-errors ledger that mean an ARM judge's call failed, was skipped or its reply was rejected
    (`err` in FAILURE_KINDS); the other rows there are the judges' anomaly notes (a stale close, a workless done), which
    are verdict facts, not failures. Every failure row is counted EXCEPT one whose judge is named in `NON_ARM_JUDGES`: those
    reshaping and summarizing judges are not read by the measure, so a stuck one (a grouper timeout) must not mark the arm
    not comparable. This is an exclusion list, not an allowlist of arm judges: the arm also runs the opener and the placer,
    and some failure kinds carry a tier or "romp" rather than the judge name (a rate-limited row carries "triage", a
    history-unreadable row "romp"), all of which an allowlist would wrongly drop; those still mark the row not comparable."""
    n = 0
    try:
        for line in Path(errors_path).open(encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("retried"):
                continue                                          # a re-sampled attempt a later one recovered: not a failure (install_call_retry)
            if r.get("err") in FAILURE_KINDS and r.get("judge") not in NON_ARM_JUDGES:
                n += 1
    except OSError:
        return 0
    return n


def count_non_arm_failure_rows(errors_path):
    """Failure rows the comparability filter EXCLUDES: `err` in FAILURE_KINDS AND `judge` in `NON_ARM_JUDGES`. Counted
    nowhere else (the results record carries only the comparable count), so an excluded fault would be invisible; the measure
    carries this beside the comparable count and the report prints it in the failures cell (the 2026-09-22 PR 2022 review)."""
    n = 0
    try:
        for line in Path(errors_path).open(encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("retried"):
                continue                                          # a recovered re-sample is not a failure, arm or non-arm (install_call_retry)
            if r.get("err") in FAILURE_KINDS and r.get("judge") in NON_ARM_JUDGES:
                n += 1
    except OSError:
        return 0
    return n


def failure_rows_by_kind(errors_path):
    """{err: count} for the ARM failure rows that mark an arm not comparable (err in FAILURE_KINDS, judge not a non-arm
    judge, not a retried attempt). The breakdown behind count_failure_rows, so a reader sees which kind remained after the
    retries: a `call` timeout that failed every attempt, a `parse` the retry does not touch (it re-samples the CALL, not a
    reply that came back and failed to parse downstream)."""
    from collections import Counter
    c = Counter()
    try:
        for line in Path(errors_path).open(encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("retried"):
                continue
            if r.get("err") in FAILURE_KINDS and r.get("judge") not in NON_ARM_JUDGES:
                c[r["err"]] += 1
    except OSError:
        pass
    return dict(c)


def _repo_commit(path):
    """The git commit of the checkout `path` lives in, or None off a checkout: a results file records WHICH shipped prompts a
    run loaded (kernel/judge.py at that commit), so two runs whose prompt files match are still told apart if judge.py moved on
    main between them (PR 2092 review, 2026-09-24). A parse-and-report step, no model call."""
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def install_call_retry(jd, errors_path, counters=None, attempts=CALL_ATTEMPTS):
    """Wrap jd._judge_run_impl so a transiently-failed arm-judge call is re-sampled up to `attempts` times, symmetric across
    arms (every arm-judge call routes through _judge_run_impl). A failed attempt's rows are KEPT but tagged "retried": true
    (count_failure_rows and count_non_arm_failure_rows skip a tagged row), so the ledger still shows every first-attempt kill
    while comparability counts only a call that failed EVERY attempt. `counters` (a dict) tallies, for the measured (arm)
    judges only: firstAttemptKills (calls whose first attempt was a transient kill), retryAttempts (re-samples made),
    recoveredCalls (calls that served a NON-EMPTY reply after a kill; a pause or stand-down after a kill is NOT a recovery),
    killedCalls (EVERY killed attempt: an attempt with last_call_fail set and NOT paused, so a pause after a kill counts as
    one kill, not two; this is the count the budget stop prices, not firstAttemptKills+retryAttempts-recoveredCalls, which
    over-counts a pause-after-kill, PR 2122 review). A served reply, or a pause/stand-down "" (jd._judge_ctx.paused: the rate
    gate, a scratch or auth pause), returns at once and is never retried. Returns the saved original for the caller to restore
    in its finally."""
    saved = jd._judge_run_impl
    ep = Path(errors_path)
    ctr = counters if counters is not None else {}
    for key in ("firstAttemptKills", "retryAttempts", "recoveredCalls", "killedCalls"):
        ctr.setdefault(key, 0)
    def _lines():
        if not ep.exists():
            return []
        return ep.open(encoding="utf-8").read().splitlines(keepends=True)   # a READ failure is SURFACED (raised), never swallowed to []: returning [] then writing it over the ledger would destroy every row (PR 2122 review low a, 2026-09-24)
    def _tag(start, end):
        if start >= end:
            return                                               # an EMPTY range: nothing to tag, never rewrite the ledger for it (PR 2092 review, 2026-09-24)
        lines = _lines()
        for i in range(start, min(end, len(lines))):
            try:
                r = json.loads(lines[i]); r["retried"] = True
                lines[i] = json.dumps(r) + "\n"
            except ValueError:
                pass
        tmp = ep.with_name(ep.name + ".tmp")                     # ATOMIC: write a temp beside it, then os.replace, so a kill mid-write cannot destroy rows already on disk
        with tmp.open("w", encoding="utf-8") as f:               # a write error is SURFACED, not swallowed: a silent failure could shrink the ledger and read the arm comparable
            f.writelines(lines)
        os.replace(str(tmp), str(ep))
    # This wrapper tags rows by LINE RANGE and rewrites the shared errors ledger whole, and the counters are a plain dict, so
    # it is correct ONLY when the arm reaches _judge_run_impl one call at a time. It does: run_arm_inprocess calls
    # _plan_session / _close_turn / _unblock_session directly and in sequence (never the pooled run_plan / run_close /
    # run_unblock orchestrators), and none of those three fans out to a ThreadPoolExecutor internally. The guard below turns
    # that construction into an enforced invariant: a concurrent entry raises rather than silently racing the tag-and-count.
    # If a future path pools these calls, mark rows at WRITE time (a per-call id via a wrapped _log_judge_error) instead.
    _guard = threading.Lock()
    def _retrying(*a, **k):
        if not _guard.acquire(blocking=False):
            raise RuntimeError("install_call_retry: _judge_run_impl reached concurrently; the by-line-range tag-and-count "
                               "assumes the arm's single-threaded loop (run_arm_inprocess). Mark rows at write time instead.")
        try:
            judge = k.get("judge") if "judge" in k else (a[4] if len(a) > 4 else None)
            arm = judge not in NON_ARM_JUDGES                            # count only the measured judges; still retry a non-arm call
            ranges, out = [], None
            for attempt in range(max(1, attempts)):                      # loop-ok: bounded re-sample of one failed call
                if attempt > 0 and arm:
                    ctr["retryAttempts"] += 1
                before = len(_lines())
                out = saved(*a, **k)
                if out or jd._judge_ctx.paused or not jd._judge_ctx.last_call_fail:
                    if ranges:                                           # a kill earlier, now served or stood down: tag its rows out (not the final failure)
                        if arm and out:                                  # a RECOVERY is a NON-EMPTY served reply; a pause/stand-down after a kill is not a recovery (PR 2122 review medium 2)
                            ctr["recoveredCalls"] += 1
                        for s, e in ranges:
                            _tag(s, e)
                    return out
                if not ranges and arm:
                    ctr["firstAttemptKills"] += 1
                if arm:
                    ctr["killedCalls"] += 1              # this attempt was a genuine kill (last_call_fail set, not paused): counted directly, so a later pause is not miscounted a kill (PR 2122 review)
                ranges.append((before, len(_lines())))
            for s, e in ranges[:-1]:                                     # every attempt failed: the LAST rows are the one real failure, tag the earlier ones
                _tag(s, e)
            return out
        finally:
            _guard.release()
    jd._judge_run_impl = _retrying
    return saved


def seal_pre_cut_adopt(jd, fsid, session, store, cut_t):
    """Prepare a cut seed store so the arm plans exactly the ending's OWN turn, independent of the store's recorded
    PLACEMENTS_V. A seed cut from a session at an OLDER placements-identity version is otherwise sealed WHOLE by
    _plan_session's _migrate_placements (its dormant-session replay guard fires on ANY version mismatch), so no arm judge
    plans anything: the 2026-09-23 PLACEMENTS_V 14->15 bump did exactly this, and a re-pilot read comparable with the planner
    silent. Instead: SEAL every ready unit born BEFORE the cut (placements[key]=None), keyed on the unit's TIME (< cut_t),
    which is derivation-independent and so survives the next version bump; then ADOPT the current version so _plan_session
    skips its own seal. "History already planned, plan the turn once" is the fairest stand-in for a live session that kept up
    (a full adoption would replay history and measure flaps we do not care about). Idempotent for an already-current seed: a
    pre-cut unit that _placed_key already dedups is left, and the version stamp is a no-op. Returns the count sealed.
    See plans/judge-prompt-experiments.md (the 2026-09-23 method change)."""
    floor = jd.episode_floor(fsid)
    live = set(seg["id"] for turn in (session.get("turns") or []) for seg in jd._segs(turn, store))
    sealed = 0
    for u in jd.plan_units(session, store, floor=floor, lazy_text=True):
        if u[2] is not None and u[2] < cut_t:                     # a unit born before the ending's cut: pre-turn history, seal it
            key = jd._unit_key(u[0], u[1])
            if not jd._placed_key(store["placements"], key, live, floor=floor):
                store["placements"][key] = None
                sealed += 1
    store["placementsV"] = jd.PLACEMENTS_V                        # adopt: _plan_session will not run its whole-store seal
    return sealed


# The planner-unit kinds a MODEL-CALL fault would silence: the excuse counts only the units THE ARM's planner plans with an
# UNCONDITIONAL planner MODEL CALL from a sealable own-turn position. plan_units yields five phases; only these two reach
# plan_llm unconditionally once the unit is applied (kernel/judge.py _plan_session), and are reachable by the pre-cut seal, so
# an unplanned one in the own turn is a fault (a seal-boundary fault, a dead planner):
#   work  an ended segment's work   -> always a planner call
#   live  an open segment's re-plan  -> always a planner call
# EXCLUDED (an unplanned own-turn instance is NOT a fault, so it never blocks the excuse):
#   prompt      the OPENER judge's unit (a workless opening message) -- never a planner call.
#   nudge       a romp Nudge on a goal: resolve-or-noop. It CAN drive a planner call (plan_llm) on a still-working goal, so it
#               is not excluded as never-plannable; but the seal reaches only PRE-CUT units, so an OWN-TURN nudge is never
#               sealed -- if the arm left it unplanned (its own record: no planner call), the nudge took one of the kernel's
#               three no-call roads (no resolvable target in the store; the moot retirement when the goal is already done; the
#               goal not open, e.g. view-cleared), never a missed call. (kernel/judge.py _plan_session nudge branch.)
#   delegation  a peer segment filed under the COURIER's goal. The ARM runs no courier (run_arm_inprocess never plants one), and
#               store_before drops the own turn's bare seg_id placement, so the delegation branch reads its target UNSET and
#               `continue`s with NO planner call in EVERY arm. An unplanned own-turn delegation is thus a HARNESS LIMITATION
#               symmetric across arms, not a fault -- delegation endings' planner behaviour is unmeasured by the experiment
#               (named as a residual in the plan).
_PLANNER_CALL_PHASES = ("work", "live")


def own_turn_plannable_count(jd, fsid, session, store):
    """The planner-MODEL-CALL units of the ending's OWN final turn (the parse's last turn), before any seal. The ground for the
    corpus-unplanned excuse (manager 2026-09-24): an ending unplanned in every arm is excused ONLY when this count is zero, so
    the excuse rests on the ending's own content, not on cross-arm agreement (which cannot tell a nothing-to-plan turn from one
    a harness fault silenced in every arm). Counts only the kinds that drive a planner model call from a sealable own-turn
    position (`_PLANNER_CALL_PHASES`: work / live); a `prompt` (opener), a `nudge` (resolve-or-noop; an own-turn nudge is never
    sealed, so an unplanned one is one of the kernel's no-call roads, not a fault) and a `delegation` (the arm runs no courier,
    so it never drives a planner call in any arm) are NOT counted, per the premise stated at _PLANNER_CALL_PHASES. Read from the parsed turn, NEVER the seed boundary, whose fault this catches: a seed boundary at or
    past the cut seals the own turn's units, so a count taken AFTER the seal reads zero for a faulted ending, while this count,
    over the last turn's own segments, still finds them. A unit the seed store already places is not counted."""
    turns = session.get("turns") or []
    if not turns:
        return 0
    floor = jd.episode_floor(fsid)
    placements = store.get("placements") or {}
    last_ids = {seg["id"] for seg in jd._segs(turns[-1], store)}                      # the ending's own turn: the truncated transcript's last turn
    live = {seg["id"] for turn in turns for seg in jd._segs(turn, store)}
    n = 0
    for u in jd.plan_units(session, store, floor=floor, lazy_text=True):
        seg_id, phase = u[0], u[1]
        if seg_id not in last_ids or phase not in _PLANNER_CALL_PHASES:              # not the own turn, or a kind that does not drive a planner call from a sealable position (prompt/nudge)
            continue
        if not jd._placed_key(placements, jd._unit_key(seg_id, phase), live, floor=floor):
            n += 1
    return n


def calls_by_judge(usage_path):
    """{judge: model-call count} from an arm's judge-usage ledger, so the report can show the per-judge counts and the
    comparability precondition can refuse an arm in which a MEASURED_JUDGE was silent."""
    from collections import Counter
    c = Counter()
    try:
        for line in Path(usage_path).open(encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("err"):
                continue                                          # a failed call (error envelope) is not the judge running: an all-failed judge stays silent
            j = r.get("judge")
            if j:
                c[j] += 1
    except OSError:
        pass
    return dict(c)


def ledger_cost(usage_path):
    """(dollars, calls, mean ms) from the arm's own usage ledger."""
    cost, n, ms = 0.0, 0, 0.0
    if not Path(usage_path).is_file():
        return 0.0, 0, 0.0
    for line in Path(usage_path).read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        cost += float(r.get("cost") or 0); n += 1; ms += float(r.get("ms") or 0)
    return cost, n, (ms / n if n else 0.0)


def top_scored(nd, lo, now):
    """Whether a top-level node is SCORED for this ending: born in the ending's OWN turn (`t` >= the cut boundary `lo`, which
    is seedStart, else startT), OR the arm filed a done/block on it this build. `lo` is seedStart NOT startT so an OPENER-LESS
    ending (startT None) still scores its own-turn tops, a top the arm MINTS without a verdict included: reverting the boundary
    to startT would read None and drop that minted-no-verdict top from scoring (PR 2099 review low 3, 2026-09-24). record_verdict
    stamps a verdict's `at` with the pass's `now`, so a row the ARM wrote this build reads `at` >= now while a seed row kept its
    earlier `at`; that parts them. A lift RIDER's done keeps the lift's own ev_t (before the turn), so an ev_t test missed its
    leak; the arm's filing catches it (the rider residual: a done the arm files for an EARLIER turn's lift also reads scored,
    left as the one over-count)."""
    born = float(nd.get("t") or 0)
    arm_filed = any(ev.get("kind") in ("done", "block") and float(ev.get("at") or 0) >= now for ev in (nd.get("log") or []))
    return bool((lo is not None and born >= lo) or arm_filed)


def run_arm_inprocess(corpus, arm, prompts_file, run_root, budget_usd, claude_bin, now=None, builds=3):
    """One arm over the corpus, in this process: the corpus state copied under run_root/<arm>/state, the judge module
    loaded against it, the arm's prompts swapped in, and per ending and per build the planner, the closer over the last
    closed turn and the unblocker, the tops' columns recorded. THREE builds from the same store copy give the majority
    column per card and the residual flaps (the pilot's flaps are model sampling; a card's column is the majority of the
    three, the flap figure the disagreement that remains). The arm's ledger is read after every ending and the run stops
    past BUDGET_OVERRUN times the budget."""
    corpus, run_root = Path(corpus), Path(run_root)
    refuse_inside_repo(run_root)
    manifest = json.loads((corpus / "manifest.json").read_text())
    arm_root = run_root / arm
    state = arm_root / "state"
    if state.exists():
        shutil.rmtree(state)
    shutil.copytree(corpus / "state", state)
    jd = load_judge(state, corpus / "claude", claude_bin)
    prompts = json.loads(Path(prompts_file).read_text()) if prompts_file else {}
    now = int(time.time()) if now is None else int(now)
    results = {"arm": arm, "prompts": sorted(prompts), "endings": {}, "stopped": None, "failures": 0, "closerNone": 0,
               "endingsUnplanned": [], "endingsCrashed": [],   # endings that planned nothing / crashed a pass: either marks the arm not comparable, both named (2026-09-23)
               "buildsPerCard": builds}     # stamp the per-card build count so a reader knows a card's column is the majority of N (the 2026-09-22 PR 2022 review, MED 1)
    results["preflightProbe"] = preflight_auth(jd, jd.TRIAGE_MODEL)   # refuse before the first ending if the arm
    #                            cannot authenticate; the probe's own cost is noted here, outside the arm's judge ledger
    saved = apply_prompts(jd, prompts)
    results["promptHashes"] = {k: hashlib.sha256((getattr(jd, k) or "").encode("utf-8")).hexdigest() for k in PROMPT_KEYS}   # the EFFECTIVE text per key AFTER the swap (a baseline key hashes the shipped text), so a results file identifies which text ran where the prompt KEYS alone could not (PR 2092 review, 2026-09-24)
    results["judgePyCommit"] = _repo_commit(ROOT)   # the harness/judge.py commit the run loaded: two runs with matching prompt files still differ if judge.py moved
    # arm runs compare WITHOUT regrouping/consolidation: the grouper and consolidator are not measured judges, their
    # non-deterministic reshaping of the top set is the largest flap component, and the grouper's stuck model calls are the
    # 120s-alarm timeouts. _plan_session calls _group_store as a module global after every placement, so patching it here
    # no-ops the grouper; _consolidate_store is patched too though the harness never runs the consolidator pass.
    saved_group, saved_consolidate = jd._group_store, jd._consolidate_store   # restored in the finally with the prompts
    jd._group_store = lambda *a, **k: 0
    jd._consolidate_store = lambda *a, **k: 0
    usage = jd.USAGE
    errors_path = Path(jd.ERRORS)
    retry_counters = {}
    saved_run_impl = install_call_retry(jd, errors_path, retry_counters)   # re-sample a transiently-failed arm call; restored in the finally
    saved_alarm = jd.CALL_ALARM_S
    jd.CALL_ALARM_S = HARNESS_ALARM_S                      # a slow-but-real closer/planner menu finishes rather than a 120s kill
    def error_rows():
        return count_failure_rows(errors_path)
    def flush():
        arm_root.mkdir(parents=True, exist_ok=True)
        (arm_root / "results.json").write_text(json.dumps(results, indent=1))
    try:
        for e in manifest["endings"]:
            eid = e["id"]
            path = next(iter((corpus / "claude" / "projects").glob("*/%s.jsonl" % eid)), None)
            if path is None:
                continue
            lo = e.get("seedStart")                           # the seed's own cut: the ending's turn start, or the previous turn's end for an
            if lo is None:                                     # OPENER-LESS continuation, as build_corpus recorded it. An older manifest carries
                lo = e.get("startT")                           # no seedStart; run `annotate` to fill it, or its startT is the same value here. NO
            #                                                   third derivation: the boundary is seedStart, else startT, else the loud refusal below
            #                                                   (never cutT, which would seal the ending's own turn).
            if lo is None:                                     # neither present (a degenerate opener-less ending): refuse LOUDLY, count against
                jd._log_judge_error("planner", eid, "no-seed-cut", note="opener-less ending with no seedStart or startT; annotate the corpus")
                results["failures"] += 1                       # comparability, and NAME it so it is never silently sealed on its own turn
                results["endingsUnplanned"].append(eid)
                results["endings"][eid] = {"class": e["class"], "builds": []}
                flush()
                continue
            seed_path = corpus / "state" / "romp" / "goals" / (eid + ".json")
            seed = seed_path.read_text() if seed_path.is_file() else None
            builds_out = []
            plannable = None                                     # the ending's own-turn planner-unit count, recorded once so the report grounds the excuse without re-parsing (2026-09-24)
            retry0 = dict(retry_counters)                        # snapshot the arm-wide retry counters so this ending's DELTA is recorded per ending (the selection effect: an ending with more kills re-drew more replies, PR 2092 review)
            planner0 = calls_by_judge(usage).get("planner", 0)   # per-ending precondition: this ending must make at least one planner call
            try:
                try:
                    for _b in range(builds):
                        errs0 = error_rows()
                        target = state / "romp" / "goals" / (eid + ".json")             # the same store copy for every build
                        if seed is None:
                            target.unlink(missing_ok=True)
                        else:
                            target.write_text(seed)
                        jd.parse_cache_clear()
                        session = jd.parsed_session(eid, [str(path)], now)
                        turns = session.get("turns") or []
                        store = jd.load_goals(eid)
                        if plannable is None:                    # BEFORE the seal, from the ending's own final turn: the ground for the excuse
                            plannable = own_turn_plannable_count(jd, eid, session, store)
                        seal_pre_cut_adopt(jd, eid, session, store, lo)   # every ending (opener-less included): plan its own turn, independent of the seed's placementsV (2026-09-23)
                        closed = jd._session_settled(eid, str(path), session, store, now=now)   # the settled gate over the ending's own transcript
                        jd.rollup_status(store, closed, now=now)                            # the flags from the seed's diary, before the first menu
                        jd.save_goals(eid, store)
                        jd._plan_session(eid, str(path), now)
                        store = jd.load_goals(eid)
                        closed_turns = [t for t in turns if not jd._turn_open(t, turns)]
                        if closed_turns:
                            seg_by_id = {seg["id"]: seg for turn in turns for seg in jd._segs(turn, store)}   # the goal-history map production sends
                            rows_before = error_rows()
                            if jd._close_turn(store, closed_turns[-1], seg_by_id=seg_by_id) is None:
                                results["closerNone"] += 1
                                if error_rows() == rows_before:
                                    results["failures"] += 1      # the closer gave nothing and filed no row (the cap road): counted once here
                        jd.rollup_status(store, closed, now=now)
                        jd.save_goals(eid, store)
                        jd._unblock_session(eid, str(path), now)
                        store = jd.load_goals(eid)
                        tops = {}
                        for nid, nd in (store.get("nodes") or {}).items():
                            if nd.get("parentId") is not None:
                                continue
                            tops[nid.split(":")[-1]] = {"column": column_of((store.get("status") or {}).get(nid)), "scored": top_scored(nd, lo, now)}
                        builds_out.append(tops)
                        results["failures"] += error_rows() - errs0
                except Exception as ex:                       # one ending's build must not abort the arm: file it, keep the rest
                    jd._log_judge_error("planner", eid, "pass-crash", note=repr(ex)[:200])
                    results["failures"] += 1
                    results["endingsCrashed"].append(eid)     # NAME a crashed ending so it shows in the cell beside pass-crash (never dropped silently)
                    results["endings"][eid] = {"class": e["class"], "builds": builds_out, "crashed": repr(ex)[:200],
                                               "plannableUnits": plannable,
                                               "retry": {k: retry_counters.get(k, 0) - retry0.get(k, 0) for k in retry_counters}}
                    flush()
                    continue
                if calls_by_judge(usage).get("planner", 0) == planner0:   # this ending planned nothing (a sealed-whole seed, a refusal): never silent
                    results["endingsUnplanned"].append(eid)
                results["endings"][eid] = {"class": e["class"], "builds": builds_out, "plannableUnits": plannable,
                                           "retry": {k: retry_counters.get(k, 0) - retry0.get(k, 0) for k in retry_counters}}   # this ending's own re-samples (selection effect)
                flush()
            finally:
                jd.em.evict_document(str(path))   # crash OR success: drop this ending from both event-model caches (low a, 2026-09-24)
            cost, n, _ = ledger_cost(usage)
            killed = retry_counters.get("killedCalls", 0)         # attempts a timeout KILLED (the wrapper's direct tally): billed by the API but ABSENT from the usage ledger; NOT fak+retry-rec, which over-counts a pause after a kill (PR 2122 review)
            per_kill = (cost / n) if n else KILL_COST_FLOOR_USD    # the mean landed call cost, else a documented floor when NO row landed (est would be 0 and never stop, PR 2122 review medium 3)
            est = cost + killed * per_kill                         # count the unseen killed attempts toward the stop, so a kill-heavy arm still stops even with an empty ledger (PR 2092 review, 2026-09-24)
            if budget_usd is not None and est > budget_usd * BUDGET_OVERRUN:
                results["stopped"] = {"after": eid, "cost": round(cost, 4), "estCost": round(est, 4), "killedAttempts": killed, "budget": budget_usd}
                break
        results["finished"] = results.get("stopped") is None   # finished ONLY when the loop ran EVERY manifest ending; a budget stop leaves the run partial (PR 2121 verifier low d, 2026-09-24)
    finally:
        restore_prompts(jd, saved)
        jd._group_store, jd._consolidate_store = saved_group, saved_consolidate   # restore the grouper/consolidator (in-process safety)
        jd._judge_run_impl = saved_run_impl                                       # restore the un-retried call and the module alarm
        jd.CALL_ALARM_S = saved_alarm
        try:
            cost, n, mean_ms = ledger_cost(usage)
            results["cost"] = round(cost, 4); results["calls"] = n; results["callMsMean"] = round(mean_ms)
        except Exception as e:
            results["costError"] = type(e).__name__    # a ledger read that raises is RECORDED, not swallowed: a reader tells a free arm from a broken tally
        results["nonArmFailures"] = count_non_arm_failure_rows(errors_path)   # excluded from comparability, surfaced beside it (review 2026-09-22 PR 2022)
        results["retry"] = retry_counters                          # firstAttemptKills / retryAttempts / recoveredCalls / killedCalls, so the comparability claim is visible (manager 2026-09-23)
        results["failuresByKind"] = failure_rows_by_kind(errors_path)   # the remaining (arm, un-retried) failures by kind: a lone `parse` stays named
        results["callsByJudge"] = calls_by_judge(usage)                  # per-judge call counts: an arm with a silent MEASURED_JUDGE is not comparable (manager 2026-09-23)
        flush()                                      # results.json is written in the finally, whatever raised in the loop or after it
    return results


def run_arm(corpus, arm, prompts_file, run_root, budget_usd, claude_bin, now=None):
    """The arm in a subprocess of its own (the judge module binds its roots at import)."""
    cmd = [sys.executable, str(Path(__file__).resolve()), "run-arm", "--corpus", str(corpus), "--run-root", str(run_root),
           "--arm", arm, "--claude-bin", str(claude_bin)]
    if prompts_file:
        cmd += ["--prompts", str(prompts_file)]
    if budget_usd is not None:
        cmd += ["--budget-usd", str(budget_usd)]
    if now is not None:
        cmd += ["--now", str(int(now))]
    subprocess.run(cmd, check=True)
    return json.loads((Path(run_root) / arm / "results.json").read_text())


# ── the measures and the report ────────────────────────────────────────────────────────────────
def _scored(build):
    """{card: column} for the cards the ending scores (born, or given a done or block verdict, in the ending's turn)."""
    out = {}
    for nid, v in (build or {}).items():
        if isinstance(v, dict):
            if v.get("scored"):
                out[nid] = v.get("column")
        else:
            out[nid] = v                                 # an older results file: every card counted
    return out


_UNSCORED = object()   # a card's per-build value when THAT build did not score it: the measure gives every card a value per
#                        build (the scored column or this sentinel), so the majority is over ALL builds and a card scored in a
#                        minority of builds does not win one (review 2026-09-22 MED 1)


def _majority(cols):
    """The card's column across ALL builds: a real column ONLY on a STRICT majority of the builds (its count more than half),
    else no column (the `_UNSCORED` sentinel). No single build decides, so the result is order-independent: an all-different
    set, a real tie (two builds one each, three builds three columns) and a card scored in a minority of builds all read no
    column; a real 2-of-3 wins. The caller passes a value per build, an unscored build as the sentinel. The scored column
    decides leaks / false interrupts / answered-then-cleared; a build disagreement (the sentinel included) is the flap figure
    (the 2026-09-22 PR 2035 review: the earlier first-seen tie rule let the first build decide)."""
    if not cols:
        return None                                             # a card scored in no build has no column
    counts = {}
    for c in cols:
        counts[c] = counts.get(c, 0) + 1
    for c in cols:                                              # at most one column can hold a strict majority; the sentinel is never a scored column
        if c is not _UNSCORED and counts[c] * 2 > len(cols):
            return c
    return _UNSCORED                                            # no strict majority: no column (no leak / no false interrupt); the flap still counts


PLACEMENT_KINDS = ("done", "block", "awaiting")   # a top-level verdict the live judges filed in the ending's turn: the placement
#                                                   whose own ev_t/at bounds which later user gestures count (never the arm's wall clock)
UNBLOCK_KINDS = ("unblock",)                       # the kernel lifts a block with an `unblock` event, but from several sources (below)
MUTE_CLEAR_WHY = "hidden from the feed"            # kernel/kernel.py's hideFromFeed mute (its _HIDDEN_FROM_FEED_WHY): excluded EXACTLY, never as a
#   substring. The ordinary feed Clear / Clear-all stamps the generic "cleared from the feed", which IS the user's own cross-off and counts.


def _fault(faults, store_key, kind):
    if faults is not None:
        faults.append((hashlib.sha256(str(store_key).encode()).hexdigest()[:12], kind))


def _is_user_crossoff(op):
    """The user's own cross-off of a card: a `clear`/`resolve` by the user whose why is NOT the hideFromFeed mute's (an
    EXACT match; the ordinary feed Clear/Clear-all's generic why counts). A mute journals a src-user clear per open top,
    which is not the user crossing the card off. Shared by the measure and tier_one_label so neither reads a mute as a finish."""
    return op.get("op") in FINISHED_OPS and op.get("src") in (None, "user") and (op.get("why") or "") != MUTE_CLEAR_WHY


def _answered_unblock(ev):
    """An unblock event by the unblocker JUDGE (`src` "unblocker"), which lifts a block it ruled ANSWERED OR MOOT (both under
    one why prefix). This is the only unblock that suppresses a false interrupt. NOT the topic-blind "you re-engaged" user
    unblock (the user typed anything), a reopen-ancestor lift, the planner's new-work unblock, or a mechanical romp unblock:
    those lift a block without the judge ruling it answered or moot. The user's reply through the card's box records the
    reply-unblock why on DESCENDANTS only, never the top, so a box reply on a top lands as a followup (a re-open) and needs
    no arm here."""
    return ev.get("kind") in UNBLOCK_KINDS and ev.get("src") == "unblocker"


def _read_ops(live_state, store_key, faults):
    """The override-journal rows for a store, or None when the journal cannot be read AT ALL: an OSError (unreadable) or a
    decode error (a ValueError, e.g. an invalid UTF-8 byte) is recorded as a fault and the ending is unscorable. A single
    TORN row (a rejected JSON line) is recorded as a fault and skipped, the rest read: a torn tail must not swallow the row
    after it silently."""
    p = Path(live_state) / "overrides" / (store_key + ".jsonl")
    if not p.is_file():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()      # UnicodeDecodeError is a ValueError: a read-level fault, not a silent miss
    except (OSError, ValueError) as e:
        _fault(faults, store_key, type(e).__name__)
        return None
    ops = []
    for line in lines:
        if not line.strip():
            continue
        try:
            ops.append(json.loads(line))
        except ValueError:
            _fault(faults, store_key, "torn-journal-row")       # a rejected line is a recorded fault, never a silent skip
    return ops


def placement_gestures(live_state, store_key, start_t, cut_t, faults=None):
    """The user's OWN later actions on each top the live judges placed in the ending's turn, from the live store and journal
    only. Per top-level node with a verdict in the turn's window, the gesture boundary is the ending's CUT (never the
    placement's own ev_t, which for a closer done is the turn's start, nor the arm's clock: review item 2); a
    `followup`/`unclear`/`restore` after the cut re-opened the card; a user `clear`/`resolve` after the cut (a cross-off, by
    the shared `_is_user_crossoff`: a src-user clear whose why is not the mute's exact why) crossed it off. A cross-off is a
    false interrupt UNLESS the unblocker JUDGE (src "unblocker") lifted the block, having ruled it ANSWERED or MOOT, strictly
    after the cut and before the first cross-off (a same-second lift is not counted as after, so a ruling at the cut's own
    second reads conservatively as a false interrupt): that lift is answered-then-cleared, the
    card having done its job. Returns None when the live store is absent/corrupt or its journal cannot be read (a fault is
    recorded for the readable-but-broken cases; a single torn journal row is a fault and skipped, not a return); {} when the
    store is present but the live judges placed no top in the turn; else {suffix: {...}}. The suffix (`gN`) is the join key:
    the results carry `<ending id>:gN` and the live store `<store key>:gN`, so the full keys never coincide by construction;
    within one store every top-level node has a distinct suffix, so the suffix is unambiguous."""
    live_state = Path(live_state)
    goals_f = live_state / "goals" / (store_key + ".json")
    arch_f = live_state / "goals-archive" / (store_key + ".json")
    if not (goals_f.is_file() or arch_f.is_file()):
        return None                                             # the live session no longer lists: unresolvable, no fault
    if goals_f.is_file():
        try:
            json.loads(goals_f.read_text(encoding="utf-8"))     # a corrupt LIVE store is a fault, recorded, never read as "nothing applies"
        except (OSError, ValueError) as e:
            _fault(faults, store_key, type(e).__name__)
            return None
    try:
        store = store_with_archive(live_state, store_key)       # the archive alone may hold a session's tops (all cleared); it carries the clear
    except (OSError, ValueError) as e:
        _fault(faults, store_key, type(e).__name__)
        return None
    ops = _read_ops(live_state, store_key, faults)
    if ops is None:                                             # the journal could not be read (unreadable or a decode error): unscorable
        return None
    out = {}
    for nid, nd in (store.get("nodes") or {}).items():
        if nd.get("parentId") is not None:
            continue
        log = nd.get("log") or []
        placed = [event_time(ev) for ev in log
                  if ev.get("kind") in PLACEMENT_KINDS and event_time(ev) is not None and in_turn_window(event_time(ev), start_t, cut_t)]
        if not placed:
            continue                                            # the live judges placed this top outside the turn: no placement time to bound gestures
        # the gesture boundary is the ending's CUT, not the placement's own ev_t (the 2026-09-22 PR 1960 note, item 2): a closer done's ev_t is the
        # turn's START, so keying on it counted a mid-turn gesture (made before the card was placed) as answering it. Every
        # arm verdict uses evidence up to the cut, so a user gesture is "later" only when it lands after the cut.
        t_place = cut_t

        def on_node(o):
            if o.get("node") == nid:
                return True
            nn = o.get("nodes")                                 # the restore row carries `nodes`, a dict, not `node`
            return isinstance(nn, dict) and nid in nn
        later = [o for o in ops if on_node(o) and float(o.get("t") or 0) > t_place]
        reopened = any(o.get("op") in NOT_FINISHED_OPS for o in later)
        clear_times = [float(o.get("t") or 0) for o in later if _is_user_crossoff(o)]   # the mute's clears are excluded by the shared predicate
        crossed_off = bool(clear_times)
        first_clear = min(clear_times) if clear_times else None
        # the unblocker JUDGE lifted the block (ruling it answered or moot), STRICTLY after the cut and before the first
        # cross-off (an unblock at or before the cut's second is not the reply this clear crossed off unanswered; a lift
        # after the clear is not it either)
        unblock_answered = any(_answered_unblock(ev) and event_time(ev) is not None and t_place < event_time(ev)
                               and (first_clear is None or event_time(ev) < first_clear) for ev in log)
        cleared_no_reply = crossed_off and not reopened and not unblock_answered
        answered_then_cleared = crossed_off and not reopened and unblock_answered
        out[nid.split(":")[-1]] = {"reopened": reopened, "clearedNoReply": cleared_no_reply,
                                   "answeredThenCleared": answered_then_cleared,
                                   "gestured": bool(reopened or crossed_off)}
    return out


def measure(manifest, results, live_state, labels=None, labels_state="absent", excused=None):
    """Per arm, scored against the user's OWN later actions on the live cards (road (b): NOT the labeller's class, which the
    pilot showed is not a truth about an ending's shape). Leaks into Completed: the arm placed a top completed that the user
    then re-opened. False interrupts: the arm left a top needs_input that the user plainly crossed off, with no re-open and
    no unblocker ruling that a reply answered the block (a topic-blind later turn does NOT suppress). answeredThenCleared: a
    needs_input top the kernel ruled answered before the user cleared it (the card did its job), a separate count. A card's
    column for scoring is the MAJORITY of the (three) builds of the same store copy, over a value per build (the scored
    column or the `_UNSCORED` sentinel, so a card scored in a minority of builds wins no column, review MED 1); the FLAP
    figure is the residual disagreement, a card whose per-build values are not all equal (the sentinel included). Failures: a
    row with any is not comparable. The live root is read only, and the store identity comes from the manifest (fixed at
    build). Every ending falls in exactly one column so they partition: unresolved (live store gone or unreadable), unplaced
    (resolved, no card placed in the turn), gestured (a placed card the user acted on, a re-open or a cross-off) or untouched
    (a placed card the user did nothing to). Every ending contributes flaps and cost."""
    by_id = {e["id"]: e for e in manifest["endings"]}
    leaks = false_interrupts = answered_then_cleared = flaps = gestured = untouched = unresolved = unplaced = 0
    leaks_by_class = {c: 0 for c in CLASSES}                     # leaks and false interrupts split by the ending's heuristic
    fi_by_class = {c: 0 for c in CLASSES}                        # class: the loose-ended strata (offer/question/undone) vs finished
    labels = labels or {}                                        # {ending id: labeller class} when labels.json exists (the 2026-09-22 PR 2022 round-two review, low 4)
    leaks_by_labeller = {}                                       # the same splits keyed on the LABELLER's class, so the landing bar's
    fi_by_labeller = {}                                          # strata sentence (read from the labeller) is derivable from the outputs
    attribution = []                                             # per-ending: id, arm, leak, false interrupt, both class keyings
    faults = []
    builds_n = results.get("buildsPerCard") or max((len(r.get("builds") or []) for r in results["endings"].values()), default=0)
    excused = set(excused or ())                                  # endings unplanned in EVERY arm whose own turn had nothing to plan: they
    #                                                              govern the comparability verdict ONLY, never the metrics. Every ending is
    #                                                              scored (an excused ending is in every arm's results, so the arms already
    #                                                              compare on one set; skipping it would drop the closer's and unblocker's
    #                                                              verdicts the candidates exist to change, manager 2026-09-24 reversing the exclusion)
    for eid, r in results["endings"].items():
        e = by_id.get(eid, {})
        store_key = e.get("storeKey")
        builds = [_scored(b) for b in r["builds"]]
        if len(builds) < builds_n:                              # a crashed ending finished fewer builds: pad to buildsPerCard with the
            builds += [{}] * (builds_n - len(builds))           # sentinel (the 2026-09-22 PR 2035 review, low 4), or one surviving build would decide the column
        # a value per build for EVERY card: the scored column where the build scored it, else the _UNSCORED sentinel (review
        # MED 1). The column is a STRICT majority over ALL builds, so a card scored in a minority of builds wins no column;
        # a flap is any disagreement across the builds, the sentinel included (a done in one build of three against none is a
        # flap the base missed by reading only the scored builds).
        cards = set().union(*[set(b) for b in builds]) if builds else set()
        vals_of = {nid: [b.get(nid, _UNSCORED) for b in builds] for nid in cards}
        majority = {nid: mv for nid, mv in ((nid, _majority(vals)) for nid, vals in vals_of.items()) if mv is not _UNSCORED}
        flaps += sum(1 for vals in vals_of.values() if len(set(vals)) > 1)
        if store_key:
            g = placement_gestures(live_state, store_key, e.get("seedStart", e.get("startT")), float(e.get("cutT") or 0), faults=faults)   # seedStart so an opener-less ending's window is [prev turn end, cut], not empty (2026-09-23)
            if g is None:
                unresolved += 1                                 # the live store is gone or unreadable (a fault is recorded for the unreadable cases)
            elif not g:
                unplaced += 1                                   # resolved, but the live judges placed no top in the turn: nothing to score
            else:
                if any(v.get("gestured") for v in g.values()):  # an ending the user actually acted on (a re-open or a cross-off)
                    gestured += 1
                else:
                    untouched += 1                              # a placed card the user did nothing to: so the columns partition the endings
                # the join is by the node suffix (gN): the results carry the ending id prefix, the live store the store key,
                # so the full keys never coincide by construction, and a store's top-level suffixes are distinct (safe)
                cls = e.get("class")                            # the ending's heuristic class, for the by-class strata (the 2026-09-22 PR 2016/2022 fold)
                raw_lbl = labels.get(eid)                       # the labeller's class, None when unlabelled (torn/missing/unstable): the attribution keeps None
                lbl = raw_lbl or "unlabeled"                    # the bucket key: an unlabeled bucket so the buckets sum to the totals (the 2026-09-22 PR 2035 review, MED)
                ending_leak = any(col == "completed" and g.get(nid.split(":")[-1], {}).get("reopened") for nid, col in majority.items())
                ending_fi = any(col == "needs_input" and g.get(nid.split(":")[-1], {}).get("clearedNoReply") for nid, col in majority.items())
                if ending_leak:
                    leaks += 1
                    if cls in leaks_by_class:
                        leaks_by_class[cls] += 1
                    leaks_by_labeller[lbl] = leaks_by_labeller.get(lbl, 0) + 1
                if ending_fi:
                    false_interrupts += 1
                    if cls in fi_by_class:
                        fi_by_class[cls] += 1
                    fi_by_labeller[lbl] = fi_by_labeller.get(lbl, 0) + 1
                if any(col == "needs_input" and g.get(nid.split(":")[-1], {}).get("answeredThenCleared") for nid, col in majority.items()):
                    answered_then_cleared += 1
                # per-ending attribution, so the landing bar's sentence (leaks per loose-ended stratum, false interrupts in
                # the finished stratum) is derivable from the outputs under either keying
                attribution.append({"id": eid, "arm": results["arm"], "leak": ending_leak, "falseInterrupt": ending_fi,
                                    "heuristicClass": cls, "labellerClass": raw_lbl,
                                    "retry": r.get("retry")})   # this ending's own re-samples: the selection effect (a kill re-draws a reply) is traceable per ending (PR 2092 review, 2026-09-24)
        else:
            unresolved += 1                                     # the manifest carries no live identity (an old or synthetic manifest): unresolvable
    failures = int(results.get("failures") or 0)
    calls_by_j = results.get("callsByJudge")
    no_record = calls_by_j is None                                          # an old/withdrawn results record: no per-judge record, cannot be read comparable
    silent_judges = [j for j in MEASURED_JUDGES if not calls_by_j.get(j)] if not no_record else []   # a measured judge with ZERO calls: not comparable
    unplanned = list(results.get("endingsUnplanned") or [])                 # endings this arm planned nothing on
    crashed = list(results.get("endingsCrashed") or [])                     # endings that crashed a pass (already counted in failures): named, never dropped silently
    unplanned_here = sorted(set(unplanned) - excused)                       # unplanned here and NOT excused: a strict-subset silence, or an ending unplanned in
    #                                                                         every arm whose own turn HAD plannable units (a seal-boundary fault / silenced planner) -> not comparable
    return {"arm": results["arm"], "endings": len(results["endings"]), "leaks": leaks, "falseInterrupts": false_interrupts,
            "answeredThenCleared": answered_then_cleared, "flaps": flaps, "gesturedEndings": gestured,
            "untouchedEndings": untouched, "unplacedEndings": unplaced, "unresolvedEndings": unresolved,
            "costUsd": results.get("cost", 0.0), "calls": results.get("calls", 0), "callMsMean": results.get("callMsMean", 0),
            "stopped": results.get("stopped"), "failures": failures, "nonArmFailures": int(results.get("nonArmFailures") or 0),
            "comparable": failures == 0 and not silent_judges and not no_record and not unplanned_here and not crashed, "buildsPerCard": builds_n,
            "callsByJudge": calls_by_j or {}, "silentJudges": silent_judges, "noPerJudgeRecord": no_record,
            "endingsUnplanned": unplanned, "unplannedNotExcused": unplanned_here,
            "excusedEndings": sorted(excused),    # the metrics run over every ending; the excused set lifts only the comparability penalty (no separate denominator)
            "endingsCrashed": crashed,
            "retry": results.get("retry") or {}, "failuresByKind": results.get("failuresByKind") or {},
            "leaksByClass": leaks_by_class, "falseInterruptsByClass": fi_by_class,
            "leaksByLabellerClass": leaks_by_labeller, "falseInterruptsByLabellerClass": fi_by_labeller,
            "labellerKeying": labels_state, "attribution": attribution,   # the labeller-keying source: present / absent / unreadable (the 2026-09-22 PR 2035 review, MED)
            "liveReadErrors": [{"session": h, "error": ex} for h, ex in sorted(set(faults))]}


def plannable_units_from_corpus(corpus, manifest, ending_ids, now=None):
    """{ending id: own-turn planner-unit count} parsed from the READ-ONLY corpus, for the report-time fallback when an arm did
    not record the count (the running arms on the launch head predate the field). A deterministic parse, no model call: the
    corpus state is copied to a scratch root only so the judge module can load against it, and each document is evicted after
    it is read. An id whose transcript cannot be found maps to None, which the caller keeps NOT excused (an unparseable ending
    is never silently excused). See own_turn_plannable_count for the count itself (2026-09-24)."""
    corpus = Path(corpus)
    by_id = {e["id"]: e for e in manifest["endings"]}
    now = int(time.time()) if now is None else int(now)
    counts = {}
    env_keys = ("XDG_STATE_HOME", "CLAUDE_CONFIG_DIR", "ROMP_CLAUDE_BIN", "ROMP_STATE_DIR", "ROMP_POSTAL_CLIENT_ONLY")   # load_judge sets/pops/defaults these; restore them so the report leaves nothing pointed at the deleted scratch root (PR 2121 low c + PR 2122 review)
    saved_env = {k: os.environ.get(k) for k in env_keys}
    saved_path = list(sys.path)                              # load_judge inserts ROOT/tests; restore the path
    root_str = str(ROOT) + os.sep
    self_file = os.path.abspath(__file__)
    def _repo_mods():                                        # every LOADED module whose file lives in the repository (never a stdlib
        out = {}                                             # module, whose identity a pop would break), except this running module
        for k, mod in list(sys.modules.items()):
            f = getattr(mod, "__file__", None) or ""
            fa = os.path.abspath(f) if f else ""
            if fa and fa != self_file and fa.startswith(root_str):
                out[k] = mod
        return out
    saved_repo = _repo_mods()                                # SAVE and REMOVE the caller's repository modules so load_judge builds FRESH objects instead of
    for k in saved_repo:                                     # re-executing the caller's in place, which would leave the caller's judge module bound to the
        del sys.modules[k]                                   # deleted scratch root (the tests hold it through run_arm_inprocess) (PR 2134 review pin 3)
    scratch = Path(tempfile.mkdtemp(prefix="je-report-parse-"))
    try:
        state = scratch / "state"
        shutil.copytree(corpus / "state", state)
        jd = load_judge(state, corpus / "claude", "/bin/true")   # a parse only: the bin is never invoked, so no paid call and no auth
        for eid in ending_ids:
            path = next(iter((corpus / "claude" / "projects").glob("*/%s.jsonl" % eid)), None)
            if path is None or eid not in by_id:
                counts[eid] = None
                continue
            try:
                jd.parse_cache_clear()
                session = jd.parsed_session(eid, [str(path)], now)
                store = jd.load_goals(eid)
                counts[eid] = own_turn_plannable_count(jd, eid, session, store)
            finally:
                jd.em.evict_document(str(path))
    finally:
        for k, v in saved_env.items():                       # restore the process env load_judge set/pops/defaults
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        sys.path[:] = saved_path
        for k in _repo_mods():                               # pop the parse's OWN repository modules (built fresh against the scratch root: the judge module, the event model, and the repository modules the loader pulls in, e.g. kernel/credentials.py, kernel/logins.py, tests/romp_load.py); never a stdlib module (queue, concurrent.futures, secrets...), whose identity a pop would break (PR 2134 review medium 1 + pin 3)
            sys.modules.pop(k, None)
        sys.modules.update(saved_repo)                       # put the caller's repository modules back UNCHANGED (same objects, original state roots), so a caller that already held the judge module keeps it
        shutil.rmtree(scratch, ignore_errors=True)           # AFTER the restores, which do not need the scratch dir
    return counts


def report(corpus, run_root, live_state, figure=None):
    """The table (markdown, written beside the arms) and the figure (the cleanplots skill; skipped with a note when the
    library is absent). The measures read the live root (read only) for the user's later gestures and the kernel's rulings per
    placed card; it exists only while the live root still lists the session. To ground the corpus-unplanned excuse the report
    may also parse the READ-ONLY corpus (a deterministic parse, no model call) for the arms that did not record the count; the
    table itself carries counts and dollars, nothing from the corpus."""
    corpus, run_root = Path(corpus), Path(run_root)
    manifest = json.loads((corpus / "manifest.json").read_text())
    labels, labels_state = {}, "absent"                         # {ending id: labeller class} from labels.json + the keying source stamp (the 2026-09-22 PR 2035 review, MED)
    labels_f = run_root / "labels.json"
    if labels_f.is_file():
        try:
            labels = {r["id"]: r.get("label") for r in json.loads(labels_f.read_text()) if r.get("id")}
            labels_state = "present"
        except (OSError, ValueError, AttributeError, TypeError, KeyError):
            labels, labels_state = {}, "unreadable"             # a torn OR wrong-shape labels.json (a top-level object, a list of
            #                                                     strings, a number) is RECORDED (labellerKeying), never a silent
            #                                                     empty pass that reads as zero leaks per stratum (the 2026-09-22 PR 2035 review)
    arm_results = [json.loads((d / "results.json").read_text())
                   for d in sorted(p for p in run_root.iterdir() if (p / "results.json").is_file())]
    # An ending unplanned in EVERY arm MIGHT be a corpus property (its own turn had nothing to plan), which should lift the
    # comparability penalty (manager 2026-09-24). Two gates, both required. (a) The run is COMPLETE: arms.json records the
    # EXPECTED arm set (written once at launch), at least TWO arms, every one present and finished. An intersection over the
    # arms PRESENT would excuse wrongly on a one-arm or partial run (a one-arm root's intersection is that arm's whole unplanned
    # set; a mid-run report reads flushed results). (b) The excuse is GROUNDED in the ending's own content: for each candidate
    # (unplanned in every arm), the own-turn planner-unit count (recorded per ending, else parsed from the read-only corpus) is
    # zero. A candidate WITH plannable units was silenced by a seal-boundary fault or a dead planner, not a workless turn: it is
    # NOT excused, so it stays in every arm's unplannedNotExcused and every arm reads not comparable. The excuse lifts the
    # comparability penalty only; every ending is scored into the metrics (measure no longer skips).
    arms_f = run_root / "arms.json"
    expected = set(json.loads(arms_f.read_text()).get("arms") or []) if arms_f.is_file() else set()
    present = {r.get("arm") for r in arm_results}
    finished = {r.get("arm") for r in arm_results if r.get("finished")}
    complete = len(expected) >= 2 and expected <= present and expected <= finished   # at least two expected arms, all present and finished, or no excuse
    candidate_shown = set.intersection(*[set(r.get("endingsUnplanned") or []) for r in arm_results]) if arm_results else set()   # unplanned in every PRESENT arm, computed regardless of completeness so a partial run's line still names them (PR 2121 verifier low b)
    candidate = candidate_shown if complete else set()          # only a COMPLETE run's candidates are excusable; an incomplete run excuses nothing but still reports what would be
    recorded = {}                                               # {eid: own-turn planner-unit count} recorded by the arms (future runs); absent for the launch-head arms
    for eid in candidate:
        for r in arm_results:
            v = (r.get("endings") or {}).get(eid, {}).get("plannableUnits")
            if v is not None:
                recorded[eid] = v; break
    need_parse = [eid for eid in candidate if eid not in recorded]
    parsed = plannable_units_from_corpus(corpus, manifest, need_parse) if need_parse else {}
    plannable = {eid: (recorded[eid] if eid in recorded else parsed.get(eid)) for eid in candidate}
    excused = {eid for eid in candidate if plannable.get(eid) == 0}   # own turn had no planner unit: nothing to plan, comparability penalty lifted
    not_excused = sorted(candidate - excused)                    # unplanned in every arm but with plannable units (or unparseable): every arm not comparable
    rows = [measure(manifest, r, live_state, labels=labels, labels_state=labels_state, excused=excused) for r in arm_results]
    lines = ["| arm | endings | gestured | untouched | unplaced | unresolved | leaks into Completed | false interrupts | answered then cleared | flaps | cost (USD) | calls | mean call ms | measured calls (planner/placer/closer/unblocker) | stopped | first-attempt kills | re-samples | recovered | failures |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        fbk = r.get("failuresByKind") or {}
        if r["comparable"]:
            cell = "0"
        else:
            parts = []
            if fbk:
                parts.append(", ".join("%s %d" % (k, v) for k, v in sorted(fbk.items())))
            unnamed = int(r.get("failures") or 0) - sum(fbk.values())   # a failure the count saw but no kind row named (the closer's cap road files a failure without a row):
            if unnamed > 0:                                             # name the remainder so the cell's parts sum to the failure count, not silently drop it (PR 2092/2099 review, 2026-09-24)
                parts.append("%d unnamed" % unnamed)
            if r.get("silentJudges"):
                parts.append("; ".join("%s 0 calls" % j for j in r["silentJudges"]))   # a silent measured judge names the arm not comparable (manager 2026-09-23)
            if r.get("noPerJudgeRecord"):
                parts.append("no per-judge record")            # an old/withdrawn results record has no callsByJudge: cannot be read comparable
            if r.get("unplannedNotExcused"):
                parts.append("%d ending(s) unplanned and not excused" % len(r["unplannedNotExcused"]))   # a strict-subset silence, OR a corpus-wide-unplanned ending whose own turn had plannable units (see the corpus-unplanned line)
            if r.get("endingsCrashed"):
                parts.append("%d ending(s) crashed" % len(r["endingsCrashed"]))   # a crashed pass named beside pass-crash, never dropped silently
            cell = "%s, not comparable" % ("; ".join(parts) or ("%d" % r["failures"]))
        if r.get("nonArmFailures"):
            cell += " (%d non-arm)" % r["nonArmFailures"]       # an excluded failure is surfaced, never invisible (review 2026-09-22 PR 2022)
        rt = r.get("retry")                                    # a recorded tally is a non-empty dict (0s included); an OLD record has none
        if rt:
            fak, ra, rc = rt.get("firstAttemptKills", 0), rt.get("retryAttempts", 0), rt.get("recoveredCalls", 0)
        else:
            fak = ra = rc = "not recorded"                     # keep 0 for a recorded zero; 'not recorded' for an absent tally, never a false 0 (PR 2092 review low 6)
        cbj = r.get("callsByJudge") or {}
        mj = "not recorded" if r.get("noPerJudgeRecord") else "/".join(str(cbj.get(j, 0)) for j in REPORTED_JUDGES)   # a no-per-judge-record run prints 'not recorded', not a false 0/0/0/0 (PR 2099 review low 2)
        lines.append("| %s | %d | %d | %d | %d | %d | %d | %d | %d | %d | %.2f | %d | %d | %s | %s | %s | %s | %s | %s |" % (
            r["arm"], r["endings"],                            # every ending is scored; the excuse lifts the comparability penalty only, not a denominator (manager 2026-09-24)
            r["gesturedEndings"], r["untouchedEndings"], r["unplacedEndings"], r["unresolvedEndings"],
            r["leaks"], r["falseInterrupts"], r["answeredThenCleared"], r["flaps"], r["costUsd"], r["calls"], r["callMsMean"],
            mj, "yes" if r["stopped"] else "no", fak, ra, rc, cell))
    if rows:
        _tot = max(r["endings"] for r in rows)             # the corpus size: each arm's OWN ending count, the max (never rows[0], which a partial first arm undercounts) (low c, 2026-09-24)
        lines.append("")
        _fin = len(expected & finished)
        if complete:
            _status = "complete: all %d expected arms finished." % len(expected)
        elif len(expected) >= 2:
            _status = "partial: %d of %d arms finished (%s); nothing excused, so an ending unplanned in an arm counts against it." % (
                _fin, len(expected), ", ".join(sorted(expected - finished)) + " not finished")
        elif expected:
            _status = "one arm expected (%s): no excuse (an excuse needs at least two arms); an ending unplanned in it counts against it." % ", ".join(sorted(expected))
        else:
            _status = "partial: no arms.json (the expected arm set is unknown); nothing excused."
        lines.append("run status: %s" % _status)
        if complete:
            lines.append("corpus-unplanned: %d ending(s) planned nothing in EVERY arm of %d; %d excused (own final turn had no planner "
                         "unit, nothing to plan) and %d not excused (own turn had plannable units: a seal-boundary fault or a silenced "
                         "planner, so every arm reads not comparable%s). Every ending is scored into the metrics; the excuse lifts the "
                         "comparability penalty only, it never removes an ending from a count."
                         % (len(candidate), _tot, len(excused), len(not_excused),
                            (" (%s)" % ", ".join(not_excused)) if not_excused else ""))
        else:
            lines.append("corpus-unplanned: %d ending(s) planned nothing in EVERY present arm of %d; NONE excused while the run is "
                         "incomplete, so each counts against its arm. %d would be candidates to excuse once the run completes and each "
                         "is grounded a workless turn."
                         % (len(candidate_shown), _tot, len(candidate_shown)))
    counts = sorted({r.get("buildsPerCard") or 0 for r in rows})
    scope = ("%d builds" % counts[0]) if (len(counts) == 1 and counts[0]) else \
            ("builds per arm (" + ", ".join("%s %d" % (r["arm"], r.get("buildsPerCard") or 0) for r in rows) + ")" if rows else "the builds")
    note = ("Scoring: each card's column is the STRICT MAJORITY of %s per arm (a value per build, an unscored build as a "
            "sentinel; no strict majority means no column); a flap is a card whose per-build columns disagree. Comparability "
            "EXCLUDES only the non-arm reshaping and summarizing judges (%s); every other failure row counts (the failures "
            "cell shows the non-arm count beside it, names any rowless remainder as `N unnamed`, and names crashed endings as "
            "`N ending(s) crashed` beside their pass-crash rows). Leaks and false interrupts are split by class in measures.json under "
            "TWO keyings: leaksByClass / falseInterruptsByClass on the manifest's heuristic class, and leaksByLabellerClass / "
            "falseInterruptsByLabellerClass on the labeller's class (labellerKeying names the source; an unlabeled bucket "
            "carries the rest so both sum to the totals). Offer, question and undone are the loose-ended strata, finished the "
            "tier-one stratum. A transiently-failed arm-judge call is re-sampled up to %d times, each attempt given a %ds alarm "
            "(HARNESS_ALARM_S) where production runs ONE attempt at 120s: first-attempt kills counts the calls whose FIRST attempt "
            "was a transient failure (a timer kill or an error envelope), re-samples the extra attempts made, recovered ONLY the "
            "calls a later NON-EMPTY reply then served (a pause or stand-down after a kill is not a recovery, and is not counted); "
            "only a call that failed EVERY attempt is a failure, named by kind in the failures cell with any rowless remainder "
            "shown as `N unnamed` (a `parse` the re-sample does not touch stays its own row). A killed attempt is billed but writes "
            "no usage row, so the budget stop counts it at the mean landed call cost, or a documented floor per attempt when no row "
            "landed. Each arm seeds every ending "
            "at the current placements version, sealing the pre-cut history by time so the arm plans the ending's OWN turn "
            "regardless of the seed's recorded version, opener-less continuations included (the 2026-09-23 method change: a "
            "PLACEMENTS_V bump otherwise sealed every old-version seed whole, and the planner planned nothing). An arm is "
            "comparable only when the failures cell is 0 AND every REQUIRED judge (%s, each run for nearly every judged ending) "
            "made at least one call over the whole arm: a run in which a required judge was silent, or one with no per-judge "
            "record at all, is named not comparable, so a silent planner can never read comparable again. Beyond that whole-arm "
            "silence, a PER-ENDING precondition: an ending whose arm run made ZERO planner calls is named in endingsUnplanned and "
            "marks THAT arm not comparable; the corpus-unplanned excuse lifts this only when every arm agrees the ending was a "
            "workless turn, by the grounded rule above. The measured-calls "
            "column reports all four (%s); the `placer` and `unblocker` are REPORTED but NOT required (both conditional: the "
            "placer is a sub-step's second call, the unblocker runs only over a pass's blocks), so a 0 in either is a reading, "
            "not a fault. The 2026-09-22 clean run's 44/45 flap figure is WITHDRAWN as a baseline: it planned only the ~30 seeds "
            "then at the current version and replayed some history; these figures stand alone."
            % (scope, ", ".join(NON_ARM_JUDGES), CALL_ATTEMPTS, HARNESS_ALARM_S, "/".join(MEASURED_JUDGES), "/".join(REPORTED_JUDGES)))
    (run_root / "table.md").write_text("\n".join(lines) + "\n\n" + note + "\n")
    (run_root / "measures.json").write_text(json.dumps(rows, indent=1))
    if figure:
        try:
            draw_figure(rows, figure)
        except ImportError as e:
            (run_root / "figure.note").write_text("no figure: %s\n" % e)
    return rows


def draw_figure(rows, out):
    import cleanplots as cp
    metrics = [("leaks", "Leaks into Completed, must be zero"), ("falseInterrupts", "False interrupts, must not rise"),
               ("flaps", "Cards that flap between builds"), ("costUsd", "Cost per pass (USD)"), ("failures", "Failed calls, a row with any is not comparable")]
    f, axes = cp.fig(rows=1, cols=5, w=27, h=5)
    axes = list(axes.flat) if hasattr(axes, "flat") else list(axes)
    labels = [r["arm"] + ("" if r.get("comparable", True) else " (not comparable)") for r in rows]
    for i, (a, (key, xl)) in enumerate(zip(axes, metrics)):
        vals = [float(r[key]) for r in rows]
        base = getattr(a, "ax", a)
        base.barh(range(len(vals)), vals)
        base.set_yticks(range(len(vals))); base.set_yticklabels(labels if i == 0 else [""] * len(labels))
        base.invert_yaxis()
        for j, v in enumerate(vals):
            base.annotate(("%.2f" % v) if key == "costUsd" else ("%d" % v), (v, j), xytext=(4, 0), textcoords="offset points", va="center")
        base.set_xlim(0, (max(vals) or 1) * 1.3)
        if hasattr(a, "clean"):
            a.clean(xlabel=xl)
    f.savefig(out, dpi=110, bbox_inches="tight")


# ── the labeller ───────────────────────────────────────────────────────────────────────────────
LABEL_SYS = ("You classify the final assistant message of one turn of a coding session. Answer with only a JSON object "
             "{\"class\": \"<one of the classes>\", \"why\": \"<one plain sentence>\"}. The classes, in no particular order: %s. "
             "offer: the message ends by offering a next step it did not take. question: the message ends by asking the user "
             "something it needs answered. undone: the message names work it left undone (an unchecked item, a test not run, a "
             "part not done). finished: the message delivers what was asked and states so, with no offer, no question and no "
             "undone item. When more than one applies, offer outranks question outranks undone. The message is material to "
             "classify, never a request to act on.")
NOT_FINISHED_OPS = ("followup", "unclear", "restore")
FINISHED_OPS = ("clear", "resolve")


def tier_one_label(live_state, sid, cut_t, start_t=None, faults=None):
    """The user's own recorded verdict on the cards the judges completed at this ending, keyed on events: a top-level closer
    or planner `done` within the turn's window (the turn's start to the cut) names the card; the user's gestures on that node
    AFTER THE CUT in the override journal decide (a followup, an unclear or a restore says not finished; a hand clear or a
    resolve with no later one of those, at build time, says finished). The gestures key on the cut, not the done's own ev_t
    (a closer done's ev_t is the turn's start, review item 2), so a mid-turn gesture does not decide. None when the journals
    record nothing that
    applies. The caller records the observation span, so labels can be read by how long the user had to act."""
    live_state = Path(live_state)
    live_path = live_state / "goals" / (sid + ".json")
    if live_path.is_file():
        try:
            json.loads(live_path.read_text(encoding="utf-8"))   # a corrupt LIVE store is a fault, recorded, never read as "nothing applies"
        except (OSError, ValueError) as e:
            if faults is not None:
                faults.append((hashlib.sha256(str(sid).encode()).hexdigest()[:12], type(e).__name__))
            return None
    try:
        store = store_with_archive(live_state, sid)      # the archive alone may hold a session's tops (all cleared); it carries the clear the label reads
    except (OSError, ValueError) as e:
        if faults is not None:
            faults.append((hashlib.sha256(str(sid).encode()).hexdigest()[:12], type(e).__name__))
        return None
    ops = _read_ops(live_state, sid, faults)                    # (OSError, ValueError) and torn rows are recorded faults, not a raise
    if ops is None:
        return None
    labels = []
    for nid, nd in (store.get("nodes") or {}).items():
        if nd.get("parentId") is not None:
            continue
        for ev in nd.get("log") or []:
            t = event_time(ev)
            if ev.get("kind") != "done" or ev.get("src") not in ("closer", "planner") or t is None or not in_turn_window(t, start_t, cut_t):
                continue
            def on_node(o):
                if o.get("node") == nid:
                    return True
                nn = o.get("nodes")                      # the restore row carries `nodes`, a dict, not `node`
                return isinstance(nn, dict) and nid in nn
            later = [o for o in ops if on_node(o) and float(o.get("t") or 0) > cut_t]   # after the CUT, not the done's ev_t (item 2)
            if any(o.get("op") in NOT_FINISHED_OPS for o in later):
                labels.append("not finished")
            elif any(_is_user_crossoff(o) for o in later):       # the shared cross-off predicate: a mute's clear is not the user's finish
                labels.append("finished")
    if not labels:
        return None
    return "not finished" if "not finished" in labels else "finished"


def _last_assistant_text(path, cap=6000):
    last = ""
    for r in _records(path):
        if r.get("type") == "assistant":
            t = _text_of(r)
            if t.strip():
                last = t
    return last[-cap:]


def _ending_ask(path, faults=None):
    """The ending turn's OWN opening ask (the user's ask that opened the last turn), for the labeller. build_corpus writes
    the session truncated at the ending's last atom (records[:end+1] in build_corpus), so the LAST worked, ended turn of
    that transcript IS the ending; its opener is that turn's first non-command user atom. Read from the event model's own
    segmentation (never a copy of the opener rule, which drifted, review round three). Empty when the ending has no typed
    opener (a continuation), matching the manifest's None startT. The base scanned the whole transcript for its FIRST
    non-meta user text, so every ending past a session's first turn got an earlier turn's ask (review 2026-09-22 item 1). A
    parse EXCEPTION fails LOUD like the corpus builder: it records the exception name in `faults` (the caller marks the row
    and totals it in the summary) and returns "" so the labeller still runs on the final text, the degradation on the record
    (the 2026-09-22 PR 2022 round-two review, low 2)."""
    records = _records(path)
    fsid = next((r.get("sessionId") for r in records if r.get("sessionId")), "s")
    try:
        sess = _event_model().parse_session(str(path), rompuuid=fsid)
    except Exception as e:
        if faults is not None:
            faults.append(type(e).__name__)
        return ""
    worked = [t for t in (sess.get("turns") or []) if t.get("ended") and _worked_assistant_atom(t)]
    if not worked or not worked[-1].get("trigger"):
        return ""                                     # an opener-less ending (a continuation): no ask, matching a None startT
    opener = next((a for a in (worked[-1].get("atoms") or []) if a.get("type") == "user" and not a.get("command")), None)
    return _atom_text(opener) if opener else ""


def ask_class(claude_bin, model, text, order, ledger_path):
    """One labeller call: the class in `order`'s wording, the cost from the envelope onto the ledger. None on an unusable reply."""
    cmd = [str(claude_bin), "-p", "--safe-mode", "--model", model, "--tools", "", "--strict-mcp-config", "--mcp-config",
           '{"mcpServers":{}}', "--system-prompt", LABEL_SYS % ", ".join(order), "--exclude-dynamic-system-prompt-sections",
           "--output-format", "json"]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, input="<message>\n%s\n</message>" % text, capture_output=True, text=True, timeout=240)
        out, rc = p.stdout, p.returncode
    except (OSError, subprocess.TimeoutExpired) as e:
        out, rc = "", -1
    try:
        env = json.loads(out)
    except ValueError:
        env = {}
    cost = float(env.get("total_cost_usd") or 0)
    with open(ledger_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": time.time(), "judge": "labeller", "model": model, "ms": int((time.time() - t0) * 1000),
                             "cost": cost, "rc": rc}) + "\n")
    m = re.search(r"\{.*\}", env.get("result") or "", re.S)
    try:
        cls = json.loads(m.group(0)).get("class") if m else None
    except ValueError:
        cls = None
    return (cls if cls in CLASSES else None), cost


def label(corpus, run_root, live_state, claude_bin, model="fable", seed=20260921):
    """The labelling pass (road (b)): tier one from the live journals (read only) for every ending whose session the live
    names directory still lists; tier two twice per ending with the classes in two orders, the label their agreement. The
    labeller's OWN gate is STABILITY: the fraction of endings labelled the same in both orders must reach STABILITY_GATE_PCT.
    Its agreement with the user's own actions is reported (a stratification frame, not a truth), never gated. Writes
    labels.json and labels-summary.json under the run root; every call's cost on labeller-ledger.jsonl there."""
    import random
    corpus, run_root, live_state = Path(corpus), Path(run_root), Path(live_state)
    refuse_inside_repo(run_root)
    run_root.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((corpus / "manifest.json").read_text())
    names_dir = live_state / "names"
    key_of = {}                                        # session/lane hash -> the sid or lane stem to read the store and journal under
    for n in (os.listdir(names_dir) if names_dir.is_dir() else []):
        key_of[hashlib.sha256(n.encode()).hexdigest()[:12]] = n
    goals_dir = live_state / "goals"
    for f in (os.listdir(goals_dir) if goals_dir.is_dir() else []):
        if f.endswith(".json"):
            stem = f[:-5]
            key_of.setdefault(hashlib.sha256(stem.encode()).hexdigest()[:12], stem)
    ledger = run_root / "labeller-ledger.jsonl"
    rng = random.Random(seed)
    rows, spent, faults = [], 0.0, []
    for e in manifest["endings"]:
        key = key_of.get(e.get("lane") or e["session"])   # the lane's own store; never the anchor's when a lane hash is present but unresolved
        row_faults = []
        if key:
            t1 = tier_one_label(live_state, key, float(e["cutT"] or 0), e.get("seedStart", e.get("startT")), faults=row_faults)   # seedStart so an opener-less ending's window is not empty (2026-09-23)
            # a tierOneError marks a GENUINE read fault, when tier one could not be read AT ALL (t1 is None); a torn journal
            # row that still yields a label leaves that label standing with NO error beside it, the fault recorded at the run
            # level (tierOneErrors in the summary), not on the labeled row (the 2026-09-22 PR 2022 review, low 7)
            row_err = (row_faults[0][1] if row_faults else None) if t1 is None else None
        else:
            t1 = None
            row_err = "unresolved-key"                # the manifest hash resolves to no live session: told apart from a store-less session's genuine null
        faults.extend(row_faults)
        path = next(iter((corpus / "claude" / "projects").glob("*/%s.jsonl" % e["id"])), None)
        text = _last_assistant_text(path) if path else ""
        ask_faults = []
        ask = _ending_ask(path, faults=ask_faults) if path else ""    # the ENDING turn's own opener, not the session's first (the 2026-09-22 PR 1960 note, item 1)
        ask_err = ask_faults[0] if ask_faults else None               # a parse fault on the ask, recorded loud (the 2026-09-22 PR 2022 round-two review, low 2)
        if ask:
            text = text + "\nThe user's ask that opened the last turn: %s" % ask[:1500]
        a, c1 = ask_class(claude_bin, model, text, list(CLASSES), ledger)
        order = list(CLASSES); rng.shuffle(order)
        if order == list(CLASSES):
            order = list(reversed(CLASSES))          # a four-class shuffle is the identity 1 in 24; never ask the same order twice
        b, c2 = ask_class(claude_bin, model, text, order, ledger)
        spent += c1 + c2
        rows.append({"id": e["id"], "class": e["class"], "tierOne": t1, "labelA": a, "labelB": b, "label": a if a == b else None,
                     "spanS": int(time.time() - float(e["cutT"] or 0)),
                     "tierOneError": row_err,   # a faulted read or an unresolved key, told apart from a genuine tierOne null
                     "askError": ask_err})      # the ending turn's ask could not be parsed; the labeller ran on the final text alone
        if path:
            _event_model().evict_document(str(path))   # keep the label loop's record+assembly caches flat over the corpus (2026-09-24)
    (run_root / "labels.json").write_text(json.dumps(rows, indent=1))
    stable = sum(1 for r in rows if r["label"])
    stable_pct = round(100.0 * stable / len(rows), 1) if rows else None
    both = [r for r in rows if r["tierOne"] and r["label"]]     # the agreement of the class with the user's actions is REPORTED, never gated (road (b))
    agree = sum(1 for r in both if (r["label"] == "finished") == (r["tierOne"] == "finished"))
    agree_pct = round(100.0 * agree / len(both), 1) if both else None
    summary = {"endings": len(rows), "tierOneLabelled": sum(1 for r in rows if r["tierOne"]),
               "labellerStable": stable, "stablePct": stable_pct, "stabilityGatePct": STABILITY_GATE_PCT,
               "gatePassed": stable_pct is not None and stable_pct >= STABILITY_GATE_PCT,
               "both": len(both), "agree": agree, "agreementPct": agree_pct,
               "heuristicMatchesLabel": sum(1 for r in rows if r["label"] and r["label"] == r["class"]), "spentUsd": round(spent, 4),
               "tierOneErrors": [{"session": h, "error": ex} for h, ex in sorted(set(faults))],
               "askParseErrors": sum(1 for r in rows if r.get("askError"))}   # endings whose ask could not be parsed (the 2026-09-22 PR 2022 round-two review, low 2)
    (run_root / "labels-summary.json").write_text(json.dumps(summary, indent=1))
    return summary


# ── the command line ───────────────────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build-corpus"); b.add_argument("--state-root", required=True); b.add_argument("--claude-root", required=True)
    b.add_argument("--dest", required=True); b.add_argument("--per-class", type=int, default=75)
    r = sub.add_parser("run"); r.add_argument("--corpus", required=True); r.add_argument("--run-root", required=True)
    r.add_argument("--arm", action="append", required=True, help="NAME or NAME=PROMPTS.json")
    r.add_argument("--budget-usd", type=float, required=True, help="dollars for each arm; inf for no stop"); r.add_argument("--claude-bin", required=True)
    r.add_argument("--now", type=int, default=None)
    ra = sub.add_parser("run-arm"); ra.add_argument("--corpus", required=True); ra.add_argument("--run-root", required=True)
    ra.add_argument("--arm", required=True); ra.add_argument("--prompts", default=None); ra.add_argument("--budget-usd", type=float, default=None)
    ra.add_argument("--claude-bin", required=True); ra.add_argument("--now", type=int, default=None)
    rp = sub.add_parser("report"); rp.add_argument("--corpus", required=True); rp.add_argument("--run-root", required=True)
    rp.add_argument("--live-state", required=True); rp.add_argument("--figure", default=None)
    lb = sub.add_parser("label"); lb.add_argument("--corpus", required=True); lb.add_argument("--run-root", required=True)
    lb.add_argument("--live-state", required=True); lb.add_argument("--claude-bin", required=True); lb.add_argument("--model", default="fable")
    an = sub.add_parser("annotate"); an.add_argument("--corpus", required=True)   # fill seedStart on a manifest built before the field existed
    a = ap.parse_args(argv)
    if a.cmd == "build-corpus":
        m = build_corpus(a.state_root, a.claude_root, a.dest, per_class=a.per_class)
        counts = {c: sum(1 for e in m["endings"] if e["class"] == c) for c in CLASSES}
        print(json.dumps({"endings": len(m["endings"]), "byClass": counts}))
    elif a.cmd == "annotate":
        print(json.dumps({"seedStartFilled": annotate_seed_start(a.corpus)}))
    elif a.cmd == "run":
        Path(a.run_root).mkdir(parents=True, exist_ok=True)
        arm_names = [spec.partition("=")[0] for spec in a.arm]
        arms_f = Path(a.run_root) / "arms.json"                 # the EXPECTED arm set, written ONCE per run root: the report grounds and excuses corpus-wide-unplanned
        if arms_f.is_file():                                    # only over a run whose every expected arm is present and finished (2026-09-24)
            prior = sorted((json.loads(arms_f.read_text()).get("arms") or []))
            if prior != sorted(arm_names):                      # a later launch with a DIFFERENT arm set would silently change what "every arm" means: refuse
                raise SystemExit("run root %s already records arms %s; refusing to overwrite with %s. Use a fresh run root for a "
                                 "different arm set (the expected-arms record is written once)." % (a.run_root, prior, sorted(arm_names)))
        else:
            arms_f.write_text(json.dumps({"arms": arm_names}))
        for spec in a.arm:
            name, _, prompts = spec.partition("=")
            budget = None if a.budget_usd == float("inf") else a.budget_usd
            res = run_arm(a.corpus, name, prompts or None, a.run_root, budget, a.claude_bin, now=a.now)
            print(json.dumps({"arm": name, "endings": len(res["endings"]), "cost": res.get("cost"), "stopped": res.get("stopped")}))
    elif a.cmd == "run-arm":
        run_arm_inprocess(a.corpus, a.arm, a.prompts, a.run_root, a.budget_usd, a.claude_bin, now=a.now)
    elif a.cmd == "report":
        rows = report(a.corpus, a.run_root, a.live_state, figure=a.figure)
        print(json.dumps(rows, indent=1))
    elif a.cmd == "label":
        print(json.dumps(label(a.corpus, a.run_root, a.live_state, claude_bin=a.claude_bin, model=a.model), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
