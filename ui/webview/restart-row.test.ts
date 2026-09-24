import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { addRestartRow, restartInFlight, restartInterrupts, restartConfirmTitle, settleRestart, RESTART_INTERRUPTS } from "./restart-row";
import { restartConfirmDetail, RESTART_LABEL, RESTART_BUSY_LABEL, RESTART_STANDING } from "./clear-confirm";

// "Restart session" (the user 2026-09-23): ONE action that relaunches a session's own CLI process in place,
// where End then Revive was the only way to get a long-lived session onto a newly installed CLI — two
// destructive-looking steps for something that destroys nothing. The row is restart-row.ts's, built once for
// both menus that offer it (the chat strip's tab menu, the Sessions pane's row menu).
//
// The rules the other files cannot drift from, executed here:
//   * the gesture — idle restarts with no dialog, a working one confirms first, Cancel posts nothing;
//   * the acknowledgement — the row latches its own label to "Restarting…" and re-arms on the KERNEL'S
//     reply for that sid, never on a clock (ui/CLAUDE.md's post-and-wait button rule);
//   * the wire — one op, { type: "restartSession", id }, and nothing else.
// The browser leg runs the real Sessions pane bundle in Chromium and reads what a user would; the pins below
// hold the chat strip's half and the kernel's, which the served lab (tests/test_restart_session_served.py) and
// tests/test_restart_session.py drive. Synthetic values only: the notes-api world, a placeholder sid.
const EXT = process.cwd();                                          // npm test runs in vscode-extension
const requireCjs = createRequire(path.join(EXT, "package.json"));
const UI = path.resolve(EXT, "..", "ui", "webview");
const read = (f: string) => fs.readFileSync(path.join(UI, f), "utf8");
const RENDER = read("render.ts");
const PANE = read("fleet.ts");
const ROW = read("restart-row.ts");
const KERNEL = fs.readFileSync(path.resolve(EXT, "..", "kernel", "kernel.py"), "utf8");

const ORIGIN = "http://notes-api.test";
const SID = "11111111-2222-3333-4444-555555555555";
const HEAD = '.fl-head[data-sid="' + SID + '"]';
const MENU = ".ctx-menu.fl-sess-menu";
const ROWSEL = MENU + " .ctx-item-restart";
const LABEL = ROWSEL + " .ctx-item-label";
const OPEN_TOP = "Wire the notes-api health route";

// ── the pure halves, executed ────────────────────────────────────────────────────────────────────

// Every chip state, named: the kernel's chip derivation (_session_chip, and build_session's `opening`) as status-chip.ts's
// ChipState spells it. The two lists below must cover the union between them, so a state added there fails here as
// well as in the typecheck (RESTART_INTERRUPTS is `satisfies Record<ChipState, boolean>`).
const CHIP_STATES = (fs.readFileSync(path.join(UI, "status-chip.ts"), "utf8").match(/export type ChipState = ([^;]+);/) || ["", ""])[1]
  .split("|").map((t) => t.trim().replace(/^"|"$/g, "")).filter(Boolean);
const COSTS = ["working", "compacting", "needsInput", "awaiting", "retrying", "awaitingBg"];
const FREE = ["interrupting", "clearing", "blocked", "ready", "idle", "closed", "opening"];

test("every chip state has a decided answer, and a restart interrupts exactly the ones with a turn in flight (working, compacting, paused on a prompt, riding an API retry) or background work it dispatched", () => {
  assert.ok(CHIP_STATES.length >= 13, "the ChipState union was read from status-chip.ts: " + CHIP_STATES.join(", "));
  assert.deepEqual([...COSTS, ...FREE].sort(), [...CHIP_STATES].sort(), "this test names every chip state once");
  assert.deepEqual(Object.keys(RESTART_INTERRUPTS).sort(), [...CHIP_STATES].sort(), "…and so does the map: one decision per state");
  // needsInput and retrying moved across on 2026-09-24: both are chip states for a turn that is still open, which the
  // relaunch cuts (this list once pinned retrying as costing nothing); awaiting is needsInput's legacy name
  for (const st of COSTS) assert.equal(restartInterrupts(st), true, st + " costs something");
  // interrupting: a Stop already asked the turn to end and a restart sends nothing more; clearing: as it always was
  for (const st of FREE) assert.equal(restartInterrupts(st), false, st + " costs nothing");
  for (const st of ["", null, undefined, "toString"]) assert.equal(restartInterrupts(st), false, String(st) + " is no state: no dialog");
});

const QUESTION = /the question it is asking you goes away with it/;

test("the confirm names what the click interrupts, and what it keeps; with no open card it still says both", () => {
  const bare = restartConfirmDetail([]);
  assert.match(bare, /turn it is running now is cut off/);
  assert.doesNotMatch(bare, /question/, "a session not waiting on you is asked nothing, so the dialog names no question");
  assert.ok(bare.endsWith(RESTART_STANDING), "…and what survives it, the standing sentence");
  const one = restartConfirmDetail([OPEN_TOP]);
  assert.match(one, /^It is working on 1 open card: Wire the notes-api health route\./);
  assert.match(restartConfirmDetail(["a", "b"]), /^It is working on 2 open cards: a, b\./);
  const long = restartConfirmDetail([("x".repeat(80) + " "), "y".repeat(80)]);
  assert.ok(long.includes("…"), "a long list is cut, as the End dialog's is");
  assert.equal(restartConfirmTitle("web"), "Restart “web”?");
  for (const s of [bare, one]) assert.doesNotMatch(s, /\bkill|\bdestroy|\bdelete/i, "a restart is not an ending: the copy never says one");
});

test("the confirm for a session waiting on your answer to a prompt says the question goes away with the turn, in plain words", () => {
  const bare = restartConfirmDetail([], true);
  assert.equal(bare, "The turn it is running now is cut off, and the question it is asking you goes away with it. " + RESTART_STANDING);
  const one = restartConfirmDetail([OPEN_TOP], true);
  assert.match(one, /^It is working on 1 open card: Wire the notes-api health route\. The turn it is running now is cut off, and the question it is asking you goes away with it\. /);
  assert.ok(one.endsWith(RESTART_STANDING), "…and still says what the restart keeps");
});

// ── the gesture in each session state, executed over an element stand-in ─────────────────────────
// Both menus hand the row `state: <the session's status.state>` (pinned for the chat strip below, for the Sessions
// pane in sessions-menu.test.ts), and the row decides from it. Here that runs for real, the module's own row and its
// own click handler, once per chip state (every one in the two lists above), and the click is read the way the user
// meets it: a dialog first, or the op at once. The post-merge review of the restart row
// (2026-09-24) found two states with a turn in flight restarting with no dialog: a session paused on a permission
// or picker prompt (needsInput) and one riding an API auto-retry (retrying). The relaunch's interrupt cut the
// turn in both, and a pending permission question went with it. The stand-in is installed for the build alone,
// so nothing else in this file sees a document.

class StubEl {
  parentNode: StubEl | null = null;
  children: StubEl[] = [];
  className = "";
  title = "";
  tabIndex = 0;
  onScreen = false;                                  // the card's root: a row under it is connected
  private text = "";
  private attrs = new Map<string, string>();
  private clicks: Array<(ev: unknown) => void> = [];
  constructor(public tagName: string) {}
  get isConnected(): boolean { for (let n: StubEl | null = this; n; n = n.parentNode) if (n.onScreen) return true; return false; }
  get textContent(): string { return this.text + this.children.map((c) => c.textContent).join(""); }
  set textContent(v: string) { this.text = String(v); this.children = []; }
  appendChild(c: StubEl): StubEl { c.parentNode = this; this.children.push(c); return c; }
  setAttribute(k: string, v: string): void { this.attrs.set(k, String(v)); }
  getAttribute(k: string): string | null { return this.attrs.get(k) ?? null; }
  removeAttribute(k: string): void { this.attrs.delete(k); }
  hasAttribute(k: string): boolean { return this.attrs.has(k); }
  addEventListener(type: string, f: (ev: unknown) => void): void { if (type === "click") this.clicks.push(f); }
  click(): void { for (const f of this.clicks) f({ stopPropagation() { /* nothing above the card */ } }); }
  querySelector(sel: string): StubEl | null {        // ".a-class", all restart-row.ts asks of a row
    const want = sel.replace(/^\./, "");
    for (const c of this.children) {
      if (c.className.split(/\s+/).includes(want)) return c;
      const d = c.querySelector(sel);
      if (d) return d;
    }
    return null;
  }
}

interface RestartMenu {
  open: () => StubEl;                                // the menu opened on the session: a fresh card on screen, and its row
  confirms: Array<{ title: string; detail: string }>;
  posts: number;
  answer: (v: string | null) => void;                // the open dialog's verdict: "restart", or null for Cancel
}

/** The session's menu, for a session in `state`: each open() builds the row on a fresh card that is on screen, the way
 *  both menus build it. What the stand-in stands for, and where it stops: a click on a session that confirms first
 *  closes the card before the dialog opens (pick() calls closeContextMenu, which removes the page's open menu). The
 *  stand-in card is not the page's registered menu, so the dialog takes it off screen here instead, and after the
 *  dialog the only place the restart can show is a card opened again, which is what these tests read. */
function restartMenu(state: string | null | undefined): RestartMenu {
  let pending: ((v: string | null) => void) | null = null;
  const m: RestartMenu = {
    confirms: [], posts: 0,
    answer: (v) => { const cb = pending; pending = null; assert.ok(cb, "no dialog is open to answer"); cb!(v); },
    open: () => {
      const g = globalThis as any;
      const had = Object.prototype.hasOwnProperty.call(g, "document"), prev = g.document;
      g.document = { createElement: (tag: string) => new StubEl(tag.toUpperCase()) };
      try {
        const card = new StubEl("DIV");
        card.onScreen = true;
        return addRestartRow(card as unknown as HTMLElement, SID, {
          name: "web",
          state,
          titles: [OPEN_TOP],
          confirm: (title, detail, _buttons, cb) => {
            card.onScreen = false;                   // the card is gone by the time the dialog is up (closeContextMenu)
            m.confirms.push({ title, detail });
            pending = cb;
          },
          post: () => { m.posts++; },
        }) as unknown as StubEl;
      } finally {
        if (had) g.document = prev; else delete g.document;
      }
    },
  };
  return m;
}

const labelOf = (row: StubEl): string => row.querySelector(".ctx-item-label")!.textContent;

/** A click on a session in `state` asks first; Cancel posts nothing and leaves nothing latched; the dialog's own button
 *  posts the op once, and a card opened again shows the restart in flight and takes no second one. */
function confirmsFirst(state: string, why: string, question = false): void {
  try {
    const m = restartMenu(state);
    m.open().click();
    assert.equal(m.confirms.length, 1, state + " (" + why + "): the click must confirm first, but it restarted with no dialog");
    assert.equal(m.posts, 0, state + ": the click alone posts nothing");
    assert.equal(m.confirms[0].title, "Restart “web”?");
    assert.match(m.confirms[0].detail, /The turn it is running now is cut off/, state + ": the confirm says what the click costs");
    if (question) assert.match(m.confirms[0].detail, QUESTION, state + ": …and that the question it is asking you goes away with it");
    else assert.doesNotMatch(m.confirms[0].detail, /question/, state + ": it is asking you nothing, so the confirm names no question");
    m.answer(null);
    assert.equal(m.posts, 0, state + ": Cancel leaves the session, and whatever it is waiting on, as it was");
    const again = m.open();
    assert.equal(labelOf(again), RESTART_LABEL, state + ": after Cancel the menu offers the restart as before");
    again.click();
    assert.equal(m.confirms.length, 2, state + ": a second click asks again");
    m.answer("restart");
    assert.equal(m.posts, 1, state + ": the dialog's Restart posts the op once");
    assert.equal(restartInFlight(SID), true, state + ": …and the restart is in flight for this session");
    const reopened = m.open();
    assert.equal(labelOf(reopened), RESTART_BUSY_LABEL, state + ": a card opened again shows Restarting…");
    assert.equal(reopened.getAttribute("aria-disabled"), "true", state + ": …and the row is disabled");
    reopened.click();
    assert.equal(m.confirms.length, 2, state + ": a click on it asks nothing more");
    assert.equal(m.posts, 1, state + ": …and posts nothing more");
  } finally {
    settleRestart(SID);
  }
}

test("a session PAUSED ON A PERMISSION OR PICKER PROMPT (needsInput) confirms first: its turn is in flight, and a restart cuts it and the question it is asking", () => {
  confirmsFirst("needsInput", "a turn waiting on the user's answer to a prompt", true);
  confirmsFirst("awaiting", "the same prompt under the legacy name an older remote kernel sends", true);
});

test("a session RIDING AN API AUTO-RETRY (retrying) confirms first: the retry runs inside an open turn, which a restart cuts", () => {
  confirmsFirst("retrying", "a turn still open while the API call is retried");
});

test("the stand-in reads both outcomes: every other state with a turn in flight or background work asks first; a session with nothing running restarts at once", () => {
  for (const st of COSTS.filter((s) => s !== "needsInput" && s !== "awaiting" && s !== "retrying")) confirmsFirst(st, "a state that costs something");
  for (const st of [...FREE, "", null, undefined]) {
    try {
      const m = restartMenu(st);
      const row = m.open();
      row.click();
      assert.equal(m.confirms.length, 0, String(st) + ": nothing more is cut, so no dialog");
      assert.equal(m.posts, 1, String(st) + ": the click posts the op at once");
      assert.equal(labelOf(row), RESTART_BUSY_LABEL, String(st) + ": …and the row, still on its card, latches in place");
      assert.equal(row.getAttribute("aria-disabled"), "true");
    } finally {
      settleRestart(SID);
    }
  }
});

// ── the chat strip's half, and the kernel's: source pins ─────────────────────────────────────────

test("the chat tab menu builds the SAME row from the same module, resolved by id and never off the node under the cursor", () => {
  assert.match(RENDER, /import \{ addRestartRow, settleRestart \} from "\.\/restart-row";/);
  const menu = RENDER.slice(RENDER.indexOf("function showTabMenu("), RENDER.indexOf("ctxMenuEl = showMenuCard(menu,"));
  assert.match(menu, /addRestartRow\(menu, id, \{/);
  assert.match(menu, /state: st\?\.state,/, "the session's own state, handed to the row, which decides from it as for the Sessions pane");
  assert.match(menu, /titles: openTopTitles\(ledgers\.get\(id\)\?\.tree\)/);
  assert.match(menu, /post: \(\) => \{ vscodeApi\?\.postMessage\(\{ type: "restartSession", id \}\); \}/, "one op, the id alone");
  assert.doesNotMatch(menu, /addRestartRow\(menu, id, \{[\s\S]*?\}\);[\s\S]*addRestartRow/, "built once");
});

test("both panes re-arm the row on the KERNEL'S reply for that sid, and a failure is loud where that pane can be loud", () => {
  assert.match(RENDER, /else if \(m\.type === "restarted" && m\.id\) settleRestart\(String\(m\.id\)\);/);
  assert.match(RENDER, /else if \(m\.type === "restartFailed" && m\.id\) \{\s*\n\s*settleRestart\(String\(m\.id\)\);\s*\n\s*warnToast\(/,
    "the chat says it in the dismissible toast, the revive failure's surface");
  assert.match(PANE, /if \(m\.type === "restarted" \|\| m\.type === "restartFailed"\) \{/);
  assert.match(PANE, /window\.parent\?\.postMessage\(\{ romp: "notify", kind: "refused",/, "the Sessions pane has no toast: the shell's bell, carrying the session");
  // a page newer than its kernel: the op is refused with unknownOp and every latched row re-arms, rather than waiting forever
  assert.match(RENDER, /if \(m\.op === "restartSession"\) \{/);
  assert.match(PANE, /if \(m\.type === "unknownOp" && m\.op === "restartSession"\) \{/);
  assert.doesNotMatch(ROW, /setTimeout|setInterval|Date\.now\(\)/, "no clock anywhere in the latch: it lifts on the event and on nothing else");
});

test("the kernel answers the op off the request thread, advertises it, and never ends the session to restart it", () => {
  assert.match(KERNEL, /elif msg and msg\.get\("type"\) == "restartSession" and msg\.get\("id"\):/);
  assert.match(KERNEL, /threading\.Thread\(target=_restart_session, args=\(msg\["id"\], client\), daemon=True\)\.start\(\)/,
    "off the recv loop, as the revive beside it");
  assert.match(KERNEL, /KERNEL_WS_CAPS = \([^)]*"restartSession"[^)]*\)/, "advertised, so an older kernel's unknownOp is the degrade path");
  const door = KERNEL.slice(KERNEL.indexOf("def _restart_session("), KERNEL.indexOf("# ───────────────────── the unowned route"));
  assert.match(door, /be = Sessions\.backend_for\(sid\)/, "the owning backend does the work; nothing here duplicates the revive's resume");
  assert.match(door, /be\.relaunch\(sid\) if hasattr\(be, "relaunch"\) else sb\.SessionBackend\.relaunch\(be, sid\)/,
    "a backend with no relaunch answers the base refusal, never an AttributeError (2026-09-23); Codex has its own since 2026-09-24");
  for (const forbidden of ["_record_death", "_kill_at_end_door", '"closed"', "_reveal_chat_for"])
    assert.ok(!door.includes(forbidden), "the restart door never " + forbidden + "s: the session stays live and the focus stays where the user put it");
});

// ── the browser leg: the real Sessions pane bundle, driven in Chromium ───────────────────────────
// No fake DOM — a real engine builds the card, runs the click, opens the confirm and repaints the row.

let pw: any = null;
try { pw = requireCjs("playwright"); } catch { pw = null; }
async function inChromium(t: any, body: (browser: any) => Promise<void>): Promise<void> {
  if (!pw) { t.skip("playwright is not installed under vscode-extension; the browser leg needs it"); return; }
  let browser: any;
  try { browser = await pw.chromium.launch(); }
  catch (e) { t.skip("no playwright Chromium on this machine (npx playwright install chromium): " + String((e as Error).message).split("\n")[0]); return; }
  try { await body(browser); } finally { await browser.close(); }
}

let paneBundle: string | null = null;
function bundlePane(): string {
  if (paneBundle) return paneBundle;
  const esbuild = requireCjs("esbuild");
  const r = esbuild.buildSync({
    entryPoints: [path.join(UI, "fleet.ts")], bundle: true, write: false, format: "iife", platform: "browser", target: "es2020",
    nodePaths: [path.join(EXT, "node_modules")], external: ["*.png", "*.svg", "*.woff", "*.ttf", "../media/*.woff2"], logLevel: "silent",
  });
  assert.equal(r.outputFiles.length, 1, "the Sessions pane's entry bundles to one output");
  paneBundle = r.outputFiles[0].text as string;
  return paneBundle;
}
function panePage(): string {
  const sheet = read("styles.css").replace('@import "katex/dist/katex.min.css";', "") + "\n" + read("fleet-pane.css");
  return `<!DOCTYPE html><html><head><meta charset=utf-8><style>${sheet}</style></head>
<body><div id="fleet-search-bar"><div id="fleet-search-wrap"><input id="fleet-search" type="search" autocomplete="off" placeholder="Search"><button id="fleet-search-clear" type="button" hidden>x</button></div></div>
<div id="fleet-list"></div><div id="fleet-foot"></div><script>
window.__posted = []; window.__notified = [];
window.acquireVsCodeApi = function () { return { postMessage: function (m) { window.__posted.push(m); } }; };
window.addEventListener("message", function (e) { if (e.data && e.data.romp === "notify") window.__notified.push(e.data); });
</script><script>${bundlePane()}</script></body></html>`;
}

/** One feed frame as the kernel's push carries it: the session in `state`, with one open top. */
function feedFrame(state: string): Record<string, unknown> {
  const now = Math.floor(Date.now() / 1000);
  return { type: "feed", now, nowAt: now * 1000, sessions: [], asks: [],
    ledgers: [{ sid: SID, name: "web", color: { bg: "#3a86ff", fg: "#ffffff" }, status: { state },
      ledger: { current: null, archivedTops: [], tree: [{ id: "g1", depth: 0, done: false, text: OPEN_TOP, t: now - 60, mt: now - 60 }] } }] };
}
const deliver = (page: any, m: Record<string, unknown>): Promise<void> =>
  page.evaluate((f: Record<string, unknown>) => { window.dispatchEvent(new MessageEvent("message", { data: f })); }, m);
const postedOf = (page: any, type: string): Promise<any[]> =>
  page.evaluate((t: string) => (window as any).__posted.filter((m: any) => m && m.type === t), type);
const rowState = (page: any, sel: string): Promise<{ label: string; sub: string; disabled: boolean } | null> =>
  page.evaluate((s: string) => {
    const r = document.querySelector(s) as HTMLElement | null;
    if (!r) return null;
    return { label: (r.querySelector(".ctx-item-label")?.textContent || "").trim(),
             sub: (r.querySelector(".ctx-item-sub")?.textContent || "").trim(),
             disabled: r.getAttribute("aria-disabled") === "true" };
  }, sel);

/** The Sessions pane on a page of its own, fed one frame, its row painted. */
async function mount(browser: any, state: string): Promise<{ page: any; errors: string[] }> {
  const page = await browser.newPage({ viewport: { width: 900, height: 600 } });
  const errors: string[] = [];
  page.on("pageerror", (e: Error) => { errors.push(e.message); });
  const html = panePage();
  await page.route((u: URL) => u.href.startsWith(ORIGIN), (route: any) =>
    route.fulfill({ status: 200, contentType: "text/html", body: html }));
  await page.goto(ORIGIN + "/fleet");
  await page.waitForFunction(() => !!document.getElementById("fleet-list") && Array.isArray((window as any).__posted), null, { timeout: 10000 });
  await deliver(page, feedFrame(state));
  await page.waitForSelector(HEAD, { timeout: 10000 });
  return { page, errors };
}

test("an IDLE session restarts with no dialog: the row latches 'Restarting…' in place on the click, posts the op once, and re-arms on the kernel's restarted", { timeout: 240000 }, async (t) => {
  await inChromium(t, async (browser) => {
    const { page, errors } = await mount(browser, "ready");
    await page.click(HEAD, { button: "right" });
    await page.waitForSelector(MENU, { timeout: 5000 });
    const labels = await page.evaluate((m: string) =>
      Array.from(document.querySelectorAll(m + " .ctx-item .ctx-item-label"), (n) => (n.textContent || "").trim()), MENU);
    assert.deepEqual(labels, ["Rename", RESTART_LABEL, "Delete"], "the row is there, between the two it belongs between");

    await page.click(ROWSEL);
    await page.waitForFunction((s: string) => {
      const r = document.querySelector(s) as HTMLElement | null;
      return !!r && (r.querySelector(".ctx-item-label")?.textContent || "").indexOf("Restart") === 0 && r.getAttribute("aria-disabled") === "true";
    }, ROWSEL, { timeout: 5000 });
    assert.equal(await page.$(MENU) !== null, true, "the card stays up: the latched row IS the acknowledgement");
    assert.equal(await page.$("#confirm"), null, "an idle session takes no dialog");
    const busy = await rowState(page, ROWSEL);
    assert.equal(busy!.label, RESTART_BUSY_LABEL);
    assert.equal(busy!.disabled, true, "…and takes no second click");
    assert.deepEqual(await postedOf(page, "restartSession"), [{ type: "restartSession", id: SID }], "one op, the id alone");

    // a second click on the latched row — dispatched, since the browser's own actionability refuses an
    // aria-disabled row (which is the point): the pick's own belt is what must hold for a real double-click
    await page.dispatchEvent(ROWSEL, "click");
    assert.equal((await postedOf(page, "restartSession")).length, 1, "the latched row posts nothing more");

    await deliver(page, { type: "restarted", id: SID, name: "web" });
    await page.waitForFunction((s: string) => {
      const r = document.querySelector(s) as HTMLElement | null;
      return !!r && (r.querySelector(".ctx-item-label")?.textContent || "").trim() === "Restart session" && r.getAttribute("aria-disabled") !== "true";
    }, ROWSEL, { timeout: 5000 });
    const back = await rowState(page, ROWSEL);
    assert.equal(back!.label, RESTART_LABEL, "the row self-restores on the kernel's own reply");
    assert.equal(back!.disabled, false);
    assert.deepEqual(errors, []);
  });
});

test("a WORKING session confirms first, naming what the click interrupts: Cancel posts nothing, and the confirm's own button posts the op", { timeout: 240000 }, async (t) => {
  await inChromium(t, async (browser) => {
    const { page, errors } = await mount(browser, "working");
    await page.click(HEAD, { button: "right" });
    await page.waitForSelector(MENU, { timeout: 5000 });
    await page.click(ROWSEL);
    await page.waitForSelector("#confirm", { timeout: 5000 });
    assert.equal(await page.$(MENU), null, "the card goes first: the dialog is not layered over an open menu");
    const box = await page.evaluate(() => ({
      title: (document.querySelector("#confirm .confirm-title")?.textContent || "").trim(),
      detail: (document.querySelector("#confirm .confirm-detail")?.textContent || "").trim(),
      buttons: Array.from(document.querySelectorAll("#confirm .confirm-btn"), (b) => ({ label: (b.textContent || "").trim(), danger: b.classList.contains("danger") })),
    }));
    assert.equal(box.title, "Restart “web”?");
    assert.ok(box.detail.includes(OPEN_TOP), "the open card is named, as the End dialog names it");
    assert.ok(box.detail.includes("cut off"), "…and what the click costs");
    assert.deepEqual(box.buttons, [{ label: "Restart session", danger: false }, { label: "Cancel", danger: false }],
      "no danger mark: a restart interrupts, it does not destroy");

    await page.click("#confirm .confirm-btn:nth-child(2)");                 // Cancel
    await page.waitForFunction(() => !document.getElementById("confirm"), null, { timeout: 5000 });
    assert.deepEqual(await postedOf(page, "restartSession"), [], "Cancel posts nothing");

    await page.click(HEAD, { button: "right" });
    await page.waitForSelector(MENU, { timeout: 5000 });
    await page.click(ROWSEL);
    await page.waitForSelector("#confirm", { timeout: 5000 });
    await page.click("#confirm .confirm-btn:nth-child(1)");                 // Restart session
    await page.waitForFunction(() => !document.getElementById("confirm"), null, { timeout: 5000 });
    assert.deepEqual(await postedOf(page, "restartSession"), [{ type: "restartSession", id: SID }]);

    // the failure is loud: the shell's bell entry, carrying the session, and the latch lifts with it
    await deliver(page, { type: "restartFailed", id: SID, name: "web", text: "the session's CLI did not start (see the kernel log)" });
    await page.waitForFunction(() => (window as any).__notified.length > 0, null, { timeout: 5000 });
    const notes = await page.evaluate(() => (window as any).__notified);
    assert.equal(notes.length, 1);
    assert.equal(notes[0].sid, SID);
    assert.match(notes[0].text, /Couldn’t restart “web” — the session's CLI did not start/);
    await page.click(HEAD, { button: "right" });
    await page.waitForSelector(MENU, { timeout: 5000 });
    assert.equal((await rowState(page, ROWSEL))!.label, RESTART_LABEL, "a reopened card shows the row re-armed after the refusal");
    assert.deepEqual(errors, []);
  });
});

test("a card reopened while the restart is still in flight shows the latched row: the latch is keyed by sid, never held on a node the next push rebuilt", { timeout: 240000 }, async (t) => {
  await inChromium(t, async (browser) => {
    const { page, errors } = await mount(browser, "ready");
    await page.click(HEAD, { button: "right" });
    await page.waitForSelector(MENU, { timeout: 5000 });
    await page.click(ROWSEL);
    await page.waitForFunction((s: string) => (document.querySelector(s) as HTMLElement | null)?.getAttribute("aria-disabled") === "true", ROWSEL, { timeout: 5000 });
    await page.keyboard.press("Escape");                                    // the card goes; the restart is still in flight
    await page.waitForFunction(() => !document.querySelector(".ctx-menu"), null, { timeout: 5000 });
    await deliver(page, feedFrame("ready"));                                // …and a kernel push rebuilds the list under it
    await page.click(HEAD, { button: "right" });
    await page.waitForSelector(MENU, { timeout: 5000 });
    const again = await rowState(page, ROWSEL);
    assert.equal(again!.label, RESTART_BUSY_LABEL, "the reopened card knows the restart is still in flight");
    assert.equal(again!.disabled, true);
    await deliver(page, { type: "restarted", id: SID, name: "web" });
    await page.waitForFunction((s: string) => (document.querySelector(s) as HTMLElement | null)?.getAttribute("aria-disabled") !== "true", ROWSEL, { timeout: 5000 });
    assert.equal((await rowState(page, ROWSEL))!.label, RESTART_LABEL, "and it re-arms in place when the answer comes");
    assert.deepEqual(errors, []);
  });
});
