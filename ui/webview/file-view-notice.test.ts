// The viewer's edit-mode notice (a degraded editor, a refused save), run FOR REAL: openFileView mounts
// through document.createElement against a copy of the small DOM stand-in fileview-chip.test.ts and
// github-link.test.ts use, extended with a parent/child tree so WHERE a node lands can be read back.
// The notice used to be prepended INSIDE .fileview-body, whose editor child is height: 100% of that same
// body: the body's content was the bar plus the whole body, so the editor's bottom rows were cut off by
// the bar's height and the body's own scroll carried the bar out of view. It is now a child of the card
// between the title bar and the body, where the card's column flex layout gives it its own row, and
// exitEdit removes it, since nothing swaps the card's children the way the body's used to be swapped.
// The card holds its notice by reference: a replaced viewer's exitEdit (its keydown handler outlives the
// replace) must not reach the live card's notice, so the last case runs Escape through every handler the
// document would run it through, oldest first.
import { test } from "node:test";
import * as assert from "node:assert/strict";

// ── a DOM stand-in with a tree: parentNode, insertBefore, prepend, remove, and getElementById over it ──
class El {
  id = ""; title = ""; hidden = false; type = ""; disabled = false; tabIndex = -1; innerHTML = "";
  href = ""; target = ""; rel = ""; spellcheck = true; value = "";
  style: Record<string, string> = {};
  dataset: Record<string, string> = {};
  parentNode: El | null = null;
  childNodes: Array<El | string> = [];
  private attrs = new Map<string, string>();
  private classes = new Set<string>();
  private listeners = new Map<string, Array<(ev: any) => void>>();
  classList = {
    add: (...c: string[]) => { for (const x of c) this.classes.add(x); },
    remove: (...c: string[]) => { for (const x of c) this.classes.delete(x); },
    toggle: (c: string, on?: boolean) => { if (on ?? !this.classes.has(c)) this.classes.add(c); else this.classes.delete(c); },
    contains: (c: string) => this.classes.has(c),
  };
  constructor(public tagName: string) {}
  get className(): string { return [...this.classes].join(" "); }
  set className(v: string) { this.classes = new Set(v.split(/\s+/).filter(Boolean)); }
  get textContent(): string { return this.childNodes.map((c) => (typeof c === "string" ? c : c.textContent)).join(""); }
  set textContent(v: string) { this.replaceChildren(...(v === "" ? [] : [v])); }
  /** Element children only, in order: the rows a layout would give this element. */
  get children(): El[] { return this.childNodes.filter((c): c is El => c instanceof El); }
  get nextSibling(): El | string | null {
    const p = this.parentNode;
    if (!p) return null;
    const i = p.childNodes.indexOf(this);
    return i >= 0 && i + 1 < p.childNodes.length ? p.childNodes[i + 1] : null;
  }
  get isConnected(): boolean { let n: El = this; while (n.parentNode) n = n.parentNode; return n === docBody; }
  private adopt(c: El | string): void { if (c instanceof El) { c.remove(); c.parentNode = this; } }
  appendChild<T extends El>(c: T): T { this.adopt(c); this.childNodes.push(c); return c; }
  prepend(...cs: Array<El | string>): void { for (const c of cs) this.adopt(c); this.childNodes.unshift(...cs); }
  insertBefore<T extends El>(c: T, ref: El | null): T {
    // the DOM throws NotFoundError here, so a wrong reference fails at the mount, not at a later read
    if (ref && !this.childNodes.includes(ref)) throw new Error("insertBefore: the reference node is not a child of this node");
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
  remove(): void {
    const p = this.parentNode;
    if (!p) return;
    const i = p.childNodes.indexOf(this);
    if (i >= 0) p.childNodes.splice(i, 1);
    this.parentNode = null;
  }
  contains(n: El): boolean { let x: El | null = n; while (x) { if (x === this) return true; x = x.parentNode; } return false; }
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
  focus(): void {}
  dispatch(type: string, ev: Record<string, unknown> = {}): void {
    for (const fn of [...(this.listeners.get(type) || [])]) fn({ type, target: this, preventDefault() {}, ...ev });
  }
  click(): void { this.dispatch("click"); }
}
const docBody = new El("body");
function findById(n: El, id: string): El | null {
  if (n.id === id) return n;
  for (const c of n.childNodes) if (c instanceof El) { const hit = findById(c, id); if (hit) return hit; }
  return null;
}
const docKeys: Array<(ev: any) => void> = [];   // the viewer's keydown handlers, one per open
const win: any = new EventTarget();              // window: a save's reply arrives as a message event
win.parent = win;
win.confirm = () => true;                       // the discard ask, when a dirty buffer is about to go
(globalThis as any).window = win;
(globalThis as any).document = {
  createElement: (tag: string) => new El(tag),
  createTextNode: (s: string) => s,
  getElementById: (id: string) => findById(docBody, id),
  querySelectorAll: () => [],                   // no bundle <script src>: the editor chunk cannot be derived
  addEventListener: (type: string, fn: (ev: any) => void) => { if (type === "keydown") docKeys.push(fn); },
  removeEventListener: (type: string, fn: (ev: any) => void) => { const i = docKeys.indexOf(fn); if (i >= 0) docKeys.splice(i, 1); },
  body: docBody,
};
const store = new Map<string, string>();
(globalThis as any).localStorage = {
  getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
  setItem: (k: string, v: string) => { store.set(k, String(v)); },
  removeItem: (k: string) => { store.delete(k); },
};
const FILE = "/tmp/notes-api/app.py";
const TEXT = "import os\nprint(os.getcwd())\n";
// The two fetches the flow makes: the file's bytes (text/plain, faithful UTF-8, an ns anchor, so Edit
// arms) and the consent read (/version says editing is already on, so no popup).
(globalThis as any).fetch = (url: string) => {
  if (url.startsWith("/version")) return Promise.resolve({ json: () => Promise.resolve({ fileEditing: true }) });
  const headers = new Map([["Content-Type", "text/plain; charset=utf-8"], ["X-Romp-Text-Utf8", "1"], ["X-Romp-Mtime-Ns", "1700000000000000000"]]);
  return Promise.resolve({ ok: true, status: 200, headers: { get: (k: string) => headers.get(k) ?? null }, text: () => Promise.resolve(TEXT) });
};
/** Let every pending promise chain run: the fetches settle and the editor chunk's rejection reaches its catch. */
const settle = async () => { for (let i = 0; i < 3; i++) await new Promise((r) => setImmediate(r)); };

const posted: any[] = [];
let bound: Promise<typeof import("./file-view")> | null = null;
function view(): Promise<typeof import("./file-view")> {
  if (!bound) bound = import("./file-view").then((fv) => { fv.initFileView((m) => posted.push(m)); return fv; });
  return bound;
}
const noteEl = () => findById(docBody, "fileview-save-err");
type Card = { wrap: El; card: El; bar: El; body: El; edit: El; save: El; cancel: El };
/** The card just opened, with its three edit-mode buttons held by reference (Save's label changes while a save is out). */
function cardUp(): Card {
  const wrap = docBody.children[docBody.children.length - 1];
  assert.equal(wrap.id, "romp-fileview");
  const card = wrap.children[0];
  assert.equal(card.className, "fileview");
  const bar = card.children[0];
  assert.equal(bar.className, "fileview-bar");
  const body = card.children[card.children.length - 1];
  assert.equal(body.className, "fileview-body");
  const acts = bar.children.find((c) => c.classList.contains("fileview-acts"))!;
  const btn = (label: string) => { const walk = (n: El): El | undefined => { for (const c of n.children) { if (c.tagName === "button" && (c.textContent === label || c.getAttribute("aria-label") === label)) return c; const d = walk(c); if (d) return d; } return undefined; }; const b = walk(acts); assert.ok(b, "the " + label + " button"); return b!; };   // T367: controls sit in groups, glyph buttons carry their word as aria-label
  return { wrap, card, bar, body, edit: btn("Edit"), save: btn("Save"), cancel: btn("Cancel") };
}
/** Open the file, let the text land, click Edit: the editor chunk has no bundle to load from, so the plain
 *  textarea mounts with the degraded-editor notice. Returns the card and the textarea. */
async function openInFallbackEditor(): Promise<Card & { ta: El }> {
  const fv = await view();
  fv.openFileView(FILE, null);
  await settle();
  const c = cardUp();
  assert.equal(c.edit.hidden, false, "Edit armed off the kernel's text/plain + ns verdict");
  c.edit.click();
  await settle();
  const ta = c.body.children.find((x) => x.className === "fileview-editor")!;
  assert.ok(ta, "the fallback textarea took the body");
  assert.equal(ta.value, TEXT);
  return { ...c, ta };
}
/** The notice as laid out: its own row of the card, directly above the body, and the body's rows untouched. */
function assertAboveBody(note: El | null, c: Card, bodyRows: string[]): El {
  assert.ok(note, "the notice is up");
  assert.equal(note!.className, "fileview-err");
  assert.ok(note!.parentNode === c.card, "a child of the card, never of the body (parent: ." + (note!.parentNode?.className ?? "none") + ")");
  assert.ok(note!.nextSibling === c.body, "directly above the body");
  assert.deepEqual(c.card.children.map((x) => x.className), ["fileview-bar", "fileview-err", "fileview-body"],
    "title bar, notice, body: one notice at a time, and nothing else in the card");
  assert.deepEqual(c.body.children.map((x) => x.className), bodyRows,
    "the body holds its content alone, so an editor's 100% height is the whole body");
  return note!;
}

test("a degraded-editor notice is a row of the card above the body, not the body's first child", async () => {
  const c = await openInFallbackEditor();
  const note = assertAboveBody(noteEl(), c, ["fileview-editor"]);
  assert.match(note.textContent, /no bundle script tag/, "the reason, in the chunk loader's words");
  assert.match(note.textContent, /editing in the plain fallback editor\.$/, "and what the user is now editing in");
});

test("Cancel leaves edit mode with the notice gone, and the read view has the body to itself", async () => {
  const c = await openInFallbackEditor();
  const note = assertAboveBody(noteEl(), c, ["fileview-editor"]);
  c.cancel.click();
  assert.equal(noteEl(), null, "the notice went with edit mode");
  assert.ok(note.parentNode === null, "detached, not merely hidden");
  assert.deepEqual(c.card.children.map((x) => x.className), ["fileview-bar", "fileview-body"]);
  assert.deepEqual(c.body.children.map((x) => x.className), ["fileview-code"], "the highlighted read view is back");
  assert.equal(c.edit.hidden, false); assert.equal(c.save.hidden, true); assert.equal(c.cancel.hidden, true);
});

test("Escape peels edit mode the same way: the notice goes with it", async () => {
  const c = await openInFallbackEditor();
  assertAboveBody(noteEl(), c, ["fileview-editor"]);
  // each open registers its own keydown handler; the newest is this viewer's
  docKeys[docKeys.length - 1]({ key: "Escape", preventDefault() {} });
  assert.equal(noteEl(), null, "the notice went with edit mode");
  assert.ok(c.wrap.isConnected, "Escape left the viewer itself up");
  assert.deepEqual(c.card.children.map((x) => x.className), ["fileview-bar", "fileview-body"]);
  assert.equal(c.edit.hidden, false);
});

test("a refused save's notice replaces the last, above the body, and the edited buffer survives beneath it", async () => {
  const c = await openInFallbackEditor();
  c.ta.value = TEXT + "print('more')\n";
  c.ta.dispatch("input");
  c.save.click();
  const ask = posted[posted.length - 1];
  assert.equal(ask.type, "saveFile");
  assert.equal(c.save.textContent, "Saving…", "acknowledged before the round-trip");
  win.dispatchEvent(new MessageEvent("message", { data: { type: "fileSaveFailed", reqId: ask.reqId, error: "app.py changed on disk since you opened it" } }));
  const note = assertAboveBody(noteEl(), c, ["fileview-editor"]);
  assert.match(note.textContent, /^app\.py changed on disk since you opened it/);
  assert.equal(c.ta.value, TEXT + "print('more')\n", "the buffer is untouched");
  assert.ok(c.body.children[0] === c.ta, "the same textarea, still the body's only row");
  const reload = note.children.find((x) => x.tagName === "button")!;
  assert.equal(reload.textContent, "Reload file", "a conflict carries a Reload button inside the notice");
  assert.equal(c.save.textContent, "Save", "and Save is re-armed");
  // a save that then lands leaves edit mode through exitEdit, and the notice goes with it
  c.save.click();
  const again = posted[posted.length - 1];
  assert.ok(again.reqId > ask.reqId, "a fresh ask, not a retry of the refused one");
  win.dispatchEvent(new MessageEvent("message", { data: { type: "fileSaved", reqId: again.reqId, mtimeNs: "1700000000000000001" } }));
  assert.equal(noteEl(), null, "a save that landed clears the notice");
  assert.deepEqual(c.card.children.map((x) => x.className), ["fileview-bar", "fileview-body"]);
  assert.deepEqual(c.body.children.map((x) => x.className), ["fileview-code"]);
  assert.equal(c.edit.hidden, false);
});

test("Reload file from a conflict re-opens a fresh card with no notice", async () => {
  const c = await openInFallbackEditor();
  c.ta.value = TEXT + "x\n";
  c.ta.dispatch("input");
  c.save.click();
  const ask = posted[posted.length - 1];
  win.dispatchEvent(new MessageEvent("message", { data: { type: "fileSaveFailed", reqId: ask.reqId, error: "app.py changed on disk since you opened it" } }));
  const note = assertAboveBody(noteEl(), c, ["fileview-editor"]);
  let asked = 0;
  win.confirm = () => { asked++; return true; };
  note.children.find((x) => x.tagName === "button")!.click();
  assert.equal(asked, 1, "the discard ask happened once, before the old card went");
  assert.equal(c.wrap.isConnected, false, "the old card, notice and all, is gone");
  assert.equal(noteEl(), null);
  await settle();
  const fresh = cardUp();
  assert.ok(fresh.wrap !== c.wrap, "a new card, not the old one re-dressed");
  assert.deepEqual(fresh.card.children.map((x) => x.className), ["fileview-bar", "fileview-body"]);
  assert.deepEqual(fresh.body.children.map((x) => x.className), ["fileview-code"], "the file as it is now, in read mode");
  win.confirm = () => true;
});

test("a replaced viewer's Escape handler leaves the live card's notice alone", async () => {
  // Every open registers a document keydown handler and only an Escape-close removes it, so after Reload
  // file (which re-opens fresh) the OLD viewer's handler still runs first on Escape, with editing still
  // true in its closure and dirty cleared by the Reload click: its exitEdit runs. Looked up by id, the
  // notice it removed was the NEW card's, while that card was still in edit mode over a dirty buffer, and
  // with it went the Reload button, the user's only way out of the conflict. A handler whose card is gone
  // now removes itself and does nothing, so the live card's handler alone answers the Escape.
  const from = docKeys.length;                   // the handlers this case registers, oldest first
  const a = await openInFallbackEditor();
  a.ta.value = TEXT + "a\n";
  a.ta.dispatch("input");
  a.save.click();
  const ask = posted[posted.length - 1];
  win.dispatchEvent(new MessageEvent("message", { data: { type: "fileSaveFailed", reqId: ask.reqId, error: "app.py changed on disk since you opened it" } }));
  const conflict = assertAboveBody(noteEl(), a, ["fileview-editor"]);
  conflict.children.find((x) => x.tagName === "button")!.click();   // Reload file: the discard ask says yes
  assert.equal(a.wrap.isConnected, false);
  await settle();
  const b = cardUp();
  assert.ok(b.wrap !== a.wrap);
  b.edit.click();
  await settle();
  const ta = b.body.children.find((x) => x.className === "fileview-editor")!;
  assertAboveBody(noteEl(), b, ["fileview-editor"]);
  ta.value = TEXT + "b\n";
  ta.dispatch("input");                          // dirty: Escape must ask, and the answer is no
  win.confirm = () => false;
  assert.equal(docKeys.length - from, 2, "two viewers were opened, so two handlers are registered");
  let prevented = 0;
  for (const fn of docKeys.slice(from)) fn({ key: "Escape", preventDefault() { prevented++; } });
  assert.equal(prevented, 1, "only the live card's handler claims the Escape");
  win.confirm = () => true;
  const note = assertAboveBody(noteEl(), b, ["fileview-editor"]);
  assert.match(note.textContent, /editing in the plain fallback editor\.$/, "the live card's own notice, still up");
  assert.ok(conflict.parentNode !== null, "the old viewer's handler, its card gone, did nothing and removed itself");
  assert.equal(ta.value, TEXT + "b\n", "the kept edits are still in the buffer");
  assert.equal(b.edit.hidden, true); assert.equal(b.cancel.hidden, false, "still in edit mode");
});
