// The uuid-anchored chat wire's list operations (T323 stage 4b): executed for real over small lists.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { applyTailAfter, prependHead, appendMore, mergeWindow, historyLabel, indexOfUuid, keyOf, windowDetached, fullFrameMerges, afterMore, reattachKeys, REATTACH_KEYS, windowLanding, olderRequestAllowed, livePausedText } from "./chat-window";

const ev = (u: string) => ({ uuid: u, kind: "user", md: u });
const run = (...u: string[]) => u.map(ev);
const uu = (l: { uuid?: string }[]) => l.map((e) => e.uuid);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("a chatTail by uuid truncates after its anchor and appends; an anchor not resident is a gap", () => {
  assert.deepEqual(uu(applyTailAfter(run("a", "b", "c"), "b", run("c2", "d"))!), ["a", "b", "c2", "d"]);
  assert.deepEqual(uu(applyTailAfter(run("a", "b", "c"), "c", run("d"))!), ["a", "b", "c", "d"]);
  assert.equal(applyTailAfter(run("a", "b"), "zz", run("d")), null, "the anchor is gone: ask for a full");
});

test("a chatHead by uuid prepends only when its beforeUuid is the resident oldest", () => {
  assert.deepEqual(uu(prependHead(run("d", "e"), "d", run("b", "c"))!), ["b", "c", "d", "e"]);
  assert.equal(prependHead(run("d", "e"), "e", run("b")), null, "stale: the oldest moved on");
  assert.deepEqual(uu(prependHead(run("d"), "d", [])!), ["d"], "an empty page (the head): the list stands");
});

test("a chatMore by uuid appends only after the resident newest", () => {
  assert.deepEqual(uu(appendMore(run("a", "b"), "b", run("c", "d"))!), ["a", "b", "c", "d"]);
  assert.equal(appendMore(run("a", "b"), "a", run("c")), null);
});

test("a chatWindow replaces a run it does not overlap and merges one it does, in transcript order, once per uuid", () => {
  const far = mergeWindow(run("x", "y", "z"), run("a", "b", "c"));
  assert.equal(far.mode, "replace"); assert.deepEqual(uu(far.events), ["a", "b", "c"]);
  const before = mergeWindow(run("c", "d", "e"), run("a", "b", "c"));          // the window ends inside the run
  assert.equal(before.mode, "merge"); assert.deepEqual(uu(before.events), ["a", "b", "c", "d", "e"]);
  const inside = mergeWindow(run("a", "b", "c", "d"), run("b", "c"));          // the window is inside the run
  assert.equal(inside.mode, "merge"); assert.deepEqual(uu(inside.events), ["a", "b", "c", "d"]);
  const after = mergeWindow(run("a", "b", "c"), run("c", "d", "e"));           // the window starts inside the run
  assert.equal(after.mode, "merge"); assert.deepEqual(uu(after.events), ["a", "b", "c", "d", "e"]);
  assert.equal(indexOfUuid(after.events, "e"), 4);
});

test("a second event of one record keeps the record's uuid and is anchored by its key", () => {
  const text = { uuid: "a1", kind: "assistant", md: "running" }, tool = { uuid: "a1", key: "a1#2", kind: "tool", name: "Bash" };
  assert.equal(keyOf(text), "a1"); assert.equal(keyOf(tool), "a1#2");
  const list = [ev("u1"), text, tool];
  assert.equal(indexOfUuid(list, "a1#2"), 2, "the tool event, by its key");
  assert.deepEqual(uu(applyTailAfter(list, "a1#2", run("r1"))!), ["u1", "a1", "a1", "r1"], "a tail after the tool event keeps both");
  assert.deepEqual(uu(applyTailAfter(list, "a1", run("x"))!), ["u1", "a1", "x"], "a tail after the text event drops the tool event");
});

test("the history strip shows no number while the head is unknown", () => {
  assert.equal(historyLabel(false, 250, null), "older history");
  assert.equal(historyLabel(false, 250, 900), "older history", "a total handed with an unknown head is not shown either");
  assert.equal(historyLabel(true, 250, 900), "650 older");
  assert.equal(historyLabel(true, 900, 900), "");
});

test("the detach rule: a window re-attaches only a run that was attached and still ends on the live tail (round 2, item 2)", () => {
  assert.equal(windowDetached(true, true, false, "replace", "z", "q"), false, "the kernel said connected");
  assert.equal(windowDetached(false, false, true, "replace", "z", "z"), false, "nothing after the window: the tail is resident");
  assert.equal(windowDetached(true, false, false, "merge", "z", "z"), false, "merged into the attached run, its live tail kept");
  assert.equal(windowDetached(true, false, false, "merge", "z", "y"), true, "merged, but the run's newest moved: an older window");
  assert.equal(windowDetached(true, false, true, "merge", "w", "w"), true, "a DETACHED client's heldLast is an older window's last, not the live tail");
  assert.equal(windowDetached(true, false, false, "replace", "z", "q"), true, "a far window replaces the run: detached");
});

test("a full frame merges only when it answers this client's own re-attach ask (round 2, item 3)", () => {
  assert.equal(fullFrameMerges("reattach"), true);
  for (const why of ["gap", "nobase", "skeleton-click", "prefetch", "skeleton-delta", null, undefined]) assert.equal(fullFrameMerges(why), false, String(why));
});

test("after a chatMore: detached while more follows; at the tail the count is the run's when the head is known", () => {
  assert.deepEqual(afterMore(true, true, 40), { detached: true, headTotal: null });
  assert.deepEqual(afterMore(false, true, 40), { detached: false, headTotal: 40 });
  assert.deepEqual(afterMore(false, false, 40), { detached: false, headTotal: null });
});

test("a detaching window lands on an attached active reader only when their own navigation asked for it (T366)", () => {
  assert.equal(windowLanding(false, true, false), "attach", "a verdict that does not detach lands as is");
  assert.equal(windowLanding(false, false, true), "attach");
  assert.equal(windowLanding(true, true, false), "reattach", "attached, on screen, nobody navigated: the window is not adopted");
  assert.equal(windowLanding(true, true, true), "detach", "the reader clicked a card, a notch or a deep link: the window lands");
  assert.equal(windowLanding(true, false, false), "detach", "a run already detached, or a view not on screen, takes the window");
  assert.equal(windowLanding(true, false, true), "detach");
});

test("the paused strip names a navigation's detach, with the opened message's clock when the frame carried its time (T366)", () => {
  const clock = (t: number) => "at-" + t;
  assert.equal(livePausedText(false, null, clock), "Live updates are paused while you read older history.", "no navigation behind the detach: the plain sentence");
  assert.equal(livePausedText(false, 1700000000, clock), "Live updates are paused while you read older history.", "a time without a navigation is not a click");
  assert.equal(livePausedText(true, 1700000000, clock), "Showing the message from at-1700000000 you opened; live updates are paused.");
  assert.equal(livePausedText(true, null, clock), "Showing the message you opened; live updates are paused.", "a navigation whose frame carried no time");
  const strip = RENDER.slice(RENDER.indexOf("function updateLivePaused(): void {"), RENDER.indexOf("\n}\n", RENDER.indexOf("function updateLivePaused(): void {")));
  assert.ok(strip.includes("livePausedTxt.textContent = livePausedText(!!s.detachNav, s.detachNav ? s.detachNav.t : null, clockOf);"), "the strip re-says its sentence on every evaluation from the session's detach");
  assert.ok(strip.indexOf("livePausedTxt.textContent = livePausedText(") > strip.indexOf("document.body.appendChild(livePausedEl);"), "…after the one-time build, so a later detach changes the words");
  const win = RENDER.slice(RENDER.indexOf("function chatWindow(msg: any) {"), RENDER.indexOf("function chatMore(msg: any) {"));
  assert.ok(win.includes("s.detachNav = detached && ask?.named ? { t: ask.t } : null;"), "a landed window records whether a card, lane or deep link detached, and its time");
  assert.ok(RENDER.includes("pendingWindowNav.set(sid, { nav, named: nav && pendingAnchorKeepY == null, t: nav ? (pendingAnchorT ?? null) : null, kind: nav ? kind : null });"), "the ask carries the navigation's time to the reply, and whether it was a click (any anchor landing without a keep offset; the reload restore of the reader's own place keeps the plain sentence)");
  // the three direct landings (a notch, a reply chip, a comment tick) arm neither kind nor time: still a click, named as opened
  assert.match(RENDER, /markjump: \(elx\) => \{\s*\n\s*const uuid = elx\.dataset\.uuid;\s*\n\s*if \(!uuid \|\| !activeId\) return;\s*\n\s*flashedAnchor = null;\s*\n\s*scrollToAnchor\(uuid\);/, "the notch lands directly");
  assert.match(RENDER, /replyjump: \(elx\) => \{[\s\S]*?flashedAnchor = null;[^\n]*\n\s*if \(scrollToAnchor\(uuid\)\) \{/, "the reply chip lands directly");
  assert.match(RENDER, /cmtjump: \(elx\) => \{[\s\S]*?flashedAnchor = null;\s*\n\s*scrollToAnchor\(uuid\);/, "the comment tick lands directly");
});

test("an older-history ask needs an upward or unchanged move; a downward gesture never asks (T366)", () => {
  assert.equal(olderRequestAllowed(undefined, 120), true, "no previous top: a fresh or rebuilt view may ask");
  assert.equal(olderRequestAllowed(null, 0), true);
  assert.equal(olderRequestAllowed(500, 120), true, "moving up");
  assert.equal(olderRequestAllowed(120, 120), true, "unchanged (a resize, a relayout)");
  assert.equal(olderRequestAllowed(120, 121), false, "moving down, by any amount");
  assert.equal(olderRequestAllowed(0, 3000), false, "a flick down from the very top");
});

test("render.ts asks for older history only on an upward move, marks each window ask with whether a navigation made it, and files the ask (T366)", () => {
  const virt = RENDER.slice(RENDER.indexOf("function virtualizeToViewport()"), RENDER.indexOf("\n}\n", RENDER.indexOf("function virtualizeToViewport()")));
  assert.ok(virt.includes("const gesture = v.gestureScroll === true;\n  v.gestureScroll = undefined;\n  if (gesture) v.edgeUp = olderRequestAllowed(v.edgeTop, st);"), "the direction is read only on the reader's own gesture, from the view's last edge-check top, and the mark is consumed");
  assert.ok(virt.indexOf("if (gesture) v.edgeUp = olderRequestAllowed(v.edgeTop, st);") < virt.indexOf("v.edgeTop = st;"), "…before the top is remembered for the next check (the page's own writes move it too)");
  assert.ok(virt.includes("const upward = v.edgeUp !== false;"), "the last gesture's verdict holds across the page's compensating writes; no verdict yet allows the ask");
  assert.ok(virt.includes("st < topH + edgePx && upward) { requestOlder(activeId, v, content); return; }"), "the older ask is gated on the direction, not only the estimate's band");
  assert.match(RENDER, /const cls = classifyScroll\(c\.scrollTop, lastScrollWriteAfter\);\n\s*const gv = activeId \? views\.get\(activeId\) : null;\n\s*if \(gv\) gv\.gestureScroll = cls === "gesture";/, "the scroll listener marks a gesture (a write's echo is none) for the edge check that runs next");
  assert.ok(RENDER.includes("edgeTop?: number; edgeUp?: boolean; gestureScroll?: boolean;"), "the view remembers the top the last edge check saw, the last verdict and the gesture mark");
  assert.ok(RENDER.includes("v.unitTotal = undefined; v.edgeTop = undefined; v.edgeUp = undefined; v.stale = true; }"), "a window rebuild forgets both: the first check after a rebuild may ask");
  const around = RENDER.slice(RENDER.indexOf("function requestAround(sid: string, uuid: string): boolean {"), RENDER.indexOf("\n}\n", RENDER.indexOf("function requestAround(sid: string, uuid: string): boolean {")));
  assert.ok(around.includes("const nav = !relandAsk;") && around.includes("pendingWindowNav.set(sid, { nav,"), "a window ask records whether a navigation made it: every anchor landing but the re-land of the reader's own row across a rebuild");
  const keep = RENDER.slice(RENDER.indexOf("function keepPlaceAcrossWindow("), RENDER.indexOf("\n}\n", RENDER.indexOf("function keepPlaceAcrossWindow(")));
  assert.ok(keep.includes("relandAsk = true;\n  let landed = false;\n  try { landed = scrollToAnchor(keep.uuid); } finally { relandAsk = false; }"), "the re-land's own flag is set only around its landing (the reload restore shares the keep offset and must land)");
  assert.equal((RENDER.match(/relandAsk = true;/g) || []).length, 1, "nothing else raises the flag");
  assert.ok(around.includes('scrollDiagRow("windowask", {'), "…and files a diagnostic row under the scroll rows' per-minute budget: the report's rows had the landing but not the ask");
  assert.ok(RENDER.includes('| "unitchange" | "windowask", data: any): void {'), "the budgeted row kinds include it");
  assert.ok(around.indexOf("pendingWindowNav.set(sid, { nav,") < around.indexOf('type: "loadAround"'), "the mark is set before the ask goes out");
  // a refused window leaves the kernel's base on the window until the re-attach lands; a tail pushed meanwhile misses its
  // anchor and asks for a full frame as a gap, which must not overwrite the pending reattach reason (a replace, not a merge)
  const full = RENDER.slice(RENDER.indexOf("function requestFullSession(id: string, why: NeedFullWhy): void {"), RENDER.indexOf("\n}\n", RENDER.indexOf("function requestFullSession(id: string, why: NeedFullWhy): void {")));
  assert.ok(full.indexOf("if (!id || awaitingFull.has(id)) return;") < full.indexOf("pendingFullWhy.set(id, why);"), "a second full ask while one is in flight is dropped before it can overwrite the pending reason");
});

test("render.ts wires the three rules, tracks the pending needFull reason, hides the paused strip on a frame and a tab switch, and lands orphan notes by record uuid", () => {
  const upsert = RENDER.slice(RENDER.indexOf("function upsert(msg: any) {"), RENDER.indexOf("\n}\n", RENDER.indexOf("function upsert(msg: any) {")));
  assert.ok(upsert.includes("fullFrameMerges(fullWhy)"), "upsert merges by the pending reason");
  assert.ok(upsert.includes("pendingFullWhy.delete(msg.id)"), "…consumed by the frame that answers it");
  assert.ok(upsert.includes("if (msg.id === activeId) updateLivePaused();"), "the strip re-evaluates when the active tab's frame lands");
  assert.ok(upsert.includes("mergedRun && prev?.headKnown ? events.length"), "a merged run with a known head carries its own count");
  assert.match(RENDER, /vscodeApi\?\.postMessage\(\{ type: "needFull", id, why \}\);\n  pendingFullWhy\.set\(id, why\);/, "requestFullSession records the reason with the ask");
  assert.match(RENDER, /window\.addEventListener\("romp:wsup", \(\) => pendingFullWhy\.clear\(\)\);/, "…and a new socket forgets the reasons with the asks");
  const win = RENDER.slice(RENDER.indexOf("function chatWindow(msg: any) {"), RENDER.indexOf("function chatMore(msg: any) {"));
  assert.ok(win.includes("const detached = windowDetached(!!msg.moreAfter, !!msg.connected, wasDetached, r.mode, heldLast, newLast);"), "chatWindow decides through the rule, with the state before the merge");
  assert.ok(win.includes("const landing = windowLanding(detached, msg.id === activeId && !wasDetached, ask?.nav ?? false);"), "…and the landing rule decides whether the verdict is adopted (T366)");
  assert.ok(win.indexOf("const landing = windowLanding(") < win.indexOf("s.events = r.events as ChatEvent[];"), "the landing is decided BEFORE the window's events replace the run");
  assert.ok(win.includes('if (landing === "reattach") {') && win.includes("reattachLive(msg.id, true);"), "a window not adopted re-bases the kernel on the tail at once");
  assert.ok(win.indexOf('if (landing === "reattach") {') < win.indexOf("s.detached = detached;"), "…and returns before the detached flag is set: no strip");
  assert.ok(win.includes("window.requestAnimationFrame(() => edgeCheckAfterWindow(msg.id));"), "a window runs the edge check once it painted");
  assert.ok(RENDER.includes("if (cur && cur.detached && c && c.scrollHeight <= c.clientHeight + 1) { requestNewer(sid); return; }"), "a detached run that does not overflow asks for its next page directly");
  const more = RENDER.slice(RENDER.indexOf("function chatMore(msg: any) {"), RENDER.indexOf("let livePausedEl"));
  assert.ok(more.includes("const am = afterMore(!!msg.more, !!s.headKnown, s.events.length);"), "chatMore decides through the rule");
  assert.ok(more.includes("if (msg.id === activeId && s.detached) window.requestAnimationFrame(() => edgeCheckAfterWindow(msg.id));"), "…and a still-detached run re-checks its edge after the page painted");
  const active = RENDER.slice(RENDER.indexOf("function setActive(id: string"), RENDER.indexOf("\n}\n", RENDER.indexOf("function setActive(id: string")));
  // (T357: the unfocused state's clear sits between the two lines; the re-evaluation still follows the activation.) The
  // pin is the ORDER: the activation, the clear line, then updateLivePaused with no statement between (a character
  // distance was consumed by every comment edit: 198 of 200 at one point)
  assert.match(active, /activeId = id;\n  vanishedId = null;[^\n]*\n  updateLivePaused\(\);/, "a tab switch re-evaluates the strip for the entering tab, right after the activation and its clear");
  assert.ok(RENDER.includes('turn.dataset.orphanOf = String((ev as { orphanOf?: string }).orphanOf)'), "an orphan note's turn carries its record uuid");
  assert.equal((RENDER.match(/\.turn\[data-orphan-of="\$\{cssEscape\(uuid\)\}"\]/g) || []).length, 2, "…and both anchor lookups read it");
  assert.ok(RENDER.includes("(e as { orphanOf?: string }).orphanOf === uuid"), "…as does the events-list search behind them");
});

test("the re-attach ask carries the run's newest keys, bounded, and render.ts posts them ahead of the ask", () => {
  const long = Array.from({ length: REATTACH_KEYS + 40 }, (_, i) => ev("k" + i));
  const keys = reattachKeys(long);
  assert.equal(keys.length, REATTACH_KEYS); assert.equal(keys[0], "k40"); assert.equal(keys[keys.length - 1], "k" + (REATTACH_KEYS + 39));
  assert.deepEqual(reattachKeys([ev("a"), { uuid: "a", key: "a#2", kind: "tool" }, { kind: "todo" }]), ["a", "a#2"], "keys, not uuids; a keyless card is skipped");
  const fn = RENDER.slice(RENDER.indexOf("function reattachLive(sid: string, force = false): void {"), RENDER.indexOf("\n}\n", RENDER.indexOf("function reattachLive(sid: string, force = false): void {")));
  assert.ok(fn.indexOf('type: "reattachKeys", id: sid, keys: reattachKeys(') > 0 && fn.indexOf('type: "reattachKeys"') < fn.indexOf('requestFullSession(sid, "reattach")'), "the keys go out before the ask");
});

test("render.ts speaks proto 2 at ready and routes the four proto-2 frames through this module", () => {
  assert.match(RENDER, /postMessage\(\{ type: "ready", proto: 2 \}\)/, "the ready frame names the protocol");
  for (const fn of ["indexOfUuid", "prependHead", "appendMore", "mergeWindow", "historyLabel", "keyOf"]) assert.ok(RENDER.includes(fn + "("), fn);   // chatTail truncates by indexOfUuid in place
  assert.ok(RENDER.includes('m.type === "chatWindow"') && RENDER.includes('m.type === "chatMore"'), "the two new frames are dispatched");
  assert.ok(RENDER.includes('type: "loadAround"') && RENDER.includes('type: "loadNewer"'), "and the two new requests are posted");
  for (const s of ['updateLivePaused(', 'reattachLive(', '"live-paused"', 'requestFullSession(sid, "reattach")']) assert.ok(RENDER.includes(s), s);   // the detached client's way back
  assert.ok(RENDER.includes("r.mode === \"merge\"") && RENDER.includes("mergedRun"), "a full frame overlapping the held run merges into it");
  assert.ok(RENDER.includes('requestFullSession(msg.id, "gap"); return; }   // the anchor is gone'), "a missing chatHead is a gap");
});

test("federation tells every remote kernel the chat protocol on its socket's open", () => {
  const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");
  assert.ok(FED.includes('ws.send(JSON.stringify({ type: "ready", proto: 2 }))'), "the remote socket's open sends the ready with the protocol");
});
