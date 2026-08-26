// A new session shows its chat box immediately and starts behind it (the user 2026-07-30).
//
// Creating a session used to raise an "Opening session…" modal over the pane: the kernel resolved the
// directory, spawned tmux or connected the SDK, and the first transcript poll came back — seconds you
// could do nothing with, watching three bouncing dots. Now the tab is there from the first click with a
// live composer; anything typed is HELD and flushed the moment the real session lands; and a create that
// fails says so in a dialog carrying the kernel's own words, instead of the cue silently timing out.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { mintProvisionalId, isProvisionalId, provisionalName, adoptsProvisional,
  PROVISIONAL_PREFIX } from "./provisional";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("a provisional id carries NO colon — federation would read it as a host", () => {
  // this is the trap that swallowed follow-ups on remote cards: routing keys off id/sid and takes the
  // part before a colon as a host name, so a "pending:" form would address a machine called "pending"
  for (const seed of ["1a2b3c", "web:api", "2026-07-30T12:00:00.000Z", "…ünïcode…"]) {
    const id = mintProvisionalId(seed);
    assert.ok(!id.includes(":"), `${id} must not contain a colon`);
    assert.ok(id.startsWith(PROVISIONAL_PREFIX));
  }
});

test("distinct seeds mint distinct ids, and every one is recognisable as provisional", () => {
  const a = mintProvisionalId("aaa1"), b = mintProvisionalId("bbb2");
  assert.notEqual(a, b);
  assert.ok(isProvisionalId(a) && isProvisionalId(b));
});

test("a real session id is never mistaken for a provisional one", () => {
  assert.ok(!isProvisionalId("11111111-2222-3333-4444-555555555555"));
  assert.ok(!isProvisionalId("web:11111111-2222-3333-4444-555555555555"));
  assert.ok(!isProvisionalId(null));
  assert.ok(!isProvisionalId(undefined));
});

test("a remote create is matched against the HOST-PREFIXED name its tab arrives under", () => {
  assert.equal(provisionalName("web", "api"), "web:api");
  assert.equal(provisionalName("", "api"), "api", "a local one arrives bare");
});

test("only an unseen session under exactly the requested name adopts the tab", () => {
  assert.ok(adoptsProvisional(false, "api", "api"));
  assert.ok(!adoptsProvisional(true, "api", "api"), "a session we already had is not the one we asked for");
  assert.ok(!adoptsProvisional(false, "tests", "api"), "…and neither is somebody else's new session");
  assert.ok(!adoptsProvisional(false, "api", null), "nothing pending, nothing to adopt");
  assert.ok(!adoptsProvisional(false, "web:api", "api"), "a remote arrival is not the local one we asked for");
});

// ── the wiring in render.ts ────────────────────────────────────────────────────────────────────────

test("creating a session opens the provisional tab instead of a modal", () => {
  assert.match(RENDER, /openProvisional\(req\);/);
  assert.doesNotMatch(RENDER, /showOpeningModal/, "the modal is gone, not merely hidden");
  assert.doesNotMatch(RENDER, /hideOpeningModal/);
  // "opening", never "working": the working chip renders an elapsed timer off sinceEpoch, and a
  // provisional tab has no honest work clock — the old "working" seed (in SECONDS, at that) showed
  // "Working" + an epoch-sized number until the first kernel payload arrived (the user 2026-08-10).
  assert.match(RENDER, /status: \{ state: "opening", sinceEpoch: Date\.now\(\) \}/,
    "the chip says what this phase IS — the session is opening");
  assert.doesNotMatch(RENDER, /state: "working", sinceEpoch: Math\.floor/, "the broken-clock seed is gone");
  // …and the tab strip shows the accent loader dot for the opening state, so the starting tab has a cue
  assert.match(RENDER, /else if \(st === "opening"\) tab\.appendChild\(el\("span", "tab-dot opening"\)\);/);
  assert.match(CSS, /\.tab-dot\.opening \{ background: var\(--accent\); animation: opening-line-pulse/);
  assert.match(RENDER, /order\.push\(id\);/, "the tab survives reconcileTabOrder as a not-yet-kernel-known extra");
});

test("a send on a provisional tab is HELD, not posted to a session that doesn't exist", () => {
  assert.match(RENDER, /provisionalQueue\.push\(text\);\s*\n\s*registerOptimistic\(sid, text, attached\.filter\(\(p\) => previewKind\(p\) === "img"\)\);/,
    "the dashed bubble goes up now — with its dragged-image thumbnails — romp has it, it is not delivered");
  // a FAILED tab has no pending spawn to queue onto: refuse loudly, the box keeps the only copy
  assert.match(RENDER, /if \(sid !== provisionalId\) \{\s*\n\s*warnToast\("“" \+ \(sessions\.get\(sid\)\?\.name \|\| "this session"\)/);
});

test("adoption flushes the held messages FOR REAL and carries the draft across", () => {
  assert.match(RENDER, /if \(adoptsProvisional\(existed, msg\.name, pendingNewSession\)\) \{\s*\n\s*adoptProvisional\(msg\.id\);/);
  assert.match(RENDER, /vscodeApi\?\.postMessage\(\{ type: "sendMessage", id: realId, text \}\);/);
  assert.match(RENDER, /registerOptimistic\(realId, text\);/);
  // the draft must be set BEFORE the switch — setActive fills the box from `drafts`
  const adopt = RENDER.slice(RENDER.indexOf("function adoptProvisional"));
  assert.ok(adopt.indexOf("drafts.set(realId, draft)") < adopt.indexOf("setActive(realId)"),
    "setActive reads the draft map, so the carry has to be in it first");
});

test("a failed create says so in a dialog, in the kernel's own words — ON the failed thread", () => {
  assert.match(RENDER, /if \(provisionalId\) failProvisional\(m\.text\); else warnToast\(m\.text\);/);
  assert.match(RENDER, /showConfirm\("Couldn't start " \+ name,/);
  assert.match(RENDER, /What you typed is in this tab's message box\./,
    "losing the text would be the one unrecoverable part");
  // The failure JUMPS BACK to its own tab first, and the text goes into THAT tab's box (the user
  // 2026-08-08, whose held text landed in the draft of an unrelated thread they happened to be
  // reading). The tab stays — the dialog is on top of the thread it is about.
  const fail = RENDER.slice(RENDER.indexOf("function failProvisional"), RENDER.indexOf("function cancelProvisional"));
  assert.ok(fail.includes("setActive(id);"), "foreground the failed thread before saying anything");
  assert.ok(fail.includes("drafts.set(id, held); persistDrafts();"), "the text belongs to the failed tab, no other");
  assert.ok(fail.indexOf("setActive(id);") < fail.indexOf("showConfirm("), "jump first, dialog second");
  assert.ok(!fail.includes("= dropProvisional()"), "the tab is NOT torn down — it holds the text");
  assert.ok(fail.includes("failedProvisionals.add(id);"));
  // the failed tab's transcript says what happened (the starting loader would be a lie)…
  assert.match(RENDER, /This session couldn't start\. What you typed is kept in the box below/);
  assert.match(RENDER, /const staleStart = !!only && only\.classList\?\.contains\("tx-starting"\) && failedProvisionals\.has\(id\);/);
  // …and its composer stays LIVE despite the closed-tab treatment, so the text is editable/copyable
  assert.match(RENDER, /const closed = s\.status\.state === "closed" && !failedProvisionals\.has\(activeId!\);/);
});

test("the silent-failure backstop is long, because it is no longer what you wait on", () => {
  const m = RENDER.match(/const PROVISIONAL_WAIT_MS = ([0-9_]+);/);
  assert.ok(m, "there is still a backstop for a spawn that dies saying nothing");
  assert.ok(Number(m![1].replace(/_/g, "")) >= 60_000, "well past the old 30s cue");
});

test("closing a provisional tab aborts the pending spawn; a FAILED one is a plain local discard", () => {
  assert.match(RENDER, /if \(id === provisionalId\) cancelProvisional\(\);\s*\n\s*else \{ failedProvisionals\.delete\(id\); dismissSession\(id\); \}/);
  assert.match(RENDER, /vscodeApi\.postMessage\(\{ type: "cancelCreate", name \}\)/);
  // the kernel never knew a provisional id — the dead-tab ✕ must not post closeTab for one
  assert.match(RENDER, /if \(!isProvisionalId\(id\)\) vscodeApi\.postMessage\(\{ type: "closeTab", id \}\);/);
});

test("the folder question retires the tab and holds what was typed for the retry", () => {
  assert.match(RENDER, /const held = dropProvisional\(\);/);
  assert.match(RENDER, /pendingCarry = \[\.\.\.held\.queued, held\.draft\]\.filter\(Boolean\)/);
  assert.match(RENDER, /if \(ta && pendingCarry\) \{ ta\.value = pendingCarry; growComposer\(ta\); \}/);
});

test("a starting tab shows the romp loader, not the 'No messages yet' placeholder", () => {
  assert.match(RENDER, /\} else if \(isProvisionalId\(id\)\) \{\s*\n\s*ph\.classList\.add\("tx-starting"\);/);
  assert.match(RENDER, /romp-swirl-glyph\.svg/);
  assert.match(RENDER, /"Starting " \+ s\.name \+ "… you can type now; romp sends it when it's up\."/);
  assert.match(CSS, /\.tx-starting-swirl \{[\s\S]*?animation: tx-starting-spin/);
  assert.match(CSS, /prefers-reduced-motion: reduce\) \{ \.tx-starting-swirl \{ animation: none/);
  assert.doesNotMatch(CSS, /opening-dots/, "the bouncing-dots modal CSS went with it");
});
