// A thin blue notch on the chat's right scroll edge for every USER message (the user 2026-08-17) —
// the conversation's shape at a glance, overview-ruler style. Proportional positions (scroll-
// invariant), painted by the rail-sticky scheduler with a signature skip so pure scrolls do no DOM
// work; the BOX is passive fixed chrome that never blocks the native scrollbar, while each NOTCH is a
// link to its message (T260); gestures (command rows, the Continue row) draw no notch — those are
// doings, not words. Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("one notch per real user message across the WHOLE loaded conversation", () => {
  // the user 2026-08-17: the chat virtualizes (a rendered window between two estimated-height
  // spacers), and window-only notches "forgot" the newer messages when scrolled back. Notches
  // come from the full resident events array, placed by the one virtual frame (pinned below).
  assert.match(RENDER, /for \(let i = 0; i < s\.events\.length; i\+\+\) \{/);
  // the filter reads the ONE senderKind verdict (2026-08-18): user → blue, romp/tagged → the gray
  // machine notch, harness noise → none — the same classifier the bubble and rail dot wear
  assert.match(RENDER, /const kind = senderKind\(ev\);\s*\n\s*if \(kind === "injected"\) continue;/);
  assert.match(RENDER, /ev\.canned === "continue" \|\| SLASH_CMD_RE\.test\(md\)/,
    "a /command or Continue gesture is a doing, not words — no notch");
});

test("the frame is VIRTUAL: one cached-height prefix over ALL units, independent of scroll and window", () => {
  // T129 (the user 2026-08-27, filming marks moving RELATIVE TO EACH OTHER while scrolling —
  // geometrically impossible for a linear map): the old frame was piecewise — live rect offsets
  // inside the render window, cache sums normalized to each spacer's height outside — so pure
  // scrolling changed the map every frame the window slid. The frame now depends on NOTHING the
  // scroll moves: a mark's position changes only when information arrives (a height measured, an
  // event appended). Lab proof: tools/romp-lab/rail-drift.mjs — learned-regime drift is 0.0px
  // across a full scroll round trip (was 5.7px, endlessly, before).
  assert.match(RENDER, /const unitHeights = new Map<string, Map<number, number>>\(\);/);
  assert.match(RENDER, /if \(Number\.isFinite\(u\) && h > 0\) uh\.set\(u, h\);/, "every rendered unit's height is remembered");
  assert.match(RENDER, /for \(let u = 0; u < unitTotal; u\+\+\) \{ t \+= uh\.get\(u\) \?\? avg; pre\.push\(t\); \}/,
    "ONE prefix-sum over every unit — cached truth where seen, the renderer's average else");
  assert.match(RENDER, /\(i >= 0 && i < unitTotal\) \? pre\[i\] \+ \(pre\[i \+ 1\] - pre\[i\]\) \/ 2 : null/,
    "every unit slots at its virtual middle — uniform semantics, no per-basis seams");
  const frameBody = RENDER.slice(RENDER.indexOf("function contentOffsetFrame("), RENDER.indexOf("function ensureScrollMarks("));
  // the VIRTUAL branch never reads the scroll position or the window (T129); the fully-rendered EXACT branch
  // above it converts client rects into scroll space, which is scroll-invariant (T245)
  const virtualBody = frameBody.split("const avg = v.avgTurnH ?? 60;")[1] || frameBody;
  assert.ok(!/scrollTop|tx-spacer|winStart|slotIn/.test(virtualBody),
    "nothing scroll-coupled inside the frame — no live offsets, no spacer reads, no window bounds");
});

test("a history load rescales the map smoothly — moved notches are carried, never teleported", () => {
  // the user 2026-08-17: scrolling back streams older history in; the scroller's world grows and
  // every proportional position compresses (the native thumb does the same). Rebuilt nodes can't
  // transition, so same-count updates move the EXISTING nodes and CSS carries them.
  assert.match(RENDER, /if \(kids\.length === ys\.length\) \{\s*\n\s*ys\.forEach\(\(o, i\) => \{ kids\[i\]\.style\.top = o\.y \+ "px"; dress\(kids\[i\], o, i\); \}\);/,
    "…and the dress (kind class, link target, tip, hit pads) updates in place too — a machine notch stays gray through a rescale");
  assert.match(RENDER, /const dress = \(m: HTMLElement, o: typeof ys\[number\], k: number\) => \{\s*\n\s*m\.className = "scroll-mark" \+ \(o\.m \? " " \+ o\.m : ""\);/,
    "one dress for both DOM paths, so the moved notch and the rebuilt notch can never disagree");
  assert.match(CSS, /transition: top 180ms ease;/);
  assert.match(CSS, /prefers-reduced-motion: reduce\) \{ \.scroll-marks \.scroll-mark \{ transition: none; \} \}/);
});

test("positions are proportional and pure scrolls do no DOM work", () => {
  assert.match(RENDER, /if \(sig !== scrollMarksSig\) \{/, "signature skip: rebuild only on real change");
  assert.match(RENDER, /paintRailSticky\(\); paintScrollMarks\(\);/, "rides the existing rAF scheduler");
});

test("passive chrome in the user's own blue", () => {
  assert.match(CSS, /\.scroll-marks \{ position: fixed; z-index: 3; pointer-events: none; width: 12px; \}/);
  assert.match(CSS, /background: var\(--you\); opacity: 0\.65;/, "the outgoing-bubble blue, never the romp accent");
});

test("marks translate EVENT indices to DISPLAY UNITS before asking the frame", () => {
  // the user 2026-08-18: "some notches are displayed, others aren't — maybe the ones where I
  // replied". Unit === event only in normal mode; compact mode folds tool runs into toolgroup
  // units, so event indices passed straight to the unit-keyed frame found no node (or the wrong
  // one) and the mark silently vanished — worst exactly beside big tool runs, where replies to a
  // working session land. Both painters now translate through eventUnitIndex.
  assert.match(RENDER, /function eventUnitIndex\(s: Session\): Int32Array/);
  assert.match(RENDER, /if \(it\.kind === "toolgroup" \|\| it\.kind === "noticegroup"\) \{ for \(const i of it\.indices\) map\[i\] = u; \}/);   // noticegroup since 2026-09-08
  assert.match(RENDER, /const evUnit = eventUnitIndex\(s\);/);
  assert.match(RENDER, /const u = evUnit\[i\];/);
  assert.match(RENDER, /const off = frame\.offsetOf\(u\);/, "notches ask the frame in unit space");
  assert.match(RENDER, /const off = frame\.offsetOf\(evUnit\[idx\]\);/, "comment ticks too — one translation, both overlays");
});

// ── T245 (the user 2026-09-07): a notch sat below the thumb while its message was on screen ───────────────
test("a rendered unit changing height re-runs the shared paint — the event the frame was missing (T245)", () => {
  const ev = RENDER.split("function ensureView(id: string): View {")[1].split("\n}")[0];
  // the observer's first statement is still the paint; the same callback carries the tail-shrink rule (T262f)
  assert.match(ev, /v\.ro = new ResizeObserver\(\(entries\) => \{\s*\n\s*scheduleRailSticky\(\);[\s\S]*?\n\s*\}\);\s*\n\s*v\.ro\.observe\(elv\);/,
    "one observer per view element: a lazy figure sizing in or a fold toggling repaints notches AND rail ticks");
  assert.match(RENDER, /ro\?: ResizeObserver; mo\?: MutationObserver; \}/, "the View carries its observers (the tail mutation one joined it, T262j)");
  assert.equal((RENDER.match(/v\.ro\?\.disconnect\(\); v\.mo\?\.disconnect\(\); v\.el\.remove\(\);/g) || []).length, 2, "both view-removal sites disconnect it (and the tail mutation observer beside it, T262j)");
  assert.doesNotMatch(ev, /setTimeout|setInterval/, "no timer stands in for the event");
});

test("with every unit rendered the frame is the scrollbar's own truth: real middles over content.scrollHeight (T245)", () => {
  const frameBody = RENDER.split("function contentOffsetFrame(")[1].split("\nfunction ")[0];
  assert.match(frameBody, /const nodes = Array\.from\(v\.el\.querySelectorAll<HTMLElement>\("\.turn\[data-unit\]"\)\);/);
  assert.match(frameBody, /exact\.set\(u, content\.scrollTop \+ \(r\.top - cRect\.top\) \+ r\.height \/ 2\);/, "scroll-space middle, invariant under scrolling");
  // one unit may own several .turn nodes (an expanded tool group's children — appendItem tags them
  // all with the unit): the unit's FIRST node is its root, and the gate counts UNITS against a spacer-free view,
  // never nodes — the first cut's node count dropped the frame back to the sum whenever a group stood open
  assert.match(frameBody, /if \(Number\.isFinite\(u\) && !exact\.has\(u\)\) \{/, "first node per unit");
  assert.doesNotMatch(frameBody, /nodes\.length === unitTotal/, "no node-count gate");
  assert.match(frameBody, /if \(exact\.size > 0 && exact\.size === unitTotal && !v\.el\.querySelector\("\.tx-spacer"\) && content\.scrollHeight > 0\) \{\s*\n\s*const shx = content\.scrollHeight;\s*\n\s*return \{ sh: shx, offsetOf: \(i: number\): number \| null => exact\.get\(i\) \?\? null \};/);
  // the virtual prefix-sum stays the fallback while spacers hide units
  assert.match(frameBody, /for \(let u = 0; u < unitTotal; u\+\+\) \{ t \+= uh\.get\(u\) \?\? avg; pre\.push\(t\); \}/);
});

// ── T260 (the user 2026-09-08): the notches are links — click one and the chat lands on that message ─────
const PAINT = RENDER.slice(RENDER.indexOf("function paintScrollMarks(): void {"), RENDER.indexOf("function paintRailSticky(): void {"));
const ENSURE = RENDER.slice(RENDER.indexOf("function ensureScrollMarks(): HTMLElement {"), RENDER.indexOf("function scrollMarkTitle("));
const TITLE = RENDER.slice(RENDER.indexOf("function scrollMarkTitle("), RENDER.indexOf("function paintScrollMarks(): void {"));

test("every notch that knows its message carries the uuid and the action, on BOTH DOM paths (T260)", () => {
  // the click resolves to a MESSAGE (a uuid), never to a pixel — and the same dress runs on the in-place
  // move and the rebuild, so a rescale re-points a moved notch rather than leaving it aimed at a ghost
  assert.match(PAINT, /if \(o\.uuid\) \{ m\.dataset\.act = "markjump"; m\.dataset\.uuid = o\.uuid; m\.title = scrollMarkTitle\(/);
  assert.match(PAINT, /else \{ delete m\.dataset\.act; delete m\.dataset\.uuid; m\.removeAttribute\("title"\); \}/,
    "a notch with nothing to land on is a plain mark: no act, no pointer cursor, no false affordance");
  assert.match(PAINT, /offs\.push\(\{ top: off, m: kind === "user" \? "" : "machine", uuid: ev\.uuid \|\| "", i, kind \}\);/,
    "machine notches carry their uuid too — gray is clickable like blue");
  assert.match(PAINT, /box\.replaceChildren\(\.\.\.ys\.map\(\(o, k\) => \{\s*\n\s*const m = el\("div", ""\);\s*\n\s*m\.style\.top = o\.y \+ "px";\s*\n\s*dress\(m, o, k\);/);
  assert.match(PAINT, /ys\.map\(\(o\) => o\.y \+ \(o\.m \? "m" : ""\) \+ o\.uuid\)\.join\(","\)/,
    "the uuid rides the signature: a message changing identity under the same pixel re-points its notch");
});

test("the click is DELEGATED on the stable box and rides the deep-link route, like a comment tick (T260)", () => {
  // click-safety: the notches are rebuilt on every paint, so the listener lives on the box (created once)
  // and keys off data-act; actions.ts's delegate() also gives the press pulse. The jump is scrollToAnchor —
  // pointer-exact on the uuid, the one-per-navigation flash re-armed — never scrollTop arithmetic.
  assert.match(ENSURE, /delegate\(scrollMarks, \{\s*\n\s*markjump: \(elx\) => \{\s*\n\s*const uuid = elx\.dataset\.uuid;\s*\n\s*if \(!uuid \|\| !activeId\) return;\s*\n\s*flashedAnchor = null;\s*\n\s*scrollToAnchor\(uuid\);/);
  assert.doesNotMatch(PAINT, /addEventListener|onclick|scrollTop =/, "no per-notch listener, no pixel jump in the painter");
  assert.equal((ENSURE.match(/delegate\(/g) || []).length, 1, "installed once, inside the create-once branch");
  assert.ok(ENSURE.indexOf("delegate(scrollMarks") > ENSURE.indexOf("document.body.appendChild(scrollMarks);"), "…after the box is made");
});

test("the tip is the rail's own time, and a machine notch names its sender (T260)", () => {
  // the same HH:MM the rail stamps (markerLabel, with a past day named the way the day's first stamp does);
  // a gray notch says who sent it — romp, or the ⚙ label — so it never poses as your words even on hover
  assert.match(TITLE, /const epoch = eventEpoch\(ev\);/);
  assert.match(TITLE, /markerLabel\(epoch, null, Date\.now\(\)\)\.text/);
  assert.match(TITLE, /kind === "romp" \? "from romp" : "from " \+ \(ev\.tag \|\| "a machine sender"\)/);
  assert.match(TITLE, /"click to jump to your message"/);
  assert.match(TITLE, /"click to jump to it"/);
  assert.match(TITLE, /\(head \? head \+ " · " : ""\)/, "a middot before the verb phrase: a clock time followed by \": \" read as a doubled colon");
});

test("hit pads are clamped to half the gap to each neighbour, so a dense stretch never answers for the wrong message (T260 review)", () => {
  // a fixed 3px pad reached over a neighbour's paint and the later sibling won the hit test: hover,
  // tip and click all answered for the message AFTER the one under the pointer wherever notches sat
  // within about 5px (verified in Chromium by two independent reviewers, 2026-09-08)
  assert.match(PAINT, /const PAD = 2;/, "the pad is 2px each side — a 6px target for a 2px line, and less of the thumb's track than 3px took");
  assert.match(PAINT, /const up = k > 0 \? Math\.floor\(\(ys\[k\]\.y - ys\[k - 1\]\.y - 2\) \/ 2\) : PAD;/);
  assert.match(PAINT, /const down = k \+ 1 < ys\.length \? Math\.floor\(\(ys\[k \+ 1\]\.y - ys\[k\]\.y - 2\) \/ 2\) : PAD;/);
  assert.match(PAINT, /return \[Math\.max\(0, Math\.min\(PAD, up\)\), Math\.max\(0, Math\.min\(PAD, down\)\)\];/, "half the gap, floored, never below 0 nor above the pad");
  assert.match(PAINT, /m\.style\.setProperty\("--hit-t", up \+ "px"\);\s*\n\s*m\.style\.setProperty\("--hit-b", down \+ "px"\);/, "set in the one dress, so both DOM paths carry them");
  assert.match(PAINT, /ys\.forEach\(\(o, i\) => \{ kids\[i\]\.style\.top = o\.y \+ "px"; dress\(kids\[i\], o, i\); \}\);/);
  assert.match(PAINT, /box\.replaceChildren\(\.\.\.ys\.map\(\(o, k\) => \{/);
});

test("the wheel over a notch scrolls the transcript — the box forwards it (T260 review)", () => {
  // the box hangs off body, not #content: a notch that takes the pointer took the wheel too, and its
  // scroll chain ended at the page — the scrollbar stopped scrolling exactly where a notch sat
  assert.match(ENSURE, /scrollMarks\.addEventListener\("wheel", \(e\) => \{/, "one listener on the stable box, never per notch");
  assert.match(ENSURE, /const k = e\.deltaMode === 1 \? 16 : e\.deltaMode === 2 \? c\.clientHeight : 1;/, "lines and pages scaled to pixels");
  assert.match(ENSURE, /scrollContentBy\(c, e\.deltaY \* k, "wheel-scale"\);\s*\n\s*\}, \{ passive: true \}\);/, "passive: the wheel is never blocked; the move rides the write helper as wheel-scale (T262j)");
});

test("only the NOTCH takes the pointer — the box stays passive over the native scrollbar (T260)", () => {
  assert.match(CSS, /\.scroll-marks \{ position: fixed; z-index: 3; pointer-events: none; width: 12px; \}/, "the box: unchanged, passive");
  assert.match(CSS, /\.scroll-marks \.scroll-mark\[data-act\] \{ pointer-events: auto; cursor: pointer; \}/, "a linked notch: the link cursor");
  assert.match(CSS, /\.scroll-marks \.scroll-mark\[data-act\]::before \{ content: ""; position: absolute; left: -2px; right: -2px; top: calc\(-1 \* var\(--hit-t, 2px\)\); bottom: calc\(-1 \* var\(--hit-b, 2px\)\); \}/,
    "the hit box is padded past the 2px paint by per-notch pads — the painted size never changes");
  const hover = (CSS.match(/\.scroll-marks \.scroll-mark\[data-act\]:hover \{[^}]*\}/) || [""])[0];
  assert.match(hover, /box-shadow: 0 0 0 1\.5px var\(--accent\)/, "the hover cue is the accent ring");
  assert.match(hover, /opacity: 1;/);
  assert.doesNotMatch(hover, /--st-|--you|width|height|#[0-9a-fA-F]{3,6}/, "never a status colour, never a size change, never a hardcoded hex");
  // both themes resolve the ring: the token exists in the dark root and in the light block
  const light = CSS.split("body.theme-light {")[1].split("\n}")[0];
  assert.match(CSS.split("body.theme-light {")[0], /--accent: #9cd2ff;/);
  assert.match(light, /--accent: #C2410C;/);
  // the hover rule outranks the machine notch's dimmer opacity by specificity (state rules must win the cascade)
  assert.match(CSS, /\.scroll-marks \.scroll-mark\.machine \{ background: #8a8f98; opacity: 0\.55; \}/);
});

// ── replies ready (the user 2026-09-08): the strip's reply DOTS are the comment rail's ticks ──────────────
// Each unread landed reply marks the scroll edge at its anchor's position in the comment colour, so the reader
// sees how many wait and where. The strip already carried this as #cmt-rail — one yellow tick per open thread,
// the UNREAD one wider, taller and double-ringed — in the SAME frame as the notches and over them; a second dot
// for the same thread on .scroll-marks would have put two marks in one column for one fact (the T129 rule: two
// marks on one scrollbar may never disagree). So the dots are pinned here, not duplicated: same frame, the
// landed colour token, unread keyed on the kernel's bit, and CLICKABLE — the tick jumps there and opens the
// thread (its 2026-08-15 contract, which is also what marks it read; the reply chips' click deliberately does
// not — reply-ready.test.ts).
const RAIL = RENDER.slice(RENDER.indexOf("function updateCommentRail(): void {"), RENDER.indexOf("function unwrapCommentMark("));

test("a reply dot sits at its anchor's position in the notches' own frame, over them in the same column", () => {
  assert.match(RAIL, /const frame = contentOffsetFrame\(content, v, s\);/, "the ONE frame the notches use");
  assert.match(RAIL, /const off = frame\.offsetOf\(evUnit\[idx\]\);/, "event → unit → offset, like a notch");
  assert.match(RAIL, /ticks\.push\(\{ th, y: Math\.min\(r\.height - 6, Math\.round\(\(off \/ frame\.sh\) \* \(r\.height - 4\)\)\) \}\);/, "the same proportional map");
  assert.match(RAIL, /rail\.style\.left = \(r\.right - 16\) \+ "px";/);   // 10 until the comment-forward rail (2026-09-24)
  assert.match(PAINT, /box\.style\.left = \(cRect\.right - 12\) \+ "px";/, "…the notch box's right edge is the rail's right edge");
  assert.match(CSS, /\.cmt-rail \{ position: fixed; width: 16px; z-index: 40; pointer-events: none; \}/, "over the notches (z 3), passive between ticks");
  assert.match(RENDER, /paintRailSticky\(\); paintScrollMarks\(\); updateCommentRail\(\);/, "one scheduler paints notches and dots from one world");
});

test("the dot is the comment's landed colour; UNREAD shouts; keyed on the kernel's bit on an open thread", () => {
  // the tick's fill is the comment INK (T310): the highlighter yellow itself in the dark theme, a darker amber on the cream
  // page — never a raw hex; its UNREAD halo is the needs-you red the passage's box and ring wear (the user 2026-09-12:
  // the rail agrees with the box's cue, by colour, since a 16×6 tick cannot show a dash)
  assert.match(CSS, /\.cmt-tick \{\s*\n\s*position: absolute; right: 1px; width: 14px; height: 4px;[\s\S]{0,120}background: var\(--cmt-hl-outline\);/, "the ink token, never a raw hex");
  assert.match(CSS, /\.cmt-tick\.unread \{ width: 16px; height: 6px; right: 0; opacity: 1; z-index: 1;\s*\n\s*box-shadow: 0 0 0 1\.5px var\(--bg\), 0 0 0 3px var\(--st-awaiting-bg\); \}/,
    "…and lifted above a read sibling at the same top (nested-marks.test.ts)");
  assert.match(RAIL, /\+ \(th\.unread && th\.status === "open" \? " unread" : ""\)/, "the same predicate the mark's ring and the reply chips read");
  assert.match(RAIL, /\+ ":" \+ \(t\.th\.unread \? 1 : 0\)/, "the unread bit rides the signature: a reply landing repaints the dot");
  assert.match(CSS, /\.cmt-tick\.busy \{ background: var\(--st-awaitbg-bg\); \}/, "a reply still being written is green here as on the mark");
});

test("a dot is a BUTTON, delegated: click = jump to the anchor and open the thread; same-tid sets move in place", () => {
  assert.match(RAIL, /const tick = el\("button", cls\(t\.th\)\) as HTMLButtonElement;/);
  assert.match(RAIL, /tick\.dataset\.act = "cmtjump";\s*\n\s*tick\.dataset\.tid = t\.th\.tid;\s*\n\s*tick\.dataset\.uuid = t\.th\.anchorUuid;/);
  assert.match(RENDER, /cmtjump: \(elx\) => \{\s*\n\s*const tid = elx\.dataset\.tid, uuid = elx\.dataset\.uuid;\s*\n\s*if \(!tid \|\| !uuid \|\| !activeId\) return;\s*\n\s*flashedAnchor = null;\s*\n\s*scrollToAnchor\(uuid\);/);
  assert.match(RENDER, /openCommentPopover\(activeId, tid, Math\.max\(8, r\.left - 380\), Math\.max\(60, r\.top - 40\)\);/, "…and opens it (which is what marks it read)");
  assert.match(RAIL, /if \(kids\.length === ticks\.length && kids\.every\(\(k, i\) => k\.dataset\.tid === ticks\[i\]\.th\.tid\)\) \{/, "an unchanged tick set moves in place — a mid-press rebuild can't eat the click");
  assert.match(CSS, /\.cmt-tick \{[\s\S]{0,200}pointer-events: auto; cursor: pointer;/, "only the tick takes the pointer");
});
