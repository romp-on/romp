// The pane shim's stale-banner rule, RUN rather than grepped. The shim is the JS the kernel inlines into
// every pane page (kernel.py _shim); its template is lifted from the kernel source here, rendered as the
// feed page renders it, and executed in a sandbox with a fake WebSocket, a fake clock and a fake shell. The
// rule under test: after a reconnect, the "what you see may be stale" prompt is raised by the SECOND
// KEEPALIVE arriving before the resync frame (one full heartbeat period, bracketed by two kernel heartbeats
// on this socket with no resync between them — a single keepalive can be a beat that was already queued
// when the socket was accepted), by the reconnected socket CLOSING before it, or by the shim ABANDONING it
// as quiet before it (the watchdog's tick or the foreground path: a socket the kernel accepted and never
// spoke on — abandon() disowns its onclose, so it runs the close rule itself; the why names the path that
// ARMED, reconnect-quiet or foreground-quiet), and by nothing else — no
// timer (every scenario runs the pending timers afterwards and asserts nothing fired, and asserts no timer
// is armed on open; the watchdog's interval is captured and ticked by hand); the first non-keepalive frame
// retires it; a keepalive never reaches the bundle. Also run here: the close breadcrumbs (one `wsclose` per
// socket that OPENED and was closed by the browser — an abandoned socket leaves none: the watchdog's own
// `watchdog-close` row went down the quiet socket before the abandon, and an armed socket's `-quiet` raise
// rides the redial; the redials an outage refused coalesced into one `wsconnfail` row on the next open)
// and the cap on queued breadcrumbs.
// Synthetic only (TESTHOST, no session data).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import * as vm from "node:vm";

const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

function shimJs(app: string, noStale = false, core = ""): string {
  const def = KERNEL.indexOf("def _shim(app, v=0, no_stale=False):");
  assert.ok(def > 0, "the shim renderer exists");
  const start = KERNEL.indexOf('return """', def) + 'return """'.length;
  // the tuple's first slot is the reload core (T265, its own executed test in tests/test_dashboard_auto_reload.py);
  // an empty core here leaves window.__rompReload undefined, so the shim's raise takes its fallback path; a `core` string
  // stands in for it where a test drives the shim's side of the offer (2026-09-16). The
  // fourth slot is the stale opt-out the Files page renders with (no_stale=True): a JS boolean literal.
  // The second slot is the chat pane's restart-diet read (PR 1661 round two: emitted for the chat app alone); the harness's apps are not
  // chat, so it substitutes the false the other panes carry, and the dial line compiles against it.
  // the tuple's head is pinned; its tail may or may not carry the label slot (a copy-aside run at an older base lacks it), so the
  // arguments follow the slots the slice actually has
  const end = KERNEL.indexOf('""" % (_reload_core(v), _RESTART_DIET_JS if app == "chat" else "var RESTART_DIET=false;", app,', start);
  assert.ok(end > start, "the template's format tuple is the one the test substitutes");
  const slice = KERNEL.slice(start, end);
  // the label slot: _pane_label's word for the key (kernel.py _PANE_ORDER), the capitalised key outside that list
  const LABELS: Record<string, string> = { chat: "Chat", timeline: "Sessions", fleet: "Outline", feed: "Feed", files: "Files" };
  const label = LABELS[app] || app.charAt(0).toUpperCase() + app.slice(1);
  const args = slice.includes('var LABEL="%s"')
    ? [core, "var RESTART_DIET=false;", app, label, "5", noStale ? "true" : "false", app, app]
    : [core, "var RESTART_DIET=false;", app, "5", noStale ? "true" : "false", app, app];
  let i = 0;
  return slice.replace(/%[sd]/g, () => args[i++]).replace(/%%/g, "%");
}

class Harness {
  posted: any[] = [];          // what the pane told the shell (wsStale / wsFresh / wsState)
  sent: any[] = [];            // what went up the socket (ready, clientDiag rows)
  toBundle: any[] = [];        // frames the shim handed to the bundle
  sockets: any[] = [];
  timers: Array<() => void> = [];
  interval: (() => void) | null = null;   // the progress watchdog's 5 s tick, run by hand (tick)
  visibility: Array<() => void> = [];
  now = 1_000_000;
  win: any;                    // the sandbox window (the shim hangs __rompLocalSend on it)
  bars: any[] = [];            // the bars a standalone page raised (selfBar): {text, kind, buttons}
  liveBar: any = null;         // the one bar standing (the #romp-stale-self slot)
  notified: any[] = [];        // what the shell's own write path (__rompNotify on the parent) received, synchronously
  constructor(js: string, opts: { standalone?: boolean; session?: Map<string, string>; pathname?: string; parentNotify?: boolean } = {}) {
    const h = this;
    const session = opts.session || new Map<string, string>();
    class FakeWS {
      url: string; readyState = 0; onopen: any; onmessage: any; onclose: any; onerror: any;
      constructor(url: string) { this.url = url; h.sockets.push(this); }
      send(s: string) { h.sent.push(JSON.parse(s)); }
      close() { if (this.readyState === 3) return; this.readyState = 3; this.onclose?.({ code: 1006, reason: "", wasClean: false }); }
      open() { this.readyState = 1; this.onopen?.(); }
      msg(o: any) { this.onmessage?.({ data: JSON.stringify(o) }); }
    }
    class FakeDate extends Date { static now() { return h.now; } }
    const sandbox: any = {
      window: {
        parent: opts.standalone ? undefined : Object.assign({ postMessage: (m: any) => h.posted.push(m) },   // embedded: the shell owns the banner
          opts.parentNotify ? { __rompNotify: (kind: string, text: string) => h.notified.push({ kind, text }) } : {}),
        sessionStorage: { getItem: (k: string) => (session.has(k) ? session.get(k) : ""), setItem: (k: string, v: string) => { session.set(k, String(v)); },
                          removeItem: (k: string) => { session.delete(k); } },
        dispatchEvent: (e: any) => { if (e && e.data !== undefined) h.toBundle.push(e.data); return true; },
        addEventListener: () => {}, innerWidth: 800, innerHeight: 600,
      },
      document: {
        addEventListener: (t: string, f: () => void) => { if (t === "visibilitychange") h.visibility.push(f); },
        visibilityState: "visible", getElementById: (id: string) => (id === "romp-stale-self" ? h.liveBar : null),
        // enough of a DOM for a standalone page's bar (selfBar): elements that take children and text, a body that holds ONE
        // bar at a time (the id slot), and removal
        createElement: () => { const el: any = { style: {}, dataset: {}, children: [] as any[], textContent: "", appendChild(c: any) { el.children.push(c); }, remove() { if (h.liveBar === el) h.liveBar = null; }, get firstChild() { return el.children[0] || null; } }; return el; },
        body: { appendChild: (b: any) => { h.liveBar = b; h.bars.push({ text: (b.children[0] || {}).textContent, kind: b.dataset.kind, buttons: b.children.slice(1).map((c: any) => c.textContent) }); } },
      },
      localStorage: { getItem: () => null, setItem: () => {} },
      location: { protocol: "http:", host: "TESTHOST:29855", search: "", pathname: opts.pathname || "/chat" },
      URLSearchParams: class { get() { return ""; } },
      WebSocket: FakeWS, Date: FakeDate, JSON, console,
      encodeURIComponent,
      Event: class { type: string; constructor(t: string) { this.type = t; } },
      MessageEvent: class { type: string; data: any; constructor(t: string, o: any) { this.type = t; this.data = o.data; } },
      setTimeout: (f: () => void) => { h.timers.push(f); return h.timers.length; },
      clearTimeout: () => {}, setInterval: (f: () => void) => { h.interval = f; return 1; },
      // 2026-09-07: the shim hands frames to the bundle through a MessageChannel-flushed queue. Delivered
      // synchronously here, so `toBundle` reads in wire order exactly as before; the slicing has its own tests
      // (tests/test_pane_shim_return.py). `performance` backs the page-load breadcrumb's navigation type.
      MessageChannel: class { port1: any = { onmessage: null }; port2: any; constructor() { const p1 = this.port1; this.port2 = { postMessage: (d: any) => { p1.onmessage?.({ data: d }); } }; } },
      performance: { getEntriesByType: () => [] },
    };
    sandbox.window.window = sandbox.window;
    if (opts.standalone) sandbox.window.parent = sandbox.window;   // no shell: the page is its own parent
    sandbox.sessionStorage = sandbox.window.sessionStorage;
    this.win = sandbox.window;
    vm.runInNewContext(js, sandbox);
  }
  get ws() { return this.sockets[this.sockets.length - 1]; }
  runTimers() { const t = this.timers.splice(0); for (const f of t) f(); }
  /** one tick of the progress watchdog (the shim's setInterval body) */
  tick() { assert.ok(this.interval, "the watchdog is armed"); this.interval!(); }
  stale() { return this.posted.filter((m) => m.romp === "wsStale" && !m.build).length; }
  fresh() { return this.posted.filter((m) => m.romp === "wsFresh").length; }
  diags(what: string) { return this.sent.filter((m) => m.type === "clientDiag" && m.what === what); }
  /** the bundle's ready frames that went up a socket, first send and any re-post */
  readys() { return this.sent.filter((m) => m.type === "ready").length; }
  kaReachedBundle() { return this.toBundle.some((m) => m && m.type === "ka"); }
  /** the bundle has loaded and installed its listener: its own connect handshake goes through the shim's send() */
  bundleReady() { this.win.__rompLocalSend({ type: "ready" }); }
  /** connect, the bundle loads, deliver the first frame, then drop the socket and let the redial run: a RECONNECTED socket */
  reconnected(): any {
    this.ws.open(); this.bundleReady(); this.ws.msg({ type: "feed", asks: [] });
    this.ws.close(); this.runTimers();
    assert.equal(this.sockets.length, 2, "the close redialed");
    this.ws.open();
    assert.equal(this.timers.length, 0, "no timer is armed on open");
    return this.ws;
  }
  /** the end of every scenario: whatever timers are pending fire, and the prompt count must not move */
  settles(expectStale: number) {
    this.runTimers();
    assert.equal(this.stale(), expectStale, "nothing raises the prompt later — no timer does");
  }
}

const FEED = () => new Harness(shimJs("feed"));

test("the shim dials the page's socket as the kernel's connect handler expects it", () => {
  const h = FEED();
  assert.match(h.ws.url, /^ws:\/\/TESTHOST:29855\/ws\?app=feed&delta=1&iid=/);
  h.settles(0);
});

test("resync first: a normal reconnect shows nothing, later keepalives are silent, and no timer is armed", () => {
  const h = FEED();
  const ws = h.reconnected();
  assert.equal(h.stale(), 0, "arming shows nothing");
  ws.msg({ type: "feed", asks: [] });
  assert.equal(h.stale(), 0);
  assert.equal(h.fresh(), 1, "the resync retires the (never shown) prompt");
  ws.msg({ type: "ka", dv: 0 }); ws.msg({ type: "ka", dv: 0 });
  assert.equal(h.stale(), 0, "keepalives after the resync are just keepalives");
  assert.equal(h.toBundle.filter((m) => m.type === "feed").length, 2, "both frames reached the bundle");
  assert.equal(h.kaReachedBundle(), false, "a keepalive never reaches the bundle");
  h.settles(0);
});

test("one keepalive, then the resync: nothing shows — a single beat can be one already queued when the socket was accepted", () => {
  const h = FEED();
  const ws = h.reconnected();
  ws.msg({ type: "ka", dv: 0 });
  assert.equal(h.stale(), 0, "a single keepalive is not the event");
  assert.equal(h.fresh(), 0, "…and it does not retire the arm either: it is not a resync");
  ws.msg({ type: "feed", asks: [] });
  assert.equal(h.stale(), 0);
  assert.equal(h.fresh(), 1, "the resync retires it");
  ws.msg({ type: "ka", dv: 0 });
  assert.equal(h.stale(), 0, "the count died with the arm");
  assert.equal(h.kaReachedBundle(), false);
  h.settles(0);
});

test("two keepalives with no resync between them: the kernel is alive, talking to this socket and has not resynced it — raised", () => {
  const h = FEED();
  const ws = h.reconnected();
  ws.msg({ type: "ka", dv: 0 });
  assert.equal(h.stale(), 0);
  ws.msg({ type: "ka", dv: 0 });
  assert.equal(h.stale(), 1, "the second heartbeat is the event");
  assert.equal(h.diags("stale-raise")[0].data.why, "reconnect");
  ws.msg({ type: "ka", dv: 0 });
  assert.equal(h.stale(), 1, "raised once, not per keepalive");
  ws.msg({ type: "feed", asks: [] });
  assert.equal(h.fresh(), 1, "the late resync still retires it");
  assert.equal(h.kaReachedBundle(), false);
  h.settles(1);
});

test("a delta frame the shim reassembles is the resync too: it retires the arm like a full frame", () => {
  // the connect push to a pane that already holds the slot arrives as {type:"delta"} (the kernel's view
  // deltas); the shim rebuilds the full message and hands THAT to the bundle — the resync, whatever its wire shape
  const h = FEED();
  const ws = h.reconnected();
  ws.msg({ type: "ka", dv: 0 });
  ws.msg({ type: "feed", asks: [{ itemId: "TESTSID:g1", text: "a" }], _keys: { asks: ["TESTSID:g1"] } });
  assert.equal(h.fresh(), 1);
  ws.close(); h.runTimers(); h.ws.open();
  h.ws.msg({ type: "ka", dv: 0 });
  h.ws.msg({ type: "delta", slot: "feed", base: 0, rev: 1, coll: { asks: { set: { "TESTSID:g1": { itemId: "TESTSID:g1", text: "b" } } } } });
  assert.equal(h.stale(), 0);
  assert.equal(h.fresh(), 2, "the reassembled delta retired the second arm");
  assert.equal(h.toBundle[h.toBundle.length - 1].asks[0].text, "b", "…and the bundle got the full message");
  h.ws.msg({ type: "ka", dv: 0 }); h.ws.msg({ type: "ka", dv: 0 });
  assert.equal(h.stale(), 0);
  h.settles(0);
});

test("the reconnected socket closing before its resync raises; the foreground path's own close does not", () => {
  const h = FEED();
  const ws = h.reconnected();
  ws.close();
  assert.equal(h.stale(), 1);
  h.runTimers(); h.ws.open();   // the breadcrumb was queued on the dead socket; it flushes on the redial
  assert.equal(h.timers.length, 0, "no timer is armed on open");
  assert.equal(h.diags("stale-raise")[0].data.why, "reconnect-closed");
  h.settles(1);
  // foreground fast-path: a quiet socket is closed by the pane itself — no raise for that close; the
  // reconnect it forces arms with why=foreground, and two keepalives before the resync raise as usual
  const g = FEED();
  g.ws.open(); g.ws.msg({ type: "feed", asks: [] });
  g.now += 31_000;
  for (const f of g.visibility) f();
  assert.equal(g.stale(), 0, "closing a quiet socket is not a raise");
  g.runTimers(); g.ws.open();
  assert.equal(g.timers.length, 0, "no timer is armed on open");
  g.ws.msg({ type: "ka", dv: 0 });
  assert.equal(g.stale(), 0);
  g.ws.msg({ type: "ka", dv: 0 });
  assert.equal(g.stale(), 1);
  assert.equal(g.diags("stale-raise")[0].data.why, "foreground");
  g.settles(1);
});

test("an announced restart's reconnect skips the arm once (T217); a second reconnect arms as always", () => {
  const h = FEED();
  h.ws.open(); h.ws.msg({ type: "feed", asks: [] });
  h.ws.msg({ type: "restarting" });
  h.ws.close(); h.runTimers(); h.ws.open();
  h.ws.msg({ type: "ka", dv: 0 }); h.ws.msg({ type: "ka", dv: 0 });
  assert.equal(h.stale(), 0, "the announced restart explained this reconnect");
  h.ws.msg({ type: "feed", asks: [] });
  h.ws.close(); h.runTimers(); h.ws.open();
  h.ws.msg({ type: "ka", dv: 0 }); h.ws.msg({ type: "ka", dv: 0 });
  assert.equal(h.stale(), 1, "the latch was one-shot");
  h.settles(1);
});

test("a reconnected socket that stays SILENT — no keepalive, no resync — raises when the watchdog abandons it; the redial resyncs as usual", () => {
  // the kernel accepted the reconnect and never spoke on it (a wedged kernel; a proxy that accepted and
  // forwards nothing): no keepalive arrives to count, and abandon() disowns the socket's onclose, so the
  // close rule never sees it either. Until abandon() ran that rule itself, every silent cycle re-armed from
  // zero — the loader flapped every 30 s and the prompt never came, where the old timer raised on the first.
  const h = FEED();
  const ws = h.reconnected();
  h.now += 31_000;
  h.tick();
  assert.equal(h.stale(), 1, "abandoning an armed socket is the event: nothing is coming on it");
  assert.equal(h.sockets.length, 3, "…and the same tick redialed");
  assert.equal(ws.onclose, null, "the abandoned socket is disowned: its eventual close is nobody's event");
  assert.equal(h.sent.filter((m) => m.type === "clientDiag" && m.what === "stale-raise").length, 0,
    "the breadcrumb queued rather than going down the quiet socket, which would have swallowed it");
  h.ws.open();
  assert.equal(h.timers.length, 0, "no timer is armed on open");
  const raised = h.diags("stale-raise");
  assert.equal(raised.length, 1, "…and it rode the reconnect");
  assert.equal(raised[0].data.why, "reconnect-quiet");
  assert.equal(h.diags("wsclose").length, 1, "the abandoned socket leaves no wsclose row: only the browser-reported drop did");
  assert.equal(h.diags("watchdog-close").length, 1, "the watchdog's own row, sent down the quiet socket before the abandon");
  h.ws.msg({ type: "feed", asks: [] });
  assert.equal(h.fresh(), 1, "the redial's resync retires the prompt as always");
  h.ws.msg({ type: "ka", dv: 0 }); h.ws.msg({ type: "ka", dv: 0 });
  assert.equal(h.stale(), 1, "…after which its keepalives are just keepalives");
  h.settles(1);
});

test("three silent cycles: one raise per cycle, one watchdog row each, and no wsclose for any abandoned socket", () => {
  const h = FEED();
  h.reconnected();
  for (let i = 1; i <= 3; i++) {
    h.now += 31_000;
    h.tick();
    assert.equal(h.stale(), i, "cycle " + i + " raised: the watchdog never eats an armed cycle");
    h.ws.open();
    assert.equal(h.timers.length, 0, "no timer is armed on open");
  }
  assert.equal(h.sockets.length, 5);
  assert.deepEqual(h.diags("stale-raise").map((m) => m.data.why), ["reconnect-quiet", "reconnect-quiet", "reconnect-quiet"]);
  assert.equal(h.diags("watchdog-close").length, 3);
  assert.equal(h.diags("wsclose").length, 1, "the first, browser-reported drop — and nothing for the three abandonments");
  h.settles(3);
});

test("the foreground path abandoning an ARMED quiet socket raises too; its redial arms as foreground", () => {
  const h = FEED();
  h.reconnected();                          // armed on the reconnect; the tab then slept on a socket that said nothing
  h.now += 31_000;
  for (const f of h.visibility) f();
  assert.equal(h.stale(), 1, "the tab came back to a socket that armed and then heard nothing");
  assert.equal(h.sockets.length, 3, "abandoned and redialed at once");
  h.ws.open();
  assert.equal(h.diags("stale-raise")[0].data.why, "reconnect-quiet", "named for the path that armed, not the one that abandoned");
  h.ws.msg({ type: "ka", dv: 0 }); h.ws.msg({ type: "ka", dv: 0 });
  assert.equal(h.stale(), 2, "the foreground redial armed on its own account, and its two beats raise as usual");
  assert.equal(h.diags("stale-raise")[1].data.why, "foreground");
  h.settles(2);
});

test("a redial armed as FOREGROUND that then stays silent raises when the watchdog abandons it: the why is foreground-quiet", () => {
  // the arm names the path that forced the reconnect, and a silent socket's raise keeps that name with the
  // -quiet suffix — the shim comment and docs/read-side.md name both spellings; only reconnect-quiet was
  // pinned before this
  const h = FEED();
  h.ws.open(); h.ws.msg({ type: "feed", asks: [] });   // connected, then the socket goes quiet while the tab sleeps
  h.now += 31_000;
  for (const f of h.visibility) f();                    // foregrounded: the quiet socket is abandoned (it never armed: no raise) and redialed now
  assert.equal(h.stale(), 0, "abandoning a socket that never armed is not a raise");
  assert.equal(h.sockets.length, 2, "abandoned and redialed at once");
  h.ws.open();                                          // the forced reconnect opens and arms as foreground
  assert.equal(h.timers.length, 0, "no timer is armed on open");
  h.now += 31_000;
  h.tick();                                             // the foreground redial said nothing either: the watchdog abandons it
  assert.equal(h.stale(), 1, "abandoning the armed foreground redial is the event");
  assert.equal(h.sockets.length, 3, "…and the same tick redialed");
  h.ws.open();
  assert.deepEqual(h.diags("stale-raise").map((m) => m.data.why), ["foreground-quiet"], "named for the path that armed, with the quiet suffix");
  assert.equal(h.diags("watchdog-close").length, 1);
  h.ws.msg({ type: "feed", asks: [] });
  assert.equal(h.fresh(), 1, "the redial's resync retires it");
  h.settles(1);
});

test("every close the browser reports for a socket that OPENED leaves a wsclose breadcrumb with the code, the socket's age and the quiet gap", () => {
  const h = FEED();
  h.ws.open(); h.now += 4_000; h.ws.msg({ type: "feed", asks: [] }); h.now += 2_500;
  h.ws.close();
  assert.equal(h.diags("wsclose").length, 0, "queued while the socket is down…");
  h.runTimers(); h.ws.open();
  const rows = h.diags("wsclose");
  assert.equal(rows.length, 1, "…and delivered on the reconnect");
  assert.equal(rows[0].surface, "pane-shim");
  // bundleReady, readyAcked, readyQueued all false: the bundle never said ready on this page, so the redial dialed as a fresh page (the four shapes below)
  assert.deepEqual(rows[0].data, { app: "feed", code: 1006, reason: "", wasClean: false, sinceOpenMs: 6_500, quietMs: 2_500, everConnected: true, bundleReady: false, readyAcked: false, readyQueued: false });
  assert.equal(h.diags("wsconnfail").length, 0, "no handshake failed");
});

// The wsclose row carries the dial term's inputs at the CLOSE (bundleReady, readyAcked, readyQueued; everConnected was already
// there and is true on every such row), and the kernel stamps every row with whether the socket that carried it declared the
// redial (?reconnect=1&proto=N). The queued row rides the redial, so the two together name the redial's kind in
// client-diag.jsonl (2026-09-10; the kernel half and the joined read-back are tests/test_client_diag_reconnect_stamp.py).
// A redial declares itself only once the kernel's caps frame has answered the bundle's ready; until then it dials fresh and
// re-posts the bundle's own ready message behind the flushed rows, and an acked ready is never re-sent.
const bits = (h: Harness) => { const d = h.diags("wsclose")[0].data; return [d.bundleReady, d.readyAcked, d.readyQueued]; };
/** the frames the redial socket carried from `from` on: a clientDiag row as its what, any other frame as its type */
const carried = (h: Harness, from: number) => h.sent.slice(from).map((m) => (m.type === "clientDiag" ? m.what : m.type));

test("wsclose bits, declared: the bundle said ready, the kernel answered, and the redial carries the term", () => {
  const h = FEED();
  h.ws.open(); h.bundleReady(); h.ws.msg({ type: "caps", caps: [] }); h.ws.msg({ type: "feed", asks: [] });
  h.ws.close(); h.runTimers();
  assert.match(h.ws.url, /&reconnect=1&proto=1$/, "the redial declares itself, with the wire protocol its ready named");
  const n = h.sent.length; h.ws.open();
  assert.deepEqual(bits(h), [true, true, false], "ready sent and answered, none waiting");
  assert.deepEqual(carried(h, n), ["wsclose"], "the queued row flushes and nothing follows it");
  assert.equal(h.readys(), 1, "the acked ready is not re-sent on a declared redial");
  h.settles(0);
});

test("wsclose bits, gated off: the socket died before the bundle said ready, and the redial dials as a fresh page", () => {
  const h = FEED();
  h.ws.open(); h.ws.close(); h.runTimers();
  assert.doesNotMatch(h.ws.url, /reconnect=1/, "no term: the page held nothing");
  const n = h.sent.length; h.ws.open();
  assert.deepEqual(bits(h), [false, false, false], "the bundle had not said ready at the close");
  assert.deepEqual(carried(h, n), ["wsclose"]);
  assert.equal(h.readys(), 0, "no ready to re-post: the bundle has not sent its own");
  h.settles(0);
});

test("wsclose bits, a ready during the close: it queued, the row says so, and it rides the redial ahead of the row", () => {
  // the browser holds a closing handshake open; the queued ready is the bundle's own, so the redial carries no term
  const h = FEED();
  h.ws.open(); h.ws.readyState = 2; h.bundleReady();
  h.ws.close(); h.runTimers();
  assert.doesNotMatch(h.ws.url, /reconnect=1/, "the ready is still queued for this open, so no term");
  const n = h.sent.length; h.ws.open();
  assert.deepEqual(bits(h), [true, false, true], "ready sent into the queue, unanswered, waiting");
  assert.deepEqual(carried(h, n), ["ready", "wsclose"], "the ready queued first, during the close, then the row");
  assert.equal(h.readys(), 1, "the queued ready went out once; nothing re-posted it");
  h.settles(0);
});

test("wsclose bits, an unacked ready: it left on the socket that died, and the redial dials fresh and re-posts it", () => {
  // no caps frame came back; without readyAcked and readyQueued on the row this shape read like the one above
  const h = FEED();
  h.ws.open(); h.bundleReady(); h.ws.msg({ type: "feed", asks: [] }); h.ws.close(); h.runTimers();
  assert.doesNotMatch(h.ws.url, /reconnect=1/, "no answer, no term: the redial dials fresh");
  const n = h.sent.length; h.ws.open();
  assert.deepEqual(bits(h), [true, false, false], "ready sent and gone, unanswered, none waiting");
  assert.deepEqual(carried(h, n), ["wsclose", "ready"], "the flushed row, then the re-posted ready");
  assert.equal(h.readys(), 2, "the ready went out on each socket: once before the drop, once re-posted");
  h.settles(0);
});

test("the redials an outage refuses leave ONE coalesced row on the next open, never a wsclose each", () => {
  const h = FEED();
  h.ws.open(); h.bundleReady(); h.ws.msg({ type: "feed", asks: [] });
  h.now += 1_000;
  h.ws.close();                                    // the real drop: this socket had opened
  const t0 = h.now;
  for (let i = 0; i < 2400; i++) {                 // an hour of 1.5 s redials, every handshake refused
    h.runTimers();                                 // the redial dials a new socket…
    h.now += 1_500;
    h.ws.close();                                  // …which the browser closes (1006) without it ever opening
  }
  h.runTimers(); h.ws.open();                      // the kernel is back
  const closes = h.diags("wsclose");
  assert.equal(closes.length, 1, "one wsclose: the socket that opened");
  assert.equal(closes[0].data.sinceOpenMs, 1_000, "with ITS timings, not the outage's");
  const fails = h.diags("wsconnfail");
  assert.equal(fails.length, 1, "the refused handshakes are one row");
  assert.deepEqual(fails[0].data, { app: "feed", attempts: 2400, firstFailMs: h.now - (t0 + 1_500) });
  assert.deepEqual(h.sent.slice(-1), fails, "…sent on the open, after the flushed wsclose");
  assert.equal(h.sent.filter((m) => m.type === "clientDiag").length, 2, "nothing else piled up");
});

test("queued breadcrumbs are capped while the socket is down; other queued messages are untouched", () => {
  const h = FEED();                                // never opened yet: everything queues
  for (let i = 0; i < 30; i++) h.win.__rompLocalSend({ type: "clientDiag", surface: "pane-shim", what: "probe", data: { i } });
  h.win.__rompLocalSend({ type: "activeTab", id: "TESTSID" });
  h.win.__rompLocalSend({ type: "clientDiag", surface: "pane-shim", what: "probe", data: { i: 30 } });
  assert.equal(h.sent.length, 0, "nothing goes up before the open");
  h.ws.open();
  const diag = h.sent.filter((m) => m.type === "clientDiag");
  assert.equal(diag.length, 20, "at most DIAG_QUEUE_MAX breadcrumbs ride the reconnect");
  assert.deepEqual(diag.map((m) => m.data.i), [...Array(20).keys()], "the oldest are kept: the rows about the drop that started it");
  assert.equal(h.sent.filter((m) => m.type === "activeTab").length, 1, "a non-diagnostic message is never dropped");
  assert.equal(h.sent.length, 21);
});

// The Files pane's page (kernel.py _files_page) is served with the opt-out: nothing is pushed to app=files
// (the viewer is request/response, so the kernel builds no frame for it), and without the opt-out the
// reconnect arm would never be retired by a resync that never comes, so the second keepalive raised the
// dashboard-wide "may be stale" prompt after every unannounced reconnect, for a file fetched over HTTP on
// demand, which a dropped socket cannot make stale. With it, neither the arm nor the retire runs: the
// page's own op replies (a GitHub-link answer) must not clear a prompt another pane raised either.
test("a page served with the stale opt-out never arms the prompt after a reconnect, and never retires one", () => {
  const h = new Harness(shimJs("files", true));
  assert.match(h.ws.url, /^ws:\/\/TESTHOST:29855\/ws\?app=files&delta=1&iid=/, "the same dial as every pane");
  h.ws.open(); h.bundleReady();
  h.ws.close(); h.runTimers();
  assert.equal(h.sockets.length, 2, "the close redialed");
  h.ws.open();
  h.ws.msg({ type: "ka", dv: 0 }); h.ws.msg({ type: "ka", dv: 0 }); h.ws.msg({ type: "ka", dv: 0 });
  assert.equal(h.stale(), 0, "three keepalives with no resync: nothing is raised");
  assert.equal(h.diags("stale-raise").length, 0, "and no raise breadcrumb");
  h.ws.msg({ type: "fileGitLink", reqId: 1, url: "" });   // an op reply, the only non-keepalive frame this page sees
  assert.equal(h.fresh(), 0, "an op reply retires nothing: wsFresh would clear a prompt another pane raised");
  assert.equal(h.toBundle.filter((m) => m.type === "fileGitLink").length, 1, "the reply still reaches the bundle");
  h.ws.close();
  assert.equal(h.stale(), 0, "the reconnected socket closing raises nothing either");
  h.runTimers(); h.ws.open();
  h.settles(0);
  // the control: the same script without the opt-out raises on the second keepalive (the rule the cases above run)
  const g = new Harness(shimJs("files"));
  g.reconnected();
  g.ws.msg({ type: "ka", dv: 0 }); g.ws.msg({ type: "ka", dv: 0 });
  assert.equal(g.stale(), 1, "the opt-out is the difference, not the app name");
});

// Invisible restarts (the user 2026-09-14): the reload core holds a changed build's reload on 'fresh' while the chat
// pane's redial awaits its first frame, so the reload lands on a kernel whose builds are warm. The round-two review
// found the hold armed in EVERY pane's shim: the Files page (the stale opt-out above, a page no resync frame ever
// reaches) armed it at its reconnect and never cleared it, so a deploy's reload was held forever behind a promise.
// The shim now arms it in the chat pane alone, stamped for the core's bound, and its resync frame clears it.
test("only the chat pane's reconnect arms the reload core's fresh hold, stamped, and its resync frame clears it", () => {
  const h = new Harness(shimJs("chat"));
  h.ws.open(); h.bundleReady();
  assert.equal(h.win.__rompFreshPending, undefined, "the first dial is not a reconnect: nothing to hold");
  h.ws.close();
  assert.equal(h.win.__rompFreshPending, true, "the drop arms the hold: the shell's own socket may reopen and ask /version before this pane redials");
  assert.equal(h.win.__rompFreshPendingSince, 1_000_000, "stamped at the drop, so the core can bound it");
  h.now = 1_030_000;
  h.runTimers(); h.ws.open();
  assert.equal(h.win.__rompFreshPending, true, "the reconnect keeps it");
  assert.equal(h.win.__rompFreshPendingSince, 1_000_000, "and keeps the stamp: the bound is per hold, not per arm");
  h.ws.close(); h.now = 1_050_000; h.runTimers(); h.ws.open();          // a flapping socket, or a kernel in a crash loop
  assert.equal(h.win.__rompFreshPendingSince, 1_000_000, "a re-arm inside the hold does not move the stamp (round three, low 1)");
  h.ws.msg({ type: "ka", dv: 0 });
  assert.equal(h.win.__rompFreshPending, true, "a keepalive is not the frame");
  h.ws.msg({ type: "resync", ops: [] });
  assert.equal(h.win.__rompFreshPending, false, "the first real frame clears it");
  h.now = 1_090_000; h.ws.close();
  assert.equal(h.win.__rompFreshPendingSince, 1_090_000, "the next hold stamps afresh");
  for (const [app, noStale] of [["files", true], ["settings", true], ["feed", false]] as [string, boolean][]) {
    const g = new Harness(shimJs(app, noStale));
    g.ws.open(); g.bundleReady(); g.ws.close(); g.runTimers(); g.ws.open();
    g.ws.msg({ type: "ka", dv: 0 });
    assert.equal(g.win.__rompFreshPending, undefined, app + ": a pane that is not the chat pane never arms the hold");
  }
});

// The sends hold: messages queued for a socket that has not come back hold the reload core's reload (their loss is the
// reload's cost). Unbounded, a dead socket kept a page on an old build forever after a deploy (2026-09-14); now the hold
// stands a minute, like the fresh hold, then the reload goes.
test("queued messages hold the reload for a minute, then the hold ends; a drained queue holds nothing", () => {
  const h = new Harness(shimJs("chat"));
  h.ws.open(); h.bundleReady();
  assert.equal(h.win.__rompPaneBusy(), "", "nothing queued: no hold");
  h.ws.close();
  h.win.__rompLocalSend({ type: "activeTab", id: "t1" });          // queued: the socket is down
  assert.equal(h.win.__rompPaneBusy(), "sends", "a queued message holds");
  h.now += 59_000;
  assert.equal(h.win.__rompPaneBusy(), "sends", "inside the bound it still holds");
  h.now += 2_000;
  assert.equal(h.win.__rompPaneBusy(), "", "past the bound the hold ends, the reload may go");
  assert.equal(h.posted.filter((m) => m.romp === "notify").length, 0, "the bound says nothing: whether the messages are lost is known only at the reload");
  h.runTimers(); h.ws.open();                                       // the redial drains the queue
  assert.equal(h.win.__rompPaneBusy(), "", "drained: no hold, and the stamp is cleared");
  h.ws.close(); h.win.__rompLocalSend({ type: "activeTab", id: "t2" });
  assert.equal(h.win.__rompPaneBusy(), "sends", "a new queue starts a new minute");
});

// The reload core's breadcrumb door: a hold that has stood for the bound is filed by the shell through a pane's shim, which
// sends it up its own socket as a clientDiag row of surface reload-core (the core itself names no send route).
test("the diagnostics door sends a reload-core breadcrumb up this pane's socket", () => {
  const h = new Harness(shimJs("chat"));
  h.ws.open(); h.bundleReady();
  h.win.__rompDiag("held", { reason: "build", hold: "typing", ageMs: 60000 });
  const rows = h.sent.filter((m) => m.type === "clientDiag" && m.surface === "reload-core");
  assert.equal(rows.length, 1);
  assert.deepEqual(rows[0], { type: "clientDiag", surface: "reload-core", what: "held", data: { reason: "build", hold: "typing", ageMs: 60000 } });
});

// The loss is decided at the RELOAD, from the queue's state then (the round-three review: a socket that returned and flushed
// after the bound had delivered the messages, and the bound-time line said otherwise). The reload core calls the shim's
// __rompShimPersist right before location.reload(): messages still queued go with the page and the pane says so, on the
// notify bridge the shell's bell listens for, naming itself by its LABEL, never its key.
test("the loss line is said at the reload from the queue's state then, names the pane by its label, and is silent after a flush", () => {
  const h = new Harness(shimJs("chat"));
  h.ws.open(); h.bundleReady(); h.ws.close();
  h.win.__rompLocalSend({ type: "activeTab", id: "t1" });
  assert.equal(h.win.__rompPaneBusy(), "sends", "the first walk stamps the hold");
  h.now += 61_000;
  assert.equal(h.win.__rompPaneBusy(), "", "the bound ended the hold");
  h.runTimers(); h.ws.open();                                       // the socket returned and the redial flushed the queue
  (h.win.__rompShimPersist || (() => {}))();
  assert.equal(h.posted.filter((m) => m.romp === "notify").length, 0, "delivered before the reload: no line");
  h.ws.close(); h.win.__rompLocalSend({ type: "activeTab", id: "t2" }); h.win.__rompLocalSend({ type: "activeTab", id: "t3" });
  (h.win.__rompShimPersist || (() => {}))();
  const said = h.posted.filter((m) => m.romp === "notify");   // this harness's parent has no write path: the message is the fallback
  assert.equal(said.length, 1, "still queued at the reload: the line");
  assert.equal(said[0].kind, "warn");   // field by field: the posted object lives in the sandbox's realm, so a strict deep compare reads two prototypes
  assert.equal(said[0].text, "2 messages queued for the Chat pane could not be sent before the dashboard reloaded; they were not delivered.");
  // the shell's own write path, when the parent exposes it: written synchronously, before location.reload() can take the page
  // (a message would be delivered after the reload began); nothing posted then
  const sync = new Harness(shimJs("chat"), { parentNotify: true });
  sync.ws.open(); sync.bundleReady(); sync.ws.close(); sync.win.__rompLocalSend({ type: "activeTab", id: "t1" });
  (sync.win.__rompShimPersist || (() => {}))();
  assert.deepEqual(sync.notified.map((n) => [n.kind, n.text]), [["warn", "1 message queued for the Chat pane could not be sent before the dashboard reloaded; it was not delivered."]]);
  assert.equal(sync.posted.filter((m) => m.romp === "notify").length, 0, "written, not posted");
  // every pane names itself by the label the rail shows, never by its internal key (a runtime pin: the key is interpolated)
  const LABELS: Record<string, string> = { chat: "Chat", timeline: "Sessions", fleet: "Outline", feed: "Feed", files: "Files", settings: "Settings" };
  for (const app of Object.keys(LABELS)) {
    const g = new Harness(shimJs(app, app === "files" || app === "settings"));
    g.ws.open(); g.bundleReady(); g.ws.close(); g.win.__rompLocalSend({ type: "activeTab", id: "x" });
    (g.win.__rompShimPersist || (() => {}))();
    const line = g.posted.filter((m) => m.romp === "notify")[0];
    assert.ok(line, app + ": a line");
    assert.match(line.text, new RegExp("^1 message queued for the " + LABELS[app] + " pane could not be sent before the dashboard reloaded; it was not delivered\\.$"), app);
    assert.doesNotMatch(line.text.toLowerCase(), /fleet|timeline/, app + ": no internal key in what the user reads");
  }
});

// A standalone page (no shell) reloads in the same task that ends the hold, so a bar raised then goes with the page (and
// selfBar declines while the connection bar already stands, the normal state after 30 s of a dead socket). The line is kept
// in the page's session store and shown by its next life, once.
test("a standalone page keeps the loss line at the reload for its next life on the same path, shows it once, dismissable, yielding to the connection bar", () => {
  const session = new Map<string, string>();
  const h = new Harness(shimJs("chat"), { standalone: true, session, pathname: "/chat" });
  h.ws.open(); h.bundleReady(); h.ws.close();
  h.win.__rompLocalSend({ type: "activeTab", id: "t1" }); h.win.__rompLocalSend({ type: "activeTab", id: "t2" });
  assert.equal(h.win.__rompPaneBusy(), "sends", "the first walk stamps the hold");
  h.now += 61_000;
  assert.equal(h.win.__rompPaneBusy(), "", "the bound ends the hold");
  assert.equal(session.has("romp:sendsDropped"), false, "the bound keeps nothing: the loss is decided at the reload");
  (h.win.__rompShimPersist || (() => {}))();                                        // the reload core, right before location.reload()
  assert.equal(h.posted.length, 0, "nothing posted: there is no shell");
  const text = "2 messages queued for the Chat pane could not be sent before the dashboard reloaded; they were not delivered.";
  assert.ok(session.has("romp:sendsDropped"), "kept for the page that follows the reload");
  assert.deepEqual(JSON.parse(session.get("romp:sendsDropped")!), { text, path: "/chat" }, "kept with the page's path");
  const elsewhere = new Harness(shimJs("feed"), { standalone: true, session, pathname: "/feed" });
  assert.equal(elsewhere.bars.length, 0, "another path shows nothing and leaves the record");
  assert.equal(session.has("romp:sendsDropped"), true);
  const next = new Harness(shimJs("chat"), { standalone: true, session, pathname: "/chat" });   // the re-entry after the reload
  assert.deepEqual(next.bars.map((b) => [b.text, b.kind, b.buttons]), [[text, "warn", ["Reload", "Dismiss"]]], "the next life shows the line, with a dismiss");
  assert.equal(session.has("romp:sendsDropped"), false, "and consumes it: once");
  // the warning does not hold the page's one bar slot for good: a connection bar replaces it
  next.ws.open(); next.bundleReady(); next.ws.close(); next.runTimers(); next.ws.open();
  next.ws.msg({ type: "ka", dv: 0 }); next.ws.msg({ type: "ka", dv: 0 });
  assert.equal(next.bars.length, 2, "the connection prompt was raised over it");
  assert.equal(next.bars[1].kind, "conn");
  assert.equal(next.liveBar && next.liveBar.dataset.kind, "conn", "the warning yielded the slot");
  const third = new Harness(shimJs("chat"), { standalone: true, session, pathname: "/chat" });
  assert.equal(third.bars.length, 0, "a later load says nothing");
});

// The 1715 lows, low 2: a kept record that parses to something other than a record (null, a number, an empty string, false)
// used to stay in the session store forever, since only a parse failure was consumed. Anything but a record is consumed.
test("a standalone page consumes a kept loss record that is not a record, and shows nothing", () => {
  for (const junk of ["null", "0", '""', "false", "not json"]) {
    const session = new Map<string, string>([["romp:sendsDropped", junk]]);
    const h = new Harness(shimJs("chat"), { standalone: true, session, pathname: "/chat" });
    assert.equal(h.bars.length, 0, junk + ": nothing to show");
    assert.equal(session.has("romp:sendsDropped"), false, junk + ": consumed");
  }
  const session = new Map<string, string>([["romp:sendsDropped", JSON.stringify({ text: "kept for another path", path: "/feed" })]]);
  const h = new Harness(shimJs("chat"), { standalone: true, session, pathname: "/chat" });
  assert.equal(h.bars.length, 0); assert.equal(session.has("romp:sendsDropped"), true, "a record for another path is kept for it");
});

// The reload OFFER on a standalone page (the user 2026-09-16: a newer build is offered, never taken; a same-build restart is
// invisible). The shim installs the reload core's offer hook and renders the one bar, kind "offer", with Reload (the core's
// accept) and Not now (the core's dismiss); a wording change is written into the standing bar and null takes it down. The
// frames the core reads ride through the shim: a keepalive's dv (noteDv), an unknownOp refusal (behind, and on to the bundle's
// degrade path), a reloadRequired (require: the safety valve, the core's alone). The offer yields the one bar slot to the
// connection prompt and comes back with the resync that retires it. A fake core stands in (the real one runs in
// tests/test_dashboard_auto_reload.py); its calls are the assertions.
const FAKE_CORE = `window.__rompReload={calls:[],offer:null,held:null,standing:null,inShell:function(){return false;},offered:function(){return this.standing;},
noteDv:function(dv){this.calls.push(["noteDv",dv]);},behind:function(){this.calls.push(["behind"]);},require:function(w){this.calls.push(["require",w]);},
accept:function(){this.calls.push(["accept"]);},dismiss:function(){this.calls.push(["dismiss"]);},announce:function(){},checkBoot:function(){this.calls.push(["checkBoot"]);},ended:function(){}};`;
test("a standalone page renders the core's offer as its bar with Reload and Not now, hands the core the frames it reads, and the offer yields to the connection prompt and returns", () => {
  const h = new Harness(shimJs("feed", false, FAKE_CORE), { standalone: true, pathname: "/feed" });
  const R = h.win.__rompReload;
  const live = (): any => h.liveBar;   // a fresh read: the strict asserts below narrow the field itself to null after a takedown
  assert.equal(typeof R.offer, "function", "the shim installed the offer hook on a standalone page");
  R.offer({ dv: 8, code: "", behind: false, text: "A newer romp build is ready." });
  assert.deepEqual(h.bars.map((b) => [b.text, b.kind, b.buttons]), [["A newer romp build is ready.", "offer", ["Reload", "Not now"]]]);
  R.offer({ dv: 8, code: "", behind: true, text: "a sharper line" });
  assert.equal(live().children[0].textContent, "a sharper line", "a wording change is written into the standing bar");
  assert.equal(h.bars.length, 1, "no second bar");
  live().children[2].onclick();                                                       // Not now
  assert.equal(R.calls.filter((c: any) => c[0] === "dismiss").length, 1, "Not now is the core's dismiss");
  assert.equal(h.liveBar, null, "the bar went with the decline");
  R.offer({ dv: 9, code: "", behind: false, text: "again" });
  const reload = live().children[1];
  reload.onclick();                                                                   // Reload
  assert.equal(R.calls.filter((c: any) => c[0] === "accept").length, 1, "Reload is the core's accept: persist, then reload, through its holds");
  assert.equal([reload.disabled, reload.textContent].join("|"), "true|Reloading\u2026", "acknowledged at once");
  R.offer(null);
  assert.equal(h.liveBar, null, "null takes the bar down");
  // the frames
  h.ws.open(); h.bundleReady();
  h.ws.msg({ type: "ka", dv: 9 });
  assert.equal(JSON.stringify(R.calls.filter((c: any) => c[0] === "noteDv")), JSON.stringify([["noteDv", 9]]), "the keepalive's dv rides in to the core");   // by JSON: the calls live in the sandbox's realm
  assert.equal(h.kaReachedBundle(), false);
  h.ws.msg({ type: "unknownOp", op: "labNoSuchOp" });
  assert.equal(R.calls.filter((c: any) => c[0] === "behind").length, 1, "the refusal sharpens the offer");
  assert.equal(h.toBundle.filter((m) => m && m.type === "unknownOp").length, 1, "and reaches the bundle's degrade path");
  h.ws.msg({ type: "reloadRequired", why: "a wire change" });
  assert.equal(JSON.stringify(R.calls.filter((c: any) => c[0] === "require")), JSON.stringify([["require", "a wire change"]]), "the safety valve is the core's");
  assert.equal(h.toBundle.filter((m) => m && m.type === "reloadRequired").length, 0, "and never the bundle's");
  // the one slot: the offer yields to the connection prompt and comes back with the resync
  R.standing = { dv: 9, code: "", behind: false, text: "again" };
  R.offer(R.standing);
  assert.equal(live().dataset.kind, "offer");
  h.ws.close(); h.runTimers(); h.ws.open();
  h.ws.msg({ type: "ka", dv: 9 }); h.ws.msg({ type: "ka", dv: 9 });                  // the second keepalive with no resync: the prompt
  assert.equal(live().dataset.kind, "conn", "the connection prompt took the slot");
  h.ws.msg({ type: "feed", asks: [] });                                               // the resync retires it
  assert.ok(live(), "and the offer came back");
  const back = h.bars[h.bars.length - 1];                                             // the comeback is a bar raised anew: the log's last entry
  assert.equal(back.kind + "|" + back.text, "offer|again");
  // a page in the shell installs no hook: the shell's banner speaks
  const inShell = new Harness(shimJs("feed", false, FAKE_CORE.replace("return false;", "return true;")));
  assert.equal(inShell.win.__rompReload.offer, null, "in a shell the pane renders no bar of its own");
});
