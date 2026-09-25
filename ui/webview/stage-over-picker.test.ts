// ⌘⏎ stages over a question picker, EXECUTED (the user 2026-09-25, who wanted ⌘⏎ to stage whatever the typing order).
// While a session waits on a question with a free-text slot, the message box is that question's "add your own answer"
// field. ⌘⏎ used to refuse there with a toast, but only for a draft begun after the question arrived; a draft already
// under way staged fine, so staging depended on typing order. Now it always stages, the emptied box is the answer field
// again (tint and placeholder), and the staged items wait through the answer: a typed answer goes to the question alone,
// and an empty ⏎ releases the staged items as ordinary messages, which the session takes after the answer.
//
// stageComposer and sendComposer are lifted out of the composer closure, and composerAnswersAsk, draftPredatesAsk and
// setComposerAskMode out of the module, transpiled at run time and run over a small fake page (the
// composer-ship-gate-exec.test.ts idiom) with the real StagedStack under them, so the answer-mode decision is the real
// one reading the real stamps. The browser run of the same story is tests/test_stage_over_picker_served.py. Synthetic
// sessions only (web).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { StagedStack } from "./staged-messages";

const requireCjs = createRequire(__filename);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

function liftBetween(startAnchor: string, endAnchor: string): string {
  const a = RENDER.indexOf(startAnchor), b = RENDER.indexOf(endAnchor, a);
  assert.ok(a > 0 && b > a, `anchors not found: ${startAnchor.slice(0, 40)} or ${endAnchor.slice(0, 40)} moved; re-anchor`);
  return requireCjs("esbuild").transformSync(RENDER.slice(a, b), { loader: "ts" }).code;
}

const RESTING = "Message this session…";
const ANSWERING = "add your own answer…  (⏎ submit)";
// a question with a free-text slot (the SDK's AskUserQuestion shape), and a permission prompt, which has none
const QUESTION = { kind: "single", question: "Which database should the notes-api use?", cursor: 1, cursorFound: true, multiSelect: false, sig: "q1",
  options: [{ n: 1, label: "Postgres", selected: true }, { n: 2, label: "SQLite", selected: false }, { n: 3, label: "Type something.", selected: false }] };
const PERMISSION = { kind: "single", question: "Allow Bash?", cursor: 1, cursorFound: true, multiSelect: false, sig: "p1",
  options: [{ n: 1, label: "Allow", selected: true }, { n: 2, label: "Deny", selected: false }] };

/** The box: its value, its class list and its placeholder, which setComposerAskMode paints. */
function fakeBox() {
  const cls = new Set<string>();
  return { value: "", placeholder: RESTING, style: {} as Record<string, string>,
           classList: { add: (c: string) => { cls.add(c); }, remove: (c: string) => { cls.delete(c); }, contains: (c: string) => cls.has(c) } };
}

/** The page the lifted code reads: session web, its box, its question (none until ask() puts one up), the stamps that
 *  decide answer-vs-message, a real staged stack, and every route the box can take recorded. `now` is the clock the
 *  stamps are written with, advanced by hand so "before" and "after" are exact. */
function world() {
  const ta = fakeBox();
  return {
    ta, now: 1000,
    document: { getElementById: (id: string) => (id === "composer-input" ? ta : null) },
    liveAsks: new Map<string, unknown>(), askArrivedAt: new Map<string, number>(), draftStartedAt: new Map<string, number>(),
    stagedMsgs: new StagedStack(),
    composerEdits: new Map<string, { uuid: string; orig: string }>(), composerFiles: new Map<string, string[]>(),
    composerCitations: new Map<string, { quote?: string }[]>(),
    answers: [] as { route: "custom" | "text"; text: string }[],   // addCustomLiveAsk / sendTextLiveAsk: the question's free-text path
    flushed: [] as { sid: string; run: string[]; typed?: string }[], // every flushStaged: the one door a normal send and a release leave by
    toasts: [] as string[], stripPaints: 0,
  };
}
type World = ReturnType<typeof world>;
type Api = { stageComposer: () => void; sendComposer: () => void; setComposerAskMode: () => void };

function page() {
  const W = world();
  const prelude = `
    const W = WORLD;
    const document = W.document;
    let activeId = "web";
    const ta = W.ta;
    const liveAsks = W.liveAsks, askArrivedAt = W.askArrivedAt, draftStartedAt = W.draftStartedAt;
    const stagedMsgs = W.stagedMsgs;
    const composerEdits = W.composerEdits, composerFiles = W.composerFiles, composerCitations = W.composerCitations;
    const drafts = new Map(), sessions = new Map([["web", { name: "web" }]]), tabMeta = new Map();
    const pendingShips = new Map(), sendOnShip = new Set(), histWalk = new Map(), lastSent = new Map();
    let shipGateSid = null;
    const hostIsDown = () => false, isProvisionalId = () => false, provisionalId = null, provisionalQueue = [];
    const addCustomLiveAsk = (text) => { W.answers.push({ route: "custom", text }); };
    const sendTextLiveAsk = (text) => { W.answers.push({ route: "text", text }); };
    const flushStaged = (sid, typed) => { const run = stagedMsgs.takeAll(sid); W.flushed.push(typed ? { sid, run: run.map((s) => s.text), typed: typed.text } : { sid, run: run.map((s) => s.text) }); return run.length; };
    const persistDrafts = () => {};
    const clearBox = () => { ta.value = ""; };
    const renderStagedStrip = () => { W.stripPaints++; };
    const renderComposerChips = () => {}, renderComposerFiles = () => {};
    const warnToast = (msg) => { W.toasts.push(msg); }, ephemeralWarnToast = warnToast;
    const syncComposerPh = () => {};
    const composerRestingPlaceholder = () => ${JSON.stringify(RESTING)};
    const liveSession = () => undefined, registerOptimistic = () => {}, previewKind = () => null;
    const isClearCmd = () => false, isNewCmd = () => false, clearConfirmDetail = () => null, openTopTitles = () => [], ledgers = new Map();
    const openMcpPanel = () => {}, showConfirm = () => { throw new Error("no dialog in this world"); }, endReloadHoldIfIdle = () => {};
    const vscodeApi = { postMessage: () => { throw new Error("a send leaves by flushStaged or the question's path in this world"); } };
    const pendingRewind = new Map(), reconcileRewind = () => {}, appendActive = () => {};
  `;
  const typeSomething = liftBetween("function isTypeSomething(label: string)", "function isMetaOption(");
  const mode = liftBetween("function composerAnswersAsk(): ", "// Render the widget matching the active session's pending prompt.");
  const composer = liftBetween("const stageComposer = () => {", "// an explicit send button on the right of the box");
  const api = (new Function("WORLD", prelude + typeSomething + mode + composer
    + "\nreturn { stageComposer, sendComposer: () => sendComposer(), setComposerAskMode };") as (w: World) => Api)(W);
  // what the page does around the lifted code: a question arriving (setLiveAsk's stamp, then renderLiveAsk's repaint)
  // and typing into the box (the input handler's draft stamp, then its repaint on empty to non-empty)
  const ask = (q: unknown) => { W.now += 10; if (!W.liveAsks.has("web")) W.askArrivedAt.set("web", W.now); W.liveAsks.set("web", q); api.setComposerAskMode(); };
  const type = (text: string) => { W.now += 10; if (!W.draftStartedAt.has("web")) W.draftStartedAt.set("web", W.now); W.ta.value = text; api.setComposerAskMode(); };
  const answering = () => W.ta.classList.contains("answering") && W.ta.placeholder === ANSWERING;
  const resting = () => !W.ta.classList.contains("answering") && W.ta.placeholder === RESTING;
  const staged = () => W.stagedMsgs.list("web").map((s) => s.text);
  return { W, ...api, ask, type, answering, resting, staged };
}

test("a line typed after the question arrived stages on ⌘⏎, with no toast, and the emptied box is the answer field again", () => {
  const p = page();
  p.ask(QUESTION);
  assert.ok(p.answering(), "the question takes the empty box");
  p.type("check the migration notes first");
  assert.ok(p.answering(), "typed after the question: an answer, so the box stays the answer field");
  p.stageComposer();
  assert.deepEqual(p.staged(), ["check the migration notes first"], "staged");
  assert.equal(p.W.ta.value, "", "the box emptied into the staged item");
  assert.deepEqual(p.W.toasts, [], "no refusal");
  assert.ok(p.answering(), "and the box is the answer field, tint and placeholder");
  assert.deepEqual(p.W.answers, [], "staging answers nothing");
  assert.equal(p.W.stripPaints, 1, "the strip shows the item");
});

test("a draft begun before the question arrived stages too, and the emptied box turns into the answer field", () => {
  const p = page();
  p.type("a note written before the question");
  p.ask(QUESTION);
  assert.ok(p.resting(), "a draft already under way when the question landed is a message, not an answer");
  p.stageComposer();
  assert.deepEqual(p.staged(), ["a note written before the question"]);
  assert.equal(p.W.ta.value, "");
  assert.deepEqual(p.W.toasts, []);
  assert.ok(p.answering(), "the draft's stamp went with it, so the question re-takes the empty box, repainted at once");
});

test("a typed answer goes to the question alone and the staged items wait: for a question with options and for a free-text prompt", () => {
  for (const [q, route] of [[QUESTION, "custom"], [null, "text"]] as const) {
    const p = page();
    p.ask(q);
    p.type("check the migration notes first");
    p.stageComposer();
    p.type("Postgres, with the pooled driver");
    p.sendComposer();
    assert.deepEqual(p.W.answers, [{ route, text: "Postgres, with the pooled driver" }], route + ": the answer is the typed words alone");
    assert.deepEqual(p.W.flushed, [], route + ": nothing released with the answer");
    assert.deepEqual(p.staged(), ["check the migration notes first"], route + ": the staged item is still held");
    assert.equal(p.W.ta.value, "", route);
  }
});

test("an empty ⏎ with the question still up releases the staged items as ordinary messages, and answers nothing", () => {
  const p = page();
  p.ask(QUESTION);
  p.type("check the migration notes first");
  p.stageComposer();
  p.type("and the rollback plan");
  p.stageComposer();
  p.sendComposer();
  assert.deepEqual(p.W.flushed, [{ sid: "web", run: ["check the migration notes first", "and the rollback plan"] }],
    "released alone, in stage order: the session takes them after the question is answered");
  assert.deepEqual(p.W.answers, [], "an empty box is no answer");
  assert.deepEqual(p.staged(), []);
});

test("a picker with no free-text slot (a permission prompt) never owned the box: ⌘⏎ stages and ⏎ sends the run as ever", () => {
  const p = page();
  p.ask(PERMISSION);
  assert.ok(p.resting(), "no free-text path, no answer field");
  p.type("hold this one");
  p.stageComposer();
  assert.deepEqual(p.staged(), ["hold this one"]);
  assert.ok(p.resting());
  p.type("and send");
  p.sendComposer();
  assert.deepEqual(p.W.flushed, [{ sid: "web", run: ["hold this one"], typed: "and send" }]);
  assert.deepEqual(p.W.answers, []);
});

test("the two refusals that remain still refuse with a question up: an edit in progress, attachments on the box", () => {
  const edit = page();
  edit.ask(QUESTION);
  edit.W.composerEdits.set("web", { uuid: "11111111-2222-3333-4444-555555555555", orig: "the old text" });
  edit.type("the new text");
  edit.stageComposer();
  assert.deepEqual(edit.staged(), []);
  assert.equal(edit.W.toasts.length, 1);
  assert.ok(edit.W.toasts[0].startsWith("An edit replaces a past message"), edit.W.toasts[0]);
  assert.equal(edit.W.ta.value, "the new text", "the words stay in the box");
  const files = page();
  files.ask(QUESTION);
  files.W.composerFiles.set("web", ["schema.sql"]);
  files.type("see the schema");
  files.stageComposer();
  assert.deepEqual(files.staged(), []);
  assert.equal(files.W.toasts.length, 1);
  assert.ok(files.W.toasts[0].startsWith("Attachments can't be staged"), files.W.toasts[0]);
});

// What the lifts do not reach, pinned at the source.
test("render.ts: no picker refusal on the stage path, and clicking an option or answering through the box never touches the staged stack", () => {
  const stage = RENDER.split("const stageComposer = () => {")[1].split("const sendComposer = (")[0];
  assert.doesNotMatch(stage, /composerAnswersAsk\(\)/, "staging never asks whether the box answers a question");
  assert.doesNotMatch(RENDER, /A picker is waiting on this box/, "the refusal's words are gone");
  assert.match(stage, /renderStagedStrip\(activeId, \{ reveal: "last" \}\);[^\n]*\n(\s*\/\/[^\n]*\n)*\s*setComposerAskMode\(\);\n\s*\};/,
    "the stage path ends by repainting the box's answer mode");
  // the question's own answer paths (an option's click, a checkbox, Submit, Cancel, the free-text answer) post to the
  // question and nothing else
  const answers = RENDER.split("function answerLiveAsk(target: number) {")[1].split("// Animated Claude-Code-style")[0];
  assert.doesNotMatch(answers, /stagedMsgs|flushStaged|renderStagedStrip/, "no answer path releases or drops a staged item");
  // the box's answer branch returns before deliver's flushStaged
  const send = RENDER.split("const sendComposer = (")[1].split("const deliver = () => {")[0];
  assert.match(send, /if \(askRoute\) \{\n\s*if \(askRoute === "custom"\) addCustomLiveAsk\(typed\); else sendTextLiveAsk\(typed\);\n[^\n]*\n\s*clearBox\(\);\n\s*return;\n\s*\}/);
  assert.doesNotMatch(send.split("if (askRoute) {")[1].split("return;")[0], /flushStaged|stagedMsgs/);
});
