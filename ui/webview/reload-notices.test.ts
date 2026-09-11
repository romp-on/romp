// The notices a page is showing when the reload core takes it (reload-notices.ts). The core's restart reload follows
// the last pending ship's retirement on the next task (render.ts endReloadHoldIfIdle, __rompReload.ended()), and the
// nack, the dismissal or the other-tab ack raised in that same task is appended one task before the page goes: the
// toast was never read, and the fresh page's loss toast reads shipsInFlight, which the retirement already emptied. So
// render.ts keeps the texts of the toasts on screen in this tab's sessionStorage on the core's synchronous hook and the
// fresh page shows them once. The readings are pure and execute here. render.ts has import-time DOM side effects, so
// its wiring is pinned to source the way reload-restore.test.ts pins the scroll record's, and the toast family with the
// refusals that report a state (the staging refusals, the branch jump's, the send on a disconnected host, the send into
// a tab whose create failed, the queued edit's send on a session that cannot be reached) is lifted out of it and
// executed over a fake DOM the way chat-exact-tail-exec.test.ts lifts chatTail. The served scenario (a nack on the last
// ship across a kernel restart; the fresh page says it again, once) is tests/test_ship_reship.py
// NackNoticeSurvivesReload. Synthetic only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { RELOAD_NOTICES_KEY, liveNotices, keepReloadNotices, takeReloadNotices } from "./reload-notices";
import { mintProvisionalId, isProvisionalId } from "./provisional";

const requireCjs = createRequire(__filename);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

const SEL = ".warn-toast:not([data-ephemeral]) .warn-toast-msg";
const box = (texts: (string | null)[]) => ({
  querySelectorAll: (sel: string) => { assert.equal(sel, SEL); return texts.map((t) => ({ textContent: t })); },
});

/** A sessionStorage stand-in: the three calls the module makes over a Map, with a log of them. */
function fakeStore(init: Record<string, string> = {}) {
  const m = new Map(Object.entries(init));
  const calls: string[] = [];
  return {
    m, calls,
    getItem: (k: string) => { calls.push("get " + k); return m.has(k) ? m.get(k)! : null; },
    setItem: (k: string, v: string) => { calls.push("set " + k); m.set(k, v); },
    removeItem: (k: string) => { calls.push("remove " + k); m.delete(k); },
  };
}

test("the toasts on screen read as their texts, in order, blanks dropped; none without the container", () => {
  assert.deepEqual(liveNotices(null), [], "the container is created by the first toast; none yet");
  assert.deepEqual(liveNotices(undefined), []);
  assert.deepEqual(liveNotices(box([])), []);
  assert.deepEqual(liveNotices(box(["shot.png couldn't be saved on the kernel, so it was not attached. Your message was NOT sent.",
                                    "  ", null, "The pending upload was dismissed. Your held message was NOT sent."])),
    ["shot.png couldn't be saved on the kernel, so it was not attached. Your message was NOT sent.",
     "The pending upload was dismissed. Your held message was NOT sent."]);
  assert.deepEqual(liveNotices(box(["  padded  "])), ["padded"], "the text as the toast shows it");
});

test("the reading asks for the toasts without the ephemeral mark: a refusal about a state the fresh page shows for itself stays behind", () => {
  // a refusal that reports a state (the staged sends' "Can't send yet": the host unreachable, the tab still being
  // created; the plain send on a disconnected host; the send into a tab whose create failed; the queued edit's send on
  // a session that cannot be reached; the staging refusals; the branch jump to a session not on this dashboard) is
  // about something the fresh page shows for itself or no longer has; render.ts marks those toasts where they are
  // raised (executed below) and the selector skips the mark. The mark's effect on a real DOM is executed by
  // tests/test_ship_reship.py NackNoticeSurvivesReload.
  const asked: string[] = [];
  assert.deepEqual(liveNotices({ querySelectorAll: (sel: string) => { asked.push(sel); return [{ textContent: "kept" }]; } }), ["kept"]);
  assert.deepEqual(asked, [SEL]);
});

test("the record is kept only when there is something to say; a page with no toast clears a record left behind", () => {
  const s = fakeStore();
  keepReloadNotices(s, ["one", "two"]);
  assert.deepEqual([...s.m.entries()], [[RELOAD_NOTICES_KEY, JSON.stringify(["one", "two"])]]);
  assert.equal(RELOAD_NOTICES_KEY, "romp:reloadNotices", "this tab's sessionStorage, beside the scroll record's key");
  // a reload the browser refused leaves the record in place; the next core reload with nothing on screen clears it
  keepReloadNotices(s, []);
  assert.equal(s.m.has(RELOAD_NOTICES_KEY), false, "cleared, not written empty");
  assert.deepEqual(s.calls, ["set " + RELOAD_NOTICES_KEY, "remove " + RELOAD_NOTICES_KEY]);
  keepReloadNotices(null, ["x"]);
  keepReloadNotices(undefined, []);
});

test("the record comes out once: strings only, and the key is gone whatever it held", () => {
  const s = fakeStore({ [RELOAD_NOTICES_KEY]: JSON.stringify(["one", "", 3, null, "two"]), other: "kept" });
  assert.deepEqual(takeReloadNotices(s), ["one", "two"]);
  assert.deepEqual([...s.m.keys()], ["other"], "the key is removed; nothing else is touched");
  assert.deepEqual(takeReloadNotices(s), [], "a second take finds nothing");
  assert.deepEqual(s.calls, ["get " + RELOAD_NOTICES_KEY, "remove " + RELOAD_NOTICES_KEY, "get " + RELOAD_NOTICES_KEY],
    "no record, no removal");
  for (const junk of ["not json", JSON.stringify("a string"), JSON.stringify({ a: 1 }), JSON.stringify(null), ""]) {
    const j = fakeStore({ [RELOAD_NOTICES_KEY]: junk });
    assert.deepEqual(takeReloadNotices(j), [], JSON.stringify(junk) + " reads as none");
    assert.equal(j.m.has(RELOAD_NOTICES_KEY), false, JSON.stringify(junk) + " is still cleared");
  }
  assert.deepEqual(takeReloadNotices(null), []);
  assert.deepEqual(takeReloadNotices(undefined), []);
});

test("kept on the page that reloads, taken on the page that follows: the same texts, then nothing", () => {
  const s = fakeStore();
  const texts = liveNotices(box(["shot.png couldn't be saved on the kernel, so it was not attached. Your message was NOT sent."]));
  keepReloadNotices(s, texts);
  assert.deepEqual(takeReloadNotices(s), texts);
  assert.deepEqual(takeReloadNotices(s), []);
});

test("a store that refuses is left alone: nothing thrown from either side", () => {
  const broken = {
    getItem: () => { throw new Error("SecurityError"); },
    setItem: () => { throw new Error("QuotaExceededError"); },
    removeItem: () => { throw new Error("SecurityError"); },
  };
  assert.doesNotThrow(() => keepReloadNotices(broken, ["x"]));
  assert.doesNotThrow(() => keepReloadNotices(broken, []));
  assert.deepEqual(takeReloadNotices(broken), []);
});

// ── The refusals that report a state, executed ───────────────────────────────────────────────────────────────────────

/** A slice of render.ts between two anchors, transpiled (TS to JS) with esbuild at run time and required dynamically so
 *  the test bundle does not bundle esbuild itself (the chat-exact-tail-exec.test.ts pattern). `wrap` closes a slice
 *  that is not a statement on its own (a property of an object literal) before it is transpiled. */
function liftBetween(startAnchor: string, endAnchor: string, wrap: (ts: string) => string = (s) => s): string {
  const a = RENDER.indexOf(startAnchor), b = RENDER.indexOf(endAnchor, a);
  assert.ok(a > 0 && b > a, `anchors not found: ${startAnchor.slice(0, 40)} or ${endAnchor.slice(0, 40)} moved; re-anchor`);
  return requireCjs("esbuild").transformSync(wrap(RENDER.slice(a, b)), { loader: "ts" }).code;
}

/** Enough of Element for the toast family and the reading: a class, a dataset, children, an id, and the container's
 *  querySelectorAll for the reading's selector shape (.a:not([data-x]) .b), evaluated as the DOM would: a one-word
 *  data attribute names its dataset key as is, whether it was set through dataset or setAttribute. */
class FakeEl {
  children: FakeEl[] = []; parent: FakeEl | null = null; dataset: Record<string, string> = {}; attrs: Record<string, string> = {};
  textContent = ""; title = ""; id = "";
  constructor(public tag: string, public className = "") {}
  has(c: string): boolean { return this.className.split(/\s+/).includes(c); }
  appendChild(c: FakeEl): FakeEl { c.parent?.removeChild(c); c.parent = this; this.children.push(c); return c; }
  append(...cs: FakeEl[]): void { for (const c of cs) this.appendChild(c); }
  removeChild(c: FakeEl): void { this.children = this.children.filter((x) => x !== c); c.parent = null; }
  remove(): void { this.parent?.removeChild(this); }
  setAttribute(k: string, v: string): void { this.attrs[k] = v; if (k.startsWith("data-")) this.dataset[k.slice(5)] = v; }
  addEventListener(): void {}
  querySelectorAll(sel: string): FakeEl[] {
    const m = /^\.([\w-]+):not\(\[data-([\w-]+)\]\) \.([\w-]+)$/.exec(sel);
    if (!m) throw new Error("unsupported selector " + sel);
    return this.children.filter((c) => c.has(m[1]) && !(m[2] in c.dataset)).flatMap((c) => c.children.filter((s) => s.has(m[3])));
  }
}
/** document as warnToast uses it: a body to append the container to, getElementById to find it again, a key listener. */
function fakeDocument() {
  const body = new FakeEl("body");
  return { body, getElementById: (id: string) => body.children.find((c) => c.id === id) ?? null, addEventListener: () => {} };
}

/** The page state the lifted closures read: the composer (a typed draft, its citations, a picker waiting, an edit in
 *  progress to a past message or to a queued one, attachments), the session roster, the reach of the active session's
 *  host (hostDown), whether the active session lives on another host (remote: its id wears the host prefix, lab:web,
 *  the shape the disconnected-host refusal names the host from) and the active tab when it is provisional (tab:
 *  "pending", a create still in flight, so its id is the pending provisional id; "failed", a create that failed, so no
 *  create is pending), with what each gesture did recorded. The toasts' timers are recorded and not run, so a toast
 *  stays on screen for the reading. */
function pageWorld(state: { ask?: "custom" | "text" | null; edit?: boolean; queuedEdit?: boolean; files?: string[]; sessions?: string[];
                            hostDown?: boolean; remote?: boolean; tab?: "pending" | "failed" }) {
  const activeId = state.tab ? mintProvisionalId("web") : state.remote ? "lab:web" : "web";
  const roster = (state.sessions || []).map((id): [string, { id: string; name: string }] => [id, { id, name: id }]);
  if (state.tab || state.remote) roster.push([activeId, { id: activeId, name: "web" }]);
  return {
    FakeEl, document: fakeDocument(), timers: [] as number[],
    activeId, ta: { value: "what did the tests say", style: {} as Record<string, string> },
    composerCitations: new Map<string, { quote?: string }[]>(), ask: state.ask ?? null,
    composerEdits: new Map<string, { uuid: string; orig: string }>(state.edit ? [[activeId, { uuid: "e1", orig: "the old text" }]] : []),
    // T306: a queued message's edit is an in-place editor on its bubble, keyed by the entry; the fixture's holds one open
    queuedEditors: new Map<string, unknown>(state.queuedEdit ? [["k", { eid: 1, sid: activeId, key: "k", ref: { md: "the queued text" },
                                                                        text: "the queued text, edited", sel: null, focused: true, open: true, note: "" }]] : []),
    composerFiles: new Map<string, string[]>(state.files ? [[activeId, state.files]] : []),
    staged: [] as [string, unknown][], persists: 0,
    sessions: new Map<string, { id: string; name: string }>(roster),
    activated: [] as [string, string | undefined][],
    hostDown: !!state.hostDown, posted: [] as { type: string }[], editsApplied: 0,
    isProvisionalId, provisionalId: state.tab === "pending" ? activeId : null, provisionalQueue: [] as string[],
    lastSent: new Map<string, string>(),
  };
}
type World = ReturnType<typeof pageWorld>;
type Lifted = { stageComposer: () => void; branchjump: (elx: { dataset: Record<string, string> }) => void; warnToast: (msg: string) => FakeEl;
                deliver: (sid: string, text: string, attached: string[]) => void; saveQueuedEdit: () => void };

/** warnToast and ephemeralWarnToast; stageComposer (the composer's staging, whose refusals say a picker is waiting on
 *  the composer, an edit is in progress to a past or a queued message, attachments are on the composer); the branch
 *  jump's delegated handler (whose refusal says the session is not on this dashboard); the refusing head of the send's
 *  deliver (the disconnected-host branch, whose refusal says the host is disconnected and asks for a re-dial, and the
 *  provisional branch, whose refusal says the tab's session never started), through the first step of a send that
 *  passed both guards (lastSent remembers the text), so a refusal that fell through would show as a remembered send;
 *  and the queued message's in-place Save (whose refusal says the session cannot be reached, so the edit was not saved),
 *  lifted from render.ts and run over the world. */
function liftToastSites(): (w: World) => Lifted {
  const toasts = liftBetween("function warnToast(msg: string): HTMLElement {", "// Tail-windowing (see the View comment)");
  const stage = liftBetween("const stageComposer = () => {", "const sendComposer = (");
  const jump = liftBetween("branchjump: (elx) => {", "// a below-response fork spot", (ts) => "const handlers = {\n" + ts + "};");
  const send = liftBetween("if (hostIsDown(sid)) {", "// The STAGED run and this message go together", (ts) => "const deliver = (sid, text, attached) => {\n" + ts + "};");
  const qsave = liftBetween("function saveQueuedEditor(ed: QueuedEditor): void {", "// the field the bubble wears while its editor is open");
  const prelude = `
    const W = WORLD;
    const document = W.document;
    const el = (tag, cls) => new W.FakeEl(tag, cls);
    const setTimeout = (fn, ms) => { W.timers.push(ms); return 0; };   // the fade and the removal are not run
    let activeId = W.activeId;
    const ta = W.ta;
    const composerCitations = W.composerCitations;
    const composerAnswersAsk = () => W.ask;
    const composerEdits = W.composerEdits;
    const queuedEditors = W.queuedEditors;
    const composerFiles = W.composerFiles;
    const stagedMsgs = { push: (id, s) => { W.staged.push([id, s]); } };
    const renderComposerChips = () => {};
    const drafts = new Map(), draftStartedAt = new Map();
    let composerManualH = null;
    const clearBox = () => { ta.value = ""; composerManualH = null; ta.style.height = ""; };   // the composer's one clear path; its menu refreshes are not lifted (composer-mention-pane.test.ts)
    const persistDrafts = () => { W.persists++; };
    const renderStagedStrip = () => {};
    const sessions = W.sessions;
    const setActive = (sid, cut) => { W.activated.push([sid, cut]); };
    const isProvisionalId = W.isProvisionalId, provisionalId = W.provisionalId, provisionalQueue = W.provisionalQueue;
    const lastSent = W.lastSent;
    const registerOptimistic = () => {}, previewKind = () => null, renderComposerFiles = () => {};
    const sendOnShip = new Set(), histWalk = new Map();
    const hostIsDown = () => W.hostDown;
    const vscodeApi = { postMessage: (m) => { W.posted.push(m); } };
    const SLASH_CMD_RE = /^[/][A-Za-z]/;   // a template literal: the real regex's escaped slash would not survive it
    const pendingEditRestores = new Map();
    const applyQueuedEditLocally = () => { W.editsApplied++; };
  `;
  return new Function("WORLD", prelude + toasts + stage + jump + send + qsave
    + "\nconst saveQueuedEdit = () => saveQueuedEditor(queuedEditors.values().next().value);"
    + "\nreturn { stageComposer, branchjump: handlers.branchjump, warnToast, deliver, saveQueuedEdit };") as (w: World) => Lifted;
}

function page(state: Parameters<typeof pageWorld>[0]) {
  const W = pageWorld(state);
  const api = liftToastSites()(W);
  const box = () => W.document.getElementById("warn-toasts");
  const shown = () => (box()?.children || []).map((t) => t.children[0].textContent);
  return { W, ...api, box, shown };
}
type Page = ReturnType<typeof page>;

const NACK = "shot.png couldn't be saved on the kernel, so it was not attached. Your message was NOT sent.";

test("staging with nothing owning the composer stages: the lifted composer is the real one, and it raises no toast", () => {
  const p = page({});
  p.stageComposer();
  assert.deepEqual(p.W.staged, [["web", { text: "what did the tests say", cites: [] }]]);
  assert.equal(p.W.ta.value, "", "the composer clears");
  assert.equal(p.W.persists, 1);
  assert.equal(p.box(), null, "no toast, so no container");
});

// The refusals that report a STATE rather than an event, raised through their real code paths. Each puts its toast on
// screen for the person at the page and refuses the gesture; the reading skips it, because the fresh page shows that
// state for itself (the picker, the attachments and the roster come back from the kernel and the persisted drafts; the
// host's reach comes back from the kernel's tunnel health and is shown as the tab mark and the transcript foot, and the
// re-dial the disconnected-host refusal speaks of is posted by the gesture, never by a replay) or no longer has it (an
// edit in progress, to a past message or to a queued one, lives in memory alone, so a replay would report an edit the
// fresh page has not got, and its "send again" would post the words as a new message; a provisional tab does not
// survive a reload, so a replay would name a session the fresh page does not show).
const STATE_REFUSALS: { name: string; state: Parameters<typeof pageWorld>[0]; raise: (p: Page) => void; text: string; refused: (p: Page) => void }[] = [
  { name: "staging while a picker waits on the composer", state: { ask: "text" }, raise: (p) => p.stageComposer(),
    text: "A picker is waiting on this box", refused: (p) => assert.deepEqual(p.W.staged, [], "nothing staged") },
  { name: "staging while an edit is in progress", state: { edit: true }, raise: (p) => p.stageComposer(),
    text: "An edit replaces a past message", refused: (p) => assert.deepEqual(p.W.staged, [], "nothing staged") },
  { name: "staging with attachments on the composer", state: { files: ["notes.md"] }, raise: (p) => p.stageComposer(),
    text: "Attachments can't be staged", refused: (p) => assert.deepEqual(p.W.staged, [], "nothing staged") },
  { name: "a branch jump to a session not on this dashboard", state: { sessions: ["web"] }, raise: (p) => p.branchjump({ dataset: { sid: "api" } }),
    text: "That session isn't on this dashboard right now.", refused: (p) => assert.deepEqual(p.W.activated, [], "no switch") },
  { name: "a send while the session's host is unreachable", state: { remote: true, hostDown: true }, raise: (p) => p.deliver(p.W.activeId, p.W.ta.value, []),
    text: "lab is disconnected, so this wasn't sent.", refused: (p) => {
      assert.deepEqual(p.W.posted.map((m) => m.type), ["redial"], "a re-dial of the host is asked for and nothing is sent");
      assert.deepEqual(p.W.provisionalQueue, [], "nothing queued");
      assert.equal(p.W.lastSent.size, 0, "nothing was remembered as sent");
    } },
  { name: "a send into a tab whose create failed", state: { tab: "failed" }, raise: (p) => p.deliver(p.W.activeId, p.W.ta.value, []),
    text: "“web” never started, so there's nowhere to send this.", refused: (p) => {
      assert.deepEqual(p.W.provisionalQueue, [], "nothing queued");
      assert.equal(p.W.lastSent.size, 0, "nothing was remembered as sent");
    } },
  { name: "a queued message's edit saved while the session's host is unreachable", state: { queuedEdit: true, hostDown: true }, raise: (p) => p.saveQueuedEdit(),
    text: "Can't reach the session right now, so the edit wasn't saved.", refused: (p) => {
      assert.deepEqual(p.W.posted.map((m) => m.type), ["redial"], "a re-dial is asked for and no edit is posted");
      assert.equal(p.W.queuedEditors.size, 1, "the field stays open with the words (T306: the edit lives on the bubble, not in the composer)");
      assert.equal(p.W.editsApplied, 0, "the queued bubble keeps its words");
    } },
  { name: "a queued message's edit saved on a tab still being created", state: { queuedEdit: true, tab: "pending" }, raise: (p) => p.saveQueuedEdit(),
    text: "Can't reach the session right now, so the edit wasn't saved.", refused: (p) => {
      assert.deepEqual(p.W.posted, [], "nothing is posted: there is no kernel session to re-dial or to edit");
      assert.equal(p.W.queuedEditors.size, 1, "the field stays open with the words");
      assert.equal(p.W.editsApplied, 0, "the queued bubble keeps its words");
    } },
];
for (const r of STATE_REFUSALS) {
  test(r.name + ": the refusal is on screen and refuses, and the reading skips it", () => {
    const p = page(r.state);
    r.raise(p);
    const shown = p.shown();
    assert.equal(shown.length, 1, "the refusal put its toast on screen");
    assert.ok(shown[0].startsWith(r.text), shown[0]);
    r.refused(p);
    assert.equal(p.W.ta.value, "what did the tests say", "the draft stays where it was");
    assert.deepEqual(liveNotices(p.box()), [], "a state the fresh page shows for itself, or no longer has, does not ride the reload");
  });
}

test("a toast about what happened, raised beside the refusals, is read: the mark is on the state refusals alone", () => {
  const p = page({ edit: true, sessions: ["web"] });
  p.stageComposer();
  p.warnToast(NACK);
  p.branchjump({ dataset: { sid: "api" } });
  assert.equal(p.shown().length, 3, "all three are on screen for the person at the page");
  assert.deepEqual(liveNotices(p.box()), [NACK]);
});

// The wiring, pinned to source (render.ts executes nothing under node --test).
test("render.ts: warnToast hands back its toast, and the refusals about a state are marked ephemeral where they are raised", () => {
  assert.match(RENDER, /^function warnToast\(msg: string\): HTMLElement \{/m);
  assert.match(RENDER, /setTimeout\(\(\) => t\.remove\(\), 12000\);\n\s*return t;/);
  assert.match(RENDER, /^function ephemeralWarnToast\(msg: string\): void \{ warnToast\(msg\)\.dataset\.ephemeral = "1"; \}/m);
  assert.equal((RENDER.match(/dataset\.ephemeral/g) || []).length, 1, "marked in one place");
  assert.equal((RENDER.match(/ephemeralWarnToast\("Can't send yet — the session isn't reachable\. They stay staged\."\);/g) || []).length, 2,
    "the staged sends' refusal at both of its sites (the strip's Send now and the empty send)");
  // the staging refusals (stageComposer) and the branch jump's: states the fresh page shows for itself or no longer has
  assert.match(RENDER, /if \(composerAnswersAsk\(\)\) \{ ephemeralWarnToast\("A picker is waiting on this box/);
  assert.match(RENDER, /if \(composerEdits\.has\(activeId\)\) \{ ephemeralWarnToast\("An edit replaces a past message/);
  assert.match(RENDER, /if \(\(composerFiles\.get\(activeId\) \|\| \[\]\)\.length\) \{ ephemeralWarnToast\("Attachments can't be staged/);
  assert.match(RENDER, /if \(!sessions\.get\(sid\)\) \{ ephemeralWarnToast\("That session isn't on this dashboard right now\."\); return; \}/);
  // the send into a tab whose create failed (a provisional tab does not survive a reload) and the queued edit's send on
  // a session that cannot be reached (the edit lives in memory alone): states the fresh page no longer has
  assert.match(RENDER, /if \(sid !== provisionalId\) \{\n\s*ephemeralWarnToast\("“" \+ \(sessions\.get\(sid\)\?\.name \|\| "this session"\) \+ "” never started, so there's "/);
  assert.match(RENDER, /if \(hostIsDown\(ed\.sid\) \|\| isProvisionalId\(ed\.sid\)\) \{\n\s*if \(hostIsDown\(ed\.sid\)\) vscodeApi\?\.postMessage\(\{ type: "redial"[^\n]*\n\s*ephemeralWarnToast\("Can't reach the session right now, so the edit wasn't saved\./,
    "the in-place Save's guard (T306): the field keeps the words, a re-dial is asked for, and the refusal is ephemeral (the edit lives in memory alone)");
  // the plain send's refusal on a disconnected host: the host's reach is a state the fresh page reads from the kernel's
  // tunnel health (the tab mark, the transcript foot), and the re-dial that makes "re-dialing now" true is posted by
  // the gesture, never by a replay
  assert.match(RENDER, /if \(hostIsDown\(sid\)\) \{\n\s*const host = String\(sid\)\.slice\(0, String\(sid\)\.indexOf\(":"\)\);\n(\s*\/\/[^\n]*\n)*\s*vscodeApi\?\.postMessage\(\{ type: "redial", host \}\);\n(\s*\/\/[^\n]*\n)*\s*ephemeralWarnToast\(host \+ " is disconnected, so this wasn't sent\. It's still in the box/);
  assert.equal((RENDER.match(/ephemeralWarnToast\(/g) || []).length, 12, "the definition, the two reachability sites, the eight state refusals and the bell toggle's word on the new state (2026-09-11: a confirmation is as stale after a reload as a refusal)");
  // what the nack, the dismissal and the other-tab ack say stays true after the reload, so they ride it unmarked
  assert.match(RENDER, /warnToast\(m\.name \+ " couldn't be saved on the kernel, so it was not attached/);
  assert.match(RENDER, /warnToast\("The pending upload was dismissed — your held message was NOT sent\."\)/);
  assert.match(RENDER, /warnToast\("attachments finished uploading on another tab — the held message was not sent; review it there\."\)/);
});

test("render.ts keeps the notices on the core's hook alone, pagehide keeps the scroll record alone, and the fresh page shows them once after the loss toast", () => {
  assert.match(RENDER, /^import \{ liveNotices, keepReloadNotices, takeReloadNotices \} from "\.\/reload-notices";/m);
  assert.match(RENDER, /^function persistNoticesForReload\(\): void \{\n\s*try \{ keepReloadNotices\(sessionStorage, liveNotices\(document\.getElementById\("warn-toasts"\)\)\); \} catch \{ \/\* ignore \*\/ \}\n\}/m);
  // the core's synchronous hook writes both records; a navigation of the user's own (pagehide) writes the scroll record
  // alone, so a load they asked for does not replay a toast they were already looking at
  assert.match(RENDER, /^function persistForReload\(\): void \{ persistScrollForReload\(\); persistNoticesForReload\(\); \}[^\n]*\n\(window as any\)\.__rompPersistForReload = persistForReload;\nwindow\.addEventListener\("pagehide", persistScrollForReload\);/m);
  assert.equal((RENDER.match(/persistNoticesForReload\(\)/g) || []).length, 2, "defined once, called from the core's hook alone");
  const scroll = RENDER.match(/^function persistScrollForReload\(\): void \{([\s\S]*?)\n\}/m);
  assert.ok(scroll && !scroll[1].includes("Notices"), "the scroll record is untouched");
  // the replay follows the loss toast's block directly: the loss first, then what the last page was saying
  assert.match(RENDER, /shipsInFlight: \[\] \}\);\n\s*\}\n\s*\}\n\} catch \{ \/\* ignore \*\/ \}\n(\/\/[^\n]*\n)*try \{ for \(const text of takeReloadNotices\(sessionStorage\)\) warnToast\(text\); \} catch \{ \/\* ignore \*\/ \}/);
  assert.equal((RENDER.match(/takeReloadNotices\(/g) || []).length, 1, "consumed once, at load");
  assert.equal((RENDER.match(/keepReloadNotices\(/g) || []).length, 1, "written from one place");
});
