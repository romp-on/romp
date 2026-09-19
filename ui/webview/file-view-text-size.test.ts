// The viewer's text size, and a rendered-markdown layout that follows the viewer's width. A− and A+ in the title
// bar step every text view through a fixed table of sizes (70 to 200 percent): a markdown file's Rendered and Raw
// views, every other text file's code view, the SVG Source view, and a document opened from a link on the
// dashboard's own address. The percentage between them, shown once the size is off the default, is the reset;
// Ctrl/Cmd + wheel over the body steps the same table. The chosen step rides the viewer root as data-fv-text and
// the sheets turn it into the one property every text size reads (--fv-scale); it persists per browser like the
// Rendered/Raw choice. Run FOR REAL over openFileView and openUrlView against a DOM stand-in (a tree, attributes
// and dataset, bubbling events, a small selector matcher, localStorage, a fetch answering the kernel's headers),
// plus pins over both sheets and an optional browser leg (headless Chromium through a child driver, skipped where
// no browser is installed; CI installs none). Synthetic fixtures only.
import { test, type TestContext } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { marked } from "marked";   // the browser leg renders a synthetic task list through the real marked for mdBlock's task stamp

const web = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const VIEW = web("file-view.ts");
const SHEETS: ReadonlyArray<readonly [string, string]> = [["styles.css", web("styles.css")], ["feed.css", web("feed.css")]];

// ── a DOM stand-in: the members the viewer touches, with a tree and bubbling events ─────────────────
class Ev {
  target: El | null = null;
  currentTarget: El | null = null;
  defaultPrevented = false;
  stopped = false;
  ctrlKey: boolean; metaKey: boolean; deltaY: number; deltaMode: number; key: string;
  constructor(public type: string, init: { ctrlKey?: boolean; metaKey?: boolean; deltaY?: number; deltaMode?: number; key?: string } = {}) {
    this.ctrlKey = !!init.ctrlKey; this.metaKey = !!init.metaKey;
    this.deltaY = init.deltaY ?? 0; this.deltaMode = init.deltaMode ?? 0; this.key = init.key ?? "";
  }
  preventDefault(): void { this.defaultPrevented = true; }
  stopPropagation(): void { this.stopped = true; }
}
type Listener = (ev: Ev) => void;
type Part = { tag: string; id: string; classes: string[]; attrs: Array<[string, string | null]>; known: boolean };
const kebab = (k: string) => k.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());
/** One compound selector (`tag#id.class[attr="v"]`); a shape the matcher does not know fits nothing. */
function part(s: string): Part {
  const m = /^([a-zA-Z][\w-]*|\*)?(#[\w-]+)?((?:\.[\w-]+)*)((?:\[[^\]]+\])*)$/.exec(s);
  if (!m) return { tag: "", id: "", classes: [], attrs: [], known: false };
  const attrs: Array<[string, string | null]> = [];
  let known = true;
  for (const a of m[4].match(/\[[^\]]+\]/g) || []) {
    const am = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(a);
    if (am) attrs.push([am[1], am[2] ?? null]); else known = false;
  }
  return { tag: (m[1] || "").toLowerCase(), id: m[2] ? m[2].slice(1) : "", classes: (m[3].match(/\.[\w-]+/g) || []).map((c) => c.slice(1)), attrs, known };
}
class El {
  parentNode: El | null = null;
  childNodes: Array<El | string> = [];
  title = ""; hidden = false; type = ""; disabled = false; tabIndex = -1; innerHTML = "";
  href = ""; target = ""; rel = ""; spellcheck = true; value = ""; src = ""; alt = ""; download = "";
  style: Record<string, string> = {};
  onclick: ((ev: Ev) => void) | null = null;
  private attrs = new Map<string, string>();
  private listeners: Array<{ type: string; fn: Listener; once: boolean; passive: boolean | undefined }> = [];
  constructor(public tagName: string) {}
  get id(): string { return this.attrs.get("id") ?? ""; }
  set id(v: string) { this.attrs.set("id", v); }
  get className(): string { return this.attrs.get("class") ?? ""; }
  set className(v: string) { this.attrs.set("class", v.trim()); }
  private get classes(): string[] { return this.className.split(/\s+/).filter(Boolean); }
  classList = {
    add: (...c: string[]) => { const s = new Set(this.classes); for (const x of c) s.add(x); this.className = [...s].join(" "); },
    remove: (...c: string[]) => { const s = new Set(this.classes); for (const x of c) s.delete(x); this.className = [...s].join(" "); },
    toggle: (c: string, on?: boolean): boolean => { const want = on ?? !this.classes.includes(c); if (want) this.classList.add(c); else this.classList.remove(c); return want; },
    contains: (c: string) => this.classes.includes(c),
  };
  /** data-* through dataset, the way the viewer writes its root attribute (camelCase to kebab-case, as the DOM does). */
  dataset: Record<string, string | undefined> = new Proxy({} as Record<string, string | undefined>, {
    get: (_, k) => this.attrs.get("data-" + kebab(String(k))),
    set: (_, k, v) => { this.attrs.set("data-" + kebab(String(k)), String(v)); return true; },
    has: (_, k) => this.attrs.has("data-" + kebab(String(k))),
    deleteProperty: (_, k) => { this.attrs.delete("data-" + kebab(String(k))); return true; },
  });
  get textContent(): string { return this.childNodes.map((c) => (typeof c === "string" ? c : c.textContent)).join(""); }
  set textContent(v: string) { this.replaceChildren(...(v === "" ? [] : [v])); }
  /** Element children only, in order. */
  get children(): El[] { return this.childNodes.filter((c): c is El => c instanceof El); }
  get isConnected(): boolean { let n: El = this; while (n.parentNode) n = n.parentNode; return n === docBody; }
  private adopt(c: El | string): void { if (c instanceof El) { c.remove(); c.parentNode = this; } }
  appendChild<T extends El>(c: T): T { this.adopt(c); this.childNodes.push(c); return c; }
  prepend(...cs: Array<El | string>): void { for (const c of cs) this.adopt(c); this.childNodes.unshift(...cs); }
  insertBefore<T extends El>(c: T, ref: El | null): T {
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
  contains(n: El | null): boolean { for (let x: El | null = n; x; x = x.parentNode) if (x === this) return true; return false; }
  setAttribute(k: string, v: string): void { this.attrs.set(k, v); }
  removeAttribute(k: string): void { this.attrs.delete(k); }
  getAttribute(k: string): string | null { return this.attrs.get(k) ?? null; }
  hasAttribute(k: string): boolean { return this.attrs.has(k); }
  addEventListener(type: string, fn: Listener, opts?: boolean | { once?: boolean; passive?: boolean; capture?: boolean }): void {
    this.listeners.push({ type, fn, once: typeof opts === "object" && !!opts.once, passive: typeof opts === "object" ? opts.passive : undefined });
  }
  /** The `passive` option each listener of `type` on this element was registered with (undefined where none was given). */
  passiveOf(type: string): Array<boolean | undefined> { return this.listeners.filter((l) => l.type === type).map((l) => l.passive); }
  removeEventListener(type: string, fn: Listener): void { this.listeners = this.listeners.filter((l) => !(l.type === type && l.fn === fn)); }
  /** Bubble `ev` from this element to the root: each element's listeners in registration order, then its onclick for a click. */
  dispatchEvent(ev: Ev): boolean {
    ev.target = this;
    for (let n: El | null = this; n && !ev.stopped; n = n.parentNode) {
      ev.currentTarget = n;
      for (const l of n.listeners.slice()) {
        if (l.type !== ev.type) continue;
        if (l.once) n.listeners = n.listeners.filter((x) => x !== l);
        l.fn.call(n, ev);
      }
      if (ev.type === "click" && n.onclick) n.onclick(ev);
    }
    return !ev.defaultPrevented;
  }
  click(): void { this.dispatchEvent(new Ev("click")); }
  focus(): void { doc.activeElement = this; }
  scrollIntoView(): void { /* inert */ }
  private fits(p: Part): boolean {
    if (!p.known) return false;
    if (p.tag && p.tag !== "*" && p.tag !== this.tagName.toLowerCase()) return false;
    if (p.id && p.id !== this.id) return false;
    if (!p.classes.every((c) => this.classes.includes(c))) return false;
    return p.attrs.every(([a, v]) => this.attrs.has(a) && (v === null || this.attrs.get(a) === v));
  }
  /** Comma groups of descendant chains, each link a compound. */
  matches(sel: string): boolean {
    return sel.split(",").some((group) => {
      const chain = group.trim().split(/\s+/).filter(Boolean).map(part);
      if (!chain.length || !this.fits(chain[chain.length - 1])) return false;
      let k = chain.length - 2;
      for (let a = this.parentNode; a && k >= 0; a = a.parentNode) if (a.fits(chain[k])) k--;
      return k < 0;
    });
  }
  closest(sel: string): El | null { for (let n: El | null = this; n; n = n.parentNode) if (n.matches(sel)) return n; return null; }
  querySelectorAll(sel: string): El[] {
    const out: El[] = [];
    const walk = (n: El) => { for (const c of n.children) { if (c.matches(sel)) out.push(c); walk(c); } };
    walk(this);
    return out;
  }
  querySelector(sel: string): El | null { return this.querySelectorAll(sel)[0] ?? null; }
}
const docBody = new El("body");
const docKeys: Listener[] = [];                  // the viewer's document keydown handlers, one per open
const doc = {
  body: docBody,
  activeElement: null as El | null,
  createElement: (tag: string) => new El(tag),
  createTextNode: (s: string) => s,
  getElementById: (id: string): El | null => docBody.querySelector("#" + id),
  querySelectorAll: (sel: string): El[] => docBody.querySelectorAll(sel),   // no bundle <script src>: the editor chunk cannot load
  addEventListener: (type: string, fn: Listener) => { if (type === "keydown") docKeys.push(fn); },
  removeEventListener: (type: string, fn: Listener) => { const i = docKeys.indexOf(fn); if (i >= 0) docKeys.splice(i, 1); },
};
const win: any = new EventTarget();
win.parent = win;
win.confirm = () => true;                       // the discard ask, when a dirty buffer is about to go
win.getSelection = () => null;
const posted: unknown[] = [];                   // what the viewer posts to its own window: the quote seed
win.postMessage = (m: unknown) => { posted.push(m); };
(globalThis as any).window = win;
(globalThis as any).document = doc;
const store = new Map<string, string>();
const writes: Array<[string, string]> = [];     // every localStorage write, so a no-op press is shown to store nothing
(globalThis as any).localStorage = {
  getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
  setItem: (k: string, v: string) => { store.set(k, String(v)); writes.push([k, String(v)]); },
  removeItem: (k: string) => { store.delete(k); },
};

// ── fixtures: a notes-api world, synthetic throughout ──────────────────────────────────────────────
const SID = "77777777-8888-9999-aaaa-bbbbbbbbbbbb";
const ROOT = "/tmp/notes-api";
const REPORT = ROOT + "/docs/report.md";
const APP = ROOT + "/src/app.py";
const PLOT = ROOT + "/docs/plot.png";
const PAPER = ROOT + "/docs/paper.pdf";
const FIG = ROOT + "/docs/fig.svg";
const DOC = "# Report\n\n## Findings\nThe api session cut p95 latency by 40%.\n\n| run | p95 |\n| --- | --- |\n| a | 120 |\n";
const PY = "def main():\n    return 0\n";
const SVG_XML = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>';
const HREF = "http://notes-api.test/reports/run-1/evidence.md";
const URL_DOC = "# Evidence\n\nThe web session's report.\n";
const MT = "1700000000000000000";
const SIZE_KEY = "romp:fileviewTextSize";
type Served = { bytes: string | Uint8Array; type: string };
const disk: Record<string, Served> = {
  [REPORT]: { bytes: DOC, type: "text/plain; charset=utf-8" },
  [APP]: { bytes: PY, type: "text/plain; charset=utf-8" },
  [PLOT]: { bytes: new Uint8Array([0x89, 0x50, 0x4e, 0x47]), type: "image/png" },
  [PAPER]: { bytes: new Uint8Array([0x25, 0x50, 0x44, 0x46]), type: "application/pdf" },
  [FIG]: { bytes: SVG_XML, type: "image/svg+xml" },
};
const fileReads: string[] = [];                 // every /file fetch by path: the quote seed's fresh read shows up here
// The fetches the viewer makes: the kernel's /file (Content-Type is its verdict: text, a picture, a PDF, an SVG),
// its /version (editing already allowed, so no consent popup), and a same-origin document for the URL viewer,
// answered as a real Response so the streamed, capped read runs as it does in a browser.
(globalThis as any).fetch = (url: string) => {
  if (url.startsWith("/version")) return Promise.resolve({ json: () => Promise.resolve({ fileEditing: true }) });
  if (/^https?:/.test(url)) return Promise.resolve(new Response(URL_DOC, { status: 200, headers: { "Content-Type": "text/markdown; charset=utf-8" } }));
  const p = decodeURIComponent((/[?&]path=([^&]*)/.exec(url) || [])[1] || "");
  fileReads.push(p);
  const f = disk[p];
  const headers = { get: (h: string) => (f ? (h === "Content-Type" ? f.type : h === "X-Romp-Mtime-Ns" ? MT : h === "X-Romp-Text-Utf8" ? "1" : null) : null) };
  if (!f) return Promise.resolve({ ok: false, status: 404, headers, text: () => Promise.resolve("no such file: " + p) });
  return Promise.resolve({
    ok: true, status: 200, headers,
    text: () => Promise.resolve(String(f.bytes)),
    blob: () => Promise.resolve(new Blob([f.bytes as unknown as BlobPart], { type: f.type })),
  });
};
/** Let every pending promise chain run: the fetch settles, a blob decodes, the editor chunk's rejection reaches its catch. */
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise<void>((r) => setImmediate(r)); };

let bound: Promise<typeof import("./file-view")> | null = null;
function view(): Promise<typeof import("./file-view")> {
  if (!bound) bound = import("./file-view").then((fv) => { fv.initFileView(() => { /* the WS poster: nothing asks the kernel here */ }); return fv; });
  return bound;
}
type Card = { fv: typeof import("./file-view"); wrap: El; root: El; bar: El; acts: El; body: El; down: El; reset: El; up: El; zoom: El; btn: (label: string) => El };   // zoom: the glyph that shows or hides the control (T367)
/** The card up now, with the control's three buttons held by reference (they are built once per open). */
function card(fv: typeof import("./file-view")): Card {
  const wrap = doc.getElementById("romp-fileview");
  assert.ok(wrap, "a viewer is up");
  const root = wrap!.children[0];
  assert.equal(root.className, "fileview");
  const bar = root.children[0];
  assert.equal(bar.className, "fileview-bar");
  const body = root.children[root.children.length - 1];
  assert.equal(body.className, "fileview-body");
  const acts = bar.children.find((c) => c.classList.contains("fileview-acts"))!;
  const btn = (label: string) => { const walk = (n: El): El | undefined => { for (const c of n.children) { if (c.tagName === "button" && (c.textContent === label || c.getAttribute("aria-label") === label)) return c; const d = walk(c); if (d) return d; } return undefined; }; const b = walk(acts); assert.ok(b, "the " + label + " button"); return b!; };   // T367: controls sit in groups, glyph buttons carry their word as aria-label
  const reset = acts.querySelector(".fileview-size-reset");   // inside the zoom flyout since T367
  assert.ok(reset, "the readout between A− and A+ (the reset)");
  return { fv, wrap: wrap!, root, bar, acts, body, down: btn("A−"), reset: reset!, up: btn("A+"), zoom: acts.querySelector(".fileview-zoom-btn")!, btn };
}
/** Open `p` for the fixture session; `wait` lets the bytes land. The stored size is the caller's, set before the call. */
async function openFile(t: TestContext, p: string, wait = true): Promise<Card> {
  const fv = await view();
  fv.openFileView(p, SID);
  t.after(() => { fv.closeFileView(); store.clear(); });
  if (wait) await settle();
  return card(fv);
}
const size = (o: Card): string | null => o.root.getAttribute("data-fv-text");
/** The readout's slot is empty at the default (the sheet hides this class by visibility): nothing to reset, nothing said. */
const blank = (o: Card): boolean => o.reset.classList.contains("fileview-size-default");
/** An end of the table: aria-disabled (dimmed by the sheet, focus kept), never the disabled property. */
const atEnd = (b: El): boolean => b.getAttribute("aria-disabled") === "true";
const wheel = (init: { dy: number; ctrl?: boolean; meta?: boolean; mode?: number }): Ev =>
  new Ev("wheel", { ctrlKey: !!init.ctrl, metaKey: !!init.meta, deltaY: init.dy, deltaMode: init.mode ?? 0 });
const labels = (acts: El): string[] => acts.children.map((c) => c.textContent);

// ── the pure table ─────────────────────────────────────────────────────────────────────────────────

test("TEXT_SIZES is a bounded, ascending table of percentages holding the default; stepTextSize walks it and clamps at both ends", async () => {
  const { TEXT_SIZES, TEXT_SIZE_DEFAULT, stepTextSize } = await view();
  assert.equal(TEXT_SIZES[0], 70); assert.equal(TEXT_SIZES[TEXT_SIZES.length - 1], 200);
  assert.equal(TEXT_SIZE_DEFAULT, 100); assert.ok(TEXT_SIZES.includes(100), "the default is a step of the table");
  for (let i = 1; i < TEXT_SIZES.length; i++) assert.ok(TEXT_SIZES[i] > TEXT_SIZES[i - 1], "ascending");
  assert.equal(stepTextSize(100, 1), 115); assert.equal(stepTextSize(100, -1), 90);
  assert.equal(stepTextSize(200, 1), 200, "the top clamps"); assert.equal(stepTextSize(70, -1), 70, "the bottom clamps");
  assert.equal(stepTextSize(175, 1), 200); assert.equal(stepTextSize(80, -1), 70);
  assert.equal(stepTextSize(123, 1), 115, "a value off the table steps from the default");
  assert.equal(stepTextSize(123, -1), 90);
  // every step is reachable from the default by presses, and the walk never leaves the table
  let at = 100; const seen = new Set<number>([at]);
  for (let i = 0; i < 20; i++) { at = stepTextSize(at, 1); seen.add(at); assert.ok(TEXT_SIZES.includes(at)); }
  for (let i = 0; i < 20; i++) { at = stepTextSize(at, -1); seen.add(at); assert.ok(TEXT_SIZES.includes(at)); }
  assert.equal(seen.size, TEXT_SIZES.length);
});

test("parseTextSize returns a stored step; absent, garbage, a size off the table or a multiplier read as the default", async () => {
  const { parseTextSize } = await view();
  assert.equal(parseTextSize("115"), 115); assert.equal(parseTextSize(" 200 "), 200); assert.equal(parseTextSize("70"), 70);
  assert.equal(parseTextSize(null), 100); assert.equal(parseTextSize(undefined), 100); assert.equal(parseTextSize(""), 100);
  assert.equal(parseTextSize("huge"), 100); assert.equal(parseTextSize('{"pct":115}'), 100);
  assert.equal(parseTextSize("110"), 100, "a size the table does not hold is not invented");
  assert.equal(parseTextSize("1.15"), 100, "a multiplier is not a percentage");
  assert.equal(parseTextSize("-100"), 100); assert.equal(parseTextSize("1e9"), 100);
});

test("foldWheel: a notch is one step, a pinch's small deltas add up to one, a reversal starts over, lines and pages are normalized, wheel-up is larger", async () => {
  const { foldWheel, WHEEL_STEP_PX } = await view();
  assert.deepEqual(foldWheel({ deltaY: -100, deltaMode: 0 }, 0), { acc: 0, dir: 1 }, "a notch up (Chrome, about 100 pixels): larger at once");
  assert.deepEqual(foldWheel({ deltaY: 100, deltaMode: 0 }, 0), { acc: 0, dir: -1 }, "a notch down: smaller");
  assert.deepEqual(foldWheel({ deltaY: -3, deltaMode: 1 }, 0), { acc: 0, dir: 1 }, "three lines (deltaMode 1) is a notch");
  assert.deepEqual(foldWheel({ deltaY: 1, deltaMode: 2 }, 0), { acc: 0, dir: -1 }, "a page (deltaMode 2) is more than a notch");
  // a pinch reports as a burst of ctrlKey wheel events a few pixels each: the third crosses the threshold and the sum clears
  let r = foldWheel({ deltaY: -15, deltaMode: 0 }, 0); assert.deepEqual(r, { acc: -15, dir: 0 });
  r = foldWheel({ deltaY: -15, deltaMode: 0 }, r.acc); assert.deepEqual(r, { acc: -30, dir: 0 });
  r = foldWheel({ deltaY: -15, deltaMode: 0 }, r.acc); assert.deepEqual(r, { acc: 0, dir: 1 });
  assert.ok(WHEEL_STEP_PX > 30 && WHEEL_STEP_PX <= 100, "a notch is at least one step; a pinch's single event is not");
  // a reversal does not pay off the other way's remainder first
  r = foldWheel({ deltaY: -30, deltaMode: 0 }, 0); assert.equal(r.acc, -30);
  r = foldWheel({ deltaY: 10, deltaMode: 0 }, r.acc); assert.deepEqual(r, { acc: 10, dir: 0 });
  assert.deepEqual(foldWheel({ deltaY: 0, deltaMode: 0 }, -20), { acc: -20, dir: 0 }, "a horizontal-only event changes nothing");
});

// ── the control over the real openFileView ─────────────────────────────────────────────────────────

test("a markdown file opens at the default: the zoom glyph after the format pair, its flyout holding A−, the readout (dimmed) and A+, the root at 100; each press steps, stores and acknowledges in the same tick; an end reads as reached and a press there changes nothing", async (t) => {
  const { TEXT_SIZES } = await view();
  const o = await openFile(t, REPORT);
  assert.equal(size(o), "100", "the root carries the step the sheets read");
  assert.equal(o.zoom.hidden, false);
  assert.equal(o.zoom.hidden, false, "the readout's slot is in the flyout from the start, so A− never moves when it fills");
  assert.equal(blank(o), true, "nothing to reset at the default: the readout reads dimmed (T367: inside the flyout, never an empty slot)");
  assert.equal(o.down.getAttribute("aria-label"), "Smaller text"); assert.equal(o.up.getAttribute("aria-label"), "Larger text");
  // T367: the row is GROUPS; the view group leads with the Rendered|Raw pair, then the zoom glyph whose flyout holds the three
  const viewGroup = o.acts.children[0];
  assert.ok(viewGroup.classList.contains("fileview-group-view"), "the view group leads the row: " + labels(o.acts).join(" | "));
  assert.ok(viewGroup.children[0].classList.contains("fileview-seg") && viewGroup.children[1].classList.contains("fileview-zoom"), "the zoom glyph follows the Rendered|Raw pair");
  const zoomBtn = viewGroup.children[1].children[0], menu = viewGroup.children[1].children[1];
  assert.equal(zoomBtn, o.zoom);
  assert.equal(zoomBtn.getAttribute("aria-label"), "Text size"); assert.equal(zoomBtn.title, "Text size 100% (Ctrl/Cmd + wheel)", "the hover says the size and the wheel binding");
  assert.equal(menu.className, "fileview-zoom-menu");
  assert.deepEqual(menu.children, [o.down, o.reset, o.up], "A−, the readout, A+ ride the flyout in that order");
  assert.ok(o.acts.children[1].classList.contains("fileview-group-file"), "the file group (edit, download, copy path) follows the view group");
  o.up.click();
  assert.equal(size(o), "115", "one step, synchronously");
  assert.equal(blank(o), false); assert.equal(o.reset.textContent, "115%", "the readout is the acknowledgement");
  assert.equal(o.zoom.title, "Text size 115% (Ctrl/Cmd + wheel)", "and the glyph's hover follows it");
  assert.equal(store.get(SIZE_KEY), "115", "stored as the percentage, under the viewer's own key");
  o.down.click(); o.down.click();
  assert.equal(size(o), "90"); assert.equal(o.reset.textContent, "90%"); assert.equal(store.get(SIZE_KEY), "90");
  for (let i = 0; i < 12; i++) o.up.click();
  assert.equal(size(o), "200", "clamped at the top however many presses");
  assert.equal(atEnd(o.up), true, "the top end reads as reached: aria-disabled, which the sheet dims"); assert.equal(atEnd(o.down), false);
  assert.equal(o.up.disabled, false, "never the disabled property: a button that disables under keyboard focus drops the focus");
  let n = writes.length;
  o.up.click();
  assert.equal(size(o), "200"); assert.equal(writes.length, n, "a press on the end changes nothing and stores nothing");
  for (let i = 0; i < 12; i++) o.down.click();
  assert.equal(size(o), "70"); assert.equal(atEnd(o.down), true); assert.equal(atEnd(o.up), false); assert.equal(o.down.disabled, false);
  n = writes.length;
  o.down.click();
  assert.equal(size(o), "70"); assert.equal(writes.length, n);
  assert.equal(store.get(SIZE_KEY), String(TEXT_SIZES[0]));
  assert.equal(o.down.className, "fileview-btn fileview-size", "wears the row's one button treatment");
  assert.ok(o.reset.classList.contains("fileview-btn"), "the readout is a button: the reset");
});

test("the readout resets to the default and empties; a reset at the default stores nothing", async (t) => {
  const o = await openFile(t, REPORT);
  o.up.click(); o.up.click();
  assert.equal(size(o), "130");
  o.reset.click();
  assert.equal(size(o), "100"); assert.equal(blank(o), true, "back at the default the slot empties"); assert.equal(store.get(SIZE_KEY), "100");
  assert.equal(atEnd(o.up), false); assert.equal(atEnd(o.down), false);
  const n = writes.length;
  o.reset.click();
  assert.equal(writes.length, n, "nothing changed, nothing stored");
});

test("persistence: the size survives a close and a fresh open, is on the root before the bytes land, and a foreign stored value opens at the default", async (t) => {
  const first = await openFile(t, REPORT);
  first.up.click(); first.up.click(); first.up.click();
  assert.equal(size(first), "150");
  first.fv.closeFileView();
  assert.equal(doc.getElementById("romp-fileview"), null);
  const again = await openFile(t, REPORT, false);                    // no settle: the loader still holds the body
  assert.equal(size(again), "150", "the stored step is on the root at open, before the fetch, so the first paint is at size");
  assert.equal(again.reset.textContent, "150%");
  assert.equal(again.zoom.hidden, true); assert.equal(again.zoom.hidden, true,
    "the control waits for the bytes: whether this is a text file is the kernel's verdict, in the fetch's headers");
  await settle();
  assert.equal(size(again), "150", "the paint keeps it");
  assert.equal(again.zoom.hidden, false); assert.equal(blank(again), false);
  again.fv.closeFileView();
  store.set(SIZE_KEY, "purple");
  const third = await openFile(t, REPORT);
  assert.equal(size(third), "100", "a corrupt entry costs the preference, never the viewer");
  assert.equal(blank(third), true);
  third.fv.closeFileView();
  store.set(SIZE_KEY, "80");
  const fourth = await openFile(t, APP);
  assert.equal(size(fourth), "80", "one size for every file this browser opens, a .py included");
  assert.equal(fourth.zoom.hidden, false, "a non-markdown text file has the control: its code view scales too");
  assert.equal(fourth.reset.textContent, "80%");
});

test("Ctrl/Cmd + wheel over the body steps the size and takes the gesture from the page zoom; a plain wheel scrolls; a pinch's deltas add up; the bar is not the text; a picture leaves the browser its zoom", async (t) => {
  const o = await openFile(t, REPORT);
  const md = o.body.querySelector(".fileview-md")!;
  assert.ok(md, "the rendered body");
  let ev = wheel({ dy: -100, ctrl: true });
  md.dispatchEvent(ev);
  assert.equal(size(o), "115", "a notch up with Ctrl: larger");
  assert.equal(ev.defaultPrevented, true, "the page zoom is prevented: the gesture is the viewer's here");
  ev = wheel({ dy: 100, meta: true });
  md.dispatchEvent(ev);
  assert.equal(size(o), "100", "Cmd works the same"); assert.equal(ev.defaultPrevented, true);
  ev = wheel({ dy: -100 });
  md.dispatchEvent(ev);
  assert.equal(size(o), "100", "no modifier: not the gesture"); assert.equal(ev.defaultPrevented, false, "and the wheel scrolls as ever");
  for (const dy of [-15, -15]) md.dispatchEvent(wheel({ dy, ctrl: true }));
  assert.equal(size(o), "100", "a pinch's first events add up");
  md.dispatchEvent(wheel({ dy: -15, ctrl: true }));
  assert.equal(size(o), "115", "and the third crosses the threshold");
  md.dispatchEvent(wheel({ dy: -3, ctrl: true, mode: 1 }));
  assert.equal(size(o), "130", "three lines is a notch");
  assert.equal(store.get(SIZE_KEY), "130", "the wheel stores like the buttons");
  assert.equal(o.reset.textContent, "130%", "and the readout follows");
  ev = wheel({ dy: -100, ctrl: true });
  o.acts.dispatchEvent(ev);
  assert.equal(size(o), "130", "a wheel over the action row is not the gesture"); assert.equal(ev.defaultPrevented, false);
  o.fv.closeFileView(); store.delete(SIZE_KEY);
  const pic = await openFile(t, PLOT);
  ev = wheel({ dy: -100, ctrl: true });
  pic.body.dispatchEvent(ev);
  assert.equal(size(pic), "100", "a picture: nothing there reads the property");
  assert.equal(ev.defaultPrevented, false, "not prevented: the page zoom stays the browser's");
});

test("click-safe: the three buttons are built once per open and never rebuilt by a paint; a Rendered/Raw flip and a step keep the same nodes", async (t) => {
  const o = await openFile(t, REPORT);
  const nodes = [o.down, o.reset, o.up];
  o.btn("Raw").click();
  assert.equal(o.body.children[0].className, "fileview-code", "the Raw paint");
  assert.deepEqual([o.btn("A−"), o.acts.querySelector(".fileview-size-reset"), o.btn("A+")], nodes, "the same elements after the Raw paint");
  o.up.click();
  assert.deepEqual([o.btn("A−"), o.acts.querySelector(".fileview-size-reset"), o.btn("A+")], nodes, "and after a step");
  assert.equal(size(o), "115", "the Raw view is scaled by the same property (.fileview-pre reads it)");
  o.btn("Rendered").click();
  assert.equal(size(o), "115", "the flip back keeps the size");
  // source: the control is declared before renderBody, outside it, with direct listeners (the format toggles' idiom)
  const at = (s: string) => { const i = VIEW.indexOf(s); assert.ok(i >= 0, s); return i; };
  const openFn = VIEW.split("export function openFileView")[1].split("function offersDownload")[0];
  assert.ok(openFn.indexOf("textSizeControl(") >= 0 && openFn.indexOf("textSizeControl(") < openFn.indexOf("const renderBody = () => {"), "built once per open, before the paint function");
  const paint = openFn.slice(openFn.indexOf("const renderBody = () => {"), openFn.indexOf("\n  };", openFn.indexOf("const renderBody = () => {")));
  assert.doesNotMatch(paint, /addEventListener/, "the paint function wires nothing: it only syncs the control's hidden state");
  assert.match(paint, /\.sync\(\);/, "which it does, every paint");
  assert.deepEqual(o.body.passiveOf("wheel"), [false], "the body's one wheel listener is non-passive, so the page zoom can be prevented");
  assert.doesNotMatch(VIEW, /e\.key === "\+"|e\.key === "-"|e\.key === "="/, "no keyboard zoom chord: Ctrl+plus/minus stay the browser's");
  assert.ok(at("function textSizeControl(") < at("export function openFileView"), "one builder, declared once, for both viewers");
});

test("the control is absent for a picture and a PDF, present for the SVG Source view, hidden in edit mode and back on exit", async (t) => {
  const pic = await openFile(t, PLOT);
  assert.equal(pic.zoom.hidden, true, "a picture has no text to size");   // the glyph is the control's visibility (T367)
  pic.fv.closeFileView();
  const pdf = await openFile(t, PAPER);
  assert.equal(pdf.zoom.hidden, true, "a PDF: the browser's viewer owns its text");   // the glyph is the control's visibility (T367)
  pdf.fv.closeFileView();
  const svg = await openFile(t, FIG);
  assert.equal(svg.zoom.hidden, true, "an SVG shown as a picture: no text yet");
  svg.btn("Source").click();
  await settle();
  assert.equal(svg.body.children[0].className, "fileview-code", "the Source view is up");
  assert.equal(svg.zoom.hidden, false, "the Source view is a text view and has the control");   // the glyph is the control's visibility (T367)
  const ev = wheel({ dy: -100, ctrl: true });
  svg.body.dispatchEvent(ev);
  assert.equal(size(svg), "115", "and the wheel steps it"); assert.equal(ev.defaultPrevented, true);
  svg.fv.closeFileView(); store.delete(SIZE_KEY);
  store.set(SIZE_KEY, "115");
  const o = await openFile(t, REPORT);
  assert.equal(o.zoom.hidden, false); assert.equal(blank(o), false, "off the default, the readout says the size");
  o.btn("Edit").click();
  await settle();
  assert.equal(o.btn("Cancel").hidden, false, "edit mode");
  assert.equal(o.acts.children[0].hidden, true, "the view group takes no room in edit mode: the pair and the glyph are hidden (review)");
  assert.equal(o.zoom.hidden, true, "the editor keeps its own size");   // the glyph is the control's visibility (T367)
  const ev2 = wheel({ dy: -100, ctrl: true });
  o.body.dispatchEvent(ev2);
  assert.equal(size(o), "115", "the wheel stands down in edit mode"); assert.equal(ev2.defaultPrevented, false);
  o.btn("Cancel").click();
  assert.equal(o.btn("Cancel").hidden, true);
  assert.equal(o.acts.children[0].hidden, false, "the view group is back with the read view");
  assert.equal(o.zoom.hidden, false);   // the glyph is the control's visibility (T367)
  assert.equal(blank(o), false, "back with the read view");
});

test("the view group holding the Source button is shown after an SVG's first paint and stays shown after Source on and after Source off; for a picture or a PDF it stays hidden", async (t) => {
  // Over a picture the Source button is the view group's one control (no format pair, the zoom glyph hidden), and
  // renderBody's media branch decides it AFTER the group's sync at the top of the paint, so the group is re-read
  // once the button is decided. The SVG's first-paint assertion on the group is the pin. The Source-on and
  // Source-off assertions held before the re-read too (on those paints the button, or the glyph, was already
  // shown when the top sync ran); they pin the two later paints. The PNG and the PDF pin the re-read's other
  // outcome: every control hidden, the group hidden with them, so the bar keeps no gap.
  const o = await openFile(t, FIG);
  const group = o.wrap.querySelector(".fileview-group-view");
  assert.ok(group, "the view group");
  assert.equal(o.body.children[0].className, "fileview-imgbox", "the first paint: the picture");
  assert.equal(o.btn("Source").hidden, false, "the Source button is shown once the SVG's bytes landed");
  assert.equal(group!.hidden, false, "after the first paint the group is shown: the Source button is its one control, decided by the media branch after the group's sync, so the group is re-read after it (before the re-read, the shown button sat in a hidden group and the Source view was unreachable)");
  o.btn("Source").click();
  await settle();
  assert.equal(o.body.children[0].className, "fileview-code", "Source on: the XML");
  assert.equal(group!.hidden, false, "Source on: still shown (a text view: the zoom glyph shows too)");
  o.btn("Source").click();
  await settle();
  assert.equal(o.body.children[0].className, "fileview-imgbox", "Source off: the picture again");
  assert.equal(group!.hidden, false, "Source off: still shown (the Source button is still its one control)");
  o.fv.closeFileView();
  const pic = await openFile(t, PLOT);
  assert.equal(pic.body.children[0].className, "fileview-imgbox", "a PNG: the picture");
  assert.equal(pic.btn("Source").hidden, true, "a PNG has no Source view");
  assert.equal(pic.wrap.querySelector(".fileview-group-view")!.hidden, true, "a picture: every view control hidden, the group with them");
  pic.fv.closeFileView();
  const pdf = await openFile(t, PAPER);
  assert.equal(pdf.body.children[0].className, "fileview-frame", "a PDF: the browser's viewer in a frame");
  assert.equal(pdf.btn("Source").hidden, true, "a PDF has no Source view");
  assert.equal(pdf.wrap.querySelector(".fileview-group-view")!.hidden, true, "a PDF: every view control hidden, the group with them");
});

test("the control shows only once a text body is KNOWN: hidden beside the loader, shown when a text file's bytes land, never for a picture", async (t) => {
  store.set(SIZE_KEY, "130");
  const o = await openFile(t, REPORT, false);
  assert.equal(o.zoom.hidden, true, "the loader holds the body: not yet a text view");   // the glyph is the control's visibility (T367)
  assert.equal(size(o), "130", "the step is on the root already, so the first paint is at size");
  await settle();
  assert.equal(o.zoom.hidden, false);   // the glyph is the control's visibility (T367)
  assert.equal(o.reset.textContent, "130%");
  o.fv.closeFileView();
  const pic = await openFile(t, PLOT, false);
  assert.equal(pic.zoom.hidden, true, "a picture: hidden for the load");
  await settle();
  assert.equal(pic.body.children[0].className, "fileview-imgbox", "the picture landed");
  assert.equal(pic.zoom.hidden, true, "and after it: nothing there reads the property");   // the glyph is the control's visibility (T367)
});

test("a press on a bar control settles no selection: with a passage selected in the body, A+ steps the size and neither re-reads the file nor re-seeds the quote chip; a lift on the bar's path or padding still settles", async (t) => {
  const composer = new El("textarea");             // this document holds the composer, so a selection seeds its chip
  composer.id = "composer-input";
  docBody.appendChild(composer);
  t.after(() => { composer.remove(); win.getSelection = () => null; });
  const o = await openFile(t, REPORT);
  const md = o.body.querySelector(".fileview-md")!;
  win.getSelection = () => ({ isCollapsed: false, anchorNode: md, toString: () => "cut p95 latency" });
  const reads = () => fileReads.filter((p) => p === REPORT).length;
  const seeds = () => posted.filter((m) => (m as { type?: string }).type === "editorSelection").length;
  let r = reads(), s = seeds();
  md.dispatchEvent(new Ev("mouseup"));
  await settle();
  assert.equal(reads(), r + 1, "a lift over the body settles the selection: the label's line is minted against a fresh read");
  assert.equal(seeds(), s + 1, "and the quote chip is seeded");
  r = reads(); s = seeds();
  o.up.dispatchEvent(new Ev("mouseup")); o.up.click();
  await settle();
  assert.equal(size(o), "115", "the step happened");
  assert.equal(reads(), r, "and the press on A+ read nothing"); assert.equal(seeds(), s, "and seeded nothing");
  o.reset.dispatchEvent(new Ev("mouseup")); o.reset.click();
  await settle();
  assert.equal(size(o), "100"); assert.equal(reads(), r); assert.equal(seeds(), s);
  o.btn("Raw").dispatchEvent(new Ev("mouseup"));
  await settle();
  assert.equal(reads(), r, "every button in the bar: the format toggles too");
  // the overshoot: a drag that starts in the body and is released over the bar's path or its padding is a selection like any other
  o.bar.children.find((c) => c.classList.contains("fileview-name"))!.dispatchEvent(new Ev("mouseup"));
  await settle();
  assert.equal(reads(), r + 1, "released over the bar's path, the drag settles: the gate is the control under the lift, not the bar");
  assert.equal(seeds(), s + 1);
  o.bar.dispatchEvent(new Ev("mouseup"));
  await settle();
  assert.equal(reads(), r + 2, "and the bar's own padding");
  assert.equal(seeds(), s + 2);
});

// ── the URL viewer: the same size for a document opened from a link on the dashboard's own address ──

test("openUrlView carries the stored step on its root and mounts the same control; a step there is the one size every document honours", async (t) => {
  const fv = await view();
  store.set(SIZE_KEY, "130");
  fv.openUrlView(HREF);
  t.after(() => { fv.closeFileView(); store.clear(); });
  const o = card(fv);
  assert.equal(size(o), "130", "the stored step is on the root before the document lands");
  assert.equal(o.zoom.hidden, true, "hidden beside the loader");   // the glyph is the control's visibility (T367)
  const row = labels(o.acts);
  const zoomAt = row.findIndex((x) => x.startsWith("A−"));   // the zoom wrap reads as one label: the flyout's three buttons (T367)
  assert.ok(row.indexOf("Raw") < zoomAt && zoomAt < row.indexOf("Open ↗"), "after Rendered and Raw, before the link out: " + row.join(" | "));
  await settle();
  assert.equal(o.body.children[0].className, "fileview-md", "the document rendered");
  assert.equal(o.zoom.hidden, false);   // the glyph is the control's visibility (T367)
  assert.equal(o.reset.textContent, "130%"); assert.equal(blank(o), false);
  o.up.click();
  assert.equal(size(o), "150"); assert.equal(store.get(SIZE_KEY), "150", "stored under the same key as a local file's");
  const ev = wheel({ dy: -100, ctrl: true });
  o.body.querySelector(".fileview-md")!.dispatchEvent(ev);
  assert.equal(size(o), "175", "Ctrl + wheel steps here too"); assert.equal(ev.defaultPrevented, true);
  assert.deepEqual(o.body.passiveOf("wheel"), [false], "non-passive here too: the page zoom can be prevented");
  const plain = wheel({ dy: -100 });
  o.body.dispatchEvent(plain);
  assert.equal(size(o), "175"); assert.equal(plain.defaultPrevented, false, "a plain wheel scrolls");
  o.btn("Raw").click();
  assert.equal(o.body.children[0].className, "fileview-code");
  assert.deepEqual([o.btn("A−"), o.acts.querySelector(".fileview-size-reset"), o.btn("A+")], [o.down, o.reset, o.up], "the same nodes across the Raw paint");
  fv.closeFileView();
  const local = await openFile(t, REPORT);
  assert.equal(size(local), "175", "a local file opened next honours the size the document set");
});

// ── the sheets: one property, read by every text size; the measure that follows the size ────────────

/** The rule whose selector opens a line as `head`. */
const ruleOf = (css: string, head: string): string => { const at = css.indexOf("\n" + head); assert.ok(at >= 0, head + " present"); return css.slice(at + 1, css.indexOf("}", at) + 1); };
const decls = (rule: string): string[] => rule.slice(rule.indexOf("{") + 1, -1).split(";").map((d) => d.trim()).filter(Boolean);

test("both sheets: the step table maps every TEXT_SIZES entry to --fv-scale on the viewer root and nothing else; exactly the text views read the one property", async () => {
  const { TEXT_SIZES } = await view();
  for (const [name, css] of SHEETS) {
    for (const n of TEXT_SIZES) {
      assert.deepEqual(decls(ruleOf(css, `.fileview[data-fv-text="${n}"] {`)), [`--fv-scale: ${n / 100}`], name + ": step " + n + " is exactly the property");
    }
    assert.equal((css.match(/\.fileview\[data-fv-text="\d+"\]/g) || []).length, TEXT_SIZES.length, name + ": no step the table does not hold");
    assert.equal((css.match(/--fv-scale:/g) || []).length, TEXT_SIZES.length, name + ": nothing else sets the property");
    // the readers: the prose (the page's size times the document's 1.15, times the scale), fenced code, the Raw view's rows and gutter
    const root = decls(ruleOf(css, ".fileview-md {"));
    assert.ok(root.includes("font-size: calc(var(--fs) * 1.15 * var(--fv-scale, 1))"), name + ": the prose reads it");
    assert.ok(!root.some((d) => d.startsWith("max-width")), name + ": the root is fluid to the viewer (the measure is its inline padding)");
    assert.ok(root.includes("padding-inline: max(18px, round(down, calc((100% - 80ch) / 2), 1px))"), name + ": the measure is 80 of the root's own ch, centred as whole-pixel padding, never under 18px");
    const plain = root.indexOf("padding-inline: max(18px, calc((100% - 80ch) / 2))"), rounded = root.findIndex((d) => d.startsWith("padding-inline: max(18px, round("));
    assert.ok(plain >= 0 && plain < rounded, name + ": a plain calc() fallback is declared first, for an engine without round()");
    assert.ok(root.includes("line-height: 1.5") && root.includes("font-family: var(--font-doc)") && root.includes("contain: layout"), name + ": the leading and the face are the document's own, and nothing inside can widen the body");
    assert.ok(decls(ruleOf(css, ".fileview-md pre code {")).includes("font-size: calc(12px * var(--fv-scale, 1))"), name + ": fenced code reads it");
    assert.ok(decls(ruleOf(css, ".fileview-pre {")).includes("font-size: calc(12px * var(--fv-scale, 1))"), name + ": the Raw rows read it");
    assert.ok(decls(ruleOf(css, ".fileview-gutter {")).includes("font-size: calc(12px * var(--fv-scale, 1))"), name + ": the gutter reads it, in lockstep with the rows");
    assert.ok(decls(ruleOf(css, ".fileview-md h1 {")).includes("font-size: 2em"), name + ": headings stay em of the prose, so they scale with it");
    assert.ok(decls(ruleOf(css, ".fileview-md h2 {")).includes("font-size: 1.5em") && decls(ruleOf(css, ".fileview-md h3 {")).includes("font-size: 1.25em"), name + ": the ladder is 2 / 1.5 / 1.25 / 1em");
    assert.ok(decls(ruleOf(css, ".fileview-md :not(pre) > code {")).includes("font-size: 0.92em"), name + ": inline code stays em of the prose");
    assert.ok(decls(ruleOf(css, ".fileview-editor {")).includes("font-size: 12px"), name + ": the editor keeps the page's size");
    assert.ok(decls(ruleOf(css, ".fileview-btn {")).includes("font-size: 0.82em"), name + ": the bar's buttons keep the page's size");
    // no other rule reads the property: the title bar, its buttons and the editor keep the page's size
    const readers = (css.match(/^[^\n{]*\{[^}]*var\(--fv-scale[^}]*\}/gm) || []).map((r) => r.slice(0, r.indexOf("{")).trim());
    assert.deepEqual(readers.sort(), [".fileview-gutter", ".fileview-md", ".fileview-md pre code", ".fileview-pre"].sort(), name + ": the readers, exactly");
  }
});

test("both sheets: the measure is the root's own inline padding; a table takes the body's width and scrolls on its own with whole words; a picture fits the column; byte-equal across the sheets", () => {
  for (const [name, css] of SHEETS) {
    assert.doesNotMatch(css, /\.fileview-md > :where\(/, name + ": no per-child measure rule remains (the root's padding is the measure)");
    assert.doesNotMatch(css, /\.fileview-md > :not\(/, name + ": nor a specificity-bearing one");
    assert.doesNotMatch(css, /860px/, name + ": the fixed cap is gone");
    assert.deepEqual(decls(ruleOf(css, ".fileview-md img {")), ["max-width: 100%"], name + ": a picture shrinks to its column (the root's content box)");
    assert.deepEqual(decls(ruleOf(css, ":where(.fileview-md) svg, :where(.fileview-md) canvas, :where(.fileview-md) video {")), ["max-width: 100%"], name + ": the media a file draws itself (svg, canvas, video) shrink to the column too, at element specificity");
    // with the column the root's own content box there is no measure for a top-level medium's cap to outrank: the general
    // caps above hold a bare <img> line or an inline <svg> block, direct children of the root, to the column by themselves
    assert.equal(css.indexOf("\n.fileview-md > img {"), -1, name + ": a bare top-level picture needs no rule of its own now that the column is the root's box");
    assert.doesNotMatch(css, /\.fileview-md > :is\(img/, name + ": nor does a top-level svg, canvas or video block");
    assert.deepEqual(decls(ruleOf(css, ".fileview-md table {")),
      ["border-collapse: collapse", "margin: 0.6em 0", "display: block", "width: max-content", "max-width: 100%", "overflow-x: auto", "overflow-wrap: normal"],
      name + ": a table is a block as wide as its columns need up to its container, scrolling inside beyond it, whole words kept");
    // a table of the page's own may grow out of the column, evenly into both gutters, up to the body's content width less
    // the root's 18px inset (--fv-body-w, written by the viewer's width observer on each top-level table); unset, both
    // declarations read the column and the shift is none
    const top = decls(ruleOf(css, ".fileview-md > table {"));
    assert.equal(top[0], "max-width: calc(var(--fv-body-w, calc(100% + 36px)) - 36px)", name + ": the cap is the body less the inset, the column before the first report");
    assert.equal(top[1], "translate: min(0px, round(calc(var(--fv-body-w, calc(100% + 36px)) / 2 - max(18px, round(down, (var(--fv-body-w, calc(100% + 36px)) - 80ch) / 2, 1px)) - 50%), 1px))", name + ": the shift left is half of what the table exceeds the column by, whole pixels, never right");
    assert.equal(top.length, 2);
    assert.match(css, /^@property --fv-body-w \{ syntax: "\*"; inherits: false; \}$/m, name + ": the property is registered non-inherited, so a write restyles the tables alone");
    assert.ok(decls(ruleOf(css, ".fileview-md {")).includes("overflow-wrap: anywhere"), name + ": prose still breaks an unbreakable string");
    assert.ok(decls(ruleOf(css, ".fileview-md pre {")).includes("overflow-x: auto"), name + ": a code block scrolls on its own");
    assert.ok(decls(ruleOf(css, ".fileview-md pre code {")).includes("white-space: pre-wrap"), name + ": and wraps first");
  }
  const [chat, feed] = SHEETS.map(([, css]) => css);
  for (const head of [".fileview-md {", ".fileview-md table {", ".fileview-md > table {", ".fileview-md pre code {", ".fileview-pre {", ".fileview-gutter {", ".fileview-md img {"]) {
    assert.equal(ruleOf(chat, head), ruleOf(feed, head), head + " mirrors exactly (the viewer mounts in both documents)");
  }
  const block = (css: string) => { const a = css.indexOf("/* ── text size and measure"); const b = css.indexOf("/* Rendered markdown ("); assert.ok(a >= 0 && b > a, "the step-table block precedes the prose block"); return css.slice(a, b); };
  assert.ok(block(chat).length > 200, "the block with its rationale");
  assert.equal(block(chat), block(feed), "the step table and its comment mirror exactly");
});

test("both sheets: the title bar wraps and its action row shrinks and wraps to the right edge while the bare classes stay as they were; a disabled or aria-disabled bar button is dimmed with an inert hover; the readout is a fixed slot, empty at the default", () => {
  for (const [name, css] of SHEETS) {
    const bar = decls(ruleOf(css, ".fileview-bar {"));
    assert.ok(bar.includes("flex-wrap: wrap") && bar.includes("gap: 6px 10px"), name + ": the bar wraps, 6px between its lines");
    assert.deepEqual(decls(ruleOf(css, ".fileview-bar .fileview-name {")), ["flex: 1 1 0", "min-width: 12em"], name + ": in the bar the path keeps 12em and takes the rest of a wide bar");
    const nm = decls(ruleOf(css, ".fileview-name {"));
    assert.ok(nm.includes("flex: 1 1 auto") && nm.includes("min-width: 0"), name + ": the class alone shrinks freely");
    const barActs = decls(ruleOf(css, ".fileview-bar .fileview-acts {"));
    for (const d of ["flex: 0 1 auto", "min-width: 0", "margin-left: auto", "flex-wrap: wrap", "justify-content: flex-end"]) assert.ok(barActs.includes(d), name + ": in the bar the action row " + d);
    assert.deepEqual(decls(ruleOf(css, ".fileview-acts {")), ["flex: 0 0 auto", "display: flex", "align-items: center", "gap: 6px"], name + ": the class alone is one rigid row (the file browser's bar wears it outside any title bar)");
    assert.ok(!decls(ruleOf(css, ".fb-bar {")).some((d) => d.startsWith("flex-wrap")), name + ": .fb-bar has no wrap of its own");
    // one disabled dress for every bar button: the GitHub unit's no-link state (disabled) and the control's ends (aria-disabled)
    assert.deepEqual(decls(ruleOf(css, '.fileview-btn:disabled, .fileview-btn[aria-disabled="true"] {')), ["opacity: 0.55", "cursor: default"], name + ": dimmed, default cursor");
    assert.deepEqual(decls(ruleOf(css, '.fileview-btn:disabled:hover, .fileview-btn[aria-disabled="true"]:hover {')), ["border-color: var(--card-border)", "color: var(--fg)", "background: transparent"], name + ": the hover is inert (the rest colours, not the accent)");
    assert.deepEqual(decls(ruleOf(css, '.fileview-btn:disabled:active, .fileview-btn[aria-disabled="true"]:active {')), ["transform: none"], name + ": no press pulse");
    assert.doesNotMatch(css, /\.fileview-gh \.fileview-btn:disabled/, name + ": the GitHub unit's disabled rules are the bar's now, not its own");
    // the readout: one width whatever it says, and dimmed (not empty) at the default since it moved into the zoom flyout (T367)
    assert.deepEqual(decls(ruleOf(css, ".fileview-size-reset {")), ["min-width: 5.5em", "box-sizing: border-box", "text-align: center", "font-variant-numeric: tabular-nums"], name + ": a slot of one width");
    assert.deepEqual(decls(ruleOf(css, ".fileview-size-reset.fileview-size-default {")), ["color: var(--dim)"], name + ": at the default the readout reads dimmed inside the flyout (T367): nothing to reset, the slot keeps its width");
  }
  const [chat, feed] = SHEETS.map(([, css]) => css);
  for (const head of [".fileview-bar {", ".fileview-name {", ".fileview-acts {", ".fileview-bar .fileview-name {", ".fileview-bar .fileview-acts {",
    '.fileview-btn:disabled, .fileview-btn[aria-disabled="true"] {', ".fileview-size-reset {", ".fileview-size-reset.fileview-size-default {"]) {
    assert.equal(ruleOf(chat, head), ruleOf(feed, head), head + " mirrors exactly");
  }
});

// ── the browser leg: a real layout over each sheet, through a child driver ─────────────────────────
// The layout claims the sheet pins cannot show (the page never widens, a wide table scrolls on its own, a table in a
// quote or a list item keeps the prose width, the action row drops below the path and the close button stays on the
// card, A− and A+ hold still when the readout fills, the file browser's bar keeps one line) are
// measured in headless Chromium. The measurement runs in a standalone driver (the test bundle is CommonJS without
// top-level await, and esbuild must never try to bundle playwright); the driver exits 3 without playwright or a
// browser, and the leg skips loudly there.
const LONG = "unbreakable".repeat(5);   // wide enough to push the table past the column at 1000px, narrow enough that the body still holds it
const SVG = (w: number) => `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="120"><rect width="${w}" height="120" fill="#369"/></svg>`)}`;
const ROWS = Array.from({ length: 3 }, (_, i) => `<tr><td>row ${i} alpha beta gamma delta</td><td>a fairly long cell of prose that keeps going on for a while</td><td>${LONG}</td><td>another long cell with many words in it to widen the table</td><td>five</td><td>six more text here</td></tr>`).join("");
/** A six-column table whose columns want more than the prose measure: at the top level, inside a quote and inside a list item. */
const TABLE = (id: string) => `<table id="${id}"><thead><tr><th>one</th><th>two</th><th>three</th><th>four</th><th>five</th><th>six</th></tr></thead><tbody>${ROWS}</tbody></table>`;
const ROW = (t: string) => `<span class="cl"><span class="ct">${t}</span></span>`;
const MD = `<h1 id="h1">Report</h1><h2 id="h2">Findings</h2><h3 id="h3">Detail</h3><p id="p">Prose ${"lorem ipsum ".repeat(40)}</p>
${TABLE("t")}
<blockquote id="bq"><p>A quoted note with a table of its own.</p>${TABLE("tq")}</blockquote>
<ul><li>A first item.</li><li id="li">An item with a table of its own.${TABLE("tl")}</li></ul>
<pre id="pre"><code>${"const x = 1; ".repeat(30)}</code></pre>
<pre id="pre2"><code>const y = 2;</code></pre>
<pre id="pre3"><code style="--ln-digits: 1">${ROW("a")}${ROW("")}${ROW("b")}</code></pre>
<pre id="pre4"><code style="--ln-digits: 5">${ROW("a")}</code></pre>
<pre id="pre5" class="has-copy"><code>z</code><button class="code-copy" id="copy">Copy</button></pre>
<ul><li id="task" class="task-list-item"><input type="checkbox" disabled> A task</li></ul>
<p id="lw">${"x".repeat(140)}</p>
<p><img id="im" width="900" height="120" src="${SVG(900)}"></p>
<img id="im2" width="1600" height="120" src="${SVG(1600)}">
<svg id="sv" xmlns="http://www.w3.org/2000/svg" width="1600" height="120" viewBox="0 0 1600 120"><rect width="1600" height="120" fill="#693"/></svg>`;
const sheet = (css: string) => `<!DOCTYPE html><html><head><meta charset="utf-8"><style>${css}</style></head>`;
/** The modal as openFileView builds it, over a rendered markdown body. */
const LAYOUT_PAGE = (css: string) => sheet(css) + `<body class="fileview-open"><div id="romp-fileview"><div class="fileview" id="root"><div class="fileview-bar"><div class="fileview-name"><span class="fileview-dir">/tmp/notes-api/docs/</span><span class="fileview-base">report.md</span></div><div class="fileview-acts"><button class="fileview-btn on">Rendered</button><button class="fileview-btn">Raw</button><button class="fileview-btn fileview-size">A−</button><button class="fileview-btn fileview-size fileview-size-reset fileview-size-default">100%</button><button class="fileview-btn fileview-size">A+</button><button class="fileview-btn fileview-close">✕</button></div></div>
<div class="fileview-body" id="body"><div class="fileview-md" id="md">${MD}</div></div></div></div></body></html>`;
const LAYOUT_MEASURE = `(() => {
  const q = (s) => document.querySelector(s);
  const w = (s) => q(s).getBoundingClientRect().width;
  const fz = (s) => parseFloat(getComputedStyle(q(s)).fontSize);
  const body = q("#body"), t = q("#t"), tq = q("#tq"), tl = q("#tl"), pre = q("#pre"), md = q("#md"), cs = getComputedStyle(md);
  const probe = document.createElement("span"); probe.textContent = "0".repeat(80); probe.style.whiteSpace = "nowrap"; probe.style.position = "absolute";
  md.appendChild(probe); const ch80 = probe.getBoundingClientRect().width; probe.remove();
  const rows = Array.from(q("#pre3 code").children).map((r) => r.getBoundingClientRect().height);
  const ctOff = (s) => q(s + " .ct").getBoundingClientRect().left - q(s + " code").getBoundingClientRect().left;
  const r = (s) => q(s).getBoundingClientRect();
  return { win: innerWidth, docScroll: document.documentElement.scrollWidth, bodyClient: body.clientWidth, bodyScroll: body.scrollWidth,
    padL: parseFloat(cs.paddingLeft), padR: parseFloat(cs.paddingRight), ch80, pLeft: r("#p").left, pRight: r("#p").right, tLeft: r("#t").left, tRight: r("#t").right,
    h2Font: fz("#h2"), h3Font: fz("#h3"), rows, ct1: ctOff("#pre3"), ct5: ctOff("#pre4"),
    taskStyle: getComputedStyle(q("#task")).listStyleType, taskScheme: getComputedStyle(q("#task input")).colorScheme,
    md: w("#md"), p: w("#p"), t: w("#t"), tClient: t.clientWidth, tScroll: t.scrollWidth, pre: w("#pre"), preClient: pre.clientWidth, preScroll: pre.scrollWidth,
    bq: w("#bq"), tq: w("#tq"), tqClient: tq.clientWidth, tqScroll: tq.scrollWidth, li: w("#li"), tl: w("#tl"), tlClient: tl.clientWidth, tlScroll: tl.scrollWidth,
    pre2: w("#pre2"), lw: w("#lw"), img: w("#im"), img2: w("#im2"), svg: w("#sv"), mdFont: fz("#md"), h1Font: fz("#h1"), codeFont: fz("#pre code") };
})()`;
/** The print sheet over the same page: what leaves, what turns black, what a table becomes. */
const PRINT_MEASURE = `(() => {
  const q = (s) => document.querySelector(s); const cs = (s) => getComputedStyle(q(s));
  return { bar: cs(".fileview-bar").display, copy: cs("#copy").display, md: cs("#md").color, h1: cs("#h1").color, code: cs("#pre code").color, dim: cs("#h1").borderBottomColor,
    table: cs("#t").display, bodyBg: getComputedStyle(document.body).backgroundColor, rootBg: getComputedStyle(document.documentElement).backgroundColor,
    bodyOverflow: cs("#body").overflowY, scheme: cs("#task input").colorScheme, tWidth: q("#t").getBoundingClientRect().width, bodyW: q("#body").clientWidth, docH: document.documentElement.scrollHeight, winH: innerHeight };
})()`;
/** The bar a kernel-answered markdown file shows: the format toggles, the control, Edit, the GitHub unit with its caption,
 *  Download, Copy path and the close button, behind a deep path and a session chip. */
const BAR_PAGE = (css: string) => sheet(css) + `<body class="fileview-open"><div id="romp-fileview"><div class="fileview" id="root"><div class="fileview-bar" id="bar"><div class="fileview-name" id="name"><span class="fileview-dir">/tmp/notes-api/services/api/internal/handlers/</span><span class="fileview-base">report.md</span></div><span class="fileview-sess">api</span><div class="fileview-acts" id="acts"><button class="fileview-btn on">Rendered</button><button class="fileview-btn">Raw</button><button class="fileview-btn fileview-size" id="down">A−</button><button class="fileview-btn fileview-size fileview-size-reset fileview-size-default" id="reset">100%</button><button class="fileview-btn fileview-size" id="up">A+</button><button class="fileview-btn">Edit</button><span class="fileview-gh"><span class="fileview-gh-why">not committed (staged only)</span><button class="fileview-btn" disabled>GitHub ↗</button></span><button class="fileview-btn">Download</button><button class="fileview-btn">Copy path</button><button class="fileview-btn fileview-close" id="close">✕</button></div></div>
<div class="fileview-body" id="body"><div class="fileview-md"><p>Prose.</p></div></div></div></div></body></html>`;
const BAR_MEASURE = `(() => {
  const r = (id) => { const b = document.getElementById(id).getBoundingClientRect(); return { l: b.left, r: b.right, t: b.top, b: b.bottom, w: b.width }; };
  const cs = (id) => getComputedStyle(document.getElementById(id));
  return { win: innerWidth, card: r("root"), bar: r("bar"), name: r("name"), acts: r("acts"), close: r("close"), down: r("down"), up: r("up"), reset: r("reset"),
    resetVis: cs("reset").visibility, nameFont: parseFloat(cs("name").fontSize), barPadRight: parseFloat(cs("bar").paddingRight) };
})()`;
const READOUT_ON = `(() => { const b = document.getElementById("reset"); b.classList.remove("fileview-size-default"); b.textContent = "115%"; })()`;
const READOUT_OFF = `(() => { const b = document.getElementById("reset"); b.classList.add("fileview-size-default"); b.textContent = "100%"; })()`;
/** The file browser's bar (file-browse.ts): the crumb trail and, wearing .fileview-acts outside any title bar, Hidden and the close button. */
const CRUMBS = ["/", "tmp", "notes-api", "services", "api", "internal", "handlers", "v2", "tests", "fixtures", "golden"];
const FB_PAGE = (css: string) => sheet(css) + `<body class="filebrowse-open"><div id="romp-filebrowse"><div class="filebrowse"><div class="fb-bar" id="fbbar"><div class="fb-crumbs" id="fb-crumbs">${CRUMBS.map((c, i) => (i ? '<span class="fb-crumb-sep">/</span>' : "") + '<span class="fb-crumb">' + c + "</span>").join("")}</div><div class="fileview-acts" id="acts"><button class="fileview-btn" id="hid">Hidden</button><button class="fileview-btn fileview-close" id="fbclose">✕</button></div></div><div class="fb-list"></div></div></div></body></html>`;
const FB_MEASURE = `(() => {
  const g = (id) => document.getElementById(id);
  const bar = g("fbbar"), crumbs = g("fb-crumbs"), acts = g("acts"), hid = g("hid").getBoundingClientRect(), close = g("fbclose").getBoundingClientRect();
  return { win: innerWidth, barH: bar.getBoundingClientRect().height, barOver: bar.scrollWidth - bar.clientWidth, hidTop: hid.top, closeTop: close.top, closeLeft: close.left, closeRight: close.right,
    acts: acts.getBoundingClientRect().width, wrap: getComputedStyle(acts).flexWrap, crumbsClient: crumbs.clientWidth, crumbsScroll: crumbs.scrollWidth };
})()`;
// mdBlock's task stamp, run in the page over marked's own output for a synthetic document, sanitized as mdBlock sanitizes
// it (the shared sanitizer, md-sanitize.ts, bundled from the source with esbuild and added to the page as a script; the
// body it returns is adopted the way mdBlock adopts it) and then stamped by the statement lifted from mdBlock. marked
// emits a task item's checkbox as the li's first node (inside its first paragraph in a loose list); a checkbox an author's
// raw HTML puts after the item's text matches the stamp's :first-child selector (elements alone count) and must not make a
// task item of the item. A tight list, a numbered one, a loose one (every item of a loose list is a paragraph, the mid-item
// case included), an enabled box (the sanitizer makes it disabled before the stamp sees it, so it opens its item like
// marked's own), a raw disabled box that opens its item, and an author's own class.
const STAMP_MD = [
  "- [ ] open task", "- [x] done task", "- plain item", '- text then <input type="checkbox" disabled> mid-item',
  '- **bold** then <input type="checkbox" disabled> after bold', '- <input type="checkbox"> enabled first', '- <input type="checkbox" disabled> raw first',
  "", "1. [ ] numbered task", "", "- [ ] loose task", "", "  with a second paragraph", "", '- loose text then <input type="checkbox" disabled> loose mid-item',
  "", '<ul><li class="task-list-item"><input type="checkbox" disabled> an author item</li></ul>', "",
].join("\n");
/** The real sanitizer for the page: md-sanitize.ts and the DOMPurify under it, bundled from the source (esbuild and the
 *  packages resolve from the extension package, as the test bundle itself was built), exposing sanitizeMd on the window. */
function sanitizerJs(): string {
  const requireExt = createRequire(path.resolve(process.cwd(), "package.json"));
  const esbuild = requireExt("esbuild");
  const r = esbuild.buildSync({
    stdin: { contents: 'import { sanitizeMd } from "./md-sanitize"; (window as any).sanitizeMd = sanitizeMd;', resolveDir: path.resolve(process.cwd(), "..", "ui", "webview"), loader: "ts", sourcefile: "fv-textsize-sanitizer-entry.ts" },
    bundle: true, write: false, format: "iife", platform: "browser", target: "es2020", nodePaths: [path.resolve(process.cwd(), "node_modules")], logLevel: "silent",
  });
  return r.outputFiles[0].text as string;
}
const STAMP_CODE = (() => { const at = VIEW.indexOf("box.querySelectorAll('li > input"); return at < 0 ? "" : VIEW.slice(at, VIEW.indexOf("\n  });", at) + 6); })();
const STAMP_PAGE = (css: string) => sheet(css) + `<body class="fileview-open"><div id="romp-fileview"><div class="fileview" id="root"><div class="fileview-body" id="body"><div class="fileview-md" id="md"></div></div></div></div></body></html>`;
const STAMP_PREP = `(() => { const box = document.getElementById("md"); box.replaceChildren(...Array.from(sanitizeMd(${JSON.stringify(marked.parse(STAMP_MD) as string)}).childNodes)); ${STAMP_CODE} })()`;
const STAMP_MEASURE = `(() => Array.from(document.querySelectorAll("#md li")).map((li) => {
  const input = li.querySelector("input"), lr = li.getBoundingClientRect();
  return { text: (li.textContent || "").replace(/\\s+/g, " ").trim(), stamped: li.classList.contains("task-list-item"), bullet: getComputedStyle(li).listStyleType,
    input: input ? { disabled: input.disabled, checked: input.checked, marginLeft: parseFloat(getComputedStyle(input).marginLeft), left: input.getBoundingClientRect().left - lr.left, scheme: getComputedStyle(input).colorScheme } : null };
}))()`;
type Step = { width?: number; size?: number; prep?: string; media?: string; measure?: string; tag: string };
type Case = { name: string; html: string; width: number; measure: string; steps: Step[]; inline?: string[] };   // inline: script text added to the page after its HTML
type Rows = Record<string, { rows: Array<{ step: Step; got: any }>; errors: string[] }>;
/** A step, and the body's content width written on each top-level table the way the viewer's width observer writes it
 *  (file-view.ts watchBodyWidth: --fv-body-w on the tables themselves, after every paint and every width change); the
 *  page here carries no script, so the prep stands in for the observer. */
const step = (n: number) => `(() => { document.getElementById("root").dataset.fvText = "${n}"; const w = document.getElementById("body").clientWidth + "px"; for (const t of document.querySelectorAll("#md > table")) t.style.setProperty("--fv-body-w", w); })()`;
function cases(): Case[] {
  const out: Case[] = [];
  const sanitizer = sanitizerJs();
  for (const [name, css] of SHEETS) {
    out.push({ name: "layout/" + name, html: LAYOUT_PAGE(css), width: 1000, measure: LAYOUT_MEASURE, steps: [
      { size: 100, prep: step(100), tag: "@1000/100" }, { size: 150, prep: step(150), tag: "@1000/150" },
      { width: 420, size: 100, prep: step(100), tag: "@420/100" }, { size: 150, prep: step(150), tag: "@420/150" },
      { width: 1000, size: 100, prep: step(100), media: "print", measure: PRINT_MEASURE, tag: "@1000 print" },
    ] });
    const bar: Step[] = [];
    for (const w of [380, 420, 480, 600, 1000, 1400]) { bar.push({ width: w, prep: READOUT_OFF, tag: "@" + w + " default" }); bar.push({ prep: READOUT_ON, tag: "@" + w + " readout" }); }
    out.push({ name: "bar/" + name, html: BAR_PAGE(css), width: 1000, measure: BAR_MEASURE, steps: bar });
    out.push({ name: "fb/" + name, html: FB_PAGE(css), width: 1000, measure: FB_MEASURE, steps: [1000, 480, 360, 320].map((w) => ({ width: w, tag: "@" + w })) });
    out.push({ name: "stamp/" + name, html: STAMP_PAGE(css), width: 1000, measure: STAMP_MEASURE, inline: [sanitizer], steps: [{ prep: STAMP_PREP, tag: "stamp" }] });
  }
  return out;
}
// The driver: playwright out of the extension package's node_modules, exit 3 when it or a browser is missing.
const DRIVER = `
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
let chromium;
try { chromium = require("playwright").chromium; } catch (e) { process.exit(3); }
let browser;
try { browser = await chromium.launch(); } catch (e) { process.exit(3); }
const spec = JSON.parse(fs.readFileSync(process.env.SPEC_PATH, "utf8"));
const out = {};
for (const c of spec) {
  const page = await browser.newPage({ viewport: { width: c.width, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => { errors.push(e.message); });
  await page.setContent(c.html);
  for (const s of c.inline || []) await page.addScriptTag({ content: s });
  const rows = [];
  for (const s of c.steps) {
    if (s.width) await page.setViewportSize({ width: s.width, height: 900 });
    if (s.prep) await page.evaluate(s.prep);
    if (s.media) await page.emulateMedia({ media: s.media });
    rows.push({ step: s, got: await page.evaluate(s.measure || c.measure) });
  }
  out[c.name] = { rows, errors };
  await page.close();
}
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\\n");
await browser.close();
process.exit(0);
`;
function measure(): Rows | null {
  const os = require("node:os");
  const cp = require("node:child_process");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "fv-textsize-"));
  const driver = path.join(dir, "driver.mjs"); fs.writeFileSync(driver, DRIVER);
  const specPath = path.join(dir, "spec.json"); fs.writeFileSync(specPath, JSON.stringify(cases()));
  try {
    const p = cp.spawnSync(process.execPath, [driver], { encoding: "utf8", timeout: 180000, maxBuffer: 64 * 1024 * 1024,   // the running node, never PATH
      env: { ...process.env, EXT_PKG: path.resolve(process.cwd(), "package.json"), SPEC_PATH: specPath } });
    if (p.status === 3) return null;                                  // no playwright / no browser here
    if (p.status !== 0) throw new Error("layout driver failed: " + String(p.stderr || p.stdout || p.error || "").slice(-800));
    const line = (p.stdout || "").split("\n").find((l: string) => l.startsWith("RESULT:"));
    if (!line) throw new Error("layout driver printed no result: " + (p.stdout || "").slice(-400));
    return JSON.parse(line.slice("RESULT:".length));
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
}
const m = measure();
const skip = m ? false : "no Playwright browser here (CI installs none); the layout claims rest on the sheet pins above";
const near = (a: number, b: number, what: string) => assert.ok(Math.abs(a - b) < 1, what + ": " + a + " vs " + b);

test("in a browser: the page never widens at 1000 and 420px, at 100% and 150%, in both sheets; prose and code keep the measure (80 of the root's ch, centred, whole pixels) and a table grows evenly out of the column up to the body while a table in a quote or a list item keeps the prose width; the heading ladder and the fence rows measure; at 150% every text size and the measure grow together; narrow, a table scrolls on its own; in print the file alone prints black on white", { skip }, () => {
  for (const [name] of SHEETS) {
    const c = m!["layout/" + name];
    assert.deepEqual(c.errors, [], name + ": no script error in the page");
    const at = (tag: string) => c.rows.find((r) => r.step.tag === tag)!.got;
    for (const r of c.rows.filter((x) => !x.step.media)) {
      const l = r.got, cell = name + " " + r.step.tag;
      assert.equal(l.bodyScroll, l.bodyClient, cell + ": the body does not scroll sideways");
      assert.equal(l.docScroll, l.win, cell + ": the page is the window");
      const col = l.bodyClient - 36 + 0.5;                            // the root's 18px padding a side
      assert.ok(l.pre <= col && l.pre2 <= col && l.lw <= col && l.img <= col, cell + ": code, the long string and the image paragraph fit the column");
      assert.ok(l.img2 <= col, cell + ": the bare <img> line fits the column too (" + l.img2 + " in " + (l.bodyClient - 36) + "): its own cap outranks the measure");
      assert.ok(l.svg <= col, cell + ": a wide inline svg block, a direct child of the root, fits the column too (" + l.svg + " in " + (l.bodyClient - 36) + "): the element-level media cap holds it to the root's content box");
      assert.ok(l.preScroll <= l.preClient + 1, cell + ": the long code line wraps inside its block");
      near(l.md, l.bodyClient, cell + ": the root is the body's width");
      assert.ok(l.bq <= l.p + 0.5 && l.tq <= l.bq + 0.5 && l.li <= l.p + 0.5 && l.tl <= l.li + 0.5,
        cell + ": a quote and a list item keep the prose width and the table inside each keeps to it (quote " + l.bq + ", its table " + l.tq + "; item " + l.li + ", its table " + l.tl + "; prose " + l.p + ")");
    }
    const base = at("@1000/100"), big = at("@1000/150");
    for (const r of c.rows.filter((x) => !x.step.media)) {
      const l = r.got, cell = name + " " + r.step.tag;
      // the measure: 80 of the root's own ch, centred as inline padding rounded down to whole pixels, never under 18px; when the
      // column cannot hold 80ch the padding is its 18px floor and the prose follows the viewer
      const want = Math.min(l.ch80, l.bodyClient - 36);
      assert.ok(l.p >= want - 0.5 && l.p <= want + 2.5, cell + ": the prose measure is 80ch or the column (" + l.p + ", 80ch " + l.ch80 + ", column " + (l.bodyClient - 36) + ")");
      near(l.p, l.bodyClient - l.padL - l.padR, cell + ": the measure is the root's content box, laid as its inline padding");
      assert.equal(l.padL, l.padR, cell + ": centred (" + l.padL + " / " + l.padR + ")");
      assert.ok(Number.isInteger(l.padL) && l.padL >= 18, cell + ": whole pixels, never under 18px (" + l.padL + ")");
      near(l.pre, l.p, cell + ": a code block keeps the measure"); near(l.pre2, l.p, cell + ": a two-line snippet too, no viewer-wide block");
      assert.ok(l.tq <= l.p + 0.5 && l.tl <= l.p + 0.5, cell + ": the same table in a quote (" + l.tq + ") or a list item (" + l.tl + ") keeps the prose measure");
      assert.ok(l.img <= l.p + 0.5 && l.lw <= l.p + 0.5 && l.img2 <= l.p + 0.5 && l.svg <= l.p + 0.5, cell + ": both pictures, the inline svg and an unbreakable string stay in the measure");
      // the ladder, em of the prose: 2 / 1.5 / 1.25
      near(l.h1Font, l.mdFont * 2, cell + ": h1 is 2em of the prose"); near(l.h2Font, l.mdFont * 1.5, cell + ": h2 is 1.5em"); near(l.h3Font, l.mdFont * 1.25, cell + ": h3 is 1.25em");
      // the fence rows: a text row, a blank row and the last row are each one line-height of the code; the gutter's basis
      // follows the fence's digit count (2.5em of the gutter's own 0.92em for one digit, plus the 0.85em gap)
      assert.equal(l.rows.length, 3);
      for (const h of l.rows) near(h, l.codeFont * 1.5, cell + ": a row is the code's line-height (" + l.rows.join(", ") + ")");
      near(l.ct1, l.codeFont * 0.92 * 3.35, cell + ": the one-digit gutter is 2.5em plus the gap (" + l.ct1 + ")");
      assert.ok(l.ct5 > l.ct1 + 3, cell + ": a five-digit fence's gutter is wider than a one-digit fence's (" + l.ct5 + " vs " + l.ct1 + ")");
      // a task item: no bullet beside the box, the box drawn for the page's scheme (no theme class here: dark)
      assert.equal(l.taskStyle, "none", cell + ": a task item has no bullet"); assert.equal(l.taskScheme, "dark", cell + ": the box follows the page's scheme");
    }
    // at 1000px and 100% the column holds 80ch with room to spare, and a table wider than the column grows out of it evenly
    assert.ok(base.padL > 18, name + " @1000/100: room to spare (" + base.padL + "px a side)");
    assert.ok(base.t > base.p + 1 && base.t <= base.bodyClient - 36 + 0.5, name + " @1000/100: the table grows out of the column (" + base.t + " past " + base.p + "), inside the body's inset");
    assert.equal(base.tScroll, base.tClient, name + " @1000/100: room enough, so the table does not scroll");
    assert.ok(Math.abs((base.pLeft - base.tLeft) - (base.tRight - base.pRight)) <= 1.5, name + " @1000/100: evenly into both gutters (" + (base.pLeft - base.tLeft) + " left, " + (base.tRight - base.pRight) + " right)");
    assert.ok(Math.abs(base.mdFont - 13 * 1.15) < 0.05, name + " @100%: the prose is 1.15 times the page's 13px (" + base.mdFont + ")");
    assert.equal(base.codeFont, 12, name + " @100%: fenced code at 12px, the size it had");
    near(big.mdFont, base.mdFont * 1.5, name + " @150%: the prose"); assert.equal(big.codeFont, 18, name + " @150%: fenced code");
    assert.ok(big.ch80 > base.ch80 * 1.4, name + " @150%: the measure's unit grows with the text (" + big.ch80 + " from " + base.ch80 + ")");
    for (const tag of ["@420/100", "@420/150"]) {
      const l = at(tag), cell = name + " " + tag;
      assert.equal(l.padL, 18, cell + ": the inset floor holds when the column cannot hold 80ch");
      near(l.t, l.bodyClient - 36, cell + ": the table is the column");
      assert.ok(l.tScroll > l.tClient + 100, cell + ": the unbreakable cell scrolls inside the table (" + l.tScroll + " in " + l.tClient + ")");
      near(l.p, l.bodyClient - 36, cell + ": the prose follows the viewer below its measure");
      near(l.img2, l.bodyClient - 36, cell + ": the wide banner is the column");
      near(l.svg, l.bodyClient - 36, cell + ": the wide inline svg is the column");
    }
    for (const tag of ["@1000/150", "@420/100", "@420/150"]) {
      const l = at(tag), cell = name + " " + tag;
      assert.ok(l.tqScroll > l.tqClient + 100 && l.tlScroll > l.tlClient + 100,
        cell + ": a nested table its quote or item cannot hold scrolls inside itself (" + l.tqScroll + " in " + l.tqClient + "; " + l.tlScroll + " in " + l.tlClient + ")");
    }
    // print: the file alone, black on white, as many pages as it needs
    const print = at("@1000 print"), cell = name + " print";
    assert.equal(print.bar, "none", cell + ": the title bar leaves"); assert.equal(print.copy, "none", cell + ": the Copy buttons leave");
    for (const [k, v] of Object.entries({ md: print.md, h1: print.h1, code: print.code, rule: print.dim })) assert.equal(v, "rgb(0, 0, 0)", cell + ": " + k + " in black");
    assert.equal(print.bodyBg, "rgb(255, 255, 255)", cell + ": the page is white"); assert.equal(print.rootBg, "rgb(255, 255, 255)", cell + ": the canvas too");
    assert.equal(print.table, "table", cell + ": a table is a table again"); assert.equal(print.scheme, "light", cell + ": the task box is drawn light");
    assert.equal(print.bodyOverflow, "visible", cell + ": the body does not clip to one screen"); assert.ok(print.docH > print.winH, cell + ": the document runs past one window's height (" + print.docH + " over " + print.winH + ")");
    assert.ok(print.tWidth > 0 && print.tWidth <= print.bodyW - 36 + 0.5, cell + ": the wide table keeps the body's room less the inset (" + print.tWidth + " in " + print.bodyW + ")");
  }
});

test("in a browser: from 380 to 1400px in both modals the close button and every action stay on the card; the path keeps 12em and the action row ends at the bar's edge; up to 600px the row drops to the line below the path and at 1400 the two share a line; A− and A+ hold still when the readout fills after the first press", { skip }, () => {
  for (const [name] of SHEETS) {
    const c = m!["bar/" + name];
    assert.deepEqual(c.errors, [], name + ": no script error in the page");
    for (let i = 0; i < c.rows.length; i += 2) {
      const off = c.rows[i].got, on = c.rows[i + 1].got, cell = name + " " + c.rows[i].step.tag.split(" ")[0];
      for (const [g, what] of [[off, "at the default"], [on, "with the readout showing"]] as Array<[any, string]>) {
        const inside = g.close.l >= g.card.l - 0.5 && g.close.r <= g.card.r + 0.5 && g.close.t >= g.card.t - 0.5 && g.close.b <= g.card.b + 0.5;
        assert.ok(inside, cell + " " + what + ": the close button lies inside the card (close " + JSON.stringify(g.close) + ", card " + JSON.stringify(g.card) + ")");
        assert.ok(g.name.w >= 12 * g.nameFont - 0.5, cell + " " + what + ": the path keeps 12em (" + g.name.w + "px at " + g.nameFont + "px)");
        near(g.acts.r, g.bar.r - g.barPadRight, cell + " " + what + ": the action row ends at the bar's right edge");
        // the bar's own wrap: three more buttons no longer fit beside a deep path below about 600px, so the row drops to
        // the line below (the row's own wrap alone would keep it beside the path, squeezed into a column); wide, one line
        if (g.win <= 600) assert.ok(g.acts.t >= g.name.b - 0.5, cell + " " + what + ": the action row is the line below the path (row top " + g.acts.t + ", path bottom " + g.name.b + ")");
        if (g.win >= 1400) assert.ok(g.acts.t < g.name.b - 0.5 && g.acts.b > g.name.t + 0.5, cell + " " + what + ": room for both, so the path and the action row share a line");
      }
      assert.equal(off.resetVis, "visible", cell + ": at the default the readout reads dimmed, its slot kept (T367: inside the flyout, visibility is never hidden)");
      assert.ok(off.reset.w > 20, cell + ": and keeps its width (" + off.reset.w + "px)");
      assert.equal(on.resetVis, "visible", cell + ": off the default the readout shows");
      near(off.down.l, on.down.l, cell + ": A− does not move when the readout fills"); near(off.down.t, on.down.t, cell + ": A− stays on its line");
      near(off.up.l, on.up.l, cell + ": A+ does not move when the readout fills"); near(off.up.t, on.up.t, cell + ": A+ stays on its line");
    }
  }
});

test("in a browser: the file browser's bar stays one line at 320, 360, 480 and 1000px in both sheets, its two buttons holding their width while the crumb trail gives up room", { skip }, () => {
  for (const [name] of SHEETS) {
    const c = m!["fb/" + name];
    assert.deepEqual(c.errors, [], name + ": no script error in the page");
    const wide = c.rows[0].got;
    assert.equal(wide.win, 1000);
    for (const r of c.rows) {
      const g = r.got, cell = name + " " + r.step.tag;
      assert.equal(g.hidTop, g.closeTop, cell + ": Hidden and the close button share a line");
      near(g.barH, wide.barH, cell + ": the bar is the height it has at 1000px (one line, not two)");
      near(g.acts, wide.acts, cell + ": the action row holds its width");
      assert.equal(g.wrap, "nowrap", cell + ": the row does not wrap");
      assert.ok(g.closeLeft >= 0 && g.closeRight <= g.win + 0.5, cell + ": the close button lies inside the window: x " + g.closeLeft + " to " + g.closeRight);
      assert.equal(g.barOver, 0, cell + ": the bar overflows nothing");
      if (g.win < 1000) assert.ok(g.crumbsScroll > g.crumbsClient, cell + ": the crumb trail is what gives up room (" + g.crumbsScroll + " in " + g.crumbsClient + ")");
    }
  }
});

test("in a browser: mdBlock's task stamp over marked's own output, sanitized as mdBlock sanitizes it, in both sheets: a disabled checkbox that opens its item (a tight list, a numbered one, a loose one's first paragraph, an author's raw box) makes a task item with no bullet and the box pulled into the gutter; an author's enabled box is made inert by the sanitizer and opens its item too; a box after the item's text leaves the item and its bullet as the file wrote them; an author's own class is kept", { skip }, () => {
  assert.ok(STAMP_CODE, "the stamp statement is lifted from the source");
  assert.match(VIEW, /box\.replaceChildren\(\.\.\.Array\.from\(sanitizeMd\(dirty\)\.childNodes\)\);/, "mdBlock adopts the shared sanitizer's body, as the page here does");
  type Row = { text: string; stamped: boolean; bullet: string; input: { disabled: boolean; checked: boolean; marginLeft: number; left: number; scheme: string } | null };
  for (const [name] of SHEETS) {
    const c = m!["stamp/" + name];
    assert.deepEqual(c.errors, [], name + ": no script error in the page");
    const rows = c.rows[0].got as Row[];
    assert.equal(rows.length, 11, name + ": every item of the document is in the page (" + rows.map((r) => r.text).join(" | ") + ")");
    const item = (start: string) => { const r = rows.find((x) => x.text.startsWith(start)); assert.ok(r, name + ": an item starting " + JSON.stringify(start)); return r!; };
    for (const start of ["open task", "done task", "numbered task", "loose task", "enabled first", "raw first", "an author item"]) {
      const r = item(start), cell = name + " " + JSON.stringify(start);
      assert.equal(r.stamped, true, cell + ": a task item");
      assert.equal(r.bullet, "none", cell + ": no bullet beside the box");
      assert.ok(r.input && r.input.disabled, cell + ": the disabled box survived the sanitize");
      assert.ok(r.input!.marginLeft < -13, cell + ": the box is pulled into the gutter (" + r.input!.marginLeft + ")");
      assert.ok(r.input!.left < 0, cell + ": the box sits before the item's edge, where the bullet was (" + r.input!.left + ")");
      assert.equal(r.input!.scheme, "dark", cell + ": drawn for the page's scheme (no theme class here: dark)");
    }
    assert.equal(item("done task").input!.checked, true, name + ": a checked box stays checked");
    // an author's enabled box is made inert by the sanitizer (md-sanitize.ts keeps marked's checkbox as the one control a note
    // carries, disabled), so by the time the stamp runs it is a disabled box opening its item, and a task item like the rest
    assert.equal(item("enabled first").input!.disabled, true, name + ": the sanitizer disabled the author's enabled box before the stamp");
    for (const start of ["plain item", "text then", "bold then", "loose text then"]) {
      const r = item(start), cell = name + " " + JSON.stringify(start);
      assert.equal(r.stamped, false, cell + ": not a task item");
      assert.equal(r.bullet, "disc", cell + ": the bullet stays");
      if (start !== "plain item") {
        assert.ok(r.input, cell + ": the sanitize kept the author's box");
        assert.ok(r.input!.marginLeft >= 0 && r.input!.left >= 0, cell + ": the box keeps the control's own margin, inside the item, not pulled into the gutter (" + r.input!.marginLeft + ", " + r.input!.left + ")");
        assert.ok(r.input!.left > 20, cell + ": the box sits after the item's text, where the file put it (" + r.input!.left + ")");
      }
    }
  }
});
