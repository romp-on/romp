// Feed card layout (the user 2026-06-14): the ask / standalone / group cards stack into THREE
// rows — title (full width), session name (own row), then a bottom row with the age on the left
// and the badges + Clear on the right. This frees the title and the (often long) session name to
// use the full card width instead of competing with the age/actions, and lets a long name wrap
// rather than overrun.
// No jsdom harness for the feed, so — like the other feed-*.test.ts — pin it at the source level.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("COMPACTNESS (the user 2026-07-07; action corner 2026-08-08): time trails the title; Continue+Clear in row1's corner; toggles grouped on row3", () => {
  // the TIME now trails the title on row1 (both cards); row3 holds the Background/Summary/Sub-goals toggles
  assert.match(FEED, /row1\.append\(title, time\)/, "the time trails the title on row1");
  assert.match(FEED, /row3\.append\(bgBtn, takeBtn, stallBtn, subBtn, taskBtn, actions\)/, "ask card: row3 is Background/Summary/Stalled/Sub-goals/Waiting-on-task (+ rare Retry/Revive)");
  assert.match(FEED, /actions\.append\(revive,/, "…so the action row is Retry/Revive (+ resume-gate) only (Clear + toggles moved up)");
  // the action corner (the user 2026-08-08): Continue+Clear ride the END of row1 in every mode; the
  // name row keeps only identity + chips
  assert.match(FEED, /btns\.append\(cont, clr\);/, "ask card: Continue left of Clear in the action corner");
  assert.match(FEED, /row1\.append\(btns\);/, "the corner floats from the END of row1's flow (title+time keep first claim)");
  assert.match(FEED, /row2\.append\(idwrap, retryBadge, apiBadge, apiRetry, jauthBadge, blkBadge, origin, fupBadge, dcBadge, nfBadge, intingBadge, intBadge, warnChip, waitOnBadge\)/, "ask card: the name row is identity + chips only");
  // its tooltip is plain-spoken (the user 2026-07-13): "clear this task", not the inbox-zero jargon
  assert.match(FEED, /clr\.title = "clear this task";/);
  assert.match(FEED, /btns\.append\(clr\);\s*\n\s*row1\.append\(btns\);\s*\n\s*row2\.append\(idwrap\);/, "group card: Clear in row1's action corner, name row is the name only");
  // the group card has no row3 anymore (its only content, the time, moved to row1)
  assert.match(FEED, /main\.append\(row1, row2, memberList\)/, "group card: no row3 (time moved to row1)");
  assert.doesNotMatch(FEED, /const row3 = el\("div", "fask-row3"\); row3\.append\(time\)/, "the group card's time-only row3 is gone");
});

test("row3 + name row are styled", () => {
  assert.match(CSS, /\.fask-row3 \{[^}]*display: flex/);
  // the session name stays on ONE line (ellipsis only if truly too long) — it used to wrap mid-word
  // while the "↪ from" provenance crowded it; now the row fills its width and origin goes right (the
  // user 2026-06-16).
  assert.match(CSS, /\.fask-id \.fname \{[^}]*white-space: nowrap/, "the session name stays on one line, never mid-word");
});

test("the ⏸ blocked (permission/picker) badge is a rounded-rect pill outlined in its own red", () => {
  // mirrors the Clear button's chrome (.fdismiss) — a border + rounded corners + padding — but kept in
  // the badge's own red (the user 2026-06-16), so a live permission/picker block reads as a pill
  assert.match(CSS, /\.fask-blocked \{[^}]*border: 1px solid #c0392b/);
  assert.match(CSS, /\.fask-blocked \{[^}]*border-radius:/);
  assert.match(CSS, /\.fask-blocked \{[^}]*padding:/);
  // no longer bare text with an underline hover — the pill fills on hover like Clear
  assert.match(CSS, /\.fask-blocked:hover \{[^}]*background: #c0392b/);
  assert.doesNotMatch(CSS, /\.fask-blocked:hover \{[^}]*text-decoration: underline/);
});

test("the ⏸ picker/approval chip jumps to the LIVE prompt in the chat (openSession + live, the user 2026-07-08)", () => {
  // the prompt is the session's live bottom, so the chip posts `live: true` → the chat lands right on it
  assert.match(FEED, /vscodeApi\?\.postMessage\(\{ type: "openSession", id: it\.sid, live: true \}\);/);
  assert.match(FEED, /a\._blocked\.title = it\.blocked\.what \+ " — click to jump to the prompt in the chat";/);
});

test("courier handoff: the '↪ from <sender>' origin marker is wired and styled", () => {
  // a chip beside the session name, hidden until the card carries a courier origin
  assert.match(FEED, /const origin = el\("a", "fask-origin"\); origin\.style\.display = "none"/);
  // it's a direct child of the wrapping row2 (NOT nested in idwrap) so a narrow card wraps it under the
  // name instead of overlapping the chips (the user 2026-06-20)
  assert.match(FEED, /row2\.append\(idwrap, retryBadge, apiBadge, apiRetry, jauthBadge, blkBadge, origin, fupBadge, dcBadge, nfBadge, intingBadge, intBadge, warnChip, waitOnBadge\)/, "the origin marker rides the name row beside the chips");
  assert.doesNotMatch(FEED, /idwrap\.append\(name, origin\)/, "origin is no longer nested inside idwrap");
  // populated from it.origin in the update path: a dim gray "↪ from" + the peer in the bold session-name
  // style (its own identity colour); click opens the sender (the user 2026-06-16)
  assert.match(FEED, /pre\.textContent = "↪ from "/);
  // the peer renders through hostPartsNodes so a FEDERATED sender wears the quiet "host:" prefix,
  // same treatment as remote session names everywhere else (the user 2026-07-26)
  assert.match(FEED, /peer\.replaceChildren\(\.\.\.hostPartsNodes\(it\.origin\.peerHost, it\.origin\.peer\)\)/);
  assert.match(FEED, /if \(it\.origin\.color\) peer\.style\.color = it\.origin\.color\.bg/);
  assert.match(FEED, /type: "openSession", id: it\.origin!\.peerSid/, "clicking the marker opens the sender");
  assert.match(CSS, /\.fask-origin-pre \{[^}]*var\(--dim\)/);     // "↪ from" dim gray
  assert.match(CSS, /\.fask-origin-peer \{[^}]*font-weight: 600/); // peer bold like other session names
});

test("the follow-up badge serves ONLY '↩ re-judging' now — the '↻ Followed up' chip was removed (the user 2026-07-01)", () => {
  assert.match(FEED, /el\("span", "fask-followedup"\); fupBadge\.textContent = "↩ re-judging"/);
  // the badge rides the SESSION-NAME row (right-justified), NOT the bottom action row
  assert.match(FEED, /row2\.append\(idwrap, retryBadge, apiBadge, apiRetry, jauthBadge, blkBadge, origin, fupBadge, dcBadge, nfBadge, intingBadge, intBadge, warnChip, waitOnBadge\)/);
  // the CARD badge block is now recheck-only: recheck → "↩ re-judging", else hidden. No followupPending branch.
  // (The modal tree's per-node "↻ Followed up" chip, ftree-followedup, is a separate thing and stays.)
  assert.match(FEED, /if \(it\.recheck\) \{\s*\n\s*a\._followedup\.style\.display = "";\s*\n\s*a\._followedup\.textContent = "↩ re-judging";[\s\S]*?\} else \{\s*\n\s*a\._followedup\.style\.display = "none";\s*\n\s*\}/);
  assert.doesNotMatch(FEED, /else if \(it\.followupPending\) \{/, "the card's reopened-to-Working '↻ Followed up' branch is gone");
  assert.match(CSS, /\.fask-followedup \{/);
});

test("session-STATE badges (⏸ approval / ⚠ API error) ride the name row; the footer is buttons only (the user 2026-06-19)", () => {
  // the bug: ⏸ approval + buttons + Clear in the SAME footer row shoved them off a narrow card.
  // Fix: the state badges move up beside the session name; the action row holds only the buttons.
  // The ⏳ "awaiting" chip was REMOVED (the user 2026-07-04) — the body "Awaiting background agents" box says it.
  assert.match(FEED, /idwrap\.append\(name\);/,
    "idwrap holds ONLY the name now (2026-08-24): grouped mode hides it wholesale, so every state badge moved to row2 direct children where both modes render them");
  assert.doesNotMatch(FEED, /waitBadge/, "the redundant awaiting chip element is gone entirely");
  assert.match(FEED, /actions\.append\(revive,/, "action row = Retry/Revive (+ resume-gate) only (Clear moved to the name row 2026-07-07)");
  assert.match(FEED, /a\._blocked = blkBadge;/);
});

test("a cleared card CONTRACTS in on itself (scale + collapse), not a slide to one side (the user 2026-06-18)", () => {
  // no translateX exit; the card scales down + fades while its height collapses so neighbours close the gap
  assert.doesNotMatch(CSS, /\.fitem\.dismissing \{[^}]*translateX/);
  assert.match(CSS, /\.fitem\.dismissing \{[^}]*animation: fask-dismiss/);
  assert.match(CSS, /@keyframes fask-dismiss \{[\s\S]*transform: scale\(0\.78\)[\s\S]*max-height: 0/);
  assert.match(CSS, /prefers-reduced-motion: reduce[\s\S]*\.fitem\.dismissing \{ animation: none/);
});

test("the footer action row WRAPS its buttons so they can NEVER run off the card edge (the user 2026-06-22)", () => {
  // ROBUST, GENERAL mechanism (not per-button width tuning, which kept regressing): .fask-actions takes the
  // width left after the age, right-aligns, and flex-WRAPS its buttons onto extra lines when they don't fit;
  // .fask-row3 wraps as a backstop. min-width:0 lets it shrink to the card so the wrap actually triggers.
  // Verified headless: the toggles + buttons wrap to their own line under the time on a narrow card, zero overflow.
  assert.match(CSS, /\.fask-actions \{[^}]*flex: 0 1 auto;[^}]*min-width: 0;[^}]*flex-wrap: wrap;[^}]*justify-content: flex-end/);
  assert.match(CSS, /\.fask-row3 \{[^}]*flex-wrap: wrap/);
  // the section toggles GROUP left and wrap together (the user 2026-07-08) — no longer spread to opposite
  // edges; the old `margin-left:auto` on a trailing secbtn is gone.
  assert.doesNotMatch(CSS, /\.fask-row3 \.fask-secbtn ~ \.fask-secbtn \{ margin-left: auto; \}/);
  // the title fills the full card width (flow-root row1 + inline title, the user 2026-07-08) and the time
  // trails it INLINE right after the last word — no reserved column, no empty space top-right. flow-root,
  // not block (the user 2026-07-31): row1 must CONTAIN its grouped-mode Clear float, else a Clear that
  // drops off a full time line overlays row 3 and hangs misaligned beside the Background/Summary toggles.
  assert.match(CSS, /\.fask-row1 \{ display: flow-root; \}/);
  assert.doesNotMatch(CSS, /\.fask-row1 \{ display: block; \}/, "a plain block gains no height from the float");
  assert.match(CSS, /\.fask-row1 \.fcard-title \{ display: inline; \}/);
  assert.match(CSS, /\.fask-row1 \.ftime \{ margin-left: 8px; \}/);
});

test("a long no-space token (file/func/type name) WRAPS instead of overflowing the card (the user 2026-06-23)", () => {
  // overflow-wrap: anywhere (not break-word) so a token like SdkBackend.pending_queued(sid:str) breaks to
  // fit — the title used break-word (kept the longest word as min-width) and the summary had NO wrap at all.
  assert.match(CSS, /\.fcard-title \{[^}]*overflow-wrap: anywhere/);
  // (the .fask-blockwhy/.fask-donewhy auto-line was removed 2026-06-27, so its wrap rule is gone too)
});

// ── grouped-mode cards carry their state badges (2026-08-24, the Retry-fix audit's finding #1):
// grouped mode hides idwrap wholesale (the name lives on the session header), which silently
// blanked every badge inside it — ⚠ retrying-since, judge-auth, and the ⏸ approval chip never
// showed on grouped-mode cards. Every state badge is a DIRECT row2 child now; placement only.
test("every session-state badge is a direct row2 child — visible in grouped AND flat mode", () => {
  assert.match(FEED, /row2\.append\(idwrap, retryBadge, apiBadge, apiRetry, jauthBadge, blkBadge, origin,/);
  assert.match(FEED, /idwrap\.append\(name\);/, "idwrap = the name alone; hiding it hides nothing else");
  assert.doesNotMatch(FEED, /idwrap\.append\([^)]*(retryBadge|apiBadge|jauthBadge|blkBadge)/,
    "no state badge ever returns to the grouped-mode-hidden wrap");
  // the grouped-mode liveness check reads DIRECT children, so a visible badge keeps row2 shown
  assert.match(FEED, /const r2live = \(Array\.from\(r2\.children\) as HTMLElement\[\]\)\.some\(\(c\) => c\.style\.display !== "none"\);/);
  // …and only idwrap (the name) is what grouped mode drops
  assert.match(FEED, /\(\(a\._name as HTMLElement\)\.parentElement as HTMLElement\)\.style\.display = gmode \? "none" : "";/);
});
