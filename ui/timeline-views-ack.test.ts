// THE TAGS DIALOG'S WRITES ARE ACKNOWLEDGED (the user 2026-09-05, who lost a batch of renames and
// assignments made in "Manage tags"). Before: every gesture posted the WHOLE views blob built from
// the dialog's own un-echoed optimistic copy, so a burst carried the pre-burst `at` stamp; the
// kernel's stale-writer guard refused the second edit to a tag (a create, then typing its name)
// against the client's OWN first write, told only stderr, and the client — which cleared its
// optimistic copy on an exact echo match or after THREE frames — kept re-posting the refused copy
// until the user paused, when the dialog snapped to the store: "tag N", no members.
// Now: tag gestures post TARGETED ops ({op, name, …, writeId}) the kernel applies by name and
// answers on the same socket ({tagEditAck, writeId, ok, views}); lens and order edits keep the
// whole-blob post, answered by viewsAck. The optimistic copy clears on the ACK (an event), never
// on a frame count; a refusal reverts it at once and shows the reason in the dialog.
// EXECUTED over the house fake-DOM shim with the real TimelinePanel: the dialog is opened, the
// [+ New tag] row clicked, the name typed — the exact gestures that were lost. Synthetic ids only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
// the real federation router, for the test that drives this panel behind it; its module-tail bootstrap runs only
// with a window AND a document defined at import time, and this file defines its fake DOM after its imports
import { FederationManager } from "./webview/federation";

function makeNode(tag: string): any {
  const n: any = {
    tag, _attrs: {}, children: [] as any[], style: {}, dataset: {}, _text: "", parentNode: null, value: "",
    // the real DOM's setter REPLACES the children; the builders clear a container with `textContent = ''`
    // before repainting it, so a fake that kept the children let post-rebuild assertions match stale nodes
    get textContent() { return this._text; },
    set textContent(v: any) { this._text = v == null ? "" : String(v); for (const c of this.children) c.parentNode = null; this.children.length = 0; },
    classList: { _s: new Set<string>(), add(...a: string[]) { a.forEach((c) => this._s.add(c)); },
      remove(...a: string[]) { a.forEach((c) => this._s.delete(c)); },
      toggle(c: string, f?: boolean) { f ? this._s.add(c) : this._s.delete(c); }, contains(c: string) { return this._s.has(c); } },
    setAttribute(k: string, v: any) { this._attrs[k] = v; }, getAttribute(k: string) { return this._attrs[k]; },
    setAttributeNS(_n: any, k: string, v: any) { this._attrs[k] = v; }, removeAttribute(k: string) { delete this._attrs[k]; },
    appendChild(c: any) {
      if (c.parentNode) { const i = c.parentNode.children.indexOf(c); if (i >= 0) c.parentNode.children.splice(i, 1); }
      c.parentNode = n; this.children.push(c); return c;
    },
    insertBefore(c: any, ref: any) { c.parentNode = n; const i = this.children.indexOf(ref); i < 0 ? this.children.push(c) : this.children.splice(i, 0, c); return c; },
    removeChild(c: any) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; },
    get firstChild() { return this.children[0] || null; },
    remove() { if (n.parentNode) n.parentNode.removeChild(n); },
    _listeners: {} as any,
    addEventListener(t: string, fn: any) { n._listeners[t] = fn; }, removeEventListener(t: string) { delete n._listeners[t]; },
    setPointerCapture() {}, releasePointerCapture() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    getBoundingClientRect() { return n._rect || { width: 200, height: 20, left: 0, top: 0, right: 200, bottom: 20 }; },
    closest() { return null; },
    // focus and caret are OBSERVABLE (the rename-draft assertions read them): focus records the active
    // element on the fake document; select() and setSelectionRange() record the selection
    focus() { g.document.activeElement = n; }, select() { n._sel = "all"; n.selectionStart = 0; n.selectionEnd = String(n.value || "").length; },
    setSelectionRange(a: number, b: number) { n._sel = [a, b]; n.selectionStart = a; n.selectionEnd = b; },
    selectionStart: 0, selectionEnd: 0,
    createEl(t: string, o: any) { const e = makeNode(t); if (o && o.cls) e.classList.add(o.cls); if (o && o.text) e.textContent = o.text; this.appendChild(e); return e; },
    createDiv(o: any) { return this.createEl("div", o); }, createSpan(o: any) { return this.createEl("span", o); },
  };
  return n;
}
const g: any = global;
g.document = {
  createElement(t: string) { return t === "canvas" ? { getContext() { return { font: "", measureText(s: string) { return { width: (s ? s.length : 0) * 6 }; } }; } } : makeNode(t); },
  createElementNS(_n: any, t: string) { return makeNode(t); },
  createTextNode(text: string) { const n = makeNode("#text"); n.textContent = text; return n; },
  body: makeNode("body"), documentElement: makeNode("html"), head: makeNode("head"),
  getElementById() { return null; },
  addEventListener() {}, removeEventListener() {},
};
g.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
g.getComputedStyle = () => ({ backgroundColor: "rgb(30,30,30)", fontFamily: "sans-serif" });
g.requestAnimationFrame = () => 0;
g.setTimeout = (fn: any) => { try { fn(); } catch { /* focus on a fake node */ } return 0; };
g.addEventListener = () => {}; g.removeEventListener = () => {};
g.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
g.window = g;
g.innerWidth = 1400; g.innerHeight = 800;

// the two host bridges, recording every post: whole-blob writes (views, writeId) and targeted edits
// (writeId, edit) — recorded flat as {writeId, ...edit} for the assertions
const posted: any[] = [];
g.__rompTimelineSetViews = (v: any, writeId: string, edited: string[]) => posted.push({ kind: "views", v: JSON.parse(JSON.stringify(v)), writeId, edited });
g.__rompTimelineTagEdit = (writeId: string, e: any) => posted.push({ kind: "tag", e: Object.assign({ writeId }, JSON.parse(JSON.stringify(e))) });

const VIEW_PATH = path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js");
const SRC = fs.readFileSync(VIEW_PATH, "utf8");
const { TimelinePanel, viewTagUnion } = createRequire(__filename)(VIEW_PATH);

const now = 1_781_000_000;
const SID1 = "11111111-2222-3333-4444-555555555501";
const SID2 = "11111111-2222-3333-4444-555555555502";
const PALETTE = ["#1EA1EB", "#54B204", "#4EA8A9", "#DD42FF", "#E87221"];
const sess = (id: string, name: string, color: string) => ({
  id, name, color, state: "working", live: true, model: "Opus", effort: "high",
  context: 40, since: now - 60, awaiting: [], compacting: [], pendingMail: 0, compactions: [], faded: false, stale: false,
});
const actives = { chat: { all: true }, outline: { all: true }, timeline: { all: true } };
// the kernel's last echo: one stamped tag, the store's `at`, and its write sequence (`seq` — every
// frame and ack carries the blob's own; a client adopts a blob only when its seq is at least the held one)
const S0 = { active: "all", actives, at: 100, seq: 1000, tags: [{ id: "gA", name: "web", color: "#3b82f6", members: [SID1], mtime: 100 }] };
const copy = (v: any) => JSON.parse(JSON.stringify(v));

// a drawn panel, connected to a kernel that announced the `tagEdit` capability at `ready` (the caps
// frame, as the kernel sends it); `views` overrides the first frame's blob (a seq-less one = a legacy kernel)
function drawnPanel(views: any = S0, caps: string[] = ["tagEdit"]): any {
  posted.length = 0;
  const panel = new TimelinePanel(makeNode("div"));
  panel.update({
    now, sessions: [sess(SID1, "web", "#f7768e"), sess(SID2, "api", "#7aa2f7")],
    turns: { [SID1]: [{ id: "t1", start: now - 400, end: now - 100, prompt: "do the thing", tid: "f1", mids: [] }] },
    messages: [], judging: [], views: copy(views), palette: PALETTE.slice(),
  });
  // the caps frame names the seq of the views blob the connect push above served (viewsSeq; null for a seq-less one)
  panel.setCaps({ type: "caps", caps, viewsSeq: typeof views.seq === "number" ? views.seq : null });
  return panel;
}
// a kernel push carrying a views blob (the two-message path's skeleton frame)
function frame(panel: any, views: any) {
  panel.update({ now, sessions: [sess(SID1, "web", "#f7768e"), sess(SID2, "api", "#7aa2f7")], views: copy(views) });
}
function walk(x: any, out: any[] = []): any[] { for (const c of x.children || []) { out.push(c); walk(c, out); } return out; }
const textOf = (n: any): string => (n.textContent || "") + (n.children || []).map(textOf).join("");
function clickNewTag(panel: any) {
  const btn = walk(panel._viewsDialog).find((n) => n.textContent === "+ New tag");
  assert.ok(btn, "the [+ New tag] row renders");
  btn._listeners.click();
}
function findNameInput(panel: any) { return walk(panel._viewsDialog).find((n) => n.tag === "input" && n._listeners.change); }
function nameInput(panel: any) {
  const inp = findNameInput(panel);
  assert.ok(inp, "a new tag opens straight into its rename input");
  return inp;
}
function tagOps() { return posted.filter((p) => p.kind === "tag").map((p) => p.e); }
// the kernel answers a [+ New tag] create: the minted tid and default name, and the blob with the new row
const NEW_TID = "g7";
function ackCreate(panel: any, seq = 1001, name = "tag 1", color = "#1EA1EB") {
  const S1 = copy(S0); S1.at = 113; S1.seq = seq; S1.tags.push({ id: NEW_TID, name, color, members: [], mtime: 113 });
  const c = tagOps()[0];
  panel.viewsAck({ type: "tagEditAck", writeId: c.writeId, ok: true, seq, tid: NEW_TID, name, views: copy(S1) });
  return S1;
}

test("harness: setting textContent replaces the children, as the real DOM does — a rebuilt container holds no stale nodes", () => {
  const box = makeNode("div");
  const a = box.createSpan({ text: "old" }); box.createDiv({ text: "older" });
  assert.equal(walk(box).length, 2);
  box.textContent = "";
  assert.deepEqual(walk(box), [], "the builders clear a container this way before repainting it");
  assert.equal(a.parentNode, null, "…and the removed nodes no longer point at it");
  box.createSpan({ text: "new" });
  assert.deepEqual(walk(box).map((n) => n.textContent), ["new"], "a find() over the walk can only match the current build");
  box.textContent = "plain";
  assert.equal(box.textContent, "plain"); assert.deepEqual(walk(box), []);
});

test("executed: [+ New tag] posts a create the KERNEL names and ids; the ack's tid opens the rename input; typing posts a rename by tid", () => {
  const panel = drawnPanel();
  panel._openViewsDialog(null);
  clickNewTag(panel);
  assert.equal(posted.length, 1, "one post for the create");
  const c = posted[0];
  assert.equal(c.kind, "tag", "a tag gesture is a targeted edit, not a whole-blob write");
  assert.equal(c.e.op, "create");
  assert.equal(c.e.color, "#1EA1EB", "the first unused palette colour, as before");
  assert.equal(c.e.name, undefined, "no dialog-minted name: a 'tag N' from a row count collided with a leftover default-named tag");
  assert.equal(c.e.id, undefined, "no dialog-minted id either — the kernel mints it");
  assert.ok(typeof c.e.writeId === "string" && c.e.writeId, "every write carries a writeId the ack names");
  assert.equal(panel._pendingViews, null, "nothing is drawn optimistically — there is no id to draw it under");
  assert.equal(findNameInput(panel), undefined, "…and no rename input yet");
  const S1 = ackCreate(panel);
  assert.equal(panel._tagEditorFor, NEW_TID, "the ack's tid is where the rename input opens");
  assert.deepEqual(panel._views, S1);
  const inp = nameInput(panel);
  assert.equal(inp.value, "tag 1", "the kernel's default name, ready to be typed over");
  // the user types the name before ANY frame — the lost gesture
  inp.value = "notes-api"; inp._listeners.change();
  const ops = tagOps();
  assert.equal(ops.length, 2);
  assert.deepEqual([ops[1].op, ops[1].tid, ops[1].newName], ["rename", NEW_TID, "notes-api"],
    "the rename addresses the tag by its stored id — no `at`, nothing for the guard to judge stale, and no name to mistake");
  assert.equal(ops[1].name, undefined, "no name field on a rename");
  assert.notEqual(ops[1].writeId, ops[0].writeId);
  assert.equal(panel._curViews().tags.find((t: any) => t.id === NEW_TID).name, "notes-api", "the optimistic copy shows the typed name at once");
  assert.equal(posted.filter((p) => p.kind === "views").length, 0, "no whole-blob write anywhere in the burst");
});

test("executed: a rename posted before any frame SURVIVES — frames never yield the optimistic copy (no frame count)", () => {
  const panel = drawnPanel();
  panel._openViewsDialog(null);
  clickNewTag(panel);
  const S1 = ackCreate(panel);
  const inp = nameInput(panel); inp.value = "notes-api"; inp._listeners.change();
  // the pusher's frames of the CREATE land (the store: "tag 1", stamped, `at` moved — the rename not yet in them)
  frame(panel, S1);
  assert.ok(panel._pendingViews, "a frame that does not carry the rename says nothing about it → the pending copy stays");
  assert.equal(panel._curViews().tags.find((t: any) => t.id === NEW_TID).name, "notes-api");
  for (let i = 0; i < 6; i++) { const e = copy(S1); e.at = 120 + i; frame(panel, e); }
  assert.ok(panel._pendingViews, "six silent frames later the user's rename is STILL showing — nothing counts frames");
  assert.equal(panel._curViews().tags.find((t: any) => t.id === NEW_TID).name, "notes-api");
});

test("executed: the pending copy clears on the ACK — the rename's ack settles it, and the dialog reads the kernel's blob", () => {
  const panel = drawnPanel();
  panel._openViewsDialog(null);
  clickNewTag(panel);
  const S1 = ackCreate(panel);
  assert.equal(panel._pendingViews, null, "the create had no optimistic copy; its ack leaves nothing pending");
  const inp = nameInput(panel); inp.value = "notes-api"; inp._listeners.change();
  const w2 = tagOps()[1].writeId;
  assert.ok(panel._pendingViews, "the rename is in flight → its optimistic copy holds");
  const S2 = copy(S1); S2.at = 114; S2.seq = 1002; S2.tags[1].name = "notes-api"; S2.tags[1].mtime = 114;
  panel.viewsAck({ type: "tagEditAck", writeId: w2, ok: true, seq: 1002, tid: NEW_TID, name: "notes-api", views: copy(S2) });
  assert.equal(panel._pendingViews, null, "the last in-flight write's ack clears the pending copy");
  assert.deepEqual(panel._curViews(), S2, "…and the dialog now reads the kernel's blob, which holds the name");
  assert.equal(panel._viewsWrites.length, 0, "nothing left in flight");
});

test("executed: a refusal reverts the optimistic copy at once and shows the reason in the open dialog; the next gesture still addresses the SAME tag", () => {
  const panel = drawnPanel();
  panel._openViewsDialog(null);
  clickNewTag(panel);
  const S1 = ackCreate(panel);
  const inp = nameInput(panel); inp.value = "web"; inp._listeners.change();   // a name that already exists
  assert.equal(panel._curViews().tags.find((t: any) => t.id === NEW_TID).name, "web", "optimistic until the kernel rules");
  const why = 'a tag named "web" already exists';
  panel.viewsAck({ type: "tagEditAck", writeId: tagOps()[1].writeId, ok: false, error: why, tid: NEW_TID, seq: 1001, views: copy(S1) });
  assert.equal(panel._pendingViews, null, "the refused copy is dropped at once");
  assert.equal(panel._curViews().tags.find((t: any) => t.id === NEW_TID).name, "tag 1", "the dialog shows the store's truth");
  assert.deepEqual(panel._tagEditErr, { host: "", name: "tag 1", error: why }, "the refusal names the tag and the reason");
  const shown = walk(panel._viewsDialog).map(textOf).find((s) => s.startsWith("⚠ "));
  assert.ok(shown && shown.startsWith("⚠ " + why), "…in the dialog, rebuilt in place (the tagEditFailed door's rendering, dismiss ✕ beside it)");
  // A recolor in the refusal window addresses the tag by ID — never by the name the
  // rename would have given it (which is the OTHER tag's name)
  const u = viewTagUnion(panel._curViews()).find((x: any) => x.ids.includes(NEW_TID));
  panel._editTagUnion(u, { color: "#DD42FF" });
  const rc = tagOps()[2];
  assert.deepEqual([rc.op, rc.tid, rc.color, rc.name], ["recolor", NEW_TID, "#DD42FF", undefined]);
  assert.equal(panel._curViews().tags.find((t: any) => t.id === "gA").color, "#3b82f6", "\"web\" is untouched in the copy too");
});

// ── DIALOG BEHAVIOUR (the 2026-09-05 review)
test("executed: a refusal's rebuild preserves another row's in-progress rename — text, caret and focus", () => {
  const S = copy(S0); S.tags.push({ id: "gB", name: "api", color: "#54B204", members: [SID2], mtime: 100 });
  const panel = drawnPanel(S);
  panel._openViewsDialog(null);
  // the user opens the rename input on "api" and types a new name, caret mid-word
  const renameBtn = walk(panel._viewsDialog).filter((n) => n.textContent === "rename")[1];
  assert.ok(renameBtn, "the api row's rename action");
  renameBtn._listeners.click();
  assert.equal(panel._tagEditorFor, "gB");
  let inp = nameInput(panel);
  assert.equal(inp.value, "api");
  assert.equal(g.document.activeElement, inp, "the input took focus on open");
  assert.equal(inp._sel, "all", "…with the stored name selected, ready to be typed over");
  inp.value = "api-v2"; inp._listeners.input(); inp.setSelectionRange(4, 4);
  // meanwhile a recolor of "web" (another row) posted earlier is REFUSED — the dialog repaints
  panel._editTagUnion(viewTagUnion(panel._curViews()).find((x: any) => x.name === "web"), { color: "#DD42FF" });
  panel.viewsAck({ type: "tagEditAck", writeId: tagOps()[0].writeId, ok: false, tid: "gA", seq: 1000, error: "that tag no longer exists — it may have been deleted from another dashboard", views: copy(S) });
  const inp2 = nameInput(panel);
  assert.notEqual(inp2, inp, "the dialog WAS rebuilt (a fresh input)");
  assert.equal(inp2.value, "api-v2", "the typed text survives the rebuild");
  assert.equal(g.document.activeElement, inp2, "focus is back in the input");
  assert.deepEqual(inp2._sel, [4, 4], "the caret is where it was — not select-all, which the next keystroke would replace");
  assert.equal(panel._tagEditorFor, "gB", "the editor stays on the same row");
  // finishing the rename posts it by tid and clears the draft; the next open starts from the stored name
  inp2._listeners.change();
  const ren = tagOps()[1];
  assert.deepEqual([ren.op, ren.tid, ren.newName], ["rename", "gB", "api-v2"]);
  assert.equal(panel._tagRenameDraft, null);
  // Escape drops a draft too
  walk(panel._viewsDialog).filter((n) => n.textContent === "rename")[1]._listeners.click();
  inp = nameInput(panel); inp.value = "zzz"; inp._listeners.input();
  inp._listeners.keydown({ key: "Escape" });
  assert.equal(panel._tagRenameDraft, null);
  assert.equal(findNameInput(panel), undefined, "the editor closed");
});

test("executed: the lane gear menu shows a refusal inline, repaints on it, and ✕ dismisses it", () => {
  const panel = drawnPanel();
  const s = panel.data.sessions.find((x: any) => x.id === SID2);
  const anchor = makeNode("g"); anchor._rect = { left: 40, top: 60, right: 60, bottom: 76, width: 20, height: 16 };
  panel._openLaneMenu(s, anchor);
  assert.ok(panel._laneMenu, "the gear menu is open");
  assert.equal(typeof panel._laneMenu._build, "function", "…and exposes its repaint");
  const notices = () => walk(panel._laneMenu).map((n) => n.textContent as string).filter((t) => t.startsWith("⚠ "));   // the notice spans themselves
  assert.deepEqual(notices(), [], "no notice at rest");
  // a tag gesture made FROM the menu (the [+] join option) is refused by the kernel
  const plus = walk(panel._laneMenu).find((n) => n.textContent === "+" && n._attrs.title === "add a tag");
  assert.ok(plus, "the menu's [+]");
  plus._listeners.click({ stopPropagation() {} });
  const opt = walk(panel._laneMenu).find((n) => n.textContent === "web");
  assert.ok(opt, "the join option for the tag this session lacks");
  opt._listeners.click();
  const w = tagOps()[0];
  assert.deepEqual([w.op, w.tid, w.sids], ["addMember", "gA", [SID2]]);
  const why = "that tag no longer exists — it may have been deleted from another dashboard";
  panel.viewsAck({ type: "tagEditAck", writeId: w.writeId, ok: false, tid: "gA", seq: 1000, error: why, views: copy(S0) });
  const shown = notices();
  assert.equal(shown.length, 1, "the refusal shows in the open menu — the dialog is not the only surface that edits tags");
  assert.ok(shown[0].startsWith("⚠ " + why), shown[0]);
  assert.deepEqual(panel._curViews().tags[0].members, [SID1], "the optimistic add reverted");
  // ✕ dismisses it and repaints the menu without it
  const x = walk(panel._laneMenu).find((n) => n.textContent === "✕" && n.parentNode && textOf(n.parentNode).startsWith("⚠ "));
  assert.ok(x, "the notice has its dismiss");
  x._listeners.click({ stopPropagation() {} });
  assert.equal(panel._tagEditErr, null);
  assert.deepEqual(notices(), [], "gone");
  // a remote refusal (tagEditFailed) lands there too
  panel.tagEditFailed({ type: "tagEditFailed", host: "TESTHOST", name: "web", error: "refused" });
  assert.deepEqual(notices(), ["⚠ TESTHOST: refused"]);
  panel._closeLaneMenu();
  assert.equal(panel._laneMenu, null);
});

test("executed: membership edits are targeted too — the join menu's option adds by tid; its new-tag input is ONE create with members, no client id", () => {
  const panel = drawnPanel();
  const box = makeNode("div");
  let rebuilt = 0;
  panel._tagJoinMenu(box, [SID2], () => rebuilt++);
  const opt = walk(box).find((n) => n.textContent === "web");
  opt._listeners.click();
  assert.deepEqual([tagOps()[0].op, tagOps()[0].tid, tagOps()[0].sids], ["addMember", "gA", [SID2]]);
  assert.equal(rebuilt, 1);
  assert.deepEqual(panel._curViews().tags[0].members.slice().sort(), [SID1, SID2].sort(), "optimistic at once");
  const box2 = makeNode("div");
  panel._tagJoinMenu(box2, [SID1, SID2], () => rebuilt++);
  const ni = walk(box2).find((n) => n.tag === "input");
  ni.value = "qa"; ni._listeners.keydown({ key: "Enter" });
  const c = tagOps()[1];
  assert.deepEqual([c.op, c.name, c.sids, c.id, c.tid], ["create", "qa", [SID1, SID2], undefined, undefined], "one op: the tag and its first members together; the kernel mints the id");
  const drawn = panel._curViews().tags.find((t: any) => t.name === "qa");
  assert.match(drawn.id, /^pending-/, "the optimistic row wears a placeholder id until the ack's blob replaces it");
  // a chip's ✕ (remove-everywhere) and the row actions ride the same door, by tid
  const u = viewTagUnion(panel._curViews()).find((x: any) => x.name === "web");
  panel._editTagUnion(u, { remove: [SID2] });
  panel._editTagUnion(u, { color: "#DD42FF" });
  panel._editTagUnion(u, { delete: true });
  assert.deepEqual(tagOps().slice(2).map((e) => [e.op, e.tid, e.sids || e.color || null, e.name]),
    [["removeMember", "gA", [SID2], undefined], ["recolor", "gA", "#DD42FF", undefined], ["delete", "gA", null, undefined]]);
  assert.equal(posted.filter((p) => p.kind === "views").length, 0, "still no whole-blob write for any tag gesture");
});

test("executed: lens and order edits keep the whole-blob post, now with a writeId, and the viewsAck settles or reverts them", () => {
  const panel = drawnPanel();
  const nv = copy(S0); nv.actives = Object.assign({}, nv.actives, { timeline: { tags: ["web"] } });
  panel._setViews(nv);
  assert.equal(posted.length, 1);
  assert.equal(posted[0].kind, "views", "a lens edit is still the whole blob (the kernel owns no lens op)");
  assert.deepEqual(posted[0].v.actives.timeline, { tags: ["web"] });
  assert.ok(typeof posted[0].writeId === "string" && posted[0].writeId, "…carrying its writeId");
  assert.deepEqual(posted[0].edited, [], "…and naming no edited tag: a refusal on a stale, untouched tag is acked ok, no notice");
  assert.ok(panel._pendingViews);
  frame(panel, S0); frame(panel, S0); frame(panel, S0); frame(panel, S0);
  assert.ok(panel._pendingViews, "stale frames never yield it");
  const S1 = copy(nv); S1.at = 130;
  panel.viewsAck({ type: "viewsAck", writeId: posted[0].writeId, ok: true, refused: [], views: copy(S1) });
  assert.equal(panel._pendingViews, null, "the viewsAck clears it");
  assert.deepEqual(panel._views, S1);
  // a genuinely stale whole-blob write (another dashboard edited `web` meanwhile): the guard kept
  // the store's copy and the ack says so; the dialog shows the kernel's blob and the reason
  const nv2 = copy(S1); nv2.tags[0].members = [];
  panel._setViews(nv2);
  const S2 = copy(S1); S2.at = 140; S2.tags[0].members = [SID1, SID2]; S2.tags[0].mtime = 140;
  const reason = "your copy predates a newer edit to it, so your change was not applied and the newer state was kept";
  panel._viewsDialog = makeNode("div"); let rebuilt = 0; panel._viewsDialogBuild = () => rebuilt++;
  panel.viewsAck({ type: "viewsAck", writeId: posted[1].writeId, ok: false, refused: [{ tid: "gA", name: "web", reason }], error: '"web": ' + reason, views: copy(S2) });
  assert.equal(panel._pendingViews, null);
  assert.deepEqual(panel._curViews(), S2, "reverted to the store's blob");
  assert.deepEqual(panel._tagEditErr, { host: "", name: "web", error: '"web": ' + reason }, "the kernel's line: the name once, what was refused, what was kept");
  assert.equal(rebuilt, 1, "the open dialog repaints with the refusal");
  // ok WITH refusals listed — a stale copy of a tag this write did not edit: settled, no notice
  panel._tagEditErr = null;
  panel._setViews(copy(S2));
  const S3 = copy(S2); S3.seq = 1003;
  panel.viewsAck({ type: "viewsAck", writeId: posted[2].writeId, ok: true, refused: [{ tid: "gA", name: "web", reason }], views: copy(S3) });
  assert.equal(panel._pendingViews, null);
  assert.equal(panel._tagEditErr, null, "nothing the user did was refused, so nothing is said");
});

// The 2026-09-05 review (a coverage gap): the door bounds a whole-blob write's refusal rows to 64 plus ONE
// nameless summary row whose reason counts the rest (`more`, `moreEdited`). The chat pane's rendering of that row is
// pinned in views-writes.test.ts (ackOutcome); the dialog's is here, on the real panel: the kernel's bounded `error`
// line when it comes, the rows' reasons joined without it, and a nameless row alone as its reason — never a name read
// off a row that has none, and never an exception before the notice is set and the dialog repainted.
test("executed: a refusal's nameless summary row renders in the dialog as its reason alone — with the kernel's error line, without it, and as the only row", () => {
  const panel = drawnPanel();
  const bound = 'a write is read to 64 tags; "qa" lay past that bound and was not read, so the stored copy was kept';
  const more = "3 more entries past the 64-tag read bound were not read (1 of them this write edited)";
  const summary = { reason: more, more: 3, moreEdited: 1 };
  panel._viewsDialog = makeNode("div"); let rebuilt = 0; panel._viewsDialogBuild = () => rebuilt++;
  // the kernel's shape: the bounded `error` names each refused tag once; the rows carry the summary
  panel._setLens({ tagOrder: ["web"] });
  panel.viewsAck({ type: "viewsAck", writeId: posted[0].writeId, ok: false, refused: [{ tid: "g9", name: "qa", reason: bound }, summary], error: '"qa": ' + bound + "; " + more, views: copy(S0) });
  assert.deepEqual(panel._tagEditErr, { host: "", name: "qa", error: '"qa": ' + bound + "; " + more }, "the kernel's line stands; the summary row adds no name");
  assert.equal(rebuilt, 1, "the open dialog repaints with it");
  // no `error` line: the rows' reasons, joined — the nameless row's after the named one's
  panel._setLens({ tagOrder: ["web"] });
  panel.viewsAck({ type: "viewsAck", writeId: posted[1].writeId, ok: false, refused: [{ tid: "g9", name: "qa", reason: bound }, summary], views: copy(S0) });
  assert.deepEqual(panel._tagEditErr, { host: "", name: "qa", error: bound + "; " + more });
  // the nameless row alone: its reason, and no name
  panel._setLens({ tagOrder: ["web"] });
  panel.viewsAck({ type: "viewsAck", writeId: posted[2].writeId, ok: false, refused: [summary], views: copy(S0) });
  assert.deepEqual(panel._tagEditErr, { host: "", name: "", error: more });
  assert.equal(panel._pendingViews, null, "each refusal reverted its write");
  assert.deepEqual(panel._viewsWrites, []);
  assert.equal(rebuilt, 3);
});

// ── ORDERING (the 2026-09-05 review): the store's write sequence decides which blob is
// newer, never the order the socket delivered them in. The pusher builds frames from a warmed cache that
// can predate a write whose ack already arrived; federation re-emits stored blobs; a net-zero burst leaves
// frames EQUAL to the copy while its writes are still in flight. The seq is exact where the exact-echo
// heuristic guessed.
test("executed: a frame carrying an OLDER write sequence than the ack's blob is ignored — store order, not wire order", () => {
  const panel = drawnPanel();                       // holds S0 (seq 1000)
  const warned: string[] = []; const cw = console.warn; console.warn = (s: any) => { warned.push(String(s)); };
  try {
    const S2 = copy(S0); S2.seq = 1002; S2.at = 120; S2.tags[0].members = [SID1, SID2]; S2.tags[0].mtime = 120;
    panel.viewsAck({ type: "tagEditAck", writeId: "w-x", ok: true, seq: 1002, views: copy(S2) });
    assert.equal(panel._views.seq, 1002);
    const stale = copy(S0); stale.seq = 1001;       // the pusher's warmed cache: built before the write, delivered after the ack
    frame(panel, stale);
    assert.equal(panel._views.seq, 1002, "the older blob does not replace the newer one");
    assert.deepEqual(panel._curViews().tags[0].members, [SID1, SID2], "the dialog keeps showing the write");
    frame(panel, stale);
    assert.equal(warned.length, 1, "the ignored blob is logged once per page, not once per frame");
    assert.match(warned[0], /older than the one held/);
    const S3 = copy(S2); S3.seq = 1003; frame(panel, S3);
    assert.equal(panel._views.seq, 1003, "a newer frame is adopted as before");
    const legacy = copy(S3); delete legacy.seq;
    frame(panel, legacy);
    assert.equal(panel._views.seq, undefined, "a blob without a seq (a kernel from before the stamp) still adopts — nothing is gated on a field it never sends");
  } finally { console.warn = cw; }
});

test("executed: a NET-ZERO burst (add, then remove) is not cleared early by frames that happen to equal the copy", () => {
  const panel = drawnPanel();
  const u = viewTagUnion(panel._curViews()).find((x: any) => x.name === "web");
  panel._editTagUnion(u, { add: [SID2] });
  const u2 = viewTagUnion(panel._curViews()).find((x: any) => x.name === "web");
  panel._editTagUnion(u2, { remove: [SID2] });
  assert.equal(panel._viewsWrites.length, 2, "two writes in flight");
  assert.deepEqual(panel._curViews().tags[0].members, [SID1], "the copy is back to the pre-burst state — net zero");
  frame(panel, copy(S0));                           // a frame equal to the copy: seq 1000, built before either write
  assert.equal(panel._viewsWrites.length, 2, "an exact match is NOT a write's echo when the blob carries a seq — nothing clears");
  assert.ok(panel._pendingViews);
  const S1 = copy(S0); S1.seq = 1001; S1.tags[0].members = [SID1, SID2];   // the add landed on the kernel
  frame(panel, S1);
  assert.deepEqual(panel._curViews().tags[0].members, [SID1], "the half-applied state never shows: the copy holds until the acks");
  const [w1, w2] = panel._viewsWrites.map((w: any) => w.id);
  panel.viewsAck({ type: "tagEditAck", writeId: w1, ok: true, seq: 1001, views: copy(S1) });
  assert.ok(panel._pendingViews, "the first ack leaves the second write pending");
  const S2 = copy(S0); S2.seq = 1002;
  panel.viewsAck({ type: "tagEditAck", writeId: w2, ok: true, seq: 1002, views: copy(S2) });
  assert.equal(panel._pendingViews, null, "the last ack settles the burst");
  assert.deepEqual(panel._curViews().tags[0].members, [SID1]);
});

test("executed: an ack whose blob a newer frame already overtook still settles its write, and does not roll the base back", () => {
  const panel = drawnPanel();
  const u = viewTagUnion(panel._curViews()).find((x: any) => x.name === "web");
  panel._editTagUnion(u, { color: "#DD42FF" });
  const w = panel._viewsWrites[0].id;
  const S2 = copy(S0); S2.seq = 1002; S2.tags[0].color = "#DD42FF"; S2.tags[0].members = [SID1, SID2];   // our recolor plus another dashboard's add
  frame(panel, S2);
  const S1 = copy(S0); S1.seq = 1001; S1.tags[0].color = "#DD42FF";
  panel.viewsAck({ type: "tagEditAck", writeId: w, ok: true, seq: 1001, views: copy(S1) });   // the ack, delivered after the newer frame
  assert.equal(panel._pendingViews, null, "the ack settles the write");
  assert.equal(panel._views.seq, 1002, "…without replacing the newer blob");
  assert.deepEqual(panel._curViews().tags[0].members, [SID1, SID2]);
});

test("executed: an ack for a write this page never made still refreshes the base and never strands a pending copy", () => {
  const panel = drawnPanel();
  const S1 = copy(S0); S1.at = 150;
  panel.viewsAck({ type: "viewsAck", writeId: "w-from-a-previous-load", ok: true, refused: [], views: copy(S1) });
  assert.deepEqual(panel._views, S1);
  assert.equal(panel._pendingViews, null);
});

test("executed: without the targeted-edit bridge (an Obsidian panel), a tag gesture falls back to the whole-blob write", () => {
  const panel = drawnPanel();
  const saved = g.__rompTimelineTagEdit;
  delete g.__rompTimelineTagEdit;
  try {
    const u = viewTagUnion(panel._curViews()).find((x: any) => x.name === "web");
    panel._editTagUnion(u, { rename: "site" });
    assert.equal(posted.length, 1);
    assert.equal(posted[0].kind, "views");
    assert.equal(posted[0].v.tags[0].name, "site");
  } finally {
    g.__rompTimelineTagEdit = saved;
  }
});

// ── CAPABILITY (the 2026-09-05 review): the kernel announces `tagEdit` at every `ready`;
// without it the panel takes the pre-cap path (the whole blob, reconciled by the legacy exact echo and
// three-frame yield, since no ack will come); an op the kernel does not know is answered unknownOp.
test("executed: against a kernel WITHOUT the tagEdit capability, a tag gesture posts the whole blob — the legacy path — and legacy frames settle it by exact echo or three silent frames", () => {
  const L0 = copy(S0); delete L0.seq;                    // an older kernel stamps no seq…
  const panel = drawnPanel(L0, []);                      // …and announces no tagEdit
  const u = viewTagUnion(panel._curViews()).find((x: any) => x.name === "web");
  panel._editTagUnion(u, { rename: "site" });
  assert.equal(posted.length, 1);
  assert.equal(posted[0].kind, "views", "no capability → the pre-cap whole-blob write");
  assert.equal(posted[0].v.tags[0].name, "site");
  assert.ok(typeof posted[0].writeId === "string", "the writeId rides anyway — an older kernel ignores it");
  assert.deepEqual(posted[0].edited, ["gA"], "…and the tag it changed, for a kernel new enough to read it");
  // LEGACY reconciliation, for this path only: a frame that echoes the edit clears it…
  const echo = copy(L0); echo.tags[0].name = "site";
  frame(panel, echo);
  assert.equal(panel._pendingViews, null, "the exact echo clears the copy (legacy)");
  // …and three silent seq-less frames yield an unechoed one (with no ack coming, it would pin forever)
  panel._editTagUnion(viewTagUnion(panel._curViews()).find((x: any) => x.name === "site"), { color: "#DD42FF" });
  assert.ok(panel._pendingViews);
  frame(panel, echo); frame(panel, echo);
  assert.ok(panel._pendingViews, "two silent frames: still holding");
  frame(panel, echo);
  assert.equal(panel._pendingViews, null, "the third yields — the legacy kernel's only clear");
  // [+ New tag] on the legacy path: a whole-blob create the dialog names and ids, the row opened for renaming
  panel._openViewsDialog(null);
  clickNewTag(panel);
  const last = posted[posted.length - 1];
  assert.equal(last.kind, "views");
  const added = last.v.tags.find((t: any) => t.name === "tag 1");
  assert.ok(added && /^g[0-9a-z]+$/.test(added.id), "legacy: the dialog mints the id and the lowest free 'tag N'");
  assert.deepEqual(last.edited, [added.id], "…names it as edited (a kernel that reads `edited` takes an unnamed unknown tag for a stale re-creation)");
  assert.equal(panel._tagEditorFor, added.id, "…and opens the rename input on it");
  // a New tag when the capability is present posts the targeted create (the same panel, the cap arriving)
  panel.setCaps({ type: "caps", caps: ["tagEdit"] });
  clickNewTag(panel);
  assert.equal(posted[posted.length - 1].kind, "tag");
});

test("executed: an unknownOp reply surfaces as a refusal, withdraws the capability, and the next gesture takes the older path", () => {
  const panel = drawnPanel();
  panel._openViewsDialog(null);
  const u = viewTagUnion(panel._curViews()).find((x: any) => x.name === "web");
  panel._editTagUnion(u, { rename: "site" });
  const w = tagOps()[0].writeId;
  assert.equal(panel._curViews().tags[0].name, "site", "optimistic");
  panel.unknownOp({ type: "unknownOp", op: "tagEdit", writeId: w });
  assert.equal(panel._pendingViews, null, "the copy reverts — the kernel did nothing with the write");
  assert.equal(panel._curViews().tags[0].name, "web");
  assert.match(panel._tagEditErr.error, /does not know the tagEdit operation/, "…and the dialog says why");
  assert.equal(panel._caps.has("tagEdit"), false, "the capability is withdrawn");
  panel._editTagUnion(viewTagUnion(panel._curViews())[0], { color: "#DD42FF" });
  assert.equal(posted[posted.length - 1].kind, "views", "the next gesture is a whole-blob write that kernel does know");
  // an unknownOp for a write this page never made changes nothing but the cap
  const n = posted.length; const before = panel._pendingViews;
  panel.unknownOp({ type: "unknownOp", op: "somethingElse", writeId: "w-not-ours" });
  assert.equal(posted.length, n); assert.equal(panel._pendingViews, before);
});

test("executed: a lost ack — the caps frame the kernel sends at every ready (a re-established socket) drops what was in flight, says so, and the next frame is adopted", () => {
  const panel = drawnPanel();
  panel._openViewsDialog(null);
  const u = viewTagUnion(panel._curViews()).find((x: any) => x.name === "web");
  panel._editTagUnion(u, { rename: "site" });
  assert.equal(panel._viewsWrites.length, 1, "the rename is in flight");
  // the socket dropped between the write and its ack; the shim reconnected and re-sent `ready`; the
  // kernel answered with its caps — the ONE event that says the in-flight answer may never come
  panel.setCaps({ type: "caps", caps: ["tagEdit"] });
  assert.deepEqual(panel._viewsWrites, [], "nothing pins: the writes are unknowable and are dropped");
  assert.equal(panel._pendingViews, null, "the copy reverts to the store's truth");
  assert.equal(panel._curViews().tags[0].name, "web");
  assert.match(panel._tagEditErr.error, /re-established.*may not have landed/, "the user is told — never a fake success, never a silent revert");
  const shown = walk(panel._viewsDialog).map(textOf).find((s) => s.startsWith("⚠ "));
  assert.ok(shown, "…in the open dialog");
  const S2 = copy(S0); S2.seq = 1002; S2.tags[0].name = "site";      // the write HAD landed: the next frame shows it
  frame(panel, S2);
  assert.equal(panel._curViews().tags[0].name, "site", "the next frame is adopted as the truth");
  // a caps frame with nothing in flight (the page's own load) changes nothing but the caps
  const before = panel._tagEditErr;
  panel.setCaps({ type: "caps", caps: ["tagEdit"] });
  assert.equal(panel._tagEditErr, before);
});

// ── The 2026-09-05 review: the caps frame adopts the blob the gate last
// turned away when its viewsSeq (the seq of the blob the kernel's own connect push served) names it, and never
// opens the gate.
test("executed: a restored store lands on the caps frame itself — the connect push the restarted kernel serves under an older seq is turned away, then adopted on the caps frame that names it, with nothing to wait for", () => {
  const panel = drawnPanel();                       // the load: S0's frame (adopted), then the caps frame (nothing kept: inert)
  assert.equal(panel._views.seq, 1000, "the load-time caps frame leaves the held seq alone");
  const warned: string[] = []; const cw = console.warn; console.warn = (s: any) => { warned.push(String(s)); };
  try {
    // the kernel was down; timeline-views.json was restored from an older copy (seq 900) while this page held 1000;
    // the kernel restarted; the shim reconnected and re-sent `ready`: the kernel's connect push comes FIRST…
    const restored = copy(S0); restored.seq = 900; restored.tags[0].name = "site";
    frame(panel, restored);
    assert.equal(panel._views.seq, 1000, "…and the gate turns it away, as it must before the reconnect event");
    assert.equal(warned.length, 1);
    panel.setCaps({ type: "caps", caps: ["tagEdit"], viewsSeq: 900 });   // …then its caps frame, naming what that push served
    assert.equal(panel._views.seq, 900, "the caps frame adopts exactly the blob the gate turned away — no repost to wait for");
    assert.equal(panel._curViews().tags[0].name, "site", "the page shows the restored store at once");
    assert.equal(panel._tagEditErr, null, "nothing was in flight: nothing is said");
    frame(panel, Object.assign(copy(restored), { seq: 899 }));
    assert.equal(panel._views.seq, 900, "and the store's own order gates again from its seq");
    frame(panel, restored);
    assert.equal(panel._views.seq, 900);
  } finally { console.warn = cw; }
});

test("executed: a healthy reconnect keeps the gate — the connect push is adopted, the caps frame names it and adopts nothing, and a pusher frame built before a concurrent write is turned away whether it lands before or after the caps frame", () => {
  const panel = drawnPanel();
  const warned: string[] = []; const cw = console.warn; console.warn = (s: any) => { warned.push(String(s)); };
  try {
    // the socket dropped and came back on the same kernel; meanwhile another dashboard's write landed (seq 1001)
    const S1 = copy(S0); S1.seq = 1001; S1.tags[0].members = [SID1, SID2];
    frame(panel, S1);                                 // the connect push: current, adopted
    // the pusher thread's frame, built from its cache BEFORE that write and enqueued between the connect push and
    // the caps frame — the window an earlier fix left open: the client turns it away and keeps it…
    frame(panel, S0);
    assert.equal(panel._views.seq, 1001);
    assert.equal(warned.length, 1);
    panel.setCaps({ type: "caps", caps: ["tagEdit"], viewsSeq: 1001 });   // …and the caps frame names the connect push, not it
    assert.equal(panel._views.seq, 1001, "the kept frame's seq is not the one named: discarded, the gate stands");
    assert.deepEqual(panel._curViews().tags[0].members, [SID1, SID2], "no flap: the other dashboard's member never blinks out");
    frame(panel, S0);                                 // the same stale frame landing AFTER the caps frame instead
    assert.equal(panel._views.seq, 1001, "turned away by the gate, which never opened");
    frame(panel, S1);
    assert.equal(panel._views.seq, 1001, "the next cycle's frame is the same write, seen again — and lets the kept blob go");
    panel.setCaps({ type: "caps", caps: ["tagEdit"], viewsSeq: 1001 });
    assert.equal(panel._views.seq, 1001);
    assert.deepEqual(panel._curViews().tags[0].members, [SID1, SID2]);
  } finally { console.warn = cw; }
});

test("executed: a caps frame whose connect push served no views blob (viewsSeq null) adopts nothing, and lets a kept blob go", () => {
  const panel = drawnPanel();
  const warned: string[] = []; const cw = console.warn; console.warn = (s: any) => { warned.push(String(s)); };
  try {
    frame(panel, Object.assign(copy(S0), { seq: 999 }));   // a stale frame turned away just before the socket dropped: kept
    assert.equal(panel._views.seq, 1000);
    panel.setCaps({ type: "caps", caps: ["tagEdit"], viewsSeq: null });   // the reconnect's push carried no views (a sentinel cycle)
    assert.equal(panel._views.seq, 1000, "nothing named, nothing adopted: the gate stands");
    panel.setCaps({ type: "caps", caps: ["tagEdit"] });                    // even a field-less frame now: the kept blob was let go
    assert.equal(panel._views.seq, 1000);
  } finally { console.warn = cw; }
});

// The 2026-09-05 review: the caps frame's viewsSeq is also the kernel's ANNOUNCEMENT of its current
// store (the served blob's seq, or the store's current seq when the connect push carried no views frame; null
// only when the kernel has no store at all). A restart over a store restored from an older copy, met by a
// reconnect whose push carried no blob (a chat page's sentinel cycle sends no tabOrder), kept nothing for the earlier
// rule to match: the pusher's next frame — the restored store, under its old seq — was turned away, and no
// second caps frame comes. The announced seq is remembered in one slot until the next adoption that changes the
// held blob, and a later
// blob carrying exactly that seq is adopted below the held one.
test("executed: a sentinel-cycle reconnect over a restored store — the caps frame announces the store's seq with nothing kept, the pusher's next frame at that seq is adopted below the held one, another lower seq is still turned away, and the slot clears on the adoption", () => {
  const panel = drawnPanel();
  const warned: string[] = []; const cw = console.warn; console.warn = (s: any) => { warned.push(String(s)); };
  try {
    panel.setCaps({ type: "caps", caps: ["tagEdit"], viewsSeq: 900 });   // the connect push carried no views blob; the restored store's current seq is 900
    assert.equal(panel._views.seq, 1000, "nothing kept, nothing adopted on the frame itself");
    assert.equal(panel._announcedViewsSeq, 900, "…but the announced seq is remembered");
    frame(panel, Object.assign(copy(S0), { seq: 899 }));
    assert.equal(panel._views.seq, 1000, "a frame at another lower seq is a stale frame: turned away");
    assert.equal(panel._announcedViewsSeq, 900, "…and the slot stands");
    const restored = copy(S0); restored.seq = 900; restored.tags[0].name = "site";
    frame(panel, restored);                            // the pusher's next cycle: the restored store under its old seq
    assert.equal(panel._views.seq, 900, "the announced seq IS the store the kernel said it holds: adopted below the held one");
    assert.equal(panel._curViews().tags[0].name, "site", "the page shows the restored store");
    assert.equal(panel._announcedViewsSeq, null, "the slot cleared on the adoption");
    assert.equal(panel._rejectedViews, null, "…and so did the kept blob");
    frame(panel, Object.assign(copy(S0), { seq: 899 }));
    assert.equal(panel._views.seq, 900, "the store's own order gates again from the adopted seq");
    assert.equal(panel._tagEditErr, null, "nothing was in flight: nothing is said");
  } finally { console.warn = cw; }
});

test("executed: the announced slot clears on an adoption that changes the held blob — a write landing before the announced blob arrives stamps the store past it, and that blob is then the stale frame it looks like; a caps frame that adopts its kept blob leaves no slot; viewsSeq null and a missing field announce nothing", () => {
  const panel = drawnPanel();
  const warned: string[] = []; const cw = console.warn; console.warn = (s: any) => { warned.push(String(s)); };
  try {
    panel.setCaps({ type: "caps", caps: ["tagEdit"], viewsSeq: 900 });
    assert.equal(panel._announcedViewsSeq, 900);
    const written = copy(S0); written.seq = 1100; written.tags[0].name = "notes";   // another dashboard's write on the restored store: a write's seq is seeded from the clock, past everything a page holds
    frame(panel, written);
    assert.equal(panel._views.seq, 1100, "adopted by the ordinary rule…");
    assert.equal(panel._announcedViewsSeq, null, "…and the slot cleared with it");
    frame(panel, Object.assign(copy(S0), { seq: 900 }));   // the pusher's frame built before that write
    assert.equal(panel._views.seq, 1100, "the announced seq is no longer a door: the store moved past it");
    assert.equal(panel._curViews().tags[0].name, "notes");
    // the caps frame that adopts its kept blob (the earlier case) leaves no slot either
    const restored = copy(S0); restored.seq = 800;
    frame(panel, restored);
    assert.equal(panel._views.seq, 1100);
    panel.setCaps({ type: "caps", caps: ["tagEdit"], viewsSeq: 800 });
    assert.equal(panel._views.seq, 800, "the kept blob is what the frame names: adopted");
    assert.equal(panel._announcedViewsSeq, null, "nothing left to remember");
    // null — the kernel has no store at all — announces nothing, and an earlier announcement does not outlive the frame
    panel.setCaps({ type: "caps", caps: ["tagEdit"], viewsSeq: 700 });
    assert.equal(panel._announcedViewsSeq, 700);
    panel.setCaps({ type: "caps", caps: ["tagEdit"], viewsSeq: null });
    assert.equal(panel._announcedViewsSeq, null, "null announces nothing");
    frame(panel, Object.assign(copy(S0), { seq: 700 }));
    assert.equal(panel._views.seq, 800, "…so a blob at the seq an earlier frame named is turned away");
    // a frame without the field (a kernel from before it) announces nothing either
    panel.setCaps({ type: "caps", caps: ["tagEdit"], viewsSeq: 600 });
    assert.equal(panel._announcedViewsSeq, 600);
    panel.setCaps({ type: "caps", caps: ["tagEdit"] });
    assert.equal(panel._announcedViewsSeq, null);
    frame(panel, Object.assign(copy(S0), { seq: 600 }));
    assert.equal(panel._views.seq, 800);
  } finally { console.warn = cw; }
});

// The 2026-09-05 review: the slot is cleared only by an adoption that CHANGES the held blob. In the browser
// dashboard this pane sees the local blob only through the federation router's merged lanes payload, which replays the
// router's stored blob on every re-emit (a remote host's lanes, a view-order storage event, a host drop) — a re-arrival
// of the blob this pane already holds, at its own seq. The earlier clear on any adoption spent the slot on that
// re-arrival, and the restored store the router adopted and re-emitted next at the announced seq was turned away here:
// router 900, pane 1000, silently, until the next write. Executed on the real panel frame by frame, then on the real
// panel behind the real router (ui/webview/federation.ts) fed the same sequence.
test("executed: a re-arrival of the held blob leaves the announced slot standing, and the restored store at the announced seq is still adopted after it; the announced seq itself, a newer blob, and a seq-less blob clear it", () => {
  const panel = drawnPanel();
  const warned: string[] = []; const cw = console.warn; console.warn = (s: any) => { warned.push(String(s)); };
  try {
    panel.setCaps({ type: "caps", caps: ["tagEdit"], viewsSeq: 900 });
    assert.equal(panel._announcedViewsSeq, 900);
    frame(panel, S0);                                  // the router's re-emit of its stored blob: what this pane already holds
    assert.equal(panel._views.seq, 1000);
    assert.equal(panel._announcedViewsSeq, 900, "no new information: the slot stands");
    frame(panel, S0);
    assert.equal(panel._announcedViewsSeq, 900, "…however often it arrives");
    assert.equal(warned.length, 0, "nothing was turned away");
    const restored = copy(S0); restored.seq = 900; restored.tags[0].name = "site";
    frame(panel, restored);
    assert.equal(panel._views.seq, 900, "the restored store, at the announced seq, is adopted below the held one");
    assert.equal(panel._curViews().tags[0].name, "site");
    assert.equal(panel._announcedViewsSeq, null, "the adoption changed the held blob: the slot is spent");
    // the announced seq arriving at the held seq spends the slot too: the announced store has arrived
    panel.setCaps({ type: "caps", caps: ["tagEdit"], viewsSeq: 900 });
    assert.equal(panel._announcedViewsSeq, 900);
    frame(panel, restored);
    assert.equal(panel._announcedViewsSeq, null);
    // a newer blob clears it, as before
    panel.setCaps({ type: "caps", caps: ["tagEdit"], viewsSeq: 850 });
    frame(panel, restored);                            // held again: the slot stands
    assert.equal(panel._announcedViewsSeq, 850);
    frame(panel, Object.assign(copy(S0), { seq: 1100 }));
    assert.equal(panel._announcedViewsSeq, null, "a newer write moved the store: cleared");
    frame(panel, Object.assign(copy(S0), { seq: 850 }));
    assert.equal(panel._views.seq, 1100, "…and the seq once announced is the stale frame it looks like");
    // a seq-less blob (a kernel from before the stamp) changes the held one too
    panel.setCaps({ type: "caps", caps: ["tagEdit"], viewsSeq: 840 });
    const legacy = copy(S0); delete legacy.seq;
    frame(panel, legacy);
    assert.equal(panel._announcedViewsSeq, null);
  } finally { console.warn = cw; }
});

test("executed: behind the real federation router, a re-emit between the caps frame and the pusher's frame — a remote host's lanes payload, a view-order re-emit — does not cost this pane the restored store", () => {
  const remoteLanes = { type: "data", data: { sessions: [{ id: SID2, name: "api" }], turns: {}, messages: [], judging: [], now, views: { active: "all", tags: [], seq: 5 } } };
  const betweens: [string, (fm: any) => void][] = [
    ["a remote host's lanes payload", (fm) => fm.inbound("TESTHOST", remoteLanes)],
    ["a view-order re-emit", (fm) => fm.emitMergedTimeline(false)],
  ];
  for (const [what, between] of betweens) {
    const panel = new TimelinePanel(makeNode("div"));
    const fm: any = new FederationManager();
    // the router's window is this global: hand its merged frames to the panel the way the page's boot glue does
    g.dispatchEvent = (ev: any) => {
      const m = ev && ev.data; if (!m) return;
      if (m.type === "data" && m.data) panel.update(m.data);
      else if (m.type === "caps") panel.setCaps(m);
    };
    const warned: string[] = []; const cw = console.warn; console.warn = (s: any) => { warned.push(String(s)); };
    try {
      const lanes = (v: any) => ({ type: "data", data: { now, sessions: [sess(SID1, "web", "#f7768e")], turns: {}, messages: [], judging: [], views: copy(v), palette: PALETTE.slice() } });
      fm.inbound("", lanes(S0));
      fm.inbound("", { type: "caps", caps: ["tagEdit"], viewsSeq: 1000 });
      assert.equal(panel._views.seq, 1000, what + ": the load");
      fm.inbound("TESTHOST", remoteLanes);                                    // a remote host federated in
      assert.equal(panel._views.seq, 1000);
      fm.inbound("", { type: "caps", caps: ["tagEdit"], viewsSeq: 900 });   // the restart over a restored store, met on a sentinel cycle
      assert.equal(panel._announcedViewsSeq, 900, what + ": the slot is filled from the frame the router hands on");
      between(fm);                                                            // the router replays its stored blob — this pane's own held one
      assert.equal(panel._views.seq, 1000);
      assert.equal(panel._announcedViewsSeq, 900, what + ": the re-arrival leaves the slot");
      const restored = copy(S0); restored.seq = 900; restored.tags[0].name = "site";
      fm.inbound("", lanes(restored));                                        // the pusher's next frame: the router adopts through its slot and re-emits
      assert.equal(panel._views.seq, 900, what + ": the pane adopts the restored store from the router's re-emit");
      assert.equal(panel._curViews().tags[0].name, "site");
      assert.equal(panel._announcedViewsSeq, null);
      between(fm);
      assert.equal(panel._views.seq, 900, what + ": and holds it through the next re-emit");
      assert.equal(warned.length, 0, what + ": nothing was ever turned away");
    } finally { console.warn = cw; delete g.dispatchEvent; }
  }
});

test("executed: a caps frame without viewsSeq (a kernel from before the field) adopts the kept blob outright — the pre-field rule", () => {
  const panel = drawnPanel();
  const warned: string[] = []; const cw = console.warn; console.warn = (s: any) => { warned.push(String(s)); };
  try {
    const restored = copy(S0); restored.seq = 900; restored.tags[0].name = "site";
    frame(panel, restored);
    assert.equal(panel._views.seq, 1000);
    panel.setCaps({ type: "caps", caps: ["tagEdit"] });
    assert.equal(panel._views.seq, 900, "no field to match against: the kept blob is adopted");
    assert.equal(panel._curViews().tags[0].name, "site");
  } finally { console.warn = cw; }
});

test("executed: a write in flight at a restored-store reconnect is dropped and said so, and the copy reverts to the ADOPTED base, not the pre-restore one", () => {
  const panel = drawnPanel();
  panel._openViewsDialog(null);
  const u = viewTagUnion(panel._curViews()).find((x: any) => x.name === "web");
  panel._editTagUnion(u, { rename: "notes" });
  assert.equal(panel._viewsWrites.length, 1, "the rename is in flight");
  assert.equal(panel._curViews().tags[0].name, "notes", "…and shows");
  const warned: string[] = []; const cw = console.warn; console.warn = (s: any) => { warned.push(String(s)); };
  try {
    const restored = copy(S0); restored.seq = 900; restored.tags[0].name = "site";   // the store the restarted kernel serves
    frame(panel, restored);                            // the connect push, turned away
    assert.equal(panel._curViews().tags[0].name, "notes", "the copy still shows until the reconnect event");
    panel.setCaps({ type: "caps", caps: ["tagEdit"], viewsSeq: 900 });
    assert.deepEqual(panel._viewsWrites, [], "the write is dropped: its ack cannot reach this socket");
    assert.equal(panel._pendingViews, null);
    assert.equal(panel._views.seq, 900, "the base is the restored store…");
    assert.equal(panel._curViews().tags[0].name, "site", "…and that is what shows — never the pre-restore blob the copy was drawn over");
    assert.match(panel._tagEditErr.error, /re-established.*may not have landed/, "the user is told");
    const shown = walk(panel._viewsDialog).map(textOf).find((s) => s.startsWith("⚠ "));
    assert.ok(shown, "…in the open dialog, rebuilt over the adopted base");
    // an ack for the dropped write — unreachable on the socket it was posted on; if one arrived it is an ack for
    // a write this page no longer tracks: its blob meets the gate like any arrival (899 < 900: turned away), and
    // nothing is re-pinned
    const late = copy(restored); late.seq = 899; late.tags[0].name = "notes";
    panel.viewsAck({ type: "tagEditAck", writeId: "w-dropped", ok: true, tid: "gA", seq: 899, views: late });
    assert.equal(panel._views.seq, 900); assert.equal(panel._curViews().tags[0].name, "site");
    assert.deepEqual(panel._viewsWrites, []); assert.equal(panel._pendingViews, null);
  } finally { console.warn = cw; }
});

// ── The 2026-09-05 review: the in-flight create gate, the legacy create's
// id, the join input's draft, the create ack and an open editor, and a refusal reverting only its own write.
test("executed: [+ New tag] takes ONE click per create — the row reads creating… until the ack, then the button is back", () => {
  const panel = drawnPanel();
  panel._openViewsDialog(null);
  clickNewTag(panel);
  assert.equal(tagOps().length, 1, "one create posted");
  const busy = walk(panel._viewsDialog).find((n) => n.textContent === "creating…");
  assert.ok(busy, "the row says a create is in flight");
  assert.equal(busy._attrs["aria-disabled"], "true");
  assert.equal(busy._listeners.click, undefined, "…and takes no click");
  assert.equal(walk(panel._viewsDialog).find((n) => n.textContent === "+ New tag"), undefined, "the button is gone while one is in flight");
  assert.equal(tagOps().length, 1, "a second click before the ack posts nothing — no second tag");
  ackCreate(panel);
  assert.ok(walk(panel._viewsDialog).find((n) => n.textContent === "+ New tag"), "the ack's repaint brings the button back");
  assert.equal(walk(panel._viewsDialog).find((n) => n.textContent === "creating…"), undefined);
  clickNewTag(panel);
  assert.equal(tagOps().length, 2, "…and the next click is a new create");
  // a refusal re-arms it too
  panel.viewsAck({ type: "tagEditAck", writeId: tagOps()[1].writeId, ok: false, error: "the views blob caps at 32 tags", seq: 1001, views: copy(panel._views) });
  assert.ok(walk(panel._viewsDialog).find((n) => n.textContent === "+ New tag"));
});

test("executed: the join menu's new-tag input takes ONE Enter per create — disabled and saying so until the ack repaints the menu", () => {
  const panel = drawnPanel();
  const s = panel.data.sessions.find((x: any) => x.id === SID2);
  const anchor = makeNode("g"); anchor._rect = { left: 40, top: 60, right: 60, bottom: 76, width: 20, height: 16 };
  panel._openLaneMenu(s, anchor);
  const plus = () => walk(panel._laneMenu).find((n) => n.textContent === "+" && n._attrs.title === "add a tag");
  plus()._listeners.click({ stopPropagation() {} });
  const ni = walk(panel._laneMenu).find((n) => n.tag === "input");
  assert.ok(ni, "the join menu's input");
  assert.equal(ni.disabled, undefined);
  ni.value = "qa"; ni._listeners.keydown({ key: "Enter" });
  assert.equal(tagOps().length, 1);
  assert.deepEqual([tagOps()[0].op, tagOps()[0].name], ["create", "qa"]);
  ni._listeners.keydown({ key: "Enter" });                          // the same node, again, before the ack
  assert.equal(tagOps().length, 1, "a second Enter before the ack posts nothing");
  assert.equal(ni.disabled, true, "the input went dead on submit");
  // the menu was rebuilt without the join box (it closes on submit); opening it again while the
  // create is in flight shows a disabled input that says so
  plus()._listeners.click({ stopPropagation() {} });
  const ni2 = walk(panel._laneMenu).find((n) => n.tag === "input");
  assert.notEqual(ni2, ni);
  assert.equal(ni2.disabled, true);
  assert.equal(ni2.placeholder, "creating…");
  ni2.value = "docs"; ni2._listeners.keydown({ key: "Enter" });
  assert.equal(tagOps().length, 1, "Enter on the disabled input posts nothing");
  // the create's ack repaints the open menu: the input is live again
  const S1 = copy(S0); S1.seq = 1001; S1.tags.push({ id: "g8", name: "qa", color: "#1EA1EB", members: [SID2], mtime: 113 });
  panel.viewsAck({ type: "tagEditAck", writeId: tagOps()[0].writeId, ok: true, seq: 1001, tid: "g8", name: "qa", views: S1 });
  const ni3 = walk(panel._laneMenu).find((n) => n.tag === "input");
  assert.ok(ni3 && ni3 !== ni2, "the menu was rebuilt on the ack");
  assert.equal(ni3.disabled, undefined);
  assert.equal(ni3.placeholder, "new tag…");
  ni3.value = "docs"; ni3._listeners.keydown({ key: "Enter" });
  assert.equal(tagOps().length, 2, "…and takes the next create");
  panel._closeLaneMenu();
});

test("executed: the join menu's new-tag input keeps its text and caret across a repaint; submit and close drop the draft", () => {
  const panel = drawnPanel();
  const box = makeNode("div");
  panel._tagJoinMenu(box, [SID2], () => {}, SID2);   // the menu's identity: which [+] is open
  const ni = walk(box).find((n) => n.tag === "input");
  assert.equal(g.document.activeElement, ni, "the input took focus");
  ni.value = "do"; ni.selectionStart = 1; ni.selectionEnd = 1; ni._listeners.input();
  ni.value = "doc"; ni.selectionStart = 2; ni.selectionEnd = 2;            // the caret moved without an input event (arrow keys)
  // a refusal for some other write repaints the surface that holds the menu — here, the builder runs again
  const box2 = makeNode("div");
  panel._tagJoinMenu(box2, [SID2], () => {}, SID2);
  const ni2 = walk(box2).find((n) => n.tag === "input");
  assert.notEqual(ni2, ni, "a fresh input");
  assert.equal(ni2.value, "doc", "the typed text survives — read from the live input, not the last input event");
  assert.deepEqual(ni2._sel, [2, 2], "the caret is where it was");
  assert.equal(g.document.activeElement, ni2, "focus is back in the input");
  // a menu for OTHER rows starts empty; the draft for these rows is kept
  const box3 = makeNode("div");
  panel._tagJoinMenu(box3, [SID1], () => {}, SID1);
  assert.equal(walk(box3).find((n) => n.tag === "input").value, "", "another [+]'s menu does not inherit the draft");
  const box4 = makeNode("div");
  panel._tagJoinMenu(box4, [SID2], () => {}, SID2);
  const ni4 = walk(box4).find((n) => n.tag === "input");
  assert.equal(ni4.value, "doc");
  // submit posts the drafted name and drops the draft
  ni4.value = "docs"; ni4._listeners.input(); ni4._listeners.keydown({ key: "Enter" });
  assert.equal(tagOps()[0].name, "docs");
  assert.equal(panel._tagNewDraft, null);
  ackCreate(panel, 1001, "docs");
  // closing the menu from the lane gear's [+] drops a draft too
  const s = panel.data.sessions.find((x: any) => x.id === SID2);
  const anchor = makeNode("g"); anchor._rect = { left: 40, top: 60, right: 60, bottom: 76, width: 20, height: 16 };
  panel._openLaneMenu(s, anchor);
  const plus = () => walk(panel._laneMenu).find((n) => n.textContent === "+" && n._attrs.title === "add a tag");
  plus()._listeners.click({ stopPropagation() {} });
  const li = walk(panel._laneMenu).find((n) => n.tag === "input");
  li.value = "zz"; li._listeners.input();
  assert.equal(panel._tagNewDraft.value, "zz");
  plus()._listeners.click({ stopPropagation() {} });                       // toggles the join box closed
  assert.equal(panel._tagNewDraft, null, "closing the menu drops the draft");
  panel._closeLaneMenu();
});

test("executed: LEGACY (no cap): a create from the join menu ships a client-minted g… id, never the pending- placeholder, and names it as edited", () => {
  const L0 = copy(S0); delete L0.seq;
  const panel = drawnPanel(L0, []);
  const box = makeNode("div");
  panel._tagJoinMenu(box, [SID1, SID2], () => {});
  const ni = walk(box).find((n) => n.tag === "input");
  ni.value = "qa"; ni._listeners.keydown({ key: "Enter" });
  assert.equal(posted.length, 1);
  assert.equal(posted[0].kind, "views", "the whole blob — the store write itself on this path");
  const row = posted[0].v.tags.find((t: any) => t.name === "qa");
  assert.ok(row, "the new row is in the blob");
  assert.match(row.id, /^g[0-9a-z]+$/, "a proper id (the dialog's pre-2026-09-05 scheme) — nothing pending- is ever persisted");
  assert.deepEqual(row.members, [SID1, SID2]);
  assert.deepEqual(posted[0].edited, [row.id], "…and the write names it: a kernel that reads `edited` would otherwise keep an unknown tag out as a stale re-creation");
  assert.equal(panel._curViews().tags.find((t: any) => t.name === "qa").id, row.id, "the optimistic copy shows the same id");
  assert.equal(JSON.stringify(posted).indexOf("pending-"), -1, "no placeholder anywhere on the wire");
});

test("executed: a create's ack opens the rename input on the new tid ONLY when no editor is open — the user's rename elsewhere is left alone", () => {
  const panel = drawnPanel();
  panel._openViewsDialog(null);
  // the user is renaming "web"…
  walk(panel._viewsDialog).find((n) => n.textContent === "rename")._listeners.click();
  assert.equal(panel._tagEditorFor, "gA");
  const inp = nameInput(panel); inp.value = "site"; inp._listeners.input();
  // …and clicks [+ New tag] meanwhile (its ack arrives while the editor is still open)
  clickNewTag(panel);
  ackCreate(panel);
  assert.equal(panel._tagEditorFor, "gA", "the editor stays on the row the user is renaming");
  const inp2 = nameInput(panel);
  assert.equal(inp2.value, "site", "…with the typed text (the rebuild kept the draft)");
  assert.ok(walk(panel._viewsDialog).some((n) => n.textContent === "tag 1"), "the new row shows under its default name, unopened");
  inp2._listeners.change();
  assert.deepEqual([tagOps()[1].op, tagOps()[1].tid, tagOps()[1].newName], ["rename", "gA", "site"]);
  assert.equal(panel._tagEditorFor, null);
  // with no editor open, a create's ack opens the new row's — as before
  clickNewTag(panel);
  const S2 = copy(panel._views); S2.seq = 1002; S2.tags.push({ id: "g9", name: "tag 2", color: "#54B204", members: [], mtime: 114 });
  panel.viewsAck({ type: "tagEditAck", writeId: tagOps()[2].writeId, ok: true, seq: 1002, tid: "g9", name: "tag 2", views: S2 });
  assert.equal(panel._tagEditorFor, "g9");
});

test("executed: a refusal reverts ONLY its own write — a later write still in flight keeps its optimistic change, and its own ack settles it", () => {
  const panel = drawnPanel();
  const u = viewTagUnion(panel._curViews()).find((x: any) => x.name === "web");
  panel._editTagUnion(u, { color: "#DD42FF" });                                           // A: recolor
  panel._editTagUnion(viewTagUnion(panel._curViews()).find((x: any) => x.name === "web"), { add: [SID2] });   // B: add, from the copy showing A
  const [wA, wB] = panel._viewsWrites.map((w: any) => w.id);
  assert.deepEqual([panel._curViews().tags[0].color, panel._curViews().tags[0].members], ["#DD42FF", [SID1, SID2]], "both show");
  // A is refused (say, the tag was recoloured elsewhere first — a targeted op the kernel refused)
  panel.viewsAck({ type: "tagEditAck", writeId: wA, ok: false, tid: "gA", seq: 1000, error: "that tag no longer exists — it may have been deleted from another dashboard", views: copy(S0) });
  assert.deepEqual(panel._viewsWrites.map((w: any) => w.id), [wB], "only A is dropped; B is still in flight");
  assert.ok(panel._pendingViews, "B's copy still shows");
  assert.equal(panel._curViews().tags[0].color, "#3b82f6", "A's recolor reverted");
  assert.deepEqual(panel._curViews().tags[0].members, [SID1, SID2], "B's add did NOT flap off");
  assert.match(panel._tagEditErr.error, /no longer exists/, "the refusal is still shown");
  // B's ack settles it
  const S1 = copy(S0); S1.seq = 1001; S1.tags[0].members = [SID1, SID2];
  panel.viewsAck({ type: "tagEditAck", writeId: wB, ok: true, seq: 1001, tid: "gA", views: S1 });
  assert.equal(panel._pendingViews, null);
  assert.deepEqual(panel._curViews().tags[0].members, [SID1, SID2]);
  // a whole-blob write in flight after a refused targeted one: the copy IS that write's blob
  panel._editTagUnion(viewTagUnion(panel._curViews()).find((x: any) => x.name === "web"), { rename: "site" });   // C
  const nv = copy(panel._curViews()); nv.actives = Object.assign({}, nv.actives, { timeline: { tags: ["site"] } });
  panel._setViews(nv);                                                                   // D: a lens write built from the copy showing C
  const wC = panel._viewsWrites[0].id;
  panel.viewsAck({ type: "tagEditAck", writeId: wC, ok: false, tid: "gA", seq: 1001, error: 'a tag named "site" already exists', views: copy(S1) });
  assert.equal(panel._viewsWrites.length, 1, "D is still in flight");
  assert.deepEqual(panel._curViews().actives.timeline, { tags: ["site"] }, "D's lens still shows");
  assert.equal(panel._curViews().tags[0].name, "site", "D posted the whole blob with C's rename in it — what the kernel will judge D by, so that is what shows");
});

test("pins: no frame count settles a stamped kernel's write; the legacy exact echo and three-frame yield live in the seq-less branch only", () => {
  assert.doesNotMatch(SRC, /_pendingViewsAge/, "the old counter is gone");
  const rec = SRC.slice(SRC.indexOf("  _reconcileViews() {"), SRC.indexOf("  // The LOCAL kernel's capabilities"));
  assert.match(rec, /viewsSeq\(this\._views\) === null\s*\n\s*&& \(this\._viewsKey\(this\._views\) === this\._viewsKey\(this\._pendingViews\)\s*\n\s*\|\| \(this\._legacyViewsAge = \(this\._legacyViewsAge \|\| 0\) \+ 1\) >= 3\)\)/,
    "both legacy clears sit under the seq-less condition — a blob with a seq comes from a kernel that acks, and only the ack settles");
  assert.equal((rec.match(/>= 3/g) || []).length, 1, "one legacy yield, nowhere else");
  assert.match(SRC, /_postTagEdit\(nv, edit, meta\) \{\s*\n\s*if \(!this\._tagEditsTargeted\(\)\) \{\s*\n\s*if \(!nv\) return;[\s\S]{0,1200}?const edited = \[edit\.tid, edit\.tid_from, edit\.tid_to\]\.filter\(Boolean\);[\s\S]{0,400}?this\._setViews\(nv, edited\);\s*\n\s*return;\s*\n\s*\}/,
    "a targeted op needs the capability AND a bridge; otherwise the whole-blob write, naming the tags it changed (a create's row included)");
  assert.match(SRC, /const ed = Array\.isArray\(edited\) \? edited : \[\];\s*\n\s*if \(hook\) window\.__rompTimelineSetViews\(v, writeId, ed\);/,
    "the whole-blob hook carries the writeId and the edited tag ids (the same list rides the Obsidian panel's POST /views, 2026-09-08)");
  assert.match(SRC, /window\.__rompTimelineTagEdit\(writeId, edit\);/, "the targeted hook carries the writeId beside the NESTED op");
  assert.doesNotMatch(SRC, /op: '(?:rename|recolor|addMember|removeMember|delete)', name:/, "no op but create carries a name — every one addresses by tid");
});

// ── The 2026-09-05 review ──────────────────────────────────────────────────────────────────
test("executed: a lens or order write is built from the STORE's blob — a rename still in flight never rides it, and its refusal reverts only the rename", () => {
  const panel = drawnPanel();
  const web = viewTagUnion(panel._curViews()).find((g: any) => g.name === "web");
  panel._editTagUnion(web, { rename: "notes" });
  assert.deepEqual([tagOps().length, tagOps()[0].op, tagOps()[0].newName], [1, "rename", "notes"]);
  assert.equal(panel._curViews().tags[0].name, "notes", "the copy shows the rename");
  // a lens toggle while the rename is in flight (the dialog's pane-filter row, the corner chip, the menu)
  panel._setLens({ actives: Object.assign({}, panel._curViews().actives, { timeline: { tags: ["web"] } }) });
  const w = posted.filter((p) => p.kind === "views");
  assert.equal(w.length, 1);
  assert.equal(w[0].v.tags[0].name, "web", "the POSTED blob carries the store's name — the rename in flight is not this write's claim");
  assert.deepEqual([w[0].v.seq, w[0].v.at], [S0.seq, S0.at], "…with the store's stamps, the guard's evidence time");
  assert.deepEqual(w[0].v.actives.timeline, { tags: ["web"] });
  assert.deepEqual(w[0].edited, []);
  assert.equal(panel._curViews().tags[0].name, "notes", "the copy SHOWN keeps the rename…");
  assert.deepEqual(panel._curViews().actives.timeline, { tags: ["web"] }, "…and the lens");
  // the rename is refused as a duplicate: only it reverts; the lens write's fields stay shown
  panel.viewsAck({ type: "tagEditAck", writeId: tagOps()[0].writeId, ok: false, error: 'a tag named "notes" already exists', seq: 1000, views: copy(S0) });
  assert.equal(panel._curViews().tags[0].name, "web");
  assert.deepEqual(panel._curViews().actives.timeline, { tags: ["web"] });
  assert.match(panel._tagEditErr.error, /already exists/);
  const S1 = copy(S0); S1.seq = 1001; S1.actives = Object.assign({}, actives, { timeline: { tags: ["web"] } });
  panel.viewsAck({ type: "viewsAck", writeId: w[0].writeId, ok: true, refused: [], views: S1 });
  assert.equal(panel._pendingViews, null, "the lens ack settles it");
  // an ORDER write: the store's tags re-sorted to the order, tagOrder set — from the store's blob
  const S2 = copy(S1); S2.seq = 1002; S2.tags.push({ id: "gB", name: "api", color: "#54B204", members: [SID2], mtime: 120 });
  frame(panel, S2);
  panel._setLens({ tagOrder: ["api", "web"] });
  const o = posted.filter((p) => p.kind === "views")[1];
  assert.deepEqual(o.v.tags.map((t: any) => t.id), ["gB", "gA"]);
  assert.deepEqual(o.v.tagOrder, ["api", "web"]);
  assert.deepEqual(panel._curViews().tagOrder, ["api", "web"]);
  // every lens and order site goes through _setLens: the pane-filter row, the pill drag, the corner chip, the menu
  // …each naming what it is ABOUT (`own`), the keys a write no kernel takes keeps local and a ruled one
  // releases (the Obsidian panel's local lens, 2026-09-08); the map is built over the WRITE base's, never the shown one
  assert.match(SRC, /this\._setLens\(\{ actives: Object\.assign\(this\._lensBaseActives\(\), upd\) \}, \{ surfaces: Object\.keys\(upd\) \}\);/, "the dialog's pane filters");
  assert.match(SRC, /this\._setLens\(\{ tagOrder: names \}, \{ tagOrder: true \}\);/, "the pill drag");
  assert.equal((SRC.match(/this\._setViews\(nv\);/g) || []).length, 0, "no whole-blob write from a copy remains outside the legacy tag path");
  assert.match(SRC, /_setLens\(fields, own\) \{\s*\n\s*own = own \|\| \{ surfaces: Object\.keys\(fields\.actives \|\| \{\}\), tagOrder: 'tagOrder' in fields, active: 'active' in fields \};\s*\n\s*this\._setViews\(lensBlob\(this\._views, fields\), \[\], fields, own\);/,
    "built from this._views, the store's blob; `own` defaults to every key the fields carry");
});

test("executed: a tag whose create is in flight takes no gesture — the dialog row reads creating… with no actions, its chip has no ✕, the join menu does not offer it; the ack restores them", () => {
  const panel = drawnPanel();
  panel._openViewsDialog(null);
  const plusFor = (i: number) => walk(panel._viewsDialog).filter((n) => n._attrs.title === "add a tag")[i];
  plusFor(1)._listeners.click();                                   // SID2's [+]
  const ni = walk(panel._viewsDialog).find((n) => n.tag === "input" && n.placeholder === "new tag…");
  ni.value = "qa"; ni._listeners.keydown({ key: "Enter" });
  assert.deepEqual([tagOps().length, tagOps()[0].op, tagOps()[0].sids], [1, "create", [SID2]]);
  let nodes = walk(panel._viewsDialog);
  const qaCell = nodes.find((n) => n._tname === "qa");
  assert.ok(qaCell, "the optimistic row renders");
  assert.ok(walk(qaCell).some((n) => n.textContent === "creating…"), "…and says the create is in flight");
  assert.equal(qaCell._listeners.pointerdown, undefined, "…and is not draggable");
  const delTitle = (name: string) => (n: any) => typeof n._attrs.title === "string" && n._attrs.title.startsWith("DELETE the tag “" + name);
  assert.equal(nodes.find(delTitle("qa")), undefined, "no delete action on it");
  assert.ok(nodes.find(delTitle("web")), "…while the settled tag keeps its actions");
  assert.equal(nodes.filter((n) => n._attrs.title === "rename this tag (everywhere it is defined)").length, 1, "one rename action: the settled tag's");
  const chip = nodes.find((n) => n._attrs.title === 'creating "qa"…');
  assert.ok(chip, "the session's chip for it shows");
  assert.equal(chip._listeners.click, undefined, "…with no ✕ and no click");
  // the union says so, and a gesture that reaches the editor anyway posts nothing addressed to the placeholder
  const pend = viewTagUnion(panel._curViews()).find((g: any) => g.name === "qa");
  assert.equal(pend.pending, true);
  panel._editTagUnion(pend, { rename: "quality" });
  panel._editTagUnion(pend, { delete: true });
  panel._editTagUnion(pend, { add: [SID1] });
  panel._editTagUnion(pend, { remove: [SID2] });
  assert.equal(tagOps().length, 1, "nothing posted");
  assert.ok(!posted.some((p) => p.kind === "tag" && /^pending-/.test(String(p.e.tid || p.e.tid_from || p.e.tid_to || ""))), "no op ever names the placeholder");
  // SID1's join menu does not offer it while pending
  plusFor(0)._listeners.click();
  nodes = walk(panel._viewsDialog);
  // the join menu's box (grid-spanning, indented) — the pane-filter rows also render a clickable "qa" pill, a lens pick by name
  const joinBox = () => walk(panel._viewsDialog).find((n) => String(n._attrs.style || "").startsWith("grid-column:1 / -1;margin:2px 0 4px 8px"));
  const joinOptions = () => walk(joinBox()).filter((n) => n._listeners.click && n.textContent === "qa");
  assert.equal(joinOptions().length, 0, "not joinable before the ack");
  // the lane gear menu's chips follow the same rule
  const s2 = panel.data.sessions.find((x: any) => x.id === SID2);
  const anchor = makeNode("g"); anchor._rect = { left: 40, top: 60, right: 60, bottom: 76, width: 20, height: 16 };
  panel._closeViewsDialog();
  panel._openLaneMenu(s2, anchor);
  const laneChip = walk(panel._laneMenu).find((n) => n._attrs.title === 'creating "qa"…');
  assert.ok(laneChip && laneChip._listeners.click === undefined, "the lane menu's chip takes no click either");
  panel._closeLaneMenu();
  // the ack names the tag: actions, chip ✕ and the join option are back
  panel._openViewsDialog(null);
  const S1 = copy(S0); S1.seq = 1001; S1.at = 113; S1.tags.push({ id: "g7", name: "qa", color: "#54B204", members: [SID2], mtime: 113 });
  panel.viewsAck({ type: "tagEditAck", writeId: tagOps()[0].writeId, ok: true, seq: 1001, tid: "g7", name: "qa", views: S1 });
  nodes = walk(panel._viewsDialog);
  assert.ok(nodes.find(delTitle("qa")), "delete is back");
  assert.equal(nodes.find((n) => n._attrs.title === 'creating "qa"…'), undefined);
  assert.ok(nodes.find((n) => typeof n._attrs.title === "string" && n._attrs.title.startsWith('tagged "qa"')), "the chip carries its ✕ again");
  assert.equal(joinOptions().length, 1, "…and SID1's menu offers it");
  assert.equal(viewTagUnion(panel._curViews()).find((g: any) => g.name === "qa").pending, undefined);
});

test("executed: the join menu's new-tag draft is keyed by the open [+], survives a change of the rows it lists, and dies with the menu or the dialog", () => {
  const panel = drawnPanel();
  panel._openViewsDialog(null);
  walk(panel._viewsDialog).find((n) => n.textContent === "tag all")._listeners.click();     // the bulk [+]
  const input = () => walk(panel._viewsDialog).find((n) => n.tag === "input" && n.placeholder === "new tag…");
  const ni = input();
  ni.value = "do"; ni._listeners.input();
  assert.equal(panel._tagNewDraft.key, "*", "the draft belongs to the MENU (the bulk [+]), not to the rows it lists");
  // the search filter changes the row set under the open menu: the draft is still this menu's
  const q = walk(panel._viewsDialog).find((n) => n.tag === "input" && n.placeholder === "search name or host…");
  q.value = "we"; q._listeners.input();
  const ni2 = input();
  assert.notEqual(ni2, ni, "the menu was rebuilt for fewer rows");
  assert.equal(ni2.value, "do", "…and the draft came with it (keyed by the row set it hid here)");
  q.value = ""; q._listeners.input();
  assert.equal(input().value, "do");
  // closing the dialog drops it — it does not reappear in the next menu opened for the same rows
  panel._closeViewsDialog();
  assert.equal(panel._tagNewDraft, null);
  assert.equal(panel._tagNewInput, null);
  panel._openViewsDialog(null);                                                            // the bulk [+] is still the open one (the key rides the instance)
  assert.equal(input().value, "", "…the reopened menu starts empty");
  panel._closeViewsDialog();
  // the lane gear menu: closing the MENU (not only toggling its [+]) drops the draft too
  const s2 = panel.data.sessions.find((x: any) => x.id === SID2);
  const anchor = makeNode("g"); anchor._rect = { left: 40, top: 60, right: 60, bottom: 76, width: 20, height: 16 };
  panel._openLaneMenu(s2, anchor);
  walk(panel._laneMenu).find((n) => n.textContent === "+" && n._attrs.title === "add a tag")._listeners.click({ stopPropagation() {} });
  const li = walk(panel._laneMenu).find((n) => n.tag === "input");
  li.value = "zz"; li._listeners.input();
  assert.equal(panel._tagNewDraft.key, SID2);
  panel._onDocClick();                                                                      // a click outside closes the menu
  assert.equal(panel._laneMenu, null);
  assert.equal(panel._tagNewDraft, null, "the draft died with the menu");
  assert.equal(panel._tagNewInput, null);
});

// The lane gear under the master default (the session-flags `*` key): a session whose mail is off only by the
// master is still listed to peers, so the mailbox row words it apart and a "Hide from peers" row writes the
// hand-set off in one click (the toggle alone gets there only through an opt-in, which opens the mail). The
// kernel ships the reason as the lane row's mailOffWhy; the latch holds the hide against a stale push, where
// postalServiceOff is already true and only the reason moves, and a refusal hands the reason back to the frame.
test("executed: the gear words a master-held mailbox apart and hides it from peers in one click", () => {
  const flags: any[] = [];
  g.__rompTimelineSetFlag = (id: string, flag: string, value: unknown) => flags.push([id, flag, value]);
  const mail = (why: string, off = true) => [
    { ...sess(SID1, "web", "#f7768e"), postalServiceOff: off, mailOffWhy: why },
    { ...sess(SID2, "api", "#7aa2f7"), postalServiceOff: true, mailOffWhy: "isolation" },
  ];
  const push = (panel: any, why: string, off = true) => panel.update({ now, sessions: mail(why, off), turns: {}, messages: [], judging: [], views: copy(S0), palette: PALETTE.slice() });
  const open = (panel: any) => {
    panel._closeLaneMenu();
    const anchor = makeNode("g"); anchor._rect = { left: 40, top: 60, right: 60, bottom: 76, width: 20, height: 16 };
    panel._openLaneMenu(panel.data.sessions.find((x: any) => x.id === SID1), anchor);
  };
  const texts = (panel: any) => walk(panel._laneMenu).map((n) => n.textContent as string);
  const hideRow = (panel: any) => { const n = walk(panel._laneMenu).find((x) => x.textContent === "Hide from peers"); return n && n.parentNode; };
  const MASTER = "off by the master default: peers still see it listed, it can't send or receive; on opts it in";
  const NORMAL = "visible to peer sessions, can send and receive their messages; off = hidden from peers, can't send or receive";
  try {
    const panel = new TimelinePanel(makeNode("div"));
    push(panel, "master");
    open(panel);
    assert.ok(texts(panel).includes("Postal service — off"));
    assert.ok(texts(panel).includes(MASTER), "the master's off, worded as listed-but-unreachable");
    assert.ok(!texts(panel).includes(NORMAL));
    assert.ok(texts(panel).includes("turns its mail off by hand: peers stop seeing it listed"));
    const hide = hideRow(panel);
    assert.ok(hide && hide._listeners.click, "the hide row is offered, and clickable");
    hide._listeners.click({ stopPropagation() {} });
    assert.deepEqual(flags, [[SID1, "postalServiceOff", true]], "a hand-set true through the same persistence, no opt-in first");
    assert.ok(!hideRow(panel), "the menu repainted in place without it");
    assert.ok(texts(panel).includes(NORMAL) && texts(panel).includes("Postal service — off"), "…as a hand-set off");
    // a push built before the kernel saw the write still says master: the latch holds the hide
    push(panel, "master");
    open(panel);
    assert.ok(!hideRow(panel), "a stale push does not bring the master's wording back");
    assert.ok(texts(panel).includes(NORMAL));
    // the kernel's own answer releases the latch
    push(panel, "isolation");
    assert.equal(panel._leftMasterMail.size, 0, "released once the kernel stops reporting the master");
    // a hand-set off never offers the hide row: it is hidden already
    open(panel);
    assert.ok(!hideRow(panel), "no hide row");
    // a refused hide: the latch drops on that event and the frame's reason (still the master's) shows again
    const r = new TimelinePanel(makeNode("div"));
    push(r, "master");
    open(r);
    hideRow(r)._listeners.click({ stopPropagation() {} });
    r.settingRefused({ type: "settingRefused", gesture: "flag", sid: SID1, flag: "postalServiceOff", value: true, text: "couldn't save that setting" });
    assert.ok(hideRow(r), "the hide row is back with the refusal");
    assert.ok(texts(r).includes(MASTER));
    // the toggle under the master opts in: the row reads on, and neither the master's wording nor the hide stays
    const o = new TimelinePanel(makeNode("div"));
    push(o, "master");
    open(o);
    const toggle = walk(o._laneMenu).find((x) => x.textContent === "Postal service — off").parentNode.parentNode;
    toggle._listeners.click({ stopPropagation() {} });
    assert.deepEqual(flags.slice(-1), [[SID1, "postalServiceOff", false]]);
    assert.ok(texts(o).includes("Postal service — on") && texts(o).includes(NORMAL));
    assert.ok(!hideRow(o), "no hide row");
    // the reason is read off the current frame on every repaint: a push that moved it while the gear was open
    // (another window's hide) is what the next repaint shows, not the copy the menu opened on
    const w = new TimelinePanel(makeNode("div"));
    push(w, "master");
    open(w);
    push(w, "isolation");
    w.settingRefused({ type: "settingRefused", gesture: "flag", sid: SID1, flag: "notify", value: false, text: "x" });
    assert.ok(!hideRow(w), "the repaint read the frame's reason");
    assert.ok(texts(w).includes(NORMAL));
    w._closeLaneMenu();
    // a kernel that ships no reason: the ordinary wording, no hide row, nothing latched
    const n = new TimelinePanel(makeNode("div"));
    n.update({ now, sessions: [{ ...sess(SID1, "web", "#f7768e"), postalServiceOff: true }], turns: {}, messages: [], judging: [], views: copy(S0), palette: PALETTE.slice() });
    open(n);
    assert.ok(texts(n).includes(NORMAL));
    assert.ok(!hideRow(n), "no hide row");
    for (const p of [panel, r, o, n]) p._closeLaneMenu();
  } finally { delete g.__rompTimelineSetFlag; }
});
