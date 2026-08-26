// The comment popover's chat-parity pass (the user 2026-08-25, four asks in one): the thread's
// statusline carries the chat's own working chip WITH the counting timer; the action buttons wear
// the chat's under-bubble button family; a fresh thread boots on the standard romp loader and then
// renders in its final (chat-parity) format ONCE — no intermediate msgs-projection flash; and the
// green→yellow settle repaints LIVE on the push event, no refocus needed (the kernel carries the
// settle count on the frame, since the wire dedup withholds the unchanged frames the client latch
// used to wait for). Source pins — the chat renderer has no jsdom harness (repo convention).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ui = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", ...p), "utf8");
const RENDER = ui("webview", "render.ts");
const COMMENTS = ui("webview", "comments.ts");
const CSS = ui("webview", "styles.css");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("the popover's bottom row IS a statusline: the chat's chip anatomy + counting timer", () => {
  // one vocabulary by construction: the row wears .statusline, the chip wears chip-working +
  // chip-pulse, the timer wears .status-timer — the chat's own classes, not restyled copies
  assert.match(RENDER, /const mrow = el\("div", "statusline cmt-meta-row"\);/);
  assert.match(RENDER, /mrow\.appendChild\(cmtStateChip\(th\)\);/);
  const chip = RENDER.split("function cmtStateChip(")[1].split("\nfunction ")[0];
  assert.ok(chip.includes('el("span", "chip chip-working")'), "the chat's working chip");
  assert.ok(chip.includes('el("span", "chip-pulse")'), "with its pulse label");
  assert.ok(chip.includes('timer.id = "cmt-work-timer"'), "and the counting timer");
  assert.ok(chip.includes("elapsedMs(th.sinceEpoch || null)"), "counting from the kernel's state-start epoch");
  // the 1s tick drives it exactly like the chat's #work-timer
  assert.match(RENDER, /const ct = document\.getElementById\("cmt-work-timer"\);/);
  assert.match(RENDER, /if \(cur && threadBusy\(cur\.th\.state\)\) ct\.textContent = elapsedMs\(cur\.th\.sinceEpoch \|\| null\);/);
  // the meta badges are the CHAT'S OWN controls (the user 2026-08-25 follow-up: sharing classes
  // wasn't enough — the popover copy drifted in dress, font, and element set): syncMetaControls
  // builds the FULL set (mode · model · effort · fast) into .sl-right, sid-scoped at the thread
  assert.match(RENDER, /const metaBox = el\("span", "spinner-meta"\);/);
  assert.match(RENDER, /syncMetaControls\(metaBox, threadMetaStatus\(th\), th\.tid\);/);
  const tms = RENDER.split("function threadMetaStatus(")[1].split("\nfunction ")[0];
  assert.ok(tms.includes("mode: th.mode ||"), "the mode badge's source rides the frame");
  assert.ok(tms.includes("fast: th.fast ||"), "the fast badge's source rides the frame");
  // …and the kernel sends both (the element-set gap: Auto and Fast were missing entirely)
  assert.match(KERNEL, /"mode": str\(meta\.get\("mode"\) or ""\), "fast": str\(meta\.get\("fast"\) or ""\),/);
  // the popover-local dress is GONE — no .cmt-meta chip skin to drift again; and the row restates
  // the page's base font inside the popover's 12px context (the adopted-context trap)
  const CSSs = CSS;
  assert.ok(!/\.cmt-meta \{/.test(CSSs), "no popover-local meta dress");
  assert.match(CSSs, /\.cmt-pop \.statusline \{[^}]*font-family: var\(--vscode-font-family\); font-size: var\(--vscode-font-size, 13px\); \}/);
  // …and the comments frame carries the epoch (milliseconds, the client convention)
  assert.match(KERNEL, /"sinceEpoch": since_ms,/);   // (the push-count field is retired — T102)
  // the in-place refresh keeps chip + timer live per frame (chips carry no listeners — click-safe)
  assert.match(RENDER, /if \(th && cs\) cs\.replaceWith\(cmtStateChip\(th\)\);/);
});

test("the action buttons wear the chat's under-bubble family; Fork stays out by the user's call", () => {
  assert.match(CSS, /\.cmt-act \{\s*\n\s*background: rgba\(255, 255, 255, 0\.06\);\s*\n\s*border: 1px solid var\(--box-border\); color: var\(--dim\); border-radius: 5px; font-size: 0\.78em;\s*\n\s*padding: 1px 8px; cursor: pointer;\s*\n\}/);
  assert.match(CSS, /\.cmt-act:hover \{ border-color: var\(--accent\); color: var\(--accent\); background: var\(--accent-wash\); \}/);   // the feed word-button hover (2026-08-25): text+outline accent, never a fill
  // the popover's verbs stay Break out / Merge / Delete — the chat's Fork button is deliberately absent
  const pop = RENDER.split("function renderCommentPopover(")[1].split("\nfunction ")[0];
  assert.ok(!/data-act="fork"|showForkPrompt/.test(pop), "no fork button in the popover");
});

test("a fresh thread boots on the romp loader, then renders its final format ONCE", () => {
  // the loader holds the list until the thread's REAL render (its events) is ready — the plain
  // msgs projection no longer flashes as an intermediate format
  assert.match(RENDER, /if \(!evs\.length && th\.status === "open" && !th\.error && cmtBootHolds\(th\.tid\)\) \{/);
  assert.match(RENDER, /boot\.appendChild\(rompLoaderInner\("opening the thread…", \{ wordmark: false \}\)\);/);
  // logo-only in the POPOVER (the user 2026-08-25): the spinning swirl + dots stay, the R-o-m-p
  // letters drop — parameterized on the ONE shared builder, so the boot splash and the pane/revive
  // loaders keep the full treatment (their call sites pass no opts)
  assert.match(RENDER, /if \(opts\?\.wordmark === false\) \{\s*\n\s*word\.appendChild\(swirl\);/);
  assert.match(RENDER, /o\.appendChild\(rompLoaderInner\(`reviving “\$\{name\}”…`\)\);/, "the revive loader keeps the wordmark");
  // event-based fade: the frame that carries events refills the list and retires the hold
  assert.match(RENDER, /if \(evs\.length\) cmtBootSince\.delete\(th\.tid\);/);
  // …with the can't-trap backstop: past it, the hold releases and the projection paints after all
  assert.match(RENDER, /const CMT_BOOT_BACKSTOP_MS = 8000;/);
  assert.match(RENDER, /window\.setTimeout\(\(\) => \{ if \(openCommentKey\?\.tid === tid\) refillOpenCommentPop\(\); \}, CMT_BOOT_BACKSTOP_MS \+ 50\);/);
  // the user's own pending sends still acknowledge under the loader — through the CHAT'S queued
  // idiom (T104: the one-off gray pill is gone; cmtPendingQueued IS renderQueued's bare group)
  const gate = RENDER.split("cmtBootHolds(th.tid)) {")[1].split("return;")[0];
  assert.ok(gate.includes("cmtPendingQueued(pend)"), "pending bubbles ride the boot view, chat-idiom");
});

test("the pulse is exchange-scoped (T102): send-gesture latch, reply-record clear — no push counts", () => {
  // the old kernel-carried confirm counter is retired root and branch: its all-quiet fork-birth
  // frames killed the create-window green, and a stall in its stepping parked green forever. The
  // pulse now latches at the SEND gesture and clears on the agent's reply RECORD in msgs
  // (comments.test.ts carries the full lifecycle pins; this guards the retirement on this surface).
  assert.doesNotMatch(KERNEL, /_comment_settle/);
  assert.doesNotMatch(RENDER, /settleConfirmed|commentBusyLatch/);
  assert.match(RENDER, /const cmtAwaitBase = new Map<string, number>\(\);/);
  // the frame handler already repaints marks AND the open popover per frame — the live wire
  assert.match(RENDER, /applyCommentMarks\(sid\);\s*\n\s*if \(openCommentKey && openCommentKey\.sid === sid\) \{/);
});
test("the model meta-menu exposes VERSIONS: submenu, remembered default, keyboard (the user 2026-08-25)", () => {
  // families with >1 live version wear a side submenu (leftward — the menu anchors bottom-right):
  // hover or an arrow key reveals every version, each pickable with the current-✓; clicking the
  // family sends its remembered DEFAULT (the kernel /models `default`), never a bare shorthand.
  // Rides the ONE builder, so the chat statusline and the popover statusline both grow it.
  assert.match(RENDER, /const versions = kind === "model" \? \(c\.versions \|\| \[\]\) : \[\]/);
  assert.match(RENDER, /pickValue\(kind === "model" \? \(c\.default \|\| c\.value\) : c\.value\)/,
    "family click sends the remembered default");
  assert.match(RENDER, /versions\.length > 1 \?/, "single-version families stay flat");
  assert.match(RENDER, /el\("span", "meta-caret"\)/, "the caret affordance marks expandable families");
  assert.match(RENDER, /\(e\.key === "ArrowRight" \|\| e\.key === "ArrowLeft"\) && openSub/,
    "arrow keys expand (the submenu opens leftward, so both arrows work)");
  assert.match(RENDER, /e\.key === "ArrowLeft"[\s\S]{0,90}closeSub\(\); item\.focus\(\)/,
    "ArrowLeft inside the submenu collapses back to the family row");
  assert.match(RENDER, /pickValue\(v\.value\)/, "version rows pick their own full id");
  assert.match(RENDER, /\(s\.status\.model \|\| ""\)\.toLowerCase\(\) === v\.label\.toLowerCase\(\)/,
    "the ✓ marks the session's current version");
  assert.match(RENDER, /querySelectorAll\("\.meta-sub"\)\.forEach/, "closing the menu drops an open submenu");
  assert.match(CSS, /\.meta-caret \{ position: absolute; right: 22px/, "caret sits left of the ✓ slot");
});


test("the badges' COLOR is the chat's too: the rank tints ride the frame (the 2026-08-25 color rider)", () => {
  // the user's sighting was two layers: a stale pre-parity window, AND a real gap underneath — the
  // chat's model/effort labels are tinted by server-computed rank colors (st.modelColor/effortColor,
  // metaColor), and the comments frame never carried them, so the popover's stayed plain gray.
  assert.match(KERNEL, /"modelColor": _model_color\(\(reg\.get\("liveModel"\) or reg\.get\("model"\) or ""\) if reg else "",\s*\n\s*cm\.stops_for\(_colormap\(\)\)\),/);
  assert.match(KERNEL, /"effortColor": _effort_color\(\(reg\.get\("effort"\) or ""\) if reg else "",\s*\n\s*cm\.stops_for\(_colormap\(\)\)\),/);
  assert.match(RENDER, /modelColor: th\.modelColor, effortColor: th\.effortColor \} as Status;/);
  // the equality bar (asserted headless over the built bundle, per the follow-up): computed
  // font-family/size/weight AND color/opacity equal chat↔popover for chip, timer, and all badges
});

test("the caret faces RIGHT and the submenu side is measured, right-preferred (the user 2026-08-25)", () => {
  assert.match(RENDER, /textContent = "\\u25B8"/, "the caret marks expandable, never the side");
  assert.ok(!RENDER.includes("\\u25C2"), "no left-facing caret anywhere");
  assert.match(RENDER, /if \(rr\.right \+ 4 \+ sw <= window\.innerWidth - 8\) sub\.style\.left = Math\.round\(rr\.right \+ 4\) \+ "px";/,
    "right side taken whenever it fits");
  assert.match(RENDER, /else sub\.style\.right = Math\.max\(8, window\.innerWidth - rr\.left \+ 4\) \+ "px";/,
    "left is the measured fallback, not the default");
});


test("the thread popover opens 70% wide right-aligned, 60% tall — and NEVER grows on content", () => {
  // the user 2026-08-25: "start out pretty big and fill in" — the size is fixed at open, streaming
  // fills within (.cmt-msgs flexes + scrolls; comments.test.ts pins that rule), the box never
  // reflows on a content event. Verified headless: 840×480 at 1200×800, right gap 8, zero rect
  // change across three streaming appends. The CREATE composer keeps its selection-point gesture.
  assert.match(RENDER, /if \(th && !pop\.style\.width\) \{ pop\.style\.width = Math\.round\(window\.innerWidth \* 0\.7\) \+ "px"; \}/);
  assert.match(RENDER, /if \(th && !pop\.style\.height\) \{ pop\.style\.height = Math\.round\(window\.innerHeight \* 0\.6\) \+ "px"; \}/);
  assert.match(RENDER, /const defaultX = th \? \(window\.innerWidth - r\.width - 8\) : \(window\.innerWidth - r\.width\) \/ 2;/);
  // a thread open ignores click coords — only a real drag parks the box elsewhere
  const opener = RENDER.split("function openCommentPopover(")[1].split("\nfunction ")[0];
  assert.ok(!opener.includes("commentPopPos = { x, y }"), "no click-coord seeding on thread open");
  // …and the CSS no longer hard-sizes the box (the inline open geometry owns it)
  assert.doesNotMatch(CSS, /\.cmt-pop \{[^}]*width: 440px/s);
});

test("the popover resizes from ANY edge or corner, macOS-style; the old grip keeps working", () => {
  // the user 2026-08-25: an 8px band per side is a live handle with the platform cursor, each pull
  // moving its own edge with the opposite one anchored, clamped to the CSS mins; the native
  // bottom-right grip's corner is deliberately deferred to (it keeps working exactly as before)
  const fn = RENDER.split("function wireEdgeResize(")[1].split("\nfunction ")[0];
  assert.ok(fn.includes('if (ev.clientX > r.right - 18 && ev.clientY > r.bottom - 18) return "";'), "the native grip corner defers");
  assert.ok(fn.includes('ne: "nesw-resize", sw: "nesw-resize", nw: "nwse-resize", se: "nwse-resize"'), "corner cursors");
  assert.ok(fn.includes('n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize"'), "edge cursors");
  assert.ok(fn.includes('if (zone.includes("w")) { wpx = r0.width - dx; left = r0.left + dx; }'), "west pulls west, east anchored");
  assert.ok(fn.includes('if (zone.includes("n")) { hpx = r0.height - dy; top = r0.top + dy; }'), "north pulls north, south anchored");
  assert.ok(fn.includes("const MIN_W = 300, MIN_H = 120;"), "min bounds match the CSS");
  assert.ok(fn.includes("pop.setPointerCapture(ev.pointerId);"), "capture keeps fast pulls on the edge");
  assert.ok(fn.includes("commentPopPos = { x: left, y: top };"), "a north/west pull persists position like the drag");
  assert.match(RENDER, /wireEdgeResize\(pop\);/);
  const CSSs = CSS;
  assert.match(CSSs, /resize: both/, "the native grip stays");
});

test("the create name input wears no underline at rest (the user 2026-08-25)", () => {
  assert.match(CSS, /\.cmt-name \{[^}]*border-bottom: 1px solid transparent;/s);
  assert.doesNotMatch(CSS, /\.cmt-name \{[^}]*dashed/s);
  assert.match(CSS, /\.cmt-name:focus \{ outline: none; border-bottom-color: var\(--accent\); \}/, "focus still shows the editing affordance");
});
