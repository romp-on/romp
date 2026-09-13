// Chat columns (the user 2026-09-08: several sessions open at once instead of tabbing through them; the partition
// 2026-09-11: the columns hold DIFFERENT sessions). The shell owns the columns and which sessions each holds
// (kernel.py _LANDING_SPLIT_JS, executed in tests/test_chat_split.py); the page's pure half is chat-columns.ts
// (executed in chat-columns.test.ts). This pins the PANE and PALETTE halves at source (no jsdom for the renderer —
// the repo convention):
//   * render.ts stands down on a focus, a revive prompt or the feed's click echo for a session another column
//     holds, hands a consumed parked reveal (`own`) to that column once, routes a pick of a session living
//     elsewhere to its owner ahead of the drafts swap, hands a moved tab's drafts over and adopts them, tells the
//     shell when a tab drags (the shell's drop zones move it), and measures ITS OWN pane when lifted;
//   * palette-main.ts aims every chat-directed command at the column last worked in, registers the move and
//     close commands under their new meanings, and wires its chords on a column made later;
//   * commands.ts keeps the move-to-a-new-column on the editor convention, the rest unbound.
// Synthetic only — no session data.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const MAIN = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "palette-main.ts"), "utf8");
const COMMANDS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "commands.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("the pane asks the shell which column holds a session, acts only when it is its own, and hands a consumed reveal to the owner once", () => {
  // the arbitration: the shell's __rompChatTarget names the frame; no shell (standalone, VS Code) → always ours
  assert.match(RENDER, /function focusIsOurs\(sid: string\): boolean \{[\s\S]*?if \(!window\.parent \|\| window\.parent === window\) return true;[\s\S]*?const t = \(window\.parent as any\)\.__rompChatTarget;[\s\S]*?if \(typeof t !== "function"\) return true;[\s\S]*?return !f \|\| f === window\.frameElement;/);
  // a kernel focus aimed at another column is swallowed BEFORE the real branch; a consumed parked reveal (`own`: the
  // kernel sent it to THIS one client) is forwarded to the owner as a copy WITHOUT `own`, so it cannot hop again
  assert.match(RENDER, /else if \(m\.type === "focus" && !focusIsOurs\(m\.id\)\) \{[\s\S]*?if \(m\.own\) \{ const copy = \{ \.\.\.m \}; delete copy\.own; forwardToOwner\(copy\); \}[\s\S]*?\n  \}\n  else if \(m\.type === "focus"\) \{/);
  // …and the create this focus answers still retires in the column that asked (the provisional tab is that column's)
  const gate = RENDER.slice(RENDER.indexOf('else if (m.type === "focus" && !focusIsOurs(m.id)) {'), RENDER.indexOf('else if (m.type === "focus") {'));
  assert.match(gate, /if \(focusResolvesProvisional\(m\.id, tabName\(m\.id\), pendingNewSession, provisionalId\)\) resolveProvisionalToExisting\(m\.id\);/);
  // …reading the name where this page has it: a skeleton tab (every tab a later column did not open on) never reaches
  // `sessions`, so `sessions.get(id)?.name` was undefined for the very sessions this branch exists for and the create
  // waited out the 90 s backstop instead (review find 2026-09-11); both focus branches read the same helper
  assert.match(RENDER, /function tabName\(id: string\): string \| undefined \{ return sessions\.get\(id\)\?\.name \?\? tabMeta\.get\(id\)\?\.name; \}/);
  assert.equal((RENDER.match(/focusResolvesProvisional\(m\.id, tabName\(m\.id\), pendingNewSession, provisionalId\)/g) || []).length, 2, "the swallow branch and the acting branch");
  assert.doesNotMatch(RENDER, /focusResolvesProvisional\(m\.id, sessions\.get/);
  // the revive prompt: the same gate, the same forward
  assert.match(RENDER, /else if \(m\.type === "confirmRevive" && m\.id && !focusIsOurs\(m\.id\)\) \{[\s\S]*?if \(m\.own\) \{ const copy = \{ \.\.\.m \}; delete copy\.own; forwardToOwner\(copy\); \}\n  \}\n  else if \(m\.type === "confirmRevive" && m\.id\) \{/);
  // the hop itself: the owner's frame by the same shell lookup, the message posted and the keyboard following; false
  // when the owner is this frame or there is no shell, so the caller acts locally
  assert.match(RENDER, /function forwardToOwner\(m: Record<string, unknown>\): boolean \{[\s\S]*?const t = \(window\.parent as any\)\.__rompChatTarget;[\s\S]*?const f = t\(String\(m\.id\)\);\n\s*if \(!f \|\| f === window\.frameElement \|\| !f\.contentWindow\) return false;\n\s*f\.contentWindow\.postMessage\(m, "\*"\);/);
  // the shell hands a NEW column no focus (2026-09-11): it seeds the column's state blob with the session before the
  // frame exists, and the page's own wantActive activates it when its frame lands (tests/test_chat_split.py pins the
  // seed); a move into an OPEN column posts a plain focus, which the owner's own gate takes
  assert.ok(!KERNEL.includes("own:true"), "no hand-over focus wearing `own` anywhere in the shell");
  // the feed's click echo reaches every column's storage listener: the same gate, after the known-session check
  assert.match(RENDER, /if \(e\.key !== "romp:focus-echo" \|\| !e\.newValue\) return;[\s\S]*?if \(!sid \|\| \(!sessions\.has\(sid\) && !tabMeta\.has\(sid\)\)\) return;\n\s*if \(!focusIsOurs\(sid\)\) return;/);
});

test("a pick of a session another column holds is shown where it lives: the setActive guard precedes the peek and the drafts swap", () => {
  const fn = RENDER.slice(RENDER.indexOf("function setActive(id: string, anchor?: string"), RENDER.indexOf("function cycleTab("));
  const guard = fn.indexOf('if (colSets && !heldHere(id) && forwardToOwner({ type: "focus", id, anchor, anchorT, anchorKind, anchorEventT })) return;');
  assert.ok(guard > 0, "the guard exists and carries every field of the pick");
  assert.ok(fn.indexOf("noteMru(id);") < guard, "after noteMru");
  assert.ok(guard < fn.indexOf("assertPeekFor(id);"), "before the peek: a forwarded pick never opens a peek here");
  assert.ok(guard < fn.indexOf("// Stash the leaving tab's draft"), "before the drafts swap: this page's box never changes hands for a session it does not show");
  // the boot memberships: a later column never adopts a non-member's frame, and a reload never re-activates a tab dragged away
  assert.match(RENDER, /const wouldAdopt = !activeId && \(!vanishedId \|\| vanishedByDecline\) && !wantActive && !wantActiveGone && heldHere\(msg\.id\);[^\n]*\n\s*const adopted = wouldAdopt && stripShows\(msg\.id\);/);   // …with T357's away, awaited and gone gates beside the membership one; a non-member neither adopts nor leaves a declined record
  assert.match(RENDER, /if \(vanishedId === msg\.id && heldHere\(msg\.id\)\) restoreIfShown\(msg\.id\);/, "the user's own tab's return is restored only while this column holds it");
  assert.match(RENDER, /if \(wantActive && msg\.id === wantActive && stripLists\(msg\.id\) && heldHere\(msg\.id\)\) \{ wantActive = null; restoreIfShown\(msg\.id\); \}/);
  // …with the sets read fresh right there, not the last render's
  const up = RENDER.slice(RENDER.indexOf("function upsert(msg: any) {"), RENDER.indexOf("function update(msg: any) {"));
  assert.ok(up.indexOf("colSets = readColSets();") > 0 && up.indexOf("colSets = readColSets();") < up.indexOf("const wouldAdopt = "), "membership re-read before the adoptions");
  // a session created from a later column is claimed for it before the switch that shows it
  const adopt = RENDER.slice(RENDER.indexOf("function adoptProvisional("), RENDER.indexOf("function resolveProvisionalToExisting("));
  assert.ok(adopt.indexOf("claimSession(realId);") > 0 && adopt.indexOf("claimSession(realId);") < adopt.indexOf("setActive(realId);"));
  assert.match(RENDER, /function claimSession\(id: string\): void \{\n\s*if \(!COL\) return;[\s\S]*?__rompClaimSession;[\s\S]*?c\(id, COL\);[\s\S]*?colSets = readColSets\(\);\n\}/);
  // the stale-active fallback and the emptiness post exist, each gated on the kernel's first strip (chat-split-exec.test.ts
  // runs them); the fallback is the PARTITION's — a page with no sets (standalone, VS Code) boots exactly as before, the first
  // arriving frame adopted (review find 2026-09-11) — and a create in flight, or a failed one holding its text, keeps a
  // column: no emptiness post while it stands (review find 2026-09-11: the column closed under it and the queued text died)
  assert.match(RENDER, /function staleActiveFallback\(ids: readonly string\[\], visibleIds: readonly string\[\]\): void \{\n\s*if \(colSets === null\) return;[^\n]*\n(?:\s*\/\/[^\n]*\n)*\s*if \(activeId \|\| vanishedId \|\| !tabOrderSeen \|\| provisionalId \|\| !visibleIds\.length\) return;\n\s*if \(wantActive && heldHere\(wantActive\)\) return;/,
    "the fallback yields to an active tab, a tab that left on its own, and a wanted tab this column holds (T357 keeps the pane unfocused for its return); a wanted tab held elsewhere is retired");
  // …a member the kernel's live set still affirms counts as present unless this page's own cross removed it (T258 on a fresh
  // column), and the post names the crossed members, the only ones the shell holds back (the vanishing tab, 2026-09-12)
  assert.match(RENDER, /function noteColumnEmptiness\(ids: readonly string\[\]\): void \{\n\s*if \(!COL \|\| !colSets \|\| !tabOrderSeen\) return;\n(?:\s*\/\/[^\n]*\n)*\s*if \(provisionalId \|\| failedProvisionals\.size\) return;\n\s*const mine = colSets\[COL\] \|\| \[\];\n\s*const present = \(id: string\) => ids\.includes\(id\) \|\| \(boardLive\.has\(id\) && !closingTabs\.has\(id\)\);\n\s*const empty = mine\.length > 0 && !mine\.some\(present\);[\s\S]*?const crossed = mine\.filter\(\(id\) => closingTabs\.has\(id\)\);\n\s*try \{ window\.parent\.postMessage\(\{ romp: "colEmpty", gone: mine\.slice\(\), crossed \}, "\*"\);/);
  assert.match(RENDER, /boardLive = liveSet;/, "applyTabOrder keeps the frame's live set for the emptiness post");
  // the flag is armed by the LOCAL kernel's own strip only (tab-order.ts localStrip): a synthetic re-emission — on a fresh
  // page served from an EMPTY store, order [] — or another host's fresh push is never the board (the vanishing tab, 2026-09-12)
  assert.match(RENDER, /if \(localStrip\(report\)\) tabOrderSeen = true;\n\s*renderTabs\(\);\n\}/, "set in applyTabOrder on the kernel's own strip, ahead of its render");
  assert.match(RENDER, /import \{ localStrip, readCloseAckMs \} from "\.\/tab-order";/);
  // the shell's two questions before it moves a tab or closes a column (kernel.py moveTab / close; tests/test_chat_split.py
  // runs the refusals): an id a column can hold, and a create in flight here
  assert.match(RENDER, /\(window as any\)\.__rompMovableSession = \(sid: unknown\): boolean => typeof sid === "string" && !!sid && !isProvisionalId\(sid\) && !isSubId\(sid\) && !settings\.tabsLocked;/);   // …and no while the tabs are locked (T395)
  assert.match(RENDER, /\(window as any\)\.__rompColumnBusy = \(\): boolean => !!provisionalId \|\| failedProvisionals\.size > 0;/);
  assert.match(KERNEL, /function movable\(f,sid\)\{[^\n]*__rompMovableSession/);
  assert.match(KERNEL, /function busy\(f\)\{[^\n]*__rompColumnBusy/);
  // the ids a colEmpty close sends home are held back on the first column's strip until the kernel's strip omits them
  // (the same closingTabs a ✕ uses), so no tab flashes into that strip on its way out
  assert.match(RENDER, /if \(m\.romp === "closing"\) \{ if \(Array\.isArray\(m\.ids\)\) for \(const id of m\.ids\) \{ if \(typeof id === "string" && id\) closingTabs\.set\(id, Date\.now\(\)\); \} renderTabs\(\); return; \}/);
  // …and the shell names ONLY the ids the page's own cross removed (colEmpty's `crossed`): the backstop behind the hold toasts
  // "Couldn't close", right for a refused cross and wrong for anything else (the vanishing tab, 2026-09-12)
  assert.ok(KERNEL.includes("var crossed=Array.isArray(m.crossed)?gone.filter(function(id){return m.crossed.indexOf(id)>=0;}):[];"));
  assert.ok(KERNEL.includes("home.contentWindow.postMessage({romp:'closing',ids:crossed},'*');"));
  assert.ok(!KERNEL.includes("{romp:'closing',ids:gone}"), "no hold over the whole gone list");
  // orphaned state (a v1 column blob's drafts for sessions the column no longer shows) is offered to the shell every
  // render while it remains, from renderTabs right after the emptiness post, and the shell hands it to the owner's page
  assert.match(RENDER, /noteColumnEmptiness\(ids\);[^\n]*\n\s*noteOrphanState\(\);/);
  assert.match(RENDER, /function orphanStateSids\(\): string\[\] \{[\s\S]*?\[\.\.\.drafts\.keys\(\), \.\.\.composerCitations\.keys\(\), \.\.\.composerFiles\.keys\(\), \.\.\.Object\.keys\(stagedMsgs\.entries\(\)\)\][\s\S]*?if \(!isProvisionalId\(id\) && !isSubId\(id\) && !heldHere\(id\)\) out\.add\(id\);/);
  assert.match(RENDER, /function noteOrphanState\(\): void \{\n\s*if \(!colSets \|\| !tabOrderSeen\) return;[\s\S]*?window\.parent\.postMessage\(\{ romp: "orphanState", sids \}, "\*"\);/);
  assert.ok(KERNEL.includes("if(m.romp==='orphanState'&&Array.isArray(m.sids)){"));
  // the no-sessions copy's third case: sessions listed, none this column's
  assert.match(RENDER, /const txt = totalCount > 0 && heldCount === 0\n\s*\? "Every session is in another column\. Drag a tab here, or start one with the \+ above\."/);
});

test("drafts travel with a moved tab: the source hands over what it holds, synchronously, and the target adopts it", () => {
  const take = RENDER.slice(RENDER.indexOf("(window as any).__rompTakeSessionState = "), RENDER.indexOf("function adoptSessionState("));
  // the composer's text is stashed first when the tab is active, then the four slices persistDrafts writes leave the maps
  assert.match(take, /if \(sid === activeId && ta\) \{ if \(ta\.value\) drafts\.set\(sid, ta\.value\); else drafts\.delete\(sid\); \}/);
  assert.match(take, /const draft = drafts\.get\(sid\) \?\? "", citations = composerCitations\.get\(sid\) \?\? \[\], files = composerFiles\.get\(sid\) \?\? \[\], staged = stagedMsgs\.takeAll\(sid\);/);
  assert.match(take, /drafts\.delete\(sid\); composerCitations\.delete\(sid\); composerFiles\.delete\(sid\);/);
  assert.match(take, /persistDrafts\(\);/);
  assert.match(take, /if \(!draft && !citations\.length && !files\.length && !staged\.length\) return null;/, "null when nothing was held");
  // an active tab's box is emptied: the re-point that follows must not re-stash the moved text here
  assert.match(take, /if \(sid === activeId\) \{ if \(ta\) \{ ta\.value = ""; growComposer\(ta\); \}/);
  // the target: into the maps, persisted, and into the box when the tab is active
  const adopt = RENDER.slice(RENDER.indexOf("function adoptSessionState("), RENDER.indexOf("function noteMru("));
  assert.match(adopt, /persistDrafts\(\);\n\s*if \(activeId === sid\) loadComposerFor\(sid\);/);
  assert.match(RENDER, /if \(m\.romp === "adopt"\) \{ adoptSessionState\(m\.sid, m\.state\); return; \}/, "the shell's message lands in the same relay as chatNav");
});

test("the tab menu offers no column item and the page never asks the shell to open a split: a tab is placed by dragging it (2026-09-11)", () => {
  const menuAt = RENDER.indexOf("function showTabMenu(");
  const menu = RENDER.slice(menuAt, RENDER.indexOf("document.body.appendChild(menu);", menuAt));
  assert.ok(!menu.includes("Move to a new column") && !menu.includes("Open in new split"), "no column item in the menu");
  assert.ok(!RENDER.includes("shellCanSplit"), "the shell probe went with the item");
  assert.ok(!RENDER.includes("openSplit"), "the page never posts openSplit");
  assert.ok(!KERNEL.includes("openSplit"), "…and the shell has no listener for it: __rompMoveTab's doors are the drop zones, the palette and the cross");
  assert.doesNotMatch(RENDER, /kind === "split"/, "the two-columns glyph went with the item");
  // the drag tells the shell instead (tab-drag-live.test.ts pins the posts; tests/test_chat_split.py runs the zones)
  assert.match(RENDER, /\{ romp: "tabDrag", on: true, sid: id, name, stripH/);
  assert.match(KERNEL, /if\(m\.romp==='tabDrag'\)\{/);
});

test("a lifted column measures ITS OWN pane, never the first column's", () => {
  assert.match(RENDER, /function liftPaneRect\(\): DOMRect \| null \{[\s\S]*?const own = window\.frameElement \? \(window\.frameElement as HTMLElement\)\.parentElement : null;\n\s*const p = own \|\| window\.parent\?\.document\?\.getElementById\("chat-pane"\);/);
  // …and the shell lifts by class, marking the asking frame (the pinned CSS moved off #f-chat)
  assert.ok(KERNEL.includes('"body.picker-open iframe.lifted{display:block;position:fixed;left:0;right:0;top:0;height:var(--app-h,100dvh);z-index:200;background:transparent}"'));
  assert.ok(!KERNEL.includes('"body.picker-open #f-chat{'));
});

test("every chat-directed shell command lands in the column last worked in", () => {
  assert.match(MAIN, /function chatPane\(\): HTMLIFrameElement \| null \{\n\s*try \{ const id = w\.__rompFocusedChatId && w\.__rompFocusedChatId\(\);[\s\S]*?return pane\("f-chat"\);\n\s*\}/);
  assert.match(MAIN, /function chatPost\(msg: object\): void \{[\s\S]*?const f = chatPane\(\);/);
  assert.match(MAIN, /\(chatPane\(\)\?\.contentWindow as any\)\?\.__rompSessionList/);
  assert.match(MAIN, /\(chatPane\(\)\?\.contentWindow as any\)\?\.__rompMru/);
  assert.match(MAIN, /chatPane\(\)!\.contentWindow!\.postMessage\(\{ romp: "chatNav", dir: -1 \}/);
  assert.match(MAIN, /chatPane\(\)!\.contentWindow!\.postMessage\(\{ romp: "chatNav", dir: 1 \}/);
  assert.match(MAIN, /onClose: \(\) => \{ try \{ chatPane\(\)!\.contentWindow!\.focus\(\); \}/);
  assert.ok(!/pane\("f-chat"\)!/.test(MAIN), "no chat-directed command still hard-wires the first column");
  // the two move commands read the focused column and its neighbours through chatPane(), never pane("f-chat")
  const mv = MAIN.slice(MAIN.indexOf("function moveActiveSession("), MAIN.indexOf('registerCommand({ id: "chat.moveToNextColumn"'));
  assert.match(mv, /const f = chatPane\(\);/);
  assert.match(mv, /if \(!sid\) \{ columnNotice\("No session is open in this column to move\."\); return; \}/, "nothing to move says so");
  assert.match(mv, /if \(i === 0\) \{ columnNotice\("This session is in the first column already\."\); return; \}/, "a move left from the first column says so");
  assert.match(mv, /w\.__rompMoveTab\(sid, i === frames\.length - 1 \? "new" : colOf\(frames\[i \+ 1\]\)\)/, "past the last column: a new one (the shell checks the cap)");
});

test("the column commands exist under their new meanings, the move to a new column keeps the editor convention, the rest are unbound, and a new column gets the chords", () => {
  assert.match(MAIN, /registerCommand\(\{ id: "chat\.split", title: "Move this session to a new column", run: \(\) => \{ if \(w\.__rompSplitChat\) w\.__rompSplitChat\(\); \} \}\);/);
  assert.match(MAIN, /registerCommand\(\{ id: "chat\.closeSplit", title: "Close this column", run: \(\) => \{ if \(w\.__rompCloseSplit\) w\.__rompCloseSplit\(\); \} \}\);/);
  assert.match(MAIN, /registerCommand\(\{ id: "chat\.moveToNextColumn", title: "Move this session to the next column", run: \(\) => moveActiveSession\(1\) \}\);/);
  assert.match(MAIN, /registerCommand\(\{ id: "chat\.moveToPrevColumn", title: "Move this session to the previous column", run: \(\) => moveActiveSession\(-1\) \}\);/);
  assert.match(COMMANDS, /"chat\.split": "Mod\+\\\\",/);
  for (const id of ["chat.closeSplit", "chat.moveToNextColumn", "chat.moveToPrevColumn"]) assert.ok(!COMMANDS.includes('"' + id + '":'), id + " stays unbound by default — the palette owns it");
  // the fixed four are wired as before, and a column the split makes later through the shell's event
  assert.match(MAIN, /\["f-chat", "f-fleet", "f-feed", "f-files", "f-timeline", "f-settings"\]\.forEach\(\(id\) => wireKeys\(pane\(id\)\)\);/);   // + the Files pane and the settings iframe (main, 2026-09-10: the gear's document holds the keyboard while open)
  assert.match(MAIN, /window\.addEventListener\("romp-chat-cols", \(e\) => wireKeys\(/);
  // …and the columns restored before this module booted (the shell's split script runs ahead of it)
  assert.match(MAIN, /\(\(w\.__rompChatFrameIds \? w\.__rompChatFrameIds\(\) : \[\]\) as string\[\]\)\.forEach\(\(id\) => \{ if \(id !== "f-chat"\) wireKeys\(pane\(id\)\); \}\);/);
  // …which the shell dispatches with the frame when a column opens
  assert.ok(KERNEL.includes("window.dispatchEvent(new CustomEvent('romp-chat-cols',{detail:{frame:f,col:n,open:true}}))"));
  // no bottom-bar button for it (the user 2026-09-08): the tab menu and the palette are the doors
  assert.ok(!KERNEL.includes("rail-split"));
});
