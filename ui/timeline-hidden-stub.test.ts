// Hidden-counterpart message STUBS (the user 2026-08-24; HORIZONTAL form later the same day — the
// angled diagonals fanned out of a lane like a starburst): a postal message whose OTHER endpoint
// has no visible lane must still show on the lane it touches — a HORIZONTAL line spanning the
// whole flight, x(sent) → x(exec), on the connectors' own MSG_DROP track offset, incoming ABOVE the lane (ending at the arrival dot ON the
// lane), outgoing BELOW it with no dot, clipped by arithmetic (no clipPath), in the SENDER's
// identity color like every full connector (the user 2026-08-24: state colors at stub alpha read as
// mud), with the arrived/in-flight state on the STROKE STYLE — dashed until the exec binds, solid
// after, keyed on the exec EVENT, never a timer. Headless draw() over the render test's DOM shim,
// asserting on the SVG child tree.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

// ---- minimal DOM shim (the timeline-render.test.ts shim: only what the view touches) ----
function makeNode(tag: string): any {
  const n: any = {
    tag, _attrs: {}, children: [] as any[], style: {}, dataset: {}, textContent: "", parentNode: null,
    classList: { _s: new Set<string>(), add(...a: string[]) { a.forEach((c) => this._s.add(c)); },
      remove(...a: string[]) { a.forEach((c) => this._s.delete(c)); },
      toggle(c: string, f?: boolean) { f ? this._s.add(c) : this._s.delete(c); }, contains(c: string) { return this._s.has(c); } },
    setAttribute(k: string, v: any) { this._attrs[k] = v; }, getAttribute(k: string) { return this._attrs[k]; },
    setAttributeNS(_n: any, k: string, v: any) { this._attrs[k] = v; }, removeAttribute(k: string) { delete this._attrs[k]; },
    appendChild(c: any) { c.parentNode = n; this.children.push(c); return c; },
    insertBefore(c: any, ref: any) { c.parentNode = n; const i = this.children.indexOf(ref); i < 0 ? this.children.push(c) : this.children.splice(i, 0, c); return c; },
    removeChild(c: any) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; },
    get firstChild() { return this.children[0] || null; },
    _listeners: {} as any,
    addEventListener(t: string, fn: any) { n._listeners[t] = fn; }, removeEventListener() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { width: 1400, height: 420, left: 0, top: 0, right: 1400, bottom: 420 }; },
    closest() { return null; }, focus() {},
    createEl(t: string, o: any) { const e = makeNode(t); if (o && o.cls) e.classList.add(o.cls); if (o && o.text) e.textContent = o.text; this.appendChild(e); return e; },
    createDiv(o: any) { return this.createEl("div", o); }, createSpan(o: any) { return this.createEl("span", o); },
  };
  return n;
}
const g: any = global;
g.document = {
  createElement(t: string) { return t === "canvas" ? { getContext() { return { font: "", measureText(s: string) { return { width: (s ? s.length : 0) * 6 }; } }; } } : makeNode(t); },
  createElementNS(_n: any, t: string) { return makeNode(t); },
  body: makeNode("body"), documentElement: makeNode("html"), head: makeNode("head"),
  getElementById() { return null; },
  addEventListener() {}, removeEventListener() {},
};
g.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
g.getComputedStyle = () => ({ backgroundColor: "rgb(30,30,30)" });
g.requestAnimationFrame = () => 0;
g.addEventListener = () => {}; g.removeEventListener = () => {};
g.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
g.window = g;
g.innerWidth = 1400; g.innerHeight = 800;

const viewPath = path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js");
const { TimelinePanel } = createRequire(__filename)(viewPath);
const SRC = fs.readFileSync(viewPath, "utf8");

const ADA = "#f7768e", BEE = "#7aa2f7";   // sender identity colors (synthData below) — a stub wears its SENDER's
const OFF = 10;             // MSG_DROP — the stub's track offset, the connectors' own approach height

// Four sessions in a FIXED sort order — ada/cee view-hidden, bee/dee visible. The hidden ones
// bracket bee so both slope directions (toward-above and toward-below) are exercised.
function synthData(): any {
  const now = 1_781_000_000;
  const turn = (id: string, dt0: number, dt1: number) => ({
    id, promptId: id + "#p", workId: id + "#w",
    start: now - dt0, end: now - dt1, prompt: "do the thing", src: "typed", mids: [],
    pending: false, summary: "did the thing", reply: "did it", tid: "fork-" + id, uuid: "u-" + id,
    workUuid: "w-" + id, replyUuid: "r-" + id,
  });
  const sess = (id: string, name: string, color: string) => ({
    id, name, color, state: "working", live: true, model: "Opus 4.8", effort: "xhigh",
    context: 40, since: now - 60, awaiting: [], compacting: [], pendingMail: 0, compactions: [], faded: false, stale: false,
  });
  return {
    now,
    sessions: [sess("SA", "ada", "#f7768e"), sess("SB", "bee", "#7aa2f7"), sess("SC", "cee", "#9ece6a"), sess("SD", "dee", "#bb9af7")],
    turns: { SB: [turn("SB:1:aa", 300, 60)], SD: [turn("SD:1:bb", 250, 40)] },
    views: { active: "untagged", hidden: [], tags: [{ id: "gx", name: "pool", color: "", members: ["SA", "SC"] }] },   // tag-hidden (the hidden set retired 2026-08-24)
    messages: [], activeChat: null, focus: null, hover: null, usage: null,
  };
}

function draw(messages: any[]): any {
  const panel: any = new TimelinePanel(makeNode("div"));
  const d = synthData();
  d.messages = messages;
  panel.update(d);
  return panel;
}
function nodes(panel: any): any[] {
  const out: any[] = [];
  (function walk(n: any) { for (const c of n.children || []) { out.push(c); walk(c); } })(panel.svg);
  return out;
}
// ONE PATH per stub since round 4 (two <line>s double-painted the elbow corner at 0.45 alpha):
// the helpers parse the path's segments back into the line-shaped views the geometry pins read —
// stubLines yields the HORIZONTAL track segment, riserLines the VERTICAL limb, of the same element.
const pathPts = (n: any) => (String(n._attrs.d).match(/-?[\d.]+/g) || []).map(Number);
const stubPathEls = (panel: any, hex?: string) =>
  nodes(panel).filter((n) => n.tag === "path" && n._attrs["stroke-width"] === 3 && n._attrs.opacity === 0.45
    && (!hex || n._attrs.stroke === hex));
const segView = (n: any, i: number, p: number[]) =>
  ({ _attrs: { ...n._attrs, x1: p[i], y1: p[i + 1], x2: p[i + 2], y2: p[i + 3] } });
const stubLines = (panel: any, hex?: string) => stubPathEls(panel, hex).map((n) => {
  const p = pathPts(n);
  for (let i = 0; i + 3 < p.length; i += 2) if (p[i + 1] === p[i + 3]) return segView(n, i, p);
  return segView(n, 0, p);
});
const riserLines = (panel: any) => stubPathEls(panel).flatMap((n) => {
  const p = pathPts(n);
  const out: any[] = [];
  for (let i = 0; i + 3 < p.length; i += 2)
    if (p[i] === p[i + 2] && p[i + 1] !== p[i + 3]) out.push(segView(n, i, p));
  return out;
});
const connPaths = (panel: any, color: string) =>
  nodes(panel).filter((n) => n.tag === "path" && n._attrs.fill === "none" && n._attrs.stroke === color && (n._attrs.opacity === 0.5 || n._attrs.opacity === 0.4));
// expected coordinates from the SAME geometry draw() used (compress map included)
const X = (panel: any) => (t: number) => { const gm = panel._geom; return gm.ml + (gm.compress(t) - gm.cT0) / gm.winSec * gm.plotW; };
const laneY = (panel: any) => (i: number) => panel._geom.top + i * 26 + 13;
const near = (a: number, b: number, msg: string) => assert.ok(Math.abs(a - b) < 1e-6, msg + " (got " + a + ", want " + b + ")");

test("a hidden-recipient message draws an outgoing stub: send-anchored, horizontal just BELOW the lane, sender-colored and dashed while pending, no dot", () => {
  const now = 1_781_000_000;
  const panel = draw([{ id: "m1", fromId: "SB", toId: "SC", from: "bee", to: "cee",
                        sent: now - 300, exec: now - 300, hasExec: false, pending: true, summary: "on its way" }]);
  const [s, ...rest] = stubLines(panel);
  assert.ok(s, "the stub line draws");
  assert.equal(rest.length, 0, "exactly one stub");
  assert.equal(s._attrs.stroke, BEE, "the stub wears its SENDER's identity color — the connector language");
  assert.equal(s._attrs["stroke-dasharray"], "1 4", "no exec yet → dashed, the pending-connector idiom");
  assert.ok(s._attrs.opacity < 1, "faded — subordinate to the work bars");
  near(s._attrs.x1, X(panel)(now - 300), "anchored at the send x");
  near(s._attrs.y1, laneY(panel)(0) + OFF, "OUTGOING rides just BELOW the lane line — the fixed convention");
  near(s._attrs.y2, s._attrs.y1, "…and stays horizontal: no angles (the starburst read)");
  // full span, not the old 13px nub (the user 2026-08-24 regression): out to the live edge while
  // pending — sub-pixel slack because the panel's live edge interpolates a hair off the fixture now
  assert.ok(Math.abs(s._attrs.x2 - X(panel)(now)) < 0.5, "ends at the live edge (got " + s._attrs.x2 + ")");
  assert.ok(Math.abs((s._attrs.x2 - s._attrs.x1) - (X(panel)(now) - X(panel)(now - 300))) < 0.5,
    "…the span IS the flight: send to live edge, however long — the run cap is gone");
  assert.ok(s._attrs.x2 - s._attrs.x1 > 13.5, "…and it beats the old 13px cap on this fixture");
  assert.ok(!nodes(panel).some((n) => n.tag === "circle" && Math.abs(n._attrs.cx - s._attrs.x2) < 1e-6),
    "an outgoing stub has NO dot — the dot is the ARRIVAL mark, and the arrival is off-view");
  // the RISER (third stub round): the half-elbow limb tying the track to the work bar at the SEND x
  const [r, ...rrest] = riserLines(panel);
  assert.ok(r, "the riser draws");
  assert.equal(rrest.length, 0, "exactly one riser");
  near(r._attrs.x1, s._attrs.x1, "…at the send x — the message visibly leaves the work");
  near(r._attrs.y1, laneY(panel)(0), "…from the bar");
  near(r._attrs.y2, s._attrs.y1, "…down to the track");
  assert.equal(r._attrs["stroke-dasharray"], "1 4", "the riser wears the stub's own dash");
  assert.equal(r._attrs.stroke, BEE, "…and its color");
  assert.equal(s._attrs.opacity, 0.45, "the stub fade level exactly — not invisible, not full-strength");
  assert.equal(s._attrs["pointer-events"], "none", "the visible mark never eats the hover — the hit target owns it");
  const hl = nodes(panel).find((n) => n.tag === "path" && n._attrs.stroke === BEE && n._attrs["stroke-width"] === 6);
  assert.ok(hl && hl._attrs.opacity === 0, "the own-color highlight overlay rides the stub, dark until hover/DAG");
  assert.equal(hl._attrs["stroke-dasharray"], undefined, "the highlight stays solid — the full-connector hl idiom");
  assert.equal(hl._attrs["pointer-events"], "none", "the highlight overlay is inert too");
  const hit = nodes(panel).find((n) => n.tag === "path" && n._attrs.stroke === "transparent" && n._attrs["stroke-width"] === 18);
  assert.ok(hit, "a wide transparent hit target covers the stub");
  // spec item 4: the SAME affordances as a full connector — tooltip via showTip/moveTip/hideTip,
  // the __tlHoverIn re-arm (a kernel-push redraw rebuilds the node; _rehover re-enters through it),
  // and the connector's click (jump to where the message landed, by the message's own id)
  assert.equal(typeof hit.__tlHoverIn, "function", "__tlHoverIn re-arm is wired");
  assert.equal(hit.style.cursor, "pointer", "the hit target reads as clickable");
  for (const ev of ["mouseenter", "mousemove", "mouseleave", "click"])
    assert.equal(typeof hit._listeners[ev], "function", ev + " is wired");
  hit._listeners.mouseenter({ clientX: 200, clientY: 30, currentTarget: hit });
  assert.equal(hl._attrs.opacity, "0.95", "hover lights the highlight overlay");
  assert.ok(panel.tip.classList.contains("show"), "hover shows the tooltip (showTip)");
  assert.ok(String(panel.tip.innerHTML).includes(">bee<") && String(panel.tip.innerHTML).includes(">cee<"),
    "the tooltip names BOTH endpoints — the hidden counterpart resolves from the row, not the lanes");
  hit._listeners.mouseleave({});
  assert.equal(hl._attrs.opacity, "0", "leave restores the dark overlay");
  const calls: any[] = [];
  panel._select = (id: string) => calls.push(["select", id]);
  panel.openChat = (...a: any[]) => calls.push(["open", ...a]);
  hit._listeners.click({});
  assert.deepEqual(calls[0], ["select", "SC"], "click selects the recipient — where the message landed");
  assert.equal(calls[1] && calls[1][2], "m1", "click deep-links the message's OWN postal card by id");
  assert.equal(connPaths(panel, "#7aa2f7").length, 0, "no full connector is drawn for a half-hidden message");
});

test("the stub goes solid for a heuristic-bound exec too — exec moved off its sent fallback, hasExec never logged", () => {
  const now = 1_781_000_000;
  // the kernel's _bind_message_execs text-heuristic path writes exec/pending but no exec event is
  // ever logged, so the row ships hasExec:false with a REAL landing time — that is exec knowledge
  const panel = draw([{ id: "m10", fromId: "SB", toId: "SC", from: "bee", to: "cee",
                        sent: now - 200, exec: now - 50, hasExec: false, pending: false, summary: "heuristic-bound" }]);
  const [s] = stubLines(panel);
  assert.ok(s, "the stub draws");
  assert.equal(s._attrs["stroke-dasharray"], undefined, "a bound exec is exec knowledge, with or without the logged event — solid");
  assert.equal(s._attrs.stroke, BEE, "…still the sender's color: state never recolors a stub");
});

test("the midStart join upgrades the stub to solid — the join IS exec knowledge (hasExec set)", () => {
  const now = 1_781_000_000;
  // recipient turn's mids carry the message id and its start EQUALS the sent time, so the ONLY
  // solid source is the join setting hasExec — pins the mm.hasExec = true half of this change
  const panel: any = new TimelinePanel(makeNode("div"));
  const d = synthData();
  d.turns.SC = [{ id: "SC:1:cc", promptId: "SC:1:cc#p", workId: "SC:1:cc#w", start: now - 300, end: now - 200,
                  prompt: "x", src: "typed", mids: ["m11"], pending: false, summary: "s", reply: "r",
                  tid: "fork-SC", uuid: "u-SC", workUuid: "w-SC", replyUuid: "r-SC" }];
  d.messages = [{ id: "m11", fromId: "SB", toId: "SC", from: "bee", to: "cee",
                  sent: now - 300, exec: now - 300, hasExec: false, pending: true, summary: "joined" }];
  panel.update(d);
  assert.equal(panel.data.messages[0].hasExec, true, "the join marks the exec as known");
  const [s] = stubLines(panel);
  assert.ok(s, "the stub draws");
  assert.equal(s._attrs["stroke-dasharray"], undefined, "joined landing → solid even with exec equal to sent");
});

test("the stub flips dashed→solid on the exec event — hasExec, never the staleness timer", () => {
  const now = 1_781_000_000;
  const landed = draw([{ id: "m2", fromId: "SB", toId: "SC", from: "bee", to: "cee",
                         sent: now - 300, exec: now - 30, hasExec: true, pending: false, summary: "landed" }]);
  const [ys] = stubLines(landed);
  assert.ok(ys, "the arrived stub draws");
  assert.equal(ys._attrs["stroke-dasharray"], undefined, "exec bound → solid, it arrived");
  // a STALE in-flight message (kernel: pending aged out by MSG_INFLIGHT_MAX, still no exec) must NOT
  // go solid — the stroke keys on the exec event, and no exec event ever happened here
  const stale = draw([{ id: "m3", fromId: "SB", toId: "SC", from: "bee", to: "cee",
                        sent: now - 400, exec: now - 400, hasExec: false, pending: false, summary: "never seen landing" }]);
  const [gs] = stubLines(stale);
  assert.ok(gs, "the stale stub still draws");
  assert.equal(gs._attrs["stroke-dasharray"], "1 4", "no exec event → still dashed (the timer is not the key)");
  assert.equal(gs._attrs.stroke, BEE, "…and still the sender's color, stale or not");
  // the SPAN keys on the same arrival knowledge as the dash (the user 2026-08-24, floating-point
  // regression): aging pending out collapsed execAt to the exec=sent fallback and the stub drew as
  // a zero-length point — un-arrived mail spans sent → the LIVE EDGE, however old
  assert.ok(Math.abs(gs._attrs.x2 - X(stale)(now)) < 0.5, "un-arrived → out to the live edge, not a point");
  assert.ok(gs._attrs.x2 - gs._attrs.x1 > 13.5, "…a real span (got " + (gs._attrs.x2 - gs._attrs.x1) + ")");
  near(gs._attrs.y2, laneY(stale)(0) + OFF, "still on its outgoing track below the lane");
});

test("a message to a DEAD counterpart with a REAL exec row spans sent to exec — never a point", () => {
  // leg (b) of the 2026-08-24 floating-point report: the kernel joins exec rows by id with no
  // liveness gate (pinned kernel-side in test_kernel.py), so the view gets hasExec + a real exec
  // even when the counterpart session/thread is dead — the span must render exactly like live mail
  const now = 1_781_000_000;
  const panel = draw([{ id: "m14", fromId: "SB", toId: "SX", from: "bee", to: "gone-thread",
                        sent: now - 300, exec: now - 60, hasExec: true, pending: false, summary: "consumed, then died" }]);
  const [s] = stubLines(panel);
  assert.ok(s, "the stub draws");
  assert.equal(s._attrs["stroke-dasharray"], undefined, "a real exec → solid");
  assert.ok(Math.abs((s._attrs.x2 - s._attrs.x1) - (X(panel)(now - 60) - X(panel)(now - 300))) < 0.5,
    "the true sent→exec span, into the counterpart's own work period");
});

test("a same-instant consume keeps a visible mark — zero-length is impossible by geometry", () => {
  const now = 1_781_000_000;
  const panel = draw([{ id: "m15", fromId: "SB", toId: "SX", from: "bee", to: "gone",
                        sent: now - 200, exec: now - 200, hasExec: true, pending: false, summary: "instant" }]);
  const [s] = stubLines(panel);
  assert.ok(s, "the stub draws");
  assert.ok(s._attrs.x2 - s._attrs.x1 >= 2, "the 2px floor: a real instant consume is a mark, not a floating point");
});

test("a hidden-sender message draws the mirror stub: horizontal just ABOVE the lane, ending at the arrival dot's x, dot kept on top", () => {
  const now = 1_781_000_000;
  const panel = draw([{ id: "m4", fromId: "SA", toId: "SB", from: "ada", to: "bee",
                        sent: now - 400, exec: now - 100, hasExec: true, pending: false, summary: "delivered" }]);
  const [s] = stubLines(panel);
  assert.ok(s, "the incoming stub draws");
  assert.equal(s._attrs.stroke, ADA, "the HIDDEN sender's color paints the incoming stub — sender, both directions");
  assert.equal(s._attrs["stroke-dasharray"], undefined, "already executed → solid");
  near(s._attrs.x2, X(panel)(now - 100), "ends at the exec x — where the arrival dot sits on the lane");
  near(s._attrs.y2, laneY(panel)(0) - OFF, "INCOMING rides just ABOVE the lane line — the fixed convention");
  near(s._attrs.y1, s._attrs.y2, "…and stays horizontal");
  near(s._attrs.x1, X(panel)(now - 400), "the FULL span back to the send x — no run cap");
  const [r] = riserLines(panel);
  assert.ok(r, "the incoming riser draws — the message visibly enters the work");
  near(r._attrs.x1, s._attrs.x2, "…at the arrival x");
  near(r._attrs.y1, s._attrs.y2, "…from the track");
  near(r._attrs.y2, laneY(panel)(0), "…down to the bar, meeting the arrival dot");
  assert.equal(r._attrs["stroke-dasharray"], undefined, "solid, like its arrived stub");
  const kids = panel.svg.children;
  const dotI = kids.findIndex((n: any) => n.tag === "circle" && Math.abs(n._attrs.cx - X(panel)(now - 100)) < 1e-6);
  assert.ok(dotI >= 0, "the arrival dot still draws on the recipient lane");
  const hitI = kids.findIndex((n: any) => n.tag === "path" && n._attrs.stroke === "transparent" && n._attrs["stroke-width"] === 18);
  assert.ok(hitI > dotI, "the stub's hit target is appended after the dots (PASS 3), so the line wins the hover");
});

test("overlapping message marks hover as ONE modal listing them all, every unit lit together", () => {
  // the user 2026-08-24: stacked marks (several exchanges on one pair, a stub riding another's
  // track) let the topmost hit swallow the hover — the modal named one message where the cursor
  // covered several. elementsFromPoint resolves the whole paint stack; hosts without it (this
  // shim's default) keep the old single-message read, which the earlier tooltip tests still pin.
  const now = 1_781_000_000;
  const panel = draw([
    { id: "m18", fromId: "SB", toId: "SC", from: "bee", to: "cee",
      sent: now - 300, exec: now - 240, hasExec: true, pending: false, summary: "first of the pile" },
    { id: "m19", fromId: "SB", toId: "SC", from: "bee", to: "cee",
      sent: now - 200, exec: now - 140, hasExec: true, pending: false, summary: "second of the pile" },
  ]);
  const hits = nodes(panel).filter((n) => n.tag === "path" && n._attrs.stroke === "transparent" && n._attrs["stroke-width"] === 18);
  const hls = nodes(panel).filter((n) => n.tag === "path" && n._attrs["stroke-width"] === 6);
  assert.equal(hits.length, 2); assert.equal(hls.length, 2);
  (globalThis as any).document.elementsFromPoint = () => hits;   // both marks under the cursor
  try {
    hits[0]._listeners.mouseenter({ clientX: 200, clientY: 30, currentTarget: hits[0] });
    const tip = String(panel.tip.innerHTML);
    assert.ok(tip.includes("first of the pile") && tip.includes("second of the pile"),
      "the modal lists ALL messages under the point, oldest first");
    assert.ok(tip.indexOf("first of the pile") < tip.indexOf("second of the pile"), "…oldest first");
    assert.equal(hls[0]._attrs.opacity, "0.95", "every covered unit lights");
    assert.equal(hls[1]._attrs.opacity, "0.95");
    hits[0]._listeners.mouseleave({});
    assert.equal(hls[0]._attrs.opacity, "0", "…and every one restores on leave");
    assert.equal(hls[1]._attrs.opacity, "0");
  } finally {
    delete (globalThis as any).document.elementsFromPoint;
  }
});

test("a stub is ONE path — riser and track share a single stroke, so the corner never double-paints", () => {
  // round 4 (the user 2026-08-24): two <line>s overlapped at the elbow and the 0.45 alpha stacked
  // to a darker joint; the connectors' own elbow-path idiom draws it contiguous
  const now = 1_781_000_000;
  const panel = draw([{ id: "m17", fromId: "SB", toId: "SC", from: "bee", to: "cee",
                        sent: now - 300, exec: now - 300, hasExec: false, pending: true, summary: "one stroke" }]);
  assert.equal(stubPathEls(panel).length, 1, "one path element per stub");
  assert.ok(!nodes(panel).some((n) => n.tag === "line" && n._attrs["stroke-width"] === 3 && n._attrs.opacity === 0.45),
    "…and no separate stub/riser <line> elements remain");
  const STUBFN = SRC.slice(SRC.indexOf("const stub = (mm, i, senderVisible)"), SRC.indexOf("// PASS 1:"));
  assert.doesNotMatch(STUBFN, /el\('line'/, "the stub function draws paths only");
  assert.match(STUBFN, /'stroke-linejoin': 'round'/, "the connectors' own round joint");
});

test("an un-arrived INCOMING stub has no riser — nothing entered the work, and its live-edge x is not a real arrival", () => {
  const now = 1_781_000_000;
  const panel = draw([{ id: "m16", fromId: "SA", toId: "SB", from: "ada", to: "bee",
                        sent: now - 120, exec: now - 120, hasExec: false, pending: true, summary: "still coming" }]);
  const [s] = stubLines(panel);
  assert.ok(s, "the un-arrived incoming stub itself draws (dashed, to the live edge)");
  assert.equal(riserLines(panel).length, 0,
    "…but no riser: a limb at a clamped/live-edge x would tie the work to a false arrival");
});

test("a counterpart absent from data.sessions rides the same fixed track: outgoing = below", () => {
  const now = 1_781_000_000;
  const panel = draw([{ id: "m5", fromId: "SB", toId: "SX", from: "bee", to: "gone",
                        sent: now - 200, exec: now - 50, hasExec: true, pending: false, summary: "to a cleared session" }]);
  const [s] = stubLines(panel);
  assert.ok(s, "the stub draws even for a counterpart nowhere in data.sessions");
  near(s._attrs.y1, laneY(panel)(0) + OFF, "outgoing = below the lane, whoever the counterpart is");
  near(s._attrs.y2, s._attrs.y1, "horizontal — the direction convention carries the meaning, not a slope");
  // the tooltip must name BOTH endpoints even with no counterpart row in data.sessions at all
  // (the user 2026-08-24): the names come from the message ROW itself, never from visible lanes
  const hit = nodes(panel).find((n) => n.tag === "path" && n._attrs.stroke === "transparent" && n._attrs["stroke-width"] === 18);
  hit._listeners.mouseenter({ clientX: 200, clientY: 30, currentTarget: hit });
  assert.ok(String(panel.tip.innerHTML).includes(">bee<"), "the visible sender is named");
  assert.ok(String(panel.tip.innerHTML).includes(">gone<"), "…and the ABSENT counterpart is named, from the row");
});

test("a nameless row still names both ends — the raw id backstops a missing display name", () => {
  const now = 1_781_000_000;
  // a row the kernel could not resolve a display name for (cleared/foreign session) must not
  // render an empty who-span: the raw id is information, not noise
  const panel = draw([{ id: "m13", fromId: "SB", toId: "SX", from: "bee", to: "",
                        sent: now - 200, exec: now - 50, hasExec: true, pending: false, summary: "to a nameless one" }]);
  const hit = nodes(panel).find((n) => n.tag === "path" && n._attrs.stroke === "transparent" && n._attrs["stroke-width"] === 18);
  hit._listeners.mouseenter({ clientX: 200, clientY: 30, currentTarget: hit });
  assert.ok(String(panel.tip.innerHTML).includes(">SX<"), "the raw id names the endpoint — never an empty span");
});

test("both endpoints hidden → no stub, no connector", () => {
  const now = 1_781_000_000;
  const panel = draw([{ id: "m6", fromId: "SA", toId: "SC", from: "ada", to: "cee",
                        sent: now - 200, exec: now - 50, hasExec: true, pending: false, summary: "between hidden lanes" }]);
  assert.equal(stubLines(panel).length, 0, "no stub for a fully hidden flow");
  assert.equal(connPaths(panel, "#f7768e").length, 0, "no connector either");
  const lx = X(panel)(now - 50);
  assert.ok(!nodes(panel).some((n) => n.tag === "circle" && Math.abs(n._attrs.cx - lx) < 1e-6),
    "no arrival dot draws for a fully hidden flow");
});

test("two visible lanes keep today's full connector — no stub elements", () => {
  const now = 1_781_000_000;
  const panel = draw([{ id: "m7", fromId: "SB", toId: "SD", from: "bee", to: "dee",
                        sent: now - 200, exec: now - 50, hasExec: true, pending: false, summary: "visible to visible" }]);
  assert.equal(connPaths(panel, "#7aa2f7").length, 1, "the full elbow connector draws exactly as before");
  assert.equal(stubLines(panel).length, 0, "no stub lines anywhere");
});

test("the full connector's dash keys on exec knowledge — the stubs' event key, never the staleness flag", () => {
  // the user 2026-08-24: the connector dashed on the kernel's staleness-aged mm.pending, so the
  // same stale message flipped solid/dashed when a lane filter toggled it between forms
  const now = 1_781_000_000;
  const landed = draw([{ id: "m20", fromId: "SB", toId: "SD", from: "bee", to: "dee",
                         sent: now - 200, exec: now - 50, hasExec: true, pending: false, summary: "landed" }]);
  const [pl] = connPaths(landed, "#7aa2f7");
  assert.equal(pl._attrs["stroke-dasharray"], undefined, "exec known -> solid");
  assert.equal(pl._attrs.opacity, 0.5, "…at the arrived weight");
  const stale = draw([{ id: "m21", fromId: "SB", toId: "SD", from: "bee", to: "dee",
                        sent: now - 400, exec: now - 400, hasExec: false, pending: false, summary: "aged out, never seen landing" }]);
  const [ps] = connPaths(stale, "#7aa2f7");
  assert.equal(ps._attrs["stroke-dasharray"], "1 4", "no exec knowledge -> dashed, however old (the timer is not the key)");
  assert.equal(ps._attrs.opacity, 0.4, "…at the in-flight weight");
  const pending = draw([{ id: "m22", fromId: "SB", toId: "SD", from: "bee", to: "dee",
                          sent: now - 60, exec: now - 60, hasExec: false, pending: true, summary: "on its way" }]);
  const [pp] = connPaths(pending, "#7aa2f7");
  assert.equal(pp._attrs["stroke-dasharray"], "1 4", "a genuinely in-flight message stays dashed — same render as before");
});

test("window clamping is arithmetic: an off-window send or landing draws no stub", () => {
  const now = 1_781_000_000;
  const out = draw([{ id: "m8", fromId: "SB", toId: "SC", from: "bee", to: "cee",
                      sent: now - 500_000, exec: now - 500_000, hasExec: false, pending: true, summary: "sent long ago" }]);
  assert.equal(stubLines(out).length, 0, "an outgoing stub is anchored at its send — off-window send, nothing to draw");
  const inc = draw([{ id: "m9", fromId: "SA", toId: "SB", from: "ada", to: "bee",
                      sent: now - 600_000, exec: now - 500_000, hasExec: true, pending: false, summary: "landed long ago" }]);
  assert.equal(stubLines(inc).length, 0, "an incoming stub is anchored at its landing — off-window exec, nothing to draw");
  // …and the mirror of the mirror: routine OLD mail (sent before the window, delivered inside it)
  // must still stub — the incoming gate reads the landing, never the send
  const old = draw([{ id: "m12", fromId: "SA", toId: "SB", from: "ada", to: "bee",
                      sent: now - 500_000, exec: now - 100, hasExec: true, pending: false, summary: "old but delivered here" }]);
  const [s] = stubLines(old);
  assert.ok(s, "an off-window send with an in-window landing still draws the incoming stub");
  near(s._attrs.x2, X(old)(now - 100), "arriving at the exec x");
  near(s._attrs.y1, laneY(old)(0) - OFF, "riding its incoming track above the lane");
});

// The window clamps only bind within ~13px of a window edge — hard to pin behaviorally — so pin
// them at the source, the timeline-threadarc.test.ts idiom for exactly this kind of plumbing.
test("both stub anchors gate on their own endpoint and clamp x to the window by arithmetic (source pins)", () => {
  assert.match(SRC, /if \(!inWin\(sendXT\(mm\)\)\) return;/);
  assert.match(SRC, /if \(!inWin\(landT\)\) return;/, "the incoming gate reads the arrival-keyed landing");
  assert.match(SRC, /const landT = arrived \? landXT\(mm\) : \(mm\.toThreadT \|\| nowS\);/,
    "the span's landing keys on arrival knowledge, never the staleness-aged pending");
  assert.match(SRC, /if \(x2 - x1 < 2\) x2 = x1 \+ 2;/, "zero-length is impossible by geometry");
  assert.match(SRC, /x2 = Math\.max\(x1, x\(Math\.min\(landT, t1\)\)\)/);
  assert.match(SRC, /x1 = Math\.min\(x2, x\(Math\.max\(sendXT\(mm\), t0\)\)\)/);
  assert.doesNotMatch(SRC, /STUB_DX/, "the run cap is GONE — it bounded a diagonal's rise; flattened it made nubs");
  assert.doesNotMatch(SRC, /el\('clipPath'|clip-path/i, "clipped by arithmetic, never a clipPath element");
  // the sender-color + dash retarget (the user 2026-08-24): color from the full connectors' own
  // lookup, arrived/in-flight on the stroke style, and the old state-color tokens are GONE
  assert.match(SRC, /const col = colorOf\(mm\.fromId\);\s*\/\/ the SENDER's color/);
  assert.match(SRC, /const arrived = mm\.hasExec \|\| mm\.exec !== mm\.sent;/);
  assert.match(SRC, /if \(!arrived\) attrs\['stroke-dasharray'\] = '1 4';/);
  assert.doesNotMatch(SRC, /STUB_GREEN|STUB_YELLOW/, "the state-color tokens are deleted with their comments"
    + " (their hexes live on legitimately in the badges/gauges — only the stub tokens die)");
});
