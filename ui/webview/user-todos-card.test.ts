// The split to-do card (plans/user-todos.md, slice 1): the agent's plan (the existing checklist)
// and "Waiting on you" (open user todos — needs the agent flagged for the person it works for)
// share ONE transcript-bottom card, each section auto-hiding when empty, so today's behavior is
// unchanged when no todos exist. Per-row Reply (injects the user's answer, anchored) and Dismiss
// (clears without one); a row WITH detail says so at a glance ("▸ details") and opens on click.
// Source pins (render.ts has no jsdom harness, the repo convention), plus the optimistic removal EXECUTED:
// el(), notice(), renderTodo and utDropRow lifted from render.ts and run over a fake DOM the way
// chat-exact-tail-exec.test.ts lifts chatTail, so a removal that walks the wrong anchor fails here.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const requireCjs = createRequire(__filename);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const TODO = RENDER.slice(RENDER.indexOf("function renderTodo"), RENDER.indexOf("function renderCompact"));
// the delegated Dismiss handler, bounded at the body delegate's close (never to end-of-file)
const DISMISS_AT = RENDER.indexOf("utdismiss: (elx) => {");
const HANDLER = RENDER.slice(DISMISS_AT, RENDER.indexOf("\n  });\n})();", DISMISS_AT));
// its two branches: the arm (first click) up to its `return`, and the confirm (second click) after it
const ARM_AT = HANDLER.indexOf("if (!utArmed.has(tid)) {");
const ARM_END = HANDLER.indexOf("\n        return;", ARM_AT);
const ARM = ARM_AT >= 0 && ARM_END > ARM_AT ? HANDLER.slice(ARM_AT, ARM_END) : "";
const CONFIRM = ARM_END > 0 ? HANDLER.slice(ARM_END) : "";
const MODAL = RENDER.slice(RENDER.indexOf("function showUserTodoReply"),
  RENDER.indexOf("\nfunction ", RENDER.indexOf("function showUserTodoReply") + 10));

test("the todo ChatEvent and the session payload both carry the user todos", () => {
  // the rows ride ON the event (the chatTail delta re-sends changed events only) AND on the
  // session as the merge seam the plan names
  assert.match(RENDER, /kind: "todo"; tasks: TodoTask\[\]; userTodos\?: UserTodo\[\]; error\?: string/);
  assert.match(RENDER, /interface UserTodo \{ id: string; text: string; detail\?: string; createdT\?: number \}/);
  assert.match(RENDER, /userTodos\?: UserTodo\[\];/);
});

test("the session field merges through the upsert's prev-fallback (the bg-tasks payload idiom)", () => {
  assert.match(RENDER, /userTodos: \("userTodos" in msg\) \? msg\.userTodos : \(prev \? prev\.userTodos : undefined\)/);
});

test("the chatTail delta carries the field on both sides of the wire", () => {
  // the chat's steady state is chatTail frames: without this a caught-up client's top-level
  // field (the tab glyph's read, a later slice) went stale until the next FULL session frame
  assert.match(KERNEL, /tail\["userTodos"\] = m\.get\("userTodos"\) or \[\]/);   // kernel _send_chat's tail frame
  const tail = RENDER.slice(RENDER.indexOf("function chatTail"), RENDER.indexOf("\nfunction ", RENDER.indexOf("function chatTail") + 10));
  assert.match(tail, /if \("userTodos" in msg\) \{[^\n]*s\.userTodos = msg\.userTodos;/);
});

test("the kernel ships only fixed store values on the field — never a per-build value", () => {
  // _send_client dedups by the serialized payload (the firstSeen lesson): a value that ticks
  // with the clock would re-send the full chat to every client about once a second
  assert.match(KERNEL, /"userTodos": _user_todos_open/);
  const helper = (KERNEL.match(/def _open_user_todos\(sid\):[\s\S]*?\n\n/) || [""])[0];
  assert.ok(helper.length > 0, "_open_user_todos exists");
  assert.doesNotMatch(helper, /time\.time\(\)/, "no build-time clock reaches the payload");
});

test("the feature switch (2026-09-03) needs no client gate: the kernel ships no rows while it is off", () => {
  // _open_user_todos is the one gated read — the session field, the split-card event, the tab glyph
  // and the feed marker all derive from it — so "renders only with open todos" below IS the off
  // behavior. Pinned at the kernel seam (user-todos-switch.test.ts holds the gear + federation pins).
  const helper = KERNEL.slice(KERNEL.indexOf("def _open_user_todos(sid):"), KERNEL.indexOf("def _user_todo_session_ended"));
  assert.match(helper, /if not _user_todos_on\(\):\n        return \[\]/);
  assert.ok(!/userTodos_on|_user_todos_on|userTodosEnabled/.test(RENDER), "no switch logic in the renderer");
});

test("both sections auto-hide when empty", () => {
  assert.match(TODO, /else if \(ev\.tasks\.length\) \{/); // the agent's plan renders only with tasks
  assert.match(TODO, /if \(uts\.length\) \{/);            // waiting-on-you renders only with open todos
});

test("a task-store error still shows the waiting-on-you section (no early return)", () => {
  const returns = TODO.match(/return notice\(/g) || [];
  assert.equal(returns.length, 1, "one exit: the error branch no longer returns before the todo section");
  assert.doesNotMatch(TODO.slice(0, TODO.indexOf("return notice(")), /\n  return\b/, "no earlier exit at the function's own depth");
  assert.ok(TODO.indexOf("if (ev.error)") < TODO.indexOf("Waiting on you"),
    "the error section precedes the waiting-on-you section");
});

test("a request-store error keeps the agent's checklist and heads its own section", () => {
  // the two stores fail independently: the task store's cause rides `error` (its branch supplants
  // the checklist, rightly — that list could not be read), the request store's rides
  // `userTodosError` and renders AFTER the checklist under the waiting-on-you heading, so a
  // corrupt user-todos.json no longer hides a checklist that WAS read under "To-do · unavailable"
  assert.match(TODO, /if \(ev\.userTodosError\) \{/);
  assert.ok(TODO.indexOf("if (ev.userTodosError)") > TODO.indexOf("else if (ev.tasks.length)"),
    "the request-store error follows the checklist branch");
  const sect = TODO.slice(TODO.indexOf("if (ev.userTodosError)"), TODO.indexOf("return notice("));
  assert.match(sect, /"Waiting on you · unavailable"/, "headed as the section it stands in for");
  assert.match(sect, /el\("div", "ut-head"\)/);
  assert.match(sect, /sev = "err";/, "…in the error dress: the severity rides the rail and dot");
  assert.match(sect, /el\("div", "notice-md"\); m\.textContent = ev\.userTodosError;/);
  assert.doesNotMatch(TODO, /if \(ev\.error \|\| ev\.userTodosError\)/, "never folded into the task store's branch");
});

test("reply and dismiss are delegated to the stable root, never per-render listeners", () => {
  // the card rebuilds on every push; a per-render listener eats a mid-press click (click-safe.test.ts)
  assert.match(TODO, /reply\.dataset\.act = "utreply"/);
  assert.match(TODO, /dis\.dataset\.act = "utdismiss"/);
  assert.doesNotMatch(TODO, /reply\.addEventListener|dis\.addEventListener\("click"/, "no per-node click handler");
  assert.match(RENDER, /utreply: \(elx\) => \{/);
  assert.match(RENDER, /utdismiss: \(elx\) => \{/);
  assert.match(RENDER, /uttoggle: \(elx\) => \{/);
});

test("dismiss arms then confirms in place, posts the op, and removes the row optimistically", () => {
  assert.ok(HANDLER.length > 0, "the utdismiss handler exists and is bounded");
  assert.ok(ARM && CONFIRM, "the handler has an arm branch that returns, then a confirm branch");
  assert.match(ARM, /utArmed\.add\(tid\)/, "the first click arms");
  assert.match(ARM, /paintUtDismiss\(elx, true\)/, "…and says so on the button");
  assert.match(CONFIRM, /utArmed\.delete\(tid\)/, "the confirming click disarms");
  assert.match(CONFIRM, /vscodeApi\?\.postMessage\(\{ type: "userTodoDismiss", id: sid, todoId: tid \}\)/);
  assert.match(CONFIRM, /utDropRow\(elx\.closest\("\.ut-item"\)\)/, "the row goes now; the next push confirms");
});

test("the armed state is keyed, never on the node: the card rebuilds on every push while the session streams", () => {
  // the todo event trails the transcript, so every transcript change of a working session re-renders
  // this card — and an arm kept on the node (class + text) was wiped by the rebuild before the
  // confirming click could land: the two-step never completed while the session streamed. The arm
  // lives in a keyed Set (the utDetailOpen pattern) and renderTodo paints it back on every rebuild.
  assert.match(RENDER, /const utArmed = new Set<string>\(\)/);
  assert.match(TODO, /paintUtDismiss\(dis, utArmed\.has\(t\.id\)\)/, "renderTodo repaints the arm from the Set");
  assert.match(HANDLER, /if \(!utArmed\.has\(tid\)\) \{/, "the handler reads the Set, never the node's class");
  assert.doesNotMatch(HANDLER, /classList\.contains\("armed"\)/);
  // the coarse-pointer one-shot is keyed by tid too, so the REBUILT node's confirm can retire the
  // listener the old node registered; its self-check finds the button by its data, not by identity
  assert.match(RENDER, /const utDisarmers = new Map<string, EventListener>\(\)/);
  assert.match(HANDLER, /utDisarmers\.set\(tid, disarm\)/);
  assert.match(HANDLER, /closest\?\.\(`\[data-act="utdismiss"\]\[data-tid="\$\{tid\}"\]`\)\) return;/,
    "a press ON the button — this node or the rebuild that replaced it — leaves the arm and the listener alone");
  assert.doesNotMatch(HANDLER, /ev\.target === elx/);
  assert.doesNotMatch(RENDER, /_utDisarm\b/, "nothing about the arm rides the node any more");
});

test("the two-step dismiss completes on coarse pointers (no hover to leave)", () => {
  // on touch the pointer "leaves" the instant the finger lifts, so an unconditional pointerleave
  // disarm killed the arm between the arming tap and the confirming one — the two-step could
  // never complete. Fine pointers keep the hover disarm; coarse pointers hold the arm until a
  // tap anywhere ELSE cancels it (one-shot document pointerdown, the folder-menu dismisser idiom).
  assert.match(TODO, /if \(!isCoarsePointer\(\)\)\s*\n\s*dis\.addEventListener\("pointerleave"/,
    "the hover disarm is gated to fine pointers");
  assert.match(HANDLER, /if \(isCoarsePointer\(\)\) \{/);
  assert.match(HANDLER, /document\.addEventListener\("pointerdown", disarm, true\);/);
  const retire = RENDER.slice(RENDER.indexOf("function utRetireDisarmer("), RENDER.indexOf("\n}", RENDER.indexOf("function utRetireDisarmer(")));
  assert.match(retire, /document\.removeEventListener\("pointerdown", one, true\)/,
    "the one-shot is removed through its keyed record — a tap genuinely elsewhere, or the confirm, retires it");
  assert.match(HANDLER, /\) return;\s*\n\s*utDisarm\(tid\);/,
    "a press ON the button leaves the arm AND the listener for the click handler to settle");
});

test("a scroll that starts ON the armed button neither disarms nor spends the one-shot", () => {
  // a pointerdown on the armed button that becomes a SCROLL fires no click: a handler that
  // removed the listener on ANY pointerdown left the arm latched with the tap-elsewhere cancel
  // gone — "Really dismiss?" forever, one accidental brush from clearing an open ask. The guard
  // must RETURN (keeping the listener registered) before the removal; only a pointerdown
  // genuinely elsewhere disarms and removes.
  const oneShot = HANDLER.slice(HANDLER.indexOf("const disarm = "), HANDLER.indexOf("utDisarmers.set("));
  assert.match(oneShot, /return;/, "the on-button guard RETURNS…");
  assert.ok(oneShot.indexOf("return;") < oneShot.indexOf("utDisarm(tid)"), "…before the disarm that removes the one-shot");
  // the confirming tap no longer spends the listener, so the confirm branch retires it itself
  // (otherwise it lingers on document and fires once more against a removed row)
  const confirm = CONFIRM.slice(0, CONFIRM.indexOf("userTodoDismiss"));
  assert.match(confirm, /utRetireDisarmer\(tid\)/, "the confirm branch retires the armed one-shot before posting the dismiss");
  const disarm = RENDER.slice(RENDER.indexOf("function utDisarm("), RENDER.indexOf("\n}", RENDER.indexOf("function utDisarm(")));
  assert.match(disarm, /utArmed\.delete\(tid\)/);
  assert.match(disarm, /utRetireDisarmer\(tid\)/);
  assert.match(disarm, /paintUtDismiss\(node, false\)/, "a cancel repaints whichever rebuild of the button is on screen");
});

test("a kernel warn re-syncs the active view only while an optimistic removal is pending", () => {
  // Reply/Dismiss remove their row before any verdict; on a refusal the kernel's state did not
  // change, so the next push can dedup to nothing — the warn itself must repaint from events. The
  // frame carries no sid or todo id, so the one gate is "this client has a removal pending": a warn
  // about anything else (a bad name on create, an MCP error) repaints nothing.
  // keyed tid -> sid: the frame carries no sid, so the map is what says WHICH view holds the refused row
  assert.match(RENDER, /const utPendingRemoval = new Map<string, string>\(\)/);
  assert.match(MODAL, /utPendingRemoval\.set\(todoId, sid\)/, "Reply's send marks the id pending, with its session");
  assert.match(CONFIRM, /utPendingRemoval\.set\(tid, sid\)/, "Dismiss's confirm marks the id pending, with its session");
  assert.equal((RENDER.match(/utPendingRemoval\.set\(/g) || []).length, 2, "the two removal sites, no other writer");
  const warn = RENDER.slice(RENDER.indexOf('m.type === "warn"'), RENDER.indexOf('m.type === "err"'));
  assert.match(warn, /if \(utPendingRemoval\.size\) \{/, "gated on the pending set");
  // EVERY view holding a pending id goes stale, not only the active one: the user may have switched
  // sessions inside the round-trip, and the refused row sits in the view they left — which used to
  // keep its optimistic removal while the active view was rebuilt for nothing
  assert.match(warn, /for \(const sid of new Set\(utPendingRemoval\.values\(\)\)\) \{ const v = views\.get\(sid\); if \(v\) v\.stale = true; \}/);
  assert.match(warn, /appendActive\(\)/, "the active view rebuilds now; a hidden one rebuilds on its next switch (stale)");
  assert.doesNotMatch(warn, /wv\.stale = true; appendActive\(\)/, "no longer the active view alone");
  assert.match(warn, /utPendingRemoval\.clear\(\)/, "the re-sync settles whatever was pending");
  // a push that no longer lists a pending id is the kernel confirming it: the gate closes for that id
  // without any warn — per session, since an id absent from ANOTHER session's frame says nothing
  const settle = RENDER.slice(RENDER.indexOf("function utSettlePending("), RENDER.indexOf("\n}", RENDER.indexOf("function utSettlePending(")));
  assert.match(settle, /utPendingRemoval\.delete\(/);
  const tail = RENDER.slice(RENDER.indexOf("function chatTail"), RENDER.indexOf("\nfunction ", RENDER.indexOf("function chatTail") + 10));
  assert.match(tail, /utSettlePending\(s\.userTodos, msg\.userTodos\)/, "the chatTail delta settles it");
  const up = RENDER.slice(RENDER.indexOf("function upsert("), RENDER.indexOf("\nfunction ", RENDER.indexOf("function upsert(") + 10));
  assert.match(up, /utSettlePending\(prev\.userTodos, msg\.userTodos\)/, "…and so does a full session frame");
});

test("optimistic removal keeps the heading's count honest: one helper for both sites", () => {
  // both removal sites dropped the row and left "Waiting on you · 3" over two rows until the next
  // push; the helper recounts the rows left in the same card and drops the heading with the last one
  // (the section auto-hides when empty, as renderTodo paints it)
  const helper = RENDER.slice(RENDER.indexOf("function utDropRow("), RENDER.indexOf("\n}", RENDER.indexOf("function utDropRow(")));
  assert.ok(helper.length > 0, "utDropRow exists");
  assert.match(helper, /row\.remove\(\)/);
  assert.match(helper, /querySelectorAll\("\.ut-item"\)\.length/, "recounts the rows left in the same card");
  assert.match(helper, /head\.textContent = `Waiting on you · \$\{n\}`/, "rewrites the heading the way renderTodo paints it");
  assert.match(helper, /head\.remove\(\)/, "…and drops it with the last row");
  assert.equal((RENDER.match(/utDropRow\(/g) || []).length, 3, "the definition plus the two removal sites");
  assert.doesNotMatch(RENDER, /closest\("\.ut-item"\)\?\.remove\(\)/, "no site removes a row on its own");
  // the last row of a card with no checklist: the empty to-do notice and its rail dot stood until
  // the next push (the kernel ships no todo event when both lists are empty). The turn is HIDDEN, never
  // removed — syncViewInner keys on v.el.childNodes, so a removed node would shift every unit after it
  assert.match(helper, /if \(!card\.childElementCount\) \(card\.closest\("\.turn-todo"\) as HTMLElement \| null\)\?\.style\.setProperty\("display", "none"\)/);
  assert.doesNotMatch(helper, /turn-todo"\)[^\n]*\.remove\(\)/, "hidden, not removed");
});

// ── The optimistic removal, executed ─────────────────────────────────────────────────────────────────

/** A render.ts slice, transpiled (TS to JS) with esbuild at run time and required dynamically so the test bundle
 *  does not bundle esbuild itself (the chat-exact-tail-exec.test.ts pattern). */
function liftBetween(startAnchor: string, endAnchor: string): string {
  const a = RENDER.indexOf(startAnchor), b = RENDER.indexOf(endAnchor, a);
  assert.ok(a > 0 && b > a, `anchors not found: ${startAnchor.slice(0, 40)} or ${endAnchor.slice(0, 40)} moved; re-anchor`);
  return requireCjs("esbuild").transformSync(RENDER.slice(a, b), { loader: "ts" }).code;
}

type Compound = { tag: string | null; classes: string[]; attrs: [string, string | null][] };
/** One compound selector as the lifted code writes them: an optional tag, .classes and [data-x="v"] attributes. */
function parseCompound(s: string): Compound {
  const c: Compound = { tag: null, classes: [], attrs: [] };
  const re = /^([a-z][\w-]*)|\.([\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]/y;
  let last = 0;
  for (let m = re.exec(s); m; m = re.exec(s)) {
    if (m[1]) c.tag = m[1]; else if (m[2]) c.classes.push(m[2]); else c.attrs.push([m[3], m[4] ?? null]);
    last = re.lastIndex;
  }
  if (last !== s.length) throw new Error("unsupported selector " + s);
  return c;
}

/** Enough of Element for el(), notice(), renderTodo and utDropRow: a class list, children, a dataset, text, an inline
 *  style, and closest / querySelector / querySelectorAll over class and data-attribute selectors with the descendant
 *  combinator, so the removal walks the rendered notice the way it walks the real one. Assertions compare primitives
 *  read off the tree, never a node: a failing deep comparison over parent-linked nodes is what node's differ chokes on. */
class FakeEl {
  childNodes: FakeEl[] = []; parentNode: FakeEl | null = null;
  classes = new Set<string>(); dataset: Record<string, string> = {}; attrs: Record<string, string> = {};
  textContent = ""; title = ""; type = "";
  style: { props: Record<string, string>; setProperty: (k: string, v: string) => void };
  constructor(public tagName: string) {
    const props: Record<string, string> = {};
    this.style = { props, setProperty: (k, v) => { props[k] = v; } };
  }
  get className(): string { return [...this.classes].join(" "); }
  set className(v: string) { this.classes = new Set(v.split(/\s+/).filter(Boolean)); }
  get classList() {
    const c = this.classes;
    return {
      add: (...ks: string[]) => { for (const k of ks) c.add(k); },
      remove: (...ks: string[]) => { for (const k of ks) c.delete(k); },
      contains: (k: string) => c.has(k),
      toggle: (k: string, force?: boolean) => { const on = force ?? !c.has(k); if (on) c.add(k); else c.delete(k); return on; },
    };
  }
  get childElementCount(): number { return this.childNodes.length; }
  appendChild(c: FakeEl): FakeEl { c.parentNode?.removeChild(c); c.parentNode = this; this.childNodes.push(c); return c; }
  append(...cs: FakeEl[]): void { for (const c of cs) this.appendChild(c); }
  removeChild(c: FakeEl): void { this.childNodes = this.childNodes.filter((x) => x !== c); c.parentNode = null; }
  remove(): void { this.parentNode?.removeChild(this); }
  setAttribute(k: string, v: string): void { this.attrs[k] = v; if (k.startsWith("data-")) this.dataset[k.slice(5)] = v; }
  getAttribute(k: string): string | null {
    if (k.startsWith("data-")) { const d = k.slice(5).replace(/-([a-z])/g, (_, ch: string) => ch.toUpperCase()); return d in this.dataset ? this.dataset[d] : null; }
    return k in this.attrs ? this.attrs[k] : null;
  }
  addEventListener(): void {}
  matchesCompound(c: Compound): boolean {
    if (c.tag && c.tag !== this.tagName) return false;
    for (const k of c.classes) if (!this.classes.has(k)) return false;
    for (const [name, value] of c.attrs) { const v = this.getAttribute(name); if (v === null) return false; if (value !== null && v !== value) return false; }
    return true;
  }
  matches(sel: string): boolean {
    const parts = sel.trim().split(/\s+/).map(parseCompound);
    if (!this.matchesCompound(parts[parts.length - 1])) return false;
    let anc: FakeEl | null = this.parentNode;
    for (let i = parts.length - 2; i >= 0; i--) {
      while (anc && !anc.matchesCompound(parts[i])) anc = anc.parentNode;
      if (!anc) return false;
      anc = anc.parentNode;
    }
    return true;
  }
  closest(sel: string): FakeEl | null { for (let n: FakeEl | null = this; n; n = n.parentNode) if (n.matches(sel)) return n; return null; }
  querySelector(sel: string): FakeEl | null { for (const e of this.walk()) if (e.matches(sel)) return e; return null; }
  querySelectorAll(sel: string): FakeEl[] { return [...this.walk()].filter((e) => e.matches(sel)); }
  *walk(): Generator<FakeEl> { for (const c of this.childNodes) { yield c; yield* c.walk(); } }
}

type Row = { id: string; text: string; detail?: string };
type Task = { subject: string; status: string; activeForm?: string };
type Lifted = { renderTodo: (ev: { kind: "todo"; tasks: Task[]; userTodos: Row[] }) => FakeEl; utDropRow: (row: FakeEl | null) => void };
/** el(), notice(), paintUtDismiss and the run from utDropRow through renderTodo, lifted from render.ts and run over the
 *  fake DOM. The module state they read (the open-state sets, the rendering session, the arm and detail sets) and the
 *  helpers beside the point (the tooltip, the glyph, the link pass, the completed-bulk label) are stubbed. */
function liftTodoCard(): Lifted {
  const elFn = liftBetween("function el(tag: string, cls?: string): HTMLElement {", "\n// ONE sanitizer for both renderers");
  const noticeFn = liftBetween("function notice(spec: NoticeSpec): HTMLElement {", "\n// A word button for a notice's action slot");
  const paint = liftBetween("function paintUtDismiss(node: HTMLElement, armed: boolean): void {", "function utRetireDisarmer(");
  const todo = liftBetween("function utDropRow(row: Element | null): void {", "\nfunction todoFoldLabel(");
  const prelude = `
    const W = WORLD;
    const document = { createElement: (tag) => new W.FakeEl(tag) };
    const openFolds = new Set(), noticeSeeded = new Set();
    const applyFold = (target, cls, key) => { if (key && openFolds.has(key)) target.classList.add(cls); };
    const dot = (kind) => el("span", "dot " + kind);
    const noticeGlyph = (kind) => el("span", "notice-glyph notice-glyph-" + kind);
    const setTip = () => {};
    let renderingSid = W.sid;
    const utArmed = new Set(), utDetailOpen = new Set();
    const isCoarsePointer = () => false;
    const linkifyFileUris = () => {};
    const todoFoldLabel = () => {};
  `;
  const make = new Function("WORLD", prelude + elFn + noticeFn + paint + todo + "\nreturn { renderTodo, utDropRow };") as
    (w: { FakeEl: typeof FakeEl; sid: string }) => Lifted;
  return make({ FakeEl, sid: "web" });
}

/** A rendered to-do notice over the rows waiting on the person (and a checklist when given), with readers for what the
 *  assertions compare, by the classes utDropRow itself reads. `drop` finds the row by its Reply button's id, the way
 *  showUserTodoReply's removal does, and hands it to utDropRow: the optimistic removal path, executed. */
function todoCard(rows: Row[], tasks: Task[] = []) {
  const api = liftTodoCard();
  const turn = api.renderTodo({ kind: "todo", tasks, userTodos: rows });
  return {
    turn,
    gist: () => turn.querySelector(".notice-gist")?.textContent ?? null,
    head: () => turn.querySelector(".ut-head")?.textContent ?? null,
    items: () => turn.querySelectorAll(".ut-item").length,
    listCount: () => turn.querySelector(".todo-list")?.childElementCount ?? -1,
    hidden: () => turn.style.props.display === "none",
    drop: (id: string) => api.utDropRow(turn.querySelector(`.ut-item [data-tid="${id}"]`)?.closest(".ut-item") ?? null),
  };
}

test("executed: removing a row recounts the section head AND the notice head's gist; the last row hides the notice", () => {
  const c = todoCard([{ id: "u1", text: "which name for the new tab" }, { id: "u2", text: "ok to delete the old branch" }]);
  assert.equal(c.turn.classList.contains("turn-todo"), true, "renderTodo rendered the to-do turn");
  assert.equal(c.items(), 2);
  assert.equal(c.head(), "Waiting on you · 2");
  assert.equal(c.gist(), "waiting on you · 2", "with no checklist the notice head names the section");
  c.drop("u9");
  assert.equal(c.items(), 2, "an id the card does not hold removes nothing");
  c.drop("u1");
  assert.equal(c.items(), 1, "the row goes now");
  assert.equal(c.head(), "Waiting on you · 1", "the section head follows the count");
  assert.equal(c.gist(), "waiting on you · 1", "and so does the notice head");
  assert.equal(c.hidden(), false);
  c.drop("u2");
  assert.equal(c.items(), 0);
  assert.equal(c.head(), null, "the section head goes with the last row");
  assert.equal(c.listCount(), 0, "nothing is left in the list");
  assert.equal(c.hidden(), true, "the empty notice is hidden until the next push replaces it");
  assert.doesNotMatch(c.gist() || "", /· 0$/, "never a zero count on the notice head: the hidden notice keeps its last count unseen");
});

test("executed: with a checklist standing, the notice head keeps its 'n of m done' and the notice stays up after the last row", () => {
  const c = todoCard([{ id: "u1", text: "which name for the new tab" }, { id: "u2", text: "ok to delete the old branch" }],
                     [{ subject: "write the tests", status: "pending" }]);
  assert.equal(c.gist(), "0 of 1 done");
  assert.equal(c.listCount(), 4, "one checklist row, the section head, two rows");
  c.drop("u1");
  assert.equal(c.head(), "Waiting on you · 1");
  assert.equal(c.gist(), "0 of 1 done", "the notice head is rewritten only when it names the section");
  c.drop("u2");
  assert.equal(c.head(), null);
  assert.equal(c.listCount(), 1, "the checklist row stands");
  assert.equal(c.hidden(), false, "a notice with a checklist stays up");
});

test("a dismissed session takes its keyed user-todo state with it", () => {
  // utArmed / utDisarmers / utPendingRemoval are settled by the SAME session's later frames
  // (utSettlePending), which never arrive once the session is dismissed: a dead session's pending
  // id held the warn gate open for the next unrelated warn, and a coarse-pointer arm's document
  // pointerdown listener outlived it until the next tap anywhere
  const forget = RENDER.slice(RENDER.indexOf("function utForgetSession("), RENDER.indexOf("\n}", RENDER.indexOf("function utForgetSession(")));
  assert.ok(forget.length > 0, "utForgetSession exists");
  assert.match(forget, /if \(utArmed\.has\(t\.id\)\) utDisarm\(t\.id\)/, "an arm goes with its one-shot");
  assert.match(forget, /for \(const \[tid, owner\] of utPendingRemoval\) if \(owner === sid\) utPendingRemoval\.delete\(tid\)/,
    "pending ids are dropped by their session, not by the rows the frame still lists");
  const dismiss = RENDER.slice(RENDER.indexOf("function dismissSession("), RENDER.indexOf("\n}", RENDER.indexOf("function dismissSession(")));
  assert.match(dismiss, /utForgetSession\(id, sessions\.get\(id\)\?\.userTodos\);/);
  assert.ok(dismiss.indexOf("utForgetSession(") < dismiss.indexOf("sessions.delete(id)"),
    "read before the map forgets the rows");
});

test("reply opens a modal (outside the rebuilt transcript) and posts one answer+stamp op", () => {
  // one kernel op both injects the reply AND stamps the todo answered — never sendMessage plus a
  // separate stamp; and a modal, not an inline box, because the card rebuilds every push
  assert.match(RENDER, /function showUserTodoReply\(sid: string, todoId: string, todoText: string, todoDetail = ""\): void/);
  assert.match(RENDER, /vscodeApi\?\.postMessage\(\{ type: "userTodoAnswer", id: sid, todoId, text \}\)/);
  const modal = MODAL;
  assert.match(modal, /overlay\.id = "ut-reply-prompt"/, "the confirm chrome, its own id");
  // the Enter branch that calls go(): fine pointers only — on a phone Enter is a NEWLINE and the Send
  // button sends (the composer's own rule; a two-line answer used to send its first line alone there)
  const enterAt = modal.indexOf('e.key === "Enter"');
  assert.ok(enterAt > 0, "the modal handles Enter");
  const enter = modal.slice(enterAt, modal.indexOf("go()", enterAt));
  assert.match(enter, /!e\.shiftKey/, "Shift+Enter keeps a newline");
  assert.match(enter, /!isCoarsePointer\(\)/, "Enter sends on a fine pointer only");
  assert.match(modal, /utDropRow\(document\.querySelector\(`\.ut-item \[data-tid="\$\{todoId\}"\]`\)\?\.closest\("\.ut-item"\)/,
    "optimistic: the row goes now, the next push confirms");
  // the ask's detail, when it has one, is quoted beneath the line in the row fold's own dress —
  // the whole need stays in view while the answer is typed; a bare ask adds nothing
  assert.match(TODO, /\(reply as any\)\._utdetail = t\.detail \|\| "";/);
  assert.match(modal, /const dd = todoDetail\.trim\(\) \? el\("div", "ut-detail open"\) : null;/);
  assert.match(modal, /if \(dd\) box\.appendChild\(dd\)/);
  assert.ok(modal.indexOf("box.append(h, d)") < modal.indexOf("if (dd) box.appendChild(dd)")
    && modal.indexOf("if (dd) box.appendChild(dd)") < modal.indexOf("box.append(input, actions)"),
    "quoted line, then the detail, then the answer box");
  const handler = RENDER.slice(RENDER.indexOf("utreply: (elx) => {"), RENDER.indexOf("utdismiss: (elx) => {"));
  assert.match(handler, /showUserTodoReply\(sid, tid, \(\(elx as any\)\._uttext as string\) \|\| "", \(\(elx as any\)\._utdetail as string\) \|\| ""\);/);
});

test("detail hides behind a keyed fold that survives re-renders (progressive disclosure)", () => {
  assert.match(RENDER, /const utDetailOpen = new Set<string>\(\)/);
  const handler = RENDER.slice(RENDER.indexOf("uttoggle: (elx) => {"), RENDER.indexOf("utreply: (elx) => {"));
  assert.match(handler, /if \(open\) utDetailOpen\.add\(tid\); else utDetailOpen\.delete\(tid\);/);
  assert.match(handler, /det\?\.classList\.toggle\("open", open\);/);
});

test("a row WITH detail says so at a glance; a bare row renders nothing extra", () => {
  // the postal tool's optional `detail` used to hide behind the text's click with no visible
  // tell, so a bare ask and one with a paragraph behind it looked identical until hovered. ONE
  // gate — the trimmed detail — drives the text's click affordance, the hint and the fold body,
  // and the kernel ships the `detail` key only for a non-blank detail (test_user_todos.py pins it)
  assert.match(TODO, /const detail = \(t\.detail \|\| ""\)\.trim\(\);/);
  assert.match(TODO, /if \(detail\) \{\s*\n\s*txt\.classList\.add\("ut-has-detail"\);\s*\n\s*txt\.dataset\.act = "uttoggle"; txt\.dataset\.tid = t\.id;/);
  assert.match(TODO, /const more = el\("span", "ut-more"\); paintUtHint\(more, utDetailOpen\.has\(t\.id\)\); txt\.appendChild\(more\);/,
    "painted INSIDE .ut-text — the delegated uttoggle target; no target and no listener of its own");
  assert.doesNotMatch(TODO, /more\.addEventListener|more\.onclick|more\.dataset\.act/);
  assert.match(TODO, /if \(detail\) \{\s*\n\s*const d = el\("div", "ut-detail"/, "the fold body renders on the same gate");
  // the words: caret + "details", flipping while open; the title doubles as the aria-label
  assert.match(RENDER, /\{ text: "▾ details", title: "click to hide the details" \}/);
  assert.match(RENDER, /\{ text: "▸ details", title: "has details — click to read" \}/);
  assert.match(RENDER, /node\.textContent = h\.text; node\.title = h\.title; node\.setAttribute\("aria-label", h\.title\);/);
  // the toggle repaints the hint in place — with the body appearing, that flip IS the click's acknowledgement
  const handler = RENDER.slice(RENDER.indexOf("uttoggle: (elx) => {"), RENDER.indexOf("utreply: (elx) => {"));
  assert.match(handler, /const more = elx\.querySelector<HTMLElement>\("\.ut-more"\);\s*\n\s*if \(more\) paintUtHint\(more, open\);/);
  assert.match(handler, /elx\.title = utHint\(open\)\.title;/, "the text's own title follows the state too");
});

test("detail renders as plain text, with paths clickable the way a transcript's are", () => {
  // textContent, never markdown: an agent's note is not the agent's prose bubble, and a path in it
  // opens like one in a message (the same linkifier, shape-only: no per-message kernel verdict here)
  const fold = TODO.slice(TODO.indexOf('el("div", "ut-detail"'));
  assert.match(fold, /d\.textContent = t\.detail \|\| "";\s*\n\s*linkifyFileUris\(d\);/);
  assert.doesNotMatch(TODO, /innerHTML|md\(/, "no markdown render on the card");
});

test("the waiting-on-you styles reuse the todo card vocabulary", () => {
  assert.match(CSS, /\.ut-head \{/);
  assert.match(CSS, /\.ut-item \{/);
  assert.match(CSS, /\.ut-detail \{ display: none;/);
  assert.match(CSS, /\.ut-detail\.open \{ display: block; \}/);
  assert.match(CSS, /\.ut-dismiss\.armed \{ border-color: var\(--err\); color: var\(--err\); \}/);
  // the reply box's "empty answer" red is the same --err token, never a hex the light theme can't re-ink
  const bad = (CSS.match(/\.ut-reply-input\.bad \{[^}]*\}/) || [""])[0];
  assert.ok(bad, ".ut-reply-input.bad rule exists");
  assert.match(bad, /border-color: var\(--err\)/);
  assert.doesNotMatch(bad, /#[0-9a-f]{3,8}\b/i, "tokens only");
  // the hint wears the row's chrome rung (.ut-btn / .ut-head) in the dim text color — a
  // disclosure cue, never the accent; inline-block keeps the text's dotted hover underline off it
  const rule = (CSS.match(/\.ut-more \{[^}]*\}/) || [""])[0];
  assert.ok(rule, ".ut-more rule exists");
  assert.match(rule, /font-size: 0\.72em;/);
  assert.match(rule, /color: var\(--dim\);/);
  assert.match(rule, /display: inline-block;/);
  assert.match(rule, /white-space: nowrap;/);
  assert.doesNotMatch(rule, /#[0-9a-f]{3,8}\b|var\(--accent/i, "tokens only, and not the accent");
  assert.match(CSS, /\.ut-has-detail:hover \.ut-more \{ color: var\(--fg\); \}/);
});
