// The relay sockets' liveness watchdog + the timeline's pending-host signal (the user 2026-09-02).
//
// The audited two-minute gap: after a phone re-foreground the dashboard's relay sockets to two attached,
// healthy hosts OPENED and then delivered nothing — dead on arrival — and, unlike the pane shim's LOCAL
// socket (30s keepalive watchdog), federation.ts's remote sockets had no liveness check at all, so the
// hosts rendered as simply absent until TCP gave up ~104s later. Executed against the real manager with
// a fake WebSocket: the verdict table, the close+redial on silence, the foreground fast-path, the
// breadcrumb, and the pending set the panes and the shell read while a host is still coming. Synthetic
// only (host TESTHOST, placeholder uuids).
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { FederationManager, mergeHostTimelines, socketVerdict,
         REMOTE_STALE_MS, REMOTE_CONNECT_MS, REMOTE_REDIAL_MS, REMOTE_PROVISIONAL_MS } from "./federation";

const U = "11111111-2222-3333-4444-555555555555";

// ── the pure verdict ─────────────────────────────────────────────────────────────────────────────
test("socketVerdict: an OPEN socket silent past the keepalive bound is closed; a fresh one is left alone", () => {
  const t = 1_000_000;
  assert.equal(socketVerdict(1, t, t - 5000, t + REMOTE_STALE_MS + 1), "close", "31s of silence since open");
  assert.equal(socketVerdict(1, t, t - 5000, t + REMOTE_STALE_MS), "", "at the bound, not past it");
  assert.equal(socketVerdict(1, t + 20000, t, t + 25000), "", "a frame 5s ago keeps it");
  // silence is measured from THIS socket's own open (lastRecv stamped at onopen), never an earlier socket's traffic
  assert.equal(socketVerdict(1, 0, t, t + REMOTE_STALE_MS + 1), "close", "no frame at all → the connect stamp is the reference");
});

test("socketVerdict: a hung handshake is aborted; a CLOSED socket with no fresh attempt is redialed", () => {
  const t = 1_000_000;
  assert.equal(socketVerdict(0, 0, t, t + REMOTE_CONNECT_MS + 1), "close", "CONNECTING past 15s");
  assert.equal(socketVerdict(0, 0, t, t + 3000), "", "a young handshake is left to finish");
  assert.equal(socketVerdict(3, 0, t, t + REMOTE_REDIAL_MS + 1), "redial", "CLOSED 8s+ with no new connect = a lost retry timer");
  assert.equal(socketVerdict(3, 0, t, t + 1000), "", "the 2s onclose redial is still coming");
  assert.equal(socketVerdict(2, 0, t, t + 99999), "", "CLOSING is in flight — onclose will follow");
});

test("the bounds are the pane shim's, byte for byte (kernel keepalive 10s → stale at 30s)", () => {
  assert.equal(REMOTE_STALE_MS, 30000);
  assert.equal(REMOTE_CONNECT_MS, 15000);
  assert.equal(REMOTE_REDIAL_MS, 8000);
  assert.equal(REMOTE_PROVISIONAL_MS, 15000, "a resumed keep no frame has confirmed: 1.5 keepalive periods");
});

// ── the manager, end to end, with a fake WebSocket ──────────────────────────────────────────────
class FakeWS {
  static made: FakeWS[] = [];
  readyState = 0;
  closed = 0;
  onopen: (() => void) | null = null;
  onmessage: ((ev: any) => void) | null = null;
  onclose: ((ev: any) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) { FakeWS.made.push(this); }
  open(): void { this.readyState = 1; this.onopen && this.onopen(); }
  frame(data: any): void { this.onmessage && this.onmessage({ data: JSON.stringify(data) }); }
  close(): void { this.closed++; this.readyState = 3; }
  die(code = 1006): void { this.readyState = 3; this.onclose && this.onclose({ code, wasClean: false }); }
}

// the test owns the clock: the manager stamps connT/lastRecv with Date.now(), and the watchdog is
// ticked with an explicit `now`, so silence is advanced by hand rather than waited out
let clock = 1_000_000_000;
async function withManager(fn: (fm: any, emitted: any[], diag: any[], posted: any[]) => void | Promise<void>): Promise<void> {
  const emitted: any[] = [], diag: any[] = [], posted: any[] = [];
  const g: any = globalThis;
  const saved: Record<string, any> = {};
  const set = (k: string, v: any) => { saved[k] = { had: k in g, v: g[k] }; g[k] = v; };
  FakeWS.made = [];
  const realNow = Date.now;
  Date.now = () => clock;
  set("WebSocket", FakeWS);
  set("location", { protocol: "http:", host: "TESTHOST.local:1" });
  set("localStorage", { getItem: () => null, setItem: () => {} });
  const parent = { postMessage: (m: any) => { posted.push(m); } };
  set("window", {
    dispatchEvent: (ev: any) => { if (ev && ev.data) emitted.push(ev.data); },
    __rompLocalSend: (m: any) => { if (m && m.type === "clientDiag") diag.push(m); },
    sessionStorage: { getItem: () => "" },
    parent,
  });
  try {
    await fn(new FederationManager(), emitted, diag, posted);   // awaited: the globals must outlive an async poll
  } finally {
    Date.now = realNow;
    for (const [k, r] of Object.entries(saved)) { if (r.had) g[k] = r.v; else delete g[k]; }
  }
}
const localFeed = { type: "feed", asks: [], items: [], working: [], ledgers: [], order: [], sessions: [], now: 1000, buildId: 1 };

test("a relay socket that opens and then goes silent past the bound is abandoned with a breadcrumb, and a fresh one dialed at once", async () => {
  await withManager((fm, _emitted, diag) => {
    fm.openRemote("TESTHOST", true);
    assert.equal(FakeWS.made.length, 1, "an up host is dialed at once");
    const ws = FakeWS.made[0];
    assert.match(ws.url, /\/remote\/TESTHOST\/ws\?app=chat/, "the dial goes through the local kernel's relay");
    assert.doesNotMatch(ws.url, /token=/, "…which adds the remote's credential itself; the page never carries one (2026-09-08)");
    ws.open();
    clock += 10_000;
    fm.watchdog(clock);
    assert.equal(ws.closed, 0, "10s after open: still inside the keepalive bound");
    clock += 15_000;
    ws.frame({ type: "ka", dv: 1 });                       // a keepalive counts as life — that IS the heartbeat
    clock += 20_000;                                        // 45s since open, but only 20s since the last frame
    fm.watchdog(clock);
    assert.equal(ws.closed, 0, "the keepalive 20s ago reset the clock");
    clock += REMOTE_STALE_MS;                               // 50s of silence since that frame
    fm.watchdog(clock);
    assert.equal(ws.closed, 1, "silent past the bound → the watchdog puts the dead-on-arrival socket down");
    const crumb = diag.find((d) => d.what === "hostconn" && d.data && d.data.ev === "watchdog-close");
    assert.ok(crumb, "the close lands in the hostconn breadcrumb family, so client-diag says WHO closed it");
    assert.equal(crumb.data.host, "TESTHOST");
    assert.equal(crumb.data.why, "quiet");
    assert.ok(crumb.data.quietMs > REMOTE_STALE_MS, "…and how long it had been silent");
    // NOT waited for: a dead socket's closing handshake never completes (the browser holds CLOSING
    // ~60s before onclose) — the fresh dial happens in the same pass, and the corpse is disowned
    assert.equal(FakeWS.made.length, 2, "a fresh socket is dialed at once");
    const conn = fm.conns.get("TESTHOST");
    assert.equal(conn.ws, FakeWS.made[1], "the conn now owns the new socket");
    assert.equal(ws.onclose, null, "the abandoned socket's handlers are detached — its late onclose redials nothing");
    assert.equal(ws.onmessage, null);
    FakeWS.made[1].open();
    FakeWS.made[1].frame({ type: "feed", asks: [] });
    assert.equal(diag.filter((d) => d.data && d.data.ev === "watchdog-close").length, 1, "one crumb per abandonment");
    conn.closed = true;
  });
});

test("the foreground fast-path kills a socket still CONNECTING, whatever its age — the sleeping tab's unfinished handshake", async () => {
  await withManager((fm, _e, diag) => {
    fm.openRemote("TESTHOST", true);
    const ws = FakeWS.made[0];              // never opens (readyState 0)
    clock += 1000;
    fm.watchdog(clock);
    assert.equal(ws.closed, 0, "a 1s-old handshake is left to finish on a plain tick");
    fm.watchdog(clock, true);
    assert.equal(ws.closed, 1, "…but a foreground pass closes it now rather than waiting the handshake bound out");
    assert.equal(diag.filter((d) => d.data && d.data.ev === "watchdog-close" && d.data.why === "connecting" && d.data.foreground).length, 1);
    assert.equal(FakeWS.made.length, 2, "…and dials a fresh one in the same pass");
    fm.conns.get("TESTHOST").closed = true;
  });
});

test("a CLOSED socket whose retry timer was lost is redialed directly (the throttled-tab case)", async () => {
  await withManager((fm) => {
    fm.openRemote("TESTHOST", true);
    const conn = fm.conns.get("TESTHOST");
    conn.ws.readyState = 3;                 // closed under us with no onclose → no 2s timer armed
    clock += REMOTE_REDIAL_MS + 1000;
    fm.watchdog(clock);
    assert.equal(FakeWS.made.length, 2, "the watchdog dialed a fresh socket");
    conn.closed = true;
  });
});

test("a detached host is never touched by the watchdog, and a DOWN tunnel is never dialed by it", async () => {
  await withManager((fm) => {
    fm.openRemote("TESTHOST", false);   // /tunnels says not up → no socket
    assert.equal(FakeWS.made.length, 0);
    clock += 999_999;
    fm.watchdog(clock);
    assert.equal(FakeWS.made.length, 0, "no dial against a tunnel the kernel calls down");
    fm.openRemote("HOSTB", true);
    fm.closeRemote("HOSTB");                  // detach → closed:true
    const n = FakeWS.made.length;
    clock += 999_999;
    fm.watchdog(clock);
    assert.equal(FakeWS.made.length, n, "a detached conn is skipped, never redialed");
  });
});

// ── the Page Lifecycle `resume` stamp (the user 2026-09-07, whose dashboard froze on return) ─────
// A Chromium tab left in the background is FROZEN: no JS runs, so no frame can stamp lastRecv even
// while the socket underneath stays open and the kernel keeps heartbeating. lastRecv then measures
// "JS did not run", not "the socket went silent" — and the foreground fast-path above read that as
// 30s+ of silence and abandoned+redialed EVERY attached host on every thaw, each redial a full resend.
// The thaw fires `resume` before `visibilitychange`; stamping every OPEN socket there makes the
// foreground pass see fresh sockets and keep them. socketVerdict itself is unchanged.
test("`resume` stamps every OPEN relay socket's lastRecv — and only the open ones", async () => {
  await withManager((fm) => {
    fm.openRemote("TESTHOST", true);
    fm.openRemote("HOSTB", true);
    fm.openRemote("HOSTC", true);
    const [a, b, c] = FakeWS.made;
    a.open(); b.open();                                   // c never finishes its handshake (CONNECTING)
    clock += 45_000;                                      // frozen: no JS ran, so no frame was stamped
    fm.resumed(clock);
    assert.equal(fm.conns.get("TESTHOST").lastRecv, clock, "an open socket is stamped at the thaw");
    assert.equal(fm.conns.get("HOSTB").lastRecv, clock, "…every open socket, not just the first");
    assert.equal(fm.conns.get("HOSTC").lastRecv, 0, "a CONNECTING socket is NOT stamped — the foreground pass must still kill it");
    assert.equal(c.readyState, 0);
    for (const h of ["TESTHOST", "HOSTB", "HOSTC"]) fm.conns.get(h).closed = true;
  });
});

test("thaw: `resume` then the foreground watchdog pass closes NOTHING — and silence AFTER the resume still counts", async () => {
  await withManager((fm, _e, diag) => {
    fm.openRemote("TESTHOST", true);
    fm.openRemote("HOSTB", true);
    const [a, b] = FakeWS.made;
    a.open(); b.open();
    clock += 45_000;                                      // well past REMOTE_STALE_MS with no frame
    fm.resumed(clock);
    fm.watchdog(clock, true);                             // the visibilitychange fast-path, as on every thaw
    assert.equal(a.closed, 0, "the open socket is kept: the resume re-based its silence to now");
    assert.equal(b.closed, 0, "…for every attached host");
    assert.equal(FakeWS.made.length, 2, "no redial → no full resend from either kernel");
    assert.equal(diag.filter((d) => d.data && d.data.ev === "watchdog-close").length, 0, "nothing was put down, so no crumb");
    clock += 5000;
    fm.watchdog(clock);
    assert.equal(a.closed, 0, "the next plain tick keeps it too");
    // the stamp re-BASES the watchdog, it does not disarm it: a socket that stays silent after the
    // thaw (dead on arrival, FIN never delivered) is still abandoned (at the provisional bound since
    // 2026-09-08, see below; this tick is past either)
    clock += REMOTE_STALE_MS;
    fm.watchdog(clock);
    assert.equal(a.closed, 1, "30s+ of real silence after the resume → abandoned as before");
    assert.equal(b.closed, 1);
    assert.equal(FakeWS.made.length, 4, "…and redialed");
    for (const h of ["TESTHOST", "HOSTB"]) fm.conns.get(h).closed = true;
  });
});

test("no `resume` (a browser without Page Lifecycle, or a tab that was hidden but running): the stale verdict still closes on foreground", async () => {
  await withManager((fm, _e, diag) => {
    fm.openRemote("TESTHOST", true);
    const ws = FakeWS.made[0];
    ws.open();
    clock += 45_000;                                      // 45s with no frame and no resume = real silence
    fm.watchdog(clock, true);
    assert.equal(ws.closed, 1, "abandoned exactly as before the resume stamp existed");
    assert.equal(FakeWS.made.length, 2, "…and a fresh socket dialed in the same pass");
    const crumb = diag.find((d) => d.data && d.data.ev === "watchdog-close");
    assert.equal(crumb.data.why, "quiet");
    assert.equal(crumb.data.foreground, true);
    fm.conns.get("TESTHOST").closed = true;
  });
});

test("a CONNECTING socket is still killed on foreground after a `resume` — the stamp never reaches an unfinished handshake", async () => {
  await withManager((fm, _e, diag) => {
    fm.openRemote("TESTHOST", true);
    const ws = FakeWS.made[0];                            // never opens
    clock += 1000;
    fm.resumed(clock);
    assert.equal(fm.conns.get("TESTHOST").lastRecv, 0, "not stamped");
    fm.watchdog(clock, true);
    assert.equal(ws.closed, 1, "the foreground pass closes it now, resume or not");
    assert.equal(FakeWS.made.length, 2, "…and dials a fresh one");
    assert.equal(diag.filter((d) => d.data && d.data.ev === "watchdog-close" && d.data.why === "connecting" && d.data.foreground).length, 1);
    fm.conns.get("TESTHOST").closed = true;
  });
});

test("the document wiring: `resume` then `visibilitychange`→visible keeps an open socket; `visibilitychange` alone abandons a quiet one; hidden fires nothing", async () => {
  await withManager((fm) => {
    // a fake document that records the listeners and lets the test fire them in the browser's order
    const listeners: Record<string, Array<() => void>> = {};
    const doc = {
      visibilityState: "visible",
      addEventListener(t: string, fn: () => void) { (listeners[t] ||= []).push(fn); },
      fire(t: string) { for (const fn of listeners[t] || []) fn(); },
    };
    fm.watchLifecycle(doc);
    assert.equal((listeners.resume || []).length, 1, "one resume listener");
    assert.equal((listeners.visibilitychange || []).length, 1, "one visibilitychange listener");
    fm.openRemote("TESTHOST", true);
    const ws = FakeWS.made[0];
    ws.open();
    clock += 45_000;
    doc.fire("resume");                                   // Chromium's thaw order: resume, then visibilitychange
    doc.fire("visibilitychange");
    assert.equal(ws.closed, 0, "kept: the resume stamped it before the foreground pass ran");
    assert.equal(FakeWS.made.length, 1);
    clock += 45_000;
    doc.visibilityState = "hidden";
    doc.fire("visibilitychange");
    assert.equal(ws.closed, 0, "going HIDDEN is not a foreground pass");
    doc.visibilityState = "visible";
    doc.fire("visibilitychange");                         // no resume this time: real silence
    assert.equal(ws.closed, 1, "abandoned on foreground, exactly today's behaviour");
    assert.equal(FakeWS.made.length, 2);
    fm.conns.get("TESTHOST").closed = true;
  });
});

// A resumed keep is PROVISIONAL (review find, 2026-09-08): the stamp re-bases the watchdog, it does not vouch
// for the far end. A peer that died without a FIN reaching the browser (a laptop sleep across a network change,
// a tunnel whose local end stays open) leaves the relay socket OPEN at the thaw, so the stamp kept it and the
// host sat absent until the tick crossed REMOTE_STALE_MS, 30 s later. Now the kernel's next frame confirms the
// keep, and until one lands the watchdog runs at REMOTE_PROVISIONAL_MS; a socket already overdue BEFORE the
// freeze is not stamped at all, its gap is real and the foreground pass redials it as it did before the stamp.
test("a resumed keep is provisional: silence after the thaw is put down at REMOTE_PROVISIONAL_MS, a frame confirms it to the full bound", async () => {
  await withManager((fm, _e, diag) => {
    const listeners: Record<string, Array<() => void>> = {};
    const doc = {
      visibilityState: "visible",
      addEventListener(t: string, fn: () => void) { (listeners[t] ||= []).push(fn); },
      fire(t: string) { for (const fn of listeners[t] || []) fn(); },
    };
    fm.watchLifecycle(doc);
    fm.openRemote("TESTHOST", true);
    fm.openRemote("HOSTB", true);
    const [a, b] = FakeWS.made;
    a.open(); b.open();
    clock += 1000;
    doc.fire("freeze");
    clock += 45_000;
    doc.fire("resume");                                   // Chromium's thaw order: resume, then visibilitychange
    doc.fire("visibilitychange");
    const stamp = clock;
    assert.equal(a.closed, 0, "the thaw itself keeps both sockets: the healthy case pays nothing");
    assert.equal(b.closed, 0);
    assert.equal(fm.conns.get("TESTHOST").resumeProvisional, stamp, "the keep records the stamp it rests on");
    clock += 2000;
    b.frame({ type: "ka", dv: 1 });                       // HOSTB's kernel speaks: its keep is confirmed, a keepalive is enough
    assert.equal(fm.conns.get("HOSTB").resumeProvisional, 0);
    assert.equal(fm.conns.get("TESTHOST").resumeProvisional, stamp, "TESTHOST's is still waiting on a frame");
    clock += 10_000;                                      // 12 s since the stamp
    fm.watchdog(clock);
    assert.equal(a.closed, 0, "inside the provisional bound the socket stands");
    clock += 3001;                                        // 15.001 s since the stamp, no beat from that kernel
    fm.watchdog(clock);
    assert.equal(a.closed, 1, "put down at the provisional bound, not at 30 s: the kept socket was dead all along");
    assert.equal(b.closed, 0, "the confirmed socket stands");
    assert.equal(FakeWS.made.length, 3, "…and TESTHOST is redialed at once");
    const crumb = diag.find((d) => d.what === "hostconn" && d.data && d.data.ev === "watchdog-close");
    assert.equal(crumb.data.host, "TESTHOST");
    assert.equal(crumb.data.why, "quiet");
    assert.equal(crumb.data.quietMs, 15_001, "silence measured from the stamp");
    assert.equal(fm.conns.get("TESTHOST").resumeProvisional, 0, "the fresh socket starts unmarked: the rule was the resumed socket's");
    clock += 15_999;                                      // 29 s since HOSTB's confirming frame
    fm.watchdog(clock);
    assert.equal(b.closed, 0, "confirmed: the shorter bound no longer applies");
    clock += 1001;                                        // 30.001 s since that frame
    fm.watchdog(clock);
    assert.equal(b.closed, 1, "real silence after the confirmation is still put down at REMOTE_STALE_MS");
    for (const h of ["TESTHOST", "HOSTB"]) fm.conns.get(h).closed = true;
  });
});

test("a socket already overdue before the freeze is not stamped by `resume`: the foreground pass redials it at once", async () => {
  await withManager((fm, _e, diag) => {
    const listeners: Record<string, Array<() => void>> = {};
    const doc = {
      visibilityState: "visible",
      addEventListener(t: string, fn: () => void) { (listeners[t] ||= []).push(fn); },
      fire(t: string) { for (const fn of listeners[t] || []) fn(); },
    };
    fm.watchLifecycle(doc);
    fm.openRemote("TESTHOST", true);
    const ws = FakeWS.made[0];
    ws.open();
    const opened = clock;
    clock += 31_000;                                      // silent past REMOTE_STALE_MS while JS still ran: that gap is real
    doc.fire("freeze");
    clock += 45_000;
    doc.fire("resume");
    assert.equal(fm.conns.get("TESTHOST").lastRecv, opened, "not stamped: the resume vouches for nothing here");
    assert.equal(fm.conns.get("TESTHOST").resumeProvisional, 0);
    doc.fire("visibilitychange");
    assert.equal(ws.closed, 1, "abandoned on foreground, exactly as before the stamp existed");
    assert.equal(FakeWS.made.length, 2, "…and a fresh socket dialed in the same pass");
    const crumb = diag.find((d) => d.data && d.data.ev === "watchdog-close");
    assert.equal(crumb.data.quietMs, 76_000);
    fm.conns.get("TESTHOST").closed = true;
  });
});

test("source pin: start() installs the lifecycle listeners through watchLifecycle(document), guarded for a document-less host", () => {
  const src = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");
  const start = src.slice(src.indexOf("  start(): void {"), src.indexOf("  watchLifecycle("));
  assert.ok(start.length > 0, "start() precedes watchLifecycle()");
  assert.match(start, /try \{\s*this\.watchLifecycle\(document\);\s*\} catch/, "installed once, inside the no-document guard");
  assert.ok(!/document\.addEventListener/.test(start), "no second, un-testable listener install in start()");
});

// ── the pending-host signal: what the panes and the shell read while a host is still coming ────
test("mergeHostTimelines names attached hosts that have no lanes payload yet, and which of those sit on a dead link", () => {
  const local = { sessions: [{ id: U, name: "web" }], turns: {}, messages: [], judging: [], now: 1000 };
  const before = mergeHostTimelines({ "": local }, ["", "TESTHOST"], [], []);
  assert.deepEqual(before.pendingHosts, ["TESTHOST"], "listed by /tunnels, no lanes yet — the placeholder window");
  assert.deepEqual(before.pendingDead, []);
  const dead = mergeHostTimelines({ "": local }, ["", "TESTHOST"], [], ["TESTHOST"]);
  assert.deepEqual(dead.pendingDead, ["TESTHOST"], "pending on a closed socket = named as reconnecting");
  const after = mergeHostTimelines({ "": local, TESTHOST: { sessions: [], turns: {}, now: 1000 } }, ["", "TESTHOST"], [], []);
  assert.deepEqual(after.pendingHosts, [], "the first lanes payload — even an EMPTY one — is the retire event");
  assert.deepEqual(mergeHostTimelines({ "": local }, [""]).pendingHosts, [], "the local kernel never pends");
});

test("the merged lanes emission carries the pending set, so the timeline can draw its placeholder rows", async () => {
  await withManager((fm, emitted) => {
    fm.app = "timeline";
    fm.openRemote("TESTHOST", true);
    fm.inbound("", { type: "data", data: { sessions: [{ id: U, name: "web" }], turns: {}, messages: [], judging: [], now: 1000 } });
    const lanes = emitted.filter((m) => m.type === "data").pop();
    assert.deepEqual(lanes.data.pendingHosts, ["TESTHOST"]);
    assert.deepEqual(lanes.data.pendingDead, [], "the socket is dialed (CONNECTING), not dead");
    fm.inbound("TESTHOST", { type: "data", data: { sessions: [], turns: {}, messages: [], judging: [], now: 1000 } });
    const after = emitted.filter((m) => m.type === "data").pop();
    assert.deepEqual(after.data.pendingHosts, [], "retired by that host's own first lanes payload");
    fm.conns.get("TESTHOST").closed = true;
  });
});

test("each pane tells the shell which hosts IT still waits on — by its own channel, on change only", async () => {
  await withManager((fm, _e, _d, posted) => {
    fm.app = "feed";
    fm.openRemote("TESTHOST", true);
    fm.inbound("", localFeed);
    assert.deepEqual(posted.pop(), { romp: "hostsPending", app: "feed", hosts: ["TESTHOST"] },
      "the feed pane pends TESTHOST until its feed payload lands");
    fm.inbound("", { ...localFeed, buildId: 2 });
    assert.equal(posted.length, 0, "unchanged set → nothing re-posted");
    fm.inbound("TESTHOST", { type: "feed", asks: [], items: [], working: [], order: [], sessions: [], now: 1000 });
    assert.deepEqual(posted.pop(), { romp: "hostsPending", app: "feed", hosts: [] }, "the payload retires it");
    fm.closeRemote("TESTHOST");
  });
  await withManager((fm, _e, _d, posted) => {
    fm.app = "timeline";
    fm.openRemote("TESTHOST", true);
    fm.inbound("", { type: "data", data: { sessions: [], turns: {}, messages: [], judging: [], now: 1000 } });
    assert.deepEqual(posted.pop(), { romp: "hostsPending", app: "timeline", hosts: ["TESTHOST"] },
      "the timeline pends on the LANES channel, not the feed");
    fm.inbound("TESTHOST", localFeed);   // a feed payload from that host means nothing to the timeline pane
    assert.equal(posted.length, 0, "still pending: no lanes from it yet");
    fm.conns.get("TESTHOST").closed = true;
  });
});

test("a host that ATTACHES is pending from that moment: the poll re-emits the merged payloads it can complete", async () => {
  const g: any = globalThis;
  const hadFetch = "fetch" in g, prevFetch = g.fetch;
  g.fetch = async () => ({ ok: true, json: async () => ({ tunnels: [{ host: "TESTHOST", hasToken: true, localPort: 5, status: "up" }] }) });
  try {
    await withManager(async (fm, emitted) => {
      fm.app = "feed";
      fm.inbound("", localFeed);
      const n = emitted.length;
      await fm.poll();
      const feed = emitted.slice(n).filter((m) => m.type === "feed").pop();
      assert.ok(feed, "the attach itself re-emitted the merged feed");
      assert.deepEqual(feed.pendingHosts, ["TESTHOST"], "…already naming the host as coming");
      fm.conns.get("TESTHOST").closed = true;
    });
  } finally {
    if (hadFetch) g.fetch = prevFetch; else delete g.fetch;
  }
});

test("the kernel's recovery counter bumping while a row reads up is a hostUp: one per bump, none on a first observation or a steady poll (T291b)", async () => {
  const g: any = globalThis;
  const hadFetch = "fetch" in g, prevFetch = g.fetch;
  let seq = 4;
  g.fetch = async () => ({ ok: true, json: async () => ({ tunnels: [{ host: "TESTHOST", hasToken: true, localPort: 5, status: "up", upSeq: seq }] }) });
  try {
    await withManager(async (fm, emitted) => {
      fm.app = "feed";
      const hostUps = () => emitted.filter((m) => m && m.type === "hostUp");
      await fm.poll();                                  // first observation: the counter is recorded, never fired
      assert.equal(hostUps().length, 0, "a first observation is not a recovery");
      await fm.poll();                                  // steady: same counter, same status
      assert.equal(hostUps().length, 0, "a steady answered row fires nothing");
      seq = 5;                                          // the kernel noted a miss-then-answer while the row stayed up
      await fm.poll();
      assert.deepEqual(hostUps().map((m) => m.hosts), [["TESTHOST"]], "one hostUp for the bump, the status never having left up");
      await fm.poll();
      assert.equal(hostUps().length, 1, "…and none for the same counter again");
      fm.conns.get("TESTHOST").closed = true;
    });
  } finally {
    if (hadFetch) g.fetch = prevFetch; else delete g.fetch;
  }
});

test("…but never drops an EMPTY merged feed onto a page still waiting for its local kernel", async () => {
  const g: any = globalThis;
  const hadFetch = "fetch" in g, prevFetch = g.fetch;
  g.fetch = async () => ({ ok: true, json: async () => ({ tunnels: [{ host: "TESTHOST", hasToken: true, localPort: 5, status: "up" }] }) });
  try {
    await withManager(async (fm, emitted) => {
      fm.app = "feed";
      await fm.poll();
      assert.equal(emitted.filter((m) => m.type === "feed").length, 0, "no local feed yet → the feed hold stands (the loader stays up)");
      fm.conns.get("TESTHOST").closed = true;
    });
  } finally {
    if (hadFetch) g.fetch = prevFetch; else delete g.fetch;
  }
});

test("a page that renders no pushed channel pends nothing and tells the shell nothing: settings and files (2026-09-18)", async () => {
  // the /settings page and the file browser load this module (the gear's fan-out, the host routing) but receive no pushed
  // view, so no frame of theirs could retire a host: the settings frame's manager posted every attached host as pending
  // for good and the network panel read every connected remote as "loading sessions…" from the first gear open
  for (const app of ["settings", "files"]) {
    await withManager((fm, _e, _d, posted) => {
      fm.app = app;
      fm.openRemote("TESTHOST", true);
      assert.deepEqual(fm.pendingFor(), [], app + ": no channel to be heard on, nothing pending");
      fm.inbound("", localFeed);
      fm.inbound("", { type: "data", data: { sessions: [], turns: {}, messages: [], judging: [], now: 1000 } });
      assert.equal(posted.filter((m) => m && m.romp === "hostsPending").length, 0, app + ": never posts hostsPending");
      fm.conns.get("TESTHOST").closed = true;
    });
  }
});

test("a pane's FIRST publish posts even an empty list, so a reloaded pane replaces the list its predecessor left (2026-09-18)", async () => {
  await withManager((fm, _e, _d, posted) => {
    fm.app = "feed";
    fm.inbound("", localFeed);   // no remote attached: the fresh instance still declares its (empty) list once
    assert.deepEqual(posted.filter((m) => m && m.romp === "hostsPending").pop(), { romp: "hostsPending", app: "feed", hosts: [] },
      "the first publish is never gated by the empty signature");
    fm.inbound("", { ...localFeed, buildId: 2 });
    assert.equal(posted.filter((m) => m && m.romp === "hostsPending").length, 1, "…and posts again only on a change");
  });
});

test("the CHAT pane pends on the TAB LIST channel — the set its pin prune reads as __rompFed.pending (render.ts reachableHosts): an attached host pends from openRemote until its own tabOrder lands here, whatever else arrives; a detach retires it", async () => {
  await withManager((fm) => {
    fm.app = "chat";
    fm.openRemote("TESTHOST", true);
    assert.deepEqual(fm.pendingFor(), ["TESTHOST"], "attached and dialed, no tab list from it yet");
    fm.inbound("", { type: "tabOrder", order: [U], tabs: [{ id: U, name: "web" }] });
    assert.deepEqual(fm.pendingFor(), ["TESTHOST"], "the LOCAL list is not the remote's");
    fm.inbound("TESTHOST", localFeed);
    assert.deepEqual(fm.pendingFor(), ["TESTHOST"], "a feed payload from the host means nothing to the chat pane");
    fm.inbound("TESTHOST", { type: "tabOrder", order: [], tabs: [] });
    assert.deepEqual(fm.pendingFor(), [], "its first tab list — even an EMPTY one — retires it");
    fm.closeRemote("TESTHOST");
    assert.deepEqual(fm.pendingFor(), [], "detached: in no list at all");
  });
});

// ── the page never holds, nor sends, a remote's credential (2026-09-08) ──────────────────────────────
// /tunnels used to ship each remote's serve token to the page, and the dial URL carried it back — even
// though the relay (_remote_ws) injects that token itself. The row now says only whether one exists.
test("the poll dials the hosts that HAVE a token, and no dial URL carries a token — not even one a row still ships", async () => {
  const g: any = globalThis;
  const hadFetch = "fetch" in g, prevFetch = g.fetch;
  const LEAKED = "remote-secret-DO-NOT-USE";
  g.fetch = async () => ({ ok: true, json: async () => ({ tunnels: [
    { host: "TESTHOST", hasToken: true, localPort: 5, status: "up" },
    { host: "HOSTB", hasToken: false, localPort: 6, status: "up" },          // no admin path to it: not dialed
    { host: "HOSTC", hasToken: true, token: LEAKED, localPort: 7, status: "up" },   // an older kernel's row shape
  ] }) });
  try {
    await withManager(async (fm) => {
      fm.app = "feed";
      await fm.poll();
      assert.deepEqual([...fm.conns.keys()].sort(), ["HOSTC", "TESTHOST"], "hasToken gates the dial; HOSTB is left alone");
      assert.equal(FakeWS.made.length, 2, "one socket per dialed host");
      for (const ws of FakeWS.made) {
        assert.match(ws.url, /\/remote\/(TESTHOST|HOSTC)\/ws\?app=feed/, "through the local kernel's relay");
        assert.doesNotMatch(ws.url, /token=/, "the relay adds the remote's credential; the page sends none");
        assert.ok(!ws.url.includes(LEAKED), "a token a row still carries never leaves the page");
      }
      for (const c of fm.conns.values()) c.closed = true;
    });
  } finally {
    if (hadFetch) g.fetch = prevFetch; else delete g.fetch;
  }
});
