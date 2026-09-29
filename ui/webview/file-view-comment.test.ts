// The file viewer's comment box, run FOR REAL: openFileView mounts over a copy of file-view-notice.test.ts's DOM
// stand-in, extended with a focus owner, a selection and a range, and the right-click menu, the box, its send and
// the kernel's answers are driven the way the browser drives them. Each box settles by its own createId, so two
// boxes answered out of order, a failure after the ack, a socket drop and a replace are all executed here rather
// than read off the source.
import { test } from "node:test";
import * as assert from "node:assert/strict";

// ── a DOM stand-in with a tree, a focus owner and ranges ──
class El {
  id = ""; title = ""; hidden = false; type = ""; disabled = false; tabIndex = -1; innerHTML = "";
  href = ""; target = ""; rel = ""; spellcheck = true; value = ""; placeholder = "";
  style: Record<string, string> = {};
  dataset: Record<string, string> = {};
  parentNode: El | null = null;
  childNodes: Array<El | string> = [];
  tagName: string;
  private attrs = new Map<string, string>();
  private classes = new Set<string>();
  private listeners = new Map<string, Array<(ev: any) => void>>();
  classList = {
    add: (...c: string[]) => { for (const x of c) this.classes.add(x); },
    remove: (...c: string[]) => { for (const x of c) this.classes.delete(x); },
    toggle: (c: string, on?: boolean) => { if (on ?? !this.classes.has(c)) this.classes.add(c); else this.classes.delete(c); },
    contains: (c: string) => this.classes.has(c),
  };
  constructor(tag: string) { this.tagName = tag.toUpperCase(); }
  get className(): string { return [...this.classes].join(" "); }
  set className(v: string) { this.classes = new Set(v.split(/\s+/).filter(Boolean)); }
  get textContent(): string { return this.childNodes.map((c) => (typeof c === "string" ? c : c.textContent)).join(""); }
  set textContent(v: string) { this.replaceChildren(...(v === "" ? [] : [v])); }
  get children(): El[] { return this.childNodes.filter((c): c is El => c instanceof El); }
  get isConnected(): boolean { let n: El = this; while (n.parentNode) n = n.parentNode; return n === docBody; }
  private adopt(c: El | string): void { if (c instanceof El) { c.remove(); c.parentNode = this; } }
  appendChild<T extends El>(c: T): T { this.adopt(c); this.childNodes.push(c); return c; }
  append(...cs: Array<El | string>): void { for (const c of cs) { this.adopt(c); this.childNodes.push(c); } }
  prepend(...cs: Array<El | string>): void { for (const c of cs) this.adopt(c); this.childNodes.unshift(...cs); }
  insertBefore<T extends El>(c: T, ref: El | null): T {
    this.adopt(c);
    const i = ref ? this.childNodes.indexOf(ref) : -1;
    if (i < 0) this.childNodes.push(c); else this.childNodes.splice(i, 0, c);
    return c;
  }
  replaceChildren(...cs: Array<El | string>): void {
    for (const c of this.childNodes) if (c instanceof El) c.parentNode = null;
    this.childNodes = [];
    for (const c of cs) this.adopt(c);
    this.childNodes = [...cs];
  }
  replaceWith(...cs: Array<El | string>): void {
    const p = this.parentNode;
    if (!p) return;
    for (const c of cs) if (c instanceof El) { c.remove(); c.parentNode = p; }
    p.childNodes.splice(p.childNodes.indexOf(this), 1, ...cs);
    this.parentNode = null;
  }
  remove(): void {
    const p = this.parentNode;
    if (!p) return;
    const i = p.childNodes.indexOf(this);
    if (i >= 0) p.childNodes.splice(i, 1);
    this.parentNode = null;
  }
  contains(n: unknown): boolean { let x = n instanceof El ? n : null; while (x) { if (x === this) return true; x = x.parentNode; } return false; }
  setAttribute(k: string, v: string): void { this.attrs.set(k, v); }
  removeAttribute(k: string): void { this.attrs.delete(k); }
  getAttribute(k: string): string | null { return this.attrs.get(k) ?? null; }
  hasAttribute(k: string): boolean { return this.attrs.has(k); }
  addEventListener(type: string, fn: (ev: any) => void): void {
    const l = this.listeners.get(type) || [];
    l.push(fn); this.listeners.set(type, l);
  }
  removeEventListener(type: string, fn: (ev: any) => void): void {
    this.listeners.set(type, (this.listeners.get(type) || []).filter((f) => f !== fn));
  }
  getBoundingClientRect() { return { left: 0, top: 0, width: 120, height: 60, right: 120, bottom: 60 }; }
  focus(): void { doc.activeElement = this; }
  closest(): null { return null; }
  dispatch(type: string, ev: Record<string, unknown> = {}): void {
    for (const fn of [...(this.listeners.get(type) || [])]) fn({ type, target: this, button: 0, preventDefault() {}, stopPropagation() {}, ...ev });
  }
  click(): void { this.dispatch("click"); }
}
const docBody = new El("body");
function walk(n: El, hit: (e: El) => boolean, out: El[] = []): El[] {
  for (const c of n.children) { if (hit(c)) out.push(c); walk(c, hit, out); }
  return out;
}
const byClass = (cls: string, root: El = docBody) => walk(root, (e) => e.classList.contains(cls));
const byId = (id: string) => walk(docBody, (e) => e.id === id)[0] ?? null;

/** A range over sibling nodes: surrounding it moves them into the wrapper and the range then holds the wrapper,
 *  as the DOM's does, so a range whose wrapper was taken back out holds nothing any more. */
class FakeRange {
  constructor(public nodes: El[] = []) {}
  cloneRange(): FakeRange { return new FakeRange([...this.nodes]); }
  setStartBefore(n: El): void { this.nodes = [n]; }
  setEndAfter(n: El): void { if (!this.nodes.includes(n)) this.nodes.push(n); }
  surroundContents(wrap: El): void {
    const first = this.nodes[0];
    const p = first?.parentNode;
    if (!p || this.nodes.some((n) => n.parentNode !== p)) throw new Error("InvalidStateError: the range is collapsed or crosses nodes");
    p.insertBefore(wrap, first);
    for (const n of this.nodes) wrap.appendChild(n);
    this.nodes = [wrap];
  }
}
let selection: any = null;
const seeds: any[] = [];
const win: any = new EventTarget();
win.parent = win;
win.confirm = () => true;
win.innerWidth = 1200; win.innerHeight = 800;
win.getSelection = () => selection;
win.postMessage = (m: unknown) => { seeds.push(m); };
(globalThis as any).window = win;
const doc: any = {
  activeElement: null as El | null,
  createElement: (tag: string) => new El(tag),
  createTextNode: (s: string) => s,
  createRange: () => new FakeRange(),
  getElementById: (id: string) => byId(id),
  querySelectorAll: (sel: string) => (sel.startsWith(".") ? byClass(sel.slice(1)) : []),
  addEventListener: () => {},
  removeEventListener: () => {},
  hasFocus: () => true,
  body: docBody,
};
(globalThis as any).document = doc;
(globalThis as any).localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
const FILE = "/tmp/notes-api/app.py";
const TEXT = "import os\nprint(os.getcwd())\n";
(globalThis as any).fetch = (url: string) => {
  if (url.startsWith("/version")) return Promise.resolve({ json: () => Promise.resolve({ fileEditing: true }) });
  const headers = new Map([["Content-Type", "text/plain; charset=utf-8"], ["X-Romp-Text-Utf8", "1"]]);
  return Promise.resolve({ ok: true, status: 200, headers: { get: (k: string) => headers.get(k) ?? null }, text: () => Promise.resolve(TEXT) });
};
const settle = async () => { for (let i = 0; i < 3; i++) await new Promise((r) => setImmediate(r)); };

const SID = "5b1e7c2a-4d3f-4a6b-9c8d-0e1f2a3b4c5d";
const posted: any[] = [];
let bound: Promise<typeof import("./file-view")> | null = null;
function view(): Promise<typeof import("./file-view")> {
  if (!bound) bound = import("./file-view").then((fv) => { fv.initFileView((m) => posted.push(m)); return fv; });
  return bound;
}
const kernel = (data: Record<string, unknown>) => win.dispatchEvent(new MessageEvent("message", { data }));

type Passage = { host: El; text: El; words: string };
/** Open the file and put selectable passages into its body; the page starts with no box and no focus. */
async function openWith(...words: string[]): Promise<{ card: El; passages: Passage[] }> {
  const fv = await view();
  for (const n of byClass("fileview-cmt")) n.remove();
  doc.activeElement = null;
  fv.openFileView(FILE, SID);
  await settle();
  const card = byClass("fileview")[0];
  const body = byClass("fileview-body", card)[0];
  const passages = words.map((w) => {
    const host = new El("p"), text = new El("#t");
    text.textContent = w; host.appendChild(text); body.appendChild(host);
    return { host, text, words: w };
  });
  return { card, passages };
}
function select(p: Passage): void {
  selection = { isCollapsed: false, anchorNode: p.text, rangeCount: 1, toString: () => p.words, getRangeAt: () => new FakeRange([p.text]) };
}
type Box = { pop: El; ta: El; send: El; cancel: El; err: El };
function boxOf(pop: El): Box {
  const [send, cancel] = byClass("fileview-cmt-acts", pop)[0].children;
  return { pop, ta: byClass("fileview-cmt-input", pop)[0], send, cancel, err: byClass("fileview-cmt-err", pop)[0] };
}
/** Right-click the passage, pick the box's row from the shared menu, type, and send; returns the box and its createId. */
function comment(card: El, p: Passage, words: string): Box & { createId: string } {
  select(p);
  card.dispatch("contextmenu", { clientX: 40, clientY: 40, button: 2 });
  const menu = byClass("fileview-ctx")[0];
  assert.ok(menu, "the shared menu is up");
  menu.children[0].click();
  const pops = byClass("fileview-cmt");
  const box = boxOf(pops[pops.length - 1]);
  box.ta.value = words;
  box.send.click();
  const frame = posted.filter((m) => m.type === "commentCreate").pop();
  return { ...box, createId: String(frame.createId) };
}
const marks = (card: El) => byClass("fileview-cmt-mark", card);
const count = (card: El) => { const c = byClass("fileview-cmt-count", card)[0]; return c.hidden ? "" : c.textContent; };
const ack = (createId: string) => kernel({ type: "commentCreated", id: SID, tid: "t-" + createId, uuid: "", createId });
const refuse = (createId: string, text: string) => kernel({ type: "commentCreateFailed", id: SID, uuid: "", createId, text });

test("two boxes answered out of order each settle by their own createId", async () => {
  const { card, passages: [pa, pb] } = await openWith("first passage", "second passage");
  const a = comment(card, pa, "about the first");
  const b = comment(card, pb, "about the second");
  assert.notEqual(a.createId, b.createId);
  ack(b.createId);
  assert.equal(b.send.textContent, "Sent");
  assert.equal(a.send.textContent, "Sending…", "the first box still waits for its own answer");
  refuse(a.createId, "a bad name");
  assert.equal(a.send.disabled, false, "the refused box is re-armed");
  assert.equal(a.ta.value, "about the first", "with its draft");
  assert.equal(a.err.textContent, "a bad name");
  assert.deepEqual(marks(card).map((m) => m.textContent), ["second passage"], "only the landed passage is marked");
  assert.equal(count(card), "1 comment");
  ack("an-id-nobody-sent");
  assert.equal(count(card), "1 comment", "an unknown createId changes nothing");
});

test("a refusal for one box leaves the caret in the box being typed in; a lone refused box takes it", async () => {
  const { card, passages: [pa, pb] } = await openWith("first passage", "second passage");
  const a = comment(card, pa, "about the first");
  select(pb);
  card.dispatch("contextmenu", { clientX: 40, clientY: 40, button: 2 });
  byClass("fileview-ctx")[0].children[0].click();
  const pops = byClass("fileview-cmt");
  const b = boxOf(pops[pops.length - 1]);
  b.ta.focus();
  refuse(a.createId, "a bad name");
  assert.equal(doc.activeElement, b.ta, "the caret stays where it is being typed");
  win.dispatchEvent(new Event("romp:wsdown"));   // nothing else is waiting: no box moves the caret
  assert.equal(doc.activeElement, b.ta);
  const { card: c2, passages: [p2] } = await openWith("lone passage");
  const lone = comment(c2, p2, "alone");
  doc.activeElement = null;
  refuse(lone.createId, "a bad name");
  assert.equal(doc.activeElement, lone.ta, "nothing else is being typed in, so the refused box takes the caret");
});

test("a failure after the ack takes the mark and the count back, reopens the words without the caret, and a retry marks again", async () => {
  const { card, passages: [p] } = await openWith("the passage");
  const a = comment(card, p, "my words");
  ack(a.createId);
  assert.equal(count(card), "1 comment");
  assert.equal(marks(card).length, 1);
  doc.activeElement = null;
  refuse(a.createId, "spawn refused");
  assert.equal(count(card), "", "the count goes back");
  assert.equal(marks(card).length, 0, "the mark goes back");
  const pops = byClass("fileview-cmt");
  const again = boxOf(pops[pops.length - 1]);
  assert.equal(again.ta.value, "my words");
  assert.match(again.err.textContent, /the thread did not start, so it was removed: spawn refused/);
  assert.equal(doc.activeElement, null, "a late verdict takes no focus");
  again.send.click();
  const retry = String(posted.filter((m) => m.type === "commentCreate").pop().createId);
  ack(retry);
  assert.deepEqual(marks(card).map((m) => m.textContent), ["the passage"], "the retry marks the passage again");
  assert.equal(count(card), "1 comment");
});

test("a socket drop fails every box still waiting on the host", async () => {
  const { card, passages: [pa, pb] } = await openWith("first passage", "second passage");
  const a = comment(card, pa, "one");
  const b = comment(card, pb, "two");
  win.dispatchEvent(new Event("romp:wsdown"));
  for (const box of [a, b]) {
    assert.equal(box.send.disabled, false);
    assert.match(box.err.textContent, /the connection dropped/);
  }
});

test("a box closed mid-send reopens with its words on a refusal", async () => {
  const { card, passages: [p] } = await openWith("the passage");
  const a = comment(card, p, "keep me");
  a.cancel.click();
  assert.equal(byClass("fileview-cmt").length, 0);
  refuse(a.createId, "a bad name");
  const pops = byClass("fileview-cmt");
  assert.equal(pops.length, 1);
  assert.equal(boxOf(pops[0]).ta.value, "keep me");
});

test("a replace drops the old viewer's boxes and their hooks: a late answer for them paints nothing", async () => {
  const { card, passages: [p] } = await openWith("the passage");
  const a = comment(card, p, "old viewer");
  const { card: fresh } = await openWith();
  assert.equal(byClass("fileview-cmt").length, 0, "the old box went with the old viewer");
  refuse(a.createId, "a bad name");
  assert.equal(byClass("fileview-cmt").length, 0, "no box reopens over the new file");
  ack(a.createId);
  assert.equal(count(fresh), "");
});

test("a Mac Ctrl+click's release seeds nothing; a Ctrl release off a Mac still seeds the quote chip", async () => {
  const input = new El("textarea"); input.id = "composer-input"; docBody.appendChild(input);
  try {
    const { card, passages: [p] } = await openWith("the passage");
    select(p);
    seeds.length = 0;
    card.dispatch("mousedown", { ctrlKey: true });
    card.dispatch("contextmenu", { clientX: 40, clientY: 40, button: 0, ctrlKey: true });
    card.dispatch("mouseup", { button: 0, ctrlKey: true });
    await settle();
    assert.equal(seeds.length, 0, "the release that follows the menu re-seeds nothing");
    card.dispatch("mousedown", { ctrlKey: true });
    card.dispatch("mouseup", { button: 0, ctrlKey: true });
    await settle();
    assert.equal(seeds.filter((m) => m.type === "editorSelection").length, 1, "a Ctrl-drag release seeds");
    card.dispatch("mouseup", { button: 2 });
    await settle();
    assert.equal(seeds.length, 1, "a right button's release seeds nothing");
  } finally { input.remove(); }
});

test("in a narrow pane the box opens inside the viewport", async () => {
  win.innerWidth = 300;
  try {
    const { card, passages: [p] } = await openWith("the passage");
    const a = comment(card, p, "narrow");
    const left = parseFloat(a.pop.style.left);
    assert.ok(left >= 0, "the left edge stays on screen (left " + left + ")");
  } finally { win.innerWidth = 1200; }
});
