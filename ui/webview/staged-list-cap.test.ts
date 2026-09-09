// The staged strip above the composer: the items sit in their own list under the head, about four items
// tall, and the rest scroll; the head with the count and Send now stays outside the scroll; a caret on
// the head collapses the strip to that one line. And the run releases as ONE message: flushStaged routes
// the posts stagedPosts composes from the staged items and the typed message.
//
// The strip's behaviour is EXECUTED: routeUserMessage, flushStaged and renderStagedStrip are lifted out
// of render.ts, transpiled at run time and driven over a minimal fake DOM (the tab-strip-skip-exec.test.ts
// idiom), with the real StagedStack and the module's real composition under them, so a change that keeps
// the pinned lines but breaks the logic around them fails here. What a fake DOM cannot see is pinned at
// the source: the CSS (the cap's px and its geometry inputs, the wrap rules) and the call sites inside
// the composer closure, which nothing lifts (deliver's send, the stage path's reveal). The cap's geometry
// is measured in a real browser by staged-list-layout.test.ts where one is installed.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { StagedStack, quoteReplyBody, stagedRunBody, stagedPosts } from "./staged-messages";

const requireCjs = createRequire(__filename);
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const FLUSH = RENDER.split("function flushStaged(")[1].split("\nfunction ")[0];
const LIST = (CSS.match(/\.staged-list \{[^}]*\}/) || [""])[0];

/** Enough of Element for the strip's paint: children, classList, dataset, style, attributes, listeners
 *  (fired by hand), a class-selector querySelector, a scroll offset with a synthetic scrollHeight (50 per
 *  child, so a reveal to the end reads as a different number from the top), and focus, which the fake
 *  document records. */
class FakeEl {
  static doc: { activeElement: FakeEl | null } = { activeElement: null };
  tag: string; className: string; children: FakeEl[] = []; parent: FakeEl | null = null;
  dataset: Record<string, string> = {}; attrs: Record<string, string> = {}; listeners: Record<string, Function[]> = {};
  textContent = ""; title = ""; scrollTop = 0;
  style: { display: string } = { display: "" };
  classList: { add(...c: string[]): void; remove(...c: string[]): void; contains(c: string): boolean };
  constructor(tag: string, cls = "") {
    this.tag = tag; this.className = cls;
    const self = this;
    this.classList = {
      add(...c: string[]) { for (const x of c) if (!self.has(x)) self.className = (self.className + " " + x).trim(); },
      remove(...c: string[]) { for (const x of c) self.className = self.className.split(/\s+/).filter((y) => y !== x).join(" "); },
      contains(x: string) { return self.has(x); },
    };
  }
  has(x: string): boolean { return this.className.split(/\s+/).includes(x); }
  get scrollHeight(): number { return this.children.length * 50; }
  appendChild(c: FakeEl): FakeEl { c.parent = this; this.children.push(c); return c; }
  append(...cs: FakeEl[]): void { for (const c of cs) this.appendChild(c); }
  replaceChildren(...cs: FakeEl[]): void { this.children = []; this.append(...cs); }
  setAttribute(k: string, v: string): void { this.attrs[k] = v; }
  addEventListener(t: string, f: Function): void { (this.listeners[t] ??= []).push(f); }
  fire(t: string, ev: { stopPropagation?: () => void } = {}): void { for (const f of this.listeners[t] ?? []) f({ stopPropagation() { /* the default: nothing to stop */ }, ...ev }); }
  focus(): void { FakeEl.doc.activeElement = this; }
  /** ".class" selectors only, the first matching descendant in document order (what the paint asks for). */
  querySelector(sel: string): FakeEl | null {
    const cls = sel.replace(/^\./, "");
    for (const c of this.children) { if (c.has(cls)) return c; const d = c.querySelector(sel); if (d) return d; }
    return null;
  }
}

type Posted = { type: string; [k: string]: unknown };
type FakeDocument = { activeElement: FakeEl | null; getElementById: (id: string) => FakeEl | null };
type Hooks = {
  FakeEl: typeof FakeEl; document: FakeDocument;
  StagedStack: typeof StagedStack; quoteReplyBody: typeof quoteReplyBody; stagedPosts: typeof stagedPosts;
  posted: Posted[];                                                   // every vscodeApi.postMessage frame, in order
  optimistic: { sid: string; text: string; imgPaths?: string[] }[];   // every registerOptimistic call
  persists: number; down: Set<string>; provisional: Set<string>; toasts: string[];
};
type Api = {
  routeUserMessage: (sid: string, text: string, cites: unknown[] | undefined, imgPaths?: string[]) => void;
  flushStaged: (sid: string, typed?: { text: string; cites?: unknown[]; imgPaths?: string[] }) => number;
  renderStagedStrip: (id: string | null, opts?: { reveal?: "last" }) => void;
  stagedMsgs: StagedStack; stagedOpen: Set<string>; stagedCollapsed: Set<string>; stagedScroll: Map<string, number>;
};

// routeUserMessage, flushStaged and renderStagedStrip, transpiled (TS to JS) with esbuild at run time and
// required dynamically so the test bundle does not try to bundle esbuild itself (the models-rev.test.ts
// pattern). The module-level state they close over is minted fresh per world.
function lift(): (hooks: Hooks) => Api {
  const a = RENDER.indexOf("function routeUserMessage("), b = RENDER.indexOf("\nfunction beginComposerEdit(");
  assert.ok(a > 0 && b > a, "anchors not found: routeUserMessage or beginComposerEdit moved; re-anchor");
  const js = requireCjs("esbuild").transformSync(RENDER.slice(a, b), { loader: "ts" }).code;
  const prelude = `
    const H = HOOKS;
    const el = (tag, cls) => new H.FakeEl(tag, cls);
    const document = H.document;
    const stagedMsgs = new H.StagedStack();
    const stagedOpen = new Set(), stagedCollapsed = new Set(), stagedScroll = new Map();
    const quoteReplyBody = H.quoteReplyBody, stagedPosts = H.stagedPosts;
    const vscodeApi = { postMessage: (m) => { H.posted.push(m); } };
    const registerOptimistic = (sid, text, imgPaths) => { H.optimistic.push({ sid, text, imgPaths }); };
    const persistDrafts = () => { H.persists++; };
    const hostIsDown = (id) => H.down.has(id); const isProvisionalId = (id) => H.provisional.has(id);
    const warnToast = (msg) => { H.toasts.push(msg); };
    const ephemeralWarnToast = (msg) => { H.toasts.push(msg); };   // the unreachable-session word rides the ephemeral toast (reload-notices)
  `;
  const epilogue = `return { routeUserMessage, flushStaged, renderStagedStrip, stagedMsgs, stagedOpen, stagedCollapsed, stagedScroll };`;
  return new Function("HOOKS", prelude + js + epilogue) as (hooks: Hooks) => Api;
}

// synthetic session ids, private to this file
const A = "5b1e7c2a-4d3f-4a6b-9c8d-0e1f2a3b4c5d", B = "8c2d9e4f-1a5b-4c7d-8e9f-0a1b2c3d4e5f";
const Q1 = { quote: "const a = 1;", src: "src/a.ts:12-12", title: "t" };
const Q2 = { quote: "first line\nsecond line", title: "t" };
const G = { itemId: A + ":g1", title: "t" };   // a goal citation: the card the words follow up on
const send = (m: Posted) => m.type !== "clientDiag";   // the frames the kernel acts on (the breadcrumb aside)

/** The strip (#composer-staged) in an otherwise empty page, and the lifted functions over fresh state. */
function world(): { H: Hooks; api: Api; strip: FakeEl; document: FakeDocument } {
  const strip = new FakeEl("div");
  const document: FakeDocument = { activeElement: null, getElementById: (id) => id === "composer-staged" ? strip : null };
  FakeEl.doc = document;
  const H: Hooks = { FakeEl, document, StagedStack, quoteReplyBody, stagedPosts, posted: [], optimistic: [], persists: 0, down: new Set(), provisional: new Set(), toasts: [] };
  return { H, api: lift()(H), strip, document };
}
const listOf = (strip: FakeEl): FakeEl => { const l = strip.querySelector(".staged-list"); assert.ok(l, "the strip holds a .staged-list"); return l; };

test("the head is the strip's first child, with the caret, the count and Send now; every chip sits in .staged-list under it", () => {
  const { api, strip } = world();
  api.renderStagedStrip(A);
  assert.equal(strip.style.display, "none", "nothing staged: hidden");
  api.stagedMsgs.push(A, { text: "rename it", cites: [Q1] });
  api.stagedMsgs.push(A, { text: "a bare note", cites: [] });
  api.renderStagedStrip(A);
  assert.equal(strip.style.display, "flex");
  assert.equal(strip.dataset.sid, A, "the strip says whose list it holds");
  assert.deepEqual(strip.children.map((c) => c.className), ["staged-head", "staged-list"], "the head, then the list; no chip lands on the strip itself");
  const [head, list] = strip.children;
  assert.deepEqual(head.children.map((c) => c.tag + "." + c.className), ["button.staged-caret", "span.staged-lbl", "button.staged-go"]);
  const [caret, lbl, go] = head.children;
  assert.equal(caret.attrs["aria-expanded"], "true");
  assert.equal(caret.attrs["aria-label"], "Hide the staged messages");
  assert.ok(lbl.textContent.startsWith("2 staged"), lbl.textContent);
  // the tooltip says what a send does, the two exceptions included
  assert.ok(lbl.title.includes("releases them as one message, in order, with your new message last"), lbl.title);
  assert.ok(lbl.title.includes("a card follow-up or a slash command goes on its own, in its place"), lbl.title);
  assert.ok(lbl.title.includes("Send now releases them alone."), lbl.title);
  assert.equal(go.textContent, "Send now");
  assert.equal(list.children.length, 2);
  assert.ok(list.children.every((c) => c.has("staged-chip")));
  // an item's anatomy is unchanged: its quote as the composer's blue pill, then the row with the words, the
  // hint and the discard ✕; and no drag and drop on any of it
  const [quoted, bare] = list.children;
  assert.deepEqual(quoted.children.map((c) => c.className), ["composer-chip staged-cite", "staged-row"]);
  assert.deepEqual(quoted.children[1].children.map((c) => c.tag + "." + c.className), ["span.composer-chip-mark", "span.composer-chip-label", "span.staged-expand", "button.composer-chip-x"]);
  assert.equal(quoted.children[1].children[1].textContent, "rename it");
  assert.deepEqual(bare.children.map((c) => c.className), ["staged-row"]);
  assert.equal(bare.querySelector(".staged-expand")!.textContent, "(click to expand)");
  for (const c of list.children) assert.ok(!c.listeners.dragstart && !c.listeners.dragover && !c.listeners.drop, "no drag and drop on a staged item");
});

test("the list's place is kept per tab across the rebuild an expand, a discard or a switch triggers; staging reveals the end; an emptied list forgets", () => {
  const { api, strip } = world();
  for (let i = 0; i < 6; i++) api.stagedMsgs.push(A, { text: "note " + i, cites: i % 2 ? [Q1] : [] });
  api.renderStagedStrip(A, { reveal: "last" });
  let list = listOf(strip);
  assert.equal(list.children.length, 6);
  assert.ok(list.scrollTop > 0 && list.scrollTop === list.scrollHeight, "the stage path reveals the new item: the list is scrolled to its end");
  list.scrollTop = 40;                              // the user scrolls
  api.renderStagedStrip(A);
  list = listOf(strip);
  assert.equal(list.scrollTop, 40, "a plain rebuild keeps the place");
  assert.equal(api.stagedScroll.get(A), 40);
  // expand an item: the strip rebuilds with the item open, at the same place
  list.children[2].fire("click");
  list = listOf(strip);
  assert.ok(list.children[2].has("open"), "the expand is keyed and survives the rebuild");
  assert.equal(list.children[2].querySelector(".staged-expand")!.textContent, "(collapse)");
  assert.equal(list.scrollTop, 40);
  // the quote pill has its own expand, and its click never toggles the row's
  list.children[1].querySelector(".staged-cite")!.fire("click");
  list = listOf(strip);
  assert.ok(list.children[1].querySelector(".staged-cite")!.has("open") && !list.children[1].has("open"));
  assert.equal(list.scrollTop, 40);
  // discard an item: gone from the stack and the list, the place kept, and the ✕'s click stops at itself
  let stopped = 0;
  list.children[0].querySelector(".composer-chip-x")!.fire("click", { stopPropagation: () => { stopped++; } });
  assert.equal(stopped, 1, "the ✕ never toggles the item it discards");
  list = listOf(strip);
  assert.equal(list.children.length, 5);
  assert.deepEqual(api.stagedMsgs.list(A).map((m) => m.text), ["note 1", "note 2", "note 3", "note 4", "note 5"]);
  assert.equal(list.scrollTop, 40);
  // a switch: the entering tab's list starts at its own place (none yet); the leaving tab's is remembered
  api.stagedMsgs.push(B, { text: "for b", cites: [] });
  api.renderStagedStrip(B);
  list = listOf(strip);
  assert.equal(strip.dataset.sid, B);
  assert.equal(list.scrollTop, 0, "B's list at B's place, not A's offset");
  assert.equal(api.stagedScroll.get(A), 40);
  list.scrollTop = 10;
  api.renderStagedStrip(A);
  assert.equal(listOf(strip).scrollTop, 40, "A comes back to its own place");
  assert.equal(api.stagedScroll.get(B), 10, "and B's is kept for its return");
  // emptied: hidden, its place forgotten with it; the other tab's stands
  api.stagedMsgs.takeAll(A);
  api.renderStagedStrip(A);
  assert.equal(strip.style.display, "none");
  assert.equal(strip.dataset.sid, undefined);
  assert.equal(api.stagedScroll.has(A), false);
  assert.equal(api.stagedScroll.get(B), 10);
  api.renderStagedStrip(null);
  assert.equal(strip.style.display, "none");
});

test("the caret and the label collapse the strip to the head line and back, per tab; the mark is the click's, and it ends with the stack", () => {
  const { api, strip } = world();
  for (let i = 0; i < 3; i++) api.stagedMsgs.push(A, { text: "note " + i, cites: [] });
  api.renderStagedStrip(A);
  assert.equal(strip.children.length, 2, "a fresh page shows the list");
  strip.querySelector(".staged-caret")!.fire("click");
  assert.deepEqual(strip.children.map((c) => c.className), ["staged-head"], "collapsed: the head line alone");
  const [caret, lbl, go] = strip.children[0].children;
  assert.equal(caret.textContent, "▸");
  assert.equal(caret.attrs["aria-expanded"], "false");
  assert.equal(caret.attrs["aria-label"], "Show the staged messages");
  assert.ok(lbl.textContent.startsWith("3 staged"), "the count stays on the head");
  assert.equal(go.textContent, "Send now", "and so does Send now");
  assert.ok(api.stagedCollapsed.has(A));
  api.renderStagedStrip(A);
  assert.equal(strip.children.length, 1, "any rebuild keeps the collapse: the state is the Set's, not the paint's");
  strip.children[0].children[1].fire("click");
  assert.equal(strip.children.length, 2, "the label opens it again");
  assert.equal(strip.children[0].children[0].textContent, "▾");
  assert.equal(strip.children[0].children[0].attrs["aria-expanded"], "true");
  assert.ok(!api.stagedCollapsed.has(A));
  // per tab: A collapsed, B shown, A still collapsed on its return
  strip.children[0].children[1].fire("click");
  api.stagedMsgs.push(B, { text: "for b", cites: [] });
  api.renderStagedStrip(B);
  assert.equal(strip.children.length, 2, "B's list shows: the collapse is A's");
  api.renderStagedStrip(A);
  assert.equal(strip.children.length, 1);
  // the stack empties (a release): the mark goes with it, so the next stack starts shown with its item in view
  api.stagedMsgs.takeAll(A);
  api.renderStagedStrip(A);
  assert.ok(!api.stagedCollapsed.has(A), "an emptied stack takes its collapse with it");
  api.stagedMsgs.push(A, { text: "a new run", cites: [] });
  api.renderStagedStrip(A, { reveal: "last" });
  assert.equal(strip.children.length, 2, "the new stack is shown, not hidden behind the old collapse");
  assert.equal(listOf(strip).scrollTop, listOf(strip).scrollHeight);
});

test("the keyboard user stays on the caret across the rebuild its click causes; a click from elsewhere moves no focus", () => {
  const { api, strip, document } = world();
  api.stagedMsgs.push(A, { text: "note", cites: [] });
  api.renderStagedStrip(A);
  const caret = strip.querySelector(".staged-caret")!;
  document.activeElement = caret;                  // reached by Tab
  caret.fire("click");
  const next = strip.querySelector(".staged-caret")!;
  assert.notEqual(next, caret, "the rebuild minted a new caret");
  assert.equal(document.activeElement, next, "and focus followed it");
  document.activeElement = null;                   // a pointer click, focus elsewhere
  next.fire("click");
  assert.equal(document.activeElement, null, "no focus taken");
});

test("flushStaged routes the posts stagedPosts composes, one routeUserMessage call each; what the kernel receives is the composed body", () => {
  const { H, api, strip } = world();
  const items = [{ text: "rename it", cites: [Q1] }, { text: "a bare note", cites: [] }];
  for (const it of items) api.stagedMsgs.push(A, it);
  api.renderStagedStrip(A);
  const typed = { text: "that is all", cites: [Q2], imgPaths: ["a.png"] };
  assert.equal(api.flushStaged(A, typed), 2, "two staged items went");
  assert.deepEqual(H.posted.filter(send), [{ type: "sendMessage", id: A, text: stagedRunBody(items, typed) }],
    "ONE message: the run in stage order, each quote with its comment, the typed message last");
  assert.deepEqual(H.optimistic, [{ sid: A, text: stagedRunBody(items, typed), imgPaths: ["a.png"] }], "one bubble, the typed images on it");
  assert.equal(H.posted.filter((m) => !send(m)).length, 1, "one breadcrumb per post");
  assert.equal(api.stagedMsgs.count(A), 0, "the release is one-shot");
  assert.equal(H.persists, 1);
  assert.equal(strip.style.display, "none", "the strip emptied with the stack");
  // nothing staged: the typed message as itself (here a goal follow-up), nothing persisted or repainted
  H.posted.length = 0; H.optimistic.length = 0;
  assert.equal(api.flushStaged(A, { text: "hi", cites: [G] }), 0);
  assert.deepEqual(H.posted.filter(send), [{ type: "askFollowUp", itemId: G.itemId, text: "hi", sid: A }]);
  assert.equal(H.persists, 1);
  // the two kinds of item that go alone, at their place: the frames leave in stage order
  H.posted.length = 0;
  const Gm = { text: "on the card", cites: [G] }, C = { text: "/compact", cites: [] }, Bn = { text: "why two?", cites: [Q2] };
  for (const it of [items[0], Gm, C, Bn]) api.stagedMsgs.push(A, it);
  api.flushStaged(A, { text: "done", cites: [] });
  assert.deepEqual(H.posted.filter(send), [
    { type: "sendMessage", id: A, text: stagedRunBody([items[0]]) },
    { type: "askFollowUp", itemId: G.itemId, text: "on the card", sid: A },
    { type: "sendMessage", id: A, text: "/compact" },
    { type: "sendMessage", id: A, text: stagedRunBody([Bn], { text: "done" }) },
  ]);
  // a command staged with a quote chip goes alone with its chip, and the chip wraps it as any quoted
  // message: the CLI reads that as prose, exactly as it did when every item was its own message
  H.posted.length = 0;
  api.stagedMsgs.push(A, { text: "/compact", cites: [Q1] });
  api.flushStaged(A);
  assert.deepEqual(H.posted.filter(send), [{ type: "sendMessage", id: A, text: quoteReplyBody([Q1], "/compact") }]);
});

test("Send now releases the stack alone, and a session that is not reachable keeps it, with a word to the user", () => {
  const { H, api, strip } = world();
  const items = [{ text: "rename it", cites: [Q1] }, { text: "a bare note", cites: [] }];
  for (const it of items) api.stagedMsgs.push(A, it);
  api.renderStagedStrip(A);
  H.down.add(A);
  strip.querySelector(".staged-go")!.fire("click");
  assert.equal(H.toasts.length, 1);
  assert.equal(api.stagedMsgs.count(A), 2, "still staged");
  assert.equal(H.posted.length, 0);
  H.down.delete(A); H.provisional.add(A);
  strip.querySelector(".staged-go")!.fire("click");
  assert.equal(H.toasts.length, 2, "a provisional tab is not reachable either");
  assert.equal(api.stagedMsgs.count(A), 2);
  H.provisional.delete(A);
  strip.querySelector(".staged-go")!.fire("click");
  assert.deepEqual(H.posted.filter(send), [{ type: "sendMessage", id: A, text: stagedRunBody(items) }], "the run alone, as one message");
  assert.equal(api.stagedMsgs.count(A), 0);
  assert.equal(strip.style.display, "none");
});

// What the fake DOM cannot see, pinned at the source.

test("CSS: the staged items live in .staged-list, capped at about four items (209px) with its own scroll; the cap's inputs are pinned", () => {
  assert.match(LIST, /max-height: 209px;/, "four items of 50px (a quote pill and a comment row each) plus three 3px gaps");
  assert.match(LIST, /overflow-y: auto;/, "the scroll is the list's own");
  assert.match(LIST, /overflow-x: hidden;/, "never sideways: the pane's rule for every scroll container");
  assert.match(LIST, /overscroll-behavior: contain;/, "a wheel at the end never scrolls the page");
  assert.match(LIST, /display: flex; flex-direction: column; gap: 3px;/, "the chips keep the strip's column and gap");
  // the cap is on the LIST, never on the strip or the head: the head must stay visible above the scroll.
  // [^{}]* spans a selector list, so a rule whose selectors name the strip or the head anywhere trips it
  assert.doesNotMatch(CSS, /#composer-staged[^{}]*\{[^}]*max-height/);
  assert.doesNotMatch(CSS, /\.staged-head[^{}]*\{[^}]*max-height/);
  // the arithmetic's inputs, so a geometry change here fails loudly instead of quietly showing three or five
  // (staged-list-layout.test.ts measures the result where a browser is installed)
  assert.match(CSS, /\.staged-chip \{[^}]*border: 1px dashed[^}]*padding: 2px 6px; font-size: 12px;/);
  assert.match(CSS, /\.staged-chip \{[^}]*gap: 2px;/);
  assert.match(CSS, /\.composer-chip \{[^}]*padding: 2px 4px 2px 8px; font-size: 12px; line-height: 1\.4;/);
  assert.match(CSS, /\.composer-chip-x \{[^}]*font-size: 11px; line-height: 1; padding: 2px 4px;/, "the row's tallest control stays under the row's line");
  assert.match(CSS, /\.staged-expand \{[^}]*font-size: 0\.86em;/);
  assert.match(CSS, /html, body \{[^}]*line-height: 1\.6;/);
  assert.match(CSS, /#composer-staged \{ flex-direction: column; gap: 3px;/);
  assert.doesNotMatch(LIST, /:has\(/, "no cap lift: an expanded item scrolls inside the list");
});

test("CSS: an expanded label wraps an unbroken token inside the list; the caret and the label are click targets in the head's own dress", () => {
  // an expanded item grows inside the scroll, and a long unbroken token in it (a digest, a query string)
  // wraps instead of widening the list: the list's overflow-x is hidden, so unwrapped it was unreadable
  assert.match(CSS, /\.staged-chip\.open \.staged-row \.composer-chip-label \{ white-space: pre-wrap; overflow: visible; overflow-wrap: anywhere; \}/);
  assert.match(CSS, /\.staged-cite\.open \.composer-chip-label \{ white-space: pre-wrap; overflow: visible; overflow-wrap: anywhere; \}/);
  // the caret: no new font size or colour (the head's own, --dim like the label), and the chip ✕'s padding
  // so its hit target is that of the strip's other small control rather than the glyph's own width
  assert.match(CSS, /\.staged-head \.staged-caret \{[^}]*padding: 2px 4px; color: var\(--dim\); font: inherit;[^}]*cursor: pointer;/);
  assert.match(CSS, /\.staged-head \.staged-lbl \{ cursor: pointer; \}/);
});

test("the composer closure's call sites: deliver sends only through flushStaged with the typed message, the stage path is the one reveal, and nothing collapses a list on its own", () => {
  // deliver lives inside setupComposer's closure, which nothing lifts: the typed message rides the flush
  // as the run's last item, and no second send follows it
  assert.match(RENDER, /const cites = composerCitations\.get\(activeId\);\s*\n\s*flushStaged\(sid, \{ text, cites, imgPaths: attached\.filter\(\(p\) => previewKind\(p\) === "img"\) \}\);/);
  const deliverBody = RENDER.split("const deliver = () => {")[1].split("setComposerAskMode();")[0];
  assert.doesNotMatch(deliverBody, /routeUserMessage\(/, "deliver sends only through flushStaged");
  assert.equal((deliverBody.match(/flushStaged\(/g) || []).length, 1);
  // the empty send (nothing typed, nothing attached, something staged) releases the stack alone
  assert.match(RENDER, /if \(!typed && !\(composerFiles\.get\(activeId\) \|\| \[\]\)\.length && stagedMsgs\.count\(activeId\)\) \{[\s\S]*?flushStaged\(activeId\);\s*\n\s*return;/);
  // the STAGE path is the one caller with the reveal intent: the new item is the event that justifies the
  // move, and every other rebuild keeps the place (executed above)
  assert.match(RENDER, /stagedMsgs\.push\(activeId, \{ text: typed, cites: \(composerCitations\.get\(activeId\) \|\| \[\]\)\.slice\(\) \}\);[\s\S]*?renderStagedStrip\(activeId, \{ reveal: "last" \}\);/);
  assert.equal((RENDER.match(/renderStagedStrip\(\w+, \{ reveal/g) || []).length, 1, "only the stage path reveals");
  // the collapse has exactly three writers, all in the strip: the click's toggle (two) and the empty stack's clear
  assert.equal((RENDER.match(/stagedCollapsed\.(add|delete|clear)\(/g) || []).length, 3, "the renderer never collapses or expands a list on its own");
  // one routing loop over the module's post list; the composition and its exceptions are decided there,
  // and quoteReplyBody lives there too (one function for the send, the release and the chip preview)
  assert.match(RENDER, /import \{ StagedStack, quoteReplyBody, stagedPosts \} from "\.\/staged-messages";/);
  assert.match(FLUSH, /for \(const p of stagedPosts\(run, typed\)\) routeUserMessage\(sid, p\.text, p\.cites as Citation\[\] \| undefined, p\.imgPaths\);/);
  assert.equal((FLUSH.match(/routeUserMessage\(/g) || []).length, 1, "no send outside the post list");
  assert.doesNotMatch(FLUSH, /stagedRunBody|itemId|isSlashCommand/, "not re-derived here");
  assert.doesNotMatch(RENDER, /^function quoteReplyBody\(/m);
});
